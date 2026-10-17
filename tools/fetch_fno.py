#!/usr/bin/env python3
"""
fetch_fno.py - derivatives positioning from NSE's static daily files (keyless, no browser session).

    python tools/fetch_fno.py                    # the newest session NSE has published
    python tools/fetch_fno.py --date 2026-10-16  # one session
    python tools/fetch_fno.py --backfill 60      # participant history for the last 60 sessions (+ index PCR for 25)
    python tools/fetch_fno.py --status           # what is on file, writes nothing

SOURCES (all plain GETs on nsearchives.nseindia.com, answered keyless from the owner's PC on 12 and 17 Oct 2026)
  content/nsccl/fao_participant_oi_DDMMYYYY.csv         open interest by participant (Client, DII, FII, Pro), about 19:15 IST
  content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip   every futures and options contract: OI, change in OI, close
  archives/fo/sec_ban/fo_secban_DDMMYYYY.csv            securities in the F&O ban for the date

WRITES data/daily/fno.json
  participants  per participant, per session: [index fut long, index fut short, stock fut long, stock fut short,
                index call long, index put long, index call short, index put short]  (contracts)
  index         NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 for the newest session: spot, futures OI and its
                change, the nearest expiry's and the nearest monthly series' put-call ratio (open interest) and max-pain strike, and all-expiry PCR
  pcr_hist      NIFTY and BANKNIFTY put-call ratio of the nearest monthly series, by session
  stocks        every stock future: close, price change, total OI over all expiries, OI change and its percent, and
                the build-up word (long build-up / short build-up / short covering / long unwinding)
  ban           securities in the ban today, with those that entered and left since the previous session

This is positioning, not cash flow: the FII figures here are contracts held in derivatives. NSE's same-day FII/DII
cash flow stays unavailable keyless. STDLIB ONLY (python -s runs it).
"""
import argparse, csv, datetime as dt, io, json, os, sys, time, urllib.error, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTF = os.path.join(ROOT, 'data', 'daily', 'fno.json')
PRICES_INDEX = os.path.join(ROOT, 'data', 'prices', 'index.json')
HOLIDAYS = os.path.join(ROOT, 'data', 'markets', 'holidays.json')
B = 'https://nsearchives.nseindia.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INDEXES = ('NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50')
PARTS = ('Client', 'DII', 'FII', 'Pro')
KEEP = 130          # sessions of participant history kept
QUIET = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from rs import utf8_stdio
except Exception:                                             # pragma: no cover
    def utf8_stdio():
        pass


def say(*a):
    if not QUIET:
        print(*a, flush=True)


def get(url, timeout=60, tries=2):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.9'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise
            last = e
        except Exception as e:
            last = e
        if k + 1 < tries:
            time.sleep(2 * (k + 1))
    raise last


def jload(p, default=None):
    try:
        with io.open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(',', ':'))
        fh.write('\n')
    os.replace(tmp, p)


def num(x):
    try:
        return float(str(x).strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------- the three files
def participant_oi(d):
    """{participant: [8 ints]} for session d (dt.date), or None when NSE has not published it."""
    try:
        raw = get(f'{B}/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv', timeout=30).decode('utf-8', 'replace')
    except urllib.error.HTTPError:
        return None
    rows = list(csv.reader(io.StringIO(raw)))
    out = {}
    for r in rows:
        if r and r[0].strip() in PARTS and len(r) >= 13:
            v = [num(x) for x in r[1:13]]
            if None in v:
                continue
            # columns: FutIdxL FutIdxS FutStkL FutStkS OptIdxCallL OptIdxPutL OptIdxCallS OptIdxPutS OptStk x4
            out[r[0].strip()] = [int(v[0]), int(v[1]), int(v[2]), int(v[3]), int(v[4]), int(v[5]), int(v[6]), int(v[7])]
    return out if len(out) == 4 else None


def ban_list(d):
    try:
        raw = get(f'{B}/archives/fo/sec_ban/fo_secban_{d:%d%m%Y}.csv', timeout=30).decode('utf-8', 'replace')
    except urllib.error.HTTPError:
        return None
    return sorted({r[1].strip() for r in csv.reader(io.StringIO(raw)) if len(r) >= 2 and r[0].strip().isdigit()})


def fo_bhav(d):
    """Parsed F&O bhavcopy rows (dicts) for session d, or None."""
    try:
        z = zipfile.ZipFile(io.BytesIO(get(f'{B}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip', timeout=120)))
    except urllib.error.HTTPError:
        return None
    text = z.read(z.namelist()[0]).decode('utf-8', 'replace')
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------------------------------------- derived numbers
def max_pain(strikes):
    """strikes {K: (call_oi, put_oi)} -> the settlement price where option writers pay least."""
    ks = sorted(strikes)
    best, best_k = None, None
    for s in ks:
        pain = sum(c * max(0.0, s - k) + p * max(0.0, k - s) for k, (c, p) in strikes.items())
        if best is None or pain < best:
            best, best_k = pain, s
    return best_k


def index_block(rows):
    out = {}
    for sym in INDEXES:
        fut = [r for r in rows if r['FinInstrmTp'] == 'IDF' and r['TckrSymb'] == sym]
        opt = [r for r in rows if r['FinInstrmTp'] == 'IDO' and r['TckrSymb'] == sym]
        if not fut and not opt:
            continue
        und = next((num(r['UndrlygPric']) for r in fut + opt if num(r['UndrlygPric'])), None)
        e = {'spot': und}
        if fut:
            fut.sort(key=lambda r: r['XpryDt'])
            near = fut[0]
            e['fut'] = {'expiry': near['XpryDt'], 'close': num(near['ClsPric']), 'prev': num(near['PrvsClsgPric']),
                        'oi': int(sum(num(r['OpnIntrst']) or 0 for r in fut)), 'oi_chg': int(sum(num(r['ChngInOpnIntrst']) or 0 for r in fut))}
        if opt:
            by_exp = {}
            for r in opt:
                by_exp.setdefault(r['XpryDt'], []).append(r)
            exp = min(by_exp)
            near = by_exp[exp]
            call = sum(num(r['OpnIntrst']) or 0 for r in near if r['OptnTp'] == 'CE')
            put = sum(num(r['OpnIntrst']) or 0 for r in near if r['OptnTp'] == 'PE')
            strikes = {}
            for r in near:
                k = num(r['StrkPric'])
                if k is None:
                    continue
                c, p = strikes.get(k, (0.0, 0.0))
                oi = num(r['OpnIntrst']) or 0
                strikes[k] = (c + oi, p) if r['OptnTp'] == 'CE' else (c, p + oi)
            call_all = sum(num(r['OpnIntrst']) or 0 for r in opt if r['OptnTp'] == 'CE')
            put_all = sum(num(r['OpnIntrst']) or 0 for r in opt if r['OptnTp'] == 'PE')
            # the monthly series (the last expiry of its month) is the steady read: the expiring weekly holds most of
            # Nifty's option OI and swings with expiry-day positioning (NIFTY all-expiry PCR 0.73 -> 1.27 in a day)
            months = {}
            for x in by_exp:
                months[x[:7]] = max(months.get(x[:7], x), x)
            mexp = min(v for v in months.values() if v >= exp)
            mrows = by_exp[mexp]
            mcall = sum(num(r['OpnIntrst']) or 0 for r in mrows if r['OptnTp'] == 'CE')
            mput = sum(num(r['OpnIntrst']) or 0 for r in mrows if r['OptnTp'] == 'PE')
            mstr = {}
            for r in mrows:
                k = num(r['StrkPric'])
                if k is None:
                    continue
                c, pp = mstr.get(k, (0.0, 0.0))
                oi = num(r['OpnIntrst']) or 0
                mstr[k] = (c + oi, pp) if r['OptnTp'] == 'CE' else (c, pp + oi)
            e['opt'] = {'expiry': exp, 'call_oi': int(call), 'put_oi': int(put), 'pcr': round(put / call, 2) if call else None,
                        'pcr_all': round(put_all / call_all, 2) if call_all else None, 'max_pain': max_pain(strikes) if strikes else None,
                        'm_expiry': mexp, 'pcr_m': round(mput / mcall, 2) if mcall else None, 'max_pain_m': max_pain(mstr) if mstr else None}
        out[sym] = e
    return out


def stock_block(rows):
    by = {}
    for r in rows:
        if r['FinInstrmTp'] == 'STF':
            by.setdefault(r['TckrSymb'], []).append(r)
    out = []
    for sym, fs in by.items():
        fs.sort(key=lambda r: r['XpryDt'])
        near = fs[0]
        close, prev = num(near['ClsPric']), num(near['PrvsClsgPric'])
        oi = sum(num(r['OpnIntrst']) or 0 for r in fs)
        chg = sum(num(r['ChngInOpnIntrst']) or 0 for r in fs)
        if not close or not prev or oi <= 0:
            continue
        pchg = round(100 * (close - prev) / prev, 2)
        base = oi - chg
        opct = round(100 * chg / base, 1) if base > 0 else None
        if pchg == 0 or chg == 0:
            kind = None
        elif pchg > 0:
            kind = 'long build-up' if chg > 0 else 'short covering'
        else:
            kind = 'short build-up' if chg > 0 else 'long unwinding'
        out.append({'s': sym, 'px': close, 'chg': pchg, 'oi': int(oi), 'oi_chg': int(chg), 'oi_pct': opct, 'k': kind})
    out.sort(key=lambda x: -x['oi'] * x['px'])
    return out


# ---------------------------------------------------------------------------------------------- sessions
def sessions():
    s = (jload(PRICES_INDEX, {}) or {}).get('dates') or []
    return [x for x in s if x]


def last_published(doc_dates):
    """The newest session to try: today's session if it is a weekday past 19:30 IST, else the last weekday."""
    now = dt.datetime.now(IST)
    closed = set((jload(HOLIDAYS, {}) or {}).get('closed') or {})
    d = now.date()
    if d.weekday() < 5 and now.time() < dt.time(19, 30):
        d -= dt.timedelta(days=1)
    while d.weekday() > 4 or d.isoformat() in closed:
        d -= dt.timedelta(days=1)
    return d


def add_participants(doc, d):
    p = participant_oi(d)
    if not p:
        return False
    P = doc.setdefault('participants', {'dates': [], **{k: [] for k in PARTS}})
    iso = d.isoformat()
    if iso in P['dates']:
        i = P['dates'].index(iso)
        for k in PARTS:
            P[k][i] = p[k]
    else:
        # keep the columns sorted by date (a backfill arrives newest first)
        j = next((i for i, x in enumerate(P['dates']) if x > iso), len(P['dates']))
        P['dates'].insert(j, iso)
        for k in PARTS:
            P[k].insert(j, p[k])
    if len(P['dates']) > KEEP:
        cut = len(P['dates']) - KEEP
        P['dates'] = P['dates'][cut:]
        for k in PARTS:
            P[k] = P[k][cut:]
    return True


def add_pcr(doc, d, idx):
    H = doc.setdefault('pcr_hist', {'dates': [], 'NIFTY': [], 'BANKNIFTY': []})
    iso = d.isoformat()
    vals = {k: ((idx.get(k) or {}).get('opt') or {}).get('pcr_m') for k in ('NIFTY', 'BANKNIFTY')}   # nearest monthly series: the steady read
    if iso in H['dates']:
        i = H['dates'].index(iso)
        for k, v in vals.items():
            H[k][i] = v
    else:
        j = next((i for i, x in enumerate(H['dates']) if x > iso), len(H['dates']))
        H['dates'].insert(j, iso)
        for k, v in vals.items():
            H[k].insert(j, v)
    if len(H['dates']) > KEEP:
        cut = len(H['dates']) - KEEP
        H['dates'] = H['dates'][cut:]
        for k in ('NIFTY', 'BANKNIFTY'):
            H[k] = H[k][cut:]


def build_day(doc, d, full=True):
    """Fold session d into doc. Returns a short outcome string."""
    got = []
    if add_participants(doc, d):
        got.append('participants')
    rows = fo_bhav(d) if full else None
    if rows:
        idx = index_block(rows)
        add_pcr(doc, d, idx)
        got.append('PCR')
        if full == 'latest':
            doc['index'] = idx
            doc['stocks'] = stock_block(rows)
            got.append('stocks')
    return got


def main():
    global QUIET
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--date', help='session YYYY-MM-DD (default: the newest NSE has published)')
    ap.add_argument('--backfill', type=int, metavar='N', help='participant history for the last N sessions; index PCR for the last 25')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    QUIET = a.quiet
    doc = jload(OUTF, {}) or {}
    if a.status:
        P = doc.get('participants') or {}
        print(f"fno.json asof {doc.get('asof')}  participants {len(P.get('dates', []))} sessions "
              f"({(P.get('dates') or ['-'])[0]} .. {(P.get('dates') or ['-'])[-1]})  stocks {len(doc.get('stocks') or [])}  "
              f"pcr_hist {len((doc.get('pcr_hist') or {}).get('dates', []))}  ban {len((doc.get('ban') or {}).get('today', []))}")
        return 0
    d = dt.date.fromisoformat(a.date) if a.date else last_published(None)
    if a.backfill:
        ss = [x for x in sessions() if x <= d.isoformat()][-a.backfill:]
        for n, iso in enumerate(reversed(ss)):
            day = dt.date.fromisoformat(iso)
            if iso in ((doc.get('participants') or {}).get('dates') or []) and n >= 25:
                continue
            got = build_day(doc, day, full=(n < 25) and iso not in ((doc.get('pcr_hist') or {}).get('dates') or []))
            say(f'  {iso}: {", ".join(got) or "not published"}')
            time.sleep(0.4)
    # the target session: everything, and it becomes the "latest" block
    got = build_day(doc, d, full='latest')
    if 'stocks' not in got:
        if a.backfill or got:
            jsave(OUTF, doc)
        say(f'NOT YET: NSE has not published the F&O files for {d} (kept {doc.get("asof")}).')
        return 0
    b = ban_list(d)
    if b is not None:
        ex = doc.get('ban') or {}
        if ex.get('date') == d.isoformat():
            prev = ex.get('prev')                                        # a rerun of the same session keeps its comparison list
        elif ex.get('date'):
            prev = ex.get('today')
        else:
            prev = None
        if prev is None:                                                 # nothing on file: read the previous session's list
            earlier = [x for x in sessions() if x < d.isoformat()]
            if earlier:
                prev = ban_list(dt.date.fromisoformat(earlier[-1]))
        doc['ban'] = {'date': d.isoformat(), 'today': b, 'prev': prev,
                      'entered': sorted(set(b) - set(prev)) if prev is not None else [],
                      'left': sorted(set(prev) - set(b)) if prev is not None else []}
        got.append('ban')
    doc.update({'schema': 1, 'updated': dt.datetime.now(IST).strftime('%Y-%m-%dT%H:%M'), 'asof': d.isoformat(),
                'source': 'NSE participant OI, F&O bhavcopy, F&O ban list'})
    order = ['schema', 'updated', 'source', 'asof', 'participants', 'index', 'pcr_hist', 'stocks', 'ban']
    jsave(OUTF, {k: doc[k] for k in order if k in doc})
    say(f'{d}: {", ".join(got)} -> data/daily/fno.json ({os.path.getsize(OUTF) // 1024} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
