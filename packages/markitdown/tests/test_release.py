"""Release identity and user-facing installation guidance for the fork."""

import re
import os
import subprocess
import sys
from pathlib import Path

import pytest

from markitdown import __version__


def test_install_hints_use_fork_distribution():
    core = Path(__file__).resolve().parents[1]
    source = core / "src" / "markitdown"
    paths = [core.parents[1] / "README.md", core / "README.md"]
    paths.extend(source.rglob("*.py"))
    paths.append(source / "GUIDE.md")
    upstream_install = re.compile(r"pip install\s+['\"]?markitdown\[")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not upstream_install.search(text), path
    guide = (source / "GUIDE.md").read_text(encoding="utf-8")
    assert "pip install markitdown-gc" in guide


@pytest.mark.parametrize("upstream,gc_version", [(True, __version__), (False, "0.0.0")])
def test_distribution_collision_warns_once_and_cli_refuses(
    tmp_path, upstream, gc_version
):
    for name, version in [("markitdown_gc", gc_version)] + (
        [("markitdown", "0.1.7")] if upstream else []
    ):
        dist = tmp_path / f"{name}-{version}.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name.replace('_', '-')}\nVersion: {version}\n"
        )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(tmp_path), str(Path(__file__).resolve().parents[1] / "src")]
        ),
    }
    code = """
import warnings
with warnings.catch_warnings(record=True) as seen:
    warnings.simplefilter('always')
    import markitdown
    import markitdown
    import importlib
    importlib.reload(markitdown)
assert markitdown.MarkItDown
messages = [str(w.message) for w in seen if 'dedicated virtual environment' in str(w.message)]
assert len(messages) == 1, messages
print(messages[0])
"""
    imported = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert imported.returncode == 0, imported.stderr
    for args in (["--version"], ["--guide", "install"], ["missing.doc"]):
        cli = subprocess.run(
            [sys.executable, "-m", "markitdown", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert cli.returncode == 1, cli.stderr
        assert not cli.stdout
        assert "markitdown-gc" in cli.stderr
        assert "python -m venv" in cli.stderr
        assert "pip install" in cli.stderr
        assert "Traceback" not in cli.stderr


def test_install_guide_warns_about_upstream_plugins():
    guide = Path(__file__).resolve().parents[1] / "src/markitdown/GUIDE.md"
    install = guide.read_text(encoding="utf-8").split("## install", 1)[1]
    assert "Never co-install" in install
    assert "plugins" in install
    assert "dedicated" in install
