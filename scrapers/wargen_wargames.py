"""
scrapers/wargen_wargames.py — Wargen Wargames (Avenida de las Águilas 2B)
from the store's public Google Calendar (iCalendar feed).

The calendar mixes TCG nights with wargaming (Warhammer, Age of Sigmar,
OPR…). Only events whose title or description names a known TCG are kept;
the rest are not card-game events. Weekly series are RRULEs with EXDATEs,
expanded by shared.ics.
"""

import logging
from datetime import datetime, timedelta, timezone

import requests

from shared.ics import parse_ics
from shared.locator_common import TZ, USER_AGENT, WINDOW_DAYS
from shared.scraper_keywords import (
    GAME_KEYWORDS,
    extract_best_of,
    extract_format_for_event,
    extract_game_from_keywords,
)

logger = logging.getLogger(__name__)

STORE = "Wargen Wargames"
CALENDAR_ID = "255msincmarr7b837fev5ht44f955dp7@import.calendar.google.com"
ICS_URL = f"https://calendar.google.com/calendar/ical/{CALENDAR_ID.replace('@', '%40')}/public/basic.ics"
CALENDAR_PAGE = "https://calendar.google.com/calendar/embed?src=255msincmarr7b837fev5ht44f955dp7%40import.calendar.google.com&ctz=Europe%2FMadrid"


def parse_events(ics_text: str, now: datetime) -> list[dict]:
    scraped_at = now.astimezone(TZ).isoformat()
    window_end = now + timedelta(days=WINDOW_DAYS)
    out: list[dict] = []
    for ev in parse_ics(ics_text, now - timedelta(days=1), window_end):
        game = extract_game_from_keywords(f"{ev.summary} {ev.description}", GAME_KEYWORDS)
        if not game or not ev.summary:
            continue  # wargaming / non-TCG
        start = ev.start.astimezone(TZ)
        fmt, fmt_official = extract_format_for_event(title=ev.summary, game=game)
        out.append({
            "title": ev.summary,
            "store": STORE,
            "game": game,
            "format": fmt,
            "format_official": fmt_official,
            "best_of": extract_best_of(ev.summary),
            "datetime_start": start.isoformat(),
            "datetime_end": ev.end.astimezone(TZ).isoformat() if ev.end else None,
            "language": "es",
            "source_url": ev.url or CALENDAR_PAGE,
            # One id per occurrence (series share a UID), keyed by the
            # scheduled start so moving one occurrence keeps its id.
            "source_event_id": f"wargen-ics:{ev.uid}:{(ev.occurrence_of or ev.start).astimezone(TZ).isoformat()}",
            "scraped_at": scraped_at,
        })
    return out


def scrape() -> list[dict]:
    resp = requests.get(ICS_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    if "BEGIN:VCALENDAR" not in resp.text:
        raise RuntimeError(f"{STORE}: response is not an iCalendar feed")
    events = parse_events(resp.text, datetime.now(timezone.utc))
    logger.info("%s: %d TCG events", STORE, len(events))
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
