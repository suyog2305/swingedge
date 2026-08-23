#!/usr/bin/env python3
"""
build_weekly.py — turn weekly screener exports into a SwingEdge weekly-report JSON file.

    python tools/build_weekly.py --week-ending 2026-08-21 \
        --india exports/screener_india.xlsx \
        --us exports/us_stocks.csv --us-etf exports/us_etfs.csv \
        --etf exports/global_etf.csv --fiidii exports/fii_dii.csv \
        --indices exports/nse_indices.csv

Every input is optional — pass whichever exports you have. The script:
  * reads .csv or .xlsx (first sheet; no third-party packages needed),
  * matches columns by name using a tolerant alias table (see ALIASES below),
  * writes/updates  library/weekly/<week-ending>.json  (schema swingedge-weekly/1),
  * PRESERVES anything already in that file that it does not compute
    (lead, headline, ticker, notes, derivatives/fx/commodities/debt/summary …),
  * and upserts the entry in  library/weekly/index.json.

Everything stock-level (top performers, near-52W-high, breadth, crossovers, rolling
comparison, ETF heatmap) is computed by the app from the rows written here — so the
narrative is the only thing left to author by hand (or by your Cowork/Claude pipeline).
"""
import argparse, csv, datetime as dt, io, json, os, re, sys, zipfile
from collections import OrderedDict
from xml.etree import ElementTree as ET

SCHEMA = 'swingedge-weekly/1'

# ----------------------------------------------------------------------------- aliases
# canonical field -> accepted column headings (matched case/space/punctuation-insensitively)
ALIASES = {
    # India / generic stock universe (screener.in "Export to Excel" headings + common variants)
    'name':        ['name', 'company name', 'company', 'stock', 'stock name', 'security name'],
    'code':        ['nse code', 'nsecode', 'nse symbol', 'symbol', 'code', 'ticker', 'nse'],
    'bse':         ['bse code', 'bsecode', 'scrip code'],
    'industry':    ['industry', 'sector', 'sector name', 'industry name'],
    'price':       ['current price', 'price', 'cmp', 'close', 'ltp', 'last price'],
    'mcap':        ['market capitalization', 'market cap', 'mcap', 'market cap (cr)', 'marketcap', 'market capitalisation', 'mcap (₹cr)', 'market cap ₹cr'],
    'w1':          ['return over 1week', 'return over 1 week', '1 week return', '1w return', '1-week', '1w', 'price change 1w', 'weekly return', 'return 1w', '1 week', 'week return', 'perf week', '1-wk'],
    'm1':          ['return over 1month', 'return over 1 month', '1 month return', '1m return', '1-month', '1m', 'price change 1m', 'return 1m', '1 month', 'perf month', '1-mo'],
    'y1':          ['return over 1year', 'return over 1 year', '1 year return', '1y return', '1-year', '1y', 'price change 1y', 'return 1y', '1 year', 'perf year'],
    'from_52wh':   ['down from 52w high', 'down from 52 week high', 'from 52w high', 'down fr 52wh', 'distance from 52w high', '% from 52w high', 'down from 52wh'],
    'high_52w':    ['high price 52w', '52w high', '52 week high', 'high 52w', '52wh', '52-week high'],
    'ath':         ['high price all time', 'all time high', 'ath', 'all-time high', 'high all time'],
    'dma50':       ['dma 50', 'dma50', '50 dma', '50dma', 'sma 50', '50 day moving average', '50-dma'],
    'dma200':      ['dma 200', 'dma200', '200 dma', '200dma', 'sma 200', '200 day moving average', '200-dma'],
    'above_dma50': ['above dma50', 'above dma 50', 'above 50 dma'],
    'above_dma200':['above dma200', 'above dma 200', 'above 200 dma'],
    # US ETFs
    'assets':      ['assets', 'aum', 'total assets', 'net assets', 'fund assets'],
    # Global ETF export: Symbol, Company Name, Region, Price Change 1W/1M/1Y, Market Cap
    'region':      ['region', 'country', 'geography', 'area'],
    # FII / DII daily file
    'date':        ['date', 'trade date', 'day'],
    'fii_buy':     ['fii buy', 'fii buy value', 'fii gross purchase', 'fii purchase', 'fpi buy', 'fii/fpi buy'],
    'fii_sell':    ['fii sell', 'fii sell value', 'fii gross sales', 'fii sales', 'fpi sell', 'fii/fpi sell'],
    'fii_net':     ['fii net', 'fii net value', 'fii net purchase / sales', 'fii net purchase/sales', 'fpi net', 'fii/fpi net'],
    'dii_buy':     ['dii buy', 'dii buy value', 'dii gross purchase', 'dii purchase'],
    'dii_sell':    ['dii sell', 'dii sell value', 'dii gross sales', 'dii sales'],
    'dii_net':     ['dii net', 'dii net value', 'dii net purchase / sales', 'dii net purchase/sales'],
    # Indices export
    'index':       ['index', 'index name', 'indices', 'name'],
    'vs_52wh':     ['vs 52w high', 'vs 52 week high', 'from 52w high', 'down from 52w high', '% from 52w high', 'vs 52wh'],
}

def norm(s):
    return re.sub(r'[^a-z0-9%]+', ' ', str(s or '').lower().replace('₹', '').replace('$', '')).strip()

ALIAS_LOOKUP = {f: {norm(a) for a in al} for f, al in ALIASES.items()}

def map_columns(headers, wanted):
    """Return {canonical: column-index} for the canonical fields we can find."""
    out = {}
    nh = [norm(h) for h in headers]
    for f in wanted:
        for i, h in enumerate(nh):
            if h in ALIAS_LOOKUP[f] and f not in out and i not in out.values():
                out[f] = i
                break
    return out

# ----------------------------------------------------------------------------- readers
def read_table(path):
    """-> (headers, rows) for .csv / .tsv / .xlsx (first sheet)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.xlsx', '.xlsm'):
        return read_xlsx(path)
    with io.open(path, encoding='utf-8-sig', newline='') as fh:
        sample = fh.read(4096); fh.seek(0)
        dialect = csv.excel_tab if sample.count('\t') > sample.count(',') else csv.excel
        rows = [r for r in csv.reader(fh, dialect)]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows: raise SystemExit(f'{path}: empty file')
    # screener.in CSVs sometimes carry a title row above the header — find the first row that looks like a header
    for i, r in enumerate(rows[:5]):
        if sum(1 for c in r if c.strip()) >= 3 and not any(re.fullmatch(r'-?[\d,.]+%?', c.strip()) for c in r if c.strip()):
            return [c.strip() for c in r], rows[i + 1:]
    return [c.strip() for c in rows[0]], rows[1:]

def read_xlsx(path):
    z = zipfile.ZipFile(path)
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns):
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % ns['m'])))
    # first sheet per workbook order
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    first = wb.find('m:sheets', ns).find('m:sheet', ns)
    rid = first.get('{%s}id' % ns['r'])
    target = next(r.get('Target') for r in rels if r.get('Id') == rid)
    sheet_path = target if target.startswith('xl/') else 'xl/' + target.lstrip('/')
    grid = {}
    for c in ET.fromstring(z.read(sheet_path)).iter('{%s}c' % ns['m']):
        ref = c.get('r'); m = re.match(r'([A-Z]+)(\d+)', ref)
        col = 0
        for ch in m.group(1): col = col * 26 + (ord(ch) - 64)
        row = int(m.group(2))
        t = c.get('t'); v = c.find('m:v', ns)
        if t == 's' and v is not None: val = shared[int(v.text)]
        elif t == 'inlineStr': val = ''.join(x.text or '' for x in c.iter('{%s}t' % ns['m']))
        else: val = v.text if v is not None else ''
        grid.setdefault(row, {})[col] = val
    rows = []
    for r in sorted(grid):
        width = max(grid[r]) if grid[r] else 0
        rows.append([str(grid[r].get(i, '') or '') for i in range(1, width + 1)])
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows: raise SystemExit(f'{path}: empty sheet')
    for i, r in enumerate(rows[:5]):
        if sum(1 for c in r if c.strip()) >= 3 and not any(re.fullmatch(r'-?[\d,.]+%?', c.strip()) for c in r if c.strip()):
            return [c.strip() for c in r], rows[i + 1:]
    return [c.strip() for c in rows[0]], rows[1:]

# ----------------------------------------------------------------------------- parsing helpers
def num(v):
    if v is None: return None
    s = str(v).strip().replace(',', '').replace('₹', '').replace('$', '').replace('%', '')
    if s in ('', '-', '—', 'nan', 'NaN', 'None', 'null', 'N/A', 'n/a'): return None
    mult = 1
    if s[-1:] in ('B', 'b'): mult, s = 1e9, s[:-1]
    elif s[-1:] in ('M', 'm'): mult, s = 1e6, s[:-1]
    elif s[-1:] in ('K', 'k'): mult, s = 1e3, s[:-1]
    try: return float(s) * mult
    except ValueError: return None

def rnd(v, d=2):
    return None if v is None else round(v, d)

def yesno(v):
    s = str(v or '').strip().lower()
    if s in ('yes', 'y', 'true', '1'): return True
    if s in ('no', 'n', 'false', '0'): return False
    return None

DATE_FORMATS = ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%d %b %Y', '%d-%b-%Y', '%d-%b-%y', '%d %B %Y', '%b %d, %Y', '%m/%d/%Y', '%Y/%m/%d', '%d.%m.%Y']
def parse_date(v):
    s = str(v or '').strip()
    if not s: return None
    if re.fullmatch(r'\d{5}', s):  # Excel serial
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(s))).isoformat()
    s = re.sub(r'(\d)(st|nd|rd|th)\b', r'\1', s)
    for f in DATE_FORMATS:
        try: return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError: pass
    m = re.match(r'(\d{4}-\d{2}-\d{2})', s)
    return m.group(1) if m else None

CODE_RE = re.compile(r'[A-Z0-9][A-Z0-9&\-_.]{0,15}')
def clean_code(v):
    s = str(v or '').strip().upper()
    if not s or re.fullmatch(r'\d+(\.\d+)?', s): return None   # numeric BSE scrip codes are not NSE codes
    return s if CODE_RE.fullmatch(s) else None

def pct_below_high(price, high):
    if price is None or not high: return None
    return rnd((price / high - 1) * 100)

def cell(row, cols, f):
    i = cols.get(f)
    return row[i] if i is not None and i < len(row) else None

# ----------------------------------------------------------------------------- builders
def build_india(path, min_mcap):
    headers, rows = read_table(path)
    cols = map_columns(headers, ['name', 'code', 'bse', 'industry', 'price', 'mcap', 'w1', 'm1', 'y1', 'from_52wh', 'high_52w', 'ath', 'dma50', 'dma200', 'above_dma50', 'above_dma200'])
    need = [f for f in ('name', 'w1') if f not in cols]
    if need: raise SystemExit(f'--india {path}: could not find column(s) {need}. Headers seen: {headers}')
    out = []
    for r in rows:
        name = (cell(r, cols, 'name') or '').strip()
        if not name: continue
        mcap = num(cell(r, cols, 'mcap'))
        if min_mcap and mcap is not None and mcap < min_mcap: continue
        price = num(cell(r, cols, 'price'))
        item = OrderedDict(name=name)
        code = clean_code(cell(r, cols, 'code'))
        if code: item['code'] = code
        ind = (cell(r, cols, 'industry') or '').strip()
        if ind: item['industry'] = ind
        if mcap is not None: item['mcap'] = rnd(mcap, 0)
        item['w1'] = rnd(num(cell(r, cols, 'w1')))
        for k in ('m1', 'y1'):
            v = num(cell(r, cols, k))
            if v is not None: item[k] = rnd(v)
        f52 = num(cell(r, cols, 'from_52wh'))
        if f52 is not None:
            # screener's "Down from 52w high" is a positive % below the high; a 0..1 ratio means price/high
            f52 = (f52 - 1) * 100 if 0 <= f52 <= 1.5 and cols.get('from_52wh') is not None and 'from' in norm(headers[cols['from_52wh']]) and 'down' not in norm(headers[cols['from_52wh']]) else -abs(f52)
            item['from_52wh'] = rnd(f52)
        elif price is not None and num(cell(r, cols, 'high_52w')):
            item['from_52wh'] = pct_below_high(price, num(cell(r, cols, 'high_52w')))
        ath = num(cell(r, cols, 'ath'))
        if price is not None and ath:
            item['near_ath'] = bool(price / ath >= 0.95)
        for k, flagk in (('dma50', 'above_dma50'), ('dma200', 'above_dma200')):
            flag = yesno(cell(r, cols, flagk))
            dma = num(cell(r, cols, k))
            if flag is None and price is not None and dma:
                flag = price > dma
            if flag is not None: item[flagk] = flag
        out.append(item)
    if not out: raise SystemExit(f'--india {path}: no usable rows')
    src = f'{os.path.basename(path)} ({len(out)} rows' + (f', mcap ≥ {min_mcap:,.0f} Cr' if min_mcap else '') + ')'
    return OrderedDict(source=src, min_mcap=min_mcap, rows=out)

def build_us(path):
    headers, rows = read_table(path)
    cols = map_columns(headers, ['name', 'code', 'industry', 'mcap', 'w1', 'm1', 'y1'])
    need = [f for f in ('name', 'code', 'w1') if f not in cols]
    if need: raise SystemExit(f'--us {path}: could not find column(s) {need}. Headers seen: {headers}')
    out = []
    for r in rows:
        name, code = (cell(r, cols, 'name') or '').strip(), (cell(r, cols, 'code') or '').strip().upper()
        if not name or not code: continue
        item = OrderedDict(name=name, code=code)
        ind = (cell(r, cols, 'industry') or '').strip()
        if ind: item['industry'] = ind
        mcap = num(cell(r, cols, 'mcap'))
        if mcap is not None: item['mcap'] = rnd(mcap, 0)
        item['w1'] = rnd(num(cell(r, cols, 'w1')))
        for k in ('m1', 'y1'):
            v = num(cell(r, cols, k))
            if v is not None: item[k] = rnd(v)
        if item['w1'] is not None: out.append(item)
    return OrderedDict(source=f'{os.path.basename(path)} ({len(out)} rows)', rows=out)

def build_us_etf(path):
    headers, rows = read_table(path)
    cols = map_columns(headers, ['name', 'code', 'assets', 'w1', 'm1', 'y1'])
    need = [f for f in ('name', 'code', 'w1') if f not in cols]
    if need: raise SystemExit(f'--us-etf {path}: could not find column(s) {need}. Headers seen: {headers}')
    out = []
    for r in rows:
        name, code = (cell(r, cols, 'name') or '').strip(), (cell(r, cols, 'code') or '').strip().upper()
        if not name or not code: continue
        item = OrderedDict(name=name, code=code)
        a = (cell(r, cols, 'assets') or '').strip()
        if a: item['assets'] = a
        item['w1'] = rnd(num(cell(r, cols, 'w1')))
        v = num(cell(r, cols, 'm1'))
        if v is not None: item['m1'] = rnd(v)
        if item['w1'] is not None: out.append(item)
    return OrderedDict(source=f'{os.path.basename(path)} ({len(out)} rows, non-leveraged)', rows=out)

def build_global_etf(path):
    headers, rows = read_table(path)
    cols = map_columns(headers, ['code', 'name', 'region', 'w1', 'm1', 'y1', 'mcap'])
    need = [f for f in ('name', 'w1') if f not in cols]
    if need: raise SystemExit(f'--etf {path}: could not find column(s) {need}. Headers seen: {headers}')
    out = []
    for r in rows:
        name = (cell(r, cols, 'name') or '').strip()
        if not name: continue
        item = OrderedDict(name=name)
        code = (cell(r, cols, 'code') or '').strip().upper()
        if code: item['code'] = code
        item['region'] = (cell(r, cols, 'region') or 'Other').strip() or 'Other'
        item['w1'] = rnd(num(cell(r, cols, 'w1')))
        for k in ('m1', 'y1'):
            v = num(cell(r, cols, k))
            if v is not None: item[k] = rnd(v)
        mcap = num(cell(r, cols, 'mcap'))
        if mcap is not None: item['mcap'] = rnd(mcap, 0)
        if item['w1'] is not None: out.append(item)
    return out

def build_fiidii(path, week_ending):
    headers, rows = read_table(path)
    cols = map_columns(headers, ['date', 'fii_buy', 'fii_sell', 'fii_net', 'dii_buy', 'dii_sell', 'dii_net'])
    if 'date' not in cols or not ({'fii_net', 'fii_buy'} & set(cols)):
        raise SystemExit(f'--fiidii {path}: need a Date column and FII/DII figures. Headers seen: {headers}')
    we = dt.date.fromisoformat(week_ending); ws = we - dt.timedelta(days=6)
    days = []
    for r in rows:
        d = parse_date(cell(r, cols, 'date'))
        if not d: continue
        dd = dt.date.fromisoformat(d)
        if not (ws <= dd <= we): continue       # only this week's sessions go in this file
        item = OrderedDict(date=d)
        for k in ('fii_buy', 'fii_sell', 'fii_net', 'dii_buy', 'dii_sell', 'dii_net'):
            v = num(cell(r, cols, k))
            if v is not None: item[k] = rnd(v)
        if 'fii_net' not in item and 'fii_buy' in item and 'fii_sell' in item: item['fii_net'] = rnd(item['fii_buy'] - item['fii_sell'])
        if 'dii_net' not in item and 'dii_buy' in item and 'dii_sell' in item: item['dii_net'] = rnd(item['dii_buy'] - item['dii_sell'])
        days.append(item)
    days.sort(key=lambda x: x['date'])
    return days

BROAD_RE = re.compile(r'^nifty\s*(50|next\s*50|100|200|500|total market|midcap\s*\d+|smallcap\s*\d+|microcap\s*\d+|largemidcap\s*250|midsmallcap\s*400)$', re.I)
def build_indices(path):
    headers, rows = read_table(path)
    cols = map_columns(headers, ['index', 'w1', 'm1', 'y1', 'vs_52wh'])
    need = [f for f in ('index', 'w1') if f not in cols]
    if need: raise SystemExit(f'--indices {path}: could not find column(s) {need}. Headers seen: {headers}')
    broad, sector = [], []
    for r in rows:
        name = (cell(r, cols, 'index') or '').strip()
        if not name: continue
        item = OrderedDict(name=name, w1=rnd(num(cell(r, cols, 'w1'))), m1=rnd(num(cell(r, cols, 'm1'))), y1=rnd(num(cell(r, cols, 'y1'))))
        v = num(cell(r, cols, 'vs_52wh'))
        if v is not None: item['vs_52wh'] = rnd(-abs(v))
        (broad if BROAD_RE.match(name) else sector).append(item)
    return broad, sector

# ----------------------------------------------------------------------------- main
def week_label(week_ending):
    we = dt.date.fromisoformat(week_ending); ws = we - dt.timedelta(days=4)
    if ws.month == we.month: return f'{ws.day}–{we.day} {we.strftime("%b %Y")}'
    return f'{ws.day} {ws.strftime("%b")} – {we.day} {we.strftime("%b %Y")}'

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--week-ending', required=True, help='ISO date of the last trading day, e.g. 2026-08-21')
    ap.add_argument('--label', help='display label, default derived e.g. "17–21 Aug 2026"')
    ap.add_argument('--india', help='India stock universe export (screener.in .xlsx/.csv)')
    ap.add_argument('--us', help='US stocks export (.xlsx/.csv)')
    ap.add_argument('--us-etf', dest='us_etf', help='US ETF export (.xlsx/.csv)')
    ap.add_argument('--etf', help='Global country/region ETF export (.xlsx/.csv)')
    ap.add_argument('--fiidii', help='daily FII/DII cash file (.xlsx/.csv); only this week\'s sessions are taken')
    ap.add_argument('--indices', help='NSE indices returns export (.xlsx/.csv)')
    ap.add_argument('--min-mcap', type=float, default=1000, help='drop India rows below this mcap (₹ Cr); 0 = keep all')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'library', 'weekly'), help='output folder (default: library/weekly)')
    ap.add_argument('--title', default='Weekly Institutional Flows & Markets Report')
    a = ap.parse_args()

    try: dt.date.fromisoformat(a.week_ending)
    except ValueError: raise SystemExit('--week-ending must be YYYY-MM-DD')
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f'{a.week_ending}.json')

    if os.path.exists(path):
        with io.open(path, encoding='utf-8') as fh: d = json.load(fh, object_pairs_hook=OrderedDict)
        print(f'updating {path} (authored sections preserved)')
    else:
        d = OrderedDict([('schema', SCHEMA), ('week_ending', a.week_ending), ('week_label', a.label or week_label(a.week_ending)),
                         ('title', a.title), ('strap', 'FII/DII · Derivatives · FX · Commodities · Equities · Debt'),
                         ('tags', ['Weekly Digest', 'Institutional Flows', 'India + US Coverage']),
                         ('lead', ''), ('headline', []), ('ticker', []),
                         ('macros', OrderedDict()), ('india', OrderedDict()), ('us', OrderedDict()),
                         ('comparison', OrderedDict(window=4)), ('etf', OrderedDict())])
        print(f'creating {path}')
    d['schema'] = SCHEMA; d['week_ending'] = a.week_ending
    if a.label: d['week_label'] = a.label
    for k in ('macros', 'india', 'us', 'comparison', 'etf'):
        d.setdefault(k, OrderedDict())

    done = []
    if a.india:
        d['india']['universe'] = build_india(a.india, a.min_mcap)
        d['india'].setdefault('top_performers', OrderedDict(limit=50, bottom_limit=20))
        d['india'].setdefault('near_high', OrderedDict(threshold=5.5, min_mcap=5000, require_code=True))
        d['india'].setdefault('breadth', OrderedDict())
        done.append(f"india universe: {len(d['india']['universe']['rows'])} rows")
    if a.us:
        d['us']['universe'] = build_us(a.us)
        d['us'].setdefault('top_performers', OrderedDict(limit=50, bottom_limit=15))
        done.append(f"us universe: {len(d['us']['universe']['rows'])} rows")
    if a.us_etf:
        d['us']['etf_universe'] = build_us_etf(a.us_etf)
        done.append(f"us etf universe: {len(d['us']['etf_universe']['rows'])} rows")
    if a.etf:
        d['etf']['rows'] = build_global_etf(a.etf)
        done.append(f"global etf: {len(d['etf']['rows'])} rows")
    if a.fiidii:
        days = build_fiidii(a.fiidii, a.week_ending)
        fd = d['macros'].setdefault('fii_dii', OrderedDict())
        fd['days'] = days
        done.append(f'fii/dii: {len(days)} sessions in week')
        if not days: print('  ! no FII/DII sessions fell inside the week — check the Date column format')
    if a.indices:
        broad, sector = build_indices(a.indices)
        ix = d['india'].setdefault('indices', OrderedDict())
        ix['broad'], ix['sector'] = broad, sector
        done.append(f'indices: {len(broad)} broad, {len(sector)} sector')
    if not done:
        print('nothing to build — pass at least one export (see --help)')

    with io.open(path, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, ensure_ascii=False, separators=(',', ':'))
    print('wrote', path, f'({os.path.getsize(path):,} bytes)')
    for line in done: print('  +', line)

    # upsert index.json
    ipath = os.path.join(a.out, 'index.json')
    idx = OrderedDict(updated=dt.date.today().isoformat(), weeks=[])
    if os.path.exists(ipath):
        with io.open(ipath, encoding='utf-8') as fh: idx = json.load(fh, object_pairs_hook=OrderedDict)
    weeks = [w for w in idx.get('weeks', []) if w.get('file') != f'{a.week_ending}.json']
    weeks.append(OrderedDict(week_ending=a.week_ending, label=d.get('week_label', a.week_ending), file=f'{a.week_ending}.json', title=d.get('title', a.title)))
    weeks.sort(key=lambda w: w.get('week_ending', ''), reverse=True)
    idx['updated'] = dt.date.today().isoformat(); idx['weeks'] = weeks
    with io.open(ipath, 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2)
    print('updated', ipath, f'({len(weeks)} weeks)')

    missing = [k for k in ('lead', 'headline', 'ticker') if not d.get(k)]
    auth = [k for k in ('derivatives', 'fx', 'commodities', 'debt', 'summary') if k not in d['macros']]
    if missing or auth:
        print('\nStill to author by hand (or via your Claude/Cowork pipeline) in', os.path.basename(path) + ':')
        if missing: print('  -', ', '.join(missing))
        if auth: print('  - macros.' + ', macros.'.join(auth))
        print('  see library/weekly/README.md for the field reference')

if __name__ == '__main__':
    main()
