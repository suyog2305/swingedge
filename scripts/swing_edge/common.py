#!/usr/bin/env python3
"""
Shared plumbing for the Swing Edge price cards (commodities & macro today; GPU & memory next).

Everything here is stdlib-only so the GitHub Actions runner and a bare laptop both work
without a pip install. A module (see commodities.py) declares its indicators; this file
supplies the fetchers, the maths and the on-disk contract:

    data/swing_edge/<module>_latest.json      the card reads this
    data/swing_edge/<module>_history.csv      one row per indicator per day, idempotent
    data/swing_edge/manual_overrides.json     values you pin by hand

A source that fails never aborts a run: the field keeps its last good value, marked stale.
"""
import csv, datetime as dt, io, json, math, os, re, statistics, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, 'data', 'swing_edge')
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
UA = 'Mozilla/5.0 (X11; Linux x86_64) SwingEdge/1.0 (+https://github.com/suyog2305/swingedge)'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

TROY_OZ_G = 31.1034768
LB_PER_TONNE = 2204.62262


def log(field, source, status, extra=''):
    print(f'field={field} source={source} status={status}' + (f' {extra}' if extra else ''), flush=True)


# ----------------------------------------------------------------------------- HTTP

class Fetcher:
    """One HTTP door for every source: short timeout, small retry, robots.txt respected,
    and a per-host record of the last successful fetch for the card footer."""

    def __init__(self, timeout=15, retries=2, sleep=1.5):
        self.timeout, self.retries, self.sleep = timeout, retries, sleep
        self.last_ok = {}
        self.errors = {}
        self._robots = {}

    def get(self, url, check_robots=False):
        if check_robots and not self.robots_allowed(url):
            raise RuntimeError('disallowed by robots.txt')
        err = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    body = r.read().decode('utf-8', 'replace')
                host = urllib.parse.urlsplit(url).netloc
                self.last_ok[host] = utcnow_iso()
                return body
            except Exception as e:  # noqa: BLE001 - any network failure is a retry
                err = e
                if attempt < self.retries:
                    time.sleep(self.sleep * (attempt + 1))
        host = urllib.parse.urlsplit(url).netloc
        self.errors[host] = short_err(err)
        raise RuntimeError(short_err(err))

    def robots_allowed(self, url):
        parts = urllib.parse.urlsplit(url)
        base = f'{parts.scheme}://{parts.netloc}'
        if base not in self._robots:
            try:
                req = urllib.request.Request(base + '/robots.txt', headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self._robots[base] = parse_robots(r.read().decode('utf-8', 'replace'))
            except Exception:  # noqa: BLE001 - no robots file means no restriction
                self._robots[base] = []
        path = parts.path or '/'
        return not any(path.startswith(rule) for rule in self._robots[base] if rule)


def parse_robots(text):
    """Disallow rules that apply to every agent (User-agent: *). Good enough for a daily pull."""
    rules, applies = [], False
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        key, _, val = line.partition(':')
        key, val = key.strip().lower(), val.strip()
        if key == 'user-agent':
            applies = val == '*'
        elif key == 'disallow' and applies:
            rules.append(val)
    return rules


def short_err(e):
    if isinstance(e, urllib.error.HTTPError):
        return f'HTTP {e.code}'
    s = str(e) or e.__class__.__name__
    return s[:120]


def utcnow_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


# ----------------------------------------------------------------------------- SOURCE PARSERS
# Each returns a sorted list of (date, float) with no None values. Fixtures for every parser
# live in tests/fixtures/swing_edge/.

def parse_yahoo_chart(text):
    d = json.loads(text)
    res = (d.get('chart') or {}).get('result') or []
    if not res:
        err = (d.get('chart') or {}).get('error') or {}
        raise ValueError(err.get('description') or 'no result')
    r = res[0]
    ts = r.get('timestamp') or []
    quote = ((r.get('indicators') or {}).get('quote') or [{}])[0]
    closes = quote.get('close') or []
    out = {}
    for t, c in zip(ts, closes):
        if c is None:
            continue
        day = dt.datetime.fromtimestamp(t, dt.timezone.utc).date()
        out[day] = float(c)  # last print of the day wins
    return sorted(out.items())


def parse_fred_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    out = []
    for r in rows[1:]:
        if len(r) < 2 or r[1] in ('.', ''):
            continue
        try:
            out.append((dt.date.fromisoformat(r[0].strip()), float(r[1])))
        except ValueError:
            continue
    return sorted(out)


def parse_stooq_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or 'Close' not in rows[0]:
        raise ValueError('not a stooq csv')
    ci, di = rows[0].index('Close'), rows[0].index('Date')
    out = []
    for r in rows[1:]:
        try:
            out.append((dt.date.fromisoformat(r[di]), float(r[ci])))
        except (ValueError, IndexError):
            continue
    return sorted(out)


_WM_ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S | re.I)
_WM_CELL = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S | re.I)
_WM_DATE = re.compile(r'(\d{1,2})\.?\s*([A-Za-z]+)\.?\s*(\d{4})')
_MONTHS = {m.lower(): i for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July',
                                                'August', 'September', 'October', 'November', 'December'], 1)}


def parse_westmetall_table(text, col=1):
    """Westmetall's public LME table: one <tr> per day, first cell a date like
    '18. September 2026', then cash settlement, 3-month, stock. `col` picks the cell."""
    out = []
    for row in _WM_ROW.findall(text):
        cells = [re.sub(r'<[^>]+>', '', c).replace('&nbsp;', ' ').strip() for c in _WM_CELL.findall(row)]
        if len(cells) <= col:
            continue
        m = _WM_DATE.search(cells[0])
        if not m:
            continue
        mon = next((v for k, v in _MONTHS.items() if k.startswith(m.group(2).lower()[:3])), None)
        if not mon:
            continue
        try:
            day = dt.date(int(m.group(3)), mon, int(m.group(1)))
            val = float(cells[col].replace(',', '').replace(' ', ''))
        except ValueError:
            continue
        out.append((day, val))
    if not out:
        raise ValueError('no rows parsed')
    return sorted(dict(out).items())


# ----------------------------------------------------------------------------- SOURCE URLS

def yahoo_url(symbol, rng='1y'):
    return ('https://query2.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol)
            + f'?range={rng}&interval=1d&events=div%2Csplit')


def fred_url(series, start):
    return f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start.isoformat()}'


def stooq_url(symbol, start, end):
    return f'https://stooq.com/q/d/l/?s={symbol}&i=d&d1={start:%Y%m%d}&d2={end:%Y%m%d}'


def westmetall_url(field):
    return f'https://www.westmetall.com/en/markdaten.php?action=table&field={field}'


# ----------------------------------------------------------------------------- HISTORY CSV

HISTORY_COLS = ['date', 'indicator', 'value', 'source', 'status']


def read_history(path):
    """{indicator: {date: (value, source, status)}}"""
    hist = {}
    if not os.path.exists(path):
        return hist
    with io.open(path, encoding='utf-8', newline='') as fh:
        for r in csv.DictReader(fh):
            try:
                d = dt.date.fromisoformat(r['date'])
                v = float(r['value'])
            except (ValueError, KeyError, TypeError):
                continue
            hist.setdefault(r['indicator'], {})[d] = (v, r.get('source', ''), r.get('status', ''))
    return hist


def write_history(path, hist):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = []
    for ind in sorted(hist):
        for d in sorted(hist[ind]):
            v, s, st = hist[ind][d]
            rows.append([d.isoformat(), ind, fmt_num(v), s, st])
    rows.sort(key=lambda r: (r[0], r[1]))
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(HISTORY_COLS)
        w.writerows(rows)


def fmt_num(v):
    s = f'{v:.6f}'.rstrip('0').rstrip('.')
    return s if s not in ('', '-0') else '0'


def read_json(path, default=None):
    try:
        with io.open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write('\n')


# ----------------------------------------------------------------------------- MATHS

def value_on_or_before(series, day):
    """series: sorted [(date, v)]. The last print at or before `day`, or None."""
    best = None
    for d, v in series:
        if d <= day:
            best = v
        else:
            break
    return best


def pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1.0) * 100.0, 3)


def diff(a, b):
    if a is None or b is None:
        return None
    return round(a - b, 4)


def series_stats(series, spark_n=90, roll_sigma=3.0, is_futures=False, mode='pct'):
    """1d/7d/30d change (calendar lookback; % by default, points when mode='diff' for yields),
    90-print z-score, sparkline, and the roll flag: a futures day whose move exceeds 3 sigma
    of the trailing 90 daily returns."""
    if not series:
        return {}
    chg = diff if mode == 'diff' else pct
    last_d, last_v = series[-1]
    prev_v = series[-2][1] if len(series) > 1 else None
    out = {
        'value': round(last_v, 6), 'date': last_d.isoformat(), 'mode': mode,
        'chg_1d': chg(last_v, prev_v),
        'chg_7d': chg(last_v, value_on_or_before(series[:-1], last_d - dt.timedelta(days=7))),
        'chg_30d': chg(last_v, value_on_or_before(series[:-1], last_d - dt.timedelta(days=30))),
        'chg_90d': chg(last_v, value_on_or_before(series[:-1], last_d - dt.timedelta(days=90))),
    }
    window = [v for _, v in series[-spark_n:]]
    out['spark'] = [round(v, 4) for v in window]
    out['spark_from'] = series[-len(window)][0].isoformat()
    if len(window) >= 20:
        mean = statistics.fmean(window)
        sd = statistics.pstdev(window)
        out['z90'] = round((last_v - mean) / sd, 2) if sd > 0 else 0.0
    else:
        out['z90'] = None
    out['roll'] = False
    if is_futures and len(window) >= 20 and out['chg_1d'] is not None:
        rets = [pct(window[i], window[i - 1]) for i in range(1, len(window) - 1)]
        rets = [r for r in rets if r is not None]
        if len(rets) >= 10:
            sd = statistics.pstdev(rets)
            if sd > 0 and abs(out['chg_1d']) > roll_sigma * sd:
                out['roll'] = True
    return out


def to_ist(iso_utc):
    t = dt.datetime.fromisoformat(iso_utc.replace('Z', '+00:00'))
    return t.astimezone(IST).strftime('%d %b %Y %H:%M IST')


# ----------------------------------------------------------------------------- UNIT CONVERSIONS

def usd_lb_to_usd_t(v):
    return None if v is None else v * LB_PER_TONNE


def usd_oz_to_inr_10g(usd_oz, usdinr):
    if usd_oz is None or usdinr is None:
        return None
    return usd_oz * usdinr / TROY_OZ_G * 10.0


def usd_oz_to_inr_kg(usd_oz, usdinr):
    if usd_oz is None or usdinr is None:
        return None
    return usd_oz * usdinr / TROY_OZ_G * 1000.0


def load_config():
    cfg = read_json(CONFIG_PATH)
    if not cfg:
        sys.exit(f'config missing or invalid: {CONFIG_PATH}')
    return cfg
