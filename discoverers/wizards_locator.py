"""
discoverers/wizards_locator.py — Wizards Store Locator discovery.

Fetches MTG stores near Madrid from the Wizards public GraphQL endpoint
using plain requests. Returns [] on any network or parsing error so the
pipeline stays robust.
"""

import requests

_GRAPHQL_URL = "https://api.tabletop.wizards.com/silverbeak-griffin-service/graphql"

_QUERY = """
query getStoresByLocation(
    $latitude: Float!
    $longitude: Float!
    $maxMeters: Int!
    $pageSize: Int
    $page: Int
    $isPremium: Boolean
) {
    storesByLocation(
        input: {
            latitude: $latitude
            longitude: $longitude
            maxMeters: $maxMeters
            pageSize: $pageSize
            page: $page
            isPremium: $isPremium
        }
    ) {
        stores {
            id
            name
            postalAddress
            website
        }
        pageInfo {
            page
            pageSize
            totalResults
        }
    }
}
"""

# Madrid city centre (Puerta del Sol)
_MADRID_LAT = 40.4168
_MADRID_LON = -3.7038
_SEARCH_RADIUS_METERS = 50_000  # ~50 km covers greater Madrid
_PAGE_SIZE = 100


def discover() -> list[dict]:
    """
    Discover potential Madrid TCG stores from the Wizards locator.

    Each returned dict includes:
      - name (str)
      - address (str)
      - source (str): "wizards_locator"
      - games (list[str]): ["MTG"]
      - website (str | None)
      - external_id (str | None): Wizards store id
    """
    try:
        resp = requests.post(
            _GRAPHQL_URL,
            json={
                "query": _QUERY,
                "variables": {
                    "latitude": _MADRID_LAT,
                    "longitude": _MADRID_LON,
                    "maxMeters": _SEARCH_RADIUS_METERS,
                    "pageSize": _PAGE_SIZE,
                    "page": 0,
                    "isPremium": None,
                },
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        # Network or HTTP error — fail silently so the pipeline stays robust.
        return []

    stores = (
        data.get("data", {})
        .get("storesByLocation", {})
        .get("stores", [])
    )
    if not isinstance(stores, list):
        return []

    results: list[dict] = []
    for s in stores:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        address = s.get("postalAddress")
        if not name or not address:
            continue
        website = s.get("website")
        # Filter out useless placeholder URLs
        if website and "locator.wizards.com" in website.lower():
            website = None
        external_id = s.get("id")
        results.append(
            {
                "name": name,
                "address": address,
                "source": "wizards_locator",
                "games": ["MTG"],
                "website": website,
                "external_id": external_id,
            }
        )

    return results
