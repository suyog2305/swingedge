# swingedge
Personal swing trading intelligence

**[ROADMAP.md](ROADMAP.md)** — what is built, what is blocked, and what is next.

## Weekly Report

A native, JSON-driven weekly markets digest (FII/DII flows, derivatives, FX, commodities, debt, India & US stock leaders, 52-week-high candidates, DMA breadth with history, a rolling 4-week comparison, and a global ETF heatmap).

- Open **Weekly Report** in the sidebar; pick a week from the selector.
- Data lives in `library/weekly/` — one JSON per week plus `index.json`. The contract is documented in [library/weekly/README.md](library/weekly/README.md).
- Build a week from your exports with `python tools/build_weekly.py --week-ending YYYY-MM-DD --india … --us … --etf … --fiidii … --indices …` (csv or xlsx, no extra packages), then author the commentary fields (or hand the file to Claude) and push.

## Momentum cockpit — RS Screen · RS Trend · Sectors · Stage 2 Tracker

A WealthLab-style momentum cockpit built natively into SwingEdge (dark **Lab** theme by default; toggle to the cream **Ledger** theme in the sidebar):

- **Markets & Macro** — the landing page (see below); the old Market Pulse's sector rotation and leaders now sit at the bottom of it.
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

## Shortlist - the end-of-day decision loop

`python tools/build_shortlist.py` ranks the whole scan for convergence (new 52-week high + Stage 2 +
earnings + today's move + a news trigger) and writes the top 15-20 names, each with the reason it
qualified. No model, no API key, no cost. Open **Ideas -> Shortlist**, tick up to five, and copy the
brief to hand to the report generator. Details in [data/scans/README.md](data/scans/README.md).

## Markets & Macro, Market News, and your own book

- **Markets & Macro** (the landing page) is the old Market Pulse and Commodities pages in one: the indices,
  India VIX, the Indian and US 10-year yields, the dollar and the rupee, crude, gold, silver and copper; a
  driver table that puts the textbook reading next to the correlation measured in your own data; foreign
  portfolio flows by day, by month and by sector; NSE's sector indices with turnover and P/E; and your
  sub-sector trackers. Data: `data/markets/markets.json`, built by `python tools/fetch_markets.py` from
  NSE's end-of-day index file, Yahoo, RBI, NSDL and SEBI. Every chart has an **Expand** view.
- **Results dates** - NSE's board-meeting calendar (`data/markets/events.json`, next 60 days, each meeting
  tagged results / results+dividend / fund-raising / other), refreshed by `python tools/fetch_nse_lists.py`
  in the evening pull.
- **Surveillance lists** - NSE's ASM (long and short term, with stage) and GSM lists by symbol, with the list
  date (`data/markets/surveillance.json`, same tool).
- **Trading holidays** - NSE's CM-segment holiday list (`data/markets/holidays.json`, same tool; the morning
  GitHub job tops it up too). The evening pull uses it to tell a holiday from a missed session.
- **Delivery %** - traded and deliverable quantity and the delivery percentage per symbol for the scan's
  session, from NSE's MTO file (`data/daily/delivery.json`, same tool; NSE publishes it about 17:00 IST).
- **Market breadth** - per scan day: the share of the universe above its 50- and 200-day averages and near
  its 52-week high, advances and declines, the trend-template and Stage 2 counts, a regime word and the
  4-week changes in the three percentages, all against one reference scan whose date is `ref_4w`
  (`data/daily/breadth.json`, rebuilt from the scan archive by `python tools/build_breadth.py`).
- **Pull watchdog** - `.github/workflows/pull_watchdog.yml` checks at 22:00 IST on weekdays that the evening
  pull has built the expected session, opens a `pull-watchdog` issue when it has not, and closes it when the
  scans catch up.
- **Market News** collects the overnight headlines early each morning (`tools/market_news.py collect`, run by
  `.github/workflows/market_news.yml`, scheduled for 03:13 IST; GitHub often starts it late) and groups them by topic; a short digest written from those
  headlines alone is merged on top by a Claude routine.
- **Global Peers** puts the Indian names you follow next to the US and global companies that mirror, supply or
  buy from them (HFCL and Sterlite Tech with Corning, MTAR with Bloom Energy, Netweb and E2E with NVIDIA, Super
  Micro and Nebius, GNG with SK hynix and Samsung), with each side's moves, how closely their daily returns have
  tracked over 3, 6 and 12 months, the next US earnings dates and recent headlines. Pairs live in
  `data/peers/pairs.json` (edit by hand); `python tools/build_peers.py` rebuilds `data/peers/peers.json`, and the
  morning GitHub job runs it too. Correlation is co-movement, not cause: a strong one says the two have tended to
  move together, not that one will follow the other.
- **RS Leaders** explains itself: an **i** beside every column, and a click on any Trend, Exhaustion or
  Verdict cell gives that stock's own reason. It also lists the names that entered the top 25 this week
  and the ones climbing toward it, and checks your holdings against the list.
- **Your holdings stay private.** They are kept in `.secrets/holdings.json` on your PC (gitignored) and in
  your browser's storage after **Import holdings**; nothing about them is published.
  `python tools/holdings_check.py` prints the same check in a terminal.
- **Your book, reviewed from the candles.** `python tools/kite_sync.py` logs you in to Zerodha once a day
  through the free Kite Connect "Personal" API and writes your holdings and positions to `.secrets/`;
  `python tools/positions_review.py` then pulls daily and hourly candles (Yahoo, no key), computes the
  structure, patterns and levels, adds the RS Leaders context, and has Claude (the Claude API, paid from your
  API credits; the cost is printed after each run) write a structured review to `.secrets/positions_review.md`.
  It describes the charts and checks your own rules; it never says buy or sell. `--dry-run` shows what would
  be sent, `--loop 60` keeps it running through the session, `--model claude-sonnet-5-5` halves the cost.
- `python tools/check_page.py` verifies `index.html` still loads after an edit.
