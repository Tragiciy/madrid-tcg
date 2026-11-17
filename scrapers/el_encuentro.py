"""
scrapers/el_encuentro.py — El Encuentro (Carrer de les Torres 44, Sant Joan
Despí, Barcelona area), from the EventON calendar on its own site
(https://elencuentro.infrecuentes.es/eventos/), read with shared.eventon.

The store's own calendar has games no locator lists (Riftbound, Yu-Gi-Oh!,
Gundam) next to the Magic, Pokémon, One Piece and Digimon nights the
locators already carry; those locator copies are dropped by the
aggregator's dedup (store listing wins). The store publishes about two
weeks ahead, so the current and next month are read.

Events whose game is not a tracked TCG (Dragon Ball Fusion World) are
skipped: an unknown-game event would swallow a locator event in the dedup.
"""

import logging
import re
from datetime import datetime, timedelta

from shared.eventon import fetch_eventon_events
from shared.locator_common import TZ
from shared.scraper_keywords import (
    GAME_KEYWORDS,
    extract_best_of,
    extract_format_for_event,
    extract_game_from_keywords,
)

logger = logging.getLogger(__name__)

STORE = "El Encuentro"
LANGUAGE = "es"
CALENDAR_URL = "https://elencuentro.infrecuentes.es/eventos/"
MONTHS = 2
# Card games the store runs that the site does not track as a game.
_UNTRACKED_RE = re.compile(r"\b(dragon\s*ball|dbfw|fusion\s*world)\b", re.IGNORECASE)


def to_events(raw_events, now: datetime) -> list[dict]:
    """Shared event dicts for the TCG events from yesterday on. Earlier
    days of the month are history the aggregator already holds (or holds
    as locator copies, which must not get a twin)."""
    scraped_at = now.astimezone(TZ).isoformat()
    since = now - timedelta(days=1)
    out: list[dict] = []
    seen: set[str] = set()
    for ev in raw_events:
        if ev.start < since or not ev.title or _UNTRACKED_RE.search(ev.title):
            continue
        game = (extract_game_from_keywords(ev.title, GAME_KEYWORDS)
                or extract_game_from_keywords(ev.description, GAME_KEYWORDS))
        if not game:
            continue
        sid = f"elencuentro:{ev.id}:{ev.ri}"
        if sid in seen:
            continue
        seen.add(sid)
        fmt, fmt_official = extract_format_for_event(title=ev.title, description=ev.description, game=game)
        out.append({
            "title": ev.title,
            "store": STORE,
            "game": game,
            "format": fmt,
            "format_official": fmt_official,
            "best_of": extract_best_of(f"{ev.title} {ev.description}"),
            "datetime_start": ev.start.isoformat(),
            "datetime_end": ev.end.isoformat() if ev.end else None,
            "language": LANGUAGE,
            "source_url": ev.url or CALENDAR_URL,
            "source_event_id": sid,
            "scraped_at": scraped_at,
        })
    out.sort(key=lambda e: e["datetime_start"])
    return out


def scrape() -> list[dict]:
    raw = fetch_eventon_events(CALENDAR_URL, months=MONTHS)
    if not raw:
        # The current month always holds the store's weekly nights (past
        # ones included); nothing at all means the calendar broke.
        raise RuntimeError(f"{STORE}: EventON calendar returned no events")
    events = to_events(raw, datetime.now(TZ))
    logger.info("%s: %d calendar entries, %d TCG events", STORE, len(raw), len(events))
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
