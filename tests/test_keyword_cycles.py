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
    "JOB_GAP_SECONDS": 0,
    "CITY_GAP_SECONDS": 0,
    "KEYWORD_GAP_SECONDS": 0,
    "CYCLE_COOLDOWN_MINUTES": 0,
    "NIGHT_PAUSE_ENABLED": False,
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
