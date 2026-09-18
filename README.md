# swingedge
Personal swing trading intelligence

**[ROADMAP.md](ROADMAP.md)** — what is built, what is blocked, and what is next.

## Weekly Report

A native, JSON-driven weekly markets digest (FII/DII flows, derivatives, FX, commodities, debt, India & US stock leaders, 52-week-high candidates, DMA breadth with history, a rolling 4-week comparison, and a global ETF heatmap).

- Open **Weekly Report** in the sidebar; pick a week from the selector.
- Data lives in `library/weekly/` — one JSON per week plus `index.json`. The contract is documented in [library/weekly/README.md](library/weekly/README.md).
- Build a week from your exports with `python tools/build_weekly.py --week-ending YYYY-MM-DD --india … --us … --etf … --fiidii … --indices …` (csv or xlsx, no extra packages), then author the commentary fields (or hand the file to Claude) and push.

## Edge — Market Pulse · RS Screen · Sectors · Stage 2 Tracker

A WealthLab-style momentum cockpit built natively into SwingEdge (dark **Lab** theme by default; toggle to the cream **Ledger** theme in the sidebar):

- **Market Pulse** — the weekly landing: RS distribution, industry momentum leaders, a sector-rotation quadrant, top-RS stocks, and Stage 2 entries/exits at a glance.
- **RS Screen** — every scanned stock ranked by a relative-strength rating (1–99) with a 7-point Weinstein/Minervini trend template, RS-vs-sector, filters, and CSV export.
- **Sectors** — industry-group rankings by median RS with a leading/improving/weakening/lagging rotation map and drill-down.
- **Stage 2 Tracker** — the weekly Stage 2 list with automatic **entered / re-entry / continues / exited** diffs and convergence against the weekly scan.

RS ratings, the trend template, sector rotation and the entry/exit diffs are **computed in the browser** from raw weekly rows — see [data/scans/README.md](data/scans/README.md). Build a week from your screener + Stage 2 exports with:

```bash
python tools/build_scan.py --date 2026-08-21 --screener "<screener export>" --stage2 "<Stage 2 list>"
```

Provide a full market-cap screen (not just the near-52W-high list) for a market-wide RS rating. The Edge data also back-feeds the legacy Stage 2 scan and Convergence funnel automatically.

To automate the weekly pull, `tools/fetch_screener.py` downloads your screener.in export with your session cookie, runs the builder, and can commit + push. Setup and the credential-safe cookie handling are documented in [data/scans/README.md](data/scans/README.md).


## PEAD Radar — quarterly earnings drift

A separate quarterly dashboard that tracks results classified for **post-earnings-announcement drift** and cross-references them with your weekly momentum data:

- **Convergence ★** — Strong/Moderate PEAD stocks that are also near a 52-week high and on the Stage 2 list (strong earnings meeting momentum).
- **Industry earnings strength**, and **quarter-over-quarter** tracking (repeat performers, newly-strong, faded) that fills in as you upload each quarter.
- The **RS Screen** gains an *Earnings* column + PEAD filter, so momentum names can be screened for strong results too.

Upload one file per quarter and build it with `python tools/build_pead.py --file "<quarterly xlsx>"`. The cross-reference is live against the selected Edge week — see [data/pead/README.md](data/pead/README.md).


## Research Desk

Curated, full-length equity-research reports — a **Today's Reads** strip (3–5 featured) plus a searchable archive. Each report is a self-contained HTML page shown in an isolated iframe (rich styling preserved), and every report cross-links to the stock's live RS / Stage 2 / earnings via its NSE code.

Add one with `python tools/build_research.py --file "<report>.html" --feature` (auto-extracts title/code/sector/rating/targets/summary). Contract: [library/research/README.md](library/research/README.md).

## Commodities & Macro — the daily price card

**Commodities → Daily** is a price card over the commodity and currency prices that drive Indian
sector swings: Brent/WTI/Henry Hub, gold and silver (also in INR per 10 g / kg), copper, aluminium
and zinc, DXY, USD/JPY, USD/INR, the US 10-year, VIX, Nifty 50 and the Nifty sector indices, plus
derived gauges (gold/silver, copper/gold, Brent in INR). Every row carries 1d/7d/30d change, a
90-day sparkline and z-score, the source that populated it, and a **stale / fallback / roll /
manual** marker so a dead number is never mistaken for a live one. Six regime tiles (Dollar,
Rupee, Yen/risk, Crude, Metals, Precious) read the 30-day trends against thresholds you can tune
in `scripts/swing_edge/config.json`, with the stock lists next to each rule.

- Data: `data/swing_edge/commodities_latest.json` (what the card reads) and
  `commodities_history.csv` (one row per indicator per day). `manual_overrides.json` pins a value
  by hand (e.g. the FBIL USD/INR reference rate).
- Refresh: the **Swing Edge prices** GitHub Actions workflow runs at 09:00 and 15:45 IST every
  day and commits the data; the card shows which run produced it and how old it is. By hand:
  `python scripts/swing_edge/fetch_prices.py --module commodities` (`--backfill 90` on first run,
  `--dry-run` to fetch without writing, `--verify-symbols` to check the config's NSE codes).
- Sources are public and key-free: Yahoo Finance chart data (primary), FRED CSV and Westmetall's
  LME table as fallbacks (Stooq was tested and blocks the runner, so it is not used). A failed
  source never aborts a run; the field carries its last good value forward, flagged stale.
  `--probe` hits every primary and fallback once and reports which answer.
- Tests: `python -m unittest discover -s tests -t .` (parsers with saved fixtures, maths, unit
  conversions, the pipeline against a stub network, and a headless-Chromium render of the card).

The same `scripts/swing_edge/` pipeline and `sePriceCard` renderer are meant to host the GPU &
memory card as a second module.

## Shortlist - the end-of-day decision loop

`python tools/build_shortlist.py` ranks the whole scan for convergence (new 52-week high + Stage 2 +
earnings + today's move + a news trigger) and writes the top 15-20 names, each with the reason it
qualified. No model, no API key, no cost. Open **Edge -> Shortlist**, tick up to five, and copy the
brief to hand to the report generator. Details in [data/scans/README.md](data/scans/README.md).
