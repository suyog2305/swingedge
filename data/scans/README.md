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

RS is only as market-wide as the universe you feed it. A "near-52W-high / weekly-gainers" export gives RS **within that already-strong set**; for a true market-wide RS rating, export a broad screen (e.g. *market capitalization > ₹1,000 Cr*, no return filter).

**Recommended screener.in screen for market-wide RS.** Create a screen with a broad filter and make sure the return columns appear in the export (on screener.in, a ratio only becomes an export column if it is referenced in the query — so tack the returns onto the query even where they don't filter anything):

```
Market Capitalization > 1000 AND
Return over 1year > -1000 AND Return over 6months > -1000 AND
Return over 3months > -1000 AND Return over 1month > -1000 AND Return over 1week > -1000
```

Add `DMA 50`, `DMA 200`, `Down from 52w high`, `Up from 52w low` and `High price all time` as columns too (the trend template and near-high markers use them). With **6-month and 1-year returns present the RS engine automatically switches to IBD-style quarterly weighting**; without them it falls back to the 3m/1m/1w blend. The RS table caps rendering at the top 800 rows for responsiveness — filters and sorting still run over the full universe.

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

## Automating the weekly pull

`tools/fetch_screener.py` pulls your screener.in export automatically, runs `build_scan.py`, and (optionally) commits — so a weekly run is one command:

```bash
python tools/fetch_screener.py --date 2026-08-21            # fetch + build
python tools/fetch_screener.py --dry-run                    # show the plan, fetch nothing
python tools/fetch_screener.py --commit                     # also git commit + push data/scans
```

**One-time setup**

1. `cp tools/screener_config.example.json tools/screener_config.json` (the copy is gitignored). The example is already set to the broad market screen via **`raw_query`** — a screener.in query string that needs **no saved screen**; the fetcher runs it at `/screen/raw/`, reads the Export-to-Excel form's CSRF token, and posts it (retrying screener's occasional 503). The default `"Market Capitalization > 1000"` is what you want; edit it to taste. Each source takes ONE of: `raw_query` (recommended), `screen_url` (a *saved* screen's address-bar URL — used the same CSRF-POST way), or `url` (a direct CSV/XLSX link, e.g. a Google-Sheets `.../export?format=csv`). Set `kind` to `screener` (universe) or `stage2`, plus `min_mcap` and a `name`. Note: screener's raw export uses a fixed column set (no 6-month/1-year returns), so RS uses the 3m/1m/1w blend market-wide; use a saved screen with those columns added if you want IBD weighting.
2. Provide your session cookie **without putting it in any prompt**: create `.secrets/screener_cookie.txt` (gitignored) containing your screener.in `sessionid` value (DevTools → Application → Cookies → `sessionid`), or set the `SCREENER_COOKIE` environment variable. The script only reads it to attach to the download; it never prints, logs, or commits it.

If the cookie is missing or expired, screener.in returns its login page instead of a spreadsheet — the script detects that and stops without writing anything, so a stale cookie can never corrupt a scan file.

### Scheduled daily (Windows Task Scheduler)

A scheduled task **"SwingEdge Daily Pull"** runs `tools/daily_pull.cmd` every day at **09:00 IST**. It (1) pulls the broad universe and rebuilds `data/scans/`, (2) rebuilds the convergence shortlist into `data/daily/shortlist.json`, and (3) commits both in a single commit, rebases on origin and pushes — logging to `.secrets/daily_pull.log`. If the cookie is missing or the fetch fails it stops before steps 2-3 and writes nothing; if the data is unchanged it detects that and skips the commit. The rebase in step 3 keeps it from colliding with the gainers-news cloud routine, which pushes `data/daily/news.json` around 18:30 IST. It runs only when you're logged in (no stored password), so `git push` uses your normal credentials. Until you put your `sessionid` in `.secrets/screener_cookie.txt` it exits cleanly without fetching. On non-trading days screener data is unchanged, so the run is a clean no-op (nothing to commit).

- Run it now / test:  `tools\daily_pull.cmd`  (or `Start-ScheduledTask -TaskName "SwingEdge Daily Pull"`)
- See last result:  `Get-ScheduledTaskInfo -TaskName "SwingEdge Daily Pull"` and the tail of `.secrets\daily_pull.log`
- Change the time:  `Set-ScheduledTask -TaskName "SwingEdge Daily Pull" -Trigger (New-ScheduledTaskTrigger -Daily -At 8:00PM)`
- Disable / remove:  `Disable-ScheduledTask` / `Unregister-ScheduledTask -TaskName "SwingEdge Daily Pull"`

Pairs with the **"SwingEdge daily gainers news"** cloud routine (weekdays 18:30 IST), which refreshes `data/daily/news.json` for the day's top gainers from whatever scan this task last committed. Re-register on another machine with the `Register-ScheduledTask` block using `tools/daily_pull.cmd` and a `-Daily` trigger. Refresh the cookie file whenever the session expires (the log shows a login-redirect message when it does).

The Stage 2 list stays in your hands — keep producing it however you do today and pass it with `build_scan.py --stage2` (or add it as a `kind: "stage2"` export URL if it lives somewhere the cookie can reach).

## Sample weeks

`2026-05-08`, `2026-05-22`, `2026-06-05` (Stage 2 lists + two near-52W-high scans) and `2026-08-21` (near-52W-high scan) are seeded from your own exports so every Edge module renders and the diffs/rotation work out of the box.

## Daily shortlist (the decision loop)

`tools/build_shortlist.py` ranks the whole scan for **convergence** and emits the 15-20 names worth a
research report, with the reason each one qualified. It needs **no model and no API key** - it runs on
data already committed (the scan, the Stage 2 list, the latest PEAD quarter and `data/daily/news.json`).

```bash
python tools/build_shortlist.py                    # newest scan, top 20
python tools/build_shortlist.py --top 15 --min-mcap 1000 --min-score 6
```

Writes `data/daily/shortlist.json` (what the app reads) plus a dated copy in `data/daily/shortlist/`.
Open **Edge -> Shortlist**, tick up to five names, and hit *Copy brief* - that produces the exact
prompt (with each name's reasons, latest results and news trigger) to hand to the report generator.
Picks are remembered per scan date in the browser.

Scoring is deliberately transparent, and every point becomes a human-readable reason on the row:

| Factor | Points |
|---|---|
| at a new 52-week high / within 5% of it | +3 / +2 |
| entered or re-entered Stage 2 this week | +3 |
| on the Stage 2 list (else) / still early (<=8 wks) | +2 / +1 |
| passes the 7-point trend template | +2 |
| Strong / Moderate PEAD earnings | +3 / +2 |
| up >=5% / >=2% today | +2 / +1 |
| up >=10% on the week | +1 |
| RS >=90 / >=80 | +2 / +1 |
| a news trigger on file | +2 |
| leading sector (median RS >=60) | +1 |
