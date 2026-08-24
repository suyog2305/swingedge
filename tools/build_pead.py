#!/usr/bin/env python3
"""
build_pead.py — turn a quarterly "good earnings / PEAD" export into a SwingEdge PEAD file.

    python tools/build_pead.py --file "exports/16 Aug 2026 _ Data.xlsx"
    python tools/build_pead.py --file q2.xlsx --quarter "Q2 FY27" --reported 2026-11-15

Input: one row per company with an earnings/PEAD classification. Columns are matched by name
(Industry, Company Name, YoY/QoQ Sales/Op Profit/EPS/PAT Growth, Market Cap, PEG Ratio,
PEAD Classification). The quarter is read from the sheet name (e.g. GoodEarningsQ1FY27 -> "Q1 FY27")
unless you pass --quarter.

Output: data/pead/<quarter-slug>.json (schema swingedge-pead/1) + data/pead/index.json (upserted).
Each row carries a normalized name `key` so the app can cross-reference PEAD names against the
weekly scan universe (name -> NSE code) and the Stage 2 list (code) entirely in the browser.
Quarter-over-quarter tracking (repeat performers, tier migration) is computed in-app from the
set of published quarter files — so it fills in as you upload each quarter.
"""
import argparse, datetime as dt, io, json, os, re, sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_weekly import read_table, map_columns, num, rnd, norm, ALIASES, ALIAS_LOOKUP  # noqa: E402

SCHEMA = 'swingedge-pead/1'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PEAD_ALIASES = {
    'industry':   ['industry', 'sector', 'industry name'],
    'name':       ['company name', 'name', 'company', 'stock'],
    'sales_yoy':  ['yoy sales growth', 'sales growth yoy', 'sales yoy'],
    'sales_qoq':  ['qoq sales growth', 'sales growth qoq', 'sales qoq'],
    'op_yoy':     ['yoy op profit growth', 'yoy operating profit growth', 'op profit growth yoy'],
    'op_qoq':     ['qoq op profit growth', 'qoq operating profit growth', 'op profit growth qoq'],
    'eps_yoy':    ['yoy eps growth', 'eps growth yoy', 'eps yoy'],
    'eps_qoq':    ['qoq eps growth', 'eps growth qoq', 'eps qoq'],
    'pat_yoy':    ['yoy pat growth', 'pat growth yoy', 'pat yoy', 'yoy net profit growth', 'net profit yoy growth'],
    'pat_qoq':    ['qoq pat growth', 'pat growth qoq', 'pat qoq', 'qoq net profit growth', 'net profit qoq growth'],
    'mcap':       ['market cap', 'market capitalization', 'mcap', 'market cap (in cr.)', 'market capitalisation'],
    'peg':        ['peg ratio', 'peg'],
    'pead':       ['pead classification', 'pead', 'pead class', 'classification'],
}
for k, v in PEAD_ALIASES.items():
    ALIASES.setdefault(k, []); ALIASES[k] = list(dict.fromkeys(ALIASES[k] + v)); ALIAS_LOOKUP[k] = {norm(a) for a in ALIASES[k]}

# --- name normalization (mirrored in the app's JS peadKey()) ---
_SUFFIX = re.compile(r'\b(ltd|limited|the|company|co|corporation|corp|inc|plc|india|indian|of|and)\b')
def name_key(n):
    n = (n or '').lower()
    n = re.sub(r'&', ' and ', n)
    n = re.sub(r'[^a-z0-9 ]', ' ', n)
    n = _SUFFIX.sub(' ', n)
    n = re.sub(r'\binds?\b', 'industries', n)
    return re.sub(r'\s+', ' ', n).strip()

TIER = {'strong pead': 3, 'moderate pead': 2, 'weak pead': 1, 'no pead': 0}
def tier_of(label):
    l = (label or '').strip().lower()
    for k, v in TIER.items():
        if k in l: return v
    if 'strong' in l: return 3
    if 'moder' in l: return 2
    if 'weak' in l: return 1
    return 0

def cell(row, cols, f):
    i = cols.get(f)
    return row[i] if i is not None and i < len(row) else None

def parse_quarter(sheet_or_arg):
    m = re.search(r'Q\s*([1-4]).*?FY\s*(\d{2,4})', sheet_or_arg or '', re.I)
    if not m: return None
    q, fy = int(m.group(1)), int(m.group(2))
    if fy < 100: fy += 2000
    return f'Q{q} FY{fy % 100:02d}', fy, q

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--file', required=True, help='quarterly PEAD export (.xlsx/.csv)')
    ap.add_argument('--quarter', help='e.g. "Q1 FY27"; default derived from the sheet name')
    ap.add_argument('--reported', help='ISO date the results were compiled/reported (for ordering); default today')
    ap.add_argument('--out', default=os.path.join(ROOT, 'data', 'pead'))
    a = ap.parse_args()

    headers, rows, sheet = read_table_with_sheet(a.file)
    cols = map_columns(headers, list(PEAD_ALIASES.keys()))
    for need in ('name', 'pead'):
        if need not in cols:
            raise SystemExit(f'{a.file}: could not find the {need!r} column. Headers seen: {headers}')

    qinfo = None
    if a.quarter: qinfo = parse_quarter(a.quarter) or (a.quarter, 0, 0)
    if not qinfo: qinfo = parse_quarter(sheet)
    if not qinfo: qinfo = parse_quarter(os.path.basename(a.file))
    if not qinfo:
        raise SystemExit('could not determine the quarter — pass --quarter "Q1 FY27".')
    quarter, fy, q = qinfo
    sortkey = fy * 10 + q if fy else 0
    slug = re.sub(r'[^a-z0-9]+', '-', quarter.lower()).strip('-')
    reported = a.reported or dt.date.today().isoformat()
    try: dt.date.fromisoformat(reported)
    except ValueError: raise SystemExit('--reported must be YYYY-MM-DD')

    out_rows = []
    for r in rows:
        name = (cell(r, cols, 'name') or '').strip()
        if not name: continue
        item = OrderedDict(name=name, key=name_key(name))
        ind = (cell(r, cols, 'industry') or '').strip()
        if ind: item['industry'] = ind
        item['pead'] = (cell(r, cols, 'pead') or '').strip()
        item['tier'] = tier_of(item['pead'])
        for f, out_f in (('sales_yoy', 'sales_yoy'), ('sales_qoq', 'sales_qoq'), ('op_yoy', 'op_yoy'), ('op_qoq', 'op_qoq'),
                         ('eps_yoy', 'eps_yoy'), ('eps_qoq', 'eps_qoq'), ('pat_yoy', 'pat_yoy'), ('pat_qoq', 'pat_qoq'), ('mcap', 'mcap'), ('peg', 'peg')):
            v = num(cell(r, cols, f))
            if v is not None: item[out_f] = rnd(v, 0 if out_f == 'mcap' else 2)
        out_rows.append(item)
    if not out_rows:
        raise SystemExit(f'{a.file}: no usable rows')

    from collections import Counter
    dist = Counter(x['tier'] for x in out_rows)
    doc = OrderedDict([
        ('schema', SCHEMA), ('quarter', quarter), ('fy', fy), ('q', q), ('sortkey', sortkey),
        ('reported', reported), ('source', os.path.basename(a.file)), ('count', len(out_rows)),
        ('distribution', OrderedDict(strong=dist.get(3, 0), moderate=dist.get(2, 0), weak=dist.get(1, 0), none=dist.get(0, 0))),
        ('rows', out_rows),
    ])
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f'{slug}.json')
    with io.open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))
    print(f'wrote {path} ({os.path.getsize(path):,} bytes)')
    print(f'  {quarter}: {len(out_rows)} companies — Strong {dist.get(3,0)}, Moderate {dist.get(2,0)}, Weak {dist.get(1,0)}, No PEAD {dist.get(0,0)}')

    ipath = os.path.join(a.out, 'index.json')
    idx = OrderedDict(updated=dt.date.today().isoformat(), quarters=[])
    if os.path.exists(ipath):
        idx = json.load(io.open(ipath, encoding='utf-8'), object_pairs_hook=OrderedDict)
    qs = [x for x in idx.get('quarters', []) if x.get('file') != f'{slug}.json']
    qs.append(OrderedDict(quarter=quarter, sortkey=sortkey, reported=reported, file=f'{slug}.json', count=len(out_rows),
                          strong=dist.get(3, 0), moderate=dist.get(2, 0)))
    qs.sort(key=lambda x: x.get('sortkey', 0), reverse=True)
    idx['updated'] = dt.date.today().isoformat(); idx['quarters'] = qs
    with io.open(ipath, 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2)
    print(f'updated {ipath} ({len(qs)} quarter{"s" if len(qs) != 1 else ""})')

def read_table_with_sheet(path):
    """read_table, plus the first sheet's name for .xlsx (used to derive the quarter)."""
    ext = os.path.splitext(path)[1].lower()
    sheet = ''
    if ext in ('.xlsx', '.xlsm'):
        import zipfile
        from xml.etree import ElementTree as ET
        z = zipfile.ZipFile(path)
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        s = wb.find('m:sheets', ns).find('m:sheet', ns)
        sheet = s.get('name', '') if s is not None else ''
    headers, rows = read_table(path)
    return headers, rows, sheet

if __name__ == '__main__':
    main()
