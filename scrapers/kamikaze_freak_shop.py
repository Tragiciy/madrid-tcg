"""
Kamikaze Freak Shop — https://kamikazefreakshop.es/index.php/eventos/

The event page describes a static weekly schedule in prose. We parse the
recurring TCG events and generate future instances for the next 90 days.

Assumptions (derived from the /eventos/ page text as of 2026-05-05):
  - Viernes 18:00h — Commander (Magic: The Gathering)
  - Sábados 17:30h — Standard o Sellado (Magic: The Gathering)

These are the only competitive TCG events with explicit times. Board-game
and RPG events are out of scope for this aggregator.
"""

import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:
    from shared.scraper_keywords import FORMAT_KEYWORDS, extract_format_from_keywords
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import FORMAT_KEYWORDS, extract_format_from_keywords

logger = logging.getLogger(__name__)

STORE = "Kamikaze Freak Shop"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
EVENTS_URL = "https://kamikazefreakshop.es/index.php/eventos/"
DEFAULT_GAME = "Magic: The Gathering"

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "text/html,application/xhtml+xml",
}

# ---------------------------------------------------------------------------
# Recurring schedule definition
# ---------------------------------------------------------------------------

# weekday: 0=Monday ... 6=Sunday
_RECURRING_EVENTS = [
    {
        "title": "Commander",
        "weekday": 4,  # Friday
        "hour": 18,
        "minute": 0,
        "format": "Commander",
    },
    {
        "title": "Standard o Sellado",
        "weekday": 5,  # Saturday
        "hour": 17,
        "minute": 30,
        "format": None,  # resolved per-instance from title
    },
]

# Expected anchor words from the page text. If any disappear, our assumptions
# are stale and we should stop rather than emit stale synthetic events.
_REQUIRED_ANCHORS = ["Commander", "Standard", "Viernes", "Sábados"]


def _generate_instances(days_ahead: int = 90) -> list[dict]:
    today = datetime.now(tz=TZ).date()
    cutoff = today + timedelta(days=days_ahead)
    instances: list[dict] = []

    for template in _RECURRING_EVENTS:
        delta = (template["weekday"] - today.weekday()) % 7
        current = today + timedelta(days=delta)
        if current < today:
            current += timedelta(days=7)

        while current <= cutoff:
            dt = datetime(
                current.year,
                current.month,
                current.day,
                template["hour"],
                template["minute"],
                tzinfo=TZ,
            )
            title = template["title"]
            fmt = template["format"] or extract_format_from_keywords(
                title, FORMAT_KEYWORDS
            )
            instances.append(
                {
                    "title": title,
                    "datetime_start": dt.isoformat(),
                    "format": fmt,
                }
            )
            current += timedelta(days=7)

    return instances


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()

    # Verify the page is reachable and still contains our anchor text.
    try:
        resp = requests.get(EVENTS_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("%s: listing fetch failed: %s", STORE, exc)
        return []

    page_text = BeautifulSoup(resp.text, "html.parser").get_text(" ", strip=True)
    for anchor in _REQUIRED_ANCHORS:
        if anchor not in page_text:
            logger.error(
                "%s: anchor word %r missing from event page; schedule may have changed",
                STORE,
                anchor,
            )
            raise RuntimeError(
                f"{STORE}: expected anchor {anchor!r} not found on event page"
            )

    raw_events = _generate_instances(days_ahead=90)

    events: list[dict] = []
    for raw in raw_events:
        events.append(
            {
                "store": STORE,
                "game": DEFAULT_GAME,
                "format": raw["format"],
                "title": raw["title"],
                "datetime_start": raw["datetime_start"],
                "datetime_end": None,
                "language": LANGUAGE,
                "source_url": EVENTS_URL,
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
