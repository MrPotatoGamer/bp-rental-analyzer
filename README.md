# Budapest Rental Analyzer

Cél: budapesti kiadó lakás hirdetések elemzése **ár / m²** alapon, kerületi rangsorral, "deal finder" logikával és automatikus PDF riporttal.

## Fő feature-ök

- **Scraping (CloudScraper + BeautifulSoup)**: találati oldalak letöltése és a hirdetések kinyerése.
- **Pandas feldolgozás**: tisztítás (ár, alapterület, kerület) + **fajlagos ár** számítás.
- **Kerületi rangsor**: bar chart az átlagos Ft/hó/m² értékekből.
- **Deal Finder**: paraméterezhető szegmens-átlag (pl. XIII. ker, 40–60 m²) és jelzés, ha egy hirdetés **15%+ az átlag alatt** van.
- **Automata PDF riport (fpdf2)**: összefoglaló + heti trend + top 5 deal kattintható linkkel.

## Projekt struktúra

```
.
├── data/               # CSV snapshotok (generált, gitignore-olva; csak .gitkeep van trackelve)
├── images/             # Generált grafikonok (gitignore-olva; csak .gitkeep van trackelve)
├── src/                # Modulok
│   ├── scraper.py      # Adatgyűjtés (CloudScraper)
│   ├── processor.py    # Tisztítás + számítások (Pandas)
│   ├── visualizer.py   # Grafikonok (Seaborn)
│   └── reporter.py     # PDF riport (fpdf2)
├── main.py             # CLI belépési pont
├── requirements.txt
└── README.md
```

## Telepítés

```bash
python -m pip install -r requirements.txt
```

## Használat

Alap futtatás (1 oldal):

```bash
python main.py
```

Több oldal + deal finder szűréssel (pl. XIII. kerület, 40–60 m²):

```bash
python main.py --pages 3 --district 13 --min-area 40 --max-area 60
```

Egyedi keresési URL-lel:

```bash
python main.py --url "https://ingatlan.com/budapest/kiado+lakas"
```

## Outputok

- CSV snapshot: `data/<search_key>/listings_YYYY-MM-DD_HHMMSS.csv` (a trend URL-onként külön mappában gyűlik)
- Grafikonok:
  - `images/district_ranking.png`
  - `images/weekly_trend.png`
- PDF riport: `report_YYYY-MM-DD_HHMMSS.pdf` (alapértelmezett)

Megjegyzés: a `data/`, `images/` és a `report_*.pdf` fájlok alapból **gitignore-olva** vannak, hogy ne kerüljön fel sok generált artefakt a GitHub-ra.

## Logó a PDF-be

Ha szeretnél logót a PDF fejlécbe, tedd be ide:

- `images/company_logo.png`

(Vagy add meg a `--logo-path` kapcsolóval.)

## Megjegyzés

Az ingatlan.com HTML-je és botvédelme változhat, ezért a scraper **best-effort** módon több jellegzetes mintából próbál adatot kinyerni. Ha üres a kimenet, csökkentsd a `--pages` értéket és nézd meg a logokat (`--log-level DEBUG`).

## GitHub feltöltés (gyors)

Ha ez a mappa még nem git repo:

```bash
git init
git add .
git commit -m "Initial commit"
```

Ezután a GitHub-on létrehozott üres repóhoz add hozzá a remote-ot és push-old (a pontos URL-t a GitHub adja meg).
