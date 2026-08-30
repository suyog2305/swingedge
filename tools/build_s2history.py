#!/usr/bin/env python3
"""
build_s2history.py — day-by-day Stage 2 memory, built from the scan archive.

    python tools/build_s2history.py

Walks every dated scan in data/scans/ (oldest to newest) and reconstructs, for each day:

  * who was in Stage 2 that day,
  * who ENTERED that day, and who EXITED,

on two independent tracks:

  LIST  — your provider's Stage 2 file (whatever the scan carried). Weekly, so it only
          changes on the days you upload a new one.
  CALC  — Stage 2 computed here from the 7-point trend template on that day's screener
          data. Available EVERY scan, so this is what gives you daily tracking without
          any new data source.

and per stock, its whole life in Stage 2: every spell (entered -> exited), how many days
each lasted, whether it is in right now, and how long the current run has been.

No database needed: the scan archive in git already is the time series — each dated scan
is an immutable snapshot, so this can always be rebuilt from scratch and will never drift.

Writes data/daily/s2history.json (what the app reads).
"""
import argparse, datetime as dt, io, json, os, glob
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = 'swingedge-s2history/1'

def jload(p):
    with io.open(p, encoding='utf-8') as fh: return json.load(fh)

def num(v):
    try:
        f = float(v); return None if f != f else f
    except (TypeError, ValueError): return None

def days_between(a, b):
    try: return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except Exception: return None

BANDS = [('Large', 50000), ('Mid', 15000), ('Small', 3000), ('Micro', 0)]
def band_of(m):
    if m is None: return None
    for n, lo in BANDS:
        if m >= lo: return n
    return 'Micro'

def rs_percentiles(rows):
    """RS 1-99 within this scan (same composite the app uses)."""
    def mom(r):
        r3, r6, r1y = num(r.get('r3m')), num(r.get('r6m')), num(r.get('r1y'))
        if None not in (r3, r6, r1y):
            q2 = ((1 + r6 / 100) / (1 + r3 / 100) - 1) * 100
            h2 = ((1 + r1y / 100) / (1 + r6 / 100) - 1) * 100
            return 0.4 * r3 + 0.2 * q2 + 0.2 * h2
        s = w = 0.0
        for k, wt in (('r3m', .5), ('r1m', .3), ('r1w', .2)):
            v = num(r.get(k))
            if v is not None: s += wt * v; w += wt
        return s / w if w else None
    for r in rows: r['_m'] = mom(r)
    rk = sorted((r for r in rows if r['_m'] is not None), key=lambda r: r['_m'])
    n = len(rk); i = 0
    while i < n:
        j = i
        while j + 1 < n and rk[j + 1]['_m'] == rk[i]['_m']: j += 1
        pct = 99 if n == 1 else round(1 + 98 * (((i + j) / 2 + 1) - 1) / (n - 1))
        for k in range(i, j + 1): rk[k]['_rs'] = pct
        i = j + 1

def trend_pass(r, prev_row):
    """The app's 7-point trend template. Needs at least 5 evaluable checks to count."""
    price, d50, d200 = num(r.get('price')), num(r.get('dma50')), num(r.get('dma200'))
    up52, f52, rs = num(r.get('up_52wl')), num(r.get('from_52wh')), r.get('_rs')
    c = []
    if price is not None and d50 is not None: c.append(price > d50)
    if price is not None and d200 is not None: c.append(price > d200)
    if d50 is not None and d200 is not None: c.append(d50 > d200)
    if up52 is not None: c.append(up52 >= 30)
    if f52 is not None: c.append(f52 >= -25)
    if rs is not None: c.append(rs >= 70)
    if prev_row is not None:
        pd200 = num(prev_row.get('dma200'))
        if pd200 is not None and d200 is not None: c.append(d200 > pd200)
    return len(c) >= 5 and all(c)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')))
    if not files: raise SystemExit('no scans in data/scans')

    days, meta, list_updates = [], {}, []     # meta: code -> latest known name/sector/band
    tracks = {'list': {}, 'calc': {}}        # track -> code -> {spells:[...], in:bool}
    prev_members = {'list': None, 'calc': None}
    prev_universe = None

    for p in files:
        d = jload(p)
        date = d.get('date') or os.path.basename(p)[:-5]
        U = [r for r in d.get('universe', []) if r.get('code')]
        S2 = d.get('stage2', []) or []
        s2_src = (d.get('sources') or {}).get('stage2')
        rs_percentiles(U)
        umap = {r['code'].upper(): r for r in U}

        for r in U:
            c = r['code'].upper()
            meta[c] = OrderedDict(name=r.get('name') or c,
                                  sector=r.get('group') or r.get('industry') or '',
                                  band=band_of(num(r.get('mcap'))))
        members = {}
        members['list'] = {str(s.get('code', '')).upper() for s in S2 if s.get('code')}
        status_map = {str(s.get('code', '')).upper(): s.get('status') for s in S2
                      if s.get('code') and s.get('status')}
        for s in S2:
            c = str(s.get('code', '')).upper()
            if c and c not in meta:
                meta[c] = OrderedDict(name=c, sector=s.get('industry') or '', band=None)
        members['calc'] = {r['code'].upper() for r in U
                           if trend_pass(r, (prev_universe or {}).get(r['code'].upper()))} if U else set()

        day = OrderedDict(date=date)
        for tk in ('list', 'calc'):
            cur, prev = members[tk], prev_members[tk]
            # a track with no data on this scan is "unknown", not "everyone exited"
            if not cur and prev:
                day[tk] = OrderedDict(count=0, entered=[], exited=[], unknown=True)
                continue
            # the provider's list is weekly: an identical code set means no new list was
            # uploaded for this scan, which is not the same as "nothing changed"
            if tk == 'list' and prev is not None and cur == prev:
                day[tk] = OrderedDict(count=len(cur), entered=[], exited=[], carried=True,
                                      source=s2_src)
                continue
            # a fresh provider list: trust its own status stamps over a set difference,
            # because export scope varies week to week and a diff cannot tell a genuine
            # new entry from a name that was merely missing last time
            if tk == 'list' and prev is not None and status_map:
                entered = sorted(c for c, st in status_map.items()
                                 if st in ('New Addition', 'Reentry'))
                shrunk = len(cur) < len(prev) * 0.9
                exited = [] if shrunk else sorted(prev - cur)
                for c in entered:
                    stt = tracks[tk].setdefault(c, {'spells': []})
                    if not (stt['spells'] and stt['spells'][-1]['to'] is None):
                        stt['spells'].append(OrderedDict([('from', date), ('to', None), ('days', None)]))
                for c in exited:
                    stt = tracks[tk].get(c)
                    if stt and stt['spells'] and stt['spells'][-1]['to'] is None:
                        sp = stt['spells'][-1]; sp['to'] = date; sp['days'] = days_between(sp['from'], date)
                day[tk] = OrderedDict(count=len(cur), entered=entered, exited=exited,
                                      source=s2_src, by_status=True)
                if shrunk:
                    day[tk]['exits_unreliable'] = True   # narrower export, absence != exit
                list_updates.append(date)
                prev_members[tk] = cur
                continue
            entered = sorted(cur - prev) if prev is not None else sorted(cur)
            exited = sorted(prev - cur) if prev is not None else []
            for c in entered:
                st = tracks[tk].setdefault(c, {'spells': []})
                st['spells'].append(OrderedDict([('from', date), ('to', None), ('days', None)]))
            for c in exited:
                st = tracks[tk].get(c)
                if st and st['spells'] and st['spells'][-1]['to'] is None:
                    sp = st['spells'][-1]; sp['to'] = date; sp['days'] = days_between(sp['from'], date)
            day[tk] = OrderedDict(count=len(cur), entered=entered, exited=exited,
                                  first=prev is None)
            if tk == 'list':
                day[tk]['source'] = s2_src            # the file this week's list came from
                if cur: list_updates.append(date)     # a genuinely new list landed today
            prev_members[tk] = cur
        days.append(day)
        if U: prev_universe = umap

    last_date = days[-1]['date']
    stocks = OrderedDict()
    for tk in ('list', 'calc'):
        for c, st in tracks[tk].items():
            e = stocks.setdefault(c, OrderedDict(code=c))
            e.update(meta.get(c, {}))
            open_spell = st['spells'][-1] if st['spells'] and st['spells'][-1]['to'] is None else None
            closed = [s for s in st['spells'] if s['days'] is not None]
            e[tk] = OrderedDict(
                current=bool(open_spell),
                since=open_spell['from'] if open_spell else None,
                days=days_between(open_spell['from'], last_date) if open_spell else None,
                spells=len(st['spells']),
                total_days=sum(s['days'] or 0 for s in closed) + (days_between(open_spell['from'], last_date) or 0 if open_spell else 0),
                history=st['spells'][-6:])

    doc = OrderedDict(schema=SCHEMA, generated=dt.date.today().isoformat(), last_scan=last_date,
                      scans=[x['date'] for x in days], list_updates=list_updates,
                      days=days, stocks=stocks)
    outdir = os.path.join(ROOT, 'data', 'daily'); os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, 's2history.json')
    with io.open(out, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))

    if not a.quiet:
        print(f'Stage 2 history across {len(days)} scans  ->  data/daily/s2history.json ({os.path.getsize(out):,} bytes)\n')
        print(f'{"DATE":<12} {"LIST":>6} {"IN":>4} {"OUT":>4}   {"CALC":>6} {"IN":>4} {"OUT":>4}')
        print('-' * 56)
        for x in days:
            l, c = x.get('list', {}), x.get('calc', {})
            lf = 'n/a' if l.get('unknown') else str(l.get('count', 0))
            carried = l.get('carried')
            cf = 'n/a' if c.get('unknown') else str(c.get('count', 0))
            li, lo = ('  —', '  —') if carried else (f'{len(l.get("entered", [])):>4}', f'{len(l.get("exited", [])):>4}')
            print(f'{x["date"]:<12} {lf:>6} {li:>4} {lo:>4}   '
                  f'{cf:>6} {len(c.get("entered", [])):>4} {len(c.get("exited", [])):>4}'
                  + ('   (no new list — carried forward)' if carried else ''))
        print('-' * 56)
        cur = [s for s in stocks.values() if (s.get('calc') or {}).get('current')]
        print(f'{len(stocks)} stocks have appeared in Stage 2; {len(cur)} are in it on the computed track today.')
        if len(days) >= 2:
            last = days[-1]
            print(f'Latest scan {last["date"]}: computed track {len(last.get("calc", {}).get("entered", []))} in, '
                  f'{len(last.get("calc", {}).get("exited", []))} out.')

if __name__ == '__main__':
    main()
