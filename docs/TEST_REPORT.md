# Test Report — Independent Indeed Job Collector

Project: `/home/ubuntu/check_indeed`
Date of run: 21 October 2026
Python: 3.10.12 · pandas 2.3.3 · openpyxl 3.1.5
Framework: the standard library's `unittest` (no pytest required)

---

## 1. How to run the tests

```bash
cd /home/ubuntu/check_indeed
source venv/bin/activate
python3 -m unittest discover -s tests -p "test_*.py" -v
```

One file at a time:

```bash
python3 -m unittest tests.test_indeed_client -v
python3 -m unittest tests.test_indeed_extractor -v
python3 -m unittest tests.test_indeed_apply_url -v
python3 -m unittest tests.test_city_distribution -v
python3 -m unittest tests.test_keyword_cycles -v
python3 -m unittest tests.test_excel_output -v
python3 -m unittest tests.test_checkpoint_recovery -v
python3 -m unittest tests.test_error_handling -v
python3 -m unittest tests.test_project_structure -v
```

---

## 2. Result

```
Ran 228 tests in 30.9s

OK
```

| Metric | Count |
|--------|-------|
| Total tests | **228** |
| Passed | **228** |
| Failed | **0** |
| Errors | **0** |
| Skipped | **0** |

### Per file

| File | Tests | Result | Time |
|------|-------|--------|------|
| `tests/test_indeed_client.py` | 26 | OK | 0.01 s |
| `tests/test_indeed_extractor.py` | 46 | OK | 0.03 s |
| `tests/test_indeed_apply_url.py` | 27 | OK | 0.01 s |
| `tests/test_city_distribution.py` | 22 | OK | 0.05 s |
| `tests/test_keyword_cycles.py` | 17 | OK | 0.32 s |
| `tests/test_excel_output.py` | 22 | OK | 33.2 s¹ |
| `tests/test_checkpoint_recovery.py` | 15 | OK | 2.3 s |
| `tests/test_error_handling.py` | 29 | OK | 2.8 s |
| `tests/test_project_structure.py` | 24 | OK | 0.2 s |

¹ The Excel file is read and rewritten on every append, exactly as in
production, and one test deliberately drives a full 100-row rollover.

`tests/test_project_structure.py` is an addition to the eight files listed
in the brief. It holds the structural contract tests the brief asks for
(items 1–6, 30 and 31 of Phase 14) which belong to no single module.

---

## 3. Coverage against the required test categories

| # | Required category | Where | Notes |
|---|-------------------|-------|-------|
| 1 | Main script imports correctly | `test_project_structure` | imported by every test file |
| 2 | `source_file` package imports | `test_project_structure.TestLayout` | `IndeedClient`, `extract_job_details`, `extract_apply_url` re-exported |
| 3 | No `indeed_config.py` dependency | `TestLayout`, `TestConfigurationLivesInTheMainFile` | file absent anywhere; no file imports it |
| 4 | All configuration in the main script | `TestConfigurationLivesInTheMainFile` | 43 required settings parsed out with `ast`; all must sit inside the configuration block, which must stay in the first quarter of the file |
| 5 | Three modules inside `source_file` | `TestLayout` | |
| 6 | No duplicate root-level modules | `TestLayout` | |
| 7 | City distribution | `test_city_distribution` (22) | 110 cities; 23/23/23/23/18; every city exactly once for 1–12 systems; no overlap; invalid ranks |
| 8 | Role selection | `test_keyword_cycles.TestRolePlan` | 68 + 17 = 85; no broad department keyword; non-IT filter with IT override |
| 9 | Keyword shuffling | `test_keyword_cycles.TestShuffle` | permutation; reproducible per cycle; differs per rank; can be switched off |
| 10 | Complete cycle progression | `TestCycleProgression` | every keyword × city visited exactly once, cities before the next keyword |
| 11 | Job listing extraction | `test_indeed_client` | raw dicts, totals, determinism, pagination, empty pages |
| 12 | Job detail extraction | `test_indeed_extractor` (46) | 32 columns in order, salary, experience, city/state, dates, skills, description structure |
| 13 | Application URL extraction | `test_indeed_apply_url` (27) | all four cases |
| 14 | External application URLs | `TestCaseAExternalEmployer` | field variants, nested blocks, preference over Indeed-hosted |
| 15 | Indeed-hosted applications | `TestCaseBIndeedHosted` | `/applystart`, `smartapply.indeed.com` |
| 16 | Missing application URLs | `TestCaseCMissing` | blank; the `source_url` is never copied |
| 17 | Invalid application URLs | `TestCaseDInvalid` | `javascript:`, `data:`, `mailto:`, malformed, over-long |
| 18 | Excel column structure | `test_excel_output.TestColumnStructure` | 32 columns preserved, including after an emptying |
| 19 | Excel saved in the project root | `TestFilePlacement` | and **no** subdirectory is created, in a temp root and in the real one |
| 20 | Excel archive naming | `TestRollover` | `current_data_cycleN.xlsx` |
| 21 | 100-job rollover | `TestRollover.test_rollover_happens_at_the_batch_size` | 99 rows → no archive; the 100th → archive + empty working file |
| 22 | Excel numbering after restart | `TestRollover`, `test_checkpoint_recovery.TestArchiveNumberRecovery` | counter re-read from disk; an existing archive is never overwritten |
| 23 | Checkpoint persistence | `test_checkpoint_recovery.TestStateFiles` | atomic writes, no temp left behind, damaged files tolerated |
| 24 | Resume after interruption | `TestResume` | a simulated crash mid-cycle resumes the same cycle and repeats only the interrupted combo |
| 25 | Duplicate job prevention | `TestDuplicatePrevention`, `test_excel_output.TestSafety` | re-crawling a combo adds no rows; master sighting counts still increment |
| 26 | HTTP error handling | `test_error_handling` (29) | 403, 429 with and without `Retry-After`, 5xx, network, bad data, incomplete jobs |
| 27 | Night pause | `TestNightPauseAndBreaks` | inside/outside the window, crossing midnight, switchable, the gate raises |
| 28 | Periodic breaks | `TestNightPauseAndBreaks` | break due after the work period; range respected; waits excluded from working time; nested pauses |
| 29 | Graceful shutdown | `TestGracefulShutdown` | SIGTERM handler saves the checkpoint; the gate unwinds; saved rows survive |
| 30 | Independent service management | `test_project_structure.TestServiceScripts` | `./indeed` and `install_service.sh` exist, are executable, pass `bash -n`; the unit stops with SIGTERM from this directory |
| 31 | Naukri project isolation | `TestNaukriIsolation` | no module imports anything "naukri"; no shipped file references the Naukri directories; every loaded project module resolves inside this project; the unit's directives name only `indeed` |

---

## 4. End-to-end run (not a unit test — a real run)

A full crawl cycle was executed against the offline fixture source with a
reduced plan (3 keywords × 6 cities, page cap 3, pacing gaps zeroed for
speed; everything else at its configured value):

```
[DONE] CRAWL CYCLE 1 COMPLETE -- every keyword crawled across all 6 rank cities.
exit code 0
```

What it produced, all directly in `/home/ubuntu/check_indeed`:

| File | Contents |
|------|----------|
| `current_data_cycle1.xlsx` | 100 rows, 32 columns in schema order |
| `current_data_cycle2.xlsx` | 100 rows, 32 columns in schema order |
| `current_data.xlsx` | 80 rows (the batch in progress) |
| `indeed_it_jobs_by_city.xlsx` | the master index |
| `cycle_state.json` | `{"cycle_num": 2}` |
| `crawl_cycle_state.json` | cycle 1, `cycle_complete: true` |
| `state_city.json`, `state_role.json` | last position, for reading |

Checks made on that output:

* 280 rows written, **280 unique `source_url` values** — no duplicates.
* Column order identical to `DETAIL_COLUMNS` in all three workbooks.
* `apply_url` populated for external and Indeed-hosted applications and
  **blank** for the jobs with no application destination (33 of the first
  100 rows) — the `source_url` was never copied into it.
* `city` / `state` resolved from the job (`Delhi` → `Delhi NCR`,
  `Pune` → `Maharashtra`), not from a fixed value.
* Salaries stored in absolute rupees (450000–720000, 35000–55000,
  800000–1400000) and blank where the fixture said "Not disclosed".
* No subdirectory was created by the run.

A **second run in a separate process** then proved continuation across a
restart:

```
archive counter before: 2
[CYCLE] CRAWL CYCLE 2 opened -- keywords shuffled once for this cycle.
      Excel batch complete -> current_data_cycle3.xlsx | rows: 100
[DONE] CRAWL CYCLE 2 COMPLETE
archive counter after: 3
```

The crawl cycle advanced 1 → 2 with a freshly shuffled keyword order, and
the Excel archive counter continued 2 → 3 rather than restarting — the two
counters moved independently, exactly as they must.

---

## 5. What is NOT tested, and why

* **Live Indeed collection.** There is no authorised self-serve Indeed
  job-search API (see `docs/TECHNICAL_REPORT.md` §9), so no test makes a
  request to Indeed and **no claim is made that live collection works**.
  `HttpTransport`'s configuration guards, retry policy and error typing are
  tested; its behaviour against a real authorised endpoint can only be
  verified once one exists.
* **systemd itself.** The unit file, the installer and the control script
  are checked for correctness and shell validity; installing a system
  service was not performed, as it needs root and would change the
  machine's configuration.
* **Multi-day scheduling.** Night pauses and work breaks are tested through
  their decision functions with injected times rather than by waiting hours
  of wall-clock time.
