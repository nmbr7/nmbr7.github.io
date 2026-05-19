#!/usr/bin/env bash
# sync notes then run zola build
set -euo pipefail

cd "$(dirname "$0")/.."

python3 scripts/sync_notes.py
zola build "$@"
