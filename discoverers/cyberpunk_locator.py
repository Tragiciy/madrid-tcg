"""
discoverers/cyberpunk_locator.py — Cyberpunk TCG store discovery.

Fetches verified Cyberpunk TCG retailers near Madrid from the public API
behind https://cyberpunktcg.com/stores. Returns [] on any network or
parsing error so the pipeline stays robust.

Events are not listed there: stores publish them on Melee.gg
(scrapers/locator_melee.py).
"""

from typing import Optional

import requests

from shared.locator_common import CITY, USER_AGENT

SOURCE = "cyberpunk_locator"
GAME = "Cyberpunk TCG"
API_URL = "https://api.netdeck.gg/api/cyberpunk/retailers/stores"
LOCATOR_URL = "https://cyberpunktcg.com/stores"
KM_PER_MILE = 1.609344
REQUEST_TIMEOUT = 30


def _candidate(store: dict) -> Optional[dict]:
    name = (store.get("storeName") or "").strip()
    address = (store.get("addressFormatted") or "").strip()
    if not name or not address:
        return None
    website = (store.get("socialWebsite") or "").strip() or None
    if website and "://" not in website:
        website = "https://" + website
    evidence = {
        "source": SOURCE,
        "type": "official_store_locator",
        "game": GAME,
        "url": LOCATOR_URL,
        "external_id": store.get("id"),
        "country": store.get("country"),
    }
    if store.get("lat") is not None and store.get("lng") is not None:
        evidence["coordinates"] = {"lat": store["lat"], "lng": store["lng"]}
    return {
        "name": name,
        "address": address,
        "source": SOURCE,
        "games": [GAME],
        "website": website,
        "external_id": store.get("id"),
        "location_precision": "street",
        "evidence": [evidence],
    }


def discover() -> list[dict]:
    try:
        resp = requests.get(API_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, params={
            "lat": CITY["lat"], "lng": CITY["lng"],
            "radius": round(CITY["radius_km"] / KM_PER_MILE),
        })
        resp.raise_for_status()
        stores = resp.json().get("stores") or []
    except (requests.RequestException, ValueError):
        return []
    return [c for c in (_candidate(s) for s in stores) if c]
