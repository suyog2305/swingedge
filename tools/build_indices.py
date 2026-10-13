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
at 100 on the first scan where at least half the members have a price. Screener's price column is
not adjusted for splits and bonuses, so a member's step is read in this order (step_return):
  1. One session apart (no weekday between the two scans) and screener's own split-adjusted 1-day
     return (r1d) disagrees with the price ratio by more than 5 points: use r1d.
  2. Otherwise, when the share count (`float`) changed by more than 5%, divide the change out of the
     price ratio - but only when the market cap confirms it (adjusted move within 3 points of the
     mcap move) and the adjustment shrinks the move. Free-float reshuffles and new issues (QIP,
     merger) also move `float`; they fail that check and keep the plain price ratio.
  3. Last resort: a step still outside x0.55 .. x1.8 is an unexplained jump, so that member sits
     the step out. Nothing is clipped: a clipped split is a fake move of its own.
The whole coded universe is chain-linked the same way as the "market" line, so index / market is
the sub-sector's own relative-strength line.

BEFORE THE FIRST BROAD SCAN the series come from the NSE price store (data/prices/, tools/fetch_bhav.py): every
tracker is chain-linked day by day on NSE closes (a member's step = close / its close in the previous store file,
members present on both days; a step beyond +-25% is a split or a bonus - the store is not adjusted - and that member
sits it out). It starts at 100 on the first store date where at least half the members have a close. The market line
there is the equal-weight basket of the first broad scan's coded universe, from the store the same way. From the first
broad scan on, the scan-to-scan logic above continues unchanged, chain-linked onto the backfilled level on that date.
--no-store reproduces the scan-only series (base 100 on the first broad scan).

Per index: levels aligned to the scan dates, returns over 1 day / 1 week / 1 month / 3 months /
since start, the same relative to the market, distance from the index high, breadth (members above
their 50-day EMA - screener's "DMA 50" is an exponential average), median RS, and every member's
price, returns, RS rating, rank and trend label from the newest scan. No network, no LLM.
"""
import argparse, datetime as dt, glob, io, json, math, os, re, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import num, rate, trend_label, utf8_stdio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(ROOT, 'data', 'scans')
CFG = os.path.join(ROOT, 'data', 'indices', 'indices.json')
OUT = os.path.join(ROOT, 'data', 'indices', 'series.json')
R1D_TOL = 5.0           # points: r1d vs the price ratio before a one-session step is read off r1d
FLOAT_TOL = 0.05        # share-count change that counts as a possible split / bonus
MCAP_TOL = 0.03         # the float-adjusted move must sit this close to the market-cap move
STEP_BAND = (0.55, 1.8) # last resort: a step ratio outside this is skipped for that member, never clipped
MIN_NAMES = 1000        # a scan counts as an index date only when it is a broad-market pull
STORE = os.path.join(ROOT, 'data', 'prices')
STORE_JUMP = 0.25       # store step: |close / previous close - 1| beyond this is a split or bonus, sat out
RETS = (('d1', 0), ('w1', 7), ('m1', 30), ('m3', 91), ('m6', 182), ('y1', 365))


def jload(p):
    with io.open(p, encoding='utf-8') as fh: return json.load(fh)


def jsave(p, doc, pretty):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'                                   # atomic: a kill mid-write must not leave a truncated file
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2 if pretty else None, separators=None if pretty else (',', ':')); fh.write('\n')
    os.replace(tmp, p)


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


def one_session(a, b):
    """True when no weekday falls strictly between the two scan dates (Fri->Mon counts; a skipped day does not)."""
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return all((d0 + dt.timedelta(days=k)).weekday() >= 5 for k in range(1, (d1 - d0).days))


def step_return(p0, p1, x0, x1, single):
    """One member's return between two consecutive scans, or None to sit the step out (see the docstring).
    x0 / x1 are (r1d, float, mcap) from the older / newer scan."""
    raw = p1 / p0 - 1
    r1d = x1[0] if x1 else None
    if single and r1d is not None and abs(raw * 100 - r1d) > R1D_TOL:
        r = r1d / 100
    else:
        r = raw
        f0, f1 = (x0[1], x1[1]) if x0 and x1 else (None, None)
        m0, m1 = (x0[2], x1[2]) if x0 and x1 else (None, None)
        if f0 and f1 and m0 and m1 and abs(f1 / f0 - 1) > FLOAT_TOL:
            adj = p1 / (p0 * f0 / f1) - 1
            if abs(adj - (m1 / m0 - 1)) <= MCAP_TOL and abs(adj) < abs(raw): r = adj
    return r if STEP_BAND[0] <= 1 + r <= STEP_BAND[1] else None


def chain(dates, prices, codes, facts=None, start=None):
    """Equal-weight chain-linked levels for `codes`, aligned to `dates`; None before the index starts.
    `facts` = {date: {code: (r1d, float, mcap)}} drives the split / bonus handling in step_return.
    `start` = a level carried in from the price-store backfill: the index is already running on dates[0]."""
    facts = facts or {}
    levels, level = [], None
    for i, d in enumerate(dates):
        pm = prices[d]
        if i == 0 and start is not None:
            level = float(start); levels.append(round(level, 3)); continue
        if level is None:
            if sum(1 for c in codes if c in pm) >= max(1, len(codes) / 2): level = 100.0; levels.append(100.0)
            else: levels.append(None)
            continue
        prev, fp, fn = prices[dates[i - 1]], facts.get(dates[i - 1], {}), facts.get(d, {})
        single = one_session(dates[i - 1], d)
        rets = [step_return(prev[c], pm[c], fp.get(c), fn.get(c), single) for c in codes if c in pm and c in prev and prev[c] > 0]
        rets = [r for r in rets if r is not None]
        if rets: level *= 1 + sum(rets) / len(rets)
        levels.append(round(level, 3))
    return levels


def load_store(upto):
    """{date: {symbol: close}} for every price-store file dated on or before `upto` (empty without a store)."""
    out = {}
    for p in sorted(glob.glob(os.path.join(STORE, '20*.json'))):
        m = re.search(r'(\d{4}-\d{2}-\d{2})\.json$', p)
        if not m or m.group(1) > upto: continue
        try:
            doc = jload(p)
            ci = (doc.get('cols') or ['close']).index('close')
            pm = {str(k).upper(): v[ci] for k, v in (doc.get('rows') or {}).items() if isinstance(v[ci], (int, float)) and v[ci] > 0}
        except Exception as e:                             # one bad file is skipped, loudly; never the whole build
            print(f'WARNING: skipping unreadable price file {os.path.basename(p)}: {type(e).__name__}: {e}', file=sys.stderr); continue
        if pm: out[m.group(1)] = pm
    return out


def chain_store(dates, closes, codes):
    """The backfill: equal-weight levels chained on store closes, one step per consecutive store date; members
    present on both days; a step beyond STORE_JUMP is sat out. Starts at 100 when half the members have a close."""
    levels, level = [], None
    for i, d in enumerate(dates):
        pm = closes[d]
        if level is None:
            if sum(1 for c in codes if c in pm) >= max(1, len(codes) / 2): level = 100.0; levels.append(100.0)
            else: levels.append(None)
            continue
        prev = closes[dates[i - 1]]
        rets = [pm[c] / prev[c] - 1 for c in codes if c in pm and c in prev]
        rets = [r for r in rets if abs(r) <= STORE_JUMP]
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


def build(quiet, use_store=True):
    cfg = jload(CFG)
    files = scan_files()
    if not files: sys.exit('no dated scan in data/scans/')
    dates, prices, universes, facts = [], {}, {}, {}
    for d, p in files:
        try: rows = jload(p).get('universe', [])
        except (ValueError, OSError) as e:                     # a truncated scan: say so loudly, never drop a day silently
            print(f'WARNING: skipping unreadable scan {os.path.basename(p)}: {type(e).__name__}: {e}', file=sys.stderr); continue
        pm = {key(r): num(r.get('price')) for r in rows if key(r) and num(r.get('price'))}
        if len(pm) < MIN_NAMES: continue                       # the narrow seed exports are not a market day
        dates.append(d); prices[d] = pm; universes[d] = rows
        facts[d] = {key(r): (num(r.get('r1d')), num(r.get('float')), num(r.get('mcap'))) for r in rows if key(r) in pm}
    newest = universes[dates[-1]]
    rate(newest)
    coded = sorted((r for r in newest if key(r) and r.get('_rs') is not None), key=lambda r: -r['_m'])
    rank = {key(r): i for i, r in enumerate(coded, 1)}
    by = {key(r): r for r in newest if key(r)}
    prev_d200 = {key(r): num(r.get('dma200')) for r in universes[dates[-2]]} if len(dates) > 1 else {}
    # The price-store backfill: store dates before the first broad scan, then the first scan's own date (the join).
    d0 = dates[0]
    try:
        closes = load_store(d0) if use_store else {}
    except Exception as e:                                 # the store must never stop the evening pull: scan-only instead
        print(f'WARNING: price store not read ({type(e).__name__}: {e}); building from the scans only', file=sys.stderr)
        closes = {}
    pre = [d for d in sorted(closes) if d < d0]
    if pre:
        # The join step lands on the scan's OWN prices, not the store's file for that date: the scan chain carries on
        # from those prices, so whatever session they really are, no move is lost or counted twice at the join.
        # (Checked 13 Oct 2026: the 2026-08-21 scan carries NSE's 24 Aug close on every name; on a correctly dated
        # scan the two are identical.)
        closes[d0] = prices[d0]
        pre_dates = pre + [d0]
    else:
        pre_dates = []

    def linked(codes, base_codes=None):
        """Store-backfilled levels up to d0, then the scan chain carried on from the level at d0."""
        if not pre_dates:
            return chain(dates, prices, codes, facts)
        lp = chain_store(pre_dates, closes, base_codes or codes)
        return lp[:-1] + chain(dates, prices, codes, facts, start=lp[-1])

    all_dates = pre_dates[:-1] + dates
    market = linked(list(prices[dates[-1]].keys()), list(prices[d0].keys()))
    mret = {k: ret_over(market, all_dates, n) for k, n in RETS}

    out = []
    for ix in cfg.get('indices', []):
        codes = [str(c).strip().upper() for c in ix.get('codes', [])]
        missing = [c for c in codes if c not in by]
        levels = linked(codes)
        have = [(i, v) for i, v in enumerate(levels) if v is not None]
        last = have[-1][1] if have else None
        hi = max(have, key=lambda t: t[1]) if have else None
        r = {k: ret_over(levels, all_dates, n) for k, n in RETS}
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
                    'levels': levels, 'last': last, 'start': all_dates[have[0][0]] if have else None,
                    'high': {'level': hi[1], 'date': all_dates[hi[0]]} if hi else None, 'from_high': (last / hi[1] - 1) * 100 if hi else None,
                    'ret': r, 'rel': {k: rel(r[k], mret[k]) for k, _ in RETS},
                    'breadth': {'above50': sum(1 for m in above50 if m['price'] > m['dma50']), 'of': len(above50)},
                    'med_rs': math.floor(statistics.median(rss) + 0.5) if rss else None, 'members': members})
        if missing and not quiet: print(f"  {ix['id']}: not in the newest scan — {', '.join(missing)}")

    note = ('Equal-weight, chain-linked sub-sector indices from data/scans/. Built by tools/build_indices.py; constituents in data/indices/indices.json.'
            + (f' Before the first broad scan ({d0}) chained daily on NSE closes from data/prices/; base 100 on {all_dates[0]}.' if pre_dates else ''))
    doc = {'schema': 'swingedge-index-series/1', 'updated': dt.date.today().isoformat(), 'scan_date': dates[-1], 'dates': all_dates,
           'note': note, 'first_scan': d0, 'store_days': len(pre_dates) - 1 if pre_dates else 0,
           'market': {'levels': market, 'ret': mret, 'names': len(prices[dates[-1]])}, 'indices': out}
    jsave(OUT, doc, pretty=False)
    if quiet: return
    f = lambda v: '    —' if v is None else f'{v:+6.1f}%'
    print(f"{len(dates)} scans {dates[0]} → {dates[-1]}" + (f" + {len(pre_dates) - 1} store days from {all_dates[0]}" if pre_dates else '')
          + f" · market 1W {f(mret['w1'])} 1M {f(mret['m1'])} 6M {f(mret['m6'])} 1Y {f(mret['y1'])}")
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
    build(quiet=a.quiet, use_store=not a.no_store)


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--no-store', action='store_true', help='scan-only series, as before the price store (base 100 on the first broad scan)')
    sub = ap.add_subparsers(dest='cmd')
    f = sub.add_parser('find'); f.add_argument('patterns', nargs='+')
    s = sub.add_parser('add'); s.add_argument('--id', required=True); s.add_argument('--name', required=True); s.add_argument('--codes', required=True)
    s.add_argument('--group', default='Custom'); s.add_argument('--note', default='')
    a = ap.parse_args()
    if a.cmd == 'find': find(a.patterns)
    elif a.cmd == 'add': add(a)
    else: build(a.quiet, use_store=not a.no_store)


if __name__ == '__main__':
    main()
