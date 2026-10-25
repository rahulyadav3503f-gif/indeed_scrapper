# Indeed Job Collector — README

A complete, independent Indeed job collector for JobEasyIndia, built on the
same architecture as the existing Naukri scraper but sharing no code with it
at runtime.

This document is written for a beginner. Read it top to bottom once and you
will know what every file does, how to run it, and what it writes where.

---

## 1. What this project does

It drives a **visible Chromium window** (Playwright) and walks a fixed
plan:

```
for each of the 85 IT job keywords   (shuffled once per cycle)
    for each city assigned to this system (23 of 110 at rank 1 of 5)
        for each listing page, up to 10
            for each job on the page
                fetch the job's details
                turn them into 32 database columns
                work out the REAL application url
                check it is valid and not a duplicate
                append it to current_data.xlsx
```

Every 100 accepted jobs, `current_data.xlsx` is archived as
`current_data_cycle1.xlsx`, `current_data_cycle2.xlsx`, … and emptied.
When every keyword has been crawled across every assigned city, the cycle is
finished, the collector rests for 45 minutes, the keywords are reshuffled,
and the next cycle begins. It runs continuously, pausing at night and taking
periodic breaks.

**Live collection is currently switched off.** See section 10.

---

## 2. The file layout

```
/home/ubuntu/check_indeed/
├── find_indeed_job.py        the main script AND the only configuration file
├── source_file/
│   ├── __init__.py
│   ├── indeed_client.py      the browser + the data source
│   ├── indeed_extractor.py   builds the 32-column row
│   └── indeed_apply_url.py   finds the real application url
├── new_mylib.py              cities + city → state  (SHARED with Naukri)
├── target_audience_roles.py  the 85 keywords + non-IT filter (SHARED)
├── requirements.txt
├── .gitignore
├── indeed                    ./indeed start|stop|restart|status|log
├── install_service.sh        installs the systemd unit
├── indeed.service            the systemd unit
├── tests/                    the automated tests (+ offline fixtures)
├── docs/                     this file and the three reports
│
│   ── created at runtime, directly in this folder ──
├── current_data.xlsx         the working workbook
├── current_data_cycle1.xlsx  archived batches of 100 jobs
├── current_data_cycle2.xlsx
├── indeed_it_jobs_by_city.xlsx   the master index of jobs seen
├── cycle_state.json          the Excel ARCHIVE counter
├── crawl_cycle_state.json    the crawl position (the real checkpoint)
├── state_city.json           readable "which city am I on" log
├── state_role.json           readable "which keyword am I on" log
└── failed_pages.json         retryable failures
```

There is **no output folder**. Every workbook and every checkpoint is
written directly into `/home/ubuntu/check_indeed`.

---

## 3. `find_indeed_job.py` — the main script

It does two jobs.

**(a) It is the configuration file.** Everything an operator may want to
change sits in one block at the top, between the banners
`INDEED JOB SCRAPER CONFIGURATION` and `END OF CONFIGURATION`: the rank, the
night window, the break lengths, the Excel filenames and batch size, the
state filenames, every timeout and retry number, the pacing gaps, the
listing cap and the live-collection switch.

**(b) It runs the crawl.** It loads the cities, builds the keyword plan,
opens or resumes a crawl cycle, drives the client, hands each job to the
extractor, saves accepted rows to Excel, checkpoints after every job, and
handles pauses, blocks, crashes and shutdown.

### Why there is no `indeed_config.py`

Because two files holding settings eventually disagree. A single block at
the top of the script an operator is already opening is simpler and cannot
drift. The modules in `source_file/` never read settings of their own —
`build_client_config()` in the main file packs them into one
`IndeedClientConfig` object and hands it over. A test
(`tests/test_project_structure.py`) fails the build if a setting goes
missing from the main file, or if any file ever imports `indeed_config`.

---

## 4. `source_file/indeed_client.py` — getting the data

Responsible for: retrieving listings, retrieving job details, pagination,
and surviving temporary connection problems. It returns jobs **raw**, in
whatever shape the source sent them; it never reshapes them.

It owns the browser too: launching Chromium, closing it, navigating, and
reading the rendered page all live here, and every setting arrives from the
configuration block in the main file.

It has three transports:

* **BrowserTransport** (the default, section 12a) drives a visible Chromium
  window. It reads listing cards and job pages out of the rendered DOM using
  Indeed-shaped selectors and schema.org JSON-LD — not the Naukri selectors,
  which do not exist on an Indeed page. With no authorised live source it
  navigates local demonstration pages.
* **FixtureTransport** reads `tests/fixtures/*.json`, and generates
  deterministic fixture jobs when no file matches. Used when
  `BROWSER_VISIBLE = False`, and as the data behind the demonstration pages.
* **HttpTransport** is the JSON API one. It has **no built-in endpoint** —
  the operator supplies the base url and paths through environment
  variables. It cannot be pointed at Indeed by accident.

Errors are typed, so the main script can respond correctly to each:
`IndeedAccessDenied` (403 — stop, never work around it),
`IndeedRateLimited` (429 — honour `Retry-After`), `IndeedServerError` (5xx —
bounded retries with backoff), `IndeedNetworkError`, `IndeedDataError`.

---

## 5. `source_file/indeed_extractor.py` — building the row

One raw job in, one 32-column row out, in exactly the order the Naukri
scraper writes it, so the database importer cannot tell the two apart.

Its rules:

1. **Nothing is invented.** No salary becomes blank, never `0`. No location
   becomes blank, never the city we happened to be searching.
2. **Salaries are absolute rupees.** "12 Lakhs" is `1200000`, not `12`.
3. **`city` and `state` describe the job**, not the search. The search city
   is used only when the job exposed no location at all.
4. **`source_url` is the job's own url**, never a constant — the database
   has a unique constraint on it.
5. **The description keeps its shape**: paragraphs, headings and bullets
   survive; "Apply now", cookie notices and navigation do not.

---

## 6. `source_file/indeed_apply_url.py` — the application url

A job's detail page and its application page are different things:

```
source_url : https://in.indeed.com/viewjob?jk=EXAMPLE123
apply_url  : https://company.example/careers/job/123
```

Four cases, all implemented and all tested:

| Case | Situation | Result |
|------|-----------|--------|
| A | the employer's own application page is exposed | that url, kind `external` |
| B | a real Indeed-hosted application (e.g. `/applystart`) | that url, kind `indeed_hosted` |
| C | nothing is exposed | **blank** — never the `source_url` |
| D | the url is unusable (`javascript:`, `data:`, malformed, too long, a company-profile or search link) | **blank**, and the reason is logged |

It validates schemes (http/https only), resolves permitted relative urls
against the job's own page, drops fragments, and refuses anything that is
merely sitting next to the Apply button. It never submits an application,
uploads a resume, or works around a sign-in.

---

## 7. Keywords, cities and one crawl cycle

* **85 keywords** — 68 target-audience roles (IT support, network and
  infrastructure, systems and servers, cloud and DevOps) plus 17 other
  high-demand Indian IT roles. They live in `target_audience_roles.py`.
  A broad "IT Department" search is never performed, and
  `is_non_it_title()` rejects a non-IT job if a role search returns one.
* **110 cities**, split positionally between systems. With `rank = 1` and
  `num_sys = 5`, this system crawls cities 1–23; ranks 2–4 take 23 each and
  rank 5 takes the remaining 18. Every city belongs to exactly one rank,
  none is skipped and none is crawled twice.
* **One cycle** = every keyword across every city of this rank. At the end:
  the cycle is marked complete, the collector sleeps 45 minutes, the
  keywords are reshuffled (seeded from rank + cycle number, so the five
  systems never march in step), and cycle N+1 opens.

---

## 8. Excel: where files go and how rollover works

* Jobs are appended to **`current_data.xlsx`** in the project root.
* When it reaches **100 rows** (`EXCEL_BATCH_SIZE`) it is saved as
  **`current_data_cycleN.xlsx`**, the number N is read from and then written
  back to `cycle_state.json`, and `current_data.xlsx` is re-created empty
  with the same 32 columns.
* **`indeed_it_jobs_by_city.xlsx`** is the master index: one row per unique
  (keyword-category, title, city), with first/last seen timestamps and a
  sighting count.

Two different counters, which are easy to confuse and must not be:

| | file | meaning |
|-|------|---------|
| Excel archive number | `cycle_state.json` | how many 100-row workbooks have been filled |
| Crawl cycle number | `crawl_cycle_state.json` | how many full keyword × city passes have finished |

Safety: workbooks are written to a temporary file and renamed into place, so
Ctrl+C can never corrupt one; control characters Excel refuses are stripped;
a corrupted workbook is replaced rather than crashing the run; an archive is
never overwritten, even if `cycle_state.json` is deleted by hand.

---

## 9. Checkpoints and recovery

`crawl_cycle_state.json` is the real checkpoint. It holds the cycle number,
the frozen keyword order for that cycle, the city list, the current keyword
index, city index and page, and the urls already saved on the current page.
It is written atomically after every job.

So, after a crash, a power cut or `./indeed stop`:

* the same cycle resumes — the keywords are **not** reshuffled mid-cycle;
* the crawl continues at the same keyword, city and page;
* jobs already written are skipped rather than duplicated;
* the Excel archive number continues from `cycle_state.json`.

`current_row.json` from the original file list is deliberately **not**
created: the row pointer already lives inside `crawl_cycle_state.json` and is
saved in the same atomic write, so a second file could only ever disagree
with it.

---

## 10. Live data access — read this before enabling anything

**Status: live Indeed collection is NOT available, and the switch is off.**

What was checked (October 2026):

* Indeed's free **Publisher Job Search API** stopped accepting new sign-ups
  at the end of 2020, was deprecated, and stopped answering in 2024. The XML
  job feed went with it.
* The surviving APIs at `partners.indeed.com` (Indeed Apply, Job Sync,
  Sponsored Jobs) are employer-side, under partner agreement, and **none of
  them returns job search results**.
* `in.indeed.com/robots.txt` disallows `/applystart`, `/rc/`, `/rpc/` and
  `/my/` for all user agents, and the site is behind bot protection.
  Scraping its pages is therefore out of scope for this project.

So `LIVE_COLLECTION_ENABLED = False`, the visible browser runs in
demonstration mode against local pages (section 12a), and the collector
produces fixture rows. Fixture rows are labelled (`fixture: true`, `*.fixture.invalid`
hosts) and must never be loaded into the production database.

To enable live collection once an authorised source exists:

```bash
# 1. put the source's details in /home/ubuntu/check_indeed/.env  (chmod 600)
INDEED_SOURCE_BASE_URL=https://api.authorised-source.example/v2
INDEED_SEARCH_PATH=jobs/search?q={keyword}&l={city}&page={page}
INDEED_DETAIL_PATH=jobs/{job_key}
INDEED_API_KEY=...            # never committed, never in source

# 2. set LIVE_COLLECTION_ENABLED = True at the top of find_indeed_job.py
# 3. ./indeed restart
```

The client refuses to start if the switch is on but the source is not
configured — it will not guess an endpoint.

---

## 11. Installation

```bash
cd /home/ubuntu/check_indeed
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
```

The last line downloads the Chromium build Playwright drives. Without it the
collector stops at startup with an explicit "run `python -m playwright
install chromium`" message rather than failing obscurely.

`Levenshtein` is optional — `new_mylib.py` falls back to a pure-Python
implementation when it is not installed.

---

## 12. Running it

```bash
cd /home/ubuntu/check_indeed
source venv/bin/activate
python3 find_indeed_job.py
```

A **visible Chromium window opens** and you can watch it work. See section
12a for what it shows and how to change it.

As a service:

```bash
./install_service.sh      # installs and enables the "indeed" unit
./indeed start
./indeed status
./indeed log
./indeed restart
./indeed stop
```

`./indeed` works with or without systemd: if the unit is not installed it
runs the collector as a background process tracked by `indeed.pid`.
`stop` sends SIGTERM, which asks the collector to save its checkpoint and
stop at the next safe boundary. It only ever touches the service named
`indeed`; the Naukri service is never started, stopped or restarted.

---

## 12a. The visible browser

`python3 find_indeed_job.py` drives a real Chromium window through
Playwright. You see it open, load a search page, open each job page, and move
on — slowed down on purpose so the steps are followable.

### The settings (top of `find_indeed_job.py`)

```python
BROWSER_VISIBLE = True        # use Chromium for collection
BROWSER_HEADLESS = False      # False = the window is on your screen
BROWSER_SLOW_MO_MS = 300      # pause between actions, so you can follow it
BROWSER_WIDTH = 1366
BROWSER_HEIGHT = 768

BROWSER_REQUIRED = True       # the collector needs a browser
BROWSER_CHANNEL = ""          # "" = Playwright's own Chromium build
BROWSER_NAV_TIMEOUT_MS = 45000
BROWSER_KEEP_OPEN_SECONDS = 0 # hold the window open before closing it
BROWSER_DEMO_PAGES = True
```

* Watching it is too slow? Lower `BROWSER_SLOW_MO_MS` to `0`.
* Running on a server with no screen? Set `BROWSER_HEADLESS = True`.
  Visible mode with no `DISPLAY` stops with a clear error rather than hanging.
* Want no browser at all (the old JSON/fixture client)? Set
  `BROWSER_VISIBLE = False`.

`slow_mo` exists so a developer can follow the run. It is not a
bot-detection measure, and nothing in this project tries to look human.

### What the window shows: DEMONSTRATION mode

Live collection from Indeed is **not authorised** for this project
(section 10), so by default the browser navigates **local fixture pages**
generated on this machine into a temporary directory:

* every page carries a red `DEMONSTRATION — … This is NOT Indeed` banner;
* the pages are built to mirror the DOM of a real job page (the same card
  containers, heading, company, location, salary chip, description block and
  apply button), so the extraction you watch is the **same code** that would
  run against an authorised live page;
* the rows written carry `source_url` hosts like `demo.fixture.invalid`, so a
  fixture row can never be mistaken for a real Indeed posting;
* **no request reaches any Indeed host in this mode.**

The startup banner says so in block capitals before any collecting starts.

### Pointing the browser at pages you are permitted to collect

Only when you have that permission, set all three in the environment (or in
`.env`, `chmod 600`) and restart:

```bash
export INDEED_BROWSER_AUTHORISED=yes
export INDEED_BROWSER_SEARCH_URL='https://<authorised-host>/jobs?q={keyword}&l={city}&start={start}'
export INDEED_BROWSER_DETAIL_URL='https://<authorised-host>/viewjob?jk={job_key}'
```

Both halves are required: authorisation without a url is refused at startup,
and a url without the acknowledgement stays a demonstration. There is no
built-in Indeed url anywhere in the browser path — supplying one is you
asserting that the collection is authorised.

### What the browser never does

* No CAPTCHA solving and no challenge bypassing. A verification page, a 403
  or a 429 **stops** that work and is recorded; it is never worked around,
  and no alternative route is tried.
* No proxy or IP rotation, no stealth plugins, no fingerprint spoofing. The
  user agent is Chromium's own.
* No robots-disallowed path is opened. An Indeed-hosted `/applystart` link is
  **recorded** as the application destination and left alone.
* The Apply button's `href` is read; the button is never clicked. No
  application is submitted and no resume is uploaded.

### Running it as a service

A systemd service has no display of its own. Either give the unit the
desktop's display (`Environment=DISPLAY=:0` — the lines are in
`indeed.service`, commented out), or set `BROWSER_HEADLESS = True`, which is
the right answer for an unattended server.

---

## 13. Testing

```bash
cd /home/ubuntu/check_indeed
python3 -m unittest discover -s tests -p "test_*.py" -v
```

No pytest needed — the suite is standard-library `unittest`. See
`docs/TEST_REPORT.md` for what is covered and the latest run, and
`docs/PERFORMANCE_FIXES.md` for why a job used to take one to two minutes and
what was changed to make it seconds, and `docs/SHARED_LIBRARY.md` for the two
files this project now shares byte-for-byte with the Naukri scraper.

---

## 14. Night pause and breaks

* **Night pause** — nothing is collected between `NIGHT_START_HOUR` (00:00)
  and `NIGHT_END_HOUR` (06:00) in `Asia/Kolkata`.
* **Periodic break** — after 3.7–4.2 hours of *actual working time*, the
  collector stops for a random 30–60 minutes. Night stops, cooldowns, access
  breaks and time spent waiting for the internet do **not** count as working
  time.
* **Cycle cooldown** — 45 minutes between finished cycles.

Pauses are only ever taken at safe boundaries (before a combo, before a
listing page), so a page is never abandoned half-finished.

---

## 15. When something goes wrong

| Symptom | What happens | What to do |
|---------|--------------|------------|
| HTTP 403 | the work is queued as `manual`, the run takes a 10-minute break, and stops after 3 of them | check the source agreement; nothing is retried automatically |
| HTTP 429 | `Retry-After` is honoured (or a configured wait) | nothing — it recovers |
| HTTP 5xx | bounded retries with backoff, then queued in `failed_pages.json` | nothing — eligible entries replay between combos |
| Network down | the collector waits, and the wait does not count as work | nothing |
| One malformed job | logged and skipped; the run continues | look it up in `failed_pages.json` |
| Any unexpected error | checkpoint saved, 5-minute cooldown, resume | check `indeed.log` |

A queued item that keeps failing is deferred for longer and longer
(30 m → 1 h → 3 h → 6 h) and is then marked `manual` and never retried
automatically. Nothing in this project retries a refused endpoint in a loop.
