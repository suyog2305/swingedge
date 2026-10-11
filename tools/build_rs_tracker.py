#!/usr/bin/env python3
"""
build_rs_tracker.py — persistence and exhaustion intelligence on the top-N RS names, week over week.

    python tools/build_rs_tracker.py                 # newest scan, top 25, 8 weekly anchors
    python tools/build_rs_tracker.py --top 25 --weeks 6 --quiet

Runs on the scans already in data/scans/ — no LLM, no network. Writes data/daily/rs_tracker.json,
which the app's "RS Leaders" page reads (it overlays your own positions client-side).

WHAT IT ANSWERS
  Of this week's top-N RS names, which are holding their place, which are new, which are slipping,
  and which left the list and keep weakening week on week — and whether any of them show the
  classic signs of a tired leader before the rating itself turns.

HOW (every number is explainable)
  Anchors   The newest scan, then the nearest scan on or before each 7-day step back (up to --weeks),
            only while the RS mode stays comparable with the newest scan.
  Rank      Position by raw RS score among every coded name in that scan (1 = strongest). Rank, not
            the 1-99 rating, is what moves at the top: RS 99 -> 98 can be rank 5 -> 40.
  Status    in the top N now:  new (not in last week) · slipping (rank worse by 8+ places vs last
            week, or worsening two weeks running) · holding (everything else)
            not in the top N:  dropped (was in last week, or within the window) · fading (left the
            list and rank worsened two weeks running) · out (never in the window)
  Exhaustion flags, read off the newest scan's own fields:
            climax   +20% in a week or +45% in a month (a climax run)
            rolling  -5% or worse this week while the rating is still 90+
            off_high more than 15% below the 52-week high
            below50  closed below the 50-DMA
            ext50    stretched 40%+ above the 50-DMA
            dist     a -3% day on twice the 1-month average volume (distribution)
            trend    fails the trend template by two or more checks
            0 = Healthy · 1 = Watch · 2 = Tiring · 3+ = Exhausted
  Verdict   direction only (RS gives the level): weakening = slipping / dropped / fading or 2+ flags;
            watch = new, or exactly 1 flag; strong = otherwise; none for "out" names (never in the
            window), so nothing outside the list reads as "holding its place"
  Evidence  for every consecutive pair of anchors: how many of the top N stayed, split by how many
            exhaustion flags they carried at the time — so the flags earn (or lose) your trust
            against your own archive, week by week.

PORTFOLIO  data/daily/holdings.json, if present, lists what you hold (written by hand or by a
           broker-MCP routine): {"updated":"YYYY-MM-DD","source":"...","names":[{"code":"NSE","qty":0,"avg":0}]}.
           Each holding gets the same stats plus "is it inside the top N and by how much". The app
           also overlays positions it already knows about, so the file is optional.
"""
import argparse, datetime as dt, glob, io, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import num, rate, rs_mode, trend_label, utf8_stdio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(ROOT, 'data', 'scans')
OUT = os.path.join(ROOT, 'data', 'daily', 'rs_tracker.json')
HOLDINGS = os.path.join(ROOT, 'data', 'daily', 'holdings.json')

FLAGS = {
    'climax':   'Climax run: +20% in a week or +45% in a month',
    'rolling':  'Rolling over: -5% or worse this week while the rating is still 90+',
    'off_high': 'More than 15% below the 52-week high',
    'below50':  'Closed below the 50-DMA',
    'ext50':    'Stretched 40%+ above the 50-DMA',
    'dist':     'Distribution day: -3% on twice the 1-month average volume',
    'trend':    'Fails the trend template by two or more checks',
}
EX_LABEL = lambda n: 'Healthy' if n == 0 else 'Watch' if n == 1 else 'Tiring' if n == 2 else 'Exhausted'
SLIP = 8            # rank places lost in a week that count as slipping


def jload(p):
    with io.open(p, encoding='utf-8') as fh: return json.load(fh)


def scan_rows(p):
    """A scan's universe, or None when the file is unreadable (a run killed mid-write) - said loudly, never silently."""
    try: return jload(p).get('universe', [])
    except (ValueError, OSError) as e:
        print(f'WARNING: skipping unreadable scan {os.path.basename(p)}: {type(e).__name__}: {e}', file=sys.stderr)
        return None


def scan_files():
    out = []
    for p in glob.glob(os.path.join(SCANS, '*.json')):
        m = re.search(r'(\d{4}-\d{2}-\d{2})\.json$', p)
        if m: out.append((m.group(1), p))
    return sorted(out)


def key(r):
    return str(r.get('code') or '').strip().upper()


def trend(r, prev):
    """The app's 7-point template; (passed, total). Needs the row's own _rs."""
    price, d50, d200 = num(r.get('price')), num(r.get('dma50')), num(r.get('dma200'))
    c = []
    if price is not None and d50 is not None: c.append(price > d50)
    if price is not None and d200 is not None: c.append(price > d200)
    if d50 is not None and d200 is not None: c.append(d50 > d200)
    if num(r.get('up_52wl')) is not None: c.append(num(r['up_52wl']) >= 30)
    if num(r.get('from_52wh')) is not None: c.append(num(r['from_52wh']) >= -25)
    if r.get('_rs') is not None: c.append(r['_rs'] >= 70)
    pd = num(prev.get('dma200')) if prev else None
    if pd is not None and d200 is not None: c.append(d200 > pd)
    return sum(c), len(c)


def flags_for(r, tp, tt):
    f = []
    r1w, r1m, r1d = num(r.get('r1w')), num(r.get('r1m')), num(r.get('r1d'))
    price, d50, f52 = num(r.get('price')), num(r.get('dma50')), num(r.get('from_52wh'))
    vol, vol1m = num(r.get('volume')), num(r.get('vol_1m'))
    if (r1w is not None and r1w >= 20) or (r1m is not None and r1m >= 45): f.append('climax')
    if r1w is not None and r1w <= -5 and (r.get('_rs') or 0) >= 90: f.append('rolling')
    if f52 is not None and f52 <= -15: f.append('off_high')
    if price is not None and d50 is not None and price < d50: f.append('below50')
    if price is not None and d50 is not None and d50 > 0 and price >= 1.4 * d50: f.append('ext50')
    if r1d is not None and r1d <= -3 and vol and vol1m and vol >= 2 * vol1m: f.append('dist')
    if tt and tt - tp >= 2: f.append('trend')
    return f


def enrich(rows, prev_rows):
    """RS, rank, trend and exhaustion flags for one scan, keyed by code. Rank counts coded names only."""
    rate(rows)
    prev = {key(r): r for r in (prev_rows or []) if key(r)}
    coded = [r for r in rows if key(r) and r.get('_rs') is not None]
    coded.sort(key=lambda r: -r['_m'])
    out = {}
    for i, r in enumerate(coded, 1):
        tp, tt = trend(r, prev.get(key(r)))
        fl = flags_for(r, tp, tt)
        out[key(r)] = {'code': key(r), 'name': r.get('name'), 'industry': r.get('industry'), 'group': r.get('group') or r.get('industry'),
                       'mcap': r.get('mcap'), 'price': r.get('price'), 'rs': r['_rs'], 'rank': i, 'score': round(r['_m'], 2),
                       'r1d': r.get('r1d'), 'r1w': r.get('r1w'), 'r1m': r.get('r1m'), 'r3m': r.get('r3m'), 'from_52wh': r.get('from_52wh'),
                       'trend': {'pass': tp, 'total': tt, 'label': trend_label(tp, tt) if tt else None},
                       'flags': fl, 'exhaustion': len(fl), 'ex_label': EX_LABEL(len(fl))}
    return out


def pick_anchors(files, weeks):
    """[(date, path)] newest first: the newest scan, then the nearest scan on or before each 7-day step."""
    dates = [dt.date.fromisoformat(d) for d, _ in files]
    newest = dates[-1]
    anchors = [files[-1]]
    for k in range(1, weeks + 1):
        target = newest - dt.timedelta(days=7 * k)
        cands = [i for i, d in enumerate(dates) if target - dt.timedelta(days=4) <= d <= target]
        anchors.append(files[max(cands)] if cands else None)
    while anchors and anchors[-1] is None: anchors.pop()
    return anchors


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--weeks', type=int, default=8)
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    files = scan_files()
    if not files: sys.exit('no dated scan in data/scans/')
    anchors = pick_anchors(files, a.weeks)
    newest_rows = scan_rows(anchors[0][1])
    if newest_rows is None: sys.exit(f'the newest scan {os.path.basename(anchors[0][1])} is unreadable - rebuild it with tools/build_scan.py')
    mode = rs_mode(newest_rows)

    # enrich every anchor (and the newest scan's previous daily scan, for the DMA-200 slope + 1-day RS change)
    idx = {d: i for i, (d, _) in enumerate(files)}
    def prev_of(date):
        i = idx[date]; return scan_rows(files[i - 1][1]) if i > 0 else None
    weeks = []                                                   # [{date, gap, map}] newest first; None where no comparable scan
    for k, an in enumerate(anchors):
        if an is None: weeks.append(None); continue
        rows = newest_rows if k == 0 else scan_rows(an[1])
        if rows is None: weeks.append(None); continue             # unreadable (warned above): treated as a week with no scan
        if rs_mode(rows) != mode: weeks.append(None); continue   # a week ranked on a different composite is not comparable
        prev = prev_of(an[0])
        weeks.append({'date': an[0], 'file': os.path.basename(an[1]), 'gap_days': (dt.date.fromisoformat(anchors[0][0]) - dt.date.fromisoformat(an[0])).days,
                      'map': enrich(rows, prev)})
    while weeks and weeks[-1] is None: weeks.pop()
    now = weeks[0]['map']
    prev_daily = prev_of(anchors[0][0])
    prev_rs = {}
    if prev_daily:
        rate(prev_daily); prev_rs = {key(r): r['_rs'] for r in prev_daily if key(r) and r.get('_rs') is not None}

    N = a.top
    in_top = lambda m, c: bool(m) and c in m and m[c]['rank'] <= N
    codes = {}
    for c, r in now.items():
        rs_hist = [(w['map'][c]['rs'] if w and c in w['map'] else None) for w in weeks]
        rk_hist = [(w['map'][c]['rank'] if w and c in w['map'] else None) for w in weeks]
        tops = [in_top(w['map'] if w else None, c) for w in weeks]
        streak = 0
        for t in tops:
            if t: streak += 1
            else: break
        weakening = 0                                            # consecutive weekly steps with a worse rank, newest first
        for i in range(len(rk_hist) - 1):
            if rk_hist[i] is not None and rk_hist[i + 1] is not None and rk_hist[i] > rk_hist[i + 1]: weakening += 1
            else: break
        d1w = (rk_hist[0] - rk_hist[1]) if len(rk_hist) > 1 and rk_hist[1] is not None else None   # +ve = lost places
        last = tops[1] if len(tops) > 1 else False
        ever = any(tops[1:5])
        if tops[0]:
            status = 'new' if not last else 'slipping' if ((d1w is not None and d1w >= SLIP) or weakening >= 2) else 'holding'
        else:
            status = 'dropped' if last else 'fading' if (ever and weakening >= 2) else 'dropped' if ever else 'out'
        ex = r['exhaustion']
        verdict = None if status == 'out' else ('weakening' if status in ('slipping', 'dropped', 'fading') or ex >= 2 else 'watch' if status == 'new' or ex == 1 else 'strong')
        codes[c] = dict(r, rs_hist=rs_hist, rank_hist=rk_hist, in_top=tops, streak=streak, weeks_in_top=sum(tops),
                        weakening_weeks=weakening, rank_d1w=d1w, rs_d1d=(r['rs'] - prev_rs[c]) if c in prev_rs else None,
                        status=status, verdict=verdict)

    leaders = sorted((v for v in codes.values() if v['in_top'][0]), key=lambda v: v['rank'])
    left = sorted((v for v in codes.values() if not v['in_top'][0] and v['status'] in ('dropped', 'fading')), key=lambda v: v['rank'])
    cut = leaders[-1] if leaders else None

    # evidence: of each older anchor's top N, how many were still top N one anchor later, by exhaustion at the time
    transitions = []
    for i in range(len(weeks) - 1, 0, -1):
        older, newer = weeks[i], weeks[i - 1]
        if not older or not newer: continue
        tops_then = [v for v in older['map'].values() if v['rank'] <= N]
        bucket = lambda v: '0' if v['exhaustion'] == 0 else '1' if v['exhaustion'] == 1 else '2+'
        by = {}
        for v in tops_then:
            b = by.setdefault(bucket(v), {'n': 0, 'retained': 0})
            b['n'] += 1; b['retained'] += int(in_top(newer['map'], v['code']))
        transitions.append({'from': older['date'], 'to': newer['date'], 'of': len(tops_then), 'retained': sum(b['retained'] for b in by.values()), 'by_exhaustion': by})
    summary = {}
    for t in transitions:
        for b, v in t['by_exhaustion'].items():
            s = summary.setdefault(b, {'n': 0, 'retained': 0}); s['n'] += v['n']; s['retained'] += v['retained']

    portfolio = None
    if os.path.exists(HOLDINGS):
        try:
            h = jload(HOLDINGS)
            names = []
            for item in h.get('names', []):
                c = str(item.get('code') or '').strip().upper()
                if not c: continue
                stat = codes.get(c)
                names.append({'code': c, 'qty': item.get('qty'), 'avg': item.get('avg'), 'rated': bool(stat),
                              'rank': stat['rank'] if stat else None, 'rs': stat['rs'] if stat else None,
                              'in_top': bool(stat and stat['in_top'][0]), 'gap_to_cut': (stat['rank'] - N) if stat and stat['rank'] > N else 0,
                              'status': stat['status'] if stat else 'unrated', 'verdict': stat['verdict'] if stat else None})
            portfolio = {'updated': h.get('updated'), 'source': h.get('source'), 'names': names,
                         'in_top': sum(1 for n in names if n['in_top']), 'of': len(names)}
        except Exception as e:
            portfolio = {'error': f'could not read holdings.json: {e}'}

    doc = {
        'schema': 'swingedge-rs-tracker/1', 'updated': dt.date.today().isoformat(), 'scan_date': weeks[0]['date'], 'top': N, 'mode': mode,
        'note': 'Top-N RS persistence and exhaustion tracker. Built by tools/build_rs_tracker.py from data/scans/; read by the RS Leaders page.',
        'anchors': [({'date': w['date'], 'file': w['file'], 'gap_days': w['gap_days']} if w else None) for w in weeks],
        'flags': FLAGS,
        'cutoff': {'rank': N, 'rs': cut['rs'] if cut else None, 'score': cut['score'] if cut else None},
        'counts': {'retained': sum(1 for v in leaders if v['status'] in ('holding', 'slipping')), 'new': sum(1 for v in leaders if v['status'] == 'new'),
                   'slipping': sum(1 for v in leaders if v['status'] == 'slipping'), 'dropped': sum(1 for v in left if v['status'] == 'dropped'),
                   'fading': sum(1 for v in left if v['status'] == 'fading'), 'rated': len(codes)},
        'leaders': leaders, 'left': left,
        'evidence': {'transitions': transitions, 'summary': summary},
        'portfolio': portfolio,
        # every rated name, short keys to keep the file small: n name · i industry · rk rank · h RS by week · k rank by week ·
        # st streak · w weeks in top · wk weakening weeks · d rank Δ1w · dd RS Δ1d · s status · f flags · ex label · t/tp/tt trend · v verdict
        'codes': {c: {'n': v['name'], 'i': v['industry'], 'rs': v['rs'], 'rk': v['rank'], 'h': v['rs_hist'], 'k': v['rank_hist'], 'st': v['streak'],
                      'w': v['weeks_in_top'], 'wk': v['weakening_weeks'], 'd': v['rank_d1w'], 'dd': v['rs_d1d'], 's': v['status'], 'f': v['flags'],
                      'ex': v['ex_label'], 't': v['trend']['label'], 'tp': v['trend']['pass'], 'tt': v['trend']['total'], 'v': v['verdict'],
                      'r1w': v['r1w'], 'fh': v['from_52wh']} for c, v in codes.items()},
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(',', ':')); fh.write('\n')

    if a.quiet: return
    wk = ' · '.join(f"{w['date']}" if w else '—' for w in weeks)
    print(f"scan {doc['scan_date']} · top {N} · anchors: {wk}")
    k = doc['counts']
    print(f"retained {k['retained']} (slipping {k['slipping']}) · new {k['new']} · dropped {k['dropped']} · fading {k['fading']} · cut-off rank {N} = RS {doc['cutoff']['rs']}")
    hist = lambda v: ' '.join(f"{x:>2}" if x is not None else ' —' for x in v['rs_hist'][:5])
    print(f"{'#':>3} {'Code':<12}{'RS hist (newest first)':<22}{'rank':>5}{'Δ1w':>5}{'wks':>4}  {'status':<9}{'exhaustion':<11}{'trend':<12}verdict   flags")
    for v in leaders + left:
        d = '-' if v['rank_d1w'] is None else f"{-v['rank_d1w']:+d}"
        print(f"{v['rank']:>3} {v['code']:<12}{hist(v):<22}{v['rank']:>5}{d:>5}{v['weeks_in_top']:>4}  {v['status']:<9}{v['ex_label']:<11}{(v['trend']['label'] or '-'):<12}{v['verdict']:<9} {' '.join(v['flags'])}")
    if transitions:
        print('evidence (top-N retention one week later, by exhaustion flags at the time):')
        for t in transitions:
            by = ' · '.join(f"{b} flags {v['retained']}/{v['n']}" for b, v in sorted(t['by_exhaustion'].items()))
            print(f"  {t['from']} → {t['to']}: {t['retained']}/{t['of']} stayed   {by}")
    if portfolio and 'names' in portfolio:
        print(f"portfolio: {portfolio['in_top']} of {portfolio['of']} holdings inside the top {N}")


if __name__ == '__main__':
    main()
