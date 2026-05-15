"""
discoverers/lorcana_locator.py — Disney Lorcana store discovery.

Fetches Madrid-area stores from the official Ravensburger Play API used by
the Disney Lorcana store locator.
"""

from typing import Optional

import requests

SOURCE = "lorcana_locator"
GAME = "Lorcana"
API_URL = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/game-stores/"
LOCATOR_URL = "https://tcg.ravensburgerplay.com/stores/search"
REQUEST_TIMEOUT = 30

# Madrid city centre, matching the locator's detected Madrid search.
MADRID_LAT = 40.3907
MADRID_LON = -3.6997
SEARCH_RADIUS_MILES = 25
PAGE_SIZE = 100
LORCANA_GAME_ID = 1


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


def _candidate(row: dict) -> Optional[dict]:
    store = row.get("store") or {}
    name = store.get("name")
    address = _address(store)
    if not name or not address:
        return None

    external_id = str(store.get("id") or row.get("id") or "")
    website = store.get("website")
    evidence = {
        "source": SOURCE,
        "type": "official_store_locator",
        "game": GAME,
        "url": LOCATOR_URL,
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
        "source": SOURCE,
        "games": [GAME],
        "website": website,
        "external_id": external_id,
        "location_precision": "street",
        "evidence": [evidence],
    }


def discover() -> list[dict]:
    """
    Discover Madrid-area Lorcana stores from Ravensburger Play.
    """
    params = {
        "num_miles": SEARCH_RADIUS_MILES,
        "page": 1,
        "page_size": PAGE_SIZE,
        "latitude": MADRID_LAT,
        "longitude": MADRID_LON,
        "game_id": LORCANA_GAME_ID,
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
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
        candidate = _candidate(row)
        if candidate:
            candidates.append(candidate)

    return candidates
