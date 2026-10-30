"""TARGET_UNIQUE_JOBS pagination, and the listing-page delay.

Every test here is local: a counting stub stands in for the client, so no
request reaches any Indeed host.  Scenarios A-I of the specification.
"""

import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402


def card(index, keyword="Python Developer", city="Pune"):
    """One listing card, shaped the way the browser transport emits them."""
    return {
        "job_key": "jk%04d" % index,
        "title": "%s %d" % (keyword, index),
        "company_name": "Company %d" % index,
        "formatted_location": "%s, Maharashtra" % city,
        "snippet": "Build and maintain services.",
        "source_url": "https://in.indeed.com/viewjob?jk=jk%04d" % index,
    }


class PagedClient:
    """Serves prepared pages and records which ones were asked for."""

    def __init__(self, pages, reported_total=None, deny_on=None):
        self.pages = pages                  # {page_number: [card, ...]}
        self.reported_total = reported_total
        self.deny_on = deny_on
        self.requested = []

    def search_jobs(self, keyword, city, page):
        self.requested.append(page)
        if self.deny_on is not None and page == self.deny_on:
            from source_file.indeed_client import IndeedAccessDenied
            raise IndeedAccessDenied(
                "HTTP 403 for search page %d -- access refused" % page,
                status=403,
                url="https://in.indeed.com/jobs?start=%d" % ((page - 1) * 10))
        cards = self.pages.get(page, [])
        total = self.reported_total
        if total is None:
            total = len(cards)
        return list(cards), total

    def page_count(self, total, cap=None, minimum=None):
        step = F.INDEED_PAGE_STEP
        pages = (int(total) + step - 1) // step if total else 1
        return max(minimum or 1, min(pages, cap or pages))


class ComboTestCase(unittest.TestCase):
    """Runs run_combo() with every delay and side effect neutralised."""

    TARGET = 100
    MAX_PAGES = 30

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="indeed-target-")
        self._saved = {name: getattr(F, name) for name in (
            "BASE_DIR", "VERBOSE", "REQUEST_GAP_SECONDS",
            "JOB_GAP_MIN_SECONDS", "JOB_GAP_MAX_SECONDS",
            "LISTING_PAGE_GAP_MIN_SECONDS", "LISTING_PAGE_GAP_MAX_SECONDS",
            "CITY_GAP_MIN_SECONDS", "CITY_GAP_MAX_SECONDS",
            "KEYWORD_GAP_MIN_SECONDS", "KEYWORD_GAP_MAX_SECONDS",
            "NIGHT_PAUSE_ENABLED", "MAX_JOBS_PER_HOUR",
            "TARGET_UNIQUE_JOBS", "MIN_LISTING_PAGES", "MAX_LISTING_PAGES",
            "STOP_ON_PAGE_WITH_NO_NEW_JOBS", "COLLECT_JOB_DETAILS",
            "LOG_EVERY_ROW", "SHOW_JOB_TIMER")}
        F.BASE_DIR = self.tmp
        F.VERBOSE = False
        F.REQUEST_GAP_SECONDS = 0
        F.JOB_GAP_MIN_SECONDS = 0
        F.JOB_GAP_MAX_SECONDS = 0
        F.LISTING_PAGE_GAP_MIN_SECONDS = 0
        F.LISTING_PAGE_GAP_MAX_SECONDS = 0
        F.CITY_GAP_MIN_SECONDS = 0
        F.CITY_GAP_MAX_SECONDS = 0
        F.KEYWORD_GAP_MIN_SECONDS = 0
        F.KEYWORD_GAP_MAX_SECONDS = 0
        F.NIGHT_PAUSE_ENABLED = False
        F.MAX_JOBS_PER_HOUR = 0
        F._JOB_READ_TIMES.clear()
        F.TARGET_UNIQUE_JOBS = self.TARGET
        F.MIN_LISTING_PAGES = 1
        F.MAX_LISTING_PAGES = self.MAX_PAGES
        F.STOP_ON_PAGE_WITH_NO_NEW_JOBS = True
        F.COLLECT_JOB_DETAILS = False
        F.LOG_EVERY_ROW = False
        F.SHOW_JOB_TIMER = False

        self.saved_rows = []
        self._real = {
            "append": F.append_detail_to_excel,
            "master": F.save_master,
            "city": F.save_city_state,
            "role": F.save_role_state,
        }
        F.append_detail_to_excel = \
            lambda row, **kw: self.saved_rows.append(row) or row
        F.save_master = lambda frame: None
        F.save_city_state = lambda city, page: None
        F.save_role_state = lambda role, keyword: None

    def tearDown(self):
        F.append_detail_to_excel = self._real["append"]
        F.save_master = self._real["master"]
        F.save_city_state = self._real["city"]
        F.save_role_state = self._real["role"]
        for name, value in self._saved.items():
            setattr(F, name, value)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_combo(self, client, state=None):
        state = state if state is not None else {"current_page": 1,
                                                 "done_job_urls": []}
        self.state = state
        saved = F.run_combo(client, state, "IT Department",
                            "Python Developer", "Pune", {})
        return saved

    def unique_seen(self):
        """What the run recorded, read back off the checkpoint it wrote."""
        return self.recorded_unique


# ---------------------------------------------------------------------------
# A -- the target is reached
# ---------------------------------------------------------------------------

class TestA_TargetReached(ComboTestCase):

    def test_pagination_stops_once_the_target_is_reached(self):
        # 16 cards on page 1, then 10 new per page -- the shape of the site.
        pages = {1: [card(i) for i in range(16)]}
        for page in range(2, 20):
            start = 16 + (page - 2) * 10
            pages[page] = [card(i) for i in range(start, start + 10)]
        client = PagedClient(pages)
        self.run_combo(client)

        # 16 + 10*n >= 100 first holds at n = 9, i.e. page 10.
        self.assertEqual(max(client.requested), 10,
                         "requested pages %s" % client.requested)
        self.assertEqual(client.requested, list(range(1, 11)))

    def test_the_stop_reason_is_target_reached(self):
        pages = {1: [card(i) for i in range(100)]}
        client = PagedClient(pages)
        reasons = []
        real = F.report_combo_stop
        F.report_combo_stop = lambda reason, *a: reasons.append(reason)
        try:
            self.run_combo(client)
        finally:
            F.report_combo_stop = real
        self.assertEqual(reasons, [F.STOP_TARGET_REACHED])
        self.assertEqual(client.requested, [1], "one page already had 100")


# ---------------------------------------------------------------------------
# B -- the target is exceeded, and that is fine
# ---------------------------------------------------------------------------

class TestB_TargetExceeded(ComboTestCase):

    def test_the_crossing_page_is_processed_in_full(self):
        """96 seen, next page adds 10 -> 106, and none are discarded."""
        pages = {1: [card(i) for i in range(96)],
                 2: [card(i) for i in range(96, 106)]}
        client = PagedClient(pages)
        self.run_combo(client)
        self.assertEqual(client.requested, [1, 2])
        # Every card from both pages became a row: nothing dropped for
        # exceeding the target.
        self.assertEqual(len(self.saved_rows), 106)
        keys = {row["source_url"] for row in self.saved_rows}
        self.assertEqual(len(keys), 106)


# ---------------------------------------------------------------------------
# C -- duplicates count once
# ---------------------------------------------------------------------------

class TestC_Duplicates(ComboTestCase):

    def test_duplicate_cards_are_counted_once(self):
        """Page 1: 16 unique. Page 2: 16 cards of which 6 repeat -> 26."""
        page1 = [card(i) for i in range(16)]
        page2 = [card(i) for i in range(10, 26)]       # 10..15 repeat
        pages = {1: page1, 2: page2, 3: []}
        client = PagedClient(pages)
        self.run_combo(client)
        tracked = self.state.get("seen_job_ids")
        # The combo finished, so the tracked set is cleared -- assert on the
        # distinct identities that reached Excel instead.
        self.assertIsNone(tracked)
        unique = {row["source_url"] for row in self.saved_rows}
        self.assertEqual(len(unique), 26,
                         "26 distinct jobs across 32 cards")

    def test_identity_ignores_cards_with_no_key(self):
        self.assertEqual(F.job_identity({"job_key": "abc"}), "abc")
        self.assertEqual(
            F.job_identity({"source_url":
                            "https://in.indeed.com/viewjob?jk=xyz"}), "xyz")
        self.assertEqual(F.job_identity({}), "",
                         "a card with no identity must not be counted")
        self.assertEqual(F.job_identity(None), "")

    def test_unidentified_cards_do_not_inflate_the_count(self):
        pages = {1: [{"title": "No key", "company_name": "X",
                      "formatted_location": "Pune"} for _ in range(5)],
                 2: []}
        client = PagedClient(pages)
        reasons = []
        real = F.report_combo_stop
        F.report_combo_stop = lambda reason, seen, *a: reasons.append((reason, seen))
        try:
            self.run_combo(client)
        finally:
            F.report_combo_stop = real
        self.assertEqual(reasons[0][1], 0,
                         "five cards with no identity counted as %d unique"
                         % reasons[0][1])


# ---------------------------------------------------------------------------
# D -- fewer results than the target
# ---------------------------------------------------------------------------

class TestD_NotEnoughResults(ComboTestCase):

    def test_it_stops_when_the_results_run_out(self):
        """40 jobs exist. It must not loop trying to reach 100."""
        pages = {1: [card(i) for i in range(16)],
                 2: [card(i) for i in range(16, 40)],
                 3: []}
        client = PagedClient(pages)
        reasons = []
        real = F.report_combo_stop
        F.report_combo_stop = lambda reason, seen, *a: reasons.append((reason, seen))
        try:
            self.run_combo(client)
        finally:
            F.report_combo_stop = real
        self.assertEqual(client.requested, [1, 2, 3])
        self.assertEqual(reasons[0][0], F.STOP_EMPTY_PAGE)
        self.assertEqual(reasons[0][1], 40)

    def test_repeating_pages_stop_on_the_no_new_jobs_rule(self):
        """A site that repeats the same page forever must not spin."""
        same = [card(i) for i in range(16)]
        pages = {page: list(same) for page in range(1, 40)}
        client = PagedClient(pages)
        reasons = []
        real = F.report_combo_stop
        F.report_combo_stop = lambda reason, seen, *a: reasons.append((reason, seen))
        try:
            self.run_combo(client)
        finally:
            F.report_combo_stop = real
        self.assertEqual(reasons[0][0], F.STOP_NO_NEW_JOBS)
        self.assertEqual(client.requested, [1, 2],
                         "stopped on the second page, not after 30")


# ---------------------------------------------------------------------------
# E -- a wrong reported total must not end pagination
# ---------------------------------------------------------------------------

class TestE_ReportedTotalIsAdvisory(ComboTestCase):

    def test_a_total_of_16_does_not_stop_a_search_with_more(self):
        """The header reports 16; real unique jobs continue past it.

        The client falls back to the CARD COUNT when no results header
        matches, so "16 reported" is routinely wrong. Pagination must
        follow the target instead.
        """
        pages = {1: [card(i) for i in range(16)],
                 2: [card(i) for i in range(16, 32)],
                 3: [card(i) for i in range(32, 48)],
                 4: []}
        client = PagedClient(pages, reported_total=16)
        self.run_combo(client)
        self.assertEqual(client.requested, [1, 2, 3, 4],
                         "stopped early on the reported total: %s"
                         % client.requested)
        self.assertEqual(len({row["source_url"] for row in self.saved_rows}),
                         48)


# ---------------------------------------------------------------------------
# F / G -- the listing-page delay
# ---------------------------------------------------------------------------

class TestF_ListingPageDelay(ComboTestCase):

    def setUp(self):
        super().setUp()
        # A real range, so the delay branch actually runs. sleep_responsive
        # is stubbed below, so the test records the chosen delay instead of
        # waiting it out.
        F.LISTING_PAGE_GAP_MIN_SECONDS = 300.0
        F.LISTING_PAGE_GAP_MAX_SECONDS = 600.0
        self.waited = []
        self._real_sleep = F.sleep_responsive
        F.sleep_responsive = lambda seconds: self.waited.append(seconds)

    def tearDown(self):
        F.sleep_responsive = self._real_sleep
        super().tearDown()

    def test_a_fresh_delay_is_taken_before_each_next_page(self):
        F.LISTING_PAGE_GAP_MIN_SECONDS = 300.0
        F.LISTING_PAGE_GAP_MAX_SECONDS = 600.0
        pages = {1: [card(i) for i in range(16)],
                 2: [card(i) for i in range(16, 32)],
                 3: [card(i) for i in range(32, 48)],
                 4: []}
        client = PagedClient(pages)
        self.run_combo(client)
        # Three transitions: 1->2, 2->3, 3->4. The stop after page 4 pays none.
        self.assertEqual(len(self.waited), 3,
                         "waited %d time(s): %s" % (len(self.waited),
                                                    self.waited))
        for delay in self.waited:
            self.assertGreaterEqual(delay, 300.0)
            self.assertLessEqual(delay, 600.0)

    def test_the_delays_are_not_all_identical(self):
        F.LISTING_PAGE_GAP_MIN_SECONDS = 300.0
        F.LISTING_PAGE_GAP_MAX_SECONDS = 600.0
        pages = {page: [card(i) for i in range((page - 1) * 10, page * 10)]
                 for page in range(1, 12)}
        client = PagedClient(pages)
        self.run_combo(client)
        self.assertGreater(len(set(self.waited)), 1,
                           "every page waited exactly the same time")

    def test_no_delay_after_the_target_is_reached(self):
        pages = {1: [card(i) for i in range(100)]}
        client = PagedClient(pages)
        self.run_combo(client)
        self.assertEqual(self.waited, [],
                         "waited after the target was already reached")

    def test_no_delay_after_an_empty_page(self):
        pages = {1: [card(i) for i in range(16)], 2: []}
        client = PagedClient(pages)
        self.run_combo(client)
        self.assertEqual(len(self.waited), 1,
                         "only the 1->2 transition should wait")

    def test_no_delay_after_the_page_ceiling(self):
        F.MAX_LISTING_PAGES = 3
        pages = {page: [card(i) for i in range((page - 1) * 10, page * 10)]
                 for page in range(1, 10)}
        client = PagedClient(pages)
        reasons = []
        real = F.report_combo_stop
        F.report_combo_stop = lambda reason, *a: reasons.append(reason)
        try:
            self.run_combo(client)
        finally:
            F.report_combo_stop = real
        self.assertEqual(client.requested, [1, 2, 3])
        self.assertEqual(reasons, [F.STOP_MAX_PAGE_LIMIT])
        self.assertEqual(len(self.waited), 2,
                         "the ceiling must not be followed by a wait")

    def test_no_delay_after_an_access_refusal(self):
        pages = {1: [card(i) for i in range(16)]}
        client = PagedClient(pages, deny_on=2)
        with self.assertRaises(F.AccessBlocked):
            self.run_combo(client)
        self.assertEqual(len(self.waited), 1,
                         "the refused transition waited once before the "
                         "request, and must not wait after the refusal")


# ---------------------------------------------------------------------------
# H -- restart safety
# ---------------------------------------------------------------------------

class TestH_CheckpointResume(ComboTestCase):

    def test_unique_progress_survives_a_restart(self):
        """Stop after page 5, resume, and the count must not reset."""
        pages = {page: [card(i) for i in range((page - 1) * 16, page * 16)]
                 for page in range(1, 12)}

        # First run: refused at page 6, so it ends after five pages.
        client = PagedClient(pages, deny_on=6)
        state = {"current_page": 1, "done_job_urls": []}
        with self.assertRaises(F.AccessBlocked):
            self.run_combo(client, state)

        self.assertEqual(state["combo_signature"],
                         F.combo_signature("Python Developer", "Pune"))
        self.assertEqual(len(state["seen_job_ids"]), 80,
                         "five pages of 16 should have been recorded")

        # Restart: the handler resumes at page 7; the count carries over.
        state["current_page"] = 7
        client2 = PagedClient(pages)
        reasons = []
        real = F.report_combo_stop
        F.report_combo_stop = lambda reason, seen, *a: reasons.append((reason, seen))
        try:
            self.run_combo(client2, state)
        finally:
            F.report_combo_stop = real
        self.assertEqual(client2.requested[0], 7, "resumed at the wrong page")
        self.assertEqual(reasons[0][0], F.STOP_TARGET_REACHED)
        # 80 carried over + page 7's 16 = 96, + page 8's 16 = 112.
        self.assertGreaterEqual(reasons[0][1], 100)
        self.assertLess(reasons[0][1], 130)

    def test_a_different_combo_does_not_inherit_the_count(self):
        state = {"current_page": 1, "done_job_urls": [],
                 "combo_signature": F.combo_signature("Other Role", "Delhi"),
                 "seen_job_ids": ["jk%04d" % i for i in range(99)]}
        restored = F.load_seen_for_combo(
            state, F.combo_signature("Python Developer", "Pune"))
        self.assertEqual(restored, set(),
                         "another combo's identities leaked in")

    def test_an_old_checkpoint_without_the_field_is_safe(self):
        state = {"current_page": 3, "done_job_urls": []}
        restored = F.load_seen_for_combo(
            state, F.combo_signature("Python Developer", "Pune"))
        self.assertEqual(restored, set())

    def test_the_tracked_set_is_cleared_when_the_combo_finishes(self):
        pages = {1: [card(i) for i in range(16)], 2: []}
        client = PagedClient(pages)
        state = {"current_page": 1, "done_job_urls": []}
        self.run_combo(client, state)
        self.assertNotIn("seen_job_ids", state)
        self.assertNotIn("combo_signature", state)
        self.assertEqual(state["current_page"], 1)

    def test_the_tracked_set_is_capped(self):
        state = {}
        F.store_seen_for_combo(state, "sig",
                               {"jk%06d" % i for i in range(F.MAX_TRACKED_JOB_IDS + 500)})
        self.assertEqual(len(state["seen_job_ids"]), F.MAX_TRACKED_JOB_IDS)


# ---------------------------------------------------------------------------
# I -- HTTP 403
# ---------------------------------------------------------------------------

class TestI_AccessRefusal(ComboTestCase):

    def test_the_refusal_is_recorded_and_not_retried(self):
        pages = {1: [card(i) for i in range(16)]}
        client = PagedClient(pages, deny_on=2)
        recorded = []
        real = F.record_failed
        F.record_failed = lambda *a, **kw: recorded.append((a, kw))
        try:
            with self.assertRaises(F.AccessBlocked) as caught:
                self.run_combo(client)
        finally:
            F.record_failed = real

        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(client.requested, [1, 2],
                         "the refused page must not be asked for twice")
        self.assertEqual(len(recorded), 1)
        args, kwargs = recorded[0]
        self.assertEqual(args[0], "listing")
        self.assertIn("Python Developer|Pune|p2", args[1])
        self.assertEqual(kwargs.get("status"), 403)
        self.assertEqual(kwargs.get("recovery_status"), "manual",
                         "a refusal must be flagged for manual review")

    def test_the_checkpoint_survives_the_refusal(self):
        pages = {1: [card(i) for i in range(16)]}
        client = PagedClient(pages, deny_on=2)
        state = {"current_page": 1, "done_job_urls": []}
        with self.assertRaises(F.AccessBlocked):
            self.run_combo(client, state)
        self.assertEqual(len(state["seen_job_ids"]), 16,
                         "page 1's work was lost by the refusal")

    def test_no_alternative_url_is_tried_after_a_refusal(self):
        """Pagination must not treat page 3 as a way around a refused page 2."""
        pages = {page: [card(i) for i in range((page - 1) * 16, page * 16)]
                 for page in range(1, 6)}
        client = PagedClient(pages, deny_on=2)
        with self.assertRaises(F.AccessBlocked):
            self.run_combo(client)
        self.assertEqual(client.requested, [1, 2],
                         "kept requesting pages after a refusal: %s"
                         % client.requested)


# ---------------------------------------------------------------------------
# Configuration contract
# ---------------------------------------------------------------------------

class TestConfigurationIsIndependent(unittest.TestCase):

    def test_the_target_and_the_page_ceiling_are_separate_settings(self):
        self.assertIsInstance(F.TARGET_UNIQUE_JOBS, int)
        self.assertIsInstance(F.MAX_LISTING_PAGES, int)
        self.assertGreater(F.TARGET_UNIQUE_JOBS, 0)
        self.assertGreater(F.MAX_LISTING_PAGES, 0)

    def test_the_page_ceiling_leaves_room_for_the_target(self):
        """At the pagination step, the ceiling must be able to reach the target."""
        reachable = F.MAX_LISTING_PAGES * F.INDEED_PAGE_STEP
        self.assertGreaterEqual(
            reachable, F.TARGET_UNIQUE_JOBS,
            "MAX_LISTING_PAGES=%d at a step of %d can only reach %d unique "
            "jobs, below the target of %d"
            % (F.MAX_LISTING_PAGES, F.INDEED_PAGE_STEP, reachable,
               F.TARGET_UNIQUE_JOBS))

    def test_the_four_delays_are_distinct_settings(self):
        names = ("JOB_GAP", "LISTING_PAGE_GAP", "CITY_GAP", "KEYWORD_GAP")
        for name in names:
            self.assertTrue(hasattr(F, name + "_MIN_SECONDS"), name)
            self.assertTrue(hasattr(F, name + "_MAX_SECONDS"), name)

    def test_a_long_delay_can_be_interrupted(self):
        import threading
        import time as time_module
        F._SHUTDOWN_REQUESTED = False

        def ask_stop():
            time_module.sleep(1)
            F._SHUTDOWN_REQUESTED = True

        threading.Thread(target=ask_stop, daemon=True).start()
        started = time_module.monotonic()
        try:
            with self.assertRaises(KeyboardInterrupt):
                F.sleep_responsive(600)
        finally:
            F._SHUTDOWN_REQUESTED = False
        self.assertLess(time_module.monotonic() - started, 15)


if __name__ == "__main__":
    unittest.main()


class TestRepeatCardsAreNotReprocessed(ComboTestCase):
    """A card repeated from an earlier page is not extracted again.

    The Excel writer would reject it as a duplicate anyway, but only after
    a full extract and a full read of the working file. Skipping it keeps
    "unique jobs found" and "rows accepted" coherent.
    """

    def test_a_repeat_from_an_earlier_page_is_skipped(self):
        page1 = [card(i) for i in range(16)]
        page2 = [card(i) for i in range(10, 26)]       # 10..15 repeat
        client = PagedClient({1: page1, 2: page2, 3: []})
        self.run_combo(client)
        urls = [row["source_url"] for row in self.saved_rows]
        self.assertEqual(len(urls), 26,
                         "processed %d cards for 26 unique jobs" % len(urls))
        self.assertEqual(len(set(urls)), 26)

    def test_a_card_with_no_identity_is_still_processed(self):
        """No identity means no dedup claim -- it must not be dropped."""
        anonymous = {"title": "Python Developer", "company_name": "Co",
                     "formatted_location": "Pune, Maharashtra",
                     "source_url": "https://in.indeed.com/jobs?q=x"}
        client = PagedClient({1: [card(0), anonymous], 2: []})
        self.run_combo(client)
        self.assertEqual(len(self.saved_rows), 2,
                         "the unidentified card was dropped")
