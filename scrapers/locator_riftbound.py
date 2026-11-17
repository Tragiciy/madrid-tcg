"""
scrapers/locator_riftbound.py — Riftbound events from the official event
locator on playriftbound.com (Riot's "Find an event" page), one search per
city (shared/cities.py).

API (checked 2026-11, against the page's own JS bundles):
  - POST https://playriftbound.com/api/gql, Riot's esports GraphQL gateway.
    No login. It only runs safelisted ("persisted") operations, sent by id:
    `extensions.persistedQuery.sha256Hash`. Freeform queries are refused.
    It also wants Apollo client headers (`apollographql-client-name` and
    `-version`; any value, we send our own name) and a JSON content type.
  - Operation `CompeteTournamentSearch`, `sport: "rb"`,
    `filter.rb = {coords {latitude, longitude}, distanceMeters, startDate}`.
    The filter has no end date: results come sorted by start date
    (`sortBy.rb: "DATE"`), so paging stops once past the window.
  - Cursor pagination: `first` (capped at 100 by the server, silently),
    `after` = `pageInfo.endCursor`, until `pageInfo.hasNextPage` is false.
  - `startsAt` is UTC (the page renders it in the viewer's zone: Nikochan's
    "NEXUS NIGHT DOMINGO 10:00" is 2026-11-29T09:00:00Z). `endsAt` is not
    in the search, and is just start + 24 h on the event page.
  - The store is the event's `organizer` (a UUID and a name). The venue is
    `tournamentLocation` (formatted address, coordinates), per event.
  - Attendance: `config.participantCapacity`, and `registrantCounts`
    per status. "REGISTERED" counts sign-ups before the event, and the
    players who played once it is completed (it matched the participant
    list on the events checked).
  - Cancelled: there is no cancelled status (tournament status is
    UNSTARTED / IN_PROGRESS / COMPLETED). Organizers delete a tournament
    instead, so it drops out of the search, and the aggregator removes it
    after MISSING_RUNS_BEFORE_DELETE runs like any vanished event.

The operation id is the sha256 of the query text in the site's persisted
query manifest. It only changes when Riot edits the query. If the gateway
answers PERSISTED_QUERY_NOT_IN_LIST, the run reads the current id from the
manifest (event page → its JS chunks → the manifest chunk) and retries once.
If that fails too, the source fails.

Event ids are "playriftbound:<tournament id>", the namespace the store
scrapers use for cards linking a playriftbound.com event (nikochan_arena),
so both copies share one event key, and the aggregator keeps the store's.
"""

import logging
import re
import time
from datetime import datetime, time as dtime, timezone
from typing import Optional

import requests

from shared.cities import City, default as default_city
from shared.locator_common import (
    TZ, StoreResolver, for_each_city, lookback_start, make_event, request_json, session,
    store_hint, to_madrid_iso, window,
)

logger = logging.getLogger(__name__)

STORE = "Riftbound Event Locator"
SOURCE = "playriftbound"
GAME = "Riftbound"
GRAPHQL_URL = "https://playriftbound.com/api/gql"
LOCATOR_PAGE = "https://playriftbound.com/es-ES/events"
EVENT_URL = "https://playriftbound.com/es-ES/events/{id}"
OPERATION = "CompeteTournamentSearch"
# sha256 of the CompeteTournamentSearch text in the persisted query manifest
# (2026-11-17). Refreshed from the manifest at run time if it goes stale.
SEARCH_HASH = "868030f676276bec926c740aada7129713d4097f6e2dd7c3a0451c4fb43852eb"
HEADERS = {
    "Content-Type": "application/json",
    "apollographql-client-name": "madrid-tcg-events",
    "apollographql-client-version": "1.0",
}
PAGE_SIZE = 100  # the server's cap
MAX_PAGES = 20
STALE_QUERY_CODES = {"PERSISTED_QUERY_NOT_IN_LIST", "PERSISTED_QUERY_NOT_FOUND"}

# tournamentType → official label (format_official). PRE_RIFT, the
# prerelease, becomes format "Prerelease" whatever pool the title names.
_TYPE_LABELS = {
    "NEXUS_NIGHT": "Nexus Night", "NEXUS_CUP": "Nexus Cup",
    "SUMMONER_SKIRMISH": "Summoner Skirmish", "CHALLENGE_SERIES": "Challenge Series",
    "SHOWDOWN_SERIES": "Showdown Series", "OPEN_PLAY": "Open Play",
    "LEARN_TO_PLAY": "Learn to Play", "REGIONAL": "Regional", "CONTINENTAL": "Continental",
    "WORLD_CHAMPIONSHIP": "World Championship", "CONVENTION_EVENT": "Convention Event",
    "SIDE_EVENT": "Side Event",
}
_POOL_HINT = {"LIMITED_SEALED": "sealed", "LIMITED_DRAFT": "draft"}
# Some organizers enter afternoon events as morning ones in the locator
# (Micelion's "Martes Locos" at 06:30, 18:30 on its own site; El Encuentro's
# Sunday Nexus Night at 05:30, 17:00 on its calendar). Events starting
# before this hour (Madrid) are skipped and logged, as in nikochan_arena.
EARLIEST_HOUR = 7

_CHUNK_RE = re.compile(r'src="([^"]*/_next/static/chunks/[^"]+\.js)"')
_MANIFEST_MODULE_RE = re.compile(r"generatePersistedQueryIdsFromManifest\)\(\{loadManifest:\(\)=>\w+\.A\((\d+)\)")
_LAZY_MODULE_RE = re.compile(r'(?<![\w$])(\d+),\w+=>\{\w+\.v\(\w+=>Promise\.all\(\["(static/chunks/[^"]+\.js)"')


class StaleQueryError(RuntimeError):
    """The gateway no longer lists our persisted operation id."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _graphql(sess, query_hash: str, variables: dict) -> dict:
    body = {"operationName": OPERATION, "variables": variables,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": query_hash}}}
    try:
        data = request_json(sess, "POST", GRAPHQL_URL, headers=HEADERS, json=body)
    except requests.HTTPError as exc:
        try:
            data = exc.response.json()
        except ValueError:
            raise exc from None
        if not data.get("errors"):
            raise
    errors = data.get("errors") or []
    if any((e.get("extensions") or {}).get("code") in STALE_QUERY_CODES for e in errors):
        raise StaleQueryError(errors[0].get("message"))
    if errors or not data.get("data"):
        raise RuntimeError(f"{STORE}: GraphQL error {errors[:1]}")
    return data["data"]


def find_manifest_id(texts, fetch) -> Optional[str]:
    """
    The persisted id of OPERATION, read the way the page's Apollo client
    gets it: one chunk calls generatePersistedQueryIdsFromManifest with a
    lazy module, whose chunk is the manifest. `texts` yields the page's
    chunk sources in order; `fetch(path)` returns a chunk by
    "static/chunks/…" path.
    """
    module, lazy = None, {}
    for text in texts:
        if module is None:
            m = _MANIFEST_MODULE_RE.search(text)
            module = m.group(1) if m else None
        lazy.update(_LAZY_MODULE_RE.findall(text))
        if module is not None and module in lazy:
            manifest = fetch(lazy[module])
            m = re.search(r'"id":"([0-9a-f]{64})","name":"' + OPERATION + '"', manifest)
            return m.group(1) if m else None
    return None


def discover_search_hash(sess) -> str:
    page = sess.get(LOCATOR_PAGE, timeout=30, headers={"Accept": "text/html"})
    page.raise_for_status()
    chunks = list(dict.fromkeys(_CHUNK_RE.findall(page.text)))
    if not chunks:
        raise RuntimeError(f"{STORE}: no JS chunks on {LOCATOR_PAGE}")
    base = chunks[0].split("static/chunks/")[0]

    def get(url: str) -> str:
        resp = sess.get(url, timeout=30, headers={"Accept": "*/*"})
        resp.raise_for_status()
        time.sleep(0.3)
        return resp.text

    found = find_manifest_id((get(c) for c in chunks), lambda path: get(base + path))
    if not found:
        raise RuntimeError(f"{STORE}: {OPERATION} id not found in the persisted query "
                           "manifest; the site's bundles changed")
    return found


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _search(sess, query_hash: str, city: City, since_utc: str, until: datetime) -> list[dict]:
    nodes: list[dict] = []
    after = None
    for _ in range(MAX_PAGES):
        variables = {
            "sport": "rb", "first": PAGE_SIZE, "sortBy": {"rb": "DATE"},
            "filter": {"rb": {"coords": {"latitude": city.lat, "longitude": city.lng},
                              "distanceMeters": city.radius_km * 1000,
                              "startDate": since_utc}},
        }
        if after:
            variables["after"] = after
        conn = _graphql(sess, query_hash, variables)["competeTournamentSearch"]
        page = [e["node"] for e in conn["edges"]]
        nodes += page
        if not conn["pageInfo"]["hasNextPage"]:
            return nodes
        last = to_madrid_iso((page[-1].get("tournament") or {}).get("startsAt") or "") if page else None
        if last and datetime.fromisoformat(last) > until:
            return nodes  # sorted by date: the rest is past the window
        after = conn["pageInfo"]["endCursor"]
    raise RuntimeError(f"{STORE} {city.id}: more than {MAX_PAGES} pages of "
                       f"{PAGE_SIZE} inside the window")


def _bounds() -> tuple[str, datetime]:
    """startDate for the search (lookback start, Madrid midnight, in UTC)
    and the end of the window (Madrid)."""
    _, end = window()
    since = datetime.combine(lookback_start(), dtime.min, tzinfo=TZ).astimezone(timezone.utc)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ"), datetime.combine(end, dtime.max, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _registered(tournament: dict) -> Optional[int]:
    for c in tournament.get("registrantCounts") or []:
        if c.get("status") == "REGISTERED":
            return c.get("count")
    return None


def parse_events(nodes: list[dict], resolver: StoreResolver, city: Optional[City] = None,
                 until: Optional[datetime] = None, now: Optional[datetime] = None) -> list[dict]:
    """Events of one city's search (default: the default city), up to
    `until` (default: the end of the window). Events that started before
    `now` are finished: their REGISTERED count is who played.

    An organizer sometimes lists one event twice (same name and start,
    two ids: Nikochan's 3 Jan 2027 Nexus Night). Only the first is kept;
    the store's own card, if it links the other id, still matches it by
    time in the aggregator's dedup."""
    city = city or default_city()
    until = until or _bounds()[1]
    now = now or datetime.now(TZ)
    out: list[dict] = []
    listed: set[tuple] = set()
    for node in nodes:
        t = node.get("tournament") or {}
        org = node.get("organizer") or {}
        start = to_madrid_iso(t.get("startsAt") or "")
        if not t.get("id") or not t.get("name") or not org.get("name") or not start:
            continue
        dt = datetime.fromisoformat(start)
        if dt > until:
            continue
        listing = (org.get("id"), start, t["name"].strip().casefold())
        if listing in listed:
            logger.info("%s: %s lists %r at %s twice; keeping one", STORE, org["name"], t["name"], start)
            continue
        listed.add(listing)
        loc = t.get("tournamentLocation") or {}
        address = loc.get("formattedAddress") or ""
        lat, lng = loc.get("latitude"), loc.get("longitude")
        store = resolver.resolve(SOURCE, org.get("id"), org["name"], address, city=city,
                                 lat=lat, lng=lng)
        if store is StoreResolver.SKIP:
            continue
        if dt.hour < EARLIEST_HOUR:
            logger.warning("%s: skipping %s %r at %s (implausible hour, likely AM/PM)",
                           STORE, org["name"], t["name"], start)
            continue
        config = t.get("config") or {}
        kind = config.get("tournamentType") or ""
        capacity, registered = config.get("participantCapacity"), _registered(t)
        finished = dt <= now
        event = make_event(
            title=t["name"],
            store=store,
            store_address=address,
            game=GAME,
            start_iso=start,
            source_event_id=f"{SOURCE}:{t['id']}",
            source_url=EVENT_URL.format(id=t["id"]),
            format_hint=_POOL_HINT.get(config.get("format") or "", ""),
            capacity=capacity,
            registered=None if finished else registered,
            participants=registered if finished else None,
            full=(not finished and bool(capacity) and (registered or 0) >= capacity),
            hint=store_hint(SOURCE, org.get("id"), org["name"], address, lat, lng, city=city),
        )
        if kind == "PRE_RIFT":
            event["format"], event["format_official"] = "Prerelease", None
        elif kind in _TYPE_LABELS and not event["format_official"]:
            event["format_official"] = _TYPE_LABELS[kind]
        out.append(event)
    return out


def scrape() -> list[dict]:
    sess = session()
    resolver = StoreResolver()
    since, until = _bounds()
    query = {"hash": SEARCH_HASH}

    def one_city(city: City) -> list[dict]:
        try:
            raw = _search(sess, query["hash"], city, since, until)
        except StaleQueryError as exc:
            logger.warning("%s: persisted query id went stale (%s); reading the manifest",
                           STORE, exc)
            query["hash"] = discover_search_hash(sess)
            raw = _search(sess, query["hash"], city, since, until)
        events = parse_events(raw, resolver, city, until)
        logger.info("%s %s: %d raw → %d events", STORE, city.id, len(raw), len(events))
        return events

    return for_each_city(STORE, one_city)


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
