"""Tests for source_file/indeed_apply_url.py.

The four cases from the brief, plus the rule that matters most: the job
detail url is NEVER copied into apply_url.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from source_file.indeed_apply_url import (         # noqa: E402
    apply_url_or_blank,
    classify_apply_url,
    extract_apply_url,
    is_indeed_host,
    is_unrelated_destination,
    normalise_candidate,
    resolve_relative,
    validate_apply_url,
)

SOURCE_URL = "https://in.indeed.com/viewjob?jk=EXAMPLE123"


class TestCaseAExternalEmployer(unittest.TestCase):
    """CASE A: an external employer application is stored as-is."""

    def test_external_url_is_kept(self):
        result = extract_apply_url(
            {"apply_url": "https://company.example/careers/job/123"},
            source_url=SOURCE_URL)
        self.assertEqual(result.url, "https://company.example/careers/job/123")
        self.assertEqual(result.kind, "external")

    def test_external_beats_an_indeed_hosted_alternative(self):
        result = extract_apply_url({
            "apply_url": "https://company.example/apply/9",
            "indeed_apply_url": "https://in.indeed.com/applystart?jk=X",
        }, source_url=SOURCE_URL)
        self.assertEqual(result.url, "https://company.example/apply/9")
        self.assertEqual(result.kind, "external")

    def test_field_name_variants_are_understood(self):
        for field in ("applyUrl", "external_apply_url", "employer_apply_url",
                      "application_url", "directApplyUrl"):
            result = extract_apply_url({field: "https://acme.example/go"},
                                       source_url=SOURCE_URL)
            self.assertEqual(result.url, "https://acme.example/go", field)

    def test_a_nested_apply_block_is_read(self):
        result = extract_apply_url(
            {"apply": {"url": "https://acme.example/nested"}},
            source_url=SOURCE_URL)
        self.assertEqual(result.url, "https://acme.example/nested")

    def test_a_fragment_is_dropped_but_the_query_is_kept(self):
        result = extract_apply_url(
            {"apply_url": "https://acme.example/go?src=indeed#form"},
            source_url=SOURCE_URL)
        self.assertEqual(result.url, "https://acme.example/go?src=indeed")


class TestCaseBIndeedHosted(unittest.TestCase):
    """CASE B: a real Indeed-hosted application destination is stored."""

    def test_applystart_is_an_application(self):
        result = extract_apply_url(
            {"indeed_apply_url": "https://in.indeed.com/applystart?jk=ABC"},
            source_url=SOURCE_URL)
        self.assertEqual(result.kind, "indeed_hosted")
        self.assertEqual(result.url, "https://in.indeed.com/applystart?jk=ABC")

    def test_smartapply_host_is_an_application(self):
        result = extract_apply_url(
            {"apply_url": "https://smartapply.indeed.com/beta/indeedapply/"
                          "form/resume"},
            source_url=SOURCE_URL)
        self.assertEqual(result.kind, "indeed_hosted")

    def test_classification_helper(self):
        self.assertEqual(
            classify_apply_url("https://in.indeed.com/applystart?jk=A"),
            "indeed_hosted")
        self.assertEqual(classify_apply_url("https://acme.example/apply"),
                         "external")
        self.assertEqual(classify_apply_url(""), "missing")


class TestCaseCMissing(unittest.TestCase):
    """CASE C: no application destination -> blank, never the source_url."""

    def test_no_field_at_all_gives_a_blank_url(self):
        result = extract_apply_url({"title": "Network Engineer"},
                                   source_url=SOURCE_URL)
        self.assertEqual(result.url, "")
        self.assertEqual(result.kind, "missing")
        self.assertFalse(result.is_present)

    def test_the_source_url_is_never_copied_into_apply_url(self):
        result = extract_apply_url({"apply_url": SOURCE_URL},
                                   source_url=SOURCE_URL)
        self.assertEqual(result.url, "")
        self.assertIn("detail url", result.reason)

    def test_a_viewjob_url_is_not_an_application_even_for_another_job(self):
        result = extract_apply_url(
            {"apply_url": "https://in.indeed.com/viewjob?jk=OTHER"},
            source_url=SOURCE_URL)
        self.assertEqual(result.url, "")

    def test_empty_placeholders_are_treated_as_missing(self):
        for value in ("", "   ", None, "null", "N/A", "#", "undefined"):
            result = extract_apply_url({"apply_url": value},
                                       source_url=SOURCE_URL)
            self.assertEqual(result.url, "", repr(value))

    def test_convenience_helper_returns_a_blank_string(self):
        self.assertEqual(apply_url_or_blank({}, SOURCE_URL), "")


class TestCaseDInvalid(unittest.TestCase):
    """CASE D: unsupported or malformed urls are rejected."""

    def test_javascript_links_are_rejected(self):
        result = extract_apply_url({"apply_url": "javascript:void(0)"},
                                   source_url=SOURCE_URL)
        self.assertEqual(result.url, "")
        self.assertEqual(result.kind, "invalid")
        self.assertIn("javascript", result.reason)

    def test_data_links_are_rejected(self):
        result = extract_apply_url(
            {"apply_url": "data:text/html;base64,PHNjcmlwdD4="},
            source_url=SOURCE_URL)
        self.assertEqual(result.url, "")
        self.assertEqual(result.kind, "invalid")

    def test_other_unsupported_schemes_are_rejected(self):
        for candidate in ("mailto:jobs@acme.example", "tel:+911234567890",
                          "ftp://files.acme.example/jd.pdf",
                          "vbscript:msgbox", "file:///etc/passwd"):
            url, reason = validate_apply_url(candidate)
            self.assertEqual(url, "", candidate)
            self.assertTrue(reason)

    def test_malformed_urls_are_rejected(self):
        for candidate in ("https://", "http://nohost", "https:// space.example",
                          "not a url at all"):
            url, _reason = validate_apply_url(candidate)
            self.assertEqual(url, "", candidate)

    def test_an_absurdly_long_url_is_rejected(self):
        url, reason = validate_apply_url("https://acme.example/" + "a" * 2500)
        self.assertEqual(url, "")
        self.assertIn("longer than", reason)

    def test_http_and_https_are_both_supported(self):
        for candidate in ("http://acme.example/apply",
                          "https://acme.example/apply"):
            url, _ = validate_apply_url(candidate)
            self.assertEqual(url, candidate)


class TestRelativeUrls(unittest.TestCase):
    """Permitted relative urls resolve against the job's own page."""

    def test_absolute_path_resolves(self):
        result = extract_apply_url({"apply_url": "/applystart?jk=ABC"},
                                   source_url=SOURCE_URL)
        self.assertEqual(result.url, "https://in.indeed.com/applystart?jk=ABC")
        self.assertEqual(result.kind, "indeed_hosted")

    def test_protocol_relative_resolves_to_https(self):
        self.assertEqual(resolve_relative("//acme.example/apply"),
                         "https://acme.example/apply")

    def test_a_relative_url_with_no_base_is_rejected(self):
        result = extract_apply_url({"apply_url": "apply/now"}, source_url="")
        self.assertEqual(result.url, "")
        self.assertEqual(result.kind, "invalid")


class TestUnrelatedButtons(unittest.TestCase):
    """Links that merely sit next to "Apply" are not captured."""

    def test_company_profile_and_search_links_are_rejected(self):
        for candidate in ("https://in.indeed.com/cmp/Acme-Ltd",
                          "https://in.indeed.com/jobs?q=network+engineer",
                          "https://in.indeed.com/salaries/network-engineer",
                          "https://www.linkedin.com/shareArticle?u=x",
                          "https://in.indeed.com/account/login"):
            self.assertTrue(is_unrelated_destination(candidate), candidate)
            result = extract_apply_url({"apply_url": candidate},
                                       source_url=SOURCE_URL)
            self.assertEqual(result.url, "", candidate)

    def test_a_genuine_employer_apply_link_is_not_rejected(self):
        self.assertFalse(
            is_unrelated_destination("https://acme.example/careers/apply/12"))


class TestHelpers(unittest.TestCase):

    def test_indeed_host_detection(self):
        self.assertTrue(is_indeed_host("https://in.indeed.com/applystart"))
        self.assertTrue(is_indeed_host("https://smartapply.indeed.com/x"))
        self.assertFalse(is_indeed_host("https://indeed.acme.example/x"))
        self.assertFalse(is_indeed_host("https://acme.example/indeed"))

    def test_candidate_normalisation(self):
        self.assertEqual(normalise_candidate('  "https://a.example/x"  '),
                         "https://a.example/x")
        self.assertEqual(normalise_candidate(["", "https://a.example/y"]),
                         "https://a.example/y")
        self.assertEqual(normalise_candidate({"url": "https://a.example/z"}),
                         "https://a.example/z")
        self.assertEqual(normalise_candidate("https://a.example/\nx"),
                         "https://a.example/x")

    def test_result_objects_compare_on_url_and_kind(self):
        first = extract_apply_url({"apply_url": "https://a.example/x"})
        second = extract_apply_url({"applyUrl": "https://a.example/x"})
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
