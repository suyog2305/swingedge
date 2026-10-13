#!/usr/bin/env python3
"""
auto_weekly.py - the Weekly Report, built automatically from data the pipeline already has.

    python tools/auto_weekly.py --week-ending 2026-10-09     # build / refresh that week, upsert index.json
    python tools/auto_weekly.py                              # the newest scan's week
    python tools/auto_weekly.py --week-ending 2026-10-09 --no-us     # skip Yahoo (keeps US/ETF sections on file)
    python tools/auto_weekly.py --week-ending 2026-10-09 --dry-run   # build in memory, print, write nothing
    python tools/auto_weekly.py --status                     # the weeks on file and what a run would build

Writes library/weekly/<week-ending>.json in the swingedge-weekly/1 contract (library/weekly/README.md) and
upserts library/weekly/index.json through tools/build_weekly.py (india_item for the rows, upsert_index).
No model writes anything: every sentence is a template filled with the week's numbers.

WHERE EACH PART COMES FROM (all already in the repo after the evening pull)
  india.universe     the week's last broad scan (data/scans/<date>.json): coded rows, screener's own 1-week /
                     1-month / 1-year returns, mcap, down-from-52-week-high, all-time-high marker, price vs
                     DMA 50 / DMA 200. The app computes performers, near-high, breadth and the comparison.
  india.indices      data/markets/markets.json nse block: close on the week's last session against the last
                     close before the week began; 1M / 1Y against the last close on or before the same date a
                     month / a year earlier; vs 52W high from the closes on file.
  macros.fii_dii     markets.json fpi.daily (NSDL, dated by REPORTING day, one session after the trades):
                     equity net per reporting day of the week, debt in the cards. DII: null, no keyless source.
  macros.fx / commodities / debt
                     markets.json global (Yahoo closes: USD/INR, DXY, Brent, WTI, gold, silver, copper, US 10Y)
                     and rbi (India 10-year, repo, 91-day bill - RBI's box, kept from the first run onward).
  macros.derivatives null + macros.derivatives_note: no keyless source yet.
  us / etf           a fixed list below (edit by hand), Yahoo chart endpoint via tools/build_peers.yahoo().
  prose              data/daily/s2history.json (Stage 2 entries and exits), data/indices/series.json (tracker
                     weekly change), data/daily/breadth.json (regime word, % above the averages).

WHAT A HUMAN EDIT KEEPS
  The file records, under "auto", a short hash of every section this tool wrote. On the next run a section
  whose content no longer matches its hash was edited by hand and is kept; so is a section deleted by hand.
  A file with no "auto" record (built by hand or by build_weekly.py) only gets the sections it lacks.
  --force overwrites every section the tool owns.

STDLIB ONLY, run with `python -s` (the scheduled pull cannot see user-site packages). Fails soft: a section
whose source is missing is left as it was and listed as a warning; the exit code is 0 unless the arguments
are wrong. Facts only: no buy / sell wording anywhere.
"""
import argparse, datetime as dt, glob, hashlib, io, json, os, sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import utf8_stdio  # noqa: E402
import build_weekly as bw  # noqa: E402  (row shape + index upsert: one definition of the contract)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(ROOT, 'data', 'scans')
MARKETS = os.path.join(ROOT, 'data', 'markets', 'markets.json')
BREADTH = os.path.join(ROOT, 'data', 'daily', 'breadth.json')
S2HIST = os.path.join(ROOT, 'data', 'daily', 's2history.json')
SERIES = os.path.join(ROOT, 'data', 'indices', 'series.json')
OUT_DIR = os.path.join(ROOT, 'library', 'weekly')

TITLE = 'Weekly Markets Review'
STRAP = 'NSE indices · NSDL FPI flows · RBI rates · Yahoo FX, commodities & US closes · screener.in scan'
TAGS = ['Weekly Review', 'Built automatically', 'India + global']
MIN_NAMES = 1000          # a broad scan (same cut as build_indices / build_s2history)
MIN_MCAP = 1000           # build_weekly's default India cut, Rs Cr

# US stocks: the US-listed global names on the Global Peers page (data/peers/pairs.json). Edit by hand.
US_STOCKS = [
    ('GLW', 'Corning', 'Optical fibre & connectivity'),
    ('LITE', 'Lumentum', 'Optical fibre & connectivity'),
    ('COHR', 'Coherent', 'Optical fibre & connectivity'),
    ('MU', 'Micron', 'Memory & chips'),
    ('NVDA', 'NVIDIA', 'AI cloud & GPU servers'),
    ('NBIS', 'Nebius', 'AI cloud & GPU servers'),
    ('CRWV', 'CoreWeave', 'AI cloud & GPU servers'),
    ('SMCI', 'Super Micro Computer', 'AI cloud & GPU servers'),
    ('BE', 'Bloom Energy', 'Fuel cells & power'),
    ('GEV', 'GE Vernova', 'Power equipment'),
    ('ACN', 'Accenture', 'IT services'),
    ('CTSH', 'Cognizant', 'IT services'),
]
# ETFs for us.etf_universe and the global heatmap (etf.rows), grouped by region. Edit by hand.
ETFS = [
    ('SPY', 'S&P 500 ETF', 'US indices'), ('QQQ', 'Nasdaq 100 ETF', 'US indices'),
    ('IWM', 'Russell 2000 ETF', 'US indices'), ('DIA', 'Dow Jones ETF', 'US indices'),
    ('XLK', 'US Technology ETF', 'US sectors'), ('XLE', 'US Energy ETF', 'US sectors'),
    ('XLF', 'US Financials ETF', 'US sectors'), ('SMH', 'Semiconductor ETF', 'US sectors'),
    ('ITA', 'Aerospace & Defence ETF', 'US sectors'), ('LIT', 'Lithium & Battery ETF', 'US sectors'),
    ('INDA', 'India ETF', 'Asia-Pacific'), ('FXI', 'China Large-Cap ETF', 'Asia-Pacific'),
    ('EWJ', 'Japan ETF', 'Asia-Pacific'), ('EWY', 'South Korea ETF', 'Asia-Pacific'),
    ('EWT', 'Taiwan ETF', 'Asia-Pacific'),
    ('EEM', 'Emerging Markets ETF', 'Global'), ('EFA', 'Developed ex-US ETF', 'Global'),
    ('GLD', 'Gold ETF', 'Commodities'), ('SLV', 'Silver ETF', 'Commodities'), ('USO', 'Crude Oil ETF', 'Commodities'),
    ('TLT', 'US Treasury 20Y+ ETF', 'Bonds'), ('HYG', 'US High Yield ETF', 'Bonds'),
]

# The sections this tool owns (dotted paths). Everything else in a file is never touched.
OWNED = ['week_label', 'report_date', 'title', 'strap', 'tags', 'lead', 'headline', 'ticker',
         'macros.fii_dii', 'macros.derivatives', 'macros.derivatives_note', 'macros.fx', 'macros.commodities',
         'macros.debt', 'macros.summary',
         'india.universe', 'india.indices', 'india.top_performers', 'india.near_high', 'india.breadth',
         'us.universe', 'us.etf_universe', 'us.top_performers', 'us.etf_top',
         'comparison', 'etf', 'extra.india']
US_PATHS = {'us.universe', 'us.etf_universe', 'us.top_performers', 'us.etf_top', 'etf'}

QUIET = False
WARN = []


def say(*a):
    if not QUIET:
        print(*a, flush=True)


def warn(msg):
    WARN.append(msg)


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh, object_pairs_hook=OrderedDict)


def jtry(p):
    try:
        return jload(p)
    except (OSError, ValueError) as e:
        warn(f'{os.path.relpath(p, ROOT)}: {type(e).__name__}')
        return None


def D(s):
    return dt.date.fromisoformat(s)


def month_back(d, n=1):
    y, m = d.year, d.month - n
    while m < 1:
        m += 12; y -= 1
    for day in (d.day, 30, 29, 28):
        try:
            return dt.date(y, m, day)
        except ValueError:
            continue


# ------------------------------------------------------------------ formatting (plain text, ASCII signs)
def ind(v, d=0):
    """Indian digit grouping: 1234567.8 -> 12,34,568."""
    neg = v < 0
    s = f'{abs(v):.{d}f}'
    whole, _, frac = s.partition('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head: parts.insert(0, head)
        whole = ','.join(parts + [tail])
    return ('-' if neg else '') + whole + ('.' + frac if frac else '')


def cr(v):
    return '—' if v is None else ('+' if v >= 0 else '-') + '₹' + ind(abs(v)) + ' Cr'


def pct(v, d=2):
    return '—' if v is None else f'{v:+.{d}f}%'


def tone(v, invert=False):
    if v is None or abs(v) < 1e-9: return 'flat'
    return ('down' if v > 0 else 'up') if invert else ('up' if v > 0 else 'down')


def dshort(iso):
    d = D(iso)
    return f'{d.day} {d.strftime("%b")}'


def week_label(monday, end):
    if monday.month == end.month:
        return f'{monday.day}–{end.day} {end.strftime("%b %Y")}'
    if monday.year == end.year:
        return f'{monday.day} {monday.strftime("%b")} – {end.day} {end.strftime("%b %Y")}'
    return f'{monday.day} {monday.strftime("%b %Y")} – {end.day} {end.strftime("%b %Y")}'


def r2(v):
    return None if v is None else round(v, 2)


def chg(a, b):
    return None if a is None or not b else (a / b - 1) * 100


# ------------------------------------------------------------------ time series helpers
def at_or_before(dates, vals, day):
    """(date, value) of the last non-null value dated on or before `day` (iso), else (None, None)."""
    for i in range(len(dates) - 1, -1, -1):
        if dates[i] <= day and i < len(vals) and vals[i] is not None:
            return dates[i], vals[i]
    return None, None


def before(dates, vals, day):
    for i in range(len(dates) - 1, -1, -1):
        if dates[i] < day and i < len(vals) and vals[i] is not None:
            return dates[i], vals[i]
    return None, None


def week_stats(dates, vals, monday, end):
    """Weekly read of one aligned series: last on or before `end` vs the last before `monday`."""
    d1, v1 = at_or_before(dates, vals, end)
    d0, v0 = before(dates, vals, monday)
    if v1 is None or d1 < monday:
        return None
    inweek = [(d, v) for d, v in zip(dates, vals) if monday <= d <= end and v is not None]
    _, vm = at_or_before(dates, vals, month_back(D(end)).isoformat())
    _, vy = at_or_before(dates, vals, (D(end) - dt.timedelta(days=365)).isoformat())
    yr = [v for d, v in zip(dates, vals) if v is not None and (D(end) - dt.timedelta(days=365)).isoformat() < d <= end]
    return {'d1': d1, 'v1': v1, 'd0': d0, 'v0': v0, 'w1': chg(v1, v0), 'm1': chg(v1, vm), 'y1': chg(v1, vy),
            'hi52': max(yr) if yr else None, 'days': inweek,
            'lo': min(v for _, v in inweek) if inweek else None, 'hi': max(v for _, v in inweek) if inweek else None}


# ------------------------------------------------------------------ scan selection
def broad_scans():
    """[(date, path)] of broad scans, oldest first (a narrow seed export or a list-only pull is skipped)."""
    idx = jtry(os.path.join(SCANS, 'index.json')) or {}
    known = {s.get('date'): s.get('universe') for s in idx.get('scans', []) if s.get('date')}
    out = []
    for p in sorted(glob.glob(os.path.join(SCANS, '20*.json'))):
        date = os.path.basename(p)[:-5]
        n = known.get(date)
        if n is None:   # not in the index yet (a scan written moments ago): open it
            d = jtry(p)
            n = len((d or {}).get('universe') or [])
        if n and n >= MIN_NAMES:
            out.append((date, p))
    return out


def resolve_week(week_ending, scans):
    """-> (week_end_scan_date, path, monday iso, previous week's last broad scan date or None)."""
    target = D(week_ending) if week_ending else (D(scans[-1][0]) if scans else None)
    if target is None:
        return None
    monday = target - dt.timedelta(days=target.weekday())
    inweek = [s for s in scans if monday.isoformat() <= s[0] <= target.isoformat()]
    if not inweek:
        return None
    date, path = inweek[-1]
    prev = [s[0] for s in scans if s[0] < monday.isoformat()]
    return date, path, monday.isoformat(), (prev[-1] if prev else None)


# ------------------------------------------------------------------ sections
def sec_universe(scan, date):
    rows = []
    for r in scan.get('universe') or []:
        code = bw.clean_code(r.get('code'))
        if not code or not r.get('name'): continue
        mcap = r.get('mcap')
        if mcap is not None and mcap < MIN_MCAP: continue
        f52 = r.get('from_52wh')
        item = bw.india_item(r['name'], code=code, industry=r.get('industry') or r.get('group'), mcap=mcap,
                             w1=r.get('r1w'), m1=r.get('r1m'), y1=r.get('r1y'),
                             from_52wh=(-abs(f52) if f52 is not None else None), price=r.get('price'),
                             ath=r.get('ath'), dma50=r.get('dma50'), dma200=r.get('dma200'))
        if 'near_ath' not in item and isinstance(r.get('near_ath'), bool):
            item['near_ath'] = r['near_ath']
        if item.get('w1') is None: continue
        rows.append(item)
    return OrderedDict(source=f'SwingEdge scan of {date} (screener.in export, {len(rows):,} coded names, mcap ≥ ₹1,000 Cr)',
                       as_of=date, min_mcap=MIN_MCAP, rows=rows)


def sec_indices(mk, monday, end):
    nse = (mk or {}).get('nse') or {}
    dates, series = nse.get('dates') or [], nse.get('series') or {}
    broad, sector, stats = [], [], {}
    for sid, s in series.items():
        if s.get('group') not in ('broad', 'sector'): continue
        st = week_stats(dates, s.get('c') or [], monday, end)
        if not st: continue
        stats[sid] = dict(st, name=s.get('name') or sid)
        row = OrderedDict(name=s.get('name') or sid, w1=r2(st['w1']), m1=r2(st['m1']), y1=r2(st['y1']),
                          vs_52wh=r2(chg(st['v1'], st['hi52'])))
        (broad if s['group'] == 'broad' else sector).append(row)
    return broad, sector, stats


def fpi_week(mk, monday, end):
    rows = [r for r in (((mk or {}).get('fpi') or {}).get('daily') or []) if monday <= r.get('d', '') <= end]
    return sorted(rows, key=lambda r: r['d'])


def mf_week(mk, monday, end):
    return [r for r in (((mk or {}).get('mf') or {}).get('daily') or []) if monday <= r.get('d', '') <= end]


def gstat(mk, sid, monday, end):
    g = (mk or {}).get('global') or {}
    s = (g.get('series') or {}).get(sid)
    if not s: return None
    st = week_stats(g.get('dates') or [], s.get('v') or [], monday, end)
    if st: st.update(name=s.get('name'), unit=s.get('unit'), dec=s.get('dec', 2))
    return st


def rbi_at(mk, day, strict=False):
    hist = sorted((((mk or {}).get('rbi') or {}).get('history') or []), key=lambda h: h.get('d', ''))
    ok = [h for h in hist if (h.get('d', '') < day if strict else h.get('d', '') <= day)]
    return ok[-1] if ok else None


def rbi_first(mk):
    hist = sorted(h.get('d', '') for h in (((mk or {}).get('rbi') or {}).get('history') or []) if h.get('d'))
    return hist[0] if hist else dt.date.today().isoformat()


def s2_members(s2):
    """{scan date: (calc member set, list count, list carried?)} replayed from s2history's entered/exited."""
    out, calc = {}, set()
    for x in (s2 or {}).get('days') or []:
        c = x.get('calc') or {}
        if not c.get('unknown'):
            calc = (calc | set(c.get('entered') or [])) - set(c.get('exited') or []) - set(c.get('lapsed') or [])
        l = x.get('list') or {}
        out[x['date']] = (set(calc), l.get('count'), bool(l.get('carried')), l.get('source'), c.get('count'))
    return out


def tracker_week(series, monday, end):
    if not series: return None, []
    dates = series.get('dates') or []
    mkt = week_stats(dates, (series.get('market') or {}).get('levels') or [], monday, end)
    rows = []
    for ix in series.get('indices') or []:
        st = week_stats(dates, ix.get('levels') or [], monday, end)
        if st and st['w1'] is not None:
            rows.append(OrderedDict(name=ix.get('name') or ix.get('id'), group=ix.get('group') or '', w1=r2(st['w1'])))
    rows.sort(key=lambda r: -r['w1'])
    return (mkt['w1'] if mkt else None), rows


def yahoo_week(items, monday, end):
    """[(sym, name, extra, {w1, m1, y1, last, d0, d1})] for the symbols Yahoo answers; build_peers' helper."""
    import build_peers  # deferred: only a run that wants the network imports it
    out = []
    for sym, name, extra in items:
        try:
            got = build_peers.yahoo(sym, 260)['closes']
        except Exception as e:   # one symbol must never sink the rest
            warn(f'Yahoo {sym}: {type(e).__name__}')
            continue
        dates, vals = [d for d, _ in got], [v for _, v in got]
        st = week_stats(dates, vals, monday, end)
        if not st or st['w1'] is None:
            warn(f'Yahoo {sym}: no close in the week')
            continue
        out.append((sym, name, extra, st))
    return out


# ------------------------------------------------------------------ the build
def build(week, monday, prev_scan, scan, mk, breadth, s2, series, want_us):
    end = week
    sec = OrderedDict()
    facts = {}

    # india.universe + the scan's own breadth numbers for the prose
    uni = sec_universe(scan, week)
    sec['india.universe'] = uni
    w1s = sorted(r['w1'] for r in uni['rows'])
    adv, dec = sum(1 for v in w1s if v > 0), sum(1 for v in w1s if v < 0)
    med = (w1s[len(w1s) // 2] if len(w1s) % 2 else (w1s[len(w1s) // 2 - 1] + w1s[len(w1s) // 2]) / 2) if w1s else None
    facts.update(adv=adv, dec=dec, n=len(w1s), med=med)

    # india.indices
    broad, sector, ist = sec_indices(mk, monday, end)
    n50 = ist.get('nifty50')
    trk_mkt, trk = tracker_week(series, monday, end)
    if broad or sector:
        base = n50['d0'] if n50 else None
        ix = OrderedDict(lead=(f'Weekly change of the NSE indices from NSE\'s end-of-day index file: the close on '
                               f'{dshort(end)} against the last close before the week began'
                               + (f' ({dshort(base)})' if base else '') + '. 1-month and 1-year compare with the last '
                               'close on or before the same date a month and a year earlier; vs 52W High uses the '
                               'closes on file.'),
                         broad=broad, sector=sector)
        sec_sorted = sorted([r for r in sector if r['w1'] is not None], key=lambda r: -r['w1'])
        notes = []
        if sec_sorted:
            up = sum(1 for r in sec_sorted if r['w1'] > 0)
            notes.append(OrderedDict(tone='teal', label='Sector indices this week',
                                     text=f'{up} of {len(sec_sorted)} sector indices rose. Best: '
                                          + ', '.join(f"{r['name']} {pct(r['w1'])}" for r in sec_sorted[:3])
                                          + '. Worst: ' + ', '.join(f"{r['name']} {pct(r['w1'])}" for r in sec_sorted[-3:][::-1]) + '.'))
        if trk:
            notes.append(OrderedDict(tone='blue', label='SwingEdge sub-sector trackers',
                                     text=tracker_sentence(trk, trk_mkt)))
        if notes: ix['notes'] = notes
        sec['india.indices'] = ix
    else:
        warn('markets.json nse block: no index closes for the week')
    sec['india.top_performers'] = OrderedDict(
        lead=f"Ranked by screener's 1-week return in the {dshort(week)} scan ({len(w1s):,} coded names, mcap ≥ ₹1,000 Cr).",
        limit=50, bottom_limit=20)
    sec['india.near_high'] = OrderedDict(
        lead="Names within 5.5% of their 52-week high (screener's \"down from 52-week high\" column) with mcap ≥ ₹5,000 Cr.",
        threshold=5.5, min_mcap=5000, require_code=True)
    sec['india.breadth'] = OrderedDict(
        lead="Share of each cap bucket trading above its 50-day / 200-day moving average, from the price and screener's "
             "DMA 50 / DMA 200 columns in the scan. Crossovers compare with the previous weekly file.")
    if trk:
        sec['extra.india'] = [OrderedDict(title='SwingEdge Sub-Sector Trackers — the Week', blocks=[
            OrderedDict(type='p', text='Equal-weight baskets from the Indices page (data/indices/series.json), close of '
                                       f'{dshort(end)} against the last close before the week began.'
                                       + (f' The equal-weight market line moved {pct(trk_mkt)}.' if trk_mkt is not None else '')),
            OrderedDict(type='table', cols=[OrderedDict(k='name', t='Tracker'), OrderedDict(k='group', t='Group'),
                                            OrderedDict(k='w1', t='1-Week', type='pct')], rows=trk)])]

    # macros.fii_dii
    fpi = fpi_week(mk, monday, end)
    mf = mf_week(mk, monday, end)
    eq = sum(r['eq'] for r in fpi if r.get('eq') is not None) if fpi else None
    debt = sum(r['debt'] for r in fpi if r.get('debt') is not None) if fpi else None
    pos = sum(1 for r in fpi if (r.get('eq') or 0) > 0)
    facts.update(fpi_eq=eq, fpi_debt=debt, fpi_n=len(fpi), fpi_pos=pos)
    if fpi:
        days = [OrderedDict(date=r['d'], fii_net=r2(r.get('eq')), dii_net=None) for r in fpi]
        cards = [OrderedDict(k='FPI equity, net (week)', v=cr(eq), d=f'{pos} of {len(fpi)} reporting days positive', tone=tone(eq)),
                 OrderedDict(k='FPI equity via exchanges', v=cr(sum(r.get('eq_se') or 0 for r in fpi)),
                             d='the stock-exchange route only (NSDL "eq_se")', tone=tone(sum(r.get('eq_se') or 0 for r in fpi))),
                 OrderedDict(k='FPI debt, net (week)', v=cr(debt), d='NSDL, all debt routes', tone=tone(debt)),
                 OrderedDict(k='DII, net (week)', v='not available', d='no keyless daily source', tone='flat')]
        if mf:
            m_eq = sum(r.get('eq') or 0 for r in mf)
            cards.append(OrderedDict(k='Mutual funds, equity (SEBI)', v=cr(m_eq),
                                     d=f"{len(mf)} day{'s' if len(mf) != 1 else ''} published so far ({', '.join(dshort(r['d']) for r in mf)}); one part of DII, not the total",
                                     tone=tone(m_eq)))
        sec['macros.fii_dii'] = OrderedDict(
            lead=(f'Foreign portfolio investors (FPI) in Indian equity, ₹ crore net, from NSDL\'s FPI Monitor. NSDL dates '
                  f'each row by the day custodians report it, one session after the trades. '
                  f'{len(fpi)} reporting day{"s" if len(fpi) != 1 else ""} fell in this week: a net '
                  f'{"inflow" if (eq or 0) >= 0 else "outflow"} of {cr(eq).lstrip("+-")}.'),
            days=days, cards=cards,
            note='DII is null: NSE and BSE serve the daily FII/DII cash figures only to a browser session, and this '
                 'pipeline does not pretend to be one. SEBI\'s mutual-fund figure (about ten days late) is the only keyless '
                 'domestic number.',
            notes=[OrderedDict(tone='amber', label='About the DII column',
                               text='DII figures are not collected: the exchanges serve them only to a browser session. '
                                    'The "FII Net" column here is NSDL\'s FPI equity figure by reporting date, which is '
                                    'not the same series as the exchanges\' provisional FII number.')])
    else:
        warn('markets.json fpi.daily: no reporting day in the week')

    sec['macros.derivatives'] = None
    sec['macros.derivatives_note'] = ('Derivatives positioning (FII index futures / options open interest) is not '
                                      'collected: no keyless source yet.')

    # macros.fx
    inr, dxy = gstat(mk, 'usdinr', monday, end), gstat(mk, 'dxy', monday, end)
    facts.update(inr=inr)
    if inr:
        days, prev = [], inr['v0']
        for d, v in inr['days']:
            c = chg(v, prev)
            days.append(OrderedDict(date=d, value=f'{v:.3f}' + (f' ({c:+.2f}%)' if c is not None else ''), tone=tone(c, invert=True)))
            prev = v
        cards = [OrderedDict(k='Week close', v=f"{inr['v1']:.2f}", d=f"{pct(inr['w1'])} on the week ({dshort(inr['d0'])} close {inr['v0']:.2f})" if inr['v0'] else 'no earlier close on file',
                             tone=tone(inr['w1'], invert=True)),
                 OrderedDict(k='Week range', v=f"{inr['lo']:.2f} – {inr['hi']:.2f}", d='daily closes'),
                 OrderedDict(k='Rupee this week', v=('weaker' if (inr['w1'] or 0) > 0 else 'stronger' if (inr['w1'] or 0) < 0 else 'unchanged'),
                             d='a higher USD/INR is a weaker rupee', tone=tone(inr['w1'], invert=True))]
        if dxy:
            cards.append(OrderedDict(k='Dollar index (DXY)', v=f"{dxy['v1']:.2f}", d=f"{pct(dxy['w1'])} on the week", tone='flat'))
        sec['macros.fx'] = OrderedDict(pair='USD/INR', lead='Daily closes from Yahoo (INR=X); the change in brackets is against the previous close.',
                                       days=days, cards=cards)
    else:
        warn('markets.json global usdinr: no close in the week')

    # macros.commodities
    crow, cfacts = [], {}
    for sid, label in (('brent', 'Brent crude'), ('wti', 'WTI crude'), ('gold', 'Gold'), ('silver', 'Silver'), ('copper', 'Copper')):
        st = gstat(mk, sid, monday, end)
        if not st: continue
        cfacts[sid] = st
        nd = st['dec'] if isinstance(st['dec'], int) else 2
        unit = st['unit'] or ''
        crow.append(OrderedDict(name=label, move=f"${st['v1']:,.{nd}f} ({pct(st['w1'])})", tone=tone(st['w1']),
                                note=f"{unit}, front-month future (Yahoo {dshort(st['d1'])}). Week range "
                                     f"{st['lo']:,.{nd}f} – {st['hi']:,.{nd}f}"
                                     + (f"; previous week's close {st['v0']:,.{nd}f} ({dshort(st['d0'])})." if st['v0'] else '.')))
    facts.update(comm=cfacts)
    if crow:
        sec['macros.commodities'] = OrderedDict(lead='Front-month futures closes from Yahoo, the week\'s last close against the last close before the week began.',
                                                rows=crow)
    else:
        warn('markets.json global: no commodity closes in the week')

    # macros.debt
    y_now, y_prev = rbi_at(mk, end), rbi_at(mk, monday, strict=True)
    us10 = gstat(mk, 'us10y', monday, end)
    nse = (mk or {}).get('nse') or {}
    gsec = week_stats(nse.get('dates') or [], ((nse.get('series') or {}).get('gsec10') or {}).get('c') or [], monday, end)
    facts.update(y_now=y_now, y_prev=y_prev, us10=us10)
    cards = []
    if y_now and y_now.get('y10') is not None:
        d = f"{y_now.get('name') or 'benchmark'} · RBI, {dshort(y_now['d'])}"
        d += (f"; {y_prev['y10']:.2f}% on {dshort(y_prev['d'])} ({(y_now['y10'] - y_prev['y10']) * 100:+.0f} bp)" if y_prev and y_prev.get('y10') is not None
              else f'; no earlier reading on file (RBI shows one day; the history on file starts {dshort(rbi_first(mk))})')
        cards.append(OrderedDict(k='India 10Y G-Sec yield', v=f"{y_now['y10']:.2f}%", d=d,
                                 tone=tone((y_now['y10'] - y_prev['y10']) if y_prev and y_prev.get('y10') is not None else None, invert=True)))
    if gsec and gsec.get('w1') is not None:
        cards.append(OrderedDict(k='NSE 10Y G-Sec index (price)', v=pct(gsec['w1']), d='clean price of the 10-year benchmark; price up = yield down',
                                 tone=tone(gsec['w1'])))
    if us10:
        bp = (us10['v1'] - us10['v0']) * 100 if us10['v0'] is not None else None
        cards.append(OrderedDict(k='US 10Y Treasury yield', v=f"{us10['v1']:.2f}%", d=(f'{bp:+.0f} bp on the week' if bp is not None else 'Yahoo ^TNX'),
                                 tone=tone(bp, invert=True)))
    if y_now and y_now.get('repo') is not None:
        was = y_prev.get('repo') if y_prev else None
        cards.append(OrderedDict(k='RBI repo rate', v=f"{y_now['repo']:.2f}%",
                                 d=(f"was {was:.2f}% at the last reading before the week ({dshort(y_prev['d'])})" if was is not None and abs(was - y_now['repo']) > 1e-9
                                    else 'unchanged on the readings on file' if was is not None else 'RBI current-rates box'),
                                 tone='flat'))
        if y_now.get('tbill91') is not None:
            cards.append(OrderedDict(k='91-day T-bill', v=f"{y_now['tbill91']:.2f}%", d=f"RBI, {dshort(y_now['d'])}", tone='flat'))
    if cards:
        sec['macros.debt'] = OrderedDict(lead='India from the RBI current-rates box (kept day by day from the first run) and NSE\'s 10-year G-Sec index; the US 10-year from Yahoo.',
                                         cards=cards)
    else:
        warn('no rates on file for the week')

    # stage 2 + breadth facts
    brow = ((breadth or {}).get('rows') or {}).get(week) or {}
    facts.update(breadth=brow)
    mem = s2_members(s2)
    if week in mem:
        cur, lcount, carried, src, ccount = mem[week]
        prevm = mem.get(prev_scan) if prev_scan else None
        facts.update(s2_calc=(ccount if ccount is not None else len(cur)), s2_in=(len(cur - prevm[0]) if prevm else None),
                     s2_out=(len(prevm[0] - cur) if prevm else None), s2_prev=prev_scan,
                     s2_list=lcount, s2_listsrc=src,
                     s2_list_new=any(not mem[d][2] for d in mem if prev_scan and prev_scan < d <= week),
                     s2_asof=scan.get('stage2_asof'))

    # US + ETFs
    if want_us:
        us = yahoo_week(US_STOCKS, monday, end)
        et = yahoo_week(ETFS, monday, end)
        if us:
            d0, d1 = min(x[3]['d0'] or x[3]['d1'] for x in us), max(x[3]['d1'] for x in us)
            sec['us.universe'] = OrderedDict(source=f'{len(us)} US-listed global peers (Global Peers page), Yahoo closes {dshort(d0)} → {dshort(d1)}',
                                             rows=[OrderedDict([('name', n), ('code', s), ('industry', ind_), ('w1', r2(st['w1']))]
                                                               + ([('m1', r2(st['m1']))] if st['m1'] is not None else []))
                                                   for s, n, ind_, st in us])
            sec['us.top_performers'] = OrderedDict(lead=f'A fixed list of {len(us)} US-listed names from the Global Peers page, ranked by 1-week return (the last close on or before {dshort(end)} against the last close before the week began).',
                                                   limit=50, bottom_limit=15)
        if et:
            d0, d1 = min(x[3]['d0'] or x[3]['d1'] for x in et), max(x[3]['d1'] for x in et)
            sec['us.etf_universe'] = OrderedDict(source=f'{len(et)} US-listed ETFs, Yahoo closes {dshort(d0)} → {dshort(d1)}',
                                                 rows=[OrderedDict([('name', n), ('code', s), ('w1', r2(st['w1']))]
                                                                   + ([('m1', r2(st['m1']))] if st['m1'] is not None else []))
                                                       for s, n, _, st in et])
            sec['us.etf_top'] = OrderedDict(lead='The same fixed ETF list as the global heatmap, ranked by 1-week return.', limit=50, bottom_limit=15)
            rows = [OrderedDict([('name', n), ('code', s), ('region', reg), ('w1', r2(st['w1']))]
                                + ([('m1', r2(st['m1']))] if st['m1'] is not None else [])
                                + ([('y1', r2(st['y1']))] if st['y1'] is not None else []))
                    for s, n, reg, st in et]
            srt = sorted(rows, key=lambda r: -r['w1'])
            up = sum(1 for r in rows if r['w1'] > 0)
            inda = next((r for r in rows if r['code'] == 'INDA'), None)
            facts.update(etf_up=up, etf_n=len(rows), inda=inda)
            sec['etf'] = OrderedDict(
                lead=f'{len(rows)} US-listed ETFs covering US indices and sectors, other regions, commodities and bonds; weekly change from Yahoo daily closes ({dshort(d0)} → {dshort(d1)}).',
                rows=rows,
                notes=[OrderedDict(tone='amber', label='The week',
                                   text=f"{up} of {len(rows)} rose. Best: {srt[0]['name']} {pct(srt[0]['w1'])}; worst: {srt[-1]['name']} {pct(srt[-1]['w1'])}."
                                        + (f" India in US dollars (INDA): {pct(inda['w1'])}." if inda else ''))])
        if not us and not et:
            warn('Yahoo answered for no symbol: US and ETF sections left as they were')

    # comparison
    sec['comparison'] = OrderedDict(window=4,
                                    india=OrderedDict(lead="Weekly returns of every coded name in this week's scan, joined with the earlier weekly files by NSE code."),
                                    us=OrderedDict(lead='Weekly returns of the fixed US list, joined with the earlier weekly files by ticker.'))

    # prose: lead, headline, ticker, summary
    sent = sentences(week, facts, ist, trk, trk_mkt)
    sec['lead'] = ' '.join(s for s in (sent.get('equity'), sent.get('flows'), sent.get('macro')) if s)
    hl = []
    if eq is not None:
        hl.append(OrderedDict(k='FPI Equity Net (Week)', v=cr(eq), d=f'NSDL, {len(fpi)} reporting days; DII not collected', tone=tone(eq)))
    if n50:
        hl.append(OrderedDict(k='Nifty 50 (Week)', v=pct(n50['w1']), d=f"close {ind(n50['v1'], 2)}", tone=tone(n50['w1'])))
    if inr:
        hl.append(OrderedDict(k='USD/INR', v=f"{inr['v1']:.2f}", d=f"{pct(inr['w1'])} on the week (rupee {'weaker' if (inr['w1'] or 0) > 0 else 'stronger'})", tone=tone(inr['w1'], invert=True)))
    if brow.get('regime'):
        hl.append(OrderedDict(k='Breadth Regime', v=str(brow['regime']).capitalize(),
                              d=f"{brow.get('pct50')}% above 50-DMA, {brow.get('pct200')}% above 200-DMA", tone='flat'))
    sec['headline'] = hl[:4]
    tk = []
    if n50: tk.append(OrderedDict(label='Nifty 50', value=ind(n50['v1'], 2), delta=f"{pct(n50['w1'])} (week)", tone=tone(n50['w1'])))
    if y_now and y_now.get('y10') is not None: tk.append(OrderedDict(label='India 10Y', value=f"{y_now['y10']:.2f}%", tone='flat'))
    if cfacts.get('brent'):
        b = cfacts['brent']; tk.append(OrderedDict(label='Brent', value=f"${b['v1']:.2f}", delta=pct(b['w1']), tone=tone(b['w1'])))
    if w1s: tk.append(OrderedDict(label='Breadth', value=f'{adv:,} Adv / {dec:,} Dec', tone='up' if adv > dec else 'down' if dec > adv else 'flat'))
    sec['ticker'] = tk
    blocks = []
    if sent.get('oneline'):
        blocks.append(OrderedDict(type='note', tone='amber', label='The week in one line', text=sent['oneline']))
    for k in ('flows', 'macro', 'equity', 'breadth', 'stage2', 'trackers'):
        if sent.get(k): blocks.append(OrderedDict(type='p', text=sent[k]))
    blocks.append(OrderedDict(type='note', tone='teal', label='How this report is built',
                              text='Built automatically by `tools/auto_weekly.py` from data the evening pull already has: the '
                                   'screener.in scan, NSE\'s index file, NSDL\'s FPI Monitor, RBI\'s current rates, Yahoo closes, '
                                   'and SwingEdge\'s own breadth, Stage 2 and tracker files. No model writes any of it: each '
                                   'sentence is a template filled with the week\'s numbers. Not collected: DII daily flows and '
                                   'derivatives positioning (no keyless source). Facts only; nothing here rates a stock.'))
    sec['macros.summary'] = OrderedDict(title='Executive Summary — The Week in Numbers', blocks=blocks)

    mon = D(monday)
    sec['week_label'] = week_label(mon, D(week))
    sec['report_date'] = dt.date.today().isoformat()
    sec['title'] = TITLE
    sec['strap'] = STRAP
    sec['tags'] = list(TAGS)
    return sec


def tracker_sentence(trk, mkt):
    top = ', '.join(f"{r['name']} ({pct(r['w1'])})" for r in trk[:3])
    bot = ', '.join(f"{r['name']} ({pct(r['w1'])})" for r in trk[-3:][::-1])
    up = sum(1 for r in trk if r['w1'] > 0)
    return (f'{up} of {len(trk)} trackers rose. Led by {top}; lagged by {bot}.'
            + (f' The equal-weight market line: {pct(mkt)}.' if mkt is not None else ''))


def sentences(week, f, ist, trk, trk_mkt):
    s = {}
    n50, mid, sml = ist.get('nifty50'), ist.get('midcap150'), ist.get('smallcap250')
    if n50:
        parts = [f"Nifty 50 {pct(n50['w1'])} to {ind(n50['v1'], 2)}"]
        if mid: parts.append(f"Midcap 150 {pct(mid['w1'])}")
        if sml: parts.append(f"Smallcap 250 {pct(sml['w1'])}")
        s['equity'] = ', '.join(parts) + f" in the week to {dshort(week)}."
    if f.get('fpi_eq') is not None:
        s['flows'] = (f"FPIs: a net equity {'inflow' if f['fpi_eq'] >= 0 else 'outflow'} of {cr(f['fpi_eq']).lstrip('+-')} "
                      f"across {f['fpi_n']} NSDL reporting day{'s' if f['fpi_n'] != 1 else ''} ({f['fpi_pos']} of {f['fpi_n']} positive)"
                      + (f"; debt {cr(f['fpi_debt'])}" if f.get('fpi_debt') is not None else '') + '.')
    m = []
    inr = f.get('inr')
    if inr: m.append(f"USD/INR {inr['v1']:.2f} ({pct(inr['w1'])}, rupee {'weaker' if (inr['w1'] or 0) > 0 else 'stronger'})")
    b = (f.get('comm') or {}).get('brent')
    if b: m.append(f"Brent ${b['v1']:.2f} ({pct(b['w1'])})")
    y, yp = f.get('y_now'), f.get('y_prev')
    if y and y.get('y10') is not None:
        m.append(f"India 10-year {y['y10']:.2f}%" + (f" ({(y['y10'] - yp['y10']) * 100:+.0f} bp)" if yp and yp.get('y10') is not None else ''))
    u = f.get('us10')
    if u and u['v0'] is not None: m.append(f"US 10-year {u['v1']:.2f}% ({(u['v1'] - u['v0']) * 100:+.0f} bp)")
    if m: s['macro'] = '; '.join(m) + '.'
    br = f.get('breadth') or {}
    if f.get('n'):
        t = (f"Of {f['n']:,} coded names in the scan, {f['adv']:,} rose and {f['dec']:,} fell on the week"
             + (f" (median {pct(f['med'])})" if f.get('med') is not None else '') + '.')
        if br.get('regime'):
            t += (f" Breadth regime: **{br['regime']}**, with {br.get('pct50')}% above the 50-DMA and {br.get('pct200')}% above the 200-DMA"
                  + (f" ({br['pct50_4w_chg']:+.1f} and {br['pct200_4w_chg']:+.1f} points over four weeks)" if br.get('pct50_4w_chg') is not None and br.get('pct200_4w_chg') is not None else '') + '.')
        s['breadth'] = t
    if f.get('s2_calc') is not None:
        t = f"Stage 2 by calculation (all 7 template checks): {f['s2_calc']} names"
        if f.get('s2_in') is not None:
            t += f", {f['s2_in']} entered and {f['s2_out']} left since {dshort(f['s2_prev'])}"
        t += '.'
        if f.get('s2_list') is not None:
            t += (f" The provider's Stage 2 list ({f['s2_list']} names) "
                  + ('was updated this week.' if f.get('s2_list_new') else
                     f"was not updated this week (the list on file is dated {dshort(f['s2_asof'])})." if f.get('s2_asof') else 'was not updated this week.'))
        s['stage2'] = t
    if trk:
        s['trackers'] = 'SwingEdge sub-sector trackers: ' + tracker_sentence(trk, trk_mkt)
    one = []
    if n50: one.append(f"Nifty 50 {pct(n50['w1'])}")
    if f.get('fpi_eq') is not None: one.append(f"FPI equity {cr(f['fpi_eq'])}")
    if inr: one.append(f"USD/INR {inr['v1']:.2f}")
    if b: one.append(f"Brent {pct(b['w1'])}")
    if br.get('regime'): one.append(f"breadth {br['regime']}")
    if one: s['oneline'] = ' · '.join(one) + '.'
    return s


# ------------------------------------------------------------------ merge with what is on file
def sig(v):
    return hashlib.sha1(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:12]


_MISSING = object()


def get_path(d, path):
    cur = d
    for k in path.split('.'):
        if not isinstance(cur, dict) or k not in cur: return _MISSING
        cur = cur[k]
    return cur


def set_path(d, path, v):
    ks = path.split('.')
    cur = d
    for k in ks[:-1]:
        if not isinstance(cur.get(k), dict): cur[k] = OrderedDict()
        cur = cur[k]
    cur[ks[-1]] = v


def merge(existing, sec, week, force, skip):
    """-> (doc, wrote[], kept[]) applying the preserve rule (see the module docstring)."""
    if existing is None:
        doc = OrderedDict([('schema', bw.SCHEMA), ('week_ending', week), ('week_label', ''), ('report_date', ''),
                           ('title', TITLE), ('strap', STRAP), ('tags', []), ('lead', ''), ('headline', []), ('ticker', []),
                           ('macros', OrderedDict()), ('india', OrderedDict()), ('us', OrderedDict()),
                           ('comparison', OrderedDict()), ('etf', OrderedDict())])
        rec = None
    else:
        doc = existing
        rec = (doc.get('auto') or {}).get('sections') if isinstance(doc.get('auto'), dict) else None
    doc['schema'], doc['week_ending'] = bw.SCHEMA, week
    for k in ('macros', 'india', 'us', 'comparison', 'etf'):
        if not isinstance(doc.get(k), dict): doc[k] = OrderedDict()
    new_rec, wrote, kept = OrderedDict(), [], []
    for path in OWNED:
        if path not in sec or path in skip:
            if rec and path in rec: new_rec[path] = rec[path]     # untouched this run: keep its record
            continue
        cur = get_path(doc, path)
        if existing is not None and not force:
            if rec is None:
                # an empty section ({} is pre-created for 'etf'/'comparison') counts as missing, so a
                # week first built while Yahoo was down still gets its ETF heatmap on a later run
                if cur is not _MISSING and cur not in ('', [], None, {}):   # a hand-built file: fill only what it lacks
                    kept.append(path); continue
            elif path in rec and (cur is _MISSING or sig(cur) != rec[path]):
                kept.append(path); continue                              # edited (or removed) by hand
            elif path not in rec and cur is not _MISSING and cur not in ('', [], None, {}):
                kept.append(path); continue                              # added by hand after the last run
        set_path(doc, path, sec[path])
        new_rec[path] = sig(sec[path])
        wrote.append(path)
    if not doc.get('extra'): doc.pop('extra', None)
    doc['auto'] = OrderedDict(tool='tools/auto_weekly.py', generated=dt.datetime.now().strftime('%Y-%m-%dT%H:%M'),
                              note='hashes of the sections this tool wrote; a section that no longer matches was edited by hand and is kept',
                              sections=new_rec)
    return doc, wrote, kept


def write_json(path, doc):
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)


def status(scans):
    idx = jtry(os.path.join(OUT_DIR, 'index.json')) or {}
    print(f"library/weekly/index.json: {len(idx.get('weeks', []))} weeks (updated {idx.get('updated')})")
    for w in idx.get('weeks', []):
        p = os.path.join(OUT_DIR, w.get('file', ''))
        d = jtry(p) if os.path.exists(p) else None
        kind = 'missing' if d is None else ('auto' if isinstance(d.get('auto'), dict) else 'hand / build_weekly')
        print(f"  {w.get('week_ending')}  {w.get('label', ''):<22} {kind}")
    r = resolve_week(None, scans)
    print('newest broad scan:', scans[-1][0] if scans else 'none',
          '-> a run with no --week-ending builds', (r[0] if r else 'nothing'))


def main():
    global QUIET
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--week-ending', help='a date in the week (normally its Friday); the file is named by the week\'s last broad scan')
    ap.add_argument('--no-us', action='store_true', help='skip Yahoo: US and ETF sections stay as they are on file')
    ap.add_argument('--dry-run', action='store_true', help='build in memory and print what would change; write nothing')
    ap.add_argument('--status', action='store_true', help='list the weeks on file; write nothing')
    ap.add_argument('--force', action='store_true', help='overwrite sections even when they were edited by hand')
    ap.add_argument('--out', default=OUT_DIR, help='output folder (default library/weekly)')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    QUIET = a.quiet
    if a.week_ending:
        try: D(a.week_ending)
        except ValueError: raise SystemExit('--week-ending must be YYYY-MM-DD')

    scans = broad_scans()
    if a.status:
        status(scans); return 0
    r = resolve_week(a.week_ending, scans)
    if not r:
        say(f'auto_weekly: no broad scan in the week of {a.week_ending or "the newest scan"}; nothing to build')
        return 0
    week, path, monday, prev_scan = r
    scan = jtry(path) or {}
    mk = jtry(MARKETS) or {}
    sec = build(week, monday, prev_scan, scan, mk, jtry(BREADTH), jtry(S2HIST), jtry(SERIES), not a.no_us)

    os.makedirs(a.out, exist_ok=True)
    out = os.path.join(a.out, f'{week}.json')
    existing = jtry(out) if os.path.exists(out) else None
    doc, wrote, kept = merge(existing, sec, week, a.force, US_PATHS if a.no_us else set())
    n_rows = len(sec['india.universe']['rows'])
    if a.dry_run:
        print(f'DRY RUN - would write {os.path.relpath(out, ROOT)} ({week_label(D(monday), D(week))}, {n_rows:,} India rows) '
              f'and upsert index.json')
        print('  would write:', ', '.join(wrote) or 'nothing')
        if kept: print('  would keep (edited by hand / hand-built):', ', '.join(kept))
        print('  lead:', doc.get('lead'))
        for w in WARN: print('  ! ' + w)
        return 0
    write_json(out, doc)
    ipath, n = bw.upsert_index(a.out, week, doc.get('week_label') or week, doc.get('title') or TITLE)
    say(f'wrote {os.path.relpath(out, ROOT)} ({os.path.getsize(out):,} bytes, {n_rows:,} India rows); index.json: {n} weeks')
    if kept: say('  kept (edited by hand / hand-built):', ', '.join(kept))
    for w in WARN: say('  ! ' + w)
    return 0


if __name__ == '__main__':
    sys.exit(main())
