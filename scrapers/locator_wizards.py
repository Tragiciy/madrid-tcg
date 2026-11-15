"""
scrapers/locator_wizards.py — Magic: The Gathering events from the Wizards
Store & Event Locator (the GraphQL API behind locator.wizards.com).

One request for store addresses (storesByLocation) plus paginated
searchEvents within CITY's radius and the next WINDOW_DAYS days. Covers
every Wizards-registered store in the area, including stores we have no
scraper for.
"""

import logging

from shared.locator_common import (
    CITY, StoreResolver, make_event, request_json, session, to_madrid_iso, window,
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
    stores { id name postalAddress }
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
      id title scheduledStartTime
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


def _fetch_store_addresses(sess) -> dict[str, str]:
    data = _graphql(sess, STORES_QUERY, {
        "lat": CITY["lat"], "lng": CITY["lng"], "m": CITY["radius_km"] * 1000,
        "page": 0, "size": 200,
    })
    return {
        str(s["id"]): s.get("postalAddress") or ""
        for s in data["storesByLocation"]["stores"]
    }


def _fetch_events(sess) -> list[dict]:
    start, end = window()
    events: list[dict] = []
    for page in range(MAX_PAGES):
        data = _graphql(sess, EVENTS_QUERY, {
            "lat": CITY["lat"], "lng": CITY["lng"], "m": CITY["radius_km"] * 1000,
            "start": f"{start.isoformat()}T00:00:00Z", "end": f"{end.isoformat()}T23:59:59Z",
            "page": page, "size": PAGE_SIZE,
        })["searchEvents"]
        events += data["events"]
        if not data["events"] or len(events) >= data["pageInfo"]["totalResults"]:
            break
    return events


def parse_events(raw_events: list[dict], addresses: dict[str, str],
                 resolver: StoreResolver) -> list[dict]:
    out: list[dict] = []
    for ev in raw_events:
        org = ev.get("organization") or {}
        start = to_madrid_iso(ev.get("scheduledStartTime") or "")
        if not org.get("name") or not start or not ev.get("title"):
            continue
        address = addresses.get(str(org.get("id")), "")
        store = resolver.resolve(SOURCE, org.get("id"), org["name"], address)
        if store is StoreResolver.SKIP:
            continue
        out.append(make_event(
            title=ev["title"],
            store=store,
            store_address=address,
            game=GAME,
            start_iso=start,
            source_event_id=f"{SOURCE}:{ev['id']}",
            source_url=f"https://locator.wizards.com/event/{ev['id']}",
            format_hint=(ev.get("eventFormat") or {}).get("name") or "",
        ))
    return out


def scrape() -> list[dict]:
    sess = session()
    addresses = _fetch_store_addresses(sess)
    raw = _fetch_events(sess)
    events = parse_events(raw, addresses, StoreResolver())
    logger.info("%s: %d raw → %d events", STORE, len(raw), len(events))
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
