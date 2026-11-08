"""Borrow the HWP parser with guaranteed temporary-file cleanup."""

import hashlib
import io
import os
import re
import tempfile
import zipfile
import zlib
from contextlib import contextmanager

from ._media import save_media, validate_members


@contextmanager
def hwp_reader(data, suffix):
    import hwp_hwpx_parser as parser

    temporary = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        with temporary:
            temporary.write(data)
        reader = parser.read(temporary.name)
        try:
            yield reader
        finally:
            reader.close()
    finally:
        os.unlink(temporary.name)


class HwpMediaLimit(ValueError):
    """HWP5 media inspection exhausted its byte or reference budget."""


def _iter_hwp5_images(data, max_bytes, max_images, stored):
    """Inspect one OLE stream at a time, bounding stored and inflated bytes."""
    import olefile
    from hwp_hwpx_parser.models import detect_image_format

    remaining = max_bytes
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        paths = (
            path
            for path in sorted(ole.listdir())
            if len(path) == 2 and path[0] == "BinData"
        )
        for index, path in enumerate(paths):
            if max_images is not None and index >= max_images:
                raise HwpMediaLimit()
            # olefile.openstream itself materializes the stored stream.
            size = ole.get_size(path)
            if size > remaining:
                raise HwpMediaLimit()
            with ole.openstream(path) as stream:
                raw = stream.read(size)
            remaining -= len(raw)
            payload = raw
            try:
                inflater = zlib.decompressobj(-15)
                decoded = inflater.decompress(raw, remaining + 1)
                if len(decoded) > remaining:
                    raise HwpMediaLimit()
                remaining -= len(decoded)
                # Match the parser's fallback for incomplete/non-deflate streams.
                if inflater.eof:
                    payload = decoded
            except zlib.error:
                pass
            ext = detect_image_format(payload)
            supported = ext != "unknown"
            if not supported:
                payload = raw
                ext = path[-1].rsplit(".", 1)[-1].lower()
            name = "/".join(path)
            stored[name] = (len(raw), hashlib.sha256(raw).hexdigest())
            yield name, payload, ext, supported


def hwp_images(data, suffix, reader, *, max_bytes=None, max_images=None, stored=None):
    """Return image payloads with stable member/stream identities, including omissions."""
    if suffix == ".hwp" and max_bytes is not None:
        # Bounded callers must never invoke the parser's eager get_images().
        if reader.is_encrypted:
            raise ValueError("Encrypted HWP5 is not inspected")
        return _iter_hwp5_images(
            data, max_bytes, max_images, stored if stored is not None else {}
        )
    images = reader.get_images()
    if suffix == ".hwpx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            validate_members(archive)
            returned = {i.filename: i for i in images}
            rows = []
            for name in sorted(archive.namelist()):
                if not name.startswith("BinData/") or name.endswith("/"):
                    continue
                ext = name.rsplit(".", 1)[-1].lower()
                from .ocr_triage import _IMAGE_EXTENSIONS

                if ext not in _IMAGE_EXTENSIONS:
                    continue
                image = returned.get(name.rsplit("/", 1)[-1])
                rows.append((name, archive.read(name), ext, image is not None))
            return rows
    rows = [("BinData/" + i.filename, i.data, i.format.lower(), True) for i in images]
    # The parser omits unknown encodings. Inspect stored OLE names as well.
    import olefile

    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            known = {r[0] for r in rows}
            for path in sorted(ole.listdir()):
                name = "/".join(path)
                if len(path) == 2 and path[0] == "BinData" and name not in known:
                    rows.append(
                        (
                            name,
                            ole.openstream(path).read(),
                            path[-1].rsplit(".", 1)[-1].lower(),
                            False,
                        )
                    )
    except (OSError, ValueError):
        # Parser payloads remain usable when stored-stream inspection is unavailable.
        pass
    return sorted(rows, key=lambda row: row[0])


def extract_hwp_markdown(markdown, rows, **options):
    links = {}
    for name, payload, ext, supported in rows:
        if supported:
            path = save_media(payload, ext, **options)
            link = f"![Image]({path})"
        else:
            label = ext.upper() if re.fullmatch("[a-z0-9]{1,12}", ext) else "Image"
            link = f"![{label} omitted]"
        basename = name.rsplit("/", 1)[-1]
        for identity in (basename, basename.rsplit(".", 1)[0]):
            if identity in links and links[identity] != link:
                raise ValueError("Unsafe or colliding image reference")
            links[identity] = link
    used = set()

    def replace(match):
        identity = match.group(1)
        link = links.get(identity)
        if link is None:
            return "![Image omitted]"
        used.add(link)
        return link

    markdown = re.sub(r"\[IMAGE: ([^\]\r\n]+)\]", replace, markdown)
    # Orphaned/skipped media remain visible, never silently disappear.
    for link in dict.fromkeys(links.values()):
        if link not in used:
            markdown += "\n\n" + link
    return markdown


def has_hwp_images(data, suffix):
    """Observe package structures, never search converted/document text."""
    try:
        if suffix == ".hwpx":
            from defusedxml import ElementTree

            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if any(
                    n.startswith("BinData/") and not n.endswith("/")
                    for n in archive.namelist()
                ):
                    return True
                for name in archive.namelist():
                    if re.fullmatch(r"Contents/section\d+\.xml", name):
                        root = ElementTree.fromstring(archive.read(name))
                        if any(
                            e.tag.rsplit("}", 1)[-1] in ("pic", "img")
                            for e in root.iter()
                        ):
                            return True
        else:
            import olefile

            with olefile.OleFileIO(io.BytesIO(data)) as ole:
                return any(p[0] == "BinData" for p in ole.listdir())
    except (OSError, ValueError, zipfile.BadZipFile):
        pass
    return False
