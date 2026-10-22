"""Application-URL extraction and validation for the Indeed scraper.

WHY THIS MODULE EXISTS
----------------------
A job DETAIL url and an APPLICATION url are not the same thing:

    source_url : https://in.indeed.com/viewjob?jk=EXAMPLE123
    apply_url  : https://company.example/careers/job/123

The Naukri reference implementation read ``a[class*='apply']`` and fell back
to the detail url itself::

    apply_url = await async_dom_attr(page, "a[class*='apply']", "href") or url

That fallback is the defect this module exists to avoid.  Copying the detail
url into ``apply_url`` makes every row look as though it has an application
destination, so a job seeker who clicks "Apply" is sent back to a listing
page.  Here, a job with no exposed application destination stores a BLANK
value, which the database reads as NULL, and the website can then decide for
itself what to show.

WHAT IT DOES NOT DO
-------------------
It never submits an application, never uploads a resume, never follows a
redirect chain on the live site to "discover" a hidden destination, and never
invents a destination from the company name.  It reads what the authorised
data source exposes, validates it, and classifies it.

THE FOUR CASES, AS SPECIFIED
----------------------------
A  external employer application  -> that url is stored, kind "external"
B  Indeed-hosted application      -> that url is stored, kind "indeed_hosted"
C  no application destination      -> "" is stored, kind "missing"
D  invalid / unsupported url       -> "" is stored, kind "invalid"
"""

from urllib.parse import urljoin, urlparse, urlunparse

# ---------------------------------------------------------------------------
# WHAT THE DATA SOURCE MAY CALL THE FIELD
# ---------------------------------------------------------------------------
# Different authorised Indeed integrations (and different fixture vintages)
# name the application destination differently.  The order here is the
# PRIORITY order: an explicit external employer destination is preferred over
# an Indeed-hosted one, because that is where the employer actually wants the
# candidate to land.
EXTERNAL_APPLY_FIELDS = (
    "apply_url",
    "applyUrl",
    "external_apply_url",
    "externalApplyUrl",
    "employer_apply_url",
    "employerApplyUrl",
    "application_url",
    "applicationUrl",
    "directApplyUrl",
    "url_apply",
)

INDEED_APPLY_FIELDS = (
    "indeed_apply_url",
    "indeedApplyUrl",
    "indeed_hosted_apply_url",
    "indeedHostedApplyUrl",
    "apply_link",
    "applyLink",
)

# Nested containers an integration may wrap the destination in.
NESTED_APPLY_KEYS = ("apply", "application", "applyMethod", "apply_method")

# Schemes we accept.  Everything else is rejected, including the two the
# brief calls out by name.
ALLOWED_SCHEMES = ("http", "https")
REJECTED_SCHEMES = (
    "javascript", "data", "vbscript", "file", "about",
    "mailto", "tel", "ftp", "blob",
)

# Hosts that mean "this is an Indeed-hosted application".
INDEED_HOST_SUFFIXES = (
    "indeed.com",
    "indeed.co.in",
    "indeed.jobs",
    "indeedassessments.com",
    "smartapply.indeed.com",
)

# Paths on an Indeed host that are a real application entry point.  A bare
# ``/viewjob`` is the job DESCRIPTION, not an application, so it is not here.
INDEED_APPLY_PATH_MARKERS = (
    "/applystart",
    "/apply",
    "/m/applystart",
    "/beta/indeedapply",
    "/viewjob/apply",
)

# Buttons and links that sit next to "Apply" on a job page and must never be
# captured as an application destination.
UNRELATED_URL_MARKERS = (
    "/cmp/",              # company profile
    "/salaries",
    "/salary",
    "/career/",
    "/companies",
    "/jobs?",             # another search
    "/q-",                # another search
    "/signin",
    "/login",
    "/account",
    "/legal",
    "/privacy",
    "/terms",
    "/about",
    "/help",
    "/contact",
    "/alerts",
    "/savejob",
    "/saved",
    "/share",
    "/report",
    "facebook.com",
    "twitter.com",
    "x.com/intent",
    "linkedin.com/share",
    "whatsapp.com",
    "mailto",
)

MAX_URL_LENGTH = 2000


class ApplyUrlResult:
    """The outcome of one application-url extraction.

    Deliberately a tiny value object rather than a bare string: the caller
    needs to know WHY a url is blank (none offered vs. one offered and
    rejected) in order to log it honestly, and the Excel row needs only the
    ``url`` field.
    """

    __slots__ = ("url", "kind", "reason", "candidate")

    def __init__(self, url="", kind="missing", reason="", candidate=""):
        self.url = url
        self.kind = kind
        self.reason = reason
        self.candidate = candidate

    @property
    def is_present(self) -> bool:
        return bool(self.url)

    def __repr__(self):                                  # pragma: no cover
        return "ApplyUrlResult(url=%r, kind=%r, reason=%r)" % (
            self.url, self.kind, self.reason,
        )

    def __eq__(self, other):
        if isinstance(other, ApplyUrlResult):
            return (self.url, self.kind) == (other.url, other.kind)
        return NotImplemented


# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------

def normalise_candidate(value) -> str:
    """Trim a raw field value down to something worth parsing."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = normalise_candidate(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in EXTERNAL_APPLY_FIELDS + INDEED_APPLY_FIELDS + ("url", "href"):
            if key in value:
                text = normalise_candidate(value[key])
                if text:
                    return text
        return ""

    text = str(value).strip()
    # HTML attributes routinely arrive quoted or with stray whitespace and
    # control characters; a url with a newline in it is not a url.
    text = text.strip("'\"")
    text = "".join(ch for ch in text if ord(ch) >= 32 and ch not in "\x7f")
    text = text.strip()
    if text.lower() in ("", "none", "null", "n/a", "na", "-", "#", "undefined"):
        return ""
    return text


def resolve_relative(candidate: str, base_url: str = "") -> str:
    """Resolve a permitted relative url against the job's own page url.

    Only ``/path`` and ``path`` forms are resolved, and only when a base url
    is known.  A protocol-relative ``//host/path`` is resolved to https.
    """
    candidate = normalise_candidate(candidate)
    if not candidate:
        return ""

    if candidate.startswith("//"):
        return "https:" + candidate

    parsed = urlparse(candidate)
    if parsed.scheme:
        return candidate

    base_url = normalise_candidate(base_url)
    if not base_url:
        return ""
    base = urlparse(base_url)
    if base.scheme not in ALLOWED_SCHEMES or not base.netloc:
        return ""
    return urljoin(base_url, candidate)


def _tidy(url: str) -> str:
    """Drop a fragment and a trailing '?' so rows compare cleanly."""
    parsed = urlparse(url)
    query = parsed.query
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc,
        parsed.path,
        parsed.params,
        query,
        "",                      # fragment removed: never part of a destination
    ))


def validate_apply_url(candidate: str, base_url: str = ""):
    """``(url, reason)`` -- url is "" when the candidate must be rejected."""
    candidate = normalise_candidate(candidate)
    if not candidate:
        return "", "no url offered"

    lowered = candidate.lower()
    scheme_part = lowered.split(":", 1)[0].strip() if ":" in lowered else ""
    if scheme_part in REJECTED_SCHEMES:
        return "", "rejected scheme %r" % scheme_part

    resolved = resolve_relative(candidate, base_url)
    if not resolved:
        return "", "relative url with no usable base url"

    parsed = urlparse(resolved)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return "", "unsupported scheme %r" % (parsed.scheme or "none")
    if not parsed.netloc:
        return "", "malformed url: no host"
    host = parsed.netloc.lower()
    if host.startswith(".") or host.endswith(".") or ".." in host:
        return "", "malformed host %r" % host
    if " " in resolved:
        return "", "malformed url: contains whitespace"
    if len(resolved) > MAX_URL_LENGTH:
        return "", "url longer than %d characters" % MAX_URL_LENGTH
    # A host with no dot and no port is not a public destination
    # ("http://apply" is not somewhere a candidate can go).
    bare_host = host.split(":", 1)[0]
    if "." not in bare_host and bare_host not in ("localhost",):
        return "", "malformed host %r" % host

    return _tidy(resolved), ""


def is_indeed_host(url: str) -> bool:
    host = urlparse(normalise_candidate(url)).netloc.lower().split(":", 1)[0]
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in INDEED_HOST_SUFFIXES)


def is_unrelated_destination(url: str) -> bool:
    """True for the links that sit NEXT TO the apply button, not on it."""
    lowered = normalise_candidate(url).lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in UNRELATED_URL_MARKERS)


def classify_apply_url(url: str) -> str:
    """``"external"`` or ``"indeed_hosted"`` for a url already validated."""
    if not url:
        return "missing"
    if is_indeed_host(url):
        lowered = url.lower()
        if any(marker in lowered for marker in INDEED_APPLY_PATH_MARKERS):
            return "indeed_hosted"
        # An Indeed url that is not an application entry point is the job
        # description again -- exactly the "do not copy source_url" case.
        return "not_an_application"
    return "external"


# ---------------------------------------------------------------------------
# EXTRACTION
# ---------------------------------------------------------------------------

def _candidates(raw: dict):
    """Yield ``(field_name, raw_value)`` in priority order."""
    if not isinstance(raw, dict):
        return

    for field in EXTERNAL_APPLY_FIELDS:
        if field in raw:
            yield field, raw[field]

    for container_key in NESTED_APPLY_KEYS:
        container = raw.get(container_key)
        if isinstance(container, dict):
            for field in EXTERNAL_APPLY_FIELDS + INDEED_APPLY_FIELDS + ("url", "href"):
                if field in container:
                    yield "%s.%s" % (container_key, field), container[field]
        elif isinstance(container, str):
            yield container_key, container

    for field in INDEED_APPLY_FIELDS:
        if field in raw:
            yield field, raw[field]


def extract_apply_url(raw, source_url: str = "", base_url: str = "") -> ApplyUrlResult:
    """The job's real application destination, or a blank result.

    ``raw``        one job as the authorised data source returned it.
    ``source_url`` the job's own detail url.  Used ONLY to reject a candidate
                   that is merely the detail url again, and as the base for a
                   permitted relative url.  It is NEVER stored as the apply
                   url -- that is the defect this module exists to prevent.
    """
    base = normalise_candidate(base_url) or normalise_candidate(source_url)
    source_tidy = ""
    if normalise_candidate(source_url):
        source_valid, _ = validate_apply_url(source_url)
        source_tidy = source_valid

    first_rejection = ""
    first_candidate = ""

    for field, value in _candidates(raw):
        candidate = normalise_candidate(value)
        if not candidate:
            continue
        if not first_candidate:
            first_candidate = candidate

        url, reason = validate_apply_url(candidate, base)
        if not url:
            first_rejection = first_rejection or "%s: %s" % (field, reason)
            continue

        if source_tidy and url == source_tidy:
            # CASE C dressed up as CASE A: the source handed back the detail
            # url.  Storing it would be the forbidden copy.
            first_rejection = first_rejection or (
                "%s: candidate is the job detail url, not an application "
                "destination" % field
            )
            continue

        if is_unrelated_destination(url):
            first_rejection = first_rejection or (
                "%s: unrelated link (company profile, search, social or "
                "account page)" % field
            )
            continue

        kind = classify_apply_url(url)
        if kind == "not_an_application":
            first_rejection = first_rejection or (
                "%s: Indeed url that is not an application entry point" % field
            )
            continue

        return ApplyUrlResult(url=url, kind=kind, reason="from %s" % field,
                              candidate=candidate)

    if first_candidate:
        return ApplyUrlResult(url="", kind="invalid",
                              reason=first_rejection or "no usable candidate",
                              candidate=first_candidate)

    return ApplyUrlResult(url="", kind="missing",
                          reason="the data source exposed no application url")


def apply_url_or_blank(raw, source_url: str = "") -> str:
    """Just the url, for callers that do not care why it is blank."""
    return extract_apply_url(raw, source_url=source_url).url
