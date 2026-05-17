"""
Next Dice — https://nextdice.es/events/

Uses The Events Calendar REST API. The host returns 403 to the default bot UA,
so this scraper uses browser-like headers and then filters non-TCG events out
of the mixed store calendar.
"""

import html
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:
    from shared.scraper_keywords import (
        GAME_KEYWORDS,
        extract_best_of,
        extract_format_for_event,
        extract_game_from_keywords,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import (
        GAME_KEYWORDS,
        extract_best_of,
        extract_format_for_event,
        extract_game_from_keywords,
    )

logger = logging.getLogger(__name__)

STORE = "Next Dice"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
BASE_URL = "https://nextdice.es"
EVENTS_URL = "https://nextdice.es/events/"
DEFAULT_GAME = "Magic: The Gathering"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

MTG_FORMAT_HINTS = {
    "commander",
    "cedh",
    "edh",
    "standard",
    "pioneer",
    "modern",
    "legacy",
    "pauper",
    "vintage",
    "draft",
    "sellado",
    "sellados",
    "prerelease",
    "presentación",
    "presentacion",
}


def _api_url(days_ahead: int) -> str:
    today = datetime.now(tz=TZ).date()
    end = today + timedelta(days=days_ahead)
    return (
        f"{BASE_URL}/wp-json/tribe/events/v1/events"
        f"?per_page=50&start_date={today.isoformat()}"
        f"&end_date={end.isoformat()}&status=publish"
    )


def _strip_html(value: str) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return html.unescape(text)


def _parse_local_datetime(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return parsed.replace(tzinfo=TZ).isoformat()
    except ValueError:
        try:
            return datetime.fromisoformat(value).astimezone(TZ).isoformat()
        except ValueError:
            return None


def _category_text(raw: dict) -> str:
    categories = raw.get("categories") or []
    if not isinstance(categories, list):
        return ""
    return " ".join(
        str(cat.get("name") or "")
        for cat in categories
        if isinstance(cat, dict)
    )


def _extract_game(text: str) -> Optional[str]:
    game = extract_game_from_keywords(text, GAME_KEYWORDS)
    if game:
        return game

    lower = text.lower()
    if any(hint in lower for hint in MTG_FORMAT_HINTS):
        return DEFAULT_GAME

    return None


def _fetch_raw_events(days_ahead: int = 90) -> list[dict]:
    resp = requests.get(_api_url(days_ahead), headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    events = data.get("events") or []
    if not isinstance(events, list):
        raise ValueError(f"Unexpected events payload type: {type(events).__name__}")
    return events


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()

    try:
        raw_events = _fetch_raw_events(days_ahead=90)
    except Exception as exc:
        logger.error("%s: event API fetch failed: %s", STORE, exc)
        return []

    events: list[dict] = []
    for raw in raw_events:
        title = html.unescape((raw.get("title") or "").strip())
        description = _strip_html(raw.get("description") or "")
        category = _category_text(raw)
        combined = f"{title} {description} {category}"

        game = _extract_game(combined)
        if not game:
            continue

        start_iso = _parse_local_datetime(raw.get("start_date") or "")
        if not title or not start_iso:
            continue

        fmt, fmt_official = extract_format_for_event(
            title=title,
            description=description,
            category=category,
            game=game,
        )

        events.append(
            {
                "store": STORE,
                "game": game,
                "format": fmt,
                "format_official": fmt_official,
                "best_of": extract_best_of(combined),
                "title": title,
                "datetime_start": start_iso,
                "datetime_end": _parse_local_datetime(raw.get("end_date") or ""),
                "language": LANGUAGE,
                "source_url": raw.get("url") or EVENTS_URL,
                "scraped_at": scraped_at,
            }
        )

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
