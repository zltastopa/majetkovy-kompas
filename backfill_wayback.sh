#!/usr/bin/env bash
set -euo pipefail

# Integrates Wayback Machine scraped declarations into the data branch.
# For each year with scraped data, restores the existing data from the
# current data branch commit, adds the Wayback-scraped files, and
# creates a new commit.
#
# Usage: ./backfill_wayback.sh <wayback-output-dir>
# Example: ./backfill_wayback.sh /tmp/wayback_full_output

WAYBACK_DIR="${1:?Usage: $0 <wayback-output-dir>}"

if [ ! -f scrape.py ]; then
  echo "Run from the repo root (where scrape.py is)" >&2
  exit 1
fi

if [ ! -d "$WAYBACK_DIR" ]; then
  echo "Wayback output directory not found: $WAYBACK_DIR" >&2
  exit 1
fi

MAIN_DIR="$(pwd)"
WORKTREE="$(cd .. && pwd)/data-wayback-backfill"

# Ensure data branch is up to date
git fetch origin data:data 2>/dev/null || true

# Clean up any previous worktree
git worktree remove "$WORKTREE" --force 2>/dev/null || true

# Create worktree
git worktree add "$WORKTREE" data
trap 'git worktree remove "$WORKTREE" --force 2>/dev/null || true' EXIT

# Get the commit for each year from the data branch (last commit wins)
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

echo "Data branch has commits for years: ${!YEAR_COMMITS[*]}"

# Find which years have Wayback data
WAYBACK_YEARS=()
for year_dir in "$WAYBACK_DIR"/*/; do
  year=$(basename "$year_dir")
  if [[ "$year" =~ ^[0-9]{4}$ ]]; then
    file_count=$(find "$year_dir" -maxdepth 1 -name '*.yaml' | wc -l | tr -d ' ')
    if [ "$file_count" -gt 0 ]; then
      WAYBACK_YEARS+=("$year")
      echo "  Wayback $year: $file_count declarations"
    fi
  fi
done

if [ ${#WAYBACK_YEARS[@]} -eq 0 ]; then
  echo "No Wayback data to integrate."
  exit 0
fi

# Sort years
IFS=$'\n' WAYBACK_YEARS=($(sort <<<"${WAYBACK_YEARS[*]}")); unset IFS

for year in "${WAYBACK_YEARS[@]}"; do
  echo ""
  echo "=== Year $year ==="

  old_commit="${YEAR_COMMITS[$year]:-}"

  data_dir="$WORKTREE/data"
  rm -rf "$data_dir"
  mkdir -p "$data_dir"

  if [ -n "$old_commit" ]; then
    # Restore existing data from the old commit
    git ls-tree --name-only "$old_commit" -- data/ | while IFS= read -r filepath; do
      filename=$(basename "$filepath")
      git show "$old_commit:$filepath" > "$data_dir/$filename" 2>/dev/null || true
    done
    existing=$(find "$data_dir" -maxdepth 1 -name '*.yaml' | wc -l | tr -d ' ')
    echo "  Restored $existing existing files"
  else
    echo "  No existing commit for $year (new year)"
  fi

  # Copy Wayback-scraped files (won't overwrite existing — only add new)
  added=0
  for yaml_file in "$WAYBACK_DIR/$year"/*.yaml; do
    filename=$(basename "$yaml_file")
    if [ ! -f "$data_dir/$filename" ]; then
      cp "$yaml_file" "$data_dir/$filename"
      added=$((added + 1))
    fi
  done

  new_count=$(find "$data_dir" -maxdepth 1 -name '*.yaml' | wc -l | tr -d ' ')
  echo "  Total: $new_count files (+$added from Wayback)"

  if [ "$added" -eq 0 ]; then
    echo "  No new data, skipping commit"
    continue
  fi

  # Commit
  cd "$WORKTREE"
  git add data/
  if git diff --cached --quiet; then
    echo "  No diff, skipping"
  else
    git commit -F - <<COMMITEOF
data: add Wayback Machine declarations for year $year

Add $added archived declarations recovered from the Wayback Machine
for officials whose data was wiped from the live NRSR site.

- Fetched from web.archive.org archived snapshots
- Parsed with the same scrape.py declaration parser
- Reporting year: $year
COMMITEOF
    echo "  Committed year $year"
  fi
  cd "$MAIN_DIR"
done

echo ""
echo "=== Done ==="
cd "$WORKTREE"
echo "Data branch now has $(git rev-list --count HEAD) commits."
echo ""
echo "Verify with: git -C $WORKTREE log --oneline"
echo "Push with:   git push origin data"
