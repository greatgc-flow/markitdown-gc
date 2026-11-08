"""
Unit tests for PdfConverterWithOCR.

For each PDF test file: convert with a mock OCR service then compare the
full output string against the expected snapshot.

OCR block format used by the converter:
    *[Image OCR]
    MOCK_OCR_TEXT_12345
    [End OCR]*
"""

import io
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from markitdown_ocr._ocr_service import OCRResult  # noqa: E402
from markitdown_ocr._pdf_converter_with_ocr import (  # noqa: E402
    PdfConverterWithOCR,
)
from markitdown import StreamInfo  # noqa: E402

TEST_DATA_DIR = Path(__file__).parent / "ocr_test_data"

_MOCK_TEXT = "MOCK_OCR_TEXT_12345"
_OCR_BLOCK = f"*[Image OCR]\n{_MOCK_TEXT}\n[End OCR]*"
_PAGE_1_SCANNED = f"## Page 1\n\n\n\n\n{_OCR_BLOCK}"


class MockOCRService:
    def extract_text(
        self,  # noqa: ANN101
        image_stream: Any,
        **kwargs: Any,
    ) -> OCRResult:
        return OCRResult(text=_MOCK_TEXT, backend_used="mock")


@pytest.fixture(scope="module")
def svc() -> MockOCRService:
    return MockOCRService()


def _convert(filename: str, ocr_service: MockOCRService) -> str:
    path = TEST_DATA_DIR / filename
    if not path.exists():
        pytest.skip(f"Test file not found: {path}")
    converter = PdfConverterWithOCR()
    with open(path, "rb") as f:
        return converter.convert(
            f, StreamInfo(extension=".pdf"), ocr_service=ocr_service
        ).text_content


@pytest.mark.parametrize("positioned_words", [False, True])
def test_pdf_interleaving_preserves_native_word_spacing(positioned_words):
    import pdfplumber

    # TJ offsets separate words geometrically without storing space characters.
    first = b"[(First) -1000 (Second)] TJ" if positioned_words else b"(First Second) Tj"
    last = b"[(Third) -1000 (Fourth)] TJ" if positioned_words else b"(Third Fourth) Tj"
    content = (
        b"BT /F1 12 Tf 40 350 Td " + first + b" ET\n"
        b"q 60 0 0 60 40 200 cm /Im1 Do Q\n"
        b"BT /F1 12 Tf 40 100 Td " + last + b" ET\n"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] "
        b"/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >> "
        b"/Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 3 >>\n"
        b"stream\n\xff\x00\x00\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        page = document.pages[0]
        assert page.extract_text() == "First Second\nThird Fourth"
        if positioned_words:
            assert all(char["text"] != " " for char in page.chars)

    converter = PdfConverterWithOCR(MockOCRService())
    result = converter.convert(io.BytesIO(pdf), StreamInfo(extension=".pdf"))

    assert "First Second" in result.markdown
    assert "Third Fourth" in result.markdown
    assert result.markdown.count(_OCR_BLOCK) == 1
    assert result.markdown.index("First Second") < result.markdown.index(_OCR_BLOCK)
    assert result.markdown.index(_OCR_BLOCK) < result.markdown.index("Third Fourth")


# ---------------------------------------------------------------------------
# pdf_image_start.pdf
# ---------------------------------------------------------------------------


def test_pdf_image_start(svc: MockOCRService) -> None:
    expected = (
        "## Page 1\n\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*\n\n\n"
        "This is text BEFORE the image.\n\n"
        "The image should appear above this text.\n\n"
        "This is more content after the image."
    )
    assert _convert("pdf_image_start.pdf", svc) == expected


# ---------------------------------------------------------------------------
# pdf_image_middle.pdf
# ---------------------------------------------------------------------------


def test_pdf_image_middle(svc: MockOCRService) -> None:
    expected = (
        "## Page 1\n\n\n"
        "Section 1: Introduction\n\n"
        "This document contains an image in the middle.\n\n"
        "Here is some introductory text.\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*\n\n\n"
        "Section 2: Details\n\n"
        "This text appears AFTER the image."
    )
    assert _convert("pdf_image_middle.pdf", svc) == expected


# ---------------------------------------------------------------------------
# pdf_image_end.pdf
# ---------------------------------------------------------------------------


def test_pdf_image_end(svc: MockOCRService) -> None:
    expected = (
        "## Page 1\n\n\n"
        "Main Content\n\n"
        "This is the main text content.\n\n"
        "The image will appear at the end.\n\n"
        "Keep reading...\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*"
    )
    assert _convert("pdf_image_end.pdf", svc) == expected


# ---------------------------------------------------------------------------
# pdf_multiple_images.pdf
# ---------------------------------------------------------------------------


def test_pdf_multiple_images(svc: MockOCRService) -> None:
    expected = (
        "## Page 1\n\n\n"
        "Document with Multiple Images\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*\n\n\n"
        "Text between first and second image.\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*\n\n\n"
        "Final text after all images."
    )
    assert _convert("pdf_multiple_images.pdf", svc) == expected


# ---------------------------------------------------------------------------
# pdf_complex_layout.pdf
# ---------------------------------------------------------------------------


def test_pdf_complex_layout(svc: MockOCRService) -> None:
    expected = (
        "## Page 1\n\n\n"
        "Complex Layout Document\n\n"
        "Table:\n\n"
        "Item Quantity\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*\n\n\n"
        "Widget A 5"
    )
    assert _convert("pdf_complex_layout.pdf", svc) == expected


# ---------------------------------------------------------------------------
# pdf_multipage.pdf
# ---------------------------------------------------------------------------


def test_pdf_multipage(svc: MockOCRService) -> None:
    expected = (
        "## Page 1\n\n\n"
        "Page 1 - Content before image\n\n"
        "This is important text that appears BEFORE the image.\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*\n\n\n"
        "This text appears AFTER the image on page 1.\n\n"
        "More content follows here.\n\n\n"
        "## Page 2\n\n\n"
        "Page 2 - Content with image at end\n\n"
        "Main content of page 2 starts here.\n\n"
        "This is paragraph 1.\n\n"
        "This is paragraph 2.\n\n"
        "Final paragraph before image.\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*\n\n\n\n"
        "## Page 3\n\n\n"
        "Page 3 - Image at top\n\n\n\n"
        "*[Image OCR]\nMOCK_OCR_TEXT_12345\n[End OCR]*\n\n\n"
        "Content that follows the image.\n\n"
        "This text is AFTER the image."
    )
    assert _convert("pdf_multipage.pdf", svc) == expected


# ---------------------------------------------------------------------------
# pdf_scanned_*.pdf — raster-only pages → full-page OCR
# ---------------------------------------------------------------------------


def test_pdf_scanned_invoice(svc: MockOCRService) -> None:
    assert _convert("pdf_scanned_invoice.pdf", svc) == _PAGE_1_SCANNED


def test_pdf_scanned_meeting_minutes(svc: MockOCRService) -> None:
    assert _convert("pdf_scanned_meeting_minutes.pdf", svc) == _PAGE_1_SCANNED


def test_pdf_scanned_minimal(svc: MockOCRService) -> None:
    assert _convert("pdf_scanned_minimal.pdf", svc) == _PAGE_1_SCANNED


def test_pdf_scanned_sales_report(svc: MockOCRService) -> None:
    assert _convert("pdf_scanned_sales_report.pdf", svc) == _PAGE_1_SCANNED


def test_pdf_scanned_report(svc: MockOCRService) -> None:
    expected = (
        f"{_PAGE_1_SCANNED}\n\n\n\n"
        f"## Page 2\n\n\n\n\n{_OCR_BLOCK}\n\n\n\n"
        f"## Page 3\n\n\n\n\n{_OCR_BLOCK}"
    )
    assert _convert("pdf_scanned_report.pdf", svc) == expected


# ---------------------------------------------------------------------------
# Scanned PDF fallback path (pdfplumber finds no text → full-page OCR)
# ---------------------------------------------------------------------------


def test_pdf_scanned_fallback_format() -> None:
    """Render a real PDF, replacing only the remote OCR service."""
    from PIL import Image

    path = TEST_DATA_DIR / "pdf_image_start.pdf"
    seen = []

    class RecordingOCRService:
        def extract_text(self, image_stream: Any) -> OCRResult:
            assert image_stream.tell() == 0
            with Image.open(image_stream) as image:
                assert image.format == "PNG"
                assert image.width > 0 and image.height > 0
                image.load()
                seen.append(image.size)
            return OCRResult(text=_MOCK_TEXT, backend_used="mock")

    converter = PdfConverterWithOCR()
    markdown = converter._ocr_full_pages(
        io.BytesIO(path.read_bytes()), RecordingOCRService()
    )

    assert len(seen) == 1
    assert markdown == "## Page 1\n\n\n" + _OCR_BLOCK


# ---------------------------------------------------------------------------
# No OCR service — no OCR tags emitted
# ---------------------------------------------------------------------------


def test_pdf_no_ocr_service_no_tags() -> None:
    path = TEST_DATA_DIR / "pdf_image_middle.pdf"
    if not path.exists():
        pytest.skip(f"Test file not found: {path}")
    converter = PdfConverterWithOCR()
    with open(path, "rb") as f:
        md = converter.convert(f, StreamInfo(extension=".pdf")).text_content
    assert "*[Image OCR]" not in md
    assert "[End OCR]*" not in md


@pytest.mark.parametrize("with_ocr", [False, True])
def test_pdf_both_parser_failures_propagate(monkeypatch, with_ocr):
    from markitdown_ocr import _pdf_converter_with_ocr as module

    def fail_primary(*args, **kwargs):
        raise ValueError("pdfplumber failed")

    def fail_fallback(stream):
        assert stream.tell() == 0
        raise RuntimeError("pdfminer failed")

    monkeypatch.setattr(module.pdfplumber, "open", fail_primary)
    monkeypatch.setattr(module.pdfminer.high_level, "extract_text", fail_fallback)
    converter = PdfConverterWithOCR(MockOCRService() if with_ocr else None)
    with pytest.raises(RuntimeError, match="pdfminer failed"):
        converter.convert(io.BytesIO(b"broken PDF"), StreamInfo(extension=".pdf"))


@pytest.mark.parametrize("recovery", ["text", "empty", "error"])
def test_pdf_parser_failures_try_full_page_ocr(monkeypatch, recovery):
    from markitdown_ocr import _pdf_converter_with_ocr as module

    def fail_primary(*args, **kwargs):
        raise ValueError("pdfplumber failed")

    parser_error = RuntimeError("pdfminer failed")

    def fail_fallback(stream):
        stream.read()
        raise parser_error

    monkeypatch.setattr(module.pdfplumber, "open", fail_primary)
    monkeypatch.setattr(module.pdfminer.high_level, "extract_text", fail_fallback)
    service = MockOCRService()
    converter = PdfConverterWithOCR()
    calls = []

    def full_pages(stream, ocr_service, **kwargs):
        assert stream.tell() == 0
        assert ocr_service is service
        calls.append(True)
        if recovery == "error":
            raise ValueError("OCR rendering failed")
        return "## Page 1\n\n" + _OCR_BLOCK if recovery == "text" else " \n"

    monkeypatch.setattr(converter, "_ocr_full_pages", full_pages)
    if recovery == "text":
        result = converter.convert(
            io.BytesIO(b"damaged PDF"),
            StreamInfo(extension=".pdf"),
            ocr_service=service,
        )
        assert result.markdown == "## Page 1\n\n" + _OCR_BLOCK
    else:
        with pytest.raises(RuntimeError) as exc:
            converter.convert(
                io.BytesIO(b"damaged PDF"),
                StreamInfo(extension=".pdf"),
                ocr_service=service,
            )
        assert exc.value is parser_error
    assert calls == [True]


@pytest.mark.parametrize("ocr_outcome", ["text", "empty", "error"])
def test_pdf_parser_failures_recover_with_pymupdf(monkeypatch, ocr_outcome):
    pytest.importorskip("fitz", reason="PyMuPDF rendering is unavailable")
    from markitdown_ocr import _pdf_converter_with_ocr as module

    def fail_primary(*args, **kwargs):
        raise ValueError("pdfplumber failed")

    def fail_fallback(stream):
        raise RuntimeError("pdfminer failed")

    monkeypatch.setattr(module.pdfplumber, "open", fail_primary)
    monkeypatch.setattr(module.pdfminer.high_level, "extract_text", fail_fallback)
    calls = []

    class Service:
        def extract_text(self, stream):
            from PIL import Image

            with Image.open(stream) as image:
                assert image.format == "PNG"
            calls.append(True)
            if ocr_outcome == "error":
                raise ValueError("OCR failed")
            return OCRResult(text=_MOCK_TEXT if ocr_outcome == "text" else " ")

    converter = PdfConverterWithOCR(Service())
    stream = io.BytesIO((TEST_DATA_DIR / "pdf_image_start.pdf").read_bytes())
    if ocr_outcome == "text":
        result = converter.convert(stream, StreamInfo(extension=".pdf"))
        assert _MOCK_TEXT in result.markdown
    else:
        with pytest.raises(RuntimeError, match="pdfminer failed"):
            converter.convert(stream, StreamInfo(extension=".pdf"))
    assert calls == [True]


@pytest.mark.parametrize("with_ocr", [False, True])
def test_pdf_mixed_text_and_vector_pages_ocr_only_textless_page(monkeypatch, with_ocr):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from PIL import Image
    from markitdown_ocr import _pdf_converter_with_ocr as module

    rendered = []

    def page(number, text):
        def to_image(*, resolution):
            assert resolution == 300
            rendered.append(number)
            return SimpleNamespace(original=Image.new("RGB", (20, 20)))

        return SimpleNamespace(extract_text=lambda: text, chars=[], to_image=to_image)

    pages = [page(1, "First text page"), page(2, " \n"), page(3, "Last text page")]
    monkeypatch.setattr(
        module.pdfplumber,
        "open",
        lambda stream: nullcontext(SimpleNamespace(pages=pages)),
    )

    def forbidden(*args):
        pytest.fail("Text pages must not trigger document-wide fallback")

    monkeypatch.setattr(module.pdfminer.high_level, "extract_text", forbidden)
    converter = PdfConverterWithOCR(MockOCRService() if with_ocr else None)
    monkeypatch.setattr(converter, "_extract_page_images", lambda *args: [])
    monkeypatch.setattr(converter, "_ocr_full_pages", forbidden)
    result = converter.convert(io.BytesIO(b"mixed PDF"), StreamInfo(extension=".pdf"))

    assert rendered == ([2] if with_ocr else [])
    if with_ocr:
        assert result.markdown.count("## Page 2") == 1
        assert result.markdown.index("First text page") < result.markdown.index(
            _MOCK_TEXT
        )
        assert result.markdown.index(_MOCK_TEXT) < result.markdown.index(
            "Last text page"
        )
    else:
        assert _MOCK_TEXT not in result.markdown
        assert "First text page" in result.markdown
        assert "Last text page" in result.markdown


@pytest.mark.parametrize("with_ocr", [False, True])
def test_pdf_textless_vector_pages_trigger_fallback(monkeypatch, with_ocr):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from PIL import Image
    from markitdown_ocr import _pdf_converter_with_ocr as module

    pages = [
        SimpleNamespace(
            extract_text=lambda: " \n\t",
            chars=[],
            to_image=lambda **kwargs: SimpleNamespace(
                original=Image.new("RGB", (20, 20))
            ),
        )
        for _ in range(2)
    ]
    monkeypatch.setattr(
        module.pdfplumber,
        "open",
        lambda stream: nullcontext(SimpleNamespace(pages=pages)),
    )
    fallback_calls = []

    def fallback(stream):
        assert stream.tell() == 0
        fallback_calls.append(True)
        return "\x0c\x0c"

    monkeypatch.setattr(module.pdfminer.high_level, "extract_text", fallback)
    ocr_calls = []

    class Service(MockOCRService):
        def extract_text(self, stream):
            assert stream.tell() == 0
            ocr_calls.append(True)
            return super().extract_text(stream)

    converter = PdfConverterWithOCR(Service() if with_ocr else None)
    monkeypatch.setattr(converter, "_extract_page_images", lambda *args: [])
    result = converter.convert(io.BytesIO(b"vector PDF"), StreamInfo(extension=".pdf"))

    if with_ocr:
        assert not fallback_calls
        assert ocr_calls == [True, True]
        assert result.markdown.count(_OCR_BLOCK) == 2
        assert result.markdown.count("## Page 1") == 1
        assert result.markdown.count("## Page 2") == 1
    else:
        assert fallback_calls == [True]
        assert not ocr_calls
        assert not result.markdown.strip()


@pytest.mark.parametrize("path", ["embedded", "per_page", "full_pages", "pymupdf"])
@pytest.mark.parametrize("outcome", ["error_empty", "error_text", "empty"])
def test_pdf_missing_ocr_text_warns_and_sets_omission_flag(monkeypatch, path, outcome):
    import warnings
    from contextlib import nullcontext
    from types import SimpleNamespace
    from PIL import Image
    from markitdown_ocr import _pdf_converter_with_ocr as module

    if path == "pymupdf":
        pytest.importorskip("fitz", reason="PyMuPDF rendering is unavailable")
    converter = PdfConverterWithOCR()
    open_calls = 0
    if path != "embedded":
        blank_page = SimpleNamespace(
            extract_text=lambda: "",
            to_image=lambda **kwargs: SimpleNamespace(
                original=Image.new("RGB", (20, 20))
            ),
        )
        pages = [blank_page]
        if path == "per_page":
            pages.append(SimpleNamespace(extract_text=lambda: "Native text retained"))

        def open_pdf(stream):
            nonlocal open_calls
            open_calls += 1
            if path == "pymupdf" or (path == "full_pages" and open_calls == 1):
                raise ValueError("pdfplumber failed")
            return nullcontext(SimpleNamespace(pages=pages))

        monkeypatch.setattr(module.pdfplumber, "open", open_pdf)
        monkeypatch.setattr(
            module.pdfminer.high_level, "extract_text", lambda stream: "\f"
        )
        monkeypatch.setattr(converter, "_extract_page_images", lambda *args: [])

    class Service:
        def extract_text(self, stream):
            assert stream.tell() == 0
            return OCRResult(
                text="ignored" if outcome == "error_text" else " \n",
                error="quota exceeded" if outcome.startswith("error") else None,
            )

    data = (TEST_DATA_DIR / "pdf_image_start.pdf").read_bytes()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = converter.convert(
            io.BytesIO(data), StreamInfo(extension=".pdf"), ocr_service=Service()
        )
    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    if outcome.startswith("error"):
        assert len(runtime_warnings) == 1
        assert "PDF image OCR failed: quota exceeded" in str(
            runtime_warnings[0].message
        )
    else:
        assert not runtime_warnings
    assert result.image_content_omitted is True
    assert "ignored" not in result.markdown
    assert "[Image OCR]" not in result.markdown
    if path == "embedded":
        assert "This is text BEFORE the image." in result.markdown
    elif path == "per_page":
        assert "Native text retained" in result.markdown

    # Omission state belongs to this conversion, not the reused converter instance.
    open_calls = 0
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        recovered = converter.convert(
            io.BytesIO(data), StreamInfo(extension=".pdf"), ocr_service=MockOCRService()
        )
    assert not [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert recovered.image_content_omitted is False
    assert _OCR_BLOCK in recovered.markdown


def test_pdf_reported_ocr_error_cannot_recover_failed_parsers(monkeypatch):
    pytest.importorskip("fitz", reason="PyMuPDF rendering is unavailable")
    from markitdown_ocr import _pdf_converter_with_ocr as module

    def fail_primary(*args, **kwargs):
        raise ValueError("pdfplumber failed")

    def fail_fallback(stream):
        raise RuntimeError("pdfminer failed")

    class Service:
        def extract_text(self, stream):
            return OCRResult(text="ignored", error="quota exceeded")

    monkeypatch.setattr(module.pdfplumber, "open", fail_primary)
    monkeypatch.setattr(module.pdfminer.high_level, "extract_text", fail_fallback)
    data = (TEST_DATA_DIR / "pdf_image_start.pdf").read_bytes()
    with pytest.warns(RuntimeWarning, match="PDF image OCR failed: quota exceeded"):
        with pytest.raises(RuntimeError, match="pdfminer failed"):
            PdfConverterWithOCR(Service()).convert(
                io.BytesIO(data), StreamInfo(extension=".pdf")
            )
