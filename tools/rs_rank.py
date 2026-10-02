#!/usr/bin/env python3
"""
rs_rank.py — the market ranked by Relative Strength, straight from the newest scan in the repo.

    python tools/rs_rank.py                    # top 25 by RS rating
    python tools/rs_rank.py --top 50 --min-mcap 1000
    python tools/rs_rank.py --date 2026-09-30 --json

RS is the same 1–99 rating the app shows (tools/rs.py: IBD-style 12-month composite, most
recent quarter double-weighted, percentile-ranked across the whole scan). The Δ column is
the change in RS since the previous scan. No LLM, no network, no cost.
"""
import argparse, glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import num, rate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(ROOT, 'data', 'scans')


def scan_files():
    return sorted(p for p in glob.glob(os.path.join(SCANS, '*.json')) if re.search(r'\d{4}-\d{2}-\d{2}\.json$', p))


def load(path):
    with open(path, encoding='utf-8') as fh: return json.load(fh)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--date', help='scan date YYYY-MM-DD (default: newest)')
    ap.add_argument('--min-mcap', type=float, default=0, help='drop names below this market cap (₹Cr)')
    ap.add_argument('--json', action='store_true', help='emit JSON instead of a table')
    a = ap.parse_args()

    files = scan_files()
    if not files: sys.exit('no dated scan in data/scans/')
    path = os.path.join(SCANS, a.date + '.json') if a.date else files[-1]
    if path not in files: sys.exit(f'no scan for {a.date}')
    d = load(path)
    U = d.get('universe', [])
    mode = rate(U)                       # ranked across the whole scan, exactly as the app does

    prev_rs = {}
    i = files.index(path)
    if i > 0:
        P = load(files[i - 1]).get('universe', [])
        rate(P)
        prev_rs = {str(r['code']).upper(): r['_rs'] for r in P if r.get('code')}

    rows = [r for r in U if r.get('code') and r['_rs'] is not None and (num(r.get('mcap')) or 0) >= a.min_mcap]
    rows.sort(key=lambda r: (-r['_rs'], -r['_m']))
    out = []
    for k, r in enumerate(rows[:a.top], 1):
        code = str(r['code']).upper()
        pr = prev_rs.get(code)
        out.append({'rank': k, 'code': code, 'name': r.get('name'), 'rs': r['_rs'],
                    'rs_delta': None if pr is None else r['_rs'] - pr,
                    'r1d': r.get('r1d'), 'r1w': r.get('r1w'), 'r1m': r.get('r1m'), 'r3m': r.get('r3m'),
                    'r6m': r.get('r6m'), 'r1y': r.get('r1y'), 'mcap': r.get('mcap'), 'industry': r.get('industry')})

    if a.json:
        print(json.dumps({'date': d.get('date'), 'mode': mode, 'rows': out}, ensure_ascii=False, indent=1)); return

    def f(v, dec=1):
        v = num(v); return '-' if v is None else f'{v:.{dec}f}'
    print(f"scan {d.get('date')} · {len(U)} names · RS mode {mode} · top {len(out)}")
    print(f"{'#':>3} {'Code':<12}{'Name':<24}{'RS':>3} {'Δ':>4}{'1D':>7}{'1W':>7}{'1M':>7}{'3M':>7}{'6M':>8}{'1Y':>8}  Industry")
    for o in out:
        dl = '-' if o['rs_delta'] is None else f"{o['rs_delta']:+d}"
        print(f"{o['rank']:>3} {o['code']:<12}{(o['name'] or '')[:23]:<24}{o['rs']:>3} {dl:>4}{f(o['r1d']):>7}{f(o['r1w']):>7}{f(o['r1m']):>7}{f(o['r3m'], 0):>7}{f(o['r6m'], 0):>8}{f(o['r1y'], 0):>8}  {o['industry'] or ''}")


if __name__ == '__main__':
    main()
