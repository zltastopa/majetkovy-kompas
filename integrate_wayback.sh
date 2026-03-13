#!/usr/bin/env bash
set -euo pipefail

# Integrates Wayback Machine scraped declarations into the data branch
# and ensures proper chronological ordering of all commits.
#
# 1. For each year with new Wayback data: restore existing data from
#    the latest commit for that year, merge in new Wayback files, commit.
# 2. Reorder ALL data branch commits so years go 2011→2012→...→2025,
#    with the baseline and daily check at the end.
#
# Usage: ./integrate_wayback.sh <wayback-output-dir>

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
WORKTREE="$(cd .. && pwd)/data-wayback-integrate"

# Clean up any previous worktree
git worktree remove "$WORKTREE" --force 2>/dev/null || true

# Create worktree on data branch
git worktree add "$WORKTREE" data
trap 'git worktree remove "$WORKTREE" --force 2>/dev/null || true' EXIT

# ── Phase 1: Identify existing year commits ─────────────────────────
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

echo "Data branch has commits for years: $(echo "${!YEAR_COMMITS[*]}" | tr ' ' '\n' | sort | tr '\n' ' ')"

# ── Phase 2: Find years with new Wayback data ───────────────────────
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

IFS=$'\n' WAYBACK_YEARS=($(sort <<<"${WAYBACK_YEARS[*]}")); unset IFS

# ── Phase 3: Create commits for each year ────────────────────────────
new_year_commits=0
for year in "${WAYBACK_YEARS[@]}"; do
  echo ""
  echo "=== Year $year ==="

  old_commit="${YEAR_COMMITS[$year]:-}"
  data_dir="$WORKTREE/data"
  rm -rf "$data_dir"
  mkdir -p "$data_dir"

  if [ -n "$old_commit" ]; then
    git ls-tree --name-only "$old_commit" -- data/ | while IFS= read -r filepath; do
      filename=$(basename "$filepath")
      git show "$old_commit:$filepath" > "$data_dir/$filename" 2>/dev/null || true
    done
    existing=$(find "$data_dir" -maxdepth 1 -name '*.yaml' | wc -l | tr -d ' ')
    echo "  Restored $existing existing files"
  else
    echo "  No existing commit for $year (new year)"
  fi

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

  cd "$WORKTREE"
  git add data/
  if git diff --cached --quiet; then
    echo "  No diff, skipping"
  else
    git commit -F - <<COMMITEOF
data: add live all-years scrape for year $year

Add $added declarations scraped from the live nrsr.sk site by
querying every available year for every known official.

- Source: https://www.nrsr.sk/web/?sid=vnf/oznamenie
- Parsed with scrape.py declaration parser
- Reporting year: $year
- Total declarations for $year after merge: $new_count
COMMITEOF
    echo "  Committed year $year"
    new_year_commits=$((new_year_commits + 1))
  fi
  cd "$MAIN_DIR"
done

if [ "$new_year_commits" -eq 0 ]; then
  echo ""
  echo "No new commits created. Nothing to reorder."
  exit 0
fi

# Update local data ref from worktree
data_head=$(git -C "$WORKTREE" rev-parse HEAD)
git update-ref refs/heads/data "$data_head"

# ── Phase 4: Reorder all commits by year ─────────────────────────────
echo ""
echo "=== Reordering data branch by year ==="

# Collect all commits, extract year, assign sort key
declare -a ALL_COMMITS=()
declare -A COMMIT_YEAR=()
declare -A COMMIT_SORT=()

i=0
while IFS= read -r hash; do
  msg=$(git log --format=%s -1 "$hash")
  year=""
  for word in $msg; do
    if [[ "$word" =~ ^[0-9]{4}$ ]]; then
      year="$word"
      break
    fi
  done

  ALL_COMMITS+=("$hash")
  if [ -n "$year" ]; then
    COMMIT_YEAR[$hash]="$year"
    # Sort key: year * 100 + batch order
    # batch order: original=0, supplementary=1, final supplementary=2, wayback=3
    batch=0
    if [[ "$msg" == *"supplementary"* ]] && [[ "$msg" != *"final"* ]]; then
      batch=1
    elif [[ "$msg" == *"final supplementary"* ]]; then
      batch=2
    elif [[ "$msg" == *"Wayback"* ]]; then
      batch=3
    elif [[ "$msg" == *"live all-years"* ]]; then
      batch=5
    elif [[ "$msg" == *"daily check"* ]]; then
      batch=6
    fi
    COMMIT_SORT[$hash]=$(printf "%d%02d%04d" "$year" "$batch" "$i")
  else
    # No year (baseline etc.) — sort after everything
    COMMIT_SORT[$hash]=$(printf "9999%02d%04d" 0 "$i")
  fi
  i=$((i + 1))
done < <(git rev-list --reverse data)

total=${#ALL_COMMITS[@]}
echo "Total commits: $total"

# Sort commits by sort key
SORTED_HASHES=()
while IFS= read -r line; do
  hash="${line#* }"
  SORTED_HASHES+=("$hash")
done < <(for hash in "${ALL_COMMITS[@]}"; do echo "${COMMIT_SORT[$hash]} $hash"; done | sort)

# Backup
echo "Creating backup ref: refs/backup/data-before-reorder"
git update-ref refs/backup/data-before-reorder data

# Build new commit chain
parent_arg=""
new_hash=""
for old_hash in "${SORTED_HASHES[@]}"; do
  tree=$(git rev-parse "${old_hash}^{tree}")
  msg=$(git log --format=%B -1 "$old_hash")

  export GIT_AUTHOR_NAME=$(git log --format=%an -1 "$old_hash")
  export GIT_AUTHOR_EMAIL=$(git log --format=%ae -1 "$old_hash")
  export GIT_AUTHOR_DATE=$(git log --format=%ai -1 "$old_hash")
  export GIT_COMMITTER_NAME=$(git log --format=%cn -1 "$old_hash")
  export GIT_COMMITTER_EMAIL=$(git log --format=%ce -1 "$old_hash")
  export GIT_COMMITTER_DATE=$(git log --format=%ci -1 "$old_hash")

  new_hash=$(echo "$msg" | git commit-tree $tree $parent_arg)
  parent_arg="-p $new_hash"

  subject=$(echo "$msg" | head -1)
  year_label="${COMMIT_YEAR[$old_hash]:-none}"
  echo "  [$year_label] $subject"
done

git update-ref refs/heads/data "$new_hash"

echo ""
echo "=== Done ==="
echo "Data branch reordered: $total commits"
echo "Backup at: refs/backup/data-before-reorder"
echo ""
echo "Verify: git log --oneline data"
echo "Push:   git push --force origin data"
