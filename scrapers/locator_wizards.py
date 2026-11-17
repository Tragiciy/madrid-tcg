"""
scrapers/locator_wizards.py — Magic: The Gathering events from the Wizards
Store & Event Locator (the GraphQL API behind locator.wizards.com).

Per city (shared/cities.py): one request for store addresses
(storesByLocation) plus paginated searchEvents within the city's radius and
the next WINDOW_DAYS days. Covers every Wizards-registered store in the
area, including stores we have no scraper for.
"""

import logging
from datetime import datetime
from typing import Optional

from shared.cities import City, default as default_city
from shared.locator_common import (
    TZ, StoreResolver, for_each_city, lookback_start, make_event, request_json, session,
    store_hint, to_madrid_iso, window,
)

logger = logging.getLogger(__name__)

STORE = "Wizards Locator"
SOURCE = "wizards"
GAME = "Magic: The Gathering"
GRAPHQL_URL = "https://api.tabletop.wizards.com/silverbeak-griffin-service/graphql"
HEADERS = {"Origin": "https://locator.wizards.com", "Content-Type": "application/json"}
PAGE_SIZE = 100
MAX_PAGES = 20

STORES_QUERY = """
query($lat: Float!, $lng: Float!, $m: Int!, $page: Int, $size: Int) {
  storesByLocation(input: {latitude: $lat, longitude: $lng, maxMeters: $m,
                           page: $page, pageSize: $size}) {
    stores { id name postalAddress latitude longitude }
    pageInfo { totalResults }
  }
}
"""

EVENTS_QUERY = """
query($lat: Float!, $lng: Float!, $m: Int!, $start: DateTime!, $end: DateTime!,
      $page: Int!, $size: Int!) {
  searchEvents(query: {latitude: $lat, longitude: $lng, maxMeters: $m, tags: [],
                       startDate: $start, endDate: $end, page: $page, pageSize: $size}) {
    events {
      id title scheduledStartTime capacity numberOfPlayers
      eventFormat { name }
      organization { id name }
    }
    pageInfo { totalResults }
  }
}
"""


def _graphql(sess, query: str, variables: dict) -> dict:
    data = request_json(sess, "POST", GRAPHQL_URL, headers=HEADERS,
                        json={"query": query, "variables": variables})
    if data.get("errors"):
        raise RuntimeError(f"{STORE}: GraphQL error {data['errors'][0].get('message')}")
    return data["data"]


def _area(city: City) -> dict:
    return {"lat": city.lat, "lng": city.lng, "m": city.radius_km * 1000}


def _fetch_store_addresses(sess, city: City) -> dict[str, dict]:
    data = _graphql(sess, STORES_QUERY, {**_area(city), "page": 0, "size": 200})
    return {
        str(s["id"]): {"address": s.get("postalAddress") or "",
                       "lat": s.get("latitude"), "lng": s.get("longitude")}
        for s in data["storesByLocation"]["stores"]
    }


def _fetch_events(sess, city: City) -> list[dict]:
    _, end = window()
    start = lookback_start()  # recent past too: their final player counts
    events: list[dict] = []
    for page in range(MAX_PAGES):
        data = _graphql(sess, EVENTS_QUERY, {
            **_area(city),
            "start": f"{start.isoformat()}T00:00:00Z", "end": f"{end.isoformat()}T23:59:59Z",
            "page": page, "size": PAGE_SIZE,
        })["searchEvents"]
        events += data["events"]
        if not data["events"] or len(events) >= data["pageInfo"]["totalResults"]:
            break
    return events


def parse_events(raw_events: list[dict], addresses: dict,
                 resolver: StoreResolver, city: Optional[City] = None) -> list[dict]:
    """Events of one city's search (default: the default city)."""
    city = city or default_city()
    out: list[dict] = []
    for ev in raw_events:
        org = ev.get("organization") or {}
        start = to_madrid_iso(ev.get("scheduledStartTime") or "")
        if not org.get("name") or not start or not ev.get("title"):
            continue
        info = addresses.get(str(org.get("id"))) or {}
        if isinstance(info, str):
            info = {"address": info}
        address = info.get("address") or ""
        store = resolver.resolve(SOURCE, org.get("id"), org["name"], address, city=city,
                                 lat=info.get("lat"), lng=info.get("lng"))
        if store is StoreResolver.SKIP:
            continue
        players = ev.get("numberOfPlayers")
        finished = datetime.fromisoformat(start) <= datetime.now(TZ)
        out.append(make_event(
            title=ev["title"],
            store=store,
            store_address=address,
            game=GAME,
            start_iso=start,
            source_event_id=f"{SOURCE}:{ev['id']}",
            source_url=f"https://locator.wizards.com/event/{ev['id']}",
            format_hint=(ev.get("eventFormat") or {}).get("name") or "",
            capacity=ev.get("capacity"),
            registered=None if finished else players,
            participants=players if finished else None,
            hint=store_hint(SOURCE, org.get("id"), org["name"], address,
                            info.get("lat"), info.get("lng"), city=city),
        ))
    return out


def scrape() -> list[dict]:
    sess = session()
    resolver = StoreResolver()

    def one_city(city: City) -> list[dict]:
        addresses = _fetch_store_addresses(sess, city)
        raw = _fetch_events(sess, city)
        events = parse_events(raw, addresses, resolver, city)
        logger.info("%s %s: %d raw → %d events", STORE, city.id, len(raw), len(events))
        return events

    return for_each_city(STORE, one_city)


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
