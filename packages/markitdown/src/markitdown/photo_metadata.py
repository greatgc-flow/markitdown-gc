"""Read only caller-selected photo fields from immutable local image bytes."""

import json
from pathlib import Path
import tempfile

from . import tools
from .ocr_triage import MAX_BYTES

PHOTO_FIELDS = (
    "DateTimeOriginal",
    "ImageWidth",
    "ImageHeight",
    "Make",
    "Model",
    "GPSLatitude",
    "GPSLongitude",
    "Artist",
    "SerialNumber",
)


def read_photo_info(source: bytes, *, fields, executable, timeout=3.0):
    """Return status and selected fields. No file changes, OCR, remote requests or caching."""
    selected = list(dict.fromkeys(fields))
    if not selected or any(field not in PHOTO_FIELDS for field in selected):
        raise ValueError("Choose supported metadata fields")
    if not isinstance(source, bytes) or len(source) > MAX_BYTES:
        raise ValueError("Expected bounded image bytes")
    if source.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = ".png"
    elif source.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
    else:
        return {"status": "unsupported", "fields": {}}
    if not executable or not Path(executable).is_absolute():
        return {"status": "not_found", "fields": {}}
    probe = tools.probe_exiftool(executable, timeout=timeout)
    if probe["status"] != "ok":
        status = {"missing": "not_found", "outdated": "too_old"}.get(
            probe["status"], "unusable"
        )
        return {"status": status, "fields": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="markitdown-photo-") as folder:
            path = Path(folder) / ("photo" + extension)
            path.write_bytes(source)
            result = tools.run_tool(
                [probe["path"], "-j", *["-" + f for f in selected], str(path)],
                cwd=folder,
                timeout=timeout,
            )
        if result.returncode:
            return {"status": "unusable", "fields": {}}
        record = json.loads(result.stdout)[0]
        found = {
            f: record[f]
            for f in selected
            if isinstance(record.get(f), (str, int, float))
        }
        return {"status": "ok" if found else "no_metadata", "fields": found}
    except (OSError, ValueError, IndexError, KeyError, AttributeError):
        return {"status": "unusable", "fields": {}}
