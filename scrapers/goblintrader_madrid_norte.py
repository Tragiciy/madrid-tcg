"""
Goblintrader Madrid-Norte — https://www.goblintrader.es/gb/norte

Custom PrestaShop site with a static event listing on each store's
sub-page. The hub page (/gb/eventos) has no events; the Madrid-Norte
store page contains the actual event cards.

Strategy:
  - GET the store page HTML directly (no JS required).
  - Parse .caja_evento containers:
      * .dianumero_evento  → day number
      * .mes_evento        → month abbreviation (Spanish)
      * .nombre_evento     → event title
  - Reconstruct the date; default time to 12:00 Europe/Madrid because
    the listing does not expose start times.
  - Extract game/format from title via shared keywords.

Notes:
  - The page uses Spanish month abbreviations: ENE, FEB, MAR, ABR,
    MAY, JUN, JUL, AGO, SEP, OCT, NOV, DIC.
  - Year is inferred from the current calendar year. If the inferred
    date falls before today, we bump the year by 1 (handles跨年).
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

STORE = "Goblintrader Madrid-Norte"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
PAGE_URL = "https://www.goblintrader.es/gb/norte"

# Fallback time when the listing does not expose an event start time.
FALLBACK_HOUR = 12
FALLBACK_MINUTE = 0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://www.goblintrader.es/gb/eventos",
}

_MONTH_MAP: dict[str, int] = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_format(text: str) -> Optional[str]:
    return extract_format_from_keywords(text, FORMAT_KEYWORDS)


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS)


def _parse_date(day_str: str, month_str: str) -> Optional[str]:
    """
    Convert day number + Spanish month abbreviation into a Madrid-tz
    ISO 8601 string (date only, with fallback time).
    """
    try:
        day = int(day_str)
        month = _MONTH_MAP.get(month_str.upper())
        if not month:
            return None
        now = datetime.now(tz=TZ)
        year = now.year
        dt = datetime(year, month, day, FALLBACK_HOUR, FALLBACK_MINUTE, 0, tzinfo=TZ)
        if dt.date() < now.date():
            dt = datetime(year + 1, month, day, FALLBACK_HOUR, FALLBACK_MINUTE, 0, tzinfo=TZ)
        return dt.isoformat()
    except Exception as exc:
        logger.debug("%s: bad date %r/%r: %s", STORE, day_str, month_str, exc)
        return None


def _parse_event(node: BeautifulSoup, scraped_at: str) -> Optional[dict]:
    """Convert one .caja_evento node into our schema."""
    # Prefer structured sub-elements
    day_node = node.select_one(".dianumero_evento")
    month_node = node.select_one(".mes_evento")
    title_node = node.select_one(".nombre_evento")

    day_str = day_node.get_text(strip=True) if day_node else None
    month_str = month_node.get_text(strip=True) if month_node else None
    title = title_node.get_text(strip=True) if title_node else None

    # Fallback: try regex on the combined .cajafechas_evento text
    if not (day_str and month_str):
        fecha_node = node.select_one(".cajafechas_evento")
        if fecha_node:
            text = fecha_node.get_text(strip=True)
            # e.g. "DOM10MAY" → extract 10 and MAY
            m = re.search(r'(\d+)([A-Z]{3})', text)
            if m:
                day_str, month_str = m.group(1), m.group(2)

    if not title:
        return None

    datetime_start = _parse_date(day_str or "", month_str or "")
    if not datetime_start:
        return None

    game = _extract_game(title)
    fmt = _extract_format(title)

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "title": title,
        "datetime_start": datetime_start,
        "datetime_end": None,
        "language": LANGUAGE,
        "source_url": PAGE_URL,
        "scraped_at": scraped_at,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    events: list[dict] = []

    try:
        resp = requests.get(PAGE_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("%s: request failed: %s", STORE, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    event_nodes = soup.select(".caja_evento")
    logger.info("%s: found %d raw event nodes", STORE, len(event_nodes))

    for node in event_nodes:
        try:
            parsed = _parse_event(node, scraped_at)
            if parsed:
                events.append(parsed)
        except Exception as exc:
            logger.warning("%s: skipping event node: %s", STORE, exc)

    # Deduplicate by (title, datetime_start, store)
    seen: dict = {}
    for ev in events:
        key = (ev["title"], ev["datetime_start"], ev["store"])
        seen.setdefault(key, ev)
    deduped = list(seen.values())
    if len(deduped) != len(events):
        logger.info("%s: dedup %d → %d (-%d)", STORE,
                    len(events), len(deduped), len(events) - len(deduped))

    deduped.sort(key=lambda e: e["datetime_start"])
    logger.info("%s: total events returned: %d", STORE, len(deduped))
    return deduped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
