# Roadmap

What's built, what's blocked, and what's next — in dependency order rather than wish order.
Status as of **2026-08-30**.

---

## 0. Blocked on one thing

**The screener.in session cookie.** `.secrets/screener_cookie.txt` still holds the scaffolded
placeholder (`PASTE_YOUR…`), so `load_cookie()` correctly treats it as unset and
`fetch_screener.py` refuses to fetch rather than downloading a login page and calling it data.

The `SwingEdge Daily Pull` scheduled task is **Ready** and has been firing daily at 09:00 IST —
and stopping cleanly every morning since 28 Aug. Nothing else is wrong with the pipeline.

Three things unblock the moment the real `sessionid` lands, and none of them can be tested before:

1. **The daily pull starts refreshing at all.** Everything downstream — shortlist, Stage 2
   journal, freshness badge — is currently frozen against the 2026-08-27 scan.
2. **The extended query gets its first end-to-end run.** `Return over 6months` and
   `Return over 1year` were added to the screener query so RS can use the IBD quarterly
   weighting instead of the short-window fallback. Verified in the browser against the live
   screen; never run through `fetch_screener.py`.
3. **The schedule wants retiming.** 09:00 IST is pre-open, so every pull captures the *previous*
   session's close. Coherent, but a day behind. Move the trigger to ~15:40 IST for same-day
   closes once the pull is proven to work at all.

> The cookie expires on its own schedule. When it does, the log line turns from `NOT SET` to
> `redirected to login — your sessionid cookie is missing or expired`, and it needs re-pasting.
> That recurring expiry is exactly why the manual **EOD button** exists next to the scheduled
> task, and why "just automate it" is not the whole answer.

---

## 1. Stage 2 — the main open engineering thread

The goal is to stop needing the weekly provider Excel: compute Stage 2 membership from the
screener export alone. `calibrate_stage2.py` grid-searches the trend template against the
provider's list to measure how close that is.

**Where it actually stands** (`python tools/calibrate_stage2.py`):

| | precision | recall | F1 |
|---|---|---|---|
| current default rule | 88.6% | 66.6% | 75.8 |
| best grid result | 77.8% | 86.5% | 81.6 |
| **out-of-sample expectation** | | | **~79** |

Read the last row, not the second. Fitting and scoring on the same week gives ~85; fitting on one
week and testing on another gives ~79. The gap is the honest measure.

**Do not `--write` the tuned parameters yet.** Only **two genuinely distinct** provider lists
exist so far (`Stage 2_23rd_Aug.xlsx`, `Stage 2_28th_Aug.xlsx`) — the four scans reuse them, and
the calibrator warns about exactly this. Two weeks is a fit, not a validation. Revisit at four or
five distinct weeks.

### What's capping the fit — in order of value

1. **The 150-DMA is missing, and it is Weinstein's actual Stage 2 line.** Confirmed against the
   live screen: `DMA 150` is **not** a screener.in field, so it cannot be added to the query
   directly. The route worth trying is the saved-screen **EDIT COLUMNS** panel, which also avoids
   the side effect below.
2. **No volume**, so a breakout cannot be volume-confirmed — one of the three things Stage 2
   actually means. `Volume > 0` works as a filter (costs ~10 names) but a volume *column* is the
   part that matters.
3. **No price history.** A single-day snapshot cannot see a *base*, which is half the Stage 1→2
   definition. Structural: it needs stored history, not a better query.
4. **Coverage ceiling of 66–77%.** A quarter to a third of the provider's names sit below the
   ₹1,000 Cr market-cap floor. Those are out of scope, not misses — the calibrator reports them
   separately so the two never get confused. Lowering `--min-mcap` trades this against noise.

> ⚠️ Adding return filters to the query costs **127 names** — it silently excludes anything listed
> under a year, which is precisely where fresh Stage 2 entries live. The EDIT COLUMNS route adds
> the data without the exclusion. Prefer it.

### Also worth doing here

- `rs_pct` from the provider is a **percentage, not a percentile** (their mean 19.8 vs our 78.7,
  correlation 0.696). Documented, not yet reconciled.
- The journal has **7 days** (2026-05-08 → 2026-08-28). Entries now come from the provider's
  `status` field rather than a set diff, which fixed a 366-vs-103 over-count. It needs density
  before the day-by-day record is worth reading as a series.

---

## 2. Testers

The feedback section shipped 2026-08-30: notes plus screenshots, attached or pasted, held in
localStorage and exported as one self-contained HTML file with the images embedded.

- [x] Feedback section, screenshot attach/paste, export, WhatsApp text fallback
- [ ] **Send the first tester invite.** Written and ready. It asks for feedback *through the
      feedback section specifically*, so the feature gets exercised by someone who didn't build it.
- [ ] Read the first exported bundle back and see whether the format survives contact
- [ ] Decide whether the 4 MB localStorage budget is enough in practice, or whether it needs
      IndexedDB. Screenshots land at ~10 KB each after downscaling, so the budget is generous —
      but that's a measurement of one synthetic image, not of real use.

---

## 3. Shipped

- **EOD pull button** — freshness badge (amber past two days), the exact command, opens
  screener.in. `tools/eod.py` runs the whole refresh as one command and **stops** on a failed step
  rather than pairing a fresh shortlist with a stale scan.
- **Research handoff** — tick up to 5 shortlist names → `Copy brief` → `publish.py <codes>`.
  Closes the loop that used to end at the clipboard.
- **`publish.py`** — verify-then-push. Refuses to push on a single mismatched figure.
  `REPORT_SPEC.md` carries the house rules.
- **Pinned watchlist** — 5 names surfaced daily regardless of rank, scored identically to
  everything else so the ranking stays honest.
- **48 research reports**, 13 sections each, every scan-derived figure machine-cross-checked.

---

## 4. Not started, deliberately

- **Concall / transcript section.** Explicitly secondary — revisit once the daily pull has been
  running unattended for a couple of weeks.
- **Postgres.** Parked. Git-as-database is working; JSON files plus commits give the time series
  for free. Revisit only if a query genuinely can't be answered by reading a file.

---

## The rule this project keeps

Every number on screen traces to a script that produced it and a commit that dated it. No figure
is ever invented, and where one can't be sourced, the report says so rather than guessing. When
the framework disagrees with the fundamentals, the disagreement leads.
