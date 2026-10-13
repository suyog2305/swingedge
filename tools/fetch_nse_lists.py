#!/usr/bin/env python3
"""
fetch_nse_lists.py — four small NSE lists the page reads next to the scan.

    python tools/fetch_nse_lists.py                          # all four blocks
    python tools/fetch_nse_lists.py --date 2026-10-09        # delivery for that session (what eod.py passes)
    python tools/fetch_nse_lists.py --only holidays --quiet  # one block (the GitHub job runs this one)
    python tools/fetch_nse_lists.py --status                 # what is on file, fetch nothing

BLOCKS, SOURCES AND FILES — all public, none needs a key or a login
  events        www.nseindia.com/api/event-calendar -> data/markets/events.json
                Board meetings (results dates). Rows dated from asof-1 to asof+60, sorted by date then symbol.
                A row that drops off NSE's list (NSE drops a meeting on its own day) stays for 7 days; once its date is
                before today it is marked "past": true.
                `kind` is the purpose normalised to results / results+dividend / fund-raising / other.
  surveillance  www.nseindia.com/api/reportASM + /api/reportGSM -> data/markets/surveillance.json
                ASM (long / short term) and GSM lists, keyed by symbol. The "(13)" serial is stripped from the
                codes. `asof` is the newest list date seen. `since` is the list date, or the previous file's
                `since` while the stock stays on the same term and stage.
  holidays      www.nseindia.com/api/holiday-master?type=trading (CM segment) -> data/markets/holidays.json
                {"closed": {"2026-10-20": "Dussehra"}}, merged with the previous file: a date is never dropped.
                tools/eod.py reads it as its second session oracle (after Yahoo).
  delivery      nsearchives.nseindia.com/archives/equities/mto/MTO_DDMMYYYY.DAT -> data/daily/delivery.json
                Deliverable quantity and percentage per symbol for one session (series EQ, BE, SM, ST).
                NSE publishes it about 17:00 IST; a 404 means not yet, and the previous file is kept.

NSE's /api/ endpoints may refuse GitHub's runners, so the evening pull on the owner's PC runs this
(tools/eod.py, a "never blocks" step); the GitHub job runs only --only holidays.

STDLIB ONLY (the scheduled task cannot see user-site packages; test with `python -s`). FAILS SOFT: each
block on its own; a block that fails keeps its previous file, the failure is printed, and the exit code
stays 0 so the evening scan is never stopped by a list.
"""
import argparse, datetime as dt, io, json, os, re, sys, time
import urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import utf8_stdio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = {
    'events': os.path.join(ROOT, 'data', 'markets', 'events.json'),
    'surveillance': os.path.join(ROOT, 'data', 'markets', 'surveillance.json'),
    'holidays': os.path.join(ROOT, 'data', 'markets', 'holidays.json'),
    'delivery': os.path.join(ROOT, 'data', 'daily', 'delivery.json'),
}
BLOCKS = ('events', 'surveillance', 'holidays', 'delivery')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
API = 'https://www.nseindia.com/api/'
MTO = 'https://nsearchives.nseindia.com/archives/equities/mto/MTO_{}.DAT'
DELIVERY_SERIES = ('EQ', 'BE', 'SM', 'ST')
QUIET = False


def say(*a):
    if not QUIET:
        print(*a, flush=True)


def get(url, accept='application/json', timeout=40, tries=2):
    """Bytes of url. A 401/403/404 is an answer and is raised at once; anything else is tried again."""
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept,
                                                       'Accept-Language': 'en-US,en;q=0.9'})
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


def get_json(path):
    return json.loads(get(API + path).decode('utf-8'))


def load(block):
    try:
        with io.open(FILES[block], encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def save(block, doc):
    path = FILES[block]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))
        fh.write('\n')
    os.replace(tmp, path)                     # never a half-written file for the page to choke on
    return os.path.getsize(path)


def nse_date(s):
    """'13-Oct-2026' or '09-Oct-2026 08:14:02' -> '2026-10-13'; None when it is not a date."""
    s = str(s or '').strip().split(' ')[0]
    for f in ('%d-%b-%Y', '%d-%B-%Y', '%d-%m-%Y'):
        try:
            return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return None


def stamp():
    return dt.datetime.now(IST).strftime('%Y-%m-%dT%H:%M')


def closed_dates():
    return set((load('holidays').get('closed') or {}).keys())


def last_session(now=None):
    """The last NSE session whose close is in: today after 15:30 IST on a weekday that is not a holiday,
    else the previous such day."""
    now = now or dt.datetime.now(IST)
    closed = closed_dates()
    d = now.date()
    ok = lambda x: x.weekday() < 5 and x.isoformat() not in closed
    if ok(d) and now.time() >= dt.time(15, 30):
        return d.isoformat()
    d -= dt.timedelta(days=1)
    while not ok(d):
        d -= dt.timedelta(days=1)
    return d.isoformat()


# ---- events ---------------------------------------------------------------------------------
def kind_of(purpose):
    p = (purpose or '').lower()
    res, div = 'result' in p, 'dividend' in p
    if res and div:
        return 'results+dividend'
    if res:
        return 'results'
    if 'fund' in p:
        return 'fund-raising'
    return 'other'


def block_events(_date):
    raw = get_json('event-calendar')
    if not isinstance(raw, list) or not raw:
        raise ValueError('the event calendar came back empty or not as a list')
    today = dt.datetime.now(IST).date()
    asof = today.isoformat()
    lo, hi = (today - dt.timedelta(days=1)).isoformat(), (today + dt.timedelta(days=60)).isoformat()
    keep_from = (today - dt.timedelta(days=7)).isoformat()
    rows, seen = [], set()
    for r in raw:
        d, sym = nse_date(r.get('date')), str(r.get('symbol') or '').strip().upper()
        if not d or not sym or not (lo <= d <= hi):
            continue
        row = {'symbol': sym, 'company': (r.get('company') or '').strip(), 'purpose': (r.get('purpose') or '').strip(),
               'desc': (r.get('bm_desc') or '').strip(), 'date': d, 'kind': kind_of(r.get('purpose'))}
        if d < asof:
            row['past'] = True
        rows.append(row)
        seen.add((sym, d))
    carried = 0
    for r in load('events').get('events') or []:      # off NSE's list from the meeting day: keep for 7 days
        d, sym = r.get('date') or '', r.get('symbol') or ''
        if (sym, d) in seen or not (keep_from <= d <= asof):   # NSE drops a meeting ON its day, so today's must carry
            continue
        row = {k: v for k, v in r.items() if k != 'past'}
        if d < asof:                                    # re-marked every run: today's meeting is not past yet
            row['past'] = True
        rows.append(row)
        seen.add((sym, d))
        carried += 1
    rows.sort(key=lambda r: (r['date'], r['symbol']))
    doc = {'schema': 1, 'updated': stamp(), 'source': 'NSE event calendar', 'asof': asof, 'events': rows}
    n = save('events', doc)
    res = sum(1 for r in rows if r['kind'].startswith('results'))
    return f'{len(rows)} board meetings ({res} with results; {carried} carried after leaving the NSE list), {n:,} bytes'


# ---- surveillance -----------------------------------------------------------------------------
SERIAL = re.compile(r'\s*\(\d+\)\s*$')
ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6}


def stage_no(stage):
    """'Stage IV' -> 4; 0 when it cannot be read."""
    w = (stage or '').split()[-1:] or ['']
    return ROMAN.get(w[0].upper(), int(w[0]) if w[0].isdigit() else 0)


GSM_STAGE = re.compile(r'GSM\s*(?:-\s*)?(?:Stage\s*)?([0-9]+|[IVX]+)\b', re.I)   # "GSM 0", "GSM - VI", "GSM Stage I"


def block_surveillance(_date):
    asm_raw, gsm_raw = get_json('reportASM'), get_json('reportGSM')
    if not isinstance(asm_raw, dict) or not isinstance(gsm_raw, list):
        raise ValueError('the ASM or GSM report did not come back in the expected shape')
    old = load('surveillance')
    old_asm, old_gsm = old.get('asm') or {}, old.get('gsm') or {}
    dates = []
    asm, counts = {}, {'asm_long': 0, 'asm_short': 0, 'gsm': 0}
    for term, key in (('long', 'longterm'), ('short', 'shortterm')):
        for r in ((asm_raw.get(key) or {}).get('data') or []):
            sym = str(r.get('symbol') or '').strip().upper()
            if not sym:
                continue
            d = nse_date(r.get('asmTime'))
            dates.append(d)
            counts['asm_' + term] += 1
            stage = (r.get('asmSurvIndicator') or '').strip()
            ent = {'term': term, 'stage': stage, 'code': SERIAL.sub('', r.get('survCode') or '').strip(), 'since': d}
            o = old_asm.get(sym) or {}
            if o.get('term') == term and o.get('stage') == stage and o.get('since') and (not d or o['since'] < d):
                ent['since'] = o['since']
            # on both lists (rare): keep the higher stage; on a tie the long-term entry, which came first
            if sym in asm and stage_no(asm[sym]['stage']) >= stage_no(stage):
                continue
            asm[sym] = ent
    gsm = {}
    for r in gsm_raw:
        sym = str(r.get('symbol') or '').strip().upper()
        if not sym:
            continue
        d = nse_date(r.get('gsmTime'))
        dates.append(d)
        counts['gsm'] += 1
        code = SERIAL.sub('', r.get('survCode') or '').strip()
        desc = (r.get('survDesc') or '').strip()
        m = GSM_STAGE.search(code) or GSM_STAGE.search(desc)
        stage = f'GSM {m.group(1).upper()}' if m else (code or 'GSM')   # gsmStage ("LVIII") is NSE's serial, not the stage
        ent = {'stage': stage, 'code': code, 'desc': desc, 'since': d}
        o = old_gsm.get(sym) or {}
        if o.get('stage') == stage and o.get('since') and (not d or o['since'] < d):
            ent['since'] = o['since']
        gsm[sym] = ent
    if not asm and not gsm:
        raise ValueError('both surveillance lists came back empty')
    dates = [x for x in dates if x]
    doc = {'schema': 1, 'updated': stamp(), 'source': 'NSE ASM/GSM reports', 'asof': max(dates) if dates else None,
           'asm': dict(sorted(asm.items())), 'gsm': dict(sorted(gsm.items())), 'counts': counts}
    n = save('surveillance', doc)
    return f'ASM {counts["asm_long"]} long + {counts["asm_short"]} short, GSM {counts["gsm"]}, list of {doc["asof"]}, {n:,} bytes'


# ---- holidays ---------------------------------------------------------------------------------
def block_holidays(_date):
    raw = get_json('holiday-master?type=trading')
    rows = (raw or {}).get('CM') if isinstance(raw, dict) else None
    if not rows:
        raise ValueError('no CM list in the holiday master')
    closed = dict(load('holidays').get('closed') or {})
    before = len(closed)
    for r in rows:
        d = nse_date(r.get('tradingDate'))
        if d:
            closed[d] = (r.get('description') or '').strip() or closed.get(d) or 'Holiday'
    doc = {'schema': 1, 'updated': stamp(), 'source': 'NSE holiday master, CM segment', 'closed': dict(sorted(closed.items()))}
    if doc['closed'] == (load('holidays').get('closed') or {}):
        return f'{len(rows)} CM holidays listed; {len(closed)} dates on file, unchanged (file left as it is)'
    save('holidays', doc)
    return f'{len(rows)} CM holidays listed; {len(closed)} dates on file ({len(closed) - before} new)'


# ---- delivery ---------------------------------------------------------------------------------
def parse_mto(text):
    """{symbol: {series, traded, deliv, pct}} from an MTO file: record-type 20 rows, the series kept."""
    out = {}
    for line in text.splitlines():
        p = [x.strip() for x in line.split(',')]
        if len(p) < 7 or p[0] != '20' or p[3] not in DELIVERY_SERIES:
            continue
        try:
            row = {'series': p[3], 'traded': int(float(p[4])), 'deliv': int(float(p[5])), 'pct': round(float(p[6]), 2)}
        except ValueError:
            continue
        sym = p[2].upper()
        if sym in out and out[sym]['series'] == 'EQ':        # one symbol in two series: EQ wins
            continue
        out[sym] = row
    return out


def block_delivery(date):
    date = date or last_session()
    d = dt.date.fromisoformat(date)
    try:
        raw = get(MTO.format(d.strftime('%d%m%Y')), accept='*/*')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            old = load('delivery').get('date')
            return f'NOT YET: NSE has no delivery file for {date} (404; it appears about 17:00 IST) - kept the {old or "(none)"} file'
        raise
    rows = parse_mto(raw.decode('latin-1'))
    if len(rows) < 500:
        raise ValueError(f'only {len(rows)} rows in the {date} delivery file')
    doc = {'schema': 1, 'date': date, 'source': 'NSE MTO', 'rows': dict(sorted(rows.items()))}
    n = save('delivery', doc)
    return f'{len(rows)} symbols for {date}, {n:,} bytes'


# ---- status -----------------------------------------------------------------------------------
def status():
    e, s, h, d = load('events'), load('surveillance'), load('holidays'), load('delivery')
    print(f'  events        asof {e.get("asof")}  {len(e.get("events") or [])} rows  (updated {e.get("updated")})')
    c = s.get('counts') or {}
    print(f'  surveillance  asof {s.get("asof")}  ASM {c.get("asm_long")}+{c.get("asm_short")}  GSM {c.get("gsm")}  (updated {s.get("updated")})')
    cl = sorted((h.get('closed') or {}).keys())
    print(f'  holidays      {len(cl)} closed dates, {cl[0] if cl else "-"} .. {cl[-1] if cl else "-"}  (updated {h.get("updated")})')
    print(f'  delivery      date {d.get("date")}  {len(d.get("rows") or {})} symbols')
    return 0


def main():
    global QUIET
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--only', help='comma-separated blocks: ' + ' '.join(BLOCKS))
    ap.add_argument('--date', help='session for the delivery file, YYYY-MM-DD (default: the last session)')
    ap.add_argument('--status', action='store_true', help='print what is on file and exit')
    ap.add_argument('--quiet', action='store_true', help='print only failures and one summary line')
    a = ap.parse_args()
    QUIET = a.quiet
    if a.status:
        return status()
    only = [b.strip() for b in a.only.split(',') if b.strip()] if a.only else list(BLOCKS)
    bad = [b for b in only if b not in BLOCKS]
    if bad:
        print('unknown block(s): ' + ', '.join(bad)); return 2
    if a.date:
        try:
            dt.date.fromisoformat(a.date)
        except ValueError:
            print(f'--date {a.date}: not YYYY-MM-DD'); return 2
    fn = {'events': block_events, 'surveillance': block_surveillance, 'holidays': block_holidays,
          'delivery': block_delivery}
    order = [b for b in BLOCKS if b in only]      # holidays before delivery: the default session reads it
    order.sort(key=lambda b: b == 'delivery')
    say(f'fetch_nse_lists: {", ".join(order)}')
    failed = []
    for b in order:
        try:
            msg = fn[b](a.date)
            if msg.startswith('NOT YET'):          # said even with --quiet: the file on disk is an older session's
                failed.append(b)
                print(f'    {b}: {msg}', flush=True)
            else:
                say(f'    {b}: {msg}')
        except Exception as e:
            failed.append(b)
            print(f'    {b}: FAILED - {type(e).__name__}: {str(e)[:160]} (the previous file is kept)', flush=True)
    if QUIET:
        print(f'fetch_nse_lists: {len(order) - len(failed)}/{len(order)} blocks refreshed'
              + (f'; kept the previous file: {", ".join(failed)}' if failed else ''), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
