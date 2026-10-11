#!/usr/bin/env python3
"""
rate_cohort.py — rank a set of names on the evidence in their research reports.

    python tools/report/rate_cohort.py                        # the dye-intermediates cluster + GIPCL
    python tools/report/rate_cohort.py --top 3
    python tools/report/rate_cohort.py --cohort holdings      # your own book, read from .secrets/

Cohorts are defined in COHORTS at the top of this file; add one by listing its NSE codes
and giving each name an entry in JUDGEMENT.

The 'holdings' cohort is PRIVATE and is never written into this file (the repo is public).
It is built at run time from .secrets/holdings.json (names[].code, the file holdings_check.py
reads), and its stated scores come from .secrets/judgement_holdings.json when that file exists:
{"CODE": [earnings 1-5, durability 1-5, "the fact that set the earnings score"], ...}.
Names without a row there score a neutral 3/3. Both files are gitignored.

This is a RATING OF EVIDENCE, not a buy list and not an allocation. It answers one
question: across a cohort, where is the case strongest on the four things the reports
actually established? It deliberately does not weight position size, cost basis,
or anything about the holder.

Four components, three computed and one stated:

  TREND       computed from the scan — RS rating, with a bonus for an established
              Stage 2 tenure (60+ days) and for sitting within 3% of the 52-week high.
              Scores ZERO if the trend template fails, because a name your own system
              has excluded should not rank on momentum at all.

  VALUATION   computed — where P/E and P/B sit within this cohort, not against the
              whole market. 5 = cheapest in the cohort, 1 = dearest.

  EARNINGS    STATED, from the reports. Is the growth real, clean and repeatable, or
  QUALITY     is it a base effect, an acquisition, an accounting artefact, or a number
              that needed adjusting? The `why` column names the single fact that set it.

  DURABILITY  STATED, from the reports. Moat, order-book visibility, customer
              concentration, pricing power.

The two stated components carry the heaviest weight, because they are what the reports
add over a screener. They are judgement and are printed alongside their reasoning so
they can be argued with — change them here if you disagree.

The ASM penalty is mechanical: surveillance-flagged scrips carry raised margin
requirements and are commonly excluded from broker margin-funding lists, which is a real
constraint on how a position can be held.

Re-run after each scan. The computed halves update automatically; the stated halves go
stale as results come in and should be revisited when a report is rebuilt.
"""
import argparse, glob, io, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from build_s2history import rs_percentiles, trend_pass, num          # noqa: E402

COHORTS = {
    'chemicals': ('BODALCHEM BHAGERIA KIRIINDUS OAL SHREEPUSHK IGPL GIPCL FOSECOIND').split(),
}
PRIVATE_COHORT = 'holdings'     # built at run time from .secrets/ - never listed in this file
SECRETS = os.path.join(ROOT, '.secrets')

# code: (earnings quality 1-5, durability 1-5, the fact that set the earnings score)
# Sourced from the August 2026 reports in library/research/. Rows for a private cohort live in
# .secrets/judgement_holdings.json, never here.
JUDGEMENT = {
    # --- chemicals cluster + GIPCL, from the August 2026 reports ----------
    'GIPCL':      (5, 4, '+1,807bps margin from solar - STRUCTURAL, 3rd straight quarter'),
    'BHAGERIA':   (5, 3, '+255bps margin, 7th straight sequential quarter, PRE-dates the squeeze'),
    'FOSECOIND':  (4, 5, 'PAT +78% on sales +14%; 22.98% margin is structural, not cyclical'),
    'IGPL':       (4, 3, 'loss to Rs 71 Cr, all operating - but a pure spread, uncontrolled both ends'),
    'BODALCHEM':  (3, 3, 'intermediates FELL 4%; margin compressed 11.66% -> 9.72% sequentially'),
    'SHREEPUSHK': (3, 3, '+9.4% PAT on a flat margin, and volumes dipped'),
    'KIRIINDUS':  (2, 2, 'Rs 286 Cr of Rs 290 Cr profit is treasury income, not operations'),
    'OAL':        (1, 2, 'margin FELL to 7.62%; company told the exchange there is no news'),
}
W_TREND, W_EARN, W_VAL, W_DUR, ASM_PENALTY = 1.0, 1.2, 0.9, 0.9, 0.6


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def private_codes():
    """The holdings cohort, read from .secrets/holdings.json the way tools/holdings_check.py reads it."""
    path = os.path.join(SECRETS, 'holdings.json')
    if not os.path.exists(path):
        raise SystemExit('no .secrets/holdings.json - pull it through Kite (see CLAUDE.md), or rate a '
                         'public cohort: --cohort ' + ', '.join(sorted(COHORTS)))
    obj = jload(path)
    arr = obj if isinstance(obj, list) else (obj.get('names') or obj.get('holdings') or [])
    out = []
    for h in arr:
        code = str((h or {}).get('code') or (h or {}).get('tradingsymbol') or '').strip().upper()
        code = re.sub(r'-(EQ|BE)$|\.NS$', '', code)
        if code and code not in out:
            out.append(code)
    if not out:
        raise SystemExit('.secrets/holdings.json has no names[].code entries')
    return out


def private_judgement():
    path = os.path.join(SECRETS, 'judgement_holdings.json')
    if not os.path.exists(path):
        return {}
    return {str(k).upper(): tuple(v) for k, v in jload(path).items()}


def cohort_score(value, pool):
    """5 = cheapest in this cohort, 1 = dearest. Missing value -> neutral 3."""
    if value is None or not pool:
        return 3.0
    rank = sum(1 for p in pool if p < value) / max(len(pool) - 1, 1)
    return round(5 - 4 * rank, 1)


def main():
    # UTF-8 console (the '−' and '·' below crash a cp1252 pipe). Inline, not a tools/_io.py: that name
    # '_io' is a built-in module, so 'from _io import utf8_stdio' raises ImportError.
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cohort', default='chemicals', choices=sorted(COHORTS) + [PRIVATE_COHORT],
                    help='which set of names to rate (default: chemicals; "holdings" reads .secrets/)')
    ap.add_argument('--top', type=int, default=0, help='show only the top N')
    a = ap.parse_args()
    judgement = dict(JUDGEMENT)
    if a.cohort == PRIVATE_COHORT:
        codes = private_codes()
        judgement.update(private_judgement())
    else:
        codes = COHORTS[a.cohort]

    scans = sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')))
    cur, prev = jload(scans[-1]), jload(scans[-2])
    universe = [r for r in cur.get('universe', []) if r.get('code')]
    rs_percentiles(universe)
    prev_map = {r['code'].upper(): r for r in prev.get('universe', []) if r.get('code')}
    hist = jload(os.path.join(ROOT, 'data', 'daily', 's2history.json'))
    stage2 = {str(s.get('code', '')).upper(): s for s in cur.get('stage2', []) if s.get('code')}
    index = {r['code'].upper(): r for r in jload(
        os.path.join(ROOT, 'library', 'research', 'index.json'))['reports'] if r.get('code')}

    rows = []
    for code in codes:
        row = next((x for x in universe if (x.get('code') or '').upper() == code), None)
        if not row:
            continue
        spell = (hist['stocks'].get(code) or {}).get('calc') or {}
        eq, dur, why = judgement.get(code, (3, 3, ''))
        rows.append(dict(code=code, rs=row.get('_rs') or 0, tt=trend_pass(row, prev_map.get(code)),
                         days=spell.get('days') or 0, wh=num(row.get('from_52wh')) or 0,
                         pe=num(row.get('pe')), pb=num(row.get('pb')),
                         asm=bool((stage2.get(code) or {}).get('asm')),
                         eq=eq, dur=dur, why=why,
                         rating=(index.get(code) or {}).get('rating', '')))

    pes = [x['pe'] for x in rows if x['pe']]
    pbs = [x['pb'] for x in rows if x['pb']]
    for x in rows:
        trend = 5 * (x['rs'] / 99)
        if x['days'] >= 60:
            trend += 0.5                       # an established trend, not a fresh entry
        if x['wh'] > -3:
            trend += 0.3                       # sitting at the highs
        x['trend'] = 0.0 if not x['tt'] else round(min(5.0, trend), 1)
        x['val'] = round((cohort_score(x['pe'], pes) + cohort_score(x['pb'], pbs)) / 2, 1)
        x['total'] = round(x['trend'] * W_TREND + x['eq'] * W_EARN + x['val'] * W_VAL
                           + x['dur'] * W_DUR - (ASM_PENALTY if x['asm'] else 0), 1)

    rows.sort(key=lambda z: -z['total'])
    shown = rows[:a.top] if a.top else rows

    print(f'Cohort: {a.cohort}   |   scan {cur.get("date")}   |   weights: trend {W_TREND} · '
          f'earnings {W_EARN} · valuation {W_VAL} · durability {W_DUR} · ASM −{ASM_PENALTY}')
    print('An evidence ranking, not a buy list or an allocation.\n')
    hdr = (f'{"#":<3}{"CODE":<12}{"TREND":>6}{"EARN":>6}{"VAL":>5}{"DUR":>5}{"ASM":>5}{"TOTAL":>7}'
           f'{"P/E":>7}{"P/B":>7}   WHAT SET THE EARNINGS SCORE')
    print(hdr)
    print('-' * (len(hdr) + 22))
    for i, x in enumerate(shown, 1):
        pe = f'{x["pe"]:.0f}' if x['pe'] else 'n/a'
        print(f'{i:<3}{x["code"]:<12}{x["trend"]:>6}{x["eq"]:>6}{x["val"]:>5}{x["dur"]:>5}'
              f'{("y" if x["asm"] else "-"):>5}{x["total"]:>7}{pe:>7}{(x["pb"] or 0):>7.2f}   {x["why"]}')

    if not a.top:
        print(f'\n{sum(1 for x in rows if x["tt"])} of {len(rows)} pass the trend template · '
              f'{sum(1 for x in rows if x["asm"])} carry an ASM flag')
    print('\nEarnings quality and durability are judgement, taken from the reports and '
          'editable in JUDGEMENT at the top of this file'
          + (' (or .secrets/judgement_holdings.json for the holdings).' if a.cohort == PRIVATE_COHORT else '.'))


if __name__ == '__main__':
    main()
