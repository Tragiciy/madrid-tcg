"""
Replay Boardgame Cafe — https://www.replayoutletcafe.com/

Wix Events site. Event detail pages are listed in event-pages-sitemap.xml and
embed their event payload in the wix-warmup-data JSON script.
"""

import html
import json
import logging
import sys
import xml.etree.ElementTree as ET
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

STORE = "Replay Boardgame Cafe"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")

BASE_URL = "https://www.replayoutletcafe.com"
EVENT_SITEMAP_URL = f"{BASE_URL}/event-pages-sitemap.xml"
DAYS_AHEAD = 90

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

EVENT_URL_HINTS = {
    "commander",
    "digimon",
    "fab",
    "flesh",
    "lorcana",
    "magic",
    "mtg",
    "netrunner",
    "one-piece",
    "onepiece",
    "pokemon",
    "pokémon",
    "riftbound",
    "star-wars",
    "swu",
    "tcg",
    "yugioh",
    "yu-gi-oh",
}


def _fetch_text(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def _event_url_has_tcg_hint(url: str, title: str = "") -> bool:
    text = f"{url} {title}".lower()
    if extract_game_from_keywords(text, GAME_KEYWORDS):
        return True
    return any(hint in text for hint in EVENT_URL_HINTS)


def _extract_game(text: str) -> Optional[str]:
    game = extract_game_from_keywords(text, GAME_KEYWORDS)
    if game:
        return game

    lower = text.lower()
    if "commander" in lower:
        return "Magic: The Gathering"
    return None


def _fetch_event_urls() -> list[str]:
    xml_text = _fetch_text(EVENT_SITEMAP_URL)
    root = ET.fromstring(xml_text)
    ns = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "image": "http://www.google.com/schemas/sitemap-image/1.1",
    }

    urls: list[str] = []
    for url_node in root.findall("sm:url", ns):
        loc = url_node.findtext("sm:loc", default="", namespaces=ns)
        title = url_node.findtext("image:image/image:title", default="", namespaces=ns)
        if loc and _event_url_has_tcg_hint(loc, title):
            urls.append(loc)

    if not urls:
        raise ValueError("Replay event sitemap did not contain TCG-looking event pages")
    return sorted(set(urls))


def _find_events_state(warmup_data: dict) -> dict:
    apps_data = warmup_data.get("appsWarmupData") or {}
    for value in apps_data.values():
        if isinstance(value, dict) and "EventsPageInitialState" in value:
            state = value["EventsPageInitialState"]
            if isinstance(state, dict):
                return state
    raise ValueError("Wix warmup data did not include EventsPageInitialState")


def _extract_event_state(page_html: str) -> dict:
    soup = BeautifulSoup(page_html, "html.parser")
    script = soup.find("script", id="wix-warmup-data")
    if not script:
        raise ValueError("Missing wix-warmup-data script")

    warmup_data = json.loads(script.string or script.get_text())
    if not isinstance(warmup_data, dict):
        raise ValueError(f"Unexpected warmup data type: {type(warmup_data).__name__}")
    return _find_events_state(warmup_data)


def _rich_text_to_plain_text(node) -> str:
    if isinstance(node, list):
        return " ".join(_rich_text_to_plain_text(child) for child in node)
    if not isinstance(node, dict):
        return ""

    text_data = node.get("textData")
    own_text = ""
    if isinstance(text_data, dict):
        own_text = text_data.get("text") or ""

    child_text = _rich_text_to_plain_text(node.get("nodes") or [])
    return " ".join(part for part in [own_text, child_text] if part)


def _clean_text(value: str) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return html.unescape(" ".join(text.split()))


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def _event_datetimes(state: dict, event: dict) -> tuple[Optional[datetime], Optional[datetime]]:
    event_id = event.get("id")
    dates_by_event = ((state.get("dates") or {}).get("events") or {})
    dates = dates_by_event.get(event_id) if event_id else None

    if isinstance(dates, dict):
        start = _parse_iso_datetime(dates.get("startDateISOFormatNotUTC") or "")
        end = _parse_iso_datetime(dates.get("endDateISOFormatNotUTC") or "")
        if start:
            return start, end

    config = ((event.get("scheduling") or {}).get("config") or {})
    start = _parse_iso_datetime(config.get("startDate") or "")
    end = _parse_iso_datetime(config.get("endDate") or "")
    return start, end


def _event_description(event: dict) -> str:
    parts = [
        event.get("description") or "",
        event.get("about") or "",
    ]
    long_description = event.get("longDescription") or {}
    parts.append(_rich_text_to_plain_text(long_description.get("nodes") or []))
    return _clean_text(" ".join(parts))


def _build_event(url: str, state: dict, scraped_at: str) -> Optional[dict]:
    event = ((state.get("event") or {}).get("event") or {})
    if not isinstance(event, dict):
        return None

    title = _clean_text(event.get("title") or "")
    description = _event_description(event)
    combined = f"{title} {description}"

    game = _extract_game(combined)
    if not title or not game:
        return None

    start, end = _event_datetimes(state, event)
    if not start:
        return None

    fmt, fmt_official = extract_format_for_event(
        title=title,
        description=description,
        game=game,
    )

    event_id = event.get("id") or event.get("slug") or url.rsplit("/", 1)[-1]

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "format_official": fmt_official,
        "best_of": extract_best_of(combined),
        "title": title,
        "datetime_start": start.isoformat(),
        "datetime_end": end.isoformat() if end and end != start else None,
        "language": LANGUAGE,
        "source_url": url,
        "source_event_id": f"replay:{event_id}",
        "scraped_at": scraped_at,
    }


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    today = datetime.now(tz=TZ)
    cutoff = today + timedelta(days=DAYS_AHEAD)

    urls = _fetch_event_urls()
    events: list[dict] = []
    failed_urls: list[str] = []

    for url in urls:
        try:
            state = _extract_event_state(_fetch_text(url))
            event = _build_event(url, state, scraped_at)
        except Exception as exc:
            failed_urls.append(url)
            logger.warning("%s: failed to parse %s: %s", STORE, url, exc)
            continue

        if not event:
            continue

        start = datetime.fromisoformat(event["datetime_start"])
        if today <= start <= cutoff:
            events.append(event)

    if failed_urls and len(failed_urls) == len(urls):
        raise RuntimeError(f"Failed to parse all Replay candidate event pages: {len(failed_urls)}")

    seen: dict = {}
    for ev in events:
        key = (ev["source_event_id"], ev["store"])
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
