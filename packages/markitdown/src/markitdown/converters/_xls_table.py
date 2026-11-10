"""Render a legacy XLS sheet as an HTML table using xlrd only (issue #63).

The output reproduces what ``pandas.read_excel(engine="xlrd")`` followed by
``DataFrame.to_html(index=False)`` produced, so XLS Markdown stays unchanged
without pandas: first row as header, pandas' default NA strings, per-column
type inference, and pandas' display formatting for numbers and dates.
"""

import datetime
import html
import math
import re
from collections import defaultdict
from typing import Any

_NAN = float("nan")
_NA_TEXT = frozenset(
    ["", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan"]
    + ["1.#IND", "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "NaN", "None"]
    + ["n/a", "nan", "null"]
)
_BOOL_TEXT = {"True": True, "TRUE": True, "true": True}
_BOOL_TEXT.update({"False": False, "FALSE": False, "false": False})
_INF_TEXT = {"inf": math.inf, "+inf": math.inf, "infinity": math.inf}
_INF_TEXT.update({"+infinity": math.inf, "-inf": -math.inf, "-infinity": -math.inf})
_NUMBER_TEXT = re.compile(
    r"[ \t\n\v\f\r]*[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?[ \t\n\v\f\r]*"
)
_DECIMAL = re.compile(r"^\s*[\+-]?[0-9]+\.[0-9]*$")


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and value != value


def _cell(value: Any, ctype: int, datemode: int) -> Any:
    import xlrd

    if ctype == xlrd.XL_CELL_DATE:
        try:
            value = xlrd.xldate.xldate_as_datetime(value, datemode)
        except OverflowError:
            return value
        # Dates on the epoch day are times only.
        if value.timetuple()[0:3] == ((1904, 1, 1) if datemode else (1899, 12, 31)):
            value = value.time()
    elif ctype == xlrd.XL_CELL_ERROR:
        value = _NAN
    elif ctype == xlrd.XL_CELL_BOOLEAN:
        value = bool(value)
    elif ctype == xlrd.XL_CELL_NUMBER and math.isfinite(value):
        if int(value) == value:
            value = int(value)
    return value


def _column_names(header: list[Any]) -> list[Any]:
    names = [f"Unnamed: {i}" if c == "" else c for i, c in enumerate(header)]
    unnamed = [i for i, c in enumerate(header) if c == ""]
    counts: defaultdict[Any, int] = defaultdict(int)
    # Named columns keep their names; duplicates and unnamed ones get ".N".
    for i in [i for i in range(len(names)) if i not in unnamed] + unnamed:
        col = old = names[i]
        cur = counts[col]
        while cur > 0:
            counts[old] = cur + 1
            col = f"{old}.{cur}"
            cur = cur + 1 if col in names else counts[col]
        names[i] = col
        counts[col] = cur + 1
    return names


def _number(value: Any) -> Any:
    """Return the numeric form of a cell, or None when the column stays text."""
    if isinstance(value, str):
        if value in _NA_TEXT:
            return _NAN
        if _NUMBER_TEXT.fullmatch(value):
            return float(value) if any(c in value for c in ".eE") else int(value)
        return _INF_TEXT.get(value.lower())
    if isinstance(value, (int, float)):
        return value
    return None


def _trim_zeros(strings: list[str]) -> list[str]:
    def numbers() -> list[str]:
        return [s for s in strings if _DECIMAL.match(s)]

    while numbers() and all(s.endswith("0") for s in numbers()):
        strings = [s[:-1] if _DECIMAL.match(s) else s for s in strings]
    return [s + "0" if _DECIMAL.match(s) and s.endswith(".") else s for s in strings]


def _format_floats(values: list[float]) -> list[str]:
    def render(spec: str) -> list[str]:
        return _trim_zeros(["NaN" if v != v else format(v, spec) for v in values])

    # pandas switches the whole column to scientific notation for tiny values,
    # or for large values whose fixed-point text gets too long.
    strings = render(".6f")
    sizes = [abs(v) for v in values if v == v]
    too_long = max(map(len, strings), default=0) > 12
    small = any(0 < v < 1e-6 for v in sizes)
    if small or (too_long and any(v > 1e6 for v in sizes)):
        strings = render(".6e")
    return strings


def _format_datetimes(values: list[Any]) -> list[str]:
    stamps = [v for v in values if isinstance(v, datetime.datetime)]
    if all(v.time() == datetime.time() for v in stamps):
        spec = ""
    elif all(v.microsecond == 0 for v in stamps):
        spec = "seconds"
    elif all(v.microsecond % 1000 == 0 for v in stamps):
        spec = "milliseconds"
    else:
        spec = "microseconds"

    def text(value: Any) -> str:
        if not isinstance(value, datetime.datetime):
            return "NaT"
        return value.isoformat(" ", spec) if spec else value.date().isoformat()

    return [text(v) for v in values]


def _format_object(value: Any) -> str:
    if isinstance(value, float):
        if value != value:
            return "NaN"
        text = f"{value: .6f}".rstrip("0")
        return text + "0" if text.endswith(".") else text
    if isinstance(value, str):
        return value.replace("\t", r"\t").replace("\r", r"\r").replace("\n", r"\n")
    return str(value)


def _format_column(values: list[Any]) -> list[str]:
    numbers = [_number(v) for v in values]
    if None not in numbers:
        if any(isinstance(n, float) for n in numbers):
            return _format_floats([float(n) for n in numbers])
        if all(isinstance(n, bool) for n in numbers):
            return [str(n) for n in numbers]
        return [str(int(n)) for n in numbers]

    memo: dict[Any, Any] = {}
    values = [
        _NAN if isinstance(v, str) and v in _NA_TEXT else memo.setdefault(v, v)
        for v in values
    ]
    if all(
        isinstance(v, bool) or (isinstance(v, str) and v in _BOOL_TEXT) or _is_nan(v)
        for v in values
    ):
        values = [_BOOL_TEXT[v] if isinstance(v, str) else v for v in values]
    if any(isinstance(v, datetime.datetime) for v in values):
        if all(isinstance(v, datetime.datetime) or _is_nan(v) for v in values):
            return _format_datetimes(values)
    return [_format_object(v) for v in values]


def _format_labels(labels: list[Any]) -> list[str]:
    numeric = all(type(v) in (int, float) for v in labels)
    if numeric and any(isinstance(v, float) for v in labels):
        return _format_floats([float(v) for v in labels])
    if labels and all(isinstance(v, datetime.datetime) for v in labels):
        return _format_datetimes(labels)
    return [_format_object(v) for v in labels]


def sheet_to_html(sheet: Any, datemode: int) -> str:
    # read_excel parses with skip_blank_lines=False: every row is kept, so a
    # blank first row becomes "Unnamed: N" headers and blank rows become NaN.
    rows = []
    for r in range(sheet.nrows):
        cells = zip(sheet.row_values(r), sheet.row_types(r))
        rows.append([_cell(value, ctype, datemode) for value, ctype in cells])
    header = _column_names(rows[0]) if rows else []
    body = rows[1:]
    columns = [_format_column([row[j] for row in body]) for j in range(len(header))]

    def cell(tag: str, text: str) -> str:
        return f"      <{tag}>{html.escape(text, quote=False).strip()}</{tag}>"

    lines = ['<table border="1" class="dataframe">', "  <thead>"]
    lines.append('    <tr style="text-align: right;">')
    lines += [cell("th", label) for label in _format_labels(header)]
    lines += ["    </tr>", "  </thead>", "  <tbody>"]
    for r in range(len(body)):
        lines.append("    <tr>")
        lines += [cell("td", column[r]) for column in columns]
        lines.append("    </tr>")
    lines += ["  </tbody>", "</table>"]
    return "\n".join(lines)
