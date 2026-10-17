# SwingEdge — working notes for Claude

Static app (`index.html`, ~10k lines — grep it, never read it whole) + JSON under `data/`, built by `tools/*.py`
(no LLM). The owner's Windows daily pull runs `tools/eod.py` after the close: build_scan → build_shortlist →
build_s2history → build_rs_tracker → build_breadth → build_indices → fetch_themes → fetch_markets → fetch_nse_lists, then commits its own files under `data/` and pushes
`main` (Phase 4 added build_history after build_breadth, fetch_bhav + build_averages after fetch_nse_lists, and
auto_weekly on the week's last session; see "Phase-4 files"). The pull writes `data/daily/pull_status.json` (ok / cookie / stale); the app shows anything but ok in red in the
sidebar and on the Shortlist pull card. `fetch_screener.py` exits 5 when screener redirects to /login/ or /register/
(session expired); `eod.py --wait-until` then keeps retrying every poll until the deadline, re-reading the cookie file
each time, so a fresh `sessionid` pasted into `.secrets/screener_cookie.txt` in the evening lets the run finish by itself.
A GitHub job (`.github/workflows/market_news.yml`) is scheduled for 03:13 IST (cron 21:43 UTC; GitHub
often starts it late): market_news collect + fetch_markets (everything except NSE's index file) + build_peers. Work on `main`
directly; rebase on `origin/main` before pushing (the pull, the early-morning job or a routine may have landed a commit).

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

## Phase-3 files (results dates, surveillance, holidays, delivery, breadth)
`python tools/fetch_nse_lists.py [--only events,surveillance,holidays,delivery] [--date D] [--quiet] [--status]` writes
`data/markets/events.json` (board meetings, `kind` results / results+dividend / fund-raising / other),
`data/markets/surveillance.json` (ASM long/short + GSM by symbol), `data/markets/holidays.json` (NSE CM holidays, never
drops a date) and `data/daily/delivery.json` (the session's MTO delivery %; a 404 before ~17:00 IST keeps the old file).
`python tools/build_breadth.py` rebuilds `data/daily/breadth.json` (pct above 50/200-DMA, near high, A/D, template,
Stage 2 calc, regime, and pct50/pct200/pct_near_high 4-week changes against one
reference scan `ref_4w`: the newest on or before 28 days back, within 14 days) from the scan archive. Both run in the evening pull as "never blocks" steps (breadth after
build_rs_tracker, the NSE lists after fetch_markets with `--date <scan date>`), and all five are in eod.py's `OUT`. The
morning GitHub job runs only `--only holidays` (NSE's /api/ may refuse its runners). eod.py's `default_scan_date()` and
`resolve_session()` read holidays.json as the second session oracle (Yahoo first; no file = the old behaviour).
`.github/workflows/pull_watchdog.yml` (22:00 IST weekdays) opens a `pull-watchdog` issue when the newest scan is older
than the expected session and closes it on catch-up.

## F&O positioning
`tools/fetch_fno.py` (keyless NSE static files: participant OI csv, F&O bhavcopy zip, ban csv) -> `data/daily/fno.json`:
`participants` {dates, FII/DII/Pro/Client: 8 numbers per date}, `index` (spot, futures OI, PCR for the nearest weekly AND
the nearest monthly series, max pain), `pcr_hist` (monthly-series PCR: the expiring weekly holds most of Nifty's OI and
swings with expiry day, so never chart the weekly), `stocks` (price change, OI change over all expiries, build-up word),
`ban` {today, prev, entered, left}. NSE publishes about 19:15 IST: eod.py's main step usually prints NOT YET and the
20:00 catch-up writes the file. The page block is `fnoBlock()` in index.html (Markets & Macro, `#mk-a-fno`, data in `NL.fn`).

## Phase-4 files (price store, history files, true averages, automatic weekly review)
All stdlib, all "never blocks" steps in eod.py, all in its `OUT`; each tool has `--status` or `--dry-run` and `--quiet`.
- `data/prices/YYYY-MM-DD.json` + `data/prices/index.json` — the NSE price store, written by `tools/fetch_bhav.py`
  (NSE `sec_bhavdata_full`, series EQ/BE/SM/ST, compact `cols` + `rows` by symbol; a complete day is never rewritten).
  The evening pull runs `--date <session>` after fetch_nse_lists; NSE publishes in the evening, so a 404 prints NOT
  YET and the 20:00 trigger's catch-up branch fetches it. `--backfill N` is resumable; misses go under `missing`.
  PREV_CLOSE is not split-adjusted: readers skip a day-step beyond ±25%.
- `data/daily/averages.json` — true simple 50/150/200-day averages of NSE closes (`sma200_22ago` for the slope,
  `above50/150/200`, `stack`), written by `tools/build_averages.py` right after fetch_bhav, for codes with 60+ closes.
  Screener's DMA columns (used by the 7-check template) are exponential; the verdict does not change.
- `data/indices/series.json` — `tools/build_indices.py` now chain-links each tracker and the market line daily from
  the store before the first broad scan (2026-08-21), then continues scan to scan; `ret` gains `m6` and `y1`.
  `--no-store` reproduces the old scan-only series.
- `data/daily/rs_history.json` (last 60 broad scans: rs, vol, price per code) and `data/daily/track.json` (last 120:
  price, rs, `s2` and `tt` as one character per date) — written by `tools/build_history.py` right after build_breadth,
  RS ranked over the whole universe with tools/rs.py exactly as the page does. RS Trend and Track Record read them
  and fall back to fetching scans when a file is missing or older than the newest scan.
- `library/weekly/<week-ending>.json` + `index.json` — the automatic weekly review, written by
  `tools/auto_weekly.py --week-ending <date>` on the week's last session (Friday, or earlier when Friday is a
  holiday; the file is named by the week's last broad scan). It imports `tools/build_weekly.py` for the row shape
  (`india_item`) and the index upsert (`upsert_index`). Sources: the scan, markets.json (nse, fpi.daily, global,
  rbi, mf), breadth.json, s2history.json, series.json, and Yahoo for a fixed US/ETF list in the tool (`--no-us`
  skips it). DII and derivatives are null with a note (no keyless source). An `auto` block in each file holds a hash
  of every section the tool wrote: a section edited or deleted by hand no longer matches and is kept on the next
  run (`--force` overrides); a file with no `auto` block only gets the sections it lacks. Prose is templated from
  the numbers, facts only. The four 2026 Jul-Aug sample weeks are still in the folder but no longer in index.json,
  so placeholder figures never enter the rolling comparison.

## Custom indices — the cheap path (do not open scans or index.html for this)
1. `python tools/build_indices.py find <name fragments>` → NSE codes from the newest scan (`python3` on Linux).
2. `python tools/build_indices.py --quiet add --id <slug> --name "<Name>" --group "<Group>" --codes A,B,C --note "<one line>"`
   → updates `data/indices/indices.json` and rebuilds `data/indices/series.json` (`--quiet` goes before `add`).
3. `git add data/indices && git commit -m "Indices: add <Name>" && git push origin main`.
The Indices page and the tracker block on Markets & Macro render whatever is in `indices.json`: no HTML changes. Indices
are equal-weight, chain-linked, base 100, with an equal-weight "market" line for relative strength. Editing a member
list rebuilds the whole series from history.

## Global Peers — the cheap path (do not open index.html for this)
1. Edit `data/peers/pairs.json`: a group is `{id, name, why, owner, india: [{code}], global: [{sym, name, rel, note, for?}]}`
   (`rel` peer / customer / supplier / parent / bellwether; `for` limits a global name to some Indian codes).
2. `python tools/build_peers.py [--only <group id>] [--no-news] [--no-earnings]` (stdlib, Yahoo + Nasdaq + Google News).
3. `git add data/peers && git commit -m "Peers: <what changed>" && git push origin main`. The morning GitHub job reruns it.
File contract: the tool writes `data/peers/peers.json` (`schema` 1, `asof`, `windows` 3M/6M/1Y = 63/126/250 trading
days, `groups[]` each with `members` keyed by NSE code or Yahoo symbol: side, last, chg, from_52w_high, a 260-day
`series`; global ones add rel, for, lag, earnings, news) and `pairs[]` (n, same, next, beta_same, beta_next, hit,
read, label). `lag` is "same" for exchanges that close before India (.KS .T .HK ...) and "next" for US/Europe, whose
day d is matched with India's next session; `read` is the correlation in that alignment and `label` is its word
(strong >= 0.5, moderate >= 0.3, weak >= 0.15, none, inverse <= -0.3) from the 3M window. The page renders whatever
is in the file. Co-movement, not cause; describe it, never rate a stock from it.

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
Two tools extend this, both stdlib-only, both writing only under `.secrets/`: `python tools/kite_sync.py` (the free Kite
Connect "Personal" API: a browser login once a day, then GET holdings/positions; needs `.secrets/kite_app.json` with the
app's key and secret - never ask for them in chat) and `python tools/positions_review.py` (daily + hourly candles from
Yahoo, features computed in Python, ONE Claude API call per run with structured JSON output, refusal fallbacks and a
cached system prompt; `--dry-run` sends nothing; `--loop N` runs through the session). The API key lives in
`.secrets/anthropic_key.txt` or `ANTHROPIC_API_KEY`. The review's rules are in the SYSTEM text of that tool: describe
structure, patterns, levels and the owner's own rule checks; no buy/sell/size/stop/target/timing language, ever.

## The news routine — ONE routine, no web search
Collectors with no model gather headlines (`tools/market_news.py collect` for the market, `tools/gainers_news.py collect`
for the newest scan's gainers; run by the evening pull and by the GitHub job). The routine only reads and chooses:
1. `python3 tools/daily_news.py brief` — prints what is due (DIGEST and/or GAINERS) with ids, or `NOTHING TO DO`. The
   tool decides what is due, and collects for itself if the scheduled collection has not landed.
2. Write `findings.json`: `{"digest": {"headline","points":[{"t","why","refs":["crude:3"]}],"watch":[]}, "gainers": {"HFCL": "HFCL:2", "X": null}}`
   — only the parts that are due. Digest: 5–8 points, each citing collected ids, nothing that is not in the headlines.
   Gainers: one of the stock's own candidate ids, or null when none is about that company.
3. `python3 tools/daily_news.py merge findings.json --commit` — validates, publishes, one commit.
Schedule (changed 11 Oct 2026): `15 2,13 * * *` UTC = 07:45 and 18:45 IST. The 04:45 IST slot was dropped: the sandbox
cannot reach news.google.com ("nothing could be collected here" every night) and the GitHub job never lands before
05:57 IST, so that run was a guaranteed no-op. A no-op costs 2 turns; the digest run about 4. Do not add slots.
One writer per file, so the collector and the routine can commit at the same moment without a conflict: collectors write
`market_news.json` and `gainers_candidates.json`; the routine writes `market_digest.json` and `news.json` (which also records
`vetted_scan`). Keep it that way.
Do not go back to web-searching each gainer: that was 28 turns and 15 searches a day (it even re-ran on a market holiday).
`gainers_news.py list` / `merge` remain for doing one by hand. The digest and the picks report news; they never rate a stock.

## Token discipline
Prefer the CLIs above over reading data files; `data/scans/*.json` are ~1 MB each. One commit per task.
