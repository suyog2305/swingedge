#!/usr/bin/env python3
"""
framework_block.py — generate the "What Your Own System Says" table for a report from the
newest scan (or an intraday snapshot), and splice it into the body between markers.

    python tools/report/framework_block.py BODALCHEM                         # print the block
    python tools/report/framework_block.py BODALCHEM --apply                 # splice into bodies/bodalchem.html
    python tools/report/framework_block.py --snapshot snap.json EBGNG --apply --label "8 Sep, intraday"
    python tools/report/framework_block.py --all --apply                     # every body carrying the markers

WHY THIS EXISTS

Price-linked figures go stale the day after a report is written, and verify_numbers.py then
reports DRIFT on every one of them. If those figures live in ONE generated block, refreshing a
report is a re-run rather than a rewrite, and the prose never has to carry a number the scan
will contradict tomorrow.

The block uses exactly the row labels verify_numbers.py checks — "Price vs 50-DMA (₹x)",
"Below 52-week high", "3-month return", "Trailing P/E" and so on — so a refreshed report passes
the cross-check by construction. --apply also rewrites the two price-linked KPI cells in the
masthead (CMP and Market Cap) and nothing else.

A body opts in by carrying the marker pair, with anything or nothing between them:

    <!--FRAMEWORK:BEGIN-->
    <!--FRAMEWORK:END-->

WHAT IT COMPUTES

  RS rating       percentile 1-99 of the momentum composite within the scan (build_s2history)
  Trend template  the app's 7 checks: price > 50-DMA, price > 200-DMA, 50 > 200, >= 30% off the
                  52-week low, within 25% of the 52-week high, RS >= 70, 200-DMA rising vs the
                  previous scan. PASSES only if >= 5 checks are evaluable and all pass.
  Stage 2         status / weeks / since from the newest scan that carries a provider list,
                  plus its ASM/ESM/GSM flag
  Levels          DMA distances, 52-week distances, distance from the all-time high, returns,
                  trailing P/E and P/B — all straight from the scan row

A --snapshot is a JSON file {"captured": "...", "names": [{code, price, mcap, dma50, ...,
rs}, ...]} captured from the live screener page; the 200-DMA slope is then measured against
the newest scan on disk. Use it when the daily pull has not run yet; re-run against the real
scan once it has.
"""
import argparse, glob, io, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from build_s2history import rs_percentiles, num                     # noqa: E402

BODIES = os.path.join(ROOT, 'tools', 'report', 'bodies')
BEGIN, END = '<!--FRAMEWORK:BEGIN-->', '<!--FRAMEWORK:END-->'
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def short_date(iso):
    """2026-09-08 -> '8 Sep'"""
    try:
        y, m, d = iso.split('-')
        return f'{int(d)} {MONTHS[int(m) - 1]}'
    except Exception:
        return iso


def scans_on_disk():
    files = sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')))
    return [(p, jload(p)) for p in files]


def newest_universe(loaded, before=None):
    """(date, {code: row}) for the newest scan carrying a broad universe."""
    for p, d in reversed(loaded):
        U = [r for r in d.get('universe', []) if r.get('code')]
        if len(U) >= 500 and (before is None or d.get('date') < before):
            return d.get('date'), {r['code'].upper(): r for r in U}, U
    return None, {}, []


def newest_stage2(loaded):
    for p, d in reversed(loaded):
        S = d.get('stage2') or []
        if S:
            return d.get('date'), {str(r.get('code', '')).upper(): r for r in S if r.get('code')}
    return None, {}


def load_snapshot(path):
    snap = jload(path)
    rows = {}
    for r in snap.get('names', []):
        c = str(r.get('code', '')).upper()
        if not c:
            continue
        # the live page reports "down from 52w high" as a positive %; the scan stores it negative
        row = dict(r)
        if row.get('from_52wh') in (None, '') and row.get('down_52wh') not in (None, ''):
            row['from_52wh'] = -abs(num(row['down_52wh']) or 0)
        row['_rs'] = num(row.get('rs'))
        rows[c] = row
    return snap.get('captured') or 'snapshot', rows


def pct(a, b):
    a, b = num(a), num(b)
    return None if a is None or not b else (a / b - 1) * 100


def fmt_pct(v, signed=True):
    if v is None:
        return '—'
    s = f'{v:+.1f}%' if signed else f'{v:.1f}%'
    return s.replace('-', '-')          # ASCII minus: verify_numbers accepts either, the regex for DMA rows needs ASCII


def fmt_money(v):
    v = num(v)
    if v is None:
        return '—'
    return f'{v:,.2f}' if v < 100 else f'{v:,.2f}'


def trend(row, prev_row):
    price, d50, d200 = num(row.get('price')), num(row.get('dma50')), num(row.get('dma200'))
    up52, f52, rs = num(row.get('up_52wl')), num(row.get('from_52wh')), num(row.get('_rs'))
    checks = []
    if None not in (price, d50):  checks.append(('price > 50-DMA', price > d50))
    if None not in (price, d200): checks.append(('price > 200-DMA', price > d200))
    if None not in (d50, d200):   checks.append(('50-DMA > 200-DMA', d50 > d200))
    if up52 is not None:          checks.append(('>= 30% off 52w low', up52 >= 30))
    if f52 is not None:           checks.append(('within 25% of 52w high', f52 >= -25))
    if rs is not None:            checks.append(('RS >= 70', rs >= 70))
    if prev_row is not None:
        pd200 = num(prev_row.get('dma200'))
        if None not in (pd200, d200): checks.append(('200-DMA rising', d200 > pd200))
    passed = [n for n, ok in checks if ok]
    failed = [n for n, ok in checks if not ok]
    ok = len(checks) >= 5 and not failed
    return ok, len(passed), len(checks), failed


def block(code, row, prev_row, s2, s2date, label, universe_n, source_note):
    ok, k, n, failed = trend(row, prev_row)
    rs = num(row.get('_rs'))
    price = num(row.get('price'))
    d50, d200, ath = num(row.get('dma50')), num(row.get('dma200')), num(row.get('ath'))
    f52, up52 = num(row.get('from_52wh')), num(row.get('up_52wl'))
    pe, pb = num(row.get('pe')), num(row.get('pb'))
    vol, vol1m = num(row.get('volume')), num(row.get('vol_1m'))
    flag = (s2 or {}).get('asm') or ''
    flag = flag.strip() if isinstance(flag, str) else ''
    rs_cls = 'green' if (rs or 0) >= 80 else ('' if (rs or 0) >= 70 else 'red')
    tt = (f'<span class="green">PASSES {k}/{n}</span>' if ok
          else f'<span class="red">FAILS {k}/{n}</span> <span class="muted">({", ".join(failed) if failed else "too few checks"})</span>')
    if s2:
        s2_txt = f'{s2.get("status") or "On list"} · {s2.get("weeks") or "?"} wks · since {s2.get("since") or "?"}'
        s2_cls = ''
    else:
        s2_txt, s2_cls = 'Not on the list', 'red'
    rows = [
        (f'RS rating (1–99, {universe_n:,} stocks)', f'{int(rs) if rs is not None else "—"}', f'bold {rs_cls}'),
        ('Trend template', tt, 'bold'),
        (f'Stage 2 (provider list {short_date(s2date)})', s2_txt, s2_cls),
        ('Surveillance', flag if flag and flag != '-' else 'None', 'bold red' if flag and flag != '-' else ''),
        (f'Price vs 50-DMA (₹{fmt_money(d50)})', fmt_pct(pct(price, d50)), ''),
        (f'Price vs 200-DMA (₹{fmt_money(d200)})', fmt_pct(pct(price, d200)), ''),
        ('Below 52-week high', (f'-{abs(f52):.1f}%' if f52 is not None else '—'), 'bold' if f52 is not None and f52 > -5 else ''),
        ('Up from 52-week low', (f'+{up52:,.1f}%' if up52 is not None else '—'), ''),
        (f'Below all-time high (₹{fmt_money(ath)})', (fmt_pct(pct(price, ath)) if ath else '—'), ''),
        ('3-month return', fmt_pct(num(row.get('r3m'))), ''),
        ('1-month return', fmt_pct(num(row.get('r1m'))), ''),
        ('1-week return', fmt_pct(num(row.get('r1w'))), ''),
        ('1-day return', fmt_pct(num(row.get('r1d'))), ''),
        # Weinstein's third condition: a breakout means nothing without volume behind it. 1.5x the
        # 1-month average is the conventional bar; below 1.0x on an up day is the warning sign.
        ('Volume vs 1-month average', (f'{vol / vol1m:.2f}×' if vol and vol1m else '—'),
         ('bold green' if vol and vol1m and vol / vol1m >= 1.5 else ('red' if vol and vol1m and vol / vol1m < 0.7 else ''))),
        ('Trailing P/E', (f'{pe:.1f}×' if pe is not None and pe > 0 else 'n/m (loss-making)'), ''),
        ('Price / Book', (f'{pb:.2f}×' if pb is not None else '—'), ''),
    ]
    trs = '\n'.join(f'      <tr><td>{lab}</td><td class="right {cls}">{val}</td></tr>' for lab, val, cls in rows)
    return (f'{BEGIN}\n'
            f'  <div class="tbl-wrap"><table>\n'
            f'    <thead><tr><th>SwingEdge signal ({label})</th><th class="right">{code}</th></tr></thead>\n'
            f'    <tbody>\n{trs}\n    </tbody>\n'
            f'  </table></div>\n'
            f'  <p class="tbl-note">{source_note} Generated by tools/report/framework_block.py — '
            f're-run it after each scan and these rows refresh; nothing here is typed by hand.</p>\n'
            f'{END}')


def kpi_rewrite(body, row, cmp_label):
    """Rewrite the two price-linked KPI cells; leave every other cell alone."""
    price, mcap = num(row.get('price')), num(row.get('mcap'))
    changed = []
    if price is not None:
        new, n = re.subn(r'(kpi-label">)CMP[^<]*(</div><div class="kpi-value">)[^<]*(</div>)',
                         lambda m: f'{m.group(1)}CMP ({cmp_label}){m.group(2)}₹{price:,.2f}{m.group(3)}', body, count=1)
        if n: body = new; changed.append(f'CMP → ₹{price:,.2f}')
    if mcap is not None:
        new, n = re.subn(r'(kpi-label">Market Cap</div><div class="kpi-value">)[^<]*(</div>)',
                         lambda m: f'{m.group(1)}~₹{mcap:,.0f} Cr{m.group(2)}', body, count=1)
        if n: body = new; changed.append(f'Market Cap → ~₹{mcap:,.0f} Cr')
    return body, changed


def apply(path, blk, row, cmp_label):
    body = io.open(path, encoding='utf-8').read()
    if BEGIN not in body or END not in body:
        return None, ['no FRAMEWORK markers']
    pat = re.compile(re.escape(BEGIN) + r'.*?' + re.escape(END), re.S)
    old = pat.search(body).group(0)
    body = pat.sub(lambda m: blk, body, count=1)
    body, changed = kpi_rewrite(body, row, cmp_label)
    io.open(path, 'w', encoding='utf-8').write(body)
    return old, changed + (['framework block replaced'] if old != blk else ['framework block unchanged'])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('codes', nargs='*', help='NSE codes')
    ap.add_argument('--all', action='store_true', help='every body in tools/report/bodies carrying the markers')
    ap.add_argument('--snapshot', help='intraday snapshot JSON instead of the newest scan')
    ap.add_argument('--label', help='column label, e.g. "8 Sep 2026 close" (default from the scan date)')
    ap.add_argument('--apply', action='store_true', help='splice into the body file(s)')
    a = ap.parse_args()

    loaded = scans_on_disk()
    s2date, S2 = newest_stage2(loaded)

    if a.snapshot:
        captured, rows = load_snapshot(a.snapshot)
        prev_date, prev_rows, _ = newest_universe(loaded)
        universe_n = jload(a.snapshot).get('universe') or len(rows)
        label = a.label or f'{captured}'
        cmp_label = (a.label or captured).split(',')[0].split(' 20')[0].strip()
        source_note = (f'Live screener snapshot ({captured}) of {universe_n:,} stocks; 200-DMA slope measured '
                       f'against the {prev_date} scan; Stage 2 from the provider list of {s2date}.')
    else:
        date, rows, U = newest_universe(loaded)
        prev_date, prev_rows, _ = newest_universe(loaded, before=date)
        rs_percentiles(U)
        universe_n = len(U)
        label = a.label or f'{short_date(date)} {date[:4]} close'
        cmp_label = short_date(date)
        source_note = (f'Screener scan of {date} ({universe_n:,} stocks); 200-DMA slope measured against the '
                       f'{prev_date} scan; Stage 2 from the provider list of {s2date}.')

    codes = [c.upper() for c in a.codes]
    if a.all:
        for p in sorted(glob.glob(os.path.join(BODIES, '*.html'))):
            b = io.open(p, encoding='utf-8').read()
            if BEGIN in b:
                m = re.search(r'NSE:\s*([A-Z0-9&-]+)', b)
                if m: codes.append(m.group(1).upper())
        codes = list(dict.fromkeys(codes))
    if not codes:
        ap.error('name at least one NSE code, or pass --all')

    rc = 0
    for code in codes:
        row = rows.get(code)
        if not row:
            print(f'{code:<12} NOT in the {"snapshot" if a.snapshot else "scan"} — skipped'); rc = 1; continue
        blk = block(code, row, prev_rows.get(code), S2.get(code), s2date, label, universe_n, source_note)
        if a.apply:
            body_path = os.path.join(BODIES, code.lower() + '.html')
            if not os.path.exists(body_path):
                print(f'{code:<12} no body at {os.path.relpath(body_path, ROOT)}'); rc = 1; continue
            old, changed = apply(body_path, blk, row, cmp_label)
            if old is None:
                print(f'{code:<12} {changed[0]} in {os.path.relpath(body_path, ROOT)}'); rc = 1; continue
            ok, k, n, failed = trend(row, prev_rows.get(code))
            print(f'{code:<12} RS {int(num(row.get("_rs")) or 0):>2}  template {"PASS" if ok else "FAIL"} {k}/{n}'
                  f'  {"; ".join(changed)}')
        else:
            print(blk)
            print()
    return rc


if __name__ == '__main__':
    sys.exit(main())
