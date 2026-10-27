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
    "MIN_LISTING_PAGES", "MAX_LISTING_PAGES", "RESULTS_PER_PAGE",
    "STOP_ON_PAGE_WITH_NO_NEW_JOBS",
    "WORK_BREAK_MIN_MINUTES", "WORK_BREAK_MAX_MINUTES",
    "WORK_PERIOD_MIN_HOURS", "WORK_PERIOD_MAX_HOURS",
    "REQUEST_GAP_SECONDS",
    "JOB_GAP_MIN_SECONDS", "JOB_GAP_MAX_SECONDS",
    "CITY_GAP_MIN_SECONDS", "CITY_GAP_MAX_SECONDS",
    "KEYWORD_GAP_MIN_SECONDS", "KEYWORD_GAP_MAX_SECONDS",
    "HTTP_CONNECT_TIMEOUT_SECONDS", "HTTP_READ_TIMEOUT_SECONDS",
    "HTTP_USER_AGENT",
    "MAX_RETRIES_5XX", "BACKOFF_5XX_SECONDS", "MAX_RETRIES_NETWORK",
    "BACKOFF_NETWORK_SECONDS", "MAX_RETRIES_429", "BACKOFF_429_SECONDS",
    "DEFAULT_429_WAIT_SECONDS", "RETRY_AFTER_CAP_SECONDS",
    "ACCESS_BREAK_SECONDS", "MAX_BLOCK_COOLDOWNS",
    "CYCLE_STATE_FILE", "CRAWL_CYCLE_STATE_FILE", "CITY_STATE_FILE",
    "ROLE_STATE_FILE", "FAILED_PAGES_FILE",
    "TIMEZONE",
    "INDEED_BASE_URL", "INDEED_SEARCH_URL_TEMPLATE", "INDEED_JOB_URL_TEMPLATE",
    "INDEED_PAGE_STEP", "AUTH_FILE", "USE_SAVED_LOGIN",
    "RESPECT_ROBOTS_DISALLOW", "ROBOTS_DISALLOWED_PREFIXES",
    "COOKIE_BANNER_ACTION",
    "LISTING_RENDER_TIMEOUT_MS", "DETAIL_RENDER_TIMEOUT_MS",
    "STOP_IMMEDIATELY_ON_CTRL_C", "SHOW_JOB_TIMER",
    "BROWSER_REQUIRED", "BROWSER_VISIBLE", "BROWSER_HEADLESS",
    "BROWSER_SLOW_MO_MS", "BROWSER_WIDTH", "BROWSER_HEIGHT",
    "BROWSER_CHANNEL", "BROWSER_NAV_TIMEOUT_MS", "BROWSER_KEEP_OPEN_SECONDS",
    "BROWSER_DEMO_PAGES",
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
                     "save_indeed_login.py", "requirements.txt",
                     ".gitignore"):
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

    def test_live_collection_settings_are_coherent(self):
        """Live mode must have the urls it needs, and obey the robots rules.

        The project collects from the live site (the operator's decision, the
        same way the Naukri scraper does). What this test protects is that
        turning it on cannot leave the collector without a url to build, and
        that the robots-disallowed prefixes are never quietly emptied.
        """
        if F.LIVE_COLLECTION_ENABLED:
            self.assertTrue(F.INDEED_BASE_URL.startswith("https://"))
            self.assertIn("{keyword}", F.INDEED_SEARCH_URL_TEMPLATE)
            self.assertIn("{city}", F.INDEED_SEARCH_URL_TEMPLATE)
            self.assertIn("{start}", F.INDEED_SEARCH_URL_TEMPLATE)
            self.assertIn("{job_key}", F.INDEED_JOB_URL_TEMPLATE)
        self.assertTrue(F.RESPECT_ROBOTS_DISALLOW,
                        "the robots rules must stay respected")
        for prefix in ("/applystart", "/rc/", "/rpc/", "/my/"):
            self.assertIn(prefix, F.ROBOTS_DISALLOWED_PREFIXES, prefix)
        self.assertEqual(F.COOKIE_BANNER_ACTION, "reject",
                         "the collector declines non-essential cookies; it "
                         "never accepts on the operator's behalf")

    def test_no_credential_handling_in_the_login_saver(self):
        """save_indeed_login.py must never type or store a credential.

        Only EXECUTABLE code is examined: the file's prose explains that the
        operator types their own password, and that sentence is the point of
        the file rather than a violation of it.
        """
        path = os.path.join(PROJECT_ROOT, "save_indeed_login.py")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                text = ast.get_docstring(node)
                if text:
                    docstrings.add(text)

        code = code_lines(source)
        for text in docstrings:
            code = code.replace(text, "")

        for forbidden in (".fill(", ".type(", "keyboard.insert_text",
                          "set_password", "credentials"):
            self.assertNotIn(forbidden, code,
                             "the login saver must not handle credentials: %s"
                             % forbidden)
        self.assertIn("storage_state", code,
                      "it saves the session the operator created")


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


class TestResetScript(unittest.TestCase):
    """reset.sh must be safe: an allowlist, never a wildcard sweep."""

    RESET = os.path.join(PROJECT_ROOT, "reset.sh")

    def body(self):
        with open(self.RESET, encoding="utf-8") as handle:
            return handle.read()

    def test_it_exists_and_is_executable(self):
        self.assertTrue(os.path.isfile(self.RESET))
        self.assertTrue(os.access(self.RESET, os.X_OK), "./reset.sh must be +x")

    def test_it_is_valid_shell(self):
        result = subprocess.run(["bash", "-n", self.RESET],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_it_supports_the_documented_flags(self):
        body = self.body()
        for flag in ("--dry-run", "--backup", "--yes", "--force", "--help"):
            self.assertIn(flag, body, flag)

    def test_the_saved_login_and_the_sources_are_protected(self):
        body = self.body()
        protected = body[body.index("PROTECTED=("):]
        protected = protected[:protected.index(")")]
        for keep in ("indeed_auth.json", "find_indeed_job.py",
                     "save_indeed_login.py", "new_mylib.py",
                     "target_audience_roles.py", "source_file",
                     "tests", "docs", "venv", ".git", "reset.sh"):
            self.assertIn(keep, protected, keep)

    def test_it_removes_every_runtime_file_the_main_script_writes(self):
        """Whatever the configuration names, reset.sh must know about it."""
        body = self.body()
        for name in (F.CYCLE_STATE_FILE, F.CRAWL_CYCLE_STATE_FILE,
                     F.CITY_STATE_FILE, F.ROLE_STATE_FILE,
                     F.FAILED_PAGES_FILE, F.DETAIL_OUTPUT_FILE,
                     F.MASTER_OUTPUT_FILE):
            self.assertIn(name, body,
                          "%s is written by the scraper but reset.sh does "
                          "not clean it" % name)
        self.assertIn("current_data_cycle*.xlsx", body, "Excel rotation files")

    def test_it_never_removes_the_saved_login(self):
        body = self.body()
        self.assertNotIn("rm -rf indeed_auth", body)
        self.assertNotIn("rm indeed_auth", body)

    def test_it_refuses_to_run_outside_the_project(self):
        body = self.body()
        self.assertIn('if [ ! -f "find_indeed_job.py" ]', body)

    def test_it_has_no_unguarded_recursive_delete(self):
        """Every rm must act on a collected target, not on a bare glob."""
        for line in self.body().splitlines():
            stripped = line.strip()
            if not stripped.startswith("rm "):
                continue
            self.assertIn('"$t"', stripped,
                          "unexpected rm target: %s" % stripped)

    def test_a_dry_run_changes_nothing(self):
        before = sorted(os.listdir(PROJECT_ROOT))
        result = subprocess.run([self.RESET, "--dry-run"],
                                cwd=PROJECT_ROOT, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing", result.stdout.lower())
        self.assertEqual(sorted(os.listdir(PROJECT_ROOT)), before,
                         "a dry run must not touch the project")

    def test_the_help_text_renders(self):
        result = subprocess.run([self.RESET, "--help"],
                                cwd=PROJECT_ROOT, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WHAT IT NEVER TOUCHES", result.stdout)
        self.assertIn("indeed_auth.json", result.stdout)

    def test_an_unknown_option_is_rejected(self):
        result = subprocess.run([self.RESET, "--delete-everything"],
                                cwd=PROJECT_ROOT, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option", result.stderr)

    def test_the_running_check_requires_a_real_python_process(self):
        """A command line that merely mentions the script is not a run.

        Without this the check refused resets because its own parent shell
        matched `pgrep -f find_indeed_job.py`.
        """
        body = self.body()
        self.assertIn("*python*find_indeed_job.py*", body)
        self.assertIn('[ "$candidate" = "$$" ] && continue', body)


class TestDocumentation(unittest.TestCase):

    def test_the_documents_exist(self):
        for name in ("README.md", "TECHNICAL_REPORT.md",
                     "FEATURE_COMPARISON.md", "TEST_REPORT.md"):
            path = os.path.join(PROJECT_ROOT, "docs", name)
            self.assertTrue(os.path.isfile(path), name)
            self.assertGreater(os.path.getsize(path), 500, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
