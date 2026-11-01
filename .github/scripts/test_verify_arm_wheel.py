"""Offline tests for single-build Windows ARM cryptography wheel provenance."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from verify_arm_wheel import check, main


class TestArmWheelManifest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.wheels = root / "wheels"
        self.wheels.mkdir()
        self.constraints = root / "constraints.txt"
        self.constraints.write_text(
            'cryptography==50.0.2; sys_platform == "win32" and platform_machine == "ARM64"\n',
            encoding="utf-8",
        )
        self.wheel = self.wheels / "cryptography-50.0.2-cp312-abi3-win_arm64.whl"
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr(
                "cryptography-50.0.2.dist-info/WHEEL",
                "Wheel-Version: 1.0\nTag: cp312-abi3-win_arm64\n",
            )

    def test_positive_write_verify(self) -> None:
        first = check(self.wheels, self.constraints, "write")
        second = check(self.wheels, self.constraints, "verify")
        self.assertEqual(first["status"], "PASS_RECORDED")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["version"], "50.0.2")
        self.assertEqual(
            json.loads((self.wheels / "manifest.json").read_text()),
            {k: v for k, v in first.items() if k not in ("status", "mode")},
        )

    def test_corrupted_wheel_fails(self) -> None:
        check(self.wheels, self.constraints, "write")
        with self.wheel.open("ab") as target:
            target.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "does not match"):
            check(self.wheels, self.constraints, "verify")

    def test_manifest_tamper_fails(self) -> None:
        check(self.wheels, self.constraints, "write")
        manifest = self.wheels / "manifest.json"
        obj = json.loads(manifest.read_text())
        obj["sha256"] = "a" * 64
        manifest.write_text(json.dumps(obj), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            check(self.wheels, self.constraints, "verify")

    def test_constraint_change_fails(self) -> None:
        check(self.wheels, self.constraints, "write")
        self.constraints.write_text(
            'cryptography==50.0.3; sys_platform == "win32" and platform_machine == "ARM64"\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "expected exactly one pinned"):
            check(self.wheels, self.constraints, "verify")

    def test_extra_wheel_fails(self) -> None:
        (self.wheels / "untrusted-1-py3-none-any.whl").write_bytes(b"no")
        with self.assertRaisesRegex(ValueError, "expected exactly one pinned"):
            check(self.wheels, self.constraints, "write")

    def test_missing_wheel_fails(self) -> None:
        self.wheel.unlink()
        with self.assertRaisesRegex(ValueError, "expected exactly one pinned"):
            check(self.wheels, self.constraints, "write")

    def test_missing_manifest_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "manifest missing"):
            check(self.wheels, self.constraints, "verify")

    def test_manifest_replay_cannot_overwrite(self) -> None:
        check(self.wheels, self.constraints, "write")
        with self.assertRaisesRegex(ValueError, "refuse to overwrite"):
            check(self.wheels, self.constraints, "write")

    def test_invalid_constraints_fail(self) -> None:
        self.constraints.write_text("cryptography>=50\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly one pinned"):
            check(self.wheels, self.constraints, "write")

    def test_wrong_distribution_metadata_fails(self) -> None:
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr("other-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\\n")
        with self.assertRaisesRegex(
            ValueError, "expected cryptography package metadata"
        ):
            check(self.wheels, self.constraints, "write")

    def test_invalid_manifest_json_fails(self) -> None:
        (self.wheels / "manifest.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            check(self.wheels, self.constraints, "verify")

    def test_nonzip_wheel_fails(self) -> None:
        self.wheel.write_bytes(b"not a real wheel archive")
        with self.assertRaises(zipfile.BadZipFile):
            check(self.wheels, self.constraints, "write")

    def test_cli_success_and_hold(self) -> None:
        args = [
            "--wheel-dir",
            str(self.wheels),
            "--constraints",
            str(self.constraints),
            "--mode",
            "write",
        ]
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "PASS_RECORDED")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(main(args), 2)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "HOLD")


if __name__ == "__main__":
    unittest.main()
