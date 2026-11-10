"""XLS reads through xlrd directly, without pandas (issue #63).

The pandas reference below is the pre-#63 XLS pipeline, rendered with the XLSX
table semantics of issue #70 (empty cells blank, whole numbers without ".0").
While pandas is installed, every case must stay byte-identical to it, except the
explicit DOCUMENTED_DIVERGENCES where pandas loses information.
"""

import io
import re
import subprocess
import sys
from pathlib import Path

import pytest
import xlrd

from markitdown import StreamInfo
from markitdown.converters import HtmlConverter, XlsConverter
from markitdown.converters._xls_table import sheet_to_html

FIXTURE = Path(__file__).parent / "test_files" / "test.xls"
NAN_ERROR = 0x2A  # #N/A


def T(text):
    return (xlrd.XL_CELL_TEXT, text)


def N(number):
    return (xlrd.XL_CELL_NUMBER, float(number))


def D(serial):
    return (xlrd.XL_CELL_DATE, float(serial))


def B(flag):
    return (xlrd.XL_CELL_BOOLEAN, int(flag))


E = (xlrd.XL_CELL_ERROR, NAN_ERROR)
_ = (xlrd.XL_CELL_EMPTY, "")


class FakeSheet:
    def __init__(self, name, rows):
        width = max((len(row) for row in rows), default=0)
        self.name = name
        self._rows = [list(row) + [_] * (width - len(row)) for row in rows]
        self.nrows = len(rows)
        self.ncols = width

    def row_values(self, rowx):
        return [value for _type, value in self._rows[rowx]]

    def row_types(self, rowx):
        return [ctype for ctype, _value in self._rows[rowx]]


class FakeBook:
    def __init__(self, sheets, datemode=0):
        self._sheets = [FakeSheet(name, rows) for name, rows in sheets.items()]
        self.datemode = datemode
        self.nsheets = len(self._sheets)

    def sheet_names(self):
        return [sheet.name for sheet in self._sheets]

    def sheet_by_name(self, name):
        return self._sheets[self.sheet_names().index(name)]

    def sheet_by_index(self, index):
        return self._sheets[index]

    def sheets(self):
        return list(self._sheets)

    def release_resources(self):
        pass


def _ms(seconds):
    return seconds / 86400


SCENARIOS = {
    "multiple_sheets": FakeBook(
        {
            "First": [[T("Name"), T("Qty")], [T("apple"), N(3)], [T("pear"), N(5)]],
            "Second": [[T("Only")], [T("x")]],
            "Empty": [],
            "Header only": [[T("A"), T("B")]],
        }
    ),
    "numbers": FakeBook(
        {
            "Numbers": [
                [
                    T("int"),
                    T("float"),
                    T("int_gap"),
                    T("mixed"),
                    T("big"),
                    T("tiny"),
                    T("numeric_text"),
                    T("na_text"),
                    T("bool"),
                    T("bool_text"),
                    T("bool_gap"),
                    T("error"),
                    T("negative"),
                    T("long"),
                ],
                [
                    N(1),
                    N(1.5),
                    N(1),
                    T("a"),
                    N(1234567.123456),
                    N(1e-7),
                    T("007"),
                    T("NA"),
                    B(1),
                    T("true"),
                    B(1),
                    E,
                    N(-0.5),
                    N(1 / 3),
                ],
                [
                    N(2),
                    N(2.25),
                    _,
                    N(2),
                    N(1),
                    N(1),
                    T("1e3"),
                    T("null"),
                    B(0),
                    T("FALSE"),
                    _,
                    N(1),
                    N(-12),
                    N(1234567.5),
                ],
                [
                    N(3),
                    N(3),
                    N(3),
                    N(2.5),
                    N(2),
                    N(2),
                    T(" 5 "),
                    T("x"),
                    B(1),
                    _,
                    B(0),
                    N(2),
                    N(0),
                    N(2),
                ],
            ]
        }
    ),
    "dates": FakeBook(
        {
            "Dates": [
                [
                    T("date"),
                    T("datetime"),
                    T("millis"),
                    T("time"),
                    T("date_gap"),
                    T("date_text"),
                    T("old"),
                ],
                [
                    D(43831),
                    D(43831.5),
                    D(43831 + _ms(43200.25)),
                    D(0.5),
                    D(43831),
                    D(43831),
                    D(1),
                ],
                [
                    D(43832),
                    D(43832.75),
                    D(43832 + _ms(1.5)),
                    D(0.75),
                    _,
                    T("later"),
                    D(61),
                ],
            ]
        }
    ),
    "dates_1904": FakeBook(
        {"Mac": [[T("d"), T("t")], [D(42369), D(0.25)], [D(42370.5), D(0.5)]]},
        datemode=1,
    ),
    "headers": FakeBook(
        {
            "Headers": [
                [_, T("dup"), T("dup"), N(2020), _, T("a<b & c"), T("dup.1")],
                [
                    T("line1\nline2\ttab"),
                    T("  padded  "),
                    T("*md* _x_"),
                    N(1),
                    _,
                    T("<b>bold</b>"),
                    T("é ü 日本"),
                ],
            ]
        }
    ),
    "single_column": FakeBook(
        {
            "Single": [
                [_],
                [T("Head")],
                [_],
                [T("v1")],
                [T("   ")],
                [T("v2")],
            ]
        }
    ),
    "blank_rows": FakeBook(
        {
            "Blank rows": [
                [T("a"), T("b"), _],
                [T("x"), N(1), _],
                [_, _, _],
                [T("y"), N(2), _],
            ],
            "Only blanks": [[_]],
        }
    ),
}


def _pandas_reference(data: bytes) -> str:
    """The pre-#63 XLS conversion, rendered like XLSX sheets (#70)."""
    pd = pytest.importorskip("pandas")
    from markitdown.converters._xlsx_converter import _frame_to_html

    sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, engine="xlrd")
    html_converter = HtmlConverter()
    md_content = ""
    for s in sheets:
        md_content += f"## {s}\n"
        html_content = _frame_to_html(sheets[s])
        md_content += (
            html_converter.convert_string(html_content).markdown.strip() + "\n\n"
        )
    return md_content.strip()


def _convert(data: bytes) -> str:
    result = XlsConverter().convert(io.BytesIO(data), StreamInfo(extension=".xls"))
    return result.markdown


def test_fixture_matches_pandas_reference():
    data = FIXTURE.read_bytes()
    assert _convert(data) == _pandas_reference(data)


@pytest.mark.parametrize("book", list(SCENARIOS.values()), ids=list(SCENARIOS))
def test_generated_workbooks_match_pandas_reference(monkeypatch, book):
    monkeypatch.setattr(xlrd, "open_workbook", lambda *args, **kwargs: book)
    assert _convert(b"generated") == _pandas_reference(b"generated")


# Edge cases from the PR #72 review, compared as HTML (stricter than Markdown)
# against pandas rendered through the same #70 transform as _pandas_reference
# (_frame_to_html: empty cells blank, whole numbers without ".0").
# The reference is pandas 3.x (the renderer follows it); the pandas comparison is
# skipped on pandas 2.x, which renders several of these differently.
EDGE_CASES = {
    "hdr_newline": [
        [T("First\nName"), T("Tab\there"), T("trail\n")],
        [T("x"), T("y"), T("z")],
    ],
    "hdr_all_dates": [[D(43831), D(43862)], [N(1), N(2)]],
    "hdr_all_dates_ms": [[D(43831.5 + _ms(0.25)), D(43862)], [N(1), N(2)]],
    "hdr_date_and_error": [[D(43831), E], [N(1), N(2)]],
    "hdr_float_labels": [[N(0.5), N(0.75)], [N(1), N(2)]],
    "hdr_float_and_int": [[N(1.5), N(2)], [N(1), N(2)]],
    "hdr_float_long": [[T("k"), N(1 / 3)], [T("a"), N(1)]],
    "hdr_float_tiny": [[T("k"), N(1e-7)], [T("a"), N(1)]],
    "hdr_error_cell": [[T("a"), E], [N(1), N(2)]],
    "hdr_bom": [[T("﻿Name"), T("b")], [T("x"), N(1)]],
    "hdr_bom_quoted": [[T('﻿"Na"me'), T("b")], [T("x"), N(1)]],
    "hdr_bom_only": [[T("﻿"), T("b")], [T("x"), N(1)]],
    "num_ge_2_64": [[T("v")], [N(1e20)], [N(1)]],
    "num_avogadro": [[T("v")], [N(6.02214076e23)]],
    "num_neg_lt_int64": [[T("v")], [N(-1e19)], [N(1)]],
    "num_uint_with_gap": [[T("v")], [N(2**63)], [_]],
    "num_uint_with_neg": [[T("v")], [N(2**63)], [N(-1)]],
    "num_uint_with_error": [[T("v")], [N(2**63)], [E]],
    "num_uint_alone": [[T("v")], [N(2**63)], [N(1)]],
    "txt_long_digits_gap": [[T("t")], [T("9400111899223197428490")], [_]],
    "txt_long_digits_zero": [[T("id")], [T("00012345678901234567890123")]],
    "txt_overflow_exp": [[T("v")], [T("1e400")], [N(1)]],
    "txt_nul_suffix": [[T("v")], [T("1.5\x00x")], [N(2)]],
    "txt_nul_int": [[T("v")], [T("1\x00x")], [N(2)]],
    "bool_int_mix": [[T("v")], [B(1)], [N(2)]],
    "bool_float_mix": [[T("v")], [B(1)], [N(2.5)]],
    "date_9999": [[T("d")], [D(2958465)], [D(43831)]],
}
# Intentionally NOT pandas-identical: pandas parses these digit strings as
# numbers and loses information (9.400112e+21, dropped leading zeros, inf), so
# the renderer keeps the text as written. Values are the <td> cells, row-major;
# empty cells are blank under #70 (pandas' na_rep "NaN" -> "").
DOCUMENTED_DIVERGENCES = {
    "txt_long_digits_gap": ["9400111899223197428490", ""],
    "txt_long_digits_zero": ["00012345678901234567890123"],
    "txt_overflow_exp": ["1e400", "1"],
}
# pandas raises "substring not found" on an unclosed quote after the BOM; the
# renderer only has to produce a table (it keeps the quote and drops the BOM).
PANDAS_RAISES = {"hdr_bom_unclosed_quote": [[T('﻿"Name'), T("b")], [T("x")]]}


@pytest.mark.parametrize("name", list(EDGE_CASES) + list(PANDAS_RAISES))
def test_edge_cases_match_pandas_reference(monkeypatch, name):
    book = FakeBook({"S": EDGE_CASES.get(name) or PANDAS_RAISES[name]})
    rendered = sheet_to_html(book.sheet_by_name("S"), book.datemode)
    if name in DOCUMENTED_DIVERGENCES:
        cells = re.findall(r"<td>(.*?)</td>", rendered)
        assert cells == DOCUMENTED_DIVERGENCES[name]
        return

    pd = pytest.importorskip("pandas")
    from markitdown.converters._xlsx_converter import _frame_to_html

    if int(pd.__version__.split(".")[0]) < 3:
        pytest.skip("edge-case reference is pandas 3; pandas 2.x renders differently")
    monkeypatch.setattr(xlrd, "open_workbook", lambda *args, **kwargs: book)

    def reference():
        sheets = pd.read_excel(io.BytesIO(b"x"), sheet_name=None, engine="xlrd")
        return _frame_to_html(sheets["S"])

    if name in PANDAS_RAISES:
        with pytest.raises(ValueError):
            reference()
        assert '<th>"Name</th>' in rendered
    else:
        assert rendered == reference()


def test_formatting_without_pandas_reference(monkeypatch):
    book = FakeBook(
        {
            "One": [
                [
                    T("int"),
                    T("float"),
                    T("gap"),
                    T("date"),
                    T("when"),
                    T("day"),
                    T("text"),
                ],
                [N(1), N(1.5), N(1), D(43831), D(43831.5), D(43831), T("a")],
                [N(2), N(2.25), _, D(43832), D(43832.75), _, _],
            ],
            "Empty": [],
            "Two": [[T("only")], [T("x")]],
        }
    )
    monkeypatch.setattr(xlrd, "open_workbook", lambda *args, **kwargs: book)
    markdown = _convert(b"generated")
    # Empty cells are blank and whole numbers drop ".0" (#70).
    assert markdown.startswith(
        "## One\n"
        "| int | float | gap | date | when | day | text |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 1 | 1.50 | 1 | 2020-01-01 | 2020-01-01 12:00:00 | 2020-01-01 | a |\n"
        "| 2 | 2.25 |  | 2020-01-02 | 2020-01-02 18:00:00 |  |  |\n\n"
        "## Empty\n"
    )
    assert markdown.endswith("## Two\n| only |\n| --- |\n| x |")


@pytest.mark.parametrize(
    "values",
    [
        [1.5, 2.25, 3.0],
        [1.0, float("nan"), 3.0],
        [1234567.123456, 1.0, 2.0],
        [1e-7, 1.0, 2.0],
        [-0.5, -12.0, 0.0],
        [1 / 3, 1234567.5, 2.0],
    ],
)
def test_float_formatting_core_still_matches_pandas(values):
    """#70 only overrides empty and whole cells; the rest is pandas' display."""
    pd = pytest.importorskip("pandas")
    from bs4 import BeautifulSoup

    from markitdown.converters._xls_table import _format_floats, format_float_cells

    html = pd.DataFrame({"x": values}).to_html(index=False)
    cells = [td.get_text().strip() for td in BeautifulSoup(html, "html.parser")("td")]
    assert _format_floats(values) == cells
    assert format_float_cells(values) == [
        "" if v != v else str(int(v)) if v.is_integer() else text
        for v, text in zip(values, cells)
    ]


def test_xls_converts_without_pandas(tmp_path):
    out = tmp_path / "out.md"
    code = (
        "import sys\n"
        "sys.modules['pandas'] = None\n"
        "from markitdown import StreamInfo\n"
        "from markitdown.converters import XlsConverter\n"
        "with open(sys.argv[1], 'rb') as stream:\n"
        "    result = XlsConverter().convert(stream, StreamInfo(extension='.xls'))\n"
        "assert sys.modules['pandas'] is None\n"
        "with open(sys.argv[2], 'w', encoding='utf-8', newline='') as out:\n"
        "    out.write(result.markdown)\n"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(FIXTURE), str(out)],
        check=True,
        timeout=120,
    )
    assert out.read_text(encoding="utf-8") == _convert(FIXTURE.read_bytes())
    assert "## 09060124-b5e7-4717-9d07-3c046eb" in out.read_text(encoding="utf-8")
