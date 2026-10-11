#!/usr/bin/env python3
"""
build_indices.py — custom sub-sector indices (CDMO, Pipes, AI data centre, ...) tracked day by day
from the scans already in the repo.

    python tools/build_indices.py                        # rebuild data/indices/series.json
    python tools/build_indices.py find laurus "divi"     # resolve names -> NSE codes from the newest scan
    python tools/build_indices.py add --id cnc --name "CNC Machines" --group "Capital goods" \\
                                      --codes JYOTICNC,MACPOWER,LMW [--note "..."]

Constituents live in data/indices/indices.json (edit by hand, or with `add`, which also rebuilds).
Each index is EQUAL-WEIGHT and CHAIN-LINKED: between two consecutive scans it moves by the average
of its members' price changes (a member without a price on either day sits that step out), starting
at 100 on the first scan where at least half the members have a price. One step is capped at +/-20%
per trading day in the gap, so a bonus or split in screener's price column cannot print a fake
crash. The whole coded universe is chain-linked the same way as the "market" line, so index /
market is the sub-sector's own relative-strength line.

Per index: levels aligned to the scan dates, returns over 1 day / 1 week / 1 month / 3 months /
since start, the same relative to the market, distance from the index high, breadth (members above
their 50-DMA), median RS, and every member's price, returns, RS rating, rank and trend label from
the newest scan. No network, no LLM.
"""
import argparse, datetime as dt, glob, io, json, os, re, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import num, rate, trend_label, utf8_stdio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(ROOT, 'data', 'scans')
CFG = os.path.join(ROOT, 'data', 'indices', 'indices.json')
OUT = os.path.join(ROOT, 'data', 'indices', 'series.json')
CAP_PER_DAY = 0.20
MIN_NAMES = 1000        # a scan counts as an index date only when it is a broad-market pull


def jload(p):
    with io.open(p, encoding='utf-8') as fh: return json.load(fh)


def jsave(p, doc, pretty):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2 if pretty else None, separators=None if pretty else (',', ':')); fh.write('\n')


def scan_files():
    out = []
    for p in glob.glob(os.path.join(SCANS, '*.json')):
        m = re.search(r'(\d{4}-\d{2}-\d{2})\.json$', p)
        if m: out.append((m.group(1), p))
    return sorted(out)


def key(r):
    return str(r.get('code') or '').strip().upper()


def trading_days(a, b):
    return max(1, round((dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days * 5 / 7))


def chain(dates, prices, codes):
    """Equal-weight chain-linked levels for `codes`, aligned to `dates`; None before the index starts."""
    levels, level = [], None
    for i, d in enumerate(dates):
        pm = prices[d]
        if level is None:
            if sum(1 for c in codes if c in pm) >= max(1, len(codes) / 2): level = 100.0; levels.append(100.0)
            else: levels.append(None)
            continue
        prev, cap = prices[dates[i - 1]], CAP_PER_DAY * trading_days(dates[i - 1], d)
        rets = [max(-cap, min(cap, pm[c] / prev[c] - 1)) for c in codes if c in pm and c in prev and prev[c] > 0]
        if rets: level *= 1 + sum(rets) / len(rets)
        levels.append(round(level, 3))
    return levels


def ret_over(levels, dates, days):
    """% change over `days` calendar days to the newest level, from the newest level `days` to `days`+4 days back
    (the RS tracker's tolerance window). None when no scan sits in that window, so a gap in the archive shows as
    "—" instead of a longer span under the same label. days=0 is the 1-day move: None when the previous scan is
    more than 4 days older (4 covers Fri->Mon and a one-day holiday)."""
    j = max((i for i, v in enumerate(levels) if v is not None), default=None)
    if j is None: return None
    end = dt.date.fromisoformat(dates[j])
    if days == 0:
        if j > 0 and levels[j - 1] and (end - dt.date.fromisoformat(dates[j - 1])).days <= 4:
            return (levels[j] / levels[j - 1] - 1) * 100
        return None
    base = [i for i in range(j) if levels[i] is not None and days <= (end - dt.date.fromisoformat(dates[i])).days <= days + 4]
    return (levels[j] / levels[base[-1]] - 1) * 100 if base else None


def rel(ri, rm):
    return None if ri is None or rm is None else ((1 + ri / 100) / (1 + rm / 100) - 1) * 100


def trend(r, prev_d200):
    price, d50, d200 = num(r.get('price')), num(r.get('dma50')), num(r.get('dma200'))
    c = []
    if price is not None and d50 is not None: c.append(price > d50)
    if price is not None and d200 is not None: c.append(price > d200)
    if d50 is not None and d200 is not None: c.append(d50 > d200)
    if num(r.get('up_52wl')) is not None: c.append(num(r['up_52wl']) >= 30)
    if num(r.get('from_52wh')) is not None: c.append(num(r['from_52wh']) >= -25)
    if r.get('_rs') is not None: c.append(r['_rs'] >= 70)
    if prev_d200 is not None and d200 is not None: c.append(d200 > prev_d200)
    return {'pass': sum(c), 'total': len(c), 'label': trend_label(sum(c), len(c)) if c else None}


def build(quiet):
    cfg = jload(CFG)
    files = scan_files()
    if not files: sys.exit('no dated scan in data/scans/')
    dates, prices, universes = [], {}, {}
    for d, p in files:
        try: rows = jload(p).get('universe', [])
        except (ValueError, OSError) as e:                     # a truncated scan: say so loudly, never drop a day silently
            print(f'WARNING: skipping unreadable scan {os.path.basename(p)}: {type(e).__name__}: {e}', file=sys.stderr); continue
        pm = {key(r): num(r.get('price')) for r in rows if key(r) and num(r.get('price'))}
        if len(pm) < MIN_NAMES: continue                       # the narrow seed exports are not a market day
        dates.append(d); prices[d] = pm; universes[d] = rows
    newest = universes[dates[-1]]
    rate(newest)
    coded = sorted((r for r in newest if key(r) and r.get('_rs') is not None), key=lambda r: -r['_m'])
    rank = {key(r): i for i, r in enumerate(coded, 1)}
    by = {key(r): r for r in newest if key(r)}
    prev_d200 = {key(r): num(r.get('dma200')) for r in universes[dates[-2]]} if len(dates) > 1 else {}
    market = chain(dates, prices, list(prices[dates[-1]].keys()))
    mret = {k: ret_over(market, dates, n) for k, n in (('d1', 0), ('w1', 7), ('m1', 30), ('m3', 91))}

    out = []
    for ix in cfg.get('indices', []):
        codes = [str(c).strip().upper() for c in ix.get('codes', [])]
        missing = [c for c in codes if c not in by]
        levels = chain(dates, prices, codes)
        have = [(i, v) for i, v in enumerate(levels) if v is not None]
        last = have[-1][1] if have else None
        hi = max(have, key=lambda t: t[1]) if have else None
        r = {k: ret_over(levels, dates, n) for k, n in (('d1', 0), ('w1', 7), ('m1', 30), ('m3', 91))}
        r['all'] = (last / have[0][1] - 1) * 100 if have else None
        members = []
        for c in codes:
            m = by.get(c)
            if not m: continue
            members.append({'code': c, 'name': m.get('name'), 'industry': m.get('industry'), 'price': m.get('price'), 'mcap': m.get('mcap'),
                            'r1d': m.get('r1d'), 'r1w': m.get('r1w'), 'r1m': m.get('r1m'), 'r3m': m.get('r3m'), 'from_52wh': m.get('from_52wh'),
                            'dma50': m.get('dma50'), 'rs': m.get('_rs'), 'rank': rank.get(c), 'trend': trend(m, prev_d200.get(c))})
        above50 = [m for m in members if num(m['price']) is not None and num(m['dma50']) is not None]
        rss = [m['rs'] for m in members if m['rs'] is not None]
        out.append({'id': ix['id'], 'name': ix['name'], 'group': ix.get('group'), 'note': ix.get('note'), 'codes': codes, 'missing': missing,
                    'levels': levels, 'last': last, 'start': dates[have[0][0]] if have else None,
                    'high': {'level': hi[1], 'date': dates[hi[0]]} if hi else None, 'from_high': (last / hi[1] - 1) * 100 if hi else None,
                    'ret': r, 'rel': {k: rel(r[k], mret[k]) for k in ('d1', 'w1', 'm1', 'm3')},
                    'breadth': {'above50': sum(1 for m in above50 if m['price'] > m['dma50']), 'of': len(above50)},
                    'med_rs': round(statistics.median(rss)) if rss else None, 'members': members})
        if missing and not quiet: print(f"  {ix['id']}: not in the newest scan — {', '.join(missing)}")

    doc = {'schema': 'swingedge-index-series/1', 'updated': dt.date.today().isoformat(), 'scan_date': dates[-1], 'dates': dates,
           'note': 'Equal-weight, chain-linked sub-sector indices from data/scans/. Built by tools/build_indices.py; constituents in data/indices/indices.json.',
           'market': {'levels': market, 'ret': mret, 'names': len(prices[dates[-1]])}, 'indices': out}
    jsave(OUT, doc, pretty=False)
    if quiet: return
    f = lambda v: '    —' if v is None else f'{v:+6.1f}%'
    print(f"{len(dates)} scans {dates[0]} → {dates[-1]} · market 1W {f(mret['w1'])} 1M {f(mret['m1'])}")
    print(f"{'index':<24}{'level':>8}{'1D':>8}{'1W':>8}{'1M':>8}{'3M':>8}  {'rel 1M':>8}{'off high':>10}{'breadth':>9}{'medRS':>7}")
    for x in out:
        print(f"{x['name']:<24}{(x['last'] or 0):>8.1f}{f(x['ret']['d1']):>8}{f(x['ret']['w1']):>8}{f(x['ret']['m1']):>8}{f(x['ret']['m3']):>8}  {f(x['rel']['m1']):>8}{f(x['from_high']):>10}{x['breadth']['above50']:>5}/{x['breadth']['of']:<3}{str(x['med_rs']):>7}")


def find(patterns):
    files = scan_files()
    rows = jload(files[-1][1]).get('universe', [])
    rate(rows)
    for p in patterns:
        pl = p.lower()
        hits = [r for r in rows if key(r) and (pl in str(r.get('name', '')).lower() or pl == key(r).lower())]
        print(f"{p}:" + ('' if hits else ' (not in scan)'))
        for r in hits: print(f"   {key(r):<12}{str(r.get('name'))[:28]:<29}{str(r.get('industry'))[:30]:<31}mcap {r.get('mcap')!s:>9}  RS {r.get('_rs')}")


def add(a):
    cfg = jload(CFG) if os.path.exists(CFG) else {'schema': 'swingedge-indices/1', 'indices': []}
    codes = [c.strip().upper() for c in a.codes.split(',') if c.strip()]
    known = {key(r) for r in jload(scan_files()[-1][1]).get('universe', []) if key(r)}
    unknown = [c for c in codes if c not in known]
    if unknown: print(f"warning: not in the newest scan (kept anyway): {', '.join(unknown)}")
    entry = {'id': a.id, 'name': a.name, 'group': a.group, 'note': a.note, 'codes': codes}
    cfg['indices'] = [x for x in cfg.get('indices', []) if x.get('id') != a.id] + [entry]
    jsave(CFG, cfg, pretty=True)
    print(f"{a.id}: {len(codes)} names saved to data/indices/indices.json")
    build(quiet=a.quiet)


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--quiet', action='store_true')
    sub = ap.add_subparsers(dest='cmd')
    f = sub.add_parser('find'); f.add_argument('patterns', nargs='+')
    s = sub.add_parser('add'); s.add_argument('--id', required=True); s.add_argument('--name', required=True); s.add_argument('--codes', required=True)
    s.add_argument('--group', default='Custom'); s.add_argument('--note', default='')
    a = ap.parse_args()
    if a.cmd == 'find': find(a.patterns)
    elif a.cmd == 'add': add(a)
    else: build(a.quiet)


if __name__ == '__main__':
    main()
