# swingedge
Personal swing trading intelligence

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
