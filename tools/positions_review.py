#!/usr/bin/env python3
"""
positions_review.py - a chart-structure review of your own book: daily and hourly candles for every holding,
the RS Leaders context the app already computes, and Claude (through the Claude API, paid from your API
credits) describing what the charts show. It describes; it never tells you what to do.

    python tools/positions_review.py                 # holdings from .secrets/holdings.json (kite_sync.py writes it)
    python tools/positions_review.py --codes A,B,C   # any codes, no holdings file needed
    python tools/positions_review.py --dry-run       # build the features and the prompt, call nothing, show the size
    python tools/positions_review.py --loop 60       # keep running every 60 min while NSE is open, plus one pass after the close
    python tools/positions_review.py --model claude-sonnet-5-5 --effort low   # cheaper run

DATA
  candles   Yahoo's chart endpoint (free, no key): daily 1 year, hourly 60 days, cached under .secrets/candles/
            for --max-age minutes. Exchange time, so the hourly bars are NSE's 09:15..15:15 bars.
  context   data/daily/rs_tracker.json (RS rank, trend template, exhaustion, verdict) and the newest scan
            (industry, Stage 2 list, screener's DMAs and returns) - the same numbers the app shows.
  book      .secrets/holdings.json (+ today's CNC buys from .secrets/positions.json when present).

THE MODEL
  Claude Opus 5.5 by default (the current Opus), adaptive thinking at --effort medium, structured JSON output,
  refusal fallbacks on. The request is one call per run with the whole book in it; the system prompt is
  marked cacheable so repeated runs in a day pay the cache-read rate for it. Cost is printed after every run
  from the response's own token counts (list prices as of 6 Oct 2026, an estimate). Typical run, 12 names:
  about 9k input + 5k output tokens -> roughly 0.14 USD on Opus 5.5, 0.07 on Sonnet 5.5, 0.004 on Haiku 5.5.
  Key: ANTHROPIC_API_KEY in the environment, or .secrets/anthropic_key.txt (gitignored; never paste it in chat).

OUTPUT  (all under .secrets/, never under data/, never committed)
  .secrets/positions_review.md            the latest review, readable
  .secrets/reviews/YYYY-MM-DD_HHMM.json   the structured review + the features it was given + token usage

HOUSE RULES, enforced in the prompt: describe structure, patterns and levels from the numbers given; check the
owner's own rules (70-80% of the book inside the RS top 25, Stage 2, trend template, exhaustion); no buy /
sell / add / trim / exit / target language; no predictions; "no evidence" where the data is silent.
STDLIB ONLY (python -s runs it).
"""
import argparse, datetime as dt, glob, io, json, math, os, re, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEC = os.path.join(ROOT, '.secrets')
HOLDINGS = os.path.join(SEC, 'holdings.json')
POSITIONS = os.path.join(SEC, 'positions.json')
KEYFILE = os.path.join(SEC, 'anthropic_key.txt')
CANDLES = os.path.join(SEC, 'candles')
REVIEWS = os.path.join(SEC, 'reviews')
LATEST = os.path.join(SEC, 'positions_review.md')
TRACKER = os.path.join(ROOT, 'data', 'daily', 'rs_tracker.json')
SCANS = os.path.join(ROOT, 'data', 'scans')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# USD per million tokens: input, output, cache read (Anthropic list prices as read on 2026-10-06; an estimate)
PRICES = {'claude-opus-5-5': (4.0, 20.0, 0.20), 'claude-sonnet-5-5': (2.0, 10.0, 0.20), 'claude-haiku-5-5': (0.10, 0.50, 0.01)}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from holdings_check import names_from  # noqa: E402


# ----------------------------------------------------------------------------------------------- data
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


def get(url, timeout=30, headers=None, data=None, tries=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept': '*/*', **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404, 422, 429) or i == tries - 1:
                raise
        except Exception as e:
            last = e
        time.sleep(1.5)
    raise last


def yahoo(symbol, interval, rng):
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='')
           + f'?interval={interval}&range={rng}')
    doc = json.loads(get(url, timeout=35))['chart']
    if not doc.get('result'):
        raise ValueError(str((doc.get('error') or {}).get('description') or 'no result'))
    res = doc['result'][0]
    off = int((res.get('meta') or {}).get('gmtoffset') or 19800)
    q = (res.get('indicators', {}).get('quote') or [{}])[0]
    bars = []
    for i, t in enumerate(res.get('timestamp') or []):
        o, h, l, c, v = (q.get(k, [None])[i] if i < len(q.get(k, [])) else None for k in ('open', 'high', 'low', 'close', 'volume'))
        if None in (o, h, l, c) or not h or h <= 0:
            continue
        when = dt.datetime.fromtimestamp(t + off, dt.timezone.utc)
        bars.append({'t': when.strftime('%Y-%m-%d' if interval == '1d' else '%Y-%m-%d %H:%M'),
                     'o': round(o, 2), 'h': round(h, 2), 'l': round(l, 2), 'c': round(c, 2), 'v': int(v or 0)})
    return bars


def candles(code, kind, max_age_min):
    """Daily (1y) or hourly (60d) bars for an NSE code, cached under .secrets/candles/."""
    p = os.path.join(CANDLES, f'{code}_{kind}.json')
    cached = jload(p)
    if cached and time.time() - cached.get('fetched', 0) < max_age_min * 60 and cached.get('bars'):
        return cached['bars'], cached.get('symbol')
    interval, rng = ('1d', '1y') if kind == 'daily' else ('60m', '60d')
    err = None
    for sym in (code + '.NS', code + '.BO'):
        try:
            bars = yahoo(sym, interval, rng)
            if bars:
                jsave(p, {'fetched': time.time(), 'symbol': sym, 'bars': bars})
                return bars, sym
        except Exception as e:
            err = e
    if cached and cached.get('bars'):
        print(f'    {code}: Yahoo failed ({err}); using the cached {kind} bars from {dt.datetime.fromtimestamp(cached["fetched"]):%d %b %H:%M}')
        return cached['bars'], cached.get('symbol')
    print(f'    {code}: no {kind} candles ({err})')
    return [], None


# ----------------------------------------------------------------------------------------------- maths
def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = [max(b['h'] - b['l'], abs(b['h'] - p['c']), abs(b['l'] - p['c'])) for p, b in zip(bars[-n - 1:-1], bars[-n:])]
    return sum(trs) / n


def pct(a, b):
    return round(100 * (a - b) / b, 2) if a is not None and b else None


def r2(x):
    return None if x is None else round(x, 2)


def bar_tags(bars, i, avg_range):
    """Candle tags for bar i (needs bars[i-1] and some history)."""
    b = bars[i]
    rng = b['h'] - b['l']
    if rng <= 0:
        return ['flat']
    body = abs(b['c'] - b['o'])
    up_sh = b['h'] - max(b['o'], b['c'])
    lo_sh = min(b['o'], b['c']) - b['l']
    tags = []
    p = bars[i - 1] if i >= 1 else None
    prior = [x['c'] for x in bars[max(0, i - 5):i]]
    falling = len(prior) >= 3 and prior[-1] < prior[0]
    rising = len(prior) >= 3 and prior[-1] > prior[0]
    if body <= 0.1 * rng:
        tags.append('doji')
    if lo_sh >= 2 * body and up_sh <= max(body, 0.1 * rng) and falling:
        tags.append('hammer')
    if up_sh >= 2 * body and lo_sh <= max(body, 0.1 * rng) and rising:
        tags.append('shooting star')
    if p:
        pbody_bear = p['c'] < p['o']
        if pbody_bear and b['c'] > b['o'] and b['o'] <= p['c'] and b['c'] >= p['o'] and body > abs(p['c'] - p['o']):
            tags.append('bullish engulfing')
        if (not pbody_bear) and b['c'] < b['o'] and b['o'] >= p['c'] and b['c'] <= p['o'] and body > abs(p['c'] - p['o']):
            tags.append('bearish engulfing')
        if b['h'] <= p['h'] and b['l'] >= p['l']:
            tags.append('inside bar')
        if b['h'] > p['h'] and b['l'] < p['l']:
            tags.append('outside bar')
    if i >= 6 and rng <= min(x['h'] - x['l'] for x in bars[i - 6:i + 1]):
        tags.append('NR7')
    if avg_range and rng >= 1.8 * avg_range:
        tags.append('wide range')
    loc = (b['c'] - b['l']) / rng
    tags.append('closed top third' if loc >= 0.67 else 'closed bottom third' if loc <= 0.33 else 'closed mid')
    if i >= 20:
        hi20 = max(x['h'] for x in bars[i - 20:i]); lo20 = min(x['l'] for x in bars[i - 20:i])
        if b['c'] > hi20:
            tags.append('close above 20-bar high')
        if b['c'] < lo20:
            tags.append('close below 20-bar low')
    return tags


def pivots(bars, k=2):
    """Swing highs/lows with k bars either side. The last k bars cannot be pivots yet."""
    out = []
    for i in range(k, len(bars) - k):
        hs = [bars[j]['h'] for j in range(i - k, i + k + 1)]
        ls = [bars[j]['l'] for j in range(i - k, i + k + 1)]
        if bars[i]['h'] == max(hs) and hs.count(bars[i]['h']) == 1:
            out.append(('H', bars[i]['t'], bars[i]['h']))
        if bars[i]['l'] == min(ls) and ls.count(bars[i]['l']) == 1:
            out.append(('L', bars[i]['t'], bars[i]['l']))
    return out


def structure(piv):
    """'HH HL' style reading of the last swings."""
    hs = [p for p in piv if p[0] == 'H'][-3:]
    ls = [p for p in piv if p[0] == 'L'][-3:]
    words = []
    if len(hs) >= 2:
        words.append('higher high' if hs[-1][2] > hs[-2][2] else 'lower high')
    if len(ls) >= 2:
        words.append('higher low' if ls[-1][2] > ls[-2][2] else 'lower low')
    return ', '.join(words) or 'too few swings', [(p[0], p[1], p[2]) for p in sorted(hs + ls, key=lambda p: p[1])]


def daily_features(bars):
    if len(bars) < 30:
        return {'note': f'only {len(bars)} daily bars'}
    cl = [b['c'] for b in bars]
    c = cl[-1]
    a14 = atr(bars)
    avg_rng20 = sum(b['h'] - b['l'] for b in bars[-20:]) / 20
    s20, s50, s200 = sma(cl, 20), sma(cl, 50), sma(cl, 200)
    s50_prev = sma(cl[:-20], 50) if len(cl) >= 70 else None
    s200_prev = sma(cl[:-20], 200) if len(cl) >= 220 else None
    hi52 = max(b['h'] for b in bars[-250:]); lo52 = min(b['l'] for b in bars[-250:])
    hi20 = max(b['h'] for b in bars[-20:]); lo20 = min(b['l'] for b in bars[-20:])
    hi10 = max(b['h'] for b in bars[-10:]); lo10 = min(b['l'] for b in bars[-10:])
    vols = [b['v'] for b in bars]
    v5, v50 = sma(vols, 5), sma(vols, 50)
    ups = sum(1 for p, q in zip(cl[-11:-1], cl[-10:]) if q > p)
    streak, i = 0, len(cl) - 1
    while i > 0 and ((cl[i] > cl[i - 1]) if cl[-1] > cl[-2] else (cl[i] < cl[i - 1])):
        streak += 1; i -= 1
    piv = pivots(bars[-60:])
    struct, swings = structure(piv)
    last3 = [{'t': b['t'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'v': b['v'],
              'tags': bar_tags(bars, len(bars) - 3 + j, avg_rng20)} for j, b in enumerate(bars[-3:])]
    return {
        'last': bars[-1]['t'], 'close': c,
        'chg_1d': pct(c, cl[-2]), 'chg_1w': pct(c, cl[-6]) if len(cl) > 6 else None, 'chg_1m': pct(c, cl[-22]) if len(cl) > 22 else None,
        'gap_today_pct': pct(bars[-1]['o'], cl[-2]),
        'sma20': r2(s20), 'sma50': r2(s50), 'sma200': r2(s200),
        'vs_sma20_pct': pct(c, s20), 'vs_sma50_pct': pct(c, s50), 'vs_sma200_pct': pct(c, s200),
        'sma50_slope_20d_pct': pct(s50, s50_prev) if s50_prev else None, 'sma200_slope_20d_pct': pct(s200, s200_prev) if s200_prev else None,
        'atr14': r2(a14), 'atr14_pct': round(100 * a14 / c, 2) if a14 else None,
        'from_sma20_in_atr': round((c - s20) / a14, 2) if a14 else None,
        'hi52': hi52, 'lo52': lo52, 'from_52w_high_pct': pct(c, hi52), 'above_52w_low_pct': pct(c, lo52),
        'hi20': hi20, 'lo20': lo20, 'hi10': hi10, 'lo10': lo10,
        'range10_pct_of_price': round(100 * (hi10 - lo10) / c, 2), 'range10_in_atr': round((hi10 - lo10) / a14, 2) if a14 else None,
        'vol_5d_vs_50d': round(v5 / v50, 2) if v50 else None, 'vol_last_vs_50d': round(vols[-1] / v50, 2) if v50 else None,
        'up_days_of_10': ups, 'streak': (('up ' if cl[-1] > cl[-2] else 'down ') + str(streak) + ' day(s)'),
        'swing_structure_60d': struct, 'last_swings': swings[-4:],
        'last3': last3,
    }


def hourly_features(bars):
    if len(bars) < 20:
        return {'note': f'only {len(bars)} hourly bars'}
    sessions = {}
    for b in bars:
        sessions.setdefault(b['t'][:10], []).append(b)
    days = sorted(sessions)
    last_day = sessions[days[-1]]
    prev_day = sessions[days[-2]] if len(days) > 1 else []
    a = atr(bars, 14)
    avg_rng = sum(b['h'] - b['l'] for b in bars[-20:]) / 20
    tp_v = sum(((b['h'] + b['l'] + b['c']) / 3) * b['v'] for b in last_day); vv = sum(b['v'] for b in last_day)
    vwap = tp_v / vv if vv else None
    piv = pivots(bars[-40:])
    struct, swings = structure(piv)
    hi10 = max(b['h'] for b in bars[-10:]); lo10 = min(b['l'] for b in bars[-10:])
    c = bars[-1]['c']
    last3 = [{'t': b['t'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'v': b['v'],
              'tags': bar_tags(bars, len(bars) - 3 + j, avg_rng)} for j, b in enumerate(bars[-3:])]
    return {
        'last': bars[-1]['t'], 'close': c, 'bars': len(bars), 'sessions': len(days),
        'session_high': max(b['h'] for b in last_day), 'session_low': min(b['l'] for b in last_day),
        'prev_session_high': max(b['h'] for b in prev_day) if prev_day else None, 'prev_session_low': min(b['l'] for b in prev_day) if prev_day else None,
        'vs_session_vwap_pct': pct(c, vwap) if vwap else None,
        'atr14_h': r2(a), 'range10_bars_in_atr': round((hi10 - lo10) / a, 2) if a else None, 'hi10_bars': hi10, 'lo10_bars': lo10,
        'swing_structure_5d': struct, 'last_swings': swings[-4:],
        'last3': last3,
        'closes_last_7': [b['c'] for b in bars[-7:]],
    }


# ----------------------------------------------------------------------------------------------- context
def context():
    t = jload(TRACKER, {}) or {}
    files = sorted(p for p in glob.glob(os.path.join(SCANS, '20*.json')) if re.search(r'\d{4}-\d{2}-\d{2}\.json$', p))
    scan = jload(files[-1], {}) if files else {}
    rows = {str(r.get('code') or '').upper(): r for r in (scan.get('universe') or [])}
    s2 = {str(r.get('code') or '').upper() for r in (scan.get('stage2') or [])}
    return {'tracker': t, 'rows': rows, 's2': s2, 'scan_date': scan.get('date'), 'stage2_asof': scan.get('stage2_asof'),
            'top': t.get('top') or 25}


def rs_context(code, ctx):
    x = (ctx['tracker'].get('codes') or {}).get(code) or {}
    r = ctx['rows'].get(code) or {}
    k = x.get('k') or []
    return {
        'name': x.get('n') or r.get('name'), 'industry': x.get('i') or r.get('industry'), 'group': r.get('group'),
        'rs_rating': x.get('rs'), 'rs_rank': x.get('rk'), 'rank_history_4w': k, 'in_top_n': bool(x.get('rk')) and x['rk'] <= ctx['top'],
        'on_stage2_list': code in ctx['s2'],
        'trend_template': f"{x.get('tp')}/{x.get('tt')} {x.get('t') or ''}".strip() if x.get('tt') else None,
        'exhaustion': x.get('ex'), 'verdict': x.get('v'), 'flags': x.get('f'),
        'screener': {k2: r.get(k2) for k2 in ('price', 'dma50', 'dma200', 'r1w', 'r1m', 'r3m', 'r6m', 'r1y', 'up_52wl', 'mcap') if r.get(k2) is not None},
    }


def load_book(a):
    if a.codes:
        return [{'code': c.strip().upper()} for c in a.codes.split(',') if c.strip()], 'codes given on the command line'
    h = jload(HOLDINGS)
    if not h:
        raise SystemExit('  ! no .secrets/holdings.json - run python tools/kite_sync.py first, or pass --codes A,B,C')
    names = names_from(h)
    seen = {n['code'] for n in names}
    pos = jload(POSITIONS) or {}
    for p in pos.get('net') or []:
        code = re.sub(r'-(EQ|BE)$', '', str(p.get('tradingsymbol') or '').upper())
        if code and code not in seen and (p.get('quantity') or 0) > 0 and str(p.get('product') or '').upper() == 'CNC':
            names.append({'code': code, 'qty': p.get('quantity'), 'avg': p.get('average_price'), 'today': True}); seen.add(code)
    return names, f"holdings updated {h.get('updated')} from {h.get('source') or 'a file'}"


# ----------------------------------------------------------------------------------------------- the prompt
SYSTEM = """You read price charts for a swing trader's own book and write a structured review. You are not an adviser.

What you do: for each position, describe the daily structure (trend, moving-average posture, distance from highs, volatility, volume), the hourly structure of the last sessions (swing sequence, compression or expansion, where the last bars closed), name the candlestick patterns present in the data given and what each conventionally signals, and read support and resistance from the levels in the data (recent swing lows/highs, 10/20-day lows/highs, moving averages, the session VWAP). Then check the owner's own rules against the context fields: is the name inside the RS top list, on the Stage 2 list, above its 50- and 200-day averages, what the trend template, exhaustion and verdict fields say. Summarise the book: how many names sit inside the RS top list (the owner's rule is 70-80% of the book), concentration by industry, how many sit below their 50-day average.

What you never do: recommend or imply buying, selling, adding, trimming, exiting, hedging, position sizing, stops, targets or timing; predict prices or probabilities; use any number that is not in the data given; invent patterns the bars do not show. Write "no evidence" when the data does not support a statement. Where fields are null, say the data is missing rather than guessing.

Style: plain English, precise, short. Rupees, two decimals at most. "health" is a description of the trend structure only (intact = structure holds, weakening = structure fraying, broken = structure broken, unclear = conflicting), never an instruction. Keep each text field to one to three sentences."""

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'asof': {'type': 'string'},
        'market_read': {'type': 'string', 'description': 'what the book as a whole shows, two sentences, from the data only'},
        'positions': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'code': {'type': 'string'},
                'daily': {'type': 'string'},
                'hourly': {'type': 'string'},
                'patterns': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
                    'tf': {'type': 'string', 'enum': ['daily', 'hourly']}, 'name': {'type': 'string'}, 'where': {'type': 'string'}, 'read': {'type': 'string'}},
                    'required': ['tf', 'name', 'where', 'read']}},
                'levels': {'type': 'object', 'additionalProperties': False, 'properties': {
                    'support': {'type': 'array', 'items': {'type': 'number'}}, 'resistance': {'type': 'array', 'items': {'type': 'number'}}},
                    'required': ['support', 'resistance']},
                'rules': {'type': 'object', 'additionalProperties': False, 'properties': {
                    'in_top_list': {'type': 'boolean'}, 'on_stage2_list': {'type': 'boolean'}, 'above_50dma': {'type': 'boolean'}, 'above_200dma': {'type': 'boolean'},
                    'trend_template': {'type': 'string'}, 'exhaustion': {'type': 'string'}, 'verdict': {'type': 'string'}},
                    'required': ['in_top_list', 'on_stage2_list', 'above_50dma', 'above_200dma', 'trend_template', 'exhaustion', 'verdict']},
                'health': {'type': 'string', 'enum': ['intact', 'weakening', 'broken', 'unclear']},
                'flags': {'type': 'array', 'items': {'type': 'string'}},
                'would_change': {'type': 'string', 'description': 'which observable in the data would change this read, stated as a fact about the chart'},
            },
            'required': ['code', 'daily', 'hourly', 'patterns', 'levels', 'rules', 'health', 'flags', 'would_change'],
        }},
        'book': {'type': 'object', 'additionalProperties': False, 'properties': {
            'names': {'type': 'integer'}, 'inside_top_list': {'type': 'integer'}, 'share_pct': {'type': 'integer'},
            'below_50dma': {'type': 'integer'}, 'read': {'type': 'string'}, 'flags': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['names', 'inside_top_list', 'share_pct', 'below_50dma', 'read', 'flags']},
    },
    'required': ['asof', 'market_read', 'positions', 'book'],
}


def user_message(feats, book, ctx, now):
    head = {
        'as_of': now.strftime('%Y-%m-%d %H:%M IST'), 'scan_date': ctx['scan_date'], 'stage2_list_asof': ctx['stage2_asof'], 'rs_top_list_size': ctx['top'],
        'units': 'prices in rupees; *_pct fields are percent; *_in_atr fields are multiples of the 14-bar ATR; hourly bars are NSE 60-minute bars in exchange time',
        'book': book,
    }
    return 'Review this book. Return the JSON described by the output schema, one entry per position in the order given.\n\n' \
           + json.dumps({'context': head, 'positions': feats}, ensure_ascii=False, separators=(',', ':'))


def api_key():
    k = os.environ.get('ANTHROPIC_API_KEY') or ''
    if not k and os.path.exists(KEYFILE):
        k = io.open(KEYFILE, encoding='utf-8').read().strip()
    if not k:
        raise SystemExit('  ! no API key: set ANTHROPIC_API_KEY or put the key in .secrets/anthropic_key.txt (never in chat)')
    return k


def call_claude(key, model, effort, max_tokens, user):
    body = {
        'model': model, 'max_tokens': max_tokens,
        'system': [{'type': 'text', 'text': SYSTEM, 'cache_control': {'type': 'ephemeral'}}],
        'messages': [{'role': 'user', 'content': user}],
        'output_config': {'effort': effort, 'format': {'type': 'json_schema', 'schema': SCHEMA}},
        'fallbacks': 'default',
    }
    headers = {'Content-Type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01',
               'anthropic-beta': 'server-side-fallback-2026-07-01'}
    req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            doc = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get('error', {})
        except Exception:
            err = {}
        raise SystemExit(f"  ! Claude API HTTP {e.code}: {err.get('type', '')} {err.get('message', e.reason)}")
    stop = doc.get('stop_reason')
    if stop == 'refusal':
        sd = doc.get('stop_details') or {}
        raise SystemExit(f"  ! the request was declined by the model's safety classifiers ({sd.get('category')}): {sd.get('explanation') or ''}")
    text = ''.join(b.get('text', '') for b in doc.get('content', []) if b.get('type') == 'text')
    if stop == 'max_tokens':
        print('  ! the answer hit max_tokens and is cut off - raise --max-tokens')
    try:
        review = json.loads(text)
    except Exception:
        raise SystemExit('  ! the model did not return the JSON asked for (stop_reason=' + str(stop) + '). First 400 chars:\n' + text[:400])
    return review, doc.get('usage') or {}, doc.get('model') or model


def cost(usage, model):
    p = PRICES.get(model)
    if not p:
        return None
    i, o, cr = p
    return round((usage.get('input_tokens', 0) * i + usage.get('cache_creation_input_tokens', 0) * i * 1.25
                  + usage.get('cache_read_input_tokens', 0) * cr + usage.get('output_tokens', 0) * o) / 1e6, 4)


# ----------------------------------------------------------------------------------------------- output
def render(review, feats, book, meta):
    L = [f"# Positions review - {review.get('asof') or meta['when']}", '',
         f"_{meta['model']} · {meta['usage'].get('input_tokens', 0)} in / {meta['usage'].get('output_tokens', 0)} out tokens"
         + (f" · ~{meta['cost']:.3f} USD" if meta.get('cost') is not None else '') + f" · scan {meta['scan_date']} · {meta['source']}_", '',
         '> Describes the charts; it is not advice. Levels and patterns come only from the candles pulled at run time.', '',
         '## The book', '', review.get('market_read', ''), '']
    b = review.get('book') or {}
    L.append(f"- {b.get('inside_top_list')} of {b.get('names')} names inside the RS top list ({b.get('share_pct')}%; the owner's rule is 70-80%); "
             f"{b.get('below_50dma')} below the 50-day average.")
    if b.get('read'):
        L.append('- ' + b['read'])
    for f in b.get('flags') or []:
        L.append('- flag: ' + f)
    L.append('')
    byc = {f['code']: f for f in feats}
    for p in review.get('positions') or []:
        f = byc.get(p.get('code')) or {}
        d, h, r = f.get('daily') or {}, f.get('hourly') or {}, f.get('context') or {}
        hold = f.get('holding') or {}
        L += [f"## {p.get('code')} - {p.get('health', '')}" + (f"  ({r.get('name')})" if r.get('name') else ''), '']
        line = f"close {d.get('close')} ({d.get('last')})"
        if hold.get('avg'):
            line += f" · avg {hold['avg']} · P&L {hold.get('pnl_pct')}%"
        if hold.get('weight_pct') is not None:
            line += f" · {hold['weight_pct']}% of the book"
        line += (f" · RS {r.get('rs_rating')} rank #{r.get('rs_rank')}" if r.get('rs_rank') else ' · not in the scan')
        line += f" · {r.get('trend_template') or '-'} · {r.get('exhaustion') or '-'} · {r.get('verdict') or '-'}"
        L += ['_' + line + '_', '', '**Daily.** ' + p.get('daily', ''), '', '**Hourly.** ' + p.get('hourly', ''), '']
        if p.get('patterns'):
            L.append('**Patterns.**')
            for q in p['patterns']:
                L.append(f"- {q.get('tf')}: {q.get('name')} at {q.get('where')} - {q.get('read')}")
            L.append('')
        lv = p.get('levels') or {}
        L.append(f"**Levels.** support {', '.join(str(x) for x in lv.get('support') or []) or '-'}; resistance {', '.join(str(x) for x in lv.get('resistance') or []) or '-'}")
        ru = p.get('rules') or {}
        L.append(f"**Rules.** top list {'yes' if ru.get('in_top_list') else 'no'} · Stage 2 list {'yes' if ru.get('on_stage2_list') else 'no'} · "
                 f"above 50DMA {'yes' if ru.get('above_50dma') else 'no'} · above 200DMA {'yes' if ru.get('above_200dma') else 'no'} · "
                 f"{ru.get('trend_template') or '-'} · {ru.get('exhaustion') or '-'} · {ru.get('verdict') or '-'}")
        for fl in p.get('flags') or []:
            L.append('- flag: ' + fl)
        if p.get('would_change'):
            L.append('- would change the read: ' + p['would_change'])
        L.append('')
    L += ['## Numbers given to the model', '', '| code | close | 1d% | 1w% | vs 50DMA% | vs 200DMA% | from 52w high% | ATR% | 10d range/ATR | vol 5d/50d | hourly swings |', '|---|---|---|---|---|---|---|---|---|---|---|']
    for f in feats:
        d, h = f.get('daily') or {}, f.get('hourly') or {}
        L.append(f"| {f['code']} | {d.get('close')} | {d.get('chg_1d')} | {d.get('chg_1w')} | {d.get('vs_sma50_pct')} | {d.get('vs_sma200_pct')} | "
                 f"{d.get('from_52w_high_pct')} | {d.get('atr14_pct')} | {d.get('range10_in_atr')} | {d.get('vol_5d_vs_50d')} | {h.get('swing_structure_5d', h.get('note', '-'))} |")
    L.append('')
    return '\n'.join(L)


def market_open(now):
    return now.weekday() < 5 and dt.time(9, 15) <= now.time() <= dt.time(15, 30)


def run(a, now):
    names, source = load_book(a)
    ctx = context()
    feats = []
    total = 0.0
    print(f'  {len(names)} names ({source}); scan {ctx["scan_date"]}; candles from Yahoo (cache {a.max_age} min)')
    for n in names:
        code = n['code']
        d_bars, sym = candles(code, 'daily', a.max_age)
        h_bars, _ = candles(code, 'hourly', a.max_age)
        d = daily_features(d_bars) if d_bars else {'note': 'no daily candles'}
        h = hourly_features(h_bars) if h_bars else {'note': 'no hourly candles'}
        hold = {k: n.get(k) for k in ('qty', 'avg', 'today') if n.get(k) is not None}
        if hold.get('qty') and d.get('close'):
            hold['value'] = round(hold['qty'] * d['close'], 2); total += hold['value']
            if hold.get('avg'):
                hold['pnl_pct'] = pct(d['close'], hold['avg'])
        feats.append({'code': code, 'symbol': sym, 'holding': hold, 'context': rs_context(code, ctx), 'daily': d, 'hourly': h})
    for f in feats:
        if total and f['holding'].get('value'):
            f['holding']['weight_pct'] = round(100 * f['holding']['value'] / total, 1)
    inside = sum(1 for f in feats if f['context'].get('in_top_n'))
    below50 = sum(1 for f in feats if (f['daily'].get('vs_sma50_pct') or 0) < 0)
    by_ind = {}
    for f in feats:
        by_ind[f['context'].get('industry') or '?'] = by_ind.get(f['context'].get('industry') or '?', 0) + 1
    book = {'names': len(feats), 'inside_top_list': inside, 'share_inside_pct': round(100 * inside / len(feats)) if feats else 0,
            'below_50dma': below50, 'by_industry': by_ind, 'value_known': bool(total)}
    user = user_message(feats, book, ctx, now)
    est_in = len(SYSTEM) // 4 + len(user) // 4
    print(f'  prompt ~{est_in} tokens for {len(feats)} names')
    stamp = now.strftime('%Y-%m-%d_%H%M')
    if a.dry_run:
        jsave(os.path.join(REVIEWS, stamp + '_features.json'), {'features': feats, 'book': book})
        print(f'  --dry-run: features written to .secrets/reviews/{stamp}_features.json; nothing was sent')
        for f in feats:
            d = f['daily']; h = f['hourly']
            print(f"    {f['code']:<12} close {d.get('close')} vs50 {d.get('vs_sma50_pct')}% 52wH {d.get('from_52w_high_pct')}% ATR {d.get('atr14_pct')}% "
                  f"| hourly {h.get('swing_structure_5d', h.get('note'))} | {' / '.join((d.get('last3') or [{}])[-1].get('tags', []))}")
        return 0
    key = api_key()
    print(f'  asking {a.model} (effort {a.effort}) ...')
    t0 = time.time()
    review, usage, served = call_claude(key, a.model, a.effort, a.max_tokens, user)
    c = cost(usage, served)
    meta = {'when': now.strftime('%Y-%m-%d %H:%M IST'), 'model': served, 'usage': usage, 'cost': c, 'scan_date': ctx['scan_date'], 'source': source,
            'seconds': round(time.time() - t0, 1)}
    md = render(review, feats, book, meta)
    jsave(os.path.join(REVIEWS, stamp + '.json'), {'meta': meta, 'review': review, 'features': feats, 'book': book})
    with io.open(LATEST, 'w', encoding='utf-8') as fh:
        fh.write(md)
    if not a.quiet:
        sys.stdout.write(md.encode('ascii', 'replace').decode('ascii') + '\n')
    print(f"  done in {meta['seconds']} s: {usage.get('input_tokens', 0)} in (+{usage.get('cache_read_input_tokens', 0)} cached) / "
          f"{usage.get('output_tokens', 0)} out" + (f', ~{c} USD' if c is not None else '') + f' -> .secrets/positions_review.md, .secrets/reviews/{stamp}.json')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--codes', help='comma-separated NSE codes instead of the holdings file')
    ap.add_argument('--model', default='claude-opus-5-5', help='claude-opus-5-5 (default), claude-sonnet-5-5 or claude-haiku-5-5')
    ap.add_argument('--effort', default='medium', choices=['low', 'medium', 'high', 'xhigh', 'max'], help='thinking depth (default medium)')
    ap.add_argument('--max-tokens', type=int, default=16000, help='output ceiling (default 16000)')
    ap.add_argument('--max-age', type=int, default=20, help='reuse cached candles younger than this many minutes (default 20)')
    ap.add_argument('--dry-run', action='store_true', help='build everything, call nothing')
    ap.add_argument('--loop', type=int, metavar='MIN', help='repeat every MIN minutes while NSE is open, plus one pass at 15:40')
    ap.add_argument('--quiet', action='store_true', help='do not print the review, only where it went')
    a = ap.parse_args()
    if not a.loop:
        return run(a, dt.datetime.now(IST))
    closed_done = None
    while True:
        now = dt.datetime.now(IST)
        if market_open(now):
            run(a, now); closed_done = None
            nxt = now + dt.timedelta(minutes=a.loop)
        elif now.weekday() < 5 and now.time() >= dt.time(15, 40) and closed_done != now.date():
            print('  after the close:'); run(a, now); closed_done = now.date()
            nxt = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(9, 20), IST)
        else:
            nd = now.date() + dt.timedelta(days=1 if now.time() > dt.time(9, 20) or now.weekday() >= 5 else 0)
            while nd.weekday() >= 5:
                nd += dt.timedelta(days=1)
            nxt = dt.datetime.combine(nd, dt.time(9, 20), IST)
            print(f'  NSE is closed; next pass {nxt:%a %d %b %H:%M}')
        time.sleep(max(60, (nxt - dt.datetime.now(IST)).total_seconds()))


if __name__ == '__main__':
    sys.exit(main())
