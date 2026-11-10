# Project Status

Current version: **0.3.7** (see [`__about__.py`](../packages/markitdown/src/markitdown/__about__.py)).

Changes in 0.3.7:

- #66: document dedicated environments; detect upstream distribution collisions and loaded-version mismatches. Library import warns once; CLI exits 1 with a fresh-venv fix.
- Optional local LibreOffice converter: explicit CLI/Python/env opt-in for DOC/PPT/RTF/HWP < 5.0, native DOCX/PPTX extraction, DERIVED provenance, isolated profile, bounded execution, whole-tree termination and temporary cleanup. Ported from MarkItDown-Flow `engine/src/mdflow/preconvert.py` (R33/R34 in its `docs/renewal/01_DECISIONS.md`).

Unreleased:

- Public `markitdown.tools` API (stable `installation_conflict`, `probe_libreoffice`, `probe_exiftool`; provisional `run_tool`) so MarkItDown-Flow (`cli.py`, `exiftool.py`, `core_guard.py`) stops importing private `markitdown._libreoffice` / `markitdown._installation`. The private functions are unchanged and still work; downstream should migrate before they are renamed.

## Release recipe

On `main`, tag the version as `vX.Y.Z`. The [`release.yml`](../.github/workflows/release.yml) workflow builds distributions, verifies the tag matches the package version, and publishes to PyPI using trusted publishing; it also attaches distributions to the GitHub Release.

The release workflow requires a successful [`tests.yml`](../.github/workflows/tests.yml) **push run on `main`** for the tagged commit (`gate` job; PR runs skip `tests-arm` and manual runs do not count); if only infra-flaky jobs failed, rerun them before tagging.
