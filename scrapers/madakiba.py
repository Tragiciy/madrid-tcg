"""
MADAKIBA — https://madakiba.com/es/c/eventos

Custom e-commerce platform. Events are product cards at /es/c/eventos. The
card's <h3> text is truncated ("... 21.11 A..."), so the full title is read
from the card image's alt text.

Two kinds of event product:

  * One-off events, with the date in the title:
      "ONE PIECE CARD GAME OP18 PRERELEASE 21.11 A LAS 12H"
    Date is DD.MM (year inferred), time "A LAS HH[:MM]H".

  * Standing weekly leagues, with no date (since late 2026 these are the
    bulk of the page):
      "POKEMON LIGA DOMINGOS DE 10:30H A 14H"
    Expanded into the next RECURRING_WEEKS weeks, with an end time, except
    for games in NO_PROJECTION_GAMES.

Everything else ("CALENDARIO EVENTOS OCTUBRE 2026", an image only, booster
boxes) is skipped. Events whose game is not a tracked TCG (e.g. Dragon Ball
Fusion World) are skipped too: an unknown-game event would swallow a locator
event in the aggregator's dedup.
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

STORE = "MADAKIBA"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
SITE_ROOT = "https://madakiba.com"
PAGE_URL = f"{SITE_ROOT}/es/c/eventos"
# The store calls its own calendar "orientativo", so standing leagues are
# only projected a few weeks ahead; each run re-reads the page.
RECURRING_WEEKS = 4
# Games whose standing league is not projected: the official locator already
# lists every session with its real name and format (MADAKIBA's Friday Magic
# league is the Wizards locator's "Friday Night Magic COMMANDER"), and a
# projected copy would replace it in the aggregator's dedup. Dated one-off
# products are still read for every game.
NO_PROJECTION_GAMES = {"Magic: The Gathering"}

HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
}

# "21.11" → day=21, month=11
_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{2})\b")
# "A LAS 17:30H", "A LAS 17H"
_TIME_RE = re.compile(r"A\s+LAS\s+(\d{1,2})(?::(\d{2}))?\s*H?", re.IGNORECASE)

_WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sabados": 5, "sábados": 5, "sabado": 5, "sábado": 5,
    "domingos": 6, "domingo": 6,
}
_DAY_ALT = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
# "LIGA DOMINGOS DE 10:30H A 14H", "LIGA SABADOS DE 17H A 20H"
_LEAGUE_RE = re.compile(
    rf"\bLIGA\s+({_DAY_ALT})\s+DE\s+(\d{{1,2}})(?::(\d{{2}}))?\s*H?"
    rf"\s+A\s+(\d{{1,2}})(?::(\d{{2}}))?\s*H\b",
    re.IGNORECASE,
)


def _card_title(product_el) -> str:
    img = product_el.select_one("img[alt]")
    if img and img.get("alt", "").strip():
        return img["alt"].strip()
    title_el = product_el.select_one("h3, h2, .product-title")
    return title_el.get_text(" ", strip=True) if title_el else ""


def _card_url(product_el) -> Optional[str]:
    link = product_el.select_one("a[href*='/p/']")
    if not link:
        return None
    href = link["href"]
    return href if href.startswith("http") else SITE_ROOT + href


def _one_off_start(title: str, now: datetime) -> Optional[datetime]:
    date_m = _DATE_RE.search(title)
    if not date_m:
        return None
    day, month = int(date_m.group(1)), int(date_m.group(2))
    time_m = _TIME_RE.search(title)
    hour = int(time_m.group(1)) if time_m else 12
    minute = int(time_m.group(2)) if (time_m and time_m.group(2)) else 0
    try:
        dt = datetime(now.year, month, day, hour, minute, tzinfo=TZ)
        if dt.date() < now.date():
            dt = dt.replace(year=now.year + 1)
    except ValueError:
        return None
    return dt


def _league_slots(title: str, today: date) -> list[tuple[datetime, datetime]]:
    m = _LEAGUE_RE.search(title)
    if not m:
        return []
    weekday = _WEEKDAYS[m.group(1).lower()]
    sh, sm = int(m.group(2)), int(m.group(3) or 0)
    eh, em = int(m.group(4)), int(m.group(5) or 0)
    first = today + timedelta(days=(weekday - today.weekday()) % 7)
    slots = []
    for week in range(RECURRING_WEEKS):
        d = first + timedelta(weeks=week)
        start = datetime(d.year, d.month, d.day, sh, sm, tzinfo=TZ)
        end = datetime(d.year, d.month, d.day, eh, em, tzinfo=TZ)
        slots.append((start, end))
    return slots


def parse_page(html: str, now: datetime) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = soup.select("div.product")
    logger.info("%s: found %d product cards", STORE, len(products))
    scraped_at = now.isoformat()

    events: list[dict] = []
    for product in products:
        title = _card_title(product)
        if not title:
            continue
        game = extract_game_from_keywords(title, GAME_KEYWORDS)
        if not game:
            logger.debug("%s: skipping non-TCG product %r", STORE, title)
            continue
        slots = _league_slots(title, now.date())
        if slots and game in NO_PROJECTION_GAMES:
            logger.debug("%s: league %r left to the official locator", STORE, title)
            continue
        if not slots:
            start = _one_off_start(title, now)
            slots = [(start, None)] if start else []
        if not slots:
            continue
        fmt, fmt_official = extract_format_for_event(title=title, game=game)
        url = _card_url(product) or PAGE_URL
        for start, end in slots:
            events.append({
                "store": STORE,
                "game": game,
                "format": fmt,
                "format_official": fmt_official,
                "best_of": extract_best_of(title),
                "title": title,
                "datetime_start": start.isoformat(),
                "datetime_end": end.isoformat() if end else None,
                "language": LANGUAGE,
                "source_url": url,
                "scraped_at": scraped_at,
            })

    seen: dict = {}
    for ev in events:
        seen.setdefault((ev["title"], ev["datetime_start"]), ev)
    return sorted(seen.values(), key=lambda e: e["datetime_start"])


def scrape() -> list[dict]:
    resp = requests.get(PAGE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    events = parse_page(resp.text, datetime.now(tz=TZ))
    logger.info("%s: total events returned: %d", STORE, len(events))
    return events


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
