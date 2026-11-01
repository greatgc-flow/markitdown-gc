"""Keep publish-path GitHub Actions pinned after the release SHA hardening."""

from collections import Counter
from pathlib import Path
import re

import pytest
import yaml

_RELEASE = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "release.yml"
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
    """Check actual YAML job/step actions, not text embedded in shell scripts.

    This is a source regression check, not upstream SHA provenance validation.
    Local actions are already versioned by the checked-out repository.
    """
    try:
        manifest = yaml.safe_load(source)
    except yaml.YAMLError:
        return ["Invalid release workflow YAML"]

    if not isinstance(manifest, dict) or not isinstance(manifest.get("jobs"), dict):
        return ["Missing release workflow jobs"]

    found = Counter()
    violations = []
    for job in manifest["jobs"].values():
        if not isinstance(job, dict):
            violations.append("Invalid release job")
            continue
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            violations.append("Invalid release job steps")
            continue
        # Includes job-level reusable-workflow refs and ordinary step refs.
        for entry in [job, *steps]:
            if not isinstance(entry, dict):
                violations.append("Invalid release step")
                continue
            if "uses" not in entry:
                continue
            ref = entry["uses"]
            if not isinstance(ref, str):
                violations.append("Invalid release action reference")
            elif ref.startswith("./"):
                continue
            elif not _EXTERNAL_SHA.fullmatch(ref):
                violations.append(f"Floating or invalid release action: {ref}")
            else:
                found[ref.split("@", 1)[0]] += 1

    for name, required_count in _REQUIRED.items():
        if found[name] < required_count:
            violations.append(
                f"Missing pinned release action {name}: "
                f"{found[name]}/{required_count}"
            )
    return violations


def _sample_release() -> str:
    return "jobs:\n  release:\n    steps:\n" + "\n".join(
        f"      - uses: {name}@{'a' * 40} # example"
        for name, count in _REQUIRED.items()
        for _ in range(count)
    )


def test_release_workflow_uses_full_commit_sha_pins():
    violations = _release_action_violations(_RELEASE.read_text(encoding="utf-8"))
    assert not violations, "\n".join(violations)


def test_release_contract_accepts_valid_pins_and_local_actions():
    source = _sample_release() + "\n      - uses: ./.github/actions/local-check\n"
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
    source = _sample_release() + f"\n      - uses: {new_ref}\n"
    assert _release_action_violations(source)


def test_release_contract_rejects_missing_required_action():
    source = _sample_release().replace(
        f"      - uses: pypa/gh-action-pypi-publish@{'a' * 40} # example",
        "      - run: echo 'publisher removed'",
    )
    assert any(
        "Missing pinned release action pypa/gh-action-pypi-publish" in issue
        for issue in _release_action_violations(source)
    )


def test_release_contract_does_not_count_script_text_as_action():
    publisher = f"pypa/gh-action-pypi-publish@{'a' * 40}"
    source = _sample_release().replace(
        f"      - uses: {publisher} # example",
        f"      - run: |\n          echo example\n          - uses: {publisher}",
    )
    assert any(
        "Missing pinned release action pypa/gh-action-pypi-publish" in issue
        for issue in _release_action_violations(source)
    )


def test_release_contract_does_not_count_yaml_comment_as_action():
    publisher = f"pypa/gh-action-pypi-publish@{'a' * 40}"
    source = _sample_release().replace(
        f"      - uses: {publisher} # example",
        f"      # - uses: {publisher}",
    )
    assert any(
        "Missing pinned release action pypa/gh-action-pypi-publish" in issue
        for issue in _release_action_violations(source)
    )


def test_release_contract_rejects_invalid_yaml():
    assert _release_action_violations("jobs: [invalid")
