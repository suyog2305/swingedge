# swingedge
Personal swing trading intelligence

## Weekly Report

A native, JSON-driven weekly markets digest (FII/DII flows, derivatives, FX, commodities, debt, India & US stock leaders, 52-week-high candidates, DMA breadth with history, a rolling 4-week comparison, and a global ETF heatmap).

- Open **Weekly Report** in the sidebar; pick a week from the selector.
- Data lives in `library/weekly/` — one JSON per week plus `index.json`. The contract is documented in [library/weekly/README.md](library/weekly/README.md).
- Build a week from your exports with `python tools/build_weekly.py --week-ending YYYY-MM-DD --india … --us … --etf … --fiidii … --indices …` (csv or xlsx, no extra packages), then author the commentary fields (or hand the file to Claude) and push.
