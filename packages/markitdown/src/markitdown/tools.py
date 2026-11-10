"""Stable public API for external-tool probes and installation checks.

Everything in ``__all__`` is a supported interface: names, parameters and the
keys of returned dictionaries change only with a deprecation period. Modules
whose names start with an underscore (``markitdown._libreoffice``,
``markitdown._installation`` ...) remain private and may change at any time.

Probe results are plain JSON-serializable dictionaries with the keys:

- ``path``: absolute executable path, or ``None`` when it was not found.
- ``version``: version string reported by the tool, or ``None``.
- ``status``: ``"ok"``, ``"missing"``, ``"error"``, or (exiftool only)
  ``"outdated"`` for versions below 12.24 (CVE-2021-22204).
- ``detail``: human-readable reason when ``status`` is not ``"ok"``, else ``None``.

Probes never raise for a missing or broken tool; they report it in ``status``.
"""

import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Optional, Sequence, Union

from . import _libreoffice
from ._installation import installation_conflict
from .converters import _exiftool

__all__ = [
    "installation_conflict",
    "probe_exiftool",
    "probe_libreoffice",
    "run_tool",
]

PathLike = Union[str, os.PathLike]


def _result(path=None, version=None, status="ok", detail=None) -> dict:
    return {"path": path, "version": version, "status": status, "detail": detail}


def _check_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")


def run_tool(
    argv: Sequence[PathLike],
    *,
    timeout: float,
    cwd: Optional[PathLike] = None,
) -> "subprocess.CompletedProcess[str]":
    """Run a local tool with the same containment as the LibreOffice converter.

    - stdin is closed; stdout and stderr are merged into ``stdout`` (UTF-8,
      undecodable bytes replaced) and capped at 64 KiB; ``stderr`` is ``None``.
    - The environment is reduced to an allowlist (PATH, SYSTEMROOT, TEMP, HOME,
      LANG ...); secrets in other variables are not inherited.
    - The whole process tree is killed when the call returns (Windows job
      object / POSIX process group), so no descendant outlives it.
    - ``cwd`` defaults to a fresh temporary directory that is removed afterwards.

    A non-zero exit status is returned, not raised; call
    ``check_returncode()`` on the result to raise ``CalledProcessError``.
    Raises ``TimeoutError`` when ``timeout`` seconds elapse or the output cap is
    exceeded, ``ValueError`` for an empty ``argv`` or a non-finite/non-positive
    ``timeout``, and ``OSError`` when the executable cannot be started.
    """
    args = [os.fspath(arg) for arg in argv]
    if not args:
        raise ValueError("argv must not be empty")
    _check_timeout(timeout)
    with tempfile.TemporaryDirectory(prefix="markitdown-tool-") as directory:
        work = Path(directory).resolve()
        returncode, output = _libreoffice._run(
            args,
            cwd=Path(cwd) if cwd is not None else work,
            timeout_s=timeout,
            output=work / "output.log",
            label=Path(args[0]).name or "tool",
        )
    return subprocess.CompletedProcess(args, returncode, output, None)


def probe_libreoffice(path: Optional[PathLike] = None, *, timeout: float = 60) -> dict:
    """Locate LibreOffice and read its version the way the converter would.

    ``path`` defaults to ``SOFFICE_PATH``, else ``soffice`` on PATH (the same
    rule as ``markitdown-gc --libreoffice``). On Windows ``soffice.exe`` resolves
    to the console launcher ``soffice.com``. ``soffice --version`` runs headless
    with a throwaway, macro-disabled profile and the given timeout.
    """
    _check_timeout(timeout)
    requested = path if path is not None else os.getenv("SOFFICE_PATH") or "soffice"
    executable = _libreoffice._resolve(requested)
    if not executable:
        return _result(
            status="missing",
            detail=f"LibreOffice not found: {os.fspath(requested)!r}. "
            "Install it locally and set SOFFICE_PATH.",
        )
    try:
        with tempfile.TemporaryDirectory(prefix="markitdown-libreoffice-") as tmp:
            work = Path(tmp).resolve()
            profile = work / "profile"
            _libreoffice._write_profile(profile)
            output = _libreoffice._execute(
                [*_libreoffice._command(executable, profile), "--version"],
                cwd=work,
                timeout_s=timeout,
                output=work / "version.log",
            )
    except OSError as exc:  # includes TimeoutError
        return _result(executable, status="error", detail=str(exc))
    match = _libreoffice._VERSION.search(output)
    if not match:
        return _result(
            executable, status="error", detail="LibreOffice version unavailable"
        )
    return _result(executable, match[1])


def probe_exiftool(path: Optional[PathLike] = None, *, timeout: float = 10) -> dict:
    """Locate exiftool and check that it is at least 12.24.

    ``path`` defaults to the rule ``MarkItDown()`` uses: ``EXIFTOOL_PATH``
    (an empty value disables exiftool), else ``exiftool`` on PATH only when it
    lives in a well-known system directory. ``exiftool -ver`` runs via
    :func:`run_tool` with the given timeout.
    """
    _check_timeout(timeout)
    requested = path if path is not None else _exiftool._discover_exiftool()
    found = shutil.which(os.fspath(requested)) if requested else None
    if not found:
        detail = (
            f"exiftool not found: {os.fspath(requested)!r}"
            if requested
            else "exiftool not configured (set EXIFTOOL_PATH or pass a path)"
        )
        return _result(status="missing", detail=detail)
    executable = os.path.abspath(found)
    try:
        completed = run_tool([executable, "-ver"], timeout=timeout)
    except OSError as exc:  # includes TimeoutError
        return _result(executable, status="error", detail=str(exc))
    version = completed.stdout.strip()
    if completed.returncode:
        return _result(
            executable,
            status="error",
            detail=f"exiftool -ver exited with {completed.returncode}",
        )
    try:
        parsed = _exiftool._parse_version(version)
    except ValueError:
        return _result(
            executable, status="error", detail=f"unparseable version {version!r}"
        )
    if parsed < _exiftool.MIN_VERSION:
        return _result(
            executable,
            version,
            "outdated",
            f"exiftool {version} is vulnerable to CVE-2021-22204; upgrade to >= 12.24",
        )
    return _result(executable, version)
