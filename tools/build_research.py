#!/usr/bin/env python3
"""
build_research.py — add a standalone HTML equity-research report to the SwingEdge Research Desk.

    python tools/build_research.py --file "Skipper_Research_Report_May2026.html" --date 2026-05-27 --feature
    python tools/build_research.py --file report.html --id tatapower-2026-08 --code TATAPOWER --no-feature

Copies the HTML into library/research/<id>.html and upserts library/research/index.json with the
report's metadata. Metadata is auto-extracted from the report's masthead (built on the Chartitude
template: <title>, the NSE:/BSE: eyebrow, the KPI strip, the rating bar, the thesis box); pass flags
to override anything it can't find. The report is rendered in an iframe in the app, so its own styling
is untouched — no schema requirements on the HTML itself beyond being a self-contained page.

Fields: id, title, subtitle, code (NSE), sector, date, rating, cmp, mcap, targets{base,bull,bear},
summary, file, featured. "Today's Reads" in the app = the newest reports (or those with featured=true).
"""
import argparse, datetime as dt, html, io, json, os, re, shutil
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'library', 'research')

def strip(s): return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', s or ''))).replace('\xa0', ' ').strip()

def find(pat, html, grp=1, flags=re.I | re.S):
    m = re.search(pat, html, flags)
    return strip(m.group(grp)) if m else None

def kpi(label, html):
    # <div class="kpi-label">CMP (May 27)</div><div class="kpi-value">~₹502</div>
    m = re.search(r'kpi-label"[^>]*>\s*' + label + r'[^<]*</div>\s*<div class="kpi-value"[^>]*>(.*?)</div>', html, re.I | re.S)
    return strip(m.group(1)) if m else None

def rating_cell(cls, html):
    m = re.search(r'rating-cell ' + cls + r'"[^>]*>.*?rating-val"[^>]*>(.*?)</div>', html, re.I | re.S)
    return strip(m.group(1)) if m else None

MONTHS = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}
def guess_date(html, title):
    for src in (find(r'report-eyebrow"[^>]*>(.*?)</p>', html) or '', title or ''):
        m = re.search(r'([A-Za-z]{3,9})\s+(\d{4})', src)
        if m and m.group(1)[:3].lower() in MONTHS:
            return f'{int(m.group(2))}-{MONTHS[m.group(1)[:3].lower()]:02d}-01'
    return None

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--file', required=True, help='the report .html file')
    ap.add_argument('--id', help='slug/id (default: from filename)')
    ap.add_argument('--date', help='publish date YYYY-MM-DD (default: parsed from the masthead)')
    ap.add_argument('--code', help='NSE code (default: parsed from the NSE: eyebrow)')
    ap.add_argument('--title'); ap.add_argument('--sector'); ap.add_argument('--summary')
    ap.add_argument('--feature', dest='feature', action='store_true', help='mark as a featured "Today\'s Read"')
    ap.add_argument('--no-feature', dest='feature', action='store_false')
    ap.set_defaults(feature=True)
    a = ap.parse_args()

    if not os.path.exists(a.file): raise SystemExit(f'no such file: {a.file}')
    html = io.open(a.file, encoding='utf-8', errors='replace').read()

    title = a.title or (find(r'<title[^>]*>(.*?)</title>', html) or '').split('—')[0].split('|')[0].strip() \
        or find(r'report-title"[^>]*>(.*?)</h1>', html) or os.path.splitext(os.path.basename(a.file))[0]
    code = a.code or find(r'NSE:\s*([A-Z0-9&\-]+)', html) or ''
    date = a.date or guess_date(html, title) or dt.date.today().isoformat()
    try: dt.date.fromisoformat(date)
    except ValueError: raise SystemExit('--date must be YYYY-MM-DD')
    ident = a.id or (re.sub(r'[^a-z0-9]+', '-', (code or title).lower()).strip('-') + '-' + date[:7])

    rep = OrderedDict(id=ident, title=title)
    sub = find(r'report-subtitle"[^>]*>(.*?)</p>', html)
    if sub: rep['subtitle'] = sub
    if code: rep['code'] = code.upper()
    sector = a.sector or find(r'<td class="bold">Sector</td>\s*<td[^>]*>(.*?)</td>', html) or (sub.split('·')[0].strip() if sub else None)
    if sector: rep['sector'] = re.split(r'\s[—/]\s', sector)[0].strip()   # keep the lead sector phrase
    rep['date'] = date
    rating = rating_cell('buy', html) or find(r'verdict-banner"[^>]*>\s*<h3[^>]*>RATING:\s*(.*?)</h3>', html)
    if rating: rep['rating'] = rating
    cmp_ = kpi('CMP', html); mcap = kpi('Market Cap', html)
    if cmp_: rep['cmp'] = cmp_
    if mcap: rep['mcap'] = mcap
    targets = OrderedDict()
    base = rating_cell('buy', html) and None  # base often in verdict banner; try there
    bull = rating_cell('bull', html); bear = rating_cell('bear', html)
    base = find(r'Base Case[^:]*:\s*<strong>(.*?)</strong>', html) or find(r'Base Case</td>\s*<td[^>]*>(.*?)</td>', html)
    if base: targets['base'] = base
    if bull: targets['bull'] = bull
    if bear: targets['bear'] = bear
    if targets: rep['targets'] = targets
    summary = a.summary or find(r'thesis-box"[^>]*>\s*<p[^>]*>(.*?)</p>', html) or sub or ''
    if summary:
        summary = strip(summary)
        rep['summary'] = (summary[:300].rsplit(' ', 1)[0] + '…') if len(summary) > 300 else summary
    rep['file'] = ident + '.html'
    rep['featured'] = bool(a.feature)

    os.makedirs(OUT, exist_ok=True)
    dest = os.path.join(OUT, rep['file'])
    if os.path.abspath(a.file) != os.path.abspath(dest): shutil.copyfile(a.file, dest)

    ipath = os.path.join(OUT, 'index.json')
    idx = OrderedDict(updated=dt.date.today().isoformat(), reports=[])
    if os.path.exists(ipath):
        idx = json.load(io.open(ipath, encoding='utf-8'), object_pairs_hook=OrderedDict)
    reports = [r for r in idx.get('reports', []) if r.get('id') != ident]
    reports.append(rep)
    reports.sort(key=lambda r: r.get('date', ''), reverse=True)
    idx['updated'] = dt.date.today().isoformat(); idx['reports'] = reports
    with io.open(ipath, 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=2)

    print(f'added report: {ident}')
    for k in ('title', 'code', 'sector', 'date', 'rating', 'cmp', 'featured'):
        if k in rep: print(f'  {k}: {rep[k]}')
    if 'targets' in rep: print(f'  targets: {dict(rep["targets"])}')
    print(f'  file: library/research/{rep["file"]}')
    print(f'updated {os.path.relpath(ipath, ROOT)} ({len(reports)} report{"s" if len(reports) != 1 else ""})')

if __name__ == '__main__':
    main()
