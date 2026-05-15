"""
discoverers/yugioh_locator.py — Yu-Gi-Oh! OTS discovery.

Fetches Official Tournament Stores near Madrid from Konami Europe's official
store locator JSON endpoint.
"""

from typing import Optional

import requests

SOURCE = "yugioh_locator"
GAME = "Yu-Gi-Oh!"
API_URL = "https://www.yugioh-card.com/eu/_store-locator/store-locator-get-info.php"
LOCATOR_URL = "https://www.yugioh-card.com/eu/play/store-locator/"
REQUEST_TIMEOUT = 30

# Madrid city centre. Use 50 km to match the existing Wizards locator scope.
MADRID_LAT = 40.4168
MADRID_LON = -3.7038
SEARCH_RADIUS_KM = 50
STORE_TYPE = "Official Tournament Stores"


def _address(row: dict) -> str:
    return row.get("Address_string") or ""


def _coordinates(row: dict) -> Optional[dict]:
    lat = row.get("Latitude")
    lng = row.get("Longitude")
    if lat is None or lng is None:
        return None
    return {"lat": lat, "lng": lng}


def _candidate(row: dict) -> Optional[dict]:
    name = row.get("Store Name")
    address = _address(row)
    if not name or not address:
        return None

    external_id = row.get("Store Code") or ""
    evidence = {
        "source": SOURCE,
        "type": "official_store_locator",
        "game": GAME,
        "url": LOCATOR_URL,
        "external_id": external_id,
        "city": row.get("City"),
        "country": row.get("Country/Region"),
        "store_category": row.get("Store Category"),
        "area": row.get("Area"),
        "distance": row.get("distance"),
    }
    coordinates = _coordinates(row)
    if coordinates:
        evidence["coordinates"] = coordinates

    return {
        "name": name,
        "address": address,
        "source": SOURCE,
        "games": [GAME],
        "website": None,
        "external_id": external_id,
        "location_precision": "street",
        "evidence": [evidence],
    }


def discover() -> list[dict]:
    params = {
        "lat": MADRID_LAT,
        "lng": MADRID_LON,
        "radius": SEARCH_RADIUS_KM,
        "type": STORE_TYPE,
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    candidates = []
    for row in data:
        if not isinstance(row, dict):
            continue
        candidate = _candidate(row)
        if candidate:
            candidates.append(candidate)

    return candidates
