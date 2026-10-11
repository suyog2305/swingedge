#!/usr/bin/env python3
"""
kite_sync.py - your Zerodha holdings and positions into .secrets/, on your own machine, through the
FREE Kite Connect "Personal" API. Nothing here is ever committed or published.

    python tools/kite_sync.py            # opens the Kite login in your browser; after you log in it writes
                                         # .secrets/holdings.json, .secrets/positions.json, .secrets/kite_session.json
    python tools/kite_sync.py --cached   # reuse today's access token (no browser) if it is still valid
    python tools/kite_sync.py --status   # say whether a usable token is on file, write nothing

SETUP, ONCE
  1. https://developers.kite.trade -> Create new app -> type "Personal". Zerodha's product page (read 11 Oct 2026)
     prices Personal at 0 rupees: orders, GTT, alerts, margins and portfolio (holdings, positions). It carries no
     market data; the candles come from Yahoo in positions_review.py, so the paid "Connect" plan is not needed.
     Redirect URL for the app:  http://127.0.0.1:5577/kite
  2. Save the key and secret as .secrets/kite_app.json  ->  {"api_key": "...", "api_secret": "..."}
     (.secrets/ is gitignored. Never paste the key, the secret or a token into a chat.)

EVERY TRADING DAY  Zerodha ends every API session at 6 AM, so one login a day is the floor; nothing here can
  run unattended before you have logged in. Run this once in the morning (or whenever you want the book
  refreshed), then `python tools/positions_review.py`.

WHAT IT WRITES  (all under .secrets/, all gitignored)
  holdings.json      {"updated", "source": "Kite", "names": [{"code", "qty", "avg", "last", "exchange"}]}
                     - the shape the app's "Import holdings" and tools/holdings_check.py read
  positions.json     {"updated", "net": [...], "day": [...]}  - Kite's own position rows, as returned
  kite_session.json  {"api_key", "access_token", "login_time"} - today's token, so --cached works
  kite_holdings_raw.json - Kite's own holdings rows, as returned

READ-ONLY. This tool calls only GET /portfolio/holdings and /portfolio/positions and the token exchange.
It never places, modifies or cancels anything. STDLIB ONLY (python -s runs it).
"""
import argparse, datetime as dt, hashlib, http.server, io, json, os, sys, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEC = os.path.join(ROOT, '.secrets')
APP = os.path.join(SEC, 'kite_app.json')
SESSION = os.path.join(SEC, 'kite_session.json')
HOLDINGS = os.path.join(SEC, 'holdings.json')
POSITIONS = os.path.join(SEC, 'positions.json')
RAW = os.path.join(SEC, 'kite_holdings_raw.json')
PORT = 5577
PATH = '/kite'
API = 'https://api.kite.trade'
LOGIN = 'https://kite.zerodha.com/connect/login?v=3&api_key='

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from holdings_check import names_from  # noqa: E402  (the one place that defines the holdings shape)


def jload(p, default=None):
    try:
        with io.open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write('\n')


def kite(method, path, api_key, token=None, data=None):
    """One Kite Connect v3 request. Returns the parsed JSON; raises SystemExit with Kite's own message on error."""
    body = urllib.parse.urlencode(data).encode() if data else None
    headers = {'X-Kite-Version': '3', 'User-Agent': 'swingedge-kite-sync/1'}
    if token:
        headers['Authorization'] = f'token {api_key}:{token}'
    req = urllib.request.Request(API + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            doc = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get('message', '')
        except Exception:
            msg = ''
        raise SystemExit(f'  ! Kite {method} {path} -> HTTP {e.code} {msg or e.reason}'
                         + ('  (the token has expired or was logged out: run without --cached)' if e.code == 403 else ''))
    if doc.get('status') != 'success':
        raise SystemExit(f"  ! Kite {method} {path}: {doc.get('message') or doc}")
    return doc['data']


def token_valid(sess):
    """Kite kills every access token at 06:00 the next day."""
    if not sess or not sess.get('access_token') or not sess.get('login_time'):
        return False
    try:
        t = dt.datetime.fromisoformat(sess['login_time'])
    except Exception:
        return False
    now = dt.datetime.now()
    cutoff = dt.datetime.combine(t.date() + dt.timedelta(days=1), dt.time(6, 0))
    return t <= now < cutoff


def catch_request_token(timeout):
    """Serve the redirect URL on 127.0.0.1:PORT until Kite sends the request_token (or timeout seconds pass)."""
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == PATH and q.get('request_token'):
                got['token'] = q['request_token'][0]
                page = b'<h3>SwingEdge: logged in. You can close this tab.</h3>'
            else:
                page = b'<h3>SwingEdge: waiting for the Kite login redirect...</h3>'
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(page)

        def log_message(self, *a):  # keep the console clean (and the token out of it)
            pass

    srv = http.server.HTTPServer(('127.0.0.1', PORT), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    try:
        end = time.time() + timeout
        while time.time() < end and 'token' not in got:
            time.sleep(0.5)
    finally:
        srv.shutdown()
    return got.get('token')


def login(app, timeout):
    url = LOGIN + urllib.parse.quote(app['api_key'])
    print(f'  opening the Kite login in your browser (listening on http://127.0.0.1:{PORT}{PATH} for the redirect)')
    print('  if nothing opened, paste this into the browser:  ' + url)
    webbrowser.open(url)
    rt = catch_request_token(timeout)
    if not rt:
        raise SystemExit(f'  ! no login within {timeout} s - nothing written. Check that the app\'s redirect URL is '
                         f'exactly http://127.0.0.1:{PORT}{PATH}')
    checksum = hashlib.sha256((app['api_key'] + rt + app['api_secret']).encode()).hexdigest()
    data = kite('POST', '/session/token', app['api_key'], data={'api_key': app['api_key'], 'request_token': rt, 'checksum': checksum})
    sess = {'api_key': app['api_key'], 'access_token': data['access_token'], 'login_time': dt.datetime.now().isoformat(timespec='seconds'),
            'user_id': data.get('user_id')}
    jsave(SESSION, sess)
    print(f"  logged in as {data.get('user_id') or '?'}; token saved for today (expires 06:00 tomorrow)")
    return sess


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--cached', action='store_true', help="reuse today's token; fail instead of opening a login if it is gone")
    ap.add_argument('--status', action='store_true', help='report whether a usable token and the files exist, write nothing')
    ap.add_argument('--timeout', type=int, default=300, help='seconds to wait for the browser login (default 300)')
    a = ap.parse_args()

    app = jload(APP)
    sess = jload(SESSION)
    if a.status:
        print(f"app credentials: {'present' if app and app.get('api_key') and app.get('api_secret') else 'MISSING (.secrets/kite_app.json)'}")
        print(f"token: {'valid until 06:00 tomorrow' if token_valid(sess) else 'none or expired - a browser login is needed'}")
        for p in (HOLDINGS, POSITIONS):
            d = jload(p)
            print(f"{os.path.relpath(p, ROOT)}: " + (f"{d.get('updated')} ({len(d.get('names') or d.get('net') or [])} rows)" if d else 'not yet written'))
        return 0
    if not app or not app.get('api_key') or not app.get('api_secret'):
        raise SystemExit('  ! .secrets/kite_app.json is missing or incomplete - see the setup notes at the top of this file')

    if not token_valid(sess):
        if a.cached:
            raise SystemExit('  ! no valid token for today - run again without --cached to log in')
        sess = login(app, a.timeout)
    key, tok = sess['api_key'], sess['access_token']

    rows = kite('GET', '/portfolio/holdings', key, tok)
    pos = kite('GET', '/portfolio/positions', key, tok)
    names = names_from(rows)
    last = {str(r.get('tradingsymbol') or '').upper(): r for r in rows}
    for n in names:
        r = last.get(n['code']) or last.get(n['code'] + '-EQ') or {}
        n['last'] = r.get('last_price')
        n['exchange'] = r.get('exchange')
    today = dt.date.today().isoformat()
    jsave(RAW, {'updated': today, 'rows': rows})
    jsave(HOLDINGS, {'updated': today, 'source': 'Kite', 'names': names})
    jsave(POSITIONS, {'updated': today, 'net': pos.get('net') or [], 'day': pos.get('day') or []})
    open_pos = [p for p in (pos.get('net') or []) if p.get('quantity')]
    print(f'  {len(names)} holdings -> .secrets/holdings.json; {len(open_pos)} open position(s) -> .secrets/positions.json')
    print('  codes: ' + ', '.join(n['code'] for n in names))
    print('  next: python tools/positions_review.py   (or tools/holdings_check.py for the RS top-25 check alone)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
