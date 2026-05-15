"""
discoverers/one_piece_events.py — One Piece Card Game discovery.

Fetches upcoming One Piece events near Madrid from Bandai TCG+'s public event
list API and returns one candidate per organizer with event evidence.
"""

from datetime import datetime, timezone
from typing import Optional

import requests

from shared.store_matching import normalize_address, normalize_name

SOURCE = "one_piece_events"
GAME = "One Piece"
API_URL = "https://api.bandai-tcg-plus.com/api/user/event/list"
LOCATOR_URL = "https://www.bandai-tcg-plus.com/"
REQUEST_TIMEOUT = 30

# Madrid city centre. Use 50 km to match the existing Wizards locator scope.
MADRID_LAT = 40.4168
MADRID_LON = -3.7038
SEARCH_RADIUS_KM = 50
PAGE_SIZE = 100
MAX_PAGES = 5
ONE_PIECE_GAME_TITLE_ID = 4

HEADERS = {
    "Origin": "https://www.bandai-tcg-plus.com",
    "Referer": "https://www.bandai-tcg-plus.com/",
}


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _address(row: dict) -> str:
    parts = [
        row.get("street_address"),
        row.get("city_code"),
        row.get("postcode"),
        row.get("country_code"),
    ]
    return ", ".join(str(part).strip() for part in parts if part)


def _coordinates(row: dict) -> Optional[dict]:
    geo = row.get("event_place_geo") or row.get("place_geo") or {}
    lat = geo.get("x")
    lng = geo.get("y")
    if lat is None or lng is None:
        return None
    return {"lat": lat, "lng": lng}


def _event_evidence(row: dict) -> dict:
    evidence = {
        "source": SOURCE,
        "type": "official_event_locator",
        "game": GAME,
        "url": LOCATOR_URL,
        "external_id": str(row.get("organizer_id") or ""),
        "event_id": row.get("id"),
        "event_series_id": row.get("event_series_id"),
        "event_series_title": row.get("event_series_title"),
        "event_date": row.get("start_datetime"),
        "city": row.get("city_code"),
        "country": row.get("country_code"),
        "distance": row.get("distance"),
    }
    coordinates = _coordinates(row)
    if coordinates:
        evidence["coordinates"] = coordinates
    return evidence


def _merge_event(stores: dict[str, dict], row: dict) -> None:
    organizer_id = str(row.get("organizer_id") or "")
    name = str(row.get("organizer_name") or "").strip()
    address = _address(row)
    if not organizer_id or not name or not address:
        return

    coordinates = _coordinates(row)
    if coordinates:
        location_key = f"{coordinates['lat']:.6f},{coordinates['lng']:.6f}"
    else:
        location_key = normalize_address(address)
    key = f"{normalize_name(name)}|{location_key}"
    if key not in stores:
        stores[key] = {
            "name": name,
            "address": address,
            "source": SOURCE,
            "games": [GAME],
            "website": row.get("organizer_url") or None,
            "external_id": organizer_id,
            "location_precision": "street",
            "evidence": [],
        }

    stores[key]["evidence"].append(_event_evidence(row))


def _fetch_page(offset: int) -> Optional[dict]:
    params = {
        "limit": PAGE_SIZE,
        "offset": offset,
        "game_title_id": ONE_PIECE_GAME_TITLE_ID,
        "current_lat": MADRID_LAT,
        "current_lng": MADRID_LON,
        "distance": SEARCH_RADIUS_KM,
        "start_date": _today_iso(),
    }
    try:
        resp = requests.get(
            API_URL,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    success = data.get("success")
    return success if isinstance(success, dict) else None


def discover() -> list[dict]:
    stores: dict[str, dict] = {}
    total = None

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        if total is not None and offset >= total:
            break

        success = _fetch_page(offset)
        if not success:
            break

        total = int(success.get("total") or 0)
        events = success.get("event_list", [])
        if not isinstance(events, list) or not events:
            break

        for row in events:
            if isinstance(row, dict):
                _merge_event(stores, row)

    return list(stores.values())
