#!/usr/bin/env python3
"""
fetch_themes.py — the outside driver, tracked next to the stock it is supposed to move.

    python tools/fetch_themes.py                 # refresh every tracker in tools/themes/
    python tools/fetch_themes.py oil-fcl         # just one
    python tools/fetch_themes.py --offline       # rebuild from cache only, fetch nothing

A thesis like "Texas crude production drives Fineotex" is a claim about two series. This tool
keeps both on disk so the app can put them on one page: the driver (crude prices, production,
rigs, wells) and the stock. It draws no conclusion. Each tracker is a config file,
tools/themes/<id>.json, that names its series and where each comes from; the output is
data/themes/<id>.json plus data/themes/index.json, which is all the app reads.

SOURCES, all public and keyless
  yahoo     daily closes from Yahoo's chart endpoint (front-month crude futures; NSE stocks,
            split-adjusted). Unofficial, so it is never the only copy of the stock's price:
  scan      the stock's own closes from data/scans/, which override Yahoo on their dates and
            extend past its last point (Yahoo's NSE closes lag by a session or two).
  eia_dnav  EIA's petroleum "history" pages: state and national crude production, monthly.
  steo      EIA's Short-Term Energy Outlook bulk file: Permian production, active rigs, wells
            drilled and completed, DUCs — with EIA's own forecast, marked as forecast.
  bh_rigs   Baker Hughes' North America rig count workbook: every rig, by county, every week
            since January 2024, summed here to Texas and to the Permian basin. Their server
            answers a plain client and returns 403 to anything pretending to be a browser, so
            this source identifies itself honestly instead of spoofing one.
  points    a hand-curated list of dated datapoints, each with its source URL, for series no
            public feed carries (memory contract prices). Never interpolated.
  derived   a difference of two other series (the WTI-Brent spread).

STDLIB ONLY. The scheduled task cannot see user-site packages (see eod.py, stage2_asof), so
this file imports nothing that is not in the standard library. Test with `python -s`.

FAILS SOFT. A source that does not answer leaves that series as it was in the previous output,
listed under "errors", and the exit code stays 0: a missing oil price must never stop the
evening scan. Downloads are cached in exports/themes_cache/ (gitignored) and reused while fresh.
"""
import argparse, datetime as dt, glob, io, json, os, re, sys, time, urllib.request, zipfile
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(ROOT, 'tools', 'themes')
OUT = os.path.join(ROOT, 'data', 'themes')
CACHE = os.path.join(ROOT, 'exports', 'themes_cache')
SCHEMA = 'swingedge-theme/1'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')
OFFLINE = False


HONEST_UA = 'SwingEdge-tracker/1.0 (personal research; +https://github.com/suyog2305/swingedge)'


def cached(url, name, max_age_hours, ua=None):
    """Bytes of `url`, from exports/themes_cache/<name> when that copy is fresh enough."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    fresh = os.path.exists(path) and (time.time() - os.path.getmtime(path)) < max_age_hours * 3600
    if fresh or (OFFLINE and os.path.exists(path)):
        return io.open(path, 'rb').read()
    if OFFLINE:
        raise RuntimeError(f'offline and no cached copy of {name}')
    req = urllib.request.Request(url, headers={'User-Agent': ua or UA, 'Accept': '*/*'})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    if not data:
        raise RuntimeError(f'empty response from {url}')
    io.open(path, 'wb').write(data)
    return data


# ---------------------------------------------------------------- sources
def src_yahoo(spec):
    sym = spec['symbol']
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.request.quote(sym, safe='')
           + f"?range={spec.get('range', '5y')}&interval=1d")
    d = json.loads(cached(url, 'yahoo_' + re.sub(r'[^A-Za-z0-9]+', '_', sym) + '.json', 6).decode('utf-8'))
    res = d['chart']['result'][0]
    closes = res['indicators']['quote'][0]['close']
    pts = OrderedDict()
    for ts, c in zip(res['timestamp'], closes):
        if c is None:
            continue
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
        pts[day] = round(float(c), 4)
    return [[k, v] for k, v in pts.items()], None


def src_scan(spec, base):
    """The stock's own closes from the scan archive win on their dates and extend the series."""
    code = spec['code'].upper()
    pts = OrderedDict((d, v) for d, v in base)
    n = 0
    for p in sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json'))):
        try:
            d = json.load(io.open(p, encoding='utf-8'))
        except Exception:
            continue
        U = d.get('universe') or []
        if len(U) < 500:
            continue                                   # narrow pulls are not a price source
        row = next((r for r in U if str(r.get('code', '')).upper() == code), None)
        if row and isinstance(row.get('price'), (int, float)):
            pts[d.get('date') or os.path.basename(p)[:10]] = float(row['price']); n += 1
    return [[k, pts[k]] for k in sorted(pts)], n


def src_eia_dnav(spec):
    sid = spec['series']
    url = f'https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s={sid}&f=M'
    h = cached(url, f'eia_{sid}.html', 72).decode('utf-8', 'replace')
    scale = spec.get('scale', 1)
    pts = []
    for year, rest in re.findall(r"<td class=['\"]B4['\"]>(?:&nbsp;|\s)*(\d{4})\s*</td>(.*?)</tr>", h, re.S):
        cells = re.findall(r"<td class=['\"]B3['\"]>\s*([\d,\.\-]*)\s*(?:&nbsp;)*</td>", rest)
        for m, c in enumerate(cells[:12], 1):
            c = c.replace(',', '').strip()
            if c and c not in ('-', '--'):
                pts.append([f'{year}-{m:02d}-01', round(float(c) * scale, 4)])
    if not pts:
        raise RuntimeError(f'no monthly values parsed from the EIA page for {sid}')
    return pts, None


_STEO = {}


def steo_all():
    if _STEO:
        return _STEO
    raw = cached('https://www.eia.gov/opendata/bulk/STEO.zip', 'STEO.zip', 72)
    z = zipfile.ZipFile(io.BytesIO(raw))
    with z.open(z.namelist()[0]) as fh:
        for line in io.TextIOWrapper(fh, encoding='utf-8'):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get('f') == 'M' and d.get('series_id'):
                _STEO[d['series_id']] = d
    return _STEO


def src_steo(spec):
    S = steo_all()
    d = S.get(f"STEO.{spec['id']}.M")
    if not d:
        raise RuntimeError(f"STEO has no monthly series {spec['id']}")
    # The file's own "lastHistoricalPeriod" is unreliable per series (it reads 2027-12 on some
    # that are plainly forecast). The WTI price series carries the true last actual month.
    ref = S.get('STEO.WTIPUUS.M') or d
    last_hist = str(ref.get('lastHistoricalPeriod') or d.get('lastHistoricalPeriod') or '')
    scale = spec.get('scale', 1)
    pts = sorted([f'{p[0][:4]}-{p[0][4:6]}-01', round(float(p[1]) * scale, 4)] for p in d['data']
                 if p and p[1] is not None)
    fc = None
    if last_hist:
        nxt = dt.date(int(last_hist[:4]), int(last_hist[4:6]), 1) + dt.timedelta(days=32)
        first_fc = nxt.replace(day=1).isoformat()
        if any(x[0] >= first_fc for x in pts):
            fc = first_fc
    return pts, fc


_BH = {}


def bh_weekly():
    """{'texas': [[date, rigs], ...], 'permian': [...]} from the current Baker Hughes workbook.
    The landing page links the workbook by a GUID that changes every Friday; the workbook is 7 MB
    and its weekly sheet has 110,000 rows, so both the file and the sums are cached by GUID."""
    if _BH:
        return _BH
    page = cached('https://rigcount.bakerhughes.com/na-rig-count', 'bh_landing.html', 6, ua=HONEST_UA).decode('utf-8', 'replace')
    best = None
    for href, title in re.findall(r'href="(/static-files/[0-9a-f\-]+)"[^>]*title="([^"]+)"', page):
        m = re.match(r'(\d{2})-(\d{2})-(\d{4}) .*Rig.?Count Report\.xlsx$', title)
        if m:
            key = (m.group(3), m.group(1), m.group(2))
            if best is None or key > best[0]:
                best = (key, href, title)
    if not best:
        raise RuntimeError('no dated rig-count workbook linked from the Baker Hughes page')
    guid = best[1].rsplit('/', 1)[1]
    agg_path = os.path.join(CACHE, f'bh_weekly_{guid}.json')
    if os.path.exists(agg_path):
        _BH.update(json.load(io.open(agg_path, encoding='utf-8')))
        return _BH
    raw = cached('https://rigcount.bakerhughes.com' + best[1], f'bh_{guid}.xlsx', 24 * 365, ua=HONEST_UA)
    z = zipfile.ZipFile(io.BytesIO(raw))
    M = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    import xml.etree.ElementTree as ET
    rels = {r.get('Id'): r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
    shared = [''.join(t.text or '' for t in si.iter('{%s}t' % M))
              for si in ET.fromstring(z.read('xl/sharedStrings.xml')).iter('{%s}si' % M)]
    sheets = ET.fromstring(z.read('xl/workbook.xml')).find('{%s}sheets' % M)
    target = next((rels[x.get('{%s}id' % R)] for x in sheets if x.get('name') == 'NAM Weekly'), None)
    if not target:
        raise RuntimeError('the workbook has no "NAM Weekly" sheet')
    path = target if target.startswith('xl/') else 'xl/' + target.lstrip('/')

    def colno(ref):
        n = 0
        for ch in re.match(r'[A-Z]+', ref).group(0):
            n = n * 26 + ord(ch) - 64
        return n - 1
    H, tx, pm = None, {}, {}
    for _, el in ET.iterparse(z.open(path), events=('end',)):
        if el.tag != '{%s}row' % M:
            continue
        cells = {}
        for c in el.findall('{%s}c' % M):
            v = c.find('{%s}v' % M)
            if v is not None:
                cells[colno(c.get('r'))] = shared[int(v.text)] if c.get('t') == 's' else v.text
        el.clear()
        if not cells:
            continue
        if H is None:
            names = {str(v).strip(): k for k, v in cells.items()}
            if 'Basin' in names and 'Rig Count Value' in names:
                H = names
            continue
        if str(cells.get(H['Country'], '')).strip().upper() != 'UNITED STATES':
            continue
        try:
            day = (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(cells[H['US_PublishDate']])))).isoformat()
            n = float(cells[H['Rig Count Value']])
        except (KeyError, TypeError, ValueError):
            continue
        if str(cells.get(H['State/Province'], '')).strip().upper() == 'TEXAS':
            tx[day] = tx.get(day, 0) + n
        if str(cells.get(H['Basin'], '')).strip().lower() == 'permian':
            pm[day] = pm.get(day, 0) + n
    if not tx or not pm:
        raise RuntimeError('no Texas or Permian rows found in the weekly sheet')
    out = {'texas': [[d, tx[d]] for d in sorted(tx)], 'permian': [[d, pm[d]] for d in sorted(pm)], 'workbook': best[2]}
    io.open(agg_path, 'w', encoding='utf-8').write(json.dumps(out))
    for old in glob.glob(os.path.join(CACHE, 'bh_*')):               # last week's 7 MB is no use to anyone
        if guid not in os.path.basename(old) and not old.endswith('bh_landing.html'):
            try: os.remove(old)
            except OSError: pass
    _BH.update(out)
    return _BH


def src_bh_rigs(spec):
    pts = bh_weekly()[spec['region']]
    return [[d, round(v, 1)] for d, v in pts], None


def src_points(spec):
    """Hand-curated datapoints: [{series, date, value, unit, basis, source_url, note}, ...]."""
    path = os.path.join(ROOT, spec['file'])
    rows = [r for r in json.load(io.open(path, encoding='utf-8')) if r.get('series') == spec['series']
            and isinstance(r.get('value'), (int, float)) and r.get('date')]
    if not rows:
        raise RuntimeError(f"no datapoints for \"{spec['series']}\" in {spec['file']}")
    rows.sort(key=lambda r: r['date'])
    pts = [[r['date'], r['value']] for r in rows]
    cites = [OrderedDict(date=r['date'], url=r.get('source_url'), note=r.get('note'), basis=r.get('basis')) for r in rows]
    return pts, cites


def trim(pts, since):
    return [p for p in pts if p[0] >= since] if since else pts


# ---------------------------------------------------------------- build
def build(cfg_path):
    cfg = json.load(io.open(cfg_path, encoding='utf-8'), object_pairs_hook=OrderedDict)
    tid = cfg['id']
    out_path = os.path.join(OUT, tid + '.json')
    prev = {}
    if os.path.exists(out_path):
        try:
            prev = json.load(io.open(out_path, encoding='utf-8')).get('series', {})
        except Exception:
            prev = {}
    series, errors = OrderedDict(), []
    for key, spec in cfg['series'].items():
        meta = OrderedDict((k, spec[k]) for k in ('label', 'short', 'unit', 'freq', 'slot', 'digits', 'source', 'url') if k in spec)
        try:
            kind = spec['src']
            fc, cites, extra = None, None, None
            if kind == 'yahoo':
                pts, _ = src_yahoo(spec)
                if spec.get('scan_code'):
                    pts, extra = src_scan({'code': spec['scan_code']}, pts)
            elif kind == 'eia_dnav':
                pts, _ = src_eia_dnav(spec)
            elif kind == 'steo':
                pts, fc = src_steo(spec)
            elif kind == 'bh_rigs':
                pts, _ = src_bh_rigs(spec)
            elif kind == 'points':
                pts, cites = src_points(spec)
            elif kind == 'derived':
                a = dict(series[spec['a']]['points']); b = dict(series[spec['b']]['points'])
                pts = [[d, round(a[d] - b[d], 4)] for d in sorted(a) if d in b]
            else:
                raise RuntimeError(f'unknown source kind "{kind}"')
            pts = trim(pts, spec.get('since'))
            if not pts:
                raise RuntimeError('no points')
            meta['points'] = pts
            if fc: meta['forecast_from'] = fc
            if cites: meta['cites'] = cites
            if extra: meta['scan_closes'] = extra
        except Exception as e:                                   # fail soft: keep what we had
            errors.append(f'{key}: {type(e).__name__}: {e}')
            if key in prev and prev[key].get('points'):
                meta.update({k: prev[key][k] for k in ('points', 'forecast_from', 'cites', 'scan_closes') if k in prev[key]})
                meta['stale'] = True
            else:
                meta['points'] = []
        series[key] = meta

    doc = OrderedDict(schema=SCHEMA, id=tid, title=cfg['title'], updated=dt.date.today().isoformat())
    for k in ('order', 'stock', 'thesis', 'audit', 'kpis', 'charts', 'watch', 'cannot', 'notes'):
        if k in cfg:
            doc[k] = cfg[k]
    doc['series'] = series
    # Tables of cited figures, for drivers no public feed carries. Rows come verbatim from a
    # curated points file: the value as recorded, the researcher's note in the source's own terms,
    # and the URL. Nothing is computed from them and nothing is interpolated.
    if cfg.get('tables'):
        doc['tables'] = []
        for t in cfg['tables']:
            try:
                allrows = json.load(io.open(os.path.join(ROOT, t['file']), encoding='utf-8'))
                want = list(t['series'])
                rows = sorted((r for r in allrows if r.get('series') in want),
                              key=lambda r: (r.get('date') or '', want.index(r['series'])))
                doc['tables'].append(OrderedDict(title=t['title'], sub=t.get('sub'), rows=[
                    OrderedDict(date=r.get('date'), period=r.get('period'), item=(t.get('names') or {}).get(r['series'], r['series']),
                                value=r.get('value'), unit=r.get('unit'), basis=r.get('basis'), note=r.get('note'), url=r.get('source_url'))
                    for r in rows]))
            except Exception as e:
                errors.append(f"table \"{t.get('title')}\": {type(e).__name__}: {e}")
    if errors:
        doc['errors'] = errors
    os.makedirs(OUT, exist_ok=True)
    io.open(out_path, 'w', encoding='utf-8').write(json.dumps(doc, ensure_ascii=False, separators=(',', ':')))
    return doc, errors, out_path


def main():
    global OFFLINE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ids', nargs='*', help='tracker ids (default: every config in tools/themes/)')
    ap.add_argument('--offline', action='store_true', help='use cached downloads only')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    OFFLINE = a.offline
    paths = sorted(p for p in glob.glob(os.path.join(CONFIGS, '*.json')) if not os.path.basename(p).startswith('_'))
    paths = [p for p in paths if os.path.basename(p)[:-5] != 'memory_points' and 'points' not in os.path.basename(p)]
    if a.ids:
        paths = [p for p in paths if os.path.basename(p)[:-5] in a.ids]
    if not paths:
        print('no tracker configs found in tools/themes/'); return 0
    index = []
    for p in paths:
        try:
            doc, errors, out_path = build(p)
        except Exception as e:
            print(f'{os.path.basename(p)}: config could not be built: {type(e).__name__}: {e}')
            continue
        newest = max((s['points'][-1][0] for s in doc['series'].values() if s.get('points') and not s.get('forecast_from')), default=None)
        index.append(OrderedDict(id=doc['id'], order=doc.get('order', 99), title=doc['title'], file=doc['id'] + '.json',
                                 stock=(doc.get('stock') or {}).get('code'), updated=doc['updated'],
                                 newest=newest, audit=(doc.get('audit') or {}).get('status'), errors=len(errors)))
        if not a.quiet:
            print(f"{doc['id']}  ->  data/themes/{doc['id']}.json  ({os.path.getsize(out_path):,} bytes)")
            for k, s in doc['series'].items():
                pts = s.get('points') or []
                tail = f"{pts[0][0]} .. {pts[-1][0]}  last {pts[-1][1]}" if pts else 'NO DATA'
                print(f"  {k:<20} {len(pts):>5} pts  {tail}"
                      + (f"  forecast from {s['forecast_from']}" if s.get('forecast_from') else '')
                      + ('  [STALE: reused previous]' if s.get('stale') else ''))
        for e in errors:
            print(f'  WARNING {e}')
    # keep index entries for trackers not rebuilt in this run
    ipath = os.path.join(OUT, 'index.json')
    if a.ids and os.path.exists(ipath):
        try:
            old = json.load(io.open(ipath, encoding='utf-8')).get('trackers', [])
            index += [t for t in old if t.get('id') not in {x['id'] for x in index}]
        except Exception:
            pass
    index.sort(key=lambda t: (t.get('order', 99), t['id']))
    os.makedirs(OUT, exist_ok=True)
    io.open(ipath, 'w', encoding='utf-8').write(json.dumps(OrderedDict(updated=dt.date.today().isoformat(), trackers=index), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
