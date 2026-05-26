#!/usr/bin/env bash
set -euo pipefail

SITE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKTREE="/tmp/nmbr7-deploy-master"
MSG="${1:-Deploy site build}"

cd "$SITE_ROOT"

echo "Building..."
python3 scripts/sync_notes.py
zola build

echo "Preparing master worktree..."
git worktree prune
git worktree add "$WORKTREE" master

find "$WORKTREE" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
rsync -a public/ "$WORKTREE"/

cd "$WORKTREE"
git add -A
if git diff --cached --quiet; then
  echo "Nothing to deploy — master already up to date."
else
  git commit -m "$MSG"
  git push origin master
fi

cd "$SITE_ROOT"
git worktree remove "$WORKTREE"

echo "Deployed."
