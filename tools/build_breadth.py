#!/usr/bin/env python3
"""
build_breadth.py — market breadth, day by day, from the scan archive -> data/daily/breadth.json

    python tools/build_breadth.py            # rebuild the whole file from data/scans/
    python tools/build_breadth.py --quiet    # one summary line (what eod.py runs)

Every broad scan (a universe of 1,000+ names) in data/scans/ gives one row, over the names that carry a price:
  n               names with a price
  above50/200     price above the 50 / 200-day average (screener's DMA 50 / DMA 200); pct50 / pct200 in percent
  near_high       within 10% of the 52-week high (from_52wh >= -10); pct_near_high
  adv / dec       up / down on the day (r1d > 0 / < 0)
  med_r1w/r1m     median 1-week / 1-month return, percent
  template        names passing all 7 checks of the trend template - the same trend() as build_rs_tracker.py, RS
                  ranked inside that scan, the 200-DMA slope against the previous broad scan (null for the first
                  scan, which has nothing to compare with)
  stage2_calc     the calculated Stage 2 track's count that day, from data/daily/s2history.json (null when absent)
  regime          "broad uptrend" when pct200 >= 60 and pct50 >= 50; "downtrend" when pct200 < 40; else "mixed"
  pct50_4w_chg    pct50 minus pct50 of the newest broad scan dated on or before 28 days earlier (null when there is
                  none within 14 days of that point)
  pct200_4w_chg, pct_near_high_4w_chg
                  the same change for pct200 / pct_near_high, against the same scan
  ref_4w          the date of that reference scan (null when there is none), so the page labels "(vs date)" from the
                  scan the three changes were actually computed against
It describes the market; it rates nothing. The rebuild is idempotent: the same archive gives the same file
(only `updated` moves). Never blocks the evening pull (tools/eod.py runs it after build_rs_tracker).
STDLIB ONLY; test with `python -s`.
"""
import argparse, datetime as dt, io, json, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import num, rate, utf8_stdio  # noqa: E402
from build_rs_tracker import key, scan_files, scan_rows, trend  # noqa: E402  (one template, one place)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'daily', 'breadth.json')
S2H = os.path.join(ROOT, 'data', 'daily', 's2history.json')
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BROAD = 1000                   # a universe smaller than this is a gainers / Stage-2-only pull, not the market
NEAR_HIGH = -10                # from_52wh at or above this counts as near the 52-week high
LOOKBACK, SLACK = 28, 14       # the *_4w_chg fields: compare with the scan on or before 28 days back, no more than 14 days older


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def regime(p50, p200):
    if p50 is None or p200 is None:
        return None
    if p200 >= 60 and p50 >= 50:
        return 'broad uptrend'
    if p200 < 40:
        return 'downtrend'
    return 'mixed'


def median(xs):
    return round(statistics.median(xs), 2) if xs else None


def s2_calc_counts():
    try:
        with io.open(S2H, encoding='utf-8') as fh:
            doc = json.load(fh)
    except Exception:
        return {}
    out = {}
    for day in doc.get('days') or []:
        c = (day.get('calc') or {}).get('count')
        if day.get('date') and c is not None:
            out[day['date']] = c
    return out


def row_for(rows, prev_rows, s2):
    priced = [r for r in rows if num(r.get('price')) is not None]
    n = len(priced)
    a50 = sum(1 for r in priced if num(r.get('dma50')) is not None and num(r['price']) > num(r['dma50']))
    a200 = sum(1 for r in priced if num(r.get('dma200')) is not None and num(r['price']) > num(r['dma200']))
    nh = sum(1 for r in priced if num(r.get('from_52wh')) is not None and num(r['from_52wh']) >= NEAR_HIGH)
    adv = sum(1 for r in priced if (num(r.get('r1d')) or 0) > 0)
    dec = sum(1 for r in priced if (num(r.get('r1d')) or 0) < 0)
    template = None
    if prev_rows is not None:
        rate(rows)                                          # RS inside this scan, as the page ranks it
        prev = {key(r): r for r in prev_rows if key(r)}
        template = 0
        for r in rows:
            if not key(r):
                continue
            p, t = trend(r, prev.get(key(r)))
            if t == 7 and p == 7:
                template += 1
    p50, p200 = pct(a50, n), pct(a200, n)
    return {'n': n, 'above50': a50, 'above200': a200, 'pct50': p50, 'pct200': p200,
            'near_high': nh, 'pct_near_high': pct(nh, n), 'adv': adv, 'dec': dec,
            'med_r1w': median([num(r['r1w']) for r in priced if num(r.get('r1w')) is not None]),
            'med_r1m': median([num(r['r1m']) for r in priced if num(r.get('r1m')) is not None]),
            'template': template, 'stage2_calc': s2, 'regime': regime(p50, p200), 'pct50_4w_chg': None,
            'pct200_4w_chg': None, 'pct_near_high_4w_chg': None, 'ref_4w': None}


def build():
    s2 = s2_calc_counts()
    out, prev_rows, skipped = {}, None, []
    for date, path in scan_files():
        rows = scan_rows(path)
        if rows is None:
            skipped.append(f'{date} (unreadable)')
            continue
        if len(rows) < BROAD:
            continue
        out[date] = row_for(rows, prev_rows, s2.get(date))
        prev_rows = rows
    dates = sorted(out)
    for d in dates:
        target = dt.date.fromisoformat(d) - dt.timedelta(days=LOOKBACK)
        older = [x for x in dates if dt.date.fromisoformat(x) <= target]
        if older and (target - dt.date.fromisoformat(older[-1])).days <= SLACK:
            ref = older[-1]
            out[d]['ref_4w'] = ref
            for f in ('pct50', 'pct200', 'pct_near_high'):
                a, b = out[d][f], out[ref][f]
                out[d][f + '_4w_chg'] = round(a - b, 1) if a is not None and b is not None else None
    return dates, out, skipped


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    dates, rows, skipped = build()
    for s in skipped:
        print(f'    skipped {s}', file=sys.stderr)
    if not dates:
        print('build_breadth: no broad scan in data/scans/ - nothing written')
        return 1
    doc = {'schema': 1, 'updated': dt.datetime.now(IST).strftime('%Y-%m-%dT%H:%M'), 'dates': dates,
           'rows': {d: rows[d] for d in dates}, 'latest': dates[-1]}
    tmp = OUT + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))
        fh.write('\n')
    os.replace(tmp, OUT)
    L = rows[dates[-1]]
    print(f'build_breadth: {len(dates)} sessions, latest {dates[-1]}: {L["regime"]}; {L["pct50"]}% above the 50-DMA, '
          f'{L["pct200"]}% above the 200-DMA, {L["pct_near_high"]}% near the high, template {L["template"]}, '
          f'Stage 2 calc {L["stage2_calc"]}')
    if not a.quiet:
        for d in dates:
            r = rows[d]
            print(f'  {d}  n {r["n"]:>5}  50d {r["pct50"]:>5}  200d {r["pct200"]:>5}  hi {r["pct_near_high"]:>5}  '
                  f'A/D {r["adv"]}/{r["dec"]}  tpl {r["template"]}  s2 {r["stage2_calc"]}  {r["regime"]}  4w {r["pct50_4w_chg"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
