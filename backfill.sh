#!/usr/bin/env bash
set -euo pipefail

# Creates an orphan 'data' branch with one commit per year (2019-2024).
# Each commit contains all available declarations for that year.
# Run from the repo root: ./backfill.sh

YEARS=(2019 2020 2021 2022 2023 2024)
WORKERS=8
BRANCH="data"

# Ensure we're in the repo root
if [ ! -f scrape.py ]; then
  echo "Run this from the repo root (where scrape.py is)" >&2
  exit 1
fi

# Save current branch to return to later
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Create orphan branch (no history)
git checkout --orphan "$BRANCH"
git rm -rf --cached . 2>/dev/null || true
rm -rf data/

for year in "${YEARS[@]}"; do
  echo ""
  echo "=== Scraping year $year ==="
  rm -rf data/
  uv run python scrape.py --year "$year" --workers "$WORKERS"

  file_count=$(ls data/*.yaml 2>/dev/null | wc -l | tr -d ' ')
  echo "  -> $file_count files for $year"

  if [ "$file_count" -eq 0 ]; then
    echo "  -> No data for $year, skipping commit"
    continue
  fi

  git add data/
  git commit -F - <<COMMITEOF
data: declarations for year $year

Scraped $file_count politician asset declarations from nrsr.sk
for the reporting year $year.

- Source: https://www.nrsr.sk/web/?sid=oznamenia_funkcionarov
- Scrape date: $(date +%Y-%m-%d)
COMMITEOF

  echo "  -> Committed $year"
done

echo ""
echo "=== Done! ==="
echo "Branch '$BRANCH' has $(git rev-list --count HEAD) commits."
echo "Run: git log --oneline $BRANCH"
echo ""

# Return to original branch
git checkout "$ORIGINAL_BRANCH"
