"""
MADAKIBA — https://madakiba.com/es/c/eventos

Custom e-commerce platform. Events are product cards at /es/c/eventos.
Dates and times are embedded in product titles:
  "TORNEO JONIN NARUTO TCG 23.05 A LAS 17H"
  "PRESENTACION POKEMON TCG ... 24.05 A LAS 11H"

Date format: DD.MM (day.month, current year inferred).
Time format: "A LAS HH:MM" or "A LAS HHH" (e.g. "17H" → 17:00).
Products without a numeric date are skipped (e.g. "CALENDARIO MAYO 2026").
"""

import json
import logging
import re
import sys
from datetime import datetime
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
        extract_format_for_event,
        extract_best_of,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import (
        FORMAT_KEYWORDS,
        GAME_KEYWORDS,
        extract_format_from_keywords,
        extract_game_from_keywords,
        extract_format_for_event,
        extract_best_of,
    )

logger = logging.getLogger(__name__)

STORE = "MADAKIBA"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
PAGE_URL = "https://madakiba.com/es/c/eventos"
DEFAULT_GAME = "Star Wars: Unlimited"

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
}

# "23.05" → day=23, month=5
_DATE_RE = re.compile(r'\b(\d{1,2})\.(\d{2})\b')
# "A LAS 17:30" or "A LAS 17H" or "DE 17H"
_TIME_RE = re.compile(r'(?:A\s+LAS|DE)\s+(\d{1,2})(?::(\d{2}))?H?', re.IGNORECASE)


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS) or DEFAULT_GAME


def _parse_datetime(title: str) -> Optional[str]:
    date_m = _DATE_RE.search(title)
    if not date_m:
        return None

    day, month = int(date_m.group(1)), int(date_m.group(2))
    now = datetime.now(tz=TZ)
    year = now.year

    time_m = _TIME_RE.search(title)
    hour = int(time_m.group(1)) if time_m else 12
    minute = int(time_m.group(2)) if (time_m and time_m.group(2)) else 0

    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=TZ)
        if dt.date() < now.date():
            dt = datetime(year + 1, month, day, hour, minute, tzinfo=TZ)
        return dt.isoformat()
    except ValueError:
        return None


def _parse_event(product_el, scraped_at: str) -> Optional[dict]:
    # The title link has text; the image link has empty text — pick the one with content
    link = None
    for a in product_el.find_all("a", href=True):
        if a.get("href", "").startswith("/es/p/") and a.get_text(strip=True):
            link = a
            break
    if not link:
        # Fallback: any /es/p/ link (img link), grab title from h3/h2
        link = product_el.select_one("a[href^='/es/p/']")
    if not link:
        return None

    title_el = product_el.select_one("h3, h2, .product-title")
    title = (title_el.get_text(strip=True) if title_el else link.get_text(strip=True)).strip()
    href = link.get("href", "")
    if href and not href.startswith("http"):
        href = "https://madakiba.com" + href

    if not title:
        return None

    datetime_start = _parse_datetime(title)
    if not datetime_start:
        return None

    game = _extract_game(title)
    fmt, fmt_official = extract_format_for_event(title=title, game=game)

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "format_official": fmt_official,
        "best_of": extract_best_of(title),
        "title": title,
        "datetime_start": datetime_start,
        "datetime_end": None,
        "language": LANGUAGE,
        "source_url": href or PAGE_URL,
        "scraped_at": scraped_at,
    }


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    events: list[dict] = []

    try:
        resp = requests.get(PAGE_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("%s: request failed: %s", STORE, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    products = soup.select("div.product")
    logger.info("%s: found %d product cards", STORE, len(products))

    for product in products:
        try:
            parsed = _parse_event(product, scraped_at)
            if parsed:
                events.append(parsed)
        except Exception as exc:
            logger.warning("%s: skipping product: %s", STORE, exc)

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
