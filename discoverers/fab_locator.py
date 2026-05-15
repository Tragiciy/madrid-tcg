"""
discoverers/fab_locator.py — Flesh and Blood store discovery.

Fetches Madrid-area stores from the public Carde Play Network endpoint used
by the Flesh and Blood Play Network. No network requests are performed on
import.
"""

from typing import Optional

import requests

SOURCE = "fab_locator"
GAME = "Flesh and Blood"
API_URL = "https://api.carde.io/api/play/establishments"
LOCATOR_URL = "https://play.carde.io/stores/search"
FAB_GAME_ID = "1ceb00e9-28ea-45ac-8dfb-c84957f75ed1"

MADRID_LAT = 40.3907
MADRID_LON = -3.6997
SEARCH_RADIUS_KM = 50
REQUEST_TIMEOUT = 30


def _address(address: dict) -> str:
    parts = [
        address.get("address1"),
        address.get("address2"),
        address.get("city"),
        address.get("state"),
        address.get("zip"),
        address.get("country"),
    ]
    return ", ".join(str(part).strip() for part in parts if part)


def _coordinates(address: dict) -> Optional[dict]:
    geo = address.get("geo") or {}
    lat = geo.get("lat")
    lng = geo.get("lng")
    if lat is None or lng is None:
        return None
    return {"lat": lat, "lng": lng}


def _candidate(row: dict) -> Optional[dict]:
    name = row.get("name")
    address = row.get("address") or {}
    full_address = _address(address)
    if not name or not full_address:
        return None

    external_id = str(row.get("id") or "")
    game_establishment = row.get("gameEstablishment") or {}
    contact = row.get("contact") or {}
    applications = row.get("applications") or {}

    evidence = {
        "source": SOURCE,
        "type": "official_store_locator",
        "game": GAME,
        "url": LOCATOR_URL,
        "external_id": external_id,
        "game_id": FAB_GAME_ID,
        "game_establishment_id": game_establishment.get("id"),
        "store_number": row.get("storeNumber"),
        "carries_product": game_establishment.get("carriesProduct"),
        "organized_play_status": (
            (game_establishment.get("applications") or {})
            .get("organizedPlay", {})
            .get("status")
        ),
        "retailer_status": (
            (game_establishment.get("applications") or {})
            .get("retailer", {})
            .get("status")
        ),
        "physical_retailer_status": (
            applications.get("physicalRetailer", {}).get("status")
        ),
        "country": address.get("country"),
        "city": address.get("city"),
        "distance": address.get("distance"),
    }
    coordinates = _coordinates(address)
    if coordinates:
        evidence["coordinates"] = coordinates

    return {
        "name": name,
        "address": full_address,
        "source": SOURCE,
        "games": [GAME],
        "website": contact.get("website"),
        "external_id": external_id,
        "location_precision": "street",
        "evidence": [evidence],
    }


def discover() -> list[dict]:
    params = {
        "lat": MADRID_LAT,
        "lng": MADRID_LON,
        "radius": SEARCH_RADIUS_KM,
    }
    headers = {"game-id": FAB_GAME_ID}
    try:
        resp = requests.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    rows = data.get("data", [])
    if not isinstance(rows, list):
        return []

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = _candidate(row)
        if candidate:
            candidates.append(candidate)

    return candidates
