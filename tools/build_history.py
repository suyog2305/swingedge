#!/usr/bin/env python3
"""
build_history.py - precomputed history for the RS Trend and Track Record pages, so the browser reads two
files instead of fetching every scan (about 1 MB each) and ranking each one itself.

    python tools/build_history.py              # build both files from data/scans/
    python tools/build_history.py --quiet      # the evening pull (tools/eod.py, after build_breadth)
    python tools/build_history.py --dry-run    # build in memory, print dates and sizes, write nothing
    python tools/build_history.py --status     # what is on disk now: dates and sizes

No network, no model, stdlib only. RS is ranked over each scan's WHOLE universe with tools/rs.py (the same
engine as the page's seRank), and the trend template is build_rs_tracker.trend(), the 7 checks the page's
seTrend applies.

data/daily/rs_history.json  - RS Trend (both tabs)
  {"schema": 1, "updated", "dates": [the last 60 broad scans (1,000+ names), oldest first],
   "codes": {"HFCL": {"rs": [1-99 or null per date], "vol": [screener's 1-month average volume, shares, per date],
                      "price": [close per date, 2 dp]}}}
  Keyed the way the RS Trend page keys a row: the NSE code, or the upper-cased name for the few rows that
  carry none. The Volume Trend tab's value traded (Rs cr) is vol x price / 1e7, which the page works out
  itself from these two exact figures, so its numbers are bit-for-bit those of the scans. (A stored, rounded
  value in Rs cr was tried: at 1 dp it moved 500+ of the percentages shown, and even at 6 significant
  figures a dozen displayed values changed by 0.1 through double rounding.) vol is absent for a code
  before the pull carried vol_1m (9 Sep 2026).
data/daily/track.json  - Track Record
  {"schema": 1, "updated", "dates": [the last 120 scans with priced, coded rows, oldest first],
   "universe": [rows in each scan], "stage2_asof": [the provider list date each scan carried],
   "codes": {"HFCL": {"price": [...], "rs": [...], "s2": "0110...", "tt": "0011...", "nh": "0100..."}}}
  One character per date: s2 = on the provider's Stage 2 list carried by that scan, tt = passes the trend
  template (5+ checks, all passed; the 200-day slope compares with the previous scan in this list, as the
  page does), nh = within 3% of the 52-week high (the page's "Near 52W high" signal). A null price means the
  code was not in that scan (or had no price), and its characters there are 0.
  Track Record has always read EVERY scan with prices (the early narrow pulls too, each ranked inside its own
  universe), so this file does as well; RS Trend uses broad scans only, and so does rs_history.json.
Names, industries and groups stay out of both files: the page joins the newest scan for those.
"""
import argparse, datetime as dt, glob, io, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import num, rate, utf8_stdio  # noqa: E402
from build_rs_tracker import trend  # noqa: E402  (one template, one place)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(ROOT, 'data', 'scans')
RS_OUT = os.path.join(ROOT, 'data', 'daily', 'rs_history.json')
TR_OUT = os.path.join(ROOT, 'data', 'daily', 'track.json')
BROAD = 1000          # the page's RS Trend pool: scans of 1,000+ names
RS_DATES = 60
TR_DATES = 120
NEAR_HIGH = -3        # TR_SIGNALS 'nearhigh': from_52wh >= -3


def jload(p):
    with io.open(p, encoding='utf-8') as fh: return json.load(fh)


def scan_list():
    """[(date, path)] oldest first: the files data/scans/index.json lists (what the page reads), else every dated file."""
    out = []
    try:
        for s in jload(os.path.join(SCANS, 'index.json')).get('scans', []):
            f = s.get('file') if isinstance(s, dict) else None
            if f and os.path.exists(os.path.join(SCANS, f)):
                out.append((str(s.get('date') or f[:10]), os.path.join(SCANS, f)))
    except (ValueError, OSError, AttributeError, TypeError):
        out = []
    if not out:
        for p in glob.glob(os.path.join(SCANS, '*.json')):
            m = re.search(r'(\d{4}-\d{2}-\d{2})\.json$', p)
            if m: out.append((m.group(1), p))
    return sorted(set(out))


def r2(v):
    v = num(v)
    if v is None: return None
    f = round(v, 2)
    return int(f) if f == int(f) else f


def load_scans(quiet):
    """Every readable scan with a universe, ranked: [{date, s2asof, rows, s2}] oldest first (by the scan's own date)."""
    out = []
    for date, p in scan_list():
        try: d = jload(p)
        except (ValueError, OSError) as e:
            print(f'WARNING: skipping unreadable scan {os.path.basename(p)}: {type(e).__name__}: {e}', file=sys.stderr); continue
        U = d.get('universe') or []
        if not isinstance(U, list) or not U: continue
        U = [dict(r) for r in U if isinstance(r, dict)]
        rate(U)                                    # whole universe, codeless rows included: same as seRank
        out.append({'date': str(d.get('date') or date), 'asof': d.get('stage2_asof'), 'rows': U,
                    's2': {str(s.get('code') or '').upper() for s in (d.get('stage2') or []) if isinstance(s, dict)}})
    out.sort(key=lambda s: s['date'])
    return out


def build_rs(scans, updated):
    broad = [s for s in scans if len(s['rows']) >= BROAD][-RS_DATES:]
    dates = [s['date'] for s in broad]
    codes = {}
    n = len(broad)
    for i, s in enumerate(broad):
        for r in s['rows']:
            k = str(r.get('code') or r.get('name') or '').strip().upper()   # the RS Trend page's row key
            if not k: continue
            c = codes.get(k)
            if c is None: c = codes[k] = {'rs': [None] * n, 'vol': [None] * n, 'price': [None] * n}
            vol = num(r.get('vol_1m'))
            c['rs'][i] = r.get('_rs')
            c['vol'][i] = (int(vol) if vol == int(vol) else vol) if vol is not None else None
            c['price'][i] = r2(r.get('price'))
    for c in codes.values():
        if all(v is None for v in c['vol']): del c['vol']
    return {'schema': 1, 'updated': updated, 'source': 'data/scans (broad scans, RS ranked by tools/rs.py)', 'dates': dates,
            'codes': codes}


def build_track(scans, updated):
    priced = [s for s in scans if any(r.get('code') and num(r.get('price')) is not None for r in s['rows'])]
    # the 200-day slope check compares with the previous scan in THIS list (the page's `prev`), so walk all of it
    per = []
    prev = None
    for s in priced:
        U = [r for r in s['rows'] if r.get('code') and num(r.get('price')) is not None]
        m = {}
        for r in U:
            k = str(r['code']).upper()
            p, t = trend(r, prev.get(k) if prev else None)
            m[k] = (r, t >= 5 and p == t)
        per.append((s, m))
        prev = {k: v[0] for k, v in m.items()}
    per = per[-TR_DATES:]
    n = len(per)
    codes = {}
    for i, (s, m) in enumerate(per):
        for k, (r, tt) in m.items():
            c = codes.get(k)
            if c is None: c = codes[k] = {'price': [None] * n, 'rs': [None] * n, 's2': ['0'] * n, 'tt': ['0'] * n, 'nh': ['0'] * n}
            c['price'][i] = r2(r.get('price'))
            c['rs'][i] = r.get('_rs')
            if k in s['s2']: c['s2'][i] = '1'
            if tt: c['tt'][i] = '1'
            fh = num(r.get('from_52wh'))
            if fh is not None and fh >= NEAR_HIGH: c['nh'][i] = '1'
    for c in codes.values():
        for f in ('s2', 'tt', 'nh'): c[f] = ''.join(c[f])
    return {'schema': 1, 'updated': updated, 'source': 'data/scans (every scan with priced rows, RS ranked by tools/rs.py)',
            'dates': [s['date'] for s, _ in per], 'universe': [len(m) for _, m in per],
            'stage2_asof': [s['asof'] for s, _ in per], 'codes': codes}


def dump(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(',', ':')) + '\n'


def write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as fh: fh.write(text)
    os.replace(tmp, path)


def describe(label, doc, size):
    d = doc.get('dates') or []
    span = f"{d[0]} -> {d[-1]}" if d else 'no dates'
    return f"{label:<17} {len(d):>3} dates ({span}) · {len(doc.get('codes') or {}):>5} codes · {size / 1024:,.0f} KB"


def status():
    for label, p in (('rs_history.json', RS_OUT), ('track.json', TR_OUT)):
        if not os.path.exists(p): print(f'{label:<17} missing ({os.path.relpath(p, ROOT)})'); continue
        try: doc = jload(p)
        except (ValueError, OSError) as e: print(f'{label:<17} unreadable: {e}'); continue
        print(describe(label, doc, os.path.getsize(p)) + f" · updated {doc.get('updated')}")
    files = scan_list()
    if files: print(f'newest scan on disk: {files[-1][0]}')


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--quiet', action='store_true', help='print nothing on success')
    ap.add_argument('--dry-run', action='store_true', help='build in memory and print sizes; write nothing')
    ap.add_argument('--status', action='store_true', help='print what is on disk now; write nothing')
    a = ap.parse_args()
    if a.status: status(); return

    scans = load_scans(a.quiet)
    if not scans: sys.exit('no readable scan with a universe in data/scans/')
    updated = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    rs_doc, tr_doc = build_rs(scans, updated), build_track(scans, updated)
    rs_txt, tr_txt = dump(rs_doc), dump(tr_doc)
    if not a.dry_run:
        write_atomic(RS_OUT, rs_txt)
        write_atomic(TR_OUT, tr_txt)
    if a.quiet: return
    tag = ' (dry run: nothing written)' if a.dry_run else ''
    print(describe('rs_history.json', rs_doc, len(rs_txt.encode('utf-8'))) + tag)
    print(describe('track.json', tr_doc, len(tr_txt.encode('utf-8'))) + tag)


if __name__ == '__main__':
    main()
