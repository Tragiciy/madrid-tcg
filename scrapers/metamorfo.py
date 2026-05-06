"""
Metamorfo — https://metamorfo.es

Uses the shared WordPress helper. The Tribe REST API responds successfully
(currently with zero upcoming events, which is accurate for the site's
published calendar).
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

try:
    from shared.scraper_keywords import (
        FORMAT_KEYWORDS,
        GAME_KEYWORDS,
        extract_format_from_keywords,
        extract_game_from_keywords,
    )
    from shared.wordpress_events import fetch_wp_events
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import (
        FORMAT_KEYWORDS,
        GAME_KEYWORDS,
        extract_format_from_keywords,
        extract_game_from_keywords,
    )
    from shared.wordpress_events import fetch_wp_events

logger = logging.getLogger(__name__)

STORE = "Metamorfo"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
BASE_URL = "https://metamorfo.es"
DEFAULT_GAME = "Yu-Gi-Oh!"


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS) or DEFAULT_GAME


def _extract_format(text: str) -> Optional[str]:
    return extract_format_from_keywords(text, FORMAT_KEYWORDS)


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    raw_events = fetch_wp_events(BASE_URL, days_ahead=90)

    events: list[dict] = []
    for raw in raw_events:
        title = raw["title"]
        combined = f"{title} {raw.get('description') or ''}"
        game = _extract_game(combined)
        fmt = _extract_format(combined)

        events.append(
            {
                "store": STORE,
                "game": game,
                "format": fmt,
                "title": title,
                "datetime_start": raw["start_iso"],
                "datetime_end": raw.get("end_iso"),
                "language": LANGUAGE,
                "source_url": raw.get("url") or BASE_URL,
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
