#!/usr/bin/env python3
"""
build_shortlist.py — the end-of-day decision loop: rank the market for convergence and
emit the 15-20 names worth a research report, with the reason each one qualified.

    python tools/build_shortlist.py                 # newest scan, top 20
    python tools/build_shortlist.py --top 15 --min-mcap 1000
    python tools/build_shortlist.py --date 2026-08-25 --quiet

Runs on data already in the repo — no LLM, no API key, no cost:
  data/scans/<date>.json   universe (price, returns, DMAs, 52W position) + the Stage 2 list
  data/pead/<quarter>.json most recent quarterly results (PEAD tier + growth)
  data/daily/news.json     curated headlines (the "trigger" leg), if present

Writes data/daily/shortlist.json (latest, what the app reads) and a dated copy under
data/daily/shortlist/<date>.json. Prints a readable table unless --quiet.

PINNED WATCHLIST — data/daily/watchlist.json, if present, lists names to surface every day
even when they do not earn a top-N slot. They are scored exactly like everything else and
get NO bonus: the ranking stays honest, they simply always appear, carrying their real
score and their true rank out of all scored names. They also bypass the --min-mcap and
--min-score floors, so a pinned name never silently vanishes. Emitted as doc.watchlist,
and any pinned name that DOES earn a top-N slot is tagged `pinned` in doc.rows.

SCORING — every point is explainable; each contributing factor becomes a `reason` string:
  new 52-week high / near high        +3 / +2
  Stage 2 (fresh entry or re-entry)   +3, else on the list +2, early in the move (<=8 wks) +1
  passes the 7-point trend template   +2
  Strong / Moderate PEAD earnings     +3 / +2
  today's move  >=5% / >=2%           +2 / +1
  week's move   >=10%                 +1
  RS  >=90 / >=80                     +2 / +1
  a news trigger on file              +2
  leading sector (median RS >=60)     +1
"""
import argparse, datetime as dt, io, json, os, re, glob
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = 'swingedge-shortlist/1'

def jload(p):
    with io.open(p, encoding='utf-8') as fh: return json.load(fh)

def num(v):
    try:
        f = float(v)
        return None if f != f else f          # drop NaN
    except (TypeError, ValueError):
        return None

def med(a):
    s = sorted(x for x in a if x is not None)
    if not s: return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

def pead_key(n):
    n = re.sub(r'&', ' and ', (n or '').lower())
    n = re.sub(r'[^a-z0-9 ]', ' ', n)
    n = re.sub(r'\b(ltd|limited|the|company|co|corporation|corp|inc|plc|india|indian|of|and)\b', ' ', n)
    n = re.sub(r'\binds?\b', 'industries', n)
    return re.sub(r'\s+', ' ', n).strip()

BANDS = [('Large', 50000), ('Mid', 15000), ('Small', 3000), ('Micro', 0)]
def band_of(mcap):
    if mcap is None: return None
    for name, lo in BANDS:
        if mcap >= lo: return name
    return 'Micro'

NEWHIGH = 1.5          # within this % of the 52-week high counts as "at a new high"
NEARHIGH = 5.0

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', help='scan date to use (default: newest in data/scans)')
    ap.add_argument('--top', type=int, default=20, help='how many names to shortlist (default 20)')
    ap.add_argument('--min-mcap', type=float, default=1000, help='ignore stocks below this mcap, Rs Cr')
    ap.add_argument('--min-score', type=float, default=5, help='ignore stocks scoring below this')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    # ---- load the newest scan -------------------------------------------------
    scans = sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')))
    if not scans: raise SystemExit('no scans in data/scans - run tools/build_scan.py first')
    path = next((p for p in scans if a.date and os.path.basename(p) == a.date + '.json'), None) if a.date else scans[-1]
    if path is None: raise SystemExit(f'no scan for {a.date}')
    scan = jload(path)
    U = [r for r in scan.get('universe', []) if r.get('code')]
    if not U: raise SystemExit(f'{os.path.basename(path)} has no universe rows')
    S2 = scan.get('stage2', []) or []
    scan_date = scan.get('date') or os.path.basename(path)[:-5]

    # ---- pinned watchlist (optional) -----------------------------------------
    watch, watch_note = set(), {}
    wpath = os.path.join(ROOT, 'data', 'daily', 'watchlist.json')
    if os.path.exists(wpath):
        try:
            for n in (jload(wpath).get('names') or []):
                c = str(n.get('code', '')).upper()
                if c:
                    watch.add(c)
                    if n.get('note'): watch_note[c] = n['note']
        except Exception:
            pass                              # a malformed watchlist must never break the build

    # previous scan, for "new to the scan" and DMA-slope
    prev = None
    idx = scans.index(path)
    if idx > 0:
        try:
            p = jload(scans[idx - 1])
            prev = {r['code']: r for r in p.get('universe', []) if r.get('code')}
        except Exception: prev = None

    # ---- RS: percentile of a recency-weighted momentum composite --------------
    def score_mom(r):
        r3, r6, r1y = num(r.get('r3m')), num(r.get('r6m')), num(r.get('r1y'))
        if r3 is not None and r6 is not None and r1y is not None:
            q1 = r3
            q2 = ((1 + r6 / 100) / (1 + r3 / 100) - 1) * 100
            h2 = ((1 + r1y / 100) / (1 + r6 / 100) - 1) * 100
            return 0.4 * q1 + 0.2 * q2 + 0.2 * h2
        parts, wsum, s = [('r3m', .5), ('r1m', .3), ('r1w', .2)], 0.0, 0.0
        for k, w in parts:
            v = num(r.get(k))
            if v is not None: s += w * v; wsum += w
        return s / wsum if wsum else None

    for r in U: r['_mom'] = score_mom(r)
    ranked = sorted((r for r in U if r['_mom'] is not None), key=lambda r: r['_mom'])
    n = len(ranked)
    i = 0
    while i < n:                                    # average-rank percentile, ties share a rank
        j = i
        while j + 1 < n and ranked[j + 1]['_mom'] == ranked[i]['_mom']: j += 1
        pct = 99 if n == 1 else round(1 + 98 * (((i + j) / 2 + 1) - 1) / (n - 1))
        for k in range(i, j + 1): ranked[k]['_rs'] = pct
        i = j + 1

    # ---- sector strength ------------------------------------------------------
    groups = {}
    for r in U:
        g = r.get('group') or r.get('industry') or 'Other'
        groups.setdefault(g, []).append(r)
    sector_rs = {g: med([x.get('_rs') for x in rows]) for g, rows in groups.items()}

    # ---- Stage 2 lookup -------------------------------------------------------
    s2map = {}
    for s in S2:
        c = str(s.get('code', '')).upper()
        if c: s2map[c] = s

    # ---- PEAD (most recent quarter) ------------------------------------------
    pead_by_code, pead_by_name, quarter = {}, {}, None
    pfiles = glob.glob(os.path.join(ROOT, 'data', 'pead', '*.json'))
    pfiles = [p for p in pfiles if not p.endswith('index.json')]
    best = None
    for p in pfiles:
        try:
            d = jload(p)
            if best is None or (d.get('sortkey', 0) > best.get('sortkey', 0)): best = d
        except Exception: pass
    if best:
        quarter = best.get('quarter')
        for r in best.get('rows', []):
            if r.get('code'): pead_by_code[str(r['code']).upper()] = r
            k = r.get('key') or pead_key(r.get('name'))
            if k and k not in pead_by_name: pead_by_name[k] = r

    # ---- news / trigger -------------------------------------------------------
    news = {}
    npath = os.path.join(ROOT, 'data', 'daily', 'news.json')
    if os.path.exists(npath):
        try: news = {k.upper(): v for k, v in (jload(npath).get('stocks') or {}).items()}
        except Exception: news = {}

    # ---- trend template -------------------------------------------------------
    def trend_pass(r):
        price, d50, d200 = num(r.get('price')), num(r.get('dma50')), num(r.get('dma200'))
        up52, f52, rs = num(r.get('up_52wl')), num(r.get('from_52wh')), r.get('_rs')
        checks = []
        if price is not None and d50 is not None: checks.append(price > d50)
        if price is not None and d200 is not None: checks.append(price > d200)
        if d50 is not None and d200 is not None: checks.append(d50 > d200)
        if up52 is not None: checks.append(up52 >= 30)
        if f52 is not None: checks.append(f52 >= -25)
        if rs is not None: checks.append(rs >= 70)
        if prev is not None:
            pr = prev.get(r['code']); pd200 = num(pr.get('dma200')) if pr else None
            if pd200 is not None and d200 is not None: checks.append(d200 > pd200)
        return (len(checks) >= 5 and all(checks)), sum(1 for c in checks if c), len(checks)

    # ---- score ----------------------------------------------------------------
    rows = []
    for r in U:
        code = str(r['code']).upper()
        mcap = num(r.get('mcap'))
        pinned = code in watch                 # pinned names bypass both floors
        if not pinned and a.min_mcap and (mcap is None or mcap < a.min_mcap): continue
        rs = r.get('_rs')
        f52, r1d, r1w = num(r.get('from_52wh')), num(r.get('r1d')), num(r.get('r1w'))
        s2 = s2map.get(code)
        pd = pead_by_code.get(code) or pead_by_name.get(pead_key(r.get('name')))
        nw = (news.get(code) or [None])[0]
        tpass, tp, tt = trend_pass(r)
        g = r.get('group') or r.get('industry') or 'Other'
        srs = sector_rs.get(g)

        score, reasons = 0.0, []
        if f52 is not None and f52 >= -NEWHIGH:
            score += 3; reasons.append('at a new 52-week high')
        elif f52 is not None and f52 >= -NEARHIGH:
            score += 2; reasons.append(f'within {abs(f52):.1f}% of the 52-week high')
        if s2:
            st = str(s2.get('status', '')).lower()
            wk = num(s2.get('weeks'))
            if re.search(r're-?\s?entry', st):
                score += 3; reasons.append('re-entered the Stage 2 list this week')
            elif 'new' in st:
                score += 3; reasons.append('entered the Stage 2 list this week')
            else:
                score += 2; reasons.append(f'on the Stage 2 list{f" ({int(wk)} wks)" if wk else ""}')
            if wk and wk <= 8: score += 1; reasons.append('still early in the Stage 2 move')
        if tpass: score += 2; reasons.append(f'passes the trend template ({tp}/{tt})')
        if pd:
            tier = pd.get('tier')
            if tier == 3: score += 3; reasons.append(f'Strong PEAD earnings ({quarter})')
            elif tier == 2: score += 2; reasons.append(f'Moderate PEAD earnings ({quarter})')
        if r1d is not None and r1d >= 5: score += 2; reasons.append(f'up {r1d:.1f}% today')
        elif r1d is not None and r1d >= 2: score += 1; reasons.append(f'up {r1d:.1f}% today')
        if r1w is not None and r1w >= 10: score += 1; reasons.append(f'up {r1w:.1f}% on the week')
        if rs is not None and rs >= 90: score += 2; reasons.append(f'RS {rs}')
        elif rs is not None and rs >= 80: score += 1; reasons.append(f'RS {rs}')
        if nw: score += 2; reasons.append('news trigger on file')
        if srs is not None and srs >= 60: score += 1; reasons.append(f'{g} is a leading sector')

        if score < a.min_score and not pinned: continue
        item = OrderedDict(code=code, name=r.get('name'), sector=g, industry=r.get('industry'),
                           band=band_of(mcap), mcap=round(mcap) if mcap is not None else None,
                           price=num(r.get('price')), score=round(score, 1), rs=rs, sector_rs=round(srs) if srs is not None else None,
                           r1d=r1d, r1w=r1w, from_52wh=f52,
                           new_high=bool(f52 is not None and f52 >= -NEWHIGH),
                           trend=f'{tp}/{tt}', trend_pass=tpass)
        if s2:
            item['s2'] = OrderedDict(weeks=num(s2.get('weeks')), status=s2.get('status'),
                                     rs_pct=num(s2.get('rs_pct')), since=s2.get('since'))
        if pd:
            item['pead'] = OrderedDict(tier=pd.get('tier'), label=pd.get('pead'), quarter=quarter,
                                       eps_yoy=num(pd.get('eps_yoy')), pat_yoy=num(pd.get('pat_yoy')),
                                       sales_yoy=num(pd.get('sales_yoy')))
        if nw: item['news'] = nw
        item['reasons'] = reasons
        if pinned:
            item['pinned'] = True
            if code in watch_note: item['pin_note'] = watch_note[code]
        rows.append(item)

    rows.sort(key=lambda x: (-x['score'], -(x['rs'] or 0), -(x['r1d'] or 0)))
    for i, r in enumerate(rows, 1):
        r['rank'] = i                          # true rank among all scored names
    top = rows[:a.top]
    in_top = {r['code'] for r in top}
    # pinned names that did NOT earn a slot - surfaced separately, never mixed into the ranking
    watched = [r for r in rows if r.get('pinned') and r['code'] not in in_top]

    doc = OrderedDict(schema=SCHEMA, date=dt.date.today().isoformat(), scan_date=scan_date,
                      quarter=quarter, universe=len(U), considered=len(rows), count=len(top),
                      params=OrderedDict(top=a.top, min_mcap=a.min_mcap, min_score=a.min_score),
                      rows=top, watchlist=watched)

    outdir = os.path.join(ROOT, 'data', 'daily')
    os.makedirs(os.path.join(outdir, 'shortlist'), exist_ok=True)
    latest = os.path.join(outdir, 'shortlist.json')
    dated = os.path.join(outdir, 'shortlist', scan_date + '.json')
    for p in (latest, dated):
        with io.open(p, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))

    if not a.quiet and watched:
        print()
        print(f'Pinned watchlist ({len(watched)}) - surfaced every day, no score bonus:')
        for r in watched:
            print(f"  {r['code']:<12} score {r['score']:>5}   rank #{r['rank']} of {len(rows)}   RS {r.get('rs') or '-'}")
            print(f"     {' | '.join(r.get('reasons') or []) or 'no qualifying factors today'}")

    if not a.quiet:
        print(f'Shortlist for scan {scan_date}  ({len(U)} stocks scanned, {len(rows)} scored >= {a.min_score}, top {len(top)} shown)')
        if quarter: print(f'Earnings quarter: {quarter}')
        print('-' * 108)
        print(f'{"#":>2}  {"CODE":<12} {"SCORE":>5}  {"RS":>3} {"DAY%":>6} {"52WH":>6}  {"CAP":<6} {"SECTOR":<26} WHY')
        print('-' * 108)
        for i, r in enumerate(top, 1):
            why = '; '.join(r['reasons'][:3])
            print(f'{i:>2}  {r["code"]:<12} {r["score"]:>5}  {str(r["rs"] or "-"):>3} '
                  f'{(f"{r["r1d"]:+.1f}" if r["r1d"] is not None else "-"):>6} '
                  f'{(f"{r["from_52wh"]:.1f}" if r["from_52wh"] is not None else "-"):>6}  '
                  f'{(r["band"] or "-"):<6} {(r["sector"] or "")[:26]:<26} {why[:44]}')
        print('-' * 108)
        print(f'wrote data/daily/shortlist.json  and  data/daily/shortlist/{scan_date}.json')
        print('Pick the 5 you want reports on, then run tools/build_research.py for each.')

if __name__ == '__main__':
    main()
