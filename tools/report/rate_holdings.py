#!/usr/bin/env python3
"""
rate_holdings.py — rank the holdings on the evidence in their research reports.

    python tools/report/rate_holdings.py           # full table
    python tools/report/rate_holdings.py --top 10

This is a RATING OF EVIDENCE, not a buy list and not an allocation. It answers one
question: across the 18 holdings, where is the case strongest on the four things the
reports actually established? It deliberately does not weight position size, cost basis,
or anything about the holder.

Four components, three computed and one stated:

  TREND       computed from the scan — RS rating, with a bonus for an established
              Stage 2 tenure (60+ days) and for sitting within 3% of the 52-week high.
              Scores ZERO if the trend template fails, because a name your own system
              has excluded should not rank on momentum at all.

  VALUATION   computed — where P/E and P/B sit within this cohort, not against the
              whole market. 5 = cheapest of the eighteen, 1 = dearest.

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
import argparse, glob, io, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from build_s2history import rs_percentiles, trend_pass, num          # noqa: E402

HOLDINGS = ('AKUMS DIVGIITTS EBGNG FCL HAPPYFORGE HFCL IOLCP JYOTICNC KRN MCX '
            'MOTILALOFS OLAELEC RATEGAIN RBA RKFORGE RPTECH SHADOWFAX TDPOWERSYS').split()

# code: (earnings quality 1-5, durability 1-5, the fact that set the earnings score)
# Sourced from the August 2026 reports in library/research/.
JUDGEMENT = {
    'HAPPYFORGE': (5, 4, 'clean print, margin +275bps, guides BELOW its own run-rate'),
    'IOLCP':      (5, 3, 'margin +220bps, capex self-funded, mix shift is measurable'),
    'DIVGIITTS':  (4, 3, 'PAT margin +620bps, but the 5-yr order value is undisclosed'),
    'TDPOWERSYS': (4, 3, 'revenue/EBITDA/PAT all +72% - clean, but no margin leverage left'),
    'MOTILALOFS': (4, 3, 'no adjustments flagged; earnings cyclical to market activity'),
    'MCX':        (4, 5, 'exchange economics, near-monopoly position; NP +103%'),
    'AKUMS':      (4, 4, 'CDMO qualification moat; NP +56%'),
    'FCL':        (4, 3, 'NP +93% on sales +175%'),
    'KRN':        (3, 2, 'strong, but a Rs 183cr state incentive exceeds a full year of profit'),
    'EBGNG':      (3, 3, 'good print, but the limit-down reaction is unexplained; WC-heavy'),
    'HFCL':       (3, 4, 'loss to Rs 246cr profit - real, but ONE quarter of history'),
    'RPTECH':     (3, 2, 'margin COMPRESSED as revenue grew; PAT outgrew EBITDA'),
    'SHADOWFAX':  (2, 2, '8x growth off a Rs 8cr base; 6.79% margin against buyer power'),
    'RKFORGE':    (2, 3, '+297% off a 1.2%-margin base; revenue FLAT sequentially'),
    'JYOTICNC':   (2, 3, 'consolidated PAT BELOW standalone; Huron probe unresolved'),
    'RATEGAIN':   (2, 2, '188% is consolidation; organic segments grew 22.7% and 3.1%'),
    'RBA':        (2, 3, 'still loss-making; Rs 118cr gap between store profit and net loss'),
    'OLAELEC':    (1, 1, 'fails every signal; the report rates it Avoid'),
}
W_TREND, W_EARN, W_VAL, W_DUR, ASM_PENALTY = 1.0, 1.2, 0.9, 0.9, 0.6


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def cohort_score(value, pool):
    """5 = cheapest in this cohort, 1 = dearest. Missing value -> neutral 3."""
    if value is None or not pool:
        return 3.0
    rank = sum(1 for p in pool if p < value) / max(len(pool) - 1, 1)
    return round(5 - 4 * rank, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--top', type=int, default=0, help='show only the top N')
    a = ap.parse_args()

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
    for code in HOLDINGS:
        row = next((x for x in universe if (x.get('code') or '').upper() == code), None)
        if not row:
            continue
        spell = (hist['stocks'].get(code) or {}).get('calc') or {}
        eq, dur, why = JUDGEMENT.get(code, (3, 3, ''))
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

    print(f'Rated against scan {cur.get("date")}   |   weights: trend {W_TREND} · '
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
          'editable in JUDGEMENT at the top of this file.')


if __name__ == '__main__':
    main()
