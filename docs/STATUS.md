# Project Status

Current version: **0.3.6** (see [`__about__.py`](../packages/markitdown/src/markitdown/__about__.py)).

## Release recipe

On `main`, tag the version as `vX.Y.Z`. The [`release.yml`](../.github/workflows/release.yml) workflow builds distributions, verifies the tag matches the package version, and publishes to PyPI using trusted publishing; it also attaches distributions to the GitHub Release.

The release workflow requires a successful [`tests.yml`](../.github/workflows/tests.yml) **push run on `main`** for the tagged commit (`gate` job; PR runs skip `tests-arm` and manual runs do not count); if only infra-flaky jobs failed, rerun them before tagging.
