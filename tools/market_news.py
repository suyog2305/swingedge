#!/usr/bin/env python3
"""
market_news.py — the overnight market-news page, in two layers that never mix.

    python tools/market_news.py collect             # fetch the feeds -> data/daily/market_news.json
    python tools/market_news.py collect --commit    # ...then commit and push (the 4 a.m. job does this)
    python tools/market_news.py brief               # the collected headlines, compact, for the digest routine
    python tools/market_news.py digest findings.json [--commit]   # validate a digest and merge it in

LAYER 1 — HEADLINES (no model). `collect` reads public Google News search feeds, one or more
queries per topic, keeps what was published inside the window (default 30 hours), drops repeats,
prefers the wire services and the business press, caps each topic, and stores only the headline,
the outlet, the time and the link. It is the whole page on its own. Scheduled at 04:00 IST by
.github/workflows/market_news.yml, a couple of hours after the US close, so it does not depend
on any PC being awake.

LAYER 2 — DIGEST (a Claude routine). `brief` prints those headlines with short ids (crude:3).
The routine reads ONLY that, writes findings.json, and `digest` merges it:
    {"headline": "one line",
     "points":  [{"t": "what happened", "why": "why it matters for Indian equities", "refs": ["crude:3", "yields:1"]}],
     "watch":   ["dated things to look out for today"]}
Every point must cite at least one collected headline by id; `digest` turns ids into links and
REJECTS a point that cites nothing or cites an id that was not collected, so the digest cannot say
something the headlines do not. No stock is recommended: the digest reports, it does not advise.

STDLIB ONLY. FAILS SOFT: a feed that does not answer is listed under "errors"; a run that collects
nothing at all leaves the previous file untouched and exits 1 so the job shows red.
"""
import argparse, datetime as dt, email.utils, io, json, os, re, subprocess, sys, time
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'daily', 'market_news.json')        # headlines: written only by `collect`
DIGEST = os.path.join(ROOT, 'data', 'daily', 'market_digest.json')   # the digest: written only by the routine
SCHEMA = 'swingedge-market-news/1'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# id, label, why it is on the page, edition (IN or US), queries, and what a headline must mention to belong there
TOPICS = [
    ('india', 'Indian market', 'the tape at home: indices, flows, the rupee, RBI', 'IN',
     ['Sensex Nifty today', 'FII DII stock market India', 'RBI rupee'],
     r'nifty|sensex|\bfii|\bfpi|\bdii|rupee|\brbi\b|forex|g-sec|stock market|d-street|dalal|indian stocks|indian equit'),
    ('orders', 'Orders and deals', 'large order wins and deals by listed Indian companies', 'IN',
     ['bags order crore', 'wins order worth crore', 'secures contract crore', 'acquires stake crore'],
     r'(bag|win|won|secur|receiv|award|land)\w*\b.{0,60}\b(order|contract)|\b(order|contract)s?\b.{0,40}\b(worth|crore|cr|million|win)\b|acqui|\bstake\b|merger|buyout'),
    ('crude', 'Crude oil and energy', 'the import bill, inflation, oil marketers, upstream', 'US',
     ['crude oil prices Brent', 'OPEC oil supply'],
     r'\boil\b|brent|\bwti\b|opec|crude|diesel|refiner|\blng\b|natural gas'),
    ('yields', 'US yields and the Fed', 'what pulls foreign money toward or away from India', 'US',
     ['Treasury yields', 'Federal Reserve interest rates'],
     r'yield|treasur|\bfed\b|federal reserve|fomc|powell|\brates?\b|bond|inflation|payroll|jobs'),
    ('us', 'Wall Street', 'the overnight lead for Asia', 'US',
     ['Wall Street stocks close S&P 500 Nasdaq'],
     r'wall street|s&p|nasdaq|\bdow\b|stocks|futures|equit'),
    ('ai', 'AI, chips and data centres', 'the theme behind the AI data-centre, fibre and EMS trackers', 'US',
     ['AI chips Nvidia', 'data center AI spending', 'memory chip prices DRAM'],
     r'\bai\b|nvidia|chip|semiconductor|data cent|dram|nand|memory|gpu|tsmc|openai|hyperscal|micron|hynix'),
    ('metals', 'Gold and metals', 'gold, silver, copper', 'US',
     ['gold price', 'copper price'],
     r'gold|silver|copper|alumin|bullion|metal|zinc|nickel'),
    ('policy', 'Policy and geopolitics', 'tariffs, sanctions, wars, budgets', 'US',
     ['tariffs trade deal', 'sanctions oil geopolitical'],
     r'tariff|trade|sanction|\bwar\b|ceasefire|geopolit|budget|shutdown|election|attack|treaty'),
]
# pages that are not news: live price tickers, results stubs, price-of-the-day posts
JUNK = re.compile(r"share price (today|live)|stock price (today|live)|price live|live updates|nine monthly results|quarterly results, |"
                  r"earnings for |horoscope|today.s fuel prices|current price of|income tax notice", re.I)
TRUSTED = ('reuters', 'bloomberg', 'cnbc', 'economic times', 'moneycontrol', 'mint', 'livemint', 'business standard',
           'financial times', 'wall street journal', 'wsj', 'businessline', 'ndtv profit', 'associated press', 'ap news',
           'financial express', 'business today', 'marketwatch', "barron's", 'nikkei', 'the hindu', 'yahoo finance',
           'investing.com', 'oilprice', 'kitco', 'fortune', 'the economist')
PER_TOPIC = 12
QUIET = False


def say(*a):
    if not QUIET:
        print(*a, flush=True)


def feed(query, edition, hours):
    q = urllib.parse.quote(f'{query} when:{max(1, round(hours / 24))}d')
    loc = 'hl=en-IN&gl=IN&ceid=IN:en' if edition == 'IN' else 'hl=en-US&gl=US&ceid=US:en'
    req = urllib.request.Request(f'https://news.google.com/rss/search?q={q}&{loc}', headers={'User-Agent': UA, 'Accept': 'application/rss+xml,*/*'})
    with urllib.request.urlopen(req, timeout=40) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter('item'):
        title = (it.findtext('title') or '').strip()
        link = (it.findtext('link') or '').strip()
        src = (it.findtext('source') or '').strip()
        try:
            at = email.utils.parsedate_to_datetime(it.findtext('pubDate') or '')
        except (TypeError, ValueError):
            continue
        if src and title.endswith(' - ' + src):
            title = title[:-len(src) - 3].strip()
        if title and link.startswith('http') and at is not None:
            out.append({'t': title, 'src': src, 'url': link, 'at': at.astimezone(dt.timezone.utc)})
    return out


def norm(title):
    return re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip()[:70]


def collect(hours):
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=hours)
    seen, topics, errors = set(), [], []
    for tid, label, why, edition, queries, must in TOPICS:
        items, must = [], re.compile(must, re.I)
        for q in queries:
            try:
                items += feed(q, edition, hours)
            except Exception as e:
                errors.append(f'{label}: "{q}": {type(e).__name__}: {str(e)[:80]}')
            time.sleep(0.4)
        keep = []
        for it in sorted(items, key=lambda x: (not any(s in x['src'].lower() for s in TRUSTED), -x['at'].timestamp())):
            k = norm(it['t'])
            if it['at'] < since or it['at'] > now + dt.timedelta(minutes=10) or len(k) < 18 or k in seen:
                continue
            if JUNK.search(it['t']) or not must.search(it['t']):
                continue                              # off-topic for this section, or not a news story
            seen.add(k)
            keep.append(it)
            if len(keep) >= PER_TOPIC:
                break
        keep.sort(key=lambda x: -x['at'].timestamp())
        topics.append({'id': tid, 'label': label, 'why': why,
                       'items': [{'t': x['t'], 'src': x['src'], 'url': x['url'], 'at': x['at'].strftime('%Y-%m-%dT%H:%MZ')} for x in keep]})
        say(f'    {label:<28} {len(keep):>2} kept of {len(items)}')
    return topics, errors


def load():
    try:
        return json.load(io.open(OUT, encoding='utf-8'))
    except Exception:
        return {}


def save(doc):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1); fh.write('\n')
    os.replace(tmp, OUT)


def commit(msg, path=None):
    """Stage only one file (the headlines by default), commit, rebase on main, push. Git's own words on failure."""
    run = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True)
    run('add', os.path.relpath(path or OUT, ROOT))
    if run('diff', '--cached', '--quiet').returncode == 0:
        say('    nothing changed - not committing'); return 0
    r = run('commit', '-q', '-m', msg)
    if r.returncode:
        print('commit failed:', r.stderr.strip() or r.stdout.strip()); return 1
    for _ in range(3):
        if run('pull', '--rebase', 'origin', 'main').returncode:
            run('rebase', '--abort'); time.sleep(4); continue
        p = run('push', 'origin', 'HEAD:main')
        if p.returncode == 0:
            say('    pushed'); return 0
        time.sleep(4)
    print('push failed - the commit is local'); return 1


def load_digest():
    """The digest on file. It has its own file so that the collector and the routine each write one file and
    their commits can never conflict; an older headlines file may still carry one inline."""
    try:
        return json.load(io.open(DIGEST, encoding='utf-8'))
    except Exception:
        return load().get('digest') or {}


def save_digest(digest):
    os.makedirs(os.path.dirname(DIGEST), exist_ok=True)
    with io.open(DIGEST, 'w', encoding='utf-8') as fh:
        json.dump({'schema': 'swingedge-market-digest/1', **digest}, fh, ensure_ascii=False, indent=1); fh.write('\n')


def ids(doc):
    return {f"{t['id']}:{k + 1}": it for t in doc.get('topics') or [] for k, it in enumerate(t.get('items') or [])}


def refresh(hours=30):
    """Collect and write the file. Returns (doc, errors); doc is None when nothing at all came back,
    and then the file on disk is left as it was."""
    topics, errors = collect(hours)
    if not sum(len(t['items']) for t in topics):
        return None, errors
    now = dt.datetime.now(IST)
    doc = {'schema': SCHEMA, 'updated': now.strftime('%Y-%m-%dT%H:%M%z'), 'run_date': now.date().isoformat(), 'window_hours': hours,
           'topics': topics, 'errors': errors}
    save(doc)
    return doc, errors


def cmd_collect(a):
    say(f'market_news: collecting the last {a.hours} hours')
    doc, errors = refresh(a.hours)
    if doc is None:
        print('nothing collected - every feed failed or was empty; the previous file is kept'); [print('  ', e) for e in errors]
        return 1
    say(f'wrote {os.path.relpath(OUT, ROOT)}: {sum(len(t["items"]) for t in doc["topics"])} headlines in {len(doc["topics"])} topics'
        + (f'; {len(errors)} feed error(s)' if errors else ''))
    return commit(f'Market news {doc["run_date"]}') if a.commit else 0


def brief_lines(doc, cap=None):
    """The collected headlines as compact lines with ids. `cap` trims each topic for the routine: the
    ids stay the ones the page uses, so a cited id always resolves."""
    out = []
    for t in doc.get('topics') or []:
        out.append(f"## {t['id']} - {t['label']} ({t['why']})")
        for k, it in enumerate((t.get('items') or [])[:cap]):
            out.append(f"{t['id']}:{k + 1} | {it['at'][5:16].replace('T', ' ')}Z | {it['src']} | {it['t']}")
    return out


def cmd_brief(a):
    doc = load()
    if not doc.get('topics'):
        print('STOP - no collected headlines; run `python tools/market_news.py collect` first'); return 2
    d = load_digest()
    print(f"run {doc['run_date']} (collected {doc['updated']}); digest on file is for {d.get('for') or 'none'}\n")
    print('\n'.join(brief_lines(doc)))
    return 0


def make_digest(doc, f):
    """(digest or None, dropped). Every point must cite at least one collected headline by id; a point
    that cites nothing, or an id that was not collected, is dropped - the digest cannot say something
    the headlines do not."""
    known = ids(doc)
    clean = lambda s, n: re.sub(r'\s+', ' ', str(s or '')).strip()[:n]
    points, dropped = [], []
    for p in (f.get('points') or [])[:10]:
        refs = [r for r in (p.get('refs') or []) if r in known]
        bad = [r for r in (p.get('refs') or []) if r not in known]
        t = clean(p.get('t'), 220)
        if not t or not refs or bad:
            dropped.append(f"{t[:60] or '(empty)'}: " + ('cites nothing' if not (p.get('refs') or []) else f'unknown id(s) {bad}' if bad else 'no text'))
            continue
        points.append({'t': t, 'why': clean(p.get('why'), 260), 'refs': [known[r]['url'] for r in refs][:3]})
    if not points:
        return None, dropped
    now = dt.datetime.now(IST)
    return {'for': doc.get('run_date'), 'at': now.strftime('%Y-%m-%dT%H:%M%z'), 'by': clean(f.get('by') or 'Claude routine', 40),
            'headline': clean(f.get('headline'), 140), 'points': points,
            'watch': [clean(w, 200) for w in (f.get('watch') or [])[:6] if clean(w, 200)]}, dropped


def cmd_digest(a):
    doc = load()
    if not ids(doc):
        print('STOP - no collected headlines to cite'); return 2
    try:
        f = json.load(io.open(a.file, encoding='utf-8'))
    except Exception as e:
        print(f'STOP - cannot read {a.file}: {e}'); return 2
    digest, dropped = make_digest(doc, f)
    if digest is None:
        print('STOP - no usable point: every point must cite a collected headline by id (see `brief`)'); [print('  dropped:', d) for d in dropped]
        return 2
    save_digest(digest)
    say(f"digest merged: {len(digest['points'])} point(s) for {digest['for']}" + (f'; dropped {len(dropped)}' if dropped else ''))
    for d in dropped:
        say('  dropped:', d)
    return commit(f"Market news digest {digest['for']}", DIGEST) if a.commit else 0


def main():
    global QUIET
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--quiet', action='store_true')
    sp = ap.add_subparsers(dest='cmd', required=True)
    c = sp.add_parser('collect'); c.add_argument('--hours', type=int, default=30); c.add_argument('--commit', action='store_true'); c.set_defaults(fn=cmd_collect)
    b = sp.add_parser('brief'); b.set_defaults(fn=cmd_brief)
    g = sp.add_parser('digest'); g.add_argument('file'); g.add_argument('--commit', action='store_true'); g.set_defaults(fn=cmd_digest)
    a = ap.parse_args()
    QUIET = a.quiet
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
