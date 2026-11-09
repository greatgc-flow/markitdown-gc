"""Explicit local pre-conversion of legacy documents, followed by native extraction."""

import io
import math
import re
from pathlib import Path
import tempfile
import time
import zipfile
from typing import Any, BinaryIO, Optional

from .. import _libreoffice as lo
from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import MissingDependencyException
from .._stream_info import StreamInfo
from ._docx_converter import DocxConverter
from ._pptx_converter import PptxConverter


class LibreOfficeConverter(DocumentConverter):
    def __init__(self, libreoffice_path, *, timeout: float = 60):
        self.path = libreoffice_path
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("libreoffice_timeout must be finite and positive")
        self.timeout = timeout

    def _extension(
        self, file_stream: BinaryIO, stream_info: StreamInfo
    ) -> Optional[str]:
        extension = (stream_info.extension or "").lower()
        if not extension:
            extension = {
                "application/msword": ".doc",
                "application/vnd.ms-powerpoint": ".ppt",
                "application/rtf": ".rtf",
                "text/rtf": ".rtf",
                "application/x-hwp": ".hwp",
                "application/haansofthwp": ".hwp",
            }.get((stream_info.mimetype or "").lower().split(";", 1)[0], "")
        if extension == ".hwp":
            position = file_stream.tell()
            try:
                header = file_stream.read(30)
            finally:
                file_stream.seek(position)
            if not header.startswith(
                (b"HWP Document File V2.", b"HWP Document File V3.")
            ):
                return None
        return extension if extension in (".doc", ".ppt", ".rtf", ".hwp") else None

    def accepts(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> bool:
        return self._extension(file_stream, stream_info) is not None

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        extension = self._extension(file_stream, stream_info)
        if extension is None:
            raise ValueError("Unsupported LibreOffice legacy format")
        executable = lo._resolve(self.path)
        if not executable:
            raise MissingDependencyException(
                "LibreOffice is unavailable. Install it locally and set SOFFICE_PATH "
                "or MarkItDown(libreoffice_path=...). See markitdown-gc --guide install."
            )
        target = "pptx" if extension == ".ppt" else "docx"
        try:
            with tempfile.TemporaryDirectory(
                prefix="markitdown-libreoffice-"
            ) as directory:
                work = Path(directory).resolve()
                profile, output = work / "profile", work / "derived"
                lo._write_profile(profile)
                output.mkdir()
                base = [
                    executable,
                    "-env:UserInstallation=" + profile.as_uri(),
                    "--headless",
                    "--norestore",
                    "--nodefault",
                    "--nofirststartwizard",
                    "--nolockcheck",
                ]
                started = time.monotonic()
                version = lo._execute(
                    [*base, "--version"],
                    cwd=work,
                    timeout_s=self.timeout,
                    output=work / "version.log",
                )
                match = re.search(r"LibreOffice\s+(\d+(?:\.\d+)+)", version)
                if not match:
                    raise ValueError("LibreOffice version unavailable")
                source = work / ("source" + extension)
                source.write_bytes(file_stream.read())
                lo._execute(
                    [
                        *base,
                        "--convert-to",
                        target,
                        "--outdir",
                        str(output),
                        str(source),
                    ],
                    cwd=work,
                    timeout_s=max(0.001, self.timeout - (time.monotonic() - started)),
                    output=work / "convert.log",
                )
                derived = output / ("source." + target)
                if derived.is_symlink():
                    raise ValueError("LibreOffice produced a symlink")
                # Validate the derived container before handing it to the native converter.
                member = (
                    "ppt/presentation.xml" if target == "pptx" else "word/document.xml"
                )
                data = derived.read_bytes()
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if member not in archive.namelist():
                        raise ValueError("LibreOffice produced an invalid " + target)
                converter = PptxConverter() if target == "pptx" else DocxConverter()
                result = converter.convert(
                    io.BytesIO(data), StreamInfo(extension="." + target), **kwargs
                )
                result.markdown = (
                    f"<!-- converted via LibreOffice {match[1]} from {extension}; "
                    "fidelity=DERIVED -->\n\n" + result.markdown
                )
                return result
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise RuntimeError(
                f"LibreOffice pre-conversion failed: {exc}. "
                "Check SOFFICE_PATH and the input document."
            ) from exc
