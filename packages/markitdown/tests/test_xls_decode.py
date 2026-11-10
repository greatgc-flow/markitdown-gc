"""Regression coverage for malformed BIFF Unicode strings (issue #55)."""

import io
import struct
import warnings
from pathlib import Path

import pytest
import xlrd
import xlrd.biffh
import xlrd.book

from markitdown import StreamInfo
from markitdown.converters import XlsConverter, _xlsx_converter


@pytest.mark.parametrize("fail", [False, True])
def test_tolerant_xls_decode_is_scoped(monkeypatch, fail):
    original_open = xlrd.open_workbook
    originals = {module: module.unicode for module in (xlrd.biffh, xlrd.book)}
    decoded = []

    def open_workbook(*args, **kwargs):
        # An odd-length BIFF8 UTF-16 record, outside the SST decode path.
        record = struct.pack("<HB", 3, 1) + b"A\x00B\x00x"
        decoded.append(xlrd.biffh.unpack_unicode(record, 0))
        decoded.append(xlrd.book.unicode(b"\x00\xd8", "utf_16_le"))
        # A literal replacement character must not inflate the warning count.
        assert xlrd.book.unicode(b"\xfd\xff", "utf_16_le") == "\ufffd"
        if fail:
            raise ValueError("workbook failure")
        workbook = original_open(*args, **kwargs)
        workbook.sheet_by_index(0)._cell_values[1][0] = decoded[0]
        workbook.sheet_by_index(0)._cell_types[1][0] = xlrd.XL_CELL_TEXT
        return workbook

    monkeypatch.setattr(xlrd, "open_workbook", open_workbook)
    original_render = _xlsx_converter.sheet_to_html

    def sheet_to_html(*args, **kwargs):
        # The tolerant decoder is scoped to open_workbook, not sheet rendering.
        assert all(module.unicode is original for module, original in originals.items())
        return original_render(*args, **kwargs)

    monkeypatch.setattr(_xlsx_converter, "sheet_to_html", sheet_to_html)
    data = (Path(__file__).parent / "test_files" / "test.xls").read_bytes()
    try:
        if fail:
            with pytest.raises(ValueError, match="workbook failure"):
                XlsConverter().convert(io.BytesIO(data), StreamInfo(extension=".xls"))
        else:
            with pytest.warns(UserWarning, match=r"Replaced 2 .*UTF-16"):
                result = XlsConverter().convert(
                    io.BytesIO(data), StreamInfo(extension=".xls")
                )
            assert "AB\ufffd" in result.markdown
        assert decoded == ["AB\ufffd", "\ufffd"]
    finally:
        assert all(module.unicode is original for module, original in originals.items())
        assert xlrd.open_workbook is open_workbook


def test_valid_xls_has_no_decode_warning():
    path = Path(__file__).parent / "test_files" / "test.xls"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with path.open("rb") as stream:
            result = XlsConverter().convert(stream, StreamInfo(extension=".xls"))
    assert result.markdown.strip()
    assert not [warning for warning in caught if "UTF-16" in str(warning.message)]
