# Edge scan data — contract (`swingedge-scan/1`)

The **Edge** modules — Market Pulse, RS Screen, Sectors, Stage 2 Tracker — are rendered natively from one JSON file per week in this folder. You publish raw rows; the app does all the analysis in the browser.

```
data/scans/
├── index.json          ← list of published weeks (newest first)
├── 2026-08-21.json     ← one file per week, named by the week-ending (Friday) date
├── 2026-06-05.json
└── …
```

## What you provide vs what the app computes

| You publish (raw rows) | The app computes in-browser |
|---|---|
| `universe` — a screener export (weekly gainers / near-52W-high, or ideally a full market-cap screen) | **RS rating (1–99)** — percentile of a recency-weighted momentum composite, ranked inside the universe |
| `stage2` — the weekly Stage 2 list | **RS vs sector** — the same percentile inside the industry group (≥5 peers) |
| | **Trend template** — 7 Weinstein/Minervini checks from price, DMA50/200, 52-week position, RS, and DMA200 slope |
| | **Stage 2 diffs** — entered / re-entry / continues / **exited**, by diffing consecutive weekly lists |
| | **Sector rotation** — industry-group median RS, Stage 2 share, momentum, and week-over-week drift + a leading/improving/weakening/lagging quadrant |
| | **Convergence** — stocks that are on the Stage 2 list *and* pass the trend template |

Everything is derived from the other weeks in this folder, so the RS-drift, entries/exits and sector-rotation arrows fill in automatically as you publish more weeks. Two weeks are enough for drift and exits.

### How RS is calculated

For each stock, a momentum composite is built and then **percentile-ranked (1–99) against every other stock in that week's universe**:

- If the export carries 3-month, 6-month and 1-year returns → **IBD-style quarterly weighting** (most recent quarter double-weighted): `0.4·Q1 + 0.2·Q2 + 0.2·H2`.
- Otherwise → a lighter composite of the returns present: `0.5·3M + 0.3·1M + 0.2·1W`.

RS is only as market-wide as the universe you feed it. A "near-52W-high / weekly-gainers" export gives RS **within that already-strong set**; for a true market-wide RS rating, export a broad screen (e.g. *market capitalization > ₹1,000 Cr*, no return filter) — the extra columns (6-month, 1-year returns) also switch on the IBD weighting.

### Trend template (Stage 2 by calculation)

Checked when the columns are present; a stock "passes" when it clears every evaluated check (min 5):

1. Price above the 50-DMA
2. Price above the 200-DMA
3. 50-DMA above the 200-DMA
4. ≥30% above the 52-week low
5. Within 25% of the 52-week high
6. RS rating ≥ 70
7. 200-DMA rising vs the previous scan (needs a prior week)

## Producing a week

```bash
python tools/build_scan.py --date 2026-08-21 \
  --screener "exports/near-52w-high-weekly-scan_23Aug.csv" \
  --stage2   "exports/Stage 2_21st Aug.xlsx"
```

- Either input is optional; `.csv` or `.xlsx`; columns are matched by name (screener.in headings and common variants are all recognised).
- `--screener` accepts your weekly near-52W-high / gainers export **or** a full market screen (recommended for market-wide RS). `--min-mcap 1000` drops tiny names.
- `--stage2` expects the usual columns: *TradingView Code, Industry, Relative Strength %, Market Cap (in cr.), Avg Weekly Volumes (in cr.), % Return, Weeks, Earliest Date, ASM ESM GSM, Days in ASM ESM GSM, Stage2 Status.*
- Re-running for the same date replaces only the sections you pass and updates `index.json`.

Commit `data/scans/` and push — GitHub Pages redeploys in ~1 minute; open **Edge → Market Pulse → Refresh**.

The Edge data also **back-feeds the older Stage 2 · 52W Highs scan and the Convergence funnel** automatically, from the newest week that carries each list — so those legacy tools stay populated without a separate import.

## `index.json`

```json
{ "updated": "2026-08-23",
  "scans": [ { "date": "2026-08-21", "file": "2026-08-21.json", "universe": 216, "stage2": 0 } ] }
```

## Week file

| key | shape |
|---|---|
| `schema` | `"swingedge-scan/1"` |
| `date` | week-ending (Friday) ISO date |
| `screen` | `{name, columns, has[]}` — label + which analytical columns the export supported |
| `universe[]` | one row per screened stock (fields below) |
| `stage2[]` | one row per Stage 2 stock (fields below) |
| `sources` | `{screener, stage2}` filenames, for provenance |

**`universe` row** — `name` (required), `code` (NSE), `isin`, `group` (industry group), `industry`, `price`, `mcap` (₹Cr), `dma50`, `dma200`, `r1d`, `r1w`, `r1m`, `r3m`, `r6m`, `r1y`, `from_52wh` (negative % below high), `up_52wl`, `near_ath`, `ath`, plus optional fundamentals (`pe`, `pb`, `peg`, `eps_yoy`, `sales_yoy`, `np_yoy`, `op_yoy`, quarter-on-quarter variants, `opm_q/pq/pyq`, `float`, `public_hold`). More columns → richer RS + trend template; the minimum is `name` + one return.

**`stage2` row** — `code` (required), `industry`, `rs_pct` (the provider's RS%), `mcap` (₹Cr), `vol_cr`, `ret` (% since entry), `weeks`, `since` (entry date), `asm`, `asm_days`, `status` (New Addition / Re-entry / Continues Trend).

## Automating the weekly export (future)

Today the flow is: export from screener.in → run `build_scan.py` → commit. To automate it, a small fetcher can log into screener.in with your session cookie, pull the saved screen + Stage 2 watchlist as CSV, run the same builder, and commit — on a schedule. That fetcher isn't in the repo yet; ping to add it when you want to wire up your login.

## Sample weeks

`2026-05-08`, `2026-05-22`, `2026-06-05` (Stage 2 lists + two near-52W-high scans) and `2026-08-21` (near-52W-high scan) are seeded from your own exports so every Edge module renders and the diffs/rotation work out of the box.
