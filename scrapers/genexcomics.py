"""
GenexComics — https://genexcomics.com/eventos/

WordPress/Elementor event archive. The listing links to per-event pages; event
date, time, and location are rendered in JetEngine dynamic fields on each page.
"""

import html
import json
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
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

STORE = "GenexComics"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
EVENTS_URL = "https://genexcomics.com/eventos/"
DEFAULT_GAME = "Magic: The Gathering"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

SPANISH_DATE_RE = re.compile(
    r"^(\d{1,2})\s+(?:de\s+)?([a-záéíóúñ]+)\s+(?:de\s+)?(\d{4})$",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")

MTG_HINTS = {
    "mtg",
    "magic",
    "commander",
    "pauper",
    "pioneer",
    "modern",
    "standard",
    "draft",
    "sellado",
    "presentación",
    "presentacion",
    "strixhaven",
}


def _fetch_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _clean_text(value: str) -> str:
    return html.unescape((value or "").strip())


def _event_url(href: str) -> bool:
    parsed = urlparse(href)
    return parsed.netloc.endswith("genexcomics.com") and "/eventos/" in parsed.path


def _collect_event_links() -> list[str]:
    soup = _fetch_soup(EVENTS_URL)
    links: list[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].split("#", 1)[0]
        if href.rstrip("/") == EVENTS_URL.rstrip("/"):
            continue
        if not _event_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)

    return links


def _parse_spanish_date(value: str) -> Optional[date]:
    match = SPANISH_DATE_RE.match(value.lower().strip())
    if not match:
        return None

    day = int(match.group(1))
    month = SPANISH_MONTHS.get(match.group(2))
    year = int(match.group(3))
    if not month:
        return None

    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_time(value: str) -> Optional[tuple[int, int]]:
    match = TIME_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _extract_game(text: str) -> Optional[str]:
    game = extract_game_from_keywords(text, GAME_KEYWORDS)
    if game:
        return game

    lower = text.lower()
    if any(hint in lower for hint in MTG_HINTS):
        return DEFAULT_GAME

    return None


def _parse_event_detail(url: str) -> Optional[dict]:
    soup = _fetch_soup(url)

    title_el = soup.select_one("h1")
    title = _clean_text(title_el.get_text(" ", strip=True) if title_el else "")
    if not title:
        return None

    fields = [
        _clean_text(el.get_text(" ", strip=True))
        for el in soup.select(".jet-listing-dynamic-field__content")
    ]

    event_date = None
    event_time = None
    location = None
    for field in fields:
        if event_date is None:
            event_date = _parse_spanish_date(field)
            if event_date is not None:
                continue
        if event_time is None:
            event_time = _parse_time(field)
            if event_time is not None:
                continue
        if "genexcomics" in field.lower():
            location = field

    if not event_date or not event_time:
        return None

    dt = datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        event_time[0],
        event_time[1],
        tzinfo=TZ,
    )
    page_text = soup.get_text(" ", strip=True)
    combined = f"{title} {page_text}"
    game = _extract_game(combined)
    if not game:
        return None

    fmt, fmt_official = extract_format_for_event(
        title=title,
        description=page_text,
        game=game,
    )

    return {
        "title": title,
        "datetime_start": dt.isoformat(),
        "location": location,
        "game": game,
        "format": fmt,
        "format_official": fmt_official,
        "best_of": extract_best_of(combined),
        "source_url": url,
    }


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    today = datetime.now(tz=TZ).date()
    cutoff = today + timedelta(days=90)

    try:
        links = _collect_event_links()
    except Exception as exc:
        logger.error("%s: event listing fetch failed: %s", STORE, exc)
        return []

    events: list[dict] = []
    for url in links:
        try:
            raw = _parse_event_detail(url)
            time.sleep(0.2)
        except Exception as exc:
            logger.debug("%s: event detail fetch failed for %s: %s", STORE, url, exc)
            continue
        if not raw:
            continue

        event_date = datetime.fromisoformat(raw["datetime_start"]).date()
        if not (today <= event_date <= cutoff):
            continue

        event = {
            "store": STORE,
            "game": raw["game"],
            "format": raw["format"],
            "format_official": raw["format_official"],
            "best_of": raw["best_of"],
            "title": raw["title"],
            "datetime_start": raw["datetime_start"],
            "datetime_end": None,
            "language": LANGUAGE,
            "source_url": raw["source_url"],
            "scraped_at": scraped_at,
        }
        if raw.get("location"):
            event["location"] = raw["location"]
        events.append(event)

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
