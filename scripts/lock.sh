#!/usr/bin/env bash
set -euo pipefail
# Generate a hash-pinned lockfile. Requires network access to PyPI.
# Run this in the build environment, then commit the lockfile.

if command -v uv >/dev/null 2>&1; then
  uv lock
else
  python -m pip install pip-tools
  pip-compile --generate-hashes -o requirements.lock pyproject.toml
  pip-compile --generate-hashes --extra dev -o requirements-dev.lock pyproject.toml
fi
