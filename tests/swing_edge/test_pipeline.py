"""End-to-end run of fetch_prices.py against a stub network: fallbacks, stale carry-forward,
manual overrides, derived rows, regimes and idempotent history."""
import contextlib, datetime as dt, io, json, os, tempfile, unittest
from unittest import mock

from tests.swing_edge._paths import fixture, ROOT
import common, fetch_prices

TODAY = '2026-09-16'


class StubFetcher(common.Fetcher):
    """Yahoo works for BZ=F, GC=F, ALI=F, ^NSEI and ^CNXIT; FRED and Westmetall answer from
    fixtures; everything else is a dead host. `down` adds hosts that should fail."""

    def __init__(self, down=()):
        super().__init__(timeout=1, retries=0, sleep=0)
        self.down = set(down)
        self.calls = []

    def get(self, url, check_robots=False):
        self.calls.append(url)
        host = url.split('/')[2]
        if host in self.down:
            self.errors[host] = 'HTTP 503'
            raise RuntimeError('HTTP 503')
        if 'yahoo' in host:
            if any(s in url for s in ('BZ%3DF', 'GC%3DF', 'ALI%3DF', '%5ENSEI', '%5ECNXIT')):
                self.last_ok[host] = common.utcnow_iso()
                return fixture('yahoo_BZ=F.json')
            raise RuntimeError('HTTP 404')
        if 'fred' in host:
            self.last_ok[host] = common.utcnow_iso()
            return fixture('fred_DCOILBRENTEU.csv')
        if 'westmetall' in host:
            self.last_ok[host] = common.utcnow_iso()
            return fixture('westmetall_LME_Cu_cash.html')
        raise RuntimeError('unknown host ' + host)


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def run(out, extra=(), fetcher=None):
    fetcher = fetcher or StubFetcher()
    buf = io.StringIO()
    with mock.patch.object(common, 'Fetcher', return_value=fetcher), contextlib.redirect_stdout(buf):
        code = fetch_prices.main(['--module', 'commodities', '--out', out, '--date', TODAY, '--label', 'morning', *extra])
    return code, buf.getvalue(), fetcher


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.out = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def latest(self):
        with open(os.path.join(self.out, 'commodities_latest.json'), encoding='utf-8') as fh:
            return json.load(fh)

    def test_sources_fallbacks_and_markers(self):
        code, log, f = run(self.out)
        doc = self.latest()
        by = {r['id']: r for r in doc['indicators']}
        self.assertEqual(by['brent']['status'], 'ok')
        self.assertTrue(by['brent']['roll'], 'the +9% last day on a futures series is flagged as a roll')
        self.assertEqual(by['wti']['status'], 'fallback')            # yahoo 404 -> FRED
        self.assertTrue(by['wti']['source'].startswith('fred:'))
        self.assertEqual(by['gold']['status'], 'ok')
        self.assertEqual(by['silver']['status'], 'failed')           # yahoo 404, no fallback, no history
        self.assertEqual(by['copper']['status'], 'fallback')         # -> LME table
        self.assertEqual(by['copper']['unit'], 'US$/t')
        self.assertEqual(by['aluminium']['status'], 'ok')
        self.assertEqual(by['usdjpy']['status'], 'fallback')         # -> FRED DEXJPUS
        self.assertEqual(by['dxy']['status'], 'fallback')
        self.assertIn('broad dollar index', by['dxy']['note'])       # fallback is labelled as not-DXY
        self.assertEqual(by['vix']['status'], 'failed')              # no fallback, no history
        self.assertEqual(by['nifty50']['status'], 'ok')
        self.assertIn('field=wti source=yahoo:CL=F status=failed', log)
        self.assertIn('field=brent source=yahoo:BZ=F status=roll', log)
        self.assertIn('field=vix source=none status=failed', log)
        # derived rows exist when their inputs do
        self.assertEqual(by['gold_inr_10g']['status'], 'ok')          # gold (yahoo) x usdinr (fred fixture)
        self.assertEqual(by['silver_inr_kg']['status'], 'failed')     # silver missing -> derived row missing too
        self.assertEqual(by['brent_inr_bbl']['status'], 'ok')
        self.assertEqual(by['copper_gold_ratio']['status'], 'ok')
        # regimes carry their inputs and stock lists
        reg = {r['id']: r for r in doc['regimes']}
        self.assertIn(reg['crude']['side'], ('hi', 'lo', 'mid'))
        self.assertIsNotNone(reg['crude']['input'])
        self.assertEqual(reg['precious']['secondary']['id'], 'silver_vs_gold')
        self.assertEqual(reg['yen']['verdict'], 'no data' if reg['yen']['input'] is None else reg['yen']['verdict'])
        # footer sources record what worked and what failed, per field
        src = {s['id']: s for s in doc['sources']}
        self.assertTrue(any(x['id'] == 'wti' for x in src['yahoo']['failed']))
        self.assertTrue(any(x['id'] == 'wti' for x in src['fred']['fields']))
        self.assertIsNotNone(src['fred']['last_ok'])
        self.assertEqual(doc['run_label'], 'morning')
        self.assertEqual(doc['data_date'], '2026-09-15')
        self.assertEqual(code, 0)

    def test_history_is_idempotent_and_only_live_prints_are_written(self):
        run(self.out, ['--backfill', '90'])
        p = os.path.join(self.out, 'commodities_history.csv')
        first = read(p)
        h = common.read_history(p)
        self.assertGreaterEqual(len(h['brent']), 60)
        self.assertNotIn('vix', h)
        self.assertNotIn('gold_inr_10g', h)              # derived rows are recomputed, never stored
        run(self.out, ['--backfill', '90'])
        self.assertEqual(read(p), first)
        run(self.out)                                      # today-only re-run changes nothing either
        self.assertEqual(read(p), first)

    def test_dead_feed_carries_last_value_forward_as_stale(self):
        run(self.out, ['--backfill', '30'])
        before = self.latest()
        brent_before = next(r for r in before['indicators'] if r['id'] == 'brent')
        code, log, _ = run(self.out, fetcher=StubFetcher(down=['query2.finance.yahoo.com', 'fred.stlouisfed.org']))
        after = self.latest()
        brent = next(r for r in after['indicators'] if r['id'] == 'brent')
        self.assertEqual(brent['status'], 'stale')
        self.assertTrue(brent['stale'])
        self.assertEqual(brent['last_good'], brent_before['date'])
        self.assertEqual(brent['value'], brent_before['value'], 'a good value is never overwritten with null')
        self.assertIn('field=brent source=carry-forward status=stale', log)
        self.assertGreaterEqual(after['counts']['stale'], 1)
        h = common.read_history(os.path.join(self.out, 'commodities_history.csv'))
        self.assertNotIn(dt.date(2026, 9, 16), h['brent'], 'a carried-forward value is not a new history row')

    def test_manual_override_wins(self):
        with open(os.path.join(self.out, 'manual_overrides.json'), 'w', encoding='utf-8') as fh:
            json.dump({'commodities': {'usdinr': {'value': 88.12, 'date': TODAY, 'note': 'FBIL reference rate'}}}, fh)
        _, log, _ = run(self.out)
        r = next(x for x in self.latest()['indicators'] if x['id'] == 'usdinr')
        self.assertEqual(r['status'], 'manual')
        self.assertEqual(r['value'], 88.12)
        self.assertEqual(r['note'], 'FBIL reference rate')
        self.assertIn('field=usdinr source=manual status=manual', log)

    def test_dry_run_writes_nothing(self):
        run(self.out, ['--dry-run'])
        self.assertEqual(sorted(os.listdir(self.out)), [])

    def test_config_stock_lists_are_uppercase_nse_codes(self):
        import commodities
        cfg = common.load_config()
        codes = commodities.stock_symbols(cfg)
        self.assertTrue(codes)
        for c in codes:
            self.assertRegex(c, r'^[A-Z0-9&-]{2,12}$')


if __name__ == '__main__':
    unittest.main()
