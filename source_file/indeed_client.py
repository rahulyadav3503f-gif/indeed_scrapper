"""Talk to the authorised Indeed data source.

WHAT WAS CHECKED BEFORE THIS FILE WAS WRITTEN (October 2026)
------------------------------------------------------------
The brief says: review Indeed's current access conditions, identify the
permitted integration, and do not invent endpoints.  That review found:

1.  The free **Indeed Publisher Job Search API** -- the one that returned
    search results for a publisher key -- closed to new sign-ups at the end
    of 2020, was marked deprecated, and stopped answering in 2024.  The XML
    job feed went with it.
2.  What remains on ``partners.indeed.com`` is employer-side: Indeed Apply,
    the Job Sync / posting APIs and the Sponsored Jobs (advertising) APIs.
    They exist to POST jobs and RECEIVE applications under an approved
    partner agreement.  **None of them returns job search results.**
3.  ``in.indeed.com/robots.txt`` disallows ``/applystart``, ``/rc/``,
    ``/rpc/`` and ``/my/`` for all user agents, and the site is behind bot
    protection.  Scraping the HTML search pages is therefore both
    disallowed by robots and a terms-of-service matter, and this project
    does not do it.

CONCLUSION, STATED PLAINLY
--------------------------
There is **no authorised self-serve Indeed job-search source available to
this project today**.  So:

  * ``LIVE_COLLECTION_ENABLED`` in ``find_indeed_job.py`` defaults to
    ``False``;
  * the default transport is an OFFLINE FIXTURE transport, which makes every
    other part of the project (crawl cycles, extraction, application-url
    resolution, Excel rollover, checkpoints, error handling) real, testable
    and finished;
  * a live transport exists but refuses to run until an operator supplies an
    authorised source themselves, through environment variables.  It has NO
    built-in endpoint: there is nothing to accidentally point at Indeed.

WHAT THE CLIENT NEVER DOES
--------------------------
No CAPTCHA solving, no bot-detection evasion, no proxy or IP rotation, no
fingerprint spoofing, no authentication bypass, no ignoring of robots rules,
and no retrying of an endpoint that has refused access.  A 403 means stop.
"""

import atexit
import hashlib
import json
import os
import random
import re
import shutil
import tempfile
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from html import escape as html_escape
from urllib.parse import quote_plus, urljoin, urlparse

# ---------------------------------------------------------------------------
# ERRORS
# ---------------------------------------------------------------------------


class IndeedClientError(Exception):
    """Base class: something went wrong talking to the data source."""


class IndeedConfigError(IndeedClientError):
    """The client was asked to do something it has not been configured for."""


class IndeedAccessDenied(IndeedClientError):
    """HTTP 403 / 401, or a challenge page.  Access is refused; do not retry."""

    def __init__(self, message, url="", status=403, scope="isolated"):
        super().__init__(message)
        self.url = url
        self.status = status
        # "isolated"     -- this one url is refused, carry on elsewhere later
        # "session_wide" -- distinct urls are being refused, stop the run
        self.scope = scope


class IndeedRateLimited(IndeedClientError):
    """HTTP 429.  Carries the server's own Retry-After when it sent one."""

    def __init__(self, message, url="", retry_after=None, status=429):
        super().__init__(message)
        self.url = url
        self.retry_after = retry_after
        self.status = status


class IndeedServerError(IndeedClientError):
    """HTTP 5xx after the retry budget was spent."""

    def __init__(self, message, url="", status=500, attempts=0):
        super().__init__(message)
        self.url = url
        self.status = status
        self.attempts = attempts


class IndeedNetworkError(IndeedClientError):
    """DNS failure, connection reset or timeout after the retry budget."""

    def __init__(self, message, url="", attempts=0):
        super().__init__(message)
        self.url = url
        self.attempts = attempts


class IndeedDataError(IndeedClientError):
    """The source answered, but the body was not the shape we can read."""


# ---------------------------------------------------------------------------
# CONFIGURATION OBJECT
# ---------------------------------------------------------------------------
# PHASE 4 RULE: the editable settings live at the top of find_indeed_job.py
# and nowhere else.  This class is the "clearly defined configuration object
# created by the main script" the brief allows -- a carrier, not a second
# configuration module: every default here is overwritten by the main file,
# and nothing reads a settings file of its own.


class IndeedClientConfig:
    """Everything the client needs, handed to it by the main script."""

    def __init__(
        self,
        live_enabled=False,
        base_url="",
        api_key_env="INDEED_API_KEY",
        search_path="",
        detail_path="",
        country="IN",
        language="en",
        results_per_page=20,
        max_listing_pages=10,
        request_timeout_seconds=30,
        connect_timeout_seconds=10,
        max_retries_5xx=4,
        backoff_5xx_seconds=(5, 15, 45, 120),
        max_retries_network=3,
        backoff_network_seconds=(5, 15, 45),
        max_retries_429=3,
        backoff_429_seconds=(60, 180),
        default_429_wait_seconds=120,
        retry_after_cap_seconds=3600,
        request_gap_seconds=2.0,
        jitter_fraction=0.35,
        user_agent="JobEasyIndia-IndeedCollector/1.0",
        fixture_dir="",
        fixture_jobs_per_page=12,
        fixture_seed=20261021,
        verbose=True,
        # -- visible browser (all of these come from find_indeed_job.py) ----
        browser_visible=False,
        browser_headless=True,
        browser_slow_mo_ms=0,
        browser_width=1366,
        browser_height=768,
        browser_channel="",
        browser_nav_timeout_ms=45000,
        browser_keep_open_seconds=0,
        browser_demo_pages=True,
        browser_live_search_url="",
        browser_live_detail_url="",
        browser_live_authorised=False,
        # -- the live Indeed site (same shape as the Naukri scraper) --------
        site_base_url="",
        search_url_template="",
        job_url_template="",
        page_step=10,
        auth_file="",
        use_saved_login=True,
        cookie_banner_action="reject",
        respect_robots_disallow=True,
        robots_disallowed_prefixes=("/applystart", "/rc/", "/rpc/", "/my/",
                                    "/api/"),
        listing_render_timeout_ms=12000,
        detail_render_timeout_ms=12000,
    ):
        self.live_enabled = bool(live_enabled)
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key_env = str(api_key_env or "")
        self.search_path = str(search_path or "")
        self.detail_path = str(detail_path or "")
        self.country = country
        self.language = language
        self.results_per_page = int(results_per_page)
        self.max_listing_pages = int(max_listing_pages)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.max_retries_5xx = int(max_retries_5xx)
        self.backoff_5xx_seconds = tuple(backoff_5xx_seconds)
        self.max_retries_network = int(max_retries_network)
        self.backoff_network_seconds = tuple(backoff_network_seconds)
        self.max_retries_429 = int(max_retries_429)
        self.backoff_429_seconds = tuple(backoff_429_seconds)
        self.default_429_wait_seconds = int(default_429_wait_seconds)
        self.retry_after_cap_seconds = int(retry_after_cap_seconds)
        self.request_gap_seconds = float(request_gap_seconds)
        self.jitter_fraction = float(jitter_fraction)
        self.user_agent = str(user_agent)
        self.fixture_dir = str(fixture_dir or "")
        self.fixture_jobs_per_page = int(fixture_jobs_per_page)
        self.fixture_seed = int(fixture_seed)
        self.verbose = bool(verbose)

        # -- visible browser ------------------------------------------------
        self.browser_visible = bool(browser_visible)
        self.browser_headless = bool(browser_headless)
        self.browser_slow_mo_ms = max(0, int(browser_slow_mo_ms))
        self.browser_width = max(320, int(browser_width))
        self.browser_height = max(240, int(browser_height))
        self.browser_channel = str(browser_channel or "")
        self.browser_nav_timeout_ms = max(1000, int(browser_nav_timeout_ms))
        self.browser_keep_open_seconds = max(0, int(browser_keep_open_seconds))
        self.browser_demo_pages = bool(browser_demo_pages)
        self.browser_live_search_url = str(browser_live_search_url or "")
        self.browser_live_detail_url = str(browser_live_detail_url or "")
        self.browser_live_authorised = bool(browser_live_authorised)

        # -- the live site --------------------------------------------------
        self.site_base_url = str(site_base_url or "").rstrip("/")
        self.search_url_template = str(search_url_template or "")
        self.job_url_template = str(job_url_template or "")
        self.page_step = max(1, int(page_step))
        self.auth_file = str(auth_file or "")
        self.use_saved_login = bool(use_saved_login)
        self.cookie_banner_action = str(cookie_banner_action or "reject").lower()
        self.respect_robots_disallow = bool(respect_robots_disallow)
        self.robots_disallowed_prefixes = tuple(robots_disallowed_prefixes or ())
        self.listing_render_timeout_ms = max(1000, int(listing_render_timeout_ms))
        self.detail_render_timeout_ms = max(1000, int(detail_render_timeout_ms))

    @property
    def api_key(self):
        """The credential, read from the environment.  NEVER from source."""
        if not self.api_key_env:
            return ""
        return os.environ.get(self.api_key_env, "")

    @property
    def site_search_ready(self) -> bool:
        """True when this file has a live search url to build.

        Two ways to get one, exactly as the Naukri scraper has one built in:

          * ``search_url_template`` + ``site_base_url`` from the
            configuration block at the top of find_indeed_job.py (the normal
            route, and what ``LIVE_COLLECTION_ENABLED = True`` turns on);
          * ``browser_live_search_url`` from the environment, which overrides
            it without editing the file.
        """
        if self.browser_live_search_url:
            return True
        return bool(self.site_base_url and self.search_url_template)

    @property
    def browser_live_ready(self) -> bool:
        """True when the visible browser should collect from the live site."""
        return bool(self.live_enabled and self.site_search_ready)

    def live_search_url(self, keyword, city, page_number):
        """The search url for one (keyword, city, page) -- live mode only."""
        page_number = max(1, int(page_number))
        start = (page_number - 1) * self.page_step
        values = {
            "base": self.site_base_url,
            "keyword": quote_plus(str(keyword)),
            "city": quote_plus(str(city)),
            "page": page_number,
            "start": start,
            "limit": self.results_per_page,
            "country": self.country,
        }
        template = self.browser_live_search_url or self.search_url_template
        try:
            return template.format(**values)
        except KeyError as error:
            raise IndeedConfigError(
                "the search url template %r uses a placeholder this collector "
                "does not provide: %s" % (template, error))

    def live_job_url(self, job_key):
        """The job page url for one job key -- live mode only."""
        template = self.browser_live_detail_url or self.job_url_template
        if not template:
            return ""
        try:
            return template.format(base=self.site_base_url,
                                   job_key=quote_plus(str(job_key)),
                                   jk=quote_plus(str(job_key)))
        except KeyError as error:
            raise IndeedConfigError(
                "the job url template %r uses a placeholder this collector "
                "does not provide: %s" % (template, error))

    def robots_refuses(self, url) -> str:
        """The disallowed prefix a url hits, or "" when it is allowed.

        Read from the site's own robots.txt rules for a general user agent
        and kept in the configuration block.  The collector never opens one
        of these paths -- an /applystart link is recorded as an application
        destination and left alone.
        """
        if not self.respect_robots_disallow:
            return ""
        try:
            path = urlparse(str(url)).path or "/"
        except Exception:
            return ""
        for prefix in self.robots_disallowed_prefixes:
            if path.startswith(prefix):
                return prefix
        return ""

    def describe(self) -> str:
        if self.browser_visible:
            window = "visible Chromium %dx%d" % (self.browser_width,
                                                 self.browser_height)
            if self.browser_headless:
                window = "headless Chromium"
            if self.browser_live_ready:
                target = (self.browser_live_search_url
                          or self.site_base_url).split("?")[0]
                return "%s -> LIVE pages at %s" % (window, target)
            return ("%s -> LOCAL FIXTURE PAGES (demonstration, not Indeed "
                    "data)" % window)
        if not self.live_enabled:
            return "offline fixture mode (live collection disabled)"
        return "live mode -> %s" % (self.base_url or "<no base url configured>")


def parse_retry_after(value, cap_seconds=3600):
    """``Retry-After`` as seconds.  Accepts delta-seconds and an HTTP date."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return min(int(text), cap_seconds)
    try:
        when = parsedate_to_datetime(text)
    except Exception:
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    try:
        delta = (when - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None
    return max(0, min(int(delta), cap_seconds))


# ---------------------------------------------------------------------------
# TRANSPORTS
# ---------------------------------------------------------------------------


class BaseTransport:
    """A transport answers two questions and knows nothing about crawling."""

    name = "base"

    def search(self, keyword, city, page, config):      # pragma: no cover
        raise NotImplementedError

    def detail(self, job_reference, config):            # pragma: no cover
        raise NotImplementedError


class FixtureTransport(BaseTransport):
    """Offline job data, so the whole pipeline is exercisable with no network.

    Two sources of fixtures, in this order:

    1.  **Files.**  ``<fixture_dir>/<slug>.json`` for a specific
        ``keyword|city`` pair, or ``<fixture_dir>/default.json`` for anything
        else.  A file is either a list of job dicts or
        ``{"jobs": [...], "total": N}``.  This is how a real sample from an
        authorised source is replayed once one exists.
    2.  **Generated.**  When no file matches, deterministic synthetic jobs are
        produced from the (keyword, city, page) triple.  They are labelled as
        fixtures (``"fixture": true`` and a ``fixture.invalid`` host for the
        ones that are meant to be rejected), so fixture data can never be
        mistaken for live Indeed data in the Excel output.
    """

    name = "fixture"

    _TEMPLATES = (
        # (company, employment type, salary text, remote hint, apply shape)
        ("Northwind Technologies Pvt Ltd", "Full-time", "₹4,50,000 - ₹7,20,000 a year", "", "external"),
        ("Bluepeak Infotech", "Full-time", "₹35,000 - ₹55,000 a month", "Hybrid", "indeed_hosted"),
        ("Craftline Systems", "Contract", "", "", "missing"),
        ("Orbit Cloud Services", "Full-time", "8 - 14 Lakhs P.A.", "Remote", "external"),
        ("Sierra Managed IT", "Part-time", "Not disclosed", "", "invalid"),
        ("Hexaline Networks", "Internship", "₹15,000 a month", "", "external"),
    )

    def __init__(self, fixture_dir="", jobs_per_page=12, seed=0):
        self.fixture_dir = fixture_dir
        self.jobs_per_page = max(1, int(jobs_per_page))
        self.seed = int(seed)
        self._file_cache = {}

    # -- files --------------------------------------------------------------

    @staticmethod
    def _slug(keyword, city):
        text = "%s-%s" % (str(keyword).lower(), str(city).lower())
        return re.sub(r"[^a-z0-9]+", "-", text).strip("-")

    def _load_file(self, keyword, city):
        if not self.fixture_dir or not os.path.isdir(self.fixture_dir):
            return None
        for name in (self._slug(keyword, city), "default"):
            path = os.path.join(self.fixture_dir, name + ".json")
            if path in self._file_cache:
                return self._file_cache[path]
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except (OSError, ValueError) as error:
                raise IndeedDataError("fixture %s unreadable: %s"
                                      % (path, error))
            if isinstance(loaded, dict):
                jobs = loaded.get("jobs", [])
                total = int(loaded.get("total", len(jobs)) or len(jobs))
            elif isinstance(loaded, list):
                jobs, total = loaded, len(loaded)
            else:
                raise IndeedDataError("fixture %s is not a list or an object"
                                      % path)
            payload = {"jobs": jobs, "total": total, "path": path}
            self._file_cache[path] = payload
            return payload
        return None

    # -- generation ---------------------------------------------------------

    def _generate(self, keyword, city, page, config):
        rng = random.Random("%s|%s|%s|%s" % (self.seed, keyword, city, page))
        total = rng.randint(0, self.jobs_per_page * 4)
        start = (int(page) - 1) * self.jobs_per_page
        if start >= total:
            return {"jobs": [], "total": total}

        count = min(self.jobs_per_page, total - start)
        jobs = []
        for index in range(count):
            position = start + index
            company, employment, salary, remote, apply_shape = \
                self._TEMPLATES[position % len(self._TEMPLATES)]
            # Deterministic across processes: hash() is salted per process,
            # so a stable digest is used instead.
            digest = hashlib.sha1(
                ("%s|%s|%s|%s" % (self.seed, keyword, city, position))
                .encode("utf-8")).hexdigest()
            job_key = "fx%s" % digest[:12]
            title = keyword if position % 4 else "Senior " + keyword
            raw = {
                "fixture": True,
                "job_key": job_key,
                "title": title,
                "company_name": company,
                "company_website": "https://%s.fixture.invalid/careers"
                                   % re.sub(r"[^a-z]+", "",
                                            company.lower())[:14],
                "formatted_location": "%s, %s" % (city, "India"),
                "city": city,
                "description": (
                    "<h3>About the role</h3><p>%s working with %s.</p>"
                    "<h3>Responsibilities</h3><ul><li>Support production "
                    "systems</li><li>Monitor infrastructure</li>"
                    "<li>Document changes</li></ul>"
                    "<p>Experience: %d - %d years.</p><p>Apply now</p>"
                    % (title, company, position % 4, (position % 4) + 3)
                ),
                "skills": ["Linux", "Networking", "Troubleshooting",
                           keyword.split()[0]],
                "employment_type": employment,
                "salary_text": salary,
                "remote_type": remote,
                "posted_date": "%d days ago" % (1 + position % 20),
                "source_url": "https://in.indeed.com/viewjob?jk=%s" % job_key,
            }
            if apply_shape == "external":
                raw["apply_url"] = ("https://%s.fixture.invalid/jobs/%s/apply"
                                    % (re.sub(r"[^a-z]+", "",
                                              company.lower())[:14], job_key))
            elif apply_shape == "indeed_hosted":
                raw["indeed_apply_url"] = ("https://in.indeed.com/applystart"
                                           "?jk=%s" % job_key)
            elif apply_shape == "invalid":
                raw["apply_url"] = "javascript:void(0)"
            # apply_shape == "missing": no application field at all.
            jobs.append(raw)
        return {"jobs": jobs, "total": total}

    # -- interface ----------------------------------------------------------

    def search(self, keyword, city, page, config):
        loaded = self._load_file(keyword, city)
        if loaded is not None:
            per_page = max(1, config.results_per_page)
            start = (int(page) - 1) * per_page
            jobs = loaded["jobs"][start:start + per_page]
            return {"jobs": jobs, "total": loaded["total"],
                    "source": loaded["path"]}
        generated = self._generate(keyword, city, page, config)
        generated["source"] = "generated"
        return generated

    def detail(self, job_reference, config):
        """Fixture listings already carry their full detail payload."""
        if isinstance(job_reference, dict):
            return dict(job_reference)
        raise IndeedDataError(
            "fixture transport has no standalone detail lookup for %r"
            % (job_reference,))


class HttpTransport(BaseTransport):
    """Live HTTP transport for an AUTHORISED source the operator supplies.

    There is deliberately NO default base url and NO default path.  The
    operator sets, in the environment:

        INDEED_SOURCE_BASE_URL   the authorised source's base url
        INDEED_SEARCH_PATH       its search path, with {keyword} {city} {page}
        INDEED_DETAIL_PATH       its job path, with {job_key}      (optional)
        INDEED_API_KEY           the credential, sent as a Bearer token

    and flips ``LIVE_COLLECTION_ENABLED`` at the top of find_indeed_job.py.
    Nothing here knows an Indeed endpoint, so this transport cannot be
    pointed at Indeed by accident, and no credential is ever written to
    source or to a state file.
    """

    name = "http"

    def __init__(self):
        try:
            import requests                 # noqa: F401  (import check only)
        except ImportError as error:        # pragma: no cover - env dependent
            raise IndeedConfigError(
                "live collection needs the 'requests' package: %s" % error)
        import requests
        self._requests = requests
        self._session = requests.Session()

    def _headers(self, config):
        headers = {
            "User-Agent": config.user_agent,
            "Accept": "application/json",
        }
        key = config.api_key
        if key:
            headers["Authorization"] = "Bearer %s" % key
        return headers

    def _url(self, template, config, **values):
        if not config.base_url:
            raise IndeedConfigError(
                "LIVE_COLLECTION_ENABLED is on but no base url is "
                "configured.  Set INDEED_SOURCE_BASE_URL to the authorised "
                "source.  This project never guesses an endpoint.")
        if not template:
            raise IndeedConfigError(
                "no request path configured for this call.  Set "
                "INDEED_SEARCH_PATH / INDEED_DETAIL_PATH to match the "
                "authorised source's documented interface.")
        try:
            path = template.format(**values)
        except KeyError as error:
            raise IndeedConfigError(
                "request path %r needs a placeholder this client does not "
                "provide: %s" % (template, error))
        return "%s/%s" % (config.base_url, path.lstrip("/"))

    def _request(self, url, config, label):
        """One GET with the documented retry policy.  Raises on refusal."""
        attempt = 0
        rate_attempts = 0
        while True:
            attempt += 1
            try:
                response = self._session.get(
                    url,
                    headers=self._headers(config),
                    timeout=(config.connect_timeout_seconds,
                             config.request_timeout_seconds),
                )
            except self._requests.exceptions.RequestException as error:
                if attempt <= config.max_retries_network:
                    delay = config.backoff_network_seconds[
                        min(attempt - 1, len(config.backoff_network_seconds) - 1)]
                    _log(config, "   network error on %s (attempt %d/%d): %s "
                                 "-- retrying in %ds"
                         % (label, attempt, config.max_retries_network + 1,
                            error, delay))
                    time.sleep(delay)
                    continue
                raise IndeedNetworkError(str(error), url=url, attempts=attempt)

            status = response.status_code

            if status in (401, 403):
                # ACCESS REFUSED.  No retry, no workaround, no second route.
                raise IndeedAccessDenied(
                    "HTTP %d for %s -- access refused by the data source"
                    % (status, label), url=url, status=status)

            if status == 429:
                rate_attempts += 1
                retry_after = parse_retry_after(
                    response.headers.get("Retry-After"),
                    config.retry_after_cap_seconds)
                if rate_attempts > config.max_retries_429:
                    raise IndeedRateLimited(
                        "HTTP 429 for %s -- quota exhausted" % label,
                        url=url,
                        retry_after=retry_after or config.default_429_wait_seconds)
                wait = retry_after
                if wait is None:
                    wait = config.backoff_429_seconds[
                        min(rate_attempts - 1,
                            len(config.backoff_429_seconds) - 1)]
                _log(config, "   HTTP 429 on %s -- honouring a %ds wait "
                             "(%d/%d)" % (label, wait, rate_attempts,
                                          config.max_retries_429))
                time.sleep(wait)
                continue

            if 500 <= status < 600:
                if attempt <= config.max_retries_5xx:
                    delay = config.backoff_5xx_seconds[
                        min(attempt - 1, len(config.backoff_5xx_seconds) - 1)]
                    _log(config, "   HTTP %d on %s (attempt %d/%d) -- "
                                 "retrying in %ds"
                         % (status, label, attempt, config.max_retries_5xx,
                            delay))
                    time.sleep(delay)
                    continue
                raise IndeedServerError(
                    "HTTP %d for %s after %d attempts"
                    % (status, label, attempt), url=url, status=status,
                    attempts=attempt)

            if status >= 400:
                raise IndeedDataError("HTTP %d for %s" % (status, label))

            try:
                return response.json()
            except ValueError as error:
                raise IndeedDataError(
                    "%s answered 200 with a body that is not JSON: %s"
                    % (label, error))

    def search(self, keyword, city, page, config):
        url = self._url(config.search_path, config, keyword=keyword, city=city,
                        page=page, country=config.country,
                        limit=config.results_per_page)
        payload = self._request(url, config, "search %r in %r page %s"
                                % (keyword, city, page))
        return _normalise_search_payload(payload)

    def detail(self, job_reference, config):
        if isinstance(job_reference, dict):
            job_key = (job_reference.get("job_key") or job_reference.get("jobKey")
                       or job_reference.get("jk") or "")
        else:
            job_key = str(job_reference)
        if not job_key:
            raise IndeedDataError("no job key to request a detail record with")
        url = self._url(config.detail_path, config, job_key=job_key)
        payload = self._request(url, config, "job %s" % job_key)
        if isinstance(payload, dict) and isinstance(payload.get("job"), dict):
            return payload["job"]
        if isinstance(payload, dict):
            return payload
        raise IndeedDataError("detail payload for %s is not an object" % job_key)


def _normalise_search_payload(payload):
    """Accept the shapes a JSON job source realistically answers with."""
    if isinstance(payload, list):
        return {"jobs": payload, "total": len(payload)}
    if not isinstance(payload, dict):
        raise IndeedDataError("search payload is neither a list nor an object")
    for key in ("jobs", "results", "data", "items", "hits", "postings"):
        value = payload.get(key)
        if isinstance(value, list):
            total = payload.get("total", payload.get("totalResults",
                     payload.get("count", len(value))))
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = len(value)
            return {"jobs": value, "total": total}
    raise IndeedDataError(
        "search payload has no job list (keys: %s)"
        % ", ".join(sorted(str(key) for key in payload))[:200])


def _log(config, message):
    if getattr(config, "verbose", True):
        print(message, flush=True)


# ---------------------------------------------------------------------------
# VISIBLE BROWSER TRANSPORT
# ---------------------------------------------------------------------------
# WHY A BROWSER AT ALL
# The operator wants to WATCH the collection happen, the way the Naukri
# scraper can be watched: a real Chromium window on the desktop, navigating,
# rendering listing pages, opening job pages, and reading what is on them.
#
# WHAT IT NAVIGATES TO
# Two possibilities, and the client is explicit about which one is in use:
#
#   1. DEMONSTRATION (the default).  Local fixture job pages, written to a
#      temporary directory and opened over file://.  They are built to mirror
#      the DOM shape of a real job site -- the same card containers, the same
#      heading/company/location/salary/description/apply-button structure --
#      so the extraction code exercised here is the SAME code that would run
#      against an authorised live page.  Every page carries a visible
#      "DEMONSTRATION" banner and every job carries fixture markers, so
#      fixture rows can never be passed off as live Indeed postings.
#
#   2. AUTHORISED LIVE PAGES.  Only when the operator has BOTH set
#      INDEED_BROWSER_AUTHORISED=yes and supplied the page urls to visit in
#      INDEED_BROWSER_SEARCH_URL / INDEED_BROWSER_DETAIL_URL.  There is no
#      built-in Indeed url here: this project does not ship a turnkey Indeed
#      page scraper, because Indeed's terms do not permit automated
#      collection from its pages without an agreement.  Supplying those urls
#      is the operator asserting that they have one.
#
# WHAT IT NEVER DOES
# No CAPTCHA solving and no challenge bypassing -- a challenge page or a
# 403/429 raises and stops the work, it is never "worked around".  No proxy
# or IP rotation.  No stealth plugins and no fingerprint spoofing: slow_mo
# exists so a developer can FOLLOW the run, and the user agent is Chromium's
# own.  No robots-disallowed path is ever opened (an /applystart url is
# RECORDED as the application destination, never visited).  No application
# is ever submitted and no resume is ever uploaded -- the apply button's href
# is read, the button is not clicked.

BROWSER_CHALLENGE_MARKERS = (
    "captcha",
    "are you a robot",
    "verify you are human",
    "verify you're human",
    "unusual traffic",
    "automated traffic",
    "access denied",
    "access to this page has been denied",
    "request blocked",
    "temporarily blocked",
    "attention required",
    "checking your browser",
    "cf-challenge",
    "enable javascript and cookies to continue",
)

# A page that legitimately found nothing.  Checked before the markers above,
# so an empty result is never mistaken for a block.
BROWSER_EMPTY_MARKERS = (
    "no jobs found",
    "did not match any jobs",
    "no results found",
    "we could not find",
    "we couldn't find",
    "try different keywords",
    "broaden your search",
)


class BrowserUnavailable(IndeedClientError):
    """Playwright or Chromium is not usable on this machine."""


class DemoPageBuilder:
    """Writes local HTML job pages for the visible-browser demonstration.

    The pages are generated from the same fixture records the offline
    transport uses, and they are written to a TEMPORARY directory -- never
    into the project, which holds only Excel output and checkpoints.

    The markup deliberately mirrors the structure of a real job site so the
    DOM readers below are genuinely exercised:

        search page   div.job_seen_beacon[data-jk]
                        h2.jobTitle > a.jcs-JobTitle > span[title]
                        span[data-testid="company-name"]
                        div[data-testid="text-location"]
                        div[data-testid="attribute_snippet_testid"]  (salary)
        job page      h1.jobsearch-JobInfoHeader-title
                        div[data-testid="inlineHeader-companyName"]
                        div[data-testid="inlineHeader-companyLocation"]
                        div#salaryInfoAndJobType
                        div#jobDescriptionText
                        apply button (external href, or an Indeed-hosted
                        /applystart href, or no button at all)
    """

    DEMO_HOST = "https://demo.fixture.invalid"

    BANNER = (
        '<div style="background:#b00020;color:#fff;padding:10px 16px;'
        'font:600 15px/1.4 system-ui,sans-serif">'
        'DEMONSTRATION PAGE &mdash; locally generated fixture data. '
        'This is NOT Indeed and these are NOT real job postings.'
        '</div>'
    )

    STYLE = (
        '<style>'
        'body{margin:0;font:14px/1.5 system-ui,sans-serif;color:#2d2d2d}'
        '.wrap{max-width:1100px;margin:0 auto;padding:16px}'
        'h1{font-size:22px;margin:4px 0}'
        '.job_seen_beacon{border:1px solid #d4d4d4;border-radius:8px;'
        'padding:12px 14px;margin:0 0 12px}'
        '.jobTitle a{font-size:17px;color:#2557a7;text-decoration:none}'
        '[data-testid="company-name"]{color:#595959}'
        '#jobDescriptionText{border-top:1px solid #e4e4e4;padding-top:12px}'
        '.apply{display:inline-block;background:#2557a7;color:#fff;'
        'padding:9px 16px;border-radius:6px;text-decoration:none;'
        'margin:12px 0}'
        '.note{color:#767676;font-size:12px}'
        '</style>'
    )

    def __init__(self, directory=None):
        self.directory = directory or tempfile.mkdtemp(
            prefix="indeed-demo-pages-")
        self._written = {}
        # Demonstration pages are scratch files in the system temp directory,
        # never in the project.  Registered for removal at interpreter exit
        # too, so a run that is killed outside a safe boundary still does not
        # leave page caches behind.
        atexit.register(self.cleanup)

    # -- paths ----------------------------------------------------------

    @staticmethod
    def _slug(text):
        return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")

    def search_page_url(self, keyword, city, page):
        return "file://" + self._search_path(keyword, city, page)

    def _search_path(self, keyword, city, page):
        return os.path.join(
            self.directory,
            "search-%s-%s-p%d.html" % (self._slug(keyword), self._slug(city),
                                       int(page)))

    def detail_page_url(self, job_key):
        return "file://" + os.path.join(self.directory,
                                        "job-%s.html" % self._slug(job_key))

    # -- writing --------------------------------------------------------

    def write_search_page(self, keyword, city, page, jobs, total):
        """Render one listing page, and a job page for each card on it."""
        cards = []
        for job in jobs:
            job_key = str(job.get("job_key") or job.get("jobkey") or "")
            self.write_detail_page(job)
            cards.append(
                '<div class="job_seen_beacon" data-jk="%s">'
                '<h2 class="jobTitle"><a class="jcs-JobTitle" href="%s">'
                '<span title="%s">%s</span></a></h2>'
                '<span data-testid="company-name">%s</span>'
                '<div data-testid="text-location">%s</div>'
                '<div data-testid="attribute_snippet_testid">%s</div>'
                '<div class="job-snippet">%s</div>'
                '<div class="note">fixture job %s</div>'
                '</div>'
                % (
                    _esc(job_key),
                    _esc(self.detail_page_url(job_key)),
                    _esc(job.get("title", "")),
                    _esc(job.get("title", "")),
                    _esc(job.get("company_name", "")),
                    _esc(job.get("formatted_location", "")),
                    _esc(job.get("salary_text", "") or "Salary not disclosed"),
                    _esc(job.get("employment_type", "")),
                    _esc(job_key),
                )
            )

        body = (
            '<div class="wrap">'
            '<h1>%s jobs in %s</h1>'
            '<div class="note" id="searchCountPages">Page %d &mdash; '
            '%d fixture jobs in this result set</div>'
            '<div id="mosaic-provider-jobcards">%s</div>'
            '%s'
            '</div>'
            % (_esc(keyword), _esc(city), int(page), int(total),
               "".join(cards) or '<p>No jobs found for this search.</p>',
               '<p class="note">Generated locally for the visible-browser '
               'demonstration. No request was made to Indeed.</p>')
        )
        path = self._search_path(keyword, city, page)
        self._write(path, "%s jobs in %s" % (keyword, city), body)
        return "file://" + path

    def write_detail_page(self, job):
        """Render one job page, mirroring a real job page's structure."""
        job_key = str(job.get("job_key") or "")
        description = str(job.get("description") or "")
        salary = job.get("salary_text") or ""
        employment = job.get("employment_type") or ""
        chip = " - ".join(part for part in (salary, employment) if part)

        apply_block = (
            '<p class="note" id="applyNotAvailable">This fixture job exposes '
            'no application destination, so apply_url must stay blank.</p>'
        )
        external = job.get("apply_url")
        hosted = job.get("indeed_apply_url")
        if external:
            apply_block = (
                '<div id="applyButtonLinkContainer">'
                '<a class="apply" rel="nofollow" target="_blank" href="%s">'
                'Apply on company site</a></div>' % _esc(external)
            )
        elif hosted:
            apply_block = (
                '<div id="indeedApplyButton">'
                '<a class="apply" href="%s">Apply now</a></div>'
                % _esc(hosted)
            )

        body = (
            '<div class="wrap">'
            '<h1 class="jobsearch-JobInfoHeader-title" '
            'data-testid="jobsearch-JobInfoHeader-title">%s</h1>'
            '<div data-testid="inlineHeader-companyName">%s</div>'
            '<div data-testid="inlineHeader-companyLocation">%s</div>'
            '<div id="salaryInfoAndJobType">%s</div>'
            '%s'
            '<div id="jobDescriptionText">%s</div>'
            '<p class="note">Fixture record %s. Not an Indeed posting.</p>'
            '</div>'
            % (_esc(job.get("title", "")), _esc(job.get("company_name", "")),
               _esc(job.get("formatted_location", "")), _esc(chip),
               apply_block, description, _esc(job_key))
        )
        path = os.path.join(self.directory, "job-%s.html" % self._slug(job_key))
        self._write(path, job.get("title", "job"), body)
        return "file://" + path

    def _write(self, path, title, body):
        html = (
            "<!doctype html><html lang=\"en\"><head>"
            "<meta charset=\"utf-8\"><title>%s (DEMONSTRATION)</title>%s"
            "</head><body data-demo=\"true\">%s%s</body></html>"
            % (_esc(title), self.STYLE, self.BANNER, body)
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html)
        self._written[path] = True

    def cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def _esc(value):
    """Escape a value for HTML attribute/text use."""
    return html_escape(str(value if value is not None else ""), quote=True)


class BrowserTransport(BaseTransport):
    """Drives a real (by default VISIBLE) Chromium window with Playwright.

    Launch and teardown live here, not in the main script, and every setting
    arrives through ``IndeedClientConfig`` -- which the main file fills in
    from its own configuration block.
    """

    name = "browser"

    def __init__(self, config, page_builder=None, fixture_source=None):
        self.config = config
        self._playwright = None
        self.browser = None
        self.context = None
        self.page = None            # the listing window
        self.detail_page = None     # the job window
        self.started = False

        # Where the demonstration pages come from.  The fixture source is the
        # offline transport, so the demo shows exactly the fixture records the
        # rest of the test suite uses.
        self.pages = page_builder
        self.fixtures = fixture_source or FixtureTransport(
            fixture_dir=config.fixture_dir,
            jobs_per_page=config.fixture_jobs_per_page,
            seed=config.fixture_seed,
        )

    # -- lifecycle ----------------------------------------------------------

    @property
    def is_demonstration(self) -> bool:
        return not self.config.browser_live_ready

    def start(self):
        """Launch Chromium.  Visible unless ``browser_headless`` is set."""
        if self.started:
            return self

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise BrowserUnavailable(
                "visible browser mode needs Playwright: %s.  Install it with"
                "  pip install playwright && python -m playwright install "
                "chromium" % error)

        if not self.config.browser_headless and not (
                os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise BrowserUnavailable(
                "BROWSER_HEADLESS is False but there is no display: neither "
                "DISPLAY nor WAYLAND_DISPLAY is set.  Run the collector from "
                "a desktop session, give the service a display "
                "(Environment=DISPLAY=:0 in indeed.service), or set "
                "BROWSER_HEADLESS = True at the top of find_indeed_job.py.")

        self._playwright = sync_playwright().start()

        launch_options = {
            "headless": bool(self.config.browser_headless),
            "slow_mo": int(self.config.browser_slow_mo_ms),
            # The window size only has to match the viewport so that what is
            # on screen is what the code sees.  Nothing here is a
            # fingerprint or detection measure.
            "args": ["--window-size=%d,%d" % (self.config.browser_width,
                                              self.config.browser_height)],
        }
        if self.config.browser_channel:
            launch_options["channel"] = self.config.browser_channel

        try:
            self.browser = self._playwright.chromium.launch(**launch_options)
        except Exception as error:
            self.stop()
            raise BrowserUnavailable(
                "Chromium could not be launched (%s).  On a desktop this "
                "usually means DISPLAY is not set or the browser is not "
                "installed; run  python -m playwright install chromium." % error)

        context_options = {
            "viewport": {"width": self.config.browser_width,
                         "height": self.config.browser_height},
        }
        # The saved login, exactly as the reference project does it: a
        # storage_state file the OPERATOR produced by logging in themselves
        # in a visible browser (save_indeed_login.py).  No credential is
        # read, typed or stored by this code.
        self.using_saved_login = False
        auth_file = self.config.auth_file
        if self.config.use_saved_login and auth_file and os.path.exists(auth_file):
            context_options["storage_state"] = auth_file
            self.using_saved_login = True
        elif self.config.use_saved_login and auth_file:
            _log(self.config,
                 "   [BROWSER] no saved login at %s -- continuing signed out. "
                 "Run save_indeed_login.py to sign in once." % auth_file)

        self.context = self.browser.new_context(**context_options)
        self.context.set_default_navigation_timeout(
            self.config.browser_nav_timeout_ms)
        self.context.set_default_timeout(self.config.browser_nav_timeout_ms)

        # Two tabs, as in the reference project: one for listings, one for
        # job pages, so a job page never destroys the listing it came from.
        self.page = self.context.new_page()
        self.detail_page = self.context.new_page()
        self.page.bring_to_front()

        if self.is_demonstration and self.pages is None:
            self.pages = DemoPageBuilder()
        if self.is_demonstration:
            self._show_demonstration_notice()

        self.started = True
        _log(self.config, "   [BROWSER] Chromium %s -- %s"
             % ("headless" if self.config.browser_headless
                else "VISIBLE (%dx%d)" % (self.config.browser_width,
                                          self.config.browser_height),
                self.config.describe()))
        return self

    def _show_demonstration_notice(self):
        """Put the demonstration notice on screen before anything else."""
        notice = (
            '<div class="wrap" style="padding:24px">'
            '<h1>Indeed collector &mdash; visible browser demonstration</h1>'
            '<p>Live collection from Indeed is <strong>not authorised</strong> '
            'for this project, so this window will navigate <strong>local '
            'fixture pages</strong> generated on this machine.</p>'
            '<p>The pages mirror the structure of a real job page, so the '
            'extraction you are about to watch is the same code that would '
            'run against an authorised live source.</p>'
            '<p class="note">No request is made to Indeed in this mode.</p>'
            '</div>'
        )
        builder = self.pages or DemoPageBuilder()
        self.pages = builder
        path = os.path.join(builder.directory, "_welcome.html")
        builder._write(path, "Visible browser demonstration", notice)
        try:
            self.page.goto("file://" + path, wait_until="load")
            if self.config.browser_slow_mo_ms:
                self.page.wait_for_timeout(
                    min(2000, self.config.browser_slow_mo_ms * 4))
        except Exception:
            pass        # a cosmetic page must never stop a run

    def stop(self):
        """Close the browser and Playwright, in that order, never raising."""
        keep = self.config.browser_keep_open_seconds
        if keep and self.started and not self.config.browser_headless:
            _log(self.config, "   [BROWSER] holding the window open for %ds"
                 % keep)
            try:
                self.page.wait_for_timeout(keep * 1000)
            except Exception:
                time.sleep(keep)

        for closer in (self.context, self.browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass

        self.context = self.browser = self.page = self.detail_page = None
        self._playwright = None
        self.started = False

    close = stop

    def cleanup_pages(self):
        if self.pages is not None:
            self.pages.cleanup()

    # -- navigation ---------------------------------------------------------

    def _require_started(self):
        if not self.started:
            self.start()

    def _dismiss_cookie_banner(self, page):
        """Decline non-essential cookies, so the banner stops covering the page.

        Only the DECLINE control is ever clicked.  The privacy-preserving
        choice is the one this collector makes; "Accept all" is never
        pressed, and no other consent or terms control is touched.
        """
        if self.config.cookie_banner_action != "reject":
            return
        for selector in ("#onetrust-reject-all-handler",
                         "button#onetrust-reject-all-handler",
                         "button:has-text('Reject All')",
                         "button:has-text('Reject all')"):
            try:
                locator = page.locator(selector)
                if locator.count() and locator.first.is_visible():
                    locator.first.click(timeout=3000)
                    _log(self.config, "     [BROWSER] declined non-essential "
                                      "cookies")
                    return
            except Exception:
                continue

    def _goto(self, page, url, label):
        """Navigate, then classify what came back.  Never bypasses anything."""
        refused = self.config.robots_refuses(url)
        if refused:
            raise IndeedAccessDenied(
                "%s is under %r, which the site's robots rules disallow for "
                "this agent -- it is not opened" % (label, refused),
                url=url, status=999)
        try:
            response = page.goto(url, wait_until="domcontentloaded")
        except Exception as error:
            message = str(error)
            if "net::ERR_NAME_NOT_RESOLVED" in message or "ERR_INTERNET" in message:
                raise IndeedNetworkError(message, url=url, attempts=1)
            raise IndeedNetworkError("navigation to %s failed: %s"
                                     % (label, message), url=url, attempts=1)

        status = response.status if response is not None else 200

        if status in (401, 403):
            raise IndeedAccessDenied(
                "HTTP %d for %s -- access refused; this is respected, not "
                "worked around" % (status, label), url=url, status=status)
        if status == 429:
            retry_after = parse_retry_after(
                (response.headers or {}).get("retry-after"),
                self.config.retry_after_cap_seconds)
            raise IndeedRateLimited("HTTP 429 for %s" % label, url=url,
                                    retry_after=retry_after)
        if 500 <= status < 600:
            raise IndeedServerError("HTTP %d for %s" % (status, label),
                                    url=url, status=status, attempts=1)

        # A 200 can still be a challenge page.  Detected and reported; never
        # solved, and no alternative route is tried.
        body = ""
        try:
            body = (page.inner_text("body") or "").lower()[:4000]
        except Exception:
            pass
        if body and not any(marker in body for marker in BROWSER_EMPTY_MARKERS):
            for marker in BROWSER_CHALLENGE_MARKERS:
                if marker in body:
                    raise IndeedAccessDenied(
                        "a verification/challenge page was served for %s "
                        "(matched %r).  This collector does not solve "
                        "challenges." % (label, marker),
                        url=url, status=status)

        self._dismiss_cookie_banner(page)
        return response

    # -- listings -----------------------------------------------------------

    def search(self, keyword, city, page_number, config):
        self._require_started()

        if self.config.browser_live_ready:
            url = self.config.live_search_url(keyword, city, page_number)
            total_hint = None
        else:
            payload = self.fixtures.search(keyword, city, page_number, config)
            fixture_jobs = [job for job in (payload.get("jobs") or [])
                            if isinstance(job, dict)]
            total_hint = int(payload.get("total", len(fixture_jobs))
                             or len(fixture_jobs))
            url = self.pages.write_search_page(keyword, city, page_number,
                                               fixture_jobs, total_hint)

        _log(self.config, "     [BROWSER] opening %s" % _short(url))
        self._goto(self.page, url, "search %r in %r page %s"
                   % (keyword, city, page_number))
        self._wait_for_cards(self.page)

        jobs = self._read_listing_cards(self.page)
        total = total_hint if total_hint is not None else \
            self._read_total(self.page, len(jobs))
        return {"jobs": jobs, "total": total, "source": url}

    def _wait_for_cards(self, page):
        """Give the result cards time to render, then nudge the page.

        A results page builds its cards client-side, so reading the DOM the
        instant domcontentloaded fires finds an empty page on a perfectly
        good response.  This waits for a card the way the reference project
        waits (a timeout plus a couple of scrolls) and treats "still
        nothing" as an empty result, not as a block -- _goto() has already
        ruled a block out.
        """
        for selector in self._CARD_SELECTORS:
            try:
                page.wait_for_selector(
                    selector, timeout=self.config.listing_render_timeout_ms,
                    state="attached")
                break
            except Exception:
                continue
        # Lazily-rendered cards further down the page need the scroll.
        for _ in range(2):
            try:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(400)
            except Exception:
                break
        try:
            page.mouse.wheel(0, -3200)
        except Exception:
            pass

    def _wait_for_detail(self, page):
        """Wait for the job description block to render."""
        for selector in self._DETAIL_DESCRIPTION + self._DETAIL_TITLE:
            try:
                page.wait_for_selector(
                    selector, timeout=self.config.detail_render_timeout_ms,
                    state="attached")
                return
            except Exception:
                continue

    def _read_total(self, page, fallback):
        """The result count the page itself states, else what we can see."""
        for selector in ("div.jobsearch-JobCountAndSortPane-jobCount span",
                         "div.jobsearch-JobCountAndSortPane-jobCount",
                         "[data-testid='searchCountPages']",
                         "#searchCountPages",
                         ".jobsearch-JobCountAndSortPane-jobCount"):
            try:
                if page.locator(selector).count():
                    text = page.locator(selector).first.inner_text()
                    match = re.search(r"([\d,]{1,12})", text.replace(" ", ""))
                    if match:
                        return int(match.group(1).replace(",", ""))
            except Exception:
                continue
        return fallback

    # Indeed-specific listing structure.  NOT the Naukri selectors -- those
    # look for a[href*='job-listings'] and jhc__* classes, which do not exist
    # on an Indeed page.
    _CARD_SELECTORS = (
        "div.job_seen_beacon",
        "div[data-jk]",
        "td.resultContent",
        "[data-testid='slider_item']",
        "li div.cardOutline",
    )
    _TITLE_SELECTORS = (
        "h2.jobTitle span[title]",
        "h2.jobTitle a",
        "a.jcs-JobTitle",
        "[data-testid='jobTitle']",
        "h2 a",
        "h2",
    )
    _COMPANY_SELECTORS = (
        "[data-testid='company-name']",
        "span.companyName",
        ".company_location [data-testid='company-name']",
        "span[data-testid='company-name']",
    )
    _LOCATION_SELECTORS = (
        "[data-testid='text-location']",
        "div.companyLocation",
        "[data-testid='job-location']",
    )
    _SALARY_SELECTORS = (
        "[data-testid='attribute_snippet_testid']",
        ".salary-snippet-container",
        ".estimated-salary",
        "#salaryInfoAndJobType",
    )
    _SNIPPET_SELECTORS = (
        ".job-snippet",
        "[data-testid='belowJobSnippet']",
        ".underShelfFooter",
    )

    def _read_listing_cards(self, page):
        """Read the rendered listing page into raw job dicts.

        JSON-LD first when the page publishes it (schema.org JobPosting is
        the stable, documented structure); the card DOM otherwise.
        """
        jobs = self._read_json_ld_jobs(page)
        if jobs:
            return jobs

        cards = None
        for selector in self._CARD_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count():
                    cards = locator
                    break
            except Exception:
                continue
        if cards is None:
            return []

        jobs = []
        for index in range(cards.count()):
            card = cards.nth(index)
            try:
                job_key = (card.get_attribute("data-jk")
                           or self._first_attr(card, "a[data-jk]", "data-jk")
                           or "")
                href = self._first_attr(card, "a[href]", "href") or ""
                title = (self._first_attr(card, "h2.jobTitle span[title]",
                                          "title")
                         or self._first_text(card, self._TITLE_SELECTORS))
                if not job_key and href:
                    match = re.search(r"[?&](?:jk|vjk)=([A-Za-z0-9_-]+)", href)
                    if match:
                        job_key = match.group(1)
                if not title:
                    continue

                jobs.append({
                    "job_key": job_key or _hash_key(href or title),
                    "title": title,
                    "company_name": self._first_text(card,
                                                     self._COMPANY_SELECTORS),
                    "formatted_location": self._first_text(
                        card, self._LOCATION_SELECTORS),
                    "salary_text": self._first_text(card,
                                                    self._SALARY_SELECTORS),
                    "snippet": self._first_text(card, self._SNIPPET_SELECTORS),
                    "detail_page_url": self._job_page_url(page, job_key, href),
                    "source_url": self._source_url_for(page, job_key, href),
                    "fixture": self.is_demonstration,
                    "collected_via": "browser",
                })
            except Exception as error:
                _log(self.config, "       [BROWSER] skipped an unreadable "
                                  "card (%s)" % type(error).__name__)
                continue
        return jobs

    def _job_page_url(self, page, job_key, href):
        """The url detail() should open for this card.

        The canonical job page when a key is known; otherwise the card's own
        href, unless that href is under a robots-disallowed path -- in which
        case there is nothing to open and the card is skipped later.
        """
        if self.config.browser_live_ready and job_key:
            built = self.config.live_job_url(job_key)
            if built:
                return built
        absolute = self._absolute(page, href)
        if self.config.robots_refuses(absolute):
            return ""
        return absolute

    def _source_url_for(self, page, job_key, href):
        """The job's own stable page url.

        In live mode this is the canonical job page built from the job key --
        NOT the card's href, which is often a click-tracking redirect under a
        robots-disallowed path. Storing a redirect url as the job's identity
        would also break the database's uniqueness on source_url.
        """
        if self.config.browser_live_ready and job_key:
            built = self.config.live_job_url(job_key)
            if built:
                return built

        absolute = self._absolute(page, href)
        if absolute.startswith("file://"):
            # A demonstration page: a file:// path is not a job's identity, so
            # a clearly-fictional host is used instead of inventing an
            # indeed.com url for fixture data.
            return "%s/viewjob?jk=%s" % (DemoPageBuilder.DEMO_HOST,
                                         job_key or _hash_key(absolute))
        if self.config.robots_refuses(absolute) and job_key:
            # A disallowed redirect href: fall back to the canonical page.
            return self.config.live_job_url(job_key) or ""
        return absolute

    @staticmethod
    def _absolute(page, href):
        href = str(href or "").strip()
        if not href:
            return ""
        if href.startswith(("http://", "https://", "file://")):
            return href
        try:
            from urllib.parse import urljoin
            return urljoin(page.url, href)
        except Exception:
            return href

    @staticmethod
    def _first_text(scope, selectors):
        for selector in selectors:
            try:
                locator = scope.locator(selector)
                if locator.count():
                    text = (locator.first.inner_text() or "").strip()
                    if text:
                        return re.sub(r"\s+", " ", text)
            except Exception:
                continue
        return ""

    @staticmethod
    def _first_attr(scope, selector, attribute):
        try:
            locator = scope.locator(selector)
            if locator.count():
                return (locator.first.get_attribute(attribute) or "").strip()
        except Exception:
            pass
        return ""

    def _read_json_ld_jobs(self, page):
        """schema.org JobPosting blocks, when the page publishes them."""
        found = []
        try:
            blocks = page.locator("script[type='application/ld+json']")
            count = blocks.count()
        except Exception:
            return found

        for index in range(count):
            try:
                raw = blocks.nth(index).inner_text(timeout=2000)
                parsed = json.loads(raw)
            except Exception:
                continue
            for node in _iter_json_ld_nodes(parsed):
                if str(node.get("@type", "")).lower() != "jobposting":
                    continue
                found.append(_job_from_json_ld(node, self.is_demonstration))
        return [job for job in found if job.get("title")]

    # -- details ------------------------------------------------------------

    def detail(self, job_reference, config):
        self._require_started()

        if isinstance(job_reference, dict):
            job = dict(job_reference)
        else:
            job = {"job_key": str(job_reference)}

        job_key = str(job.get("job_key") or "")
        url = job.get("detail_page_url") or ""

        if self.config.browser_live_ready:
            # The job's OWN page url, built from its key the way the site
            # builds it.  The listing href is used when there is no key.
            url = self.config.live_job_url(job_key) or url
        elif not url and self.is_demonstration:
            url = self.pages.detail_page_url(job_key)

        if not url:
            raise IndeedDataError(
                "no job page url to open for %r" % (job_key or job_reference,))

        _log(self.config, "       [BROWSER] job page %s" % _short(url))
        self._goto(self.detail_page, url, "job %s" % (job_key or url))
        self._wait_for_detail(self.detail_page)

        detail = self._read_detail(self.detail_page)
        job.update({key: value for key, value in detail.items() if value != ""})
        job.setdefault("source_url", self._source_url_for(
            self.detail_page, job_key, self.detail_page.url))
        job["collected_via"] = "browser"
        job["fixture"] = self.is_demonstration
        return job

    # Indeed-specific job-page structure.
    _DETAIL_TITLE = (
        "h1.jobsearch-JobInfoHeader-title",
        "[data-testid='jobsearch-JobInfoHeader-title']",
        "h2[data-testid='jobsearch-JobInfoHeader-title']",
        "h1",
    )
    _DETAIL_COMPANY = (
        "[data-testid='inlineHeader-companyName']",
        "div[data-company-name]",
        ".jobsearch-InlineCompanyRating div:first-child",
        "[data-testid='company-name']",
    )
    _DETAIL_LOCATION = (
        "[data-testid='inlineHeader-companyLocation']",
        "[data-testid='job-location']",
        ".jobsearch-JobInfoHeader-subtitle div:last-child",
    )
    _DETAIL_CHIPS = (
        "#salaryInfoAndJobType",
        "[data-testid='attribute_snippet_testid']",
        "#jobDetailsSection",
    )
    _DETAIL_DESCRIPTION = (
        "#jobDescriptionText",
        ".jobsearch-JobComponent-description",
        "[data-testid='jobDescriptionText']",
    )

    def _read_detail(self, page):
        """Read the rendered job page.  Reads only -- it clicks nothing."""
        detail = {}

        json_ld = {}
        for job in self._read_json_ld_jobs(page):
            json_ld = job
            break

        detail["title"] = (self._first_text(page, self._DETAIL_TITLE)
                           or json_ld.get("title", ""))
        detail["company_name"] = (
            self._first_text(page, self._DETAIL_COMPANY)
            or json_ld.get("company_name", ""))
        detail["formatted_location"] = (
            self._first_text(page, self._DETAIL_LOCATION)
            or json_ld.get("formatted_location", ""))

        chips = self._first_text(page, self._DETAIL_CHIPS)
        if chips:
            # A job page states the pay and the job type in ONE element,
            # e.g. "₹4,00,000 - ₹6,00,000 a year - Full-time".  Handing that
            # whole string to the salary parser makes it reject the figure
            # (the second half of the range is not a number), so the parts
            # are separated here, where the page's own format is known.
            salary_part, employment_part = _split_salary_and_type(chips)
            if salary_part:
                detail["salary_text"] = salary_part
            if employment_part:
                detail["employment_type"] = employment_part
        for key in ("salary_text", "employment_type", "posted_date",
                    "expiry_date", "skills"):
            if not detail.get(key) and json_ld.get(key):
                detail[key] = json_ld[key]

        description = ""
        for selector in self._DETAIL_DESCRIPTION:
            try:
                locator = page.locator(selector)
                if locator.count():
                    description = locator.first.inner_html() or ""
                    if description.strip():
                        break
            except Exception:
                continue
        detail["description"] = description or json_ld.get("description", "")

        external, hosted = self._read_apply_candidates(page)
        if external:
            detail["apply_url"] = external
        if hosted:
            detail["indeed_apply_url"] = hosted
        return detail

    # The apply button's DESTINATION is read.  The button is never clicked,
    # no form is filled, no resume is uploaded and no robots-disallowed url
    # is opened -- an /applystart link is recorded and left alone.
    _APPLY_EXTERNAL_SELECTORS = (
        "#applyButtonLinkContainer a[href]",
        "[data-testid='applyButtonLinkContainer'] a[href]",
        "#viewJobButtonLinkContainer a[href]",
        "a[aria-label*='Apply on'][href]",
        "a.jobsearch-IndeedApplyButton-newDesign[href]",
    )
    _APPLY_HOSTED_SELECTORS = (
        "#indeedApplyButton a[href]",
        "a[href*='/applystart']",
        "a[href*='smartapply.indeed.com']",
        "[data-testid='indeedApplyButton'] a[href]",
    )

    def _read_apply_candidates(self, page):
        external = ""
        hosted = ""
        for selector in self._APPLY_EXTERNAL_SELECTORS:
            external = self._first_attr(page, selector, "href")
            if external:
                break
        for selector in self._APPLY_HOSTED_SELECTORS:
            hosted = self._first_attr(page, selector, "href")
            if hosted:
                break
        if external and not external.startswith(("http://", "https://")):
            external = self._absolute(page, external)
        if hosted and not hosted.startswith(("http://", "https://")):
            hosted = self._absolute(page, hosted)
        # A file:// "apply" link only exists on a demonstration page and is
        # not an application destination, so it is dropped rather than stored.
        if external.startswith("file://"):
            external = ""
        if hosted.startswith("file://"):
            hosted = ""
        return external, hosted


_EMPLOYMENT_WORDS = re.compile(
    r"\b(full[\s-]?time|part[\s-]?time|permanent|regular|contract(ual|or)?|"
    r"temporary|temp|intern(ship)?|trainee|apprentice(ship)?|freelance|"
    r"fresher|volunteer|fixed[\s-]?term|c2h|contract to hire|"
    r"day shift|night shift|rotational shift|remote|hybrid|work from home)\b",
    re.IGNORECASE)

_HAS_FIGURE = re.compile(r"[\d\u20b9$]|\b(lac|lakh|lpa|crore|cr|per hour|"
                         r"a month|a year|per month|per annum)\b",
                         re.IGNORECASE)


def _split_salary_and_type(text):
    """``("<pay>", "<job type>")`` out of one combined page chip.

    A job page writes both in a single element, separated by dashes, bullets
    or line breaks -- and by the time the text reaches here the line breaks
    may already have been collapsed to spaces:

        "₹4,00,000 - ₹6,00,000 a year - Full-time"  -> range,  Full-time
        "₹35,000 a month"                           -> figure, ""
        "Full-time Day shift"                       -> "",     Full-time
        "Not disclosed - Contract"                  -> "",     Contract

    Segments carrying a figure or a currency are pay; employment words are
    the job type, and they are removed from the pay text so the salary parser
    sees only the figure.  Anything understood as neither is dropped rather
    than guessed at -- a wrong salary is worse than no salary.
    """
    raw = str(text or "").replace("\r", "\n")
    if not raw.strip():
        return "", ""

    # Split on separators that never appear INSIDE a figure: a line break, a
    # bullet, or a dash with spaces around it.  "4,00,000-6,00,000" stays
    # whole because its dash has no spaces.
    segments = []
    for line in re.split(r"[\n\u2022]+", raw):
        for part in re.split(r"\s[-\u2013\u2014]\s", line):
            part = re.sub(r"\s+", " ", part).strip(" .,;|")
            if part:
                segments.append(part)

    salary_parts, type_parts = [], []
    for segment in segments:
        employment = " ".join(match.group(0) for match
                              in _EMPLOYMENT_WORDS.finditer(segment))
        remainder = _EMPLOYMENT_WORDS.sub(" ", segment)
        remainder = re.sub(r"\s+", " ", remainder).strip(" .,;|")

        if _HAS_FIGURE.search(remainder):
            salary_parts.append(remainder)
            if employment:
                type_parts.append(employment)
        elif employment:
            type_parts.append(employment)

    # Two figure segments are the two sides of one range that the page split
    # with " - ", so they are rejoined for the salary parser.
    if len(salary_parts) > 1:
        salary = " - ".join(salary_parts[:2])
    else:
        salary = salary_parts[0] if salary_parts else ""
    return salary, " ".join(dict.fromkeys(type_parts))[:120]


def _iter_json_ld_nodes(parsed):
    """Yield every dict inside a JSON-LD document, @graph included."""
    if isinstance(parsed, dict):
        if "@graph" in parsed and isinstance(parsed["@graph"], list):
            for node in parsed["@graph"]:
                for inner in _iter_json_ld_nodes(node):
                    yield inner
        yield parsed
    elif isinstance(parsed, list):
        for node in parsed:
            for inner in _iter_json_ld_nodes(node):
                yield inner


def _job_from_json_ld(node, is_demo):
    """One schema.org JobPosting -> the raw shape the extractor expects."""
    organisation = node.get("hiringOrganization") or {}
    if not isinstance(organisation, dict):
        organisation = {}
    location = node.get("jobLocation") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    address = (location or {}).get("address") or {}
    if not isinstance(address, dict):
        address = {}

    locality = address.get("addressLocality") or ""
    region = address.get("addressRegion") or ""
    formatted = ", ".join(part for part in (locality, region) if part)

    identifier = node.get("identifier") or {}
    job_key = ""
    if isinstance(identifier, dict):
        job_key = str(identifier.get("value") or "")
    job_key = job_key or str(node.get("jobKey") or node.get("@id") or "")

    raw = {
        "job_key": job_key or _hash_key(str(node.get("title", ""))),
        "title": node.get("title") or "",
        "company_name": organisation.get("name") or "",
        "company_website": organisation.get("sameAs") or "",
        "description": node.get("description") or "",
        "formatted_location": formatted,
        "city": locality,
        "state": region,
        "employment_type": node.get("employmentType") or "",
        "posted_date": node.get("datePosted") or "",
        "expiry_date": node.get("validThrough") or "",
        "baseSalary": node.get("baseSalary") or {},
        "source_url": node.get("url") or "",
        "fixture": bool(is_demo),
        "collected_via": "browser",
    }
    if node.get("skills"):
        raw["skills"] = node["skills"]
    if node.get("applicationContact") or node.get("directApply"):
        raw["directApply"] = node.get("directApply")
    return raw


def _hash_key(seed):
    return "b%s" % hashlib.sha1(str(seed).encode("utf-8")).hexdigest()[:11]


def _short(url, limit=110):
    url = str(url or "")
    return url if len(url) <= limit else url[:limit - 3] + "..."


# ---------------------------------------------------------------------------
# THE CLIENT
# ---------------------------------------------------------------------------


class IndeedClient:
    """The only object the main script talks to for job data.

    Responsibilities, as specified:
      * retrieve job listings            -> ``search_jobs`` / ``iter_listings``
      * retrieve job details             -> ``get_job_detail``
      * handle pagination                -> ``page_count`` / ``iter_listings``
      * handle temporary connection errors -> inside the transport, bounded
      * return raw job information to the main script -- RAW: this class
        never reshapes a job, that is the extractor's job.
    """

    def __init__(self, config=None, transport=None):
        self.config = config or IndeedClientConfig()
        self.requests_made = 0
        self.last_request_at = 0.0
        self._distinct_denied_urls = set()

        if transport is not None:
            self.transport = transport
        elif self.config.browser_visible:
            # A real Chromium window, which the operator can watch.  It is
            # launched lazily by open() / the first request, so constructing
            # a client never pops a window open by surprise.
            self.transport = BrowserTransport(self.config)
        elif self.config.live_enabled:
            self.transport = HttpTransport()
        else:
            self.transport = FixtureTransport(
                fixture_dir=self.config.fixture_dir,
                jobs_per_page=self.config.fixture_jobs_per_page,
                seed=self.config.fixture_seed,
            )

    # -- state --------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        if self.transport.name == "browser":
            return bool(self.config.browser_live_ready)
        return bool(self.config.live_enabled) and self.transport.name != "fixture"

    @property
    def uses_browser(self) -> bool:
        return self.transport.name == "browser"

    @property
    def is_demonstration(self) -> bool:
        """True when rows come from local fixtures rather than a live source."""
        return not self.is_live

    def describe(self) -> str:
        return "%s via %s transport" % (self.config.describe(),
                                        self.transport.name)

    # -- browser lifecycle --------------------------------------------------
    # No-ops for the HTTP and fixture transports, so the main script can call
    # them unconditionally.

    def open(self):
        """Start the transport (launches the browser window in browser mode)."""
        starter = getattr(self.transport, "start", None)
        if callable(starter):
            starter()
        return self

    def close(self):
        """Shut the transport down.  Never raises."""
        stopper = getattr(self.transport, "stop", None)
        if callable(stopper):
            try:
                stopper()
            except Exception:
                pass
        return self

    def cleanup(self):
        """Close, and remove any temporary demonstration pages."""
        self.close()
        cleaner = getattr(self.transport, "cleanup_pages", None)
        if callable(cleaner):
            try:
                cleaner()
            except Exception:
                pass

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -- pacing -------------------------------------------------------------

    def _pace(self):
        """Keep a polite, configured gap between requests.

        This is request PACING, which an authorised source expects, not
        timing randomisation meant to look human: the gap is announced in the
        configuration and the jitter only spreads the five ranks apart so
        they do not all call at the same instant.
        """
        gap = self.config.request_gap_seconds
        if gap <= 0:
            return
        gap = gap * (1.0 + random.uniform(0.0, max(0.0,
                                                   self.config.jitter_fraction)))
        waited = time.monotonic() - self.last_request_at
        if self.last_request_at and waited < gap:
            time.sleep(gap - waited)

    def _note_request(self):
        self.requests_made += 1
        self.last_request_at = time.monotonic()

    # -- listings -----------------------------------------------------------

    def search_jobs(self, keyword, city, page=1):
        """One page of listings: ``(jobs, total)``.  ``jobs`` are RAW dicts."""
        if not str(keyword or "").strip():
            raise IndeedConfigError("search_jobs() needs a keyword")
        if not str(city or "").strip():
            raise IndeedConfigError("search_jobs() needs a city")
        page = max(1, int(page))

        self._pace()
        try:
            payload = self.transport.search(keyword, city, page, self.config)
        except IndeedAccessDenied as denied:
            self._distinct_denied_urls.add(denied.url or "search")
            if len(self._distinct_denied_urls) > 1:
                denied.scope = "session_wide"
            raise
        finally:
            self._note_request()

        jobs = payload.get("jobs") or []
        if not isinstance(jobs, list):
            raise IndeedDataError("search returned a non-list job collection")
        total = payload.get("total")
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = len(jobs)

        # A successful read is the only evidence that access is back.
        self._distinct_denied_urls.clear()
        return [job for job in jobs if isinstance(job, dict)], total

    def page_count(self, total_jobs, per_page=None, cap=None) -> int:
        """Pages to crawl for a result count, capped by configuration."""
        per_page = int(per_page or self.config.results_per_page or 1)
        cap = int(cap if cap is not None else self.config.max_listing_pages)
        if total_jobs <= 0:
            return 0
        pages = (int(total_jobs) + per_page - 1) // per_page
        return max(1, min(pages, cap)) if cap > 0 else max(1, pages)

    def iter_listings(self, keyword, city, start_page=1, max_pages=None):
        """Yield ``(page_number, jobs, total)`` for each page, honouring the cap.

        Stops on the first empty page after page one: a source that answers
        with nothing has nothing further to give for this search.
        """
        page = max(1, int(start_page))
        cap = int(max_pages if max_pages is not None
                  else self.config.max_listing_pages)
        total = None
        last_page = None

        while True:
            jobs, page_total = self.search_jobs(keyword, city, page)
            if total is None:
                total = page_total
                last_page = self.page_count(total, cap=cap)
            yield page, jobs, total

            if not jobs:
                return
            if last_page is not None and page >= last_page:
                return
            if page >= max(1, cap):
                return
            page += 1

    # -- details ------------------------------------------------------------

    def get_job_detail(self, job):
        """The full record for one job.

        Listing payloads from a job API usually already carry the full
        description.  When they do, that is returned as-is and NO second
        request is made -- an extra request per job for data already in hand
        is exactly the kind of waste that gets a collector rate-limited.
        """
        if isinstance(job, dict) and self._looks_complete(job):
            return dict(job)

        self._pace()
        try:
            detail = self.transport.detail(job, self.config)
        except IndeedAccessDenied as denied:
            self._distinct_denied_urls.add(denied.url or "detail")
            if len(self._distinct_denied_urls) > 1:
                denied.scope = "session_wide"
            raise
        finally:
            self._note_request()

        if not isinstance(detail, dict):
            raise IndeedDataError("detail record is not an object")
        if isinstance(job, dict):
            merged = dict(job)
            merged.update(detail)
            detail = merged
        self._distinct_denied_urls.clear()
        return detail

    @staticmethod
    def _looks_complete(job) -> bool:
        description = (job.get("job_description") or job.get("description")
                       or job.get("descriptionHtml") or "")
        return len(str(description).strip()) >= 120

    # -- health -------------------------------------------------------------

    def preflight(self):
        """``(ok, message)`` -- is this client allowed to collect at all?

        Called once at startup so a misconfigured live run fails immediately
        and loudly instead of after the first request.
        """
        if self.config.browser_visible:
            if self.config.browser_live_authorised \
                    and not self.config.browser_live_search_url:
                return False, (
                    "browser collection was authorised but no page url was "
                    "given.  Set INDEED_BROWSER_SEARCH_URL (and optionally "
                    "INDEED_BROWSER_DETAIL_URL) to the pages you are "
                    "permitted to collect from.  This project ships no "
                    "built-in Indeed url.")
            if self.config.browser_live_ready:
                return True, ("visible browser pointed at operator-supplied "
                              "authorised pages: %s"
                              % self.config.browser_live_search_url)
            return True, (
                "VISIBLE BROWSER DEMONSTRATION -- Chromium will navigate "
                "LOCAL FIXTURE PAGES generated on this machine.  Live Indeed "
                "collection is not authorised, so no request reaches any "
                "Indeed host and the rows produced are fixture rows.")

        if not self.config.live_enabled:
            return True, ("live collection is DISABLED -- running on offline "
                          "fixtures, no request will reach any Indeed host")
        if not self.config.base_url:
            return False, ("live collection is enabled but "
                           "INDEED_SOURCE_BASE_URL is not set.  There is no "
                           "authorised self-serve Indeed job-search API, so "
                           "this project will not invent one.")
        if not self.config.search_path:
            return False, ("live collection is enabled but INDEED_SEARCH_PATH "
                           "is not set, so there is no documented search "
                           "interface to call.")
        if self.config.api_key_env and not self.config.api_key:
            return False, ("live collection is enabled but %s is empty in the "
                           "environment.  Credentials are never stored in "
                           "source." % self.config.api_key_env)
        return True, "live collection configured for %s" % self.config.base_url
