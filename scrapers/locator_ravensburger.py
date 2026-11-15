"""
scrapers/locator_ravensburger.py — Disney Lorcana events from Ravensburger
Play Hub (the hydraproxy API behind tcg.ravensburgerplay.com).
"""

import logging

from shared.locator_common import (
    CITY, StoreResolver, make_event, request_json, session, to_madrid_iso, window,
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


def _fetch_events(sess) -> list[dict]:
    start, end = window()
    events: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = request_json(sess, "GET", EVENTS_URL, params={
            "game_id": LORCANA_GAME_ID,
            "latitude": CITY["lat"], "longitude": CITY["lng"],
            "num_miles": round(CITY["radius_km"] / KM_PER_MILE),
            "start_date_after": f"{start.isoformat()}T00:00:00Z",
            "start_date_before": f"{end.isoformat()}T23:59:59Z",
            "display_status": "upcoming",
            "page": page, "page_size": PAGE_SIZE,
        })
        events += data.get("results") or []
        if not data.get("next"):
            break
    return events


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
