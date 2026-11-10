"""Small synthetic PDFs: stdlib writer, plus an optional Pillow scan helper."""

import io


def pdf(pages):
    """Pages specify text, images (x, y, width, height), rotate, vectors, cid, inline."""
    objects = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]

    def add(value):
        objects.append(value)
        return len(objects)

    def stream(attrs, data):
        return (
            b"<< "
            + attrs
            + f" /Length {len(data)} >>\nstream\n".encode()
            + data
            + b"\nendstream"
        )

    image = add(
        stream(
            b"/Type /XObject /Subtype /Image /Width 100 /Height 100 /ColorSpace /DeviceRGB /BitsPerComponent 8",
            b"\xff\xff\xff" * 10000,
        )
    )
    children = []
    for page in pages:
        font = 3
        if page.get("cid"):
            descendant = add(
                b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Synthetic /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>"
            )
            font = add(
                f"<< /Type /Font /Subtype /Type0 /BaseFont /Synthetic /Encoding /Identity-H /DescendantFonts [{descendant} 0 R] >>".encode()
            )
        text = (
            page.get("text", "")
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        commands = [f"BT /F1 1 Tf 1 50 Td ({text}) Tj ET"] if text else []
        if page.get("cid"):
            commands.append("BT /F1 1 Tf 1 50 Td <00010002000300040005> Tj ET")
        for x, y, w, h in page.get("images", []):
            commands.append(f"q {w} 0 0 {h} {x} {y} cm /Im1 Do Q")
        for matrix in page.get("matrices", []):
            commands.append(f"q {' '.join(map(str, matrix))} cm /Im1 Do Q")
        if page.get("inline"):
            commands.append(
                "q 100 0 0 100 0 0 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID abc EI Q"
            )
        commands.extend("0 0 1 1 re S" for _ in range(page.get("vectors", 0)))
        content = add(stream(b"", "\n".join(commands).encode("ascii")))
        children.append(
            add(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Rotate {page.get('rotate', 0)} /Resources << /Font << /F1 {font} 0 R >> /XObject << /Im1 {image} 0 R >> >> /Contents {content} 0 R >>".encode()
            )
        )
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    return _serialize(objects, children)


def _escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_pdf(pages, outline=(), dests=None):
    """Letter pages of ``(font_size, text)`` lines plus an optional bookmark tree.

    A line's text may be a list of cells, placed in columns on one baseline.
    ``outline`` holds ``(title, target, children)`` tuples. A str title is
    written as UTF-16BE, bytes verbatim. ``target`` is a page index, a
    named-destination string, ``("A", index_or_name)`` for a GoTo action, or
    None for no destination. ``dests`` maps names to page indexes.
    """
    objects = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]

    def add(value=b""):
        objects.append(value)
        return len(objects)

    page_ids = []
    for lines in pages:
        y = 720
        commands = []
        for size, text in lines:
            cells = text if isinstance(text, list) else [text]
            for column, cell in enumerate(cells):
                commands.append(
                    f"BT /F1 {size} Tf {72 + 180 * column} {y} Td ({_escape(cell)}) Tj ET"
                )
            y -= size * 3
        data = "\n".join(commands).encode("latin-1")
        content = add(
            f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"
        )
        page_ids.append(
            add(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {content} 0 R >>".encode()
            )
        )

    def dest(target):
        if isinstance(target, str):
            return f"({_escape(target)})"
        return f"[{page_ids[target]} 0 R /Fit]"

    def build(items, parent):
        ids = [add() for _ in items]
        for n, (obj, (title, target, kids)) in enumerate(zip(ids, items)):
            if isinstance(title, str):
                title = b"\xfe\xff" + title.encode("utf-16-be")
            fields = [f"/Title <{title.hex()}>", f"/Parent {parent} 0 R"]
            if isinstance(target, tuple):
                fields.append(f"/A << /S /GoTo /D {dest(target[1])} >>")
            elif target is not None:
                fields.append(f"/Dest {dest(target)}")
            if n:
                fields.append(f"/Prev {ids[n - 1]} 0 R")
            if n + 1 < len(ids):
                fields.append(f"/Next {ids[n + 1]} 0 R")
            if kids:
                first, last = build(kids, obj)
                fields.append(f"/First {first} 0 R /Last {last} 0 R /Count {len(kids)}")
            objects[obj - 1] = ("<< " + " ".join(fields) + " >>").encode()
        return ids[0], ids[-1]

    catalog = "<< /Type /Catalog /Pages 2 0 R"
    if outline:
        root = add()
        first, last = build(outline, root)
        objects[root - 1] = (
            f"<< /Type /Outlines /First {first} 0 R /Last {last} 0 R /Count {len(outline)} >>".encode()
        )
        catalog += f" /Outlines {root} 0 R"
    if dests:
        pairs = " ".join(f"{dest(n)} {dest(p)}" for n, p in sorted(dests.items()))
        catalog += f" /Names << /Dests << /Names [{pairs}] >> >>"
    objects[0] = (catalog + " >>").encode()
    return _serialize(objects, page_ids)


def _serialize(objects, page_ids):
    objects[1] = (
        f"<< /Type /Pages /Count {len(page_ids)} /Kids [{' '.join(f'{n} 0 R' for n in page_ids)}] >>".encode()
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(output)


def scan_pdf():
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, "PDF", resolution=72)
    return output.getvalue()
