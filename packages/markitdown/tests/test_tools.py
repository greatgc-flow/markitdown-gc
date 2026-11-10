"""Public markitdown.tools API: probes, bounded runner, installation check."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from markitdown import tools

KEYS = {"path", "version", "status", "detail"}


def _fake_tool(directory: Path, name: str, stdout: str, code: int = 0) -> Path:
    """A real executable (resolvable by shutil.which) that prints ``stdout``."""
    if os.name == "nt":
        script = directory / f"{name}.cmd"
        script.write_text(f"@echo {stdout}\r\n@exit /b {code}\r\n", encoding="ascii")
    else:
        script = directory / name
        script.write_text(f"#!/bin/sh\necho '{stdout}'\nexit {code}\n")
        script.chmod(0o755)
    return script


def test_public_surface_and_private_delegates_remain():
    from markitdown import _installation, _libreoffice

    assert sorted(tools.__all__) == [
        "installation_conflict",
        "probe_exiftool",
        "probe_libreoffice",
        "run_tool",
    ]
    assert tools.installation_conflict is _installation.installation_conflict
    # Private names stay importable for existing callers during migration.
    for name in ("_execute", "_resolve", "_write_profile"):
        assert callable(getattr(_libreoffice, name))


def test_run_tool_returns_completed_process_and_cleans_up(monkeypatch):
    script = (
        "import os,sys; print(os.getcwd()); "
        "print(os.environ.get('MARKITDOWN_TOOLS_SECRET')); sys.stdout.flush(); "
        "sys.stderr.write('err\\n'); sys.exit(3)"
    )
    monkeypatch.setenv("MARKITDOWN_TOOLS_SECRET", "leak")
    result = tools.run_tool([sys.executable, "-c", script], timeout=30)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 3 and result.stderr is None
    cwd, secret, err = result.stdout.splitlines()
    assert secret == "None"  # environment is allowlisted
    assert err == "err"  # stderr merged into stdout
    assert not Path(cwd).exists()  # default cwd is a removed temporary directory
    with pytest.raises(subprocess.CalledProcessError):
        result.check_returncode()


def test_run_tool_honours_cwd_and_pathlike(tmp_path):
    result = tools.run_tool(
        [Path(sys.executable), "-c", "import os; print(os.getcwd())"],
        timeout=30,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()
    assert list(tmp_path.iterdir()) == []  # capture file lives elsewhere


def test_run_tool_timeout_and_validation():
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(TimeoutError):
        tools.run_tool(sleeper, timeout=1)
    with pytest.raises(ValueError):
        tools.run_tool([], timeout=1)
    for timeout in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite and positive"):
            tools.run_tool([sys.executable, "-V"], timeout=timeout)
    with pytest.raises(OSError):
        tools.run_tool(["no-such-tool-markitdown-gc"], timeout=5)


def test_probe_libreoffice_ok(monkeypatch):
    from markitdown import _libreoffice as lo

    seen = []
    monkeypatch.setattr(lo, "_resolve", lambda path: seen.append(path) or "/x/soffice")

    def command(executable, profile):
        assert (profile / "user/registrymodifications.xcu").is_file()
        return [sys.executable, "-c", "print('LibreOffice 25.2.1.2 abc')"]

    monkeypatch.setattr(lo, "_command", command)
    monkeypatch.setenv("SOFFICE_PATH", "from-env")
    result = tools.probe_libreoffice()
    assert result == {
        "path": "/x/soffice",
        "version": "25.2.1.2",
        "status": "ok",
        "detail": None,
    }
    assert seen == ["from-env"]
    json.dumps(result)


def test_probe_libreoffice_missing_and_errors(monkeypatch):
    from markitdown import _libreoffice as lo

    missing = tools.probe_libreoffice("no-such-soffice-markitdown-gc")
    assert set(missing) == KEYS
    assert missing["status"] == "missing" and "SOFFICE_PATH" in missing["detail"]

    monkeypatch.setattr(lo, "_resolve", lambda path: "/x/soffice")
    monkeypatch.setattr(
        lo, "_command", lambda e, p: [sys.executable, "-c", "print('garbage')"]
    )
    unparsed = tools.probe_libreoffice("x")
    assert unparsed["status"] == "error" and unparsed["path"] == "/x/soffice"

    def fail(*args, **kwargs):
        raise TimeoutError("LibreOffice deadline/output limit exceeded")

    monkeypatch.setattr(lo, "_execute", fail)
    timed_out = tools.probe_libreoffice("x", timeout=1)
    assert timed_out["status"] == "error" and "deadline" in timed_out["detail"]
    with pytest.raises(ValueError):
        tools.probe_libreoffice("x", timeout=0)


@pytest.mark.parametrize(
    "stdout,code,status,version",
    [
        ("13.10", 0, "ok", "13.10"),
        ("12.24", 0, "ok", "12.24"),
        ("12.23", 0, "outdated", "12.23"),
        ("garbage", 0, "error", None),
        ("13.10", 2, "error", None),
    ],
)
def test_probe_exiftool(tmp_path, stdout, code, status, version):
    fake = _fake_tool(tmp_path, "exiftool", stdout, code)
    result = tools.probe_exiftool(fake)
    assert set(result) == KEYS
    assert result["status"] == status
    assert result["version"] == version
    assert Path(result["path"]).resolve() == fake.resolve()
    assert (result["detail"] is None) == (status == "ok")


def test_probe_exiftool_discovery_matches_markitdown(tmp_path, monkeypatch):
    from markitdown import MarkItDown

    monkeypatch.setenv("EXIFTOOL_PATH", "")  # empty disables, as in MarkItDown
    assert MarkItDown()._exiftool_path == ""
    result = tools.probe_exiftool()
    assert result["status"] == "missing" and "EXIFTOOL_PATH" in result["detail"]

    fake = _fake_tool(tmp_path, "exiftool", "13.10")
    monkeypatch.setenv("EXIFTOOL_PATH", str(fake))
    assert MarkItDown()._exiftool_path == str(fake)
    assert tools.probe_exiftool()["status"] == "ok"

    assert tools.probe_exiftool(tmp_path / "nope")["status"] == "missing"
