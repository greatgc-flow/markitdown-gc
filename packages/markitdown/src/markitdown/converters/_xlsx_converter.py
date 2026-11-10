import io
import re
import sys
import zipfile
import zlib
import warnings
from contextlib import contextmanager
from typing import BinaryIO, Any, Iterator, Optional
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from ._html_converter import HtmlConverter
from ._xls_table import sheet_to_html
from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import MissingDependencyException, MISSING_DEPENDENCY_MESSAGE
from .._stream_info import StreamInfo
from .._text import is_html, strict_text

# Try loading optional (but in this case, required) dependencies
# Save reporting of any exceptions for later
_xlsx_dependency_exc_info = None
try:
    import pandas as pd
    import openpyxl  # noqa: F401
except ImportError:
    _xlsx_dependency_exc_info = sys.exc_info()

# XLS reads through xlrd directly; pandas is not needed (#63).
_xls_dependency_exc_info = None
try:
    import xlrd
except ImportError:
    _xls_dependency_exc_info = sys.exc_info()

ACCEPTED_XLSX_MIME_TYPE_PREFIXES = [
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
]
ACCEPTED_XLSX_FILE_EXTENSIONS = [".xlsx"]

ACCEPTED_XLS_MIME_TYPE_PREFIXES = [
    "application/vnd.ms-excel",
    "application/excel",
]
ACCEPTED_XLS_FILE_EXTENSIONS = [".xls", ".xlsm"]


def _is_ooxml_workbook(file_stream: BinaryIO) -> bool:
    """Inspect only the bounded content-types part; leave the stream untouched."""
    position = file_stream.tell()
    try:
        with zipfile.ZipFile(file_stream) as archive:
            if any(item.flag_bits & 1 for item in archive.infolist()):
                return False
            archive.getinfo("xl/workbook.xml")
            content_types = archive.getinfo("[Content_Types].xml")
            if content_types.file_size > 65536:
                return False
            with archive.open(content_types) as part:
                root = ET.fromstring(part.read(65537))
            return any(
                item.get("PartName") == "/xl/workbook.xml"
                and item.get("ContentType")
                in (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                    "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
                )
                for item in root.findall(
                    "{http://schemas.openxmlformats.org/package/2006/content-types}Override"
                )
            )
    except (
        zipfile.BadZipFile,
        zlib.error,
        EOFError,
        KeyError,
        RuntimeError,
        NotImplementedError,
        ET.ParseError,
        DefusedXmlException,
    ):
        return False
    finally:
        file_stream.seek(position)


# Some producers write the legacy attribute "showZeroes" on <sheetView>, where the
# schema calls it "showZeros". openpyxl rejects the unknown attribute outright, so the
# workbook is repaired by renaming it. The rename is confined to <sheetView> start tags.
_SHEET_VIEW_START_TAG = re.compile(rb"<sheetView(?=[\s/>])[^>]*>")
_SHOW_ZEROES_ATTRIBUTE = re.compile(rb"(?<=[\s])showZeroes(\s*=)")


@contextmanager
def _read_xlsx_sheets(
    file_stream: BinaryIO,
) -> Iterator[tuple[dict[str, Any], BinaryIO]]:
    start_pos = file_stream.tell()
    repaired_stream = None
    try:
        try:
            sheets = pd.read_excel(file_stream, sheet_name=None, engine="openpyxl")
        except TypeError as exc:
            if "showZeroes" not in str(exc):
                raise
            repaired_stream = _repair_sheetview_show_zeroes(file_stream, start_pos)
            sheets = pd.read_excel(repaired_stream, sheet_name=None, engine="openpyxl")
        yield sheets, repaired_stream if repaired_stream is not None else file_stream
    finally:
        if repaired_stream is not None:
            repaired_stream.close()


def _rename_show_zeroes_attribute(data: bytes) -> bytes:
    return _SHEET_VIEW_START_TAG.sub(
        lambda match: _SHOW_ZEROES_ATTRIBUTE.sub(rb"showZeros\1", match.group(0)),
        data,
    )


def _repair_sheetview_show_zeroes(
    file_stream: BinaryIO, start_pos: int = 0
) -> io.BytesIO:
    file_stream.seek(start_pos)
    repaired_stream = io.BytesIO()

    with zipfile.ZipFile(file_stream) as source:
        with zipfile.ZipFile(repaired_stream, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if (
                    item.filename.startswith("xl/worksheets/")
                    and item.filename.endswith(".xml")
                    and b"showZeroes" in data
                ):
                    data = _rename_show_zeroes_attribute(data)
                target.writestr(item, data)

    repaired_stream.seek(0)
    return repaired_stream


class XlsxConverter(DocumentConverter):
    """
    Converts XLSX files to Markdown, with each sheet presented as a separate Markdown table.
    """

    def __init__(self):
        super().__init__()
        self._html_converter = HtmlConverter()

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()

        if extension in ACCEPTED_XLSX_FILE_EXTENSIONS:
            return True

        for prefix in ACCEPTED_XLSX_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True

        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> DocumentConverterResult:
        # Check the dependencies
        if _xlsx_dependency_exc_info is not None:
            raise MissingDependencyException(
                MISSING_DEPENDENCY_MESSAGE.format(
                    converter=type(self).__name__,
                    extension=".xlsx",
                    feature="xlsx",
                )
            ) from _xlsx_dependency_exc_info[
                1
            ].with_traceback(  # type: ignore[union-attr]
                _xlsx_dependency_exc_info[2]
            )

        from .._media import check_media_options, image_html, validate_members

        check_media_options(kwargs)
        if kwargs.get("extract_media"):
            with zipfile.ZipFile(file_stream) as archive:
                validate_members(archive)
            file_stream.seek(0)
        has_images = False
        md_content = ""
        with _read_xlsx_sheets(file_stream) as (sheets, workbook_stream):
            with zipfile.ZipFile(workbook_stream) as archive:
                has_images = any(
                    n.startswith("xl/media/") and not n.endswith("/")
                    for n in archive.namelist()
                )
            images = None
            if (
                kwargs.get("extract_media")
                or type(self)._image_to_html is not XlsxConverter._image_to_html
            ):
                from ..converter_utils._xlsx_images import _XlsxImages

                images = _XlsxImages(workbook_stream)

            for s in sheets:
                md_content += f"## {s}\n"
                html_content = sheets[s].to_html(index=False)
                md_content += (
                    self._html_converter.convert_string(
                        html_content, **kwargs
                    ).markdown.strip()
                    + "\n\n"
                )
                if images is not None:
                    image_content = images.to_html(
                        s,
                        (
                            image_html
                            if kwargs.get("extract_media")
                            else self._image_to_html
                        ),
                        kwargs,
                    )
                    if image_content:
                        md_content += (
                            self._html_converter.convert_string(
                                image_content, **kwargs
                            ).markdown.strip()
                            + "\n\n"
                        )

        return DocumentConverterResult(
            markdown=md_content.strip(),
            image_content_omitted=has_images and not kwargs.get("extract_media"),
        )

    def _image_to_html(
        self,
        image_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> Optional[str]:
        """Override to render an embedded image as an HTML fragment.

        The stream is borrowed, seekable, and positioned at zero; do not close
        or retain it. StreamInfo describes the image, not the workbook. Existing
        conversion options are forwarded through kwargs.

        Return None or blank text to retain the native representation (no image
        output). Otherwise return HTML, escaping any literal text. Images appear
        after their sheet's table, in the existing worksheet image order. Linked
        images are not fetched. Hook failures propagate through the normal
        conversion failure path.
        """
        return None


def _read_xls_sheets(file_stream: BinaryIO) -> list[tuple[str, str]]:
    """Return (sheet name, HTML table) pairs read with xlrd.

    Damaged UTF-16 is tolerated only while xlrd eagerly loads the workbook.
    """
    originals = {
        module: module.unicode
        for name, module in list(sys.modules.items())
        if (name == "xlrd" or name.startswith("xlrd.")) and hasattr(module, "unicode")
    }
    replaced = 0

    def tolerant_unicode(data: bytes, encoding: str) -> str:
        nonlocal replaced
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            if not exc.encoding.replace("-", "_").startswith("utf_16"):
                raise
            text = data.decode(encoding, errors="replace")
            # Exclude literal U+FFFD characters already present in the input.
            replaced += text.count("\ufffd") - data.decode(
                encoding, errors="ignore"
            ).count("\ufffd")
            return text

    try:
        for module in originals:
            module.unicode = tolerant_unicode
        book = xlrd.open_workbook(file_contents=file_stream.read(), on_demand=False)
    finally:
        for module, original in originals.items():
            module.unicode = original
    try:
        sheets = [
            (name, sheet_to_html(book.sheet_by_name(name), book.datemode))
            for name in book.sheet_names()
        ]
    finally:
        book.release_resources()
    if replaced:
        warnings.warn(
            f"Replaced {replaced} invalid UTF-16 sequence(s) in XLS strings with U+FFFD. Verify output.",
            UserWarning,
            stacklevel=2,
        )
    return sheets


class XlsConverter(DocumentConverter):
    """
    Converts XLS files to Markdown, with each sheet presented as a separate Markdown table.
    """

    def __init__(self):
        super().__init__()
        self._html_converter = HtmlConverter()

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()

        if extension in ACCEPTED_XLS_FILE_EXTENSIONS:
            return True

        for prefix in ACCEPTED_XLS_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True

        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> DocumentConverterResult:
        extension = (stream_info.extension or "").lower()
        if extension in ACCEPTED_XLS_FILE_EXTENSIONS:
            position = file_stream.tell()
            try:
                prefix = file_stream.read(8192)
                file_stream.seek(position)
                text = None
                result = None
                if prefix.startswith(b"PK\x03\x04") and _is_ooxml_workbook(file_stream):
                    result = XlsxConverter().convert(file_stream, stream_info, **kwargs)
                    detected = "XLSX"
                elif extension != ".xls":
                    pass
                elif prefix.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
                    try:
                        text = strict_text(file_stream.read())
                    except (UnicodeError, ValueError):
                        pass
                elif is_html(prefix.decode("ascii", errors="replace")):
                    result = self._html_converter.convert(
                        file_stream, stream_info, **kwargs
                    )
                    detected = "HTML"
                if text is not None:
                    detected = "HTML" if is_html(text) else "text"
                    result = (
                        self._html_converter.convert_string(text, **kwargs)
                        if detected == "HTML"
                        else DocumentConverterResult(markdown=text)
                    )
                if result is not None:
                    warnings.warn(
                        f"Spreadsheet extension does not match detected content; converted as {detected}. Verify output.",
                        UserWarning,
                        stacklevel=2,
                    )
                    return result
            finally:
                file_stream.seek(position)

        # Load the dependencies
        if _xls_dependency_exc_info is not None:
            raise MissingDependencyException(
                MISSING_DEPENDENCY_MESSAGE.format(
                    converter=type(self).__name__,
                    extension=".xls",
                    feature="xls",
                )
            ) from _xls_dependency_exc_info[
                1
            ].with_traceback(  # type: ignore[union-attr]
                _xls_dependency_exc_info[2]
            )

        md_content = ""
        for s, html_content in _read_xls_sheets(file_stream):
            md_content += f"## {s}\n"
            md_content += (
                self._html_converter.convert_string(
                    html_content, **kwargs
                ).markdown.strip()
                + "\n\n"
            )

        return DocumentConverterResult(markdown=md_content.strip())
