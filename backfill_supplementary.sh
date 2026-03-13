#!/usr/bin/env bash
set -euo pipefail

# Backfills supplementary users into the data branch.
# For each year (2019-2025), restores the existing data from the old
# commit, scrapes only the supplementary users for that year, and
# creates a new commit.  build_site.py picks the last commit per year,
# so these new commits will include both original + supplementary data.
#
# Usage: ./backfill_supplementary.sh

YEARS=(2019 2020 2021 2022 2023 2024 2025)
WORKERS=16
SUPP_FILE="supplementary_user_ids.txt"

if [ ! -f scrape.py ] || [ ! -f "$SUPP_FILE" ]; then
  echo "Run from the repo root (where scrape.py and $SUPP_FILE are)" >&2
  exit 1
fi

MAIN_DIR="$(pwd)"
WORKTREE="$(cd .. && pwd)/data-backfill"

# Ensure data branch is available locally
git fetch origin data:data 2>/dev/null || true

# Clean up any previous worktree
git worktree remove "$WORKTREE" --force 2>/dev/null || true

# Create worktree
git worktree add "$WORKTREE" data
trap 'git worktree remove "$WORKTREE" --force 2>/dev/null || true' EXIT

# Get the commit for each year from the data branch
declare -A YEAR_COMMITS
while IFS= read -r commit_hash; do
  msg=$(git log --format=%s -1 "$commit_hash")
  for word in $msg; do
    if [[ "$word" =~ ^[0-9]{4}$ ]]; then
      YEAR_COMMITS[$word]="$commit_hash"
      break
    fi
  done
done < <(git rev-list --reverse data)

echo "Found commits for years: ${!YEAR_COMMITS[*]}"

for year in "${YEARS[@]}"; do
  echo ""
  echo "=== Year $year ==="

  old_commit="${YEAR_COMMITS[$year]:-}"
  if [ -z "$old_commit" ]; then
    echo "  No existing commit for $year, skipping"
    continue
  fi

  # Restore existing data from the old commit
  rm -rf "$WORKTREE/data"
  mkdir -p "$WORKTREE/data"

  # Use git ls-tree to get file list, then restore each file
  git ls-tree --name-only "$old_commit" -- data/ | while IFS= read -r filepath; do
    filename=$(basename "$filepath")
    git show "$old_commit:$filepath" > "$WORKTREE/data/$filename" 2>/dev/null || true
  done

  existing_count=$(find "$WORKTREE/data" -maxdepth 1 -name '*.yaml' | wc -l | tr -d ' ')
  echo "  Restored $existing_count existing files"

  # Scrape ONLY supplementary users for this year
  cd "$MAIN_DIR"
  uv run python scrape.py \
    --year "$year" \
    --workers "$WORKERS" \
    --only-supplementary \
    --supplementary-ids "$SUPP_FILE" \
    --data-dir "$WORKTREE/data" \
    2>&1 | tail -3

  new_count=$(find "$WORKTREE/data" -maxdepth 1 -name '*.yaml' | wc -l | tr -d ' ')
  added=$((new_count - existing_count))
  echo "  Total: $new_count files (+$added supplementary)"

  # Commit
  cd "$WORKTREE"
  git add data/
  if git diff --cached --quiet; then
    echo "  No new data, skipping commit"
  else
    git commit -F - <<COMMITEOF
data: add supplementary declarations for year $year

Previously the data branch only included officials listed on the
current NRSR index page.  This commit adds declarations from
officials whose pages are still accessible but no longer linked
from the main listing.

- Source user IDs recovered via Wayback Machine snapshot comparison
- Scraped directly from live nrsr.sk individual declaration pages
- Reporting year: $year
COMMITEOF
    echo "  Committed year $year"
  fi
done

echo ""
echo "=== Done ==="
cd "$WORKTREE"
echo "Data branch now has $(git rev-list --count HEAD) commits."
echo ""
echo "Push with:"
echo "  git -C $WORKTREE push origin HEAD:data"
