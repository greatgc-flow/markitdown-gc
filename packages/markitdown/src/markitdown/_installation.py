"""Detect distributions which overwrite the shared markitdown package."""

from importlib import metadata
from typing import Optional
import warnings

from .__about__ import __version__


def installation_conflict() -> Optional[str]:
    try:
        installed = metadata.version("markitdown-gc")
    except metadata.PackageNotFoundError:
        return None  # A source checkout without installed distribution metadata.
    try:
        upstream = metadata.version("markitdown")
    except metadata.PackageNotFoundError:
        upstream = None
    if upstream is None and installed == __version__:
        return None
    reason = (
        f"markitdown {upstream} and markitdown-gc {installed} are co-installed"
        if upstream is not None
        else f"markitdown-gc metadata version {installed} differs from loaded version {__version__}"
    )
    return (
        f"Conflicting markitdown installation: {reason}. Shared package files may be overwritten. "
        "Never co-install upstream markitdown or plugins that depend on it with markitdown-gc. "
        "Use a dedicated virtual environment. Fix: python -m venv .venv-markitdown-gc; "
        "then .venv-markitdown-gc/Scripts/python -m pip install markitdown-gc (Windows), "
        "or .venv-markitdown-gc/bin/python -m pip install markitdown-gc (macOS/Linux)."
    )


_warned = False


def warn_if_conflicting() -> None:
    global _warned
    message = installation_conflict()
    if message and not _warned:
        _warned = True
        warnings.warn(message, RuntimeWarning, stacklevel=2)
