"""Fail-closed SHA-256/metadata verification for same-run Windows ARM wheel fan-out.

This tool is offline; it neither downloads nor executes a wheel. The manifest
attests to the wheel built in THIS workflow run. It is not a vendor signature,
a reproducible-build claim, or a cross-run trusted checksum.

Usage: python .github/scripts/verify_arm_wheel.py --wheel-dir DIR --constraints .github/constraints.txt --mode write
       python .github/scripts/verify_arm_wheel.py --wheel-dir DIR --constraints .github/constraints.txt --mode verify
Outputs machine-readable stdout JSON; returns 0 PASS or 2 HOLD.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

_MAX_WHEEL_BYTES = 20 * 1024 * 1024
_MAX_MANIFEST_BYTES = 4096
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_VERSION = re.compile(r"cryptography==([0-9]+\.[0-9]+\.[0-9]+)\s*;")
_MANIFEST_NAME = "manifest.json"
_KEYS = frozenset(
    {"schema", "package", "version", "wheel", "size", "sha256", "constraints_sha256"}
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(65536)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _expected(constraints: Path) -> tuple[str, str]:
    if constraints.is_symlink() or not constraints.is_file():
        raise ValueError("constraints file missing or linked")
    raw = constraints.read_bytes()
    if len(raw) > 128 * 1024:
        raise ValueError("constraints file exceeds limit")
    text = raw.decode("utf-8")
    matches = []
    for line in text.splitlines():
        match = _VERSION.match(line.strip())
        if match:
            if "win32" not in line or "ARM64" not in line:
                raise ValueError("cryptography pin must target Windows ARM64")
            matches.append(match.group(1))
    if len(matches) != 1:
        raise ValueError(
            "cryptography must have exactly one pinned Windows ARM64 version"
        )
    return matches[0], hashlib.sha256(raw).hexdigest()


def _inspect_wheel(wheel_dir: Path, constraints: Path) -> dict[str, Any]:
    if wheel_dir.is_symlink() or not wheel_dir.is_dir():
        raise ValueError("wheel directory missing or linked")
    version, constraints_hash = _expected(constraints)
    expected_filename = f"cryptography-{version}-cp312-abi3-win_arm64.whl"
    wheels = list(wheel_dir.glob("*.whl"))
    if len(wheels) != 1 or wheels[0].name != expected_filename:
        raise ValueError("expected exactly one pinned cryptography Windows ARM64 wheel")
    wheel = wheels[0]
    if wheel.is_symlink() or not wheel.is_file():
        raise ValueError("wheel cannot be a symbolic link or directory")
    size = wheel.stat().st_size
    if not 0 < size <= _MAX_WHEEL_BYTES:
        raise ValueError("wheel size is outside configured safety bounds")
    with zipfile.ZipFile(wheel) as archive:
        entries = archive.infolist()
        if (
            len(entries) > 600
            or sum(item.file_size for item in entries) > 96 * 1024 * 1024
        ):
            raise ValueError("wheel expanded metadata exceeds safety budget")
        expected_metadata = f"cryptography-{version}.dist-info/WHEEL"
        if expected_metadata not in {item.filename for item in entries}:
            raise ValueError("wheel missing expected cryptography package metadata")
        if archive.testzip() is not None:
            raise ValueError("wheel ZIP member CRC validation failed")
    return {
        "schema": 1,
        "package": "cryptography",
        "version": version,
        "wheel": expected_filename,
        "size": size,
        "sha256": _sha256(wheel),
        "constraints_sha256": constraints_hash,
    }


def check(wheel_dir: Path, constraints: Path, mode: str) -> dict[str, Any]:
    """Write a producer manifest or prove the consumer received identical bytes."""
    expected = _inspect_wheel(wheel_dir, constraints)
    path = wheel_dir / _MANIFEST_NAME
    if path.is_symlink():
        raise ValueError("manifest may not be a symbolic link")
    if mode == "write":
        if path.exists():
            raise ValueError("refuse to overwrite an existing wheel manifest")
        path.write_text(json.dumps(expected, sort_keys=True) + "\n", encoding="utf-8")
    elif mode == "verify":
        if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("wheel manifest missing or over budget")
        recorded = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(recorded, dict)
            or set(recorded) != _KEYS
            or recorded.get("schema") != 1
            or not isinstance(recorded.get("sha256"), str)
            or not _SHA256.fullmatch(recorded["sha256"])
            or recorded != expected
        ):
            raise ValueError("wheel manifest does not match installed candidate")
    else:
        raise ValueError("invalid audit mode")
    return {"status": "PASS_RECORDED", "mode": mode, **expected}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--constraints", type=Path, required=True)
    parser.add_argument("--mode", choices=("write", "verify"), required=True)
    args = parser.parse_args(argv)
    try:
        result = check(args.wheel_dir, args.constraints, args.mode)
    except (
        OSError,
        ValueError,
        UnicodeError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
