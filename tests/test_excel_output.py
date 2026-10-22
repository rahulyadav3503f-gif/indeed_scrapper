"""Tests for Excel saving, rollover, archive numbering and file placement.

The strict requirement these tests defend: every workbook is written
DIRECTLY into the project root -- never into runtime/output/, output/,
excel_files/, cycle_files/ or archives/.
"""

import os
import shutil
import sys
import tempfile
import unittest

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402
from source_file.indeed_extractor import DETAIL_COLUMNS  # noqa: E402

FORBIDDEN_DIRS = ("runtime", "output", "excel_files", "cycle_files",
                  "archives", "runtime/output")


def sample_row(index=0, **overrides):
    row = {column: "" for column in DETAIL_COLUMNS}
    row.update({
        "job_id": "job-%04d" % index,
        "internal_job_id": "jk%04d" % index,
        "job_title": "Network Engineer",
        "company_name": "Acme Infotech",
        "job_description": "Support the network.",
        "city": "Pune",
        "state": "Maharashtra",
        "country": "India",
        "remote_type": "Onsite",
        "employment_type": "Full-time",
        "apply_url": "https://acme.example/apply/%d" % index,
        "fetched_at": "2026-10-21 10:00:00",
        "is_fake": False,
        "is_active": True,
        "source_url": "https://in.indeed.com/viewjob?jk=jk%04d" % index,
        "source_type": "job_portal",
        "source_platform": "Indeed",
        "score": 30,
    })
    row.update(overrides)
    return row


class ExcelTestCase(unittest.TestCase):
    """Each test gets a throw-away project root."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="indeed-excel-")
        self._old_base = F.BASE_DIR
        self._old_verbose = F.VERBOSE
        F.BASE_DIR = self.tmp
        F.VERBOSE = False

    def tearDown(self):
        F.BASE_DIR = self._old_base
        F.VERBOSE = self._old_verbose
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def read(self, name):
        return pd.read_excel(self.path(name), engine="openpyxl")


class TestColumnStructure(ExcelTestCase):

    def test_a_saved_row_has_the_32_columns_in_order(self):
        F.append_detail_to_excel(sample_row(1))
        frame = self.read(F.DETAIL_OUTPUT_FILE)
        self.assertEqual(list(frame.columns), DETAIL_COLUMNS)
        self.assertEqual(len(frame), 1)

    def test_an_emptied_working_file_keeps_the_32_columns(self):
        for index in range(5):
            F.append_detail_to_excel(sample_row(index), cycle_size=5)
        frame = self.read(F.DETAIL_OUTPUT_FILE)
        self.assertEqual(list(frame.columns), DETAIL_COLUMNS)
        self.assertEqual(len(frame), 0)

    def test_extra_keys_never_reach_the_workbook(self):
        F.append_detail_to_excel(sample_row(2, _apply_url_kind="external"))
        self.assertEqual(list(self.read(F.DETAIL_OUTPUT_FILE).columns),
                         DETAIL_COLUMNS)


class TestFilePlacement(ExcelTestCase):

    def test_everything_lands_directly_in_the_project_root(self):
        for index in range(12):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        F.save_master(pd.DataFrame([{column: "x" for column
                                     in F.MASTER_COLUMNS}]))

        self.assertTrue(os.path.isfile(self.path("current_data.xlsx")))
        self.assertTrue(os.path.isfile(self.path("current_data_cycle1.xlsx")))
        self.assertTrue(os.path.isfile(
            self.path("indeed_it_jobs_by_city.xlsx")))

    def test_no_output_directory_is_ever_created(self):
        for index in range(12):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        for name in FORBIDDEN_DIRS:
            self.assertFalse(os.path.exists(self.path(name)),
                             "%s must not be created" % name)
        subdirs = [entry for entry in os.listdir(self.tmp)
                   if os.path.isdir(self.path(entry))]
        self.assertEqual(subdirs, [], "no subdirectory belongs in the root")

    def test_the_live_project_has_no_excel_output_folder(self):
        for name in FORBIDDEN_DIRS:
            self.assertFalse(
                os.path.exists(os.path.join(PROJECT_ROOT, name)),
                "the real project must not contain %s" % name)


class TestRollover(ExcelTestCase):

    def test_rollover_happens_at_the_batch_size(self):
        for index in range(F.EXCEL_BATCH_SIZE - 1):
            F.append_detail_to_excel(sample_row(index))
        self.assertFalse(os.path.exists(self.path("current_data_cycle1.xlsx")))
        self.assertEqual(len(self.read("current_data.xlsx")),
                         F.EXCEL_BATCH_SIZE - 1)

        F.append_detail_to_excel(sample_row(F.EXCEL_BATCH_SIZE))
        self.assertTrue(os.path.exists(self.path("current_data_cycle1.xlsx")))
        self.assertEqual(len(self.read("current_data_cycle1.xlsx")),
                         F.EXCEL_BATCH_SIZE)
        self.assertEqual(len(self.read("current_data.xlsx")), 0)

    def test_archives_are_numbered_in_sequence(self):
        for index in range(3 * 10):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        for number in range(1, 4):
            name = "current_data_cycle%d.xlsx" % number
            self.assertTrue(os.path.isfile(self.path(name)), name)
            self.assertEqual(len(self.read(name)), 10)
        self.assertEqual(F.load_cycle_num(), 3)

    def test_an_earlier_archive_is_never_overwritten(self):
        for index in range(10):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        first_archive = self.read("current_data_cycle1.xlsx")
        for index in range(100, 110):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        self.assertEqual(list(self.read("current_data_cycle1.xlsx")["job_id"]),
                         list(first_archive["job_id"]))
        self.assertTrue(os.path.isfile(self.path("current_data_cycle2.xlsx")))

    def test_numbering_survives_a_restart(self):
        for index in range(10):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        self.assertEqual(F.load_cycle_num(), 1)

        # "Restart": the counter is re-read from cycle_state.json, not from
        # anything held in memory.
        self.assertEqual(F.get_next_cycle_file(), ("current_data_cycle2.xlsx", 2))
        for index in range(100, 110):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        self.assertTrue(os.path.isfile(self.path("current_data_cycle2.xlsx")))
        self.assertEqual(F.load_cycle_num(), 2)

    def test_a_lost_counter_does_not_destroy_existing_archives(self):
        for index in range(10):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        os.remove(self.path(F.CYCLE_STATE_FILE))        # operator deleted it
        self.assertEqual(F.load_cycle_num(), 0)
        name, number = F.get_next_cycle_file()
        self.assertEqual((name, number), ("current_data_cycle2.xlsx", 2),
                         "an existing archive must never be overwritten")

    def test_the_excel_counter_is_separate_from_the_crawl_cycle(self):
        for index in range(20):
            F.append_detail_to_excel(sample_row(index), cycle_size=10)
        self.assertEqual(F.load_cycle_num(), 2)

        state = F.load_or_open_crawl_cycle(["Pune"],
                                           [("IT Department", "Network Engineer")])
        self.assertEqual(state["crawl_cycle_number"], 1)
        self.assertEqual(F.load_cycle_num(), 2,
                         "opening a crawl cycle must not touch the Excel "
                         "archive counter")
        self.assertNotEqual(F.CYCLE_STATE_FILE, F.CRAWL_CYCLE_STATE_FILE)


class TestSafety(ExcelTestCase):

    def test_duplicates_are_not_written_twice(self):
        F.append_detail_to_excel(sample_row(1))
        self.assertIsNone(F.append_detail_to_excel(sample_row(1)))
        self.assertEqual(len(self.read("current_data.xlsx")), 1)

    def test_a_different_job_with_the_same_title_is_still_saved(self):
        F.append_detail_to_excel(sample_row(1))
        F.append_detail_to_excel(sample_row(2))
        self.assertEqual(len(self.read("current_data.xlsx")), 2)

    def test_non_it_rows_are_rejected(self):
        self.assertIsNone(
            F.append_detail_to_excel(sample_row(3, job_title="Sales Executive")))
        self.assertFalse(os.path.exists(self.path("current_data.xlsx")))

    def test_a_row_with_no_company_is_rejected(self):
        self.assertIsNone(
            F.append_detail_to_excel(sample_row(4, company_name="")))

    def test_a_row_with_no_source_url_is_rejected(self):
        self.assertIsNone(
            F.append_detail_to_excel(sample_row(5, source_url="")))

    def test_illegal_control_characters_are_cleaned_not_fatal(self):
        saved = F.append_detail_to_excel(
            sample_row(6, job_description="Line one\x07\x00 line two"))
        self.assertIsNotNone(saved)
        text = str(self.read("current_data.xlsx")["job_description"].iloc[0])
        self.assertNotIn("\x07", text)
        self.assertIn("Line one", text)

    def test_a_corrupted_working_file_is_replaced_not_fatal(self):
        with open(self.path("current_data.xlsx"), "w") as handle:
            handle.write("this is not a workbook")
        saved = F.append_detail_to_excel(sample_row(7))
        self.assertIsNotNone(saved)
        self.assertEqual(len(self.read("current_data.xlsx")), 1)

    def test_an_interrupted_write_leaves_no_temp_file_behind(self):
        F.append_detail_to_excel(sample_row(8))
        leftovers = [name for name in os.listdir(self.tmp)
                     if name.startswith(".") and name.endswith(".xlsx")]
        self.assertEqual(leftovers, [])

    def test_the_master_workbook_keeps_its_own_columns(self):
        F.save_master(pd.DataFrame([{column: "x" for column
                                     in F.MASTER_COLUMNS}]))
        frame = self.read(F.MASTER_OUTPUT_FILE)
        self.assertEqual(list(frame.columns), F.MASTER_COLUMNS)

    def test_non_dict_input_is_ignored(self):
        self.assertIsNone(F.append_detail_to_excel("not a row"))
        self.assertIsNone(F.append_detail_to_excel(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
