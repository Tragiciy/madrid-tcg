"""
shared/goblintrader.py — parser for the event list on goblintrader.es
store pages (PrestaShop CMS pages, one per shop: /gb/content/<id>-<slug>).

Each shop page has a server-rendered block, so plain HTTP is enough:

  div.gts-store-events
    li.gts-store-events__item
      time.gts-store-events__date[datetime="YYYY-MM-DD"]
      p.gts-store-events__meta     → spans: [badge], event type, game label
      h3.gts-store-events__name    → title
      ul.gts-store-events__details → "16:30 a 20:00", places, price

The page shows all activity: wargaming demos, painting courses and card
games. Every event is kept; the game is set only when the title or the
game label names a known TCG.

No network requests on import.
"""

import logging
import re
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from shared.locator_common import TZ
from shared.scraper_keywords import (
    GAME_KEYWORDS,
    extract_best_of,
    extract_format_for_event,
    extract_game_from_keywords,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "text/html,application/xhtml+xml",
}


# "16:30 a 20:00", "17:00 - 19:00", "09:00-14:30", "17h"
_TIME_RE = re.compile(r"(\d{1,2})[:.h](\d{2})(?:\s*(?:a|-|–|hasta)\s*(\d{1,2})[:.h](\d{2}))?")

# Star Wars miniatures games, which the "star wars" keyword would
# otherwise tag as Star Wars: Unlimited.
_STAR_WARS_MINIATURES_RE = re.compile(r"\b(armada|legion|shatterpoint|x-wing)\b", re.IGNORECASE)


def _game(title: str, label: str) -> Optional[str]:
    text = f"{title} {label}"
    game = extract_game_from_keywords(text, GAME_KEYWORDS)
    if (
        game == "Star Wars: Unlimited"
        and _STAR_WARS_MINIATURES_RE.search(text)
        and "unlimited" not in text.lower()
    ):
        return None
    return game


def _parse_times(details: str, day: date) -> Optional[tuple[datetime, Optional[datetime]]]:
    """(start, end), or None when the listing gives no start time — such
    entries (ongoing leagues) are skipped rather than shown at a made-up
    hour."""
    m = _TIME_RE.search(details)
    if not m:
        return None
    sh, sm, eh, em = m.groups()
    start = datetime(day.year, day.month, day.day, int(sh), int(sm), tzinfo=TZ)
    end = None
    if eh is not None:
        end = datetime(day.year, day.month, day.day, int(eh), int(em), tzinfo=TZ)
        if end <= start:
            end = None
    return start, end


def parse_store_page(html: str, store: str, source_url: str, now: datetime) -> list[dict]:
    """Parse one shop page. Raises if the event block is missing."""
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one(".gts-store-events")
    if block is None:
        raise RuntimeError(f"{store}: no .gts-store-events block; page structure changed")

    scraped_at = now.astimezone(TZ).isoformat()
    out: list[dict] = []
    for item in block.select(".gts-store-events__item"):
        time_node = item.select_one("time.gts-store-events__date[datetime]")
        title_node = item.select_one(".gts-store-events__name")
        title = title_node.get_text(" ", strip=True) if title_node else ""
        if not (time_node and title):
            logger.warning("%s: skipping event item without date or title", store)
            continue
        try:
            day = date.fromisoformat(time_node["datetime"][:10])
        except ValueError:
            logger.warning("%s: bad date %r for %r", store, time_node["datetime"], title)
            continue

        meta = [
            s.get_text(" ", strip=True)
            for s in item.select(".gts-store-events__meta > span")
            if "gts-store-events__badge" not in (s.get("class") or [])
        ]
        # Spans are [event type, game label]; either may be missing.
        category = meta[0] if meta else ""
        label = meta[-1] if len(meta) > 1 else ""
        details = " ".join(
            li.get_text(" ", strip=True) for li in item.select(".gts-store-events__details li")
        )

        times = _parse_times(details, day)
        if times is None:
            logger.info("%s: skipping %r on %s — no start time listed", store, title, day)
            continue
        start, end = times
        game = _game(title, label)
        fmt, fmt_official = extract_format_for_event(title=title, game=game)
        out.append({
            "title": title,
            "store": store,
            "game": game,
            "format": fmt,
            "format_official": fmt_official,
            "best_of": extract_best_of(title),
            "datetime_start": start.isoformat(),
            "datetime_end": end.isoformat() if end else None,
            "language": "es",
            "source_url": source_url,
            "scraped_at": scraped_at,
        })
        logger.debug("%s: %s | %s | %s", store, start.date(), category, title)

    out.sort(key=lambda e: e["datetime_start"])
    return out


def scrape_store_page(url: str, store: str) -> list[dict]:
    """Fetch and parse one shop page. Raises on HTTP or structure errors."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    events = parse_store_page(resp.text, store, url, datetime.now(TZ))
    logger.info("%s: %d events", store, len(events))
    return events
