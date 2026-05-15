"""
shared/carde_store_locator.py — helpers for Carde/Hydra store locators.

Used by official locator sites such as Ravensburger Play and Riftbound Play
Network. No network requests are performed on import.
"""

from typing import Optional

import requests

MADRID_LAT = 40.3907
MADRID_LON = -3.6997
SEARCH_RADIUS_MILES = 25
PAGE_SIZE = 100
REQUEST_TIMEOUT = 30


def _address(store: dict) -> str:
    return (
        store.get("full_address")
        or store.get("street_address")
        or store.get("address", {}).get("formatted_address")
        or ""
    )


def _coordinates(store: dict) -> Optional[dict]:
    lat = store.get("latitude")
    lng = store.get("longitude")
    if lat is None or lng is None:
        return None
    return {"lat": lat, "lng": lng}


def _candidate(
    row: dict,
    *,
    source: str,
    game: str,
    locator_url: str,
) -> Optional[dict]:
    store = row.get("store") or {}
    name = store.get("name")
    address = _address(store)
    if not name or not address:
        return None

    external_id = str(store.get("id") or row.get("id") or "")
    evidence = {
        "source": source,
        "type": "official_store_locator",
        "game": game,
        "url": locator_url,
        "external_id": external_id,
        "game_store_id": row.get("id"),
        "store_types": store.get("store_types", []),
        "store_types_pretty": store.get("store_types_pretty", []),
        "country": store.get("country"),
        "zipcode": store.get("zipcode"),
    }
    coordinates = _coordinates(store)
    if coordinates:
        evidence["coordinates"] = coordinates

    return {
        "name": name,
        "address": address,
        "source": source,
        "games": [game],
        "website": store.get("website"),
        "external_id": external_id,
        "location_precision": "street",
        "evidence": [evidence],
    }


def discover_game_stores(
    *,
    source: str,
    game: str,
    api_url: str,
    locator_url: str,
    game_id: int,
) -> list[dict]:
    params = {
        "num_miles": SEARCH_RADIUS_MILES,
        "page": 1,
        "page_size": PAGE_SIZE,
        "latitude": MADRID_LAT,
        "longitude": MADRID_LON,
        "game_id": game_id,
    }
    try:
        resp = requests.get(api_url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    results = data.get("results", [])
    if not isinstance(results, list):
        return []

    candidates = []
    for row in results:
        if not isinstance(row, dict):
            continue
        candidate = _candidate(
            row,
            source=source,
            game=game,
            locator_url=locator_url,
        )
        if candidate:
            candidates.append(candidate)

    return candidates
