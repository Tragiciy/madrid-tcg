"""
shared/locator_common.py — helpers for official publisher event locators.

Locator scrapers (scrapers/locator_*.py) return events for many stores at
once, unlike per-store scrapers. This module owns what they share:

  - the search area (CITY) and time window;
  - a polite HTTP session (honest User-Agent, timeout, small retry);
  - StoreResolver: maps a locator's store/organizer name to our canonical
    store name (STORE_META), with manual overrides in
    data/locator_store_overrides.json;
  - make_event(): builds an event dict in the shared schema.

No network requests and no file reads on import.
"""

import json
import pathlib
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from shared.scraper_keywords import extract_best_of, extract_format_for_event
from shared.store_matching import load_existing_stores, match_existing_store, normalize_name

TZ = ZoneInfo("Europe/Madrid")

# Search area. One city for now; multi-city turns this into a list.
CITY = {"name": "Madrid", "lat": 40.4168, "lng": -3.7038, "radius_km": 50}
WINDOW_DAYS = 60

USER_AGENT = "Mozilla/5.0 (compatible; madrid-tcg-events/1.0; +https://madrid-tcg.pages.dev)"
OVERRIDES_FILE = pathlib.Path("data/locator_store_overrides.json")


def window() -> tuple[date, date]:
    today = datetime.now(TZ).date()
    return today, today + timedelta(days=WINDOW_DAYS)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def request_json(sess: requests.Session, method: str, url: str, *,
                 retries: int = 2, pause_s: float = 1.0, **kwargs):
    """HTTP call returning parsed JSON; retries transient failures, then raises."""
    kwargs.setdefault("timeout", 30)
    for attempt in range(retries + 1):
        try:
            resp = sess.request(method, url, **kwargs)
            resp.raise_for_status()
            time.sleep(pause_s)  # be gentle between pages
            return resp.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retriable = status is None or status >= 500 or status == 429
            if attempt == retries or not retriable:
                raise
            time.sleep(pause_s * (attempt + 2))


# ---------------------------------------------------------------------------
# Datetimes
# ---------------------------------------------------------------------------

_FRACTION_RE = re.compile(r"(\.\d+)")


def to_madrid_iso(value: str, assume_tz: Optional[str] = None) -> Optional[str]:
    """
    Parse a locator timestamp and return an ISO string in Europe/Madrid.

    Handles 'Z', 7-digit fractions (Wizards) and naive local times paired
    with an explicit IANA zone (Bandai). Returns None if unparsable or if a
    naive time comes without a zone.
    """
    if not value:
        return None
    text = _FRACTION_RE.sub("", value.strip()).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        if not assume_tz:
            return None
        dt = dt.replace(tzinfo=ZoneInfo(assume_tz))
    return dt.astimezone(TZ).isoformat()


# ---------------------------------------------------------------------------
# Store resolution
# ---------------------------------------------------------------------------

class StoreResolver:
    """
    Resolve a locator store to our canonical store name.

    Order: manual override by "<source>:<external_id>" or by normalized
    name → confident match against STORE_META (name or address) → the
    locator's own name, unchanged. An override value of null skips the
    store entirely (e.g. online-only organizers).
    """

    SKIP = object()

    def __init__(self, known_stores: Optional[list[dict]] = None,
                 overrides: Optional[dict] = None):
        self.known = known_stores if known_stores is not None else load_existing_stores()
        if overrides is None:
            overrides = (
                json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
                if OVERRIDES_FILE.exists() else {}
            )
        self.by_id = overrides.get("by_id", {})
        self.by_name = {normalize_name(k): v for k, v in overrides.get("by_name", {}).items()}

    def resolve(self, source: str, external_id, name: str, address: str = ""):
        """Canonical store name, or StoreResolver.SKIP."""
        key = f"{source}:{external_id}"
        if key in self.by_id:
            return self.by_id[key] if self.by_id[key] is not None else self.SKIP
        norm = normalize_name(name or "")
        if norm in self.by_name:
            return self.by_name[norm] if self.by_name[norm] is not None else self.SKIP
        match = match_existing_store({"name": name or "", "address": address or ""}, self.known)
        if match["status"] == "matched_existing_store" and match["confidence"] >= 0.8:
            return match["matched_existing_store"]
        return (name or "").strip() or self.SKIP


# ---------------------------------------------------------------------------
# Event construction
# ---------------------------------------------------------------------------

def make_event(*, title: str, store: str, game: str, start_iso: str,
               source_event_id: str, source_url: str,
               store_address: str = "", end_iso: Optional[str] = None,
               format_hint: str = "") -> dict:
    """An event dict in the shared schema (see docs/PROJECT_CONTEXT.md §8)."""
    fmt, fmt_official = extract_format_for_event(title=title, category=format_hint, game=game)
    return {
        "title": title.strip(),
        "store": store,
        "store_address": store_address or None,
        "game": game,
        "format": fmt,
        "format_official": fmt_official,
        "best_of": extract_best_of(title),
        "datetime_start": start_iso,
        "datetime_end": end_iso,
        "language": "es",
        "source_url": source_url,
        "source_event_id": source_event_id,
        "scraped_at": datetime.now(timezone.utc).astimezone(TZ).isoformat(),
    }
