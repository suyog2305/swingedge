# Report spec — the house rules

What **Edge → Shortlist → Copy brief** hands over, and what the generator must follow. This is the
contract for every report in `library/research/`. Written from the 48 reports already published.

---

## The pipeline

```bash
# 1. write the body fragment (13 sections, no <head>, no <style> — the template supplies those)
tools/report/bodies/<code>.html

# 2. assemble it into the house shell
python tools/report/build_report.py tools/report/bodies/<code>.html <code>-YYYY-MM.html "<footer>" "<sources>"

# 3. register it in the Research Desk index
python tools/build_research.py --file library/research/<code>-YYYY-MM.html --id <code>-YYYY-MM --date YYYY-MM-DD --feature

# 4. verify, then publish — publish.py refuses to push if the cross-check fails
python tools/report/publish.py <code>
```

Never hand-write the `<head>`/`<style>`. `build_report.py` lifts them from the existing reports so a new
one is visually identical to the rest of the library.

---

## Structure — 13 sections, always

`01 Company Overview` · `02–09 the analysis` · `10 Valuation` · `11 Scenarios` · `12 Key Risks` ·
`13 Summary`. Sections 02–09 are chosen for the company; the outer five are fixed.

Every report carries:

- a **masthead** with 5 KPI cells and a 3-cell rating bar
- a **sticky nav** with one anchor per section
- **§12 as `risk-row` blocks**, numbered, most material first
- **§13 as a `rating-box`** ending in a `What to monitor` list of dated, checkable items

---

## Sourcing — the rule that matters most

**Never invent a number, a source, or a URL. Omit rather than fabricate.**

- Every figure traces to results, a filing, a company disclosure, or the scan. No estimates presented as facts.
- **When sources conflict, say so and show the working.** Pick the figure that reconciles with the scan
  data and state why. Bodal's Q1 PAT circulated as ₹9.53 Cr / ₹28.78 Cr / ₹30.38 Cr — the report names all
  three and uses the two that reconcile.
- **When a figure can't be sourced, say that too**, in a `tbl-note`. Divgi's five-year order value and
  Sigma's balance sheet are both flagged as unobtainable rather than guessed.
- **Derived arithmetic must be labelled derived.** Annualising one quarter is fine; presenting it as a
  forecast is not. Always add the caveat inline.
- **Check entity identity.** "Foseco Crucible (India)" is a different company from Foseco India; searching
  "federal" returns Federal Bank, not Federal-Mogul Goetze. Resolve tickers against the scan universe by
  **explicit NSE code**, never by fuzzy name match.

---

## Always run the framework first

Pull RS, the trend template, Stage 2 tenure and distance from the 52-week high from the newest scan, and
put them in the report.

**When the framework disagrees with the fundamentals, lead with the disagreement.** MTAR had a record
order book and failed the trend template on RS 60 — the report opens with that table, not with the order
book. The system exists to keep the holder out of exactly that situation.

## Always check the timing

**Does the reported quarter actually contain the catalyst?** The whole dye-intermediates cluster hinged on
this: Q1 FY27 ended in June, H-acid spiked on 28 July, so no reported number contained the thesis. Bodal's
intermediates segment had actually *fallen* 4%. State the gap explicitly when it exists.

## Always flag surveillance

ASM and ESM constrain how a position can be held — raised margins, and routine exclusion from broker MTF
lists. ESM is the more restrictive. State it in §01, in §12, and in the monitoring list.

---

## Ratings

Use the house vocabulary. The badge colour is derived by `rdRatingCls` in `index.html`:

| Rating starts with / contains | Renders as |
|---|---|
| `Avoid`, `Reduce`, `Sell`, `Underweight` | **sell** |
| `Hold…`, `Neutral…`, or any `hold`, `below`, `at CMP` | **hold** |
| `Accumulate`, `Buy`, `Add`, `Overweight` | **buy** |

Write the rating so the colour is right. *"Quality Compounder at a Full Price"* renders **buy** — it was
changed to *"Hold at CMP — Quality at a Full Price"*. Comparative notes that carry no call must say so:
*"Evidence ranking — not a recommendation"*.

---

## Scope — what a report must not do

Reports analyse companies. They do **not**:

- recommend position sizes, allocations, or how much capital to deploy
- advise on trimming, adding to, or exiting specific holdings
- assume leverage, or model a portfolio

Surfacing the framework's own signals is analysis. Converting them into "buy this much of that" is
investment advice, and it is out of scope regardless of how the request is phrased.

---

## Before publishing

```bash
python tools/report/verify_numbers.py <code>
```

Recomputes every scan-derived figure — CMP, market cap, P/E, P/B, the DMA levels **and the "+25% above"
percentages derived from them**, distance from the high, returns, sales growth. Derived percentages are
where errors hide, because nothing else recalculates them.

**0 mismatches is the bar.** Reports published before the current scan show price `DRIFT` instead, which is
staleness and not an error.

`publish.py` runs this and refuses to push on a real mismatch.
