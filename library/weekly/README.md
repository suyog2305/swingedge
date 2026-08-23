# Weekly Report — data contract (`swingedge-weekly/1`)

The **Weekly Report** section of SwingEdge is rendered natively from one JSON file per week in this folder. Nothing is hard-coded in the app: drop a new `<week-ending>.json` here, add it to `index.json`, push, and it appears (click **Refresh** if the page is already open).

```
library/weekly/
├── index.json          ← list of published weeks (newest first)
├── 2026-08-14.json     ← one file per week, named by the week-ending (Friday) date
├── 2026-08-07.json
└── …
```

**Division of labour**

| Authored in the JSON (by you / your Claude-Cowork pipeline) | Computed by the app from the raw rows |
|---|---|
| hero text, headline KPIs, ticker strip | cap buckets (mcap rank: 1–100 Large, 101–250 Mid, 251–500 Small, 501+ Micro) |
| FII/DII daily sessions, FX, commodities, derivatives, debt, executive summary | top / bottom performers, 52-week-high candidates |
| index returns table, callout notes | market breadth (adv/dec, median), industries table (if rows carry `industry`) |
| *(optional)* pre-computed breadth buckets & crossovers | % above DMA50/200 by bucket + history chart, crossovers (from per-stock flags) |
| | rolling N-week comparison (India & US) — joins this week's rows with prior files by code |
| | Global ETF heatmap, region averages, top/bottom 5, week-over-week table |

Because the comparison, FII/DII history and breadth history are assembled from **the other files in this folder**, the rolling window extends itself: publish week 5 and week 1 drops off automatically.

## Producing a weekly file

1. Export your data as usual (screener.in stock export, US stock / ETF exports, the daily FII/DII file, NSE indices, the global ETF list).
2. Run the converter — every input is optional, it accepts `.csv` or `.xlsx`, and matches columns by name:

```bash
python tools/build_weekly.py --week-ending 2026-08-21 \
  --india exports/screener_india.xlsx \
  --us exports/us_stocks.csv --us-etf exports/us_etfs.csv \
  --etf exports/global_etf.csv --fiidii exports/fii_dii.csv \
  --indices exports/nse_indices.csv
```

   It writes `library/weekly/2026-08-21.json`, upserts `index.json`, and **preserves** anything already in the file that it does not compute — so you can run it first, then hand the file to Claude to fill in `lead`, `headline`, `ticker` and the `macros.*` commentary (or do it by hand). Re-running after new exports only refreshes the data sections.
3. Commit and push. GitHub Pages redeploys in about a minute.

Ask Claude to author the narrative with a prompt like: *"Here is `library/weekly/2026-08-21.json` and this week's notes. Fill `lead`, `headline` (4 KPIs), `ticker`, and `macros.derivatives / fx / commodities / debt / summary` following `library/weekly/README.md`. Keep the computed sections untouched."*

## `index.json`

```json
{
  "updated": "2026-08-22",
  "weeks": [
    { "week_ending": "2026-08-14", "label": "10–14 Aug 2026", "file": "2026-08-14.json", "title": "Weekly Institutional Flows & Markets Report" }
  ]
}
```

`weeks` is sorted newest-first by the app regardless of file order.

## Week file — top level

| key | type | notes |
|---|---|---|
| `schema` | string | `"swingedge-weekly/1"` |
| `week_ending` | `YYYY-MM-DD` | last trading day of the week (Friday). Used for ordering, labels and the rolling window |
| `week_label` | string | display, e.g. `"10–14 Aug 2026"` |
| `report_date` | `YYYY-MM-DD` | optional |
| `title`, `strap`, `tags[]` | string | hero heading, italic strap-line, tag pills (first tag amber, second teal) |
| `lead` | text | hero paragraph. Text fields accept `**bold**`, `*italic*`, `` `code` `` and `[links](https://…)` |
| `headline[]` | `{label, value, sub?, tone?}` | the KPI strip in the hero (4 fit best). `tone`: `up` / `down` / `flat` |
| `ticker[]` | `{label, value, delta?, tone?}` | the dark meta bar under the hero |
| `macros` | object | see below |
| `india` | object | see below |
| `us` | object | see below |
| `comparison` | `{window?, india?: {lead?, notes?}, us?: {lead?, notes?}}` | `window` = number of weeks in the rolling comparison (default 4) |
| `etf` | `{lead?, rows[], notes?}` | global country/region ETFs |
| `extra` | `{macros?[], india?[], us?[], "cmp-india"?[], "cmp-us"?[], etf?[]}` | optional extra sections per tab: `[{title, blocks:[…]}]` |

**Reusable pieces**

* `notes[]` — callouts: `{tone?, label?, text}`; tones `amber` (default) `teal` `blue` `rust` `red` `green`.
* `cards[]` — KPI cards: `{k, v, d?, tone?}` (`k` label, `v` value, `d` footnote).
* `blocks[]` — free-form content: `{type:"p"|"lead"|"sub"|"note"|"cards"|"table", …}` (`table` takes `cols:[{k,t,type}]` + `rows`).

Any section that is absent is simply not rendered, so a back-filled or partial week is fine.

## `macros`

| key | shape |
|---|---|
| `fii_dii` | `{lead?, days:[{date, fii_buy?, fii_sell?, fii_net, dii_buy?, dii_sell?, dii_net}], cards?, notes?}` — **this week's sessions only**; the full history table is the union of every published week |
| `derivatives` | `{title?, lead?, cards?, blocks?, notes?}` |
| `fx` | `{pair?, lead?, days:[{date, value, tone?}], cards?, notes?}` — `value` is free text (`"95.46 (week high)"`) |
| `commodities` | `{lead?, rows:[{name, move, tone?, note?}], cards?, notes?}` |
| `debt` | `{title?, lead?, cards?, blocks?, notes?}` |
| `summary` | `{title?, blocks:[…], notes?}` — executive summary |

## `india`

| key | shape |
|---|---|
| `universe` | `{source?, as_of?, min_mcap?, rows:[…]}` — the full stock export (see row fields) |
| `indices` | `{lead?, broad:[{name, w1, m1, y1, vs_52wh}], sector:[…], breadth?: {adv, dec, universe, median_w1, prev_adv?, prev_dec?}, industries?: [{name, avg_w1, n}], notes?}` — `breadth` / `industries` are computed from the rows when omitted |
| `top_performers` | `{lead?, limit? (50), bottom_limit? (20), notes?}` |
| `near_high` | `{lead?, threshold? (% below 52-week high, 5.5 ≈ screener's "from 52w high ≥ 0.95"), min_mcap? (5000), require_code? (true), notes?}` |
| `breadth` | `{lead?, buckets?: [{bucket, n?, above_dma50?, above_dma200?, pct_above_dma50?, pct_above_dma200?}], crossovers?: {above_dma200[], below_dma200[], above_dma50[], below_dma50[]}, notes?}` — both optional: computed from per-stock `above_dma50/200` flags (crossovers need the previous week's flags too) |

**Universe row fields** (`india.universe.rows[]`)

| field | required | notes |
|---|---|---|
| `name` | ✔ | |
| `code` | | NSE code; omit for BSE-only names (they still join the comparison by `name`) |
| `w1` | ✔ | 1-week return, % |
| `mcap` | | ₹ Cr — drives the cap bucket; alternatively give `bucket` explicitly |
| `m1`, `y1` | | 1-month / 1-year return, % |
| `from_52wh` | | % below the 52-week high, **negative or zero** (`-3.2`) — needed for the near-high table and the `#` marker |
| `near_ath` | | `true` when within 5% of the all-time high (`*` marker) |
| `industry` | | enables the computed industries table |
| `above_dma50`, `above_dma200` | | booleans; enable computed breadth + crossovers |

## `us`

| key | shape |
|---|---|
| `universe` | `{source?, rows:[{name, code, industry?, mcap?, w1, m1?}]}` |
| `etf_universe` | `{source?, rows:[{name, code, assets?, w1, m1?}]}` |
| `top_performers` | `{lead?, limit? (50), bottom_limit? (15), notes?}` |
| `etf_top` | `{lead?, limit? (50), bottom_limit? (15), notes?}` |

## `etf` (global heatmap)

`rows:[{name, code?, region, w1, m1?, y1?, mcap?}]` — grouped by `region` in first-seen order, sorted best-to-worst inside each region. The week-over-week table appears automatically once an earlier file also carries `etf.rows`.

## Sample data

`2026-07-24` → `2026-08-14` were seeded from the public market figures in a reference weekly report so that every tab renders on day one; the commentary in them is placeholder text. Replace them with your own pipeline output whenever you like — nothing else references them.
