# INDEED SCRAPER — HTTP 403 ROOT CAUSE INVESTIGATION

| | |
|---|---|
| **Report date** | 2026-10-30 |
| **Primary target** | `abhinav@192.168.1.123:/home/abhinav/indeed_script` (rank 3 / 5) |
| **Comparison hosts** | `192.168.1.121` (rank 1), `.122` (rank 2), `.124` (rank 4), `.125` (rank 5) |
| **Scope** | Read-only evidence-based investigation. No fixes applied. |
| **Investigation type** | Static code analysis + existing logs, checkpoints and saved diagnostic state |

> All line numbers refer to the **deployed** files on `192.168.1.123`, which were copied
> read-only to a scratchpad for analysis. The deployed `find_indeed_job.py` does **not**
> match any commit in this repository (see §I, "Deployment drift").

---

## A. Executive Summary

1. **The 403 is real.** It is `response.status` from the main-frame navigation of
   `page.goto()`. It is not a missing-DOM-element guess, not a challenge-page heuristic,
   and not a robots refusal — those paths produce different messages and different status
   values. The message stored in `failed_pages.json` is uniquely produced by
   `indeed_client.py:1446-1448`, which only fires on a literal 401/403.

2. **Authentication is broken, but it does not explain *this* failure.** The saved
   session's bearer/PPID JWTs expired on 2026-10-29 14:58:54 UTC — about 13 hours before
   the failure. However `/jobs` search listings are public, and the machine holding an
   **8-days-more-expired** session (`.122`) was still receiving listing **HTTP 200** on the
   same morning. Expired login cannot be the cause of a listing-level 403.

3. **The single strongest signal is session/IP reuse at fleet scale.** One hand-distributed
   `indeed_auth.json` — including a Cloudflare `cf_clearance` bot-clearance token — is
   `scp`'d to five collectors on one LAN (one public IP). The fleet now runs **two different
   Indeed accounts that share the same verified phone number**. Access refusal has escalated
   over four days: job-detail pages first (Oct 26), then listing pages (Oct 27 onward). That
   trajectory is consistent with a server-side/edge access decision against this
   IP + session + account cluster.

4. **The exact server-side reason cannot be proven from local evidence**, because the code
   throws away the response URL, the response headers (`server`, `cf-ray`, `cf-mitigated`)
   and the body the instant it sees a 403. There is no stored artifact that identifies
   *which* layer refused or *why*.

5. **Separately, real local defects were found** — including the collector requesting
   **fabricated job keys** (`123456789abcdef0`, `abcdef0123456789`) against the live site,
   and a pagination-arithmetic inconsistency. These cause a measurable share of the 403s on
   `/viewjob` and are fixable locally.

6. **The entire Indeed fleet is currently stopped** — zero collector processes on
   `.121`–`.125`.

---

## B. Exact Failure Point

The call chain for the reported failure:

| Step | Location |
|---|---|
| 1. Listing loop issues the request | `find_indeed_job.py:1574` — `jobs, total = client.search_jobs(keyword, city, page_number)` |
| 2. Client delegates to transport | `indeed_client.py:2420` — `self.transport.search(...)` |
| 3. Browser transport builds URL + navigates | `indeed_client.py:1487` (`live_search_url`), `:1498` (logs "opening"), `:1499` (`self._goto(...)`) |
| 4. Navigation | `indeed_client.py:1435` — `response = page.goto(url, wait_until="domcontentloaded")` |
| 5. Status read | `indeed_client.py:1443` — `status = response.status if response is not None else 200` |
| 6. **← FAILURE RAISED HERE** | **`indeed_client.py:1445-1448`** — `if status in (401, 403): raise IndeedAccessDenied(...)` |
| 7. Caught, recorded, escalated | `find_indeed_job.py:1575-1582` — `record_failed(..., recovery_status="manual")` then `raise AccessBlocked` |
| 8. Orchestrator skips + cools down | `find_indeed_job.py:2119-2137` (skip to page+1), `:2182-2186` (10-minute break) |

Startup flow before that point:
`main()` `find_indeed_job.py:2050` → `build_client()` `:1469` → `preflight()`
`indeed_client.py:2540` → `print_startup_banner()` `:1969` → `run_crawl_cycle()` `:1825` →
`run_combo()` `:1547` → the loop at `:1568`.

---

## C. HTTP Status Verification

**Verdict: a genuine HTTP response status from the main navigation.** Not a
script-generated classification.

Evidence:

- `indeed_client.py:1443` takes the status from the Playwright `Response` object returned by
  `page.goto()`. Playwright returns the response of the **final** document in the redirect
  chain, so a 403 means the final main-frame document was 403.
- The "response is None" case **defaults to 200**, not 403 — so a navigation that returns no
  response object cannot fabricate a 403.
- Navigation *exceptions* are converted to `IndeedNetworkError` at `:1436-1441`, a different
  class with a different message ("navigation to … failed"). `.122`'s history shows dozens of
  those, clearly distinguishable.
- The challenge-page detector (`:1469-1476`) only runs **after** a non-error status and raises
  a different message ("a verification/challenge page was served for …"). The recorded message
  is not that one.
- The robots refusal (`:1429-1433`) uses `status=999`. Not that one either.
- The stored reason string in `failed_pages.json` —
  `"HTTP 403 for search 'Desktop Support Engineer' in 'Vapi' page 1 -- access refused; this is respected, not worked around"`
  — is byte-for-byte the format string at `indeed_client.py:1447-1448`, reachable **only** from
  `if status in (401, 403)`.

**What cannot be determined:** which URL finally answered, whether a redirect occurred, and
which layer refused. `_goto()` raises immediately and never records `response.url`,
`response.headers`, or the body. A grep for body/screenshot capture on failure found none.
The 403 is proven; its origin is not locally attributable.

**Timing:** the error occurs *after* navigation completed (a response object existed) and
*before* any DOM reading — `_wait_for_cards()` at `:1501` is never reached.

---

## D. Authentication Findings

*(metadata only — no cookie values, tokens, emails, or phone numbers are reproduced)*

| Check | Result |
|---|---|
| 1. File exists | Yes — `indeed_auth.json`, 21,921 bytes, mtime 2026-10-30 09:27:39 |
| 2. Valid JSON | Yes |
| 3. Playwright storage-state schema | Yes — `cookies` (53) + `origins` (4) |
| 4. Configured path correct | Yes — `auth_file=project_path(AUTH_FILE)` (`find_indeed_job.py:1459`) resolves against the script directory, so cwd is irrelevant |
| 5. Context created with it | Yes — `indeed_client.py:1210-1211` sets `context_options["storage_state"]`, applied at `:1218` |
| 6. Cookies relevant to the target domain | Yes — 28 on `.indeed.com`, 3 on `in.indeed.com`, plus `secure.`/`profile.`/`apis.` subdomains |
| 7. **JWTs expired** | **YES.** Bearer token and `PPID`: `iat` 2026-10-29 13:58:54 UTC, `exp` 2026-10-29 **14:58:54 UTC** (1-hour lifetime). `__Secure-PassportAuthProxy-OauthExpires` agrees. All ~13 h stale at the 09:37 IST (04:07 UTC) failure. |
| 8. Refresh token present | A `__Secure-PassportAuthProxy-RefreshToken` cookie exists, but its value is only **11 characters** — implausibly short for a usable refresh credential, even though the bearer scope does include `offline_access`. Treat as effectively unusable. |
| 9. Script verifies signed-in status | **No.** A grep for any signed-in / logged-in / account-menu check across the whole project returned **nothing**. |
| 10. Does `saved login: indeed_auth.json (loaded)` prove anything? | **Almost nothing.** `find_indeed_job.py:2012-2014` prints it from `os.path.exists(auth_path)` alone. The `preflight()` text "with a saved login" (`indeed_client.py:2559-2563`) uses the same existence test. Neither parses the file, checks expiry, nor confirms a session with Indeed. |

Also present: `cf_clearance` (Cloudflare bot-management clearance, 348 chars) and five
`__cf_bm` cookies which are **already past their expiry** (30-minute lifetime, captured
2026-10-29 14:28 UTC). Expired cookies are simply not sent by Chromium, so they are inert —
but they date-stamp the capture.

### The four distinct states, as required

| State | Verdict |
|---|---|
| Storage-state file loaded | **TRUE** — proven (file exists, valid schema, passed to `new_context`) |
| Authentication tokens present | **TRUE** — bearer, PPID, SHOE, SOCK, CTK, rememberMe all present |
| Tokens not expired | **FALSE** — bearer and PPID expired 2026-10-29 14:58:54 UTC |
| Server-confirmed authenticated session | **UNKNOWN** — never checked by the code, and no server response is retained |

**Authentication failure classification: CONFIRMED as a fact (tokens are expired), but
RULED OUT as the cause of the listing 403** — see the controlled contrast in §G.

### Account / session provenance

Comparing the deployed file against the one still on `.121`/`.122`/`.124` (hashed
comparison; identities withheld):

- Two different files are in production: `ac4b7125…` (21,921 B) on **.123 and .125**;
  `8f3995fe…` (19,723 B) on **.121, .122, .124**.
- They are **different Indeed accounts** — `sub` differs, `email` differs — but the
  **`phone_number` claim is identical**. Two accounts, one verified phone.
- Account ages: old file's account `created` 2026-08-28; new file's account `created`
  2025-03-01.
- Dev-box shell history (`192.168.1.16`, `evisionserver`) shows the file being hand-handled
  and fanned out:
  - `nano indeed_auth.json`
  - `scp …/indeed_auth.json abhinav@192.168.1.122:/home/abhinav/indeed_script/`
  - `scp …/indeed_auth.json abhinav@192.168.1.121:/home/abhinav/indeed_script/`
- The deployed file's MD5 is **identical** to the dev box's copy
  (`ac4b712559accddb7b003f1899916350`), and `save_indeed_login.py` appears **nowhere** in
  `.123`'s shell history. The session was captured elsewhere and copied in — it was never
  established from this machine or this browser profile.

Both files are otherwise structurally sound, real captures (both contain a full
`cf_clearance`, both show `auth: emailOtp`, `last_auth_level: STRONG`), so the `nano` edit
does not appear to have corrupted them.

---

## E. Browser and Navigation Findings

Chromium and Playwright are **functioning correctly**. This is ruled out as a cause.

| Question | Finding |
|---|---|
| Chromium executable | `/usr/lib/chromium/chromium`, **Chromium 142.0.7444.175** (system build). Confirmed via `p.chromium.executable_path`. |
| Playwright | Kali/Debian system package `python3-playwright` at `/usr/lib/python3/dist-packages/playwright`, version string `0.0.0`. The project `venv` was created with system site-packages, so it resolves to the same system package. Bundled browsers exist in `~/.cache/ms-playwright/` (chromium-1243/1248) but are **not** what gets launched. |
| Python | 3.14.7 |
| Headed or headless | **Headed.** `BROWSER_HEADLESS = False` (`find_indeed_job.py:258`), `launch(headless=False, slow_mo=350)` at `indeed_client.py:1179-1192`. |
| Xvfb | Present and working. The earlier `BrowserUnavailable` in `indeed.log` was the display guard at `indeed_client.py:1168-1175` firing because neither `DISPLAY` nor `WAYLAND_DISPLAY` was set. `xvfb-run -a -s "-screen 0 1366x768x24"` resolved it. **This is a separate, already-fixed problem and is unrelated to the 403.** |
| Context persistent or fresh | **Fresh every time.** `browser.new_context()` at `:1218` — never `launch_persistent_context`. No profile directory, so no cookie, cache or local-storage continuity between runs. |
| Saved state reused | Yes, via `storage_state` at `:1211` — but reloaded from the **same static snapshot** on every launch, so nothing Indeed sets during a run survives. |
| Listing and detail share a context | Yes — two tabs from one context (`self.page`, `self.detail_page`, `:1225-1226`). Correct. |
| Context closed/recreated | `stop()`/`close()` at `:1269`. The orchestrator calls `client.close()` on every pause and `client.open()` on resume (`find_indeed_job.py:2107`, `:2113`, `:2140`), so **each 10-minute access break discards the whole browser and reloads the stale snapshot.** |
| Session changes preserved | **No.** `context.storage_state()` is never written back — only `save_indeed_login.py:98` ever writes it. Any refreshed cookie or new clearance token Indeed issues mid-run is destroyed at the next `close()`. |
| Transport | Browser navigation only. The `requests`-based HTTP transport (with its own `User-Agent` at `indeed_client.py:564`) is not in play; `HTTP_USER_AGENT` never reaches the browser context. |
| Proxy | **None.** No `proxy=` anywhere; the only matches are comments stating that proxy rotation is not done. |
| Environment overrides | `INDEED_BROWSER_SEARCH_URL` / `INDEED_BROWSER_DETAIL_URL` are read at `find_indeed_job.py:1447-1450` but are unset, so the built-in templates are used. Confirmed by the logged URL. |

No fingerprint manipulation exists anywhere: the context sets only `viewport` — no
`user_agent`, `locale`, `timezone_id`, `extra_http_headers`, and no `navigator.webdriver`
tampering. Chromium is launched with Playwright's standard flags, which include
`--enable-automation`. That is the honest, unmodified configuration.

**Note on `cf_clearance`:** Cloudflare clearance tokens are issued against the specific IP
and client fingerprint that earned them. This one was issued 2026-10-29 ~13:58 UTC on an
unknown machine and is now replayed from a different Chromium on different hosts. A clearance
token that no longer matches its issuing conditions is a plausible trigger for an edge 403 —
but this cannot be confirmed locally, because no response headers are captured.

---

## F. Search URL Findings

**No defect in the URL that would explain a 403.** The URL is well-formed.

- Template: `"{base}/jobs?q={keyword}&l={city}&start={start}&sort=date"`
  (`find_indeed_job.py:79-81`).
- Built at `indeed_client.py:269-288`. Keyword and city are encoded with `quote_plus`
  (`:275-276`) → `Desktop Support Engineer` → `Desktop+Support+Engineer`. Correct.
- Pagination: `start = (page_number - 1) * self.page_step` (`:272`) → page 1 ⇒ `start=0`.
  Correct.
- Parameters are not duplicated or malformed. `template.format(**values)` with a `KeyError`
  guard at `:285-288`.
- The browser opens exactly the configured URL — the logged
  `https://in.indeed.com/jobs?q=Desktop+Support+Engineer&l=Vapi&start=0&sort=date` matches
  the template exactly.
- Detail URLs: the listing card's **own** `source_url` is preferred
  (`indeed_extractor.py:239-243`); `INDEED_JOB_URL_TEMPLATE` is only a fallback when the card
  exposed no absolute URL (`:245-249`).

Two genuine defects found here, neither capable of causing a 403:

1. **`RESULTS_PER_PAGE = 16` contradicts `INDEED_PAGE_STEP = 10`**
   (`find_indeed_job.py:164` vs `:83`), and the comment on line 164 wrongly asserts they are
   equal. `results_per_page` drives page-count arithmetic (`indeed_client.py:2461-2471`) while
   `page_step` drives the `start` offset. With pages that genuinely return 16 cards (confirmed
   — `.121`'s log: `page 1: 16 listing(s)`) and `start` advancing by 10, **every page
   re-fetches ~6 cards already seen**. That is ~37% redundant listing volume, and it also
   partly defeats the "page brought nothing new → stop" rule at `find_indeed_job.py:1732`.

2. **City is sent without its state.** The URL carries `l=Vapi` while the log prints
   "Vapi, Gujarat" — the state comes from `city_state_name()` (`find_indeed_job.py:1182`) and
   is used only for display and the output row. Ambiguous city names are therefore not
   geographically disambiguated to Indeed. A data-quality issue, not an access issue.

---

## G. Historical 401 / 403 Analysis

**`.123` itself has almost no history.** It has no `.xlsx` output at all, no systemd unit
(`journalctl -u indeed` → no entries), and `indeed.log` (2,747 B) covers only the 09:27–09:30
run that died on the missing display. The 09:37 run was launched interactively under
`xvfb-run`, so **its output went to the terminal and was never written to a file.** For
`.123`, `failed_pages.json` is the only durable record of the 403.

Because the `./indeed` wrapper appends (`nohup … >>"$LOG_FILE"`, line 46), the richer history
survives on the sibling collectors. `.122` has a 4,751-line log and 75 queued failures
spanning **2026-10-25 22:55 → 2026-10-30 09:17**:

| Question | Answer (evidence) |
|---|---|
| First recorded 401 | **2026-10-27 11:35:58**, job detail `ba56ae64b2d46339` (`.122`) |
| First recorded 403 | **2026-10-26 11:15:12**, job **detail** `b6b647bd83c5ecd2` (`.122`) |
| Were 403s initially detail-only? | **Yes.** Oct 26 produced four 403s, all `/viewjob`, at 11:15:12 / 11:25:27 / 11:35:42 / 11:45:58. |
| When did listing 403s begin? | **2026-10-27 10:35:59** — `'Network Operations Analyst'` in `'Pune'` page 1. A second on 2026-10-27 16:32:12 (`'IT Administrator'` / `'Erode'`). |
| Did behaviour change after replacing `indeed_auth.json`? | **Strongly correlated, but listing 403s pre-date it.** See the interleaving below. |
| Did behaviour change after Xvfb? | **No — ruled out.** Before Xvfb, `.123` never reached Indeed (`BrowserUnavailable`, 09:30). Xvfb only made the request possible; the 403 came on the first real request afterwards. `.125` hit the same listing 403 on display `:102` without the new Xvfb invocation. |
| Errors tied to specific URLs? | **No.** 403s span many distinct keywords, cities and job keys. |
| Were HTTP 200s observed between failures? | **Yes, abundantly.** `.122` holds 7 archived workbooks and logs successful listing reads; `.121` logged `16 job(s) reported … page 1: 16 listing(s) (16 new)` on the morning of Oct 30. |

### The decisive interleaving (same morning, same public IP)

| Time (IST, 2026-10-30) | Host | Auth file | Listing result |
|---|---|---|---|
| 09:15:41 | **.125** | NEW `ac4b7125…` | **403 on the first listing request** |
| ~09:16–09:17 | .122 | OLD `8f3995fe…` | **200** — cookies declined, 7 listings read; 403 only later on `/viewjob` at 09:17:03 |
| ~09:19:40 | .121 | OLD `8f3995fe…` | **200** — 16 listings read; 403 only later on `/viewjob` |
| 09:37:29 | **.123** | NEW `ac4b7125…` | **403 on the first listing request** |

Within roughly 90 seconds, a machine carrying the **old** session got a listing 200 while a
machine carrying the **new** session got a listing 403 — from the same LAN and therefore the
same public IP. That isolates the differing variable to the **session state**, not the IP and
not the code.

**Honest caveat:** listing 403s were already occurring on Oct 27 under the *old* session. So
the new session file is not the sole precondition for a listing-level refusal — it correlates
with an immediate and so-far 100% refusal rate (2 of 2 machines, 2 of 2 first requests),
whereas the old session's listing refusals were intermittent.

### Detail-403 periodicity

`.122`'s detail 403s land at 11:15:12, 11:25:27, 11:35:42, 11:45:58 (Oct 26) and 15:02:02,
15:12:21, 15:22:39, 15:33:02 (Oct 29) — a ~10 m 15 s cadence. That is
`ACCESS_BREAK_SECONDS = 600` plus one request. The refusal is therefore **persistent across
cooldowns, not transient**: every post-break detail request is refused again.

---

## H. Request Pattern and Retry Analysis

**Ordering** (`find_indeed_job.py:1825` → `:1547`): department → keyword shuffled once per
cycle (seeded on rank + cycle, so the five ranks do not march in lockstep) → all rank cities →
pages 1..N → job cards in page order. Rank 3 = 85 keywords × 23 cities = 1,955 combos/cycle.

**Timing** (`find_indeed_job.py:182-186`, enforced by `_pace()` at
`indeed_client.py:2395-2402`): 5.0 s between source requests, 10 s between job pages, 15 s
between cities, 15 s between keywords, all with up to +35% jitter. Plus a 45-min cycle
cooldown, 3.7–4.2 h working periods with 30–60 min breaks, and a 23:00–05:00 IST night pause.
This pacing is deliberate and is **not** flagged as a fault.

### Genuine request-volume and retry defects

1. **~37% redundant listing reads** from the `results_per_page=16` / `page_step=10` mismatch
   (§F.1). Overlapping result windows mean repeated cards on every page beyond the first.

2. **Fabricated job keys requested against the live site — confirmed defect.** `.121` logged
   `[BROWSER] job page https://in.indeed.com/viewjob?jk=123456789abcdef0` → 403; `.122` logged
   the same for `abcdef0123456789`. Both strings are the sixteen hex digits `0-9a-f` each used
   exactly once, merely rotated — these are template/placeholder values, not real Indeed job
   keys (the chance of two real random keys both being permutations of the full hex alphabet is
   negligible). Cause: cards are harvested by the selector **`div[data-jk]`**
   (`indeed_client.py:1602`), which matches *any* element carrying a `data-jk` attribute
   including hidden template nodes; `extract_internal_job_id()`
   (`indeed_extractor.py:187-205`) accepts whatever it finds with **no format or plausibility
   validation**, and `build_source_url()` (`:236-250`) turns it into a `viewjob` URL. The
   collector then spends a request, a 403, a queue entry and a 10-minute access break on a job
   that does not exist. **This inflates the detail-403 count and is the clearest
   locally-fixable contributor.**

3. **Every access break throws away the browser.** `client.close()` on pause and
   `client.open()` on resume (`find_indeed_job.py:2113`, `:2140`, `:2107`) rebuild the context
   from the same stale snapshot. Any cookie Indeed refreshed during the run is discarded, so
   the collector cannot recover from a cookie-staleness condition on its own.

4. **Session state is never written back.** `context.storage_state()` is never persisted by
   the collector — only by `save_indeed_login.py:98`. A rotated token can never be retained.

5. **Session-wide escalation can be masked indefinitely.** `_distinct_denied_urls` escalates
   `scope` to `session_wide` only when more than one distinct URL has been denied
   (`indeed_client.py:2423-2424`, `:2518-2519`), but **any** successful search or detail read
   clears the set (`:2439`, `:2530`). A run that alternates "listing 200 → detail 403"
   therefore never escalates and is only stopped by the blunt `MAX_BLOCK_COOLDOWNS` counter
   (`find_indeed_job.py:2164`). That is exactly what `.121` did — eleven 403s, then "persisted
   through 3 cooldown(s)".

6. **Denied work is correctly never auto-retried.** `record_failed(..., recovery_status="manual")`
   (`find_indeed_job.py:1578`, `:801-852`) and `queue_eligible()` skipping `"manual"`
   (`:874-875`) mean no retry loop hammers a refused endpoint. This is correct and commendable.

No other duplicate or repeated-navigation defects were found; `seen_in_combo` (`:1566`),
`done_job_urls` (`:1631`), and the per-job checkpoint (`:1704`) all behave correctly.

---

## Failed-Page Handling (Phase 8 detail)

Observed state on `.123`:

- `failed_pages.json` — one entry: `"Desktop Support Engineer|Vapi|p1"`, `http_status: 403`,
  `work_type: "listing"`, `recovery_status: "manual"`, `attempts: 1`,
  `failed_at`/`first_failed_at` `2026-10-30 09:37:29`, `next_retry_at` `2026-10-30 10:07:29`.
- `crawl_cycle_state.json` — cycle 1, 23 cities, 85 keywords, opened `2026-10-30 09:27:54`.
- `state_city.json` / `state_role.json` — `Vapi` page 1, `IT Department` /
  `Desktop Support Engineer`.

| Question | Finding |
|---|---|
| 1. Are 403 entries classified correctly? | Yes — `http_status: 403`, `work_type: "listing"`, correct reference. |
| 2. Are denied pages marked for manual review? | Yes — `recovery_status: "manual"` (`find_indeed_job.py:1578`). |
| 3. Are they automatically retried? | **No** — `queue_eligible()` skips `"resolved"` and `"manual"` (`:874-875`). Correct and deliberate. |
| 4. Could page 1 → page 2 cause missing data? | **Yes — confirmed data-completeness risk.** `state["current_page"] = failed_page + 1` with `done_job_urls = []` (`:2130-2137`) permanently skips page 1 for that (keyword, city), and because the entry is `manual` it is never re-crawled without human action. |
| 5. Does the checkpoint resume where expected? | Yes — it resumes at page 2 as logged, which is the intended (if lossy) behaviour. |
| 6. Do access breaks continue indefinitely? | No — bounded by `MAX_BLOCK_COOLDOWNS = 3` (`:203`, `:2164`), after which the run stops with the checkpoint intact. `.121` demonstrated this. |
| 7. Are 401 / 403 / 429 distinguished correctly? | Partly. 429 is handled separately and correctly (`indeed_client.py:1449-1454`, retry-after honoured). **401 and 403 are collapsed into one branch** (`:1445`), so an authentication failure is indistinguishable from an authorisation refusal in the logs and queue. Both also receive the same 10-minute access break, even though a 401 is the case where re-authentication — not waiting — is the correct response. |
| 8. Is server-wide vs isolated refusal distinguished? | Mechanism exists (`scope`, `:2423-2424`, `:2518-2519`) but is unreliable — any single success resets it (see §H.5). |
| Minor | `next_retry_at` is written even for `manual` entries that are never eligible for retry — misleading to an operator reading the file. |

---

## I. Root Cause Evidence Table

| Suspected Cause | Evidence | File / Line | Confidence | Status |
|---|---|---|---|---|
| Incorrect HTTP status classification | Status read from `response.status` on main-frame nav; `None` defaults to **200**; nav errors → `IndeedNetworkError`; challenge → different message; robots → `status=999`. Stored reason matches the 401/403-only format string exactly. | `indeed_client.py:1435`, `:1443`, `:1445-1448`, `:1436-1441`, `:1469-1476`, `:1429-1433` | High | **Ruled out** — 403 is genuine |
| Malformed / duplicated search URL | `quote_plus` encoding; `start=(page-1)*10` ⇒ `start=0`; logged URL matches template byte-for-byte | `find_indeed_job.py:79-81`, `indeed_client.py:269-288` | High | **Ruled out** |
| Xvfb / display misconfiguration | Pre-Xvfb failure was `BrowserUnavailable` from the DISPLAY guard and never reached Indeed; `.125` 403'd on `:102` independently | `indeed_client.py:1168-1175`; `.123 indeed.log` 09:30 | High | **Ruled out** |
| Browser / Playwright malfunction | Chromium 142.0.7444.175 launches, renders, declines cookie banners, reads 16 cards on sibling hosts the same morning | `indeed_client.py:1179-1241`; `.121` log 09:19 | High | **Ruled out** |
| Proxy / transport misdirection | No `proxy=` anywhere; URL-override env vars unset; browser transport only | `find_indeed_job.py:1447-1450`; grep | High | **Ruled out** |
| Auth-file path or loading error | Absolute path via `project_path()`; valid JSON; valid storage-state schema; passed to `new_context` | `find_indeed_job.py:1459`, `indeed_client.py:1210-1218` | High | **Ruled out** |
| Expired authentication (as cause of **listing** 403) | Bearer/PPID expired 2026-10-29 14:58 UTC — yet `.122`, whose session expired **2026-10-22**, got listing 200s at ~09:17 on Oct 30. `/jobs` is public. | auth metadata; `.122` log/queue | Medium-High | **Ruled out** for listing 403 |
| Expired authentication (as contributor to **detail** 403/401) | 401s appear only on `/viewjob`; expired bearer is a credible trigger there | `.122` queue 2026-10-27 11:35:58, 2026-10-29 08:28:44 | Medium | **Possible contributing factor** |
| Shared/replayed session incl. Cloudflare `cf_clearance` across 5 hosts & 1 public IP | Identical MD5 on dev box + `.123`; `scp` fan-out in dev history; `save_indeed_login.py` never run on `.123`; `cf_clearance` issued 2026-10-29 13:58 UTC elsewhere; context rebuilt from the snapshot on every break | dev `~/.bash_history:1797,1800,1803`; `indeed_client.py:1211`, `:1218` | Medium-High | **Strongly supported cause** |
| Two Indeed accounts sharing one verified phone number | `sub` and `email` differ between the two deployed files; `phone_number` claim identical | PPID claims (hashed comparison) | High (fact) | **Possible contributing factor** |
| Server-side access policy / automated-access restriction | Escalation detail (Oct 26) → listing (Oct 27+) → immediate first-request listing refusal on the new session (Oct 30, 2/2 hosts); persistent across every 10-min cooldown; 200s interleaved on the old session 90 s apart | `.122`/`.121`/`.125` queues + logs | Medium-High | **Strongly supported cause** |
| Which layer refused, and its stated reason | `_goto()` discards `response.url`, headers (`server`, `cf-ray`, `cf-mitigated`) and body on 403; no screenshot/body capture exists | `indeed_client.py:1445-1448` | — | **Unknown / requires external confirmation** |
| Rate limiting (429) | Zero 429s in any log or queue on any host; handler exists and is unused | `indeed_client.py:1449-1454` | High | **Ruled out** for this failure |
| Fabricated job keys driving detail 403s | `jk=123456789abcdef0` and `jk=abcdef0123456789` requested and refused; `div[data-jk]` matches template nodes; no key validation | `.121`/`.122` logs; `indeed_client.py:1602`; `indeed_extractor.py:187-205`, `:236-250` | High | **Confirmed defect** (partial cause of detail 403s) |
| Pagination arithmetic inconsistency | `RESULTS_PER_PAGE=16` vs `INDEED_PAGE_STEP=10`, with a comment asserting equality; pages confirmed to return 16 cards | `find_indeed_job.py:164`, `:83`; `indeed_client.py:2461-2471` | High | **Confirmed defect** (not a 403 cause) |
| `(loaded)` banner is misleading | Printed from `os.path.exists()` alone; no signed-in check exists anywhere in the project | `find_indeed_job.py:2012-2014`; `indeed_client.py:2559-2563`; grep | High | **Confirmed defect** (masked the expiry) |
| Listing page 1 skipped to page 2 on 403 | `state["current_page"] = failed_page + 1`; entry is `manual`, and `queue_eligible` skips `manual` ⇒ page 1 never re-crawled | `find_indeed_job.py:2130-2137`, `:874-875` | High | **Confirmed defect** (data completeness) |
| 401 and 403 collapsed into one branch | `if status in (401, 403)` — same exception, same message shape, same 10-min break | `indeed_client.py:1445-1448` | High | **Confirmed defect** (diagnostic clarity) |
| Inert live-authorisation flag | `browser_live_authorised` is parsed but `browser_live_ready` ignores it; live mode turns on from `LIVE_COLLECTION_ENABLED` alone | `find_indeed_job.py:1451-1453`; `indeed_client.py:265-267`, `:2548` | High | **Confirmed defect** (dead safety gate) |
| Deployment drift | Deployed `find_indeed_job.py` (MD5 `69042b1f…`, Oct 25 22:34) matches **no** commit in the last 8; `.123` has no `.git` | `.123` filesystem; local `git show` comparison | High | **Possible contributing factor** (unverifiable code in production) |

---

## J. Final Diagnosis

**Why is the Indeed scraper receiving HTTP 403?**

Indeed is genuinely returning `403` on the first `GET https://in.indeed.com/jobs?...` of the
run. This is a real server response, confirmed at `indeed_client.py:1445-1448`, and the
scraper handles it correctly — it stops, records, and does not work around it.

**The precise server-side reason cannot be proven from local evidence.** To be explicit: the
code discards the response URL, every response header, and the body the moment it sees a 403,
so there is no local artifact that says whether the refusal came from Indeed's application
layer, its edge/bot-management layer, or an account-level restriction — nor what reason either
gave.

What the evidence **does** establish:

- Not a code bug in URL construction, status handling, browser setup, Xvfb, transport,
  proxying, or auth-file loading. All ruled out with direct evidence.
- Not rate limiting. Zero 429s have ever been recorded on any of the five hosts.
- Not simply expired login. The tokens *are* expired (2026-10-29 14:58 UTC), but `/jobs` is a
  public endpoint, and on the same morning the host with an 8-days-more-expired session was
  still reading listing pages successfully.

What the evidence **strongly supports**: a **server-side access decision against this
collector's session, account and source IP**, reached progressively. One browser session —
including a Cloudflare `cf_clearance` bot-clearance token issued on a different machine — was
captured once, hand-copied, and `scp`'d to five collectors sharing a single public IP, with
the context rebuilt from that same frozen snapshot after every pause and nothing ever written
back. Over four days the refusals escalated from job-detail pages (Oct 26) to listing pages
(Oct 27), and when the newer session file was rolled out on Oct 30 both receiving machines
were refused on their very first listing request — while, within the same ninety seconds and
from the same public IP, a machine still holding the older session was served HTTP 200. That
contrast points at the session rather than the network or the code. The fleet is now also
split across two Indeed accounts registered to the same verified phone number.

Two **confirmed local defects** are independently generating refusals and must not be
conflated with the above: the collector requests **job keys that do not exist**
(`jk=123456789abcdef0`, `jk=abcdef0123456789` — harvested from template DOM nodes by the
unvalidated `div[data-jk]` selector), and it over-reads listings by ~37% because
`RESULTS_PER_PAGE=16` contradicts `INDEED_PAGE_STEP=10`.

In short: **the access refusal is real, is Indeed's decision to make, and the local evidence
identifies the session-sharing and account posture as the most likely trigger — but confirming
Indeed's actual reason requires either response-level diagnostics the code does not currently
capture, or contact with Indeed.**

---

## K. Recommended Next Actions

*(ranked by diagnostic value; none implemented)*

### 1. Local software defects that can be corrected

1. **Capture evidence on refusal — highest diagnostic value, zero added traffic.** In
   `_goto()` (`indeed_client.py:1445-1448`), before raising, record `response.url`,
   `response.status`, the `server` / `cf-ray` / `cf-mitigated` / `retry-after` headers, and the
   first ~2 KB of the body to a diagnostics file. This single change converts "403, cause
   unknown" into an attributable finding on the very next run. Everything else on this list is
   guesswork until it exists.
2. **Validate job keys before requesting a detail page.** Require the `jk` to match Indeed's
   real key shape and reject the all-hex-permutation placeholders; restrict the card selector
   so hidden template nodes are not harvested (`indeed_client.py:1602`,
   `indeed_extractor.py:187-205`). Removes a class of self-inflicted 403s.
3. **Reconcile `RESULTS_PER_PAGE` with `INDEED_PAGE_STEP`** (`find_indeed_job.py:164` / `:83`)
   and fix the comment that claims they are equal. Cuts ~37% of listing requests.
4. **Make the startup banner honest.** Parse the storage state and report cookie count, token
   presence and **expiry** instead of printing `(loaded)` from `os.path.exists()`
   (`find_indeed_job.py:2012-2014`). This is what let a 13-hour-expired session look healthy.
5. **Add a real signed-in check** after context creation, and log signed-in vs signed-out
   explicitly — no such check exists anywhere today.
6. **Separate 401 from 403** (`indeed_client.py:1445`). A 401 should trigger
   re-authentication, not a 10-minute wait.
7. **Persist session state back.** Write `context.storage_state()` on clean shutdown so
   refreshed cookies survive, instead of reloading the frozen snapshot after every access break.
8. **Fix the listing-403 data gap.** `state["current_page"] = failed_page + 1` with
   `recovery_status="manual"` (`find_indeed_job.py:2130-2137`, `:874-875`) means a refused
   page 1 is never re-crawled. Track it as an explicit coverage gap, and drop the misleading
   `next_retry_at` on entries that are never eligible for retry.
9. **Either enforce or remove `INDEED_BROWSER_AUTHORISED`** — it is parsed at
   `find_indeed_job.py:1451-1453` but `browser_live_ready` ignores it, so the intended
   authorisation gate does nothing.
10. **Reconcile deployment drift.** The deployed `find_indeed_job.py` matches no commit and
    `.123` is not a git checkout; production is running unversioned code.

### 2. Authentication issues requiring normal reauthentication

11. **Re-establish the session the intended way**: run `save_indeed_login.py` **on the machine
    that will use it**, signing in by hand, so the resulting `cf_clearance` and cookies belong
    to that host's own browser and IP. Expect to repeat this — the bearer token has a one-hour
    lifetime and there is no usable refresh path.
12. **Stop fanning one session file out by `scp`.** One captured session replayed across five
    collectors is the pattern most likely to be read as abuse, and it is the variable that
    distinguished the refused hosts from the served ones on Oct 30.
13. **Resolve the two-accounts-one-phone-number situation.** Decide which single account is
    authorised for this work; running two accounts bound to the same verified phone across an
    automated fleet is a liability independent of the 403.

### 3. Access restrictions requiring Indeed authorisation or support

14. **Treat continued refusal as Indeed's answer and seek authorisation.** If listing pages
    remain refused after a locally-captured, non-shared session, the decision is server-side
    and the correct route is Indeed's official data access / partner channel — not more
    retries. Note `preflight()` already contemplates this: it expects an authorised run to
    supply `INDEED_BROWSER_SEARCH_URL`, and the code's own comments state the project "ships no
    built-in Indeed url".
15. **Do not restart the fleet to probe.** All five collectors are currently stopped. Each
    unauthenticated retry is another refused request from an IP that is already being refused.

### 4. Unknowns requiring further evidence

16. Which layer returns the 403, and its stated reason — blocked until recommendation 1 ships.
17. Whether the refusal is IP-scoped, session-scoped, or account-scoped. Testable at near-zero
    traffic cost once diagnostics exist: one single request with the new session, one with the
    old, from the same host, with headers captured.
18. Whether the `.123`/`.125` account carries a specific restriction — only Indeed can confirm.
19. Whether `cf_clearance` replay is implicated — determinable from the `cf-ray` /
    `cf-mitigated` headers in recommendation 1.

---

## Appendix — Fleet Snapshot at Time of Investigation (2026-10-30 ~09:50 IST)

| Host | Rank | `indeed_auth.json` | Size | Collector running | `indeed.log` | Workbooks |
|---|---|---|---|---|---|---|
| 192.168.1.121 | 1 | `8f3995fe…` (OLD) | 19,723 B | No | 7,995 B | 1 |
| 192.168.1.122 | 2 | `8f3995fe…` (OLD) | 19,723 B | No | 302,178 B | 7 |
| **192.168.1.123** | **3** | **`ac4b7125…` (NEW)** | **21,921 B** | **No** | **2,747 B** | **0** |
| 192.168.1.124 | 4 | `8f3995fe…` (OLD) | 19,723 B | No | none | 0 |
| 192.168.1.125 | 5 | `ac4b7125…` (NEW) | 21,921 B | No | 3,232 B | 0 |

Dev/staging box: `192.168.1.16` (`evisionserver`) — holds `ac4b7125…`, identical MD5 to
`.123`/`.125`; no Playwright and no Chromium installed; shell history shows the `scp`
distribution.

---

## Mandatory Verification

```
Python files modified:        NO
Configuration changed:        NO
Authentication file modified: NO
Checkpoint files modified:    NO
Excel files modified:         NO
Scraper restarted:            NO
New Indeed requests sent:     NO
Git operations performed:     NO  (read-only `git log` / `git show` on the local dev repo only)
Browser fingerprint changed:  NO
CAPTCHA bypass attempted:     NO
Production data deleted:      NO
```

All restrictions were maintained. Verified after the investigation: every file on `.123`
retains its pre-investigation mtime (`indeed_auth.json` 09:27:39, `failed_pages.json`
09:37:29, `crawl_cycle_state.json` 09:42:22, `indeed.log` 09:30:37, `find_indeed_job.py`
Oct 25 22:34:53) and hash. Remote access was limited to reads (`cat`, `ls`, `stat`, `md5sum`,
`grep`, `ps`, `scp` *from* the hosts, and `python3 -c` scripts that only called `json.load`).
No file was written on any of the five collectors; analysis copies lived only in a session
scratchpad.

Two disclosures for completeness:

- **Read-only inspection touched the sibling collectors** `.121`, `.122`, `.124`, `.125` as
  well as `.123`. Only `.123` was named in the task, but the fleet comparison is what made the
  controlled contrast in §G possible. Nothing was modified on any of them.
- **`sync_playwright().start()` was run twice** (once on `.123`, once on the dev box) purely to
  read `chromium.executable_path`. This starts and immediately stops the Playwright driver;
  **no browser page was opened and no network request was made.** It is how §E establishes that
  Chromium 142 is the binary in use.

One correction to an intermediate reading recorded during the investigation: the first process
check used `pgrep -f find_indeed_job.py`, which matched its own SSH command string and falsely
reported a collector running on every host. The accurate check confirms **zero** Indeed
collector processes across all five machines.

### Data-handling note

No cookie values, session tokens, bearer/refresh tokens, passwords, account email addresses or
phone numbers appear anywhere in this report. Token comparisons were performed on truncated
SHA-256 digests and value lengths only; JWT inspection was limited to timestamp and
non-identifying claims.
