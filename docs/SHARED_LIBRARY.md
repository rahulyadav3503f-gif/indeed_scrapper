# `new_mylib.py` and `target_audience_roles.py` are now shared with Naukri

Changed: 25 October 2026

---

## 1. What changed

Both files in this project are now **byte-identical to the Naukri project's
copies**:

```
/home/ubuntu/check_indeed/new_mylib.py               ==  /home/ubuntu/check_naukri/naukri_script/new_mylib.py
/home/ubuntu/check_indeed/target_audience_roles.py   ==  /home/ubuntu/check_naukri/naukri_script/target_audience_roles.py
```

| File | Before | After |
|------|--------|-------|
| `new_mylib.py` | 399 lines (trimmed rewrite) | **2,701 lines** (the Naukri file) |
| `target_audience_roles.py` | 318 lines (Naukri + extra docstring) | **304 lines** (the Naukri file) |

They are still **copies inside this project**, not imports from the Naukri
directory. Nothing in the Indeed scraper reads anything out of
`/home/ubuntu/check_naukri/` at runtime, so the two scrapers can still be
started, stopped, moved and reset independently. What they now share is the
*code*: `cp` the file from either project to the other and nothing changes.

The Naukri project was not modified.

---

## 2. Why the main script needed one new line

`new_mylib.py` ships a 432-entry `"IT Department"` list, and the reference
scraper narrows it to the 85-role target plan **in its own main script**
(`find_naukri_job.py:1147`). The previous trimmed copy did that narrowing
inside `new_mylib.py` itself, so `find_indeed_job.py` never had to.

With the shared file in place, the plan arrived unnarrowed:

```
after the swap, before the fix :  407 keywords × 23 cities = 9,361 combos/cycle
                                  (and it included the roles the 80/20 plan excludes)
```

So the same line now lives in `find_indeed_job.py`, right after the imports,
with the reason written next to it:

```python
if build_role_plan is not None:
    job_by_department = {"IT Department": build_role_plan()}
```

`ROLE_CATEGORY` is now **derived** from that dictionary
(`next(iter(job_by_department), "IT Department")`) instead of being a second
hardcoded literal, so the department name cannot drift out of step with the
plan.

Verified after the change:

```
ROLE_CATEGORY    : IT Department
keyword plan     : 85 keywords
role plan split  : (68, 17, 80.0)
cities for rank  : 23
combos per cycle : 1,955
first five       : Desktop Support Engineer, Desktop Support Technician,
                   IT Support Engineer, IT Support Specialist,
                   IT Support Technician
```

Identical to what it was before the swap.

---

## 3. What else the shared file brings with it

### `gtts` is now a hard dependency

`new_mylib.py` does `from gtts import gTTS` at module level (line 2649) for a
`speak()` helper no crawl uses. Without it, `import new_mylib` raises
`ModuleNotFoundError`, so `gtts` was installed into the project venv and
added to `requirements.txt`. `psutil` (for `check_battery`) and `Levenshtein`
were already present.

```bash
pip install gtts        # already done in venv/
```

### Code the Indeed collector does not call

`speak()`, `check_internet()`, `isinternet()`, `check_battery()`,
`city_by_langs`, `city_to_lang()`, `file_langs`, `load_json()`,
`dump_json()`, `readit()`, `writeit()`, and the module-level `rank = 1` /
`num_sys = 3`.

The module-level `rank`/`num_sys` are worth knowing about: they are **not**
the ones this collector uses — `find_indeed_job.py` has its own `rank = 1`
and `num_sys = 5` in its configuration block, and the main script imports
only the three names it needs:

```python
from new_mylib import city_by_states, city_to_state, job_by_department
```

A `from new_mylib import *` anywhere would overwrite `num_sys` with `3` and
silently re-shard the city slice. Don't write one; the import above is the
only one in the project.

### ⚠ Credentials are now in this repository too

`new_mylib.py` contains `file_langs`, which holds **nine plaintext email app
passwords** for `recruitment.*@evisiontechnoserve.com` (around line 2587).
Sharing the file means those credentials are now committed to **two** git
repositories instead of one.

Nothing in the Indeed collector reads `file_langs`. The recommendation
stands regardless of this change: rotate those app passwords and read them
from environment variables in both projects. I have not touched them,
because editing them changes the Naukri file and would break the byte-for-byte
sharing you asked for — it needs doing in both projects at once, and that is
your call.

---

## 4. Tests that had to change, and why

| Test | Was | Now |
|------|-----|-----|
| `test_city_distribution` | imported `all_crawl_cities` from `new_mylib` | the helper does not exist in the shared file; the test flattens `city_by_states` itself and still asserts the main script's flattener agrees |
| `test_visible_browser.TestConfiguration` | asserted `BROWSER_SLOW_MO_MS == 300` | it is 350 now (raised in the field after a 429). The two switches that were an explicit requirement (`BROWSER_VISIBLE = True`, `BROWSER_HEADLESS = False`) are still asserted exactly; the tunable numbers are checked for being sane instead of being a particular value |

Both were the same kind of defect: a test pinning a value that an operator is
expected to tune. The night-window test had the same problem last week.

Everything else passed unchanged — the city list, the state resolver, the
85-keyword plan, the non-IT filter, the 32-column output, the Excel rollover
and the checkpoints are all unaffected, which is the point: the shared file
carries the same data the trimmed one did.

---

## 5. A production bug found while chasing a flaky test

The intermittent test error recorded as unresolved in
`docs/PERFORMANCE_FIXES.md` §9 turned out to be a **real defect in the
collector**, not test noise.

`_require_started()` checked only its own `started` flag:

```python
if not self.started:
    self.start()
```

If Chromium died underneath the collector — killed by the kernel under memory
pressure, or simply crashed — the flag stayed `True`. Every page load after
that failed with *"Target page, context or browser has been closed"*, each
failure was filed as a retryable page, and the run carried on cheerfully
collecting nothing until something else restarted it. One browser crash
became a wasted cycle.

It now checks whether the browser is actually answering, and relaunches it:

```python
def is_alive(self):
    return bool(self.started and self.browser
                and self.browser.is_connected())
```

`_require_started()` tears down a dead browser and starts a fresh one,
logging `the browser is gone (crashed or was killed) -- relaunching it`.

Five tests in `TestBrowserSelfHealing` cover it: a live browser reports
itself alive, an unstarted one does not, a closed one is detected, the next
search relaunches it rather than raising, and the relaunched browser's pages
work.

---

## 6. How to keep the two files in step from now on

They are identical today. To copy a change either way:

```bash
# Naukri -> Indeed
cp /home/ubuntu/check_naukri/naukri_script/new_mylib.py /home/ubuntu/check_indeed/
cp /home/ubuntu/check_naukri/naukri_script/target_audience_roles.py /home/ubuntu/check_indeed/

# Indeed -> Naukri
cp /home/ubuntu/check_indeed/new_mylib.py /home/ubuntu/check_naukri/naukri_script/
cp /home/ubuntu/check_indeed/target_audience_roles.py /home/ubuntu/check_naukri/naukri_script/
```

To check whether they have drifted:

```bash
cmp /home/ubuntu/check_naukri/naukri_script/new_mylib.py /home/ubuntu/check_indeed/new_mylib.py
cmp /home/ubuntu/check_naukri/naukri_script/target_audience_roles.py /home/ubuntu/check_indeed/target_audience_roles.py
```

Silence means identical.

**Two things to remember when editing the shared file:**

1. Adding to `job_by_department` does **not** widen either crawl — both main
   scripts narrow it to `build_role_plan()`. The roles live in
   `target_audience_roles.py`.
2. `city_by_states` is **positional**. `get_cities_by_rank()` slices it, so
   inserting or reordering a city re-shards every rank in both fleets, and a
   cycle in progress will warn that its saved city list has changed and
   finish against the old one.
