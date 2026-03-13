#!/usr/bin/env python3
"""
Scraper for NR SR asset declarations (majetkové priznania).

Produces one YAML file per politician in data/<UserId>.yaml.
Git tracks yearly changes — each scrape run updates the files,
and `git diff` shows exactly what changed.
"""

import re
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

BASE_URL = "https://www.nrsr.sk/web/"
LIST_URL = f"{BASE_URL}Default.aspx?sid=vnf/zoznam"
DECL_URL = f"{BASE_URL}Default.aspx?sid=vnf/oznamenie&UserId="
DATA_DIR = Path("data")


# ---------------------------------------------------------------------------
# YAML config: keep output deterministic and diff-friendly
# ---------------------------------------------------------------------------

class OrderedDumper(yaml.SafeDumper):
    pass

def _dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())

OrderedDumper.add_representer(dict, _dict_representer)


def dump_yaml(data):
    return yaml.dump(
        data,
        Dumper=OrderedDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch(url):
    resp = requests.get(url, timeout=30)
    resp.encoding = "utf-8"
    resp.raise_for_status()
    return resp.text


def fetch_politician_list():
    """Return list of dicts with 'user_id' and 'display_name'."""
    html = fetch(LIST_URL)
    return parse_politician_list(html)


def parse_politician_list(html):
    """Return list of dicts with 'user_id' and 'display_name'."""
    soup = BeautifulSoup(html, "html.parser")
    politicians = []
    for link in soup.select('a[href*="vnf/oznamenie"]'):
        href = link.get("href", "")
        if "UserId=" not in href:
            continue
        user_id = href.split("UserId=")[-1]
        display_name = link.get_text(strip=True)
        politicians.append({"user_id": user_id, "display_name": display_name})
    return politicians


def parse_available_years(html):
    """Return available declaration years and the selected dropdown year."""
    soup = BeautifulSoup(html, "html.parser")
    dropdown = soup.select_one("#_sectionLayoutContainer_ctl01_OznameniaList")
    if not dropdown:
        return [], None

    years = []
    selected_year = None
    for opt in dropdown.select("option"):
        try:
            year = int(opt.get_text(strip=True))
        except ValueError:
            continue
        years.append(year)
        if opt.has_attr("selected"):
            selected_year = year

    years = sorted(set(years), reverse=True)
    return years, selected_year


def fetch_declaration_html(user_id, year=None):
    """Fetch the declaration page for a given UserId.
    If year is None, returns the latest year.
    If year is given, performs ASP.NET postback to select that year."""
    url = f"{DECL_URL}{user_id}"
    html = fetch(url)
    if year is None:
        return html

    # Find available years and their declaration IDs
    soup = BeautifulSoup(html, "html.parser")
    dropdown = soup.select_one("#_sectionLayoutContainer_ctl01_OznameniaList")
    if not dropdown:
        return None  # no year dropdown = no data available
    year_map = {}
    for opt in dropdown.select("option"):
        try:
            y = int(opt.get_text(strip=True))
            year_map[y] = opt.get("value", "")
        except ValueError:
            pass

    if year not in year_map:
        return None  # requested year not available for this politician

    # Already showing the requested year
    selected = dropdown.select_one("option[selected]")
    if selected and selected.get_text(strip=True) == str(year):
        return html

    # Perform ASP.NET postback
    viewstate = soup.select_one("#__VIEWSTATE")
    validation = soup.select_one("#__EVENTVALIDATION")
    viewstate_gen = soup.select_one("#__VIEWSTATEGENERATOR")
    post_data = {
        "__EVENTTARGET": "_sectionLayoutContainer$ctl01$OznameniaList",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": viewstate["value"] if viewstate else "",
        "__EVENTVALIDATION": validation["value"] if validation else "",
        "__VIEWSTATEGENERATOR": viewstate_gen["value"] if viewstate_gen else "",
        "_sectionLayoutContainer$ctl01$OznameniaList": year_map[year],
    }
    resp = requests.post(url, data=post_data, timeout=30)
    resp.encoding = "utf-8"
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_declaration(html):
    """Parse declaration HTML into a structured dict. Returns None if unavailable."""
    if html is None:
        return None
    soup = BeautifulSoup(html, "html.parser")
    output = soup.select_one("#_sectionLayoutContainer_ctl01_OutPut")
    if not output:
        return None
    if "štádiu spracovania" in output.get_text():
        return None

    table = output.select_one("table.oznamenie_table")
    if not table:
        return None

    # Build ordered list of (label, value_td) pairs
    pairs = []
    current_label = None
    for row in table.select("tr"):
        label_td = row.select_one("td.label")
        value_td = row.select_one("td.value")
        if label_td:
            current_label = label_td.get_text(strip=True).rstrip(":\xa0 ")
        if value_td and current_label:
            pairs.append((current_label, value_td))

    return extract_structured(dict(pairs))


def extract_structured(raw):
    """Convert {sk_label: value_td} into a clean dict for YAML."""
    result = {}

    # Simple text fields
    for sk, en in [
        ("Interné číslo", "id"),
        ("ID oznámenia", "declaration_id"),
        ("titul, meno, priezvisko", "name"),
        ("oznámenie za rok", "year"),
        ("oznámenie bolo podané", "filed"),
    ]:
        if sk in raw:
            val = raw[sk].get_text(strip=True)
            if en in ("year", "declaration_id"):
                try:
                    val = int(val)
                except ValueError:
                    pass
            result[en] = val

    # Public function
    key = _find_key(raw, "vykonávaná verejná funkcia")
    if key:
        public_functions = _parse_public_functions(raw[key])
        result["public_function"] = "\n".join(public_functions) if public_functions else None
        result["public_functions"] = public_functions or None

    # Income
    key = _find_key(raw, "príjmy za rok")
    if key:
        result["income"] = _parse_income(raw[key].get_text(strip=True))

    # Incompatibility
    key = _find_key(raw, "nezlučiteľnosti")
    if key:
        val = raw[key].get_text(strip=True)
        result["incompatibility"] = val == "áno"

    # Employment
    key = _find_key(raw, "zamestnanie")
    if key:
        val = _null_if(raw[key], "nevykonávam")
        if val:
            # Separate employer from additional info (e.g., "Dlhodobo plne uvoľnený...")
            result["employment"] = "\n".join(raw[key].stripped_strings)
        else:
            result["employment"] = None

    # Business activity
    key = _find_key(raw, "podnikateľskú činnosť")
    if key:
        result["business_activity"] = _null_if(raw[key], "nevykonávam")

    # Positions
    key = _find_key(raw, "tieto funkcie")
    if key:
        positions = _parse_positions(raw[key])
        # "nevykonávam" gets parsed as a single-item list with no org
        if positions and len(positions) == 1 and "nevykonávam" in (positions[0].get("role") or "").lower():
            positions = None
        result["positions"] = positions

    # Real estate
    if "vlastníctvo nehnuteľnej veci" in raw:
        result["real_estate"] = _parse_real_estate(raw["vlastníctvo nehnuteľnej veci"])

    # Movable property (owned vehicles, etc.)
    if "vlastníctvo hnuteľnej veci" in raw:
        td = raw["vlastníctvo hnuteľnej veci"]
        if td.get_text(strip=True).lower() == "nevlastním":
            result["movable_property"] = None
        else:
            result["movable_property"] = _parse_movable_property(td)

    # Property rights
    key = _find_key(raw, "majetkového práva")
    if key:
        result["property_rights"] = _parse_divs_as_text(raw[key])

    # Obligations
    if "existencia záväzku" in raw:
        result["obligations"] = _parse_obligations(raw["existencia záväzku"])

    # Use of others' real estate
    key = _find_key(raw, "užívanie nehnuteľnej")
    if key:
        result["use_of_others_real_estate"] = _null_if(raw[key], "neužívam")

    # Use of others' movable property (vehicles etc.)
    key = _find_key(raw, "užívanie hnuteľnej")
    if key:
        result["vehicles"] = _parse_vehicles(raw[key])

    # Gifts
    if "prijaté dary alebo iné výhody" in raw:
        result["gifts"] = _null_if(raw["prijaté dary alebo iné výhody"], "žiadne")

    return result


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------

def _find_key(d, substring):
    for k in d:
        if substring in k:
            return k
    return None


def _parse_public_functions(td):
    entries = []
    divs = td.select("div")
    if divs:
        for div in divs:
            text = " ".join(div.stripped_strings)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                entries.append(text)
        return _sorted_text_items(entries)

    text = " ".join(td.stripped_strings)
    text = re.sub(r"\s+", " ", text).strip()
    return _sorted_text_items([text] if text else [])


def _null_if(td, null_text):
    text = td.get_text(strip=True)
    if text.lower() == null_text.lower():
        return None
    return text


def _parse_income(text):
    """Parse '76808 € (z výkonu verejnej funkcie), 9000 € (iné)' into dict."""
    income = {}
    for part in text.split("),"):
        part = part.strip().rstrip(")")
        m = re.match(r"([\d\s]+)\s*€\s*\((.+)", part)
        if m:
            amount = int(m.group(1).replace(" ", ""))
            label = m.group(2).strip()
            if "verejnej funkcie" in label:
                income["public_function"] = amount
            else:
                income["other"] = amount
    return income if income else text


def _parse_positions(td):
    """Parse position divs into list of {role, organization, benefits}."""
    positions = []
    for div in td.select("div"):
        lines = list(div.stripped_strings)
        if not lines:
            continue
        role = lines[0]
        org = None
        benefits = None
        span = div.select_one("span.normal")
        if span:
            span_lines = list(span.stripped_strings)
            for line in span_lines:
                if line.startswith("(") and line.endswith(")"):
                    org = line[1:-1]
                elif line.startswith("požitky:"):
                    benefits = line.replace("požitky:", "").strip()
        positions.append({"role": role, "organization": org, "benefits": benefits})
    if not positions:
        return None
    positions.sort(
        key=lambda item: (
            str(item.get("role") or ""),
            str(item.get("organization") or ""),
            str(item.get("benefits") or ""),
        )
    )
    return positions


def _parse_real_estate(td):
    """Parse real estate divs into list of {type, cadastral_territory, lv_number, share}."""
    items = []
    for div in td.select("div"):
        text = div.get_text(strip=True)
        if not text or text.lower() == "nevlastním":
            return None
        item = _parse_semicolon_entry(text, {
            "kat. územie": "cadastral_territory",
            "číslo LV": "lv_number",
            "podiel": "share",
        })
        items.append(item)
    if not items:
        return None
    items.sort(
        key=lambda item: (
            str(item.get("type") or ""),
            str(item.get("cadastral_territory") or ""),
            str(item.get("lv_number") or ""),
            str(item.get("share") or ""),
        )
    )
    return items


def _parse_obligations(td):
    """Parse obligation divs into list of {type, share, date}."""
    items = []
    for div in td.select("div"):
        text = div.get_text(strip=True)
        if not text or "nemám" in text.lower():
            return None
        item = _parse_comma_entry(text, {
            "podiel": "share",
            "dátum vzniku": "date",
        })
        items.append(item)
    if not items:
        return None
    items.sort(
        key=lambda item: (
            str(item.get("type") or ""),
            str(item.get("date") or ""),
            str(item.get("share") or ""),
        )
    )
    return items


def _parse_vehicles(td):
    """Parse vehicle divs into list of {type, brand, year}."""
    items = []
    for div in td.select("div"):
        text = div.get_text(strip=True)
        if not text or "neužívam" in text.lower():
            return None
        item = _parse_comma_entry(text, {
            "továrenská značka": "brand",
            "rok výroby": "year_of_manufacture",
        })
        # Convert year to int
        if "year_of_manufacture" in item:
            try:
                item["year_of_manufacture"] = int(item["year_of_manufacture"])
            except ValueError:
                pass
        items.append(item)
    if not items:
        return None
    items.sort(
        key=lambda item: (
            str(item.get("type") or ""),
            str(item.get("brand") or ""),
            str(item.get("year_of_manufacture") or ""),
        )
    )
    return items


def _parse_movable_property(td):
    """Parse owned movable property (vehicles etc.) into structured list."""
    items = []
    for div in td.select("div"):
        text = div.get_text(strip=True)
        if not text or text.lower() == "nevlastním":
            return None
        item = _parse_comma_entry(text, {
            "továrenská značka": "brand",
            "rok výroby": "year_of_manufacture",
            "podiel": "share",
        })
        if "year_of_manufacture" in item:
            try:
                item["year_of_manufacture"] = int(item["year_of_manufacture"])
            except ValueError:
                pass
        items.append(item)
    if not items:
        return None
    items.sort(
        key=lambda item: (
            str(item.get("type") or ""),
            str(item.get("brand") or ""),
            str(item.get("year_of_manufacture") or ""),
            str(item.get("share") or ""),
        )
    )
    return items


def _parse_divs_as_text(td):
    """Parse divs as simple text list."""
    items = []
    for div in td.select("div"):
        text = div.get_text(strip=True)
        if text and text.lower() not in ("nevlastním", "nemám"):
            items.append(text)
    return _sorted_text_items(items) or None


def _sorted_text_items(items):
    return sorted(items, key=lambda value: re.sub(r"\s+", " ", str(value or "")).strip())


def _parse_semicolon_entry(text, field_map):
    """Parse 'TYPE; key1: val1; key2: val2' into dict.
    Handles both 'key: val' and 'key val' (e.g., 'kat. územie BYSTRÁ')."""
    parts = [p.strip() for p in text.split(";")]
    entry = {"type": parts[0]} if parts else {}
    for part in parts[1:]:
        for sk_prefix, en_key in field_map.items():
            if sk_prefix in part:
                rest = part[part.index(sk_prefix) + len(sk_prefix) :]
                rest = rest.lstrip(": ").strip()
                if rest:
                    entry[en_key] = rest
    return entry


def _parse_comma_entry(text, field_map):
    """Parse 'TYPE, key1: val1, key2: val2' into dict."""
    parts = [p.strip() for p in text.split(",")]
    entry = {"type": parts[0]} if parts else {}
    for part in parts[1:]:
        for sk_prefix, en_key in field_map.items():
            if sk_prefix in part:
                entry[en_key] = part.split(":", 1)[1].strip()
    return entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape_one(user_id, year=None):
    """Scrape one politician, return structured data or None."""
    html = fetch_declaration_html(user_id, year=year)
    data = parse_declaration(html)
    if year is not None or data:
        return data

    available_years, selected_year = parse_available_years(html)
    for candidate_year in available_years:
        if candidate_year == selected_year:
            continue
        candidate_html = fetch_declaration_html(user_id, year=candidate_year)
        data = parse_declaration(candidate_html)
        if data:
            return data
    return None


def load_supplementary_ids(path):
    """Load additional user IDs from a text file (one per line)."""
    ids = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            uid = line.strip()
            if uid and not uid.startswith("#"):
                ids.append(uid)
    return ids


def main():
    parser = argparse.ArgumentParser(description="Scrape NR SR asset declarations")
    parser.add_argument("--user-id", help="Scrape a single politician by UserId (e.g., Tomas.Abel)")
    parser.add_argument("--year", type=int, help="Scrape a specific year (default: latest available)")
    parser.add_argument("--limit", type=int, help="Limit number of politicians to scrape")
    parser.add_argument("--workers", type=int, default=8, help="Number of parallel workers (default: 8)")
    parser.add_argument(
        "--supplementary-ids",
        type=Path,
        help="File with additional UserId values (one per line) to scrape alongside the NRSR list",
    )
    parser.add_argument(
        "--only-supplementary",
        action="store_true",
        help="Scrape only the supplementary IDs (skip fetching the NRSR list)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Output directory for scraped YAML files (default: data/)",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.user_id:
        politicians = [{"user_id": args.user_id, "display_name": args.user_id}]
    elif args.only_supplementary:
        if not args.supplementary_ids or not args.supplementary_ids.exists():
            print("--only-supplementary requires --supplementary-ids", file=sys.stderr)
            sys.exit(1)
        extra_ids = load_supplementary_ids(args.supplementary_ids)
        politicians = [{"user_id": uid, "display_name": uid} for uid in extra_ids]
        print(f"Loaded {len(politicians)} supplementary politicians", file=sys.stderr)
    else:
        print("Fetching politician list...", file=sys.stderr)
        politicians = fetch_politician_list()
        print(f"Found {len(politicians)} politicians from NRSR", file=sys.stderr)

        if args.supplementary_ids and args.supplementary_ids.exists():
            existing_ids = {p["user_id"] for p in politicians}
            extra_ids = load_supplementary_ids(args.supplementary_ids)
            added = 0
            for uid in extra_ids:
                if uid not in existing_ids:
                    politicians.append({"user_id": uid, "display_name": uid})
                    existing_ids.add(uid)
                    added += 1
            print(f"Added {added} supplementary politicians", file=sys.stderr)

        print(f"Total: {len(politicians)} politicians", file=sys.stderr)

    if args.limit:
        politicians = politicians[: args.limit]

    total = len(politicians)
    scraped = 0
    skipped = 0
    errors = 0

    def _process(pol):
        uid = pol["user_id"]
        data = scrape_one(uid, year=args.year)
        if data:
            out_path = data_dir / f"{uid}.yaml"
            out_path.write_text(dump_yaml(data), encoding="utf-8")
        return uid, data

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_process, p): p for p in politicians}
        for future in as_completed(futures):
            uid = futures[future]["user_id"]
            done = scraped + skipped + errors
            try:
                uid, data = future.result()
                if data:
                    scraped += 1
                    print(f"[{done+1}/{total}] {uid} ok ({data.get('year', '?')})", file=sys.stderr)
                else:
                    skipped += 1
                    print(f"[{done+1}/{total}] {uid} no data", file=sys.stderr)
            except Exception as e:
                errors += 1
                print(f"[{done+1}/{total}] {uid} ERROR: {e}", file=sys.stderr)

    print(f"\nDone: {scraped} scraped, {skipped} skipped, {errors} errors", file=sys.stderr)


if __name__ == "__main__":
    main()
