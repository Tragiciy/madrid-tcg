"""
Generacion X - Elfo — https://genexcomics.com/eventos/

The site uses a WordPress custom post type "evento" with ACF meta fields.
A public REST API endpoint exposes upcoming events with clean structured data.

Strategy:
  - GET /wp-json/wp/v2/evento?per_page=100
  - Map each event to our schema using ACF meta fields:
      * fecha_del_evento → Unix timestamp (date)
      * hora_del_evento  → "HH:MM" string (time)
  - Combine into Europe/Madrid ISO 8601 datetime.
  - Extract game/format from title + content via shared keywords.

Notes:
  - The API returns publish dates in UTC; meta timestamps are also UTC.
    We convert fecha_del_evento to Europe/Madrid local time and overlay
    the wall-clock hour/minute from hora_del_evento.
  - Only published (status=publish) events are returned by default.
"""

import html as _html
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
        GAME_KEYWORDS,
        FORMAT_KEYWORDS,
        extract_game_from_keywords,
        extract_format_from_keywords,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import (
        GAME_KEYWORDS,
        FORMAT_KEYWORDS,
        extract_game_from_keywords,
        extract_format_from_keywords,
    )

logger = logging.getLogger(__name__)

STORE = "Generacion X - Elfo"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
API_URL = "https://genexcomics.com/wp-json/wp/v2/evento"
PER_PAGE = 100

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "application/json",
    "Referer": "https://genexcomics.com/eventos/",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _datetime_iso(timestamp: int, time_str: str) -> Optional[str]:
    """
    Build a Madrid-tz ISO 8601 string from a Unix timestamp (date)
    and a "HH:MM" time string.
    """
    try:
        base = datetime.fromtimestamp(timestamp, tz=TZ)
        hour, minute = map(int, (time_str or "00:00").split(":"))
        dt = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return dt.isoformat()
    except Exception as exc:
        logger.debug("%s: bad date/time %r / %r: %s", STORE, timestamp, time_str, exc)
        return None


def _extract_format(text: str) -> Optional[str]:
    return extract_format_from_keywords(text, FORMAT_KEYWORDS)


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS)


def _parse_event(raw: dict, scraped_at: str) -> Optional[dict]:
    """Convert one /wp-json/wp/v2/evento item into our schema."""
    meta = raw.get("meta") or {}
    fecha_ts = meta.get("fecha_del_evento")
    hora_str = meta.get("hora_del_evento")
    if not fecha_ts:
        return None

    datetime_start = _datetime_iso(fecha_ts, hora_str)
    if not datetime_start:
        return None

    raw_title = _html.unescape((raw.get("title") or {}).get("rendered") or "").strip()
    source_url = raw.get("link") or None

    # Game + format from title + content text
    content_html = (raw.get("content") or {}).get("rendered") or ""
    content_text = (
        BeautifulSoup(content_html, "html.parser").get_text(" ", strip=True)
        if content_html else ""
    )
    combined_text = f"{raw_title} {content_text}"

    game = _extract_game(combined_text)
    fmt = _extract_format(combined_text)

    # Clean up title — strip HTML entities and collapse whitespace
    title = re.sub(r"\s+", " ", raw_title).strip()
    if not title:
        return None

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "title": title,
        "datetime_start": datetime_start,
        "datetime_end": None,
        "language": LANGUAGE,
        "source_url": source_url,
        "scraped_at": scraped_at,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    events: list[dict] = []

    try:
        resp = requests.get(
            API_URL,
            params={"per_page": PER_PAGE},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("%s: request failed: %s", STORE, exc)
        return []

    try:
        items = resp.json()
    except Exception as exc:
        logger.error("%s: JSON decode failed: %s", STORE, exc)
        return []

    if not isinstance(items, list):
        logger.error("%s: unexpected response type %s", STORE, type(items).__name__)
        return []

    for raw in items:
        try:
            parsed = _parse_event(raw, scraped_at)
            if parsed:
                events.append(parsed)
        except Exception as exc:
            logger.warning("%s: skipping id=%s: %s", STORE, raw.get("id"), exc)

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
