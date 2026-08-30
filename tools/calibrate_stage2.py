#!/usr/bin/env python3
"""
calibrate_stage2.py — fit our Stage 2 rule to the provider's list, so we can eventually stop
needing the provider's list.

    python tools/calibrate_stage2.py                 # report the fit on every usable scan
    python tools/calibrate_stage2.py --write         # also save the best fit to stage2_params.json
    python tools/calibrate_stage2.py --date 2026-08-27

WHAT STAGE 2 IS

Stan Weinstein's four stages describe where a stock sits in its cycle:

  Stage 1  BASING       — sideways after a decline. The long moving average flattens.
  Stage 2  ADVANCING    — the markup phase. Price breaks out of the base, holds above a RISING
                          long MA, and makes higher highs and higher lows. This is the only
                          stage worth buying, and the whole point of tracking it.
  Stage 3  TOPPING      — the advance stalls, the MA flattens, price whipsaws around it.
  Stage 4  DECLINING    — price below a falling long MA.

A Stage 1 -> 2 transition is therefore three things at once: a BREAKOUT from a base, above a
long MA that has TURNED UP, confirmed by VOLUME. Minervini's trend template is the commonly
coded approximation: price above the 50/150/200-DMAs, 50 > 150 > 200, the 200-DMA rising,
price >= 30% off the 52-week low and within 25% of the high, and relative strength leading.

WHAT WE CAN ACTUALLY TEST, AND WHAT WE CANNOT

The screener export gives price, DMA50, DMA200, distance from the 52-week high and low, and
returns. That supports most of the template. It does NOT give:

  * the 150-DMA / 30-week MA  — Weinstein's actual Stage 2 line
  * volume and average volume — so no breakout confirmation
  * price history              — so no base detection; a snapshot cannot see a consolidation

Those absences are why the fit plateaus. Adding "DMA 150" and a volume column to the screener
query is the single highest-value change available, and this script prints the ceiling so the
trade-off is visible rather than assumed.

HOW THE FIT IS MEASURED

Only names the screen can actually see are scored. A provider name below the market-cap floor
is not a miss - it is out of scope, and the script reports that coverage ceiling separately so
it is never confused with a modelling error.

  precision  of the names WE flag, how many the provider also has  (few false alarms)
  recall     of the provider names we CAN see, how many we catch   (few misses)
  F1         their harmonic mean - the single number being maximised

Grid-searched over the RS floor, distance-from-high, distance-off-low, whether 50>200 is
required, whether the 200-DMA slope is required, and the minimum number of evaluable checks.
"""
import argparse, io, itertools, json, glob, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from build_s2history import rs_percentiles, num                      # noqa: E402

PARAMS = os.path.join(ROOT, 'data', 'daily', 'stage2_params.json')

# the grid. Deliberately coarse - a finer grid on one week of data would be fitting noise.
GRID = dict(
    rs_min=[None, 40, 50, 55, 60, 65, 70, 75, 80],
    wh_max=[20, 25, 30, 40, None],
    low_min=[None, 20, 30, 40],
    need_50gt200=[True, False],
    need_slope=[True, False],
    min_checks=[4, 5],
)
DEFAULTS = dict(rs_min=70, wh_max=25, low_min=30, need_50gt200=True, need_slope=True, min_checks=5)


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def evaluate(row, prev_row, rs_min, wh_max, low_min, need_50gt200, need_slope, min_checks):
    """The parameterised trend template. Returns True only if every evaluable check passes."""
    price, d50, d200 = num(row.get('price')), num(row.get('dma50')), num(row.get('dma200'))
    up52, f52, rs = num(row.get('up_52wl')), num(row.get('from_52wh')), row.get('_rs')
    c = []
    if None not in (price, d50): c.append(price > d50)
    if None not in (price, d200): c.append(price > d200)
    if need_50gt200 and None not in (d50, d200): c.append(d50 > d200)
    if low_min is not None and up52 is not None: c.append(up52 >= low_min)
    if wh_max is not None and f52 is not None: c.append(f52 >= -wh_max)
    if rs_min is not None and rs is not None: c.append(rs >= rs_min)
    if need_slope and prev_row is not None:
        pd200 = num(prev_row.get('dma200'))
        if None not in (pd200, d200): c.append(d200 > pd200)
    return len(c) >= min_checks and all(c)


def usable_scans():
    """Scans carrying BOTH a broad universe and a provider Stage 2 list."""
    out, files = [], sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')))
    for i, p in enumerate(files):
        d = jload(p)
        U = [r for r in d.get('universe', []) if r.get('code')]
        S2 = {str(s.get('code', '')).upper() for s in (d.get('stage2') or []) if s.get('code')}
        if len(U) < 500 or not S2:              # a narrow export cannot calibrate a market-wide rule
            continue
        prev = jload(files[i - 1]) if i else None
        out.append(dict(date=d.get('date'), U=U, S2=S2,
                        prev={r['code'].upper(): r for r in (prev or {}).get('universe', []) if r.get('code')},
                        src=(d.get('sources') or {}).get('stage2')))
    return out


def score(scan, params):
    umap = {r['code'].upper(): r for r in scan['U']}
    scope = set(umap) & scan['S2']              # provider names the screen can actually see
    mine = {c for c in umap if evaluate(umap[c], scan['prev'].get(c), **params)}
    tp = len(mine & scope)
    p = tp / len(mine) if mine else 0.0
    r = tp / len(scope) if scope else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return dict(precision=p, recall=r, f1=f1, flagged=len(mine), scope=len(scope),
                provider=len(scan['S2']), tp=tp)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', help='calibrate on one scan date only')
    ap.add_argument('--write', action='store_true', help='save the best fit to data/daily/stage2_params.json')
    ap.add_argument('--top', type=int, default=8, help='how many grid results to show')
    a = ap.parse_args()

    scans = usable_scans()
    if a.date:
        scans = [s for s in scans if s['date'] == a.date]
    if not scans:
        raise SystemExit('no scan carries both a broad universe (500+ rows) and a provider Stage 2 list')

    for s in scans:
        rs_percentiles(s['U'])

    print(f'Calibrating on {len(scans)} scan(s): ' + ', '.join(s["date"] for s in scans) + '\n')

    # ---- the coverage ceiling, which no amount of tuning can move --------------
    print('COVERAGE CEILING — provider names the screen cannot see at all')
    print(f'  {"date":<12}{"provider":>10}{"visible":>9}{"invisible":>11}{"max recall":>12}')
    for s in scans:
        umap = {r['code'].upper() for r in s['U']}
        vis = len(umap & s['S2']); tot = len(s['S2'])
        print(f'  {s["date"]:<12}{tot:>10}{vis:>9}{tot - vis:>11}{100 * vis / tot:>11.0f}%')
    print('  Those are below the market-cap floor of the screen. Lower --min-mcap to reach them.\n')

    # ---- grid search ----------------------------------------------------------
    keys = list(GRID)
    results = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        params = dict(zip(keys, combo))
        per = [score(s, params) for s in scans]
        f1 = sum(x['f1'] for x in per) / len(per)
        results.append((f1, params, per))
    results.sort(key=lambda x: -x[0])

    base_per = [score(s, DEFAULTS) for s in scans]
    base_f1 = sum(x['f1'] for x in base_per) / len(base_per)

    print(f'GRID SEARCH — {len(results)} combinations, ranked by mean F1')
    print(f'  {"F1":>6}{"prec":>7}{"recall":>8}{"flagged":>9}   parameters')
    for f1, params, per in results[:a.top]:
        p = sum(x['precision'] for x in per) / len(per)
        r = sum(x['recall'] for x in per) / len(per)
        n = sum(x['flagged'] for x in per) // len(per)
        bits = ', '.join(f'{k}={params[k]}' for k in keys)
        print(f'  {100*f1:>6.1f}{100*p:>6.1f}%{100*r:>7.1f}%{n:>9}   {bits}')
    print(f'\n  current default: F1 {100*base_f1:.1f}  '
          f'(prec {100*sum(x["precision"] for x in base_per)/len(base_per):.1f}%, '
          f'recall {100*sum(x["recall"] for x in base_per)/len(base_per):.1f}%)')
    best_f1, best, best_per = results[0]
    print(f'  best found     : F1 {100*best_f1:.1f}  -> +{100*(best_f1-base_f1):.1f} points\n')

    # How many INDEPENDENT provider lists are behind this? Scans that reuse the same weekly
    # file are the same observation counted twice, and treating them as several would
    # flatter the fit badly.
    distinct = {frozenset(s['S2']) for s in scans}
    if len(distinct) < len(scans):
        print(f'  WARNING: {len(scans)} scans but only {len(distinct)} DISTINCT provider list(s).')
        print('  Scans reusing the same weekly file are ONE observation, not several.')
        print('  Files seen: ' + ', '.join(sorted({str(x["src"]) for x in scans})))
    if len(distinct) == 1:
        print('  This is a FIT, not a validation - a rule tuned on a single week will always')
        print('  look better than it is. Do NOT --write until a second, genuinely different')
        print('  provider list has landed; then re-run and compare.')
        print()

    if a.write:
        doc = dict(schema='swingedge-stage2params/1',
                   fitted_on=[s['date'] for s in scans],
                   provider_files=[s['src'] for s in scans],
                   metric=dict(f1=round(best_f1, 4),
                               precision=round(sum(x['precision'] for x in best_per)/len(best_per), 4),
                               recall=round(sum(x['recall'] for x in best_per)/len(best_per), 4)),
                   params=best,
                   note=('Fitted by tools/calibrate_stage2.py against the provider list. '
                         'build_s2history.py reads this if present. Re-fit whenever a new '
                         'provider list arrives; delete the file to fall back to defaults.'))
        os.makedirs(os.path.dirname(PARAMS), exist_ok=True)
        with io.open(PARAMS, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        print(f'wrote {os.path.relpath(PARAMS, ROOT)}')
    else:
        print('  (--write to save these parameters)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
