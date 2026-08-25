# Research Desk — reports

The **Research Desk** (Edge → Research Desk) shows curated, full-length equity-research reports — "Today's Reads" (the featured/newest few) plus a searchable archive. Each report is a **self-contained HTML page** rendered in an isolated `<iframe>`, so its own styling is untouched and can be as rich as you like.

```
library/research/
├── index.json              ← report metadata (cards + featured flags)
├── skipper-2026-05.html    ← one self-contained HTML report per file
└── …
```

## Add a report

```bash
python tools/build_research.py --file "Skipper_Research_Report_May2026.html" --date 2026-05-27 --feature
```

It copies the HTML into `library/research/<id>.html` and upserts `index.json`, auto-extracting the metadata from the report's masthead (title, NSE code, sector, rating, CMP/market-cap, bull/base/bear targets, a summary from the thesis box). Override anything with flags:

- `--id` slug (default: `<code|title>-<YYYY-MM>`) · `--code` NSE code · `--date YYYY-MM-DD`
- `--title` · `--sector` · `--summary`
- `--feature` / `--no-feature` — whether it appears in **Today's Reads** (default: featured)

Then commit `library/research/` and push; open **Research Desk → Refresh**. "Today's Reads" = reports flagged `featured` (or, if none, the newest few). To rotate the daily set, `--feature` the new ones and `--no-feature` the old (or edit `featured` in `index.json`).

The metadata parser is tuned to the Chartitude report template (the `<title>`, the `NSE:/BSE:` eyebrow, the KPI strip, the rating bar, the thesis box). A report built on a different template still displays perfectly in the reader — just pass the card fields explicitly (`--title/--code/--sector/--summary/--date`) since auto-extraction may miss them.

## index.json / report fields

| field | notes |
|---|---|
| `id` | slug + filename stem |
| `title` | company / report name |
| `subtitle` | optional one-liner (from the masthead subtitle) |
| `code` | NSE code — powers the "View &lt;CODE&gt;" cross-link to the stock's live RS / Stage 2 / earnings |
| `sector`, `date`, `rating`, `cmp`, `mcap` | shown on the card |
| `targets` | `{base, bull, bear}` price targets |
| `summary` | 1–2 sentence thesis for the card |
| `file` | the HTML filename in this folder |
| `featured` | `true` → appears in Today's Reads |

## Sample

`skipper-2026-05.html` (Skipper Limited, Power T&D, Accumulate) is seeded so the desk renders out of the box. Replace/extend with your own reports.
