"""
discoverers/wizards_locator.py — Wizards Store Locator discovery.

Status: PLACEHOLDER / RUNNABLE SKELETON
The live Wizards Store Locator endpoint is not publicly documented as a
simple JSON API. To avoid brittle scraping or browser automation at this
stage, this module returns an empty list with a clear TODO for future
implementation.

TODO: Replace placeholder with live fetch once a stable public endpoint is
identified (e.g., a documented locator API or simple POST/GET endpoint).
"""

from typing import Optional


def discover() -> list[dict]:
    """
    Discover potential Madrid TCG stores from the Wizards locator.

    Each returned dict must include:
      - name (str)
      - address (str)
      - source (str): "wizards_locator"
      - games (list[str]): e.g., ["MTG"]
      - website (str | None)
    """
    # Placeholder: return empty so the pipeline is runnable from day one.
    # When implementing live fetching:
    #   1. Call the Wizards locator endpoint with Madrid/ES filters.
    #   2. Parse the response (JSON or HTML).
    #   3. Map each result to the schema above.
    #   4. Wrap network calls in try/except and return [] on failure.
    return []


def _fetch_wizards_locator() -> Optional[list[dict]]:
    """
    Future helper for live fetching.

    Likely steps:
        import requests
        url = "https://locator.wizards.com/api/data/store/search"
        payload = {"city": "Madrid", "country": "ES", ...}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        ...
    """
    return None
