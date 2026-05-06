"""
Panda Games — https://pandagames.es/juegos/eventos/

The site uses a WooCommerce product grid for events. Dates are often embedded
in product titles (e.g. "Torneo Naruto Mythos 24-05-2026"), but some events
only show the date inside the product page description
(e.g. "sábado 14 de Junio a las 10h").

Strategy:
  1. Fetch the event listing page.
  2. Extract product URLs and clean titles.
  3. For each product, fetch the detail page and extract date + time.
  4. Filter to future events (today..today+90).
  5. Map to our schema with game/format keyword detection.
"""

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
        FORMAT_KEYWORDS,
        GAME_KEYWORDS,
        extract_format_from_keywords,
        extract_game_from_keywords,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import (
        FORMAT_KEYWORDS,
        GAME_KEYWORDS,
        extract_format_from_keywords,
        extract_game_from_keywords,
    )

logger = logging.getLogger(__name__)

STORE = "Panda Games"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
LISTING_URL = "https://pandagames.es/juegos/eventos/"
DEFAULT_GAME = "Magic: The Gathering"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

_SPANISH_MONTHS = {
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

_NUMERIC_DATE_RE = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})")
_SPANISH_DATE_RE = re.compile(
    r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)(?:\s+de\s+(\d{4}))?",
    re.IGNORECASE,
)

_TIME_RE = re.compile(r"a las (\d{1,2})[:.](\d{2})h")
_TIME_RE_HOUR_ONLY = re.compile(r"a las (\d{1,2})h")


def _parse_date(text: str, default_year: int) -> Optional[date]:
    """Extract date from text. Returns None if not found."""
    m = _NUMERIC_DATE_RE.search(text)
    if m:
        d, mon, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mon, d)
        except ValueError:
            pass

    m = _SPANISH_DATE_RE.search(text)
    if m:
        d = int(m.group(1))
        month_name = m.group(2).lower()
        year_str = m.group(3)
        month_num = _SPANISH_MONTHS.get(month_name)
        if month_num:
            y = int(year_str) if year_str else default_year
            try:
                return date(y, month_num, d)
            except ValueError:
                pass
    return None


def _parse_time(text: str) -> Optional[tuple[int, int]]:
    m = _TIME_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _TIME_RE_HOUR_ONLY.search(text)
    if m:
        return int(m.group(1)), 0
    return None


def _fetch_event_detail(url: str) -> Optional[tuple[date, tuple[int, int], str]]:
    """Fetch product page and return (event_date, (hour, minute), desc_text) or None."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        # Use description text for game/format extraction to avoid nav-menu noise
        desc_el = soup.select_one(
            ".woocommerce-product-details__short-description, "
            "#tab-description, .entry-summary"
        )
        desc_text = desc_el.get_text(" ", strip=True) if desc_el else page_text

        default_year = datetime.now(tz=TZ).year

        # Try title first for date
        title = ""
        title_el = soup.select_one("h1.product-title, h1.entry-title")
        if title_el:
            title = title_el.get_text(" ", strip=True)

        event_date = _parse_date(title, default_year) or _parse_date(page_text, default_year)
        if not event_date:
            return None

        event_time = _parse_time(page_text) or _parse_time(title)
        if not event_time:
            event_time = (11, 0)

        return event_date, event_time, desc_text
    except Exception as exc:
        logger.debug("%s: error fetching %s: %s", STORE, url, exc)
        return None


def _extract_game(title: str, page_text: str = "") -> Optional[str]:
    combined = f"{title} {page_text}"
    if "pre-rift" in combined.lower():
        return "Riftbound"
    return extract_game_from_keywords(combined, GAME_KEYWORDS) or DEFAULT_GAME


def _extract_format(title: str) -> Optional[str]:
    return extract_format_from_keywords(title, FORMAT_KEYWORDS)


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    today = datetime.now(tz=TZ).date()
    cutoff = today + timedelta(days=90)

    try:
        resp = requests.get(LISTING_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("%s: listing page failed: %s", STORE, exc)
        raise RuntimeError(f"{STORE}: could not fetch event listing") from exc

    soup = BeautifulSoup(resp.text, "html.parser")
    products = soup.select("ul.products li.product")
    if not products:
        products = soup.select("li.product.type-product")

    product_links: list[tuple[str, str]] = []
    for prod in products:
        link = prod.select_one("a.woocommerce-LoopProduct-link") or prod.select_one("a")
        if not link:
            continue
        href = link.get("href")
        # Clean title from h2 if available, otherwise strip price from link text
        title_el = prod.select_one("h2.woocommerce-loop-product__title")
        if title_el:
            title = title_el.get_text(" ", strip=True)
        else:
            title = link.get_text(" ", strip=True)
        if not title or not href:
            continue
        product_links.append((title, href))

    events: list[dict] = []
    for title, url in product_links:
        detail = _fetch_event_detail(url)
        if not detail:
            continue
        event_date, (hour, minute), page_text = detail
        if not (today <= event_date <= cutoff):
            continue

        dt = datetime(
            event_date.year, event_date.month, event_date.day,
            hour, minute, tzinfo=TZ,
        )

        fmt = _extract_format(title)
        game = _extract_game(title, page_text)

        events.append(
            {
                "store": STORE,
                "game": game,
                "format": fmt,
                "title": title,
                "datetime_start": dt.isoformat(),
                "datetime_end": None,
                "language": LANGUAGE,
                "source_url": url,
                "scraped_at": scraped_at,
            }
        )

    # Deduplicate
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
