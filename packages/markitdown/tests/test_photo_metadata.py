import json
from pathlib import Path

import pytest


def test_selected_fields_only_and_original_remains_intact(monkeypatch, tmp_path):
    from markitdown import photo_metadata, tools

    executable = str(tmp_path / "exiftool")
    data = b"\x89PNG\r\n\x1a\nfake"
    monkeypatch.setattr(
        tools, "probe_exiftool", lambda *a, **kw: {"status": "ok", "path": executable}
    )
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert Path(args[-1]).read_bytes() == data
        return type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    [{"GPSLatitude": 23, "ImageWidth": 42, "SourceFile": "private"}]
                ),
            },
        )()

    monkeypatch.setattr(tools, "run_tool", run)
    assert photo_metadata.read_photo_info(
        data, fields=["ImageWidth"], executable=executable
    ) == {"status": "ok", "fields": {"ImageWidth": 42}}
    assert calls[0][1:-1] == ["-j", "-ImageWidth"]
    assert not Path(calls[0][-1]).exists()
    with pytest.raises(ValueError):
        photo_metadata.read_photo_info(data, fields=["-unknown"], executable=executable)
    assert len(calls) == 1
