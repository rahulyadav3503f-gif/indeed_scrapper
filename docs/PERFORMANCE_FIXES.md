# Why a job took one to two minutes, and what was changed

Project: `/home/ubuntu/check_indeed`
Investigated and fixed: 25 October 2026

---

## 1. The symptom

> "why it takes so much time on scrapping the job details page and after it
> scrape the job details page and go to next job card it takes time … like
> 1-2 minutes"

Each job was taking roughly a minute or two end to end. Nothing was broken —
the rows written were correct — it was simply spending almost all of its time
waiting, not collecting.

---

## 2. How it was measured (not guessed)

Three measurements, all taken against **local demonstration pages** so that
nothing extra was requested from Indeed while profiling:

| Script | What it measured |
|--------|------------------|
| `profile_browser.py` | how many Playwright round trips one listing page and one job page cost, with a counter wrapped around `Locator.count/inner_text/get_attribute` and `Page.goto/evaluate/inner_text` |
| `stall_test.py` | how long the two render waits take on a page whose markup does **not** match any expected selector |
| an Excel timing loop | how long one `append_detail_to_excel()` takes as the workbook fills up |

These scripts live in the session scratchpad, not in the project — they were
diagnostics, not shipped code. Their results are below.

---

## 3. What was actually slow

### Cause 1 — the render waits were serial (the big one)

`_wait_for_cards()` and `_wait_for_detail()` each looped over their selector
candidates and called `page.wait_for_selector(selector, timeout=12000)` for
**every candidate in turn**, breaking out on the first one that matched. When
the page served markup that the *first* selector did not match — a different
Indeed layout variant, or a search that returned no results — each miss burned
the full 12-second timeout before the next candidate was tried.

Measured on a page that matches none of them:

| Wait | Candidates | Measured stall |
|------|-----------|----------------|
| `_wait_for_cards` | 5 card selectors × 12s | **60.8 s** |
| `_wait_for_detail` | 7 detail selectors × 12s | **84.0 s** |

60.8 + 84.0 = **144 seconds**, which is precisely the "1–2 minutes" that was
being reported. When the first selector *did* match, the same waits took 0.9s
and 0.0s — which is why it looked intermittent.

### Cause 2 — one Playwright round trip per selector, per field, per card

The DOM readers walked the selector lists with Playwright locators: for every
field of every card, `locator.count()` and then
`locator.first.inner_text()`. Each of those is a round trip to the browser,
and **`slow_mo` is applied to every one of them**.

Measured cost before the change:

| Page | Round trips |
|------|-------------|
| one job page | **24** |
| one listing page | **38** (≈19 per card) |

With the shipped `BROWSER_SLOW_MO_MS = 300`, that is 7.2s of pure slow_mo on a
job page — and on a 16-card listing page, 16 × 19 × 0.3 ≈ **90 seconds**
before a single job was opened. `slow_mo` is meant to make the run watchable;
it was accidentally the dominant cost.

### Ruled out by measurement

* **Excel saving** — 0.09s to 0.27s per append, flat as the workbook grew to
  100 rows. Not a factor.
* **Checkpoint writes** — one small atomic JSON write per job, milliseconds.
* **The consent banner** — real, but small: 8 round trips per page.

---

## 4. What was changed

All in `source_file/indeed_client.py`, except the two settings.

### 4.1 One wait instead of N

New `BrowserTransport._wait_for_any(page, selectors, timeout_ms)` joins the
candidates into a single CSS selector list and waits **once**:

```python
joined = ", ".join(s for s in selectors if ":has-text(" not in s)
page.wait_for_selector(joined, timeout=timeout_ms, state="attached")
```

A page that matches none of them now costs one timeout, not one per
candidate. Playwright-specific selectors (`:has-text(...)`) are left out of
the join, because they are not valid in a plain CSS list.

### 4.2 One DOM round trip per page

Three new JavaScript readers do in the page what the locator loops did over
the wire:

| Constant | Replaces |
|----------|----------|
| `_JS_READ_CARDS` | every card's `job_key`, `href`, title, company, location, salary, snippet — **and** the result count — in one `page.evaluate()` |
| `_JS_READ_DETAIL` | the job page's title, company, location, salary/type chip, description HTML and both apply hrefs, in one `page.evaluate()` |
| `_JS_READ_JSONLD` | all `script[type="application/ld+json"]` blocks in one call, instead of a `count()` plus one `inner_text()` each |

The **selector lists stay in Python** and are passed in as data, so they
remain the single source of truth and the priority order is unchanged.

Methods added: `_read_listing_cards_fast`, `_read_detail_fast`,
`_merge_detail`, `_tidy_apply_href`, `_TOTAL_SELECTORS`.
The original per-element readers are kept as
`_read_listing_cards_via_locators` and `_read_detail_via_locators`, used
automatically if `evaluate` ever fails — so nothing depends on JavaScript
being available, and a test asserts the two paths produce the same row.

### 4.3 Two smaller probes collapsed

* **Consent banner** — `_JS_TAG_REJECT_BUTTON` finds the decline control and
  tags it with `data-collector-reject="1"` in one call; Playwright then
  clicks that marker. A page with no banner (every page after the first) now
  costs 1 round trip instead of 8. It still matches **only** decline
  controls — a test asserts no accept button is ever tagged, and that the
  string "Accept All" appears nowhere in the client.
* **Challenge detection** — the body text is now sliced to 4,000 characters
  *inside* the page (`_JS_BODY_SNIPPET`) instead of transferring a whole job
  page's text and slicing it in Python.

### 4.4 The render timeouts were lowered

In `find_indeed_job.py` (configuration block, as always):

```python
LISTING_RENDER_TIMEOUT_MS = 8000    # was 12000
DETAIL_RENDER_TIMEOUT_MS  = 6000    # was 12000
```

These are **maxima**, not delays: a page that renders in 400ms is read in
400ms. They only bite when the expected markup never appears, so they cap how
long an unreadable page can hold up the run. A comment next to
`BROWSER_SLOW_MO_MS` now states its real cost.

---

## 5. Results

| | Before | After |
|-|--------|-------|
| Round trips, one job page | 24 | **6** |
| Round trips, one listing page | 38 (≈19/card) | **8** (fixed, any card count) |
| Unreadable listing page | 60.8 s | **8.8 s** |
| Unreadable job page | 84.0 s | **6.0 s** |
| Worst case for one job | ~144 s | **~15 s** |
| slow_mo overhead per job page @300ms | 7.2 s | 1.8 s |
| slow_mo overhead, 16-card listing page @300ms | ~90 s | 2.4 s |

End-to-end, with the shipped settings against demonstration pages:

```
slow_mo=300ms  JOB_GAP=1.0s  REQUEST_GAP=2.0s   ->  4.8s per job
slow_mo=0ms    JOB_GAP=1.0s  REQUEST_GAP=2.0s   ->  3.7s per job
```

On the live site the page load itself is added to that, so expect a few
seconds per job rather than a minute.

---

## 6. The time that is still spent on purpose

Roughly 4 seconds per job is **deliberate politeness**, not waste. If you want
it faster, these are the knobs, all at the top of `find_indeed_job.py`:

| Setting | Shipped | What it costs per job |
|---------|---------|----------------------|
| `REQUEST_GAP_SECONDS` | 2.0 | 2.0–2.7 s before each page load |
| `JOB_GAP_SECONDS` | 1.0 | 1.0–1.35 s after each job |
| `BROWSER_SLOW_MO_MS` | 300 | ~1.8 s (6 browser calls per page) |
| `PACING_JITTER_FRACTION` | 0.35 | spreads the ranks apart |

Setting `BROWSER_SLOW_MO_MS = 0` keeps the window fully visible and removes
the slow-motion cost. **Do not** set the two gaps to zero on the live site:
they are what keeps the collector polite, and a collector that stops being
polite gets rate-limited, which costs far more time than it saves.

The `⏱` timer line printed after each job (`SHOW_JOB_TIMER = True`) reports
the real figure per job, the running average, and the elapsed time for the
page, so any future slowdown is visible immediately rather than guessed at.

---

## 6a. Only two listing pages per city (25 October)

### The symptom

> "why it takes jobs only 2 listening pages after 2 pages it change the city"

### The cause

The number of pages crawled for a (keyword, city) pair came **entirely from
the job count in the site's own results header**:

```
"16 jobs" / RESULTS_PER_PAGE (10)  =  2 pages, then move to the next city
```

That header under-reports for a narrow search — the site will happily serve
pages 3, 4 and 5 for a search it summarises as "16 jobs" — so pages that
genuinely existed were never opened. Two smaller faults sat alongside it:

* the header text was parsed with "first number wins", so a header reading
  `Page 1 of 1,234 jobs` was read as **1** and planned a single page;
* a header of 0 meant "stop", even when the page in front of it was full of
  job cards.

### The fix: a floor and a cap, with honest early stops

```python
MIN_LISTING_PAGES = 4           # floor: do not believe a small header count
MAX_LISTING_PAGES = 10          # ceiling, per (keyword, city) pair
RESULTS_PER_PAGE  = 10          # the site paginates in steps of 10
STOP_ON_PAGE_WITH_NO_NEW_JOBS = True
```

`page_count()` now takes a `minimum` as well as a `cap`. The cap still wins,
so politeness is never loosened by the floor:

| Header says | Planned before | Planned now |
|-------------|----------------|-------------|
| 16 jobs | 2 pages | **4 pages** |
| 45 jobs | 5 pages | 5 pages |
| 150 jobs | 10 pages | 10 pages |
| 0 jobs, cards on the page | stop | crawl the floor |

A floor would be wasteful on its own, so pagination now stops the moment the
results actually run out, by either of two signals:

1. **an empty page** — stop immediately;
2. **a page whose every job this combo has already collected** — the site
   repeats cards when it runs out of real results, so "nothing new on this
   page" is the end of the results, and a more reliable signal than the
   header count.

The header parser now takes the **largest** number in the text, so
`Page 1 of 1,234 jobs` reads as 1,234.

### Measured against a stub that mimics the real behaviour

| Scenario | Pages requested | Rows |
|----------|-----------------|------|
| 5 real pages, header says 16 | 1, 2, 3, 4 (was 1, 2) | 12 (was 6) |
| 1 real page, header says 16 | 1, 2 — the floor costs **one** wasted request | 3 |
| 10 pages promised, results end at 2 | 1, 2, 3 — **7 requests saved** | 6 |
| Genuinely empty search | 1 | 0 |
| Rich search | 1…10 (the cap) | 30 |

That last row matters for the 429 you hit: the old code would have spent all
ten requests whenever the header promised them, whether or not there was
anything left to collect. Stopping on the first repeat page spends fewer
requests on exhausted searches while collecting more from the deep ones.

Tuning: raise `MIN_LISTING_PAGES` to dig deeper into small-city results
(each extra page costs one request per city only until the results run out);
raise `MAX_LISTING_PAGES` to go deeper into big-city results. 13 tests in
`tests/test_indeed_client.py` (`TestPagePlanning`, `TestResultHeaderParsing`,
`TestPaginationStops`) hold all of this in place.

---

## 6b. Delay ranges and an escalating 403 cooldown (27 October)

### The three crawl delays are now MIN/MAX ranges

They were a single number each plus a shared jitter fraction, so the real
delay was `base x (1 + random(0, 0.35))` -- correct, but hard to read off
the configuration. Each one is now an explicit range, drawn uniformly on
every use:

```python
JOB_GAP_MIN_SECONDS = 10.0      # after each job detail page
JOB_GAP_MAX_SECONDS = 14.0
CITY_GAP_MIN_SECONDS = 15.0     # between two cities
CITY_GAP_MAX_SECONDS = 21.0
KEYWORD_GAP_MIN_SECONDS = 15.0  # between two keywords
KEYWORD_GAP_MAX_SECONDS = 21.0
```

The shipped ranges reproduce the pacing that was tuned after the 429: the
old `base`..`base x 1.35` became `MIN`..`MAX`, so nothing got faster or
slower by accident. `MIN == MAX` is a fixed delay, `0` is no delay, and a
reversed pair is swapped rather than producing a negative one.

`pick_delay(min, max)` does the draw and `sleep_between(min, max, label)`
waits, logging what it drew and the range it drew from:

```
      ... next job page in 12.4s (random 10-14s)
```

`REQUEST_GAP_SECONDS` (the gap before each individual page request) is
unchanged, still one number with jitter -- it follows the source's own pace
rather than the crawl's shape.

### A 403 now escalates, then the unit is skipped

Before: every refusal cost the same 10 minutes, and after three of them the
**run stopped**. One job the source would not serve could therefore end a
night's collecting.

Now the wait grows with each *consecutive* refusal, and when the escalation
is spent the refused unit is given up on and the crawl carries on:

| Consecutive refusal | What happens |
|---------------------|--------------|
| 1st | wait 1 x `ACCESS_BREAK_SECONDS` = 10 min |
| 2nd | wait 2 x = 20 min |
| 3rd | wait 3 x = 30 min |
| 4th | wait 4 x = 40 min |
| 5th | **skip that job / listing page**, record it as `manual` in `failed_pages.json`, reset the escalation, keep crawling |

```
   [would sleep 10m 00s] HTTP 403 access break 1/4 -- 1x the 10m 00s base cooldown
   [would sleep 20m 00s] HTTP 403 access break 2/4 -- 2x the 10m 00s base cooldown
   [would sleep 30m 00s] HTTP 403 access break 3/4 -- 3x the 10m 00s base cooldown
   [would sleep 40m 00s] HTTP 403 access break 4/4 -- 4x the 10m 00s base cooldown
GIVING UP ON THIS UNIT. HTTP 403 survived all 4 escalating cooldowns
```

Two details that matter:

* **"Consecutive" means with no successful page in between.** The client now
  counts successful requests; if that count has moved since the last block,
  real collection happened and the escalation restarts at 1x. A refusal this
  morning does not make this evening's refusal wait 20 minutes.
* **A `Retry-After` from the source still wins when it asks for longer** --
  `max()`, never `min()`. Our own schedule can lengthen a wait, never
  shorten one the source asked for.

Settings: `ACCESS_BREAK_SECONDS = 600` (the 1x base) and
`MAX_BLOCK_COOLDOWNS = 4` (how many escalating cooldowns before the skip).
Set `MAX_BLOCK_COOLDOWNS = 2` for a quicker give-up, or raise
`ACCESS_BREAK_SECONDS` to be gentler.

### The one case where the run still stops

If refusals look **session-wide** -- distinct urls refused with no successful
page in between, i.e. the source is refusing everything, not one bad job --
the run still ends with every checkpoint intact. Skipping unit after unit
in that situation would mean hammering a source that has just said no, which
is the one thing this collector must not do. Re-running continues from the
exact position once access is back.

16 tests cover both changes in `tests/test_error_handling.py`
(`TestEscalatingAccessCooldown`, `TestCrawlDelayRanges`), including the
escalation sequence, the skip, the reset after a success, and that a longer
`Retry-After` is never shortened.

---

## 6d. The 403, the 401, and a real rate cap (30 October)

### What the 403 actually was

A signed-in session was blocked nine minutes after it was created (login
saved 19:29, first 403 at 19:38), and it stayed blocked across three
different mobile IPs. Verified by deleting the saved session and making one
request:

| | Signed in (blocked session) | Cookies deleted |
|---|---|---|
| `/jobs?q=…&start=0&sort=date` | 403 on every page | **HTTP 200**, 16 cards |
| `/viewjob?jk=…` | worked, then 403 | **HTTP 401** |

So the refusal was attached to **that logged-in session** -- not to the IP
and not to Playwright. Changing IP could never have helped. Also checked and
ruled out: the robots pre-check (it raises status 999, and all recorded
entries were 403), the request path (robots permits `/jobs` with
`&start=0`..`&start=90`), and the User-Agent (the browser sends Chromium's
own; `HTTP_USER_AGENT` only applies to the unused JSON transport).

Signed out, job **detail** pages answer 401, so listings are readable but no
rows can be built without a session.

### Defect: 401 was treated as a block

`if status in (401, 403)` sent a 401 into the escalating access cooldown --
`HTTP 401 access break 1/4 -- 1x the 10m 00s base cooldown` -- which is 40
minutes of waiting for "please sign in".

A 401 now raises `IndeedLoginRequired`, which:

* costs **no cooldown** and skips that job immediately;
* is recorded in `failed_pages.json` as needing a session;
* after `MAX_LOGIN_REQUIRED_SKIPS = 5` consecutive ones, stops the run
  (exit code 3) and prints `python3 save_indeed_login.py`, instead of
  crawling all night and saving nothing;
* resets its counter as soon as one job page reads normally.

`IndeedLoginRequired` is deliberately **not** a subclass of
`IndeedAccessDenied`, so a 401 can never be caught as a refusal again -- a
test asserts that.

### A real ceiling on the collection rate

The operator asked for "no more than 3 jobs an hour". The gaps could not
deliver that: with `JOB_GAP 20-30s` inside a city and `CITY_GAP 2000-4000s`
between cities, a city with 30 pages collected hundreds of jobs in a burst
and then idled for an hour. Burst-then-idle is the shape that provokes rate
limiting, and its peak rate is nothing like three an hour.

```python
MAX_JOBS_PER_HOUR = 3        # one job every ~20 minutes, flat
```

A rolling-window gate checked **before** every job page, so the ceiling
holds however the gaps are set. The window lives in
`crawl_cycle_state.json`, so stopping and restarting cannot burst past it:
after three reads the gate reports `59m 59s`, and a simulated restart still
reports `59m 59s`.

The gaps had to come **down** to match: a 67-minute city gap is longer than
the cap's own 20-minute spacing, so it, not the cap, would have set the rate
and the collector would have fallen short of three an hour.

```python
CITY_GAP_MIN_SECONDS = 60.0     # was 2000
CITY_GAP_MAX_SECONDS = 180.0    # was 4000
KEYWORD_GAP_MIN_SECONDS = 120.0 # was 3000
KEYWORD_GAP_MAX_SECONDS = 300.0 # was 6000
```

A test asserts the gaps stay below the cap's spacing, so this cannot drift
back. Net effect: a much lower peak rate than before, a steady 3 jobs/hour,
which with the 23:00-05:00 night pause is **54 jobs a day**.

For reference, the Naukri reference project's own gaps are 1s / 5s / 15s --
it is not paced to three an hour; that is simply its observed throughput.

17 tests cover this in `tests/test_error_handling.py`
(`TestLoginRequiredIsNotABlock`, `TestHourlyJobCap`).

### Not done, and why

Masking `navigator.webdriver`, spoofing the browser fingerprint and rotating
IPs or proxies were all declined. They are circumvention of an access
control, the project's own banner promises none of them, and in this case
they would not have worked either -- the block was on the session.

---

## 6e. The decisive signed-out test, and a stop that took five minutes

### The measurement that settled the 403

Two accounts had now been refused, after the IP had been changed twice.
To find out what the refusal was actually attached to, one request was
sent with no session at all -- the same URL, the same machine, the same
public IP, nine minutes after the signed-in attempt:

```
11:22  signed in  (second account)  ->  403 on the FIRST listing request
11:33  signed out (no cookies)      ->  HTTP 200, 16 cards parsed
                                        title: "200 Cloud Administrator
                                        Job Vacancies in Delhi | Indeed"
```

Same URL, same IP, same Chromium build, eleven minutes apart. That rules
out the three things people usually blame:

| Suspected cause | Ruled out by |
|---|---|
| The public IP | The same IP served HTTP 200 signed out |
| Playwright / the browser | The same browser was served signed out |
| One bad account | Two accounts, both refused -- the second faster |

What is left is the session itself. An authenticated session running
automated searches is refused; the same automation signed out is served
normally. Note the direction of the second account's result: it was
refused on its very first listing request, where the first account had
managed about nine minutes of collection. Signing in again, with any
account, is not a route back to collection.

### Consequence for the project

Signed-out collection works, and it reaches the listing pages only.
That fills 25 of the 32 columns. The seven that need the job detail page
are unavailable without a session, and `/viewjob` returns 401 signed out:

```
apply_url  skills_required  experience_min_years  experience_max_years
company_website  expiry_date  application_deadline
```

No code change was made for this. Which of the two routes to take --
listing-only collection, or a licensed data feed through the
`HttpTransport` that is already built and tested -- is a decision about
what is allowed to land in the production database, not a performance
question.

### Defect: a stop during a pause took up to five minutes

`./indeed stop` on a running collector printed this:

```
Stopping (pid 24927) gracefully...
Still running after 120s -- sending SIGKILL.
```

`sleep_long()` slept in 300-second steps and only checked
`_SHUTDOWN_REQUESTED` between them. During a 10-minute access cooldown a
stop could therefore go unnoticed for up to five minutes, and the
launcher gave up and sent SIGKILL at 120 seconds. A SIGKILL mid-write is
exactly the case the atomic Excel writes exist to survive, so nothing was
lost -- but the collector was being killed rather than stopping.

The loop now wakes every three seconds and keeps a deadline on the
monotonic clock, so the pause is still exactly as long as it was, and the
log still only reports progress every five minutes:

```
before:  stop noticed after up to 300s  ->  SIGKILL at 120s
after :  stop noticed after 3.0s
```

Two tests cover it in `tests/test_error_handling.py`: one asserts a stop
is seen in under 15 seconds during a 600-second pause, the other asserts
a short pause still waits its full time.

### Live sessions could be committed to git

`.gitignore` covered `indeed_auth.json` but not the `indeed_auth.json.bak.*`
files that parking a session creates, so `git add .` would have committed
a full set of live cookies. Both patterns are now ignored, along with
`*_auth.json` generally.

## 6f. Listing-only mode: the url is copied, the page is not opened

### What was asked for

> just copy the url and paste it into excel field not open that job detail
> page in the new tab

That is exactly right, and it is cheap, because the job page's url is
already on the listing card. The card carries `data-jk` (the job key) and
the title link's href, and `BrowserTransport._read_listing_cards` already
turns either of those into the job's canonical page url:

```
card data-jk = d71599c0a9fdd97d
  -> source_url = https://in.indeed.com/viewjob?jk=d71599c0a9fdd97d
```

Nothing has to be fetched to record it. The old code fetched it anyway --
one request per job -- to read the description, the skills and the apply
button, and those requests were the ones returning 401 and 403.

### The switch

```python
COLLECT_JOB_DETAILS = False            # ships off: job pages are not opened
APPLY_URL_FALLS_BACK_TO_JOB_PAGE = True
```

`process_job()` now returns through `save_listing_only_row()` before the
client is ever asked for a detail record. A test asserts this by handing
`process_job` a client whose `get_job_detail` raises `AssertionError`:
the row is still saved, so no request was made.

`apply_url` would otherwise be blank. The job page is where the Apply
button lives, so the card's url is written there too -- marked
`_apply_url_kind = "job_page"` so the provenance is not lost. Set the flag
`False` to leave the column genuinely empty instead.

### What a card alone fills: 26 of 32 columns

Measured on a real card shape:

| Filled from the card | Blank |
|---|---|
| job_id, internal_job_id, job_title, company_name, job_description (the snippet), salary_min/max/currency, location, city, state, country, remote_type, employment_type, job_category, posted_date, apply_url, source_url, updated_at, fetched_at, is_fake, is_active, source_type, source_platform, score | skills_required, company_website, expiry_date, application_deadline |

The three experience columns sit in between: a snippet that happens to say
"3-6 years" fills them, one that says nothing leaves them blank.

`source_type` is written as `job_portal_listing` rather than `job_portal`,
so a row collected without its job page is distinguishable downstream.

### A data defect this surfaced

`parse_experience()` returns its SOURCE TEXT when it finds no year count.
In the detail path that meant a whole job description could end up in a
column named `experience_required`; from a card it meant the snippet was
duplicated there. The listing path now blanks `experience_required` when
no years were actually parsed -- the snippet already lives in
`job_description`, and duplicating it under a misleading name would make
the sheet claim something was stated when it was not.

**The same fallback still applies to the detail path**, which is shared
code and out of scope here. It is worth fixing separately.

### Pacing had to become mode-aware

`MAX_JOBS_PER_HOUR = 3` exists to bound REQUESTS. In listing-only mode a
job costs no request -- all 16 cards arrived in the one listing request
already made -- so counting cards against a request cap would idle for
over five hours while fetching nothing. The cap and the 20-30 second
between-jobs gap now apply only when job pages are actually opened. The
listing requests themselves stay bounded by `REQUEST_GAP_SECONDS` and the
city and keyword delays.

### The run now says which mode it is in

```
[MODE]   LISTING ONLY -- job pages are NOT opened. The card's job page url
         is written to apply_url and source_url. Always blank:
         skills_required, company_website, expiry_date, application_deadline.
         Blank unless the snippet happens to state it: experience_required,
         experience_min_years, experience_max_years
```

Eight tests cover the mode in `tests/test_excel_output.py`.

## 6g. Signed out, as a guarantee

### The setting

```python
USE_SAVED_LOGIN = False
```

Listing pages do not need a session, and attaching one is what gets the
request refused:

```
signed in  (either of two accounts)  ->  403 on the first request
signed out (no cookies at all)       ->  HTTP 200, 16 cards
```

With `COLLECT_JOB_DETAILS = False` as well, there is no page left in the
run that wants a login, so `indeed_auth.json` is never read and
`save_indeed_login.py` does not need to be run at all.

### Why it needed a code change, not just a flag

"Signed out" was previously only *what happened* when the auth file was
missing -- the decision was three inline conditions in `start()`. An auth
file left lying in the project (and the session-parking workflow creates
`indeed_auth.json.bak.*` files full of live cookies) would silently
re-attach a session and turn every request into a 403.

The decision is now `BrowserTransport._saved_session_path()`, which returns
the file to attach or `None`, so being signed out can be asserted directly
rather than inferred. Three tests cover it: an existing auth file is
ignored when login is off, the same file IS used when it is on, and a
missing file also means signed out. A fourth asserts the two settings stay
consistent -- listing-only mode must not ship with a login enabled.

### A test that pinned a tunable (again)

`test_the_saved_session_file_is_loaded_not_created` asserted
`config.use_saved_login` was True. That is a tunable, and the moment it
was turned off the test failed for no good reason -- the same mistake as
the night-window and `MIN_LISTING_PAGES` tests earlier in this document.
Its real intent was that this code only ever READS a session file the
operator produced themselves. It now asserts that, plus that no
`password=` style call exists anywhere in the client, plus that the
`.bak.*` files are gitignored.

### What was ruled out, and what is left

Two more theories tested and eliminated:

| Theory | Test | Result |
|---|---|---|
| Our cookie-decline click trips it | `cookie_banner_action = "ignore"`, nothing clicked | 200 then 403 -- identical |
| We are simply going too fast | identical url, 7 minutes apart | 200 then 403 |

Combined with the earlier measurements, every externally adjustable factor
is eliminated: the query, the city, the account, the IP, the request rate,
and our own consent click. What remains is that Chromium under Playwright
reports `navigator.webdriver === true`, which a page reads on load.

Changing that means spoofing the browser's identity, which this project
does not do -- the banner in `find_indeed_job.py` promises no fingerprint
spoofing, and relaunching the browser with a clean profile for each page
would be the same evasion by another route. So the honest ceiling for
signed-out collection is **one listing page per browser run**, around 16
jobs, and the route to volume is a licensed feed through the `HttpTransport`
that is already built and tested.

## 6h. Why only one listing page, and a wasted request that caused it

### What the log actually said

```
16 job(s) reported -> 2 page(s) to crawl (floor 1, cap 3)
page 1: 16 listing(s) (16 new)
...
page 1 done: 16 job(s) in 7s (15 saved so far for this city)
[BROWSER] opening .../jobs?q=Server+Administrator&l=Delhi&start=10&sort=date
 📌 queued for a later retry (access denied: HTTP 403 ...): Server Administrator|Delhi|p2
```

Two separate things, easy to conflate:

1. **This search only had two pages to begin with.** The header reported 16
   results. `MAX_LISTING_PAGES = 3` was never the limit -- the result count
   was. Different keyword-and-city pairs report thousands and do plan three.
2. **Page 2 was refused.** That is the 403 documented in §6e-§6g: the
   second request from a browser gets refused regardless of pacing, query
   or account.

### The wasted request

Worse: **page 2 could not have contained anything new.** The header said 16
results and page 1 displayed all 16 of them. The site paginates in steps of
`INDEED_PAGE_STEP = 10` while a page SHOWS about 16 cards, so
`page_count(16, step=10)` plans two pages -- and `start=10` would return
cards 11 to 16, every one of them already collected.

The old code requested it anyway and let `STOP_ON_PAGE_WITH_NO_NEW_JOBS`
notice afterwards. That is one wasted request per combo, and on this site a
wasted request is exactly what earns the 403 and the ten-minute cooldown
sitting behind it.

`run_combo()` now counts what actually arrived rather than trusting the
step:

```python
if total and len(seen_in_combo) >= int(total):
    log("     page %d already held all %s reported result(s) -- "
        "no page %d request needed" % ...)
```

```
before:  page 1 (16 cards)  ->  request page 2  ->  403  ->  10 min cooldown
after :  page 1 (16 cards)  ->  no request      ->  next city
```

Two tests in `tests/test_keyword_cycles.py` hold the line in both
directions: with 16 reported and 16 arrived, exactly one request is made;
with 40 reported and 16 arrived, page 2 IS requested, because it holds real
results. The second test matters more than the first -- an over-eager stop
would silently halve the collection.

### The job-card gap is now 5 to 10 minutes

```python
JOB_GAP_MIN_SECONDS = 300.0     # 5 minutes
JOB_GAP_MAX_SECONDS = 600.0     # 10 minutes
```

Drawn fresh per card, so 2000 draws average 7.4 minutes across the full
5-to-10 range.

The gap also applies in listing-only mode again. It had been skipped there,
reasoning that a card costs no request -- true, but beside the point. The
wait is what spaces out the **listing page** requests, which are the ones
being refused:

```
15 cards of waiting -> page 2 arrives 75 to 150 minutes after page 1,
                       instead of the 7 seconds that got it refused
```

Whether that interval is long enough is **not established**. A 7-minute gap
was measured and still refused (§6g); 75 minutes has not been tested, and
the evidence so far points at `navigator.webdriver` rather than timing, so
it may well make no difference to the 403.

The last card of a page is not waited on -- the city and keyword delays
take over at that boundary, so the two do not stack.

`MAX_JOBS_PER_HOUR = 3` stays inactive in listing-only mode. At 5-10
minutes a card the run already collects about 8 an hour, and a 3-per-hour
request cap would force 20 minutes between cards, overriding the 5-to-10
that was asked for.

## 7. Two unrelated defects found while measuring

**`BrowserTransport.kill()` was too broad.** The immediate-stop path (Ctrl+C)
killed *every direct child process* of the collector, read out of
`/proc/self/task/*/children`. That would also kill an unrelated subprocess
the collector happened to own — and it was observed killing another
transport's browser during the test run. It now records the pid of the
Playwright driver it launched (`_driver_pid`) and kills only that; the browser
goes with it, because the browser is the driver's own child. The `_child_pids`
helper was deleted rather than left as dead code. A test spawns an innocent
child process and asserts `kill()` leaves it alone.

**A brittle test.** `test_night_window_detection` hardcoded the 00:00–06:00
window. The night window is an operator setting and had been changed to
23:00–05:00, so the test failed for a configuration change that was perfectly
valid. It now derives its expectation from `NIGHT_START_HOUR` /
`NIGHT_END_HOUR`.

Also tidied: the new JavaScript constants are raw strings (`r"""`), which
removes a `DeprecationWarning: invalid escape sequence '\s'` that the test
run was emitting.

---

## 8. Tests added

In `tests/test_visible_browser.py` (16 new tests, 72 in that file now):

| Class | Pins down |
|-------|-----------|
| `TestPageReadingCost` | a listing page and a job page each cost at most 2 `evaluate` calls and **zero** per-element reads, whatever the card count; a whole job read stays under 10 round trips |
| `TestRenderWaitsAreBounded` | the candidates are joined into one selector with one timeout; `:has-text` selectors are excluded from the join; an unreadable listing/job page costs one timeout, not N |
| `TestFastAndSlowReadersAgree` | the in-page reader and the per-element fallback produce identical fields; the fallback actually runs when `evaluate` fails |
| `TestConsentBannerProbe` | the decline control is found by id and by label; an **accept** button is never tagged; a page with no banner is silent |

Plus `test_kill_only_touches_the_driver_it_launched` in
`tests/test_error_handling.py`.

Suite total: **327 tests**.

---

## 9. Honest status

* The performance work is done and measured; the numbers in §5 are from real
  runs, not estimates.
* The full suite passed on two of three consecutive runs and reported **one
  intermittent error on the third**. I had started isolating it when this
  document was requested, so it is **not yet identified or fixed**. It
  appeared only in the full-suite run (several browsers being started and
  stopped in one process), never when a file was run on its own, which
  points at cross-test interference rather than at collector behaviour — but
  that is a hypothesis, not a conclusion. It should be chased down before
  this is called finished.
* No change in this document alters what is collected or what is written to
  Excel. The 32-column output, the apply-url rules, the checkpoints and the
  Excel rollover are untouched; a test asserts both read paths produce the
  same row.
