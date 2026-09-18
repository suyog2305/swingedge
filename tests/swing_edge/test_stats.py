import datetime as dt
import unittest

from tests.swing_edge._paths import fixture
import common


def daily(values, start=dt.date(2026, 6, 1)):
    out, d = [], start
    for v in values:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        out.append((d, float(v)))
        d += dt.timedelta(days=1)
    return out


class Changes(unittest.TestCase):
    def test_pct_changes_use_calendar_lookback(self):
        s = daily([100] * 60 + [110])
        st = common.series_stats(s)
        self.assertAlmostEqual(st['chg_1d'], 10.0)
        self.assertAlmostEqual(st['chg_7d'], 10.0)
        self.assertAlmostEqual(st['chg_30d'], 10.0)
        self.assertEqual(st['mode'], 'pct')

    def test_diff_mode_reports_points(self):
        s = daily([4.10] * 40 + [4.35])
        st = common.series_stats(s, mode='diff')
        self.assertAlmostEqual(st['chg_1d'], 0.25)
        self.assertAlmostEqual(st['chg_30d'], 0.25)

    def test_short_series_has_no_zscore(self):
        st = common.series_stats(daily([1, 2, 3]))
        self.assertIsNone(st['z90'])
        self.assertIsNone(st['chg_30d'])
        self.assertEqual(st['chg_1d'], 50.0)

    def test_zscore_and_sparkline_window(self):
        s = daily(list(range(1, 121)))
        st = common.series_stats(s)
        self.assertEqual(len(st['spark']), 90)
        self.assertGreater(st['z90'], 1.5)
        self.assertEqual(st['spark_from'], s[-90][0].isoformat())


class Roll(unittest.TestCase):
    def test_futures_jump_beyond_3_sigma_is_a_roll(self):
        s = common.parse_yahoo_chart(fixture('yahoo_BZ=F.json'))  # ends with a +9% day
        self.assertTrue(common.series_stats(s, is_futures=True)['roll'])
        self.assertFalse(common.series_stats(s, is_futures=False)['roll'])

    def test_ordinary_day_is_not_a_roll(self):
        s = common.parse_yahoo_chart(fixture('yahoo_BZ=F.json'))[:-1]
        self.assertFalse(common.series_stats(s, is_futures=True)['roll'])


class UnitConversions(unittest.TestCase):
    def test_usd_per_lb_to_usd_per_tonne(self):
        self.assertAlmostEqual(common.usd_lb_to_usd_t(4.5), 9920.80, places=1)
        self.assertIsNone(common.usd_lb_to_usd_t(None))

    def test_usd_per_oz_to_inr_per_10g(self):
        # 2400 $/oz at 83.5 INR/$: 2400*83.5/31.1034768*10 = 64,430.4
        self.assertAlmostEqual(common.usd_oz_to_inr_10g(2400, 83.5), 64430.4, places=0)
        self.assertIsNone(common.usd_oz_to_inr_10g(2400, None))

    def test_usd_per_oz_to_inr_per_kg(self):
        self.assertAlmostEqual(common.usd_oz_to_inr_kg(30, 83.5), 80537.9, places=0)


class History(unittest.TestCase):
    def test_roundtrip_and_upsert(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'h.csv')
            h = {'brent': {dt.date(2026, 9, 15): (67.31, 'fred:DCOILBRENTEU', 'fallback')}}
            common.write_history(p, h)
            h2 = common.read_history(p)
            h2['brent'][dt.date(2026, 9, 15)] = (68.0, 'yahoo:BZ=F', 'ok')  # same day, newer print
            common.write_history(p, h2)
            self.assertEqual(common.read_history(p)['brent'][dt.date(2026, 9, 15)], (68.0, 'yahoo:BZ=F', 'ok'))
            with open(p) as fh:
                self.assertEqual(len(fh.read().splitlines()), 2)  # header + one row, not two


if __name__ == '__main__':
    unittest.main()
