#!/usr/bin/env python3
"""
holdings_check.py — your own holdings against the RS leaders list, on your own machine.

    python tools/holdings_check.py                       # read .secrets/holdings.json, print the check
    python tools/holdings_check.py --from-kite raw.json  # first convert a Kite holdings dump into that file
    python tools/holdings_check.py --codes HFCL,STLTECH  # or just name the codes

PRIVATE BY DESIGN. The site and this repository are public, so holdings are never written under
data/ and never committed: they live in .secrets/holdings.json, which .gitignore excludes, and in
the browser's own storage once you press "Import holdings" on the RS Leaders page. This tool reads
that file, prints the check, and writes nothing else.

WHERE THE FILE COMES FROM
  Kite MCP   Ask Claude to "pull my Kite holdings": after you log in to Zerodha in the browser, it
             reads the holdings and saves them here. Zerodha requires that login once a day, so
             this cannot run unattended.
  By hand    Download the holdings file from Kite or Console and press "Import holdings" in the
             app (it reads .csv and .xlsx too); this tool is then optional.

THE CHECK. Each holding is placed in one group, the same four the page uses:
  In the top N        rank N or better in data/daily/rs_tracker.json
  Gaining strength    outside the list but within 125 places of it, and up 10+ places in a week
                      or up two weeks running
  Stage 2 list only   on the provider's Stage 2 list carried by the newest scan
  Outside             none of those
and the share inside the top N is compared with the 70-80% rule the owner set for himself. It
describes the book; it does not tell you what to do with it.

STDLIB ONLY.
"""
import argparse, glob, io, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, '.secrets', 'holdings.json')
TRACKER = os.path.join(ROOT, 'data', 'daily', 'rs_tracker.json')


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def names_from(obj):
    """Accepts the app's own shape, a Kite MCP holdings array, or anything with tradingsymbol / symbol."""
    arr = obj if isinstance(obj, list) else (obj.get('names') or obj.get('holdings') or obj.get('data') or [])
    out, seen = [], set()
    for h in arr:
        code = str(h.get('code') or h.get('tradingsymbol') or h.get('symbol') or h.get('instrument') or '').strip().upper()
        code = re.sub(r'-(EQ|BE)$|\.NS$', '', code)
        if not code or code in seen:
            continue
        seen.add(code)
        qty = (h.get('qty') or h.get('quantity') or 0) + (h.get('t1_quantity') or 0)
        out.append({'code': code, 'qty': qty or None, 'avg': h.get('avg') or h.get('average_price') or None})
    return out


def climbing(x, n):
    k, r = x.get('k') or [], x.get('rk')
    return bool(r and n < r <= n + 125 and len(k) > 1 and k[1] is not None
                and (k[1] - r >= 10 or (len(k) > 2 and k[2] is not None and k[1] < k[2] and r < k[1])))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from-kite', metavar='FILE', help='a Kite holdings JSON dump to convert into .secrets/holdings.json first')
    ap.add_argument('--codes', help='comma-separated NSE codes, instead of the private file')
    a = ap.parse_args()
    if a.from_kite:
        names = names_from(jload(a.from_kite))
        if not names:
            print('no holdings found in ' + a.from_kite); return 2
        os.makedirs(os.path.dirname(PRIVATE), exist_ok=True)
        import datetime as dt
        with io.open(PRIVATE, 'w', encoding='utf-8') as fh:
            json.dump({'updated': dt.date.today().isoformat(), 'source': 'Kite', 'names': names}, fh, indent=1)
        print(f'saved {len(names)} holdings to .secrets/holdings.json (gitignored - never committed)')
    if a.codes:
        names = [{'code': c.strip().upper()} for c in a.codes.split(',') if c.strip()]
    elif os.path.exists(PRIVATE):
        names = names_from(jload(PRIVATE))
    else:
        print('no .secrets/holdings.json yet - pull it through Kite, or pass --codes A,B,C'); return 2
    t = jload(TRACKER)
    n, codes = t['top'], t['codes']
    scans = sorted(p for p in glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')))
    s2 = {str(r.get('code') or '').upper() for r in (jload(scans[-1]).get('stage2') or [])} if scans else set()
    rows = []
    for h in names:
        x = codes.get(h['code'])
        if not x:
            rows.append((10 ** 9, h['code'], '-', '-', 'not in the scan', '-', '-')); continue
        where = f'in the top {n}' if x['rk'] <= n else 'gaining strength' if climbing(x, n) else 'Stage 2 list only' if h['code'] in s2 else 'outside'
        k = x.get('k') or []
        rows.append((x['rk'], h['code'], f"#{x['rk']}", x.get('rs'), where, f"{x.get('tp')}/{x.get('tt')} {x.get('t') or ''}".strip(),
                     (f"#{k[1]} -> #{x['rk']}" if len(k) > 1 and k[1] else 'new') + f" | {x.get('ex') or ''} | {x.get('v') or ''}"))
    rows.sort()
    inside = sum(1 for r in rows if r[4].startswith('in the top'))
    print(f"\nscan {t['scan_date']} - {inside} of {len(rows)} holdings inside the top {n} ({round(100 * inside / len(rows)) if rows else 0}%); the rule set is 70-80%\n")
    print(f"{'code':<12} {'rank':>6} {'RS':>3}  {'against the list':<18} {'trend':<16} last week -> now | exhaustion | verdict")
    for _rk, code, rank, rs, where, trend, tail in rows:
        print(f'{code:<12} {rank:>6} {str(rs):>3}  {where:<18} {trend:<16} {tail}')
    out = [r for r in rows if not r[4].startswith('in the top')]
    if out:
        print('\noutside the list: ' + ', '.join(f'{r[1]} ({r[2]}, {r[4]})' for r in out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
