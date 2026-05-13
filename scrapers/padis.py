"""
Padis — https://www.padis-store.com/284-inscripciones-torneos

PrestaShop site listing tournaments as product cards. Events have no
dedicated date fields; dates and times are embedded in the product title.

Date formats seen in titles:
  "Torneo X (Domingo 17 de Mayo 2026, 17:00)"
  "Torneo Y Domingo 28/06 11:00"
  "Torneo Z Sábado 24/05 11:00"

Strategy:
  - GET the listing page (server-rendered, no JS).
  - Parse article.product-miniature elements.
  - Extract title from h2.product-title (or product link text).
  - Detect date with two regex patterns; skip products with no date.
"""

import json
import locale
import logging
import re
import sys
from datetime import datetime, date
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

STORE = "Padis"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
PAGE_URL = "https://www.padis-store.com/284-inscripciones-torneos"

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
}

_MONTH_ES: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# "17 de Mayo 2026, 17:00"  or  "(Domingo 17 de Mayo 2026, 17:00)"
_LONG_DATE_RE = re.compile(
    r'(\d{1,2})\s+de\s+(\w+)\s+(\d{4})[,\s]+(\d{1,2}):(\d{2})',
    re.IGNORECASE,
)
# "Domingo 28/06 11:00" or "Sábado 24/05 11:00" (no year)
_SHORT_DATE_RE = re.compile(
    r'(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+(\d{1,2})/(\d{2})\s+(\d{1,2}):(\d{2})',
    re.IGNORECASE,
)


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS)


def _parse_datetime(title: str) -> Optional[str]:
    # Pattern 1: full date with year and Spanish month name
    m = _LONG_DATE_RE.search(title)
    if m:
        day, month_name, year, hour, minute = (
            int(m.group(1)), m.group(2).lower(), int(m.group(3)),
            int(m.group(4)), int(m.group(5)),
        )
        month = _MONTH_ES.get(month_name)
        if month:
            try:
                return datetime(year, month, day, hour, minute, tzinfo=TZ).isoformat()
            except ValueError:
                pass

    # Pattern 2: short date DD/MM HH:MM (no year → infer current/next year)
    m = _SHORT_DATE_RE.search(title)
    if m:
        day, month, hour, minute = (
            int(m.group(1)), int(m.group(2)),
            int(m.group(3)), int(m.group(4)),
        )
        now = datetime.now(tz=TZ)
        year = now.year
        try:
            dt = datetime(year, month, day, hour, minute, tzinfo=TZ)
            if dt.date() < now.date():
                dt = datetime(year + 1, month, day, hour, minute, tzinfo=TZ)
            return dt.isoformat()
        except ValueError:
            pass

    return None


def _parse_event(article, scraped_at: str) -> Optional[dict]:
    title_el = article.select_one("h2.product-title a, h2.product-title, .product-title a")
    if not title_el:
        title_el = article.select_one("a.thumbnail")
    title = (title_el.get_text(strip=True) if title_el else "").strip()
    if not title:
        return None

    href_el = article.select_one("a.thumbnail.product-thumbnail, h2.product-title a")
    href = href_el.get("href") if href_el else None

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
    articles = soup.select("article.product-miniature")
    logger.info("%s: found %d product cards", STORE, len(articles))

    for article in articles:
        try:
            parsed = _parse_event(article, scraped_at)
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
