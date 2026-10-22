"""Contract tests for the project's shape.

These assert the STRICT structural rules from the brief, so a later edit
cannot quietly break them:

  * the three Indeed modules live in source_file/ and nowhere else;
  * indeed_config.py does not exist;
  * every editable setting is at the top of find_indeed_job.py;
  * nothing imports from the Naukri project at runtime;
  * the service files exist and run from this directory.
"""

import ast
import os
import re
import subprocess
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402

MAIN_FILE = os.path.join(PROJECT_ROOT, "find_indeed_job.py")
SOURCE_DIR = os.path.join(PROJECT_ROOT, "source_file")
INDEED_MODULES = ("indeed_client.py", "indeed_extractor.py",
                  "indeed_apply_url.py")

# Every setting the brief requires to be in the main file.
REQUIRED_SETTINGS = [
    "rank", "num_sys",
    "LIVE_COLLECTION_ENABLED",
    "NIGHT_PAUSE_ENABLED", "NIGHT_START_HOUR", "NIGHT_END_HOUR",
    "KEYWORD_SHUFFLE_ENABLED",
    "DETAIL_OUTPUT_FILE", "MASTER_OUTPUT_FILE", "EXCEL_BATCH_SIZE",
    "CYCLE_COOLDOWN_MINUTES",
    "MAX_LISTING_PAGES", "RESULTS_PER_PAGE",
    "WORK_BREAK_MIN_MINUTES", "WORK_BREAK_MAX_MINUTES",
    "WORK_PERIOD_MIN_HOURS", "WORK_PERIOD_MAX_HOURS",
    "REQUEST_GAP_SECONDS", "JOB_GAP_SECONDS", "CITY_GAP_SECONDS",
    "KEYWORD_GAP_SECONDS",
    "HTTP_CONNECT_TIMEOUT_SECONDS", "HTTP_READ_TIMEOUT_SECONDS",
    "HTTP_USER_AGENT",
    "MAX_RETRIES_5XX", "BACKOFF_5XX_SECONDS", "MAX_RETRIES_NETWORK",
    "BACKOFF_NETWORK_SECONDS", "MAX_RETRIES_429", "BACKOFF_429_SECONDS",
    "DEFAULT_429_WAIT_SECONDS", "RETRY_AFTER_CAP_SECONDS",
    "ACCESS_BREAK_SECONDS", "MAX_BLOCK_COOLDOWNS",
    "CYCLE_STATE_FILE", "CRAWL_CYCLE_STATE_FILE", "CITY_STATE_FILE",
    "ROLE_STATE_FILE", "FAILED_PAGES_FILE",
    "TIMEZONE",
    "BROWSER_REQUIRED", "BROWSER_HEADLESS",
]


TESTS_DIR = os.path.join(PROJECT_ROOT, "tests")


def main_source():
    with open(MAIN_FILE, encoding="utf-8") as handle:
        return handle.read()


def project_files(suffixes=(".py",), include_tests=False):
    """Every source file in the project.

    The tests directory is skipped by default: these tests necessarily
    quote the very strings they forbid elsewhere ("from indeed_config",
    "check_naukri"), and a test file is not shipped code.
    """
    for folder, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [name for name in dirs
                   if name not in (".git", "__pycache__", "venv", ".venv")]
        if not include_tests and os.path.abspath(folder).startswith(TESTS_DIR):
            continue
        for name in files:
            if name.endswith(suffixes) or name in ("indeed",):
                yield os.path.join(folder, name)


def code_lines(text):
    """Executable lines only -- comments do not count as behaviour."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


class TestLayout(unittest.TestCase):

    def test_the_main_script_is_where_it_should_be(self):
        self.assertTrue(os.path.isfile(MAIN_FILE))

    def test_the_three_modules_are_inside_source_file(self):
        for name in INDEED_MODULES:
            self.assertTrue(os.path.isfile(os.path.join(SOURCE_DIR, name)),
                            "source_file/%s is missing" % name)
        self.assertTrue(os.path.isfile(os.path.join(SOURCE_DIR,
                                                    "__init__.py")))

    def test_they_are_not_duplicated_in_the_project_root(self):
        for name in INDEED_MODULES:
            self.assertFalse(os.path.exists(os.path.join(PROJECT_ROOT, name)),
                             "%s must not also exist in the root" % name)

    def test_indeed_config_does_not_exist_anywhere(self):
        for folder, _dirs, files in os.walk(PROJECT_ROOT):
            if ".git" in folder or "__pycache__" in folder:
                continue
            self.assertNotIn("indeed_config.py", files,
                             "indeed_config.py found in %s" % folder)

    def test_the_package_imports_cleanly(self):
        import source_file
        self.assertTrue(hasattr(source_file, "IndeedClient"))
        self.assertTrue(hasattr(source_file, "extract_job_details"))
        self.assertTrue(hasattr(source_file, "extract_apply_url"))

    def test_the_supporting_libraries_are_project_local(self):
        for name in ("new_mylib.py", "target_audience_roles.py",
                     "requirements.txt", ".gitignore"):
            self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, name)),
                            name)


class TestConfigurationLivesInTheMainFile(unittest.TestCase):

    def test_every_required_setting_is_defined_in_the_main_file(self):
        tree = ast.parse(main_source())
        assigned = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.add(target.id)
        missing = [name for name in REQUIRED_SETTINGS if name not in assigned]
        self.assertEqual(missing, [], "missing from find_indeed_job.py")

    def test_the_settings_sit_near_the_top_of_the_file(self):
        source = main_source()
        # rindex: the phrase also appears in the block's own explanatory
        # comment, and the real banner is the last occurrence.
        block = source[source.index("INDEED JOB SCRAPER CONFIGURATION"):
                       source.rindex("END OF CONFIGURATION")]
        for name in REQUIRED_SETTINGS:
            self.assertTrue(
                re.search(r"(?m)^%s\s*=" % re.escape(name), block),
                "%s is not assigned inside the configuration block" % name)

    def test_the_configuration_block_really_is_at_the_top(self):
        source = main_source()
        self.assertLess(source.rindex("END OF CONFIGURATION"),
                        len(source) // 4,
                        "the configuration block must stay near the top")

    def test_nothing_imports_a_configuration_module(self):
        self.assertNotIn("import indeed_config", main_source())
        for path in project_files():
            with open(path, encoding="utf-8") as handle:
                body = handle.read()
            self.assertNotIn("from indeed_config", body, path)
            self.assertNotIn("import indeed_config", body, path)

    def test_the_client_takes_its_settings_from_the_main_file(self):
        config = F.build_client_config()
        self.assertEqual(config.max_listing_pages, F.MAX_LISTING_PAGES)
        self.assertEqual(config.results_per_page, F.RESULTS_PER_PAGE)
        self.assertEqual(config.request_timeout_seconds,
                         F.HTTP_READ_TIMEOUT_SECONDS)
        self.assertEqual(config.max_retries_5xx, F.MAX_RETRIES_5XX)
        self.assertEqual(config.live_enabled, F.LIVE_COLLECTION_ENABLED)

    def test_no_credential_is_written_into_source(self):
        source = main_source()
        for marker in ("api_key =", "API_KEY =", "password", "PASSWORD ="):
            for line in source.splitlines():
                if marker in line and "ENV" not in line and "env" not in line:
                    self.fail("possible credential in source: %s" % line)

    def test_live_collection_is_off_by_default(self):
        self.assertFalse(F.LIVE_COLLECTION_ENABLED,
                         "live collection must stay disabled until an "
                         "authorised source is configured")


class TestNaukriIsolation(unittest.TestCase):

    def test_no_file_imports_from_the_naukri_project(self):
        for path in project_files(include_tests=True):
            if not path.endswith(".py"):
                continue
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = ",".join(alias.name for alias in node.names)
                self.assertNotIn("naukri", module.lower(),
                                 "%s imports %s" % (path, module))

    def test_no_shipped_file_points_at_the_naukri_directory(self):
        for path in project_files(suffixes=(".py", ".sh", ".service")):
            with open(path, encoding="utf-8", errors="ignore") as handle:
                body = handle.read()
            self.assertNotIn("check_naukri", body, path)
            self.assertNotIn("naukri_script", body, path)

    def test_every_loaded_project_module_lives_inside_this_project(self):
        for name in ("find_indeed_job", "new_mylib", "target_audience_roles",
                     "source_file.indeed_client",
                     "source_file.indeed_extractor",
                     "source_file.indeed_apply_url"):
            module = sys.modules.get(name)
            if module is None:
                continue
            self.assertTrue(
                os.path.abspath(module.__file__).startswith(PROJECT_ROOT),
                "%s is loaded from outside the project: %s"
                % (name, module.__file__))

    def test_the_service_name_is_independent(self):
        """The unit's DIRECTIVES must not mention Naukri.

        Its comments may (they explain that the two services are separate);
        only the executable directives are checked.
        """
        with open(os.path.join(PROJECT_ROOT, "indeed.service"),
                  encoding="utf-8") as handle:
            unit = handle.read()
        self.assertIn(PROJECT_ROOT, unit)
        directives = code_lines(unit).lower()
        self.assertNotIn("naukri", directives)
        self.assertIn("description=", directives)


class TestServiceScripts(unittest.TestCase):

    def test_the_control_script_exists_and_is_executable(self):
        control = os.path.join(PROJECT_ROOT, "indeed")
        self.assertTrue(os.path.isfile(control))
        self.assertTrue(os.access(control, os.X_OK), "./indeed must be +x")

    def test_the_installer_exists_and_is_executable(self):
        installer = os.path.join(PROJECT_ROOT, "install_service.sh")
        self.assertTrue(os.path.isfile(installer))
        self.assertTrue(os.access(installer, os.X_OK))

    def test_the_control_script_supports_every_documented_command(self):
        with open(os.path.join(PROJECT_ROOT, "indeed"),
                  encoding="utf-8") as handle:
            body = handle.read()
        for command in ("start", "stop", "restart", "status", "log"):
            self.assertIn(command, body)

    def test_the_control_script_is_valid_shell(self):
        result = subprocess.run(["bash", "-n",
                                 os.path.join(PROJECT_ROOT, "indeed")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_installer_is_valid_shell(self):
        result = subprocess.run(["bash", "-n",
                                 os.path.join(PROJECT_ROOT,
                                              "install_service.sh")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_unit_stops_the_service_gracefully(self):
        with open(os.path.join(PROJECT_ROOT, "indeed.service"),
                  encoding="utf-8") as handle:
            unit = handle.read()
        self.assertIn("KillSignal=SIGTERM", unit)
        self.assertIn("WorkingDirectory=%s" % PROJECT_ROOT, unit)


class TestDocumentation(unittest.TestCase):

    def test_the_documents_exist(self):
        for name in ("README.md", "TECHNICAL_REPORT.md",
                     "FEATURE_COMPARISON.md", "TEST_REPORT.md"):
            path = os.path.join(PROJECT_ROOT, "docs", name)
            self.assertTrue(os.path.isfile(path), name)
            self.assertGreater(os.path.getsize(path), 500, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
