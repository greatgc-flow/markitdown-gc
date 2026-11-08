# Project Status

Current version: **0.3.5** (see [`__about__.py`](../packages/markitdown/src/markitdown/__about__.py)).

## Release recipe

On `main`, tag the version as `vX.Y.Z`. The [`release.yml`](../.github/workflows/release.yml) workflow builds distributions, verifies the tag matches the package version, and publishes to PyPI using trusted publishing; it also attaches distributions to the GitHub Release.

## Follow-ups

- The release workflow does not gate publishing on [`tests.yml`](../.github/workflows/tests.yml); tracked in [#57](https://github.com/greatgc-flow/markitdown-gc/issues/57).
