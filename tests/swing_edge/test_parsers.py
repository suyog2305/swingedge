import datetime as dt
import unittest

from tests.swing_edge._paths import fixture
import common


class YahooParser(unittest.TestCase):
    def test_parses_closes_and_skips_null(self):
        s = common.parse_yahoo_chart(fixture('yahoo_BZ=F.json'))
        self.assertEqual(len(s), 119)  # 120 prints, one null gap dropped
        self.assertEqual(s[-1][0], dt.date(2026, 9, 15))
        self.assertAlmostEqual(s[-1][1], 71.97)
        self.assertTrue(all(s[i][0] < s[i + 1][0] for i in range(len(s) - 1)))

    def test_error_document_raises(self):
        with self.assertRaises(ValueError):
            common.parse_yahoo_chart(fixture('yahoo_error.json'))


class FredParser(unittest.TestCase):
    def test_skips_dot_placeholders(self):
        s = common.parse_fred_csv(fixture('fred_DCOILBRENTEU.csv'))
        self.assertEqual([d.isoformat() for d, _ in s],
                         ['2026-09-08', '2026-09-10', '2026-09-11', '2026-09-14', '2026-09-15'])
        self.assertAlmostEqual(s[-1][1], 67.31)


class StooqParser(unittest.TestCase):
    def test_uses_close_column(self):
        s = common.parse_stooq_csv(fixture('stooq_xauusd.csv'))
        self.assertEqual(len(s), 4)
        self.assertEqual(s[-1], (dt.date(2026, 9, 15), 3671.9))

    def test_rejects_non_csv(self):
        with self.assertRaises(ValueError):
            common.parse_stooq_csv('<html>No data</html>')


class WestmetallParser(unittest.TestCase):
    def test_reads_dates_and_thousands_separators(self):
        s = common.parse_westmetall_table(fixture('westmetall_LME_Cu_cash.html'))
        self.assertEqual(s[0], (dt.date(2026, 9, 11), 9790.0))
        self.assertEqual(s[-1], (dt.date(2026, 9, 15), 9876.0))
        self.assertEqual(len(s), 3)  # the blank row is ignored

    def test_three_month_column(self):
        s = common.parse_westmetall_table(fixture('westmetall_LME_Cu_cash.html'), col=2)
        self.assertEqual(s[-1][1], 9900.5)

    def test_empty_table_raises(self):
        with self.assertRaises(ValueError):
            common.parse_westmetall_table('<table><tr><td>no data</td></tr></table>')


class Robots(unittest.TestCase):
    def test_only_star_agent_rules_apply(self):
        rules = common.parse_robots(fixture('robots_disallow_all.txt'))
        self.assertEqual(rules, ['/en/markdaten.php'])

    def test_fetcher_blocks_disallowed_path(self):
        f = common.Fetcher()
        f._robots['https://www.westmetall.com'] = ['/en/markdaten.php']
        self.assertFalse(f.robots_allowed('https://www.westmetall.com/en/markdaten.php?action=table'))
        self.assertTrue(f.robots_allowed('https://www.westmetall.com/en/other.php'))


if __name__ == '__main__':
    unittest.main()
