#!/usr/bin/env python3
"""
fetch_markets.py — everything the Markets page reads, in one file.

    python tools/fetch_markets.py                 # top up data/markets/markets.json
    python tools/fetch_markets.py --only nse,fpi  # just these blocks (nse global rbi fpi sectors mf)
    python tools/fetch_markets.py --days 400      # calendar days of daily history to keep
    python tools/fetch_markets.py --only fpi --fresh   # throw that block away and fetch it again
    python tools/fetch_markets.py --status        # what is on file, fetch nothing

The page answers one question: what is moving what. So this file keeps the Indian indices, the
outside drivers (US yields, the dollar and the rupee, crude, metals, the big foreign markets)
and where foreign money went, side by side and on the same dates. It draws no conclusion.

BLOCKS AND SOURCES — all public, none needs a key or a login
  nse      NSE's own end-of-day index file, one CSV per session
           (nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv): close, turnover
           and P/E for the broad, sector, thematic and factor indices, India VIX and the 10-year
           G-Sec benchmark. A weekday with no file was a holiday and is remembered as closed.
           The file appears in the evening; until it does, the session's closes for the indices
           Yahoo carries are filled in from Yahoo and marked provisional, then replaced.
  global   Yahoo's chart endpoint, daily closes: S&P 500, Nasdaq, Dow, Nikkei, Hang Seng,
           Shanghai, KOSPI, DAX, FTSE, the EM and India ETFs in dollars; US 3-month, 5, 10 and
           30-year yields; VIX; DXY, USD/INR, USD/JPY; Brent, WTI, natural gas, gold, silver,
           copper, aluminium (front-month futures). Unofficial, like every Yahoo series here.
  rbi      The "Current Rates" box on rbi.org.in: the benchmark G-Sec yields, the 91-day bill,
           the repo rate, the call-rate band and the reference rupee rate. RBI shows one day, so
           this block keeps each day's reading and the history grows from the first run.
  fpi      NSDL's FPI Monitor: net investment by foreign portfolio investors for every reporting
           date (the archive form returns a month at a time) and the calendar-year monthly table.
           NSDL's date is the day custodians REPORT, one session after the trades.
  sectors  NSDL's fortnightly sector-wise FPI report: equity assets under custody and net
           investment for each of 24 sectors. A new report appears about a week after the 15th
           and the month-end; the six newest are kept.
  mf       SEBI's daily mutual-fund trend: net equity and debt investment by mutual funds — the
           only public daily number for domestic institutions without a login. SEBI publishes it
           about ten days late and shows one day, so this block also accumulates.
  Not here: the exchanges' same-day FII/DII cash figures. NSE and BSE serve them only to a
  browser session, and this tool does not pretend to be one.

STDLIB ONLY. The scheduled task cannot see user-site packages (see eod.py), so nothing outside
the standard library is imported. Test with `python -s`.

FAILS SOFT. Each block is fetched on its own. A source that does not answer leaves that block
as it was, the failure is listed under "errors" with the time, and the exit code stays 0: a
missing bond yield must never stop the evening scan. The output file is its own cache — a run
fetches only the dates and reports it does not already hold.
"""
import argparse, csv, datetime as dt, html.parser, io, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import utf8_stdio  # noqa: E402  (stdout/stderr as UTF-8, so a cp1252 pipe cannot end the run on a print)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'markets', 'markets.json')
SCHEMA = 'swingedge-markets/1'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
QUIET = False
MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
          'october', 'november', 'december']

# id, the index's name in NSE's file (lower case), label, group, Yahoo symbol for the provisional fill
NSE = [
    ('nifty50', 'nifty 50', 'Nifty 50', 'broad', '^NSEI'),
    ('next50', 'nifty next 50', 'Nifty Next 50', 'broad', '^NSMIDCP'),
    ('nifty500', 'nifty 500', 'Nifty 500', 'broad', '^CRSLDX'),
    ('midcap150', 'nifty midcap 150', 'Nifty Midcap 150', 'broad', 'NIFTYMIDCAP150.NS'),
    ('smallcap250', 'nifty smallcap 250', 'Nifty Smallcap 250', 'broad', 'NIFTYSMLCAP250.NS'),
    ('microcap250', 'nifty microcap 250', 'Nifty Microcap 250', 'broad', 'NIFTY_MICROCAP250.NS'),
    ('bank', 'nifty bank', 'Nifty Bank', 'sector', '^NSEBANK'),
    ('pvtbank', 'nifty private bank', 'Nifty Private Bank', 'sector', 'NIFTY_PVT_BANK.NS'),
    ('psubank', 'nifty psu bank', 'Nifty PSU Bank', 'sector', '^CNXPSUBANK'),
    ('finserv', 'nifty financial services', 'Nifty Financial Services', 'sector', 'NIFTY_FIN_SERVICE.NS'),
    ('it', 'nifty it', 'Nifty IT', 'sector', '^CNXIT'),
    ('auto', 'nifty auto', 'Nifty Auto', 'sector', '^CNXAUTO'),
    ('fmcg', 'nifty fmcg', 'Nifty FMCG', 'sector', '^CNXFMCG'),
    ('pharma', 'nifty pharma', 'Nifty Pharma', 'sector', '^CNXPHARMA'),
    ('healthcare', 'nifty healthcare index', 'Nifty Healthcare', 'sector', 'NIFTY_HEALTHCARE.NS'),
    ('metal', 'nifty metal', 'Nifty Metal', 'sector', '^CNXMETAL'),
    ('energy', 'nifty energy', 'Nifty Energy', 'sector', '^CNXENERGY'),
    ('oilgas', 'nifty oil & gas', 'Nifty Oil & Gas', 'sector', 'NIFTY_OIL_AND_GAS.NS'),
    ('realty', 'nifty realty', 'Nifty Realty', 'sector', '^CNXREALTY'),
    ('media', 'nifty media', 'Nifty Media', 'sector', '^CNXMEDIA'),
    ('consdur', 'nifty consumer durables', 'Nifty Consumer Durables', 'sector', 'NIFTY_CONSR_DURBL.NS'),
    ('infra', 'nifty infrastructure', 'Nifty Infrastructure', 'sector', '^CNXINFRA'),
    ('pse', 'nifty pse', 'Nifty PSE', 'sector', '^CNXPSE'),
    ('capgoods', 'nifty capital goods', 'Nifty Capital Goods', 'sector', None),
    ('chemicals', 'nifty chemicals', 'Nifty Chemicals', 'sector', None),
    ('cement', 'nifty cement', 'Nifty Cement', 'sector', None),
    ('power', 'nifty power', 'Nifty Power', 'sector', None),
    ('telecom', 'nifty telecommunications', 'Nifty Telecom', 'sector', None),
    ('construction', 'nifty construction', 'Nifty Construction', 'sector', None),
    ('consserv', 'nifty consumer services', 'Nifty Consumer Services', 'sector', None),
    ('translog', 'nifty transportation & logistics', 'Nifty Transport & Logistics', 'sector', None),
    ('capmkt', 'nifty capital markets', 'Nifty Capital Markets', 'sector', None),
    ('defence', 'nifty india defence', 'Nifty India Defence', 'theme', 'NIFTY_IND_DEFENCE.NS'),
    ('mfg', 'nifty india manufacturing', 'Nifty India Manufacturing', 'theme', 'NIFTY_INDIA_MFG.NS'),
    ('consumption', 'nifty india consumption', 'Nifty India Consumption', 'theme', '^CNXCONSUM'),
    ('commodities', 'nifty commodities', 'Nifty Commodities', 'theme', '^CNXCMDT'),
    ('digital', 'nifty india digital', 'Nifty India Digital', 'theme', None),
    ('ev', 'nifty ev & new age automotive', 'Nifty EV & New Age Auto', 'theme', None),
    ('railpsu', 'nifty india railways psu', 'Nifty India Railways PSU', 'theme', None),
    ('housing', 'nifty housing', 'Nifty Housing', 'theme', None),
    ('cpse', 'nifty cpse', 'Nifty CPSE', 'theme', 'NIFTY_CPSE.NS'),
    ('mnc', 'nifty mnc', 'Nifty MNC', 'theme', '^CNXMNC'),
    ('highbeta', 'nifty high beta 50', 'Nifty High Beta 50', 'factor', None),
    ('lowvol', 'nifty low volatility 50', 'Nifty Low Volatility 50', 'factor', None),
    ('alpha50', 'nifty alpha 50', 'Nifty Alpha 50', 'factor', None),
    ('momentum', 'nifty200 momentum 30', 'Nifty200 Momentum 30', 'factor', None),
    ('eqw50', 'nifty50 equal weight', 'Nifty 50 Equal Weight', 'factor', None),
    ('nifty50usd', 'nifty50 usd', 'Nifty 50 in US$', 'factor', None),
    ('vix', 'india vix', 'India VIX', 'vol', '^INDIAVIX'),
    ('gsec10', 'nifty 10 yr benchmark g-sec (clean price)', 'India 10Y G-Sec, clean price', 'rates', None),
    ('gsec10tr', 'nifty 10 yr benchmark g-sec', 'India 10Y G-Sec, total return', 'rates', None),
    ('gseccomp', 'nifty composite g-sec index', 'India G-Sec composite', 'rates', None),
]
NSE_PE_SERIES = ('nifty50', 'nifty500', 'midcap150', 'smallcap250')   # a P/E history is kept for these only
NSE_HOSTS = ('https://nsearchives.nseindia.com', 'https://archives.nseindia.com')

# id, Yahoo symbol, label, group, unit, decimals
GLOBAL = [
    ('sp500', '^GSPC', 'S&P 500', 'equity', 'index', 2),
    ('nasdaq', '^IXIC', 'Nasdaq Composite', 'equity', 'index', 2),
    ('dow', '^DJI', 'Dow Jones', 'equity', 'index', 2),
    ('nikkei', '^N225', 'Nikkei 225', 'equity', 'index', 2),
    ('hangseng', '^HSI', 'Hang Seng', 'equity', 'index', 2),
    ('shanghai', '000001.SS', 'Shanghai Composite', 'equity', 'index', 2),
    ('kospi', '^KS11', 'KOSPI', 'equity', 'index', 2),
    ('dax', '^GDAXI', 'DAX', 'equity', 'index', 2),
    ('ftse', '^FTSE', 'FTSE 100', 'equity', 'index', 2),
    ('em', 'EEM', 'Emerging markets (EEM)', 'equity', 'US$', 2),
    ('inda', 'INDA', 'India in US$ (INDA)', 'equity', 'US$', 2),
    ('us10y', '^TNX', 'US 10-year yield', 'rates', '%', 3),
    ('us5y', '^FVX', 'US 5-year yield', 'rates', '%', 3),
    ('us3m', '^IRX', 'US 3-month bill', 'rates', '%', 3),
    ('us30y', '^TYX', 'US 30-year yield', 'rates', '%', 3),
    ('vixus', '^VIX', 'VIX (US)', 'vol', 'index', 2),
    ('dxy', 'DX-Y.NYB', 'Dollar index (DXY)', 'fx', 'index', 2),
    ('usdinr', 'INR=X', 'USD/INR', 'fx', 'INR per US$', 3),
    ('usdjpy', 'JPY=X', 'USD/JPY', 'fx', 'JPY per US$', 2),
    ('brent', 'BZ=F', 'Brent crude', 'energy', 'US$/bbl', 2),
    ('wti', 'CL=F', 'WTI crude', 'energy', 'US$/bbl', 2),
    ('natgas', 'NG=F', 'Natural gas (Henry Hub)', 'energy', 'US$/MMBtu', 3),
    ('gold', 'GC=F', 'Gold', 'metals', 'US$/oz', 1),
    ('silver', 'SI=F', 'Silver', 'metals', 'US$/oz', 2),
    ('copper', 'HG=F', 'Copper', 'metals', 'US$/lb', 3),
    ('aluminium', 'ALI=F', 'Aluminium', 'metals', 'US$/t', 0),
]

SOURCES = {
    'nse': {'label': 'NSE end-of-day index file', 'url': 'https://www.nseindia.com/all-reports'},
    'global': {'label': 'Yahoo Finance chart data', 'url': 'https://finance.yahoo.com/'},
    'rbi': {'label': 'RBI current rates', 'url': 'https://www.rbi.org.in/'},
    'fpi': {'label': 'NSDL FPI Monitor, daily and monthly', 'url': 'https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx'},
    'sectors': {'label': 'NSDL fortnightly sector-wise FPI report', 'url': 'https://www.fpi.nsdl.co.in/web/Reports/FPI_Fortnightly_Selection.aspx'},
    'mf': {'label': 'SEBI mutual-fund daily trend', 'url': 'https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doMfd=yes&type=1'},
}
BLOCKS = ('nse', 'global', 'rbi', 'fpi', 'sectors', 'mf')


def say(*a):
    if not QUIET:
        print(*a, flush=True)


def get(url, timeout=45, data=None, headers=None, tries=2):
    """Bytes of url. A 403/404 is an answer and is raised at once; anything else is tried again."""
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept': '*/*', **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            last = e
        except Exception as e:
            last = e
        if k + 1 < tries:
            time.sleep(1.5 * (k + 1))
    raise last


def num(v):
    """'1,23,456.7' -> 123456.7, '(9569.57)' -> -9569.57, '-' or '' -> None."""
    s = str(v if v is not None else '').replace(',', '').replace('Rs.', '').strip()
    if not s or s in ('-', '--', 'NA', 'N.A.'):
        return None
    neg = s.startswith('(') and s.endswith(')')
    try:
        x = float(s.strip('()'))
    except ValueError:
        return None
    return -x if neg else x


class Tables(html.parser.HTMLParser):
    """Every <table> on a page as rows of cell texts."""
    def __init__(self):
        super().__init__(); self.tables = []; self.stack = []; self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.stack.append([]); self.tables.append(self.stack[-1])
        elif tag == 'tr' and self.stack:
            self.stack[-1].append([])
        elif tag in ('td', 'th') and self.stack and self.stack[-1]:
            self.cell = []
        elif tag == 'br' and self.cell is not None:
            self.cell.append(' ')

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None and self.stack and self.stack[-1]:
            self.stack[-1][-1].append(re.sub(r'\s+', ' ', ''.join(self.cell)).strip()); self.cell = None
        elif tag == 'table' and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def tables_of(text):
    p = Tables(); p.feed(text)
    return [[r for r in tb if any(c for c in r)] for tb in p.tables]


def month_no(word):
    w = word.strip().lower()[:3]
    for i, m in enumerate(MONTHS):
        if m[:3] == w:
            return i + 1
    return None


# ------------------------------------------------------------------ NSE end-of-day index file
def nse_day(day):
    """{index name lower: (close, turnover, pe, pb, dy)} for one session, or None if NSE has no file."""
    last = None
    for host in NSE_HOSTS:
        try:
            raw = get(f'{host}/content/indices/ind_close_all_{day:%d%m%Y}.csv', timeout=40)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e; continue
        except Exception as e:
            last = e; continue
        rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig', 'replace'))))
        if not rows or 'Index Name' not in rows[0]:
            last = RuntimeError('not the index file'); continue
        h = {c.strip(): i for i, c in enumerate(rows[0])}
        ci, ti = h['Closing Index Value'], h.get('Turnover (Rs. Cr.)')
        pe, pb, dy = h.get('P/E'), h.get('P/B'), h.get('Div Yield')
        cell = lambda r, i: num(r[i]) if i is not None and i < len(r) else None
        out = {}
        for r in rows[1:]:
            if len(r) > ci and r[0].strip():
                out[re.sub(r'\s+', ' ', r[0]).strip().lower()] = (cell(r, ci), cell(r, ti), cell(r, pe), cell(r, pb), cell(r, dy))
        return out
    raise last or RuntimeError('NSE did not answer')


def yahoo_daily(symbol, days):
    """[(iso date, close)] for the last `days` calendar days, dated in the exchange's own time zone."""
    now = int(time.time())
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='')
           + f'?period1={now - (days + 6) * 86400}&period2={now + 86400}&interval=1d')
    res = json.loads(get(url, timeout=35))['chart']['result'][0]
    off = int((res.get('meta') or {}).get('gmtoffset') or 0)
    ts = res.get('timestamp') or []
    cl = (res.get('indicators', {}).get('quote') or [{}])[0].get('close') or []
    out = {}
    for t, c in zip(ts, cl):
        if c is not None:
            out[dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()] = float(c)
    return sorted(out.items())


def block_nse(doc, days, today):
    old = doc.get('nse') or {}
    table = {}                                    # iso date -> {id: (close, turnover, pe)}
    for i, d in enumerate(old.get('dates') or []):
        if d == old.get('provisional'):
            continue                              # a provisional day is always fetched again
        row = {}
        for sid, s in (old.get('series') or {}).items():
            c = s['c'][i] if i < len(s.get('c') or []) else None
            if c is not None:
                row[sid] = (c, (s.get('t') or [None] * (i + 1))[i] if s.get('t') else None,
                            (s.get('pes') or [None] * (i + 1))[i] if s.get('pes') else None)
        if row:
            table[d] = row
    closed = set(old.get('closed') or [])
    latest = dict(old.get('latest') or {})
    by_name = {name: sid for sid, name, *_ in NSE}
    first = today - dt.timedelta(days=days)
    want = [first + dt.timedelta(days=k) for k in range(days + 1)]
    want = [d for d in want if d.weekday() < 5 and d.isoformat() not in table and d.isoformat() not in closed]
    say(f'    nse: {len(table)} sessions on file, {len(want)} weekday(s) to ask for')
    got = fails = 0
    for d in want:
        iso = d.isoformat()
        try:
            day = nse_day(d)
        except Exception as e:
            fails += 1
            if fails >= 4:
                raise RuntimeError(f'NSE stopped answering after {got} file(s): {type(e).__name__}: {e}')
            continue
        if day is None:
            if (today - d).days > 3:
                closed.add(iso)                   # an old weekday with no file was a holiday; a recent one may just be late
            continue
        row = {}
        for name, v in day.items():
            sid = by_name.get(name)
            if sid and v[0] is not None:
                row[sid] = (round(v[0], 2), None if v[1] is None else round(v[1]), v[2])
                if not latest.get('date') or iso >= latest['date']:
                    latest.setdefault('vals', {})[sid] = {'pe': v[2], 'pb': v[3], 'dy': v[4]}
        if row:
            table[iso] = row; got += 1
            if not latest.get('date') or iso >= latest['date']:
                latest['date'] = iso
        if got and got % 40 == 0:
            say(f'      ... {got} files')
        time.sleep(0.12)
    # the session NSE has not published yet: fill what Yahoo carries, marked provisional
    provisional = None
    try:
        nifty = yahoo_daily('^NSEI', 12)
        session = nifty[-1][0] if nifty else None
    except Exception:
        session = None
    if session and session not in table and session >= max(table or ['']):
        row = {}
        for sid, _n, _l, _g, sym in NSE:
            if not sym:
                continue
            try:
                pts = dict(yahoo_daily(sym, 6))
                if session in pts:
                    row[sid] = (round(pts[session], 2), None, None)
            except Exception:
                pass
        if len(row) >= 5:
            table[session] = row; provisional = session
            say(f'      {session}: NSE has not published the index file yet; {len(row)} closes filled from Yahoo, provisional')
    keep = sorted(d for d in table if d >= first.isoformat())
    series = {}
    for sid, _name, label, group, _sym in NSE:
        c = [table[d].get(sid, (None,))[0] for d in keep]
        if not any(v is not None for v in c):
            continue
        s = {'name': label, 'group': group, 'c': c}
        t = [table[d].get(sid, (None, None))[1] for d in keep]
        if any(v is not None for v in t):
            s['t'] = t
        if sid in NSE_PE_SERIES:
            s['pes'] = [table[d].get(sid, (None, None, None))[2] for d in keep]
        v = (latest.get('vals') or {}).get(sid) or {}
        for k in ('pe', 'pb', 'dy'):
            if v.get(k) is not None:
                s[k] = v[k]
        series[sid] = s
    official = [d for d in keep if d != provisional]
    say(f'    nse: +{got} file(s); {len(keep)} sessions, {len(series)} indices, through {keep[-1] if keep else "-"}')
    return {'asof': keep[-1] if keep else None, 'official_asof': official[-1] if official else None, 'provisional': provisional,
            'dates': keep, 'closed': sorted(d for d in closed if d >= first.isoformat()),
            'latest': {'date': latest.get('date'), 'vals': latest.get('vals') or {}}, 'series': series}


# ------------------------------------------------------------------ global markets (Yahoo)
def block_global(doc, days, today):
    old = (doc.get('global') or {})
    table, meta, errors = {}, {}, []
    for sid, sym, label, group, unit, dec in GLOBAL:
        try:
            pts = yahoo_daily(sym, days)
            if sym == '^TNX':
                pts = [(d, v / 10.0 if v > 20 else v) for d, v in pts]      # CBOE quotes x10 on some feeds
            if len(pts) < 20:
                raise RuntimeError(f'only {len(pts)} points')
        except Exception as e:
            errors.append(f'{label} ({sym}): {type(e).__name__}: {str(e)[:80]}')
            o = (old.get('series') or {}).get(sid)
            pts = [(d, v) for d, v in zip(old.get('dates') or [], (o or {}).get('v') or []) if v is not None]
            if not pts:
                continue
            meta[sid] = {'stale': True}
        table[sid] = dict(pts)
        meta.setdefault(sid, {}).update({'name': label, 'group': group, 'unit': unit, 'dec': dec, 'symbol': sym})
        time.sleep(0.08)
    first = (today - dt.timedelta(days=days)).isoformat()
    # weekdays only: Yahoo returns the odd Saturday FX tick (USD/INR on 10 Oct 2026), which moved `asof` to a day
    # no other series has and gave USD/INR a bogus 1-day move. Weekday holidays stay - some markets trade on them.
    dates = sorted({d for pts in table.values() for d in pts if d >= first and dt.date.fromisoformat(d).weekday() < 5})
    series = {}
    for sid in table:
        s = dict(meta[sid]); s['v'] = [None if table[sid].get(d) is None else round(table[sid][d], s['dec'] + 1) for d in dates]
        series[sid] = s
    say(f'    global: {len(series)} series, {len(dates)} dates through {dates[-1] if dates else "-"}'
        + (f'; {len(errors)} did not answer' if errors else ''))
    return {'asof': dates[-1] if dates else None, 'dates': dates, 'series': series}, errors


# ------------------------------------------------------------------ RBI current rates
def block_rbi(doc, days, today):
    old = doc.get('rbi') or {}
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', get('https://www.rbi.org.in/', timeout=60).decode('utf-8', 'replace')))
    gs = [{'name': f'{c}% GS {y}', 'mat': int(y), 'y': float(v)} for c, y, v in
          re.findall(r'(\d{1,2}\.\d{2})% GS (20\d\d)\s*:\s*(\d{1,2}\.\d+)\s*%', text)]
    if not gs:
        raise RuntimeError('no G-Sec yields found on the RBI home page - the layout has changed')
    seen, gsec = set(), []
    for g in gs:
        if g['name'] not in seen:
            seen.add(g['name']); gsec.append(g)
    m = re.search(r'((?:%s)\w*)\s+(\d{1,2}),\s*(20\d\d)\s*Government Securities Market' % '|'.join(x[:3].title() for x in MONTHS), text)
    asof = dt.date(int(m.group(3)), month_no(m.group(1)), int(m.group(2))).isoformat() if m and month_no(m.group(1)) else today.isoformat()
    year = int(asof[:4])
    ten = min(gsec, key=lambda g: abs(g['mat'] - (year + 10)))

    def find(pat):
        mm = re.search(pat, text)
        return float(mm.group(1)) if mm else None
    call = re.search(r'Call Rates\s*:\s*(\d+\.\d+)%\s*-\s*(\d+\.\d+)%', text)
    now = {'asof': asof, 'gsec': gsec, 'y10': ten['y'], 'y10_name': ten['name'],
           'repo': find(r'Policy Repo Rate\s*:\s*(\d+\.\d+)%'), 'tbill91': find(r'91 day T-bills\s*:\s*(\d+\.\d+)%'),
           'call': [float(call.group(1)), float(call.group(2))] if call else None,
           'usdinr_ref': find(r'INR / 1 USD\s*:\s*(\d+\.\d+)')}
    hist = {h['d']: h for h in (old.get('history') or [])}
    hist[asof] = {'d': asof, 'y10': ten['y'], 'name': ten['name'], 'tbill91': now['tbill91'], 'repo': now['repo']}
    first = (today - dt.timedelta(days=days)).isoformat()
    now['history'] = [hist[d] for d in sorted(hist) if d >= first]
    say(f"    rbi: {ten['name']} at {ten['y']}% on {asof}; {len(now['history'])} day(s) of history kept")
    return now


# ------------------------------------------------------------------ NSDL: FPI daily and monthly
DATE_RE = re.compile(r'^(\d{1,2})-([A-Za-z]{3})-(\d{4})$')


def parse_fpi_daily(text):
    """{iso reporting date: {eq, eq_se, debt, hybrid, total, fx}} in Rs crore, from any NSDL daily page."""
    out = {}
    for tb in tables_of(text):
        if not any('Net Investment' in c for r in tb[:4] for c in r):
            continue
        cur = cat = None
        for r in tb:
            m = DATE_RE.match(r[0]) if r else None
            if m and len(r) >= 6 and num(r[2]) is None:
                iso = dt.date(int(m.group(3)), month_no(m.group(2)), int(m.group(1))).isoformat()
                cur = out.setdefault(iso, {'eq': None, 'eq_se': None, 'debt': 0.0, 'hybrid': None, 'total': None, 'fx': None})
                cat, route, nums = r[1], r[2], r[3:]
                if len(r) >= 8:
                    cur['fx'] = num(r[7])
            elif len(r) >= 7:
                cur = None                        # "Total for September", "Total for 2026": a summary, not a reporting date
                continue
            elif cur is None:
                continue
            elif len(r) == 6 and num(r[1]) is None:
                cat, route, nums = r[0], r[1], r[2:]
            elif len(r) == 5:
                route, nums = r[0], r[1:]
            else:
                continue
            net = num(nums[2]) if len(nums) > 2 else None
            c, rt = cat.strip().lower(), route.strip().lower()
            if rt.startswith('total'):
                cur['total'] = net; cur = None    # the day is complete; whatever follows belongs to the next date or to a summary
            elif rt.startswith('sub-total'):
                if c == 'equity': cur['eq'] = net
                elif c.startswith('debt'): cur['debt'] = round(cur['debt'] + (net or 0), 2)
                elif c == 'hybrid': cur['hybrid'] = net
            elif c == 'equity' and rt.startswith('stock exchange'):
                cur['eq_se'] = net
    return {d: v for d, v in out.items() if v['eq'] is not None}


def block_fpi(doc, days, today):
    old = doc.get('fpi') or {}
    daily = {r['d']: r for r in (old.get('daily') or [])}
    done = set(old.get('months_done') or [])
    base = 'https://www.fpi.nsdl.co.in/web/Reports/'
    form = get(base + 'Archive.aspx', timeout=60).decode('utf-8', 'replace')
    hidden = {m.group(1): m.group(2) for m in re.finditer(r'<input[^>]*name="(__[A-Z]+)"[^>]*value="([^"]*)"', form)}
    if '__VIEWSTATE' not in hidden:
        raise RuntimeError('the NSDL archive form has changed')
    first = today - dt.timedelta(days=days)
    months, y, mth = [], first.year, first.month
    while (y, mth) <= (today.year, today.month):
        months.append((y, mth)); y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
    asked = 0
    for y, mth in months:
        key = f'{y}-{mth:02d}'
        if key in done:
            continue
        current = (y, mth) == (today.year, today.month)
        end = today if current else (dt.date(y + (mth == 12), mth % 12 + 1, 1) - dt.timedelta(days=1))
        stamp = f'{end.day:02d}-{MONTHS[end.month - 1][:3].title()}-{end.year}'
        body = dict(hidden); body.update({'__EVENTTARGET': 'btnSubmit1', '__EVENTARGUMENT': '', 'txtDate': stamp, 'hdnDate': stamp,
                                          'HdnValexceldata': '', 'hdnFlag': ''})
        page = get(base + 'Archive.aspx', timeout=90, data=urllib.parse.urlencode(body).encode(),
                   headers={'Content-Type': 'application/x-www-form-urlencoded'}).decode('utf-8', 'replace')
        rows = parse_fpi_daily(page)
        asked += 1
        for d, v in rows.items():
            daily[d] = {'d': d, **v}
        if not current and rows:
            done.add(key)
        say(f'      fpi {key}: {len(rows)} reporting date(s)')
        time.sleep(0.6)
    # the calendar-year monthly table
    monthly = old.get('monthly') or {}
    try:
        text = get(base + 'Yearwise.aspx?RptType=6', timeout=60).decode('utf-8', 'replace')
        rows, year, upto = [], None, None
        for tb in tables_of(text):
            for r in tb:
                name = r[0].replace('*', '').strip().lower() if r else ''
                if name in MONTHS and len(r) >= 13:
                    n = [num(c) for c in r[1:]]
                    rows.append({'m': MONTHS.index(name) + 1, 'eq': n[0], 'debt': round(sum(x or 0 for x in n[1:4])), 'hybrid': n[4],
                                 'total': n[-1], 'partial': '*' in r[0]})
                elif name.startswith('total -'):
                    year = int(re.sub(r'\D', '', r[0])[:4])
                elif len(r) == 1 and re.search(r'up to \d{1,2} [A-Za-z]{3} \d{4}', r[0]):
                    upto = re.search(r'up to (\d{1,2} [A-Za-z]{3} \d{4})', r[0]).group(1)
        if rows and year:
            monthly = {'year': year, 'rows': rows, 'upto': upto}
    except Exception as e:
        say(f'      fpi monthly table: {type(e).__name__}: {e} (kept the previous one)')
    keep = [daily[d] for d in sorted(daily) if d >= first.isoformat()]
    say(f'    fpi: {asked} month(s) asked for; {len(keep)} reporting dates through {keep[-1]["d"] if keep else "-"}')
    return {'asof': keep[-1]['d'] if keep else None, 'daily': keep, 'months_done': sorted(done), 'monthly': monthly,
            'note': 'NSDL dates each row by the day custodians report it, one session after the trades.'}


# ------------------------------------------------------------------ NSDL: sector-wise FPI, fortnightly
def parse_sector_report(text):
    tb = max(tables_of(text), key=lambda t: max((len(r) for r in t), default=0))
    head = [c for c in tb[0] if c]
    wide = max(len(r) for r in tb)
    block = (wide - 2) // 8                       # four blocks, each INR then USD, each `block` columns
    if block < 5 or len(head) < 4:
        raise RuntimeError('the sector report layout has changed')
    col = lambda k: 2 + k * 2 * block             # the Equity column of block k, in INR
    rows, total = [], None
    for r in tb:
        if len(r) < wide or not r[1] or r[1].strip().lower() == 'sectors':
            continue
        item = {'s': r[1].strip(), 'auc_prev': num(r[col(0)]), 'net_prev': num(r[col(1)]), 'net': num(r[col(2)]), 'auc': num(r[col(3)])}
        if item['s'].lower().startswith('grand total'):
            total = item
        elif item['auc'] is not None or item['net'] is not None:
            rows.append(item)
    period = lambda h: re.sub(r'^Net Investment\s*', '', h).strip()
    m = re.search(r'([A-Za-z]+)\s+(\d{1,2}),\s*(20\d\d)', head[3])
    asof = dt.date(int(m.group(3)), month_no(m.group(1)), int(m.group(2))).isoformat() if m and month_no(m.group(1)) else None
    return {'asof': asof, 'period': period(head[2]), 'prev_period': period(head[1]), 'rows': rows, 'total': total}


def block_sectors(doc, days, today, keep=6):
    old = {r['asof']: r for r in ((doc.get('sectors') or {}).get('reports') or []) if r.get('asof')}
    page = get('https://www.fpi.nsdl.co.in/web/Reports/FPI_Fortnightly_Selection.aspx', timeout=60).decode('utf-8', 'replace')
    opts = []
    for val, label in re.findall(r'<option[^>]*value="(~/StaticReports/[^"]+)"[^>]*>\s*([^<]+)', page):
        m = re.match(r'\s*([A-Za-z]+)\s+(\d{1,2}),\s*(20\d\d)', label)
        if m and month_no(m.group(1)):
            opts.append((dt.date(int(m.group(3)), month_no(m.group(1)), int(m.group(2))).isoformat(), val))
    opts = sorted(set(opts), reverse=True)[:keep]
    if not opts:
        raise RuntimeError('no fortnightly reports listed on the NSDL selection page')
    got = 0
    for asof, val in opts:
        if asof in old:
            continue
        url = 'https://www.fpi.nsdl.co.in/web/' + val.replace('~/', '')
        rep = parse_sector_report(get(url, timeout=90).decode('utf-8', 'replace'))
        rep['asof'] = rep['asof'] or asof; rep['url'] = url
        old[rep['asof']] = rep; got += 1
        time.sleep(0.6)
    reports = [old[d] for d in sorted(old, reverse=True)][:keep]
    say(f'    sectors: +{got} report(s); newest covers {reports[0]["period"]}')
    return {'asof': reports[0]['asof'], 'reports': reports}


# ------------------------------------------------------------------ SEBI: mutual funds, daily
def block_mf(doc, days, today):
    old = {r['d']: r for r in ((doc.get('mf') or {}).get('daily') or [])}
    text = get(SOURCES['mf']['url'], timeout=60).decode('utf-8', 'replace')
    row = None
    for tb in tables_of(text):
        for r in tb:
            m = re.match(r'^(\d{1,2}) ([A-Za-z]{3}), (20\d\d)$', r[0]) if r else None
            if m and len(r) >= 5 and r[1].strip().lower() == 'equity':
                row = {'d': dt.date(int(m.group(3)), month_no(m.group(2)), int(m.group(1))).isoformat(), 'eq': num(r[4]), 'debt': None}
            elif row and row['debt'] is None and r and r[0].strip().lower() == 'debt' and len(r) >= 4:
                row['debt'] = num(r[3])
        if row:
            break
    if not row or row['eq'] is None:
        raise RuntimeError('no daily mutual-fund row found on the SEBI page - the layout has changed')
    old[row['d']] = row
    first = (today - dt.timedelta(days=days)).isoformat()
    keep = [old[d] for d in sorted(old) if d >= first]
    say(f"    mf: {row['d']} equity net {row['eq']:+,.0f} Cr; {len(keep)} day(s) kept")
    return {'asof': keep[-1]['d'], 'daily': keep,
            'note': 'SEBI publishes one day at a time, about ten days late; this history grows from the first run.'}


# ------------------------------------------------------------------ driver
def main():
    global QUIET
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--only', help='comma-separated blocks: ' + ' '.join(BLOCKS))
    ap.add_argument('--days', type=int, default=400, help='calendar days of daily history to keep (default 400)')
    ap.add_argument('--fresh', action='store_true', help='discard what is on file for the chosen blocks and fetch them again')
    ap.add_argument('--status', action='store_true', help='print what is on file and exit')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    QUIET = a.quiet
    try:
        doc = json.load(io.open(OUT, encoding='utf-8'))
    except Exception:
        doc = {}
    if a.status:
        for b in BLOCKS:
            print(f'  {b:<8} as of {(doc.get(b) or {}).get("asof")}')
        for e in doc.get('errors') or []:
            print('  error:', e)
        return 0
    only = [b.strip() for b in a.only.split(',')] if a.only else list(BLOCKS)
    bad = [b for b in only if b not in BLOCKS]
    if bad:
        print('unknown block(s): ' + ', '.join(bad)); return 2
    if a.fresh:
        for b in only:
            doc.pop(b, None)
    now = dt.datetime.now(IST)
    today = now.date()
    stamp = now.strftime('%Y-%m-%dT%H:%M%z')
    errors = [e for e in (doc.get('errors') or []) if e.get('block') not in only]
    fn = {'nse': block_nse, 'rbi': block_rbi, 'fpi': block_fpi, 'sectors': block_sectors, 'mf': block_mf}
    say(f'fetch_markets: {", ".join(only)}')
    for b in only:
        try:
            if b == 'global':
                doc['global'], errs = block_global(doc, a.days, today)
                errors += [{'block': 'global', 'at': stamp, 'error': e} for e in errs]
            else:
                doc[b] = fn[b](doc, a.days, today)
            doc.setdefault('fetched', {})[b] = stamp
        except Exception as e:
            errors.append({'block': b, 'at': stamp, 'error': f'{type(e).__name__}: {str(e)[:160]}'})
            say(f'    {b}: FAILED - {type(e).__name__}: {str(e)[:160]} (the previous data is kept)')
    doc.update({'schema': SCHEMA, 'generated': stamp, 'errors': errors, 'sources': SOURCES})
    order = ['schema', 'generated', 'fetched', 'errors', 'sources'] + list(BLOCKS)
    out = {k: doc[k] for k in order if k in doc}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(',', ':')); fh.write('\n')
    os.replace(tmp, OUT)                          # never leave a half-written file for the page to choke on
    say(f'wrote {os.path.relpath(OUT, ROOT)} ({os.path.getsize(OUT):,} bytes)' + (f'; {len(errors)} source error(s)' if errors else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
