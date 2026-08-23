#!/usr/bin/env python3
"""
fetch_screener.py — pull your weekly screener.in export automatically and build the Edge scan file.

    python tools/fetch_screener.py --date 2026-08-21
    python tools/fetch_screener.py --dry-run          # show the plan, fetch nothing
    python tools/fetch_screener.py --commit           # also git-commit + push data/scans

WHAT IT DOES
  1. reads one or more export URLs from a config file you control (tools/screener_config.json),
  2. downloads each with your screener.in session cookie attached,
  3. saves them under exports/, and
  4. runs build_scan.py to produce data/scans/<date>.json.

CREDENTIALS — you stay in control, the cookie never goes through chat:
  * Put your screener.in session cookie in the SCREENER_COOKIE environment variable,
    or in a file .secrets/screener_cookie.txt (both are gitignored; the file wins if present).
  * To get it: log in to screener.in in your browser → DevTools → Application/Storage → Cookies
    → copy the value of `sessionid` (or the whole Cookie header). Paste it into that file.
  * This script only READS the cookie to attach it to the download request. It never prints,
    logs, or commits it. If the cookie is missing/expired the download returns screener's login
    page and the script stops with a clear message — nothing partial is written.

CONFIG — tools/screener_config.json (copy tools/screener_config.example.json):
  {
    "exports": [
      { "url": "https://www.screener.in/screen/raw/<id>/?...&limit=2000", "kind": "screener",
        "name": "Market cap > 1000", "min_mcap": 1000 }
    ],
    "git": { "push": true, "branch": "main" }
  }
  Get each `url` by opening your saved screen on screener.in and copying the address of the
  "Export to Excel" link (right-click → Copy link address). `kind` is "screener" (the universe)
  or "stage2" (a Stage 2 list, if you host one somewhere exportable). You can list several.

Nothing here is screener-specific beyond the login-page sniff — any URL that returns a CSV/XLSX
with your cookie works, so it also fits a Google-Sheets "export?format=csv" link, etc.
"""
import argparse, datetime as dt, io, json, os, subprocess, sys, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
DEF_CONFIG = os.path.join(TOOLS, 'screener_config.json')
EXPORTS = os.path.join(ROOT, 'exports')
SECRET_FILE = os.path.join(ROOT, '.secrets', 'screener_cookie.txt')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) SwingEdge-fetch/1.0'

def load_cookie():
    if os.path.exists(SECRET_FILE):
        c = io.open(SECRET_FILE, encoding='utf-8').read().strip()
        if c: return c, '.secrets/screener_cookie.txt'
    c = os.environ.get('SCREENER_COOKIE', '').strip()
    if c: return c, 'SCREENER_COOKIE env'
    return None, None

def normalize_cookie(c):
    # accept a bare sessionid value or a full "k=v; k2=v2" cookie header
    return c if ('=' in c) else ('sessionid=' + c)

def last_friday(today=None):
    today = today or dt.date.today()
    return (today - dt.timedelta(days=(today.weekday() - 4) % 7)).isoformat()

def looks_like_login(head_bytes, ctype):
    if 'html' in (ctype or '').lower():
        return True
    low = head_bytes[:2048].lower()
    return b'<html' in low or b'login' in low and b'password' in low

def fetch(url, cookie, dest):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    if cookie:
        req.add_header('Cookie', normalize_cookie(cookie))
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            ctype = resp.headers.get('Content-Type', '')
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f'  ! HTTP {e.code} fetching the export — {"cookie likely expired; refresh it" if e.code in (401, 403) else e.reason}')
    except urllib.error.URLError as e:
        raise SystemExit(f'  ! could not reach the export URL: {e.reason}')
    if looks_like_login(data, ctype):
        raise SystemExit('  ! the download returned an HTML/login page, not a spreadsheet — your screener.in cookie is missing or expired. Refresh it and retry (nothing was written).')
    # xlsx files are zip archives (magic bytes "PK"); everything else we treat as CSV.
    # Content-Type is unreliable here (Windows reports .csv as application/vnd.ms-excel).
    if not (dest.endswith('.csv') or dest.endswith('.xlsx')):
        dest += '.xlsx' if data[:2] == b'PK' else '.csv'
    io.open(dest, 'wb').write(data)
    return dest, len(data)

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
        raise SystemExit(f'no config at {a.config}\n  copy tools/screener_config.example.json to tools/screener_config.json and put your export URL(s) in it.')
    cfg = json.load(io.open(a.config, encoding='utf-8'))
    exports = cfg.get('exports') or []
    if not exports:
        raise SystemExit('config has no "exports" — add at least one {url, kind} entry.')

    cookie, src = load_cookie()
    print(f'week-ending {date}')
    print(f'cookie: {"loaded from " + src + f" ({len(cookie)} chars)" if cookie else "NOT SET — add SCREENER_COOKIE env or .secrets/screener_cookie.txt"}')
    print(f'{len(exports)} export(s) configured:')
    for e in exports:
        print(f'  - [{e.get("kind","screener")}] {e.get("name","(unnamed)")}: {e.get("url","")[:80]}')
    if a.dry_run:
        print('dry-run: nothing fetched.'); return
    if not cookie:
        raise SystemExit('refusing to fetch without a cookie (the download would just be a login page).')

    os.makedirs(EXPORTS, exist_ok=True)
    built = {'screener': None, 'stage2': None}
    for e in exports:
        kind = e.get('kind', 'screener')
        base = os.path.join(EXPORTS, f'{kind}_{date}')
        print(f"fetching {kind} ...")
        path, n = fetch(e['url'], cookie, base)
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
    print("running build_scan.py ...")
    if subprocess.call(args) != 0:
        raise SystemExit('  ! build_scan.py failed — see output above.')

    if a.commit:
        git = cfg.get('git', {})
        rel = os.path.relpath(os.path.join(ROOT, 'data', 'scans'), ROOT)
        subprocess.check_call(['git', '-C', ROOT, 'add', rel])
        msg = f'Edge scan: week ending {date}'
        rc = subprocess.call(['git', '-C', ROOT, 'commit', '-m', msg])
        if rc == 0 and git.get('push'):
            subprocess.check_call(['git', '-C', ROOT, 'push', 'origin', git.get('branch', 'main')])
            print('committed and pushed.')
        elif rc == 0:
            print('committed (push disabled in config.git.push).')
        else:
            print('nothing to commit.')
    print('done.')

if __name__ == '__main__':
    main()
