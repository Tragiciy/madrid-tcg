"""
scrapers/locator_gem.py — Flesh and Blood events from GEM, the official
FaB event locator (gem.fabtcg.com).

Quirks (checked 2026-11):
  - lat/lon parameters do not filter results, so we query by city name
    (`search`) and filter by distance ourselves;
  - start_time is the venue's wall-clock time with a bogus "+13:00"
    offset; the offset is dropped and the time read as Madrid time.
"""

import logging
import math
from datetime import datetime

from shared.locator_common import (
    CITY, StoreResolver, TZ, make_event, request_json, store_hint, session, window,
)

logger = logging.getLogger(__name__)

STORE = "GEM Locator"
SOURCE = "gem"
GAME = "Flesh and Blood"
EVENTS_URL = "https://gem.fabtcg.com/api/v1/locator/events/"
LOCATOR_PAGE = "https://fabtcg.com/en/locator/"
SEARCH_TERMS = ["Madrid"]
MAX_PAGES = 10


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _wall_clock(value: str):
    """'2026-11-21T17:00:00+13:00' → 2026-11-21 17:00 Madrid (offset ignored)."""
    try:
        naive = datetime.fromisoformat(value[:19])
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=TZ)


def _fetch(sess) -> list[dict]:
    seen: dict[int, dict] = {}
    for term in SEARCH_TERMS:
        url, params = EVENTS_URL, {"search": term}
        for _ in range(MAX_PAGES):
            data = request_json(sess, "GET", url, params=params)
            for ev in data.get("results") or []:
                seen[ev["id"]] = ev
            url, params = data.get("next"), None
            if not url:
                break
    return list(seen.values())


def parse_events(raw_events: list[dict], resolver: StoreResolver) -> list[dict]:
    start_day, end_day = window()
    out: list[dict] = []
    for ev in raw_events:
        if (ev.get("status") or "").upper() != "PLANNED":
            continue
        if ev.get("lat") is None or ev.get("lon") is None:
            continue
        if _km(CITY["lat"], CITY["lng"], ev["lat"], ev["lon"]) > CITY["radius_km"]:
            continue
        start = _wall_clock(ev.get("start_time") or "")
        if not start or not (start_day <= start.date() <= end_day):
            continue
        name, address = ev.get("organiser_name"), ev.get("address") or ""
        if not name:
            continue
        store = resolver.resolve(SOURCE, ev.get("organiser_store_slug") or name, name, address)
        if store is StoreResolver.SKIP:
            continue
        kind = ev.get("tournament_type") or "Flesh and Blood event"
        fmt = ev.get("format_name") or ""
        out.append(make_event(
            title=f"{kind} – {fmt}" if fmt else kind,
            store=store,
            store_address=address,
            game=GAME,
            start_iso=start.isoformat(),
            source_event_id=f"{SOURCE}:{ev['id']}",
            source_url=ev.get("event_link") or LOCATOR_PAGE,
            format_hint=f"{kind} {fmt}",
            capacity=ev.get("player_cap"),
            hint=store_hint(SOURCE, ev.get("organiser_store_slug") or name, name, address,
                            ev.get("lat"), ev.get("lon")),
        ))
    return out


def scrape() -> list[dict]:
    raw = _fetch(session())
    events = parse_events(raw, StoreResolver())
    logger.info("%s: %d raw → %d events", STORE, len(raw), len(events))
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
