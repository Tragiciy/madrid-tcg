"""
shared/bandai_locator.py — Bandai TCG+ event locator client
(api.bandai-tcg-plus.com), shared by one scraper module per game:
scrapers/locator_bandai_onepiece.py and scrapers/locator_bandai_digimon.py.
One module per game makes each game its own source, so protection and
suspect_zero health work per game (Digimon going to zero is not hidden by
One Piece still returning events).

Bandai returns local start times without an offset plus an IANA timezone,
and event titles are series names like "[November 2026] OP Store
Tournament Event"; the bracketed month prefix is dropped. The search is
server-side (current_lat/current_lng/distance), one per city. In `place_geo`,
`x` is the latitude and `y` the longitude (the reverse of the usual x/y).
"""

import logging
import re
from typing import Optional

from shared.cities import City, default as default_city
from shared.locator_common import (
    StoreResolver, for_each_city, make_event, request_json, store_hint, session, to_madrid_iso,
    window,
)

logger = logging.getLogger(__name__)

SOURCE = "bandai"
LIST_URL = "https://api.bandai-tcg-plus.com/api/user/event/list"
HEADERS = {
    "Origin": "https://www.bandai-tcg-plus.com",
    "Referer": "https://www.bandai-tcg-plus.com/",
}
# Bandai TCG+ game_title_id values (probed 2026-11-17; ids it doesn't know
# are ignored or rejected, so a wrong id shows up as a wrong game_title).
GAME_TITLE_IDS = {
    "One Piece": 4,
    "Digimon": 2,
    "Dragon Ball Fusion World": 10,
    "Dragon Ball Super Masters": 1,  # the paper "DBSCG MASTERS" game
    "Gundam Card Game": 16,
}
PAGE_SIZE = 100
MAX_EVENTS_PER_GAME = 1000

_MONTH_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]\s*")


def _fetch_game(sess, game_title_id: int, city: City) -> list[dict]:
    start, end = window()
    events: list[dict] = []
    offset = 0
    while offset < MAX_EVENTS_PER_GAME:
        data = request_json(sess, "GET", LIST_URL, headers=HEADERS, params={
            "limit": PAGE_SIZE, "offset": offset, "game_title_id": game_title_id,
            "current_lat": city.lat, "current_lng": city.lng,
            "distance": city.radius_km,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        })
        success = data.get("success")
        if not isinstance(success, dict):
            raise RuntimeError(f"Bandai TCG+: unexpected response {str(data)[:200]}")
        batch = success.get("event_list") or []
        # An id Bandai doesn't know can come back as another game's events
        # (3 returned DBSCG MASTERS): never publish those under our label.
        wrong = {str(e.get("game_title_id")) for e in batch} - {str(game_title_id)}
        if wrong:
            raise RuntimeError(f"Bandai TCG+: asked for game_title_id {game_title_id}, got {sorted(wrong)}")
        events += batch
        offset += PAGE_SIZE
        if not batch or offset >= int(success.get("total") or 0):
            break
    return events


def _address(ev: dict) -> str:
    parts = [ev.get("street_address"), ev.get("postcode"), ev.get("city_code")]
    return ", ".join(p for p in parts if p)


def parse_events(raw_events: list[dict], game: str, resolver: StoreResolver,
                 city: Optional[City] = None) -> list[dict]:
    """Events of one city's search (default: the default city)."""
    city = city or default_city()
    out: list[dict] = []
    for ev in raw_events:
        if ev.get("is_canceled") or str(ev.get("online_flag")) == "1":
            continue
        # Bandai's `timezone` is the organizer account's setting, not the
        # venue's: Madrid shops show up as America/Chicago. Venues in Spain
        # are read as Madrid local time.
        tz = "Europe/Madrid" if (ev.get("country_code") or "ES") == "ES" else ev.get("timezone")
        if tz != ev.get("timezone"):
            logger.debug("Bandai event %s: timezone %s overridden to %s",
                         ev.get("id"), ev.get("timezone"), tz)
        start = to_madrid_iso(ev.get("start_datetime") or "", tz or "Europe/Madrid")
        name = ev.get("organizer_name")
        title = _MONTH_PREFIX_RE.sub("", ev.get("event_series_title") or "").strip()
        if not start or not name or not title:
            continue
        address = _address(ev)
        geo = ev.get("place_geo") or {}  # x = latitude, y = longitude
        store = resolver.resolve(SOURCE, ev.get("organizer_id"), name, address, city=city,
                                 lat=geo.get("x"), lng=geo.get("y"))
        if store is StoreResolver.SKIP:
            continue
        out.append(make_event(
            title=title,
            store=store,
            store_address=address,
            game=game,
            start_iso=start,
            source_event_id=f"{SOURCE}:{ev['id']}",
            source_url=f"https://www.bandai-tcg-plus.com/event/{ev['id']}",
            capacity=None if ev.get("is_capacity_hidden") else ev.get("max_join_count"),
            full=bool(ev.get("is_over_max_join_count")),
            # place_geo: x = latitude, y = longitude.
            hint=store_hint(SOURCE, ev.get("organizer_id"), name, address,
                            geo.get("x"), geo.get("y"), city=city),
        ))
    return out


def scrape_game(game: str, label: str) -> list[dict]:
    """Events for one game within each city's radius and the time window."""
    sess, resolver = session(), StoreResolver()

    def one_city(city: City) -> list[dict]:
        raw = _fetch_game(sess, GAME_TITLE_IDS[game], city)
        events = parse_events(raw, game, resolver, city)
        logger.info("%s %s: %d raw → %d events", label, city.id, len(raw), len(events))
        return events

    return for_each_city(label, one_city)
