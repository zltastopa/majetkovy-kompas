#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/Users/mrshu/work/dev/zltastopa/majetkovy-kompas"
UV_BIN="/Users/mrshu/.local/bin/uv"
LOG_DIR="$REPO_DIR/logs"
LOCK_DIR="/tmp/majetkovy-kompas-local-evidence-daily.lock"

mkdir -p "$LOG_DIR"
exec >> "$LOG_DIR/local-evidence-daily.log" 2>&1

echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') starting local evidence daily run ==="

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another local evidence daily run is already active; exiting."
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

cd "$REPO_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Refusing to run with uncommitted changes in $REPO_DIR"
  git status --short
  exit 1
fi

git fetch origin main
git switch main
git pull --ff-only origin main

EVIDENCE_TSA_URL="${EVIDENCE_TSA_URL:-http://timestamp.digicert.com}" \
  "$UV_BIN" run python run_local_evidence_daily.py --push --publish-releases

echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') finished local evidence daily run ==="
