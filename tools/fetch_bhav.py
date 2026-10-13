#!/usr/bin/env python3
"""
fetch_bhav.py — the NSE price store: one compact JSON file per session from NSE's full bhavcopy.

    python tools/fetch_bhav.py --date 2026-10-09      # one session (what eod.py runs; a 404 = NOT YET, exit 0)
    python tools/fetch_bhav.py --backfill 260         # the last 260 weekdays, skipping what is on disk (resumable)
    python tools/fetch_bhav.py --status               # what is on file, fetch nothing

SOURCE  https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
        Public, keyless, a plain GET with a browser User-Agent. About 350 KB per session; NSE publishes it in the
        evening (a 404 before then, and on a holiday). Columns: SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE,
        HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES,
        DELIV_QTY, DELIV_PER (the header carries a space after each comma; stripped).
        Kept: series EQ, BE, SM, ST; EQ wins when a symbol appears in more than one. DELIV_PER '-' -> null.
        Prices are NOT adjusted for splits or bonuses (PREV_CLOSE is the previous session's raw close).

FILES   data/prices/YYYY-MM-DD.json   {"schema": 1, "date", "source", "n", "cols": [...], "rows": {SYMBOL: [...]}}
                                      cols = close, prev_close, open, high, low, volume, deliv_pct, turnover_lacs,
                                      series. Written once per session and never rewritten (--force to redo one).
        data/prices/index.json        {"schema": 1, "updated", "dates": [...], "first", "last", "missing": {date: why}}
                                      `dates` is rebuilt from the files on disk on every write. `missing` records a
                                      weekday older than a few days that returned 404 and is not a listed holiday
                                      (data/markets/holidays.json); the backfill skips those unless --retry-missing.

STDLIB ONLY (the scheduled task cannot see user-site packages; test with `python -s`). Writes are atomic.
"""
import argparse, csv, datetime as dt, glob, io, json, os, re, sys, time
import urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import utf8_stdio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, 'data', 'prices')
INDEX = os.path.join(STORE, 'index.json')
HOLIDAYS = os.path.join(ROOT, 'data', 'markets', 'holidays.json')
URL = 'https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{}.csv'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')
SERIES = ('EQ', 'BE', 'SM', 'ST')
COLS = ['close', 'prev_close', 'open', 'high', 'low', 'volume', 'deliv_pct', 'turnover_lacs', 'series']
SOURCE = 'NSE sec_bhavdata_full'
PAUSE = 0.3          # seconds between requests in a backfill
RECENT_DAYS = 4      # a 404 this close to today is "not yet published", never recorded as missing
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
QUIET = False


def say(*a):
    if not QUIET:
        print(*a, flush=True)


def today_ist():
    return dt.datetime.now(IST).date()


def jload(p, default=None):
    try:
        with io.open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return default


def jsave(p, doc):
    """Atomic: a kill mid-write leaves the old file (or none), never a truncated one."""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, separators=(',', ':')) + '\n')
    os.replace(tmp, p)


def closed_dates():
    return set(((jload(HOLIDAYS, {}) or {}).get('closed') or {}).keys())


def path_for(date):
    return os.path.join(STORE, f'{date}.json')


def on_disk():
    out = []
    for p in glob.glob(os.path.join(STORE, '20*.json')):
        m = re.search(r'(\d{4}-\d{2}-\d{2})\.json$', p)
        if m:
            out.append(m.group(1))
    return sorted(out)


def num(v):
    v = (v or '').strip().replace(',', '')
    if v in ('', '-'):
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    if f != f:
        return None
    return int(f) if f == int(f) and abs(f) < 1e15 else f


class NotYet(Exception):
    pass


def download(date, timeout=30):
    """The CSV text for a session. Raises NotYet on a 404, OSError / ValueError on anything else."""
    d = dt.date.fromisoformat(date)
    req = urllib.request.Request(URL.format(d.strftime('%d%m%Y')),
                                 headers={'User-Agent': UA, 'Accept': 'text/csv,text/plain,*/*'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotYet(date)
        raise
    text = raw.decode('utf-8-sig', errors='replace')
    if 'SYMBOL' not in text[:200].upper():
        raise ValueError('the response is not the bhavcopy CSV (no SYMBOL header)')
    return text


def parse(text, date):
    """{symbol: row} for series EQ/BE/SM/ST, EQ winning a duplicate symbol; also returns the file's own date."""
    rd = csv.reader(io.StringIO(text))
    head = [h.strip().upper() for h in next(rd)]
    ix = {h: i for i, h in enumerate(head)}
    need = ('SYMBOL', 'SERIES', 'DATE1', 'PREV_CLOSE', 'OPEN_PRICE', 'HIGH_PRICE', 'LOW_PRICE', 'CLOSE_PRICE',
            'TTL_TRD_QNTY', 'TURNOVER_LACS', 'DELIV_PER')
    lack = [c for c in need if c not in ix]
    if lack:
        raise ValueError('bhavcopy columns missing: ' + ', '.join(lack))
    rows, fdate = {}, None
    for r in rd:
        if len(r) < len(head):
            continue
        r = [x.strip() for x in r]
        sym, ser = r[ix['SYMBOL']].upper(), r[ix['SERIES']].upper()
        if not sym or ser not in SERIES:
            continue
        if sym in rows and rows[sym][-1] == 'EQ' and ser != 'EQ':
            continue
        close = num(r[ix['CLOSE_PRICE']])
        if close is None:
            continue
        if fdate is None:
            try:
                fdate = dt.datetime.strptime(r[ix['DATE1']], '%d-%b-%Y').date().isoformat()
            except ValueError:
                pass
        vol = num(r[ix['TTL_TRD_QNTY']])
        rows[sym] = [close, num(r[ix['PREV_CLOSE']]), num(r[ix['OPEN_PRICE']]), num(r[ix['HIGH_PRICE']]),
                     num(r[ix['LOW_PRICE']]), int(vol) if vol is not None else None, num(r[ix['DELIV_PER']]),
                     num(r[ix['TURNOVER_LACS']]), ser]
    return rows, fdate


def write_index(missing=None, drop_missing=()):
    """Rebuild data/prices/index.json from the files on disk, merging `missing` into the recorded misses."""
    old = jload(INDEX, {}) or {}
    miss = dict(old.get('missing') or {})
    miss.update(missing or {})
    dates = on_disk()
    for d in list(miss):
        if d in dates or d in drop_missing:
            miss.pop(d, None)
    doc = {'schema': 1, 'updated': dt.datetime.now(IST).strftime('%Y-%m-%dT%H:%M'), 'source': SOURCE,
           'dates': dates, 'first': dates[0] if dates else None, 'last': dates[-1] if dates else None,
           'missing': dict(sorted(miss.items()))}
    jsave(INDEX, doc)
    return doc


def fetch_one(date, force=False):
    """'ok' | 'have' | 'notyet' | 'stale:<the file's own date>' | 'error: ...'. Writes data/prices/<date>.json on 'ok'."""
    if os.path.exists(path_for(date)) and not force:
        return 'have'
    try:
        text = download(date)
    except NotYet:
        return 'notyet'
    except Exception:
        time.sleep(2)                                       # one retry for a network blip
        try:
            text = download(date)
        except NotYet:
            return 'notyet'
        except Exception as e:
            return f'error: {type(e).__name__}: {str(e)[:160]}'
    try:
        rows, fdate = parse(text, date)
    except Exception as e:
        return f'error: {type(e).__name__}: {str(e)[:160]}'
    if fdate and fdate < date:
        return f'stale:{fdate}'                             # a holiday: NSE serves the previous session's file under it
    if fdate and fdate != date:
        return f'error: the file for {date} carries the date {fdate}; not written'
    if len(rows) < 500:
        return f'error: only {len(rows)} rows in series {"/".join(SERIES)}; not written'
    jsave(path_for(date), {'schema': 1, 'date': date, 'source': SOURCE, 'n': len(rows), 'cols': COLS,
                           'rows': dict(sorted(rows.items()))})
    return 'ok'


def cmd_date(date, force):
    dt.date.fromisoformat(date)
    res = fetch_one(date, force)
    if res == 'have':
        say(f'{date}: already in the store (data/prices/{date}.json) - not rewritten')
        return 0
    if res == 'notyet':
        print(f'{date}: NOT YET - NSE has not published the bhavcopy (404: not out yet, or not a session). Nothing written.')
        return 0
    if res.startswith('stale:'):
        print(f'{date}: NOT A SESSION - NSE serves the {res[6:]} file under this date (a holiday). Nothing written.')
        return 0
    if res.startswith('error'):
        print(f'{date}: {res}')
        return 1
    doc = jload(path_for(date), {})
    write_index(drop_missing=(date,))
    say(f'{date}: {doc.get("n")} symbols written to data/prices/{date}.json')
    return 0


def weekdays_back(n, start):
    out, x = [], start
    while len(out) < n:
        if x.weekday() < 5:
            out.append(x.isoformat())
        x -= dt.timedelta(days=1)
    return out


def cmd_backfill(n, retry_missing, force):
    closed = closed_dates()
    t = today_ist()
    known_missing = set(((jload(INDEX, {}) or {}).get('missing') or {}).keys())
    days = weekdays_back(n, t)
    todo = [d for d in reversed(days) if d not in closed and (force or not os.path.exists(path_for(d)))
            and (retry_missing or d not in known_missing)]
    skipped_h = sum(1 for d in days if d in closed)
    say(f'backfill: {n} weekdays {days[-1]} .. {days[0]} - {len(todo)} to fetch '
        f'({skipped_h} listed holidays, {len(days) - skipped_h - len(todo)} on disk or recorded missing)')
    miss, got, notyet, errors, streak = {}, 0, [], [], 0
    for k, d in enumerate(todo, 1):
        if k > 1:
            time.sleep(PAUSE)
        res = fetch_one(d, force)
        if res == 'ok':
            got += 1; streak = 0
        elif res == 'notyet':
            streak = 0
            if (t - dt.date.fromisoformat(d)).days <= RECENT_DAYS:
                notyet.append(d)
            else:
                miss[d] = '404 on a weekday not listed as a holiday'
        elif res.startswith('stale:'):
            streak = 0
            miss[d] = f'not a session: NSE serves the {res[6:]} file under this date'
        elif res.startswith('error'):
            errors.append((d, res)); streak += 1
            if streak >= 5:
                print(f'  stopping: five errors in a row (last: {d} {res}). Run again to resume.')
                break
        if k % 20 == 0:
            write_index(miss)                               # resumable: the index keeps up with the files
            say(f'  [{k}/{len(todo)}] {d}: {got} written, {len(miss)} missing, {len(errors)} errors')
    doc = write_index(miss)
    say(f'backfill done: {got} written, {len(miss)} newly missing, {len(notyet)} not yet published, '
        f'{len(errors)} errors. Store: {len(doc["dates"])} sessions {doc["first"]} .. {doc["last"]}')
    for d, e in errors[:10]:
        print(f'  {d}: {e}')
    return 1 if errors and not got else 0


def size_of(paths):
    return sum(os.path.getsize(p) for p in paths if os.path.exists(p))


def cmd_status():
    idx = jload(INDEX, {}) or {}
    dates = on_disk()
    total = size_of([path_for(d) for d in dates] + [INDEX])
    print(f'price store: {len(dates)} sessions on disk' + (f', {dates[0]} .. {dates[-1]}' if dates else ''))
    print(f'  size on disk: {total / 1e6:.1f} MB ({total / max(1, len(dates)) / 1e3:.0f} KB per session)')
    print(f'  index.json: updated {idx.get("updated") or "-"}, {len(idx.get("dates") or [])} dates'
          + ('' if (idx.get('dates') or []) == dates else ' - OUT OF STEP with the files (any write rebuilds it)'))
    miss = idx.get('missing') or {}
    if miss:
        print(f'  recorded missing ({len(miss)}): ' + ', '.join(sorted(miss)[:12]) + (' ...' if len(miss) > 12 else ''))
    if dates:
        closed = closed_dates()
        x, end, gaps = dt.date.fromisoformat(dates[0]), dt.date.fromisoformat(dates[-1]), []
        have = set(dates)
        while x <= end:
            s = x.isoformat()
            if x.weekday() < 5 and s not in have and s not in closed and s not in miss:
                gaps.append(s)
            x += dt.timedelta(days=1)
        print(f'  unexplained weekday gaps: {len(gaps)}' + (': ' + ', '.join(gaps[:12]) if gaps else ''))
        last = jload(path_for(dates[-1]), {})
        print(f'  newest file: {dates[-1]}, {last.get("n")} symbols, cols {",".join(last.get("cols") or [])}')
    return 0


def main():
    global QUIET
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--date', help='fetch one session (YYYY-MM-DD)')
    g.add_argument('--backfill', type=int, metavar='N', help='fetch the last N weekdays not already on disk')
    g.add_argument('--status', action='store_true', help='print what is in the store; fetch nothing')
    ap.add_argument('--retry-missing', action='store_true', help='backfill: try again dates recorded as missing')
    ap.add_argument('--force', action='store_true', help='refetch and rewrite files already on disk')
    ap.add_argument('--quiet', action='store_true', help='print only problems and NOT YET')
    a = ap.parse_args()
    QUIET = a.quiet
    if a.status:
        return cmd_status()
    if a.backfill:
        return cmd_backfill(a.backfill, a.retry_missing, a.force)
    if a.date:
        return cmd_date(a.date, a.force)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
