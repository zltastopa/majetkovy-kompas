# Majetkový kompas

Prehľadný nástroj na prezeranie a porovnávanie majetkových priznaní
verejných funkcionárov Slovenskej republiky. Dáta pochádzajú z
[Národnej rady SR](https://www.nrsr.sk/web/?sid=oznamenia_funkcionarov)
a pokrývajú roky 2019 – 2024.

**[→ Živá verzia](https://mrshu.github.io/majetkovy-kompas/)**

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
scrape.py          # Scraper (NR SR → YAML)
build_site.py      # Generátor statického webu (git história → JSON)
backfill.sh        # Jednorázový skript na scrape všetkých rokov
site/
  index.html       # Frontend (HTML + inline CSS/JS)
  index.json       # Zoznam funkcionárov (generovaný)
  highlights.json  # Rebríčky a signály (generovaný)
  meta.json        # Metadáta (generovaný)
  politicians/     # Detail JSON pre každého funkcionára (generovaný)
```

Dáta žijú na samostatnej git vetve `data` — každý commit
predstavuje jeden rok. Build skript prechádza túto históriu a
počíta diffy.

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
# Jeden politik
uv run python scrape.py --user-id Tomas.Abel --year 2023

# Obmedzený počet (na testovanie)
uv run python scrape.py --year 2024 --limit 10

# Paralelné sťahovanie (predvolené: 8 workerov)
uv run python scrape.py --year 2024 --workers 16
```

## Deploy

GitHub Actions automaticky buildí a deployuje na GitHub Pages
pri každom push-e na `main` alebo `data` vetvu. Workflow je v
`.github/workflows/deploy.yml`.

## Licencia

Dáta sú verejné informácie zverejnené Národnou radou SR.
