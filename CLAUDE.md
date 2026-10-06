# SwingEdge — working notes for Claude

Static app (`index.html`, ~10k lines — grep it, never read it whole) + JSON under `data/`, built by `tools/*.py`
(no LLM). The owner's Windows daily pull runs `tools/eod.py` after the close: build_scan → build_shortlist →
build_s2history → build_rs_tracker → build_indices → fetch_themes → fetch_markets, then commits `data/` and pushes
`main`. The pull writes `data/daily/pull_status.json` (ok / cookie / stale); the app shows anything but ok in red in the
sidebar and on the Shortlist pull card. `fetch_screener.py` exits 5 when screener redirects to /login/ or /register/
(session expired); `eod.py --wait-until` then keeps retrying every poll until the deadline, re-reading the cookie file
each time, so a fresh `sessionid` pasted into `.secrets/screener_cookie.txt` in the evening lets the run finish by itself.
A GitHub job (`.github/workflows/market_news.yml`) runs at 04:00 IST: market_news collect + fetch_markets
(everything except NSE's index file). Work on `main` directly; rebase on `origin/main` before pushing (the pull, the
4 a.m. job or a routine may have landed a commit).

## After any edit to index.html
`python tools/check_page.py` — syntax, a top-to-bottom load in a stubbed page, duplicate ids, sidebar/section pairs.
The load check exists because a `const` used above its own declaration parses fine and then stops the whole app
(3 Oct 2026). New top-level `const`s that an earlier block uses must be declared above that block.

## Sidebar and pages
Six groups: Markets & Macro · Relative Strength & Stage 2 · Ideas · Personal · Review · System. A page is a
`<div class="nav" id="nav-X" onclick="show('X')">` plus `<div class="sec" id="sec-X">`; `show()` tolerates a page with
no sidebar entry (the old Research Library) and `NAV_ALIAS` redirects pages folded into another (`comm` → `pulse`).
The landing page `pulse` is **Markets & Macro**: scan hero (`#se-pulse-body`) → macro, drivers, flows, NSE indices,
trackers (`#mk-body`, from `data/markets/markets.json`) → sector rotation, leaders (`#se-pulse-rest`, from the scan).

## Charts — one engine
Line charts outside Theme Trackers go through `CH.reg[key] = {title, sub, wins, win, build(days) → chGrid(...)}`,
`chCard(key)` for the markup and `chMount(key)` once it is in the DOM. That gives real calendar time on the x-axis,
round-number y ticks, a clickable legend that re-fits the axis, and Expand (full screen, window + log scale).
One y-axis per chart: different units are rebased to 100 (`chGrid(series, start, true)`), never a second axis.

## The "i" buttons
`SE_INFO['key'] = html | () => html`; `seInfoBtn('key')` anywhere, or `info: 'key'` on a `wrTable` column. A stock's own
reason is `rlWhy(event, 'trend' | 'ex' | 'verdict', code)`. Owner's rule: a newcomer must be able to tell what every
label means from the page itself — add an entry whenever a column or label is added.

## Markets & Macro data — `python tools/fetch_markets.py [--only nse,global,rbi,fpi,sectors,mf] [--status]`
One file, `data/markets/markets.json`, which is its own cache (a run fetches only dates it lacks). Sources and their
limits are in the tool's docstring. Each block fails soft and keeps its last good data. No same-day FII/DII from NSE
or BSE: they serve it only to a browser session and this project does not pretend to be one.

## Custom indices — the cheap path (do not open scans or index.html for this)
1. `python3 tools/build_indices.py find <name fragments>` → NSE codes from the newest scan.
2. `python3 tools/build_indices.py --quiet add --id <slug> --name "<Name>" --group "<Group>" --codes A,B,C --note "<one line>"`
   → updates `data/indices/indices.json` and rebuilds `data/indices/series.json` (`--quiet` goes before `add`).
3. `git add data/indices && git commit -m "Indices: add <Name>" && git push origin main`.
The Indices page and the tracker block on Markets & Macro render whatever is in `indices.json`: no HTML changes. Indices
are equal-weight, chain-linked, base 100, with an equal-weight "market" line for relative strength. Editing a member
list rebuilds the whole series from history.

## Relative strength
One engine: `tools/rs.py` ⇄ `seRsQuarters` / `seScore` / `seRank` in `index.html` — IBD 40/20/20/20 weighted average
quarterly return, percentile 1–99 across the scan; keep the two in step. `python3 tools/rs_rank.py --top 25` ranks the
market; `python3 tools/build_rs_tracker.py` is the top-25 persistence / exhaustion tracker (RS Leaders page). "Gaining a
spot" (entered this week, knocking on the door) is computed in the page from the tracker's `codes` map (`rlClimbing`).
UI rule from the owner: show numbers and word labels (Very strong / Strong / Weak / Very weak), never bars; a small
trend line for a path is fine.

## The owner's holdings are PRIVATE — never under `data/`, never committed
The repo and site are public. Holdings live in `.secrets/holdings.json` (gitignored) and in the browser's storage
after "Import holdings" on RS Leaders. To refresh them: `mcp__kite__login` → give the owner the link (Zerodha needs
a manual login every day; it cannot run unattended) → `mcp__kite__get_holdings` → write `.secrets/holdings.json` as
`{"updated","source":"Kite","names":[{"code","qty","avg"}]}` → `python tools/holdings_check.py` prints the check
against the top 25 (the owner's own rule: 70–80% of the book inside it). Read-only: never call a Kite order, GTT or
modify tool. Never write holdings to `data/daily/holdings.json` — the page would read it, but so would everyone else.
Describe the book; do not advise on it.

## The news routine — ONE routine, no web search
Collectors with no model gather headlines (`tools/market_news.py collect` for the market, `tools/gainers_news.py collect`
for the newest scan's gainers; run by the evening pull and by the GitHub job). The routine only reads and chooses:
1. `python3 tools/daily_news.py brief` — prints what is due (DIGEST and/or GAINERS) with ids, or `NOTHING TO DO`. The
   tool decides what is due, and collects for itself if the scheduled collection has not landed.
2. Write `findings.json`: `{"digest": {"headline","points":[{"t","why","refs":["crude:3"]}],"watch":[]}, "gainers": {"HFCL": "HFCL:2", "X": null}}`
   — only the parts that are due. Digest: 5–8 points, each citing collected ids, nothing that is not in the headlines.
   Gainers: one of the stock's own candidate ids, or null when none is about that company.
3. `python3 tools/daily_news.py merge findings.json --commit` — validates, publishes, one commit.
One writer per file, so the collector and the routine can commit at the same moment without a conflict: collectors write
`market_news.json` and `gainers_candidates.json`; the routine writes `market_digest.json` and `news.json` (which also records
`vetted_scan`). Keep it that way.
Do not go back to web-searching each gainer: that was 28 turns and 15 searches a day (it even re-ran on a market holiday).
`gainers_news.py list` / `merge` remain for doing one by hand. The digest and the picks report news; they never rate a stock.

## Token discipline
Prefer the CLIs above over reading data files; `data/scans/*.json` are ~1 MB each. One commit per task.
