# SwingEdge — working notes for Claude

Static app (`index.html`, ~10k lines — grep it, never read it whole) + JSON under `data/`, built by `tools/*.py`
(no LLM, no network). The owner's Windows daily pull runs `tools/eod.py` after the close: build_scan →
build_shortlist → build_s2history → build_rs_tracker → build_indices, then commits `data/` and pushes `main`.
Work on `main` directly; rebase on `origin/main` before pushing (the pull may have landed a new scan).

## Custom indices — the cheap path (do not open scans or index.html for this)
1. `python3 tools/build_indices.py find <name fragments>` → NSE codes from the newest scan.
2. `python3 tools/build_indices.py add --id <slug> --name "<Name>" --group "<Group>" --codes A,B,C --note "<one line>"`
   → updates `data/indices/indices.json` and rebuilds `data/indices/series.json`.
3. `git add data/indices && git commit -m "Indices: add <Name>" && git push origin main`.
The Indices page renders whatever is in `indices.json`: no HTML changes. Indices are equal-weight, chain-linked, base 100,
with an equal-weight "market" line for relative strength. Editing a member list rebuilds the whole series from history.

## Relative strength
One engine: `tools/rs.py` ⇄ `seRsQuarters` / `seScore` / `seRank` in `index.html` — IBD 40/20/20/20 weighted average
quarterly return, percentile 1–99 across the scan; keep the two in step. `python3 tools/rs_rank.py --top 25` ranks the
market; `python3 tools/build_rs_tracker.py` is the top-25 persistence / exhaustion tracker (RS Leaders page).
UI rule from the owner: show numbers and word labels (Very strong / Strong / Weak / Very weak), never bars.

## Daily gainers news routine
`python3 tools/gainers_news.py list` → web-search only the rows marked `search` → `merge findings.json --commit`.

## Token discipline
Prefer the CLIs above over reading data files; `data/scans/*.json` are ~1 MB each. One commit per task.
