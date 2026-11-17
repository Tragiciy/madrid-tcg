"""
scrapers/locator_ravensburger.py — Disney Lorcana events from Ravensburger
Play Hub (the hydraproxy API behind tcg.ravensburgerplay.com).
"""

import logging
from datetime import datetime

from shared.locator_common import (
    CITY, TZ, StoreResolver, lookback_start, make_event, request_json, store_hint, session,
    to_madrid_iso, window,
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


def _fetch(sess, start, end, status: str) -> list[dict]:
    events: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = request_json(sess, "GET", EVENTS_URL, params={
            "game_id": LORCANA_GAME_ID,
            "latitude": CITY["lat"], "longitude": CITY["lng"],
            "num_miles": round(CITY["radius_km"] / KM_PER_MILE),
            "start_date_after": f"{start.isoformat()}T00:00:00Z",
            "start_date_before": f"{end.isoformat()}T23:59:59Z",
            "display_status": status,
            "page": page, "page_size": PAGE_SIZE,
        })
        events += data.get("results") or []
        if not data.get("next"):
            break
    return events


def _fetch_events(sess) -> list[dict]:
    """Upcoming events, plus the last LOOKBACK_DAYS of finished ones (for
    their final player counts)."""
    start, end = window()
    upcoming = _fetch(sess, start, end, "upcoming")
    past = _fetch(sess, lookback_start(), start, "past")
    seen = {ev["id"] for ev in upcoming}
    return upcoming + [ev for ev in past if ev["id"] not in seen]


def parse_events(raw_events: list[dict], resolver: StoreResolver) -> list[dict]:
    out: list[dict] = []
    for ev in raw_events:
        if ev.get("event_is_online") or ev.get("is_test_event"):
            continue
        if (ev.get("event_status") or "").upper() in ("CANCELED", "CANCELLED"):
            continue
        store_info = ev.get("store") or {}
        start = to_madrid_iso(ev.get("start_datetime") or "")
        if not store_info.get("name") or not start or not ev.get("name"):
            continue
        address = store_info.get("full_address") or ev.get("full_address") or ""
        store = resolver.resolve(SOURCE, store_info.get("id"), store_info["name"], address)
        if store is StoreResolver.SKIP:
            continue
        finished = datetime.fromisoformat(start) <= datetime.now(TZ)
        out.append(make_event(
            title=ev["name"],
            store=store,
            store_address=address,
            game=GAME,
            start_iso=start,
            end_iso=to_madrid_iso(ev.get("end_datetime") or "")
            if not ev.get("is_end_datetime_estimated") else None,
            source_event_id=f"{SOURCE}:{ev['id']}",
            source_url=f"https://tcg.ravensburgerplay.com/events/{ev['id']}",
            format_hint=(ev.get("gameplay_format") or {}).get("name") or "",
            capacity=ev.get("capacity"),
            # Before the event: sign-ups so far. After it ("complete"): who played.
            registered=None if finished else ev.get("registered_user_count"),
            participants=(ev.get("starting_player_count") or ev.get("registered_user_count"))
            if finished else None,
            hint=store_hint(SOURCE, store_info.get("id"), store_info["name"], address,
                            store_info.get("latitude"), store_info.get("longitude")),
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
