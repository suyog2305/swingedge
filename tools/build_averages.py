#!/usr/bin/env python3
"""
build_averages.py — true simple moving averages from NSE closes (the price store), for every name in the scan.

    python tools/build_averages.py                 # rebuild data/daily/averages.json as of the newest store date
    python tools/build_averages.py --date D        # as of an earlier store date
    python tools/build_averages.py --dry-run       # compute and print, write nothing
    python tools/build_averages.py --status        # what is on file, compute nothing

WHY  screener's "DMA 50 / DMA 200" columns are EXPONENTIAL averages. The trend template is written around simple
     ones, and the price store (data/prices/, tools/fetch_bhav.py) now holds a year of NSE's own closes, so this
     computes the simple 50-, 150- and 200-session averages directly. The 7-check verdict in the app is unchanged;
     the page shows these as a separate chip.

WHAT For every code in the newest broad scan (data/scans/, >= 1000 names) that has at least 60 closes in the store:
       sma50 / sma150 / sma200    mean of the last 50 / 150 / 200 closes (null with fewer closes than that)
       sma200_22ago               the 200-session average as it stood 22 sessions earlier (needs 222 closes)
       above50 / above150 / above200   the last close above that average (null when the average is null)
       stack                      sma50 > sma150 > sma200 (null when any is null)
       close, n                   the last close used, and how many closes the name has in the store
     Averages are over the sessions the name actually traded (a suspended day is not a zero).

SPLITS AND BONUSES  The store is raw: NSE's PREV_CLOSE is the previous session's unadjusted close, so a 1:10 split is
     a 90% one-day "fall". A one-session fall beyond 25% is a CANDIDATE, and is adjusted only on evidence; a real crash
     (PB Fintech, 24 Sep 2026: -36%, share count unchanged) must keep its raw closes. In order:
       1. the scans either side (each placed before / after by its own price, since scan dates can lag a session):
          share count (mcap / price) up 20% or more        -> a split or bonus; scale by 1 / that ratio (snapped to a
                                                              standard ratio within 3%)
          share count flat, screener's 200-DMA rescaled by about the fall    -> a demerger or capital reduction
                                                              (screener's feed adjusted it); scale by the observed fall
          share count flat, screener's 200-DMA flat (within 10%)           -> a crash; NOT adjusted
       2. no scan pair: the session OPENED beyond 25% down. NSE's price bands (at most 20%) apply from the base price,
          so an open that far down means the exchange reset the base for a corporate action; a crash opens inside the
          band and falls during the day. Scale by the standard split / bonus ratio nearest the open (within 5%) or the
          close (within 8%), else by the observed fall (a demerger).
       3. no open on file: the standard ratio alone (within 8% of the close), else nothing.
     Anything else keeps its raw closes. Each candidate is listed on the row as `ca`: [date, factor, how], factor null
     when not adjusted (how = crash / no-evidence); `adj` counts the adjustments applied. Rises are never adjusted.

STDLIB ONLY (the scheduled task cannot see user-site packages; test with `python -s`). Writes are atomic.
"""
import argparse, datetime as dt, glob, io, json, math, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import utf8_stdio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, 'data', 'prices')
SCANS = os.path.join(ROOT, 'data', 'scans')
OUT = os.path.join(ROOT, 'data', 'daily', 'averages.json')
MIN_CLOSES = 60
MIN_NAMES = 1000         # a broad scan, as everywhere else
CA_FALL = 0.75           # close / previous close below this = a corporate-action CANDIDATE (adjusted only on evidence)
CA_SNAP = 0.08           # the close within this of a standard ratio -> the standard ratio
CA_SNAP_OPEN = 0.05      # the open within this of a standard ratio -> the standard ratio (tried first)
CA_SNAP_SHARES = 0.03    # 1 / (scan share-count ratio) within this of a standard ratio -> the standard ratio
SHARES_UP = 1.2          # scan share count up at least this much across the date = a split or bonus
SHARES_FLAT = 0.05       # scan share count within this of unchanged
DMA_FLAT = 0.10          # screener's 200-DMA within this of unchanged (it did not rescale its history: a crash)
DMA_MATCH = 0.15         # screener's 200-DMA ratio within this of the fall (it rescaled its history)
SCAN_DAYS = 45           # scans this many calendar days either side of the date count as evidence
# split a:b -> b/a; bonus a:b -> b/(a+b); with combinations (split 1:2 + bonus 1:1 = 1/4, split 1:5 + bonus 1:1 = 1/10)
STANDARD = sorted({1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 6, 1 / 7, 1 / 8, 1 / 9, 1 / 10, 1 / 20, 2 / 3, 2 / 5, 3 / 5, 4 / 7})
QUIET = False


def say(*a):
    if not QUIET:
        print(*a, flush=True)


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def jsave(p, doc):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, separators=(',', ':')) + '\n')
    os.replace(tmp, p)


def dated(pattern):
    out = []
    for p in glob.glob(pattern):
        m = re.search(r'(\d{4}-\d{2}-\d{2})\.json$', p)
        if m:
            out.append((m.group(1), p))
    return sorted(out)


def newest_scan():
    """(date, [codes]) of the newest broad scan."""
    for d, p in reversed(dated(os.path.join(SCANS, '*.json'))):
        try:
            u = jload(p).get('universe') or []
        except (ValueError, OSError):
            continue
        codes = sorted({str(r.get('code') or '').strip().upper() for r in u if r.get('code')})
        if len(u) >= MIN_NAMES and codes:
            return d, codes
    return None, []


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def load_closes(codes, upto):
    """{code: [(date, close, open), ...]} oldest first, from every store file dated on or before `upto`
    (open is None when the file has no open column or no usable value)."""
    want, out, used = set(codes), {c: [] for c in codes}, []
    for d, p in dated(os.path.join(STORE, '20*.json')):
        if d > upto:
            continue
        try:
            doc = jload(p)
            cols = doc.get('cols') or ['close']
            ci = cols.index('close')
            oi = cols.index('open') if 'open' in cols else None
            rows = doc.get('rows') or {}
        except Exception as e:
            print(f'WARNING: skipping unreadable price file {os.path.basename(p)}: {type(e).__name__}: {e}', file=sys.stderr)
            continue
        used.append(d)
        for c in want.intersection(rows):
            row = rows[c]
            v = row[ci]
            if num(v):
                o = row[oi] if oi is not None and oi < len(row) else None
                out[c].append((d, float(v), float(o) if num(o) else None))
    return out, used


def candidates(series):
    """True when the series has a one-session fall beyond CA_FALL (anything that needs the scan evidence)."""
    return any(series[t][1] / series[t - 1][1] < CA_FALL for t in range(1, len(series)))


def load_scan_marks(codes):
    """{code: [(date, price, shares, dma200), ...]} oldest first from every broad scan: the share count (mcap / price)
    and screener's own 200-DMA, which together tell a corporate action from a crash. dma200 is None when missing."""
    want, out = set(codes), {}
    if not want:
        return out
    for d, p in dated(os.path.join(SCANS, '*.json')):
        try:
            u = jload(p).get('universe') or []
        except (ValueError, OSError):
            continue
        if len(u) < MIN_NAMES:
            continue
        for r in u:
            c = str(r.get('code') or '').strip().upper()
            pr, mc, dm = r.get('price'), r.get('mcap'), r.get('dma200')
            if c in want and num(pr) and num(mc):
                out.setdefault(c, []).append((d, float(pr), float(mc) / float(pr), float(dm) if num(dm) else None))
    return out


def scan_verdict(marks, prev_date, date, p0, p1):
    """What the scans either side of a fall from p0 (on prev_date) to p1 (on date) say:
    ('shares', 1/share-count ratio) | ('rescaled', dma ratio) | ('crash', None) | None (no usable pair).
    Each scan is placed before or after the fall by its own price (nearer p0 or p1 on a log scale), not by its date,
    because a scan can carry the next session's close; only scans within SCAN_DAYS of the date are used."""
    if not marks:
        return None
    d0 = dt.date.fromisoformat(date)
    lo = (d0 - dt.timedelta(days=SCAN_DAYS)).isoformat()
    hi = (d0 + dt.timedelta(days=SCAN_DAYS)).isoformat()
    l0, l1 = math.log(p0), math.log(p1)
    after = lambda m: abs(math.log(m[1]) - l1) < abs(math.log(m[1]) - l0)
    pre = None
    for m in marks:
        if lo <= m[0] <= date and not after(m):
            pre = m
    post = next((m for m in marks if prev_date <= m[0] <= hi and after(m) and (pre is None or m[0] > pre[0])), None)
    if pre is None or post is None:
        return None
    sr = post[2] / pre[2]
    if sr >= SHARES_UP:
        return ('shares', 1 / sr)
    if abs(sr - 1) <= SHARES_FLAT and pre[3] and post[3]:
        dr = post[3] / pre[3]
        if abs(dr - 1) <= DMA_FLAT:
            return ('crash', None)
        if abs(dr / (p1 / p0) - 1) <= DMA_MATCH:
            return ('rescaled', dr)
    return None


def nearest(r, tol):
    """The standard split / bonus ratio within `tol` (relative) of r, else None."""
    s = min(STANDARD, key=lambda x: abs(r / x - 1))
    return s if abs(r / s - 1) <= tol else None


def adjusted(series, marks=None):
    """Back-adjusted closes (newest unchanged), the number of adjustments, and every candidate fall as
    [date, factor or None, how] (see SPLITS AND BONUSES in the docstring). series = [(date, close, open), ...]."""
    out, f, n, ca = [0.0] * len(series), 1.0, 0, []
    for t in range(len(series) - 1, -1, -1):
        d, c, o = series[t]
        out[t] = c * f
        if t == 0:
            continue
        pd, pc = series[t - 1][0], series[t - 1][1]
        r = c / pc
        if r >= CA_FALL:
            continue
        ev = scan_verdict(marks, pd, d, pc, c)
        k, how = None, 'no-evidence'
        if ev and ev[0] == 'shares':
            k, how = nearest(ev[1], CA_SNAP_SHARES) or ev[1], 'shares'
        elif ev and ev[0] == 'rescaled':
            k, how = r, 'rescaled'
        elif ev and ev[0] == 'crash':
            how = 'crash'
        elif o is not None and o / pc < CA_FALL:
            k, how = nearest(o / pc, CA_SNAP_OPEN) or nearest(r, CA_SNAP) or r, 'open-gap'
        elif o is None and nearest(r, CA_SNAP):
            k, how = nearest(r, CA_SNAP), 'ratio'
        if k:
            f *= k
            n += 1
        ca.append([d, None if k is None else round(k, 6), how])
    ca.reverse()
    return out, n, ca


def sma(xs, n, back=0):
    end = len(xs) - back
    if end < n:
        return None
    return sum(xs[end - n:end]) / n


def build(asof=None):
    scan_date, codes = newest_scan()
    if not codes:
        raise SystemExit('no broad scan in data/scans/')
    store = dated(os.path.join(STORE, '20*.json'))
    if not store:
        raise SystemExit('the price store is empty (run tools/fetch_bhav.py --backfill 260)')
    asof = asof or store[-1][0]
    series, used = load_closes(codes, asof)
    if not used:
        raise SystemExit(f'no store file on or before {asof}')
    date = used[-1]
    keep = [c for c in codes if len(series.get(c) or []) >= MIN_CLOSES and series[c][-1][0] == date]
    marks = load_scan_marks([c for c in keep if candidates(series[c])])
    rows, n_adj, n_raw = {}, 0, 0
    r2 = lambda v: None if v is None else round(v, 2)
    for c in keep:                                            # too short, or did not trade on the as-of date: skipped
        s = series[c]
        xs, adj, ca = adjusted(s, marks.get(c))
        last = xs[-1]
        a50, a150, a200, a200b = sma(xs, 50), sma(xs, 150), sma(xs, 200), sma(xs, 200, 22)
        ab = lambda a: None if a is None else last > a
        row = {'sma50': r2(a50), 'sma150': r2(a150), 'sma200': r2(a200), 'sma200_22ago': r2(a200b),
               'above50': ab(a50), 'above150': ab(a150), 'above200': ab(a200),
               'stack': None if None in (a50, a150, a200) else (a50 > a150 > a200), 'close': r2(last), 'n': len(xs)}
        if adj:
            row['adj'] = adj; n_adj += 1
        if ca:
            row['ca'] = ca
            n_raw += any(x[1] is None for x in ca)
        rows[c] = row
    doc = {'schema': 1, 'date': date, 'scan_date': scan_date, 'source': 'NSE closes (data/prices)', 'n': len(rows),
           'sessions': len(used), 'first': used[0], 'adjusted': n_adj, 'kept_raw': n_raw,
           'note': 'Simple averages of NSE closes over the sessions each name traded. A one-session fall beyond 25% is '
                   'scaled out of the earlier closes only on evidence of a split, bonus or demerger (scan share count, '
                   "screener's rescaled 200-DMA, or an open beyond the price band); otherwise, a crash, the closes stay "
                   'raw. Each such date is listed in the row\'s "ca" as [date, factor or null, how]. '
                   'Built by tools/build_averages.py.',
           'rows': rows}
    return doc, len(codes)


def summary(doc, scanned=None):
    R = doc.get('rows') or {}
    cnt = lambda k: sum(1 for r in R.values() if r.get(k))
    have = lambda k: sum(1 for r in R.values() if r.get(k) is not None)
    print(f"averages as of {doc.get('date')} (scan {doc.get('scan_date')}): {doc.get('n')} names"
          + (f' of {scanned} in the scan' if scanned else '') + f", {doc.get('sessions')} store sessions from {doc.get('first')}")
    print(f"  sma200 for {have('sma200')}, sma200_22ago for {have('sma200_22ago')}; above 50/150/200: "
          f"{cnt('above50')}/{cnt('above150')}/{cnt('above200')}; stacked {cnt('stack')}; "
          f"{doc.get('adjusted')} names with a split/bonus adjustment, {doc.get('kept_raw', 0)} with a big fall kept raw")
    hows = {}
    for c, r in sorted(R.items()):
        for d, k, how in r.get('ca') or []:
            hows.setdefault(how, []).append(f'{c} {d}' + ('' if k is None else f' x{k:.4g}'))
    for how in sorted(hows):
        print(f'  {how} ({len(hows[how])}): ' + ', '.join(hows[how]))


def main():
    global QUIET
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', help='as of this store date (default: the newest)')
    ap.add_argument('--dry-run', action='store_true', help='compute and print; write nothing')
    ap.add_argument('--status', action='store_true', help='print what is on file; compute nothing')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    QUIET = a.quiet
    if a.status:
        if not os.path.exists(OUT):
            print('no data/daily/averages.json yet'); return 0
        summary(jload(OUT)); print(f'  file size {os.path.getsize(OUT) / 1e3:.0f} KB'); return 0
    if a.date:
        dt.date.fromisoformat(a.date)
    doc, scanned = build(a.date)
    if a.dry_run:
        summary(doc, scanned); print('--dry-run: nothing written'); return 0
    jsave(OUT, doc)
    if not QUIET:
        summary(doc, scanned); print(f'  wrote data/daily/averages.json ({os.path.getsize(OUT) / 1e3:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
