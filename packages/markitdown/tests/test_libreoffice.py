"""Opt-in legacy routing, isolated fake soffice, and real-tool smoke test."""

import io
from pathlib import Path
import shutil
import sys
import time

import pytest

from markitdown import (
    FileConversionException,
    MarkItDown,
    StreamInfo,
    UnsupportedFormatException,
)


def test_isolated_profile_disables_active_content(tmp_path):
    import xml.etree.ElementTree as ET
    from markitdown import _libreoffice as lo

    lo._write_profile(tmp_path / "profile")
    root = ET.parse(tmp_path / "profile/user/registrymodifications.xcu").getroot()
    properties = {
        prop.attrib["{http://openoffice.org/2001/registry}name"]: prop.findtext("value")
        for prop in root.iter("prop")
    }
    assert properties["DisableMacrosExecution"] == "true"
    assert properties["DisableActiveContent"] == "true"
    assert properties["Enabled"] == "false"


@pytest.fixture
def fake_soffice(tmp_path, monkeypatch):
    from markitdown import _libreoffice as lo

    script = tmp_path / "soffice.py"
    script.write_text(
        """
from pathlib import Path
import shutil, sys
args = sys.argv[1:]
profile = next(a for a in args if a.startswith('-env:UserInstallation='))
assert profile.startswith('-env:UserInstallation=file:')
assert '--headless' in args and '--norestore' in args
if '--version' in args:
    print('LibreOffice 25.2.1.2 fake')
else:
    target = args[args.index('--convert-to') + 1]
    out = Path(args[args.index('--outdir') + 1])
    source = Path(args[-1])
    shutil.copyfile(Path(__file__).with_name('test.' + target), out / (source.stem + '.' + target))
""",
        encoding="utf-8",
    )
    fixtures = Path(__file__).parent / "test_files"
    for suffix in ("docx", "pptx"):
        shutil.copyfile(fixtures / f"test.{suffix}", tmp_path / f"test.{suffix}")
    calls = []
    execute = lo._execute

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return execute([sys.executable, str(script), *args[1:]], **kwargs)

    monkeypatch.setattr(lo, "_execute", run)
    monkeypatch.setattr(lo, "_resolve", lambda path: str(script))
    return calls


@pytest.mark.parametrize(
    "extension,data",
    [
        (".doc", b"legacy doc"),
        (".ppt", b"legacy ppt"),
        (".rtf", b"{\\rtf1 legacy}"),
        (".hwp", b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05"),
    ],
)
def test_legacy_delegates_and_marks_derived(fake_soffice, extension, data):
    result = MarkItDown(libreoffice_path="fake").convert_stream(
        io.BytesIO(data), stream_info=StreamInfo(extension=extension)
    )
    assert result.markdown.startswith(
        f"<!-- converted via LibreOffice 25.2.1.2 from {extension}; fidelity=DERIVED -->"
    )
    assert len(result.markdown) > 150
    assert len(fake_soffice) == 2
    work = fake_soffice[0][1]["cwd"]
    assert not work.exists()
    assert fake_soffice[1][1]["cwd"] == work


def test_disabled_even_when_on_path(monkeypatch):
    from markitdown.converters import LibreOfficeConverter

    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    md = MarkItDown()
    assert not any(
        isinstance(r.converter, LibreOfficeConverter) for r in md._converters
    )
    md._magika = None
    with pytest.raises(UnsupportedFormatException):
        md.convert_stream(
            io.BytesIO(b"\x00\x01"), stream_info=StreamInfo(extension=".doc")
        )


def test_env_enables(fake_soffice, monkeypatch):
    monkeypatch.setenv("SOFFICE_PATH", "fake")
    result = MarkItDown().convert_stream(
        io.BytesIO(b"x"), stream_info=StreamInfo(extension=".doc")
    )
    assert "fidelity=DERIVED" in result.markdown


def test_failed_opt_in_does_not_return_raw_rtf(monkeypatch):
    from markitdown import _libreoffice as lo

    monkeypatch.setattr(lo, "_resolve", lambda path: None)
    with pytest.raises(FileConversionException, match="SOFFICE_PATH"):
        MarkItDown(libreoffice_path="missing").convert_stream(
            io.BytesIO(b"{\\rtf1 document}"),
            stream_info=StreamInfo(extension=".rtf", mimetype="text/rtf"),
        )


@pytest.mark.parametrize(
    "mimetype",
    [
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/rtf",
        "text/rtf",
    ],
)
def test_mime_only_routing(fake_soffice, mimetype):
    result = MarkItDown(libreoffice_path="fake").convert_stream(
        io.BytesIO(b"legacy"), stream_info=StreamInfo(mimetype=mimetype)
    )
    assert result.markdown.startswith("<!-- converted via LibreOffice ")


def test_native_hwp_remains_native(fake_soffice):
    from ._hwpx_builder import build_hwpx

    result = MarkItDown(libreoffice_path="fake").convert_stream(
        io.BytesIO(build_hwpx()), stream_info=StreamInfo(extension=".hwpx")
    )
    assert result.markdown
    assert "converted via LibreOffice" not in result.markdown
    assert not fake_soffice


@pytest.mark.parametrize(
    "data", [b"HWP Document File V2.00 ", b"HWP Document File V3.00 "]
)
def test_hwp_legacy_signature_only_and_position_preserved(data):
    from markitdown.converters import LibreOfficeConverter

    converter = LibreOfficeConverter("fake")
    for payload, expected in [
        (data, True),
        (b"HWP Document File V5.00 ", False),
        (b"\xd0\xcf\x11\xe0", False),
        (b"PK\x03\x04", False),
        (b"garbage", False),
    ]:
        stream = io.BytesIO(b"prefix" + payload)
        stream.seek(6)
        assert converter.accepts(stream, StreamInfo(extension=".hwp")) == expected
        assert stream.tell() == 6


@pytest.mark.parametrize(
    "failure", ["timeout", "nonzero", "no-output", "bad-output", "version", "delegate"]
)
def test_failures_cleanup_and_remain_visible(tmp_path, monkeypatch, failure):
    from markitdown import _libreoffice as lo
    from markitdown.converters import LibreOfficeConverter

    works = []
    monkeypatch.setattr(lo, "_resolve", lambda path: "fake")

    def execute(args, **kwargs):
        works.append(kwargs["cwd"])
        if failure == "timeout":
            raise TimeoutError("LibreOffice timed out")
        if failure == "nonzero":
            raise OSError("LibreOffice failed")
        if "--version" in args:
            return "unknown" if failure == "version" else "LibreOffice 25.2"
        if failure in ("bad-output", "delegate"):
            target = Path(args[args.index("--outdir") + 1]) / "source.docx"
            if failure == "bad-output":
                target.write_bytes(b"bad")
            else:
                shutil.copyfile(Path(__file__).parent / "test_files/test.docx", target)
        return ""

    monkeypatch.setattr(lo, "_execute", execute)
    if failure == "delegate":
        from markitdown.converters import DocxConverter

        def fail(*args, **kwargs):
            raise ValueError("delegate failed")

        monkeypatch.setattr(DocxConverter, "convert", fail)
    md = MarkItDown(enable_builtins=False)
    md.register_converter(LibreOfficeConverter("fake", timeout=1))
    with pytest.raises(FileConversionException, match="LibreOffice|delegate"):
        md.convert_stream(
            io.BytesIO(b"legacy"), stream_info=StreamInfo(extension=".doc")
        )
    assert works and all(not work.exists() for work in works)


def test_missing_explicit_tool_reports_fix(monkeypatch):
    from markitdown.converters import LibreOfficeConverter

    md = MarkItDown(enable_builtins=False)
    md.register_converter(LibreOfficeConverter("no-such-soffice"))
    with pytest.raises(FileConversionException, match="SOFFICE_PATH"):
        md.convert_stream(
            io.BytesIO(b"legacy"), stream_info=StreamInfo(extension=".doc")
        )


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_timeout_must_be_bounded(timeout):
    from markitdown.converters import LibreOfficeConverter

    with pytest.raises(ValueError, match="finite and positive"):
        LibreOfficeConverter("fake", timeout=timeout)


@pytest.mark.parametrize("parent_exits", [False, True])
def test_process_tree_is_killed(tmp_path, parent_exits):
    from markitdown import _libreoffice as lo

    heartbeat = tmp_path / "heartbeat"
    child = (
        "import pathlib,time; p=pathlib.Path("
        + repr(str(heartbeat))
        + "); exec('while True:\\n p.write_text(str(time.time())); time.sleep(.02)')"
    )
    parent = (
        "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', "
        + repr(child)
        + "]); time.sleep(.3); "
        + ("pass" if parent_exits else "time.sleep(30)")
    )
    if parent_exits:
        lo._execute(
            [sys.executable, "-c", parent],
            cwd=tmp_path,
            timeout_s=3,
            output=tmp_path / "run.log",
        )
    else:
        with pytest.raises(TimeoutError):
            lo._execute(
                [sys.executable, "-c", parent],
                cwd=tmp_path,
                timeout_s=1,
                output=tmp_path / "run.log",
            )
    assert heartbeat.exists()
    stamp = heartbeat.read_text()
    time.sleep(0.15)
    assert heartbeat.read_text() == stamp
    assert not (tmp_path / "run.log").exists()


@pytest.mark.parametrize(
    "code,exception",
    [
        ("import sys; sys.exit(4)", OSError),
        (
            "import sys,time; sys.stdout.write('x'*70000); "
            "sys.stdout.flush(); time.sleep(5)",
            TimeoutError,
        ),
    ],
)
def test_executable_failure_and_output_limit(tmp_path, code, exception):
    from markitdown import _libreoffice as lo

    with pytest.raises(exception, match="LibreOffice"):
        lo._execute(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            timeout_s=3,
            output=tmp_path / "run.log",
        )
    assert not (tmp_path / "run.log").exists()


def test_cli_libreoffice_flag(fake_soffice, monkeypatch, tmp_path, capsys):
    from markitdown.__main__ import main

    source = tmp_path / "test.doc"
    source.write_bytes(b"legacy")
    monkeypatch.setattr(sys, "argv", ["markitdown-gc", str(source), "--libreoffice"])
    main()
    assert "converted via LibreOffice" in capsys.readouterr().out


def test_real_libreoffice(tmp_path):
    executable = shutil.which("soffice")
    if not executable:
        for name in ("soffice.com", "soffice.exe", "soffice"):
            candidate = Path("C:/Program Files/LibreOffice/program") / name
            if candidate.is_file():
                executable = str(candidate)
                break
    if not executable:
        pytest.skip(
            "LibreOffice soffice is not installed "
            "(PATH or C:/Program Files/LibreOffice/program)"
        )
    source = tmp_path / "owned.rtf"
    source.write_bytes(b"{\\rtf1\\ansi Legacy LibreOffice smoke text.}")
    result = MarkItDown(libreoffice_path=executable).convert(source)
    assert "Legacy LibreOffice smoke text." in result.markdown
    assert result.markdown.startswith("<!-- converted via LibreOffice ")
