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
from pathlib import Path

import yaml


SITE_DIR = Path("site")
DATA_BRANCH = "data"
SITE_NAME = "Majetkový kompas"
SITE_DESCRIPTION = (
    "Majetkové priznania verejných funkcionárov na Slovensku v prehľadnej, "
    "porovnateľnej podobe."
)
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
NRSR_LIST_URL = "https://www.nrsr.sk/web/?sid=oznamenia_funkcionarov"
NRSR_DECL_URL = "https://www.nrsr.sk/web/Default.aspx?sid=vnf/oznamenie&UserId="
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
COMPARE_PAGE = {
    "slug": "compare",
    "title": "Porovnanie",
    "intro": "Porovnajte 2 až 4 funkcionárov alebo si na detaile rozbaľte porovnanie rokov.",
}
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


def get_commits():
    """Get ordered list of (commit_hash, year) from data branch."""
    hashes = git("rev-list", "--reverse", data_branch_ref()).split("\n")
    commits = []
    for commit_hash in hashes:
        msg = git("log", "--format=%s", "-1", commit_hash)
        for word in msg.split():
            if word.isdigit() and len(word) == 4:
                commits.append((commit_hash, int(word)))
                break
    return commits


def read_yaml_at_commit(commit, path):
    try:
        content = git("show", f"{commit}:{path}")
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


def person_href(slug, prefix=""):
    return f"{prefix}politicians/{slug}/"


def page_href(slug, prefix=""):
    return f"{prefix}{slug}/"


def abs_url(path):
    if not SITE_URL:
        return ""
    return f"{SITE_URL}{path}"


def json_for_script(data):
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


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
    links = ['<a href="' + esc(prefix or "./") + '"' + (' aria-current="page"' if current == "home" else "") + ">Domov</a>"]
    for key, page in SECTION_PAGES.items():
        href = page_href(page["slug"], prefix)
        current_attr = ' aria-current="page"' if current == key else ""
        links.append(f'<a href="{esc(href)}"{current_attr}>{esc(page["title"])}</a>')
    compare_href = page_href(COMPARE_PAGE["slug"], prefix)
    compare_attr = ' aria-current="page"' if current == "compare" else ""
    links.append(f'<a href="{esc(compare_href)}"{compare_attr}>{esc(COMPARE_PAGE["title"])}</a>')
    return "\n".join(links)


def shell(title, description, path, body, *, prefix="", current_nav="", json_ld=""):
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
<div class="page-shell">
  <header class="site-header">
    <div class="site-header__inner">
      <a class="site-brand" href="{esc(prefix or './')}">{SITE_NAME}</a>
      <nav class="site-nav">
        {nav_links(prefix, current_nav)}
      </nav>
    </div>
  </header>
  <main class="page-main">
    {body}
  </main>
  <footer class="site-footer">
    <p>Dáta: <a href="{NRSR_LIST_URL}" target="_blank" rel="noreferrer">Národná rada SR</a>.</p>
  </footer>
</div>
</body>
</html>
"""


def stats_block(meta, stats):
    return f"""
<section class="page-title page-title--home">
  <p class="eyebrow">Majetkové priznania verejných funkcionárov SR</p>
  <h1>Majetkové priznania s vlastnými URL pre každého funkcionára.</h1>
  <p class="lede">{SITE_DESCRIPTION} Dáta pokrývajú roky {meta["years"][0]} až {meta["years"][-1]}.</p>
  <dl class="metric-strip">
    <div><dt>Funkcionárov</dt><dd>{fmt_int(meta["count"])}</dd></div>
    <div><dt>Rokov dát</dt><dd>{len(meta["years"])}</dd></div>
    <div><dt>Medián príjmu</dt><dd>{fmt_currency(stats["median_income"])}</dd></div>
    <div><dt>Slovenský medián</dt><dd>{fmt_currency(SK_MEDIAN_INCOME[meta["years"][-1]])}</dd></div>
  </dl>
</section>
"""


def topic_row(page, prefix=""):
    return f"""
<li class="topic-row">
  <a href="{esc(page_href(page["slug"], prefix))}">
    <strong>{esc(page["title"])}</strong>
    <span>{esc(page["intro"])}</span>
    <em>Otvoriť prehľad</em>
  </a>
</li>
"""


def featured_sections(prefix="", *, current=""):
    rows = []
    for key, page in SECTION_PAGES.items():
        if key == current:
            continue
        rows.append(topic_row(page, prefix))
    return f"""
<section class="section-block section-block--tight">
  <div class="section-heading">
    <h2>Tematické stránky</h2>
    <p>Každý prehľad má vlastnú indexovateľnú stránku namiesto anchor navigácie.</p>
  </div>
  <ul class="topic-list">
    {''.join(rows)}
  </ul>
</section>
"""


def person_row(person):
    total = total_income(person["income"])
    role = display_role(person.get("public_function"))
    summary_bits = []
    if person.get("n_properties"):
        summary_bits.append(f"{person['n_properties']} nehnuteľností")
    if person.get("n_obligations"):
        summary_bits.append(f"{person['n_obligations']} záväzkov")
    if person.get("total_changes"):
        summary_bits.append(f"{person['total_changes']} zmien")
    summary = " · ".join(summary_bits) or "Bez ďalších evidovaných zmien"
    return f"""
<li class="person-row"
    data-name="{esc(normalize_whitespace(person['name']).lower())}"
    data-function="{esc(role.lower())}"
    data-income="{total}"
    data-properties="{person.get('n_properties', 0)}"
    data-changes="{person.get('total_changes', 0)}">
  <a class="person-row__link" href="{esc(person_href(person['slug']))}">
    <strong class="person-row__name">{esc(person['name'])}</strong>
    <small class="person-row__role">{esc(role)}</small>
    <span class="person-row__summary">{esc(summary)}</span>
    <span class="person-row__meta">{fmt_currency(total) if total else 'Bez uvedeného príjmu'}</span>
  </a>
</li>
"""


def render_home(index, highlights, meta, stats):
    latest_year = meta["years"][-1]
    ld = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": SITE_NAME,
        "description": SITE_DESCRIPTION,
        "license": "https://creativecommons.org/publicdomain/mark/1.0/",
        "creator": {"@type": "Organization", "name": SITE_NAME},
        "url": abs_url("/") or "/",
        "keywords": [
            "majetkové priznania",
            "verejní funkcionári",
            "Slovensko",
            "transparentnosť",
        ],
    }
    body = f"""
{stats_block(meta, stats)}
<section class="section-block section-block--tight">
  <div class="section-heading">
    <h2>Vyhľadávanie funkcionárov</h2>
    <p>Filtrovanie funguje bez zmeny URL, ale každý výsledok smeruje na samostatnú detailnú stránku.</p>
  </div>
  <div class="toolbar">
    <label class="toolbar__search">
      <span class="sr-only">Hľadať</span>
      <input id="search" type="search" placeholder="Hľadať meno alebo funkciu">
    </label>
    <label class="toolbar__sort">
      <span class="sr-only">Triediť</span>
      <select id="sort">
        <option value="name">Meno A-Z</option>
        <option value="income_desc">Príjem ↓</option>
        <option value="income_asc">Príjem ↑</option>
        <option value="properties">Nehnuteľnosti ↓</option>
        <option value="changes">Zmeny ↓</option>
      </select>
    </label>
  </div>
  <p id="result-count" class="result-count">{len(index)} funkcionárov</p>
  <div class="results-head results-head--people">
    <span>Meno</span>
    <span>Funkcia</span>
    <span>Prehľad</span>
    <span>Príjem</span>
  </div>
  <ul id="person-list" class="person-list">
    {''.join(person_row(person) for person in index)}
  </ul>
</section>
{featured_sections()}
<section class="section-block section-block--tight">
  <div class="section-heading">
    <h2>Najvyššie príjmy v roku {latest_year}</h2>
    <p>Výber z funkcionárov s najvyššími celkovými príjmami.</p>
  </div>
  <div class="results-head results-head--highlights">
    <span>Meno</span>
    <span>Funkcia</span>
    <span>{highlight_metric_label('top_earners')}</span>
    <span>Kontext</span>
  </div>
  <ul class="highlight-list">
    {''.join(highlight_card(item, 'top_earners') for item in highlights["top_earners"][:8])}
  </ul>
  <p class="section-cta"><a href="{SECTION_PAGES['top_earners']['slug']}/">Otvoriť kompletný rebríček</a></p>
</section>
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
<script src="app.js"></script>
"""
    return shell(
        SITE_NAME,
        SITE_DESCRIPTION,
        "/",
        body,
        current_nav="home",
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
    if kind == "income_jumps":
        sign = "+" if item["delta"] > 0 else ""
        sub = f"{fmt_currency(item['old_total'])} → {fmt_currency(item['new_total'])} · rok {item['year']}"
        if item.get("delta_pct") is not None:
            sub += f" ({sign}{item['delta_pct']} %)"
        value = f"{sign}{fmt_currency(item['delta'])}"
    elif kind == "new_properties":
        value = f"+{item['added']}"
        sub = f"pribudli {item['added']} · celkom {item['total']} · rok {item['year']}"
    elif kind == "new_obligations":
        value = f"+{item['added']}"
        sub = f"pribudli {item['added']} · celkom {item['total']} · rok {item['year']}"
    else:
        value = fmt_currency(item["income"])
        sub = income_multiple_text(item["income"], item.get("year", 2024)) or f"rok {item.get('year', 2024)}"
    return f"""
<li class="highlight-card">
  <a href="{esc(person_href(item['slug'], prefix))}">
    <strong class="highlight-card__name">{esc(item['name'])}</strong>
    <small class="highlight-card__role">{esc(role)}</small>
    <span class="highlight-card__value">{esc(value)}</span>
    <em class="highlight-card__detail">{esc(sub)}</em>
  </a>
</li>
"""


def render_section_page(kind, page, items, meta):
    latest_year = meta["years"][-1]
    title = f"{page['title']} | {SITE_NAME}"
    description = f"{page['intro']} {SITE_NAME} spracúva dáta z NR SR."
    body = f"""
<section class="page-title">
  <p class="eyebrow">Tematický prehľad</p>
  <h1>{esc(page['title'])}</h1>
  <p class="lede">{esc(page['intro'])}</p>
</section>
<section class="section-block section-block--tight">
  <div class="section-heading">
    <h2>Výsledky</h2>
    <p>Každá položka smeruje na samostatnú detailnú stránku funkcionára.</p>
  </div>
  <div class="results-head results-head--highlights">
    <span>Meno</span>
    <span>Funkcia</span>
    <span>{highlight_metric_label(kind)}</span>
    <span>Porovnanie</span>
  </div>
  <ul class="highlight-list">
    {''.join(highlight_card(item, kind, '../') for item in items)}
  </ul>
</section>
{featured_sections('../', current=kind)}
<section class="section-block section-block--tight">
  <p class="section-cta"><a href="../">Späť na domovskú stránku</a> · <a href="../{SECTION_PAGES['top_earners']['slug']}/">Najvyššie príjmy {latest_year}</a></p>
</section>
"""
    return shell(
        title,
        description,
        f"/{page['slug']}/",
        body,
        prefix="../",
        current_nav=kind,
    )


def render_compare_page(index, meta):
    title = f"{COMPARE_PAGE['title']} | {SITE_NAME}"
    description = "Porovnajte 2 až 4 verejných funkcionárov podľa príjmov, majetku, záväzkov a medziročných zmien."
    body = f"""
<section class="page-title">
  <p class="eyebrow">Porovnávací nástroj</p>
  <h1>{esc(COMPARE_PAGE['title'])}</h1>
  <p class="lede">{esc(COMPARE_PAGE['intro'])}</p>
</section>
<section class="section-block section-block--tight">
  <div class="section-heading">
    <h2>Nastavenie porovnania</h2>
    <p>Vyberte aspoň dvoch funkcionárov a rok, pre ktorý chcete porovnať posledný dostupný stav aj medziročnú zmenu.</p>
  </div>
  <div id="compare-tool" class="compare-tool" data-json-prefix="../politicians/">
    <div class="compare-toolbar">
      <label class="compare-field">
        <span>Funkcionár 1</span>
        <select data-compare-slot="0"></select>
      </label>
      <label class="compare-field">
        <span>Funkcionár 2</span>
        <select data-compare-slot="1"></select>
      </label>
      <label class="compare-field">
        <span>Funkcionár 3</span>
        <select data-compare-slot="2"></select>
      </label>
      <label class="compare-field">
        <span>Funkcionár 4</span>
        <select data-compare-slot="3"></select>
      </label>
      <label class="compare-field compare-field--year">
        <span>Rok</span>
        <select id="compare-year"></select>
      </label>
    </div>
    <p id="compare-status" class="result-count">Vyberte aspoň dvoch funkcionárov.</p>
    <div id="compare-results" hidden>
      <section class="compare-section-block">
        <div class="section-heading">
          <h2>Rýchly prehľad</h2>
          <p>Každá karta zobrazuje vybraný rok, zmenu oproti predchádzajúcemu priznaniu a odkaz na detail.</p>
        </div>
        <div id="compare-summary-grid" class="compare-summary-grid"></div>
      </section>
      <section class="compare-section-block">
        <div class="section-heading">
          <h2>Príjmový posun</h2>
          <p>Dumbbell prehľad zobrazuje zmenu medzi predchádzajúcim rokom a vybraným priznaním.</p>
        </div>
        <div id="compare-dumbbells" class="compare-dumbbells"></div>
      </section>
      <section class="compare-section-block">
        <div class="section-heading">
          <h2>Porovnávacia tabuľka</h2>
          <p>Najdôležitejšie metriky vedľa seba pre rýchle porovnanie.</p>
        </div>
        <div id="compare-table-wrap"></div>
      </section>
      <section class="compare-section-block">
        <div class="section-heading">
          <h2>Majetok a záväzky</h2>
          <p>Podrobnejšie položky pre vybraný rok zoradené po osobách.</p>
        </div>
        <div id="compare-lists"></div>
      </section>
    </div>
  </div>
</section>
<script type="application/json" id="compare-index-data">{json_for_script(index)}</script>
<script type="application/json" id="compare-meta-data">{json_for_script(meta)}</script>
<script src="../compare.js"></script>
"""
    return shell(
        title,
        description,
        f"/{COMPARE_PAGE['slug']}/",
        body,
        prefix="../",
        current_nav="compare",
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
    return (
        f"<strong>{esc(normalize_whitespace(item.get('type')))}</strong>"
        f"<span>{esc(join_parts([
            f'Kat. územie: {normalize_whitespace(item.get("cadastral_territory"))}' if item.get('cadastral_territory') else '',
            f'LV: {normalize_whitespace(item.get("lv_number"))}' if item.get('lv_number') else '',
            f'Podiel: {normalize_whitespace(item.get("share"))}' if item.get('share') else '',
        ]))}</span>"
    )


def render_obligation_item(item):
    return (
        f"<strong>{esc(normalize_whitespace(item.get('type')))}</strong>"
        f"<span>{esc(join_parts([
            f'Podiel: {normalize_whitespace(item.get("share"))}' if item.get('share') else '',
            f'Vznik: {normalize_whitespace(item.get("date"))}' if item.get('date') else '',
        ]))}</span>"
    )


def field_summary(change):
    label = {
        "income": "Príjmy",
        "employment": "Zamestnanie",
        "positions": "Funkcie",
        "real_estate": "Nehnuteľnosti",
        "movable_property": "Hnuteľný majetok",
        "obligations": "Záväzky",
        "vehicles": "Vozidlá",
        "gifts": "Dary",
        "property_rights": "Majetkové práva",
    }.get(change["field"], change["field"])
    if change["field"] == "income" and change.get("delta") is not None:
        sign = "+" if change["delta"] > 0 else ""
        return f"{label}: {fmt_currency(change['old_total'])} → {fmt_currency(change['new_total'])} ({sign}{fmt_currency(change['delta'])})"
    if change.get("old_count") is not None:
        diff = change["new_count"] - change["old_count"]
        sign = "+" if diff > 0 else ""
        return f"{label}: {change['old_count']} → {change['new_count']} ({sign}{diff})"
    return label


def render_person_page(person, meta):
    latest = person["timeline"][-1]["data"]
    latest_year = person["years"][-1]
    income = total_income(latest)
    income_detail = income_parts(latest)
    properties = count_items(latest, "real_estate")
    obligations = count_items(latest, "obligations")
    role = display_role(person.get("public_function"))
    summary_parts = []
    if income:
        summary_parts.append(f"príjem {fmt_currency(income)}")
    if properties:
        summary_parts.append(f"{properties} nehnuteľností")
    if obligations:
        summary_parts.append(f"{obligations} záväzkov")
    description = " · ".join([role, *summary_parts])
    title = f"{person['name']} | {SITE_NAME}"

    timeline_html = []
    for idx in range(len(person["timeline"]) - 1, -1, -1):
        entry = person["timeline"][idx]
        diff = entry["diff"]
        prev_year = person["timeline"][idx - 1]["year"] if idx > 0 else None
        if diff["type"] == "new":
            change_text = "Prvý dostupný záznam."
        elif diff["type"] == "unchanged":
            change_text = "Bez zmien oproti predchádzajúcemu roku."
        else:
            change_text = "; ".join(field_summary(change) for change in diff["changes"])
        compare_link = ""
        if prev_year is not None:
            compare_link = (
                f'<button type="button" class="timeline-compare-link" '
                f'data-compare-years="{prev_year},{entry["year"]}">Porovnať roky</button>'
            )
        timeline_html.append(
            f"""
<li>
  <strong>{entry['year']}</strong>
  <span>{esc(change_text)}</span>
  {compare_link}
</li>
"""
        )

    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": abs_url("/") or "/"},
            {
                "@type": "ListItem",
                "position": 2,
                "name": person["name"],
                "item": abs_url(f"/politicians/{person['slug']}/") or f"/politicians/{person['slug']}/",
            },
        ],
    }
    person_ld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": person["name"],
        "description": description,
        "url": abs_url(f"/politicians/{person['slug']}/") or f"/politicians/{person['slug']}/",
        "jobTitle": role,
    }

    body = f"""
<article class="detail-page">
  <nav class="breadcrumb">
    <a href="../../">Domov</a>
    <span>/</span>
    <span>{esc(person['name'])}</span>
  </nav>
  <header class="detail-hero">
    <p class="eyebrow">Detail funkcionára</p>
    <h1>{esc(person['name'])}</h1>
    <p class="lede">{esc(role)}</p>
    <p class="detail-summary">{esc(' · '.join(summary_parts))}</p>
    <p class="detail-links"><a href="{NRSR_DECL_URL}{person['user_id']}" target="_blank" rel="noreferrer">Originál na nrsr.sk</a></p>
  </header>
  <div class="detail-switch">
    <button type="button" class="detail-switch__button is-active" data-detail-mode="profile">Profil</button>
    <button type="button" class="detail-switch__button" data-detail-mode="compare">Porovnanie rokov</button>
    <a class="detail-switch__link" href="../../{COMPARE_PAGE['slug']}/?ids={esc(person['user_id'])}">Porovnať s ďalšími</a>
  </div>
  <div class="detail-mode detail-mode--active" id="detail-profile" data-detail-panel="profile">
    <section class="stats-grid stats-grid--detail">
      <div class="stat-card"><span>{fmt_currency(income) if income else '—'}</span><small>celkový príjem ({latest_year})</small></div>
      <div class="stat-card"><span>{properties or '—'}</span><small>nehnuteľností</small></div>
      <div class="stat-card"><span>{obligations or '—'}</span><small>záväzkov</small></div>
      <div class="stat-card"><span>{person['total_changes'] or '—'}</span><small>zaznamenaných zmien</small></div>
    </section>
    <section class="detail-grid">
      <section class="card">
        <h2>Príjmy</h2>
        <dl class="facts">
          <div><dt>Z verejnej funkcie</dt><dd>{fmt_currency(income_detail['public_function'])}</dd></div>
          <div><dt>Iné</dt><dd>{fmt_currency(income_detail['other'])}</dd></div>
        </dl>
      </section>
      <section class="card">
        <h2>Zamestnanie</h2>
        <p>{esc(normalize_whitespace(latest.get('employment') or 'Neuvedené'))}</p>
      </section>
      <section class="card">
        <h2>Nehnuteľnosti</h2>
        {render_items(
            latest.get("real_estate"),
            render_real_estate_item,
            "Bez uvedených nehnuteľností.",
        )}
      </section>
      <section class="card">
        <h2>Záväzky</h2>
        {render_items(
            latest.get("obligations"),
            render_obligation_item,
            "Bez uvedených záväzkov.",
        )}
      </section>
      <section class="card card--wide">
        <h2>Zmeny v čase</h2>
        <ul class="timeline-list">
          {''.join(timeline_html)}
        </ul>
      </section>
    </section>
  </div>
  <section class="section-block section-block--tight compare-shell" id="detail-compare" data-detail-panel="compare" hidden>
    <div class="section-heading">
      <h2>Porovnanie rokov</h2>
      <p>Vyberte dve priznania a porovnajte príjmy, funkcie, majetok a záväzky.</p>
    </div>
    <div id="person-compare-panel"></div>
  </section>
</article>
<script type="application/ld+json">{json.dumps(breadcrumb_ld, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(person_ld, ensure_ascii=False)}</script>
<script type="application/json" id="person-compare-data">{json_for_script(person)}</script>
<script src="../../compare.js"></script>
"""
    return shell(
        title,
        description,
        f"/politicians/{person['slug']}/",
        body,
        prefix="../../",
    )


def compute_site_stats(index):
    incomes = sorted(total_income(person["income"]) for person in index if total_income(person["income"]) > 0)
    median_income = incomes[len(incomes) // 2] if incomes else 0
    return {"median_income": median_income}


def build():
    commits = get_commits()
    years = [year for _, year in commits]
    print(f"Found {len(commits)} commits: {years}", file=sys.stderr)

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

        politicians[user_id] = {
            "user_id": user_id,
            "name": name,
            "public_function": latest.get("public_function"),
            "role": display_role(latest.get("public_function")),
            "years": [entry["year"] for entry in timeline],
            "timeline": timeline,
            "total_changes": total_changes,
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
                "role": politician["role"],
                "years": politician["years"],
                "income": latest.get("income"),
                "n_properties": count_items(latest, "real_estate"),
                "n_obligations": count_items(latest, "obligations"),
                "total_changes": politician["total_changes"],
            }
        )

    (SITE_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (SITE_DIR / "highlights.json").write_text(json.dumps(highlights, ensure_ascii=False), encoding="utf-8")
    meta = {"years": years, "count": len(politicians)}
    (SITE_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    detail_json_dir = SITE_DIR / "politicians"
    detail_json_dir.mkdir(exist_ok=True)
    for uid, data in politicians.items():
        (detail_json_dir / f"{uid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    stats = compute_site_stats(index)

    (SITE_DIR / "index.html").write_text(render_home(index, highlights, meta, stats), encoding="utf-8")

    for kind, page in SECTION_PAGES.items():
        target_dir = SITE_DIR / page["slug"]
        target_dir.mkdir(exist_ok=True)
        (target_dir / "index.html").write_text(
            render_section_page(kind, page, highlights[kind], meta),
            encoding="utf-8",
        )

    compare_dir = SITE_DIR / COMPARE_PAGE["slug"]
    compare_dir.mkdir(exist_ok=True)
    (compare_dir / "index.html").write_text(
        render_compare_page(index, meta),
        encoding="utf-8",
    )

    detail_html_dir = SITE_DIR / "politicians"
    for child in detail_html_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
    for uid, data in politicians.items():
        target_dir = detail_html_dir / data["slug"]
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "index.html").write_text(render_person_page(data, meta), encoding="utf-8")

    robots_lines = ["User-agent: *", "Allow: /"]
    if SITE_URL:
        sitemap_entries = [abs_url("/")]
        for page in SECTION_PAGES.values():
            sitemap_entries.append(abs_url(f"/{page['slug']}/"))
        sitemap_entries.append(abs_url(f"/{COMPARE_PAGE['slug']}/"))
        for data in politicians.values():
            sitemap_entries.append(abs_url(f"/politicians/{data['slug']}/"))
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
