"""
discoverers/swu_calendar.py — Star Wars: Unlimited organized play discovery.

Fetches Spain/Madrid store entries from the official Organized Play Calendar.
The calendar is event-oriented rather than a full store locator, so addresses
are city-level. This is still useful provenance for matching known stores and
surfacing Madrid-area SWU candidates for review.
"""

from urllib.parse import parse_qs, urlparse
from typing import Optional

import requests
from bs4 import BeautifulSoup

SOURCE = "swu_calendar"
GAME = "Star Wars: Unlimited"
CALENDAR_URL = "https://starwarsunlimited.com/organized-play-calendar"
REQUEST_TIMEOUT = 30

# Keep scope aligned with the Madrid project. SWU's calendar exposes city,
# region, country columns; it does not expose street addresses in this HTML.
TARGET_COUNTRY = "Spain"
TARGET_REGION = "Madrid"


def _store_id(url: str) -> Optional[str]:
    if not url:
        return None
    values = parse_qs(urlparse(url).query).get("store", [])
    return values[0] if values else None


def _address(city: str, region: str, country: str) -> str:
    parts = [city, region, country]
    return ", ".join(part for part in parts if part)


def _merge_store(stores: dict[str, dict], row: dict) -> None:
    key = row["external_id"] or row["name"].casefold()
    if key not in stores:
        stores[key] = {
            "name": row["name"],
            "address": row["address"],
            "source": SOURCE,
            "games": [GAME],
            "website": row["url"],
            "external_id": row["external_id"],
            "location_precision": "city",
            "evidence": [],
        }

    stores[key]["evidence"].append(
        {
            "source": SOURCE,
            "type": "official_event_calendar",
            "game": GAME,
            "url": row["url"],
            "external_id": row["external_id"],
            "event_date": row["event_date"],
            "event_type": row["event_type"],
            "city": row["city"],
            "region": row["region"],
            "country": row["country"],
        }
    )


def discover() -> list[dict]:
    """
    Discover Madrid-area SWU stores from the official Organized Play Calendar.

    Returns one candidate per store. If a store appears in multiple calendar
    rows, all rows are preserved as evidence.
    """
    try:
        resp = requests.get(CALENDAR_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    stores: dict[str, dict] = {}

    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 6:
            continue

        event_date, name, event_type, city, region, country = cells[:6]
        if country != TARGET_COUNTRY or region != TARGET_REGION:
            continue

        link = tr.find("a", href=True)
        if not link:
            continue

        url = link["href"]
        external_id = _store_id(url)
        row = {
            "name": name,
            "address": _address(city, region, country),
            "url": url,
            "external_id": external_id,
            "event_date": event_date,
            "event_type": event_type,
            "city": city,
            "region": region,
            "country": country,
        }
        _merge_store(stores, row)

    return list(stores.values())
