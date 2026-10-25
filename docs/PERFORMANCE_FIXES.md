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
