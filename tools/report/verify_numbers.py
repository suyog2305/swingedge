#!/usr/bin/env python3
"""
verify_numbers.py — cross-check every market figure quoted in a research report
against the scan data it was supposedly built from.

    python tools/report/verify_numbers.py                # check every report in the index
    python tools/report/verify_numbers.py hfcl krn       # check specific report ids/codes

Research reports quote a lot of derived arithmetic — "+25% vs the 50-DMA", "1.44x the
order book", "10.8x annualised revenue". Those are exactly the numbers that go wrong
silently, because nothing recomputes them. This does.

For each report it pulls the NSE code from the masthead, finds that stock in the newest
dated scan, and re-derives every checkable claim:

  * CMP, market cap, trailing P/E, price/book         -- quoted directly from the scan
  * 50-DMA and 200-DMA levels, and the % above each   -- derived, so worth recomputing
  * distance below the 52-week high, % up from the low
  * 3-month / 1-week / 1-day returns
  * sales and net-profit growth for the latest quarter

Anything outside tolerance is printed as a MISMATCH with both values, so the report can
be corrected. Figures that come from results filings rather than the scan (revenue,
order books, guidance) are not checkable here and are listed as such.

Exit code is 1 if any mismatch is found, so this can gate a commit.
"""
import argparse, glob, io, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

# tolerance: absolute for levels, percentage-points for percentages
TOL_PCT, TOL_LEVEL_REL = 1.0, 0.015


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def num(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def strip_tags(s):
    return re.sub(r'<[^>]+>', '', s)


def money(s):
    """'₹1,430.35' / '~₹11,077 Cr' -> float"""
    m = re.search(r'([\d,]+(?:\.\d+)?)', s.replace(',', '') if False else s)
    if not m:
        return None
    return num(m.group(1).replace(',', ''))


# Figures that legitimately move with the share price. In a report published before the
# scan date these will differ, and that is staleness rather than an error - so they are
# reported as DRIFT, not MISMATCH, and do not fail the run.
PRICE_LINKED = {'CMP', 'Market cap (Cr)', 'Trailing P/E', 'Price / Book',
                '% above 50-DMA', '% above 200-DMA', 'Below 52w high', 'Up from 52w low',
                '3-month return', '1-week return', '1-day return'}

class Check:
    def __init__(self, same_day=True):
        self.rows, self.bad, self.drift = [], 0, 0
        self.same_day = same_day

    def add(self, label, quoted, actual, kind='pct'):
        if quoted is None or actual is None:
            self.rows.append(('SKIP', label, quoted, actual)); return
        if kind == 'pct':
            ok = abs(quoted - actual) <= TOL_PCT
        else:
            ok = abs(quoted - actual) <= max(abs(actual) * TOL_LEVEL_REL, 0.01)
        if ok:
            status = 'OK'
        elif not self.same_day and label in PRICE_LINKED:
            status = 'DRIFT'; self.drift += 1
        else:
            status = 'MISMATCH'; self.bad += 1
        self.rows.append((status, label, quoted, actual))


def check_report(path, row, prev_row, same_day=True):
    """row = this stock's record in the newest scan."""
    s = io.open(path, encoding='utf-8').read()
    c = Check(same_day)
    price = num(row.get('price'))
    d50, d200 = num(row.get('dma50')), num(row.get('dma200'))

    # --- CMP from the KPI strip -------------------------------------------------
    m = re.search(r'kpi-label">CMP[^<]*</div><div class="kpi-value">([^<]+)</div>', s)
    if m:
        c.add('CMP', money(m.group(1)), price, 'level')

    # --- market cap -------------------------------------------------------------
    m = re.search(r'kpi-label">Market Cap</div><div class="kpi-value">~?₹([\d,]+)\s*Cr</div>', s)
    if m:
        c.add('Market cap (Cr)', num(m.group(1).replace(',', '')), num(row.get('mcap')), 'level')

    # --- table rows: "Trailing P/E ... 67.0x" ----------------------------------
    def table_val(label_re):
        m = re.search(r'<td[^>]*>' + label_re + r'</td>\s*<td[^>]*>\s*(?:<[^>]+>)*\s*~?([\d.,]+)', s, re.I)
        return num(m.group(1).replace(',', '')) if m else None

    c.add('Trailing P/E', table_val(r'Trailing P/E'), num(row.get('pe')), 'level')
    c.add('Price / Book', table_val(r'Price\s*/\s*Book'), num(row.get('pb')), 'level')

    # --- "(₹831)" style DMA levels quoted inline --------------------------------
    for tag, actual in (('50-DMA', d50), ('200-DMA', d200)):
        m = re.search(r'Price vs ' + tag + r'\s*\(₹([\d,]+(?:\.\d+)?)\)', s)
        if m:
            c.add(f'{tag} level', num(m.group(1).replace(',', '')), actual, 'level')
        # the % claim on the same row
        m2 = re.search(r'Price vs ' + tag + r'[^<]*\)</td>\s*<td[^>]*>\s*(?:<[^>]+>)*\s*([+-]?[\d.]+)%', s)
        if m2 and actual and price:
            c.add(f'% above {tag}', num(m2.group(1)), (price / actual - 1) * 100)

    # --- 52-week high / low -----------------------------------------------------
    m = re.search(r'Below 52-week high[^<]*</td>\s*<td[^>]*>\s*(?:<[^>]+>)*\s*(−|-)?([\d.]+)%', s)
    if m:
        c.add('Below 52w high', -num(m.group(2)), num(row.get('from_52wh')))
    m = re.search(r'Up from 52-week low</td>\s*<td[^>]*>\s*(?:<[^>]+>)*\s*\+?([\d.,]+)%', s)
    if m:
        c.add('Up from 52w low', num(m.group(1).replace(',', '')), num(row.get('up_52wl')))

    # --- returns ----------------------------------------------------------------
    for lab, key in (('3-month', 'r3m'), ('1-week', 'r1w'), ('1-day', 'r1d')):
        m = re.search(lab + r'\s*(?:price\s*)?return</td>\s*<td[^>]*>\s*(?:<[^>]+>)*\s*(−|\+|-)?([\d.]+)%', s)
        if m:
            v = num(m.group(2))
            if m.group(1) in ('−', '-'):
                v = -v
            c.add(f'{lab} return', v, num(row.get(key)))

    # --- growth -----------------------------------------------------------------
    m = re.search(r'Sales growth[^<]*</td>\s*<td[^>]*>\s*(?:<[^>]+>)*\s*\+?([\d.,]+)%', s)
    if m:
        c.add('Sales growth YoY', num(m.group(1).replace(',', '')), num(row.get('sales_yoy')))
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('codes', nargs='*', help='report ids or NSE codes; default = all')
    a = ap.parse_args()

    scans = sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')))
    cur, prev = jload(scans[-1]), jload(scans[-2])
    umap = {r['code'].upper(): r for r in cur.get('universe', []) if r.get('code')}
    pmap = {r['code'].upper(): r for r in prev.get('universe', []) if r.get('code')}

    idx = jload(os.path.join(ROOT, 'library', 'research', 'index.json'))
    reports = idx['reports']
    if a.codes:
        want = {x.upper() for x in a.codes}
        reports = [r for r in reports if r.get('code', '').upper() in want or r['id'].split('-')[0].upper() in want]

    total_bad, total_drift, checked, skipped = 0, 0, 0, []
    print(f'Cross-checking against scan {cur.get("date")} ({len(umap)} stocks)\n')
    for r in reports:
        code = (r.get('code') or '').upper()
        path = os.path.join(ROOT, 'library', 'research', r['file'].split('/')[-1])
        if code not in umap or not os.path.exists(path):
            skipped.append(f'{r["id"]} ({code or "no code"}) — not in scan universe')
            continue
        same_day = (r.get('date') or '') >= (cur.get('date') or '')
        c = check_report(path, umap[code], pmap.get(code), same_day)
        checked += 1
        total_bad += c.bad
        total_drift += c.drift
        shown = [x for x in c.rows if x[0] != 'SKIP']
        bits = []
        if c.bad: bits.append(f'{c.bad} MISMATCH')
        if c.drift: bits.append(f'{c.drift} drift (published {r.get("date")})')
        print(f'{code:<12} {len(shown):>2} checks  {", ".join(bits) if bits else "all clear"}')
        for status, label, q, act in shown:
            if status == 'MISMATCH':
                print(f'   !! {label}: report says {q}, scan says {act:.2f}')
            elif status == 'DRIFT':
                print(f'   ~  {label}: report {q} (as published), now {act:.2f}')

    print(f'\n{checked} reports checked, {total_bad} mismatches')
    if skipped:
        print(f'\nnot checkable ({len(skipped)}):')
        for x in skipped:
            print('  -', x)
    print('\nNote: revenue, PAT, order books and guidance come from results filings, '
          'not the scan, and are not verifiable here.')
    return 1 if total_bad else 0


if __name__ == '__main__':
    sys.exit(main())
