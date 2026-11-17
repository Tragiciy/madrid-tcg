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
"""

import logging
import math
import re
from datetime import date, timedelta
from typing import Optional

import requests

from shared.locator_common import (
    CITY, StoreResolver, make_event, session, store_hint, to_madrid_iso, window,
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
MAX_RECORDS = 500
MAX_POSTS = 6  # window splits if a response is ever capped

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
    runtime = _call(sess, "GET", HOST + RUNTIME_SCRIPT + versions[RUNTIME_SCRIPT]).text
    home = _call(sess, "GET", HOST + HOME_SCRIPT + versions[HOME_SCRIPT]).text
    csrf, api = _CSRF_RE.search(runtime), _API_VERSION_RE.search(home)
    if not (manifest.get("versionToken") and csrf and api):
        raise RuntimeError("Pokémon locator: could not discover moduleVersion/apiVersion/CSRF "
                           "token; the app's assets changed")
    return {"moduleVersion": manifest["versionToken"], "apiVersion": api.group(1),
            "csrf": csrf.group(1)}


def _body(app: dict, start: date, end: date) -> dict:
    lat, lng, km = str(CITY["lat"]), str(CITY["lng"]), CITY["radius_km"]
    return {
        "versionInfo": {"moduleVersion": app["moduleVersion"], "apiVersion": app["apiVersion"]},
        "viewName": "MainFlow.Home",
        "screenData": {"variables": {
            "latitude": lat, "longitude": lng, "range": str(km), "iskm": "true",
            "locale": "en-us", "filters": "", "MaxRecords": MAX_RECORDS, "FiltersOnHold": False,
        }},
        "clientVariables": {
            "Latitude": lat, "Longitude": lng, "Range": km, "IsKm": True,
            "LocationName": f"{CITY['name']}, Spain",
            "StartDate": start.isoformat(), "EndDate": end.isoformat(),  # both inclusive
            "UserLocale": "en-us", "FilterEventTypes": "", "IsSearch": True, "IsShowEvents": True,
            "MaxDateLimit": 180, "RemovedFilter": "", "PremierEventsIdList": "",
            "AreEventsBeingSearched": True,
        },
    }


def _post(sess, app: dict, start: date, end: date) -> list[dict]:
    data = _json(_call(sess, "POST", ACTION_URL, json=_body(app, start, end), headers={
        "X-CSRFToken": app["csrf"], "Content-Type": "application/json; charset=UTF-8",
        "Origin": HOST, "Referer": LOCATOR_PAGE,
    }))
    info = data.get("versionInfo") or {}
    if info.get("hasModuleVersionChanged") or info.get("hasApiVersionChanged"):
        raise RuntimeError(f"Pokémon locator: app redeployed mid-run ({info})")
    if data.get("exception"):
        raise RuntimeError(f"Pokémon locator: server exception {data['exception']}")
    return data["data"]["EventList"]["List"]


def _fetch_events(sess) -> list[dict]:
    app = discover(sess)
    start, end = window()
    pending, items, posts = [(start, end)], {}, 0
    while pending:
        a, b = pending.pop()
        if posts == MAX_POSTS:
            raise RuntimeError("Pokémon locator: too many requests splitting a capped window")
        posts += 1
        batch = _post(sess, app, a, b)
        if len(batch) >= MAX_RECORDS and b > a:  # capped: split and ask again
            mid = a + (b - a) // 2
            pending += [(a, mid), (mid + timedelta(days=1), b)]
            continue
        for item in batch:
            items[item["Events"]["Guid"]] = item
    return list(items.values())


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


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


def parse_events(items: list[dict], resolver: StoreResolver) -> list[dict]:
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
        if _km(CITY["lat"], CITY["lng"], float(lat), float(lng)) > CITY["radius_km"]:
            continue
        start = start_iso(ev)
        if not start or not ev.get("Guid"):
            continue
        address = (addr.get("Full_address") or "").strip()
        store = resolver.resolve(SOURCE, addr.get("Guid"), venue, address)
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
            hint=store_hint(SOURCE, addr.get("Guid"), venue, address, lat, lng),
        ))
    return out


def scrape() -> list[dict]:
    sess = session()
    raw = _fetch_events(sess)
    events = parse_events(raw, StoreResolver())
    logger.info("%s: %d raw → %d events", STORE, len(raw), len(events))
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
