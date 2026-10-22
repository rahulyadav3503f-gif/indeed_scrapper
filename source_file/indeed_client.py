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

import hashlib
import json
import os
import random
import re
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

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

    @property
    def api_key(self):
        """The credential, read from the environment.  NEVER from source."""
        if not self.api_key_env:
            return ""
        return os.environ.get(self.api_key_env, "")

    def describe(self) -> str:
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
        return bool(self.config.live_enabled) and self.transport.name != "fixture"

    def describe(self) -> str:
        return "%s via %s transport" % (self.config.describe(),
                                        self.transport.name)

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
