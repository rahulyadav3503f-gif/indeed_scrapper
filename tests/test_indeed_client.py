"""Tests for source_file/indeed_client.py -- listings, pagination, errors."""

import json
import os
import sys
import tempfile
import shutil
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402
from source_file.indeed_client import (            # noqa: E402
    FixtureTransport,
    _largest_number,
    IndeedAccessDenied,
    IndeedClient,
    IndeedClientConfig,
    IndeedConfigError,
    IndeedDataError,
    IndeedRateLimited,
    _normalise_search_payload,
    parse_retry_after,
)


def F_BUILD_CONFIG():
    """The configuration the main script actually builds."""
    import find_indeed_job
    return find_indeed_job.build_client_config(verbose=False)


def offline_client(**overrides):
    config = IndeedClientConfig(verbose=False, request_gap_seconds=0,
                                **overrides)
    return IndeedClient(config)


class TestFixtureListings(unittest.TestCase):
    """Retrieve job listings -- the client's first responsibility."""

    def test_search_returns_raw_dicts_and_a_total(self):
        client = offline_client()
        jobs, total = client.search_jobs("Network Engineer", "Pune", 1)
        self.assertIsInstance(jobs, list)
        self.assertTrue(all(isinstance(job, dict) for job in jobs))
        self.assertIsInstance(total, int)
        self.assertGreaterEqual(total, len(jobs))

    def test_results_are_deterministic_across_client_instances(self):
        first, _ = offline_client().search_jobs("Cloud Engineer", "Chennai", 1)
        second, _ = offline_client().search_jobs("Cloud Engineer", "Chennai", 1)
        self.assertEqual([job["job_key"] for job in first],
                         [job["job_key"] for job in second])

    def test_different_cities_produce_different_jobs(self):
        client = offline_client()
        pune, _ = client.search_jobs("Network Engineer", "Pune", 1)
        delhi, _ = client.search_jobs("Network Engineer", "Delhi", 1)
        self.assertNotEqual([job["job_key"] for job in pune],
                            [job["job_key"] for job in delhi])

    def test_keyword_and_city_are_required(self):
        client = offline_client()
        with self.assertRaises(IndeedConfigError):
            client.search_jobs("", "Pune", 1)
        with self.assertRaises(IndeedConfigError):
            client.search_jobs("Network Engineer", "", 1)

    def test_every_fixture_job_is_labelled_as_a_fixture(self):
        jobs, _ = offline_client().search_jobs("DevOps Engineer", "Mumbai", 1)
        self.assertTrue(all(job.get("fixture") is True for job in jobs))


class TestPagination(unittest.TestCase):
    """Handle pagination -- the client's third responsibility."""

    def test_page_count_rounds_up_and_respects_the_cap(self):
        client = offline_client(results_per_page=20, max_listing_pages=10)
        self.assertEqual(client.page_count(0), 0)
        self.assertEqual(client.page_count(1), 1)
        self.assertEqual(client.page_count(20), 1)
        self.assertEqual(client.page_count(21), 2)
        self.assertEqual(client.page_count(10_000), 10)       # capped
        self.assertEqual(client.page_count(10_000, cap=3), 3)

    def test_pages_do_not_repeat_jobs(self):
        client = offline_client(results_per_page=12)
        seen = []
        for _page, jobs, _total in client.iter_listings("Linux Engineer",
                                                        "Hyderabad"):
            seen.extend(job["job_key"] for job in jobs)
        self.assertEqual(len(seen), len(set(seen)))

    def test_iteration_stops_at_the_configured_cap(self):
        client = offline_client(max_listing_pages=2, results_per_page=1)
        pages = [page for page, _jobs, _total
                 in client.iter_listings("NOC Engineer", "Pune", max_pages=2)]
        self.assertLessEqual(max(pages), 2)

    def test_a_page_past_the_end_is_empty_not_an_error(self):
        client = offline_client()
        jobs, _total = client.search_jobs("NOC Engineer", "Pune", 99)
        self.assertEqual(jobs, [])


class TestFixtureFiles(unittest.TestCase):
    """A real sample from an authorised source can be replayed from disk."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="indeed-fixtures-")
        payload = {
            "total": 2,
            "jobs": [
                {"job_key": "file001", "title": "Network Engineer",
                 "company_name": "Fixture Files Ltd",
                 "description": "x" * 200},
                {"job_key": "file002", "title": "NOC Analyst",
                 "company_name": "Fixture Files Ltd",
                 "description": "y" * 200},
            ],
        }
        with open(os.path.join(self.tmp, "network-engineer-pune.json"),
                  "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_matching_fixture_file_is_used(self):
        client = offline_client(fixture_dir=self.tmp)
        jobs, total = client.search_jobs("Network Engineer", "Pune", 1)
        self.assertEqual(total, 2)
        self.assertEqual([job["job_key"] for job in jobs],
                         ["file001", "file002"])

    def test_an_unmatched_search_falls_back_to_generated_jobs(self):
        client = offline_client(fixture_dir=self.tmp)
        jobs, _total = client.search_jobs("Cloud Engineer", "Kochi", 1)
        self.assertTrue(all(job["job_key"].startswith("fx") for job in jobs))

    def test_a_corrupt_fixture_file_raises_a_data_error(self):
        with open(os.path.join(self.tmp, "broken-city.json"), "w") as handle:
            handle.write("{not json")
        client = offline_client(fixture_dir=self.tmp)
        with self.assertRaises(IndeedDataError):
            client.search_jobs("broken", "city", 1)


class TestJobDetails(unittest.TestCase):
    """Retrieve job details -- the client's second responsibility."""

    def test_a_complete_listing_record_is_not_re_requested(self):
        client = offline_client()
        jobs, _ = client.search_jobs("Network Engineer", "Pune", 1)
        before = client.requests_made
        detail = client.get_job_detail(jobs[0])
        self.assertEqual(client.requests_made, before,
                         "a listing that already carries the description "
                         "must not cost a second request")
        self.assertEqual(detail["job_key"], jobs[0]["job_key"])

    def test_a_thin_record_is_fetched_and_merged(self):
        class ThinTransport(FixtureTransport):
            def detail(self, job_reference, config):
                return {"description": "z" * 300, "company_name": "Merged Ltd"}

        client = IndeedClient(IndeedClientConfig(verbose=False,
                                                 request_gap_seconds=0),
                              transport=ThinTransport())
        detail = client.get_job_detail({"job_key": "thin01",
                                        "title": "Network Engineer"})
        self.assertEqual(detail["title"], "Network Engineer")   # kept
        self.assertEqual(detail["company_name"], "Merged Ltd")  # merged in
        self.assertEqual(len(detail["description"]), 300)


class TestPayloadShapes(unittest.TestCase):
    """A JSON source may answer in several shapes; all are understood."""

    def test_bare_list(self):
        payload = _normalise_search_payload([{"job_key": "a"}])
        self.assertEqual(payload["total"], 1)

    def test_known_containers(self):
        for key in ("jobs", "results", "data", "items", "hits", "postings"):
            payload = _normalise_search_payload({key: [{"job_key": "a"}],
                                                 "total": 7})
            self.assertEqual(payload["total"], 7)
            self.assertEqual(len(payload["jobs"]), 1)

    def test_an_unknown_shape_is_an_error_not_a_silent_empty_result(self):
        with self.assertRaises(IndeedDataError):
            _normalise_search_payload({"unexpected": {"a": 1}})


class TestAccessAndQuota(unittest.TestCase):
    """Access refusals escalate; they are never worked around."""

    def test_repeated_refusals_on_distinct_urls_become_session_wide(self):
        class DenyingTransport(FixtureTransport):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def search(self, keyword, city, page, config):
                self.calls += 1
                raise IndeedAccessDenied("denied", url="url-%d" % self.calls,
                                         status=403)

        client = IndeedClient(IndeedClientConfig(verbose=False,
                                                 request_gap_seconds=0),
                              transport=DenyingTransport())
        with self.assertRaises(IndeedAccessDenied) as first:
            client.search_jobs("a", "b", 1)
        self.assertEqual(first.exception.scope, "isolated")

        with self.assertRaises(IndeedAccessDenied) as second:
            client.search_jobs("c", "d", 1)
        self.assertEqual(second.exception.scope, "session_wide")

    def test_a_successful_read_clears_the_refusal_memory(self):
        class FlakyTransport(FixtureTransport):
            def __init__(self):
                super().__init__()
                self.deny_next = True

            def search(self, keyword, city, page, config):
                if self.deny_next:
                    self.deny_next = False
                    raise IndeedAccessDenied("denied", url="one", status=403)
                return super().search(keyword, city, page, config)

        client = IndeedClient(IndeedClientConfig(verbose=False,
                                                 request_gap_seconds=0),
                              transport=FlakyTransport())
        with self.assertRaises(IndeedAccessDenied):
            client.search_jobs("a", "b", 1)
        client.search_jobs("a", "b", 1)                      # succeeds
        self.assertEqual(client._distinct_denied_urls, set())

    def test_rate_limit_carries_the_servers_own_retry_after(self):
        error = IndeedRateLimited("quota", url="u", retry_after=321)
        self.assertEqual(error.retry_after, 321)

    def test_retry_after_parsing(self):
        self.assertEqual(parse_retry_after("120"), 120)
        self.assertEqual(parse_retry_after("  45 "), 45)
        self.assertIsNone(parse_retry_after(""))
        self.assertIsNone(parse_retry_after("not a date"))
        self.assertEqual(parse_retry_after("99999", cap_seconds=600), 600)
        self.assertIsNotNone(
            parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT"))


class TestLiveModeGuards(unittest.TestCase):
    """Live collection refuses to guess an endpoint."""

    def test_offline_preflight_states_that_nothing_reaches_indeed(self):
        ok, message = offline_client().preflight()
        self.assertTrue(ok)
        self.assertIn("DISABLED", message)

    def test_live_without_a_base_url_fails_preflight(self):
        config = IndeedClientConfig(live_enabled=True, verbose=False)
        client = IndeedClient(config, transport=FixtureTransport())
        ok, message = client.preflight()
        self.assertFalse(ok)
        self.assertIn("INDEED_SOURCE_BASE_URL", message)

    def test_live_without_a_search_path_fails_preflight(self):
        config = IndeedClientConfig(live_enabled=True, verbose=False,
                                    base_url="https://example.invalid")
        client = IndeedClient(config, transport=FixtureTransport())
        ok, message = client.preflight()
        self.assertFalse(ok)
        self.assertIn("INDEED_SEARCH_PATH", message)

    def test_credentials_come_from_the_environment_only(self):
        config = IndeedClientConfig(api_key_env="INDEED_TEST_KEY_NOT_SET")
        os.environ.pop("INDEED_TEST_KEY_NOT_SET", None)
        self.assertEqual(config.api_key, "")
        os.environ["INDEED_TEST_KEY_NOT_SET"] = "secret-value"
        try:
            self.assertEqual(config.api_key, "secret-value")
        finally:
            os.environ.pop("INDEED_TEST_KEY_NOT_SET", None)

    def test_the_client_builds_urls_from_configuration_not_from_itself(self):
        """Every url the client requests comes from the main file's settings.

        The site, the search url and the job url are configuration, so they
        can be changed, pointed at a staging mirror, or switched off in one
        place -- the client hardcodes no request url of its own.
        """
        with open(os.path.join(PROJECT_ROOT, "source_file",
                               "indeed_client.py"), encoding="utf-8") as handle:
            source = handle.read()
        code = "\n".join(line for line in source.splitlines()
                          if not line.strip().startswith("#"))
        for marker in ("https://in.indeed.com/jobs", "api.indeed.com",
                       "indeed.com/rpc", "publisher="):
            self.assertNotIn(marker, code, marker)

    def test_configured_urls_build_correctly(self):
        config = IndeedClientConfig(
            live_enabled=True, browser_visible=True, verbose=False,
            site_base_url="https://example.invalid",
            search_url_template="{base}/jobs?q={keyword}&l={city}&start={start}",
            job_url_template="{base}/viewjob?jk={job_key}",
            page_step=10)
        self.assertEqual(
            config.live_search_url("Network Engineer", "Pune", 1),
            "https://example.invalid/jobs?q=Network+Engineer&l=Pune&start=0")
        self.assertEqual(
            config.live_search_url("Network Engineer", "Pune", 4),
            "https://example.invalid/jobs?q=Network+Engineer&l=Pune&start=30")
        self.assertEqual(config.live_job_url("ab12"),
                         "https://example.invalid/viewjob?jk=ab12")

    def test_a_bad_url_template_is_a_configuration_error(self):
        config = IndeedClientConfig(
            live_enabled=True, site_base_url="https://example.invalid",
            search_url_template="{base}/jobs?q={nonexistent}")
        with self.assertRaises(IndeedConfigError):
            config.live_search_url("a", "b", 1)

    def test_robots_disallowed_paths_are_refused(self):
        config = IndeedClientConfig(
            site_base_url="https://example.invalid",
            robots_disallowed_prefixes=("/applystart", "/rc/", "/rpc/",
                                        "/my/", "/api/"))
        for refused in ("https://example.invalid/applystart?jk=x",
                        "https://example.invalid/rc/clk?jk=x",
                        "https://example.invalid/rpc/jobdescs",
                        "https://example.invalid/my/jobs",
                        "https://example.invalid/api/fetch/mc-anon"):
            self.assertTrue(config.robots_refuses(refused), refused)
        for allowed in ("https://example.invalid/jobs?q=a&l=b",
                        "https://example.invalid/viewjob?jk=x",
                        "https://example.invalid/cmp/Acme"):
            self.assertEqual(config.robots_refuses(allowed), "", allowed)

    def test_the_robots_guard_cannot_be_emptied_by_accident(self):
        config = F_BUILD_CONFIG()
        self.assertTrue(config.respect_robots_disallow)
        self.assertTrue(config.robots_disallowed_prefixes)




class TestPagePlanning(unittest.TestCase):
    """How many listing pages one (keyword, city) pair is worth.

    The count in the site's results header cannot be trusted downwards: a
    narrow search that really has five pages often advertises "16 jobs",
    which divided by the page size said two pages and sent the crawl on to
    the next city. MIN_LISTING_PAGES is the floor that fixes that; the cap
    still wins over it.
    """

    def client(self, **overrides):
        settings = dict(verbose=False, request_gap_seconds=0,
                        results_per_page=10, min_listing_pages=4,
                        max_listing_pages=10)
        settings.update(overrides)
        return IndeedClient(IndeedClientConfig(**settings))

    def test_a_small_header_count_is_lifted_to_the_floor(self):
        client = self.client()
        for reported in (1, 5, 8, 16, 30, 40):
            self.assertEqual(client.page_count(reported), 4,
                             "header %d" % reported)

    def test_a_large_header_count_is_used_as_is(self):
        client = self.client()
        self.assertEqual(client.page_count(45), 5)
        self.assertEqual(client.page_count(70), 7)

    def test_the_cap_always_wins_over_the_floor(self):
        client = self.client(min_listing_pages=20, max_listing_pages=3)
        self.assertEqual(client.page_count(10_000), 3)
        self.assertEqual(client.page_count(1), 3)

    def test_no_cards_still_plans_nothing(self):
        client = self.client()
        self.assertEqual(client.page_count(0), 0)
        self.assertEqual(client.page_count(-5), 0)

    def test_the_floor_defaults_to_one_so_the_carrier_is_safe(self):
        config = IndeedClientConfig(verbose=False)
        self.assertEqual(config.min_listing_pages, 1)

    def test_the_floor_is_never_below_one(self):
        config = IndeedClientConfig(verbose=False, min_listing_pages=0)
        self.assertEqual(config.min_listing_pages, 1)

    def test_an_explicit_minimum_overrides_the_configured_one(self):
        client = self.client(min_listing_pages=4)
        self.assertEqual(client.page_count(1, minimum=1), 1)
        self.assertEqual(client.page_count(1, minimum=7), 7)


class TestResultHeaderParsing(unittest.TestCase):
    """The result count is the biggest number in the header, not the first."""

    def test_a_plain_count(self):
        self.assertEqual(_largest_number("16 jobs"), 16)

    def test_a_page_prefix_does_not_win(self):
        self.assertEqual(_largest_number("Page 1 of 16 jobs"), 16)
        self.assertEqual(_largest_number("Page 2 of 1,234 jobs"), 1234)

    def test_thousands_separators(self):
        self.assertEqual(_largest_number("1,234 jobs"), 1234)
        self.assertEqual(_largest_number("12,345 jobs"), 12345)

    def test_no_number_at_all(self):
        self.assertIsNone(_largest_number("no numbers here"))
        self.assertIsNone(_largest_number(""))
        self.assertIsNone(_largest_number(None))


class TestPaginationStops(unittest.TestCase):
    """The floor must not cost requests on a city that has one real page."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="indeed-pages-")
        self._saved = {name: getattr(F, name) for name in
                       ("BASE_DIR", "VERBOSE", "REQUEST_GAP_SECONDS",
                        "JOB_GAP_SECONDS", "NIGHT_PAUSE_ENABLED",
                        "LOG_EVERY_ROW", "SHOW_JOB_TIMER")}
        F.BASE_DIR = self.tmp
        F.VERBOSE = False
        F.REQUEST_GAP_SECONDS = 0
        F.JOB_GAP_SECONDS = 0
        F.NIGHT_PAUSE_ENABLED = False
        F.LOG_EVERY_ROW = False
        F.SHOW_JOB_TIMER = False

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(F, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def crawl(self, transport):
        client = IndeedClient(
            IndeedClientConfig(verbose=False, request_gap_seconds=0,
                               results_per_page=F.RESULTS_PER_PAGE,
                               min_listing_pages=F.MIN_LISTING_PAGES,
                               max_listing_pages=F.MAX_LISTING_PAGES),
            transport=transport)
        state = F.load_or_open_crawl_cycle(
            ["Pune"], [("IT Department", "Network Engineer")])
        state["current_page"] = 1
        saved = F.run_combo(client, state, "IT Department",
                            "Network Engineer", "Pune", {})
        return saved, transport.asked

    @staticmethod
    def pages_transport(real_pages, header, repeat_after=None):
        class Pages(FixtureTransport):
            def __init__(self):
                super().__init__()
                self.asked = []

            def search(self, keyword, city, page, config):
                self.asked.append(page)
                if page > real_pages:
                    return {"jobs": [], "total": header}
                shown = min(page, repeat_after) if repeat_after else page
                jobs = [{"job_key": "p%dj%d" % (shown, i),
                         "title": "Network Engineer",
                         "company_name": "Acme Ltd",
                         "description": "d" * 200,
                         "source_url": "https://x.invalid/p%dj%d" % (shown, i)}
                        for i in range(3)]
                return {"jobs": jobs, "total": header}
        return Pages()

    def test_an_under_reporting_header_no_longer_stops_at_two_pages(self):
        """16 jobs / 10 per page used to mean two pages and a new city."""
        saved, asked = self.crawl(self.pages_transport(5, header=16))
        self.assertEqual(asked, [1, 2, 3, 4], "the floor must be crawled")
        self.assertEqual(saved, 12)

    def test_an_empty_page_stops_it_at_once(self):
        saved, asked = self.crawl(self.pages_transport(1, header=16))
        self.assertEqual(asked, [1, 2],
                         "the floor costs ONE wasted request, not three")
        self.assertEqual(saved, 3)

    def test_a_page_that_repeats_earlier_jobs_stops_it(self):
        """A header promising ten pages must not cost ten requests."""
        saved, asked = self.crawl(
            self.pages_transport(10, header=500, repeat_after=2))
        self.assertEqual(asked, [1, 2, 3])
        self.assertEqual(saved, 6)

    def test_a_rich_search_is_crawled_up_to_the_cap(self):
        saved, asked = self.crawl(self.pages_transport(50, header=5000))
        self.assertEqual(asked, list(range(1, F.MAX_LISTING_PAGES + 1)))
        self.assertEqual(saved, 3 * F.MAX_LISTING_PAGES)

    def test_cards_are_trusted_over_a_zero_header(self):
        saved, asked = self.crawl(self.pages_transport(2, header=0))
        self.assertGreaterEqual(len(asked), 2,
                                "a wrong header must not skip a real page")
        self.assertEqual(saved, 6)

    def test_a_genuinely_empty_search_costs_one_request(self):
        saved, asked = self.crawl(self.pages_transport(0, header=0))
        self.assertEqual(asked, [1])
        self.assertEqual(saved, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
