"""
scrapers/locator_gem.py — Flesh and Blood events from GEM, the official
FaB event locator (gem.fabtcg.com).

Quirks (checked 2026-11):
  - lat/lon parameters do not filter results, so we query by each city's
    search terms (`search`, shared/cities.py) and keep only events inside
    that city's radius;
  - start_time is the venue's wall-clock time with a bogus "+13:00"
    offset; the offset is dropped and the time read as Madrid time.
"""

import logging
from datetime import datetime
from typing import Optional

from shared.cities import City, default as default_city
from shared.locator_common import (
    StoreResolver, TZ, for_each_city, make_event, request_json, store_hint, session, window,
)

logger = logging.getLogger(__name__)

STORE = "GEM Locator"
SOURCE = "gem"
GAME = "Flesh and Blood"
EVENTS_URL = "https://gem.fabtcg.com/api/v1/locator/events/"
LOCATOR_PAGE = "https://fabtcg.com/en/locator/"
MAX_PAGES = 10


def _wall_clock(value: str):
    """'2026-11-21T17:00:00+13:00' → 2026-11-21 17:00 Madrid (offset ignored)."""
    try:
        naive = datetime.fromisoformat(value[:19])
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=TZ)


def _fetch(sess, city: City) -> list[dict]:
    seen: dict[int, dict] = {}
    for term in city.search_terms:
        url, params = EVENTS_URL, {"search": term}
        for _ in range(MAX_PAGES):
            data = request_json(sess, "GET", url, params=params)
            for ev in data.get("results") or []:
                seen[ev["id"]] = ev
            url, params = data.get("next"), None
            if not url:
                break
    return list(seen.values())


def parse_events(raw_events: list[dict], resolver: StoreResolver,
                 city: Optional[City] = None) -> list[dict]:
    """Events of one city's search inside its radius (default: the default city)."""
    city = city or default_city()
    start_day, end_day = window()
    out: list[dict] = []
    for ev in raw_events:
        if (ev.get("status") or "").upper() != "PLANNED":
            continue
        if ev.get("lat") is None or ev.get("lon") is None:
            continue
        if not city.contains(ev["lat"], ev["lon"]):
            continue
        start = _wall_clock(ev.get("start_time") or "")
        if not start or not (start_day <= start.date() <= end_day):
            continue
        name, address = ev.get("organiser_name"), ev.get("address") or ""
        if not name:
            continue
        store = resolver.resolve(SOURCE, ev.get("organiser_store_slug") or name, name, address,
                                 city=city)
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
                            ev.get("lat"), ev.get("lon"), city=city),
        ))
    return out


def scrape() -> list[dict]:
    sess = session()
    resolver = StoreResolver()

    def one_city(city: City) -> list[dict]:
        raw = _fetch(sess, city)
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
