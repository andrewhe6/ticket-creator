#!/usr/bin/env bash
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
cp -R "$here/files/." .
# The stand-in GitHub CLI lives with the unit tests; use the current copy. The config's
# github.cli points at it, and .fakegh/fake_gh_state.json is the simulated board.
cp "$here/../../tests/fake_gh.py" .fakegh/fake_gh.py
