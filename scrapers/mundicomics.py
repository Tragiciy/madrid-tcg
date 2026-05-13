"""
Mundicomics — https://www.mundicomics.com/torneos-tcg

Oleoshop platform. The tournament page shows 3 weeks of recurring events in a
custom calendar grid:

  mc-week-grid-expanded
    mc-day-card
      mc-day-label
        mc-day-name  → "LUNES" / "MARTES" / … / "CERRADO"
        mc-day-num   → day-of-month int ("11", "12", …)
      mc-events
        mc-event (a or div)
          mc-event-game  → "TCG One Piece"
          mc-event-mode  → "BO1" / "BO3"
          mc-event-details
            mc-detail-time  → "17:30"

Month context comes from the first mc-week-dates element on the page
(text like "11 - 17 de mayo de 2026"). Fallback: infer from day numbers
relative to today.
"""

import json
import logging
import re
import sys
from datetime import datetime, date, timedelta
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

STORE = "Mundicomics"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
PAGE_URL = "https://www.mundicomics.com/torneos-tcg"

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.mundicomics.com/",
}

_MONTH_ES: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# "11 - 17 de mayo de 2026" or "11-17 mayo 2026"
_WEEK_HEADER_RE = re.compile(
    r'(\d{1,2})\s*[-–]\s*\d{1,2}\s+(?:de\s+)?(\w+)\s+(?:de\s+)?(\d{4})',
    re.IGNORECASE,
)

# mc-tag-{game} class → game hint
_TAG_GAME_MAP = {
    "onepiece": "One Piece",
    "one-piece-tcg": "One Piece",
    "magic": "Magic: The Gathering",
    "mtg": "Magic: The Gathering",
    "pokemon": "Pokémon",
    "riftbound": "Riftbound",
    "digimon": "Digimon",
    "lorcana": "Lorcana",
    "yugioh": "Yu-Gi-Oh!",
    "fab": "Flesh and Blood",
    "starwars": "Star Wars: Unlimited",
}


def _extract_game(text: str, tag_classes: list[str]) -> Optional[str]:
    # Try keyword extraction first
    game = extract_game_from_keywords(text, GAME_KEYWORDS)
    if game:
        return game
    # Fallback: mc-tag-* CSS class on the event element
    for cls in tag_classes:
        for tag_key, game_name in _TAG_GAME_MAP.items():
            if tag_key in cls.lower():
                return game_name
    return None


def _parse_week_month_year(week_el) -> tuple[Optional[int], Optional[int]]:
    """Extract (month, year) from a mc-week-dates or mc-week-label element."""
    if not week_el:
        return None, None
    text = week_el.get_text(" ", strip=True)
    m = _WEEK_HEADER_RE.search(text)
    if m:
        month = _MONTH_ES.get(m.group(2).lower())
        year = int(m.group(3))
        return month, year
    return None, None


def _infer_date(day_num: int, hint_month: Optional[int], hint_year: Optional[int]) -> Optional[date]:
    """
    Build a date from day_num + optional month/year hint.
    If no hint, use current month; bump to next month if day already passed.
    """
    today = date.today()
    if hint_month and hint_year:
        try:
            return date(hint_year, hint_month, day_num)
        except ValueError:
            return None
    # Fallback: infer
    year = today.year
    month = today.month
    try:
        d = date(year, month, day_num)
        if d < today:
            # Try next month
            if month == 12:
                d = date(year + 1, 1, day_num)
            else:
                d = date(year, month + 1, day_num)
        return d
    except ValueError:
        return None


def _parse_week(week_el, scraped_at: str) -> list[dict]:
    """Parse all events within one mc-week-grid-expanded block."""
    events: list[dict] = []

    # Try to find month/year from mc-week-dates sibling or parent label
    week_dates_el = week_el.find_previous(class_=re.compile(r'mc-week-dates|mc-week-label'))
    hint_month, hint_year = _parse_week_month_year(week_dates_el)

    for day_card in week_el.select(".mc-day-card"):
        name_el = day_card.select_one(".mc-day-name")
        num_el = day_card.select_one(".mc-day-num")

        day_name = (name_el.get_text(strip=True) if name_el else "").upper()
        if day_name in ("CERRADO", "CLOSED", ""):
            continue

        day_num_str = num_el.get_text(strip=True) if num_el else ""
        try:
            day_num = int(day_num_str)
        except ValueError:
            continue

        day_date = _infer_date(day_num, hint_month, hint_year)
        if not day_date:
            continue

        for ev_el in day_card.select(".mc-event"):
            game_el = ev_el.select_one(".mc-event-game")
            mode_el = ev_el.select_one(".mc-event-mode")
            time_el = ev_el.select_one(".mc-detail-time")

            game_text = (game_el.get_text(strip=True) if game_el else "")
            mode_text = (mode_el.get_text(strip=True) if mode_el else "")
            time_text = (time_el.get_text(strip=True) if time_el else "")

            title = game_text
            if mode_text:
                title = f"{game_text} {mode_text}"

            if not title or not time_text:
                continue

            # Parse time HH:MM
            time_m = re.match(r'(\d{1,2}):(\d{2})', time_text)
            if not time_m:
                continue
            hour, minute = int(time_m.group(1)), int(time_m.group(2))

            try:
                dt = datetime(day_date.year, day_date.month, day_date.day,
                              hour, minute, tzinfo=TZ)
            except ValueError:
                continue

            tag_classes = ev_el.get("class") or []
            game = _extract_game(game_text, tag_classes)
            fmt, fmt_official = extract_format_for_event(title=title, game=game)

            # best_of from BO1/BO3 mode
            best_of = extract_best_of(mode_text) or extract_best_of(title)
            if not best_of:
                if "BO3" in mode_text.upper():
                    best_of = 3
                elif "BO1" in mode_text.upper():
                    best_of = 1

            # External link if available
            href = ev_el.get("href") if ev_el.name == "a" else None

            events.append({
                "store": STORE,
                "game": game,
                "format": fmt,
                "format_official": fmt_official,
                "best_of": best_of,
                "title": title,
                "datetime_start": dt.isoformat(),
                "datetime_end": None,
                "language": LANGUAGE,
                "source_url": href or PAGE_URL,
                "scraped_at": scraped_at,
            })

    return events


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
    week_grids = soup.select(".mc-week-grid-expanded")
    logger.info("%s: found %d week grids", STORE, len(week_grids))

    for week_el in week_grids:
        try:
            week_events = _parse_week(week_el, scraped_at)
            events.extend(week_events)
            logger.info("%s: week → %d events", STORE, len(week_events))
        except Exception as exc:
            logger.warning("%s: skipping week block: %s", STORE, exc)

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
