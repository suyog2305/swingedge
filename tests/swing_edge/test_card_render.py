"""Renders index.html in headless Chromium with a sample commodities_latest.json and checks the
card shows its freshness badge, stale / fallback / roll markers, regime tiles and sector strip,
in both themes and at phone width without horizontal page scroll.

Needs `pip install playwright` plus a Chromium (`python -m playwright install chromium`, or the
preinstalled one via PLAYWRIGHT_BROWSERS_PATH). Skips cleanly when neither is present."""
import datetime as dt, functools, http.server, json, os, socket, threading, unittest

from tests.swing_edge._paths import ROOT, FIXTURES

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional dependency
    sync_playwright = None


def preinstalled_chromium():
    import glob
    for pat in (os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', ''),
                os.path.join(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', ''), 'chromium_headless_shell-*', 'chrome-linux', 'headless_shell'),
                os.path.join(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', ''), 'chromium-*', 'chrome-linux', 'chrome')):
        hits = sorted(glob.glob(pat)) if pat else []
        if hits and os.access(hits[-1], os.X_OK):
            return hits[-1]
    return None


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # keep test output readable
        pass


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class CardRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        handler = functools.partial(Quiet, directory=ROOT)
        cls.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', cls.port), handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        with open(os.path.join(FIXTURES, 'commodities_latest.sample.json'), encoding='utf-8') as fh:
            cls.sample = json.load(fh)
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:  # pragma: no cover - try a preinstalled chromium of any revision
            exe = preinstalled_chromium()
            if not exe:
                cls.pw.stop()
                cls.httpd.shutdown()
                raise unittest.SkipTest(f'no chromium available: {str(e)[:80]}')
            cls.browser = cls.pw.chromium.launch(executable_path=exe)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()

    def open_card(self, doc, width=1200, theme=None):
        page = self.browser.new_page(viewport={'width': width, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        # keep the test hermetic: only the local server answers; CDN fonts/libs are dropped
        page.route('**/*', lambda route: route.continue_() if '127.0.0.1' in route.request.url else route.abort())
        page.route('**/data/swing_edge/commodities_latest.json',
                   lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(doc)))
        page.goto(f'http://127.0.0.1:{self.port}/index.html', wait_until='domcontentloaded')
        if theme:
            page.evaluate(f"document.documentElement.setAttribute('data-theme', '{theme}')")
        page.evaluate("show('comm')")
        page.wait_for_selector('#se-card-commodities .sepc-title', timeout=10000)
        return page, errors

    def fresh_doc(self, hours_ago):
        d = dict(self.sample)
        d['run_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        return d

    def test_markers_regimes_and_sectors(self):
        page, errors = self.open_card(self.fresh_doc(2))
        card = page.locator('#se-card-commodities')
        self.assertIn('Commodities & Macro', card.locator('.sepc-title').inner_text())
        self.assertGreaterEqual(card.locator('tr[data-indicator] .sepc-mark.stale').count(), 1)
        self.assertGreaterEqual(card.locator('tr[data-indicator] .sepc-mark.fallback').count(), 1)
        self.assertEqual(card.locator('tr[data-indicator] .sepc-mark.roll').count(), 1)
        self.assertEqual(card.locator('tr[data-indicator="brent"] .sepc-mark.roll').count(), 1)
        self.assertEqual(card.locator('.sepc-reg').count(), 6)
        self.assertEqual(card.locator('.sepc-sec').count(), 4)
        self.assertGreaterEqual(card.locator('svg.sepc-spark').count(), 5)
        self.assertIn('bg', card.locator('.sepc-fresh').get_attribute('class'))       # green: <= 24 h
        self.assertIn('last ok', card.locator('.sepc-foot').inner_text())
        self.assertFalse([e for e in errors if 'sepc' in e or 'SeCard' in e], errors)
        page.close()

    def test_freshness_badge_turns_amber_then_red(self):
        page, _ = self.open_card(self.fresh_doc(30))
        self.assertIn('ba', page.locator('.sepc-fresh').get_attribute('class'))
        page.close()
        page, _ = self.open_card(self.fresh_doc(72))
        self.assertIn('br', page.locator('.sepc-fresh').get_attribute('class'))
        page.close()

    def test_phone_width_has_no_horizontal_page_scroll_in_both_themes(self):
        for theme in ('ledger', 'lab'):
            page, _ = self.open_card(self.fresh_doc(1), width=390, theme=theme)
            page.evaluate("document.querySelector('.sidebar') && (document.querySelector('.sidebar').style.display='none')")
            scroll_w, inner_w = page.evaluate('[document.documentElement.scrollWidth, window.innerWidth]')
            self.assertLessEqual(scroll_w, inner_w, f'{theme}: page scrolls horizontally at 390px')
            colour = page.evaluate("getComputedStyle(document.querySelector('.sepc-title')).color")
            self.assertTrue(colour, 'title has a computed colour')
            page.close()

    def test_missing_file_shows_how_to_populate(self):
        page = self.browser.new_page()
        page.route('**/*', lambda route: route.continue_() if '127.0.0.1' in route.request.url else route.abort())
        page.route('**/data/swing_edge/commodities_latest.json', lambda route: route.fulfill(status=404, body='nope'))
        page.goto(f'http://127.0.0.1:{self.port}/index.html', wait_until='domcontentloaded')
        page.evaluate("show('comm')")
        page.wait_for_selector('#se-card-commodities .sepc-empty code', timeout=10000)
        self.assertIn('fetch_prices.py', page.locator('#se-card-commodities').inner_text())
        page.close()


if __name__ == '__main__':
    unittest.main()
