#!/usr/bin/env python3
"""
signals.py — what the framework said about a stock ON THE DAY its report was published.

    python tools/report/signals.py KIRIINDUS 2026-09-08        # one lookup, printed
    python tools/report/signals.py --backfill                   # stamp every report in the index
    python tools/report/signals.py --drift                      # at-publication vs now, every report

WHY THIS EXISTS

A report is a dated document. Kiri passed the trend template 7/7 at the 8 Sep close and the
report said so; two sessions later it failed on RS. Rewriting the report would be dishonest;
leaving the reader to guess is unhelpful. So the Research Desk shows two things side by side:
what the framework said when the report was written, and what it says now. This file supplies
the first half. The second half is computed in the browser from the newest scan, so it refreshes
with every daily pull and no report is ever touched.

WHAT "AT PUBLICATION" MEANS, PRECISELY

The newest dated data point ON OR BEFORE the report's date that contains the stock:

  * a full scan in data/scans/ (universe of 100+ names), with RS re-ranked across that universe
    exactly as build_s2history does; or
  * a live-page snapshot in data/daily/snapshots/, which carries each name's RS as captured.

The snapshot rule matters. The seven reports dated 8 Sep 2026 quote figures from the 8 Sep close
snapshot; the nearest full scan is 27 Aug and would contradict their own tables. On a shared
date a full scan outranks a snapshot. The 200-DMA slope is measured against the newest full scan
strictly before that date, and the Stage 2 status and surveillance flag come from the newest
provider list on or before it.

Every field is stored, not derived later, so the index says what was true and stays true.
"""
import argparse, glob, io, json, os, sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'report'))
from build_s2history import rs_percentiles, num          # noqa: E402
from framework_block import load_snapshot, trend, jload   # noqa: E402

SCANS = os.path.join(ROOT, 'data', 'scans')
SNAPS = os.path.join(ROOT, 'data', 'daily', 'snapshots')
INDEX = os.path.join(ROOT, 'library', 'research', 'index.json')
SHORT = {'price > 50-DMA': '50-DMA', 'price > 200-DMA': '200-DMA', '50-DMA > 200-DMA': '50>200',
         '>= 30% off 52w low': '52w low', 'within 25% of 52w high': '52w band', 'RS >= 70': 'RS',
         '200-DMA rising': '200 slope'}
_rows = {}


def data_points():
    """Every dated data point, oldest first. On a shared date the full scan sorts last, so a
    reverse walk meets it first."""
    pts = []
    for p in glob.glob(os.path.join(SCANS, '20*.json')):
        d = jload(p)
        n = len(d.get('universe', []) or [])                             # the ranking universe, all rows
        pts.append(dict(kind='scan', date=d.get('date') or os.path.basename(p)[:10], path=p, n=n,
                        has_s2=bool(d.get('stage2'))))
    for p in glob.glob(os.path.join(SNAPS, '20*.json')):
        d = jload(p)
        pts.append(dict(kind='snapshot', date=d.get('date') or os.path.basename(p)[:10], path=p,
                        n=len(d.get('names', [])), has_s2=False))
    return sorted(pts, key=lambda x: (x['date'], x['kind'] == 'scan'))


def rows_for(pt):
    if pt['path'] not in _rows:
        if pt['kind'] == 'snapshot':
            _, rows = load_snapshot(pt['path'])
        else:
            allU = jload(pt['path']).get('universe', []) or []      # rank over every row, as the app does
            rs_percentiles(allU)
            rows = {r['code'].upper(): r for r in allU if r.get('code')}
        _rows[pt['path']] = rows
    return _rows[pt['path']]


def prev_universe(before, pts):
    for pt in reversed(pts):
        if pt['kind'] == 'scan' and pt['date'] < before and pt['n'] >= 500:
            return rows_for(pt)
    return {}


def stage2_on_or_before(date, pts):
    for pt in reversed(pts):
        if pt['kind'] == 'scan' and pt['has_s2'] and pt['date'] <= date:
            S = jload(pt['path']).get('stage2') or []
            return {str(r.get('code', '')).upper(): r for r in S if r.get('code')}, pt['date']
    return {}, None


def describe(code, row, pt, pts, s2, s2date):
    ok, k, n, failed = trend(row, prev_universe(pt['date'], pts).get(code))
    s = s2.get(code)
    flag = (s or {}).get('asm')
    flag = flag.strip() if isinstance(flag, str) and flag.strip() not in ('', '-') else None
    rs = num(row.get('_rs'))
    return OrderedDict(
        date=pt['date'], source=pt['kind'], universe=pt['n'],
        rs=int(rs) if rs is not None else None,
        template=(('PASS' if ok else 'FAIL') if n >= 5 else None),
        passed=k, checks=n, failed=[SHORT.get(f, f) for f in failed],
        stage2=((f"{s.get('status') or 'On list'} · {s.get('weeks') or '?'} wks" if s else 'Not on list') if s2date else None),
        stage2_list_date=s2date, flag=flag, price=num(row.get('price')))


def signals_for(code, date, pts=None):
    """The framework's reading of `code` from the newest data point on or before `date`."""
    code = str(code or '').upper()
    if not code or not date:
        return None
    pts = pts or data_points()
    s2, s2date = stage2_on_or_before(date, pts)
    for pt in reversed(pts):
        if pt['date'] > date or (pt['kind'] == 'scan' and pt['n'] < 100):
            continue
        row = rows_for(pt).get(code)
        if row:
            return describe(code, row, pt, pts, s2, s2date)
    return None


def now_for(code, pts=None):
    """The same reading from the newest full scan — what the browser computes live."""
    pts = pts or data_points()
    newest = next((pt for pt in reversed(pts) if pt['kind'] == 'scan' and pt['n'] >= 500), None)
    if not newest:
        return None
    row = rows_for(newest).get(str(code or '').upper())
    s2, s2date = stage2_on_or_before(newest['date'], pts)
    return describe(str(code).upper(), row, newest, pts, s2, s2date) if row else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('code', nargs='?'); ap.add_argument('date', nargs='?')
    ap.add_argument('--backfill', action='store_true', help='stamp signals_at_pub on every report in the index')
    ap.add_argument('--drift', action='store_true', help='table: at publication vs the newest scan, every report')
    a = ap.parse_args()
    pts = data_points()

    if a.backfill or a.drift:
        idx = json.load(io.open(INDEX, encoding='utf-8'), object_pairs_hook=OrderedDict)
        done, skipped, flips = 0, [], 0
        print(f"{'report':<26}{'code':<12}{'at pub':<12}{'RS':>4} {'template':<10}{'| now':<12}{'RS':>4} {'template':<10} drift")
        for r in idx['reports']:
            sig = signals_for(r.get('code'), r.get('date'), pts)
            if not sig:
                skipped.append(r['id']); continue
            if a.backfill:
                r['signals_at_pub'] = sig; done += 1
            n = now_for(r.get('code'), pts)
            drift = ''
            if n and sig.get('template') and n.get('template') and sig['template'] != n['template']:
                drift = 'FLIPPED -> ' + ('passes' if n['template'] == 'PASS' else 'fails'); flips += 1
            tt = lambda s: (f"{s['template']} {s['passed']}/{s['checks']}" if s and s.get('template') else '—')
            print(f"{r['id']:<26}{r.get('code',''):<12}{sig['date']:<12}{str(sig['rs']):>4} {tt(sig):<10}"
                  f"| {(n or {}).get('date', '—'):<10}{str((n or {}).get('rs', '—')):>4} {tt(n):<10} {drift}")
        if skipped:
            print(f'\nno data point on or before the report date for: {", ".join(skipped)}')
        print(f'\n{flips} verdict(s) flipped since publication')
        if a.backfill:
            json.dump(idx, io.open(INDEX, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
            print(f'stamped signals_at_pub on {done} report(s) -> {os.path.relpath(INDEX, ROOT)}')
        return 0

    if not (a.code and a.date):
        ap.error('give CODE DATE, or --backfill / --drift')
    sig = signals_for(a.code, a.date, pts)
    print(json.dumps(sig, ensure_ascii=False, indent=1) if sig else f'no data point on or before {a.date} contains {a.code}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
