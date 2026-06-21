# Majetkový kompas

Prehľadný nástroj na prezeranie a porovnávanie majetkových priznaní
verejných funkcionárov Slovenskej republiky. Dáta pochádzajú z
[Národnej rady SR](https://www.nrsr.sk/web/?sid=oznamenia_funkcionarov)
a pokrývajú roky 2004 – 2025 (podľa dostupnosti na stránke NR SR
a v archíve Wayback Machine).

**[→ Živá verzia](https://kompas.zltastopa.sk/)**

## Čo to robí

- **Scraper** (`scrape.py`) sťahuje majetkové priznania z nrsr.sk
  a ukladá ich ako YAML súbory — jeden na politika
- **Build** (`build_site.py`) číta históriu git-vetvy `data`,
  počíta medziročné zmeny a generuje JSON pre frontend
- **Frontend** (`site/index.html`) je statická stránka so
  vyhľadávaním, rebríčkami a detailom pre každého funkcionára

### Hlavné funkcie

- Vyhľadávanie podľa mena alebo funkcie
- Rebríčky: najväčšie zmeny príjmov, nové nehnuteľnosti, nové
  záväzky, najvyššie príjmy
- Detail funkcionára s grafom príjmov a časovou osou zmien
- **Porovnanie rokov** — plnohodnotný side-by-side diff medzi
  ľubovoľnými dvoma rokmi, so zvýraznenými zmenami
- Kontextové porovnanie s mediánom príjmu funkcionárov aj mediánom
  na Slovensku

## Štruktúra projektu

```
scrape.py              # Scraper (NR SR → YAML) — jeden rok
acquire_evidence.py    # Akvizícia surových HTTP dôkazov bez extrakcie
extract_from_evidence.py # Extrakcia YAML zo surového evidence balíka
validate_evidence.py   # Kontrola hashov a externých kotiev evidence balíka
harden_evidence.py     # RFC3161 timestamp a podpis manifestu
publish_evidence.py    # Zabalenie a publikovanie evidence balíka cez GitHub Releases
reproduce_from_evidence.py # Reprodukcia data/ stavu zo surových dôkazov
scrape_all_years.py    # Scraper všetkých rokov pre každú osobu
scrape_wayback.py      # Obnova deklarácií z Wayback Machine
build_site.py          # Generátor statického webu (git história → JSON)
backfill.sh            # Jednorázový skript na scrape rokov 2019–2024
integrate_wayback.sh   # Integrácia Wayback dát + chronologické zoradenie
supplementary_user_ids.txt  # ~2 700 doplnkových ID funkcionárov
site/
  index.html           # Frontend (HTML + inline CSS/JS)
  index.json           # Zoznam funkcionárov (generovaný)
  highlights.json      # Rebríčky a signály (generovaný)
  meta.json            # Metadáta (generovaný)
  politicians/         # Detail JSON pre každého funkcionára (generovaný)
```

Dáta žijú na samostatnej git vetve `data` — commity sú zoradené
chronologicky podľa roka uznámenia (najstaršie prvé). Pre každý rok
môže existovať viacero commitov (pôvodný scrape, doplnkový scrape,
Wayback Machine). Build skript prechádza túto históriu, použije
posledný commit pre každý rok a počíta diffy.

Surové akvizičné dôkazy žijú na samostatnej vetve `evidence`.
Denný workflow najprv uloží raw HTTP telá, metadáta, manifest a
SHA-256 hash manifestu do `evidence/<github-run-id>/...`; až potom
samostatný krok extrahuje YAML do vetvy `data`. Extrakcia nesiaha na
sieť a musí byť opakovateľná iba z uloženého evidence balíka.

Ak sú nastavené príslušné CI secrets/variables, workflow navyše externe
ukotví každý evidence balík pred publikovaním:

- `EVIDENCE_TSA_URL` — RFC3161 timestamp authority URL; ak existuje,
  `validate_evidence.py` vyžaduje `manifest.sha256.tsr`
- `EVIDENCE_SIGNING_KEY_PEM` — PEM privátny kľúč pre detached podpis
  `manifest.sha256`; ak existuje, validácia vyžaduje podpis
Každý validovaný balík sa zároveň zabalí do `.tar.gz`, publikuje ako
samostatný GitHub Release asset a workflow preň vytvorí GitHub attestation.
Git vetva `evidence` ostáva browsovateľná audítorská kópia; release asset,
attestation, timestamp a podpis sú tvrdšie kotvy pre integritu balíka.

### Zoradenie dátovej vetvy

Commity na vetve `data` **musia** byť chronologicky zoradené podľa
roku (napr. 2004, 2005, …, 2025). Po pridaní nových dát sa vetva
preradí pomocou `git commit-tree` (zachová stromy, zmení len rodičov)
a force-pushne.

## Spustenie lokálne

Potrebujete Python 3.13+ a [uv](https://docs.astral.sh/uv/).

```bash
# Scrape aktuálny rok
uv run python scrape.py --year 2024

# Alebo scrape všetkých rokov naraz (vytvorí 'data' vetvu)
./backfill.sh

# Build statického webu
uv run python build_site.py

# Otvorenie v prehliadači
open site/index.html
```

### Scraper

```bash
# Forenzný dvojkrok: najprv uložiť raw dôkazy
uv run python acquire_evidence.py \
  --evidence-dir evidence/manual-2026-06-21 \
  --user-id Tomas.Abel

# Potom extrahovať YAML iba z uloženého evidence balíka
uv run python extract_from_evidence.py \
  --evidence-dir evidence/manual-2026-06-21 \
  --data-dir data

# Overiť hash-e, manifest a prípadné externé kotvy
uv run python validate_evidence.py \
  --evidence-dir evidence/manual-2026-06-21

# Voliteľne ukotviť manifest mimo git histórie
uv run python harden_evidence.py \
  --evidence-dir evidence/manual-2026-06-21 \
  --timestamp-url "$EVIDENCE_TSA_URL" \
  --signing-key signing-key.pem

# Zabaliť balík pre GitHub Release asset
uv run python publish_evidence.py \
  --evidence-dir evidence/manual-2026-06-21 \
  --output-dir /tmp/evidence-assets

# Reprodukovať celý odvodený data/ stav z evidence balíkov
uv run python reproduce_from_evidence.py \
  --evidence-root evidence/123456789-1 \
  --base-data-dir /tmp/base-data \
  --output-data-dir /tmp/recreated-data \
  --expected-data-dir data \
  --require-timestamp

# Jeden politik, jeden rok
uv run python scrape.py --user-id Tomas.Abel --year 2023

# Všetky roky pre všetkých funkcionárov (z živej stránky nrsr.sk)
uv run python scrape_all_years.py --data-dir /tmp/all_years --workers 8

# Obnova z Wayback Machine archívu
uv run python scrape_wayback.py --input manifest.json --data-dir /tmp/wb

# Obmedzený počet (na testovanie)
uv run python scrape.py --year 2024 --limit 10

# Paralelné sťahovanie (predvolené: 8 workerov)
uv run python scrape.py --year 2024 --workers 16
```

### Hash-e obsahu

```bash
# Jednorázovo vygenerovať hash-e z aktuálneho obsahu v data/
uv run python generate_content_hashes.py \
  --data-dir data \
  --output data/_checks/content-hashes.json
```

## Deploy

GitHub Actions automaticky buildí a deployuje na GitHub Pages
pri každom push-e na `main` alebo `data` vetvu. Workflow je v
`.github/workflows/deploy.yml`.

Samostatný workflow `.github/workflows/check-data.yml` navyše každý
deň znovu scrape-ne najnovšie dostupné priznania a ak sa na NRSR objaví
zmena, uloží nový snapshot do vetvy `data`. Popri YAML dátach zapisuje aj
kanonické hash-e extrahovaného obsahu do `data/_checks/content-hashes.json`,
aby bolo možné sledovať zmeny v samotných deklaráciách z dňa na deň.

Ak je v repozitári nastavený secret `DISCORD_WEBHOOK_URL`, denný workflow
po úspešnom push-i zmenených dát pošle stručný súhrn do príslušného Discord
kanála. Správa vychádza z `content-hashes.json`, ignoruje technické súbory
v `_checks/`, uvádza počty nových/upravených/odstránených priznaní a linkuje
GitHub porovnanie aj pôvodné stránky NR SR.

Build zároveň exportuje do `site/data-status.json` posledný dátum
extrakcie a GitHub odkaz na konkrétny commit alebo diff vo vetve `data`.

## Licencia

Dáta sú verejné informácie zverejnené Národnou radou SR.
