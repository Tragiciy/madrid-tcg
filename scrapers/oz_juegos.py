"""
OZ Juegos — https://ozjuegos.com/categoria-producto/eventos/eventosfleshandblood/

WooCommerce store with game-specific event product categories. The current
Flesh and Blood event category is empty, but this scraper is wired to the
category API so published event products are picked up when they appear.
"""

import html
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:
    from shared.scraper_keywords import (
        extract_best_of,
        extract_format_for_event,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import (
        extract_best_of,
        extract_format_for_event,
    )

logger = logging.getLogger(__name__)

STORE = "OZ JUEGOS"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
DEFAULT_GAME = "Flesh and Blood"

EVENT_CATEGORY_ID = 236
CATEGORY_URL = "https://ozjuegos.com/categoria-producto/eventos/eventosfleshandblood/"
PRODUCTS_API_URL = (
    "https://ozjuegos.com/wp-json/wc/store/v1/products"
    f"?category={EVENT_CATEGORY_ID}&per_page=50"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

NUMERIC_DATE_RE = re.compile(r"(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?")
SPANISH_DATE_RE = re.compile(
    r"(\d{1,2})\s+(?:de\s+)?([a-záéíóúñ]+)(?:\s+(?:de\s+)?(\d{4}))?",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})|(\d{1,2})\s*h\b", re.IGNORECASE)


def _strip_html(value: str) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return html.unescape(text)


def _parse_date(text: str, default_year: int) -> Optional[date]:
    match = NUMERIC_DATE_RE.search(text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3) or default_year)
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            pass

    match = SPANISH_DATE_RE.search(text)
    if match:
        day = int(match.group(1))
        month = SPANISH_MONTHS.get(match.group(2).lower())
        year = int(match.group(3) or default_year)
        if not month:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


def _parse_time(text: str) -> tuple[int, int]:
    match = TIME_RE.search(text)
    if not match:
        return (11, 0)
    if match.group(1) and match.group(2):
        return int(match.group(1)), int(match.group(2))
    return int(match.group(3)), 0


def _fetch_products() -> list[dict]:
    resp = requests.get(PRODUCTS_API_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected products payload type: {type(data).__name__}")
    return data


def _roll_forward_if_yearless(event_date: date) -> date:
    today = datetime.now(tz=TZ).date()
    cutoff = today + timedelta(days=90)
    if event_date >= today:
        return event_date

    try:
        next_year_date = event_date.replace(year=today.year + 1)
    except ValueError:
        return event_date

    if next_year_date <= cutoff:
        return next_year_date
    return event_date


def _build_event(product: dict, scraped_at: str) -> Optional[dict]:
    title = html.unescape(product.get("name") or "").strip()
    description = _strip_html(
        " ".join(
            [
                product.get("short_description") or "",
                product.get("description") or "",
            ]
        )
    )
    combined = f"{title} {description}"
    event_date = _parse_date(combined, datetime.now(tz=TZ).year)
    if not title or not event_date:
        return None

    event_date = _roll_forward_if_yearless(event_date)

    hour, minute = _parse_time(combined)
    dt = datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        hour,
        minute,
        tzinfo=TZ,
    )

    fmt, fmt_official = extract_format_for_event(
        title=title,
        description=description,
        game=DEFAULT_GAME,
    )

    return {
        "store": STORE,
        "game": DEFAULT_GAME,
        "format": fmt,
        "format_official": fmt_official,
        "best_of": extract_best_of(combined),
        "title": title,
        "datetime_start": dt.isoformat(),
        "datetime_end": None,
        "language": LANGUAGE,
        "source_url": product.get("permalink") or CATEGORY_URL,
        "scraped_at": scraped_at,
    }


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    today = datetime.now(tz=TZ).date()
    cutoff = today + timedelta(days=90)

    try:
        products = _fetch_products()
    except Exception as exc:
        logger.error("%s: event product fetch failed: %s", STORE, exc)
        return []

    events: list[dict] = []
    for product in products:
        event = _build_event(product, scraped_at)
        if not event:
            continue

        event_date = datetime.fromisoformat(event["datetime_start"]).date()
        if today <= event_date <= cutoff:
            events.append(event)

    seen: dict = {}
    for ev in events:
        key = (ev["title"], ev["datetime_start"], ev["store"])
        seen.setdefault(key, ev)
    deduped = list(seen.values())

    deduped.sort(key=lambda e: e["datetime_start"])
    logger.info("%s: total events returned: %d", STORE, len(deduped))
    return deduped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
