#!/usr/bin/env python3
"""
Build a static site from git history of the data branch.

The build outputs:
- JSON data files for programmatic reuse
- crawlable landing pages for each highlight section
- one dedicated HTML page per politician
- robots/sitemap metadata when SITE_URL is configured
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from bisect import bisect_right
from pathlib import Path

import yaml


SITE_DIR = Path("site")
DATA_BRANCH = "data"
SITE_NAME = "Majetkový kompas"
PARENT_SITE_NAME = "Žltá Stopa"
PARENT_SITE_URL = "https://www.zltastopa.sk"
PROJECT_TITLE_SUFFIX = f"Projekt {PARENT_SITE_NAME}"
SITE_DESCRIPTION = (
    "Majetkové priznania verejných funkcionárov na Slovensku v prehľadnej, "
    "porovnateľnej podobe."
)
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
NRSR_LIST_URL = "https://www.nrsr.sk/web/?sid=oznamenia_funkcionarov"
NRSR_DECL_URL = "https://www.nrsr.sk/web/Default.aspx?sid=vnf/oznamenie&UserId="
# 2024 Slovak salary median reported by Alma Career/Platy.sk via TASR/HN24:
# 1 500 EUR gross monthly, annualized here to 18 000 EUR.
SK_MEDIAN_SOURCE = (
    "https://hn24.hnonline.sk/hn24/96189316-"
    "desatina-pracujucich-vlani-zarabala-menej-ako-tisic-eur-mesacne"
)
SK_MEDIAN_INCOME = {
    2019: 13100,
    2020: 13600,
    2021: 14500,
    2022: 15600,
    2023: 17200,
    2024: 18000,
}
SECTION_PAGES = {
    "income_jumps": {
        "slug": "najvacsie-zmeny-prijmov",
        "title": "Najväčšie zmeny príjmov",
        "intro": "Najväčšie medziročné zmeny celkových príjmov v rokoch 2019 až 2024.",
    },
    "new_properties": {
        "slug": "nove-nehnutelnosti",
        "title": "Nové nehnuteľnosti",
        "intro": "Funkcionári, ktorým v priznaní pribudli nehnuteľnosti.",
    },
    "new_obligations": {
        "slug": "nove-zavazky",
        "title": "Nové záväzky",
        "intro": "Nové úvery, hypotéky a ďalšie záväzky podľa medziročných zmien.",
    },
    "top_earners": {
        "slug": "najvyssie-prijmy",
        "title": "Najvyššie príjmy",
        "intro": "Najvyššie celkové príjmy v poslednom dostupnom roku.",
    },
}
PERSON_PAGE_SLUG = "osoba"
LEGACY_PERSON_PAGE_SLUG = "politicians"
DETAIL_JSON_DIRNAME = "politicians"
ROLE_BREAK_KEYWORDS = (
    "člen ",
    "členka ",
    "poslanec ",
    "predseda ",
    "podpredseda ",
    "primátor ",
    "starosta ",
    "štatutárny orgán ",
    "sudca ",
    "sudkyňa ",
    "prokurátor ",
    "riaditeľ ",
    "riaditeľka ",
    "generálny ",
    "guvernér ",
    "prezident ",
    "rektor ",
    "dekan ",
)


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def git_ref_exists(ref):
    return subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def data_branch_ref():
    if git_ref_exists(DATA_BRANCH):
        return DATA_BRANCH
    remote_ref = f"origin/{DATA_BRANCH}"
    if git_ref_exists(remote_ref):
        return remote_ref
    raise RuntimeError(f"Missing git ref for data branch: {DATA_BRANCH}")


def parse_github_repo_url():
    try:
        remote_url = git("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return ""

    if remote_url.startswith("git@github.com:"):
        repo = remote_url.removeprefix("git@github.com:").removesuffix(".git")
        return f"https://github.com/{repo}"
    if remote_url.startswith("https://github.com/"):
        return remote_url.removesuffix(".git")
    return ""


def get_commits():
    """Get ordered list of (commit_hash, year) from data branch.

    The data branch may contain multiple snapshot commits for the same
    reporting year as the scraper re-checks the latest declarations over
    time. For site generation we only want the newest commit for each year,
    while preserving the original year order.
    """
    hashes = git("rev-list", "--reverse", data_branch_ref()).split("\n")
    commits_by_year = {}
    for commit_hash in hashes:
        msg = git("log", "--format=%s", "-1", commit_hash)
        for word in msg.split():
            if word.isdigit() and len(word) == 4:
                commits_by_year[int(word)] = commit_hash
                break
    return [(commit_hash, year) for year, commit_hash in sorted(commits_by_year.items())]


def latest_data_commit_info():
    ref = data_branch_ref()
    commit_hash = git("rev-parse", ref)
    committed_at = git("log", "-1", "--format=%cs", ref)
    parent_hash = git("rev-list", "--parents", "-n", "1", ref).split()[1:]
    repo_url = parse_github_repo_url()
    commit_url = f"{repo_url}/commit/{commit_hash}" if repo_url else ""
    compare_url = ""
    if repo_url and parent_hash:
        compare_url = f"{repo_url}/compare/{parent_hash[0]}...{commit_hash}"

    return {
        "branch": DATA_BRANCH,
        "commit": commit_hash,
        "previous_commit": parent_hash[0] if parent_hash else "",
        "committed_at": committed_at,
        "commit_url": commit_url,
        "compare_url": compare_url,
    }


def latest_data_diffs(status):
    current_commit = status.get("commit", "")
    previous_commit = status.get("previous_commit", "")
    if not current_commit:
        return {}

    if previous_commit:
        files = git("diff", "--name-only", previous_commit, current_commit, "--", "data/").split("\n")
    else:
        files = git("ls-tree", "--name-only", current_commit, "data/").split("\n")

    diffs = {}
    for filepath in files:
        if not filepath.endswith(".yaml"):
            continue
        user_id = filepath.replace("data/", "").replace(".yaml", "")
        old_data = read_yaml_at_commit(previous_commit, filepath) if previous_commit else None
        new_data = read_yaml_at_commit(current_commit, filepath)
        diffs[user_id] = compute_diff(old_data, new_data)
    return diffs


def read_yaml_at_commit(commit, path):
    try:
        content = subprocess.check_output(
            ["git", "show", f"{commit}:{path}"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return yaml.safe_load(content)
    except subprocess.CalledProcessError:
        return None


def total_income(data):
    if not isinstance(data, dict):
        return 0
    inc = data.get("income") if "income" in data else data
    if not inc or not isinstance(inc, dict):
        return 0
    return (inc.get("public_function") or 0) + (inc.get("other") or 0)


def count_items(data, key):
    val = data.get(key)
    if isinstance(val, list):
        return len(val)
    return 0


def compute_diff(old, new):
    if old is None:
        return {"type": "new"}
    if new is None:
        return {"type": "removed"}

    changes = []
    all_keys = list(dict.fromkeys(list(old.keys()) + list(new.keys())))
    skip_keys = {"id", "declaration_id", "year", "filed", "name"}

    for key in all_keys:
        if key in skip_keys:
            continue
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            change = {"field": key, "old": old_val, "new": new_val}

            if key == "income" and isinstance(old_val, dict) and isinstance(new_val, dict):
                old_total = (old_val.get("public_function") or 0) + (old_val.get("other") or 0)
                new_total = (new_val.get("public_function") or 0) + (new_val.get("other") or 0)
                change["old_total"] = old_total
                change["new_total"] = new_total
                change["delta"] = new_total - old_total
                if old_total > 0:
                    change["delta_pct"] = round((new_total - old_total) / old_total * 100, 1)

            if isinstance(old_val, list) and isinstance(new_val, list):
                change["old_count"] = len(old_val)
                change["new_count"] = len(new_val)

            changes.append(change)

    return {"type": "changed", "changes": changes} if changes else {"type": "unchanged"}


def title_case_name(name):
    """Convert 'JUDr. TOMÁŠ ABEL, PhD.' to 'JUDr. Tomáš Abel, PhD.'"""
    parts = []
    for part in name.split():
        if part.endswith(".") or part.endswith(","):
            base = part.rstrip(".,")
            if base and any(c.islower() for c in base):
                parts.append(part)
            elif base and base.isupper() and len(base) <= 5:
                parts.append(part)
            else:
                parts.append(part.capitalize())
        elif part.isupper() and len(part) > 3:
            parts.append(part.capitalize())
        else:
            parts.append(part)
    return " ".join(parts)


def esc(value):
    return html.escape(str(value or ""))


def normalize_whitespace(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_public_function(value):
    if isinstance(value, list):
        return " · ".join(normalize_public_function(item) for item in value if item)
    text = normalize_whitespace(value)
    for keyword in ROLE_BREAK_KEYWORDS:
        text = re.sub(rf"(?<=[\w\)])(?={re.escape(keyword)})", " · ", text)
    return text


def display_role(value, fallback="Verejný funkcionár"):
    return normalize_public_function(value) or fallback


def strip_diacritics(value):
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def slugify(value):
    ascii_value = strip_diacritics(value).lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")


def unique_slug(value, used_slugs, fallback):
    base = slugify(value) or slugify(fallback) or "politik"
    candidate = base
    suffix = 2
    while candidate in used_slugs:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_slugs.add(candidate)
    return candidate


def fmt_int(value):
    return f"{int(value):,}".replace(",", " ")


def fmt_currency(value):
    return f"{fmt_int(value)} €"


def annual_to_monthly(value):
    return round(value / 12)


def income_parts(data):
    if not isinstance(data, dict):
        return {"public_function": 0, "other": 0}
    inc = data.get("income") if "income" in data else data
    if not isinstance(inc, dict):
        return {"public_function": 0, "other": 0}
    return {
        "public_function": inc.get("public_function") or 0,
        "other": inc.get("other") or 0,
    }


def income_multiple_text(value, year):
    median = SK_MEDIAN_INCOME.get(year)
    if not median or value <= 0:
        return ""
    return f"{value / median:.1f}× slovenský medián"


def person_path(slug):
    return f"/{PERSON_PAGE_SLUG}/{slug}/"


def person_href(slug, prefix=""):
    return f"{prefix}{PERSON_PAGE_SLUG}/{slug}/"


def legacy_person_path(slug):
    return f"/{LEGACY_PERSON_PAGE_SLUG}/{slug}/"


def legacy_person_href(slug, prefix=""):
    return f"{prefix}{LEGACY_PERSON_PAGE_SLUG}/{slug}/"


def page_href(slug, prefix=""):
    return f"{prefix}{slug}/"


def abs_url(path):
    if not SITE_URL:
        return ""
    return f"{SITE_URL}{path}"


def json_for_script(data):
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def page_title(*parts):
    return " | ".join([*parts, PROJECT_TITLE_SUFFIX])


def json_ld_script(data):
    return f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'


def clamp_meta_description(value, limit=160):
    text = normalize_whitespace(value)
    if len(text) <= limit:
        return text
    trimmed = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{trimmed}…"


def person_seo_fields(person, meta):
    role = display_role(person.get("public_function"))
    year_from = meta["years"][0]
    year_to = meta["years"][-1]
    title = page_title(
        f'{person["name"]} – majetkové priznania verejných funkcionárov',
        SITE_NAME,
    )
    description = clamp_meta_description(
        f'{person["name"]} – {role}. Pozri majetkové priznania verejných '
        f'funkcionárov, príjmy, nehnuteľnosti, záväzky a zmeny za roky '
        f"{year_from}–{year_to}."
    )
    return title, description, role


def meta_tags(title, description, path, *, image_path="", noindex=False):
    tags = [
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        '<meta property="og:type" content="website">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    if noindex:
        tags.append('<meta name="robots" content="noindex,follow">')
    if SITE_URL:
        canonical = abs_url(path)
        tags.extend(
            [
                f'<link rel="canonical" href="{esc(canonical)}">',
                f'<meta property="og:url" content="{esc(canonical)}">',
            ]
        )
        if image_path:
            tags.append(f'<meta property="og:image" content="{esc(abs_url(image_path))}">')
    return "\n".join(tags)


def nav_links(prefix="", current=""):
    links = ['<a href="' + esc(prefix or "./") + '"' + (' aria-current="page"' if current == "home" else "") + ">Vyhľadávanie</a>"]
    for key, page in SECTION_PAGES.items():
        href = page_href(page["slug"], prefix)
        current_attr = ' aria-current="page"' if current == key else ""
        links.append(f'<a href="{esc(href)}"{current_attr}>{esc(page["title"])}</a>')
    return "\n".join(links)


def shell(
    title,
    description,
    path,
    body,
    *,
    prefix="",
    current_nav="",
    json_ld="",
    header_extra="",
    header_note="",
    nav_markup="",
    subtitle="",
):
    nav = nav_markup or nav_links(prefix, current_nav)
    subtitle_markup = subtitle or (
        f'Majetkové priznania verejných funkcionárov SR · dáta z '
        f'<a href="{NRSR_LIST_URL}" target="_blank" rel="noreferrer">NR SR</a>'
    )
    return f"""<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta_tags(title, description, path)}
<link rel="stylesheet" href="{esc(prefix)}styles.css">
{json_ld}
</head>
<body>
<div class="container">
  <header class="site-header">
    <div class="site-header-top">
      <div class="site-brand-block"><a class="site-brand" href="{esc(prefix or './')}">{SITE_NAME}</a></div>
      <a class="corner-brand-badge" href="{PARENT_SITE_URL}" target="_blank" rel="noreferrer" aria-label="Projekt {PARENT_SITE_NAME}">
        <img src="{esc(prefix)}projekt-zlta-stopa.png" alt="Projekt {PARENT_SITE_NAME}">
      </a>
    </div>
    <div class="subtitle">{subtitle_markup}</div>
    {header_extra}
    {header_note}
    <nav class="landing-tabs landing-tabs--links">
      {nav}
    </nav>
  </header>
  {body}
  <footer class="site-footer">
    <p>Dáta: <a href="{NRSR_LIST_URL}" target="_blank" rel="noreferrer">Národná rada SR</a>.</p>
    <p><a class="parent-footer-link" href="{PARENT_SITE_URL}" target="_blank" rel="noreferrer">Projekt {PARENT_SITE_NAME} ↗</a></p>
  </footer>
</div>
</body>
</html>
"""


def render_person_redirect(person):
    target_path = person_path(person["slug"])
    target_href = person_href(person["slug"], "../../")
    description = f"Detail osoby {person['name']} bol presunutý na slovenskú URL."
    return f"""<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta_tags(page_title(person["name"], SITE_NAME), description, target_path, noindex=True)}
<meta http-equiv="refresh" content="0; url={esc(target_href)}">
</head>
<body>
<main style="max-width: 720px; margin: 4rem auto; padding: 0 1rem; font: 16px/1.55 Georgia, 'Times New Roman', serif;">
  <h1 style="font-size: 2rem; line-height: 1.1;">Stránka bola presunutá</h1>
  <p>Nová adresa detailu je <a href="{esc(target_href)}">{esc(target_href)}</a>.</p>
  <script>window.location.replace({json.dumps(target_href)});</script>
</main>
</body>
</html>
"""


def header_stats(meta, stats=None):
    median_markup = ""
    if stats:
        latest_year = meta["years"][-1]
        slovak_median_year = latest_year
        if slovak_median_year not in SK_MEDIAN_INCOME:
            slovak_median_year = max(SK_MEDIAN_INCOME)
        slovak_median = SK_MEDIAN_INCOME[slovak_median_year]
        median_markup = f"""
  <div class="stat stat--median"><span class="stat-value">{fmt_currency(stats["median_income"])}</span><span class="stat-label">medián funkcionárov</span></div>
  <div class="stat stat--median stat--median-secondary"><span class="stat-value">{fmt_currency(slovak_median)}</span><span class="stat-label"><a href="{SK_MEDIAN_SOURCE}" target="_blank" rel="noreferrer">ročný medián SR</a></span><span class="stat-source">zdroj {slovak_median_year}: {fmt_int(annual_to_monthly(slovak_median))} €/mes.</span></div>
"""
    return f"""
<div class="stats">
  <div class="stat"><span class="stat-value">{fmt_int(meta["count"])}</span><span class="stat-label">funkcionárov</span></div>
  <div class="stat"><span class="stat-value">{len(meta["years"])}</span><span class="stat-label">rokov dát</span></div>
  <div class="stat"><span class="stat-value">{meta["years"][0]}–{meta["years"][-1]}</span><span class="stat-label">obdobie</span></div>
  {median_markup}
</div>
"""


def header_explainer():
    return f"""
<section class="explainer explainer-static" aria-label="Ako čítať Majetkový kompas">
  <h2 class="explainer-title">Ako čítať tento kompas</h2>
  <p>Majetkový kompas spracúva verejné majetkové priznania z <a href="{NRSR_LIST_URL}" target="_blank" rel="noreferrer">NR SR</a>. Vidíš príjmy, nehnuteľnosti, záväzky aj medziročné zmeny na jednom mieste.</p>
  <p class="explainer-mini">Tip: porovnanie rokov ukáže starší rok vľavo a novší vpravo.</p>
</section>
"""


def person_row(person, prefix=""):
    total = total_income(person["income"])
    role = display_role(person.get("public_function"))
    income_pct = person.get("income_percentile", 0)
    property_pct = person.get("property_percentile", 0)
    bar_class = ""
    if income_pct >= 99:
        bar_class = " top"
    elif income_pct >= 90:
        bar_class = " high"
    indicators = []
    if person.get("n_properties"):
        title = "nehnuteľnosti"
        if property_pct:
            title += f" · viac ako {property_pct}% funkcionárov"
        indicators.append(f'<span title="{title}">{person["n_properties"]} neh.</span>')
    if person.get("n_obligations"):
        indicators.append(f'<span title="záväzky">{person["n_obligations"]} záv.</span>')
    if person.get("total_changes"):
        indicators.append(f'<span title="zmeny">{person["total_changes"]} zmien</span>')
    return f"""
<li class="person-row"
    data-name="{esc(normalize_whitespace(person['name']).lower())}"
    data-function="{esc(role.lower())}"
    data-income="{total}"
    data-properties="{person.get('n_properties', 0)}"
    data-changes="{person.get('total_changes', 0)}">
  <a class="person-row__link" href="{esc(person_href(person['slug'], prefix))}">
    <div>
      <div class="pol-name">{esc(person['name'])}</div>
      <div class="pol-meta">{esc(role)}</div>
    </div>
    <div class="pol-right">
      <div class="pol-income">{fmt_currency(total) if total else ''}</div>
      {f'<div class="pol-pct-bar" aria-hidden="true"><div class="pol-pct-fill{bar_class}" style="width:{max(income_pct, 2)}%"></div></div>' if total else ''}
      <div class="pol-indicators">{''.join(indicators)}</div>
    </div>
  </a>
</li>
"""


def render_home(index, highlights, meta, stats):
    latest_year = meta["years"][-1]
    base_url = abs_url("/") or "/"
    ld = [
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": SITE_NAME,
            "url": base_url,
            "potentialAction": {
                "@type": "SearchAction",
                "target": f"{base_url}?q={{search_term_string}}",
                "query-input": "required name=search_term_string",
            },
        },
        {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": SITE_NAME,
            "description": SITE_DESCRIPTION,
            "license": "https://creativecommons.org/publicdomain/mark/1.0/",
            "creator": {"@type": "Organization", "name": SITE_NAME},
            "url": base_url,
            "keywords": [
                "majetkové priznania",
                "verejní funkcionári",
                "Slovensko",
                "transparentnosť",
            ],
        },
    ]
    body = f"""
<div id="list-view">
<div class="landing-section active" id="tab-search">
  <h1 class="page-title">Majetkové priznania verejných funkcionárov</h1>
  <p class="section-note">Vyhľadávaj v majetkových priznaniach, príjmoch, nehnuteľnostiach a záväzkoch verejných funkcionárov na Slovensku.</p>
  <div class="controls">
    <label class="search-box">
      <span class="sr-only">Hľadať</span>
      <input id="search" type="search" placeholder="Skús: Robert, poslanec, primátor mesta">
    </label>
    <label class="sort-select">
      <span class="sr-only">Triediť</span>
      <select id="sort">
        <option value="name">Meno A–Z</option>
        <option value="income_desc">Príjem ↓</option>
        <option value="income_asc">Príjem ↑</option>
        <option value="properties">Nehnuteľnosti ↓</option>
        <option value="changes">Zmeny ↓</option>
      </select>
    </label>
  </div>
  <p id="result-count" class="result-count">{len(index)} funkcionárov</p>
  <ul id="politician-list" class="politician-list">
    {''.join(person_row(person) for person in index)}
  </ul>
</div>

<div class="landing-section" id="tab-income_jumps">
  <p class="section-note">Najväčšie medziročné zmeny celkových príjmov ({meta["years"][0]}–{meta["years"][-1]})</p>
  <div class="highlight-list">
    {''.join(highlight_card(item, 'income_jumps') for item in highlights["income_jumps"][:8])}
  </div>
</div>

<div class="landing-section" id="tab-new_properties">
  <p class="section-note">Funkcionári, ktorým pribudli nehnuteľnosti</p>
  <div class="highlight-list">
    {''.join(highlight_card(item, 'new_properties') for item in highlights["new_properties"][:8])}
  </div>
</div>

<div class="landing-section" id="tab-new_obligations">
  <p class="section-note">Nové úvery a hypotéky</p>
  <div class="highlight-list">
    {''.join(highlight_card(item, 'new_obligations') for item in highlights["new_obligations"][:8])}
  </div>
</div>

<div class="landing-section" id="tab-top_earners">
  <p class="section-note">Najvyššie celkové príjmy v roku {latest_year}</p>
  <div class="highlight-list">
    {''.join(highlight_card(item, 'top_earners') for item in highlights["top_earners"][:8])}
  </div>
</div>
</div>
{json_ld_script(ld)}
<script src="app.js"></script>
"""
    nav_markup = """
<button class="active" data-tab="search">Vyhľadávanie</button>
<button data-tab="income_jumps">Najväčšie zmeny príjmov</button>
<button data-tab="new_properties">Nové nehnuteľnosti</button>
<button data-tab="new_obligations">Nové záväzky</button>
<button data-tab="top_earners">Najvyššie príjmy</button>
"""
    return shell(
        page_title(SITE_NAME),
        SITE_DESCRIPTION,
        "/",
        body,
        current_nav="home",
        header_extra=header_stats(meta, stats),
        header_note=header_explainer(),
        nav_markup=nav_markup,
        subtitle=(
            f'Majetkové priznania verejných funkcionárov SR · dáta z '
            f'<a href="{NRSR_LIST_URL}" target="_blank" rel="noreferrer">NR SR</a> · '
            f'{meta["years"][0]} – {meta["years"][-1]}'
        ),
    )


def highlight_metric_label(kind):
    return {
        "income_jumps": "Zmena",
        "new_properties": "Prírastok",
        "new_obligations": "Prírastok",
        "top_earners": "Príjem",
    }[kind]


def highlight_card(item, kind, prefix=""):
    role = display_role(item.get("function"), "Bez uvedenej funkcie")
    year_badge = (
        f' <span class="hl-badge badge-year">{item["year"]}</span>'
        if kind != "top_earners" and item.get("year")
        else ""
    )
    if kind == "income_jumps":
        sign = "+" if item["delta"] > 0 else ""
        sub = f"{fmt_currency(item['old_total'])} → {fmt_currency(item['new_total'])}"
        if item.get("delta_pct") is not None:
            sub += f" ({sign}{item['delta_pct']} %)"
        value = f"{sign}{fmt_currency(item['delta'])}"
        value_class = "positive" if item["delta"] > 0 else "negative"
    elif kind == "new_properties":
        value = f"+{item['added']}"
        sub = f"celkom {item['total']} nehnuteľností"
        value_class = "positive"
    elif kind == "new_obligations":
        value = f"+{item['added']}"
        sub = f"celkom {item['total']} záväzkov"
        value_class = "negative"
    else:
        value = fmt_currency(item["income"])
        sub = ""
        value_class = "neutral"
    return f"""
<li class="highlight-card">
  <a href="{esc(person_href(item['slug'], prefix))}">
    <div class="hl-left">
      <div class="hl-name">{esc(item['name'])}{year_badge}</div>
      <div class="hl-meta">{esc(role)}</div>
    </div>
    <div class="hl-right">
      <div class="hl-big {value_class}">{esc(value)}</div>
      {f'<div class="hl-small">{esc(sub)}</div>' if sub else ''}
    </div>
  </a>
</li>
"""


def render_section_page(kind, page, items, meta, stats):
    title = page_title(page["title"], SITE_NAME)
    description = f"{page['intro']} {SITE_NAME} spracúva dáta z NR SR."
    page_path = f"/{page['slug']}/"
    page_url = abs_url(page_path) or page_path
    json_ld = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": page["title"],
        "description": description,
        "url": page_url,
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": index + 1,
                    "url": abs_url(person_path(item["slug"])) or person_path(item["slug"]),
                    "name": item["name"],
                }
                for index, item in enumerate(items)
            ],
        },
    }
    extra_link = ""
    if kind != "top_earners":
        extra_link = (
            f' · <a href="../{SECTION_PAGES["top_earners"]["slug"]}/">'
            f'Najvyššie príjmy {meta["years"][-1]}</a>'
        )
    body = f"""
<div class="landing-section active">
  <h1 class="page-title">{esc(page['title'])}</h1>
  <p class="section-note">{esc(page['intro'])}</p>
  <div class="highlight-list">
    {''.join(highlight_card(item, kind, '../') for item in items)}
  </div>
</div>
<p class="section-cta"><a href="../">&larr; Späť na vyhľadávanie</a>{extra_link}</p>
"""
    return shell(
        title,
        description,
        f"/{page['slug']}/",
        body,
        prefix="../",
        current_nav=kind,
        json_ld=json_ld_script(json_ld),
        header_extra=header_stats(meta, stats),
        header_note=header_explainer(),
        subtitle=(
            f'Majetkové priznania verejných funkcionárov SR · dáta z '
            f'<a href="{NRSR_LIST_URL}" target="_blank" rel="noreferrer">NR SR</a> · '
            f'{meta["years"][0]} – {meta["years"][-1]}'
        ),
    )


def render_items(items, renderer, empty_text):
    if not items:
        return f'<p class="empty-state">{esc(empty_text)}</p>'
    return "<ul class=\"detail-list\">" + "".join(
        f"<li>{renderer(item)}</li>" for item in items
    ) + "</ul>"


def join_parts(parts):
    return " · ".join(part for part in parts if part)


def render_real_estate_item(item):
    parts = []
    if item.get("cadastral_territory"):
        parts.append(f"Kat. územie: {normalize_whitespace(item.get('cadastral_territory'))}")
    if item.get("lv_number"):
        parts.append(f"LV: {normalize_whitespace(item.get('lv_number'))}")
    if item.get("share"):
        parts.append(f"Podiel: {normalize_whitespace(item.get('share'))}")

    return (
        f"<strong>{esc(normalize_whitespace(item.get('type')))}</strong>"
        f"<span>{esc(join_parts(parts))}</span>"
    )


def render_obligation_item(item):
    parts = []
    if item.get("share"):
        parts.append(f"Podiel: {normalize_whitespace(item.get('share'))}")
    if item.get("date"):
        parts.append(f"Vznik: {normalize_whitespace(item.get('date'))}")

    return (
        f"<strong>{esc(normalize_whitespace(item.get('type')))}</strong>"
        f"<span>{esc(join_parts(parts))}</span>"
    )


def field_summary(change):
    label = {
        "income": "Príjmy",
        "employment": "Zamestnanie",
        "business_activity": "Podnikanie",
        "positions": "Funkcie",
        "real_estate": "Nehnuteľnosti",
        "movable_property": "Hnuteľný majetok",
        "obligations": "Záväzky",
        "vehicles": "Vozidlá",
        "gifts": "Dary",
        "property_rights": "Majetkové práva",
        "public_function": "Verejná funkcia",
        "incompatibility": "Nezlučiteľnosť",
        "use_of_others_real_estate": "Užívanie nehnuteľností",
    }.get(change["field"], change["field"])
    if change["field"] == "income" and change.get("delta") is not None:
        sign = "+" if change["delta"] > 0 else ""
        return f"{label}: {fmt_currency(change['old_total'])} → {fmt_currency(change['new_total'])} ({sign}{fmt_currency(change['delta'])})"
    if change.get("old_count") is not None:
        diff = change["new_count"] - change["old_count"]
        sign = "+" if diff > 0 else ""
        return f"{label}: {change['old_count']} → {change['new_count']} ({sign}{diff})"
    return label


def render_person_page(person, meta, stats):
    title, description, role = person_seo_fields(person, meta)

    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": abs_url("/") or "/"},
            {
                "@type": "ListItem",
                "position": 2,
                "name": person["name"],
                "item": abs_url(person_path(person["slug"])) or person_path(person["slug"]),
            },
        ],
    }
    person_ld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": person["name"],
        "description": description,
        "url": abs_url(person_path(person["slug"])) or person_path(person["slug"]),
        "jobTitle": role,
    }

    body = f"""
<article class="detail-page">
  <a href="../../" class="back-link">&larr; Späť na zoznam</a>
  <div class="detail-header">
    <h1 id="detail-name">{esc(person['name'])}</h1>
    <div class="function" id="detail-function">{esc(role)}</div>
    <div class="source-link" id="detail-source"><a href="{NRSR_DECL_URL}{person['user_id']}" target="_blank" rel="noreferrer">→ Originál na nrsr.sk</a></div>
  </div>
  <div class="detail-switch">
    <button type="button" class="detail-switch__button is-active" data-detail-mode="profile">Profil</button>
    <button type="button" class="detail-switch__button" data-detail-mode="compare">Porovnanie rokov</button>
  </div>
  <div class="detail-mode detail-mode--active" id="detail-profile" data-detail-panel="profile">
    <div class="year-tabs" id="year-tabs"></div>
    <div id="detail-context"></div>
    <div id="detail-content"></div>
    <div class="section" id="income-section">
      <h3>Vývoj príjmov</h3>
      <div class="section-card"><div class="income-chart-wrap"><div class="income-chart" id="income-chart"></div></div></div>
    </div>
    <div class="section" id="timeline-section">
      <h3>Zmeny v čase</h3>
      <div class="section-card"><div id="timeline"></div></div>
    </div>
  </div>
  <div class="detail-mode" id="person-compare-panel" data-detail-panel="compare" hidden></div>
</article>
<script type="application/ld+json">{json.dumps(breadcrumb_ld, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(person_ld, ensure_ascii=False)}</script>
<script type="application/json" id="person-detail-data">{json_for_script(person)}</script>
<script type="application/json" id="person-compare-data">{json_for_script(person)}</script>
<script src="../../detail.js"></script>
<script src="../../compare.js"></script>
"""
    return shell(
        title,
        description,
        person_path(person["slug"]),
        body,
        prefix="../../",
        header_extra=header_stats(meta, stats),
        header_note=header_explainer(),
        subtitle=(
            f'Majetkové priznania verejných funkcionárov SR · dáta z '
            f'<a href="{NRSR_LIST_URL}" target="_blank" rel="noreferrer">NR SR</a> · '
            f'{meta["years"][0]} – {meta["years"][-1]}'
        ),
    )


def percentile_rank(values, value):
    if not values:
        return 0
    return round(bisect_right(values, value) / len(values) * 100)


def compute_site_stats(index):
    incomes = sorted(total_income(person["income"]) for person in index)
    nonzero_incomes = [value for value in incomes if value > 0]
    properties = sorted(person.get("n_properties", 0) for person in index)
    median_income = nonzero_incomes[len(nonzero_incomes) // 2] if nonzero_incomes else 0
    median_properties = properties[len(properties) // 2] if properties else 0
    return {
        "all_incomes": incomes,
        "all_properties": properties,
        "median_income": median_income,
        "median_properties": median_properties,
    }


def build():
    commits = get_commits()
    years = [year for _, year in commits]
    data_status = latest_data_commit_info()
    extraction_diffs = latest_data_diffs(data_status)
    repo_url = parse_github_repo_url()
    print(f"Found {len(commits)} commits: {years}", file=sys.stderr)

    # Years with fewer declarations than this are excluded from the site —
    # they are too sparse to be meaningful (e.g. early 2025 with only a
    # handful of "pri ujatí sa funkcie" filings).
    MIN_DECLARATIONS_PER_YEAR = 10

    all_ids = set()
    year_data = {}

    for commit_hash, year in commits:
        print(f"Reading year {year}...", file=sys.stderr)
        files = git("ls-tree", "--name-only", commit_hash, "data/").split("\n")
        year_data[year] = {}
        for filepath in files:
            if not filepath.endswith(".yaml"):
                continue
            user_id = filepath.replace("data/", "").replace(".yaml", "")
            all_ids.add(user_id)
            data = read_yaml_at_commit(commit_hash, filepath)
            if data:
                year_data[year][user_id] = data

        if len(year_data[year]) < MIN_DECLARATIONS_PER_YEAR:
            print(
                f"  Skipping year {year}: only {len(year_data[year])} declarations"
                f" (minimum {MIN_DECLARATIONS_PER_YEAR})",
                file=sys.stderr,
            )
            # Remove the sparse year so it doesn't appear in the site.
            for uid in year_data[year]:
                # Only remove from all_ids if the official has no other year.
                pass
            del year_data[year]

    # Rebuild years list and all_ids from the surviving year_data.
    years = sorted(year_data.keys())
    all_ids = set()
    for yd in year_data.values():
        all_ids.update(yd.keys())

    print(f"Total politicians: {len(all_ids)}", file=sys.stderr)

    politicians = {}
    highlights = {
        "income_jumps": [],
        "new_properties": [],
        "new_obligations": [],
        "top_earners": [],
        "most_properties": [],
        "most_obligations": [],
    }

    for user_id in sorted(all_ids):
        timeline = []
        prev_data = None
        total_changes = 0

        for year in years:
            current_data = year_data.get(year, {}).get(user_id)
            if current_data is None:
                prev_data = None
                continue
            diff = compute_diff(prev_data, current_data)
            timeline.append({"year": year, "data": current_data, "diff": diff})

            if diff["type"] == "changed":
                total_changes += len(diff["changes"])
                for change in diff["changes"]:
                    if change["field"] == "income" and "delta" in change:
                        highlights["income_jumps"].append(
                            {
                                "user_id": user_id,
                                "name": title_case_name(current_data.get("name", user_id)),
                                "function": current_data.get("public_function"),
                                "year": year,
                                "old_total": change["old_total"],
                                "new_total": change["new_total"],
                                "delta": change["delta"],
                                "delta_pct": change.get("delta_pct"),
                            }
                        )
                    if change["field"] == "real_estate" and isinstance(change.get("new"), list):
                        old_count = change.get("old_count", 0)
                        new_count = change.get("new_count", 0)
                        if new_count > old_count:
                            highlights["new_properties"].append(
                                {
                                    "user_id": user_id,
                                    "name": title_case_name(current_data.get("name", user_id)),
                                    "function": current_data.get("public_function"),
                                    "year": year,
                                    "added": new_count - old_count,
                                    "total": new_count,
                                }
                            )
                    if change["field"] == "obligations" and isinstance(change.get("new"), list):
                        old_count = change.get("old_count", 0)
                        new_count = change.get("new_count", 0)
                        if new_count > old_count:
                            highlights["new_obligations"].append(
                                {
                                    "user_id": user_id,
                                    "name": title_case_name(current_data.get("name", user_id)),
                                    "function": current_data.get("public_function"),
                                    "year": year,
                                    "added": new_count - old_count,
                                    "total": new_count,
                                }
                            )

            prev_data = current_data

        if not timeline:
            continue

        latest = timeline[-1]["data"]
        name = title_case_name(latest.get("name", user_id))
        latest_extraction_diff = extraction_diffs.get(user_id, {"type": "unchanged"})
        current_file_url = (
            f"{repo_url}/blob/{data_status['commit']}/data/{user_id}.yaml"
            if repo_url and data_status.get("commit")
            else ""
        )
        previous_file_url = (
            f"{repo_url}/blob/{data_status['previous_commit']}/data/{user_id}.yaml"
            if repo_url and data_status.get("previous_commit")
            else ""
        )

        politicians[user_id] = {
            "user_id": user_id,
            "name": name,
            "public_function": latest.get("public_function"),
            "public_functions": latest.get("public_functions"),
            "role": display_role(latest.get("public_functions") or latest.get("public_function")),
            "years": [entry["year"] for entry in timeline],
            "timeline": timeline,
            "total_changes": total_changes,
            "latest_extraction": {
                "committed_at": data_status["committed_at"],
                "commit": data_status["commit"],
                "previous_commit": data_status.get("previous_commit", ""),
                "commit_url": data_status["commit_url"],
                "compare_url": data_status["compare_url"],
                "file_url": current_file_url,
                "previous_file_url": previous_file_url,
                "diff": latest_extraction_diff,
                "summary": [
                    field_summary(change)
                    for change in latest_extraction_diff.get("changes", [])
                ],
            },
        }

        latest_income = total_income(latest)
        latest_properties = count_items(latest, "real_estate")
        latest_obligations = count_items(latest, "obligations")

        if latest_income > 0:
            highlights["top_earners"].append(
                {
                    "user_id": user_id,
                    "name": name,
                    "function": latest.get("public_function"),
                    "year": timeline[-1]["year"],
                    "income": latest_income,
                }
            )
        if latest_properties > 0:
            highlights["most_properties"].append(
                {
                    "user_id": user_id,
                    "name": name,
                    "function": latest.get("public_function"),
                    "count": latest_properties,
                }
            )
        if latest_obligations > 0:
            highlights["most_obligations"].append(
                {
                    "user_id": user_id,
                    "name": name,
                    "function": latest.get("public_function"),
                    "count": latest_obligations,
                }
            )

    highlights["income_jumps"].sort(key=lambda item: abs(item["delta"]), reverse=True)
    highlights["income_jumps"] = highlights["income_jumps"][:30]
    highlights["new_properties"].sort(key=lambda item: item["added"], reverse=True)
    highlights["new_properties"] = highlights["new_properties"][:30]
    highlights["new_obligations"].sort(key=lambda item: item["added"], reverse=True)
    highlights["new_obligations"] = highlights["new_obligations"][:30]
    highlights["top_earners"].sort(key=lambda item: item["income"], reverse=True)
    highlights["top_earners"] = highlights["top_earners"][:30]
    highlights["most_properties"].sort(key=lambda item: item["count"], reverse=True)
    highlights["most_properties"] = highlights["most_properties"][:30]
    highlights["most_obligations"].sort(key=lambda item: item["count"], reverse=True)
    highlights["most_obligations"] = highlights["most_obligations"][:30]

    SITE_DIR.mkdir(exist_ok=True)

    used_slugs = set()
    slug_by_uid = {}
    for uid in sorted(politicians, key=lambda key: politicians[key]["name"]):
        slug_by_uid[uid] = unique_slug(politicians[uid]["name"], used_slugs, uid)

    for politician in politicians.values():
        politician["slug"] = slug_by_uid[politician["user_id"]]

    for key, items in highlights.items():
        for item in items:
            item["slug"] = slug_by_uid[item["user_id"]]

    index = []
    for uid in sorted(politicians, key=lambda key: politicians[key]["name"]):
        politician = politicians[uid]
        latest = politician["timeline"][-1]["data"]
        index.append(
            {
                "user_id": uid,
                "name": politician["name"],
                "slug": politician["slug"],
                "public_function": politician["public_function"],
                "public_functions": politician.get("public_functions"),
                "role": politician["role"],
                "years": politician["years"],
                "income": latest.get("income"),
                "n_properties": count_items(latest, "real_estate"),
                "n_obligations": count_items(latest, "obligations"),
                "total_changes": politician["total_changes"],
            }
        )

    stats = compute_site_stats(index)
    latest_sk_median = SK_MEDIAN_INCOME[max(SK_MEDIAN_INCOME)]
    for person in index:
        income = total_income(person["income"])
        person["income_percentile"] = percentile_rank(stats["all_incomes"], income) if income else 0
        person["property_percentile"] = percentile_rank(stats["all_properties"], person.get("n_properties", 0))

    for uid, politician in politicians.items():
        latest = politician["timeline"][-1]["data"]
        latest_year = politician["years"][-1]
        income = total_income(latest)
        properties = count_items(latest, "real_estate")
        obligations = count_items(latest, "obligations")
        sk_median = SK_MEDIAN_INCOME.get(latest_year, latest_sk_median)
        politician["context"] = {
            "latest_year": latest_year,
            "income": income,
            "properties": properties,
            "obligations": obligations,
            "income_percentile": percentile_rank(stats["all_incomes"], income) if income else 0,
            "property_percentile": percentile_rank(stats["all_properties"], properties),
            "median_income": stats["median_income"],
            "median_properties": stats["median_properties"],
            "slovak_median_income": sk_median,
            "slovak_income_multiple": round(income / sk_median, 1) if income > 0 and sk_median else 0,
        }

    (SITE_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (SITE_DIR / "highlights.json").write_text(json.dumps(highlights, ensure_ascii=False), encoding="utf-8")
    meta = {"years": years, "count": len(politicians)}
    (SITE_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (SITE_DIR / "data-status.json").write_text(
        json.dumps(data_status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    detail_json_dir = SITE_DIR / DETAIL_JSON_DIRNAME
    detail_json_dir.mkdir(exist_ok=True)
    for uid, data in politicians.items():
        (detail_json_dir / f"{uid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    (SITE_DIR / "index.html").write_text(render_home(index, highlights, meta, stats), encoding="utf-8")

    for kind, page in SECTION_PAGES.items():
        target_dir = SITE_DIR / page["slug"]
        target_dir.mkdir(exist_ok=True)
        (target_dir / "index.html").write_text(
            render_section_page(kind, page, highlights[kind], meta, stats),
            encoding="utf-8",
        )

    compare_dir = SITE_DIR / "compare"
    if compare_dir.exists():
        shutil.rmtree(compare_dir)

    legacy_detail_dir = SITE_DIR / LEGACY_PERSON_PAGE_SLUG
    legacy_detail_dir.mkdir(exist_ok=True)
    for child in legacy_detail_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
    detail_html_dir = SITE_DIR / PERSON_PAGE_SLUG
    detail_html_dir.mkdir(exist_ok=True)
    for child in detail_html_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
    for uid, data in politicians.items():
        target_dir = detail_html_dir / data["slug"]
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "index.html").write_text(render_person_page(data, meta, stats), encoding="utf-8")
        legacy_dir = legacy_detail_dir / data["slug"]
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "index.html").write_text(render_person_redirect(data), encoding="utf-8")

    robots_lines = ["User-agent: *", "Allow: /"]
    if SITE_URL:
        sitemap_entries = [abs_url("/")]
        for page in SECTION_PAGES.values():
            sitemap_entries.append(abs_url(f"/{page['slug']}/"))
        for data in politicians.values():
            sitemap_entries.append(abs_url(person_path(data["slug"])))
        sitemap_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(f"  <url><loc>{esc(url)}</loc></url>\n" for url in sitemap_entries)
            + "</urlset>\n"
        )
        (SITE_DIR / "sitemap.xml").write_text(sitemap_xml, encoding="utf-8")
        robots_lines.append(f"Sitemap: {SITE_URL}/sitemap.xml")
    (SITE_DIR / "robots.txt").write_text("\n".join(robots_lines) + "\n", encoding="utf-8")

    print(f"Built site: {len(index)} politicians, {len(years)} years", file=sys.stderr)
    print(f"Output: {SITE_DIR}/", file=sys.stderr)


if __name__ == "__main__":
    build()
