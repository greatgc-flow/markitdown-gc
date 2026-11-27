import pytest

from ._pdf_builder import pdf


def inspect(data, **options):
    from markitdown.document_inspection import inspect_pdf

    return inspect_pdf(data, **options)


def test_text_and_mixed_files_have_distinct_first_class_outcomes():
    text = {"text": "Existing readable text for this document"}
    image = {"images": [(0, 0, 100, 100)]}
    assert inspect(pdf([text]))["reason"] == "direct"
    result = inspect(pdf([text, image, text]), stop_at_ocr=True)
    assert result["reason"] == "ocr_required"
    assert result["inspected_pages"] == 2
    assert result["total_pages"] == 3
    assert result["pages"][-1]["number"] == 2


def test_preview_measures_blank_and_image_without_reading_image_pixels():
    result = inspect(pdf([{}, {"images": [(0, 0, 100, 100)]}]), measure=True)
    assert [p["role"] for p in result["pages"]] == ["blank", "ocr"]
    assert result["area_percent"] == {"text": 0, "image": 50, "blank": 50, "other": 0}
    assert result["measured_pages"] == 2
    assert all("text" not in p for p in result["pages"])


def test_partial_text_preserves_page_order_and_omission_even_on_mixed_page():
    result = inspect(
        pdf(
            [
                {"text": "Visible caption", "images": [(50, 0, 50, 100)]},
                {"images": [(0, 0, 100, 100)]},
                {"text": "Final page"},
            ]
        ),
        include_text=True,
    )
    assert [(p["role"], p["text"]) for p in result["pages"]] == [
        ("mixed", "Visible caption"),
        ("ocr", ""),
        ("text", "Final page"),
    ]


def test_partial_coverage_is_never_reported_as_complete():
    result = inspect(pdf([{"text": "first"}, {"text": "second"}]), max_pages=1)
    assert result["reason"] == "undetermined"
    assert result["complete"] is False
    assert result["area_percent"] is None
    with pytest.raises(ValueError):
        inspect(b"not a PDF")
