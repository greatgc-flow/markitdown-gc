"""Bounded, local PDF inspection for document routing and text-only handoff.

The four surface categories describe placed objects inside the visible crop.
Unresolved clipping, compositing and form contents are deliberately unknown.
"""

import math
import re
import unicodedata
from pathlib import Path

import pypdfium2 as pdfium
from pypdfium2 import raw

from .ocr_triage import MAX_BYTES, MAX_PAGES

_CATEGORIES = ("text", "image", "blank", "other")
_MAX_OBJECTS = 10000
_MAX_FRAGMENTS = 4096
_MAX_TEXT = 4000000


def _intersection(a, b):
    box = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return box if box[2] > box[0] and box[3] > box[1] else None


def _area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def _paint(tiles, box, kind):
    """Cut only the covered tiles. Later PDF objects cover earlier ones."""
    result = []
    for rect, prior in tiles:
        hit = _intersection(rect, box)
        if not hit or prior == kind:
            result.append((rect, prior))
            continue
        x, y, r, t = rect
        l, b, h, u = hit
        for part in ((x, y, l, t), (h, y, r, t), (l, y, h, b), (l, u, h, t)):
            if _area(part) > 0:
                result.append((part, prior))
        result.append((hit, kind))
    return result


def _usable(text):
    return bool(text.strip()) and not (
        re.search(r"\(cid:\d+\)|\ufffd", text)
        or any(unicodedata.category(c) in {"Co", "Cs"} for c in text)
    )


def _page(page, number, measure, include_text):
    crop = page.get_bbox()
    if not all(map(math.isfinite, crop)) or crop[2] <= crop[0] or crop[3] <= crop[1]:
        raise ValueError("Invalid PDF crop box")
    unclear = raw.FPDFPage_GetAnnotCount(page) != 0
    annotations = unclear
    covers = []
    incomplete = False
    tiles = [(crop, "blank")] if measure and not unclear else None
    blocks = []
    images = []
    uncertain_text = False
    work = 0
    text_size = 0
    textpage = page.get_textpage()
    try:
        for order, obj in enumerate(page.get_objects(max_depth=1, textpage=textpage)):
            if order >= _MAX_OBJECTS:
                unclear, incomplete, tiles = True, True, None
                break
            bounds = obj.get_bounds()
            if not all(map(math.isfinite, bounds)):
                unclear, incomplete, tiles = True, True, None
                continue
            box = _intersection(crop, bounds)
            if box is None:
                continue
            kind = "other"
            clip = raw.FPDFPageObj_GetClipPath(obj)
            complex_paint = raw.FPDFPageObj_HasTransparency(obj) or (
                clip and raw.FPDFClipPath_CountPaths(clip) > 0
            )
            if obj.type == raw.FPDF_PAGEOBJ_TEXT:
                mode = raw.FPDFTextObj_GetTextRenderMode(obj)
                if mode == raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                    uncertain_text = True
                    continue
                if mode in (0, 1, 2) and not complex_paint:
                    value = obj.extract()
                    if _usable(value) and box == bounds:
                        text_size += len(value)
                        if text_size > _MAX_TEXT:
                            raise ValueError("PDF text exceeds inspection limit")
                        blocks.append((box, value, order))
                        kind = "text"
                    elif value.strip():
                        uncertain_text = True
            elif obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                if not complex_paint:
                    kind = "image"
                images.append((box, order))
            if kind != "text":
                covers.append((box, order))
            if kind == "other":
                unclear = True
            if tiles is not None:
                work += len(tiles)
                tiles = _paint(tiles, box, kind) if work < 1000000 else None
                if tiles is not None and len(tiles) > _MAX_FRAGMENTS:
                    tiles = None
    finally:
        textpage.close()
    # Never export words occluded by a later image. Overlapping layers need review.
    if annotations or incomplete or len(blocks) * len(covers) > 1000000:
        return {
            "number": number,
            "role": "undetermined",
            **({"text": ""} if include_text else {}),
        }, None
    visible = [
        entry
        for entry in blocks
        if not any(
            order > entry[2] and _intersection(entry[0], image)
            for image, order in covers
        )
    ]
    if len(visible) != len(blocks):
        uncertain_text = True
    # A standalone scan establishes exclusion. Small embedded images can be logos.
    # Mark their content unresolved instead of passing them as complete direct text.
    largest_image = max((_area(box) for box, _ in images), default=0)
    # Match Core scan_like evidence using both sparse text and a material raster.
    chars = sum(not c.isspace() for _, value, _ in visible for c in value)
    scan = bool(images) and chars < 30 and largest_image >= _area(crop) * 0.5
    if scan and not (uncertain_text or unclear):
        role = "mixed" if visible else "ocr"
    elif unclear or uncertain_text or images:
        role = "undetermined"
    else:
        role = "text" if visible else "blank"
    row = {"number": number, "role": role}
    if include_text:
        row["text"] = "\n".join(text for _, text, _ in visible)
    areas = None
    if tiles is not None:
        areas = dict.fromkeys(_CATEGORIES, 0.0)
        for rect, kind in tiles:
            areas[kind] += _area(rect)
    return row, areas


def inspect_pdf(
    source, *, measure=False, include_text=False, stop_at_ocr=False, max_pages=MAX_PAGES
):
    """Inspect a path or immutable PDF bytes; never render, transcribe or persist."""
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError("Invalid PDF page limit")
    if isinstance(source, (Path, str)):
        with open(source, "rb") as stream:
            source = stream.read(MAX_BYTES + 1)
    if not isinstance(source, bytes) or len(source) > MAX_BYTES:
        raise ValueError("Invalid or oversized PDF")
    pages = []
    totals = dict.fromkeys(_CATEGORIES, 0.0)
    measured = 0
    characters = 0
    try:
        with pdfium.PdfDocument(source) as document:
            if raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
                raise ValueError("Protected PDF")
            total = len(document)
            attachments = raw.FPDFDoc_GetAttachmentCount(document) != 0
            for index in range(min(total, max_pages)):
                page = document[index]
                try:
                    row, areas = _page(page, index + 1, measure, include_text)
                finally:
                    page.close()
                pages.append(row)
                characters += len(row.get("text", ""))
                if characters > _MAX_TEXT:
                    raise ValueError("Document text exceeds inspection limit")
                if areas is not None:
                    measured += 1
                    for key, value in areas.items():
                        totals[key] += value
                if stop_at_ocr and row["role"] in {"ocr", "mixed"}:
                    break
    except pdfium.PdfiumError as exc:
        raise ValueError("PDF could not be inspected") from exc
    roles = {page["role"] for page in pages}
    complete = len(pages) == total
    if roles & {"ocr", "mixed"}:
        reason = "ocr_required"
    elif not complete or attachments or "undetermined" in roles:
        reason = "undetermined"
    elif "text" in roles:
        reason = "direct"
    else:
        reason = "empty"
    denominator = sum(totals.values())
    return {
        "schema_version": 1,
        "reason": reason,
        "complete": complete,
        "total_pages": total,
        "inspected_pages": len(pages),
        "measured_pages": measured,
        "pages": pages,
        "area": totals if measured else None,
        "area_percent": (
            {k: round(100 * v / denominator, 1) for k, v in totals.items()}
            if denominator
            else None
        ),
    }
