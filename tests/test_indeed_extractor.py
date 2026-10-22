"""Tests for source_file/indeed_extractor.py -- the 32-column row."""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from source_file.indeed_extractor import (         # noqa: E402
    DETAIL_COLUMNS,
    build_source_url,
    city_from_location_text,
    clean_description,
    extract_internal_job_id,
    extract_job_details,
    generate_job_id,
    normalise_city,
    parse_experience,
    parse_posted_date,
    parse_salary,
    resolve_company,
    resolve_employment_type,
    resolve_location,
    resolve_remote_type,
    resolve_skills,
    row_for_excel,
    validate_row,
)
from new_mylib import city_to_state                # noqa: E402

RAW_JOB = {
    "job_key": "a1b2c3d4e5f6",
    "title": "Network Support Engineer",
    "company_name": "Acme Infotech Pvt Ltd",
    "company_website": "https://acme.example",
    "description": "<h3>About</h3><p>Support the LAN.</p>"
                   "<ul><li>Cisco</li><li>Routing</li></ul>",
    "skills": ["Cisco", "Routing", "LAN"],
    "formatted_location": "Pune, Maharashtra 411014",
    "employment_type": "Full-time",
    "salary_text": "₹4,00,000 - ₹6,00,000 a year",
    "experience": "2 - 5 years",
    "posted_date": "2026-10-01T08:30:00Z",
    "apply_url": "https://acme.example/careers/apply/77",
    "source_url": "https://in.indeed.com/viewjob?jk=a1b2c3d4e5f6",
}

INPUT_ROW = {
    "role_category": "IT Department",
    "job_title": "Network Support Engineer",
    "search_keyword": "Network Support Engineer",
    "city": "Mumbai",            # the SEARCH city, deliberately different
    "location": "Mumbai",
    "state": "Maharashtra",
    "source_type": "job_portal",
}


def build(raw=None, input_row=None):
    return extract_job_details(raw if raw is not None else RAW_JOB,
                               input_row if input_row is not None else INPUT_ROW,
                               state_resolver=city_to_state)


class TestSchema(unittest.TestCase):

    def test_exactly_the_32_columns_in_order(self):
        self.assertEqual(len(DETAIL_COLUMNS), 32)
        row = row_for_excel(build())
        self.assertEqual(list(row.keys()), DETAIL_COLUMNS)

    def test_platform_and_type_are_fixed(self):
        row = build()
        self.assertEqual(row["source_platform"], "Indeed")
        self.assertEqual(row["source_type"], "job_portal")
        self.assertEqual(row["country"], "India")
        self.assertEqual(row["score"], 30)
        self.assertIs(row["is_fake"], False)
        self.assertIs(row["is_active"], True)

    def test_source_url_is_per_job_and_never_a_constant(self):
        first = build()["source_url"]
        other = dict(RAW_JOB, job_key="zzz999", source_url="")
        second = build(other)["source_url"]
        self.assertTrue(first.startswith("https://in.indeed.com/viewjob?jk="))
        self.assertNotEqual(first, second)
        self.assertIn("zzz999", second)

    def test_internal_job_id_comes_from_the_job_key(self):
        self.assertEqual(build()["internal_job_id"], "a1b2c3d4e5f6")
        self.assertEqual(
            extract_internal_job_id({}, "https://in.indeed.com/viewjob?jk=QQ1"),
            "QQ1")

    def test_job_id_is_stable_for_the_same_job(self):
        first = generate_job_id("T", "Indeed", "2026-01-01 00:00:00", "k1")
        second = generate_job_id("T", "Indeed", "2026-06-06 06:06:06", "k1")
        self.assertEqual(first, second)
        self.assertNotEqual(first, generate_job_id("T", "Indeed", "x", "k2"))

    def test_private_diagnostic_keys_never_reach_excel(self):
        row = build()
        self.assertIn("_apply_url_kind", row)
        self.assertNotIn("_apply_url_kind", row_for_excel(row))


class TestDescription(unittest.TestCase):

    def test_structure_is_preserved(self):
        text = build()["job_description"]
        self.assertIn("About", text)
        self.assertIn("- Cisco", text)
        self.assertIn("- Routing", text)
        self.assertNotIn("<", text)

    def test_page_furniture_is_removed(self):
        text = clean_description(
            "<p>Real content.</p><p>Apply Now</p><p>Save this job</p>"
            "<p>We use cookies to improve your experience</p>")
        self.assertIn("Real content.", text)
        self.assertNotIn("Apply Now", text)
        self.assertNotIn("Save this job", text)
        self.assertNotIn("cookies", text)

    def test_scripts_and_entities(self):
        text = clean_description(
            "<script>alert(1)</script><p>R&amp;D team &nbsp;here</p>")
        self.assertNotIn("alert", text)
        self.assertIn("R&D team", text)

    def test_empty_input_gives_empty_output(self):
        self.assertEqual(clean_description(None), "")
        self.assertEqual(clean_description("   "), "")


class TestSalary(unittest.TestCase):
    """Absolute rupees, never a bare "12" for 12 lakhs, never 0."""

    def test_rupee_range(self):
        self.assertEqual(parse_salary("₹4,00,000 - ₹6,00,000 a year")[:3],
                         (400000, 600000, "INR"))

    def test_lakhs_are_expanded(self):
        self.assertEqual(parse_salary("8 - 14 Lakhs P.A.")[:2],
                         (800000, 1400000))
        self.assertEqual(parse_salary("12 LPA")[:2], (1200000, 1200000))

    def test_crores_are_expanded(self):
        self.assertEqual(parse_salary("1 - 2 Cr")[:2], (10000000, 20000000))

    def test_unit_inheritance_only_below_the_ceiling(self):
        self.assertEqual(parse_salary("8 - 12 Lakhs")[:2], (800000, 1200000))
        self.assertEqual(parse_salary("50,000 - 2 Lacs")[:2], (50000, 200000))

    def test_a_single_figure_fills_both_bounds(self):
        self.assertEqual(parse_salary("₹35,000 a month")[:2], (35000, 35000))

    def test_period_is_recorded_not_converted(self):
        self.assertEqual(parse_salary("₹35,000 a month")[3], "MONTHLY")
        self.assertEqual(parse_salary("₹6,00,000 per annum")[3], "YEARLY")

    def test_reversed_ranges_are_ordered(self):
        self.assertEqual(parse_salary("₹9,00,000 - ₹4,00,000")[:2],
                         (400000, 900000))

    def test_nothing_is_invented_when_no_figure_is_published(self):
        for text in ("Not disclosed", "Negotiable", "Best in industry",
                     "As per industry standards", "", None, "Competitive"):
            low, high, _currency, _period = parse_salary(text)
            self.assertEqual(low, "", repr(text))
            self.assertEqual(high, "", repr(text))

    def test_a_blank_salary_is_blank_in_the_row_not_zero(self):
        row = build(dict(RAW_JOB, salary_text="Not disclosed"))
        self.assertEqual(row["salary_min"], "")
        self.assertEqual(row["salary_max"], "")
        self.assertNotEqual(row["salary_min"], 0)

    def test_structured_salary_block_is_used(self):
        row = build(dict(RAW_JOB, salary_text="",
                         baseSalary={"currency": "INR",
                                     "value": {"minValue": 500000,
                                               "maxValue": 900000,
                                               "unitText": "YEAR"}}))
        self.assertEqual((row["salary_min"], row["salary_max"]),
                         (500000, 900000))

    def test_an_empty_structured_block_falls_back_to_its_text(self):
        row = build(dict(RAW_JOB, salary_text="",
                         baseSalary={"currency": "INR",
                                     "value": {"minValue": None,
                                               "maxValue": None,
                                               "value": "7 - 9 Lacs P.A."}}))
        self.assertEqual((row["salary_min"], row["salary_max"]),
                         (700000, 900000))


class TestExperience(unittest.TestCase):

    def test_range(self):
        self.assertEqual(parse_experience("2 - 5 years")[1:], (2, 5))

    def test_minimum_only(self):
        self.assertEqual(parse_experience("Minimum 3 years required")[1:],
                         (3, ""))

    def test_fresher(self):
        self.assertEqual(parse_experience("Freshers welcome")[1:], (0, 1))

    def test_nothing_stated(self):
        self.assertEqual(parse_experience("", None)[1:], ("", ""))

    def test_the_row_carries_the_parsed_numbers(self):
        row = build()
        self.assertEqual(row["experience_min_years"], 2)
        self.assertEqual(row["experience_max_years"], 5)


class TestLocation(unittest.TestCase):
    """city and state describe the JOB, not the search."""

    def test_the_jobs_own_city_wins_over_the_search_city(self):
        row = build()
        self.assertEqual(row["city"], "Pune")
        self.assertEqual(row["state"], "Maharashtra")
        self.assertNotEqual(row["city"], INPUT_ROW["city"])

    def test_the_search_city_is_used_only_when_the_job_has_no_location(self):
        raw = {key: value for key, value in RAW_JOB.items()
               if key != "formatted_location"}
        row = build(raw)
        self.assertEqual(row["city"], "Mumbai")

    def test_a_remote_job_does_not_borrow_the_search_city(self):
        row = build(dict(RAW_JOB, formatted_location="Remote"))
        self.assertNotEqual(row["city"], "Mumbai")

    def test_pin_codes_and_areas_are_trimmed(self):
        self.assertEqual(normalise_city("Pune (Viman Nagar)"), "Pune")
        self.assertEqual(city_from_location_text("Pune, Maharashtra 411014"),
                         "Pune")

    def test_aliases_are_normalised(self):
        self.assertEqual(normalise_city("Bangalore"), "Bengaluru")
        self.assertEqual(normalise_city("Gurgaon"), "Gurugram")
        self.assertEqual(normalise_city("New Delhi"), "Delhi")

    def test_structured_location_objects_are_read(self):
        location, city, _state = resolve_location(
            {"jobLocation": {"addressLocality": "Chennai",
                             "addressRegion": "Tamil Nadu"}},
            state_resolver=city_to_state)
        self.assertEqual(city, "Chennai")
        self.assertIn("Chennai", location)

    def test_remote_type(self):
        self.assertEqual(resolve_remote_type("Remote"), "Remote")
        self.assertEqual(resolve_remote_type("Hybrid work"), "Hybrid")
        self.assertEqual(resolve_remote_type("Pune, Maharashtra"), "Onsite")


class TestOtherFields(unittest.TestCase):

    def test_employment_type(self):
        self.assertEqual(resolve_employment_type("Full-time"), "Full-time")
        self.assertEqual(resolve_employment_type("Contractual / Temporary"),
                         "Contract")
        self.assertEqual(resolve_employment_type("Internship"), "Internship")
        self.assertEqual(resolve_employment_type(""), "Full-time")

    def test_skills_are_joined_and_de_duplicated(self):
        self.assertEqual(resolve_skills({"skills": ["Linux", "linux", "AWS"]}),
                         "Linux; AWS")
        self.assertEqual(resolve_skills({"skills": "Linux, AWS; Azure"}),
                         "Linux; AWS; Azure")
        self.assertEqual(resolve_skills({}), "")

    def test_dates(self):
        self.assertEqual(parse_posted_date("2026-10-01T08:30:00Z"),
                         "2026-10-01 08:30:00")
        self.assertTrue(parse_posted_date("3 days ago"))
        self.assertTrue(parse_posted_date(1760000000))
        self.assertEqual(parse_posted_date("sometime last summer"), "")

    def test_company_name_noise_is_removed_but_never_invented(self):
        self.assertEqual(resolve_company({"company_name": "Acme Ltd"}),
                         "Acme Ltd")
        self.assertEqual(resolve_company({"company_name": "Hiring Acme Ltd"}),
                         "Acme Ltd")
        self.assertEqual(resolve_company({}), "")

    def test_an_indeed_company_profile_is_not_a_company_website(self):
        row = build(dict(RAW_JOB,
                         company_website="https://in.indeed.com/cmp/Acme"))
        self.assertEqual(row["company_website"], "")

    def test_apply_url_is_the_employer_destination(self):
        row = build()
        self.assertEqual(row["apply_url"],
                         "https://acme.example/careers/apply/77")
        self.assertNotEqual(row["apply_url"], row["source_url"])

    def test_source_url_is_not_copied_into_a_missing_apply_url(self):
        raw = {key: value for key, value in RAW_JOB.items()
               if key != "apply_url"}
        row = build(raw)
        self.assertEqual(row["apply_url"], "")


class TestValidation(unittest.TestCase):

    def test_a_good_row_validates(self):
        ok, reason = validate_row(build())
        self.assertTrue(ok, reason)

    def test_a_row_with_no_company_is_rejected(self):
        raw = {key: value for key, value in RAW_JOB.items()
               if key != "company_name"}
        ok, reason = validate_row(build(raw, {"role_category": "IT Department"}))
        self.assertFalse(ok)
        self.assertIn("company", reason)

    def test_a_row_with_no_title_is_rejected(self):
        raw = {key: value for key, value in RAW_JOB.items() if key != "title"}
        ok, reason = validate_row(build(raw, {"role_category": "IT Department"}))
        self.assertFalse(ok)
        self.assertIn("title", reason)

    def test_a_row_with_no_source_url_is_rejected(self):
        ok, reason = validate_row(build({"title": "T",
                                         "company_name": "C"}, INPUT_ROW))
        self.assertFalse(ok)
        self.assertIn("source_url", reason)

    def test_non_dict_input_does_not_crash_the_extractor(self):
        row = extract_job_details(None, INPUT_ROW)
        self.assertEqual(row["job_title"], "Network Support Engineer")
        self.assertEqual(row["source_url"], "")

    def test_build_source_url_without_any_identifier(self):
        self.assertEqual(build_source_url({}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
