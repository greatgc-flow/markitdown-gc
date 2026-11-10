"""PDF outline (bookmark) entries become Markdown headings (issue #68)."""

import io
import re

import pdfminer.high_level

from markitdown import MarkItDown, StreamInfo
from markitdown.converters._pdf_converter import (
    _apply_outline_headings,
    _read_outline,
)

from ._pdf_builder import _serialize, pdf, text_pdf

BODY = "Body text that explains the section in a full sentence."

PAGES = [
    [
        (20, "Introduction"),
        (11, BODY),
        (16, "Background"),
        (11, "Background work is summarized in this paragraph."),
    ],
    [
        (20, "2 Methods"),
        (11, "We describe the Methods used and the Background again."),
    ],
]

OUTLINE = [
    ("Introduction", 0, [("Background", 0, [])]),
    ("Methods", 1, []),
]


def _convert(data):
    return MarkItDown().convert_stream(
        io.BytesIO(data), stream_info=StreamInfo(extension=".pdf")
    )


def _lines(markdown):
    return [line.strip() for line in markdown.split("\n")]


def _headings(markdown):
    return [line for line in _lines(markdown) if line.startswith("#")]


def test_outline_titles_become_headings():
    markdown = _convert(text_pdf(PAGES, OUTLINE)).markdown
    lines = _lines(markdown)

    assert "# Introduction" in lines
    assert "## Background" in lines
    # A heading at the top of page 2 (after pdfminer's form feed) keeps the
    # section number that the bookmark title omits.
    assert "# 2 Methods" in markdown.split("\n")
    # Body lines that merely mention a title are not promoted.
    assert BODY in lines
    assert "We describe the Methods used and the Background again." in lines
    assert len(_headings(markdown)) == 3


def test_outline_is_read_with_levels_and_pages():
    assert _read_outline(io.BytesIO(text_pdf(PAGES, OUTLINE))) == [
        (1, "Introduction", 0),
        (2, "Background", 0),
        (1, "Methods", 1),
    ]


def test_pdf_without_outline_is_unchanged():
    data = text_pdf(PAGES)
    markdown = _convert(data).markdown

    raw = pdfminer.high_level.extract_text(io.BytesIO(data))
    # Same normalization MarkItDown applies to every converter result.
    lines = [line.rstrip() for line in re.split(r"\r?\n", raw)]
    expected = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))

    assert _read_outline(io.BytesIO(data)) == []
    assert "Introduction" in markdown
    assert markdown == expected
    assert not _headings(markdown)


def test_unreadable_outline_input_yields_no_headings():
    assert _read_outline(io.BytesIO(b"not a pdf")) == []
    assert _read_outline(io.BytesIO(pdf([{"text": "x"}]))) == []


def test_contents_page_does_not_consume_bookmarks():
    # pdfminer splits a contents line from its distant page number, so the
    # contents page repeats the numbered titles as standalone lines.
    outline = [(1, "Introduction", 1), (1, "Methods", 2)]
    markdown = (
        "Contents\n1 Introduction\n2 Methods\n" "\f1 Introduction\nText\n" "\f2 Methods"
    )

    assert _apply_outline_headings(markdown, outline).split("\n") == [
        "Contents",
        "1 Introduction",
        "2 Methods",
        "\f",
        "# 1 Introduction",
        "Text",
        "\f",
        "# 2 Methods",
    ]


def test_contents_page_end_to_end():
    pages = [
        [(20, "Contents"), (11, "1 Introduction"), (11, "2 Methods")],
        [(20, "1 Introduction"), (11, BODY)],
        [(20, "2 Methods"), (11, "Results Methods text.")],
    ]
    outline = [("Introduction", 1, []), ("Methods", 2, [])]
    markdown = _convert(text_pdf(pages, outline)).markdown

    assert _headings(markdown) == ["# 1 Introduction", "# 2 Methods"]
    assert "1 Introduction" in _lines(markdown)
    assert "2 Methods" in _lines(markdown)


def test_matching_is_whole_line_per_page_and_skips_tables():
    outline = [(1, "Summary", 0), (2, "Details", 0), (1, "Summary", 0), (1, "X", 0)]
    markdown = "\n".join(
        [
            "| Summary | Details |",  # table row: never a heading
            "Summary of results continues here.",  # partial line: not a heading
            "Summary",
            "Details",
            "Details",  # bookmark already consumed
            "3.1 Summary",  # second "Summary" bookmark, numbered in the text
            "Appendix",
        ]
    )

    assert _apply_outline_headings(markdown, outline).split("\n") == [
        "| Summary | Details |",
        "Summary of results continues here.",
        "# Summary",
        "## Details",
        "Details",
        "# 3.1 Summary",
        "Appendix",
    ]


def test_toc_lines_with_page_numbers_are_not_headings():
    outline = [(1, "Introduction", 0), (1, "Results", 1)]
    markdown = "Introduction 1\nResults 4\nIntroduction\n\fResults 4\nResults"

    assert _apply_outline_headings(markdown, outline).split("\n") == [
        "Introduction 1",
        "Results 4",
        "# Introduction",
        "\fResults 4",
        "# Results",
    ]


def test_normalized_matching_and_number_prefixes():
    outline = [
        (1, "概要", 0),
        (2, "詳細", 0),
        (1, "Introduction", 0),
        (1, "Results", 0),
        (1, "5", 0),
    ]
    lines = ["１．概要", "詳 細", "INTRODUCTION", "2019 Results", "1.5"]

    assert _apply_outline_headings("\n".join(lines), outline).split("\n") == [
        "# １．概要",  # full-width numbering, NFKC
        "## 詳 細",  # spaces between CJK characters
        "# INTRODUCTION",  # case-insensitive
        "2019 Results",  # a year is not a section number
        "1.5",  # a decimal is not a section number
    ]


def test_line_after_lone_partial_number_is_not_promoted():
    # The partial-numbering merge would otherwise yield ".1 # Scope".
    markdown = ".1\n\nScope\nScope"
    assert _apply_outline_headings(markdown, [(1, "Scope", 0)]).split("\n") == [
        ".1",
        "",
        "Scope",
        "# Scope",
    ]


def test_first_page_offsets_chunk_pages():
    outline = [(1, "Intro", 3)]
    assert _apply_outline_headings("Intro\nText", outline, first_page=3) == (
        "# Intro\nText"
    )
    assert _apply_outline_headings("Intro\nText", outline, first_page=2) == (
        "Intro\nText"
    )


def test_empty_outline_returns_markdown_verbatim():
    markdown = "  Intro \n\fBody"
    assert _apply_outline_headings(markdown, []) is markdown


def test_destinations_actions_and_destless_parents():
    outline = [
        ("Part I", None, [("Intro", ("A", 0), [])]),  # parent without a target
        ("Named", "sec2", []),
        ("Action named", ("A", "sec2"), []),
        ("Broken", "missing", []),
    ]
    data = text_pdf([[(11, "a")], [(11, "b")]], outline, dests={"sec2": 1})

    assert _read_outline(io.BytesIO(data)) == [
        (1, "Intro", 0),
        (1, "Named", 1),
        (1, "Action named", 1),
    ]


def test_title_encodings():
    outline = [
        ("概要", 0, []),  # UTF-16BE with BOM
        (b"Caf\xe9", 0, []),  # PDFDocEncoding
        (b"\xef\xbb\xbfR\xc3\xa9sum\xc3\xa9", 0, []),  # UTF-8 with BOM (PDF 2.0)
        (b"\xff\xfe" + "Ölç".encode("utf-16-le"), 0, []),  # UTF-16LE (in the wild)
    ]
    titles = [t for _, t, _ in _read_outline(io.BytesIO(text_pdf([[]], outline)))]
    assert titles == ["概要", "Café", "Résumé", "Ölç"]


def _raw_outline_pdf(nodes):
    """One empty page (object 3); outline root 4; nodes are objects 5, 6, ..."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R >>",
        b"",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        b"<< /Type /Outlines /First 5 0 R >>",
    ]
    return _serialize(objects + [node.encode() for node in nodes], [3])


def test_shared_and_cyclic_outline_nodes_are_visited_once():
    # Each node is both First child and Next sibling of its predecessor and
    # the last loops back: a naive walk visits about 2**40 nodes.
    depth = 40
    links = [
        f"/First {6 + i} 0 R /Last {6 + i} 0 R /Next {6 + i} 0 R"
        for i in range(depth - 1)
    ] + ["/Next 5 0 R"]
    titled = [
        f"<< /Title (T{i}) /Dest [3 0 R /Fit] {links[i]} >>" for i in range(depth)
    ]
    untitled = [f"<< {link} >>" for link in links]

    assert _read_outline(io.BytesIO(_raw_outline_pdf(titled))) == [
        (min(i + 1, 6), f"T{i}", 0) for i in range(depth)
    ]
    assert _read_outline(io.BytesIO(_raw_outline_pdf(untitled))) == []


def test_many_siblings_keep_a_capped_partial_outline():
    # Over 1000 siblings used to hit the recursion limit and lose everything.
    outline = [(f"S{i}", 0, []) for i in range(2500)]
    entries = _read_outline(io.BytesIO(text_pdf([[]], outline)))

    assert len(entries) == 2000
    assert entries[0] == (1, "S0", 0)
    assert entries[-1] == (1, "S1999", 0)


def test_form_pages_get_headings_per_page():
    # Page 1 is a borderless table (pdfplumber form path, no form feeds).
    pages = [
        [
            (20, "Introduction"),
            (11, ["Item", "Qty", "Price"]),
            (11, ["Apple", "2", "1.00"]),
            (11, ["Pear", "5", "2.50"]),
        ],
        [(20, "2 Methods"), (11, BODY)],
    ]
    outline = [("Introduction", 0, []), ("Methods", 1, [])]
    markdown = _convert(text_pdf(pages, outline)).markdown

    assert "\f" not in markdown
    assert any(line.startswith("| Item") for line in _lines(markdown))
    assert _headings(markdown) == ["# Introduction", "# 2 Methods"]
