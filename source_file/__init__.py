"""Indeed-specific collection modules.

The three modules the brief requires live HERE and nowhere else:

    source_file/indeed_client.py      talks to the authorised data source
    source_file/indeed_extractor.py   builds the 32-column database row
    source_file/indeed_apply_url.py   resolves the real application url

There is no ``indeed_config.py``: every editable setting lives at the top of
``find_indeed_job.py`` and is passed in, so this package holds behaviour
only.  Nothing here imports from the Naukri project.
"""

from .indeed_apply_url import (
    ApplyUrlResult,
    apply_url_or_blank,
    classify_apply_url,
    extract_apply_url,
    validate_apply_url,
)
from .indeed_client import (
    IndeedAccessDenied,
    IndeedClient,
    IndeedClientConfig,
    IndeedClientError,
    IndeedConfigError,
    IndeedDataError,
    IndeedNetworkError,
    IndeedRateLimited,
    IndeedServerError,
    FixtureTransport,
    HttpTransport,
    parse_retry_after,
)
from .indeed_extractor import (
    DETAIL_COLUMNS,
    SOURCE_PLATFORM,
    extract_job_details,
    row_for_excel,
    validate_row,
)

__all__ = [
    "ApplyUrlResult", "apply_url_or_blank", "classify_apply_url",
    "extract_apply_url", "validate_apply_url",
    "IndeedAccessDenied", "IndeedClient", "IndeedClientConfig",
    "IndeedClientError", "IndeedConfigError", "IndeedDataError",
    "IndeedNetworkError", "IndeedRateLimited", "IndeedServerError",
    "FixtureTransport", "HttpTransport", "parse_retry_after",
    "DETAIL_COLUMNS", "SOURCE_PLATFORM", "extract_job_details",
    "row_for_excel", "validate_row",
]
