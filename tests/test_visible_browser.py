"""Tests for the visible Chromium browser mode.

The launch tests drive a REAL Chromium window, so they are skipped when
there is no display or Playwright is not installed -- and the skip says
which, rather than passing silently.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402
from source_file.indeed_client import (            # noqa: E402
    BrowserTransport,
    VirtualDisplay,
    BrowserUnavailable,
    DemoPageBuilder,
    IndeedAccessDenied,
    IndeedClient,
    IndeedClientConfig,
    _job_from_json_ld,
    _split_salary_and_type,
)

try:
    import playwright                              # noqa: F401
    PLAYWRIGHT_AVAILABLE = True
except ImportError:                                 # pragma: no cover
    PLAYWRIGHT_AVAILABLE = False

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

needs_browser = unittest.skipUnless(
    PLAYWRIGHT_AVAILABLE and HAS_DISPLAY,
    "needs Playwright (%s) and a display (%s)"
    % (PLAYWRIGHT_AVAILABLE, HAS_DISPLAY))


def browser_config(**overrides):
    """A config like the main script's, but OFFLINE, quiet and quick.

    ``live_enabled`` is False on purpose: running the test suite must never
    send a request to the live site. The live paths are covered by asserting
    the urls and guards they would use, not by crawling.
    """
    settings = dict(
        live_enabled=False,
        browser_visible=True,
        browser_headless=False,
        browser_slow_mo_ms=0,
        browser_width=1366,
        browser_height=768,
        request_gap_seconds=0,
        fixture_jobs_per_page=4,
        verbose=False,
    )
    settings.update(overrides)
    return IndeedClientConfig(**settings)


FIXTURE_JOBS = [
    {"job_key": "vb001", "title": "Network Support Engineer",
     "company_name": "Acme Infotech Pvt Ltd",
     "formatted_location": "Pune, Maharashtra",
     "employment_type": "Full-time",
     "salary_text": "₹4,00,000 - ₹6,00,000 a year",
     "description": "<h3>About</h3><p>Keep the LAN running.</p>"
                    "<ul><li>Cisco</li><li>Routing</li></ul>",
     "apply_url": "https://acme.example/careers/apply/77"},
    {"job_key": "vb002", "title": "NOC Analyst",
     "company_name": "Bluepeak Managed Services",
     "formatted_location": "Mumbai, Maharashtra",
     "employment_type": "Contract",
     "salary_text": "Not disclosed",
     "description": "<p>Monitor customer infrastructure.</p>",
     "indeed_apply_url": "https://in.indeed.com/applystart?jk=vb002"},
    {"job_key": "vb003", "title": "Linux System Administrator",
     "company_name": "Craftline Systems",
     "formatted_location": "Chennai, Tamil Nadu",
     "employment_type": "Full-time",
     "description": "<p>Patch and monitor Linux fleets.</p>"},
]


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------


class TestConfiguration(unittest.TestCase):
    """The browser settings live at the top of the main file, as required."""

    def test_the_required_settings_exist_with_the_requested_values(self):
        """The two switches are a requirement; the numbers are tunable.

        "visible, not headless" is what was asked for, so it is asserted
        exactly. The slow-motion delay and the window size are operator
        settings that get tuned in the field -- slow_mo was raised to 350ms
        after a 429 -- so they are checked for being sane, not for being a
        particular number. A test that pins a tunable breaks on a perfectly
        valid change to it.
        """
        self.assertIs(F.BROWSER_VISIBLE, True)
        self.assertIs(F.BROWSER_HEADLESS, False)

        self.assertIsInstance(F.BROWSER_SLOW_MO_MS, int)
        self.assertGreaterEqual(F.BROWSER_SLOW_MO_MS, 0)
        self.assertLess(F.BROWSER_SLOW_MO_MS, 5000,
                        "a slow_mo this large would make a run unusable")
        self.assertGreaterEqual(F.BROWSER_WIDTH, 800)
        self.assertGreaterEqual(F.BROWSER_HEIGHT, 600)

    def test_they_are_inside_the_configuration_block(self):
        with open(os.path.join(PROJECT_ROOT, "find_indeed_job.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        block = source[source.index("INDEED JOB SCRAPER CONFIGURATION"):
                       source.rindex("END OF CONFIGURATION")]
        for name in ("BROWSER_VISIBLE", "BROWSER_HEADLESS",
                     "BROWSER_SLOW_MO_MS", "BROWSER_WIDTH", "BROWSER_HEIGHT",
                     "BROWSER_REQUIRED", "BROWSER_NAV_TIMEOUT_MS",
                     "BROWSER_KEEP_OPEN_SECONDS", "BROWSER_CHANNEL"):
            self.assertIn("\n%s = " % name, block, name)

    def test_there_is_still_no_configuration_module(self):
        self.assertFalse(os.path.exists(
            os.path.join(PROJECT_ROOT, "indeed_config.py")))

    def test_the_settings_reach_the_client_config(self):
        config = F.build_client_config(verbose=False)
        self.assertTrue(config.browser_visible)
        self.assertFalse(config.browser_headless)
        self.assertEqual(config.browser_slow_mo_ms, F.BROWSER_SLOW_MO_MS)
        self.assertEqual(config.browser_width, F.BROWSER_WIDTH)
        self.assertEqual(config.browser_height, F.BROWSER_HEIGHT)

    def test_the_client_picks_the_browser_transport(self):
        client = IndeedClient(browser_config())
        self.assertEqual(client.transport.name, "browser")
        self.assertTrue(client.uses_browser)
        self.assertFalse(client.transport.started,
                         "constructing a client must not open a window")

    def test_turning_the_browser_off_restores_the_offline_client(self):
        client = IndeedClient(browser_config(browser_visible=False))
        self.assertEqual(client.transport.name, "fixture")

    def test_browser_launch_logic_lives_in_the_client_module(self):
        with open(os.path.join(PROJECT_ROOT, "find_indeed_job.py"),
                  encoding="utf-8") as handle:
            main_source = handle.read()
        self.assertNotIn("sync_playwright", main_source)
        self.assertNotIn("chromium.launch", main_source)
        with open(os.path.join(PROJECT_ROOT, "source_file",
                               "indeed_client.py"), encoding="utf-8") as handle:
            client_source = handle.read()
        self.assertIn("chromium.launch", client_source)


# ---------------------------------------------------------------------------
# AUTHORISATION
# ---------------------------------------------------------------------------


class TestCollectionMode(unittest.TestCase):
    """Live when the main file says so; a labelled demonstration otherwise.

    None of these tests opens a page: they assert which mode a configuration
    selects, so running the suite never sends a request to the live site.
    """

    def test_the_shipped_configuration_collects_live(self):
        config = F.build_client_config(verbose=False)
        self.assertTrue(config.live_enabled)
        self.assertTrue(config.browser_live_ready)
        self.assertTrue(config.site_search_ready)

    def test_turning_live_off_gives_a_labelled_demonstration(self):
        client = IndeedClient(browser_config())
        ok, message = client.preflight()
        self.assertTrue(ok)
        self.assertIn("DEMONSTRATION", message)
        self.assertTrue(client.is_demonstration)
        self.assertIn("LOCAL FIXTURE PAGES", client.describe())

    def test_live_mode_needs_a_url_template(self):
        config = browser_config(live_enabled=True, site_base_url="",
                                search_url_template="")
        self.assertFalse(config.browser_live_ready)

    def test_a_url_template_alone_does_not_start_collecting(self):
        config = browser_config(
            browser_live_search_url="https://example.invalid/j?q={keyword}")
        self.assertFalse(config.live_enabled)
        self.assertFalse(config.browser_live_ready,
                         "LIVE_COLLECTION_ENABLED is the switch")

    def test_an_environment_url_overrides_the_configured_template(self):
        config = browser_config(
            live_enabled=True,
            browser_live_search_url=("https://staging.example/j?q={keyword}"
                                     "&l={city}&start={start}"))
        self.assertTrue(config.browser_live_ready)
        self.assertEqual(config.live_search_url("NOC Engineer", "Pune", 2),
                         "https://staging.example/j?q=NOC+Engineer&l=Pune"
                         "&start=10")

    def test_the_urls_the_collector_builds_are_all_robots_allowed(self):
        config = F.build_client_config(verbose=False)
        for page in range(1, F.MAX_LISTING_PAGES + 1):
            url = config.live_search_url("Network Engineer", "Pune", page)
            self.assertEqual(config.robots_refuses(url), "", url)
        self.assertEqual(config.robots_refuses(config.live_job_url("abc")), "")

    def test_the_robots_guard_refuses_a_disallowed_path(self):
        config = F.build_client_config(verbose=False)
        transport = BrowserTransport(config)

        class FakePage:
            url = "about:blank"

        with self.assertRaises(IndeedAccessDenied) as caught:
            transport._goto(FakePage(),
                            config.site_base_url + "/applystart?jk=x",
                            "apply page")
        self.assertIn("robots", str(caught.exception))

    def test_a_tracking_redirect_never_becomes_a_jobs_identity(self):
        config = F.build_client_config(verbose=False)
        transport = BrowserTransport(config)

        class FakePage:
            url = config.site_base_url + "/jobs?q=a&l=b"

        canonical = config.live_job_url("KEY1")
        self.assertEqual(
            transport._job_page_url(FakePage(), "KEY1", "/rc/clk?jk=KEY1"),
            canonical)
        self.assertEqual(
            transport._source_url_for(FakePage(), "KEY1", "/rc/clk?jk=KEY1"),
            canonical)
        self.assertEqual(
            transport._job_page_url(FakePage(), "", "/rc/clk?jk=Z"), "",
            "a disallowed href with no key leaves nothing to open")

    def test_only_the_cookie_decline_control_is_ever_clicked(self):
        with open(os.path.join(PROJECT_ROOT, "source_file",
                               "indeed_client.py"), encoding="utf-8") as handle:
            code = "\n".join(line for line in handle.read().splitlines()
                             if not line.strip().startswith("#"))
        self.assertIn("reject-all-handler", code)
        self.assertNotIn("accept-btn-handler", code)

    def test_the_saved_session_file_is_loaded_not_created(self):
        """Where the session comes from, not whether one is switched on.

        USE_SAVED_LOGIN is a tunable -- it ships False, because a signed-in
        request to the live site is refused. What must hold either way is
        that this code only ever READS a session file the operator made
        themselves, and that the file is never committed.
        """
        config = F.build_client_config(verbose=False)
        self.assertEqual(config.auth_file,
                         os.path.join(PROJECT_ROOT, "indeed_auth.json"))
        with open(os.path.join(PROJECT_ROOT, ".gitignore"),
                  encoding="utf-8") as handle:
            ignored = handle.read()
        self.assertIn("indeed_auth.json", ignored)
        # Parking a session before signing out leaves a .bak file full of
        # live cookies in the project root; that must not be committed either.
        self.assertIn("_auth.json.bak.*", ignored)

        # No credential is ever read, typed or stored.
        with open(os.path.join(PROJECT_ROOT, "source_file",
                               "indeed_client.py"), encoding="utf-8") as handle:
            code = handle.read()
        for forbidden in ("password", "fill_password", "type_password"):
            self.assertNotIn(forbidden + "=", code)

    def test_a_missing_session_file_is_not_an_error(self):
        config = browser_config(auth_file="/nonexistent/indeed_auth.json")
        transport = BrowserTransport(config)
        self.assertFalse(getattr(transport, "using_saved_login", False))


# ---------------------------------------------------------------------------
# DEMONSTRATION PAGES
# ---------------------------------------------------------------------------


class TestDemoPages(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="indeed-demo-test-")
        self.builder = DemoPageBuilder(self.directory)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def read(self, url):
        with open(url[len("file://"):], encoding="utf-8") as handle:
            return handle.read()

    def test_a_search_page_is_written_with_one_card_per_job(self):
        url = self.builder.write_search_page("Network Engineer", "Pune", 1,
                                             FIXTURE_JOBS, len(FIXTURE_JOBS))
        html = self.read(url)
        self.assertEqual(html.count('class="job_seen_beacon"'), 3)
        for job in FIXTURE_JOBS:
            self.assertIn('data-jk="%s"' % job["job_key"], html)

    def test_every_page_is_labelled_as_a_demonstration(self):
        url = self.builder.write_search_page("Network Engineer", "Pune", 1,
                                             FIXTURE_JOBS, 3)
        html = self.read(url)
        self.assertIn("DEMONSTRATION", html)
        self.assertIn("NOT Indeed", html)
        self.assertIn('data-demo="true"', html)

    def test_job_pages_carry_the_apply_destination_that_was_given(self):
        external = self.read(self.builder.write_detail_page(FIXTURE_JOBS[0]))
        self.assertIn("https://acme.example/careers/apply/77", external)
        hosted = self.read(self.builder.write_detail_page(FIXTURE_JOBS[1]))
        self.assertIn("/applystart?jk=vb002", hosted)
        none = self.read(self.builder.write_detail_page(FIXTURE_JOBS[2]))
        self.assertIn("applyNotAvailable", none)

    def test_pages_are_written_outside_the_project(self):
        url = self.builder.write_detail_page(FIXTURE_JOBS[0])
        path = url[len("file://"):]
        self.assertFalse(os.path.abspath(path).startswith(PROJECT_ROOT),
                         "demonstration pages must not be written into the "
                         "project directory")

    def test_cleanup_removes_them(self):
        self.builder.write_detail_page(FIXTURE_JOBS[0])
        self.builder.cleanup()
        self.assertFalse(os.path.exists(self.directory))


# ---------------------------------------------------------------------------
# JSON-LD READER (no browser needed)
# ---------------------------------------------------------------------------


class TestJsonLdReader(unittest.TestCase):

    def test_a_job_posting_node_becomes_a_raw_job(self):
        raw = _job_from_json_ld({
            "@type": "JobPosting",
            "title": "Cloud Support Engineer",
            "hiringOrganization": {"name": "Orbit Cloud",
                                   "sameAs": "https://orbit.example"},
            "jobLocation": {"address": {"addressLocality": "Hyderabad",
                                        "addressRegion": "Telangana"}},
            "employmentType": "FULL_TIME",
            "datePosted": "2026-10-01",
            "validThrough": "2026-11-30",
            "description": "<p>Run the cloud.</p>",
            "identifier": {"value": "ld123"},
            "url": "https://jobs.example/viewjob?jk=ld123",
        }, is_demo=False)

        self.assertEqual(raw["title"], "Cloud Support Engineer")
        self.assertEqual(raw["company_name"], "Orbit Cloud")
        self.assertEqual(raw["formatted_location"], "Hyderabad, Telangana")
        self.assertEqual(raw["city"], "Hyderabad")
        self.assertEqual(raw["employment_type"], "FULL_TIME")
        self.assertEqual(raw["job_key"], "ld123")
        self.assertFalse(raw["fixture"])
        self.assertEqual(raw["collected_via"], "browser")


# ---------------------------------------------------------------------------
# THE REAL BROWSER
# ---------------------------------------------------------------------------


@needs_browser
class TestVisibleChromium(unittest.TestCase):
    """Launches Chromium for real, reads a rendered page, closes it."""

    @classmethod
    def setUpClass(cls):
        cls.transport = BrowserTransport(browser_config())
        cls.transport.start()

    @classmethod
    def tearDownClass(cls):
        cls.transport.stop()
        cls.transport.cleanup_pages()

    def test_playwright_imports(self):
        from playwright.sync_api import sync_playwright
        self.assertTrue(callable(sync_playwright))

    def test_chromium_launched_and_is_connected(self):
        self.assertTrue(self.transport.started)
        self.assertTrue(self.transport.browser.is_connected())

    def test_the_window_is_headed_not_headless(self):
        agent = self.transport.page.evaluate("navigator.userAgent")
        self.assertNotIn("Headless", agent,
                         "a headless Chromium reports itself in the UA")

    def test_the_window_uses_the_configured_size_on_the_session_display(self):
        self.assertEqual(self.transport.page.evaluate("window.innerWidth"),
                         1366)
        self.assertGreaterEqual(self.transport.page.evaluate("window.outerWidth"),
                                1366)
        self.assertTrue(os.environ.get("DISPLAY")
                        or os.environ.get("WAYLAND_DISPLAY"))

    def test_it_can_open_a_page_and_read_it(self):
        self.transport.page.goto(
            "data:text/html,<h1 id=probe>hello from the test</h1>")
        self.assertEqual(
            self.transport.page.locator("#probe").inner_text(),
            "hello from the test")

    def test_the_demonstration_notice_is_shown_before_any_collection(self):
        welcome = os.path.join(self.transport.pages.directory, "_welcome.html")
        self.assertTrue(os.path.exists(welcome))
        with open(welcome, encoding="utf-8") as handle:
            self.assertIn("not authorised", handle.read())

    def test_a_second_start_does_not_open_a_second_browser(self):
        browser = self.transport.browser
        self.transport.start()
        self.assertIs(self.transport.browser, browser)


@needs_browser
class TestBrowserCollection(unittest.TestCase):
    """The watched workflow: search page -> cards -> job page -> fields."""

    @classmethod
    def setUpClass(cls):
        cls.transport = BrowserTransport(browser_config())
        cls.transport.start()
        cls.config = cls.transport.config
        # A fixed fixture set, so the assertions are about extraction rather
        # than about which random jobs the generator produced.
        cls.transport.pages.write_search_page("Network Engineer", "Pune", 1,
                                              FIXTURE_JOBS, len(FIXTURE_JOBS))
        url = cls.transport.pages.search_page_url("Network Engineer", "Pune", 1)
        cls.transport._goto(cls.transport.page, url, "test search")
        cls.cards = cls.transport._read_listing_cards(cls.transport.page)

    @classmethod
    def tearDownClass(cls):
        cls.transport.stop()
        cls.transport.cleanup_pages()

    def test_the_browser_displays_the_job_listings(self):
        self.assertIn("job_seen_beacon",
                      self.transport.page.content())
        self.assertEqual(len(self.cards), 3)

    def test_listing_fields_are_read_from_the_rendered_dom(self):
        first = self.cards[0]
        self.assertEqual(first["job_key"], "vb001")
        self.assertEqual(first["title"], "Network Support Engineer")
        self.assertEqual(first["company_name"], "Acme Infotech Pvt Ltd")
        self.assertIn("Pune", first["formatted_location"])
        self.assertIn("4,00,000", first["salary_text"])

    def test_listing_rows_are_marked_as_fixture_data(self):
        for card in self.cards:
            self.assertTrue(card["fixture"])
            self.assertEqual(card["collected_via"], "browser")
            self.assertTrue(card["source_url"].startswith(
                "https://demo.fixture.invalid/"),
                "a demonstration row must not claim an indeed.com url")

    def test_job_details_are_extracted_from_the_job_page(self):
        detail = self.transport.detail(self.cards[0], self.config)
        self.assertEqual(detail["title"], "Network Support Engineer")
        self.assertEqual(detail["company_name"], "Acme Infotech Pvt Ltd")
        self.assertIn("Keep the LAN running", detail["description"])
        self.assertIn("<li>", detail["description"],
                      "the description keeps its structure")

    def test_an_external_apply_destination_is_read_from_the_button(self):
        detail = self.transport.detail(self.cards[0], self.config)
        self.assertEqual(detail["apply_url"],
                         "https://acme.example/careers/apply/77")

    def test_an_indeed_hosted_apply_destination_is_read(self):
        detail = self.transport.detail(self.cards[1], self.config)
        self.assertEqual(detail.get("indeed_apply_url"),
                         "https://in.indeed.com/applystart?jk=vb002")
        self.assertFalse(detail.get("apply_url"))

    def test_a_job_with_no_apply_button_yields_no_apply_url(self):
        detail = self.transport.detail(self.cards[2], self.config)
        self.assertFalse(detail.get("apply_url"))
        self.assertFalse(detail.get("indeed_apply_url"))

    def test_the_full_row_is_built_from_browser_data(self):
        from source_file.indeed_extractor import (DETAIL_COLUMNS,
                                                  extract_job_details,
                                                  validate_row)
        detail = self.transport.detail(self.cards[0], self.config)
        row = extract_job_details(detail,
                                  {"role_category": "IT Department",
                                   "city": "Pune", "location": "Pune"},
                                  state_resolver=F.city_state_name)
        ok, reason = validate_row(row)
        self.assertTrue(ok, reason)
        self.assertEqual([column for column in DETAIL_COLUMNS
                          if column not in row], [])
        self.assertEqual(row["source_platform"], "Indeed")
        self.assertEqual(row["city"], "Pune")
        self.assertEqual(row["salary_min"], 400000)
        self.assertEqual(row["apply_url"],
                         "https://acme.example/careers/apply/77")
        self.assertNotEqual(row["apply_url"], row["source_url"])

    def test_a_challenge_page_is_reported_and_never_solved(self):
        page = self.transport.pages
        path = os.path.join(page.directory, "challenge.html")
        page._write(path, "Checkpoint",
                    "<div class='wrap'><h1>Additional Verification "
                    "Required</h1><p>Please verify you are human to "
                    "continue.</p></div>")
        with self.assertRaises(IndeedAccessDenied) as caught:
            self.transport._goto(self.transport.detail_page,
                                 "file://" + path, "challenge probe")
        self.assertIn("does not solve", str(caught.exception))

    def test_an_empty_result_page_is_not_mistaken_for_a_block(self):
        url = self.transport.pages.write_search_page("Nothing Here", "Pune",
                                                     1, [], 0)
        self.transport._goto(self.transport.detail_page, url, "empty probe")
        self.assertEqual(
            self.transport._read_listing_cards(self.transport.detail_page), [])


@needs_browser
class TestBrowserLifecycle(unittest.TestCase):

    def test_open_and_close_through_the_client(self):
        client = IndeedClient(browser_config())
        client.open()
        try:
            self.assertTrue(client.transport.started)
            self.assertTrue(client.transport.browser.is_connected())
        finally:
            client.close()
        self.assertFalse(client.transport.started)
        self.assertIsNone(client.transport.browser)

    def test_the_client_works_as_a_context_manager(self):
        with IndeedClient(browser_config()) as client:
            self.assertTrue(client.transport.started)
        self.assertFalse(client.transport.started)

    def test_close_is_safe_to_call_twice_and_before_opening(self):
        client = IndeedClient(browser_config())
        client.close()
        client.open()
        client.close()
        client.close()
        self.assertFalse(client.transport.started)

    def test_cleanup_removes_the_demonstration_pages(self):
        client = IndeedClient(browser_config())
        client.open()
        directory = client.transport.pages.directory
        self.assertTrue(os.path.exists(directory))
        client.cleanup()
        self.assertFalse(os.path.exists(directory))

    def test_a_real_search_through_the_client_returns_jobs(self):
        client = IndeedClient(browser_config())
        try:
            client.open()
            jobs, total = client.search_jobs("Desktop Support Engineer",
                                             "Delhi", 1)
            self.assertIsInstance(jobs, list)
            self.assertIsInstance(total, int)
            for job in jobs:
                self.assertTrue(job["fixture"])
        finally:
            client.cleanup()


@needs_browser
class TestPageReadingCost(unittest.TestCase):
    """A page must cost a FIXED, small number of browser round trips.

    The first implementation walked every selector candidate of every field
    with its own Playwright call: ~19 round trips per listing card and ~24
    for one job page. With slow_mo at 300ms that alone put ~90s on a 16-card
    listing page. These tests pin the cost down so it cannot creep back.
    """

    @classmethod
    def setUpClass(cls):
        cls.transport = BrowserTransport(browser_config())
        cls.transport.start()
        cls.config = cls.transport.config
        cls.transport.pages.write_search_page("Network Engineer", "Pune", 1,
                                              FIXTURE_JOBS, len(FIXTURE_JOBS))

    @classmethod
    def tearDownClass(cls):
        cls.transport.stop()
        cls.transport.cleanup_pages()

    def count_calls(self, action):
        """Run `action` and count the Playwright calls it makes."""
        from playwright.sync_api import Locator, Page

        calls = []
        patched = []

        def wrap(cls, name):
            original = getattr(cls, name, None)
            if original is None:
                return

            def counted(self, *args, **kwargs):
                calls.append("%s.%s" % (cls.__name__, name))
                return original(self, *args, **kwargs)

            setattr(cls, name, counted)
            patched.append((cls, name, original))

        for name in ("count", "inner_text", "inner_html", "get_attribute",
                     "text_content", "is_visible"):
            wrap(Locator, name)
        for name in ("evaluate", "inner_text", "content", "goto",
                     "wait_for_selector"):
            wrap(Page, name)
        try:
            result = action()
        finally:
            for cls, name, original in patched:
                setattr(cls, name, original)
        return result, calls

    def open_listing(self):
        url = self.transport.pages.search_page_url("Network Engineer",
                                                   "Pune", 1)
        self.transport._goto(self.transport.page, url, "cost test")

    def test_a_listing_page_costs_one_evaluate_whatever_the_card_count(self):
        self.open_listing()
        jobs, calls = self.count_calls(
            lambda: self.transport._read_listing_cards(self.transport.page))
        self.assertEqual(len(jobs), len(FIXTURE_JOBS))
        evaluates = [call for call in calls if call == "Page.evaluate"]
        self.assertLessEqual(len(evaluates), 2,
                             "one evaluate for JSON-LD, one for the cards")
        self.assertEqual([call for call in calls
                          if call.startswith("Locator.")], [],
                         "no per-element reads: %s" % calls)

    def test_a_job_page_costs_one_evaluate(self):
        self.open_listing()
        cards = self.transport._read_listing_cards(self.transport.page)
        self.transport._goto(self.transport.detail_page,
                             cards[0]["detail_page_url"], "cost test")
        _fields, calls = self.count_calls(
            lambda: self.transport._read_detail(self.transport.detail_page))
        self.assertLessEqual(len([c for c in calls if c == "Page.evaluate"]), 2)
        self.assertEqual([c for c in calls if c.startswith("Locator.")], [],
                         "no per-element reads: %s" % calls)

    def test_the_whole_job_read_stays_in_single_figures(self):
        """goto + render wait + challenge probe + JSON-LD + fields + cookies."""
        self.open_listing()
        cards = self.transport._read_listing_cards(self.transport.page)

        def read_one_job():
            return self.transport.detail(cards[1], self.config)

        detail, calls = self.count_calls(read_one_job)
        self.assertTrue(detail.get("title"))
        self.assertLessEqual(len(calls), 10,
                             "a job page should cost ~6 round trips, got "
                             "%d: %s" % (len(calls), calls))


@needs_browser
class TestRenderWaitsAreBounded(unittest.TestCase):
    """A page the collector cannot read must not hold up the run for minutes.

    Waiting for each selector candidate in turn cost
    len(selectors) x timeout -- 60s for the listing selectors and 84s for the
    job-page selectors, which is what made one job take one to two minutes.
    The candidates are now joined into a single wait.
    """

    @classmethod
    def setUpClass(cls):
        cls.transport = BrowserTransport(browser_config())
        cls.transport.start()
        cls.tmp = tempfile.mkdtemp(prefix="indeed-bare-page-")
        cls.bare = os.path.join(cls.tmp, "bare.html")
        with open(cls.bare, "w", encoding="utf-8") as handle:
            handle.write("<html><body><p>nothing familiar</p></body></html>")

    @classmethod
    def tearDownClass(cls):
        cls.transport.stop()
        cls.transport.cleanup_pages()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_candidates_are_joined_into_one_selector(self):
        joined = ", ".join(self.transport._CARD_SELECTORS)
        seen = {}

        def record(selector, **kwargs):
            seen["selector"] = selector
            seen["timeout"] = kwargs.get("timeout")
            raise RuntimeError("timed out")

        page = type("FakePage", (), {"wait_for_selector": staticmethod(record)})()
        self.assertFalse(self.transport._wait_for_any(
            page, self.transport._CARD_SELECTORS, 1234))
        self.assertEqual(seen["selector"], joined, "one joined selector")
        self.assertEqual(seen["timeout"], 1234)

    def test_playwright_specific_selectors_are_left_out_of_the_join(self):
        seen = {}

        def record(selector, **kwargs):
            seen["selector"] = selector
            return None

        page = type("FakePage", (), {"wait_for_selector": staticmethod(record)})()
        self.transport._wait_for_any(
            page, ("div.card", "button:has-text('Reject All')"), 100)
        self.assertEqual(seen["selector"], "div.card")

    def test_an_unreadable_listing_page_costs_one_timeout(self):
        self.transport.page.goto("file://" + self.bare)
        budget = self.transport.config.listing_render_timeout_ms / 1000.0
        started = time.monotonic()
        self.transport._wait_for_cards(self.transport.page)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, budget + 4,
                        "%.1fs for a %.0fs budget -- the waits are serial "
                        "again" % (elapsed, budget))

    def test_an_unreadable_job_page_costs_one_timeout(self):
        self.transport.detail_page.goto("file://" + self.bare)
        budget = self.transport.config.detail_render_timeout_ms / 1000.0
        started = time.monotonic()
        self.transport._wait_for_detail(self.transport.detail_page)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, budget + 2,
                        "%.1fs for a %.0fs budget -- the waits are serial "
                        "again" % (elapsed, budget))


class TestVirtualDisplayConfiguration(unittest.TestCase):
    """The Xvfb settings live at the top of the main file, like the rest."""

    def test_the_settings_exist_and_are_sane(self):
        self.assertIn(F.XVFB_MODE, ("auto", "always", "never"))
        self.assertIsInstance(F.XVFB_DISPLAY, str)
        self.assertGreaterEqual(F.XVFB_COLOUR_DEPTH, 8)

    def test_they_are_inside_the_configuration_block(self):
        with open(os.path.join(PROJECT_ROOT, "find_indeed_job.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        block = source[source.index("INDEED JOB SCRAPER CONFIGURATION"):
                       source.rindex("END OF CONFIGURATION")]
        for name in ("XVFB_MODE", "XVFB_DISPLAY", "XVFB_COLOUR_DEPTH"):
            self.assertIn("\n%s = " % name, block, name)

    def test_they_reach_the_client_config(self):
        config = F.build_client_config(verbose=False)
        self.assertEqual(config.xvfb_mode, F.XVFB_MODE)
        self.assertEqual(config.xvfb_display, F.XVFB_DISPLAY)
        self.assertEqual(config.xvfb_colour_depth, F.XVFB_COLOUR_DEPTH)

    def test_the_screen_matches_the_browser_window(self):
        """Xvfb's framebuffer must be the size the browser asks for."""
        config = F.build_client_config(verbose=False)
        self.assertEqual("%dx%dx%d" % (config.browser_width,
                                       config.browser_height,
                                       config.xvfb_colour_depth),
                         "%dx%dx%d" % (F.BROWSER_WIDTH, F.BROWSER_HEIGHT,
                                       F.XVFB_COLOUR_DEPTH))


class TestVirtualDisplay(unittest.TestCase):
    """Xvfb lifecycle. Skipped when Xvfb is not installed."""

    def setUp(self):
        self.saved = {name: os.environ.get(name)
                      for name in ("DISPLAY", "WAYLAND_DISPLAY")}

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def strip_display(self):
        os.environ.pop("DISPLAY", None)
        os.environ.pop("WAYLAND_DISPLAY", None)

    def test_a_real_display_is_detected(self):
        os.environ["DISPLAY"] = ":0"
        self.assertTrue(VirtualDisplay.display_available())
        self.strip_display()
        self.assertFalse(VirtualDisplay.display_available())

    def test_a_wayland_session_counts_as_a_display(self):
        self.strip_display()
        os.environ["WAYLAND_DISPLAY"] = "wayland-0"
        self.assertTrue(VirtualDisplay.display_available())

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    def test_it_starts_sets_display_and_stops(self):
        self.strip_display()
        display = VirtualDisplay(screen="800x600x24", verbose=False)
        display.start()
        try:
            self.assertTrue(display.display.startswith(":"))
            self.assertEqual(os.environ["DISPLAY"], display.display)
            self.assertIsNone(display.process.poll(), "Xvfb died")
            self.assertTrue(os.path.exists(
                "/tmp/.X11-unix/X%s" % display.display.lstrip(":")))
        finally:
            socket = "/tmp/.X11-unix/X%s" % display.display.lstrip(":")
            display.stop()
        self.assertIsNone(display.process)
        self.assertNotIn("DISPLAY", os.environ, "DISPLAY must be restored")
        self.assertFalse(os.path.exists(socket), "the socket must be gone")

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    def test_it_restores_a_pre_existing_display(self):
        os.environ["DISPLAY"] = ":0"
        display = VirtualDisplay(verbose=False)
        display.start()
        self.assertNotEqual(os.environ["DISPLAY"], ":0")
        display.stop()
        self.assertEqual(os.environ["DISPLAY"], ":0")

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    def test_two_displays_do_not_collide(self):
        """Two ranks on one laptop must not grab the same display number."""
        self.strip_display()
        first = VirtualDisplay(verbose=False)
        second = VirtualDisplay(verbose=False)
        first.start()
        try:
            second.start()
            try:
                self.assertNotEqual(first.display, second.display)
            finally:
                second.stop()
        finally:
            first.stop()

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    def test_a_requested_display_number_is_honoured(self):
        self.strip_display()
        display = VirtualDisplay(display="137", verbose=False)
        if os.path.exists("/tmp/.X11-unix/X137"):
            self.skipTest(":137 is already in use on this machine")
        display.start()
        try:
            self.assertEqual(display.display, ":137")
        finally:
            display.stop()

    def test_stop_is_safe_before_start_and_twice(self):
        display = VirtualDisplay(verbose=False)
        display.stop()
        display.stop()
        self.assertIsNone(display.process)

    def test_a_missing_xvfb_is_a_clear_error(self):
        display = VirtualDisplay(binary="definitely-not-xvfb", verbose=False)
        self.assertFalse(display.is_installed())
        with self.assertRaises(BrowserUnavailable) as caught:
            display.start()
        message = str(caught.exception)
        self.assertIn("apt-get install", message)
        self.assertIn("BROWSER_HEADLESS", message)


@needs_browser
class TestHeadedBrowserWithoutADesktop(unittest.TestCase):
    """The fleet case: a headed browser on a laptop with no desktop session."""

    def setUp(self):
        self.saved = {name: os.environ.get(name)
                      for name in ("DISPLAY", "WAYLAND_DISPLAY")}
        os.environ.pop("DISPLAY", None)
        os.environ.pop("WAYLAND_DISPLAY", None)
        self.transport = None

    def tearDown(self):
        if self.transport is not None:
            self.transport.stop()
            self.transport.cleanup_pages()
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    def test_a_headed_browser_starts_on_a_virtual_display(self):
        self.transport = BrowserTransport(browser_config(xvfb_mode="auto"))
        self.transport.start()

        self.assertIsNotNone(self.transport._display,
                             "a virtual display should have been started")
        self.assertEqual(os.environ["DISPLAY"],
                         self.transport._display.display)
        self.assertTrue(self.transport.browser.is_connected())

        agent = self.transport.page.evaluate("navigator.userAgent")
        self.assertNotIn("Headless", agent,
                         "it must still be a HEADED browser, just unwatched")
        self.assertEqual(self.transport.page.evaluate("screen.width"),
                         self.transport.config.browser_width)

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    def test_a_page_can_be_read_through_the_virtual_display(self):
        self.transport = BrowserTransport(browser_config(xvfb_mode="auto"))
        self.transport.start()
        payload = self.transport.search("Network Engineer", "Pune", 1,
                                        self.transport.config)
        self.assertTrue(payload["jobs"], "no cards read on a virtual display")

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    def test_stopping_releases_the_virtual_display(self):
        transport = BrowserTransport(browser_config(xvfb_mode="auto"))
        transport.start()
        display = transport._display.display
        transport.stop()
        transport.cleanup_pages()
        self.assertIsNone(transport._display)
        self.assertFalse(os.path.exists("/tmp/.X11-unix/X%s"
                                        % display.lstrip(":")))
        self.assertNotIn("DISPLAY", os.environ)

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    def test_an_immediate_kill_also_releases_it(self):
        transport = BrowserTransport(browser_config(xvfb_mode="auto"))
        transport.start()
        display = transport._display.display
        transport.kill()
        self.assertIsNone(transport._display)
        self.assertFalse(os.path.exists("/tmp/.X11-unix/X%s"
                                        % display.lstrip(":")))

    def test_never_mode_refuses_instead_of_starting_one(self):
        self.transport = BrowserTransport(browser_config(xvfb_mode="never"))
        with self.assertRaises(BrowserUnavailable) as caught:
            self.transport.start()
        self.transport = None
        message = str(caught.exception)
        self.assertIn("XVFB_MODE", message)
        self.assertIn("BROWSER_HEADLESS", message)

    def test_headless_mode_needs_no_display_at_all(self):
        self.transport = BrowserTransport(
            browser_config(browser_headless=True, xvfb_mode="never"))
        self.transport.start()
        self.assertIsNone(self.transport._display,
                          "headless must not start an X server")
        self.assertTrue(self.transport.browser.is_connected())


@needs_browser
class TestBrowserSelfHealing(unittest.TestCase):
    """A browser that dies under the collector must be relaunched.

    Chromium can be killed by the kernel under memory pressure or simply
    crash. The `started` flag stayed True, so every later page load failed
    with "Target page, context or browser has been closed" and each failure
    was filed as a retryable page -- one crash turned into a cycle that
    collected nothing.
    """

    def setUp(self):
        self.transport = BrowserTransport(browser_config())
        self.transport.start()

    def tearDown(self):
        self.transport.stop()
        self.transport.cleanup_pages()

    def test_a_live_browser_reports_itself_alive(self):
        self.assertTrue(self.transport.is_alive())

    def test_an_unstarted_transport_is_not_alive(self):
        fresh = BrowserTransport(browser_config())
        self.assertFalse(fresh.is_alive())

    def test_a_killed_browser_is_detected(self):
        self.transport.browser.close()
        self.assertFalse(self.transport.is_alive(),
                         "a closed browser must not report itself alive")

    def test_the_next_request_relaunches_a_dead_browser(self):
        self.transport.browser.close()
        self.assertFalse(self.transport.is_alive())

        # The next search must succeed rather than raise TargetClosedError.
        payload = self.transport.search("Network Engineer", "Pune", 1,
                                        self.config_of(self.transport))
        self.assertTrue(self.transport.is_alive(), "it was not relaunched")
        self.assertIsInstance(payload.get("jobs"), list)

    def test_the_relaunched_browser_has_working_pages(self):
        self.transport.browser.close()
        self.transport._require_started()
        self.assertTrue(self.transport.is_alive())
        self.transport.page.goto("data:text/html,<h1 id=p>back</h1>")
        self.assertEqual(self.transport.page.locator("#p").inner_text(),
                         "back")

    @staticmethod
    def config_of(transport):
        return transport.config


@needs_browser
class TestFastAndSlowReadersAgree(unittest.TestCase):
    """The in-page reader and the per-element fallback must agree.

    The fallback exists for the case where evaluate is unavailable, so it has
    to produce the same row -- otherwise the data would quietly depend on
    which path ran.
    """

    @classmethod
    def setUpClass(cls):
        cls.transport = BrowserTransport(browser_config())
        cls.transport.start()
        cls.config = cls.transport.config
        cls.transport.pages.write_search_page("Network Engineer", "Pune", 1,
                                              FIXTURE_JOBS, len(FIXTURE_JOBS))
        url = cls.transport.pages.search_page_url("Network Engineer", "Pune", 1)
        cls.transport._goto(cls.transport.page, url, "agreement test")

    @classmethod
    def tearDownClass(cls):
        cls.transport.stop()
        cls.transport.cleanup_pages()

    def test_the_two_listing_readers_produce_the_same_cards(self):
        fast = self.transport._read_listing_cards_fast(self.transport.page)
        slow = self.transport._read_listing_cards_via_locators(
            self.transport.page)
        self.assertEqual(len(fast), len(slow))
        for quick, careful in zip(fast, slow):
            for field in ("job_key", "title", "company_name",
                          "formatted_location", "salary_text", "source_url",
                          "detail_page_url"):
                self.assertEqual(quick[field], careful[field], field)

    def test_the_two_detail_readers_produce_the_same_fields(self):
        cards = self.transport._read_listing_cards(self.transport.page)
        self.transport._goto(self.transport.detail_page,
                             cards[0]["detail_page_url"], "agreement test")
        fast = self.transport._read_detail(self.transport.detail_page)
        slow = self.transport._read_detail_via_locators(
            self.transport.detail_page)
        for field in ("title", "company_name", "formatted_location",
                      "salary_text", "employment_type", "apply_url"):
            self.assertEqual(fast.get(field, ""), slow.get(field, ""), field)
        self.assertEqual(fast.get("description", "").strip(),
                         slow.get("description", "").strip())

    def test_the_fallback_runs_when_evaluate_is_unavailable(self):
        cards = self.transport._read_listing_cards(self.transport.page)
        self.transport._goto(self.transport.detail_page,
                             cards[0]["detail_page_url"], "fallback test")

        original = self.transport.detail_page.evaluate

        def refuse(*_args, **_kwargs):
            raise RuntimeError("evaluate is blocked")

        self.transport.detail_page.evaluate = refuse
        try:
            detail = self.transport._read_detail(self.transport.detail_page)
        finally:
            self.transport.detail_page.evaluate = original

        self.assertEqual(detail["title"], "Network Support Engineer")
        self.assertEqual(detail["apply_url"],
                         "https://acme.example/careers/apply/77")


@needs_browser
class TestConsentBannerProbe(unittest.TestCase):
    """One round trip to find the banner, and only decline controls."""

    @classmethod
    def setUpClass(cls):
        cls.transport = BrowserTransport(browser_config())
        cls.transport.start()
        cls.tmp = tempfile.mkdtemp(prefix="indeed-consent-")

    @classmethod
    def tearDownClass(cls):
        cls.transport.stop()
        cls.transport.cleanup_pages()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def load(self, body):
        path = os.path.join(self.tmp, "consent.html")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>%s</body></html>" % body)
        self.transport.page.goto("file://" + path)

    def tagged(self):
        from source_file.indeed_client import _JS_TAG_REJECT_BUTTON
        return self.transport.page.evaluate(_JS_TAG_REJECT_BUTTON)

    def test_the_onetrust_decline_button_is_found(self):
        self.load('<button id="onetrust-reject-all-handler">Reject All</button>')
        self.assertTrue(self.tagged())

    def test_a_decline_button_is_found_by_its_label(self):
        self.load("<button>Reject all</button>")
        self.assertTrue(self.tagged())

    def test_an_accept_button_is_never_tagged(self):
        self.load('<button id="onetrust-accept-btn-handler">Accept All</button>'
                  "<button>Accept all cookies</button>")
        self.assertFalse(self.tagged(),
                         "only decline controls may ever be clicked")

    def test_a_page_with_no_banner_is_cheap_and_silent(self):
        self.load("<p>no banner here</p>")
        self.assertFalse(self.tagged())

    def test_the_accept_path_does_not_exist_in_the_source(self):
        with open(os.path.join(PROJECT_ROOT, "source_file",
                               "indeed_client.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("accept-btn-handler", source)
        self.assertNotIn("Accept All", source)


class TestSalaryAndTypeChip(unittest.TestCase):
    """A job page states pay and job type in one element; they are split."""

    def test_a_range_and_a_job_type(self):
        self.assertEqual(
            _split_salary_and_type("₹4,00,000 - ₹6,00,000 a year - Full-time"),
            ("₹4,00,000 - ₹6,00,000 a year", "Full-time"))

    def test_a_single_figure(self):
        self.assertEqual(_split_salary_and_type("₹35,000 a month"),
                         ("₹35,000 a month", ""))

    def test_a_job_type_on_its_own(self):
        self.assertEqual(_split_salary_and_type("Full-time"), ("", "Full-time"))

    def test_collapsed_whitespace_still_splits(self):
        salary, kind = _split_salary_and_type(
            "₹4,00,000 - ₹6,00,000 a year Full-time Day shift")
        self.assertEqual(salary, "₹4,00,000 - ₹6,00,000 a year")
        self.assertIn("Full-time", kind)

    def test_an_undisclosed_salary_yields_no_figure(self):
        self.assertEqual(_split_salary_and_type("Not disclosed - Contract"),
                         ("", "Contract"))

    def test_lakhs_survive_the_split(self):
        salary, kind = _split_salary_and_type("8 - 14 Lakhs P.A. - Permanent")
        self.assertEqual(kind, "Permanent")
        self.assertIn("14 Lakhs", salary)

    def test_empty_input(self):
        self.assertEqual(_split_salary_and_type(""), ("", ""))
        self.assertEqual(_split_salary_and_type(None), ("", ""))

    def test_the_split_feeds_the_salary_parser_correctly(self):
        from source_file.indeed_extractor import parse_salary
        salary, _kind = _split_salary_and_type(
            "₹4,00,000 - ₹6,00,000 a year - Full-time")
        self.assertEqual(parse_salary(salary)[:2], (400000, 600000))


class TestDisplayGuard(unittest.TestCase):
    """What happens with no display, now that Xvfb is an option.

    Before Xvfb support, "no display" was a hard error. It is now a hard
    error only when XVFB_MODE is "never" -- otherwise a virtual display is
    started. Every test here closes whatever it opened: an earlier version
    of this class left a started transport behind, which broke four later
    classes in the same process with "Playwright Sync API inside the asyncio
    loop".
    """

    def setUp(self):
        self.saved = {name: os.environ.pop(name, None)
                      for name in ("DISPLAY", "WAYLAND_DISPLAY")}
        self.transport = None

    def tearDown(self):
        if self.transport is not None:
            self.transport.stop()
            self.transport.cleanup_pages()
            self.transport = None
        for name, value in self.saved.items():
            if value is not None:
                os.environ[name] = value

    def test_no_display_with_xvfb_disabled_is_a_clear_error(self):
        self.transport = BrowserTransport(
            browser_config(browser_headless=False, xvfb_mode="never"))
        with self.assertRaises(BrowserUnavailable) as caught:
            self.transport.start()
        self.transport = None                        # it never started
        message = str(caught.exception)
        self.assertIn("XVFB_MODE", message)
        self.assertIn("BROWSER_HEADLESS", message)

    @unittest.skipUnless(shutil.which("Xvfb"), "Xvfb is not installed")
    @unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "needs Playwright")
    def test_no_display_with_xvfb_auto_just_works(self):
        self.transport = BrowserTransport(
            browser_config(browser_headless=False, xvfb_mode="auto"))
        self.transport.start()
        self.assertTrue(self.transport.browser.is_connected())
        self.assertIsNotNone(self.transport._display)

    @unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "needs Playwright")
    def test_headless_mode_does_not_need_a_display(self):
        self.transport = BrowserTransport(
            browser_config(browser_headless=True, xvfb_mode="never"))
        self.transport.start()
        self.assertTrue(self.transport.started)
        self.assertTrue(self.transport.page.evaluate("navigator.userAgent"))
        self.assertIsNone(self.transport._display)

    @unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "needs Playwright")
    def test_a_failed_launch_releases_the_virtual_display(self):
        """An orphaned Xvfb per failed launch would pile up on a laptop."""
        self.transport = BrowserTransport(browser_config(
            browser_headless=False, xvfb_mode="auto",
            browser_channel="definitely-not-a-real-browser-channel"))
        with self.assertRaises(BrowserUnavailable):
            self.transport.start()
        leaked = self.transport._display
        self.transport = None
        self.assertIsNone(leaked, "the virtual display was not released")
        self.assertNotIn("DISPLAY", os.environ)


class TestBrowserUnavailableIsReported(unittest.TestCase):
    """A machine with no Chromium gets a clear error, not a silent fallback."""

    def test_a_launch_failure_raises_browser_unavailable(self):
        transport = BrowserTransport(browser_config(
            browser_channel="definitely-not-a-real-browser-channel"))
        with self.assertRaises(BrowserUnavailable) as caught:
            transport.start()
        self.assertIn("Chromium", str(caught.exception))
        self.assertFalse(transport.started)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSignedOutCollection(unittest.TestCase):
    """USE_SAVED_LOGIN = False must mean no session is attached at all.

    Measured on the live site: a signed-in request is refused (403) while
    the same url signed out is served (200). So "signed out" has to be a
    real guarantee, not merely what happens when the auth file is missing --
    an auth file left in the project must not quietly re-attach a session.
    """

    def auth_file(self):
        folder = tempfile.mkdtemp(prefix="indeed-auth-")
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "indeed_auth.json")
        with open(path, "w") as handle:
            handle.write('{"cookies": [], "origins": []}')
        return path

    def transport_with(self, auth, use_login):
        config = F.build_client_config(verbose=False)
        config.auth_file = auth
        config.use_saved_login = use_login
        return BrowserTransport(config)

    def test_an_existing_auth_file_is_ignored_when_login_is_off(self):
        auth = self.auth_file()
        transport = self.transport_with(auth, False)
        self.assertIsNone(transport._saved_session_path(),
                          "a session would be attached despite "
                          "use_saved_login=False")

    def test_the_same_file_is_used_when_login_is_on(self):
        auth = self.auth_file()
        transport = self.transport_with(auth, True)
        self.assertEqual(transport._saved_session_path(), auth)

    def test_a_missing_file_also_means_signed_out(self):
        transport = self.transport_with("/nonexistent/indeed_auth.json", True)
        self.assertIsNone(transport._saved_session_path())

    def test_listing_only_needs_no_login_at_all(self):
        """The two settings are consistent: nothing in the run wants a session."""
        if F.COLLECT_JOB_DETAILS:
            self.skipTest("job pages are enabled, so a session may be wanted")
        self.assertFalse(
            F.USE_SAVED_LOGIN,
            "listing-only opens no page that needs a login, so attaching a "
            "session only risks the 403 that signing in causes")
