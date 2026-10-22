# Feature Comparison — Naukri reference vs. independent Indeed collector

Reference: `/home/ubuntu/check_naukri/naukri_script/find_naukri_job.py`
(plus `new_mylib.py`, `target_audience_roles.py`, `save_naukri_login.py`)
Target: `/home/ubuntu/check_indeed`

Legend — **Same**: behaviour preserved. **Adapted**: same purpose, different
mechanism, because the data source is different. **Not carried over**: a
deliberate decision, with the reason given.

| # | Feature | Naukri reference | Indeed project | Status |
|---|---------|------------------|----------------|--------|
| 1 | Configuration at the top of the main script | `rank`, `num_sys`, work/break, `DETAIL_OUTPUT_FILE`, plus `HX_*` constants further down | one block between the `INDEED JOB SCRAPER CONFIGURATION` and `END OF CONFIGURATION` banners, asserted by a test | **Same, tightened** |
| 2 | `rank` / `num_sys` | `rank = 1`, `num_sys = 5` | identical, same meaning | Same |
| 3 | Symmetric city distribution | `get_cities_by_rank()`, `one_unit = int(len/num_sys) + 1` | the same function, same formula, plus explicit handling of an invalid rank | Same |
| 4 | City list | 110 cities from `city_by_states` | the same 110 cities, independent copy in the project's own `new_mylib.py` | Same |
| 5 | City → state | `city_to_state()` with Levenshtein | same, with a pure-Python fallback when the package is absent, and a cache | Same |
| 6 | Job keyword generation | `hx_keyword_plan_pairs()`, broad department keyword dropped | `keyword_plan_pairs()`, identical logic | Same |
| 7 | Target-audience roles | 68 target + 17 other = 85 | identical, independent copy of `target_audience_roles.py` | Same |
| 8 | Non-IT filtering | `is_non_it_title()` blocklist with IT override, applied before saving | identical, applied in `append_detail_to_excel()` | Same |
| 9 | Keyword shuffle | once per cycle, within department, seeded by (rank, cycle, department) | identical, plus `KEYWORD_SHUFFLE_ENABLED` to switch it off | Same + switch |
| 10 | Job listing extraction | Playwright DOM: `a[href*='job-listings']`, scroll, `get_total_jobs()` | `IndeedClient.search_jobs()` / `iter_listings()` over a JSON source or fixtures | **Adapted** |
| 11 | Pagination | `calculate_total_pages()`, capped at `HX_MAX_LISTING_PAGES = 10` | `client.page_count()`, capped at `MAX_LISTING_PAGES = 10` | Same |
| 12 | Job detail extraction | `scrape_job_detail_async()` (final wrapper at line 3811) | `extract_job_details()` in `source_file/indeed_extractor.py` | **Adapted** |
| 13 | 32-column schema | `DETAIL_COLUMNS` | identical list, identical order, `source_platform = "Indeed"` | Same |
| 14 | Per-job `source_url` | P1 fix: the job's own detail url | the job's own url, or `viewjob?jk=<key>`; a row with no stable url is rejected | Same |
| 15 | Company name | P2 fix: JSON-LD first, row dropped when untrustworthy | `resolve_company()`, row dropped when empty | Same |
| 16 | City / state from the JOB | P3 fix: job locality first, search city only as a last resort | `resolve_location()`, same resolution order, same alias table | Same |
| 17 | Salary parsing | P6A: absolute rupees, lakh/crore expansion, never 0, period recorded not converted | `parse_salary()` + structured-block reader, same rules, plus a currency detector | Same + |
| 18 | Remote type | P6B: chip text → Remote/Hybrid/Onsite | `resolve_remote_type()` over the fields the source exposes | Same |
| 19 | Employment type | P6C: token resolution, Full-time default | `resolve_employment_type()`, same default | Same |
| 20 | Experience | parsed from the listing url | `parse_experience()` over structured fields then the description | **Adapted** |
| 21 | Skills | DOM chips joined with "; " | `resolve_skills()`, de-duplicated, joined with "; " | Same |
| 22 | Description | `strip_html()` → flat text | `clean_description()` → **keeps paragraphs, headings and bullets**, removes page furniture | **Improved** |
| 23 | Application URL | `a[class*='apply']` href **or the detail url as a fallback** | dedicated module: external / Indeed-hosted / blank / rejected — the detail url is **never** copied | **Improved** |
| 24 | Excel saving | `safe_to_excel()` — temp file + atomic rename, control characters stripped | identical | Same |
| 25 | Excel rollover | 100 rows → `current_data_cycleN.xlsx`, working file emptied | identical, plus a guard that never overwrites an existing archive | Same + |
| 26 | Excel cycle numbering | `cycle_state.json`, read from disk every time | identical | Same |
| 27 | Excel location | project root, no subfolders | identical, asserted by tests | Same |
| 28 | Master workbook | `naukri_it_jobs_by_city.xlsx`-style index, 12 columns | `indeed_it_jobs_by_city.xlsx`, same 12 columns | Same |
| 29 | Duplicate prevention | `done_job_urls` + master `make_unique_key()` | both, plus a `source_url` / `job_id` check against the working workbook | Same + |
| 30 | Crawl cycle checkpoint | `crawl_cycle_state.json` — cycle number, frozen order, indices, done urls | identical structure and semantics | Same |
| 31 | Readable progress files | `state_city.json`, `state_role.json` (logs, not authority) | identical | Same |
| 32 | Resume | `hx_resume_position()` by index, no reshuffle mid-cycle | `resume_position()`, identical | Same |
| 33 | Continuous cycles | cycle → 45-minute cooldown → reshuffle → next cycle | identical | Same |
| 34 | HTTP 403 | recognised, access break, stop after 3, session-wide detection | identical policy, typed as `IndeedAccessDenied` | Same |
| 35 | HTTP 429 | `Retry-After` honoured, capped, `max()` never `min()` | identical | Same |
| 36 | HTTP 5xx | 4 attempts, 5/15/45/120 s | identical | Same |
| 37 | Network failures | 3 attempts, 5/15/45 s, then queued | identical | Same |
| 38 | Failed-work queue | `failed_pages.json`, de-duplicated, escalating deferrals, `manual` terminal state | identical policy | Same |
| 39 | Queue replay | between combos, batch of 3, every 5 combos, single attempt | between combos, batch of 3, every 5 combos | Same |
| 40 | Night pause | 00:00–06:00, crossing midnight handled | identical, plus `NIGHT_PAUSE_ENABLED` and an explicit timezone | Same + |
| 41 | Periodic breaks | 4 h of working time → 30–60 min | 3.7–4.2 h of working time → 30–60 min (the range spreads the ranks apart) | Same + |
| 42 | Work clock excludes waits | accumulator paused around every long wait | identical | Same |
| 43 | Graceful shutdown | `KeyboardInterrupt` → checkpoint saved | `KeyboardInterrupt` **and** SIGTERM → checkpoint saved, stop at the next safe boundary | **Improved** |
| 44 | Crash tolerance | unexpected error → save, 5-minute cooldown, resume | identical | Same |
| 45 | Internet check | socket probe, waiting excluded from work time | identical | Same |
| 46 | Battery check | `wait_for_battery()` via `psutil` | **not carried over** — server deployment, no battery sensor | Not carried over |
| 47 | Login / session file | `save_naukri_login.py` writes `naukri_auth.json` | **not carried over** — there is no authorised interactive Indeed session to save; credentials for an authorised API come from the environment | Not carried over |
| 48 | Browser automation | Playwright, Chromium | **not carried over** — see the live-access assessment | Not carried over |
| 49 | Service management | external to the reference project | `indeed`, `install_service.sh`, `indeed.service` (unit name `indeed`) | **New** |
| 50 | Automated tests | 5 test files in `tests/` | 9 test files, 227 tests, standard-library `unittest` | Same + |
| 51 | Offline operation | not possible (needs the live site) | fixture transport runs the whole pipeline with no network | **New** |

## Summary

* **Carried over unchanged in behaviour:** 38 features.
* **Adapted to a JSON data source:** 4 (listing extraction, detail
  extraction, experience parsing, transport).
* **Improved:** 4 (description structure, application urls, SIGTERM
  shutdown, duplicate prevention).
* **Deliberately not carried over:** 3 (battery check, browser login,
  browser automation) — each because of the data source or the deployment,
  and each recorded above rather than silently dropped.
* **New:** 2 (service management, offline fixture mode).
