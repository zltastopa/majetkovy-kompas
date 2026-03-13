# Agents Guide — Majetkový kompas

## Project Overview

Majetkový kompas tracks asset declarations of Slovak public officials.
Data is scraped from [nrsr.sk](https://www.nrsr.sk/web/?sid=oznamenia_funkcionarov)
and from [Wayback Machine](https://web.archive.org/) archives, then
published as a static GitHub Pages site.

## Architecture

### Two branches

- **`main`** — source code: scrapers, build script, frontend, CI workflows
- **`data`** — orphan branch with YAML declaration files, one commit per
  year-snapshot, ordered chronologically (oldest year first)

### Data branch ordering

**Critical invariant:** commits on `data` must be chronologically ordered
by reporting year.  `build_site.py` iterates commits oldest→newest and
picks the last commit for each year.  If years are out of order, the site
will display data incorrectly.

After any data modification, reorder the branch:

1. Collect all commits with `git rev-list --reverse data`
2. Extract the year from each commit message (first 4-digit number)
3. Sort by year, then by batch type (original < supplementary < wayback)
4. Rebuild using `git commit-tree` to preserve trees, only changing parents
5. Force-push: `git push --force origin data`

The `integrate_wayback.sh` script automates this pattern.

### Batch ordering within a year

Multiple commits may exist for the same year.  Canonical order:

1. Original scrape (`chore: add declarations for year YYYY`)
2. Supplementary scrape (`data: add supplementary declarations for year YYYY`)
3. Final supplementary (`data: add final supplementary declarations for year YYYY`)
4. Wayback Machine recovery (`data: add Wayback Machine declarations for year YYYY`)
5. Live all-years scrape (`data: add live all-years scrape for year YYYY`)

The **last** commit for a given year is used by `build_site.py`.

### Branch tail

After all year-specific commits:

- `chore: add current declaration baseline` — full snapshot with `_checks/`
- `data: daily check for YYYY` — HEAD; the daily CI workflow appends here

## Data Sources

### Live nrsr.sk

Each official's declaration page has a year dropdown.  Some officials
have data going back to 2004.

- `scrape.py --year YYYY` — scrape one year for all officials
- `scrape_all_years.py` — scrape **every** available year for **every** official

### Supplementary IDs

`supplementary_user_ids.txt` (~2,700+ IDs) lists officials not on the
current NRSR listing but whose pages still exist.  Discovered by comparing
Wayback Machine snapshots of the listing page against the live version,
plus the full Wayback CDX index of individual declaration page URLs.

### Wayback Machine

`scrape_wayback.py` recovers declarations from archived snapshots for
officials whose data was wiped from the live site.  Input is a JSON
manifest mapping `UserId → [{timestamp, url}]`, built from the
Wayback CDX API:

```
https://web.archive.org/cdx/search/cdx?url=https://www.nrsr.sk/web/Default.aspx?sid=vnf/oznamenie%26UserId=*&output=json
```

## Scraping Workflow

### Full historical scrape

```bash
# 1. Scrape all years for all officials from live site
uv run python scrape_all_years.py --data-dir /tmp/all_years --workers 8

# 2. Integrate into data branch and reorder chronologically
./integrate_wayback.sh /tmp/all_years
# (works for any year-organized output, not just Wayback data)

# 3. Force-push
git push --force origin data
```

### Daily maintenance

The GitHub Actions workflow `check-data.yml` runs daily at 04:17 UTC,
scrapes the latest declarations, and auto-commits to the `data` branch.

## Key Files

| File | Purpose |
|------|---------|
| `scrape.py` | Core scraper — one year, all officials |
| `scrape_all_years.py` | All years × all officials from live site |
| `scrape_wayback.py` | Wayback Machine archive recovery |
| `build_site.py` | Git history → static site generator |
| `integrate_wayback.sh` | Merge year-organized data + reorder branch |
| `supplementary_user_ids.txt` | Extra official IDs not on current listing |
| `generate_content_hashes.py` | Content hash tracking for data integrity |
