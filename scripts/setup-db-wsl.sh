#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../backend"

# Keep uv's cache writable in restricted WSL environments. Override this in
# the shell when a persistent cache location is preferred.
export UV_CACHE_DIR="${UV_CACHE_DIR:-${TMPDIR:-/tmp}/music-agent-uv-cache}"

exec uv run python scripts/setup_db.py
