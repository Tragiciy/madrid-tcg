"""
scrapers/locator_ravensburger.py — Disney Lorcana events from Ravensburger
Play Hub (the hydraproxy API behind tcg.ravensburgerplay.com), one search
per city (shared/cities.py).
"""

import logging
from datetime import datetime
from typing import Optional

from shared.cities import City, default as default_city
from shared.locator_common import (
    TZ, StoreResolver, for_each_city, lookback_start, make_event, request_json, store_hint,
    session, to_madrid_iso, venue_wall_clock, window,
)

logger = logging.getLogger(__name__)

STORE = "Ravensburger Play Locator"
SOURCE = "ravensburger"
GAME = "Lorcana"
EVENTS_URL = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/events/"
LORCANA_GAME_ID = 1
PAGE_SIZE = 100
MAX_PAGES = 20
KM_PER_MILE = 1.609344


def _fetch(sess, city: City, start, end, status: str) -> list[dict]:
    events: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = request_json(sess, "GET", EVENTS_URL, params={
            "game_id": LORCANA_GAME_ID,
            "latitude": city.lat, "longitude": city.lng,
            "num_miles": round(city.radius_km / KM_PER_MILE),
            "start_date_after": f"{start.isoformat()}T00:00:00Z",
            "start_date_before": f"{end.isoformat()}T23:59:59Z",
            "display_status": status,
            "page": page, "page_size": PAGE_SIZE,
        })
        events += data.get("results") or []
        if not data.get("next"):
            break
    return events


def _fetch_events(sess, city: City) -> list[dict]:
    """Upcoming events, plus the last LOOKBACK_DAYS of finished ones (for
    their final player counts)."""
    start, end = window()
    upcoming = _fetch(sess, city, start, end, "upcoming")
    past = _fetch(sess, city, lookback_start(), start, "past")
    seen = {ev["id"] for ev in upcoming}
    return upcoming + [ev for ev in past if ev["id"] not in seen]


def event_times(ev: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(start, end, source_start) in Madrid time.

    A few Spanish stores' events carry an American `timezone` (the
    organizer account's): the API then stores their typed time as New York
    or Port of Spain time — Kingdom Wargames' Friday 21:00 arrives as
    Saturday 03:00 Madrid. For a store in Spain whose event zone is another
    one, the wall-clock time in that zone is read as Madrid time.
    source_start is what the source said, set only when the time changed
    (Europe/Paris, same clock as Madrid, changes nothing).
    """
    raw_start, raw_end = ev.get("start_datetime") or "", ev.get("end_datetime") or ""
    start = to_madrid_iso(raw_start)
    end = to_madrid_iso(raw_end) if not ev.get("is_end_datetime_estimated") else None
    zone = ev.get("timezone")
    country = ((ev.get("store") or {}).get("country") or "").upper()
    if not start or country != "ES" or not zone or zone == "Europe/Madrid":
        return start, end, None
    fixed = venue_wall_clock(raw_start, zone)
    if not fixed or fixed == start:
        return start, end, None
    return fixed, (venue_wall_clock(raw_end, zone) if end else None), start


def parse_events(raw_events: list[dict], resolver: StoreResolver,
                 city: Optional[City] = None) -> list[dict]:
    """Events of one city's search (default: the default city)."""
    city = city or default_city()
    out: list[dict] = []
    for ev in raw_events:
        if ev.get("event_is_online") or ev.get("is_test_event"):
            continue
        if (ev.get("event_status") or "").upper() in ("CANCELED", "CANCELLED"):
            continue
        store_info = ev.get("store") or {}
        start, end, source_start = event_times(ev)
        if not store_info.get("name") or not start or not ev.get("name"):
            continue
        address = store_info.get("full_address") or ev.get("full_address") or ""
        store = resolver.resolve(SOURCE, store_info.get("id"), store_info["name"], address,
                                 city=city, lat=store_info.get("latitude"),
                                 lng=store_info.get("longitude"))
        if store is StoreResolver.SKIP:
            continue
        finished = datetime.fromisoformat(start) <= datetime.now(TZ)
        event = make_event(
            title=ev["name"],
            store=store,
            store_address=address,
            game=GAME,
            start_iso=start,
            end_iso=end,
            source_event_id=f"{SOURCE}:{ev['id']}",
            source_url=f"https://tcg.ravensburgerplay.com/events/{ev['id']}",
            format_hint=(ev.get("gameplay_format") or {}).get("name") or "",
            capacity=ev.get("capacity"),
            # Before the event: sign-ups so far. After it ("complete"): who played.
            registered=None if finished else ev.get("registered_user_count"),
            participants=(ev.get("starting_player_count") or ev.get("registered_user_count"))
            if finished else None,
            hint=store_hint(SOURCE, store_info.get("id"), store_info["name"], address,
                            store_info.get("latitude"), store_info.get("longitude"), city=city),
        )
        if source_start:
            # Same fields as pipeline/time_fixes.py (shown as "Source says …").
            event["datetime_start_source"] = source_start
            event["time_corrected"] = True
        out.append(event)
    return out


def scrape() -> list[dict]:
    sess = session()
    resolver = StoreResolver()

    def one_city(city: City) -> list[dict]:
        raw = _fetch_events(sess, city)
        events = parse_events(raw, resolver, city)
        logger.info("%s %s: %d raw → %d events", STORE, city.id, len(raw), len(events))
        return events

    return for_each_city(STORE, one_city)


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
