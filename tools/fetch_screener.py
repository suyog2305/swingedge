#!/usr/bin/env python3
"""
fetch_screener.py — pull your weekly screener.in export automatically and build the Edge scan file.

    python tools/fetch_screener.py --date 2026-08-21
    python tools/fetch_screener.py --dry-run          # show the plan, fetch nothing
    python tools/fetch_screener.py --commit           # also git-commit + push data/scans

WHAT IT DOES
  1. reads one or more sources from a config file you control (tools/screener_config.json),
  2. downloads each with your screener.in session cookie attached
     — screener.in's "Export to Excel" is a CSRF-protected POST, so for a "screen_url" source
       the script opens your screen page, reads the export form's token, and posts it for you,
  3. saves the result under exports/, and
  4. runs build_scan.py to produce data/scans/<date>.json.

CREDENTIALS — you stay in control, the cookie never goes through chat:
  * Put your screener.in session cookie in the SCREENER_COOKIE environment variable,
    or in a file .secrets/screener_cookie.txt (both are gitignored; the file wins if present).
  * Easiest: log in to screener.in in your browser, open DevTools → Application → Cookies →
    https://www.screener.in, and copy the value of `sessionid` into that file. (You can paste the
    whole "sessionid=...; csrftoken=..." cookie string too — either works; the script fetches a
    fresh csrftoken from your screen page if you only give sessionid.)
  * This script only READS the cookie to attach it to the request. It never prints, logs, or
    commits it. If the cookie is missing/expired screener.in redirects to its login page and the
    script stops with a clear message — nothing partial is written.

CONFIG — tools/screener_config.json (copy tools/screener_config.example.json):
  {
    "sources": [
      { "raw_query": "Market Capitalization > 1000 AND DMA 50 > 0 AND DMA 200 > 0 AND Return over 3months > -1000 AND Return over 1month > -1000 AND Return over 1week > -1000",
        "kind": "screener", "name": "Market cap > 1000 (broad market)", "min_mcap": 1000 }
    ],
    "git": { "push": true, "branch": "main" }
  }
  Give each source ONE of:
  * raw_query   — a screener.in query string (no saved screen needed). The script runs it at
                  /screen/raw/ and posts the Export-to-Excel form. RECOMMENDED — this is the broad
                  market screen. Note: screener's raw export is occasionally 503; the script retries.
  * screen_url  — OR the address-bar URL of a SAVED screener.in screen. The script finds the
                  Export-to-Excel form on that page and posts it.
  * url         — OR a direct download URL that returns a CSV/XLSX on a plain GET with your cookie
                  (e.g. a Google-Sheets ".../export?format=csv" link).
  * kind        — "screener" (the weekly universe) or "stage2" (a Stage 2 list, if exportable).
  * name/min_mcap — passed through to build_scan.py for the universe.
  screener's raw export uses a fixed column set (no 6-month / 1-year returns), so RS uses the
  3m/1m/1w blend market-wide; add those columns to a SAVED screen if you want IBD weighting.
"""
import argparse, datetime as dt, html, io, json, os, re, subprocess, sys, time
import urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
DEF_CONFIG = os.path.join(TOOLS, 'screener_config.json')
SCREENER_RAW = 'https://www.screener.in/screen/raw/'

def raw_query_url(query):
    # screener runs a raw (unsaved) query as a GET to /screen/raw/; the results page carries the export form.
    return SCREENER_RAW + '?' + urllib.parse.urlencode({'sort': '', 'order': '', 'source_id': '', 'latest': '', 'query': ' '.join(str(query).split())})
EXPORTS = os.path.join(ROOT, 'exports')
SECRET_FILE = os.path.join(ROOT, '.secrets', 'screener_cookie.txt')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) SwingEdge-fetch/1.0'

# ---------------------------------------------------------------- credentials
def load_cookie():
    placeholder = lambda c: (not c) or 'PASTE_YOUR' in c.upper()   # the scaffolded placeholder isn't a real cookie
    if os.path.exists(SECRET_FILE):
        c = io.open(SECRET_FILE, encoding='utf-8').read().strip()
        if not placeholder(c): return c, '.secrets/screener_cookie.txt'
    c = os.environ.get('SCREENER_COOKIE', '').strip()
    if not placeholder(c): return c, 'SCREENER_COOKIE env'
    return None, None

def parse_cookies(s):
    jar = {}
    for part in (s or '').split(';'):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1); jar[k.strip()] = v.strip()
        elif part:                          # a bare value is assumed to be the sessionid
            jar['sessionid'] = part
    return jar

def cookie_header(jar):
    return '; '.join(f'{k}={v}' for k, v in jar.items())

# ---------------------------------------------------------------- http (no auto-redirect, so we can spot login)
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
_OPENER = urllib.request.build_opener(_NoRedirect)

def request(method, url, jar=None, referer=None, form=None):
    headers = {'User-Agent': UA, 'Accept': '*/*'}
    if jar: headers['Cookie'] = cookie_header(jar)
    if referer:
        headers['Referer'] = referer
        p = urllib.parse.urlparse(referer); headers['Origin'] = f'{p.scheme}://{p.netloc}'
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    if data is not None: headers['Content-Type'] = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = _OPENER.open(req, timeout=60)
        return getattr(r, 'status', 200), r.headers, r.read()
    except urllib.error.HTTPError as e:      # 3xx (no-redirect) and 4xx/5xx land here
        return e.code, e.headers, e.read()
    except urllib.error.URLError as e:
        raise SystemExit(f'  ! could not reach {url}: {e.reason}')

def is_redirect(status):
    return status in (301, 302, 303, 307, 308)

def merge_setcookie(jar, headers):
    for sc in headers.get_all('Set-Cookie') or []:
        first = sc.split(';', 1)[0]
        if '=' in first:
            k, v = first.split('=', 1); jar[k.strip()] = v.strip()

def looks_like_login(data, ctype):
    if 'html' in (ctype or '').lower():
        return True
    low = data[:2048].lower()
    return b'<html' in low or (b'login' in low and b'password' in low)

# ---------------------------------------------------------------- fetch strategies
def fetch_direct(url, jar):
    """Plain GET that already returns a CSV/XLSX (Google Sheets export, a direct file, file:// fixture)."""
    status, headers, data = request('GET', url, jar)
    if is_redirect(status):
        raise SystemExit(f'  ! {url} redirected to {headers.get("Location","?")} — cookie missing/expired or not a direct download URL.')
    if looks_like_login(data, headers.get('Content-Type', '')):
        raise SystemExit('  ! got an HTML/login page, not a spreadsheet — cookie missing or expired (nothing written).')
    return data

def fetch_screen(page_url, jar, kind_hint='screen'):
    """screener.in flow: GET the screen (saved or raw-query) results page, read the export form's CSRF
    token, and POST it. Works for a saved screen_url and for a /screen/raw/?query=... page alike."""
    status, headers, body = request('GET', page_url, jar)
    if is_redirect(status) and 'login' in headers.get('Location', '').lower():
        raise SystemExit('  ! screener.in redirected to login — your sessionid cookie is missing or expired (nothing written).')
    if is_redirect(status):
        raise SystemExit(f'  ! screener.in redirected to {headers.get("Location","?")} — check the ' + kind_hint + '.')
    merge_setcookie(jar, headers)                                   # picks up a fresh csrftoken
    page = body.decode('utf-8', 'replace')
    if 'unknown' in page.lower() and 'ratio' in page.lower():
        m = re.search(r'([^<>]*unknown[^<>]*ratio[^<>]*)', page, re.I)
        raise SystemExit('  ! screener.in rejected the query' + (f': "{m.group(1).strip()[:120]}"' if m else '') + ' — check the raw_query ratio names.')
    m = re.search(r'<form[^>]+action="([^"]*?/api/export/[^"]*?)"[^>]*>(.*?)</form>', page, re.S)
    if not m:
        raise SystemExit('  ! no Export-to-Excel form on that page. Make sure the ' + kind_hint + ' is right and the cookie is a logged-in session.')
    action = html.unescape(m.group(1))
    tok = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', m.group(2))
    if not tok:
        raise SystemExit('  ! export form found but no CSRF token in it — screener.in markup may have changed.')
    token = tok.group(1)
    jar.setdefault('csrftoken', token)
    export_url = urllib.parse.urljoin(page_url, action)
    # screener's export endpoint is intermittently 503 (especially for raw queries) — retry a few times.
    for attempt in range(4):
        status, headers, data = request('POST', export_url, jar, referer=page_url, form={'csrfmiddlewaretoken': token})
        if status == 503 and attempt < 3:
            print(f'    export returned 503 (attempt {attempt + 1}/4), retrying…')
            time.sleep(2 + 2 * attempt)
            continue
        break
    if status == 503:
        raise SystemExit('  ! screener.in export kept returning 503 (busy) — try again in a minute (nothing written).')
    if is_redirect(status):
        loc = headers.get('Location', '')
        raise SystemExit('  ! export POST redirected to ' + (loc or '?') + (' (login — session expired)' if 'login' in loc.lower() else '') + ' — nothing written.')
    if status != 200:
        raise SystemExit(f'  ! export POST returned HTTP {status} — nothing written.')
    if looks_like_login(data, headers.get('Content-Type', '')):
        raise SystemExit('  ! export returned an HTML/login page — session expired (nothing written).')
    return data

def save(data, base):
    ext = '.xlsx' if data[:2] == b'PK' else '.csv'      # xlsx is a zip ("PK"); Content-Type is unreliable on Windows
    path = base + ext
    io.open(path, 'wb').write(data)
    return path, len(data)

# ---------------------------------------------------------------- main
def last_friday(today=None):
    today = today or dt.date.today()
    return (today - dt.timedelta(days=(today.weekday() - 4) % 7)).isoformat()

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', default=DEF_CONFIG)
    ap.add_argument('--date', default=None, help='week-ending ISO date; default = most recent Friday')
    ap.add_argument('--dry-run', action='store_true', help='show the plan and check the cookie, fetch nothing')
    ap.add_argument('--no-build', action='store_true', help='download only, do not run build_scan.py')
    ap.add_argument('--commit', action='store_true', help='git add/commit data/scans (and push if config.git.push)')
    a = ap.parse_args()

    date = a.date or last_friday()
    try: dt.date.fromisoformat(date)
    except ValueError: raise SystemExit('--date must be YYYY-MM-DD')

    if not os.path.exists(a.config):
        raise SystemExit(f'no config at {a.config}\n  copy tools/screener_config.example.json to tools/screener_config.json and add a raw_query (or screen_url).')
    cfg = json.load(io.open(a.config, encoding='utf-8'))
    sources = cfg.get('sources') or cfg.get('exports') or []      # accept the old key name too
    if not sources:
        raise SystemExit('config has no "sources" — add at least one {screen_url|url, kind} entry.')

    raw_cookie, src = load_cookie()
    print(f'week-ending {date}')
    print(f'cookie: {"loaded from " + src + f" ({len(raw_cookie)} chars)" if raw_cookie else "NOT SET — add SCREENER_COOKIE env or .secrets/screener_cookie.txt"}')
    print(f'{len(sources)} source(s):')
    for e in sources:
        loc = e.get('screen_url') or e.get('url') or (('raw query: ' + ' '.join(str(e['raw_query']).split())) if e.get('raw_query') else '(missing url/query)')
        print(f'  - [{e.get("kind","screener")}] {e.get("name","(unnamed)")}: {loc[:90]}')
    if a.dry_run:
        print('dry-run: nothing fetched.'); return
    if not raw_cookie:
        raise SystemExit('refusing to fetch without a cookie (the download would just be a login page).')

    os.makedirs(EXPORTS, exist_ok=True)
    built = {'screener': None, 'stage2': None}
    for e in sources:
        kind = e.get('kind', 'screener')
        jar = parse_cookies(raw_cookie)                            # a fresh jar per source
        print(f'fetching {kind} ...')
        if e.get('raw_query'):
            data = fetch_screen(raw_query_url(e['raw_query']), jar, kind_hint='raw_query')
        elif e.get('screen_url'):
            data = fetch_screen(e['screen_url'], jar, kind_hint='screen_url')
        elif e.get('url'):
            data = fetch_direct(e['url'], jar)
        else:
            raise SystemExit(f'  ! source "{e.get("name","?")}" has none of raw_query / screen_url / url.')
        path, n = save(data, os.path.join(EXPORTS, f'{kind}_{date}'))
        print(f'  saved {os.path.relpath(path, ROOT)} ({n:,} bytes)')
        built[kind] = (path, e)

    if a.no_build:
        print('done (--no-build).'); return

    args = [sys.executable, os.path.join(TOOLS, 'build_scan.py'), '--date', date]
    if built['screener']:
        path, e = built['screener']; args += ['--screener', path]
        if e.get('name'): args += ['--screen-name', e['name']]
        if e.get('min_mcap') is not None: args += ['--min-mcap', str(e['min_mcap'])]
    if built['stage2']:
        args += ['--stage2', built['stage2'][0]]
    print('running build_scan.py ...')
    if subprocess.call(args) != 0:
        raise SystemExit('  ! build_scan.py failed — see output above.')

    if a.commit:
        git = cfg.get('git', {})
        subprocess.check_call(['git', '-C', ROOT, 'add', 'data/scans'])
        rc = subprocess.call(['git', '-C', ROOT, 'commit', '-m', f'Edge scan: week ending {date}'])
        if rc == 0 and git.get('push'):
            subprocess.check_call(['git', '-C', ROOT, 'push', 'origin', git.get('branch', 'main')]); print('committed and pushed.')
        elif rc == 0:
            print('committed (push disabled in config.git.push).')
        else:
            print('nothing to commit.')
    print('done.')

if __name__ == '__main__':
    main()
