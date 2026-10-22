"""Convert one raw Indeed job into the 32-column JobEasyIndia row.

RESPONSIBILITY
--------------
``source_file/indeed_client.py`` returns job records exactly as the authorised
data source gave them.  This module is the only place that decides what each
of the 32 database columns contains, in the exact order the Naukri scraper
already writes them, so an Indeed row and a Naukri row are
interchangeable to the importer.

THE RULES THAT MATTER
---------------------
1.  NOTHING IS INVENTED.  A field the source did not expose is left empty,
    which the validation engine stores as NULL.  Never ``0`` for a salary --
    zero is a real number that would sort as the lowest-paid job on the site;
    never the search city for a job whose own location is elsewhere.

2.  SALARY IS STORED IN ABSOLUTE RUPEES.  "12 Lakhs" is 1,200,000, not 12.
    This is the unit expansion the Naukri reference project documents at
    length (storing the bare number damaged 413 rows on another platform).
    A monthly figure is recorded as the monthly figure: converting periods is
    forbidden, the website infers the period from magnitude.

3.  city / state DESCRIBE THE JOB, NOT THE SEARCH.  The Naukri project
    measured 43.5% of its rows carrying the crawl city instead of the job's
    own city.  Resolution order here is: the job's own structured city, then
    the first locality parsed out of its location text, then -- only when the
    job exposed NO location at all -- the search city.

4.  ``source_platform`` is always ``"Indeed"`` and ``source_url`` is the
    job's own stable detail url, never a constant.  A constant source_url is
    what reduced 1,030 Naukri rows to a single row in the database, because
    of the unique constraint on that column.

5.  THE DESCRIPTION KEEPS ITS SHAPE.  Paragraphs, headings and bullet points
    survive; navigation, cookie banners and "apply now" furniture do not.
"""

import hashlib
import html as html_module
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from .indeed_apply_url import extract_apply_url

# ---------------------------------------------------------------------------
# THE SCHEMA
# ---------------------------------------------------------------------------
# EXACT ORDER, 32 COLUMNS.  find_indeed_job.py re-exports this as
# DETAIL_COLUMNS and writes Excel from it, so the order lives in one place.
DETAIL_COLUMNS = [
    "job_id", "internal_job_id", "job_title", "company_name", "company_website",
    "job_description", "skills_required", "experience_required",
    "experience_min_years", "experience_max_years",
    "salary_min", "salary_max", "salary_currency",
    "location", "city", "state", "country",
    "remote_type", "employment_type", "job_category",
    "posted_date", "expiry_date", "application_deadline",
    "apply_url", "updated_at", "fetched_at",
    "is_fake", "is_active", "source_url", "source_type", "source_platform", "score",
]

SOURCE_PLATFORM = "Indeed"
SOURCE_TYPE = "job_portal"
DEFAULT_COUNTRY = "India"
DEFAULT_SCORE = 30

# ---------------------------------------------------------------------------
# TEXT CLEANING
# ---------------------------------------------------------------------------

_BLOCK_CLOSE = re.compile(
    r"</\s*(p|div|section|article|br|tr|table|h[1-6]|ul|ol|blockquote)\s*>",
    re.IGNORECASE,
)
_LIST_ITEM_OPEN = re.compile(r"<\s*li[^>]*>", re.IGNORECASE)
_LIST_ITEM_CLOSE = re.compile(r"</\s*li\s*>", re.IGNORECASE)
_BR = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_HEADING_OPEN = re.compile(r"<\s*(h[1-6])[^>]*>", re.IGNORECASE)
_SCRIPT_STYLE = re.compile(
    r"<\s*(script|style|noscript|svg|iframe)[^>]*>.*?</\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ANY_TAG = re.compile(r"<[^>]+>")
_MANY_BLANKS = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")

# Page furniture that is never part of a job description.  Matched on a whole
# line, so a sentence that merely mentions "apply" is untouched.
_FURNITURE_LINES = re.compile(
    r"^(?:"
    r"apply now|apply on company site|easily apply|apply with indeed|"
    r"save this job|save job|report job|report this job|share this job|"
    r"view all jobs|similar jobs|jobs you might like|"
    r"sign in to apply|create an account|"
    r"we use cookies.*|accept all cookies|cookie preferences|"
    r"skip to main content|back to search results|"
    r"\d+ days? ago|just posted|today|hiring multiple candidates"
    r")[.!:\s]*$",
    re.IGNORECASE,
)

# Excel refuses these; openpyxl raises IllegalCharacterError on them.
_ILLEGAL_EXCEL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_illegal(text: str) -> str:
    """Remove the control characters Excel refuses to store."""
    return _ILLEGAL_EXCEL.sub("", str(text))


def clean_value(value, default=""):
    """A single-line scalar, or ``default`` when there is nothing to store."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = strip_illegal(str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    return text or default


def clean_text(value, default=""):
    """Like ``clean_value`` but for text that is allowed to be empty."""
    return clean_value(value, default)


def clean_description(raw) -> str:
    """Readable plain text that KEEPS the document's structure.

    HTML in, structured text out:
      * ``<li>`` becomes a real "- " bullet;
      * headings and block ends become paragraph breaks;
      * ``<script>``/``<style>`` and page furniture are removed;
      * entities are decoded once (``&amp;nbsp;`` arrives doubly encoded from
        some feeds, so the decode runs twice and then stops).
    """
    if raw is None:
        return ""
    text = str(raw)
    if not text.strip():
        return ""

    text = _SCRIPT_STYLE.sub("\n", text)
    text = _BR.sub("\n", text)
    text = _LIST_ITEM_OPEN.sub("\n- ", text)
    text = _LIST_ITEM_CLOSE.sub("\n", text)
    text = _HEADING_OPEN.sub("\n\n", text)
    text = _BLOCK_CLOSE.sub("\n\n", text)
    text = _ANY_TAG.sub("", text)

    text = html_module.unescape(text)
    if "&" in text:
        text = html_module.unescape(text)

    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = strip_illegal(text).replace("\r\n", "\n").replace("\r", "\n")

    kept = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if _FURNITURE_LINES.match(line):
            continue
        kept.append(line)

    text = "\n".join(kept)
    text = _TRAILING_SPACE.sub("\n", text)
    text = _MANY_BLANKS.sub("\n\n", text)
    # Consecutive bullet points belong to one list, so the blank line the
    # block-close rule inserted between them is removed again.
    text = re.sub(r"(?m)^(- .+)\n\n(?=- )", r"\1\n", text)
    text = re.sub(r"(?m)^(- .+)\n\n(?=- )", r"\1\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# IDENTIFIERS
# ---------------------------------------------------------------------------

_JK_PATTERN = re.compile(r"[?&](?:jk|vjk)=([A-Za-z0-9_-]+)", re.IGNORECASE)
_PATH_ID_PATTERN = re.compile(r"/(?:viewjob|job|jobs)/([A-Za-z0-9_-]{6,})")


def extract_internal_job_id(raw, source_url: str = "") -> str:
    """Indeed's own id for the job (the ``jk`` key), when it is knowable."""
    if isinstance(raw, dict):
        for field in ("job_key", "jobkey", "jobKey", "jk", "id", "job_id",
                      "internal_job_id"):
            value = clean_value(raw.get(field))
            if value:
                return str(value)

    url = clean_value(source_url) or (
        clean_value(raw.get("url")) if isinstance(raw, dict) else "")
    if url:
        match = _JK_PATTERN.search(url)
        if match:
            return match.group(1)
        match = _PATH_ID_PATTERN.search(url)
        if match:
            return match.group(1)
    return ""


def generate_job_id(job_title: str, source_platform: str, fetched_at: str,
                    internal_job_id: str = "") -> str:
    """A stable-per-job hash, not a random id.

    ``internal_job_id`` is included first, so re-fetching the same Indeed job
    produces the same ``job_id`` and the importer can recognise it.  When the
    source exposed no id, the timestamp keeps the hash unique instead.
    """
    seed = "|".join([
        str(internal_job_id or ""),
        str(job_title or ""),
        str(source_platform or ""),
        "" if internal_job_id else str(fetched_at or ""),
    ])
    return hashlib.sha256(seed.encode("utf-8", "ignore")).hexdigest()[:32]


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# SOURCE URL
# ---------------------------------------------------------------------------

INDEED_VIEWJOB_TEMPLATE = "https://in.indeed.com/viewjob?jk=%s"


def build_source_url(raw, fallback_domain: str = "in.indeed.com") -> str:
    """The job's OWN stable detail url.  Never a constant, never a search url."""
    if isinstance(raw, dict):
        for field in ("source_url", "job_url", "jobUrl", "url", "detail_url",
                      "viewJobUrl", "link"):
            value = clean_value(raw.get(field))
            if value and value.lower().startswith(("http://", "https://")):
                return value

    job_key = extract_internal_job_id(raw)
    if job_key:
        if fallback_domain and fallback_domain != "in.indeed.com":
            return "https://%s/viewjob?jk=%s" % (fallback_domain, job_key)
        return INDEED_VIEWJOB_TEMPLATE % job_key
    return ""


# ---------------------------------------------------------------------------
# EXPERIENCE
# ---------------------------------------------------------------------------

_EXP_RANGE = re.compile(
    r"(\d{1,2})(?:\s*\.\s*\d)?\s*(?:-|–|—|to)\s*(\d{1,2})(?:\s*\.\s*\d)?\s*\+?\s*"
    r"(?:years?|yrs?|year)",
    re.IGNORECASE,
)
_EXP_MIN_ONLY = re.compile(
    r"(?:minimum|min|at least|atleast|over)?\s*(\d{1,2})\s*\+?\s*"
    r"(?:years?|yrs?|year)",
    re.IGNORECASE,
)
_EXP_FRESHER = re.compile(
    r"\b(fresher|freshers|no experience|entry level|0\s*-\s*1\s*year)\b",
    re.IGNORECASE,
)


def parse_experience(*sources):
    """``(text, min_years, max_years)`` -- blanks when nothing is stated.

    The first source that yields a number wins, so a structured
    ``experience`` field beats a sentence buried in the description.
    """
    for source in sources:
        text = clean_value(source)
        if not text:
            continue

        match = _EXP_RANGE.search(text)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if low > high:
                low, high = high, low
            return text, low, high

        if _EXP_FRESHER.search(text):
            return text, 0, 1

        match = _EXP_MIN_ONLY.search(text)
        if match:
            low = int(match.group(1))
            return text, low, ""

    first_text = next((clean_value(s) for s in sources if clean_value(s)), "")
    return first_text, "", ""


# ---------------------------------------------------------------------------
# SALARY
# ---------------------------------------------------------------------------

_SALARY_MULTIPLIERS = {
    "lac": 100000, "lacs": 100000, "lakh": 100000, "lakhs": 100000,
    "l": 100000, "lpa": 100000,
    "cr": 10000000, "crore": 10000000, "crores": 10000000,
    "k": 1000,
}

_SALARY_NOT_A_NUMBER = re.compile(
    r"^(?:not\s*disclosed|undisclosed|unpaid|not\s*mentioned|not\s*available"
    r"|unknown|none|null|n\.?/?a\.?|nil|-{1,2}|as\s*per\s*(?:industry|company)"
    r".*|negotiable|best\s*in\s*industry.*|confidential|competitive.*"
    r"|salary\s*not\s*specified)$",
    re.IGNORECASE,
)

_CURRENCY_SIGNS = (
    ("₹", "INR"), ("rs.", "INR"), ("rs", "INR"), ("inr", "INR"),
    ("$", "USD"), ("usd", "USD"),
    ("£", "GBP"), ("gbp", "GBP"),
    ("€", "EUR"), ("eur", "EUR"),
    ("aed", "AED"), ("sgd", "SGD"),
)

_SALARY_PERIOD = re.compile(
    r"\b(per\s+hour|an?\s+hour|hourly|per\s+day|a\s+day|daily|per\s+week|"
    r"a\s+week|weekly|per\s+month|a\s+month|monthly|per\s+year|a\s+year|"
    r"yearly|per\s+annum|annually|p\.?\s*a\.?|p\.?\s*m\.?)\b",
    re.IGNORECASE,
)

_SALARY_SIDE = re.compile(
    r"^\s*([\d,]+(?:\.\d+)?)\s*(lacs?|lakhs?|crores?|cr|lpa|l|k)?\s*$",
    re.IGNORECASE,
)
_SALARY_RANGE_SPLIT = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)

# Below this, a bare number may inherit the other side's unit
# ("8 - 12 Lakhs"); at or above it the number is already in rupees
# ("50,000 - 2 Lacs").
_UNIT_INHERIT_CEILING = 1000
# Job.salary_min / salary_max are DecimalField(max_digits=12, decimal_places=2).
_MAX_STORABLE = 9999999999


def _salary_side(text):
    match = _SALARY_SIDE.match(text or "")
    if not match:
        return None, None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None, None
    unit = (match.group(2) or "").lower() or None
    return number, unit


def detect_currency(text: str, default="INR") -> str:
    lowered = str(text or "").lower()
    for token, code in _CURRENCY_SIGNS:
        if token in lowered:
            return code
    return default


def parse_salary(raw_text, currency_hint="", period_hint=""):
    """``(min, max, currency, period)`` in ABSOLUTE currency units.

    Both bounds stay blank unless the text is confidently understood.  A
    single figure fills BOTH bounds, which is what "₹50,000 a month" means.
    """
    text = clean_value(raw_text)
    currency = clean_value(currency_hint) or ""
    period = clean_value(period_hint) or ""

    if not text:
        return "", "", currency or "", period

    if _SALARY_NOT_A_NUMBER.match(text.strip()):
        return "", "", currency or detect_currency(text), period

    currency = currency or detect_currency(text)

    period_match = _SALARY_PERIOD.search(text)
    if period_match and not period:
        word = period_match.group(1).lower().replace(".", "").replace(" ", "")
        if "hour" in word:
            period = "HOURLY"
        elif "day" in word:
            period = "DAILY"
        elif "week" in word:
            period = "WEEKLY"
        elif "month" in word or word == "pm":
            period = "MONTHLY"
        else:
            period = "YEARLY"

    # Strip decoration, keep digits, separators and unit words.
    working = text
    working = _SALARY_PERIOD.sub(" ", working)
    working = re.sub(r"(?:₹|\brs\.?|\binr\b|\$|£|€|\busd\b|"
                     r"\bgbp\b|\beur\b|\baed\b|\bsgd\b)", " ", working,
                     flags=re.IGNORECASE)
    working = re.sub(r"\b(?:from|upto|up\s*to|starting|between|salary|ctc|"
                     r"estimated|a|an|per)\b", " ", working, flags=re.IGNORECASE)
    working = re.sub(r"\s+", " ", working).strip(" .,:;")

    parts = _SALARY_RANGE_SPLIT.split(working, maxsplit=1)
    if len(parts) == 2:
        low_value, low_unit = _salary_side(parts[0])
        high_value, high_unit = _salary_side(parts[1])
        if low_value is None or high_value is None:
            return "", "", currency, period

        # Unit inheritance: "8 - 12 Lakhs" means both sides are lakhs.
        if low_unit is None and high_unit and low_value < _UNIT_INHERIT_CEILING:
            low_unit = high_unit
        if high_unit is None and low_unit and high_value < _UNIT_INHERIT_CEILING:
            high_unit = low_unit

        low = low_value * _SALARY_MULTIPLIERS.get(low_unit or "", 1)
        high = high_value * _SALARY_MULTIPLIERS.get(high_unit or "", 1)
        if low > high:
            low, high = high, low
        if low <= 0 or high <= 0 or high > _MAX_STORABLE:
            return "", "", currency, period
        return int(round(low)), int(round(high)), currency, period

    value, unit = _salary_side(working)
    if value is None:
        return "", "", currency, period
    amount = value * _SALARY_MULTIPLIERS.get(unit or "", 1)
    if amount <= 0 or amount > _MAX_STORABLE:
        return "", "", currency, period
    amount = int(round(amount))
    return amount, amount, currency, period


def _structured_salary(raw):
    """Read a structured salary block before falling back to text parsing."""
    if not isinstance(raw, dict):
        return None

    for key in ("salary", "baseSalary", "base_salary", "compensation",
                "salary_range", "salaryRange"):
        block = raw.get(key)
        if isinstance(block, dict):
            value = block.get("value") if isinstance(block.get("value"), dict) \
                else block
            minimum = value.get("minValue", value.get("min",
                       value.get("salary_min", value.get("minimum"))))
            maximum = value.get("maxValue", value.get("max",
                       value.get("salary_max", value.get("maximum"))))
            currency = clean_value(block.get("currency")
                                   or value.get("currency")
                                   or block.get("salary_currency"))
            period = clean_value(value.get("unitText") or block.get("period")
                                 or block.get("unitText"))
            single = value.get("value", value.get("amount"))

            def _number(candidate):
                try:
                    number = float(str(candidate).replace(",", ""))
                except (TypeError, ValueError):
                    return None
                return number if number > 0 else None

            low, high = _number(minimum), _number(maximum)
            if low is None and high is None:
                low = high = _number(single)
            if low is None and high is not None:
                low = high
            if high is None and low is not None:
                high = low
            if low is None or high is None:
                # Structured block present but empty -- the Naukri project
                # measured exactly this on Naukri (minValue/maxValue never
                # populated), so fall through to the text.
                text = clean_value(value.get("value") or block.get("text"))
                if text:
                    return parse_salary(text, currency, period)
                return None
            if low > high:
                low, high = high, low
            if high > _MAX_STORABLE:
                return None
            return (int(round(low)), int(round(high)),
                    currency or "INR", period.upper() if period else "")

        if isinstance(block, str) and block.strip():
            return parse_salary(block, raw.get("salary_currency", ""),
                                raw.get("salary_period", ""))
    return None


# ---------------------------------------------------------------------------
# LOCATION
# ---------------------------------------------------------------------------

_AREA_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
_PIN_CODE = re.compile(r"\b\d{6}\b")

CITY_ALIASES = {
    "bangalore": "Bengaluru", "bangaluru": "Bengaluru",
    "bengalooru": "Bengaluru", "blr": "Bengaluru",
    "mysore": "Mysuru", "mangalore": "Mangaluru",
    "gurgaon": "Gurugram",
    "new delhi": "Delhi", "delhi / ncr": "Delhi", "delhi/ncr": "Delhi",
    "delhi ncr": "Delhi", "ncr": "Delhi",
    "bombay": "Mumbai", "madras": "Chennai", "calcutta": "Kolkata",
    "cochin": "Kochi", "ernakulam": "Kochi",
    "trivandrum": "Thiruvananthapuram",
    "vizag": "Visakhapatnam", "pondicherry": "Puducherry",
    "baroda": "Vadodara", "poona": "Pune",
    "trichy": "Tiruchirappalli", "tiruchi": "Tiruchirappalli",
    "gauhati": "Guwahati", "banaras": "Varanasi", "benares": "Varanasi",
    "allahabad": "Prayagraj", "hubli": "Hubballi",
}

REMOTE_WORDS = re.compile(r"\b(remote|work from home|wfh|anywhere)\b",
                          re.IGNORECASE)
HYBRID_WORDS = re.compile(r"\bhybrid\b", re.IGNORECASE)
ONSITE_WORDS = re.compile(r"\b(on[- ]?site|in[- ]?office|in person)\b",
                          re.IGNORECASE)


def normalise_city(name: str) -> str:
    """Trim an "(area)" suffix and a pin code, then apply alias correction."""
    text = clean_value(name)
    if not text:
        return ""
    text = _AREA_SUFFIX.sub("", text)
    text = _PIN_CODE.sub("", text).strip(" ,-")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return CITY_ALIASES.get(text.lower(), text)


def city_from_location_text(location_text: str) -> str:
    """The primary city out of "Pune, Maharashtra 411001" style text."""
    text = clean_value(location_text)
    if not text:
        return ""
    if REMOTE_WORDS.fullmatch(text.strip()) or text.strip().lower() == "remote":
        return ""
    first = text.split(",")[0]
    first = REMOTE_WORDS.sub("", first)
    first = re.sub(r"\b(in|at|near)\b", " ", first, flags=re.IGNORECASE)
    return normalise_city(first)


def resolve_location(raw, input_row=None, state_resolver=None):
    """``(location, city, state)`` describing the JOB, not the search.

    ``state_resolver`` is injected by the main script (``city_to_state`` from
    the project's own ``new_mylib``), so this module needs no knowledge of
    the geography tables and stays unit-testable on its own.
    """
    input_row = input_row or {}
    raw = raw if isinstance(raw, dict) else {}

    location = ""
    for field in ("formattedLocation", "formatted_location", "location",
                  "job_location", "jobLocation", "city_state", "place"):
        value = raw.get(field)
        if isinstance(value, dict):
            parts = [clean_value(value.get(key)) for key in
                     ("addressLocality", "locality", "city",
                      "addressRegion", "region", "state")]
            value = ", ".join(part for part in parts if part)
        elif isinstance(value, (list, tuple)):
            value = ", ".join(clean_value(item) for item in value
                              if clean_value(item))
        value = clean_value(value)
        if value:
            location = value
            break

    city = ""
    for field in ("city", "addressLocality", "locality", "town"):
        city = normalise_city(raw.get(field))
        if city:
            break
    if not city:
        city = city_from_location_text(location)

    state = ""
    for field in ("state", "addressRegion", "region", "province"):
        state = clean_value(raw.get(field))
        if state:
            break

    # The search city is the LAST resort and only when the job exposed no
    # location at all.  When a job says "Remote" or "Dubai", storing the
    # crawl city there is the very defect this rule exists to remove.
    if not location and not city:
        location = clean_value(input_row.get("location"))
        city = normalise_city(input_row.get("city") or input_row.get("location"))
        if not state:
            state = clean_value(input_row.get("state"))

    if city and not state and callable(state_resolver):
        try:
            resolved = state_resolver(city)
        except Exception:
            resolved = ""
        if resolved and str(resolved).lower() != "other":
            state = str(resolved)

    return location or city, city, state


def resolve_remote_type(*sources) -> str:
    """``"Remote"`` | ``"Hybrid"`` | ``"Onsite"``, decided by what is stated."""
    for source in sources:
        text = clean_value(source)
        if not text:
            continue
        if HYBRID_WORDS.search(text):
            return "Hybrid"
        if REMOTE_WORDS.search(text):
            return "Remote"
        if ONSITE_WORDS.search(text):
            return "Onsite"
    return "Onsite"


# ---------------------------------------------------------------------------
# EMPLOYMENT TYPE
# ---------------------------------------------------------------------------

_EMPLOYMENT_MAP = (
    (re.compile(r"\bfull[\s_-]?time\b|\bfulltime\b|\bpermanent\b|\bregular\b",
                re.IGNORECASE), "Full-time"),
    (re.compile(r"\bpart[\s_-]?time\b|\bparttime\b", re.IGNORECASE), "Part-time"),
    (re.compile(r"\bintern(ship)?\b|\btrainee\b|\bapprentice(ship)?\b",
                re.IGNORECASE), "Internship"),
    (re.compile(r"\bcontract(ual|or)?\b|\bfixed[\s_-]?term\b|\bc2h\b|"
                r"\bcontract to hire\b", re.IGNORECASE), "Contract"),
    (re.compile(r"\btemporar(y|ily)\b|\bseasonal\b", re.IGNORECASE), "Temporary"),
    (re.compile(r"\bfreelanc(e|er|ing)\b|\bconsultant\b|\bgig\b",
                re.IGNORECASE), "Freelance"),
    (re.compile(r"\bvolunteer\b", re.IGNORECASE), "Volunteer"),
)


def resolve_employment_type(*sources) -> str:
    """The stated employment type, defaulting to ``"Full-time"``.

    Full-time is the default the Naukri schema already uses, and it is also
    what Indeed itself shows when an employer omits the field.
    """
    for source in sources:
        if isinstance(source, (list, tuple)):
            source = " ".join(clean_value(item) for item in source)
        text = clean_value(source)
        if not text:
            continue
        for pattern, label in _EMPLOYMENT_MAP:
            if pattern.search(text):
                return label
    return "Full-time"


# ---------------------------------------------------------------------------
# SKILLS
# ---------------------------------------------------------------------------

def resolve_skills(raw, description: str = "") -> str:
    """``"A; B; C"`` -- the source's own skill list, never guessed from prose."""
    raw = raw if isinstance(raw, dict) else {}
    collected = []
    for field in ("skills", "skills_required", "skillsRequired", "key_skills",
                  "keySkills", "tags", "qualifications", "attributes"):
        value = raw.get(field)
        if isinstance(value, str):
            value = re.split(r"[;,|\n]", value)
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, dict):
                    item = item.get("name") or item.get("label") or item.get("skill")
                text = clean_value(item)
                if text and len(text) <= 80:
                    collected.append(text)
        if collected:
            break

    seen, unique = set(), []
    for skill in collected:
        key = skill.lower()
        if key not in seen:
            seen.add(key)
            unique.append(skill)
    return "; ".join(unique)


# ---------------------------------------------------------------------------
# DATES
# ---------------------------------------------------------------------------

_RELATIVE_DAYS = re.compile(r"(\d+)\+?\s*(day|days|hour|hours|week|weeks|"
                            r"month|months)\s*ago", re.IGNORECASE)
_JUST_POSTED = re.compile(r"\b(just posted|today|new)\b", re.IGNORECASE)


def parse_posted_date(*sources) -> str:
    """An ISO date for the posting, or "" when nothing usable is exposed.

    Indeed exposes either an ISO timestamp, an epoch in milliseconds, or the
    human "3 days ago".  All three are understood; anything else is left
    blank rather than guessed.
    """
    from datetime import timedelta

    for source in sources:
        if source is None:
            continue
        if isinstance(source, (int, float)) and source > 0:
            seconds = float(source)
            if seconds > 1e11:                           # milliseconds
                seconds /= 1000.0
            try:
                return datetime.fromtimestamp(
                    seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            except (OverflowError, OSError, ValueError):
                continue

        text = clean_value(source)
        if not text:
            continue

        if text.isdigit() and len(text) >= 10:
            return parse_posted_date(int(text))

        iso = text.replace("Z", "+00:00")
        for pattern in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                        "%d-%m-%Y", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y"):
            try:
                parsed = datetime.strptime(iso.split(".")[0], pattern)
            except ValueError:
                continue
            return parsed.strftime("%Y-%m-%d %H:%M:%S")

        match = _RELATIVE_DAYS.search(text)
        if match:
            amount, unit = int(match.group(1)), match.group(2).lower()
            factor = {"hour": 1 / 24.0, "hours": 1 / 24.0, "day": 1, "days": 1,
                      "week": 7, "weeks": 7, "month": 30, "months": 30}[unit]
            when = datetime.now(timezone.utc) - timedelta(days=amount * factor)
            return when.strftime("%Y-%m-%d %H:%M:%S")

        if _JUST_POSTED.search(text):
            return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    return ""


def parse_deadline(*sources) -> str:
    """The application deadline / expiry, when the source states one."""
    return parse_posted_date(*sources)


# ---------------------------------------------------------------------------
# COMPANY
# ---------------------------------------------------------------------------

_COMPANY_NOISE = re.compile(
    r"\b(hiring|urgent(ly)?|we are hiring|apply now|recruiting|"
    r"confidential|company name withheld)\b", re.IGNORECASE)


def resolve_company(raw, input_row=None) -> str:
    """The employer's name, or "" when the source does not expose one.

    A row with no trustworthy company name is dropped by the main script
    rather than saved with a placeholder -- the Naukri project proved
    placeholders reach the database and cannot be cleaned up afterwards.
    """
    raw = raw if isinstance(raw, dict) else {}
    for field in ("company_name", "companyName", "company", "employer",
                  "employer_name", "hiringOrganization"):
        value = raw.get(field)
        if isinstance(value, dict):
            value = value.get("name") or value.get("displayName")
        text = clean_value(value)
        if not text:
            continue
        text = _COMPANY_NOISE.sub("", text).strip(" -|,:")
        text = re.sub(r"\s+", " ", text).strip()
        if text and text.lower() not in ("na", "n/a", "none", "null", "-"):
            return text
    return clean_value((input_row or {}).get("company_name"))


def resolve_company_website(raw) -> str:
    """The employer's own site, when exposed.  An Indeed company profile is
    not a website, so it is not stored as one."""
    raw = raw if isinstance(raw, dict) else {}
    for field in ("company_website", "companyWebsite", "employer_website",
                  "website", "companyUrl", "company_url", "sameAs"):
        value = raw.get(field)
        if isinstance(value, dict):
            value = value.get("url") or value.get("sameAs")
        text = clean_value(value)
        if not text or not text.lower().startswith(("http://", "https://")):
            continue
        host = urlparse(text).netloc.lower()
        if not host or "indeed." in host:
            continue
        return text
    return ""


# ---------------------------------------------------------------------------
# THE ROW
# ---------------------------------------------------------------------------

def extract_job_details(raw, input_row=None, state_resolver=None,
                        fetched_at=None, country=DEFAULT_COUNTRY,
                        default_score=DEFAULT_SCORE) -> dict:
    """One raw Indeed job -> one 32-column row, in schema order.

    ``input_row`` carries what the CRAWL knows (keyword, role category, the
    city being searched).  It is used for the job category and, only as a
    last resort, for the location.
    """
    raw = raw if isinstance(raw, dict) else {}
    input_row = input_row or {}
    fetched_at = fetched_at or now_utc()

    source_url = build_source_url(raw)
    internal_job_id = extract_internal_job_id(raw, source_url)

    job_title = clean_value(raw.get("job_title") or raw.get("title")
                            or raw.get("jobTitle")
                            or input_row.get("job_title"))
    company_name = resolve_company(raw, input_row)

    description = clean_description(
        raw.get("job_description") or raw.get("description")
        or raw.get("descriptionHtml") or raw.get("snippet") or ""
    )

    experience_required, experience_min, experience_max = parse_experience(
        raw.get("experience_required"), raw.get("experience"),
        raw.get("experienceRange"), raw.get("requirements"), description,
    )

    salary_block = _structured_salary(raw)
    if salary_block is None:
        salary_min, salary_max, salary_currency, _period = parse_salary(
            raw.get("salary_text") or raw.get("salarySnippet")
            or raw.get("salary_snippet") or raw.get("estimated_salary") or "",
            raw.get("salary_currency") or raw.get("currency") or "",
            raw.get("salary_period") or "",
        )
    else:
        salary_min, salary_max, salary_currency, _period = salary_block

    if salary_min == "" and salary_max == "":
        # Keep a currency only when it was actually stated; an empty salary
        # with a currency tells the importer nothing.
        salary_currency = clean_value(salary_currency) or ""

    location, city, state = resolve_location(raw, input_row, state_resolver)

    remote_type = resolve_remote_type(
        raw.get("remote_type"), raw.get("remote"), raw.get("workplace_type"),
        raw.get("jobLocationType"), location, job_title,
    )
    employment_type = resolve_employment_type(
        raw.get("employment_type"), raw.get("employmentType"),
        raw.get("job_type"), raw.get("jobTypes"), raw.get("schedule_type"),
    )

    posted_date = parse_posted_date(
        raw.get("posted_date"), raw.get("datePosted"), raw.get("postedAt"),
        raw.get("created_at"), raw.get("date"), raw.get("posted"),
    )
    expiry_date = parse_deadline(
        raw.get("expiry_date"), raw.get("validThrough"), raw.get("expires_at"),
        raw.get("expiry"),
    )
    application_deadline = parse_deadline(
        raw.get("application_deadline"), raw.get("applicationDeadline"),
    ) or expiry_date

    apply_result = extract_apply_url(raw, source_url=source_url)

    row = {
        "job_id": generate_job_id(job_title, SOURCE_PLATFORM, fetched_at,
                                  internal_job_id),
        "internal_job_id": internal_job_id,
        "job_title": job_title,
        "company_name": company_name,
        "company_website": resolve_company_website(raw),
        "job_description": description,
        "skills_required": resolve_skills(raw, description),
        "experience_required": experience_required,
        "experience_min_years": experience_min,
        "experience_max_years": experience_max,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "location": location,
        "city": city,
        "state": state,
        "country": clean_value(raw.get("country"), country) or country,
        "remote_type": remote_type,
        "employment_type": employment_type,
        "job_category": clean_value(input_row.get("role_category")
                                    or input_row.get("job_category")),
        "posted_date": posted_date,
        "expiry_date": expiry_date,
        "application_deadline": application_deadline,
        "apply_url": apply_result.url,
        "updated_at": parse_posted_date(raw.get("updated_at"),
                                        raw.get("dateModified")) or posted_date,
        "fetched_at": fetched_at,
        "is_fake": False,
        "is_active": True,
        "source_url": source_url,
        "source_type": clean_value(input_row.get("source_type"), SOURCE_TYPE),
        "source_platform": SOURCE_PLATFORM,
        "score": default_score,
    }

    # Schema order is part of the contract with the importer, so the row is
    # rebuilt in DETAIL_COLUMNS order rather than relying on insertion order.
    ordered = {column: row.get(column, "") for column in DETAIL_COLUMNS}
    ordered["_apply_url_kind"] = apply_result.kind
    ordered["_apply_url_reason"] = apply_result.reason
    return ordered


def row_for_excel(row: dict) -> dict:
    """Drop the private diagnostic keys before the row reaches Excel."""
    return {column: row.get(column, "") for column in DETAIL_COLUMNS}


def validate_row(row: dict):
    """``(is_valid, reason)`` -- the minimum a row needs to be worth saving.

    A row that fails here is skipped and logged; it never stops the run.
    """
    if not isinstance(row, dict):
        return False, "row is not a dict"
    if not clean_value(row.get("job_title")):
        return False, "no job title"
    if not clean_value(row.get("company_name")):
        return False, "no trustworthy company name"
    if not clean_value(row.get("source_url")):
        return False, "no stable per-job source_url"
    missing = [column for column in DETAIL_COLUMNS if column not in row]
    if missing:
        return False, "missing columns: %s" % ", ".join(missing)
    return True, ""
