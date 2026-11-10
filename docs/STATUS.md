# Project Status

Current version: **0.3.8** (see [`__about__.py`](../packages/markitdown/src/markitdown/__about__.py)).

Changes in 0.3.8:

- Public `markitdown.tools` API (#77): stable `installation_conflict`, `probe_libreoffice`, `probe_exiftool`; **provisional** `run_tool` (may change without a deprecation period). MarkItDown-Flow (`cli.py`, `exiftool.py`, `core_guard.py`) should import these instead of private `markitdown._libreoffice` / `markitdown._installation`; the private functions are unchanged and still work, but downstream should migrate before they are renamed.
- Audio (#74): with `[audio-transcription]` installed but ffmpeg/ffprobe missing, an `OSError` during transcription no longer aborts the conversion; ExifTool metadata is kept and only the transcript is skipped.
- Upstream sync (#78, microsoft/markitdown main 2026-11): ExifTool directory allow-list matches case-insensitively on Windows (upstream #2837); the SpeechRecognition endpoint is passed explicitly (upstream #2846, so `SpeechRecognition>=3.10.4` is required); README hosted-service wording (upstream #2845).
- XLS (#63): `.xls` is read with `xlrd` directly instead of through pandas, and the `[xls]` extra is now just `xlrd`. The output follows the previous pandas+xlrd pipeline (header row, NA strings, type inference, number/date display) apart from #70 below and one deliberate exception: text cells that pandas would coerce into a lossy number (digit strings beyond int64/uint64, floats overflowing to `inf`) stay text.
- XLSX/XLS (#70): empty cells render as blank (not `NaN`) and integral floats render without a trailing `.0`. This changes table output for affected workbooks.
- DOCX (#68, #69): a `Title` paragraph is emitted as a Markdown heading; a table whose first row is empty no longer produces an empty header row.
- PDF (#68): when a PDF has an outline (bookmarks), bookmark titles are promoted to Markdown headings. Conservative: titles match whole standalone lines in outline order; table rows and contents lines with page numbers are never promoted. PDFs without an outline convert as before.
- TSV (#71): `.tsv` / `text/tab-separated-values` convert as a Markdown table through the CSV converter (`.csv` behaviour is unchanged).

Output changes (may break downstream snapshot tests):

- DOCX `Title` paragraphs render as Markdown headings (`# ...`) instead of plain paragraphs (#68).
- PDFs with an outline: bookmarked standalone lines render as Markdown headings (#68).
- DOCX tables whose first row is empty no longer emit a blank header row (#69).
- `.tsv` renders as a Markdown table (#71).
- XLSX/XLS: empty cells are blank instead of `NaN`, and integral floats have no trailing `.0` (#70).
- Audio: without ffmpeg/ffprobe, conversion returns the ExifTool metadata instead of raising `OSError` (#74).

Dependency status: `pandas<4` is still capped in `markitdown` core dependencies, `[all]` and `[xlsx]`, and in `markitdown-ocr`, so default installs still pull pandas. Only XLSX reads through pandas (openpyxl engine); XLS (xlrd) and CSV/TSV (`csv` module) do not. The `[xls]` extra is `xlrd` only and therefore uncapped. The cap can be lifted once XLSX is validated on pandas 4.

Changes in 0.3.7:

- #66: document dedicated environments; detect upstream distribution collisions and loaded-version mismatches. Library import warns once; CLI exits 1 with a fresh-venv fix.
- Optional local LibreOffice converter: explicit CLI/Python/env opt-in for DOC/PPT/RTF/HWP < 5.0, native DOCX/PPTX extraction, DERIVED provenance, isolated profile, bounded execution, whole-tree termination and temporary cleanup. Ported from MarkItDown-Flow `engine/src/mdflow/preconvert.py` (R33/R34 in its `docs/renewal/01_DECISIONS.md`).

## Release recipe

On `main`, tag the version as `vX.Y.Z`. The [`release.yml`](../.github/workflows/release.yml) workflow builds distributions, verifies the tag matches the package version, and publishes to PyPI using trusted publishing; it also attaches distributions to the GitHub Release.

The release workflow requires a successful [`tests.yml`](../.github/workflows/tests.yml) **push run on `main`** for the tagged commit (`gate` job; PR runs skip `tests-arm` and manual runs do not count); if only infra-flaky jobs failed, rerun them before tagging.
