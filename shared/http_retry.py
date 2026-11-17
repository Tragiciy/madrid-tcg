"""
shared/http_retry.py — GET with a few retries for flaky store sites.

Small shop sites sometimes answer one request with a bot-protection 403, a
5xx or a placeholder page, then work again a minute later. Without a retry,
one such answer fails the scraper for the day and opens a health issue.

Retries only what can be transient: network errors, 403, 429 and 5xx, and a
response `check` rejects (for example, a page without the expected markup).
After the last attempt the error is raised, so a real outage stays loud.
"""

import logging
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

RETRIABLE_STATUS = {403, 429}


def get_with_retry(url: str, *, retries: int = 2, wait_s: float = 5.0,
                   check: Optional[Callable[[requests.Response], None]] = None,
                   sleep: Callable[[float], None] = time.sleep,
                   **kwargs) -> requests.Response:
    """requests.get(url, **kwargs), retried on transient failures.

    `check(resp)` may raise ValueError to mark a 200 response as bad (it is
    retried like a 5xx). Waits wait_s, 2*wait_s, … between attempts.
    """
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, **kwargs)
            resp.raise_for_status()
            if check is not None:
                check(resp)
            return resp
        except (requests.RequestException, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retriable = (status is None or status >= 500 or status in RETRIABLE_STATUS)
            if attempt == retries or not retriable:
                raise
            wait = wait_s * (attempt + 1)
            logger.warning("GET %s attempt %d failed (%s); retrying in %.0fs", url, attempt + 1, exc, wait)
            sleep(wait)
    raise AssertionError("unreachable")
