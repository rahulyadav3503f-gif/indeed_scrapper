"""Tests for JSON checkpoints: persistence, resume and recovery."""

import json
import os
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402
from source_file.indeed_client import (            # noqa: E402
    IndeedClient,
    IndeedClientConfig,
)

PAIRS = [("IT Department", "Network Engineer"),
         ("IT Department", "Cloud Engineer"),
         ("IT Department", "Linux Engineer")]
CITIES = ["Pune", "Mumbai", "Chennai"]


class CheckpointTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="indeed-state-")
        self._saved = {name: getattr(F, name) for name in
                       ("BASE_DIR", "VERBOSE", "REQUEST_GAP_SECONDS",
                        "JOB_GAP_MIN_SECONDS", "JOB_GAP_MAX_SECONDS",
                        "LISTING_PAGE_GAP_MIN_SECONDS",
                        "LISTING_PAGE_GAP_MAX_SECONDS",
                        "CITY_GAP_MIN_SECONDS", "CITY_GAP_MAX_SECONDS",
                        "KEYWORD_GAP_MIN_SECONDS",
                        "KEYWORD_GAP_MAX_SECONDS", "NIGHT_PAUSE_ENABLED",
                        "MAX_JOBS_PER_HOUR")}
        F.BASE_DIR = self.tmp
        F.VERBOSE = False
        F.REQUEST_GAP_SECONDS = 0
        F.JOB_GAP_MIN_SECONDS = 0
        F.LISTING_PAGE_GAP_MIN_SECONDS = 0
        F.LISTING_PAGE_GAP_MAX_SECONDS = 0
        F.JOB_GAP_MAX_SECONDS = 0
        F.CITY_GAP_MIN_SECONDS = 0
        F.CITY_GAP_MAX_SECONDS = 0
        F.KEYWORD_GAP_MIN_SECONDS = 0
        F.KEYWORD_GAP_MAX_SECONDS = 0
        F.NIGHT_PAUSE_ENABLED = False
        F.MAX_JOBS_PER_HOUR = 0        # no rate cap in tests
        F._JOB_READ_TIMES.clear()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(F, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def client(self, jobs_per_page=2):
        return IndeedClient(IndeedClientConfig(
            verbose=False, request_gap_seconds=0,
            fixture_jobs_per_page=jobs_per_page))


class TestStateFiles(CheckpointTestCase):

    def test_all_state_files_live_in_the_project_root(self):
        F.save_cycle_num(3)
        F.save_city_state("Pune", 2)
        F.save_role_state("IT Department", "Network Engineer")
        F.load_or_open_crawl_cycle(CITIES, PAIRS)
        F.record_failed("listing", "ref-1", "5xx", status=503)

        for name in (F.CYCLE_STATE_FILE, F.CITY_STATE_FILE, F.ROLE_STATE_FILE,
                     F.CRAWL_CYCLE_STATE_FILE, F.FAILED_PAGES_FILE):
            self.assertTrue(os.path.isfile(self.path(name)), name)
        subdirs = [entry for entry in os.listdir(self.tmp)
                   if os.path.isdir(self.path(entry))]
        self.assertEqual(subdirs, [], "state must not live in a subfolder")

    def test_no_redundant_current_row_file_is_created(self):
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        F.save_crawl_cycle_state(state)
        self.assertFalse(os.path.exists(self.path("current_row.json")),
                         "the row pointer lives inside crawl_cycle_state.json")

    def test_json_is_written_atomically(self):
        F.write_json_atomic("probe.json", {"a": 1})
        with open(self.path("probe.json")) as handle:
            self.assertEqual(json.load(handle)["a"], 1)
        leftovers = [name for name in os.listdir(self.tmp)
                     if name.startswith(".") and name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_an_unreadable_state_file_is_treated_as_absent(self):
        with open(self.path(F.CRAWL_CYCLE_STATE_FILE), "w") as handle:
            handle.write("{half written")
        self.assertIsNone(F.load_crawl_cycle_state())
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        self.assertEqual(state["crawl_cycle_number"], 1)

    def test_a_missing_cycle_counter_reads_as_zero(self):
        self.assertEqual(F.load_cycle_num(), 0)
        F.save_cycle_num(5)
        self.assertEqual(F.load_cycle_num(), 5)

    def test_city_and_role_state_round_trip(self):
        F.save_city_state("Nagpur", 4)
        F.save_role_state("IT Department", "NOC Engineer")
        self.assertEqual(F.load_city_state(),
                         {"city_name": "Nagpur", "page_number": 4})
        self.assertEqual(F.load_role_state()["search_keyword"], "NOC Engineer")
        F.reset_city_state()
        F.reset_role_state()
        self.assertIsNone(F.load_city_state()["city_name"])
        self.assertIsNone(F.load_role_state()["role_category"])


class TestResume(CheckpointTestCase):

    def test_the_position_is_saved_before_the_first_request(self):
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        with open(self.path(F.CRAWL_CYCLE_STATE_FILE)) as handle:
            stored = json.load(handle)
        self.assertEqual(stored["crawl_cycle_number"],
                         state["crawl_cycle_number"])
        self.assertEqual(stored["keyword_order"], state["keyword_order"])

    def test_a_mid_cycle_restart_resumes_at_the_same_place(self):
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        state["current_keyword_index"] = 2
        state["current_city_index"] = 1
        state["current_page"] = 4
        state["done_job_urls"] = ["https://in.indeed.com/viewjob?jk=a"]
        F.save_crawl_cycle_state(state)

        resumed = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        self.assertEqual(F.resume_position(resumed), (2, 1, 4))
        self.assertEqual(resumed["done_job_urls"],
                         ["https://in.indeed.com/viewjob?jk=a"])

    def test_out_of_range_indices_are_clamped_not_crashed(self):
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        state["current_keyword_index"] = 999
        state["current_city_index"] = 999
        state["current_page"] = -5
        keyword_index, city_index, page = F.resume_position(state)
        self.assertLessEqual(keyword_index, len(state["keyword_order"]))
        self.assertLess(city_index, len(state["cities"]))
        self.assertEqual(page, 1)

    def test_an_interrupted_cycle_finishes_the_remaining_combos_only(self):
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        visited = []
        crashed = []

        def record(client, state_, role_category, keyword, city, existing):
            visited.append((keyword, city))
            if len(visited) == 4 and not crashed:   # "crash" once, mid-cycle
                crashed.append(True)
                raise KeyboardInterrupt("simulated stop")
            return 0

        original = F.run_combo
        F.run_combo = record
        try:
            with self.assertRaises(KeyboardInterrupt):
                F.run_crawl_cycle(self.client(), state, {})
            F.save_crawl_cycle_state(state)
            first_half = list(visited)

            # Restart: a brand-new state object, loaded from disk.
            resumed = F.load_or_open_crawl_cycle(CITIES, PAIRS)
            visited.clear()
            F.run_combo = record
            result = F.run_crawl_cycle(self.client(), resumed, {})
        finally:
            F.run_combo = original

        self.assertEqual(result, "complete")
        self.assertEqual(resumed["crawl_cycle_number"], 1, "same cycle")
        total = len(first_half) + len(visited)
        self.assertEqual(total, len(PAIRS) * len(CITIES) + 1,
                         "only the interrupted combo is repeated")
        self.assertEqual(len(set(first_half[:-1]) & set(visited)), 0,
                         "completed combos are not crawled again")

    def test_a_completed_cycle_is_not_resumed(self):
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        F.complete_crawl_cycle(state)
        nxt = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        self.assertEqual(nxt["crawl_cycle_number"], 2)
        self.assertEqual(F.resume_position(nxt), (0, 0, 1))


class TestDuplicatePrevention(CheckpointTestCase):

    def productive_combo(self, client):
        """A (keyword, city) pair the fixture source actually has jobs for."""
        for keyword in ("Network Engineer", "Cloud Engineer", "Linux Engineer"):
            for city in ("Pune", "Mumbai", "Chennai", "Delhi", "Kolkata"):
                jobs, _total = client.search_jobs(keyword, city, 1)
                if jobs:
                    return keyword, city
        self.fail("no fixture combo returned any job")

    def test_re_crawling_a_combo_writes_no_duplicate_rows(self):
        import pandas as pd

        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        client = self.client()
        keyword, city = self.productive_combo(client)

        F.run_combo(client, state, "IT Department", keyword, city, {})
        first = pd.read_excel(self.path(F.DETAIL_OUTPUT_FILE))
        self.assertGreater(len(first), 0)

        # The same combo again -- after a restart the working file is the
        # only memory, and it must stop every row from being written twice.
        F.run_combo(client, state, "IT Department", keyword, city, {})
        second = pd.read_excel(self.path(F.DETAIL_OUTPUT_FILE))
        self.assertEqual(len(first), len(second))
        self.assertEqual(second["source_url"].nunique(), len(second))

    def test_the_master_workbook_counts_repeat_sightings(self):
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        client = self.client()
        keyword, city = self.productive_combo(client)
        existing = {}
        F.run_combo(client, state, "IT Department", keyword, city, existing)
        counts_before = [entry["seen_count"] for entry in existing.values()]
        self.assertTrue(counts_before)
        F.run_combo(client, state, "IT Department", keyword, city, existing)
        counts_after = [entry["seen_count"] for entry in existing.values()]
        self.assertEqual(sum(counts_before) * 2, sum(counts_after))


class TestArchiveNumberRecovery(CheckpointTestCase):

    def test_the_archive_number_is_recovered_from_disk_after_a_restart(self):
        F.save_cycle_num(7)
        self.assertEqual(F.get_next_cycle_file(),
                         ("current_data_cycle8.xlsx", 8))

    def test_a_crawl_cycle_restart_does_not_reset_the_archive_number(self):
        F.save_cycle_num(4)
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        F.complete_crawl_cycle(state)
        F.load_or_open_crawl_cycle(CITIES, PAIRS)
        self.assertEqual(F.load_cycle_num(), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
