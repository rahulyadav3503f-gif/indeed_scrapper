"""Tests for HTTP errors, the failed-work queue, pauses and shutdown."""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402
from source_file.indeed_client import (            # noqa: E402
    FixtureTransport,
    IndeedAccessDenied,
    IndeedClient,
    IndeedClientConfig,
    IndeedDataError,
    IndeedNetworkError,
    IndeedRateLimited,
    IndeedServerError,
)

PAIRS = [("IT Department", "Network Engineer")]
CITIES = ["Pune", "Mumbai"]


class FailingTransport(FixtureTransport):
    """A transport that raises a chosen error on every search."""

    def __init__(self, error):
        super().__init__()
        self.error = error
        self.calls = 0

    def search(self, keyword, city, page, config):
        self.calls += 1
        raise self.error


class ErrorTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="indeed-errors-")
        self._saved = {name: getattr(F, name) for name in
                       ("BASE_DIR", "VERBOSE", "REQUEST_GAP_SECONDS",
                        "JOB_GAP_SECONDS", "CITY_GAP_SECONDS",
                        "KEYWORD_GAP_SECONDS", "NIGHT_PAUSE_ENABLED",
                        "_SHUTDOWN_REQUESTED")}
        F.BASE_DIR = self.tmp
        F.VERBOSE = False
        F.REQUEST_GAP_SECONDS = 0
        F.JOB_GAP_SECONDS = 0
        F.CITY_GAP_SECONDS = 0
        F.KEYWORD_GAP_SECONDS = 0
        F.NIGHT_PAUSE_ENABLED = False
        F._SHUTDOWN_REQUESTED = False

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(F, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def client_raising(self, error):
        return IndeedClient(IndeedClientConfig(verbose=False,
                                               request_gap_seconds=0),
                            transport=FailingTransport(error))


class TestAccessDenied(ErrorTestCase):
    """403: recognise the refusal, record it, never work around it."""

    def test_a_403_stops_the_combo_and_raises_access_blocked(self):
        client = self.client_raising(
            IndeedAccessDenied("denied", url="u", status=403))
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        with self.assertRaises(F.AccessBlocked) as caught:
            F.run_combo(client, state, "IT Department", "Network Engineer",
                        "Pune", {})
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(client.transport.calls, 1,
                         "a refusal must not be retried immediately")

    def test_a_403_is_queued_as_manual_not_as_retryable_work(self):
        client = self.client_raising(
            IndeedAccessDenied("denied", url="u", status=403))
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        with self.assertRaises(F.AccessBlocked):
            F.run_combo(client, state, "IT Department", "Network Engineer",
                        "Pune", {})
        queue = F.load_failed_pages()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["recovery_status"], "manual")
        self.assertEqual(queue[0]["http_status"], 403)
        self.assertEqual(F.queue_eligible(), [],
                         "a denied endpoint is never auto-retried")

    def test_a_session_wide_refusal_is_reported_as_such(self):
        denied = IndeedAccessDenied("denied", url="u", status=403,
                                    scope="session_wide")
        client = self.client_raising(denied)
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        with self.assertRaises(F.AccessBlocked) as caught:
            F.run_combo(client, state, "IT Department", "Network Engineer",
                        "Pune", {})
        self.assertEqual(caught.exception.scope, "session_wide")


class TestRateLimit(ErrorTestCase):
    """429: respect Retry-After, respect the quota."""

    def test_the_servers_retry_after_is_carried_through(self):
        client = self.client_raising(
            IndeedRateLimited("quota", url="u", retry_after=321))
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        with self.assertRaises(F.AccessBlocked) as caught:
            F.run_combo(client, state, "IT Department", "Network Engineer",
                        "Pune", {})
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.wait_seconds, 321)

    def test_a_missing_retry_after_falls_back_to_the_configured_wait(self):
        client = self.client_raising(IndeedRateLimited("quota", url="u"))
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        with self.assertRaises(F.AccessBlocked):
            F.run_combo(client, state, "IT Department", "Network Engineer",
                        "Pune", {})
        entry = F.load_failed_pages()[0]
        self.assertEqual(entry["http_status"], 429)
        delay = (datetime.strptime(entry["next_retry_at"], "%Y-%m-%d %H:%M:%S")
                 - datetime.strptime(entry["failed_at"], "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(delay, timedelta(seconds=F.DEFAULT_429_WAIT_SECONDS))


class TestServerAndNetworkErrors(ErrorTestCase):
    """5xx and network failures are queued, and the run carries on."""

    def test_a_5xx_does_not_stop_the_run(self):
        client = self.client_raising(
            IndeedServerError("boom", url="u", status=503, attempts=4))
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        saved = F.run_combo(client, state, "IT Department",
                            "Network Engineer", "Pune", {})
        self.assertEqual(saved, 0)
        entry = F.load_failed_pages()[0]
        self.assertEqual(entry["http_status"], 503)
        self.assertEqual(entry["recovery_status"], "pending")

    def test_a_network_failure_is_queued_for_a_later_retry(self):
        client = self.client_raising(IndeedNetworkError("timeout", url="u",
                                                        attempts=3))
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        F.run_combo(client, state, "IT Department", "Network Engineer",
                    "Pune", {})
        self.assertEqual(len(F.load_failed_pages()), 1)

    def test_unreadable_data_is_queued_not_fatal(self):
        client = self.client_raising(IndeedDataError("not json"))
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        F.run_combo(client, state, "IT Department", "Network Engineer",
                    "Pune", {})
        self.assertEqual(len(F.load_failed_pages()), 1)

    def test_an_incomplete_job_record_skips_only_that_job(self):
        class HalfBrokenTransport(FixtureTransport):
            def search(self, keyword, city, page, config):
                return {"jobs": [
                    {"job_key": "ok1", "title": "Network Engineer",
                     "company_name": "Acme Ltd", "description": "d" * 200,
                     "source_url": "https://in.indeed.com/viewjob?jk=ok1"},
                    {"job_key": "bad1", "description": "d" * 200},   # no title
                ], "total": 2}

        client = IndeedClient(IndeedClientConfig(verbose=False,
                                                 request_gap_seconds=0),
                              transport=HalfBrokenTransport())
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        saved = F.run_combo(client, state, "IT Department",
                            "Network Engineer", "Pune", {})
        self.assertEqual(saved, 1, "the good job is still saved")


class TestFailedQueue(ErrorTestCase):
    """The queue escalates; it never loops."""

    def test_the_same_failure_updates_one_entry_instead_of_growing(self):
        for _ in range(3):
            F.record_failed("listing", "ref-1", "5xx", status=503)
        queue = F.load_failed_pages()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["attempts"], 3)

    def test_different_statuses_are_separate_entries(self):
        F.record_failed("listing", "ref-1", "5xx", status=503)
        F.record_failed("listing", "ref-1", "timeout", status=None)
        self.assertEqual(len(F.load_failed_pages()), 2)

    def test_only_due_entries_are_eligible(self):
        F.record_failed("listing", "later", "5xx", status=503,
                        retry_delay_seconds=3600)
        self.assertEqual(F.queue_eligible(), [])
        F.record_failed("listing", "now", "5xx", status=500,
                        retry_delay_seconds=0)
        self.assertEqual([entry["reference"] for entry in F.queue_eligible()],
                         ["now"])

    def test_deferrals_escalate_and_then_require_a_human(self):
        F.record_failed("listing", "ref-1", "5xx", status=503,
                        retry_delay_seconds=0)
        entry = F.queue_eligible()[0]
        for _ in range(F.QUEUE_MAX_DEFERRALS):
            F.defer_queue_entry(entry, "still failing")
        stored = F.load_failed_pages()[0]
        self.assertEqual(stored["recovery_status"], "manual")
        self.assertEqual(F.queue_eligible(), [])

    def test_recovered_work_is_marked_resolved(self):
        F.record_failed("listing", "ref-1", "5xx", status=503)
        F.mark_failed_resolved("ref-1")
        self.assertEqual(F.load_failed_pages()[0]["recovery_status"],
                         "resolved")
        self.assertEqual(F.failed_queue_summary(), (0, 0, 0, 1))

    def test_a_damaged_queue_file_never_stops_the_run(self):
        with open(self.path(F.FAILED_PAGES_FILE), "w") as handle:
            handle.write("{not json")
        self.assertEqual(F.load_failed_pages(), [])
        F.record_failed("listing", "ref-1", "5xx", status=503)
        self.assertEqual(len(F.load_failed_pages()), 1)

    def test_the_queue_is_visited_only_every_few_combos(self):
        F._COMBOS_SINCE_QUEUE = 0
        results = [F.queue_visit_due() for _ in range(F.QUEUE_CHECK_EVERY_COMBOS)]
        self.assertEqual(results.count(True), 1)
        self.assertTrue(results[-1])


class TestNightPauseAndBreaks(ErrorTestCase):

    def test_night_window_detection(self):
        """Derived from the CONFIGURED hours, not from assumed ones.

        The window is an operator setting (it is 23:00-05:00 today, it was
        00:00-06:00 before), so the expectation is computed from it rather
        than hardcoded -- otherwise editing the configuration breaks a test
        that is not about the configuration.
        """
        F.NIGHT_PAUSE_ENABLED = True
        start = int(F.NIGHT_START_HOUR) % 24
        end = int(F.NIGHT_END_HOUR) % 24

        # One hour into the window, whichever hour that is.
        inside_hour = (start + 1) % 24
        inside = datetime(2026, 10, 21, inside_hour, 0, 0)
        expected = ((end - inside_hour) % 24) * 3600
        self.assertEqual(F.night_seconds_remaining(inside), expected)

        # An hour after it ends is outside it.
        outside = datetime(2026, 10, 21, (end + 1) % 24, 0, 0)
        self.assertEqual(F.night_seconds_remaining(outside), 0)

    def test_the_window_may_cross_midnight(self):
        F.NIGHT_PAUSE_ENABLED = True
        saved = (F.NIGHT_START_HOUR, F.NIGHT_END_HOUR)
        F.NIGHT_START_HOUR, F.NIGHT_END_HOUR = 22, 6
        try:
            self.assertGreater(
                F.night_seconds_remaining(datetime(2026, 10, 21, 23, 0)), 0)
            self.assertGreater(
                F.night_seconds_remaining(datetime(2026, 10, 21, 1, 0)), 0)
            self.assertEqual(
                F.night_seconds_remaining(datetime(2026, 10, 21, 12, 0)), 0)
        finally:
            F.NIGHT_START_HOUR, F.NIGHT_END_HOUR = saved

    def test_night_pause_can_be_switched_off(self):
        F.NIGHT_PAUSE_ENABLED = False
        self.assertEqual(F.night_seconds_remaining(datetime(2026, 10, 21, 2, 0)),
                         0)

    def test_the_pacing_gate_raises_during_the_night(self):
        F.NIGHT_PAUSE_ENABLED = True
        original = F.night_seconds_remaining
        F.night_seconds_remaining = lambda now=None: 1800
        try:
            with self.assertRaises(F.ScheduledPause) as caught:
                F.pacing_gate()
            self.assertEqual(caught.exception.seconds, 1800)
            self.assertIn("night", caught.exception.reason)
        finally:
            F.night_seconds_remaining = original

    def test_a_break_is_due_after_the_work_period(self):
        F.reset_work_timer()
        F._WORK_PERIOD_HOURS = 0.0001                # ~0.36 seconds
        import time
        time.sleep(0.5)
        with self.assertRaises(F.ScheduledPause) as caught:
            F.pacing_gate()
        self.assertGreaterEqual(caught.exception.seconds,
                                F.WORK_BREAK_MIN_MINUTES * 60)
        self.assertLessEqual(caught.exception.seconds,
                             F.WORK_BREAK_MAX_MINUTES * 60)

    def test_the_work_period_is_drawn_from_the_configured_range(self):
        for _ in range(20):
            hours = F.pick_work_period_hours()
            self.assertGreaterEqual(hours, F.WORK_PERIOD_MIN_HOURS)
            self.assertLessEqual(hours, F.WORK_PERIOD_MAX_HOURS)

    def test_waiting_does_not_count_as_working_time(self):
        import time
        F.reset_work_timer()
        time.sleep(0.2)
        worked = F.work_seconds()
        F.work_clock_pause()
        time.sleep(0.3)
        self.assertAlmostEqual(F.work_seconds(), worked, delta=0.05)
        F.work_clock_resume()
        time.sleep(0.1)
        self.assertGreater(F.work_seconds(), worked)

    def test_nested_pauses_stop_the_clock_once(self):
        F.reset_work_timer()
        F.work_clock_pause()
        F.work_clock_pause()
        F.work_clock_resume()
        self.assertIsNone(F._WORK_MARK, "still paused after one resume")
        F.work_clock_resume()
        self.assertIsNotNone(F._WORK_MARK)


class TestGracefulShutdown(ErrorTestCase):

    def test_a_shutdown_request_unwinds_at_the_next_safe_boundary(self):
        F._SHUTDOWN_REQUESTED = True
        with self.assertRaises(KeyboardInterrupt):
            F.pacing_gate()

    def test_the_signal_handler_saves_the_checkpoint(self):
        saved = []
        F._CHECKPOINT_SAVER = lambda: saved.append(True)
        F.install_signal_handlers()
        import signal
        handler = signal.getsignal(signal.SIGTERM)
        self.assertTrue(callable(handler))
        try:
            handler(signal.SIGTERM, None)
            self.assertTrue(F._SHUTDOWN_REQUESTED)
            self.assertEqual(saved, [True])
        finally:
            F._CHECKPOINT_SAVER = None
            F._SHUTDOWN_REQUESTED = False
            signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def test_an_interrupted_run_keeps_every_row_it_had_saved(self):
        import pandas as pd

        class StoppingTransport(FixtureTransport):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def search(self, keyword, city, page, config):
                self.calls += 1
                if self.calls > 1:
                    raise KeyboardInterrupt("stop")
                return {"jobs": [
                    {"job_key": "k1", "title": "Network Engineer",
                     "company_name": "Acme Ltd", "description": "d" * 200,
                     "source_url": "https://in.indeed.com/viewjob?jk=k1"},
                ], "total": 1}

        client = IndeedClient(IndeedClientConfig(verbose=False,
                                                 request_gap_seconds=0),
                              transport=StoppingTransport())
        state = F.load_or_open_crawl_cycle(CITIES, PAIRS)
        try:
            F.run_crawl_cycle(client, state, {})
        except KeyboardInterrupt:
            pass
        frame = pd.read_excel(self.path(F.DETAIL_OUTPUT_FILE))
        self.assertEqual(len(frame), 1)
        self.assertTrue(os.path.exists(self.path(F.CRAWL_CYCLE_STATE_FILE)))


class TestImmediateStopOnCtrlC(ErrorTestCase):
    """Ctrl+C ends the process at once, and a repeat press does nothing."""

    def setUp(self):
        super().setUp()
        import signal as signal_module
        self.signal = signal_module
        self._saved_handlers = {
            sig: signal_module.getsignal(sig)
            for sig in (signal_module.SIGINT, signal_module.SIGTERM)
        }
        self._saved_exit = F._exit
        self._saved_saver = F._CHECKPOINT_SAVER
        self._saved_killer = F._CLIENT_KILLER
        self._saved_closer = F._CLIENT_CLOSER

        self.events = []
        F._exit = lambda code: self.events.append(("exit", code))
        F._CHECKPOINT_SAVER = lambda: self.events.append(("saved", True))
        F._CLIENT_KILLER = lambda: self.events.append(("killed", True))

    def tearDown(self):
        F._exit = self._saved_exit
        F._CHECKPOINT_SAVER = self._saved_saver
        F._CLIENT_KILLER = self._saved_killer
        F._CLIENT_CLOSER = self._saved_closer
        for sig, handler in self._saved_handlers.items():
            try:
                self.signal.signal(sig, handler)
            except (ValueError, OSError):            # pragma: no cover
                pass
        super().tearDown()

    def fire(self, signum):
        handler = self.signal.getsignal(signum)
        self.assertTrue(callable(handler), "no handler installed")
        handler(signum, None)
        return handler

    def test_ctrl_c_saves_kills_and_exits_immediately(self):
        F.STOP_IMMEDIATELY_ON_CTRL_C = True
        F.install_signal_handlers()
        self.fire(self.signal.SIGINT)

        self.assertIn(("saved", True), self.events)
        self.assertIn(("killed", True), self.events)
        self.assertIn(("exit", 130), self.events)
        self.assertEqual([name for name, _ in self.events].count("exit"), 1,
                         "the process ends exactly once")

    def test_the_browser_is_killed_not_politely_closed(self):
        """A polite close cannot run while a navigation is in flight."""
        F.STOP_IMMEDIATELY_ON_CTRL_C = True
        F._CLIENT_CLOSER = lambda: self.events.append(("closed", True))
        F.install_signal_handlers()
        self.fire(self.signal.SIGINT)
        self.assertIn(("killed", True), self.events)
        self.assertNotIn(("closed", True), self.events)

    def test_a_repeated_ctrl_c_is_ignored(self):
        F.STOP_IMMEDIATELY_ON_CTRL_C = True
        F.install_signal_handlers()
        self.fire(self.signal.SIGINT)
        # Both signals are disarmed by the handler itself, so a second press
        # cannot re-enter it and print a second message.
        self.assertEqual(self.signal.getsignal(self.signal.SIGINT),
                         self.signal.SIG_IGN)
        self.assertEqual(self.signal.getsignal(self.signal.SIGTERM),
                         self.signal.SIG_IGN)
        # SIG_IGN is an operating-system level ignore: a second Ctrl+C never
        # reaches Python at all, so no second message can be printed.
        self.assertEqual([name for name, _ in self.events].count("exit"), 1)

    def test_no_further_work_is_queued_after_ctrl_c(self):
        F.STOP_IMMEDIATELY_ON_CTRL_C = True
        F.install_signal_handlers()
        self.fire(self.signal.SIGINT)
        self.assertEqual(F.load_failed_pages(), [],
                         "an immediate stop must not queue a retry")

    def test_a_service_stop_still_finishes_the_job_in_hand(self):
        """SIGTERM keeps the graceful path, so systemd never cuts a job."""
        F.STOP_IMMEDIATELY_ON_CTRL_C = True
        F.install_signal_handlers()
        self.fire(self.signal.SIGTERM)

        self.assertTrue(F._SHUTDOWN_REQUESTED)
        self.assertIn(("saved", True), self.events)
        self.assertNotIn(("exit", 143), self.events)
        with self.assertRaises(KeyboardInterrupt):
            F.pacing_gate()                          # unwinds at the boundary

    def test_a_second_service_stop_ends_the_process(self):
        F.install_signal_handlers()
        handler = self.signal.getsignal(self.signal.SIGTERM)
        handler(self.signal.SIGTERM, None)           # first: graceful
        F.install_signal_handlers()                  # re-arm, as a run would
        self.signal.getsignal(self.signal.SIGTERM)(self.signal.SIGTERM, None)
        self.assertIn(("exit", 143), self.events)

    def test_ctrl_c_can_be_made_graceful_by_configuration(self):
        F.STOP_IMMEDIATELY_ON_CTRL_C = False
        try:
            F.install_signal_handlers()
            self.fire(self.signal.SIGINT)
            self.assertNotIn(("exit", 130), self.events)
            self.assertIn(("saved", True), self.events)
            self.assertTrue(F._SHUTDOWN_REQUESTED)
        finally:
            F.STOP_IMMEDIATELY_ON_CTRL_C = True

    def test_stop_now_survives_a_missing_saver_and_killer(self):
        F._CHECKPOINT_SAVER = None
        F._CLIENT_KILLER = None
        F.stop_now(7)
        self.assertIn(("exit", 7), self.events)

    def test_killing_an_unstarted_transport_is_a_no_op(self):
        from source_file.indeed_client import BrowserTransport
        transport = BrowserTransport(IndeedClientConfig(
            browser_visible=True, verbose=False))
        transport.kill()                             # must not raise
        self.assertFalse(transport.started)
        self.assertIsNone(transport.browser)

    def test_a_browser_can_be_opened_again_after_a_kill(self):
        """A kill must not make the process unable to open another browser.

        Killing the driver leaves Playwright's thread-local event loop
        behind, and the next sync_playwright().start() then fails with "you
        are using Playwright Sync API inside the asyncio loop". Measured
        before the fix: the relaunch failed; after it, it works.
        """
        import shutil as shutil_module
        if not shutil_module.which("Xvfb") and not os.environ.get("DISPLAY"):
            self.skipTest("needs a display or Xvfb")
        try:
            import playwright                        # noqa: F401
        except ImportError:
            self.skipTest("needs Playwright")

        from source_file.indeed_client import BrowserTransport

        def config():
            return IndeedClientConfig(live_enabled=False,
                                      browser_visible=True,
                                      browser_headless=True,
                                      request_gap_seconds=0, verbose=False)

        first = BrowserTransport(config())
        first.start()
        first.kill()

        second = BrowserTransport(config())
        try:
            second.start()
            self.assertTrue(second.browser.is_connected(),
                            "the process could not open a browser after a kill")
        finally:
            second.stop()
            second.cleanup_pages()

    def test_kill_only_touches_the_driver_it_launched(self):
        """It must not sweep up unrelated child processes.

        An earlier version SIGKILLed every direct child of the collector,
        which could kill a subprocess that had nothing to do with the
        browser.
        """
        import subprocess
        from source_file.indeed_client import BrowserTransport

        innocent = subprocess.Popen([sys.executable, "-c",
                                     "import time; time.sleep(30)"])
        try:
            transport = BrowserTransport(IndeedClientConfig(
                browser_visible=True, verbose=False))
            transport.started = True                 # pretend it launched
            transport._driver_pid = None             # but owns no driver
            transport.kill()
            self.assertIsNone(innocent.poll(),
                              "an unrelated child process was killed")
        finally:
            innocent.kill()
            innocent.wait(timeout=10)


class TestInternetCheck(ErrorTestCase):

    def test_the_check_can_be_disabled(self):
        saved = F.INTERNET_CHECK_ENABLED
        F.INTERNET_CHECK_ENABLED = False
        try:
            self.assertTrue(F.is_internet_available())
            F.wait_for_internet()                    # returns immediately
        finally:
            F.INTERNET_CHECK_ENABLED = saved

    def test_waiting_for_the_internet_does_not_count_as_work(self):
        saved = F.is_internet_available
        F.is_internet_available = lambda: True
        try:
            F.reset_work_timer()
            F.wait_for_internet()
            self.assertEqual(F._WORK_PAUSE_DEPTH, 0)
        finally:
            F.is_internet_available = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
