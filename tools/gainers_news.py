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

    python tools/gainers_news.py collect                  # candidate headlines, no model, no web search

`collect` is what makes the routine cheap. For each gainer marked `search` it reads a public Google
News search feed for the company, keeps the headlines of the last week that actually name it, drops
price-ticker pages, prefers the business press, and writes up to three candidates per stock to
data/daily/gainers_candidates.json with ids (HFCL:1, HFCL:2). It publishes nothing. The daily news
routine (tools/daily_news.py) then only has to choose an id per stock, or none - a few hundred tokens
instead of fifteen web searches - and the headline that is published is the publisher's own words.
The routine records the scan it has reviewed in news.json (`vetted_scan`), so a holiday or a weekend
does not repeat the work, and this file is written by collectors only. STDLIB ONLY; fails soft (a feed that does not answer leaves that stock without
candidates).
"""
import argparse, datetime as dt, glob, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import utf8_stdio  # noqa: E402  (stdout/stderr as UTF-8, so a cp1252 pipe cannot end the run on a print)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(ROOT, 'data', 'scans')
NEWS = os.path.join(ROOT, 'data', 'daily', 'news.json')
CANDS = os.path.join(ROOT, 'data', 'daily', 'gainers_candidates.json')
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# words that say nothing about which company a name is
FILLER = {'ltd', 'limited', 'the', 'and', 'co', 'corp', 'corpn', 'india', 'indian', 'inds', 'industries', 'intl', 'international'}
# first words too common to identify a company on their own (Gujarat Kidney is not every Gujarat headline)
COMMON = {'gujarat', 'bharat', 'hindustan', 'national', 'oriental', 'standard', 'universal', 'general', 'premier', 'supreme', 'global',
          'united', 'bombay', 'madras', 'kerala', 'punjab', 'bengal', 'rajasthan', 'maharashtra', 'andhra', 'karnataka', 'kolkata',
          'mumbai', 'chennai', 'atlanta', 'elevate', 'diffusion', 'transformers', 'schneider', 'siemens', 'quality', 'capital',
          'reliance', 'mahindra', 'godrej', 'kirloskar', 'jindal', 'electronics', 'precision', 'advanced', 'integrated', 'dynamic'}
# a quote page, a results stub or a bare company page is not a headline
TICKER_PAGE = re.compile(r'share price (today|live|target)|stock price (today|live|target)|price today|live nse|nse/bse|live updates|'
                         r'share price, stock price|stock price & news|historical data|financials, |balance sheet|'
                         r'(share|stock) price\s*$|share/stock price|(yearly|half[- ]yearly|quarterly|nine monthly) results[,:]|'
                         r'earnings for |financial summary|company profile|stock quote|stock analysis|'
                         r'^[\w\s&.()\-]{3,60}\b(limited|ltd\.?)\s*$', re.I)


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


def pending(top=15, fresh_days=3):
    """(scan date, [(code, name, r1d)]) - today's top gainers that do not yet carry a recent headline."""
    scan = preflight()
    d, news = load(scan), load(NEWS)
    rows = [r for r in d.get('universe', []) if r.get('code') and isinstance(r.get('r1d'), (int, float)) and r['r1d'] > 0]
    rows.sort(key=lambda r: -r['r1d'])
    cutoff = today() - dt.timedelta(days=fresh_days)
    out = []
    for r in rows[:top]:
        code = str(r['code']).upper()
        have = (news.get('stocks') or {}).get(code) or []
        if not any(ISO.match(str(h.get('date', ''))) and dt.date.fromisoformat(h['date']) >= cutoff for h in have):
            out.append((code, str(r.get('name') or ''), r['r1d']))
    return d.get('date', os.path.basename(scan)[:-5]), out


def name_test(name, code):
    """A regex that says whether a headline is about this company. screener's names are cut at 16
    characters and abbreviated (Molbio Diagnosti, Omnitech Engg.), so the first word must appear and
    the second is matched on its opening letters; a name made of initials (M T N L) falls back to the
    NSE code as a whole word."""
    raw = [t for t in re.split(r'[\s\-&/,]+', name) if t.strip('.')]
    words = [t for t in raw if len(t.strip('.')) >= 3 and t.strip('.').lower() not in FILLER]
    if not words:
        return re.compile(r'\b' + re.escape(code) + r'\b', re.I)
    first = re.escape(words[0].strip('.'))
    if len(words) == 1:
        return re.compile(r'\b' + first + r'\w*', re.I)
    w2 = words[1]
    cut = w2.endswith('.') or (w2 is raw[-1] and len(name) >= 15)        # abbreviated, or cut off by the 16-character limit
    stem = w2.strip('.')[:3 if w2.endswith('.') else 5 if cut else max(5, len(w2))]
    both = r'\b' + first + r'\w*\W+(?:\w+\W+)?' + re.escape(stem) + r'\w*'
    w1 = words[0].strip('.')
    alone = r'\b' + first + r'\b' if len(w1) >= 7 and w1.lower() not in COMMON else None   # a long, unusual first word is enough: "Augmont shares rally"
    return re.compile(both + ('|' + alone if alone else ''), re.I)


def candidates_doc():
    try: return load(CANDS)
    except Exception: return {}


def collect(top=15, fresh_days=3, per=4, force=False, quiet=False):
    """Write data/daily/gainers_candidates.json for the newest scan. Returns the doc, or None when this
    scan's candidates are already on file (collected or reviewed) and nothing was fetched."""
    import market_news                                    # the feed reader and the trusted-source list live there
    scan_date, need = pending(top, fresh_days)
    old = candidates_doc()
    if old.get('scan_date') == scan_date and not force:
        return None
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    stocks, errors = {}, []
    for code, name, r1d in need:
        test, q = name_test(name, code), (re.sub(r'\.', '', name) if re.search(r'[A-Za-z]{3}', name) else code)
        try:
            items = market_news.feed(f'{q} share', 'IN', 24 * 7)
        except Exception as e:
            errors.append(f'{code}: {type(e).__name__}: {str(e)[:70]}'); items = []
        seen, keep = set(), []
        for it in sorted(items, key=lambda x: (not any(s in x['src'].lower() for s in market_news.TRUSTED), -x['at'].timestamp())):
            k = market_news.norm(it['t'])
            if it['at'] < since or k in seen or TICKER_PAGE.search(it['t']) or not test.search(it['t']):
                continue
            seen.add(k)
            keep.append({'t': it['t'][:160], 'src': it['src'], 'url': it['url'], 'date': it['at'].astimezone(IST).date().isoformat()})
            if len(keep) >= per:
                break
        stocks[code] = {'name': name, 'r1d': round(r1d, 2), 'cands': keep}
        if not quiet:
            print(f'    {code:<12} {len(keep)} candidate(s) of {len(items)}')
        time.sleep(0.4)
    doc = {'schema': 'swingedge-gainers-candidates/1', 'scan_date': scan_date, 'collected': dt.datetime.now(IST).strftime('%Y-%m-%dT%H:%M%z'),
           'stocks': stocks, 'errors': errors}
    with open(CANDS, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1); fh.write('\n')
    return doc


def cmd_collect(a):
    doc = collect(a.top, a.fresh_days, force=a.force, quiet=a.quiet)
    if doc is None:
        if not a.quiet: print(f"candidates for scan {candidates_doc().get('scan_date')} are already on file - nothing fetched (use --force to redo)")
        return
    n = sum(1 for s in doc['stocks'].values() if s['cands'])
    print(f"scan {doc['scan_date']}: {n} of {len(doc['stocks'])} gainers have candidate headlines" + (f"; {len(doc['errors'])} feed error(s)" if doc['errors'] else ''))


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    l = sub.add_parser('list'); l.add_argument('--top', type=int, default=15); l.add_argument('--fresh-days', type=int, default=3)
    m = sub.add_parser('merge'); m.add_argument('findings'); m.add_argument('--commit', action='store_true')
    c = sub.add_parser('collect'); c.add_argument('--top', type=int, default=15); c.add_argument('--fresh-days', type=int, default=3)
    c.add_argument('--force', action='store_true'); c.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    {'list': cmd_list, 'merge': cmd_merge, 'collect': cmd_collect}[a.cmd](a)


if __name__ == '__main__':
    main()
