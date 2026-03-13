# Majetkový kompas — Agent Instructions

## Data Branch Ordering

The `data` branch uses one commit per year-snapshot.  **Commits MUST be
ordered chronologically by reporting year** (oldest year first).  Within
the same year, commits follow the batch order:

1. `chore: add declarations for year YYYY` — original live scrape
2. `data: add supplementary declarations for year YYYY` — supplementary IDs
3. `data: add final supplementary declarations for year YYYY` — expanded supplementary
4. `data: add Wayback Machine declarations for year YYYY` — archive recovery
5. `data: add live all-years scrape for year YYYY` — all-years backfill from live site

The tail of the branch (after all year commits) is:

- `chore: add current declaration baseline` — catch-all snapshot
- `data: daily check for YYYY` — automated daily scrape (CI adds these)

`build_site.py` picks the **last** commit per year (by insertion order)
and sorts by year.  Keeping the branch in chronological order ensures
the dict insertion order already matches, with `sorted()` as a safety net.

### After adding new data

Always reorder the branch so years are chronological.  Use
`integrate_wayback.sh` or the reorder pattern from the repo history:

```bash
# Integrate Wayback-recovered data and reorder
./integrate_wayback.sh /path/to/wayback_output

# Or just reorder (no new data)
# See integrate_wayback.sh Phase 4 for the git commit-tree pattern
```

After reordering, force-push: `git push --force origin data`

## Scraping

| Script | Purpose |
|--------|---------|
| `scrape.py` | Scrape one year for all officials (or a single user) |
| `scrape_all_years.py` | Scrape ALL available years for every official from live nrsr.sk |
| `scrape_wayback.py` | Recover declarations from Wayback Machine archives |

### Supplementary IDs

`supplementary_user_ids.txt` contains ~2,700+ user IDs discovered from
Wayback Machine CDX and archived listing pages.  These are officials
whose pages still exist on nrsr.sk but are no longer linked from the
main listing.  The daily CI scrape includes them automatically.

## Key Invariants

- Each commit on `data` replaces `data/` entirely with that year's YAML files
- The last commit for each year must contain the most complete data
- The branch HEAD must be suitable for the daily CI check to build on
- `build_site.py` reads individual files via `git show COMMIT:data/FILE`
