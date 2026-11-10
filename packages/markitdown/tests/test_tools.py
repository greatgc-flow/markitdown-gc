"""Public markitdown.tools API: probes, bounded runner, installation check."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from markitdown import tools

KEYS = {"path", "version", "status", "detail"}


def _fake_tool(
    directory: Path, name: str, stdout: str, code: int = 0, stderr: str = ""
) -> Path:
    """A real executable (resolvable by shutil.which) that prints ``stdout``."""
    if os.name == "nt":
        script = directory / f"{name}.cmd"
        warn = f"@echo {stderr} 1>&2\r\n" if stderr else ""
        script.write_text(
            f"{warn}@echo {stdout}\r\n@exit /b {code}\r\n", encoding="ascii"
        )
    else:
        script = directory / name
        warn = f"echo '{stderr}' >&2\n" if stderr else ""
        script.write_text(f"#!/bin/sh\n{warn}echo '{stdout}'\nexit {code}\n")
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
    for name in ("_capture", "_execute", "_resolve", "_write_profile"):
        assert callable(getattr(_libreoffice, name))
    assert not hasattr(tools, "PathLike")  # type alias is private
    assert "provisional" in tools.run_tool.__doc__.lower()


def test_private_capture_keeps_str_shape(tmp_path):
    from markitdown import _libreoffice as lo

    ok = [sys.executable, "-c", "print('hi')"]
    out = lo._capture(ok, cwd=tmp_path, timeout_s=30, output=tmp_path / "a.log")
    assert out.strip() == "hi"
    with pytest.raises(OSError, match="LibreOffice failed"):
        lo._capture(
            [sys.executable, "-c", "raise SystemExit(1)"],
            cwd=tmp_path,
            timeout_s=30,
            output=tmp_path / "b.log",
        )


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


def test_run_tool_output_limit_applies_after_fast_exit():
    # Exits before the first poll can see the file grow: must not truncate.
    burst = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 70000)"]
    with pytest.raises(TimeoutError, match="output limit"):
        tools.run_tool(burst, timeout=30)


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


def test_probe_exiftool_ignores_stderr_warning(tmp_path):
    # e.g. Perl's locale warning; the version is the last line of output.
    warning = "perl: warning: Setting locale failed."
    fake = _fake_tool(tmp_path, "exiftool", "13.10", stderr=warning)
    result = tools.probe_exiftool(fake)
    assert result["status"] == "ok" and result["version"] == "13.10"


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_probe_exiftool_discovery_is_case_insensitive(tmp_path, monkeypatch):
    # PATH spells C:\WINDOWS\system32 while the trusted list has C:\Windows\System32.
    from markitdown import MarkItDown
    from markitdown.converters import _exiftool

    fake = _fake_tool(tmp_path, "exiftool", "13.10")
    which = _exiftool.shutil.which
    monkeypatch.delenv("EXIFTOOL_PATH", raising=False)
    monkeypatch.setattr(_exiftool, "_TRUSTED_DIRS", (str(tmp_path).upper(),))
    monkeypatch.setattr(
        _exiftool.shutil,
        "which",
        lambda cmd, *a, **k: str(fake) if cmd == "exiftool" else which(cmd, *a, **k),
    )
    assert MarkItDown()._exiftool_path == str(fake)
    assert tools.probe_exiftool()["status"] == "ok"


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
