#!/usr/bin/env python3
"""
Commodities & Macro module for the Swing Edge price card.

Declares the indicators (primary + fallback source each), derives the cross-series
(gold in INR/10 g, copper/gold, Brent in INR ...), scores the read-through regimes from
scripts/swing_edge/config.json and hands one JSON document to the shared writer.
"""
import datetime as dt

from common import (log, parse_yahoo_chart, parse_fred_csv, parse_westmetall_table,
                    yahoo_url, fred_url, westmetall_url, series_stats, value_on_or_before,
                    usd_lb_to_usd_t, usd_oz_to_inr_10g, usd_oz_to_inr_kg, utcnow_iso, to_ist)

MODULE = 'commodities'
WRITE_WINDOW_DAYS = 7

GROUPS = [
    {'id': 'energy', 'label': 'A · Energy'},
    {'id': 'precious', 'label': 'B · Precious metals'},
    {'id': 'base', 'label': 'C · Base metals'},
    {'id': 'fx', 'label': 'D · Currencies & rates'},
    {'id': 'india', 'label': 'E · India'},
    {'id': 'derived', 'label': 'F · Derived'},
]

# source tuples: (kind, symbol[, extra]). `mode='diff'` reports changes in points, not %.
INDICATORS = [
    dict(id='brent', label='Brent crude, front month', group='energy', unit='US$/bbl', dec=2, futures=True,
         primary=('yahoo', 'BZ=F'), fallback=('fred', 'DCOILBRENTEU')),
    dict(id='wti', label='WTI crude, front month', group='energy', unit='US$/bbl', dec=2, futures=True,
         primary=('yahoo', 'CL=F'), fallback=('fred', 'DCOILWTICO')),
    dict(id='natgas', label='Natural gas (Henry Hub)', group='energy', unit='US$/MMBtu', dec=3, futures=True,
         optional=True, primary=('yahoo', 'NG=F'), fallback=('fred', 'DHHNGSP')),
    # No key-free daily fallback exists for the precious metals (FRED's gold fix was discontinued and
    # Stooq blocks the runner), so a Yahoo outage carries the last print forward, flagged stale.
    dict(id='gold', label='Gold, front month', group='precious', unit='US$/oz', dec=1, futures=True,
         primary=('yahoo', 'GC=F'), fallback=None),
    dict(id='silver', label='Silver, front month', group='precious', unit='US$/oz', dec=2, futures=True,
         primary=('yahoo', 'SI=F'), fallback=None),
    dict(id='copper', label='Copper', group='base', unit='US$/t', dec=0, futures=True,
         primary=('yahoo', 'HG=F', 'lb_to_t'), fallback=('westmetall', 'LME_Cu_cash'),
         note='COMEX HG=F is quoted in US$/lb; converted ×2204.62 to US$/t. Fallback is the LME cash settlement.'),
    dict(id='aluminium', label='Aluminium', group='base', unit='US$/t', dec=0, futures=True, primary_max_age_days=3,
         primary=('yahoo', 'ALI=F'), fallback=('westmetall', 'LME_Al_cash'),
         note='COMEX ALI=F is thin; the LME cash table wins whenever COMEX is stale or gapped.'),
    dict(id='zinc', label='Zinc', group='base', unit='US$/t', dec=0, optional=True,
         primary=('westmetall', 'LME_Zn_cash'), fallback=None),
    dict(id='dxy', label='Dollar index (DXY)', group='fx', unit='index', dec=2,
         primary=('yahoo', 'DX-Y.NYB'), fallback=('fred', 'DTWEXBGS'),
         fallback_note='Showing the FRED trade-weighted broad dollar index (not DXY) — different level and weights.'),
    dict(id='usdjpy', label='USD/JPY', group='fx', unit='JPY per US$', dec=2,
         primary=('yahoo', 'JPY=X'), fallback=('fred', 'DEXJPUS')),
    dict(id='usdinr', label='USD/INR', group='fx', unit='INR per US$', dec=3,
         primary=('yahoo', 'INR=X'), fallback=('fred', 'DEXINUS'),
         note='Fallback is the Fed H.10 noon buying rate (FRED DEXINUS). FBIL publishes the RBI reference rate only as web pages, not a stable file; pin it in manual_overrides.json when the official print matters.'),
    dict(id='us10y', label='US 10-year yield', group='fx', unit='%', dec=3, mode='diff',
         primary=('yahoo', '^TNX'), fallback=('fred', 'DGS10')),
    dict(id='vix', label='VIX', group='fx', unit='index', dec=2, optional=True,
         primary=('yahoo', '^VIX'), fallback=None),
    dict(id='nifty50', label='Nifty 50', group='india', unit='index', dec=1,
         primary=('yahoo', '^NSEI'), fallback=None),
]

SECTORS = [
    dict(id='nifty_metal', label='Nifty Metal', symbol='^CNXMETAL'),
    dict(id='nifty_energy', label='Nifty Energy', symbol='^CNXENERGY'),
    dict(id='nifty_it', label='Nifty IT', symbol='^CNXIT'),
    dict(id='nifty_bank', label='Nifty Bank', symbol='^NSEBANK'),
]

SOURCE_META = {
    'yahoo': {'label': 'Yahoo Finance', 'url': 'https://finance.yahoo.com/'},
    'fred': {'label': 'FRED (St. Louis Fed)', 'url': 'https://fred.stlouisfed.org/'},
    'westmetall': {'label': 'Westmetall LME table', 'url': 'https://www.westmetall.com/en/markdaten.php'},
    'manual': {'label': 'manual_overrides.json', 'url': 'data/swing_edge/manual_overrides.json'},
    'derived': {'label': 'computed in fetch_prices.py', 'url': 'scripts/swing_edge/commodities.py'},
}


def source_url(kind, symbol, today):
    if kind == 'yahoo':
        return yahoo_url(symbol)
    if kind == 'fred':
        return fred_url(symbol, today - dt.timedelta(days=400))
    if kind == 'westmetall':
        return westmetall_url(symbol)
    raise ValueError(kind)


def fetch_series(fetcher, spec, today):
    kind, symbol = spec[0], spec[1]
    url = source_url(kind, symbol, today)
    text = fetcher.get(url, check_robots=(kind == 'westmetall'))
    if kind == 'yahoo':
        series = parse_yahoo_chart(text)
    elif kind == 'fred':
        series = parse_fred_csv(text)
    else:
        series = parse_westmetall_table(text)
    if len(spec) > 2 and spec[2] == 'lb_to_t':
        series = [(d, usd_lb_to_usd_t(v)) for d, v in series]
    if symbol == '^TNX':
        series = [(d, v / 10.0 if v > 20 else v) for d, v in series]  # CBOE quotes ×10 on some feeds
    if not series:
        raise ValueError('empty series')
    return series, f'{kind}:{symbol}', url


def merge_series(history_map, fetched, min_depth=60):
    """A source that returns enough history is used on its own, so two sources with slightly
    different levels (COMEX vs LME, Yahoo vs FRED) never meet inside one series and fake a move.
    A short feed (the LME table, a manual pin) is padded with the history CSV instead."""
    fetched = sorted(fetched)
    if len(fetched) >= min_depth:
        return fetched
    merged = {d: v for d, (v, _s, _st) in (history_map or {}).items()}
    for d, v in fetched:
        merged[d] = v
    return sorted(merged.items())


def resolve_indicator(fetcher, ind, history, overrides, prev_latest, today, max_age_default):
    """Returns (row, fetched_series_or_None). Order: manual override > primary (if fresh) >
    fallback(s) > carry-forward from history (stale) > failed."""
    iid = ind['id']
    hist_map = history.get(iid, {})
    row = {'id': iid, 'label': ind['label'], 'group': ind['group'], 'unit': ind['unit'], 'dec': ind.get('dec', 2),
           'source': None, 'source_url': None, 'status': 'failed', 'stale': False, 'last_good': None,
           'note': ind.get('note'), 'errors': []}
    ov = (overrides or {}).get(iid)
    if ov and ov.get('value') is not None:
        d = dt.date.fromisoformat(ov.get('date') or today.isoformat())
        fetched = [(d, float(ov['value']))]
        series = merge_series(hist_map, fetched)
        row.update(series_stats(series, is_futures=ind.get('futures', False)))
        row.update(source='manual', source_url=SOURCE_META['manual']['url'], status='manual',
                   note=(ov.get('note') or row['note']))
        log(iid, 'manual', 'manual', f'value={ov["value"]} date={d}')
        return row, fetched, series

    max_age = ind.get('primary_max_age_days', max_age_default)
    attempts = [('ok', ind.get('primary')), ('fallback', ind.get('fallback'))]
    chosen = None
    for status, spec in attempts:
        if not spec:
            continue
        try:
            series, src, url = fetch_series(fetcher, spec, today)
        except Exception as e:  # noqa: BLE001 - the next source gets its turn
            row['errors'].append(f'{spec[0]}:{spec[1]}: {e}')
            log(iid, f'{spec[0]}:{spec[1]}', 'failed', str(e)[:100])
            continue
        age = (today - series[-1][0]).days
        if chosen is None or series[-1][0] > chosen[0][-1][0]:
            chosen = (series, src, url, status, spec)
        if age <= max_age:
            break  # fresh enough; no need to hit the fallback
        log(iid, src, 'stale-source', f'last print {series[-1][0]} is {age}d old, trying fallback')
    if chosen:
        series, src, url, status, spec = chosen
        fetched = series
        full = merge_series(hist_map, fetched)
        row.update(series_stats(full, mode=ind.get('mode', 'pct'), is_futures=ind.get('futures', False)))
        row.update(source=src, source_url=url, status=status)
        if status == 'fallback' and ind.get('fallback_note'):
            row['note'] = ind['fallback_note']
        age = (today - series[-1][0]).days
        if age > max_age:
            row['stale'] = True
            row['last_good'] = series[-1][0].isoformat()
        log(iid, src, 'roll' if row.get('roll') else status, f'value={row["value"]} date={row["date"]}')
        return row, fetched, full

    # everything failed: carry the last value forward, flagged
    series = merge_series(hist_map, [])
    if not series and prev_latest:
        pv = next((r for r in prev_latest.get('indicators', []) if r['id'] == iid and r.get('value') is not None), None)
        if pv:
            series = [(dt.date.fromisoformat(pv['date']), pv['value'])]
    if series:
        row.update(series_stats(series, mode=ind.get('mode', 'pct'), is_futures=ind.get('futures', False)))
        row.update(status='stale', stale=True, last_good=series[-1][0].isoformat(), source='carry-forward')
        log(iid, 'carry-forward', 'stale', f'last_good={row["last_good"]} errors={len(row["errors"])}')
        return row, None, series
    log(iid, 'none', 'failed', '; '.join(row['errors'])[:200])
    return row, None, []


def align(a, b, fn):
    """Combine two [(date, v)] series on a's dates, b forward-filled."""
    out = []
    for d, va in a:
        vb = value_on_or_before(b, d)
        if vb is None:
            continue
        v = fn(va, vb)
        if v is not None:
            out.append((d, v))
    return out


def derived_rows(series_by_id):
    gold, silver, copper, brent, inr = (series_by_id.get(k, []) for k in ('gold', 'silver', 'copper', 'brent', 'usdinr'))
    specs = [
        ('gold_inr_10g', 'Gold in INR per 10 g', 'INR/10 g', 0, align(gold, inr, usd_oz_to_inr_10g),
         'US$/oz × USD/INR ÷ 31.1035 × 10. MCX differs by import duty and local premium.'),
        ('silver_inr_kg', 'Silver in INR per kg', 'INR/kg', 0, align(silver, inr, usd_oz_to_inr_kg),
         'US$/oz × USD/INR ÷ 31.1035 × 1000. MCX differs by duty and premium.'),
        ('gold_silver_ratio', 'Gold / silver ratio', 'oz', 1, align(gold, silver, lambda g, s: g / s if s else None),
         'Ounces of silver per ounce of gold; rising = silver lagging.'),
        ('copper_gold_ratio', 'Copper / gold ratio', 'ratio', 3, align(copper, gold, lambda c, g: c / g if g else None),
         'US$/t copper ÷ US$/oz gold — growth-versus-fear gauge; rising = growth bid.'),
        ('brent_inr_bbl', 'Brent in INR per barrel', 'INR/bbl', 0, align(brent, inr, lambda b, i: b * i),
         "India's import-bill pressure: Brent US$/bbl × USD/INR."),
    ]
    rows = []
    for iid, label, unit, dec, series, note in specs:
        row = {'id': iid, 'label': label, 'group': 'derived', 'unit': unit, 'dec': dec, 'source': 'derived',
               'source_url': SOURCE_META['derived']['url'], 'status': 'ok' if series else 'failed',
               'stale': False, 'last_good': None, 'note': note, 'errors': []}
        if series:
            row.update(series_stats(series, is_futures=False))
            row['roll'] = False
        log(iid, 'derived', row['status'], f'value={row.get("value")}' if series else 'inputs missing')
        rows.append(row)
    return rows


def evaluate_regime(rule, by_id):
    vals = []
    for iid in rule['inputs']:
        r = by_id.get(iid) or {}
        vals.append(r.get(rule.get('metric', 'chg_30d')))
    if any(v is None for v in vals):
        value = None
    elif rule.get('combine') == 'mean':
        value = sum(vals) / len(vals)
    elif rule.get('combine') == 'diff':
        value = vals[0] - vals[1]
    else:
        value = vals[0]
    out = {'id': rule['id'], 'label': rule.get('label', rule['id']), 'input_label': rule.get('input_label', rule['id']),
           'input': None if value is None else round(value, 2), 'metric': rule.get('metric', 'chg_30d'),
           'hi': rule.get('hi'), 'lo': rule.get('lo'), 'inputs': rule['inputs'],
           'stale': any((by_id.get(i) or {}).get('stale') or (by_id.get(i) or {}).get('status') in ('failed', 'stale') for i in rule['inputs'])}
    stocks = rule.get('stocks') or {}
    if value is None:
        out.update(verdict='no data', read='inputs missing', tone='neutral', side='none', positive=[], negative=[])
    elif rule.get('hi') is not None and value > rule['hi']:
        out.update(verdict=rule.get('hi_verdict', 'high'), read=rule.get('hi_read', ''), tone=rule.get('hi_tone', 'warn'),
                   side='hi', positive=stocks.get('hi_positive', []), negative=stocks.get('hi_negative', []))
    elif rule.get('lo') is not None and value < rule['lo']:
        out.update(verdict=rule.get('lo_verdict', 'low'), read=rule.get('lo_read', ''), tone=rule.get('lo_tone', 'ok'),
                   side='lo', positive=stocks.get('lo_positive', []), negative=stocks.get('lo_negative', []))
    else:
        out.update(verdict=rule.get('mid_verdict', 'neutral'), read=rule.get('mid_read', ''), tone='neutral',
                   side='mid', positive=[], negative=[])
    if rule.get('secondary'):
        out['secondary'] = evaluate_regime(rule['secondary'], by_id)
    return out


def build(fetcher, cfg, history, overrides, prev_latest, today, run_label, backfill_days):
    """Fetch + score. Returns (latest_doc, history_updates) where history_updates is
    {indicator: {date: (value, source, status)}} for rows that came from a live print."""
    mcfg = cfg[MODULE]
    max_age = mcfg.get('primary_max_age_days', 5)
    rows, updates, series_by_id = [], {}, {}
    for ind in INDICATORS:
        row, fetched, full = resolve_indicator(fetcher, ind, history, overrides, prev_latest, today, max_age)
        row.setdefault('roll', False)
        rows.append(row)
        series_by_id[ind['id']] = full
        if fetched:
            # always rewrite the last week: a morning run stores an intraday print for today, and
            # the next runs replace it with the settled close once the feed carries it
            keep = fetched[-max(WRITE_WINDOW_DAYS, backfill_days):]
            updates[ind['id']] = {d: (v, row['source'], row['status']) for d, v in keep}
    rows.extend(derived_rows(series_by_id))
    by_id = {r['id']: r for r in rows}

    nifty = by_id.get('nifty50') or {}
    sectors = []
    for s in SECTORS:
        srow = {'id': s['id'], 'label': s['label'], 'symbol': s['symbol'], 'status': 'failed'}
        try:
            series, src, url = fetch_series(fetcher, ('yahoo', s['symbol']), today)
            full = merge_series(history.get(s['id'], {}), series)
            srow.update(series_stats(full, is_futures=False))
            srow.update(source=src, source_url=url, status='ok',
                        stale=(today - series[-1][0]).days > max_age)
            keep = series[-max(WRITE_WINDOW_DAYS, backfill_days):]
            updates[s['id']] = {d: (v, src, 'ok') for d, v in keep}
            for k in ('chg_1d', 'chg_7d', 'chg_30d'):
                srow['rel_' + k[4:]] = (None if srow.get(k) is None or nifty.get(k) is None
                                        else round(srow[k] - nifty[k], 3))
            log(s['id'], src, 'ok', f'value={srow["value"]} date={srow["date"]}')
        except Exception as e:  # noqa: BLE001 - a missing sector index is not fatal
            srow['error'] = str(e)[:120]
            log(s['id'], f'yahoo:{s["symbol"]}', 'failed', str(e)[:100])
        sectors.append(srow)

    regimes = [evaluate_regime(rule, by_id) for rule in mcfg.get('regimes', [])]

    sources = {}
    for r in rows:
        kind = (r.get('source') or 'none').split(':')[0]
        meta = SOURCE_META.get(kind, {'label': kind, 'url': ''})
        s = sources.setdefault(kind, {'id': kind, 'label': meta['label'], 'url': meta['url'], 'fields': [], 'failed': []})
        s['fields'].append({'id': r['id'], 'url': r.get('source_url'), 'status': r['status']})
    for r in rows:
        for err in r.get('errors', []):
            kind = err.split(':')[0]
            meta = SOURCE_META.get(kind, {'label': kind, 'url': ''})
            s = sources.setdefault(kind, {'id': kind, 'label': meta['label'], 'url': meta['url'], 'fields': [], 'failed': []})
            s['failed'].append({'id': r['id'], 'error': err.split(': ', 1)[-1][:120]})
    for kind, s in sources.items():
        host = {'yahoo': 'query2.finance.yahoo.com', 'fred': 'fred.stlouisfed.org',
                'westmetall': 'www.westmetall.com'}.get(kind)
        s['last_ok'] = fetcher.last_ok.get(host) if host else None

    dated = [r['date'] for r in rows if r.get('date') and r['status'] in ('ok', 'fallback', 'manual', 'roll')]
    run_at = utcnow_iso()
    doc = {
        'schema': 'swing_edge-prices/1', 'module': MODULE, 'title': mcfg.get('title', 'Commodities & Macro'),
        'data_date': max(dated) if dated else None, 'run_at': run_at, 'run_at_ist': to_ist(run_at),
        'run_label': run_label, 'groups': GROUPS, 'indicators': rows, 'regimes': regimes, 'sectors': sectors,
        'sources': list(sources.values()), 'disclaimer': mcfg.get('disclaimer', ''),
        'counts': {'ok': sum(r['status'] == 'ok' for r in rows), 'fallback': sum(r['status'] == 'fallback' for r in rows),
                   'stale': sum(bool(r.get('stale')) for r in rows), 'failed': sum(r['status'] == 'failed' for r in rows),
                   'roll': sum(bool(r.get('roll')) for r in rows), 'manual': sum(r['status'] == 'manual' for r in rows)},
    }
    return doc, updates


def probe_sources(fetcher, today):
    """Hit every primary and fallback once and report what answers — the source table for a
    build report. Writes nothing."""
    bad = 0
    targets = [(ind['id'], spec) for ind in INDICATORS for spec in (ind.get('primary'), ind.get('fallback')) if spec]
    targets += [(s['id'], ('yahoo', s['symbol'])) for s in SECTORS]
    for iid, spec in targets:
        try:
            series, src, _url = fetch_series(fetcher, spec, today)
            log(iid, src, 'ok', f'last={series[-1][0]} n={len(series)} age={(today - series[-1][0]).days}d')
        except Exception as e:  # noqa: BLE001
            bad += 1
            log(iid, f'{spec[0]}:{spec[1]}', 'failed', str(e)[:120])
    print(f'probe: {len(targets) - bad}/{len(targets)} sources answered')
    return bad


def stock_symbols(cfg):
    out = set()
    for rule in cfg[MODULE].get('regimes', []):
        for r in (rule, rule.get('secondary') or {}):
            for lst in (r.get('stocks') or {}).values():
                out.update(lst)
    return sorted(out)
