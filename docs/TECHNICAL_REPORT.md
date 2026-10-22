# Technical Report — Independent Indeed Job Collector

Project: `/home/ubuntu/check_indeed`
Reference implementation studied: `/home/ubuntu/check_naukri/naukri_script`
(`find_naukri_job.py`, `new_mylib.py`, `target_audience_roles.py`,
`save_naukri_login.py`)
Date: 21 October 2026

---

## 1. What was studied, and what was actually active

`find_naukri_job.py` is 5,391 lines and has evolved by **wrapping** rather
than editing: several functions are defined more than once, each new
definition capturing the previous one. Reading the first definition of a
function would have reproduced behaviour that has since been replaced, so
the active definitions were identified before anything was copied:

| Function | Defined at lines | Active definition | What the active one adds |
|----------|------------------|-------------------|--------------------------|
| `append_detail_to_excel` | 197, 1351, 1490, 1875 | **1875** | drops rows with no company (P2), skips rows Excel refuses (P1A), rejects non-IT titles (TA), on top of the original rollover at 197 |
| `safe_to_excel` | 116, 1311 | **1311** | strips Excel-illegal control characters before the atomic write |
| `scrape_job_detail_async` | 410, 1236, 1478, 1627, 1736, 1834, 2116, 2333, 2638, 3811 | **3811** | per-job `source_url`, real company, real city/state, salary parsing, remote type, employment type, stale-page guard, HTTP status checking |
| `build_search_list` | 665, 4416 | **4416** | the broad department keyword removed |
| `main` | 868, 5164 | **5164** | the continuous crawl-cycle orchestrator (R2/R3/R4/R5) |
| `wait_for_internet` / `wait_for_battery` | 542/327, 3018/3026 | **3018/3026** | wrapped so waiting does not count as working time |

The two counters that are easy to confuse were traced to their sources:

* `cycle_state.json` / `load_cycle_num()` / `current_data_cycle{N}.xlsx` is
  the **Excel output batch** (100 accepted rows).
* `crawl_cycle_state.json` is the **keyword crawl cycle** (every keyword ×
  every rank city). The reference code says so explicitly in its own
  comment at line 2796, and the Indeed project keeps both the separation
  and the naming.

The city list was also checked rather than assumed: in `new_mylib.py`,
`city_by_states` is followed at line 1888 by a triple-quoted block that
comments out the per-state entries, so the **active** list is a single
`"Major Cities"` key containing **110 cities**. `city_by_states_original`
(29 states, 905 cities) is live and is what `city_to_state()` searches.

---

## 2. Architecture of the Indeed project

```
find_indeed_job.py
├─ configuration block (the only place settings live)
├─ Excel layer        safe read / safe write / rollover / master workbook
├─ state layer        crawl cycle, Excel counter, readable logs, failed queue
├─ schedule layer     work clock, night window, breaks, pacing gate
├─ plan layer         rank cities, 85 keywords, per-cycle shuffle
└─ orchestrator       run_combo → run_crawl_cycle → main
        │
        └── source_file/
              indeed_client.py     ── owns Chromium; raw jobs in, typed
                                      errors out
                ├─ BrowserTransport  visible Chromium, DOM extraction
                ├─ FixtureTransport  offline JSON records
                └─ HttpTransport     an authorised JSON API
              indeed_extractor.py  ── raw job → 32 columns
              indeed_apply_url.py  ── raw job → validated apply url
```

Data flows one way. The client knows nothing about Excel; the extractor
knows nothing about the network; the apply-url module knows nothing about
either. The main file is the only thing that knows about all of them, and
the only thing that holds configuration.

### Deliberate design differences from the reference

| | Naukri reference | Indeed project | Why |
|-|------------------|----------------|-----|
| Transport | Playwright browser, logged in with `naukri_auth.json` | Playwright browser (default), or a JSON client, or offline fixtures | the operator wants to watch the run, as they can watch Naukri's. What the browser *navigates* differs: see "the visible browser" below |
| Concurrency | `async`/`await` (Playwright's async API) | synchronous (Playwright's sync API) | nothing here needs an event loop; synchronous code is easier to test and to reason about during a crash |
| Patch style | wrappers layered over originals, for historical reasons | one definition per function | a fresh project has no history to preserve; the *behaviour* of the active wrappers is implemented directly |
| Battery check | `wait_for_battery()` via `psutil` | not carried over | the service runs on a server, and an unavailable battery sensor would be a pointless dependency. The internet check and its "waiting is not working" rule **are** carried over |
| `current_row.json` | not used by the reference either | not created | the row pointer is part of the single atomic `crawl_cycle_state.json` write |

---

## 3. Configuration (Phase 4)

Everything editable is between the two banners at the top of
`find_indeed_job.py`: rank and `num_sys`; the live-collection switch and the
environment variable names for an authorised source; the fixture settings;
night pause; keyword shuffle; Excel filenames and batch size; cycle
cooldown; listing cap and page size; work period and break ranges; request
pacing; HTTP timeouts and the user agent; the full retry and backoff table;
the failed-queue policy; the five state filenames; the internet check;
timezone; row defaults; browser settings (recorded as "not required" rather
than omitted); and logging.

`build_client_config()` copies those values into an `IndeedClientConfig`
object and passes it to the client. That object is a carrier, not a second
configuration module: every one of its defaults is overwritten by the main
file, and it reads no file of its own.

`tests/test_project_structure.py` parses `find_indeed_job.py` with `ast` and
fails if any required setting is missing, if a setting is defined outside
the configuration block, if the block drifts away from the top of the file,
or if anything anywhere imports `indeed_config`.

---

## 4. The crawl engine

**`run_combo(client, state, role_category, keyword, city, existing_data)`**
crawls one (keyword, city) pair: it asks the client for page 1, works out
how many pages to take (`min(ceil(total / RESULTS_PER_PAGE), 10)`), and for
each job calls `process_job()`. After each job the url is added to
`done_job_urls` and the checkpoint is written; after each page the page
number advances and the master workbook is saved. The page counter advances
**after** the page's work, never before — advancing first is how a crash
loses a page's jobs.

**`run_crawl_cycle`** walks the frozen keyword order: for each keyword, every
city; the checkpoint is saved before each combo; `pacing_gate()` is called at
combo and page boundaries only, so a pause never interrupts a page
mid-flight. Every fifth combo it replays a few eligible failed items.

**`main`** owns the cycle loop: open or resume a cycle, run it, and respond
to what comes back —

* `ScheduledPause` → save, sleep, reset the work timer, continue;
* `AccessBlocked` → mark the refused unit so the resumed run steps over it,
  save, take the access break, and stop after `MAX_BLOCK_COOLDOWNS` or
  immediately if the refusal looks session-wide;
* `KeyboardInterrupt` → save and exit cleanly;
* any other exception → save, cool down 5 minutes, resume from the
  checkpoint (one bad job must never end a run);
* `"complete"` → save the master, mark the cycle finished, report the queue,
  sleep the cooldown, reset the work timer, and open the next cycle.

---

## 4a. The visible browser

Added on 22 October 2026, after the collector was found to be doing all of
its work without ever opening a window. It never had: nothing in the project
imported Playwright, so there was no browser to be headless *or* visible.

### Where the code lives

All of it is in `source_file/indeed_client.py`:

| Piece | Responsibility |
|-------|----------------|
| `BrowserTransport.start/stop` | launch and tear down Chromium; two tabs (listing + job page), as the reference project does |
| `BrowserTransport._goto` | navigate, then classify: 401/403 → `IndeedAccessDenied`, 429 → `IndeedRateLimited` (with `Retry-After`), 5xx → `IndeedServerError`, a challenge page on a 200 → `IndeedAccessDenied` |
| `BrowserTransport._read_listing_cards` | JSON-LD first, then Indeed-shaped card selectors (`div.job_seen_beacon`, `[data-jk]`, `h2.jobTitle span[title]`, `[data-testid="company-name"]`, `[data-testid="text-location"]`, `[data-testid="attribute_snippet_testid"]`) |
| `BrowserTransport._read_detail` | `h1.jobsearch-JobInfoHeader-title`, `[data-testid="inlineHeader-companyName"]`, `[data-testid="inlineHeader-companyLocation"]`, `#salaryInfoAndJobType`, `#jobDescriptionText`, with JSON-LD as the fallback for each |
| `BrowserTransport._read_apply_candidates` | the apply button's `href` only — external (`#applyButtonLinkContainer a`) and Indeed-hosted (`#indeedApplyButton a`, `a[href*="/applystart"]`) |
| `_split_salary_and_type` | a job page states pay and job type in ONE element ("₹4,00,000 - ₹6,00,000 a year - Full-time"); handing that whole string to the salary parser made it reject the figure, so the parts are separated where the page's format is known |
| `DemoPageBuilder` | writes the demonstration pages into a temp directory (never into the project) and removes them at exit |

The Naukri selectors were **not** reused: they look for
`a[href*='job-listings']` and `jhc__*` classes, which do not exist on an
Indeed page. The main script contains no Playwright code at all — a test
asserts that `sync_playwright` and `chromium.launch` appear only in the
client module.

### Lifecycle

`main()` calls `client.open()` at the start of each cycle attempt and
`client.close()` before every long wait, so a collector that is paused for a
45-minute cooldown does not leave a browser sitting on the desktop. A second
SIGTERM closes the browser before exiting, and `DemoPageBuilder` registers an
`atexit` hook, so a killed run leaves neither an orphaned Chromium process
nor a page cache behind (both verified after a SIGTERM'd run).

### What it navigates, and the two gates

Default: **local demonstration pages**, banner-labelled, rows marked
`fixture: true` with `demo.fixture.invalid` source urls.

Live pages need BOTH `INDEED_BROWSER_AUTHORISED=yes` AND
`INDEED_BROWSER_SEARCH_URL`. Authorisation without a url fails preflight;
a url without authorisation stays a demonstration. No Indeed url is built in,
which is the same rule the HTTP transport already followed.

### What it refuses to do

A challenge page, a 403 or a 429 ends that unit of work and is recorded in
`failed_pages.json` (403 as `manual`, never auto-retried). There is no
CAPTCHA handling, no proxy or IP rotation, no stealth plugin and no
fingerprint spoofing; `slow_mo` is a debugging aid and is documented as one.
Robots-disallowed paths are never opened — an `/applystart` link is recorded
as a destination and left alone. The apply button is read, never clicked.

### Display handling

Visible mode with neither `DISPLAY` nor `WAYLAND_DISPLAY` set raises
`BrowserUnavailable` with instructions, instead of hanging or failing
cryptically. `indeed.service` carries commented-out `Environment=DISPLAY=:0`
lines for a desktop session, and recommends `BROWSER_HEADLESS = True` for an
unattended server.

---

## 5. Excel (Phase 9)

The reference method, preserved:

```python
df = safe_read_excel_or_empty(output_file, DETAIL_COLUMNS)
df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
if len(df) >= cycle_size:
    cycle_file, cycle_num = get_next_cycle_file(output_file)   # cycle_state.json + 1
    safe_to_excel(df, cycle_file)
    save_cycle_num(cycle_num)
    safe_to_excel(pd.DataFrame(columns=DETAIL_COLUMNS), output_file)
else:
    safe_to_excel(df, output_file)
```

Additions, each of which only ever skips one job:

1. `validate_row()` — a row needs a title, a trustworthy company and a
   per-job `source_url`.
2. the non-IT filter.
3. a duplicate check against the working workbook (`source_url`, `job_id`).
4. a guard around the write, so a row Excel still refuses is skipped and
   logged rather than ending the run.

`get_next_cycle_file()` additionally steps past any archive number that
already exists on disk, so deleting `cycle_state.json` by hand cannot
overwrite collected data.

All paths are resolved through `project_path()` against `BASE_DIR`, so the
service, an interactive run and a test all agree on where files live — and
no code path can create a subdirectory.

---

## 6. Error handling (Phase 11)

| Condition | Client | Main script |
|-----------|--------|-------------|
| 403 / 401 | `IndeedAccessDenied`, no retry; a second distinct refusal with no success in between is marked `session_wide` | queued as `manual`, 10-minute access break, stop after 3; a session-wide refusal stops immediately with the checkpoint intact |
| 429 | `Retry-After` honoured (delta-seconds or HTTP date, capped at 1 h); otherwise 60 s then 180 s; `IndeedRateLimited` after 3 | `max(ACCESS_BREAK_SECONDS, Retry-After)` — a longer server wait is never shortened |
| 5xx | 4 attempts, backoff 5/15/45/120 s | queued as retryable; the combo moves on |
| Network | 3 attempts, backoff 5/15/45 s | queued; the run continues |
| Bad body | `IndeedDataError` | queued; the run continues |
| Incomplete job | — | logged, skipped, queued; the other jobs on the page are still saved |

The failed queue is for *retryable* work only. Entries are de-duplicated on
(reference, status), escalate 30 m → 1 h → 3 h → 6 h, and then become
`manual` and are never retried automatically. A 403 enters the queue as
`manual` from the start, so the queue can never become a retry loop against
an endpoint that has refused access.

---

## 7. Scheduling (Phase 12)

The work clock is an accumulator that is stopped around every long wait, so
"4 hours of work" means four hours of collecting. `work_clock_pause()` /
`work_clock_resume()` nest, and the internet wait and every long sleep are
wrapped in them.

`pacing_gate()` raises `ScheduledPause` when the night window is open or the
drawn work period is spent. The work period is drawn fresh from
3.7–4.2 hours and the break fresh from 30–60 minutes for each period: this
spreads the five systems apart so they do not all hit the source at the same
minute. It is capacity management, not an attempt to look human — the gaps
are declared in the configuration, the user agent identifies the collector,
and any quota the source publishes takes precedence.

---

## 8. Security and compliance

* No credential is in source. The API key is read from the environment by
  `IndeedClientConfig.api_key` at the moment of use.
* `HttpTransport` has no default endpoint, so there is nothing to point at
  Indeed by accident.
* No CAPTCHA handling, no proxy or IP rotation, no fingerprint spoofing, no
  authentication bypass, no retrying of a refused endpoint.
* The apply-url module never submits an application or uploads a resume.
* Fixture data is labelled so it cannot be mistaken for live postings.

---

## 9. Live access assessment

There is no authorised self-serve Indeed job-search API as of October 2026,
and automated collection from Indeed's own pages is not permitted either --
which is why the visible browser ships pointed at local demonstration pages
rather than at indeed.com.
The Publisher API and XML feed were retired in 2024; the remaining partner
APIs are employer-side and return no search results; the site's robots rules
disallow the apply and RPC paths. `LIVE_COLLECTION_ENABLED` is therefore
`False`, the project runs on offline fixtures, and **no claim of working
live Indeed collection is made**. Everything else in the project — crawl
cycles, extraction, application-url resolution, Excel rollover, checkpoint
recovery, error handling, scheduling, service management — is complete,
exercised and tested.
