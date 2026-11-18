"""
shared/locator_common.py — helpers for official publisher event locators.

Locator scrapers (scrapers/locator_*.py) return events for many stores at
once, unlike per-store scrapers. This module owns what they share:

  - the search areas (shared/cities.py CITIES, one search per city) and the
    time window; for_each_city() runs a locator once per city;
  - a polite HTTP session (honest User-Agent, timeout, small retry);
  - StoreResolver: maps a locator's store/organizer name to our canonical
    store name (STORE_META), with manual overrides in
    the store registry (data/stores.json);
  - make_event(): builds an event dict in the shared schema.

No network requests and no file reads on import.
"""

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from shared.cities import CITIES, City, default as default_city
from shared.scraper_keywords import extract_best_of, extract_format_for_event
from shared.store_matching import match_existing_store, normalize_name
from shared.store_registry import StoreRegistry, same_place

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Madrid")

WINDOW_DAYS = 60
# Locators that report how many people actually played are also read this
# many days back, so finished events get their final player count.
LOOKBACK_DAYS = 7

USER_AGENT = "Mozilla/5.0 (compatible; madrid-tcg-events/1.0; +https://madrid-tcg.pages.dev)"


def lookback_start() -> date:
    return datetime.now(TZ).date() - timedelta(days=LOOKBACK_DAYS)


def positive_int(value) -> Optional[int]:
    """int(value) if it is a positive number, else None (0/None/'' = unknown)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def window() -> tuple[date, date]:
    today = datetime.now(TZ).date()
    return today, today + timedelta(days=WINDOW_DAYS)


# Cities whose search failed in this process: {city id: error}. One scraper
# runs per process (tools.run_scraper), which reports these with its result
# so the run's health stats show them (scraper_stats[...]["city_errors"]).
CITY_ERRORS: dict[str, str] = {}


def for_each_city(label: str, fetch, cities: tuple[City, ...] = CITIES) -> list[dict]:
    """
    Run one locator search per city, in order (sequential: one host, gently)
    and concatenate the events. `fetch(city)` returns that city's events.

    An event already returned for an earlier city is dropped (overlapping
    radii). Which city an event belongs to is decided later by its store's
    registry `city`, not by the search that found it; the search city only
    seeds the city of a store the registry does not know yet (store_hint).

    The default city's failure raises: the whole source fails, as any failed
    scraper. Another city's failure is logged as an ERROR and recorded in
    CITY_ERRORS; that city gets no events this run and the others publish.
    The pipeline's per-city suspect-zero guard (find_suspect_zero_cities)
    then keeps the city's existing events from this source, so a partial
    result never makes them look cancelled.
    """
    seen: set = set()
    out: list[dict] = []
    for city in cities:
        try:
            events = fetch(city)
        except Exception as exc:
            if city.id == default_city().id:
                raise
            CITY_ERRORS[city.id] = f"{type(exc).__name__}: {exc}"
            logger.error("%s: %s failed, no events for %s this run (its existing events are "
                         "kept by the per-city guard): %s", label, city.id, city.id,
                         CITY_ERRORS[city.id])
            continue
        fresh = [e for e in events if e["source_event_id"] not in seen]
        seen.update(e["source_event_id"] for e in fresh)
        logger.info("%s: %s → %d events (%d already seen in another city)",
                    label, city.id, len(fresh), len(events) - len(fresh))
        out += fresh
    return out


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


def venue_wall_clock(value: str, event_tz: Optional[str]) -> Optional[str]:
    """
    The same wall-clock time read in Madrid instead of `event_tz`.

    For a Spanish venue whose event carries another zone (an organizer
    account set to America/New_York): the organizer typed Madrid times, the
    source converted them as New York ones. "02:00Z" in New York is Friday
    21:00; this returns Friday 21:00 Madrid. Returns the plain Madrid time
    when the zone is missing or unknown.
    """
    if not value:
        return None
    text = _FRACTION_RE.sub("", value.strip()).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        zone = ZoneInfo(event_tz) if event_tz else None
    except (ValueError, KeyError):
        return to_madrid_iso(value)
    if dt.tzinfo is None or zone is None:
        return to_madrid_iso(value)
    return dt.astimezone(zone).replace(tzinfo=None).replace(tzinfo=TZ).isoformat()


# ---------------------------------------------------------------------------
# Store resolution
# ---------------------------------------------------------------------------

_POSTCODE_RE = re.compile(r"\b(\d{5})\b")


def _postcode(address: str) -> Optional[str]:
    m = _POSTCODE_RE.search(address or "")
    return m.group(1) if m else None


class StoreResolver:
    """
    Resolve a locator store to our canonical store name, using the store
    registry (data/stores.json) read-only.

    Order: registry alias by "<source>:<external_id>" → registry alias by
    normalized name, unless the postcodes differ and the pins (`lat`/`lng`)
    are more than SAME_PLACE_KM apart → confident match against registry
    stores (name or address) whose postcode does not contradict the
    locator's → the locator's own name (suffixed with its postcode if it
    would collide with a known store). Ignored names/ids resolve to SKIP.

    With `city` (the city whose search found the store), name and address
    matching only considers stores of that city: those whose registry city
    it is, plus any store located inside its radius (overlapping radii: the
    store keeps its registry city). An external id matches in any city. A
    name that already belongs to a store elsewhere gets a suffix, so a new
    "Dragon Den" in Barcelona never merges into a Madrid "Dragon Den".

    New stores and aliases are not written here (this runs inside scraper
    subprocesses); events carry a `store_hint` and the aggregator records
    them via StoreRegistry.learn().
    """

    SKIP = object()

    def __init__(self, registry: Optional[StoreRegistry] = None):
        self.registry = registry if registry is not None else StoreRegistry.load()
        self.known = self.registry.known_stores()
        self.address_of = {s["name"]: s["address"] for s in self.known}
        self._scopes: dict[str, set[str]] = {}

    def _scope(self, city: City) -> set[str]:
        """Ids of the registry stores a search for `city` may match by name."""
        if city.id not in self._scopes:
            self._scopes[city.id] = {
                s["id"] for s in self.registry.stores
                if self.registry.city_of(s) == city.id
                or (s.get("lat") is not None and s.get("lng") is not None
                    and city.contains(s["lat"], s["lng"]))
            }
        return self._scopes[city.id]

    def resolve(self, source: str, external_id, name: str, address: str = "",
                city: Optional[City] = None, lat=None, lng=None):
        """Canonical store name, or StoreResolver.SKIP."""
        external = f"{source}:{external_id}" if external_id is not None else None
        own = (name or "").strip()
        if self.registry.is_ignored(own, external):
            return self.SKIP
        if external and external in self.registry.by_external:
            return self.registry.by_external[external]["name"]
        scope = self._scope(city) if city is not None else None
        norm = normalize_name(own)
        for s in self.registry.named(norm):
            # Same name: one store unless the postcodes differ and the pins
            # are more than SAME_PLACE_KM apart.
            if (scope is None or s["id"] in scope) and same_place(s, address, lat, lng):
                return s["name"]
        if not own:
            return self.SKIP
        known = self.known if scope is None else [
            {"name": s["name"], "address": s.get("address") or ""}
            for s in self.registry.stores if s["id"] in scope]
        match = match_existing_store({"name": own, "address": address or ""}, known)
        if match["status"] == "matched_existing_store" and match["confidence"] >= 0.8:
            target = match["matched_existing_store"]
            if self._same_postcode(address, target):
                return target
            # Same brand, different branch ("Júpiter Juegos" in Alcobendas vs
            # our Calle de la Cruz store): keep it separate, and make sure it
            # cannot collide with the known store's name.
            logger.warning(
                "%s '%s' matches '%s' but postcode %s != %s — treated as another "
                "branch. If the registry address is outdated, fix data/stores.json.",
                external, own, target, _postcode(address),
                _postcode(self.address_of.get(target, "")),
            )
            if normalize_name(own) == normalize_name(target):
                return f"{own} ({_postcode(address)})"
        if scope is not None and any(s["id"] not in scope for s in self.registry.named(norm)):
            # The name already belongs to a store in another city.
            return f"{own} ({_postcode(address) or city.name})"
        return own

    def _same_postcode(self, address: str, store_name: str) -> bool:
        """False only when both addresses carry postcodes and they differ."""
        theirs = _postcode(address)
        ours = _postcode(self.address_of.get(store_name, ""))
        return not (theirs and ours and theirs != ours)


def store_hint(source: str, external_id, name: str, address: str = "",
               lat=None, lng=None, city: Optional[City] = None) -> dict:
    """What a locator told us about a store, for StoreRegistry.learn().
    `city` is the city whose search found it: it becomes the store's city
    if the registry does not know the store yet."""
    hint = {
        "external": f"{source}:{external_id}" if external_id is not None else None,
        "name": name, "address": address or None,
        "lat": float(lat) if lat not in (None, "") else None,
        "lng": float(lng) if lng not in (None, "") else None,
    }
    if city is not None:
        hint["city"] = city.id
    return hint


# ---------------------------------------------------------------------------
# Event construction
# ---------------------------------------------------------------------------

def make_event(*, title: str, store: str, game: str, start_iso: str,
               source_event_id: str, source_url: str,
               store_address: str = "", end_iso: Optional[str] = None,
               format_hint: str = "", hint: Optional[dict] = None,
               capacity: Optional[int] = None, registered: Optional[int] = None,
               participants: Optional[int] = None, full: bool = False) -> dict:
    """
    An event dict in the shared schema (see docs/PROJECT_CONTEXT.md §8).

    Attendance (all optional, only set when the source reports it):
    capacity = player cap; registered = sign-ups so far (upcoming events);
    participants = players who actually played (finished events);
    full = the source says no spots are left.
    """
    fmt, fmt_official = extract_format_for_event(title=title, category=format_hint, game=game)
    attendance = {k: v for k, v in (("capacity", positive_int(capacity)),
                                    ("registered", positive_int(registered)),
                                    ("participants", positive_int(participants)),
                                    ("full", True if full else None)) if v is not None}
    return {**attendance,
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
        # Consumed and removed by the aggregator (StoreRegistry.learn).
        "store_hint": hint,
    }
