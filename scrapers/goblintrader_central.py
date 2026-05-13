"""
Goblintrader Central — https://www.goblintrader.es/gb/madrid

Custom PrestaShop site. Identical structure to goblintrader_madrid_norte.py:
  .caja_evento → .dianumero_evento / .mes_evento / .nombre_evento
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

STORE = "Goblintrader Central"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
PAGE_URL = "https://www.goblintrader.es/gb/madrid"

FALLBACK_HOUR = 12
FALLBACK_MINUTE = 0

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://www.goblintrader.es/gb/eventos",
}

_MONTH_MAP: dict[str, int] = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS)


def _parse_date(day_str: str, month_str: str) -> Optional[str]:
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
    day_node = node.select_one(".dianumero_evento")
    month_node = node.select_one(".mes_evento")
    title_node = node.select_one(".nombre_evento")

    day_str = day_node.get_text(strip=True) if day_node else None
    month_str = month_node.get_text(strip=True) if month_node else None
    title = title_node.get_text(strip=True) if title_node else None

    if not (day_str and month_str):
        fecha_node = node.select_one(".cajafechas_evento")
        if fecha_node:
            m = re.search(r'(\d+)([A-Z]{3})', fecha_node.get_text(strip=True))
            if m:
                day_str, month_str = m.group(1), m.group(2)

    if not title:
        return None

    datetime_start = _parse_date(day_str or "", month_str or "")
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
        "source_url": PAGE_URL,
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
    event_nodes = soup.select(".caja_evento")
    logger.info("%s: found %d raw event nodes", STORE, len(event_nodes))

    for node in event_nodes:
        try:
            parsed = _parse_event(node, scraped_at)
            if parsed:
                events.append(parsed)
        except Exception as exc:
            logger.warning("%s: skipping event node: %s", STORE, exc)

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
