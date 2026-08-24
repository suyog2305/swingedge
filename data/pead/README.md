# PEAD Radar — data contract (`swingedge-pead/1`)

**PEAD Radar** (Edge → PEAD Radar) tracks quarterly results classified for **post-earnings-announcement drift** — the tendency of stocks with strong earnings to keep drifting higher — and cross-references them against your weekly momentum data. You upload one file per quarter; the app does the ranking, the quarter-over-quarter tracking, and the cross-reference.

```
data/pead/
├── index.json          ← quarters published (newest first)
├── q1-fy27.json        ← one file per quarter
└── …
```

## What you provide vs what the app computes

| You upload (one quarterly file) | The app computes in-browser |
|---|---|
| one row per company: industry, name, YoY/QoQ Sales·OpProfit·EPS·PAT growth, market cap, PEG, and a **PEAD Classification** (Strong / Moderate / Weak / No PEAD) | tier distribution + a Strong→Weak bar |
| | **cross-reference** to the selected Edge week: each name → NSE code (via the scan universe) → near-52W-high, RS rating, and Stage 2 membership |
| | **Convergence ★** — Strong/Moderate PEAD that is *also* near a 52-week high *and* on the Stage 2 list (strong earnings meeting momentum) |
| | **industry earnings strength** — where the Strong/Moderate results cluster (ties to Sector rotation) |
| | **quarter-over-quarter** — repeat performers, newly-strong, and faded names, across the quarters you've uploaded |

The cross-reference is **live against whichever Edge week is selected** — change the week in Market Pulse / RS Screen and PEAD Radar re-matches. Because it bridges *company name → NSE code* through the scan universe, a broad market screen (see `data/scans/README.md`) matches far more names than a narrow near-52W export. The reverse link also lights up: the **RS Screen gains an "Earnings" column and a PEAD filter**, so you can screen momentum names for the ones that also delivered strong results.

## Producing a quarter

```bash
python tools/build_pead.py --file "16 Aug 2026 _ Data.xlsx"
# quarter is read from the sheet name (GoodEarningsQ1FY27 -> "Q1 FY27"); override with --quarter "Q2 FY27"
python tools/build_pead.py --file q2.xlsx --quarter "Q2 FY27" --reported 2026-11-15
```

Columns are matched by name (Industry, Company Name, YoY/QoQ Sales Growth, YoY/QoQ Op Profit Growth, YoY/QoQ EPS Growth, YoY/QoQ PAT Growth, Market Cap, PEG Ratio, PEAD Classification). It writes `data/pead/<quarter>.json`, stamps a normalized `key` on each row for name-matching, and upserts `index.json`. Commit `data/pead/` and push; open **Edge → PEAD Radar → Refresh**. Upload each quarter and the QoQ panel fills in automatically.

## Name matching

Company names (e.g. "Kabra Extrusion Technik Ltd") are normalized — lowercased, `&`→`and`, punctuation and boilerplate words (Ltd, Limited, India, Industries↔Inds, …) removed — and matched to the scan universe's names, then to a whole-word prefix on either side for close variants (so "Kabra Extrusion" ↔ "Kabra Extrusion Technik"). Matches carry the NSE code, which links to the Stage 2 list. A company with no match in the current scan still shows its PEAD data; it just can't display momentum badges until it appears in a scan.

## Week file

| key | shape |
|---|---|
| `schema` | `"swingedge-pead/1"` |
| `quarter` | e.g. `"Q1 FY27"` · `fy`, `q`, `sortkey` (for ordering) |
| `reported` | ISO date the file was compiled (ordering / display) |
| `count`, `distribution` | totals + `{strong, moderate, weak, none}` |
| `rows[]` | `{name, key, industry, pead, tier (3/2/1/0), sales_yoy, sales_qoq, op_yoy, op_qoq, eps_yoy, eps_qoq, pat_yoy, pat_qoq, mcap, peg}` |

## Sample

`q1-fy27.json` (511 companies from the 16 Aug 2026 export — Strong 100, Moderate 75, Weak 130, No PEAD 206) is seeded so the dashboard renders and the cross-reference works against the current scan out of the box.
