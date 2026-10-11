#!/usr/bin/env python3
"""
build_peers.py - Global Peers: the US and global names that lead, supply or mirror Indian stocks, and how
closely the two sides have actually moved together.

    python tools/build_peers.py                       # every group in data/peers/pairs.json -> data/peers/peers.json
    python tools/build_peers.py --only optical        # one group; the other groups already on file are kept
    python tools/build_peers.py --no-news --no-earnings --out /tmp/peers.json --quiet

Config: data/peers/pairs.json (edit by hand). Output: data/peers/peers.json, read by the Global Peers page.

PRICES. Yahoo's chart endpoint, daily closes, dated in each exchange's own time zone. India codes map to
<CODE>.NS (URL-encoded, so GVT&D -> GVT%26D.NS). A bar for a session that is still open is dropped, so every
close on file is a final one. Per member: last close, close-to-close change over 1W/1M/3M/6M/1Y (5/21/63/126/250
trading days), distance from the 52-week high, and the last --days closes (oldest first, no gaps filled).

PAIRS. Returns are ln(c_t / c_{t-1}) on each symbol's own trading days. Exchanges that close before India
(.KS .KQ .T .HK .SS .SZ .TW) are read on the SAME calendar date; the rest (US, Europe) close after India, so a
global return on day d is matched with India's return on its first session strictly after d ("next"). When
several global days fall before one India session (an Indian holiday), their log returns are added, because
India's return on that session spans all of them. A window (3M/6M/1Y = the last 63/126/250 Indian return dates)
gives: n paired days; Pearson correlation for both alignments (same, next); OLS beta of the Indian return on the
global one (beta_same, beta_next); the hit rate (share of days, ignoring exact zeros, on which the two returns had
the same sign, in the pair's own alignment); read = next or same per the lag; and a word label from read.3M:
|r| >= 0.5 strong, >= 0.3 moderate, >= 0.15 weak, else none, and r <= -0.3 inverse. Metrics are null when n < 40.
Correlation measures co-movement of daily returns; it says nothing about cause.

EARNINGS. Nasdaq's public earnings calendar, US-listed names only (no suffix), weekdays over the next 60 days,
stopping once every name is found or after repeated failures. NEWS. Google News RSS through tools/market_news.py
(US edition, last 7 days, query '"<name>" stock'), headlines that name the company and are not price-ticker
pages, 3 per global name.

FAILS SOFT. A symbol Yahoo does not answer keeps the series from the previous peers.json when there is one
(marked "stale": true), else series: null and null metrics for its pairs. Skipped or failed news / earnings keep
the previous values. A run that fetches no prices at all leaves the file untouched and exits 1.
STDLIB ONLY. No model.
"""
import argparse, datetime as dt, io, json, math, os, re, sys, time
import urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs import utf8_stdio  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, 'data', 'peers', 'pairs.json')
OUT = os.path.join(ROOT, 'data', 'peers', 'peers.json')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
WINDOWS = {'3M': 63, '6M': 126, '1Y': 250}             # trading-day windows for the pair metrics
CHG = {'1W': 5, '1M': 21, '3M': 63, '6M': 126, '1Y': 250}
SAME_DAY = ('.KS', '.KQ', '.T', '.HK', '.SS', '.SZ', '.TW')   # close before India -> same-day read-across
MIN_N = 40
NEWS_PER = 3
EARN_DAYS = 60
QUIET = False


def say(*a):
    if not QUIET:
        print(*a, flush=True)


def jload(p):
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def get(url, headers=None, timeout=30, tries=2):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*', **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 403, 404):
                raise
            last = e
        except Exception as e:
            last = e
        if k + 1 < tries:
            time.sleep(1.5 * (k + 1))
    raise last


def r1(x):
    return None if x is None else round(x, 1)


def r2(x):
    return None if x is None else round(x, 2)


# ---------------------------------------------------------------- prices
def yahoo(sym, days):
    """{'closes': [(iso, close)], 'currency', 'exchange', 'name'} - final daily closes, oldest first."""
    rng = '1y' if days <= 240 else '2y'                # 1y is ~250 bars; a 260-day series and a 1Y change need more
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(sym, safe='')
           + f'?interval=1d&range={rng}')
    res = json.loads(get(url))['chart']['result'][0]
    meta = res.get('meta') or {}
    off = int(meta.get('gmtoffset') or 0)
    ts = res.get('timestamp') or []
    cl = ((res.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []
    reg = (meta.get('currentTradingPeriod') or {}).get('regular') or {}
    now = time.time()
    live = bool(reg.get('start')) and reg['start'] <= now < (reg.get('end') or 0)
    out = {}
    for t, c in zip(ts, cl):
        if c is None or not (c > 0):
            continue
        if live and t >= reg['start']:
            continue                                   # today's bar while the session is still open
        out[dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()] = float(c)
    if not out:
        raise RuntimeError('no closes')
    return {'closes': sorted(out.items()), 'currency': meta.get('currency'),
            'exchange': meta.get('exchangeName'), 'name': meta.get('longName') or meta.get('shortName')}


def describe(closes, days):
    """last, last_date, chg, from_52w_high, series - from the full fetched history."""
    c = [v for _, v in closes]
    last = c[-1]
    chg = {k: (r1((last / c[-1 - n] - 1) * 100) if len(c) > n else None) for k, n in CHG.items()}
    hi = max(c[-250:])
    tail = closes[-days:]
    return {'last': r2(last), 'last_date': closes[-1][0], 'chg': chg,
            'from_52w_high': r1((last / hi - 1) * 100),
            'series': {'dates': [d for d, _ in tail], 'close': [round(v, 2) for _, v in tail]}}


def log_returns(series):
    """[(date, ln(c_t/c_{t-1}))] on the symbol's own trading days."""
    d, c = series['dates'], series['close']
    return [(d[i], math.log(c[i] / c[i - 1])) for i in range(1, len(c)) if c[i] > 0 and c[i - 1] > 0]


def lag_of(sym):
    return 'same' if sym.upper().endswith(SAME_DAY) else 'next'


# ---------------------------------------------------------------- pair metrics
def corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None, None
    return sxy / math.sqrt(sxx * syy), sxy / sxx      # Pearson r, OLS slope of y on x


def hit_rate(xs, ys):
    both = [(x, y) for x, y in zip(xs, ys) if x != 0 and y != 0]
    return sum(1 for x, y in both if (x > 0) == (y > 0)) / len(both) if both else None


def align(ind, glo, how):
    """{india date: (global return, india return)}. same: equal dates. next: global day d -> first India date
    strictly after d; global days that land on the same India session are summed."""
    ir = dict(ind)
    if how == 'same':
        return {d: (g, ir[d]) for d, g in glo if d in ir}
    idates = [d for d, _ in ind]
    out, j = {}, 0
    for d, g in glo:                                   # both lists are oldest first
        while j < len(idates) and idates[j] <= d:
            j += 1
        if j == len(idates):
            break
        if j == 0:
            continue                                   # before India's first return: no session spans it
        i = idates[j]
        out[i] = (out[i][0] + g, ir[i]) if i in out else (g, ir[i])
    return out


def label(r):
    if r is None:
        return None
    if r <= -0.3:
        return 'inverse'
    a = abs(r)
    return 'strong' if a >= 0.5 else 'moderate' if a >= 0.3 else 'weak' if a >= 0.15 else 'none'


def pair_metrics(india_m, glob_m, code, sym, rel, lag):
    p = {'india': code, 'global': sym, 'rel': rel, 'lag': lag}
    keys = ('n', 'same', 'next', 'beta_same', 'beta_next', 'hit', 'read')
    for k in keys:
        p[k] = {w: None for w in WINDOWS}
    p['label'] = None
    si, sg = india_m.get('series'), glob_m.get('series')
    if not si or not sg:
        p['n'] = {w: 0 for w in WINDOWS}
        return p
    ind, glo = log_returns(si), log_returns(sg)
    idates = [d for d, _ in ind]
    al = {how: align(ind, glo, how) for how in ('same', 'next')}
    for w, size in WINDOWS.items():
        keep = set(idates[-size:])
        cnt = {}
        for how in ('same', 'next'):
            rows = [v for d, v in sorted(al[how].items()) if d in keep]
            cnt[how] = len(rows)
            if len(rows) < MIN_N:
                continue
            xs, ys = [g for g, _ in rows], [i for _, i in rows]
            r, b = corr(xs, ys)
            p[how][w], p['beta_' + how][w] = r2(r), r2(b)
            if how == lag:
                p['hit'][w] = r2(hit_rate(xs, ys))
        p['n'][w] = cnt[lag]
        p['read'][w] = p[lag][w]
    p['label'] = label(p['read']['3M'])
    return p


# ---------------------------------------------------------------- earnings and news
def earnings(syms):
    """{sym: {'date', 'time'}} for the US-listed syms over the next EARN_DAYS calendar days. None when Nasdaq never answered."""
    want, found, fails, answered = set(syms), {}, 0, False
    tmap = {'time-pre-market': 'pre-market', 'time-after-hours': 'after-market'}
    day = dt.datetime.now(IST).date()
    for k in range(EARN_DAYS):
        d = day + dt.timedelta(days=k)
        if d.weekday() >= 5 or not (want - set(found)):
            continue
        try:
            raw = get(f'https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}',
                      headers={'Accept': 'application/json', 'Accept-Language': 'en-US'}, timeout=20, tries=1)
            rows = ((json.loads(raw).get('data') or {}).get('rows')) or []
            answered, fails = True, 0
        except Exception as e:
            fails += 1
            say(f'  earnings {d}: {type(e).__name__}: {str(e)[:60]}')
            if fails >= 4:
                say('  earnings: Nasdaq stopped answering; keeping what is on file')
                break
            continue
        for row in rows:
            s = (row.get('symbol') or '').strip().upper()
            if s in want and s not in found:
                found[s] = {'date': d.isoformat(), 'time': tmap.get(row.get('time'))}
        time.sleep(0.3)
    return found if answered else None


# headlines that name the company but are about someone else, or are quote pages rather than news
HOMONYMS = re.compile(r'owens corning|corning natural gas', re.I)
QUOTE_PAGE = re.compile(r'stock price, news, quote|quote & history|stock quote|stock price$|price target|stock forecast|'
                        r'better buy|buy or sell|should you buy|stocks? to buy|time to (?:buy|sell)|buy (?:now|the dip)|top (?:stocks?|picks?)|'
                        r'\b(?:EUR|CHF|USD|GBP)\s?\d|(?:acquired|sold|purchased|bought) by|position (?:in|raised|lowered|trimmed)|'
                        r'stake (?:raised|lowered|trimmed|cut|boosted)|holdings (?:raised|lowered|trimmed)|pay revealed|tokenized stock', re.I)
NAME_TAIL = re.compile(r'(?:\s+(?:India|Limited|Ltd\.?|Inc\.?|Corp\.?|Corporation|plc|AG|SA|N\.?V\.?))+$', re.I)
TICKER_REF = re.compile(r'\((?:[A-Za-z]{2,9}\s*:\s*)?([A-Z]{1,5})\)|\b(?:NYSE|NASDAQ|Nasdaq|NasdaqGS|NasdaqGM|AMEX|OTC|TSE|TYO|'
                        r'XTRA|ETR|FRA|SWX|SIX|LSE|ASX|HKEX|KRX|KOSE|EPA|AMS|NSE|BSE)\s*:\s*([A-Z0-9]{1,6})\b')
ACRONYMS = {'AI', 'CEO', 'CFO', 'IPO', 'ETF', 'EPS', 'HBM', 'GPU', 'DRAM', 'NAND', 'US', 'UK', 'EU', 'Q1', 'Q2', 'Q3', 'Q4'}
LEAD_TICKER = re.compile(r'^([A-Z]{2,5})\s+(?:stock|shares)\b', re.I)


def news_for(name, sym, feed, junk, others=()):
    """<= NEWS_PER headlines, newest first, from the last 7 days, that name the company (its first word) and are
    not quote pages, known namesakes, or tagged with another company's ticker. `others` are the other names in the
    run: a longer one that starts with the same word (Siemens Energy for Siemens, Hitachi Energy for Hitachi) is
    blanked out of the title first, so a headline that only names it does not count for this company."""
    word = name.split()[0]
    must = re.compile(r'(?<![A-Za-z0-9])' + re.escape(word) + r'(?![A-Za-z0-9])', re.I)
    longer = sorted({NAME_TAIL.sub('', o).strip() for o in others}, key=len, reverse=True)
    longer = [o for o in longer if len(o) > len(name) and o.lower().startswith(word.lower() + ' ')
              and not name.lower().startswith(o.lower())]
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    own = sym.split('.')[0].upper()
    seen, out = set(), []
    for it in sorted(feed(f'"{name}" stock', 'US', 24 * 7), key=lambda x: -x['at'].timestamp()):
        t = it['t']
        k = re.sub(r'[^a-z0-9]+', ' ', t.lower()).strip()[:70]
        bare = t
        for o in longer:
            bare = re.sub(re.escape(o), ' ', bare, flags=re.I)
        if it['at'] < since or k in seen or not must.search(bare) or junk.search(t) or HOMONYMS.search(t) or QUOTE_PAGE.search(t):
            continue
        tickers = {a or b for a, b in TICKER_REF.findall(t)} - ACRONYMS
        lead = LEAD_TICKER.match(t)
        if lead and lead.group(1).isupper():
            tickers.add(lead.group(1))
        if tickers and own not in tickers:
            continue                                   # tagged with another company's ticker
        seen.add(k)
        out.append({'t': t, 'src': it['src'], 'url': it['url'], 'date': it['at'].date().isoformat()})
        if len(out) == NEWS_PER:
            break
    return out


# ---------------------------------------------------------------- build
def load_prev(path):
    try:
        doc = jload(path)
    except Exception:
        return None
    return None if doc.get('sample') else doc        # never carry anything over from the hand-made fixture


def build(a):
    cfg = jload(CFG)
    groups = [g for g in cfg['groups'] if not a.only or g['id'] == a.only]
    if not groups:
        print(f'no group with id {a.only!r} in pairs.json', file=sys.stderr); return 2
    prev = load_prev(a.out) or {}
    prev_members = {}
    for g in prev.get('groups') or []:
        for k, m in (g.get('members') or {}).items():
            prev_members.setdefault(k, m)
    today = dt.datetime.now(IST).date().isoformat()

    # one fetch per symbol, even when it sits in two groups
    want = {}
    for g in groups:
        for m in g.get('india') or []:
            want[m['code']] = m['code'] + '.NS'
        for m in g.get('global') or []:
            want[m['sym']] = m['sym']
    fetched, errors = {}, []
    for key, sym in want.items():
        try:
            y = yahoo(sym, a.days)
            fetched[key] = {**describe(y['closes'], a.days), 'currency': y['currency'], 'exchange': y['exchange'], '_name': y['name']}
        except Exception as e:
            errors.append(f'{sym}: {type(e).__name__}: {str(e)[:80]}')
        time.sleep(0.25)
    if not fetched:
        print('no prices could be fetched; file left as it was:', *errors, sep='\n  ', file=sys.stderr)
        return 1

    us = sorted({m['sym'] for g in groups for m in g.get('global') or [] if '.' not in m['sym'] and '^' not in m['sym']})
    earn = None
    if not a.no_earnings and us:
        say(f'earnings: Nasdaq calendar for {len(us)} US names')
        earn = earnings(us)
    news, feed_fn, junk = {}, None, None
    if not a.no_news:
        try:
            from market_news import feed as feed_fn, JUNK as junk   # noqa: E402
        except Exception as e:
            say(f'news: market_news.py could not be imported ({e}); skipped')

    all_names = {m.get('name') or m['sym'] for g in groups for m in g.get('global') or []}
    all_names |= {f['_name'] for f in fetched.values() if f.get('_name')}
    out_groups, summary = [], []
    for g in groups:
        india_codes = [m['code'] for m in g.get('india') or []]
        members = {}

        def base(key, side, sym, extra):
            f = fetched.get(key)
            if f:
                m = {k: v for k, v in f.items() if not k.startswith('_')}
            elif (prev_members.get(key) or {}).get('series'):
                pm = prev_members[key]
                m = {k: pm.get(k) for k in ('currency', 'exchange', 'last', 'last_date', 'chg', 'from_52w_high', 'series')}
                m['stale'] = True
            else:
                m = {'currency': None, 'exchange': None, 'last': None, 'last_date': None,
                     'chg': {k: None for k in CHG}, 'from_52w_high': None, 'series': None}
            return {**extra, 'side': side, 'sym': sym, **m}

        for im in g.get('india') or []:
            code = im['code']
            nm = (fetched.get(code) or {}).get('_name') or (prev_members.get(code) or {}).get('name') or code
            extra = {'name': nm}
            if im.get('note'):
                extra['note'] = im['note']
            members[code] = base(code, 'india', code + '.NS', extra)

        for gm in g.get('global') or []:
            sym = gm['sym']
            fr = [c for c in (gm.get('for') or india_codes) if c in india_codes]
            bad = [c for c in (gm.get('for') or []) if c not in india_codes]
            if bad:
                errors.append(f'{g["id"]}/{sym}: `for` names codes not in the group: {", ".join(bad)}')
            m = base(sym, 'global', sym, {'name': gm.get('name') or sym, 'rel': gm.get('rel') or 'peer',
                                          'note': gm.get('note') or '', 'for': fr, 'lag': lag_of(sym)})
            pm = prev_members.get(sym) or {}
            if sym in us and earn is not None:
                m['earnings'] = earn.get(sym)
            else:
                pe = pm.get('earnings')
                m['earnings'] = pe if (sym in us and pe and pe.get('date', '') >= today) else None
            if feed_fn is not None:
                if sym not in news:
                    try:
                        news[sym] = news_for(m['name'], sym, feed_fn, junk, all_names)
                    except Exception as e:
                        errors.append(f'news {sym}: {type(e).__name__}: {str(e)[:60]}')
                        news[sym] = None
                    time.sleep(0.4)
                m['news'] = news[sym] if news[sym] is not None else (pm.get('news') or [])
            else:
                m['news'] = pm.get('news') or []
            members[sym] = m

        pairs = []
        for gm in g.get('global') or []:
            for code in members[gm['sym']]['for']:
                pairs.append(pair_metrics(members[code], members[gm['sym']], code, gm['sym'],
                                          members[gm['sym']]['rel'], members[gm['sym']]['lag']))
        out_groups.append({'id': g['id'], 'name': g.get('name') or g['id'], 'why': g.get('why') or '',
                           'owner': bool(g.get('owner')), 'members': members, 'pairs': pairs})

        got = sum(1 for k in members if k in fetched)
        best = max((p for p in pairs if p['read']['3M'] is not None), key=lambda p: abs(p['read']['3M']), default=None)
        bs = (f"best {best['india']} ~ {best['global']} {best['lag']} r(3M) {best['read']['3M']:+.2f} {best['label']}"
              if best else 'no pair with enough history')
        summary.append(f"{g['id']}: {got}/{len(members)} members fetched, {len(pairs)} pairs; {bs}")

    if a.only:                                        # keep the other groups already on file, in config order
        have = {g['id']: g for g in prev.get('groups') or []}
        have.update({g['id']: g for g in out_groups})
        out_groups = [have[g['id']] for g in cfg['groups'] if g['id'] in have]

    def newest(side):
        ds = [m['last_date'] for g in out_groups for m in g['members'].values() if m.get('side') == side and m.get('last_date')]
        return max(ds) if ds else None

    doc = {'schema': 1, 'updated': dt.datetime.now(IST).strftime('%Y-%m-%dT%H:%M'), 'sample': False,
           'asof': {'india': newest('india'), 'global': newest('global')}, 'windows': dict(WINDOWS),
           'groups': out_groups}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, a.out)

    for s in summary:
        say(s)
    for e in errors:
        print('  !', e, file=sys.stderr)      # visible even with --quiet, so the GitHub log shows a partial failure
    say(f'wrote {a.out} (india as of {doc["asof"]["india"]}, global as of {doc["asof"]["global"]})')
    return 0


def main():
    global QUIET
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--quiet', action='store_true', help='print nothing but failures of the whole run')
    ap.add_argument('--out', default=OUT, help='output file (default data/peers/peers.json)')
    ap.add_argument('--days', type=int, default=260, help='trading days of closes kept per member (default 260)')
    ap.add_argument('--no-news', action='store_true', help='skip the Google News headlines (keeps those on file)')
    ap.add_argument('--no-earnings', action='store_true', help="skip Nasdaq's earnings calendar (keeps dates on file)")
    ap.add_argument('--only', metavar='GROUPID', help='rebuild one group; the others on file are kept')
    a = ap.parse_args()
    QUIET = a.quiet
    if a.days < 60:
        ap.error('--days must be at least 60')
    return build(a)


if __name__ == '__main__':
    sys.exit(main())
