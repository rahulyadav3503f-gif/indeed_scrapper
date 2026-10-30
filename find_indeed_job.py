"""Indeed job collector -- main script AND the project's only configuration file.

HOW THIS PROJECT IS ORGANISED
-----------------------------
    find_indeed_job.py          <- you are here: all settings + the crawl loop
    source_file/indeed_client.py      talks to the authorised data source
    source_file/indeed_extractor.py   builds the 32-column database row
    source_file/indeed_apply_url.py   resolves the real application url
    new_mylib.py                      cities and city -> state resolution
    target_audience_roles.py          the 85 search keywords + non-IT filter

There is deliberately NO ``indeed_config.py``.  Every editable setting is in
the block below, so one file is the only thing an operator has to open, and
there is no second place for a setting to disagree with itself.  The modules
in ``source_file/`` receive what they need through parameters or through the
``IndeedClientConfig`` object this file builds.

WHAT ONE RUN DOES
-----------------
    for each of the 85 keywords (shuffled once per cycle)
        for each city assigned to this rank (23 of 110 at rank 1 of 5)
            for each listing page, up to MAX_LISTING_PAGES
                for each job on the page
                    fetch detail -> extract 32 columns -> resolve apply url
                    -> validate -> de-duplicate -> append to current_data.xlsx

Every 100 accepted rows, ``current_data.xlsx`` is archived as
``current_data_cycleN.xlsx`` and emptied -- the same Excel rollover the
Naukri scraper already uses, with the same ``cycle_state.json`` counter.  All
Excel files are written directly into this directory; there is no output
folder.

LIVE COLLECTION
---------------
``LIVE_COLLECTION_ENABLED`` is ``False`` and must stay ``False`` until an
authorised Indeed data source is configured.  See the long note at the top of
``source_file/indeed_client.py``: Indeed's public job-search API was retired
in 2024 and the surviving partner APIs do not return search results, so this
project collects from offline fixtures and is honest about it.
"""

import json
import os
import random
import re
import signal
import socket
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

# ===========================================================================
# INDEED JOB SCRAPER CONFIGURATION
# ===========================================================================
# Everything below this banner is meant to be edited.  Nothing below the
# "END OF CONFIGURATION" banner needs to be touched to change behaviour.
# ---------------------------------------------------------------------------

# -- Rank configuration -----------------------------------------------------
# The 110 crawl cities are split between num_sys systems; this system takes
# the slice for `rank`.  rank is 1-based.  Five systems => 23/23/23/23/18.
rank = 1
num_sys = 5

# -- Collection mode --------------------------------------------------------
# True  -> the visible Chromium window collects from the LIVE Indeed site,
#          the same way the Naukri scraper collects from Naukri.
# False -> no live request is made; the browser walks local demonstration
#          pages instead (useful for testing the pipeline offline).
LIVE_COLLECTION_ENABLED = True

# -- The live site ---------------------------------------------------------
# The search and job urls, in the same place the Naukri scraper keeps its
# build_search_url().  {start} steps by INDEED_PAGE_STEP, which is how the
# site's own "next page" link steps.
INDEED_BASE_URL = "https://in.indeed.com"
INDEED_SEARCH_URL_TEMPLATE = (
    "{base}/jobs?q={keyword}&l={city}&start={start}&sort=date"
)
INDEED_JOB_URL_TEMPLATE = "{base}/viewjob?jk={job_key}"
INDEED_PAGE_STEP = 10               # the site paginates in steps of 10

# -- Saved login ------------------------------------------------------------
# Produced by running  python3 save_indeed_login.py  once: a visible browser
# opens, YOU sign in by hand, and the session is saved here.  The collector
# only ever LOADS this file; it never reads, types or stores a credential.
# Searching works signed out too, so a missing file is a warning, not an
# error.
AUTH_FILE = "indeed_auth.json"

# USE_SAVED_LOGIN = False -- the collector runs SIGNED OUT.
#
# This is deliberate, and it is the only setting that collects anything.
# Measured on the live site, same url, same machine, minutes apart:
#
#   signed in  (either of two accounts)  ->  403 on the first request
#   signed out (no cookies at all)       ->  HTTP 200, 16 cards
#
# Listing pages do not need a session, and attaching one is what gets the
# request refused. Combined with COLLECT_JOB_DETAILS = False there is no
# page left in the run that wants a login, so indeed_auth.json is not read
# at all and save_indeed_login.py does not need to be run.
#
# Set this True again only if job pages are re-enabled against a source that
# permits a signed-in session.
USE_SAVED_LOGIN = False

# -- Access rules the collector obeys ---------------------------------------
# Paths the site's robots.txt disallows for a general agent.  The collector
# never opens one; an /applystart link is RECORDED as an application
# destination and left alone.
RESPECT_ROBOTS_DISALLOW = True
ROBOTS_DISALLOWED_PREFIXES = ("/applystart", "/rc/", "/rpc/", "/my/", "/api/")
# Cookie banner: "reject" clicks the decline control so the banner stops
# covering the page.  "Accept all" is never pressed, and no other consent or
# terms control is ever touched.
COOKIE_BANNER_ACTION = "reject"

# -- Optional JSON data source (unused while the browser collects) ----------
# If an authorised JSON API is ever available, these environment variables
# point the HTTP transport at it without editing this file.
SOURCE_BASE_URL_ENV = "INDEED_SOURCE_BASE_URL"
SOURCE_SEARCH_PATH_ENV = "INDEED_SEARCH_PATH"
SOURCE_DETAIL_PATH_ENV = "INDEED_DETAIL_PATH"
SOURCE_API_KEY_ENV = "INDEED_API_KEY"

# Offline fixtures.  A directory of *.json files replayed instead of the
# network; when a file does not match, the client generates deterministic
# fixture jobs so the whole pipeline still runs.
FIXTURE_DIR = "tests/fixtures"
FIXTURE_JOBS_PER_PAGE = 12
FIXTURE_SEED = 20261021

# -- Night pause ------------------------------------------------------------
# 11:00 PM to 5:00 AM IST: the script sleeps instead of collecting.
NIGHT_PAUSE_ENABLED = True
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 5

# -- Keyword configuration --------------------------------------------------
# The 85 keywords are shuffled ONCE per crawl cycle, within their department,
# seeded from (rank, cycle number) so the order is reproducible and the five
# ranks do not march in lockstep.
KEYWORD_SHUFFLE_ENABLED = True

# -- Excel configuration ----------------------------------------------------
# Both files are written directly into this directory.  No output folder.
DETAIL_OUTPUT_FILE = "current_data.xlsx"
MASTER_OUTPUT_FILE = "indeed_it_jobs_by_city.xlsx" #not important
EXCEL_BATCH_SIZE = 100          # rows per archived current_data_cycleN.xlsx

# -- Cycle configuration ----------------------------------------------------
#MIN AND MAX USED HERE
#MIN = 10 MIN
#MAX = 2 HOURS
CYCLE_COOLDOWN_MINUTES = 45    # rest between finished crawl cycles

# -- Listing configuration --------------------------------------------------
# HOW MANY LISTING PAGES ARE CRAWLED FOR ONE (keyword, city) PAIR
#
# The site states a job count in its results header ("16 jobs"), and the
# number of pages used to be taken from that count alone:
#
#     16 jobs / 10 per page = 2 pages, then move to the next city
#
# That header under-reports for a narrow search, so pages that genuinely
# exist were never opened. The two settings below bound it from both ends:
#
#   MIN_LISTING_PAGES  always try at least this many pages, whatever the
#                      header claims. Pagination still stops early the
#                      moment a page comes back empty or repeats jobs
#                      already collected -- so a city with only one real
#                      page costs one wasted request, not MIN of them.
#   MAX_LISTING_PAGES  never open more than this many, however many the
#                      header promises. This is the politeness ceiling.
# NOTE ON THESE TWO AND MAX_JOBS_PER_HOUR
# The hourly cap gates JOB pages, and the job loop sits inside the page loop,
# so while jobs are being read the listing requests are throttled too. The
# exception is a page whose jobs are ALL already collected: no job is read,
# no quota is spent, and the collector walks straight on to the next listing
# page. With a 30-page ceiling that is up to 30 requests in a couple of
# minutes -- the one burst the cap cannot see.
#
# Under a 3-jobs-an-hour cap a deep ceiling is pointless anyway: ONE listing
# page of ~16 cards is already five hours of work. So the ceiling is low on
# purpose; raise it only if the cap is raised with it.
MIN_LISTING_PAGES = 1           # the header count decides, from page 1 up
MAX_LISTING_PAGES = 3           # ceiling, per (keyword, city) pair
# The site's PAGINATION STEP, not the number of cards you can see. "start="
# advances by INDEED_PAGE_STEP (10), while a page displays ~16 cards, so
# consecutive pages overlap -- which is exactly why
# STOP_ON_PAGE_WITH_NO_NEW_JOBS exists. Page planning must use the step, or
# it plans fewer pages than the site actually has, so this is DERIVED and
# cannot drift out of step with it.
RESULTS_PER_PAGE = INDEED_PAGE_STEP

# Stop paginating when a page adds no job this combo has not already seen.
# The site repeats cards across pages once it runs out of real results, so
# "a page with nothing new on it" is the honest end-of-results signal --
# more reliable than the header count.
STOP_ON_PAGE_WITH_NO_NEW_JOBS = True

# -- Open the job page, or just record its url? -----------------------------
# COLLECT_JOB_DETAILS = True   the old behaviour: for every card, open the
#                              job's own page and read the full description,
#                              the skills, the stated experience and the
#                              apply button's destination. One extra request
#                              per job.
# COLLECT_JOB_DETAILS = False  LISTING ONLY. The job page is never opened.
#                              The card's url is written into the sheet as it
#                              stands, and the columns that only the job page
#                              can fill are left blank.
#
# Listing-only is set here because the job page returns HTTP 401 for a
# signed-out browser and HTTP 403 for a signed-in one, so opening it
# produced no data and one failed request per job either way. The card
# itself still carries the title, company, location, salary, posted date and
# the snippet -- and, most importantly, the job page's own url.
COLLECT_JOB_DETAILS = False

# In listing-only mode apply_url cannot be read (it lives on the job page
# behind the Apply button). The job page is where a person goes to apply, so
# its url is written into apply_url rather than leaving the column empty.
# Set this False to leave apply_url genuinely blank instead.
APPLY_URL_FALLS_BACK_TO_JOB_PAGE = True

# Columns no card can fill. Listed here so the run can say plainly, once,
# what will be blank -- rather than leaving it to be discovered in the sheet.
# Columns a card cannot fill, so the run can say plainly what will be blank
# rather than leaving it to be discovered in the sheet. The experience
# columns are "usually blank": a snippet that happens to say "3-6 years"
# does fill them, one that says nothing leaves them empty.
DETAIL_ONLY_COLUMNS = (
    "skills_required", "company_website",
    "expiry_date", "application_deadline",
)
MOSTLY_BLANK_COLUMNS = (
    "experience_required", "experience_min_years", "experience_max_years",
)

# -- Periodic breaks --------------------------------------------------------
# After WORK_PERIOD hours of ACTUAL WORKING time (night stops, breaks,
# cooldowns and waits do not count), stop for a random break.
WORK_BREAK_MIN_MINUTES = 30
WORK_BREAK_MAX_MINUTES = 60
WORK_PERIOD_MIN_HOURS = 3.7
WORK_PERIOD_MAX_HOURS = 4.2

# -- Request pacing ---------------------------------------------------------
#MIN AND MAX
REQUEST_GAP_SECONDS = 10.0      # polite gap before each source request
PACING_JITTER_FRACTION = 0.35   # spreads the ranks apart; not evasion

# THE THREE CRAWL DELAYS ARE RANGES, NOT FIXED NUMBERS
# Each one is drawn fresh, uniformly, between its MIN and its MAX every time
# it is used. Two reasons: a fleet of five ranks does not fall into step and
# queue up on the source together, and a range is easier to tune than a base
# plus a jitter fraction -- what you write is exactly what you get.
#
# MIN == MAX is allowed and means a fixed delay. MIN > MAX is tolerated (they
# are swapped), and 0 means no delay at all.
# A HARD CEILING ON HOW MANY JOBS AN HOUR ARE COLLECTED
# The gaps below space requests out, but they cannot bound the RATE: a city
# with 30 pages of results collects hundreds of jobs quickly and then idles
# through a long city gap. That burst-then-idle shape is what provokes rate
# limiting, and it is not what "three jobs an hour" means.
#
# This is a rolling-window cap, checked before every job page: if the last
# hour already holds MAX_JOBS_PER_HOUR job reads, the collector waits until
# the oldest one falls out of the window. The count survives a restart (it
# lives in crawl_cycle_state.json), so stopping and starting cannot be used
# to burst past it.
#
#   3   -> one job roughly every 20 minutes, flat, all day
#   0   -> no cap; only the gaps below apply
MAX_JOBS_PER_HOUR = 3

# After EACH job card, a fresh random wait between these two values.
# 5 to 10 minutes. In listing-only mode no request is made for a card, so
# this does not slow any request down directly -- what it actually does is
# push the NEXT LISTING PAGE request far into the future: 16 cards at 5-10
# minutes each puts page 2 roughly 80 to 160 minutes after page 1, instead
# of the 7 seconds that got it refused.
JOB_GAP_MIN_SECONDS = 300.0     # 5 minutes
JOB_GAP_MAX_SECONDS = 600.0     # 10 minutes
# With MAX_JOBS_PER_HOUR doing the pacing, these only need to space the
# LISTING requests out; they must stay BELOW the cap's own spacing (3/hour =
# one job every ~20 min) or they, not the cap, become the binding constraint
# and the collector falls short of three jobs an hour.
CITY_GAP_MIN_SECONDS = 60.0       # between two cities
CITY_GAP_MAX_SECONDS = 180.0
KEYWORD_GAP_MIN_SECONDS = 120.0   # between two keywords
KEYWORD_GAP_MAX_SECONDS = 300.0

# -- HTTP timeout settings --------------------------------------------------
HTTP_CONNECT_TIMEOUT_SECONDS = 20
HTTP_READ_TIMEOUT_SECONDS = 40
HTTP_USER_AGENT = "JobEasyIndia-IndeedCollector/1.0"

# -- Retry configuration ----------------------------------------------------
MAX_RETRIES_5XX = 4
BACKOFF_5XX_SECONDS = (5, 15, 45, 120)
MAX_RETRIES_NETWORK = 3
BACKOFF_NETWORK_SECONDS = (5, 15, 45)
MAX_RETRIES_429 = 3
BACKOFF_429_SECONDS = (60, 180)             # only when Retry-After is absent
DEFAULT_429_WAIT_SECONDS = 120
RETRY_AFTER_CAP_SECONDS = 3600
# THE 403 COOLDOWN ESCALATES, THEN THE REFUSED UNIT IS SKIPPED
# A refusal used to cost the same 10-minute wait every time, so a job the
# source will not serve could hold the crawl up indefinitely. The wait now
# grows with each CONSECUTIVE refusal, and once the escalation is spent the
# refused job (or listing page) is given up on and the crawl carries on:
#
#   1st refusal  ->  1 x ACCESS_BREAK_SECONDS   (10 min)
#   2nd          ->  2 x                        (20 min)
#   3rd          ->  3 x                        (30 min)
#   4th          ->  4 x                        (40 min)
#   5th          ->  SKIP that unit, reset the escalation, keep crawling
#
# "Consecutive" means with no successful page in between: one good page
# resets the escalation to 1x. A Retry-After from the source is still
# honoured when it asks for longer -- max(), never min().
# A 401 is NOT a 403. It means "this page needs a signed-in session", and no
# cooldown can change that, so a 401 costs no wait at all -- the job is
# skipped and recorded. If job pages keep asking for a login, waiting hours
# collects nothing, so the run stops after this many and says what to do.
MAX_LOGIN_REQUIRED_SKIPS = 5

ACCESS_BREAK_SECONDS = 600                  # the base 403 cooldown (1x)
MAX_BLOCK_COOLDOWNS = 4                     # 1x..4x, then skip the unit
UNEXPECTED_ERROR_COOLDOWN_SECONDS = 300

# -- Failed-work queue ------------------------------------------------------
# Retryable failures only.  A denied endpoint is never retried in a loop.
FAILED_PAGES_MAX = 5000
FAILED_RETRY_DELAY_SECONDS = 1800
QUEUE_BATCH_SIZE = 3                        # queued items per visit
QUEUE_CHECK_EVERY_COMBOS = 5
QUEUE_DEFER_SECONDS = (1800, 3600, 10800, 21600)      # 30m 1h 3h 6h
QUEUE_MAX_DEFERRALS = len(QUEUE_DEFER_SECONDS)

# -- State files ------------------------------------------------------------
# All JSON checkpoints live directly in this directory, like the Naukri
# project's.  ``current_row.json`` from the brief's example list is NOT
# created: the crawl position is (keyword index, city index, page, done job
# urls) inside crawl_cycle_state.json, which is written atomically as one
# unit.  A second file holding the same row pointer could disagree with it
# after a crash, and the reference implementation does not keep one either.
CYCLE_STATE_FILE = "cycle_state.json"               # EXCEL archive counter
CRAWL_CYCLE_STATE_FILE = "crawl_cycle_state.json"   # keyword-cycle position
CITY_STATE_FILE = "state_city.json"                 # readable progress log
ROLE_STATE_FILE = "state_role.json"                 # readable progress log
FAILED_PAGES_FILE = "failed_pages.json"             # retryable failures

# -- Internet check ---------------------------------------------------------
INTERNET_CHECK_ENABLED = True
INTERNET_CHECK_HOST = "8.8.8.8"
INTERNET_CHECK_PORT = 53
INTERNET_CHECK_TIMEOUT = 3
INTERNET_RECHECK_SECONDS = 5

# -- Timezone ---------------------------------------------------------------
# The night window and the break schedule are read in this timezone.
TIMEZONE = "Asia/Kolkata"

# -- Scoring / defaults written into every row ------------------------------
DEFAULT_JOB_SCORE = 30
DEFAULT_COUNTRY = "India"

# -- Browser settings -------------------------------------------------------
# The collector drives a REAL Chromium window through Playwright, so the run
# can be watched on the desktop exactly as the Naukri scraper can be.
#
#   BROWSER_VISIBLE   True  -> use Chromium for collection (the normal mode)
#                     False -> no browser at all; use the JSON/fixture client
#   BROWSER_HEADLESS  False -> the window is on screen.  True hides it, which
#                              is what a server without a display needs.
#   BROWSER_SLOW_MO_MS       slows each Playwright action down so a developer
#                              can FOLLOW what the browser is doing.  It is a
#                              debugging aid, not a bot-detection measure.
#                              COST: the collector makes ~6 browser calls per
#                              page, so 300ms adds about 2s per job. Set it
#                              to 0 for full speed; the window stays visible.
BROWSER_VISIBLE = True
BROWSER_HEADLESS = False
BROWSER_SLOW_MO_MS = 350
BROWSER_WIDTH = 1366
BROWSER_HEIGHT = 768

# RUNNING ON A LAPTOP WITH NO DESKTOP SESSION (the rest of the fleet)
# A headed Chromium needs an X display. When there is none -- the run was
# started over SSH, or by systemd at boot -- the collector starts a virtual
# one (Xvfb) and the browser behaves exactly as it does on the desktop; the
# only difference is that there is no screen to watch it on.
#
#   XVFB_MODE  "auto"   start Xvfb only when no real display exists (default)
#              "always" always use Xvfb, even on a desktop -- lets this
#                       machine reproduce the fleet's exact configuration
#              "never"  never start it; no display is then a hard error
#
# Needs the package:  sudo apt-get install -y xvfb
# The alternative, needing no display and no package at all, is
# BROWSER_HEADLESS = True.
XVFB_MODE = "auto"
XVFB_DISPLAY = ""                   # "" = pick the first free one (:99 up)
XVFB_COLOUR_DEPTH = 24              # the screen is BROWSER_WIDTH x HEIGHT

BROWSER_REQUIRED = True             # this collector needs a browser to run
BROWSER_CHANNEL = ""                # "" = Playwright's own Chromium build
BROWSER_NAV_TIMEOUT_MS = 45000
BROWSER_KEEP_OPEN_SECONDS = 0       # hold the window open before closing
# How long to wait for a page's own content to appear. These are MAXIMA:
# a page that renders in 400ms is read in 400ms. They only bite when the
# expected markup never appears at all, so they are the cap on how long a
# page the collector cannot read may hold up the run.
LISTING_RENDER_TIMEOUT_MS = 8000    # wait for result cards to render
DETAIL_RENDER_TIMEOUT_MS = 6000     # wait for the job description to render

# WHICH PAGES THE BROWSER OPENS
# With LIVE_COLLECTION_ENABLED = True it opens the live search and job pages
# built from INDEED_SEARCH_URL_TEMPLATE / INDEED_JOB_URL_TEMPLATE above.
# With it False it walks LOCAL DEMONSTRATION PAGES generated on this machine
# and labelled as such, so the pipeline can be exercised offline.
#
# These environment variables override the templates without editing this
# file (useful for a staging mirror or a licensed feed's own pages):
#   INDEED_BROWSER_SEARCH_URL='https://.../jobs?q={keyword}&l={city}&start={start}'
#   INDEED_BROWSER_DETAIL_URL='https://.../viewjob?jk={job_key}'
BROWSER_DEMO_PAGES = True
BROWSER_LIVE_ACK_ENV = "INDEED_BROWSER_AUTHORISED"
BROWSER_LIVE_SEARCH_URL_ENV = "INDEED_BROWSER_SEARCH_URL"
BROWSER_LIVE_DETAIL_URL_ENV = "INDEED_BROWSER_DETAIL_URL"

# -- Stopping the collector -------------------------------------------------
# True  -> Ctrl+C stops NOW: the checkpoint is written, the browser is killed
#          and the process ends. No further job is scraped and nothing is
#          queued for a retry.
# False -> Ctrl+C behaves like a service stop: finish the job in hand, then
#          stop at the next safe boundary.
# A service stop (SIGTERM, i.e. ./indeed stop) always takes the graceful
# path, so systemd never cuts a job in half.
STOP_IMMEDIATELY_ON_CTRL_C = True

# -- Logging ----------------------------------------------------------------
VERBOSE = True
LOG_EVERY_ROW = True
SHOW_JOB_TIMER = True           # time each job and the wait before the next

# ===========================================================================
# END OF CONFIGURATION
# ===========================================================================

from source_file.indeed_apply_url import extract_apply_url            # noqa: E402
from source_file.indeed_client import (                                # noqa: E402
    IndeedAccessDenied,
    IndeedLoginRequired,
    IndeedClient,
    IndeedClientConfig,
    IndeedClientError,
    IndeedDataError,
    IndeedNetworkError,
    IndeedRateLimited,
    IndeedServerError,
)
from source_file.indeed_extractor import (                             # noqa: E402
    DETAIL_COLUMNS,
    SOURCE_PLATFORM,
    clean_value,
    extract_job_details,
    row_for_excel,
    validate_row,
)

from new_mylib import city_by_states, city_to_state, job_by_department  # noqa: E402

try:
    from target_audience_roles import (                                # noqa: E402
        build_role_plan,
        is_non_it_title,
        role_plan_split,
    )
except ImportError:                                 # pragma: no cover
    build_role_plan = None
    is_non_it_title = None
    role_plan_split = None

# ---------------------------------------------------------------------------
# THE CRAWL PLAN IS NARROWED TO THE TARGET AUDIENCE
# ---------------------------------------------------------------------------
# new_mylib.py is the SHARED library: it is the same file as the Naukri
# project's, byte for byte, so the two scrapers cannot drift apart. That file
# carries a 432-entry "IT Department" list, and the reference scraper narrows
# it to the 85-role target plan in its OWN main script
# (find_naukri_job.py:1147):
#
#     job_by_department = {"IT Department": build_role_plan()}
#
# The same line has to live here, for the same reason. Without it this
# collector would crawl 407 keywords x 23 cities = 9,361 combos per cycle
# instead of 85 x 23 = 1,955, and would ask for the roles the 80/20 plan
# exists to exclude.
if build_role_plan is not None:
    job_by_department = {"IT Department": build_role_plan()}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Every runtime file is resolved against the project root, so the service and
# an interactive run write to the same place no matter the working directory.


def project_path(name: str) -> str:
    """A runtime file's absolute path inside the project root."""
    if os.path.isabs(name):
        return name
    return os.path.join(BASE_DIR, name)


MASTER_COLUMNS = [
    "state", "role_category", "job_title", "location",
    "search_keyword", "sample_job_url",
    "first_seen_at", "last_seen_at",
    "seen_count", "is_active",
    "source_platform", "source_type",
]

CRAWL_CYCLE_SCHEMA_VERSION = 1
# Derived from the plan above, so the two can never disagree about the
# department name.
ROLE_CATEGORY = next(iter(job_by_department), "IT Department")

_SHUTDOWN_REQUESTED = False
_LOGIN_REQUIRED_SKIPS = 0
_CHECKPOINT_SAVER = None
_CLIENT_CLOSER = None       # set in main(): closes the browser gracefully
_CLIENT_KILLER = None       # set in main(): kills the browser immediately

# Aliased so a test can intercept the process-ending call.
_exit = os._exit


# ===========================================================================
# SMALL HELPERS
# ===========================================================================

def log(message=""):
    if VERBOSE:
        print(message, flush=True)


def now_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def local_now():
    """Now in TIMEZONE, falling back to system local time."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:                               # pragma: no cover
        return datetime.now()


def human_duration(seconds) -> str:
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


def make_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def make_unique_key(role_category: str, job_title: str, location: str) -> str:
    """The master workbook's identity for a job.  Same shape as Naukri's."""
    return "|".join([
        normalize_text(role_category),
        normalize_text(job_title),
        normalize_text(location),
    ])


def write_json_atomic(path: str, payload) -> None:
    """Write JSON through a temp file, so a kill never leaves half a file."""
    path = project_path(path)
    folder = os.path.dirname(path) or "."
    temp = os.path.join(folder, ".%s.tmp" % os.path.basename(path))
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def read_json_or_none(path: str):
    path = project_path(path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as error:
        log("  [WARN] %s unreadable (%s) -- treating as absent."
            % (os.path.basename(path), error))
        return None


# ===========================================================================
# INTERNET
# ===========================================================================

def is_internet_available() -> bool:
    if not INTERNET_CHECK_ENABLED:
        return True
    try:
        socket.setdefaulttimeout(INTERNET_CHECK_TIMEOUT)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.connect((INTERNET_CHECK_HOST, INTERNET_CHECK_PORT))
        return True
    except OSError:
        return False


def wait_for_internet(check_interval=None) -> None:
    """Block until the connection is back.  Waiting is not working time."""
    if not INTERNET_CHECK_ENABLED or is_internet_available():
        return
    interval = check_interval or INTERNET_RECHECK_SECONDS
    work_clock_pause()
    try:
        log("  [NET] No internet. Waiting; the checkpoint is already saved.")
        while not is_internet_available():
            time.sleep(interval)
        log("  [NET] Internet is back -- resuming.")
    finally:
        work_clock_resume()


# ===========================================================================
# EXCEL: SAFE READ, SAFE WRITE, ROLLOVER
# ===========================================================================
# This is the Naukri method, unchanged in behaviour:
#
#   * current_data.xlsx is the working file;
#   * at EXCEL_BATCH_SIZE rows it is archived as current_data_cycleN.xlsx,
#     where N comes from cycle_state.json and is then incremented;
#   * current_data.xlsx is re-created empty with the same 32 columns;
#   * nothing is ever written into a subdirectory.
#
# The archive counter in cycle_state.json has NOTHING to do with the keyword
# crawl cycle in crawl_cycle_state.json.  Two different things, two files,
# two different words.

_ILLEGAL_EXCEL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _needs_cleaning(value) -> bool:
    return isinstance(value, str) and bool(_ILLEGAL_EXCEL_CHARS.search(value))


def _clean_cell(value):
    if isinstance(value, str):
        return _ILLEGAL_EXCEL_CHARS.sub("", value)
    return value


def safe_read_excel_or_empty(file_path: str, columns: list) -> pd.DataFrame:
    """Read Excel safely.

    A missing, empty, half-written or corrupted workbook is removed and
    replaced by an empty frame rather than ending the run.
    """
    from zipfile import BadZipFile

    file_path = project_path(file_path)
    if not os.path.exists(file_path):
        return pd.DataFrame(columns=columns)

    try:
        if os.path.getsize(file_path) == 0:
            raise ValueError("Excel file is empty.")
        return pd.read_excel(file_path, engine="openpyxl")
    except (BadZipFile, ValueError, OSError, EOFError, KeyError) as error:
        log("⚠️ Corrupted Excel found: %s. Removing it and continuing. "
            "Error: %s" % (file_path, error))
        try:
            os.remove(file_path)
        except OSError as remove_error:
            log("⚠️ Could not remove corrupted file %s: %s"
                % (file_path, remove_error))
        return pd.DataFrame(columns=columns)


def safe_to_excel(df: pd.DataFrame, file_path: str) -> None:
    """Sanitise, then write through a temp file and rename into place.

    Ctrl+C in the middle of a save can therefore never corrupt the real
    workbook: the rename is atomic.
    """
    file_path = project_path(file_path)

    cleaned = None
    cleaned_cells = 0
    for column in list(df.columns):
        original = df[column]
        changed = int(original.map(_needs_cleaning).sum())
        if changed:
            if cleaned is None:
                cleaned = df.copy()
            cleaned[column] = original.map(_clean_cell)
            cleaned_cells += changed
    if cleaned_cells:
        log("      🧹 Removed Excel-illegal control characters from %d cell(s) "
            "before saving %s." % (cleaned_cells, os.path.basename(file_path)))
    frame = cleaned if cleaned is not None else df

    folder = os.path.dirname(os.path.abspath(file_path))
    name, ext = os.path.splitext(os.path.basename(file_path))
    temp_file = os.path.join(folder, ".%s.tmp%s" % (name, ext))
    if os.path.exists(temp_file):
        try:
            os.remove(temp_file)
        except OSError:
            pass

    frame.to_excel(temp_file, index=False, engine="openpyxl")
    os.replace(temp_file, file_path)


def load_cycle_num() -> int:
    """The Excel ARCHIVE counter from cycle_state.json (0 when absent)."""
    data = read_json_or_none(CYCLE_STATE_FILE)
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("cycle_num", 0))
    except (TypeError, ValueError):
        return 0


def save_cycle_num(cycle_num: int) -> None:
    write_json_atomic(CYCLE_STATE_FILE, {"cycle_num": int(cycle_num)})


def get_next_cycle_file(base_file=None):
    """``(filename, number)`` for the next archive.

    cycle_num = 0 -> current_data_cycle1.xlsx
    cycle_num = 1 -> current_data_cycle2.xlsx

    Reading the number from disk every time is what makes the sequence
    survive a restart: nothing is ever hardcoded and no archive is
    overwritten.
    """
    base_file = base_file or DETAIL_OUTPUT_FILE
    base_name = os.path.splitext(os.path.basename(base_file))[0]
    next_cycle = load_cycle_num() + 1

    # Belt and braces: if an archive with that number somehow exists already
    # (a state file deleted by hand, say), step past it rather than
    # destroying collected data.
    while os.path.exists(project_path("%s_cycle%d.xlsx"
                                      % (base_name, next_cycle))):
        next_cycle += 1

    return "%s_cycle%d.xlsx" % (base_name, next_cycle), next_cycle


_SAVED_KEYS = None          # (source_url, job_id) already in the working file


def _saved_keys(df: pd.DataFrame) -> set:
    keys = set()
    for column in ("source_url", "job_id"):
        if column in df.columns:
            for value in df[column].tolist():
                text = str(value or "").strip()
                if text:
                    keys.add(text)
    return keys


def is_duplicate_row(row: dict, df: pd.DataFrame) -> bool:
    """True when this job is already in the working workbook."""
    if df is None or df.empty:
        return False
    for column in ("source_url", "job_id"):
        value = str(row.get(column) or "").strip()
        if not value or column not in df.columns:
            continue
        if (df[column].astype(str).str.strip() == value).any():
            return True
    return False


def append_detail_to_excel(row, output_file=None, cycle_size=None):
    """Append one accepted job to current_data.xlsx, archiving at the batch size.

    Guards applied in order, each of which skips THIS job only and never
    stops the run:
      1. the row must be a dict with the required fields (validate_row);
      2. non-IT titles are rejected (target_audience_roles.is_non_it_title);
      3. a job already in the working file is not written twice;
      4. a row Excel still refuses after sanitising is skipped and logged.
    """
    global _SAVED_KEYS

    output_file = output_file or DETAIL_OUTPUT_FILE
    cycle_size = int(cycle_size or EXCEL_BATCH_SIZE)

    if not isinstance(row, dict):
        return None

    is_valid, reason = validate_row(row)
    if not is_valid:
        log("      ⚠️  Skipped incomplete job (%s): %s"
            % (reason, str(row.get("job_title", ""))[:60]))
        return None

    title = str(row.get("job_title", ""))
    if is_non_it_title is not None and title and is_non_it_title(title):
        log("      🚫 Skipped non-IT job: %s" % title[:70])
        return None

    df = safe_read_excel_or_empty(output_file, DETAIL_COLUMNS)
    if df.empty and list(df.columns) != DETAIL_COLUMNS:
        df = pd.DataFrame(columns=DETAIL_COLUMNS)

    if is_duplicate_row(row, df):
        log("      ⏭️  Duplicate, already saved: %s" % title[:60])
        return None

    clean_row = row_for_excel(row)
    df = pd.concat([df, pd.DataFrame([clean_row])], ignore_index=True)

    try:
        if len(df) >= cycle_size:
            cycle_file, cycle_num = get_next_cycle_file(output_file)
            safe_to_excel(df, cycle_file)
            save_cycle_num(cycle_num)
            safe_to_excel(pd.DataFrame(columns=DETAIL_COLUMNS), output_file)
            log("      📦 Excel batch complete → %s | rows: %d"
                % (cycle_file, len(df)))
            log("      ♻️  Emptied → %s" % output_file)
            _SAVED_KEYS = set()
        else:
            safe_to_excel(df, output_file)
            if LOG_EVERY_ROW:
                log("      💾 Saved detail → %s | rows: %d/%d"
                    % (output_file, len(df), cycle_size))
            if _SAVED_KEYS is not None:
                _SAVED_KEYS.update(_saved_keys(pd.DataFrame([clean_row])))
    except Exception as error:
        # openpyxl's IllegalCharacterError and friends: one bad job must not
        # end a run that has been collecting for hours.
        if type(error).__name__ not in ("IllegalCharacterError", "ValueError",
                                        "TypeError"):
            raise
        log("      ⚠️  Excel refused this job (%s: %s) -- skipping it: %s"
            % (type(error).__name__, error, title[:60]))
        return None

    return clean_row


# -- master workbook --------------------------------------------------------

def load_existing_master() -> pd.DataFrame:
    df = safe_read_excel_or_empty(MASTER_OUTPUT_FILE, MASTER_COLUMNS)
    for column in MASTER_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[MASTER_COLUMNS] if not df.empty else pd.DataFrame(
        columns=MASTER_COLUMNS)


def save_master(df: pd.DataFrame) -> None:
    if df is None or len(df) == 0:
        return
    for column in MASTER_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    safe_to_excel(df[MASTER_COLUMNS], MASTER_OUTPUT_FILE)


# ===========================================================================
# READABLE PROGRESS STATE (state_city.json / state_role.json)
# ===========================================================================
# Kept because the reference project keeps them and operators read them.
# They are a LOG, not the resume authority -- crawl_cycle_state.json is.

def load_city_state() -> dict:
    data = read_json_or_none(CITY_STATE_FILE) or {}
    try:
        page = int(data.get("page_number", 1))
    except (TypeError, ValueError):
        page = 1
    return {"city_name": data.get("city_name"), "page_number": page}


def save_city_state(city_name, page_number) -> None:
    write_json_atomic(CITY_STATE_FILE, {
        "city_name": city_name,
        "page_number": int(page_number),
    })


def reset_city_state() -> None:
    save_city_state(None, 1)


def load_role_state() -> dict:
    data = read_json_or_none(ROLE_STATE_FILE) or {}
    return {
        "role_category": data.get("role_category"),
        "search_keyword": data.get("search_keyword"),
    }


def save_role_state(role_category, search_keyword) -> None:
    write_json_atomic(ROLE_STATE_FILE, {
        "role_category": role_category,
        "search_keyword": search_keyword,
    })


def reset_role_state() -> None:
    save_role_state(None, None)


# ===========================================================================
# FAILED-WORK QUEUE (retryable failures only)
# ===========================================================================

def load_failed_pages() -> list:
    loaded = read_json_or_none(FAILED_PAGES_FILE)
    return loaded if isinstance(loaded, list) else []


def record_failed(kind, reference, reason, status=None, attempts=1,
                  context=None, recovery_status="pending",
                  retry_delay_seconds=None) -> None:
    """Record one unit of lost work, de-duplicated on (reference, status).

    A 403 is recorded with ``recovery_status="manual"`` by the caller, so the
    queue is never used to hammer an endpoint that has refused access.
    """
    context = dict(context or {})
    now = datetime.now()
    delay = (FAILED_RETRY_DELAY_SECONDS if retry_delay_seconds is None
             else int(retry_delay_seconds))

    entry = {
        "reference": str(reference),
        "work_type": "detail" if kind == "detail" else "listing",
        "http_status": int(status) if status is not None else None,
        "keyword": context.get("keyword"),
        "city": context.get("city"),
        "page_number": context.get("page_number"),
        "attempts": int(attempts),
        "failed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "next_retry_at": (now + timedelta(seconds=delay)).strftime(
            "%Y-%m-%d %H:%M:%S"),
        "reason": str(reason)[:500],
        "recovery_status": recovery_status,
        "deferrals": 0,
        "rank": rank,
        "kind": kind,
    }

    queue = load_failed_pages()
    for existing in queue:
        if (existing.get("reference") == entry["reference"]
                and existing.get("http_status") == entry["http_status"]):
            entry["attempts"] = int(existing.get("attempts", 0)) + int(attempts)
            entry["deferrals"] = int(existing.get("deferrals", 0))
            entry["first_failed_at"] = existing.get(
                "first_failed_at", existing.get("failed_at"))
            existing.clear()
            existing.update(entry)
            write_json_atomic(FAILED_PAGES_FILE, queue)
            log("      📌 queue updated (%s, %d attempt(s)): %s"
                % (reason, entry["attempts"], reference))
            return

    entry["first_failed_at"] = entry["failed_at"]
    queue.append(entry)
    if len(queue) > FAILED_PAGES_MAX:
        queue = queue[-FAILED_PAGES_MAX:]
    write_json_atomic(FAILED_PAGES_FILE, queue)
    log("      📌 queued for a later retry (%s): %s" % (reason, reference))


def mark_failed_resolved(reference) -> None:
    queue = load_failed_pages()
    touched = False
    for entry in queue:
        if (entry.get("reference") == str(reference)
                and entry.get("recovery_status") != "resolved"):
            entry["recovery_status"] = "resolved"
            entry["resolved_at"] = now_time()
            touched = True
    if touched:
        write_json_atomic(FAILED_PAGES_FILE, queue)
        log("      ✔ previously failed work recovered: %s" % reference)


def queue_eligible(limit=None) -> list:
    """Entries whose retry time has passed and that are still retryable."""
    now = datetime.now()
    eligible = []
    for entry in load_failed_pages():
        if entry.get("recovery_status") in ("resolved", "manual"):
            continue
        when = entry.get("next_retry_at")
        try:
            due = datetime.strptime(str(when), "%Y-%m-%d %H:%M:%S") <= now
        except (TypeError, ValueError):
            due = True
        if due:
            eligible.append(entry)
        if limit and len(eligible) >= int(limit):
            break
    return eligible


def defer_queue_entry(entry, reason="") -> None:
    """Push an entry further out; after the last step it needs a human.

    Escalation, not repetition -- this is what stops an endless retry loop.
    """
    reference = entry.get("reference")
    queue = load_failed_pages()
    for stored in queue:
        if stored.get("reference") != reference:
            continue
        deferrals = int(stored.get("deferrals", 0)) + 1
        step = QUEUE_DEFER_SECONDS[min(deferrals - 1,
                                       len(QUEUE_DEFER_SECONDS) - 1)]
        stored["deferrals"] = deferrals
        stored["next_retry_at"] = (
            datetime.now() + timedelta(seconds=step)
        ).strftime("%Y-%m-%d %H:%M:%S")
        stored["last_reason"] = str(reason)[:300]
        if deferrals >= QUEUE_MAX_DEFERRALS:
            stored["recovery_status"] = "manual"
            log("      🛑 %s has failed %d times -- marked 'manual', it will "
                "not be retried automatically again." % (reference, deferrals))
        else:
            log("      ⏳ %s deferred for %s (deferral %d/%d)"
                % (reference, human_duration(step), deferrals,
                   QUEUE_MAX_DEFERRALS))
        break
    write_json_atomic(FAILED_PAGES_FILE, queue)


def failed_queue_summary():
    """``(pending, due_now, manual, resolved)`` for end-of-cycle reporting."""
    pending = due = manual = resolved = 0
    now = datetime.now()
    for entry in load_failed_pages():
        status = entry.get("recovery_status")
        if status == "resolved":
            resolved += 1
            continue
        if status == "manual":
            manual += 1
            continue
        pending += 1
        try:
            if datetime.strptime(str(entry.get("next_retry_at")),
                                 "%Y-%m-%d %H:%M:%S") <= now:
                due += 1
        except (TypeError, ValueError):
            due += 1
    return pending, due, manual, resolved


# ===========================================================================
# WORK CLOCK, NIGHT WINDOW, BREAKS
# ===========================================================================
# The clock measures WORKING time.  Night stops, work breaks, cycle
# cooldowns, access cooldowns and waiting for the internet are all excluded,
# so "4 hours of work" means four hours of actual collecting.


class ScheduledPause(Exception):
    """A scheduled stop is due.  Unwinds to the orchestrator."""

    def __init__(self, reason, seconds, reset_work_timer=True):
        super().__init__(reason)
        self.reason = reason
        self.seconds = max(0, int(seconds))
        self.reset_work_timer = reset_work_timer


class LoginRequiredRepeatedly(Exception):
    """Job pages keep asking for a session, so there is nothing to collect.

    Raised instead of grinding on: signed out, Indeed serves listing pages
    but answers 401 for /viewjob, so every job would be skipped. Stopping
    with a clear instruction beats crawling for hours and saving nothing.
    """


class AccessBlocked(Exception):
    """Access was refused (403) or the quota is spent (429)."""

    def __init__(self, status, reference, wait_seconds=None, detail="",
                 work_type="listing", scope="isolated", attempts=1,
                 context=None):
        super().__init__("HTTP %s on %s" % (status, reference))
        self.status = status
        self.reference = reference
        self.wait_seconds = wait_seconds
        self.detail = detail
        self.work_type = work_type
        self.scope = scope
        self.attempts = attempts
        self.context = dict(context or {})

    @property
    def label(self):
        return "HTTP %s" % self.status


_WORK_SESSION_START = None
_WORK_ACCUMULATED = 0.0
_WORK_MARK = None
_WORK_PAUSE_DEPTH = 0
_WORK_PERIOD_HOURS = None       # drawn fresh for each work period


def pick_work_period_hours() -> float:
    """A fresh work period from the configured range.

    A range rather than a single number so the five ranks do not all stop at
    the same minute and queue up on the data source together.
    """
    low = float(min(WORK_PERIOD_MIN_HOURS, WORK_PERIOD_MAX_HOURS))
    high = float(max(WORK_PERIOD_MIN_HOURS, WORK_PERIOD_MAX_HOURS))
    if high <= low:
        return max(0.01, low)
    return max(0.01, random.uniform(low, high))


def pick_break_seconds() -> int:
    low = int(min(WORK_BREAK_MIN_MINUTES, WORK_BREAK_MAX_MINUTES)) * 60
    high = int(max(WORK_BREAK_MIN_MINUTES, WORK_BREAK_MAX_MINUTES)) * 60
    if high <= low:
        return low
    return random.randint(low, high)


def reset_work_timer() -> None:
    global _WORK_SESSION_START, _WORK_ACCUMULATED, _WORK_MARK
    global _WORK_PAUSE_DEPTH, _WORK_PERIOD_HOURS
    _WORK_SESSION_START = time.monotonic()
    _WORK_ACCUMULATED = 0.0
    _WORK_MARK = time.monotonic()
    _WORK_PAUSE_DEPTH = 0
    _WORK_PERIOD_HOURS = pick_work_period_hours()


def work_clock_pause() -> None:
    global _WORK_ACCUMULATED, _WORK_MARK, _WORK_PAUSE_DEPTH
    _WORK_PAUSE_DEPTH += 1
    if _WORK_PAUSE_DEPTH == 1 and _WORK_MARK is not None:
        _WORK_ACCUMULATED += time.monotonic() - _WORK_MARK
        _WORK_MARK = None


def work_clock_resume() -> None:
    global _WORK_MARK, _WORK_PAUSE_DEPTH
    if _WORK_PAUSE_DEPTH > 0:
        _WORK_PAUSE_DEPTH -= 1
    if _WORK_PAUSE_DEPTH == 0 and _WORK_MARK is None:
        _WORK_MARK = time.monotonic()


def work_seconds() -> float:
    if _WORK_SESSION_START is None:
        return 0.0
    total = _WORK_ACCUMULATED
    if _WORK_MARK is not None:
        total += time.monotonic() - _WORK_MARK
    return total


def night_seconds_remaining(now=None) -> int:
    """Seconds until the night window ends, or 0 when outside it."""
    if not NIGHT_PAUSE_ENABLED:
        return 0
    now = now or local_now()
    start = int(NIGHT_START_HOUR) % 24
    end = int(NIGHT_END_HOUR) % 24
    if start == end:
        return 0

    hour = now.hour
    if start < end:
        inside = start <= hour < end
    else:                                       # window crosses midnight
        inside = hour >= start or hour < end
    if not inside:
        return 0

    target = now.replace(hour=end, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(0, int((target - now).total_seconds()))


def pacing_gate() -> None:
    """Raise ScheduledPause when a stop is due.

    Called ONLY at safe boundaries -- before a combo and before a listing
    page -- so a page is never abandoned half way through its jobs.
    """
    if _SHUTDOWN_REQUESTED:
        raise KeyboardInterrupt("shutdown requested")

    night = night_seconds_remaining()
    if night > 0:
        raise ScheduledPause(
            "night pause %02d:00-%02d:00 %s"
            % (NIGHT_START_HOUR, NIGHT_END_HOUR, TIMEZONE), night)

    worked = work_seconds()
    period = _WORK_PERIOD_HOURS or pick_work_period_hours()
    if _WORK_SESSION_START is not None and worked >= period * 3600.0:
        break_seconds = pick_break_seconds()
        raise ScheduledPause(
            "%.2f-hour work period reached (%.2f h actually worked) -- taking "
            "a %s break (random %d-%d min)"
            % (period, worked / 3600.0, human_duration(break_seconds),
               WORK_BREAK_MIN_MINUTES, WORK_BREAK_MAX_MINUTES),
            break_seconds)


_JOB_READ_TIMES = []            # monotonic-free: wall clock, so it survives


def load_job_read_times(state) -> None:
    """Restore the hour's job times from the checkpoint after a restart."""
    global _JOB_READ_TIMES
    stored = state.get("recent_job_reads") or []
    cutoff = time.time() - 3600
    _JOB_READ_TIMES = sorted(float(t) for t in stored
                             if _is_number(t) and float(t) > cutoff)


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def note_job_read(state=None) -> None:
    """Record that a job page was read, for the hourly cap."""
    _JOB_READ_TIMES.append(time.time())
    _prune_job_reads()
    if state is not None:
        state["recent_job_reads"] = [round(t, 1) for t in _JOB_READ_TIMES]


def _prune_job_reads() -> None:
    cutoff = time.time() - 3600
    while _JOB_READ_TIMES and _JOB_READ_TIMES[0] <= cutoff:
        _JOB_READ_TIMES.pop(0)


def seconds_until_job_allowed() -> float:
    """How long before the hourly cap allows another job page.  0 = now."""
    cap = int(MAX_JOBS_PER_HOUR or 0)
    if cap <= 0:
        return 0.0
    _prune_job_reads()
    if len(_JOB_READ_TIMES) < cap:
        return 0.0
    # The oldest read in the window has to age out before the next one.
    return max(0.0, (_JOB_READ_TIMES[0] + 3600) - time.time())


def wait_for_job_quota() -> None:
    """Hold until the hourly cap allows the next job page."""
    wait = seconds_until_job_allowed()
    if wait <= 0:
        return
    log("      ⏳ hourly cap reached (%d job(s) in the last hour) -- next job "
        "in %s" % (len(_JOB_READ_TIMES), human_duration(wait)))
    deadline = time.time() + wait
    while time.time() < deadline:
        if _SHUTDOWN_REQUESTED:
            raise KeyboardInterrupt("shutdown requested")
        time.sleep(min(30, max(1, deadline - time.time())))


def pick_delay(minimum, maximum) -> float:
    """A delay drawn uniformly from [minimum, maximum] seconds.

    Order does not matter -- the pair is sorted first, so writing the larger
    number on the MIN line cannot produce a negative delay. A pair that is
    equal gives a fixed delay; zero gives none.
    """
    try:
        low, high = float(minimum), float(maximum)
    except (TypeError, ValueError):
        return 0.0
    if low > high:
        low, high = high, low
    low = max(0.0, low)
    high = max(0.0, high)
    if high <= low:
        return low
    return random.uniform(low, high)


def sleep_between(minimum, maximum, label="") -> None:
    """Wait for a delay drawn from the configured range."""
    delay = pick_delay(minimum, maximum)
    if delay <= 0:
        return
    if label:
        log("      ... %s %.1fs (random %.0f-%.0fs)"
            % (label, delay, min(minimum, maximum), max(minimum, maximum)))
    time.sleep(delay)


def sleep_jitter(base_seconds, label="") -> None:
    """A single gap with jitter.  Kept for the request-pacing path only.

    The three crawl delays use sleep_between() and their own MIN/MAX pairs;
    this remains for places where one base number is the natural way to say
    it (the gap between listing pages, which follows the source's own pace).
    """
    if base_seconds <= 0:
        return
    delay = float(base_seconds) * (1.0 + random.uniform(
        0.0, max(0.0, PACING_JITTER_FRACTION)))
    if label:
        log("      ... %s %.1fs" % (label, delay))
    time.sleep(delay)


def sleep_long(seconds, reason) -> None:
    """A long wait, reported every five minutes so a run never looks hung."""
    seconds = max(0, int(seconds))
    if seconds <= 0:
        return
    resume_at = datetime.now() + timedelta(seconds=seconds)
    log("\n⏸  PAUSE: %s" % reason)
    log("   sleeping %s -- back at %s"
        % (human_duration(seconds), resume_at.strftime("%Y-%m-%d %H:%M:%S")))

    work_clock_pause()
    try:
        # The shutdown flag is checked every few seconds, not every five
        # minutes. It used to sleep in 300-second steps, so "./indeed stop"
        # during a 10-minute cooldown waited up to five minutes to be
        # noticed and was SIGKILLed by the launcher at 120s instead.
        # Progress is still only REPORTED every five minutes, so the log
        # does not fill with noise.
        deadline = time.monotonic() + seconds
        next_report = time.monotonic() + 300
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if _SHUTDOWN_REQUESTED:
                raise KeyboardInterrupt("shutdown requested during pause")
            time.sleep(min(3.0, left))
            if time.monotonic() >= next_report and deadline - time.monotonic() > 0:
                log("   still paused -- %s to go"
                    % human_duration(deadline - time.monotonic()))
                next_report = time.monotonic() + 300
    finally:
        work_clock_resume()
    log("▶️  Resuming: %s\n" % reason)


# ===========================================================================
# CITY DISTRIBUTION
# ===========================================================================

def get_cities_by_rank(all_cities: list, rank: int, num_sys: int) -> list:
    """The symmetric slice this rank owns.

    The reference implementation, preserved exactly -- it is positional, so
    every city is assigned to exactly one rank, none is skipped, and the
    slice sizes differ by at most one:

        one_unit  = int(len(cities) / num_sys) + 1
        cities[one_unit * (rank - 1) : one_unit * rank]

    With 110 cities and num_sys=5 this gives 23/23/23/23/18.
    """
    if not all_cities:
        return []
    try:
        rank = int(rank)
        num_sys = int(num_sys)
    except (TypeError, ValueError):
        return []
    if num_sys < 1 or rank < 1 or rank > num_sys:
        return []

    len_city = len(all_cities)
    one_unit = int(len_city / num_sys) + 1
    first_idx = one_unit * (rank - 1)
    last_idx = one_unit * rank
    return all_cities[first_idx:last_idx]


def all_cities() -> list:
    """The 110 crawl cities, de-duplicated, ORDER PRESERVED (it is positional)."""
    cities = [city for cities in city_by_states.values() for city in cities]
    return list(dict.fromkeys(cities))


def rank_cities() -> list:
    cities = get_cities_by_rank(all_cities(), rank, num_sys)
    if not cities:
        raise RuntimeError(
            "get_cities_by_rank() returned no cities for rank=%s num_sys=%s "
            "out of %d cities. Check rank/num_sys at the top of this file."
            % (rank, num_sys, len(all_cities())))
    return cities


_CITY_STATE_CACHE = {}


def city_state_name(city: str) -> str:
    """``city_to_state()`` with a cache -- the answer never changes."""
    if city not in _CITY_STATE_CACHE:
        try:
            _CITY_STATE_CACHE[city] = city_to_state(city)
        except Exception as error:
            log("  [WARN] city_to_state(%r) failed: %s" % (city, error))
            _CITY_STATE_CACHE[city] = "Other"
    return _CITY_STATE_CACHE[city]


# ===========================================================================
# KEYWORD PLAN AND THE PER-CYCLE SHUFFLE
# ===========================================================================

def keyword_plan_pairs() -> list:
    """``[(role_category, keyword), ...]`` -- the 85-keyword plan, file order.

    The bare department keyword is dropped: a broad "IT Department" search
    returns whatever the source chooses to show, which is one of the ways
    non-IT jobs reach the database.
    """
    pairs = []
    seen = set()
    for category, titles in job_by_department.items():
        category_key = str(category).strip().lower()
        for title in titles:
            keyword = str(title).strip()
            if not keyword or keyword.lower() == category_key:
                continue
            dedupe_key = (category_key, keyword.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            pairs.append((str(category), keyword))
    if not pairs:
        raise RuntimeError("the role plan produced no searchable keywords.")
    return pairs


def fingerprint(items) -> str:
    import hashlib
    payload = " ".join(str(item) for item in items)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def plan_fingerprint(plan_pairs) -> str:
    return fingerprint(sorted("%s|%s" % pair for pair in plan_pairs))


def build_keyword_order(plan_pairs, crawl_cycle_number):
    """A permutation of the plan, shuffled WITHIN each department.

    The seed is derived from (rank, cycle number, department), so:
      * the order is reproducible from the cycle number alone -- a lost state
        file cannot force a reshuffle in the middle of a cycle;
      * rank is in the seed, so rank 1 and rank 5 crawl different orders.

    A permutation cannot drop or duplicate a keyword, and that is asserted
    rather than assumed.
    """
    import hashlib
    from collections import OrderedDict

    buckets = OrderedDict()
    for category, keyword in plan_pairs:
        buckets.setdefault(category, []).append(keyword)

    order = []
    seeds = {}
    for category, keywords in buckets.items():
        shuffled = list(keywords)
        if KEYWORD_SHUFFLE_ENABLED:
            seed_source = "%s|%s|%s" % (rank, crawl_cycle_number, category)
            seed = int(hashlib.sha1(seed_source.encode("utf-8"))
                       .hexdigest()[:12], 16)
            seeds[category] = seed
            random.Random(seed).shuffle(shuffled)
        else:
            seeds[category] = None

        if len(shuffled) != len(keywords) or set(shuffled) != set(keywords):
            raise RuntimeError(
                "shuffle corrupted the keyword plan for %r (%d in, %d out)"
                % (category, len(keywords), len(shuffled)))
        order.extend([category, keyword] for keyword in shuffled)

    if len(order) != len(plan_pairs):
        raise RuntimeError("keyword order has %d entries but the plan has %d"
                           % (len(order), len(plan_pairs)))
    return order, seeds


# ===========================================================================
# CRAWL CYCLE STATE
# ===========================================================================

def load_crawl_cycle_state():
    data = read_json_or_none(CRAWL_CYCLE_STATE_FILE)
    return data if isinstance(data, dict) else None


def save_crawl_cycle_state(state) -> None:
    state["updated_at"] = now_time()
    write_json_atomic(CRAWL_CYCLE_STATE_FILE, state)


def open_new_crawl_cycle(cities, plan_pairs, previous_number):
    """Open cycle N+1: shuffle once, persist BEFORE the first request."""
    number = int(previous_number) + 1
    order, seeds = build_keyword_order(plan_pairs, number)

    state = {
        "schema_version": CRAWL_CYCLE_SCHEMA_VERSION,
        "rank": rank,
        "num_sys": num_sys,
        "crawl_cycle_number": number,
        "cycle_started_at": now_time(),
        "cities": list(cities),
        "cities_fingerprint": fingerprint(cities),
        "keyword_order": order,
        "keyword_plan_fingerprint": plan_fingerprint(plan_pairs),
        "shuffle_seed": seeds,
        "current_keyword_index": 0,
        "current_city_index": 0,
        "current_page": 1,
        "done_job_urls": [],
        "cycle_complete": False,
    }
    save_crawl_cycle_state(state)

    log("\n[CYCLE] CRAWL CYCLE %d opened -- keywords shuffled once for this "
        "cycle." % number)
    log("   keywords : %d   cities : %d   combos : %d"
        % (len(order), len(cities), len(order) * len(cities)))
    preview = ", ".join(keyword for _, keyword in order[:6])
    log("   order    : %s%s" % (preview, " ..." if len(order) > 6 else ""))
    return state


def load_or_open_crawl_cycle(cities, plan_pairs):
    """Resume the cycle in progress, or open the next one.  Never both.

    The shuffle is gated on the cycle NUMBER alone, so a restart in the
    middle of a cycle resumes the saved order instead of reshuffling.
    """
    state = load_crawl_cycle_state()
    previous_number = int((state or {}).get("crawl_cycle_number", 0) or 0)

    resumable = bool(
        state
        and not state.get("cycle_complete")
        and state.get("keyword_order")
        and state.get("cities")
    )

    if resumable and (state.get("rank") != rank
                      or state.get("num_sys") != num_sys):
        log("  [WARN] Saved cycle was rank=%s num_sys=%s but this process is "
            "rank=%s num_sys=%s. The city slice is positional, so the saved "
            "position means nothing here -- opening a fresh cycle."
            % (state.get("rank"), state.get("num_sys"), rank, num_sys))
        resumable = False

    if not resumable:
        return open_new_crawl_cycle(cities, plan_pairs, previous_number)

    if state.get("cities_fingerprint") != fingerprint(cities):
        log("  [WARN] The rank's city list has CHANGED since cycle %d started "
            "(saved %d, now %d). Finishing this cycle against the SAVED list; "
            "the new list applies from the next cycle."
            % (state["crawl_cycle_number"], len(state["cities"]), len(cities)))

    if state.get("keyword_plan_fingerprint") != plan_fingerprint(plan_pairs):
        log("  [WARN] The keyword plan has CHANGED since cycle %d started. "
            "Finishing this cycle with the SAVED order so no keyword is "
            "dropped or crawled twice inside it."
            % state["crawl_cycle_number"])

    state.setdefault("done_job_urls", [])
    state["current_keyword_index"] = max(
        0, int(state.get("current_keyword_index", 0)))
    state["current_city_index"] = max(
        0, int(state.get("current_city_index", 0)))
    state["current_page"] = max(1, int(state.get("current_page", 1)))
    return state


def resume_position(state):
    """``(keyword_index, city_index, page)`` from the saved order.

    Indices rather than a name scan, so "Bengaluru" and "Bangalore" in the
    same rank can never be confused and resume is O(1).
    """
    order = state.get("keyword_order") or []
    cities = state.get("cities") or []
    keyword_index = min(max(0, int(state.get("current_keyword_index", 0))),
                        len(order))
    city_index = min(max(0, int(state.get("current_city_index", 0))),
                     max(0, len(cities) - 1))
    page = max(1, int(state.get("current_page", 1)))
    return keyword_index, city_index, page


def complete_crawl_cycle(state) -> None:
    """Mark the cycle finished and clear the per-cycle progress."""
    state["cycle_complete"] = True
    state["completed_at"] = now_time()
    state["current_keyword_index"] = len(state.get("keyword_order") or [])
    state["current_city_index"] = 0
    state["current_page"] = 1
    state["done_job_urls"] = []
    save_crawl_cycle_state(state)

    # ONLY here: a cycle that was cut short by a block must not look finished.
    reset_city_state()
    reset_role_state()

    log("\n[DONE] CRAWL CYCLE %d COMPLETE -- every keyword crawled across all "
        "%d rank cities."
        % (state["crawl_cycle_number"], len(state.get("cities") or [])))


# ===========================================================================
# THE CLIENT
# ===========================================================================

def build_client_config(verbose=None) -> IndeedClientConfig:
    """Hand the client everything it needs, from THIS file's configuration."""
    return IndeedClientConfig(
        live_enabled=LIVE_COLLECTION_ENABLED,
        base_url=os.environ.get(SOURCE_BASE_URL_ENV, ""),
        api_key_env=SOURCE_API_KEY_ENV,
        search_path=os.environ.get(SOURCE_SEARCH_PATH_ENV, ""),
        detail_path=os.environ.get(SOURCE_DETAIL_PATH_ENV, ""),
        results_per_page=RESULTS_PER_PAGE,
        max_listing_pages=MAX_LISTING_PAGES,
        min_listing_pages=MIN_LISTING_PAGES,
        request_timeout_seconds=HTTP_READ_TIMEOUT_SECONDS,
        connect_timeout_seconds=HTTP_CONNECT_TIMEOUT_SECONDS,
        max_retries_5xx=MAX_RETRIES_5XX,
        backoff_5xx_seconds=BACKOFF_5XX_SECONDS,
        max_retries_network=MAX_RETRIES_NETWORK,
        backoff_network_seconds=BACKOFF_NETWORK_SECONDS,
        max_retries_429=MAX_RETRIES_429,
        backoff_429_seconds=BACKOFF_429_SECONDS,
        default_429_wait_seconds=DEFAULT_429_WAIT_SECONDS,
        retry_after_cap_seconds=RETRY_AFTER_CAP_SECONDS,
        request_gap_seconds=REQUEST_GAP_SECONDS,
        jitter_fraction=PACING_JITTER_FRACTION,
        user_agent=HTTP_USER_AGENT,
        fixture_dir=project_path(FIXTURE_DIR),
        fixture_jobs_per_page=FIXTURE_JOBS_PER_PAGE,
        fixture_seed=FIXTURE_SEED,
        verbose=VERBOSE if verbose is None else verbose,
        # -- the visible browser, straight from the block at the top --------
        browser_visible=BROWSER_VISIBLE and BROWSER_REQUIRED,
        browser_headless=BROWSER_HEADLESS,
        browser_slow_mo_ms=BROWSER_SLOW_MO_MS,
        browser_width=BROWSER_WIDTH,
        browser_height=BROWSER_HEIGHT,
        browser_channel=BROWSER_CHANNEL,
        browser_nav_timeout_ms=BROWSER_NAV_TIMEOUT_MS,
        browser_keep_open_seconds=BROWSER_KEEP_OPEN_SECONDS,
        browser_demo_pages=BROWSER_DEMO_PAGES,
        xvfb_mode=XVFB_MODE,
        xvfb_display=XVFB_DISPLAY,
        xvfb_colour_depth=XVFB_COLOUR_DEPTH,
        browser_live_search_url=os.environ.get(
            BROWSER_LIVE_SEARCH_URL_ENV, ""),
        browser_live_detail_url=os.environ.get(
            BROWSER_LIVE_DETAIL_URL_ENV, ""),
        browser_live_authorised=str(
            os.environ.get(BROWSER_LIVE_ACK_ENV, "")
        ).strip().lower() in ("1", "yes", "true", "on"),
        # -- the live site, same shape as the Naukri scraper ---------------
        site_base_url=INDEED_BASE_URL,
        search_url_template=INDEED_SEARCH_URL_TEMPLATE,
        job_url_template=INDEED_JOB_URL_TEMPLATE,
        page_step=INDEED_PAGE_STEP,
        auth_file=project_path(AUTH_FILE),
        use_saved_login=USE_SAVED_LOGIN,
        cookie_banner_action=COOKIE_BANNER_ACTION,
        respect_robots_disallow=RESPECT_ROBOTS_DISALLOW,
        robots_disallowed_prefixes=ROBOTS_DISALLOWED_PREFIXES,
        listing_render_timeout_ms=LISTING_RENDER_TIMEOUT_MS,
        detail_render_timeout_ms=DETAIL_RENDER_TIMEOUT_MS,
    )


def build_client(transport=None) -> IndeedClient:
    client = IndeedClient(build_client_config(), transport=transport)
    ok, message = client.preflight()
    if not ok:
        raise RuntimeError("the Indeed data source is not usable: %s" % message)
    log("  [SOURCE] %s" % client.describe())
    log("  [SOURCE] %s" % message)
    log("  [MODE]   %s" % describe_collection_mode())
    return client


def describe_collection_mode() -> str:
    """One line saying which columns this run can actually fill."""
    if COLLECT_JOB_DETAILS:
        return ("full detail -- each job's own page is opened, all %d columns "
                "attempted" % len(DETAIL_COLUMNS))
    where = "apply_url and source_url" if APPLY_URL_FALLS_BACK_TO_JOB_PAGE \
        else "source_url"
    return ("LISTING ONLY -- job pages are NOT opened. The card's job page "
            "url is written to %s. Always blank: %s. Blank unless the "
            "snippet happens to state it: %s"
            % (where, ", ".join(DETAIL_ONLY_COLUMNS),
               ", ".join(MOSTLY_BLANK_COLUMNS)))


# ===========================================================================
# ONE (KEYWORD, CITY) COMBO
# ===========================================================================

def listing_only_row(raw_job, input_row):
    """The 32 columns from ONE CARD, without opening the job page.

    The card already carries the job's own page url, so nothing has to be
    fetched to record it. ``extract_job_details`` is the same function the
    detail path uses -- it reads the card's ``snippet`` as the description
    and leaves what it cannot find blank -- so a listing-only row has the
    identical shape to a detail row, just with fewer columns filled.
    """
    row = extract_job_details(
        raw_job, input_row, state_resolver=city_state_name,
        country=DEFAULT_COUNTRY, default_score=DEFAULT_JOB_SCORE,
    )

    # parse_experience() falls back to returning its SOURCE text when it
    # finds no year count, so a card with no stated experience would copy
    # the whole snippet into a column named experience_required. The snippet
    # already lives in job_description; duplicating it under a misleading
    # name would make the sheet lie about what was stated.
    if not clean_value(row.get("experience_min_years")) \
            and not clean_value(row.get("experience_max_years")):
        row["experience_required"] = ""

    job_page_url = clean_value(row.get("source_url"))
    if APPLY_URL_FALLS_BACK_TO_JOB_PAGE and not clean_value(row.get("apply_url")):
        # Not an invention: this is the page the Apply button lives on, and
        # the url came from the card, not from guesswork.
        row["apply_url"] = job_page_url
        row["_apply_url_kind"] = "job_page"
        row["_apply_url_reason"] = ("listing-only mode: the job page url from "
                                    "the card, which is where a person "
                                    "applies")

    row["source_type"] = "job_portal_listing"
    return row


def save_listing_only_row(raw_job, input_row):
    """Build and save one listing-only row.  ``"saved"``/``"skipped"``/``"failed"``."""
    reference = str(raw_job.get("source_url")
                    or raw_job.get("job_key")
                    or raw_job.get("title", ""))[:200]
    try:
        row = listing_only_row(raw_job, input_row)
    except Exception as error:
        log("      ⚠️  Could not read this card (%s: %s) -- skipping."
            % (type(error).__name__, error))
        record_failed("listing-card", reference,
                      "card extraction failed: %s" % error, context=input_row)
        return "failed"

    if LOG_EVERY_ROW:
        log("      🔗 %s" % (clean_value(row.get("source_url")) or "no url"))

    saved = append_detail_to_excel(row)
    if saved is None:
        return "skipped"
    mark_failed_resolved(reference)
    return "saved"


def process_job(client, raw_job, input_row):
    """One job: detail -> 32 columns -> apply url -> validate -> save.

    Returns ``"saved"``, ``"skipped"`` or ``"failed"``.  A single bad job is
    always a skip, never the end of the run.
    """
    global _LOGIN_REQUIRED_SKIPS

    reference = str(raw_job.get("source_url")
                    or raw_job.get("job_key")
                    or raw_job.get("title", ""))[:200]

    if not COLLECT_JOB_DETAILS:
        # LISTING ONLY. The job page is not opened -- not in this tab, not in
        # a new one. Its url is already on the card, so it is simply carried
        # through to the sheet, and the card's own fields fill the rest.
        return save_listing_only_row(raw_job, input_row)

    try:
        detail = client.get_job_detail(raw_job)
    except IndeedLoginRequired as needs_login:
        # Not a refusal: the page wants a session. No cooldown would help, so
        # the job is skipped at once and the operator is told once.
        _LOGIN_REQUIRED_SKIPS += 1
        record_failed("detail", reference,
                      "needs a signed-in session: %s" % needs_login,
                      status=needs_login.status, context=input_row,
                      recovery_status="manual")
        log("      🔑 this job page needs a signed-in session (HTTP 401) -- "
            "skipping it (%d/%d)"
            % (_LOGIN_REQUIRED_SKIPS, MAX_LOGIN_REQUIRED_SKIPS))
        if _LOGIN_REQUIRED_SKIPS == 1:
            log("         Run  python3 save_indeed_login.py  and sign in "
                "yourself to read job pages.")
        if _LOGIN_REQUIRED_SKIPS >= MAX_LOGIN_REQUIRED_SKIPS:
            raise LoginRequiredRepeatedly(
                "%d job pages in a row asked for a signed-in session"
                % _LOGIN_REQUIRED_SKIPS)
        return "skipped"

    except IndeedAccessDenied as denied:
        record_failed("detail", reference, "access denied: %s" % denied,
                      status=denied.status, context=input_row,
                      recovery_status="manual")
        raise AccessBlocked(denied.status, reference, detail=str(denied),
                            work_type="detail", scope=denied.scope,
                            context=input_row)
    except IndeedRateLimited as limited:
        record_failed("detail", reference, "rate limited", status=429,
                      context=input_row,
                      retry_delay_seconds=limited.retry_after
                      or DEFAULT_429_WAIT_SECONDS)
        raise AccessBlocked(429, reference,
                            wait_seconds=limited.retry_after,
                            detail=str(limited), work_type="detail",
                            context=input_row)
    except (IndeedServerError, IndeedNetworkError) as error:
        record_failed("detail", reference, str(error),
                      status=getattr(error, "status", None),
                      attempts=getattr(error, "attempts", 1),
                      context=input_row)
        return "failed"
    except IndeedDataError as error:
        record_failed("detail", reference, "unreadable job data: %s" % error,
                      context=input_row)
        return "failed"

    try:
        row = extract_job_details(
            detail, input_row, state_resolver=city_state_name,
            country=DEFAULT_COUNTRY, default_score=DEFAULT_JOB_SCORE,
        )
    except Exception as error:
        # Incomplete or malformed job data: log it, keep going.
        log("      ⚠️  Could not extract this job (%s: %s) -- skipping."
            % (type(error).__name__, error))
        record_failed("detail", reference, "extraction failed: %s" % error,
                      context=input_row)
        return "failed"

    kind = row.get("_apply_url_kind")
    if kind in ("missing", "invalid") and LOG_EVERY_ROW:
        log("      🔗 apply_url left blank (%s: %s)"
            % (kind, row.get("_apply_url_reason", "")))

    saved = append_detail_to_excel(row)
    if saved is None:
        return "skipped"

    _LOGIN_REQUIRED_SKIPS = 0           # a job page read fine: session is OK
    mark_failed_resolved(reference)
    return "saved"


def run_combo(client, state, role_category, keyword, city, existing_data):
    """Crawl every capped listing page for one (keyword, city) pair."""
    state_name = city_state_name(city)

    # The readable progress files keep being written, as in the reference
    # project.  They are a log, not the resume authority.
    save_city_state(city, max(1, int(state.get("current_page", 1))))
    save_role_state(role_category, keyword)

    log("\n  >> %s | %s, %s" % (keyword, city, state_name))

    page_number = max(1, int(state.get("current_page", 1)))
    total = None
    saved_here = 0
    pages_planned = None
    # Every job key this combo has seen, across all of its pages. The site
    # repeats cards once it runs out of real results, so "this page brought
    # nothing new" is the end of the results -- a more honest signal than
    # the header count, which under-reports.
    seen_in_combo = set()

    while page_number <= MAX_LISTING_PAGES:
        pacing_gate()
        wait_for_internet()

        context = {"keyword": keyword, "city": city, "page_number": page_number}
        try:
            jobs, total = client.search_jobs(keyword, city, page_number)
        except IndeedAccessDenied as denied:
            record_failed("listing", "%s|%s|p%d" % (keyword, city, page_number),
                          "access denied: %s" % denied, status=denied.status,
                          context=context, recovery_status="manual")
            raise AccessBlocked(denied.status,
                                "%s|%s|p%d" % (keyword, city, page_number),
                                detail=str(denied), work_type="listing",
                                scope=denied.scope, context=context)
        except IndeedRateLimited as limited:
            record_failed("listing", "%s|%s|p%d" % (keyword, city, page_number),
                          "rate limited", status=429, context=context,
                          retry_delay_seconds=limited.retry_after
                          or DEFAULT_429_WAIT_SECONDS)
            raise AccessBlocked(429,
                                "%s|%s|p%d" % (keyword, city, page_number),
                                wait_seconds=limited.retry_after,
                                detail=str(limited), work_type="listing",
                                context=context)
        except (IndeedServerError, IndeedNetworkError, IndeedDataError) as error:
            record_failed("listing", "%s|%s|p%d" % (keyword, city, page_number),
                          str(error), status=getattr(error, "status", None),
                          attempts=getattr(error, "attempts", 1),
                          context=context)
            log("     ⚠️  page %d unavailable (%s) -- queued, moving on."
                % (page_number, type(error).__name__))
            break

        if pages_planned is None:
            pages_planned = client.page_count(
                total, cap=MAX_LISTING_PAGES, minimum=MIN_LISTING_PAGES)
            if total == 0 and jobs:
                # The header said nothing but the page is full of cards, so
                # the header is not to be trusted at all here. Capping at the
                # FLOOR would miss real pages (the floor can be 1), so the
                # ceiling applies and the early-stop rules below -- an empty
                # page, or a page with nothing new -- decide where to stop.
                pages_planned = max(1, MAX_LISTING_PAGES)
                log("     header reported 0 jobs but the page has %d card(s)"
                    " -- ignoring the header, crawling up to %d page(s) and "
                    "stopping when a page brings nothing new"
                    % (len(jobs), pages_planned))
            else:
                log("     %s job(s) reported -> %d page(s) to crawl "
                    "(floor %d, cap %d)"
                    % ("{:,}".format(total), pages_planned,
                       MIN_LISTING_PAGES, MAX_LISTING_PAGES))
            if total == 0 and not jobs:
                break

        page_keys = {str(job.get("job_key") or job.get("source_url") or "")
                     for job in jobs}
        page_keys.discard("")
        new_keys = page_keys - seen_in_combo
        seen_in_combo |= page_keys

        log("     page %d: %d listing(s) (%d new)"
            % (page_number, len(jobs), len(new_keys)))

        already_done = set(state.get("done_job_urls") or [])

        page_started = time.monotonic()
        job_times = []

        for job_index, raw_job in enumerate(jobs, start=1):
            job_url = str(raw_job.get("source_url")
                          or raw_job.get("url")
                          or raw_job.get("job_key") or "")
            job_title = clean_value(raw_job.get("title")
                                    or raw_job.get("job_title"))

            if job_url and job_url in already_done:
                log("       (already saved on this page -- skipping) %s"
                    % job_title[:60])
                continue

            key = make_unique_key(role_category, job_title, city)
            if key in existing_data:
                entry = existing_data[key]
                try:
                    seen = int(entry.get("seen_count", 0))
                except (TypeError, ValueError):
                    seen = 0
                entry["seen_count"] = seen + 1
                entry["last_seen_at"] = now_time()
                entry["is_active"] = 1
                if not entry.get("sample_job_url"):
                    entry["sample_job_url"] = job_url
            else:
                existing_data[key] = {
                    "state": state_name,
                    "role_category": role_category,
                    "job_title": job_title,
                    "location": city,
                    "search_keyword": keyword,
                    "sample_job_url": job_url,
                    "first_seen_at": now_time(),
                    "last_seen_at": now_time(),
                    "seen_count": 1,
                    "is_active": 1,
                    "source_platform": SOURCE_PLATFORM,
                    "source_type": "job_portal",
                }

            input_row = {
                "state": state_name,
                "role_category": role_category,
                "job_title": job_title,
                "location": city,
                "city": city,
                "search_keyword": keyword,
                "source_platform": SOURCE_PLATFORM,
                "source_type": "job_portal",
            }

            # The hourly cap is checked HERE, before the request, so the
            # ceiling holds however the gaps are set.
            #
            # It bounds REQUESTS, and in listing-only mode a job costs no
            # request at all -- the whole page of cards arrived in the one
            # listing request already made. Counting cards against a
            # request cap would idle for hours while fetching nothing, so
            # the cap applies only when job pages are actually opened. The
            # listing requests themselves stay bounded by REQUEST_GAP_SECONDS
            # and the city and keyword delays.
            if COLLECT_JOB_DETAILS:
                wait_for_job_quota()
                note_job_read(state)

            job_started = time.monotonic()
            outcome = process_job(client, raw_job, input_row)
            job_seconds = time.monotonic() - job_started
            if outcome == "saved":
                saved_here += 1

            if SHOW_JOB_TIMER:
                job_times.append(job_seconds)
                average = sum(job_times) / len(job_times)
                log("      ⏱  job %d/%d %s in %.1fs | avg %.1fs | page "
                    "elapsed %s"
                    % (job_index, len(jobs), outcome, job_seconds, average,
                       human_duration(time.monotonic() - page_started)))

            if job_url:
                already_done.add(job_url)
                state["done_job_urls"] = sorted(already_done)
                # Checkpointed per job: a crash cannot lose or repeat a row.
                save_crawl_cycle_state(state)

            # Labelled, so the wait before the next job page is visible
            # rather than looking like a hang.
            # The gap applies in BOTH modes. It was briefly skipped in
            # listing-only mode on the grounds that a card costs no request
            # -- true, but it misses the point: the wait is what spaces out
            # the listing page requests, which are the ones being refused.
            # The last card of a page is not waited on; the city and keyword
            # delays take over there.
            if job_index < len(jobs):
                sleep_between(JOB_GAP_MIN_SECONDS, JOB_GAP_MAX_SECONDS,
                              "next job card in" if SHOW_JOB_TIMER else "")

        save_master(pd.DataFrame(existing_data.values()))

        if SHOW_JOB_TIMER and jobs:
            log("     page %d done: %d job(s) in %s (%d saved so far for this "
                "city)" % (page_number, len(jobs),
                           human_duration(time.monotonic() - page_started),
                           saved_here))

        if not jobs:
            if page_number > 1:
                log("     empty page -- stopping pagination early.")
            break

        # The checkpoint advances AFTER the page's work, never before it.
        page_number += 1
        state["current_page"] = page_number
        state["done_job_urls"] = []
        save_crawl_cycle_state(state)
        save_city_state(city, page_number)

        if STOP_ON_PAGE_WITH_NO_NEW_JOBS and jobs and not new_keys:
            log("     every job on page %d was already collected for this "
                "city -- end of results, moving on" % (page_number - 1))
            state["current_page"] = 1
            state["done_job_urls"] = []
            save_crawl_cycle_state(state)
            return saved_here

        if pages_planned is not None and page_number > pages_planned:
            break

        # DON'T SPEND A REQUEST ON A PAGE THAT CANNOT HOLD ANYTHING NEW.
        #
        # The site paginates in steps of INDEED_PAGE_STEP (10) but a page
        # DISPLAYS more than that -- 16 cards is typical. So a search the
        # header reports as 16 results is "2 pages" by the step, while page
        # 1 already showed all 16. The old code requested page 2 anyway,
        # got back the same cards, and STOP_ON_PAGE_WITH_NO_NEW_JOBS then
        # noticed. That is one wasted request per combo, and a wasted
        # request is what triggers a refusal and the 10-minute cooldown
        # after it.
        #
        # Counting what actually arrived is the honest check: once the cards
        # in hand cover the reported total, the results are exhausted.
        if total and len(seen_in_combo) >= int(total):
            log("     page %d already held all %s reported result(s) -- "
                "no page %d request needed"
                % (page_number - 1, "{:,}".format(total), page_number))
            state["current_page"] = 1
            state["done_job_urls"] = []
            save_crawl_cycle_state(state)
            return saved_here

    state["current_page"] = 1
    state["done_job_urls"] = []
    return saved_here


# ===========================================================================
# THE FAILED-WORK REPLAY
# ===========================================================================

_COMBOS_SINCE_QUEUE = 0


def queue_visit_due() -> bool:
    global _COMBOS_SINCE_QUEUE
    _COMBOS_SINCE_QUEUE += 1
    if _COMBOS_SINCE_QUEUE >= QUEUE_CHECK_EVERY_COMBOS:
        _COMBOS_SINCE_QUEUE = 0
        return True
    return False


def process_failed_queue(client, existing_data) -> None:
    """Replay a few eligible queued listings, under strict limits.

    Runs only between combos, only on entries whose retry time has passed,
    and never on an entry marked "manual" (an access refusal).  A queued item
    that fails again is deferred, not retried in a loop.
    """
    batch = queue_eligible(QUEUE_BATCH_SIZE)
    if not batch:
        return

    log("\n  [QUEUE] replaying %d queued item(s)." % len(batch))
    for entry in batch:
        keyword = entry.get("keyword")
        city = entry.get("city")
        page = int(entry.get("page_number") or 1)
        if not keyword or not city:
            defer_queue_entry(entry, "not enough context to replay")
            continue

        try:
            jobs, _total = client.search_jobs(keyword, city, page)
        except AccessBlocked:
            raise
        except IndeedAccessDenied:
            defer_queue_entry(entry, "still refused")
            return
        except IndeedClientError as error:
            defer_queue_entry(entry, str(error))
            continue

        role_category = entry.get("role_category") or ROLE_CATEGORY
        recovered = 0
        for raw_job in jobs:
            input_row = {
                "state": city_state_name(city),
                "role_category": role_category,
                "job_title": clean_value(raw_job.get("title")),
                "location": city,
                "city": city,
                "search_keyword": keyword,
                "source_platform": SOURCE_PLATFORM,
                "source_type": "job_portal",
            }
            try:
                if process_job(client, raw_job, input_row) == "saved":
                    recovered += 1
            except AccessBlocked:
                defer_queue_entry(entry, "refused during replay")
                raise
        mark_failed_resolved(entry.get("reference"))
        log("  [QUEUE] %s | %s page %d replayed: %d row(s) recovered."
            % (keyword, city, page, recovered))

    save_master(pd.DataFrame(existing_data.values()))


# ===========================================================================
# ONE COMPLETE CRAWL CYCLE
# ===========================================================================

def run_crawl_cycle(client, state, existing_data) -> str:
    """Walk the cycle's frozen keyword order.  Returns "complete" when done.

    Raises ScheduledPause for a scheduled stop and AccessBlocked for a 403 or
    429, so the orchestrator can wait and resume from the checkpoint.  Neither
    ever marks the cycle complete.
    """
    order = state["keyword_order"]
    cities = state["cities"]
    keyword_index, city_index, _page = resume_position(state)

    while keyword_index < len(order):
        role_category, keyword = order[keyword_index]

        log("\n%s\nCYCLE %d | KEYWORD %d/%d: %s\n%s"
            % ("=" * 72, state["crawl_cycle_number"], keyword_index + 1,
               len(order), keyword, "=" * 72))

        while city_index < len(cities):
            state["current_keyword_index"] = keyword_index
            state["current_city_index"] = city_index
            save_crawl_cycle_state(state)

            pacing_gate()

            log("  [cycle %d | keyword %d/%d | city %d/%d | worked %s]"
                % (state["crawl_cycle_number"], keyword_index + 1, len(order),
                   city_index + 1, len(cities), human_duration(work_seconds())))

            run_combo(client, state, role_category, keyword,
                      cities[city_index], existing_data)

            city_index += 1
            state["current_city_index"] = city_index
            state["current_page"] = 1
            state["done_job_urls"] = []
            save_crawl_cycle_state(state)

            # A combo boundary is the only safe place to replay the queue:
            # the checkpoint above is consistent and no job is in flight.
            if queue_visit_due():
                process_failed_queue(client, existing_data)

            if city_index < len(cities):
                sleep_between(CITY_GAP_MIN_SECONDS,
                              CITY_GAP_MAX_SECONDS,
                              "next city in")

        keyword_index += 1
        city_index = 0
        state["current_keyword_index"] = keyword_index
        state["current_city_index"] = 0
        state["current_page"] = 1
        state["done_job_urls"] = []
        save_crawl_cycle_state(state)

        if keyword_index < len(order):
            sleep_between(KEYWORD_GAP_MIN_SECONDS,
                          KEYWORD_GAP_MAX_SECONDS,
                          "next keyword in")

    return "complete"


# ===========================================================================
# ORCHESTRATOR
# ===========================================================================

def save_checkpoint_now() -> bool:
    """Write the crawl checkpoint. True when it was saved."""
    if not callable(_CHECKPOINT_SAVER):
        return False
    try:
        _CHECKPOINT_SAVER()
        return True
    except Exception as error:                       # pragma: no cover
        log("[STOP] Checkpoint save failed: %s" % error)
        return False


def stop_now(exit_code=130) -> None:
    """Save, kill the browser, and end the process -- immediately.

    This runs inside a signal handler, so it must not wait for anything:

      * the checkpoint is one small atomic JSON write, so it is safe here
        and means the next run resumes from this exact position;
      * the browser is KILLED rather than closed, because a polite close
        needs an idle driver connection and there is a navigation in
        flight -- that is what produced "Connection closed while reading
        from the driver" in the log before this existed;
      * ``os._exit`` ends the process without unwinding, so no library can
        swallow the exit, no traceback is printed, no further job is
        scraped and nothing is queued for a retry.
    """
    saved = save_checkpoint_now()
    if callable(_CLIENT_KILLER):
        try:
            _CLIENT_KILLER()
        except Exception:
            pass
    log("[STOP] Stopped. Checkpoint %s -- re-run to continue from here."
        % ("saved" if saved else "unchanged"))
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    _exit(int(exit_code))


def install_signal_handlers() -> None:
    """Ctrl+C stops immediately; a service stop (SIGTERM) stops gracefully.

    The first thing the handler does is disarm BOTH signals, so pressing
    Ctrl+C again cannot re-enter it and print a second message -- a repeated
    Ctrl+C is simply ignored while the stop completes.
    """
    def handler(signum, _frame):
        global _SHUTDOWN_REQUESTED

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, signal.SIG_IGN)
            except (ValueError, OSError):            # pragma: no cover
                pass

        already_asked = _SHUTDOWN_REQUESTED
        _SHUTDOWN_REQUESTED = True

        interrupt = signum == getattr(signal, "SIGINT", None)
        if already_asked or (interrupt and STOP_IMMEDIATELY_ON_CTRL_C):
            print("", flush=True)                    # close off the "^C" line
            stop_now(130 if interrupt else 143)
            return                                   # unreachable

        # The graceful path: a service stop finishes the job in hand.
        log("\n[STOP] Signal %s received -- finishing this job, saving the "
            "checkpoint and stopping." % signum)
        save_checkpoint_now()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):                # pragma: no cover
            pass                                      # not the main thread


def client_success_count(client) -> int:
    """How many requests this client has answered successfully.

    Used to tell a fresh refusal from a continuing one: if this number has
    moved since the last block, real collection happened in between and the
    403 escalation starts again at 1x.
    """
    return int(getattr(client, "successful_requests", 0) or 0)


def describe_escalation() -> str:
    """e.g. "10m 00s, 20m 00s, 30m 00s, 40m 00s" -- for the log."""
    return ", ".join(human_duration(ACCESS_BREAK_SECONDS * step)
                     for step in range(1, max(1, MAX_BLOCK_COOLDOWNS) + 1))


def print_startup_banner(cities, plan_pairs, client) -> None:
    log("\n" + "=" * 72)
    log("INDEED COLLECTOR -- continuous keyword-first cycles")
    log("=" * 72)
    log("  project root       : %s" % BASE_DIR)
    log("  rank / num_sys     : %s / %s" % (rank, num_sys))
    log("  cities for rank %-2s : %d  (%s ... %s)"
        % (rank, len(cities), cities[0], cities[-1]))
    if role_plan_split is not None:
        target, other, percent = role_plan_split()
        log("  keywords in plan   : %d  (%d target + %d other IT = %.1f%% "
            "target)" % (len(plan_pairs), target, other, percent))
    else:
        log("  keywords in plan   : %d" % len(plan_pairs))
    log("  combos per cycle   : %s"
        % "{:,}".format(len(plan_pairs) * len(cities)))
    log("  listing page cap   : %d" % MAX_LISTING_PAGES)
    log("  order              : department -> shuffled keyword -> all rank "
        "cities -> pages -> jobs")
    log("  night pause        : %s (%02d:00 - %02d:00 %s)"
        % ("on" if NIGHT_PAUSE_ENABLED else "off", NIGHT_START_HOUR,
           NIGHT_END_HOUR, TIMEZONE))
    log("  work / break       : %.1f-%.1f h of WORKING time, then a random "
        "%d-%d min break" % (WORK_PERIOD_MIN_HOURS, WORK_PERIOD_MAX_HOURS,
                             WORK_BREAK_MIN_MINUTES, WORK_BREAK_MAX_MINUTES))
    log("                       (pauses, night stops and cooldowns do not "
        "count as working time)")
    log("  cycle cooldown     : %d min" % CYCLE_COOLDOWN_MINUTES)
    log("  crawl state        : %s" % CRAWL_CYCLE_STATE_FILE)
    log("  excel working file : %s (archived every %d accepted rows as "
        "current_data_cycleN.xlsx)" % (DETAIL_OUTPUT_FILE, EXCEL_BATCH_SIZE))
    log("  excel master file  : %s" % MASTER_OUTPUT_FILE)
    log("  excel archive #    : %d so far (cycle_state.json)" % load_cycle_num())
    log("  data source        : %s" % client.describe())
    if BROWSER_VISIBLE and BROWSER_REQUIRED:
        log("  browser            : Chromium %s, %dx%d, slow_mo %dms"
            % ("HEADLESS" if BROWSER_HEADLESS else "VISIBLE on %s"
               % (os.environ.get("DISPLAY") or "<no DISPLAY set>"),
               BROWSER_WIDTH, BROWSER_HEIGHT, BROWSER_SLOW_MO_MS))
    if LIVE_COLLECTION_ENABLED:
        log("  live site          : %s" % INDEED_BASE_URL)
        log("  search url         : %s"
            % INDEED_SEARCH_URL_TEMPLATE.replace("{base}", ""))
        auth_path = project_path(AUTH_FILE)
        if USE_SAVED_LOGIN and os.path.exists(auth_path):
            log("  saved login        : %s (loaded)" % AUTH_FILE)
        elif USE_SAVED_LOGIN:
            log("  saved login        : none -- running SIGNED OUT. Run "
                "save_indeed_login.py")
        else:
            log("  saved login        : disabled")
        log("  robots rules       : %s never opened"
            % ", ".join(ROBOTS_DISALLOWED_PREFIXES))
        log("  blocks             : a challenge, 403 or 429 STOPS that work "
            "and is recorded;")
        log("                       no CAPTCHA solving, no proxy rotation, "
            "no fingerprint spoofing")
    if getattr(client, "is_demonstration", True):
        log("")
        log("  " + "!" * 68)
        log("  !! DEMONSTRATION MODE -- NOT LIVE INDEED DATA")
        log("  !! LIVE_COLLECTION_ENABLED is False, so the browser walks")
        log("  !! LOCAL DEMONSTRATION PAGES generated on this machine. No")
        log("  !! request reaches Indeed, and every row written is a")
        log("  !! labelled fixture row that must not be loaded into the")
        log("  !! production database.")
        log("  " + "!" * 68)
    log("=" * 72 + "\n")


def load_existing_master_map() -> dict:
    existing = {}
    master = load_existing_master()
    if not master.empty:
        for _index, row in master.iterrows():
            existing[make_unique_key(row.get("role_category", ""),
                                     row.get("job_title", ""),
                                     row.get("location", ""))] = row.to_dict()
    return existing


def main(max_cycles=None, client=None) -> int:
    """Run crawl cycles until stopped, or ``max_cycles`` cycles for a test."""
    global _CHECKPOINT_SAVER, _CLIENT_CLOSER, _CLIENT_KILLER

    install_signal_handlers()

    cities_now = rank_cities()
    plan_pairs = keyword_plan_pairs()
    client = client or build_client()
    _CLIENT_CLOSER = client.cleanup
    _CLIENT_KILLER = client.kill
    print_startup_banner(cities_now, plan_pairs, client)

    reset_work_timer()
    block_cooldowns = 0
    block_progress_mark = 0
    cycles_done = 0

    while True:
        if _SHUTDOWN_REQUESTED:
            log("[STOP] Shutdown requested -- nothing in flight, exiting.")
            client.cleanup()
            return 0
        if max_cycles is not None and cycles_done >= int(max_cycles):
            client.cleanup()
            return 0

        try:
            pacing_gate()
        except ScheduledPause as pause:
            sleep_long(pause.seconds, pause.reason)
            if pause.reset_work_timer:
                reset_work_timer()
            continue
        except KeyboardInterrupt:
            return 0

        state = load_or_open_crawl_cycle(cities_now, plan_pairs)
        _CHECKPOINT_SAVER = lambda: save_crawl_cycle_state(state)   # noqa: E731

        keyword_index, city_index, page_number = resume_position(state)
        order = state["keyword_order"]
        if (keyword_index, city_index, page_number) != (0, 0, 1) \
                and keyword_index < len(order):
            log("RESUMING cycle %d at keyword %d/%d (%s) | city %d/%d (%s) | "
                "page %d"
                % (state["crawl_cycle_number"], keyword_index + 1, len(order),
                   order[keyword_index][1], city_index + 1,
                   len(state["cities"]), state["cities"][city_index],
                   page_number))

        load_job_read_times(state)      # the hourly cap survives a restart
        existing_data = load_existing_master_map()

        try:
            # In browser mode this is where Chromium actually appears.  The
            # window is opened per cycle attempt and closed again before any
            # long wait, so a paused collector does not leave a browser
            # sitting on the desktop for 45 minutes.
            client.open()
            result = run_crawl_cycle(client, state, existing_data)

        except ScheduledPause as pause:
            save_crawl_cycle_state(state)
            save_master(pd.DataFrame(existing_data.values()))
            client.close()
            sleep_long(pause.seconds, pause.reason)
            if pause.reset_work_timer:
                reset_work_timer()
            continue

        except LoginRequiredRepeatedly as needs_login:
            save_crawl_cycle_state(state)
            save_master(pd.DataFrame(existing_data.values()))
            log("\n" + "=" * 72)
            log("STOPPING: %s." % needs_login)
            log("")
            log("Signed out, Indeed serves LISTING pages but answers HTTP 401")
            log("for job pages, so every job would be skipped and nothing")
            log("collected. This is not a block and no cooldown will fix it.")
            log("")
            log("  Sign in yourself:   python3 save_indeed_login.py")
            log("")
            log("The checkpoint is intact -- cycle %d, keyword %d/%d, "
                "city %d/%d." % (state["crawl_cycle_number"],
                                 state["current_keyword_index"] + 1,
                                 len(state["keyword_order"]),
                                 state["current_city_index"] + 1,
                                 len(state["cities"])))
            log("=" * 72)
            hold_browser_on_screen(client)
            client.cleanup()
            return 3

        except AccessBlocked as blocked:
            # Decide what to step over BEFORE the break, so the resumed run
            # does not walk straight back into the refused unit.
            if blocked.scope != "session_wide":
                if blocked.work_type == "detail":
                    done = set(state.get("done_job_urls") or [])
                    done.add(blocked.reference)
                    state["done_job_urls"] = sorted(done)
                    log("  -> isolated: this job is skipped; it stays in %s "
                        "for later." % FAILED_PAGES_FILE)
                else:
                    failed_page = blocked.context.get("page_number")
                    if failed_page:
                        state["current_page"] = int(failed_page) + 1
                        state["done_job_urls"] = []
                        log("  -> isolated: listing page %d stays in %s; "
                            "resuming at page %d."
                            % (failed_page, FAILED_PAGES_FILE,
                               int(failed_page) + 1))
            save_crawl_cycle_state(state)
            save_master(pd.DataFrame(existing_data.values()))
            client.close()

            # "Consecutive" means with no successful page in between. A
            # refusal hours after the last one, with collection in between,
            # starts the escalation again at 1x rather than carrying on from
            # where a long-gone problem left off.
            progress_now = client_success_count(client)
            if progress_now > block_progress_mark:
                block_cooldowns = 0
            block_progress_mark = progress_now

            block_cooldowns += 1
            if blocked.scope == "session_wide":
                log("\n" + "=" * 72)
                log("STOPPING. %s looks SESSION-WIDE: distinct references are "
                    "being refused," % blocked.label)
                log("so this is not one bad page. Working around an access "
                    "control would be exactly")
                log("the wrong response, so the run ends here with every "
                    "checkpoint preserved:")
                log("  cycle %d, keyword %d/%d, city %d/%d, page %d"
                    % (state["crawl_cycle_number"],
                       state["current_keyword_index"] + 1,
                       len(state["keyword_order"]),
                       state["current_city_index"] + 1, len(state["cities"]),
                       state["current_page"]))
                log("Queued in %s for review. No CAPTCHA handling, no proxy "
                    "or IP rotation," % FAILED_PAGES_FILE)
                log("no fingerprint spoofing.")
                log("=" * 72)
                client.cleanup()
                return 2

            if block_cooldowns > MAX_BLOCK_COOLDOWNS:
                # The escalation is spent. The unit was already marked
                # skipped above, so give up on it and carry on with the rest
                # of the crawl rather than ending the run.
                log("\n" + "-" * 72)
                log("GIVING UP ON THIS UNIT. %s survived all %d escalating "
                    "cooldowns" % (blocked.label, MAX_BLOCK_COOLDOWNS))
                log("(%s). The %s is skipped and recorded in %s; the crawl "
                    "continues with" % (describe_escalation(), blocked.work_type,
                                        FAILED_PAGES_FILE))
                log("the next one, from cycle %d, keyword %d/%d, city %d/%d."
                    % (state["crawl_cycle_number"],
                       state["current_keyword_index"] + 1,
                       len(state["keyword_order"]),
                       state["current_city_index"] + 1, len(state["cities"])))
                log("-" * 72 + "\n")
                record_failed(blocked.work_type, blocked.reference,
                              "skipped after %d escalating cooldowns"
                              % MAX_BLOCK_COOLDOWNS,
                              status=blocked.status,
                              context=blocked.context,
                              recovery_status="manual")
                block_cooldowns = 0
                block_progress_mark = client_success_count(client)
                continue

            # The wait grows with each consecutive refusal: 1x, 2x, 3x, 4x.
            # max(), never min(): a longer Retry-After from the source must
            # never be shortened by our own schedule.
            wait = max(ACCESS_BREAK_SECONDS * block_cooldowns,
                       int(blocked.wait_seconds or 0))
            sleep_long(wait, "%s access break %d/%d -- %dx the %s base "
                             "cooldown (checkpoint saved; the next real "
                             "request tells us whether access is back)"
                       % (blocked.label, block_cooldowns, MAX_BLOCK_COOLDOWNS,
                          block_cooldowns,
                          human_duration(ACCESS_BREAK_SECONDS)))
            continue

        except KeyboardInterrupt:
            save_crawl_cycle_state(state)
            save_master(pd.DataFrame(existing_data.values()))
            client.cleanup()
            log("\nStopped. Checkpoint saved -- re-run to continue from here.")
            return 0

        except Exception as error:
            # One bad page must never end a run that has been collecting for
            # hours.  Save, cool down, resume from the checkpoint.
            save_crawl_cycle_state(state)
            save_master(pd.DataFrame(existing_data.values()))
            client.close()
            log("\n  [ERROR] %s: %s" % (type(error).__name__, error))
            log("  Checkpoint saved. Cooling down, then resuming.")
            sleep_long(UNEXPECTED_ERROR_COOLDOWN_SECONDS, "unexpected error")
            continue

        block_cooldowns = 0
        save_master(pd.DataFrame(existing_data.values()))
        client.close()              # nothing in flight: let the window go

        if result == "complete":
            complete_crawl_cycle(state)
            cycles_done += 1

            pending, due, manual, resolved = failed_queue_summary()
            if pending or manual or resolved:
                log("   %s: %d pending (%d due), %d needing manual attention, "
                    "%d recovered."
                    % (FAILED_PAGES_FILE, pending, due, manual, resolved))
                if manual:
                    log("   Entries marked 'manual' were refused access -- "
                        "check the source agreement before retrying them.")

            if max_cycles is not None and cycles_done >= int(max_cycles):
                client.cleanup()
                return 0

            sleep_long(CYCLE_COOLDOWN_MINUTES * 60,
                       "cooldown after crawl cycle %d (keywords reshuffle for "
                       "cycle %d)" % (state["crawl_cycle_number"],
                                      state["crawl_cycle_number"] + 1))
            reset_work_timer()
            continue

        log("  [WARN] crawl cycle returned %r instead of 'complete' -- "
            "cooling down before retrying from the checkpoint." % (result,))
        sleep_long(UNEXPECTED_ERROR_COOLDOWN_SECONDS, "unexpected cycle result")


if __name__ == "__main__":
    sys.exit(main())
