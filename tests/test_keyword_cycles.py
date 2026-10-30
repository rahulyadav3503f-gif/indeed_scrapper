"""Tests for the 85-keyword plan, the per-cycle shuffle and cycle progression."""

import os
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402
import target_audience_roles as TAR                # noqa: E402
from source_file.indeed_client import (            # noqa: E402
    IndeedClient,
    IndeedClientConfig,
)


# Settings a test must neutralise: real pacing gaps would make the suite
# take minutes, and the banner noise would bury the test output.  Nothing
# about the crawl LOGIC is changed by zeroing them.
FAST_SETTINGS = {
    "REQUEST_GAP_SECONDS": 0,
    "JOB_GAP_MIN_SECONDS": 0,
    "JOB_GAP_MAX_SECONDS": 0,
    "LISTING_PAGE_GAP_MIN_SECONDS": 0,
    "LISTING_PAGE_GAP_MAX_SECONDS": 0,
    "CITY_GAP_MIN_SECONDS": 0,
    "CITY_GAP_MAX_SECONDS": 0,
    "KEYWORD_GAP_MIN_SECONDS": 0,
    "KEYWORD_GAP_MAX_SECONDS": 0,
    "CYCLE_COOLDOWN_MINUTES": 0,
    "NIGHT_PAUSE_ENABLED": False,
    "MAX_JOBS_PER_HOUR": 0,
    "VERBOSE": False,
}


class TempProject:
    """Run a test against a throw-away project directory, with no sleeps."""

    def start(self):
        self.tmp = tempfile.mkdtemp(prefix="indeed-cycle-")
        self._old_base = F.BASE_DIR
        F.BASE_DIR = self.tmp
        self._saved = {name: getattr(F, name) for name in FAST_SETTINGS}
        for name, value in FAST_SETTINGS.items():
            setattr(F, name, value)
        F._JOB_READ_TIMES.clear()       # the hourly cap starts each test clean

    def stop(self):
        F.BASE_DIR = self._old_base
        for name, value in self._saved.items():
            setattr(F, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)


def quiet_client():
    config = IndeedClientConfig(verbose=False, request_gap_seconds=0,
                                fixture_jobs_per_page=3)
    return IndeedClient(config)


class TestRolePlan(unittest.TestCase):

    def test_68_target_plus_17_other_equals_85(self):
        target, other, percent = TAR.role_plan_split()
        self.assertEqual(target, 68)
        self.assertEqual(other, 17)
        self.assertEqual(target + other, 85)
        self.assertEqual(percent, 80.0)

    def test_the_crawl_plan_has_85_keywords(self):
        pairs = F.keyword_plan_pairs()
        self.assertEqual(len(pairs), 85)
        self.assertEqual(len({keyword for _c, keyword in pairs}), 85)

    def test_the_broad_department_keyword_is_never_searched(self):
        for category, keyword in F.keyword_plan_pairs():
            self.assertNotEqual(keyword.strip().lower(),
                                category.strip().lower())

    def test_the_four_target_groups_are_all_present(self):
        keywords = {keyword for _c, keyword in F.keyword_plan_pairs()}
        for expected in ("Desktop Support Engineer", "Network Engineer",
                         "System Administrator", "DevOps Engineer",
                         "Python Developer"):
            self.assertIn(expected, keywords)

    def test_non_it_titles_are_rejected_with_an_it_override(self):
        self.assertTrue(TAR.is_non_it_title("Sales Executive"))
        self.assertTrue(TAR.is_non_it_title("Staff Nurse"))
        self.assertTrue(TAR.is_non_it_title("Civil Engineer"))
        self.assertFalse(TAR.is_non_it_title("Network Security Engineer"))
        self.assertFalse(TAR.is_non_it_title("Cloud Support Engineer"))
        self.assertFalse(TAR.is_non_it_title(""))


class TestShuffle(unittest.TestCase):

    def setUp(self):
        self.pairs = F.keyword_plan_pairs()

    def test_the_shuffle_is_a_permutation(self):
        order, _seeds = F.build_keyword_order(self.pairs, 1)
        self.assertEqual(len(order), len(self.pairs))
        self.assertEqual(sorted(keyword for _c, keyword in order),
                         sorted(keyword for _c, keyword in self.pairs))

    def test_the_order_changes_between_cycles(self):
        first, _ = F.build_keyword_order(self.pairs, 1)
        second, _ = F.build_keyword_order(self.pairs, 2)
        self.assertNotEqual([k for _c, k in first], [k for _c, k in second])

    def test_the_order_is_reproducible_from_the_cycle_number(self):
        first, _ = F.build_keyword_order(self.pairs, 7)
        again, _ = F.build_keyword_order(self.pairs, 7)
        self.assertEqual(first, again)

    def test_two_ranks_get_different_orders_for_the_same_cycle(self):
        original = F.rank
        try:
            F.rank = 1
            first, _ = F.build_keyword_order(self.pairs, 3)
            F.rank = 5
            fifth, _ = F.build_keyword_order(self.pairs, 3)
        finally:
            F.rank = original
        self.assertNotEqual([k for _c, k in first], [k for _c, k in fifth])

    def test_shuffling_can_be_switched_off(self):
        original = F.KEYWORD_SHUFFLE_ENABLED
        try:
            F.KEYWORD_SHUFFLE_ENABLED = False
            order, _ = F.build_keyword_order(self.pairs, 4)
        finally:
            F.KEYWORD_SHUFFLE_ENABLED = original
        self.assertEqual([k for _c, k in order],
                         [k for _c, k in self.pairs])

    def test_the_plan_fingerprint_is_order_independent(self):
        shuffled, _ = F.build_keyword_order(self.pairs, 9)
        as_pairs = [(category, keyword) for category, keyword in shuffled]
        self.assertEqual(F.plan_fingerprint(self.pairs),
                         F.plan_fingerprint(as_pairs))


class TestCycleProgression(unittest.TestCase):
    """One cycle = every keyword across every city of this rank."""

    def setUp(self):
        self.project = TempProject()
        self.project.start()
        self.pairs = F.keyword_plan_pairs()[:3]
        self.cities = F.rank_cities()[:4]

    def tearDown(self):
        self.project.stop()

    def test_a_new_cycle_starts_at_the_beginning_and_is_persisted(self):
        state = F.load_or_open_crawl_cycle(self.cities, self.pairs)
        self.assertEqual(state["crawl_cycle_number"], 1)
        self.assertEqual(F.resume_position(state), (0, 0, 1))
        self.assertTrue(os.path.exists(
            os.path.join(self.project.tmp, F.CRAWL_CYCLE_STATE_FILE)))

    def test_every_keyword_city_combination_is_visited_exactly_once(self):
        state = F.load_or_open_crawl_cycle(self.cities, self.pairs)
        visited = []

        def record(client, state_, role_category, keyword, city, existing):
            visited.append((keyword, city))
            return 0

        original = F.run_combo
        F.run_combo = record
        try:
            result = F.run_crawl_cycle(quiet_client(), state, {})
        finally:
            F.run_combo = original

        self.assertEqual(result, "complete")
        self.assertEqual(len(visited), len(self.pairs) * len(self.cities))
        self.assertEqual(len(set(visited)), len(visited))
        self.assertEqual([city for _k, city in visited[:len(self.cities)]],
                         self.cities, "all cities before the next keyword")

    def test_a_finished_cycle_opens_the_next_one_with_a_new_order(self):
        state = F.load_or_open_crawl_cycle(self.cities, self.pairs)
        first_order = [k for _c, k in state["keyword_order"]]
        F.complete_crawl_cycle(state)

        next_state = F.load_or_open_crawl_cycle(self.cities, self.pairs)
        self.assertEqual(next_state["crawl_cycle_number"], 2)
        self.assertFalse(next_state["cycle_complete"])
        self.assertNotEqual([k for _c, k in next_state["keyword_order"]],
                            first_order)
        self.assertEqual(sorted(k for _c, k in next_state["keyword_order"]),
                         sorted(first_order))

    def test_an_unfinished_cycle_resumes_without_reshuffling(self):
        state = F.load_or_open_crawl_cycle(self.cities, self.pairs)
        order_before = list(state["keyword_order"])
        state["current_keyword_index"] = 1
        state["current_city_index"] = 2
        state["current_page"] = 3
        F.save_crawl_cycle_state(state)

        resumed = F.load_or_open_crawl_cycle(self.cities, self.pairs)
        self.assertEqual(resumed["crawl_cycle_number"], 1)
        self.assertEqual(resumed["keyword_order"], order_before)
        self.assertEqual(F.resume_position(resumed), (1, 2, 3))

    def test_a_rank_change_opens_a_fresh_cycle(self):
        F.load_or_open_crawl_cycle(self.cities, self.pairs)
        original = F.rank
        try:
            F.rank = 2
            state = F.load_or_open_crawl_cycle(self.cities, self.pairs)
        finally:
            F.rank = original
        self.assertEqual(state["crawl_cycle_number"], 2)
        self.assertEqual(F.resume_position(state), (0, 0, 1))

    def test_completing_a_cycle_resets_the_readable_progress_files(self):
        state = F.load_or_open_crawl_cycle(self.cities, self.pairs)
        F.save_city_state("Pune", 4)
        F.save_role_state("IT Department", "Network Engineer")
        F.complete_crawl_cycle(state)
        self.assertIsNone(F.load_city_state()["city_name"])
        self.assertIsNone(F.load_role_state()["search_keyword"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestReportedTotalDoesNotEndPagination(unittest.TestCase):
    """The reported total is advisory; the unique-job target decides.

    An earlier version stopped as soon as the cards in hand covered the
    reported total. That saved one request per exhausted combo, but it is
    wrong: the client falls back to the CARD COUNT when no results header
    matches, so a search whose own page title says "200 job vacancies" can
    report 16 and would have been abandoned after one page.

    The honest end-of-results signal is a page that brings nothing new,
    which costs one extra request and cannot be fooled by a bad header.
    """

    def setUp(self):
        self.requested = []
        # run_combo() checkpoints, and the checkpoint path is resolved
        # against BASE_DIR at WRITE time -- so a test that does not
        # redirect it writes the real crawl_cycle_state.json.
        self.tmp = tempfile.mkdtemp(prefix="indeed-total-")
        self._saved = {name: getattr(F, name) for name in
                       ("BASE_DIR",
                        "JOB_GAP_MIN_SECONDS", "JOB_GAP_MAX_SECONDS",
                        "LISTING_PAGE_GAP_MIN_SECONDS",
                        "LISTING_PAGE_GAP_MAX_SECONDS",
                        "MAX_JOBS_PER_HOUR", "COLLECT_JOB_DETAILS",
                        "CITY_GAP_MIN_SECONDS", "CITY_GAP_MAX_SECONDS",
                        "TARGET_UNIQUE_JOBS", "MAX_LISTING_PAGES", "VERBOSE")}
        for name in ("JOB_GAP_MIN_SECONDS", "JOB_GAP_MAX_SECONDS",
                     "LISTING_PAGE_GAP_MIN_SECONDS",
                     "LISTING_PAGE_GAP_MAX_SECONDS",
                     "CITY_GAP_MIN_SECONDS", "CITY_GAP_MAX_SECONDS"):
            setattr(F, name, 0.0)
        F.MAX_JOBS_PER_HOUR = 0
        F.COLLECT_JOB_DETAILS = False
        F.TARGET_UNIQUE_JOBS = 100
        F.MAX_LISTING_PAGES = 30
        F.VERBOSE = False
        F.BASE_DIR = self.tmp
        F._JOB_READ_TIMES = []

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(F, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, pages, reported_total):
        requested = self.requested

        class CountingClient:
            def search_jobs(self, keyword, city, page):
                requested.append(page)
                return list(pages.get(page, [])), reported_total

            def page_count(self, total, cap=None, minimum=None):
                step = F.INDEED_PAGE_STEP
                planned = (int(total) + step - 1) // step if total else 1
                return max(minimum or 1, min(planned, cap or planned))

        return CountingClient()

    def crawl(self, pages, reported_total):
        state = {"current_page": 1, "done_job_urls": []}
        real = (F.append_detail_to_excel, F.save_master, F.save_city_state,
                F.save_role_state)
        try:
            F.append_detail_to_excel = lambda row, **kw: row
            F.save_master = lambda frame: None
            F.save_city_state = lambda city, page: None
            F.save_role_state = lambda role, keyword: None
            F.run_combo(self.client(pages, reported_total), state,
                        "IT Department", "Server Administrator", "Delhi", {})
        finally:
            (F.append_detail_to_excel, F.save_master, F.save_city_state,
             F.save_role_state) = real
        return state

    @staticmethod
    def cards(low, high):
        return [{"job_key": "jk%04d" % i,
                 "title": "Server Administrator",
                 "company_name": "Acme",
                 "formatted_location": "Delhi",
                 "source_url": "https://in.indeed.com/viewjob?jk=jk%04d" % i}
                for i in range(low, high)]

    def test_a_total_of_16_does_not_stop_after_one_page(self):
        pages = {1: self.cards(0, 16), 2: self.cards(16, 32), 3: []}
        self.crawl(pages, reported_total=16)
        self.assertEqual(self.requested, [1, 2, 3],
                         "stopped on the reported total: %s" % self.requested)

    def test_a_repeated_page_ends_it_with_one_extra_request(self):
        """The cost of not trusting the header: exactly one more request."""
        same = self.cards(0, 16)
        pages = {1: same, 2: list(same)}
        self.crawl(pages, reported_total=16)
        self.assertEqual(self.requested, [1, 2],
                         "the no-new-jobs rule should end it at page 2")
