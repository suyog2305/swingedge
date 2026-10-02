#!/usr/bin/env python3
"""
gainers_news.py — the deterministic half of the "SwingEdge daily gainers news" routine, so the
cloud run spends tokens only on the web searches themselves.

    python tools/gainers_news.py list                     # today's top gainers, marked search / skip
    python tools/gainers_news.py merge findings.json      # validate + merge into data/daily/news.json
    python tools/gainers_news.py merge findings.json --commit   # ...then commit and push to main

`list` reads the newest data/scans/<date>.json, ranks the universe by 1-day return and prints the
top N with a non-empty code and r1d > 0 — one line each. A gainer that already carries a headline
dated within --fresh-days is marked `skip`, so it is not searched again. Exits 2 with a plain
message if the scan or the news file is missing (never creates placeholders).

`merge` takes {"CODE": {"t","src","url","date"}} (a one-item list per code is accepted too),
rejects anything without a real http(s) URL, source, headline or ISO date, writes each kept code
as the single best headline, keeps every other code, stamps `updated` with today's UTC date and
preserves `note`. With --commit it stages only news.json, commits
"Daily gainers news refresh <date>" and pushes origin main, surfacing git's exact error on failure.
"""
import argparse, datetime as dt, glob, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(ROOT, 'data', 'scans')
NEWS = os.path.join(ROOT, 'data', 'daily', 'news.json')
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def load(path):
    with open(path, encoding='utf-8') as fh: return json.load(fh)


def newest_scan():
    files = sorted(p for p in glob.glob(os.path.join(SCANS, '*.json')) if re.search(r'\d{4}-\d{2}-\d{2}\.json$', p))
    return files[-1] if files else None


def preflight():
    scan = newest_scan()
    missing = [m for m, ok in (('a dated scan file in data/scans/', scan), ('data/daily/news.json', os.path.exists(NEWS))) if not ok]
    if missing: sys.exit('STOP — missing: ' + '; '.join(missing))
    return scan


def today():
    return dt.datetime.now(dt.timezone.utc).date()


def cmd_list(a):
    scan = preflight()
    d, news = load(scan), load(NEWS)
    rows = [r for r in d.get('universe', []) if r.get('code') and isinstance(r.get('r1d'), (int, float)) and r['r1d'] > 0]
    rows.sort(key=lambda r: -r['r1d'])
    cutoff = today() - dt.timedelta(days=a.fresh_days)
    search = 0
    print(f"scan {d.get('date', os.path.basename(scan)[:-5])} · top {a.top} gainers")
    for r in rows[:a.top]:
        code = str(r['code']).upper()
        have = (news.get('stocks') or {}).get(code) or []
        fresh = next((h for h in have if ISO.match(str(h.get('date', ''))) and dt.date.fromisoformat(h['date']) >= cutoff), None)
        tag = f"skip (has news dated {fresh['date']})" if fresh else 'search'
        if not fresh: search += 1
        print(f"{code} | {r.get('name', '')} | {r['r1d']:+.2f}% | {tag}")
    print(f"→ web-search the {search} marked `search`, write findings.json, then: python tools/gainers_news.py merge findings.json --commit")


def clean(code, item):
    if isinstance(item, list): item = item[0] if item else None
    if not isinstance(item, dict): return None, 'not an object'
    t, src, url, date = (str(item.get(k, '')).strip() for k in ('t', 'src', 'url', 'date'))
    if not t: return None, 'empty headline'
    if not src: return None, 'empty source'
    if not re.match(r'^https?://\S+\.\S+', url): return None, f'not a real URL: {url!r}'
    if not ISO.match(date): return None, f'date not YYYY-MM-DD: {date!r}'
    return {'t': t[:160], 'src': src, 'url': url, 'date': date}, None


def cmd_merge(a):
    preflight()
    findings = load(a.findings)
    if not isinstance(findings, dict): sys.exit('findings must be an object keyed by code')
    news = load(NEWS)
    news.setdefault('stocks', {})
    kept, dropped = [], []
    for code, item in findings.items():
        c = str(code).strip().upper()
        obj, why = clean(c, item)
        if obj: news['stocks'][c] = [obj]; kept.append(c)
        else: dropped.append(f'{c}: {why}')
    stamp = today().isoformat()
    news['updated'] = stamp
    with open(NEWS, 'w', encoding='utf-8') as fh:
        json.dump(news, fh, ensure_ascii=False, indent=2); fh.write('\n')
    print(f"updated {len(kept)}: {', '.join(kept) or '—'}")
    if dropped: print('dropped ' + '; '.join(dropped))
    if not kept: sys.exit('nothing valid to merge — news.json re-stamped only, not committed')
    if a.commit:
        git = lambda *args: subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
        for step in (('add', 'data/daily/news.json'), ('commit', '-m', f'Daily gainers news refresh {stamp}'), ('push', 'origin', 'main')):
            r = git(*step)
            if r.returncode: sys.exit(f"git {' '.join(step)} failed ({r.returncode}):\n{r.stderr.strip() or r.stdout.strip()}")
        print(f'committed and pushed: Daily gainers news refresh {stamp}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    l = sub.add_parser('list'); l.add_argument('--top', type=int, default=15); l.add_argument('--fresh-days', type=int, default=3)
    m = sub.add_parser('merge'); m.add_argument('findings'); m.add_argument('--commit', action='store_true')
    a = ap.parse_args()
    (cmd_list if a.cmd == 'list' else cmd_merge)(a)


if __name__ == '__main__':
    main()
