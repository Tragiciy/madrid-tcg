"""
Panda Games — https://pandagames.es/juegos/eventos/

Since autumn 2026 the events archive is a server-rendered monthly calendar
from a custom plugin (CSS prefix `pgec-`). The old WooCommerce product grid
is gone. One GET per month (`?pgec_month=YYYY-MM`) returns every event of
that month:

    <section class="pgec-calendar">
      <div class="pgec-filters"> ... </div>
        <button class="pgec-filter" data-game="naruto">My Little Pony</button>
      <section class="pgec-day" id="pgec-day-20261118">
        <article class="pgec-event-row" data-game="riftbound">
          <p class="pgec-event-row__time"><svg/><span>18:30</span></p>
          <h3 class="pgec-event-row__title"><a href="...">Liga Semanal ...</a></h3>

The store reuses its tag slugs for other games: slug `naruto` is shown as
"My Little Pony" and `star-wars` as "Flesh & Blood". So the game is read
from the filter button's visible label for the row's slug, never from the
slug itself. The title wins when it names a game, matching the aggregator.
Events whose game is not a tracked TCG (board games, My Little Pony) are
skipped: an unknown-game event would also swallow a locator event in the
aggregator's dedup.
"""

import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
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

STORE = "Panda Games"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
LISTING_URL = "https://pandagames.es/juegos/eventos/"
HORIZON_DAYS = 90

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
}

_DAY_ID_RE = re.compile(r"pgec-day-(\d{4})(\d{2})(\d{2})$")
# "18:30", "18.30", "12" (the site prints whole hours without minutes)
_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.h](\d{2}))?\b")


def _month_url(year: int, month: int) -> str:
    return f"{LISTING_URL}?pgec_month={year:04d}-{month:02d}"


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def _filter_labels(soup: BeautifulSoup) -> dict[str, str]:
    """Map each tag slug to the label the store shows for it."""
    labels: dict[str, str] = {}
    for button in soup.select(".pgec-filter[data-game]"):
        label = button.get_text(" ", strip=True)
        if label:
            labels.setdefault(button["data-game"], label)
    return labels


def _resolve_game(title: str, slug: str, labels: dict[str, str]) -> Optional[str]:
    label = labels.get(slug) or slug.replace("-", " ")
    return (
        extract_game_from_keywords(title, GAME_KEYWORDS)
        or extract_game_from_keywords(label, GAME_KEYWORDS)
    )


def _parse_time(text: str) -> Optional[tuple[int, int]]:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def parse_calendar(html: str, scraped_at: str) -> list[dict]:
    """Parse one month page into events (no date-window filtering)."""
    soup = BeautifulSoup(html, "html.parser")
    if not soup.select_one("section.pgec-calendar"):
        raise RuntimeError(f"{STORE}: calendar markup not found, layout changed?")
    labels = _filter_labels(soup)

    events: list[dict] = []
    for day in soup.select("section.pgec-day[id]"):
        m = _DAY_ID_RE.match(day["id"])
        if not m:
            continue
        year, month, dom = (int(g) for g in m.groups())
        for row in day.select("article.pgec-event-row"):
            link = row.select_one(".pgec-event-row__title a")
            title_el = link or row.select_one(".pgec-event-row__title")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if not title:
                continue
            time_el = row.select_one(".pgec-event-row__time")
            hm = _parse_time(time_el.get_text(" ", strip=True) if time_el else "")
            if not hm:
                logger.debug("%s: no time for %r on %s", STORE, title, day["id"])
                continue
            game = _resolve_game(title, row.get("data-game", ""), labels)
            if not game:
                logger.debug("%s: skipping non-TCG event %r", STORE, title)
                continue
            dt = datetime(year, month, dom, hm[0], hm[1], tzinfo=TZ)
            fmt, fmt_official = extract_format_for_event(title=title, game=game)
            events.append({
                "store": STORE,
                "game": game,
                "format": fmt,
                "format_official": fmt_official,
                "best_of": extract_best_of(title),
                "title": title,
                "datetime_start": dt.isoformat(),
                "datetime_end": None,
                "language": LANGUAGE,
                "source_url": (link.get("href") if link else None) or LISTING_URL,
                "scraped_at": scraped_at,
            })
    return events


def scrape() -> list[dict]:
    now = datetime.now(tz=TZ)
    scraped_at = now.isoformat()
    today = now.date()
    cutoff = today + timedelta(days=HORIZON_DAYS)

    events: list[dict] = []
    for year, month in _months_between(today, cutoff):
        url = _month_url(year, month)
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        month_events = parse_calendar(resp.text, scraped_at)
        logger.info("%s: %04d-%02d: %d events", STORE, year, month, len(month_events))
        events.extend(month_events)

    seen: dict = {}
    for ev in events:
        if not (today <= date.fromisoformat(ev["datetime_start"][:10]) <= cutoff):
            continue
        seen.setdefault((ev["title"], ev["datetime_start"]), ev)
    deduped = sorted(seen.values(), key=lambda e: e["datetime_start"])
    logger.info("%s: total events returned: %d", STORE, len(deduped))
    return deduped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
