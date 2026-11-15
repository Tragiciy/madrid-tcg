"""
scrapers/locator_bandai.py — One Piece and Digimon events from the Bandai
TCG+ event locator (api.bandai-tcg-plus.com).

Bandai returns local start times without an offset plus an IANA timezone,
and event titles are series names like "[November 2026] OP Store
Tournament Event"; the bracketed month prefix is dropped.
"""

import logging
import re

from shared.locator_common import (
    CITY, StoreResolver, make_event, request_json, session, to_madrid_iso, window,
)

logger = logging.getLogger(__name__)

STORE = "Bandai TCG+ Locator"
SOURCE = "bandai"
LIST_URL = "https://api.bandai-tcg-plus.com/api/user/event/list"
HEADERS = {
    "Origin": "https://www.bandai-tcg-plus.com",
    "Referer": "https://www.bandai-tcg-plus.com/",
}
GAMES = {4: "One Piece", 2: "Digimon"}  # game_title_id → canonical game
PAGE_SIZE = 100
MAX_EVENTS_PER_GAME = 1000

_MONTH_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]\s*")


def _fetch_game(sess, game_title_id: int) -> list[dict]:
    start, end = window()
    events: list[dict] = []
    offset = 0
    while offset < MAX_EVENTS_PER_GAME:
        data = request_json(sess, "GET", LIST_URL, headers=HEADERS, params={
            "limit": PAGE_SIZE, "offset": offset, "game_title_id": game_title_id,
            "current_lat": CITY["lat"], "current_lng": CITY["lng"],
            "distance": CITY["radius_km"],
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        })
        success = data.get("success")
        if not isinstance(success, dict):
            raise RuntimeError(f"{STORE}: unexpected response {str(data)[:200]}")
        batch = success.get("event_list") or []
        events += batch
        offset += PAGE_SIZE
        if not batch or offset >= int(success.get("total") or 0):
            break
    return events


def _address(ev: dict) -> str:
    parts = [ev.get("street_address"), ev.get("postcode"), ev.get("city_code")]
    return ", ".join(p for p in parts if p)


def parse_events(raw_events: list[dict], game: str, resolver: StoreResolver) -> list[dict]:
    out: list[dict] = []
    for ev in raw_events:
        if ev.get("is_canceled") or str(ev.get("online_flag")) == "1":
            continue
        start = to_madrid_iso(ev.get("start_datetime") or "", ev.get("timezone") or "Europe/Madrid")
        name = ev.get("organizer_name")
        title = _MONTH_PREFIX_RE.sub("", ev.get("event_series_title") or "").strip()
        if not start or not name or not title:
            continue
        address = _address(ev)
        store = resolver.resolve(SOURCE, ev.get("organizer_id"), name, address)
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
        ))
    return out


def scrape() -> list[dict]:
    sess = session()
    resolver = StoreResolver()
    events: list[dict] = []
    for game_title_id, game in GAMES.items():
        raw = _fetch_game(sess, game_title_id)
        parsed = parse_events(raw, game, resolver)
        logger.info("%s %s: %d raw → %d events", STORE, game, len(raw), len(parsed))
        events += parsed
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
