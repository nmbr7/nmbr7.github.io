#!/usr/bin/env bash
# sync notes then run zola serve
set -euo pipefail

cd "$(dirname "$0")/.."

python3 scripts/sync_notes.py
zola serve "$@"
