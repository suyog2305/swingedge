# Roadmap

What's built, what's blocked, and what's next — in dependency order rather than wish order.
Status as of **2026-09-20**.

---

## 0. Unblocked — 2026-09-09

**The screener cookie is in.** `fetch_screener.py` reads it, the pipeline ran end to end for
the first time since 27 August, and the app now serves a live 9 September scan of **1,622
names**. The scheduled task was moved from 09:00 to **15:40 IST**, ten minutes after the close,
so it captures the same day rather than the previous one.

The cookie still expires on its own schedule. When it does the log line turns from `NOT SET` to
`redirected to login`, and it needs re-pasting — which is why the manual **EOD button** stays.
Note the session belongs to the account the subscription is named after, not to a separate login.

### What the first real pull taught us, which no browser test had reached

**screener.in appends AT MOST TWO query terms as export columns, in query order.** Terms beyond
the second still filter but never appear in the file. Proven by swapping `Return over 6months`
and `Return over 1year` in the query and re-exporting — the first two arrived, the third did not.

Two routes were tested and ruled out:

- `/user/columns/` (**EDIT COLUMNS**) changes only the **on-screen table**. It does not affect the
  export. The earlier roadmap entry recommending it as the fix for missing columns was **wrong**.
- `DMA 150` still is not a field at all, so Weinstein's 30-week line remains unavailable.

That created a false choice: full coverage *or* the columns the RS composite needs. The plain
query sees every name but has no `r6m`/`r1y`, so RS silently falls back to
`0.5·r3m + 0.3·r1m + 0.2·r1w`; adding the return filters engages IBD quarterly weighting but
drops ~120 names with no full year of history, twelve of which pass every template check except RS.

**Resolved by unioning two pulls.** `tools/merge_exports.py` takes the broad export as the base —
it decides who is visible — and overlays the filtered one for the extra columns, matching on
exact NSE code and never on name. `eod.py` now does both pulls and the merge automatically.
Result: **1,622 names, IBD quarterly weighting active on 1,500, correct short-window fallback on
122.** A name with no year of history is now visible and falls back, instead of vanishing.

> The weighting change moves RS materially — Tejas 67 to 47, Kiri 94 to 78 — but flips **zero**
> trend-template verdicts across the seven names reported on 8 September. Every pass stayed a
> pass and the one fail stayed a fail, so no published conclusion rests on which weighting
> produced it. Checked before the change was accepted, not after.

### 2026-09-10 — the first automatic runs, and what they exposed

The task fired on time (15:40:01). It ran the old `daily_pull.cmd`, which called
`fetch_screener.py` with **no date** — defaulting to the previous Friday — so both automatic runs
wrote to `data/scans/2026-09-04.json`, today's close over yesterday's. `build_shortlist` then
picked the newest scan **by date string**, built from the 9 Sep file, and left the fresh data
unused. It was also a single pull, so `r6m`/`r1y` never arrived. Two independent defects, both
invisible until a run actually succeeded.

Fixed (commit `e0eb231`): the wrapper now calls `eod.py --push`, the one maintained path, which
dates the scan **today**, makes **three** pulls (broad, returns, **volume**) and unions them by
exact code, refuses to write on a non-trading day (every price identical to the newest scan of a
different date — screener keeps serving the last close over weekends), and aborts a failed rebase
instead of leaving the repo mid-rebase. Trigger narrowed to **weekdays**.

The archive was repaired the same day: the mislabelled file deleted, and my own 9 Sep scan — an
11:52 **intraday** pull — replaced with the true close recovered from git, noted inside the file.
The archive is end-of-day throughout. `2026-09-10.json` is the first scan carrying everything:
**1,620 names, IBD weighting on 1,495, volume + 1-month average on 1,615.**

### 2026-09-11 — the first unattended run worked, and exposed that 15:40 is too early

The rewritten wrapper fired at **15:40:01**, made all three pulls, unioned them, built the scan,
rebuilt the shortlist and journal, committed and pushed. The loop closed. And the data it wrote was
wrong in a way no earlier test could have shown: **screener refreshes its fields in stages after
the close.** At 15:40, prices had changed on 98.8% of names and the 52-week distances on 98.6%,
but the **200-DMA was unchanged on 99.9%**, and 3-month returns, 6-month returns and volume on
**100%**. Today's prices against yesterday's averages. Every "200-DMA rising" check failed and the
trend-template pass count collapsed from **388 to 1**. The 9 and 10 Sep scans, pulled at 17:40 and
22:40, show 99% of averages changed — the refresh completes later in the evening.

Three things followed. The 11 Sep data was **reverted** (`ed78543`) so the app serves the clean
10 Sep close. `eod.py` gained a **stale-refresh guard**: if prices moved but the 200-DMA did not,
against the newest scan of a different date, it stops with exit 2 and writes nothing — proven on the
offending export. And the task moved to **20:00 IST**, which is where it was originally wanted, for
the reason originally given.

> A process failure is recorded here because it cost something. While repairing this, a patch to
> `eod.py` missed its anchor and aborted before writing, the "test" that followed ran the unpatched
> tool and rebuilt the stale scan, the note builder filled its blocks from that scan under a
> "10 Sep close" label, verification passed vacuously against the same bad data, and publish pushed
> it. A wrong version of the ibuprofen note was live for roughly fifteen minutes before it was
> rebuilt against the clean scan and republished. Two rules now hold: patch scripts live in files
> and parse before they write, and no commit or publish runs in the same command as the change it
> depends on.

### 2026-09-14 — the guard's first catch, and a holiday

Monday 14 Sep was Ganesh Chaturthi. The 20:00 run pulled all three variants, found every one of
1,556 shared prices identical to the 11 Sep scan, and stopped with "non-trading day. Nothing
written, nothing committed." First unattended no-op, exactly as designed.

### 2026-09-15 — the weekly Stage 2 list now rides along on every daily scan

The 9, 10 and 11 Sep scans carried **no Stage 2 list at all**. The provider's list is weekly and
downloaded by hand; the pull is daily and never knew where to find it. So the RS Screen showed no
Stage 2 marks, the shortlist scored nobody for being on the list, and the tracker was blank —
silently, because an empty list is a valid list.

`eod.py` now looks where the weekly file lands (`exports/`, the folder above the repo, or
`stage2_dir` in `tools/screener_config.json`), reads each file's **own date** — the newest
"Earliest Date" in it, which is the day the provider cut the list — and attaches the newest list
**whose week has closed by the scan date** (the Friday on or before the list's date). A Sunday
list is never attached to the Thursday before it. The scan is stamped with `stage2_asof` and
`stage2_rs_floor`; the week selector and the tracker say "provider list of 13 Sep" and flag a
list older than nine days. `--stage2 PATH` overrides the pick, `--no-stage2` skips it. Backfilled
9 and 10 Sep with the 28 Aug list (the newest whose week had closed) and rebuilt 11 Sep through
the automated path, which picked the new file on its own. All three universes byte-unchanged.

**The list this week was cut at RS 5.07%; the 28 Aug one ran to −8%.** So 336 names "vanished"
between the two, and most of them merely fell out of the export, not out of Stage 2. The file
cannot say which. Both the journal and the tracker now handle this the same way: a name absent
that last sat within 10 RS points of the new file's floor is **presumed still in** (carried
forward unverified until a wider export can see it, 242 names); a name absent that last sat well
above the floor is an **exit, flagged uncertain** because the file is cut (94 names, PURPLEWAVE
at RS 138 among them). Entries come from the provider's own status stamps (27 new, 32 re-entries),
never from a set difference — 8 of the "Continues Trend" names that look new entered on the
5 Sep list that was never downloaded. And every name on today's list gets an open spell whether
or not an earlier, narrower export ever showed it; that also repaired 225 names on the full
28 Aug list that the 23 Aug export (cut at 4.42%) had hidden. The 10-point margin is the width
of ordinary fortnightly drift in the provider's own RS series, not a tuned number.

RS Trend gained the provider's Stage 2 RS% as a column beside our percentile — the two measures
side by side on purpose — and its Cap column, which had been blank because the band was never
computed for those rows, now works.

### 2026-09-19 — two silent failures in the automation, a hole in the publishing gate, and Theme Trackers

**The Stage 2 carry-forward shipped on the 15th never worked in production.** All four scheduled
runs since printed "none found" for the folder where the same function, run by hand, finds the
file. `stage2_asof()` imported `openpyxl`, which lives in the user site-packages — and had been
pip-installed from inside a packaged desktop app, whose writes to `%APPDATA%` Windows redirects to
a private per-app copy. Every shell launched from that app sees the package; Task Scheduler reports
the very same path as non-existent. A bare `except` swallowed the ImportError, so the 16 and 18 Sep
scans carried no list while every manual test passed. Reproduced with a temporary scheduled task,
fixed by reading the date with the stdlib reader the build tools already use, and unreadable files
are now named with their exception. **Rule: anything the scheduled task runs is stdlib-only and is
tested with `python -s`.**

**A missed 20:00 run fires at the next logon and mislabels the data.** At 08:05 on 18 Sep it wrote
17 Sep's close as `2026-09-18.json`; the 20:00 run rebuilt that file and the 17 Sep close was gone.
At 10:00 on 16 Sep it committed a live mid-session snapshot. The default date is now the session
screener is actually serving — the previous weekday before 09:10 and at weekends, today after 15:40,
nothing at all while the market is open. The 17 Sep close was recovered from git (FCL 52.99 equals
the exchange close). **15 Sep is lost:** the machine was off and the catch-up fired mid-session.

**The publishing gate could pass with nothing checked.** `verify_numbers.py` matched its arguments
against a report's code or the first dash-token of its id, so a full id selected no report, printed
"0 reports checked" and exited 0; `publish.py` passed codes, so one CLUSTER note re-verified all of
them, and it read zero checks as a pass. An id now selects exactly that report, selecting nothing is
exit 2, and zero checks aborts a publish unless `--allow-unverified`. Known limit, unchanged: the
verifier checks a block's eleven scan-linked rows, not the computed RS rating or template verdict.

**Theme Trackers** — a new Edge page: the outside driver next to the stock it is supposed to move.
`tools/fetch_themes.py` builds `data/themes/<id>.json` from a config in `tools/themes/`; keyless
public sources, stdlib only, fails soft, runs after the journal as a step that can never stop the
scan. Every chart has one y-axis — measures on different scales are indexed to 100 at a common
date, never given a second axis — a legend carrying the latest value, measured end-labels, a
crosshair tooltip, dashes reserved for forecast, a table twin, and a palette run through a
colour-vision validator against both card surfaces.

- **Texas crude and Fineotex.** Asked for a Texas-production tracker on the reasoning "more output,
  more wells, proportionally more for Fineotex", the desk audited the chain first (43 sources):
  **fails as framed.** The exposure is real — CrudeChem was ~65% of Q1 FY27 revenue — but only ~29%
  of owners' profit (53.33% stake, ~8% net), so +10% CrudeChem sales is about +3% on owners'
  profit. Texas output rose 8.7% from Jan 2023 to Dec 2025 on 39% fewer rigs. CrudeChem sells
  friction reducers and frac additives, so completions and frac crews lead, not barrels; and its
  H1 2026 doubling came against falling completions. The share price correlates −0.03 with WTI
  since the deal. The page is ordered accordingly: weekly Texas and Permian rigs from Baker Hughes'
  own workbook (which answers a plain client and 403s a spoofed browser; sums reproduce the
  published totals exactly), completions, output against rigs, Texas production as the lagging
  series it is, EIA's $58 WTI forecast as a tile. **The audit also corrects the desk's August FCL
  report twice:** the consideration was disclosed (up to USD 11.2 mn for 53.33%), and CrudeChem is a
  frac-chemicals business, not a "less cyclical production chemicals" one. An updated FCL report
  is owed.
- **Memory prices and GNG Electronics.** Not a price feed — TrendForce's quotes are its product, as
  Primary Vision's frac count is — but 40 cited figures in four tables, plus GNG indexed against
  Micron and SK hynix. From GNG's listing day: Micron 885, SK hynix 705, GNG 211; weekly-return
  correlation with Micron 0.11.

**Reports.** The shared evidence base for the data-centre theme found memory pricing holds
strongly, fibre partly (Birla Cable's own fibre-cable revenue FELL in FY26), and optical
interconnect fails for all six names — none makes transceivers or lasers. Published: the
Birla Cable–Vindhya Telelinks merger note (the deal is six months old, Birla Cable is the company
that disappears, Universal Cables is not a party, and BCL closed 18 Sep 77% above its 10-for-115
swap value). Writers now save as they go — a usage limit killed four at once with nothing on disk,
and then two more at the exact moment each finished researching and was about to write. The brief
that works is "read the inputs, a first round of about twenty fetches, then WRITE THE WHOLE FILE;
research further only after it exists".

### 2026-09-20 — the set is complete: eight notes in two days, 64 reports in the library

| Report | Verdict | The finding that matters |
|---|---|---|
| `mpbirlamerger-2026-09` | Merger note | BCL is absorbed INTO Vindhya; 10 VTL per 115 BCL; BCL closed 77% above its swap value |
| `bbox-2026-09` | Hold | Record USD 949M backlog; data-centre share never cleanly disclosed (two estimates that do not reconcile) |
| `ebgng-memory-2026-09` | Hold at CMP | Memory reaches GNG through seven channels; FY26 operating cash flow −₹215.3 Cr against ₹132.0 Cr profit |
| `stltech-2026-09` | Hold | Data-centre 21% of Q1 FY27 sales (1% in FY26); both hyperscaler deals are allocation frameworks; ~760 bps of the margin story is tariff relief |
| `hfcl-2026-09` | Hold | Data-centre ~5% of Q1 FY27 revenue, target 10–12%; operating cash flow −₹378 Cr; LT-ASM Stage IV |
| `stlnetwork-2026-09` | Avoid | Government contracts, five straight losses, 377 debtor days; no AI contract at all |
| `fcl-2026-09` | Neutral | Corrects the August report twice, finds a third omission (₹800 Cr equity authority); "Accumulate on Declines" withdrawn as an entry instruction |
| `dctheme-2026-09` | Thematic note | The commodity story holds far more consistently than the equity-exposure story |

Every one went through the repaired gate; every generated figure matched the 18 Sep close. Both
trackers now carry a verdict and open their report. **Two things the editor caught that the gate
cannot:** the STL Networks draft called relative strength "weak" when the desk's own generated block
ranks it RS 91 passing 7/7 (the writer had read the provider's RS% as ours) — corrected before
publishing; and the GNG writer found the tracker's laptop figure (₹42,000, from a news report)
disagreed with the company's filed transcript (₹40,000) — the datapoint now cites the filing. The
verifier still does not check RS ratings or template verdicts quoted in PROSE; that is the next
hole worth closing.

Sonnet wrote five of the eight at roughly the same token cost as the larger model and with no
visible loss in sourcing discipline, given a brief that names the inputs, the structure and the
scope. The judgment-heavy ones (GNG's seven channels, the Fineotex corrections) stayed on the
larger model.

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

1. **The 150-DMA is missing, and it is Weinstein's actual Stage 2 line.** `DMA 150` is **not** a
   screener.in field — confirmed twice, including in the `/user/columns/` catalogue on 9 Sep — and
   EDIT COLUMNS does not touch exports anyway. Not obtainable from screener. It can, however, be
   **computed from the archive** once ~150 trading days of daily closes have accumulated (from
   10 Sep 2026, roughly April 2027) — the same dependency as item 3.
2. ~~**No volume.**~~ **Solved 10 Sep.** `Volume` and `Volume 1month average` arrive via the third
   pull, so the volume-confirmed breakout — Weinstein's third condition — is computable for the
   first time. The framework block shows `Volume vs 1-month average`; 1.5× is the conventional
   bar. **Not yet wired into the trend template or the shortlist score** — both would change Stage
   2 classification and rankings, so that is a decision, not a patch. Decide once a few weeks of
   volume history show how often the bar is actually cleared on entry days.
3. **No price history.** A single-day snapshot cannot see a *base*, which is half the Stage 1→2
   definition. Structural: it needs stored history, not a better query.
4. **Coverage ceiling of 66–77%.** A quarter to a third of the provider's names sit below the
   ₹1,000 Cr market-cap floor. Those are out of scope, not misses — the calibrator reports them
   separately so the two never get confused. Lowering `--min-mcap` trades this against noise.

> ~~⚠️ Adding return filters to the query costs 127 names…~~ **Moot since 9 Sep**: the broad pull
> decides coverage and the filtered pulls only add columns, so no name is excluded by a filter.

### Also worth doing here

- `rs_pct` from the provider is a **percentage, not a percentile** (their mean 19.8 vs our 78.7,
  correlation 0.696). Documented, not yet reconciled.
- The journal now grows **one day per trading day automatically** (10 days as of 10 Sep). Entries
  come from the provider's `status` field rather than a set diff. The provider list itself is
  still the one dated 28 Aug — a newer weekly Excel is the single input this thread is waiting on.
- ~~**A published verdict can go stale in two days, and the app should show it.**~~ **Built 11 Sep**
  (commit `b53a271`). Every Research Desk card and the reader bar carry one line: what the
  framework said **at publication** (stamped into `index.json` by `tools/report/signals.py` from
  the data point the report's own figures came from — a full scan, or the 8 Sep close snapshot for
  the seven September reports), an arrow, and what it says **now** (computed in the browser from the
  newest scan, so it refreshes with every pull). A FLIPPED pill marks a changed verdict; the hero
  counts them; a filter isolates them. No report is ever rewritten.

  On 10 Sep the count is **ten flips**: Kiri, RateGain, Shadowfax, GIPCL, Foseco and Motilal Oswal
  now fail; MTAR, Astra Micro, Kirloskar Engines and Paramount Cables now pass. GIPCL and Foseco
  were ranked first and third in the chemicals cluster note thirteen days earlier.

  Found while checking it: the Python tools ranked RS over rows carrying an NSE code while the app
  ranks over **every** row in the scan, and at the RS 70 line that moved borderline names — the
  tools said five flips each way, the app six and four. The app's basis is the one every report was
  written on, so `framework_block.py` and `signals.py` now rank over every row too, and the two
  agree exactly. `build_s2history.py` still ranks over code-bearing rows for the journal; align it
  the next time the journal logic is touched.

---

## 1b. Reports — the cross-check that was not checking

Found by running it on 8 September, not by reading it.

`verify_numbers.py` selected `data/scans/<newest>` unconditionally. That file can carry only a
provider Stage 2 list with an **empty universe** — and 2026-08-28 does. Every report was then
skipped as "not in scan universe", `publish.py` saw zero mismatches, and pushed. **The gate had
been open**, and reports were being published against figures nothing had verified. Three fixes
went in together (commit `16e7730`):

1. Walk back to the newest scan that actually carries a universe.
2. `--snapshot` on both `verify_numbers.py` and `publish.py`, so the same live capture that
   generated a report's framework block also verifies it. The two cannot disagree by construction.
3. Moving-average **levels** reclassified as price-linked. A 50-DMA is a function of recent prices
   and drifts exactly as the price does; treating it as fixed failed every older report on two
   rows that were never wrong, only old.

Negative control, and it should stay in the habit: publishing without the snapshot still refuses,
13 mismatches on Syrma. With it, six reports check clean.

`framework_block.py` (commit `da21b0e`) is the other half. Every price-linked figure in a report
now lives in one generated block between `<!--FRAMEWORK:BEGIN-->` / `<!--FRAMEWORK:END-->`, so
refreshing a report is a re-run rather than a rewrite, and prose never carries a number the next
scan will contradict. Validated by regenerating August's Kiri table from the 27 Aug scan and
getting back exactly what had been typed by hand.

### Open, not urgent

- ~~**The masthead rating colour is decorative, not derived.**~~ **Fixed 10 Sep** (commit
  `644430e`): `fix_rating_colour.py` ports `rdRatingCls` verbatim and recoloured 41 Hold and 2
  Avoid cells that had been green. Header and card can no longer disagree; the tool is idempotent
  and should be re-run after any batch of new reports.
- A report body must not label a comparison row `Trailing P/E` or `Price / Book`. The verifier
  takes the **first** match in the document, so an August-versus-September table would feed it the
  stale figures. Caught during the Bodal rewrite and now called out in the generation brief.

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
- **56 research reports**, 13 sections each, every scan-derived figure machine-cross-checked.
- **RS Trend** — a new Edge page: relative strength (and volume) side by side across the last
  few scans, with 1-week / longer-window deltas and an Up / Strong Up / Down / Strong Down
  bucket, built from a screenshot the user shared of a similar tool. RS at each point is
  recomputed from that week's own scan universe — never one week's rank against another week's
  raw score — and verified exactly against an independent Python computation across four stocks
  and four dates. Anchors snap to the nearest broad-market scan (1,000+ names) on or before each
  target date, skipping anything already claimed by a nearer target, so a gap in the archive can
  never make two columns silently repeat the same data; every header shows its real date instead
  of a possibly-false "N weeks ago". Right now that gap is real — broad scans jump from 27 Aug to
  9 Sep, so the three historical columns land at 15/17/21 days back, not an even 7/14/21 — and the
  page says so. The Volume Trend tab is even younger: `vol_1m` only exists from 9 Sep (when the
  3-pull merge shipped), so its own anchor pool is date-gated separately and currently spans just
  2 days; both will read closer to true weekly spacing as the daily archive fills in.
- **The weekly Stage 2 list rides along on every daily scan** (see 2026-09-15 above): picked by
  its own date, stamped on the scan, shown with its age; cut exports handled honestly in both the
  journal and the tracker instead of reading 336 missing names as 336 exits.
- **Multi-name notes carry one generated technical block per stock** (named markers, commit
  `be0549b`), and the verifier checks each block against its own scan row — a note registered as
  `CLUSTER` can no longer slip past the gate. Proven by corrupting one figure and watching it fail.
- **The ibuprofen thesis audit** (`ibuprofen-2026-09`). Asked for a report on Europe's ibuprofen
  shortage amid Chinese supply cuts and the Indian beneficiaries, the desk first verified the
  premise and found it **fails as framed**: 22 finished-dose presentations on five national
  registers with product-level causes and alternatives available, ibuprofen on no EU or German
  critical list, and the Chinese makers' own H1 2026 filings reporting oversupply and price cuts.
  The two articles that launched the theme never mention China. The note says so in its title,
  maps each of the five names' real exposure — Shree Pushkar has none — and finds the only verified
  2026 effect is feedstock-driven cost-push, which favours the backward-integrated makers. Vinati,
  the sole-source IBB maker and purest volume play, fails every technical check.
- **September 2026 editions** for Bodal, Kiri, GNG Electronics, Syrma SGS and Tejas Networks,
  plus new coverage of Tejas and Syrma. Written against 8 September closing prices and verified
  against the capture that produced them.

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
