#!/usr/bin/env python3
"""
build_scan.py — publish one week of scan data for SwingEdge's Edge modules
(Market Pulse, RS Screen, Sectors, Stage 2 Tracker).

    python tools/build_scan.py --date 2026-08-21 \
        --screener "exports/near-52w-high-weekly-scan_23Aug.csv" \
        --stage2   "exports/Stage 2_21st Aug.xlsx"

Inputs (each optional, .csv or .xlsx, columns matched by name):
  --screener   screener.in export of your weekly scan (near-52W-high / weekly gainers,
               or — better for market-wide RS — a full "market cap > 1000" screen)
  --stage2     the weekly Stage 2 list (TradingView Code, Industry, Relative Strength %,
               Market Cap, Avg Weekly Volumes, % Return, Weeks, Earliest Date, ASM/ESM/GSM,
               Days in ASM, Stage2 Status)

Output:  data/scans/<date>.json  (schema swingedge-scan/1, raw rows only — RS ratings,
         trend-template checks, sector rotation and entry/exit diffs are computed in-app)
         data/scans/index.json   (upserted, newest first)

Re-running for the same date replaces the sections you pass and keeps the others.
"""
import argparse, datetime as dt, io, json, os, re, sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_weekly import read_table, map_columns, num, rnd, clean_code, norm, ALIASES, ALIAS_LOOKUP  # noqa: E402

SCHEMA = 'swingedge-scan/1'

# extra headings for the screener universe + the Stage 2 list
EXTRA = {
    'group':      ['industry group', 'sector group', 'industry_group'],
    'isin':       ['isin code', 'isin'],
    'r1d':        ['return over 1day', 'return over 1 day', '1 day return', '1d', 'price change 1d', 'return 1d', 'perf day'],
    'r3m':        ['return over 3months', 'return over 3 months', '3 month return', '3m return', '3m', 'price change 3m', 'return 3m', 'return over 3month', 'perf quarter', 'return over 3 month'],
    'r6m':        ['return over 6months', 'return over 6 months', '6 month return', '6m return', '6m', 'price change 6m', 'return 6m', 'return over 6month', 'perf half y'],
    'up_52wl':    ['up from 52w low', 'up from 52 week low', 'from 52w low', 'above 52w low', '% from 52w low'],
    'peg':        ['peg ratio', 'peg'],
    'pe':         ['price to earning', 'price to earnings', 'pe', 'p/e', 'pe ratio', 'stock p/e'],
    'pb':         ['price to book value', 'price to book', 'pb', 'p/b'],
    'sales_qoq':  ['sales growth qoq', 'sales growth (qoq)', 'qoq sales growth', 'sales qoq'],
    'op_qoq':     ['qoq oper profit growth', 'qoq operating profit growth', 'oper profit growth qoq', 'operating profit growth qoq'],
    'eps_qoq':    ['eps qoq growth', 'eps growth qoq', 'qoq eps growth'],
    'np_qoq':     ['net profit qoq growth', 'net profit growth qoq', 'qoq net profit growth'],
    'sales_yoy':  ['sales growth yoy', 'sales growth (yoy)', 'yoy sales growth', 'sales yoy', 'sales growth'],
    'op_yoy':     ['oper profit growth yoy', 'oper  profit growth yoy', 'operating profit growth yoy', 'oper profit growth'],
    'np_yoy':     ['net profit yoy growth', 'net profit growth yoy', 'yoy net profit growth', 'profit growth'],
    'eps_yoy':    ['eps yoy growth', 'eps growth yoy', 'yoy eps growth', 'eps growth'],
    'opm_q':      ['opm latest quarter', 'opm last quarter', 'opm current quarter', 'opm latest qtr'],
    'opm_pq':     ['opm preceding quarter', 'opm previous quarter', 'opm preceding qtr'],
    'opm_pyq':    ['opm preceding year quarter', 'opm previous year quarter', 'opm preceding year qtr'],
    'public_hold':['ex public holding', 'public holding', 'public shareholding'],
    'float':      ['public float', 'free float', 'float'],
    'rec_sales':  ['receivables to sales ratio', 'receivables to sales'],
    'volume':     ['volume', 'avg volume', 'average volume', 'volume 1w', 'avg volume 1w'],
    # Stage 2 list
    'tv_code':    ['tradingview code', 'tradingview', 'tv code', 'symbol', 'code', 'ticker', 'nse code'],
    'rs_pct':     ['relative strength %', 'relative strength', 'rs %', 'rs%', 'rs'],
    'mcap_cr':    ['market cap (in cr.)', 'market cap in cr', 'market cap (cr)', 'mcap (cr)', 'market cap', 'market capitalization', 'mcap'],
    'vol_cr':     ['avg weekly volumes (in cr.)', 'avg weekly volumes', 'avg weekly volume', 'weekly volume (cr)', 'avg volume (cr)', 'avg weekly volume (in cr.)'],
    'ret':        ['% return', 'return %', 'return', 'pct return', 'return since entry', '% return since entry'],
    'weeks':      ['weeks', 'weeks in stage 2', 'weeks in stage2', 'no of weeks', 'week count'],
    'since':      ['earliest date', 'entry date', 'since', 'first date', 'date'],
    'asm':        ['asm esm gsm', 'asm/esm/gsm', 'asm', 'surveillance'],
    'asm_days':   ['days in asm esm gsm', 'days in asm', 'asm days', 'days in surveillance'],
    'status':     ['stage2 status', 'stage 2 status', 'status', 'stage2status'],
}
for k, v in EXTRA.items():
    ALIASES.setdefault(k, []); ALIASES[k] = list(dict.fromkeys(ALIASES[k] + v)); ALIAS_LOOKUP[k] = {norm(a) for a in ALIASES[k]}

def cell(row, cols, f):
    i = cols.get(f)
    return row[i] if i is not None and i < len(row) else None

def excel_date(v):
    s = str(v or '').strip()
    if not s: return None
    if re.fullmatch(r'\d{4,5}(\.0+)?', s):
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(s)))).isoformat()
    for f in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%d %b %Y', '%d-%b-%Y', '%d-%b-%y', '%m/%d/%Y', '%d %B %Y'):
        try: return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError: pass
    return None

def build_universe(path, min_mcap):
    headers, rows = read_table(path)
    cols = map_columns(headers, ['name', 'bse', 'code', 'isin', 'group', 'industry', 'price', 'mcap', 'dma50', 'dma200',
                                 'r3m', 'r6m', 'y1', 'm1', 'w1', 'r1d', 'from_52wh', 'high_52w', 'up_52wl', 'peg', 'pe', 'pb',
                                 'sales_qoq', 'op_qoq', 'eps_qoq', 'np_qoq', 'sales_yoy', 'op_yoy', 'np_yoy', 'eps_yoy',
                                 'opm_q', 'opm_pq', 'opm_pyq', 'public_hold', 'float', 'rec_sales', 'ath', 'volume'])
    need = [f for f in ('name', 'w1') if f not in cols]
    if need: raise SystemExit(f'--screener {path}: could not find column(s) {need}. Headers seen: {headers}')
    # screener.in has both "Down from 52w high" (positive % below) and "From 52w high" (0..1 ratio); prefer the % one
    down_i = next((i for i, h in enumerate(headers) if norm(h) == 'down from 52w high'), None)
    ratio_i = next((i for i, h in enumerate(headers) if norm(h) == 'from 52w high'), None)
    out = []
    for r in rows:
        name = (cell(r, cols, 'name') or '').strip()
        if not name: continue
        mcap = num(cell(r, cols, 'mcap'))
        if min_mcap and mcap is not None and mcap < min_mcap: continue
        item = OrderedDict(name=name)
        code = clean_code(cell(r, cols, 'code'))
        if code: item['code'] = code
        bse = (cell(r, cols, 'bse') or '').strip()
        if bse and re.fullmatch(r'\d+(\.0)?', bse): item['bse'] = bse.split('.')[0]
        for k in ('isin', 'group', 'industry'):
            v = (cell(r, cols, k) or '').strip()
            if v: item[k] = v
        price = num(cell(r, cols, 'price'))
        if price is not None: item['price'] = rnd(price)
        if mcap is not None: item['mcap'] = rnd(mcap, 0)
        for k, out_k, d in (('dma50', 'dma50', 2), ('dma200', 'dma200', 2), ('w1', 'r1w', 2), ('m1', 'r1m', 2), ('r3m', 'r3m', 2), ('r6m', 'r6m', 2), ('y1', 'r1y', 2), ('r1d', 'r1d', 2),
                            ('up_52wl', 'up_52wl', 2), ('peg', 'peg', 2), ('pe', 'pe', 2), ('pb', 'pb', 2),
                            ('sales_qoq', 'sales_qoq', 2), ('op_qoq', 'op_qoq', 2), ('eps_qoq', 'eps_qoq', 2), ('np_qoq', 'np_qoq', 2),
                            ('sales_yoy', 'sales_yoy', 2), ('op_yoy', 'op_yoy', 2), ('np_yoy', 'np_yoy', 2), ('eps_yoy', 'eps_yoy', 2),
                            ('opm_q', 'opm_q', 2), ('opm_pq', 'opm_pq', 2), ('opm_pyq', 'opm_pyq', 2), ('public_hold', 'public_hold', 2), ('float', 'float', 0), ('rec_sales', 'rec_sales', 2), ('ath', 'ath', 2), ('volume', 'volume', 0)):
            v = num(cell(r, cols, k))
            if v is not None: item[out_k] = rnd(v, d)
        f52 = None
        if down_i is not None: v = num(r[down_i] if down_i < len(r) else None); f52 = -abs(v) if v is not None else None
        if f52 is None and ratio_i is not None:
            v = num(r[ratio_i] if ratio_i < len(r) else None); f52 = rnd((v - 1) * 100) if v is not None else None
        if f52 is None and price is not None and num(cell(r, cols, 'high_52w')):
            f52 = rnd((price / num(cell(r, cols, 'high_52w')) - 1) * 100)
        if f52 is not None: item['from_52wh'] = rnd(f52)
        if price is not None and item.get('ath'):
            item['near_ath'] = bool(price / item['ath'] >= 0.95)
        out.append(item)
    if not out: raise SystemExit(f'--screener {path}: no usable rows')
    return out, headers

def build_stage2(path):
    headers, rows = read_table(path)
    cols = map_columns(headers, ['tv_code', 'industry', 'rs_pct', 'mcap_cr', 'vol_cr', 'ret', 'weeks', 'since', 'asm', 'asm_days', 'status'])
    if 'tv_code' not in cols: raise SystemExit(f'--stage2 {path}: could not find the code column. Headers seen: {headers}')
    out = []
    for r in rows:
        code = (cell(r, cols, 'tv_code') or '').strip().upper()
        if not code: continue
        item = OrderedDict(code=code)
        ind = (cell(r, cols, 'industry') or '').strip()
        if ind: item['industry'] = ind
        for k, d in (('rs_pct', 2), ('mcap_cr', 2), ('vol_cr', 2), ('ret', 2)):
            v = num(cell(r, cols, k))
            if v is not None: item[k if k != 'mcap_cr' else 'mcap'] = rnd(v, d)
        w = num(cell(r, cols, 'weeks'))
        if w is not None: item['weeks'] = int(w)
        since = excel_date(cell(r, cols, 'since'))
        if since: item['since'] = since
        asm = (cell(r, cols, 'asm') or '').strip()
        if asm: item['asm'] = asm
        ad = num(cell(r, cols, 'asm_days'))
        if ad is not None: item['asm_days'] = int(ad)
        status = (cell(r, cols, 'status') or '').strip()
        if status: item['status'] = status
        out.append(item)
    if not out: raise SystemExit(f'--stage2 {path}: no usable rows')
    return out, headers

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', required=True, help='week-ending (Friday) ISO date the data represents, e.g. 2026-08-21')
    ap.add_argument('--screener', help='screener.in export (.csv/.xlsx)')
    ap.add_argument('--stage2', help='Stage 2 list (.xlsx/.csv)')
    ap.add_argument('--screen-name', default=None, help='label for the screener universe, e.g. "Near 52W high — weekly gainers"')
    ap.add_argument('--min-mcap', type=float, default=0, help='drop screener rows below this mcap (₹ Cr)')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'scans'))
    a = ap.parse_args()
    try: dt.date.fromisoformat(a.date)
    except ValueError: raise SystemExit('--date must be YYYY-MM-DD')
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f'{a.date}.json')
    if os.path.exists(path):
        with io.open(path, encoding='utf-8') as fh: d = json.load(fh, object_pairs_hook=OrderedDict)
        print(f'updating {path}')
    else:
        d = OrderedDict([('schema', SCHEMA), ('date', a.date), ('sources', OrderedDict())]); print(f'creating {path}')
    d['schema'] = SCHEMA; d['date'] = a.date
    d.setdefault('sources', OrderedDict())
    done = []
    if a.screener:
        rows, headers = build_universe(a.screener, a.min_mcap)
        d['universe'] = rows
        d['sources']['screener'] = os.path.basename(a.screener)
        d['screen'] = OrderedDict(name=a.screen_name or d.get('screen', {}).get('name') or 'Weekly screener scan', columns=len(headers),
                                  has=[k for k in ('dma50', 'dma200', 'r3m', 'r6m', 'r1y', 'ath', 'volume') if any(k in r for r in rows)])
        done.append(f'universe: {len(rows)} rows ({", ".join(d["screen"]["has"]) or "basic columns"})')
    if a.stage2:
        rows, headers = build_stage2(a.stage2)
        d['stage2'] = rows
        d['sources']['stage2'] = os.path.basename(a.stage2)
        done.append(f'stage2: {len(rows)} rows')
    if not done: print('nothing to build — pass --screener and/or --stage2')
    with io.open(path, 'w', encoding='utf-8') as fh: json.dump(d, fh, ensure_ascii=False, separators=(',', ':'))
    print('wrote', path, f'({os.path.getsize(path):,} bytes)')
    for line in done: print('  +', line)
    ipath = os.path.join(a.out, 'index.json')
    idx = OrderedDict(updated=dt.date.today().isoformat(), scans=[])
    if os.path.exists(ipath):
        with io.open(ipath, encoding='utf-8') as fh: idx = json.load(fh, object_pairs_hook=OrderedDict)
    scans = [s for s in idx.get('scans', []) if s.get('file') != f'{a.date}.json']
    scans.append(OrderedDict(date=a.date, file=f'{a.date}.json', universe=len(d.get('universe', [])), stage2=len(d.get('stage2', []))))
    scans.sort(key=lambda s: s['date'], reverse=True)
    idx['updated'] = dt.date.today().isoformat(); idx['scans'] = scans
    with io.open(ipath, 'w', encoding='utf-8') as fh: json.dump(idx, fh, ensure_ascii=False, indent=2)
    print('updated', ipath, f'({len(scans)} weeks)')

if __name__ == '__main__':
    main()
