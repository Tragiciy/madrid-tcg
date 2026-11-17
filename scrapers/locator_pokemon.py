"""
scrapers/locator_pokemon.py — Pokémon TCG events from the Play! Pokémon
Event Locator (events.pokemon.com/EventLocator), an OutSystems app.

The page loads its event list with one POST to the screen action
`DataActionGetEventList`. We make the same calls the page makes:

  1. GET  moduleservices/moduleinfo   → `versionToken` (moduleVersion) and
     the versioned URLs of every script;
  2. GET  scripts/OutSystems.js       → the public anonymous CSRF token
     (`AnonymousCSRFToken="..."`), sent as `X-CSRFToken`;
  3. GET  scripts/EventLocator.MainFlow.Home.mvc.js → the action's
     apiVersion (the string after the action's URL);
  4. POST DataActionGetEventList      → the events.

moduleVersion and apiVersion change on every deploy of their app, so they
are discovered on each run, never hard-coded. If the server reports either
as changed, the run fails rather than trusting a stale response.

Quirks (checked 2026-11, against the page's own JS and store sites):
  - Start_date always ends in "Z", but only play sessions (weekly
    "League" nights) are really UTC. Tournaments carry the venue's
    wall-clock time with a bogus "Z". The page does the same split
    (FormatDateByLocale: convert to Address.Timezone for play_session,
    strip the "Z" otherwise).
  - Play sessions have no name; the title is derived from EventTypeName.
  - No capacity, sign-up or attendance counts, and no per-event public page.
  - The site sits behind Imperva. If it serves its interstitial or a 403,
    the run raises (BlockedError) instead of returning nothing. On
    2026-11-17 plain requests worked for ~8 calls, then the IP got the
    "Pardon Our Interruption" page for 15+ minutes; headless Chromium got
    an hCaptcha. We do not work around either.

Request budget. CI probes on 2026-11-17 (fresh runner IPs) got 7 calls
through and the 8th blocked, also after a 90 s pause: the limit counts
calls, not rate. So a run spends at most CALL_BUDGET calls: 3 for discovery
and one POST per search area. Cities far apart cannot each get a POST, so
AREA_GROUPS searches some of them together: one POST over a circle that
encloses every member city's circle, whose venues are then split between
the cities by distance (radii never overlap, shared/cities.py). A city in
no group is searched alone, with the exact request a one-city search
always made. The server refuses a range past about 200 km (MAX_RANGE_KM).

Failure is contained per area, most important first. Areas run in CITIES
order: Madrid alone, then Barcelona+Valencia, then the newer cities, so
when calls run short the newest cities lose out. Discovery or Madrid's
search failing fails the whole source, as before. Any other area failing
(capped, HTTP error, block, too wide, no budget left) logs an ERROR naming
the area and gives its cities no events this run; the others publish. The
pipeline then protects those cities' existing Pokémon events and opens a
health issue naming the city (find_suspect_zero_cities, "<source>@<city>").
A city with no Pokémon history yet has nothing to protect: the ERROR is
its signal. Nothing is ever published from a truncated response.
"""

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import requests

from shared.cities import CITIES, City, default as default_city, distance_km
from shared.locator_common import (
    StoreResolver, for_each_city, make_event, session, store_hint, to_madrid_iso, window,
)

logger = logging.getLogger(__name__)

STORE = "Play! Pokémon Event Locator"
SOURCE = "pokemon"
GAME = "Pokémon"
HOST = "https://events.pokemon.com"
APP = "/EventLocator/"
LOCATOR_PAGE = HOST + APP
MODULEINFO_URL = HOST + APP + "moduleservices/moduleinfo"
RUNTIME_SCRIPT = APP + "scripts/OutSystems.js"
HOME_SCRIPT = APP + "scripts/EventLocator.MainFlow.Home.mvc.js"
ACTION_PATH = "screenservices/EventLocator/MainFlow/Home/DataActionGetEventList"
ACTION_URL = HOST + APP + ACTION_PATH
MAX_RECORDS = 500  # what the page asks for; a response this big may be capped
CALL_BUDGET = 7  # Imperva blocked the 8th call from one IP (see module docstring)
DISCOVERY_CALLS = 3
# Cities searched together in one POST (ids from shared/cities.py). The only
# 4-POST plan within MAX_RANGE_KM with Madrid alone: a pair fits only when its
# centres are at most ~330 km apart, so Bilbao can only pair with Zaragoza,
# which leaves Barcelona+Valencia (Valencia is 468-540 km from Sevilla and
# Málaga). Splitting Barcelona and Valencia would need an 8th call. Probe
# 2026-11-17, 60 days, raw: madrid 300, barcelona+valencia 479 (Barcelona
# alone 249), sevilla+malaga 321, bilbao+zaragoza 90.
AREA_GROUPS = (("barcelona", "valencia"), ("sevilla", "malaga"), ("bilbao", "zaragoza"))
AREA_MARGIN_KM = 5  # slack between the server's distance and ours
MAX_RANGE_KM = 200  # 201 km worked, 250 km got "400 Bad Request" (probe 2026-11-17)

_CSRF_RE = re.compile(r'AnonymousCSRFToken\s*=\s*"([^"]+)"')
_API_VERSION_RE = re.compile(re.escape(f'"{ACTION_PATH}"') + r'\s*,\s*"([^"]+)"')
_BLOCK_MARKERS = ("Pardon Our Interruption", "_Incapsula_Resource", "Request unsuccessful. Incapsula")

# Untitled events (play sessions) get a title from their type.
_TITLE_BY_TYPE = {
    "League": "Liga Pokémon",
    "Cup": "League Cup Pokémon",
    "Challenge": "League Challenge Pokémon",
    "Friendly Tournament": "Torneo amistoso Pokémon",
    "Prerelease": "Presentación Pokémon",
}
# EventTypeName → format hint (matched against shared FORMAT_KEYWORDS).
_FORMAT_BY_TYPE = {
    "League": "Liga Pokémon",
    "Cup": "League Cup",
    "Challenge": "League Challenge",
    "Prerelease": "Prerelease",
}
_CANCELLED = {"cancelled", "canceled", "rejected", "deleted"}


class BlockedError(RuntimeError):
    """events.pokemon.com answered with its bot-protection page."""


class BudgetExceeded(RuntimeError):
    """The run would spend more calls than Imperva tolerates."""


class CallBudget:
    """A session that refuses the call after `limit` calls instead of
    letting Imperva block the IP mid-run."""

    def __init__(self, sess, limit: int = CALL_BUDGET):
        self.sess, self.limit, self.used = sess, limit, 0

    def request(self, method: str, url: str, **kwargs):
        if self.used >= self.limit:
            raise BudgetExceeded(f"Pokémon locator: call {self.used + 1} would exceed the budget "
                                 f"of {self.limit} ({method} {url})")
        self.used += 1
        return self.sess.request(method, url, **kwargs)


# ---------------------------------------------------------------------------
# Search areas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchArea:
    """One POST: a circle enclosing the circles of `cities`."""
    cities: tuple[City, ...]
    lat: float
    lng: float
    radius_km: int
    location_name: str

    @property
    def label(self) -> str:
        return "+".join(c.id for c in self.cities)

    @classmethod
    def of(cls, members: tuple[City, ...]) -> "SearchArea":
        if len(members) == 1:  # the request a one-city search always made
            c = members[0]
            return cls(members, c.lat, c.lng, c.radius_km, c.location_name)
        lats, lngs = [c.lat for c in members], [c.lng for c in members]
        lat = round((min(lats) + max(lats)) / 2, 4)
        lng = round((min(lngs) + max(lngs)) / 2, 4)
        reach = max(distance_km(lat, lng, c.lat, c.lng) + c.radius_km for c in members)
        return cls(members, lat, lng, math.ceil(reach) + AREA_MARGIN_KM, members[0].location_name)

    def split(self, items: list[dict]) -> dict[str, list[dict]]:
        """Each member city's items: venues inside its radius. Items outside
        every member's radius (or without a pin) are dropped."""
        out: dict[str, list[dict]] = {c.id: [] for c in self.cities}
        for item in items:
            addr = (item.get("Events") or {}).get("Address") or {}
            lat, lng = addr.get("Latitude"), addr.get("Longitude")
            if lat in (None, "") or lng in (None, ""):
                continue
            city = next((c for c in self.cities if c.contains(lat, lng)), None)
            if city:
                out[city.id].append(item)
        return out


def search_areas(cities: tuple[City, ...] = CITIES,
                 groups: tuple[tuple[str, ...], ...] = AREA_GROUPS) -> list[SearchArea]:
    """One area per group, one per ungrouped city, in the order of `cities`
    (an area sits where its first member city is)."""
    group_of = {cid: g for g in groups for cid in g}
    by_id = {c.id: c for c in cities}
    areas, done = [], set()
    for c in cities:
        if c.id in done:
            continue
        members = tuple(by_id[cid] for cid in group_of.get(c.id, (c.id,)) if cid in by_id)
        done.update(m.id for m in members)
        areas.append(SearchArea.of(members))
    return areas


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _call(sess: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 60)
    resp = sess.request(method, url, **kwargs)
    head = resp.text[:20000] if "html" in resp.headers.get("content-type", "") else ""
    if any(m in head for m in _BLOCK_MARKERS):
        raise BlockedError(f"Imperva bot protection answered {method} {url} "
                           f"(HTTP {resp.status_code}); not retrying")
    resp.raise_for_status()
    return resp


def _json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError(f"{resp.url}: expected JSON, got "
                           f"{resp.headers.get('content-type')!r}") from exc


def discover(sess: requests.Session) -> dict:
    """moduleVersion, apiVersion and CSRF token of the deployed app."""
    manifest = _json(_call(sess, "GET", MODULEINFO_URL))["manifest"]
    versions = manifest["urlVersions"]
    # The session asks for JSON by default; scripts need a script Accept or
    # the server answers 406.
    js = {"Accept": "application/javascript, */*;q=0.8"}
    runtime = _call(sess, "GET", HOST + RUNTIME_SCRIPT + versions[RUNTIME_SCRIPT], headers=js).text
    home = _call(sess, "GET", HOST + HOME_SCRIPT + versions[HOME_SCRIPT], headers=js).text
    csrf, api = _CSRF_RE.search(runtime), _API_VERSION_RE.search(home)
    if not (manifest.get("versionToken") and csrf and api):
        raise RuntimeError("Pokémon locator: could not discover moduleVersion/apiVersion/CSRF "
                           "token; the app's assets changed")
    return {"moduleVersion": manifest["versionToken"], "apiVersion": api.group(1),
            "csrf": csrf.group(1)}


def _body(app: dict, start: date, end: date, area: SearchArea) -> dict:
    lat, lng, km = str(area.lat), str(area.lng), area.radius_km
    return {
        "versionInfo": {"moduleVersion": app["moduleVersion"], "apiVersion": app["apiVersion"]},
        "viewName": "MainFlow.Home",
        "screenData": {"variables": {
            "latitude": lat, "longitude": lng, "range": str(km), "iskm": "true",
            "locale": "en-us", "filters": "", "MaxRecords": MAX_RECORDS, "FiltersOnHold": False,
        }},
        "clientVariables": {
            "Latitude": lat, "Longitude": lng, "Range": km, "IsKm": True,
            "LocationName": area.location_name,
            "StartDate": start.isoformat(), "EndDate": end.isoformat(),  # both inclusive
            "UserLocale": "en-us", "FilterEventTypes": "", "IsSearch": True, "IsShowEvents": True,
            "MaxDateLimit": 180, "RemovedFilter": "", "PremierEventsIdList": "",
            "AreEventsBeingSearched": True,
        },
    }


def _post(sess, app: dict, start: date, end: date, area: SearchArea) -> list[dict]:
    data = _json(_call(sess, "POST", ACTION_URL, json=_body(app, start, end, area), headers={
        "X-CSRFToken": app["csrf"], "Content-Type": "application/json; charset=UTF-8",
        "Origin": HOST, "Referer": LOCATOR_PAGE,
    }))
    info = data.get("versionInfo") or {}
    if info.get("hasModuleVersionChanged") or info.get("hasApiVersionChanged"):
        raise RuntimeError(f"Pokémon locator: app redeployed mid-run ({info})")
    if data.get("exception"):
        raise RuntimeError(f"Pokémon locator: server exception {data['exception']}")
    return data["data"]["EventList"]["List"]


def _fetch_area(sess, area: SearchArea, app: dict, split: bool = True) -> list[dict]:
    """Every event of one search area. A response of MAX_RECORDS may be
    capped: with `split`, the window is halved and asked again (CallBudget
    bounds it); without, the area fails rather than spend later areas' calls."""
    if area.radius_km > MAX_RANGE_KM:
        raise RuntimeError(f"Pokémon locator: area {area.label} is {area.radius_km} km, wider than "
                           f"the server allows ({MAX_RANGE_KM} km); regroup AREA_GROUPS")
    start, end = window()
    pending, items = [(start, end)], {}
    while pending:
        a, b = pending.pop()
        batch = _post(sess, app, a, b, area)
        if len(batch) >= MAX_RECORDS:
            if not split:
                raise RuntimeError(f"Pokémon locator: area {area.label} returned {len(batch)} "
                                   f"events, the response may be capped")
            if b <= a:
                raise RuntimeError(f"Pokémon locator: {area.label} has {len(batch)} events "
                                   f"on {a} alone; the response may be capped")
            mid = a + (b - a) // 2
            pending += [(a, mid), (mid + timedelta(days=1), b)]
            continue
        for item in batch:
            items[item["Events"]["Guid"]] = item
    return list(items.values())


def _fetch_events(sess, city: Optional[City] = None, app: Optional[dict] = None) -> list[dict]:
    """One city searched alone (default: the default city). `app` is
    discover()'s result."""
    city = city or default_city()
    return _fetch_area(sess, SearchArea.of((city,)), app or discover(sess))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def start_iso(ev: dict) -> Optional[str]:
    """Start time in Madrid. Play sessions are UTC; other activities are
    venue wall-clock time with a bogus 'Z' (see module docstring)."""
    raw = (ev.get("Start_date") or "").strip()
    if not raw:
        return None
    if ev.get("Activity_type") == "play_session":
        return to_madrid_iso(raw)
    venue_tz = (ev.get("Address") or {}).get("Timezone") or "Europe/Madrid"
    return to_madrid_iso(raw[:19], assume_tz=venue_tz)


def _format_hint(ev: dict, type_name: str) -> str:
    if ev.get("Category") == "tcg_exp":
        return "Expanded"
    if ev.get("Activity_type") == "prerelease":
        return "Prerelease"
    return _FORMAT_BY_TYPE.get(type_name, "")


def parse_events(items: list[dict], resolver: StoreResolver,
                 city: Optional[City] = None) -> list[dict]:
    """Events of one city's search inside its radius (default: the default city)."""
    city = city or default_city()
    out: list[dict] = []
    for item in items:
        ev = item.get("Events") or {}
        products = {p.lower() for p in (ev.get("Products") or {}).get("List") or []}
        if "tcg" not in products:  # video game / Pokémon GO only
            continue
        if "online" in (ev.get("Activity_type") or "").lower():
            continue
        if (ev.get("Status") or "").lower() in _CANCELLED:
            continue
        addr = ev.get("Address") or {}
        venue, lat, lng = (addr.get("Name") or "").strip(), addr.get("Latitude"), addr.get("Longitude")
        if not venue or lat in (None, "") or lng in (None, ""):
            continue  # no physical venue
        if not city.contains(lat, lng):
            continue
        start = start_iso(ev)
        if not start or not ev.get("Guid"):
            continue
        address = (addr.get("Full_address") or "").strip()
        store = resolver.resolve(SOURCE, addr.get("Guid"), venue, address, city=city,
                                 lat=lat, lng=lng)
        if store is StoreResolver.SKIP:
            continue
        type_name = (item.get("EventTypeName") or "").strip()
        title = (ev.get("Name") or "").strip() or _TITLE_BY_TYPE.get(type_name, "Evento Pokémon TCG")
        out.append(make_event(
            title=title,
            store=store,
            store_address=address,
            game=GAME,
            start_iso=start,
            source_event_id=f"{SOURCE}:{ev['Guid']}",
            source_url=LOCATOR_PAGE,  # the locator has no per-event page
            format_hint=_format_hint(ev, type_name),
            hint=store_hint(SOURCE, addr.get("Guid"), venue, address, lat, lng, city=city),
        ))
    return out


def scrape() -> list[dict]:
    """One discovery (3 GETs), then one POST per search area, most important
    first; each area's venues are split between its cities. See "Request
    budget" and "Failure is contained per area" above."""
    areas = search_areas()
    critical = areas[0]  # Madrid alone: its failure fails the source
    if critical.cities != (default_city(),):
        raise RuntimeError(f"Pokémon locator: the first search area must be "
                           f"{default_city().id} alone, not {critical.label}")
    sess = CallBudget(session())
    app = discover(sess)
    resolver = StoreResolver()
    raw_by_city: dict[str, list[dict]] = {}
    failed: list[str] = []
    for area in areas:
        try:
            raw = _fetch_area(sess, area, app, split=area is critical)
        except (RuntimeError, requests.RequestException) as exc:
            if area is critical:
                raise
            failed.append(area.label)
            logger.error("%s area %s failed; no Pokémon events for %s this run "
                         "(existing ones are kept by the per-city guard): %s",
                         STORE, area.label, ", ".join(c.id for c in area.cities), exc)
            raw_by_city.update({c.id: [] for c in area.cities})
            continue
        raw_by_city.update(area.split(raw))
        logger.info("%s area %s (%d km): %d raw → %s", STORE, area.label, area.radius_km, len(raw),
                    ", ".join(f"{c.id} {len(raw_by_city[c.id])}" for c in area.cities))

    def one_city(city: City) -> list[dict]:
        raw = raw_by_city[city.id]
        events = parse_events(raw, resolver, city)
        logger.info("%s %s: %d raw → %d events", STORE, city.id, len(raw), len(events))
        return events

    events = for_each_city(STORE, one_city)
    logger.info("%s: %d calls of %d; failed areas: %s", STORE, sess.used, CALL_BUDGET,
                ", ".join(failed) or "none")
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
