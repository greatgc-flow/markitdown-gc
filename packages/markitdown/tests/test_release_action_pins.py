"""Keep publish-path GitHub Actions pinned after the release SHA hardening."""

from collections import Counter
from pathlib import Path
import re

import pytest


_RELEASE = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "release.yml"
)
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)", re.MULTILINE)
_EXTERNAL_SHA = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*@[0-9a-f]{40}\Z"
)
_REQUIRED = Counter(
    {
        "actions/checkout": 1,
        "actions/setup-python": 1,
        "actions/upload-artifact": 1,
        "actions/download-artifact": 2,
        "pypa/gh-action-pypi-publish": 1,
    }
)


def _release_action_violations(source: str) -> list[str]:
    """Check immutable external refs and the six existing release-path uses.

    This is a source regression check, not upstream SHA provenance validation.
    Same-repository local actions are versioned by the checked-out source.
    """
    found = Counter()
    violations = []
    for raw in _USES.findall(source):
        ref = raw.strip("\"'")
        if ref.startswith("./"):
            continue
        if not _EXTERNAL_SHA.fullmatch(ref):
            violations.append(f"Floating or invalid release action: {ref}")
            continue
        found[ref.split("@", 1)[0]] += 1

    for name, required_count in _REQUIRED.items():
        if found[name] < required_count:
            violations.append(
                f"Missing pinned release action {name}: "
                f"{found[name]}/{required_count}"
            )
    return violations


def _sample_release() -> str:
    return "\n".join(
        f"    - uses: {name}@{'a' * 40} # checked example only"
        for name, count in _REQUIRED.items()
        for _ in range(count)
    )


def test_release_workflow_uses_full_commit_sha_pins():
    violations = _release_action_violations(_RELEASE.read_text(encoding="utf-8"))
    assert not violations, "\n".join(violations)


def test_release_contract_accepts_valid_pins_and_local_actions():
    source = _sample_release() + "\n    - uses: ./.github/actions/local-check\n"
    assert not _release_action_violations(source)


@pytest.mark.parametrize(
    "new_ref",
    [
        "actions/checkout@v7",
        "actions/checkout@main",
        "actions/checkout@1234567",
        "actions/checkout@" + "A" * 40,
        "actions/checkout@" + "$" + "{{ github.ref }}",
        "docker://example/image:latest",
    ],
)
def test_release_contract_rejects_floating_or_invalid_new_action(new_ref):
    source = _sample_release() + f"\n  - uses: {new_ref}\n"
    assert _release_action_violations(source)


def test_release_contract_rejects_missing_required_action():
    source = _sample_release().replace(
        f"    - uses: pypa/gh-action-pypi-publish@{'a' * 40} # checked example only",
        "    # publisher intentionally omitted",
    )
    assert any(
        "Missing pinned release action pypa/gh-action-pypi-publish" in issue
        for issue in _release_action_violations(source)
    )
