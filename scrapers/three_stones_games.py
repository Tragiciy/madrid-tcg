"""
Three Stones Games — https://threestonesgames.com/pages/calendario-de-eventos

Shopify page embedding the Calee Events Calendar app. The page posts to the
Calee API with the shop domain and calendar id, then renders FullCalendar from
the returned event_data payload.
"""

import html
import json
import logging
import sys
from calendar import monthrange
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

STORE = "Three Stones Games"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")

PAGE_URL = "https://threestonesgames.com/pages/calendario-de-eventos"
API_URL = "https://theweeapps.com/caly-v2/api.php"
SHOP_DOMAIN = "three-stones-games.myshopify.com"
CALENDAR_ID = "1"
DEFAULT_GAME = "Star Wars: Unlimited"
DAYS_AHEAD = 90

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Origin": "https://threestonesgames.com",
    "Referer": PAGE_URL,
}

SWU_HINTS = {
    "star wars",
    "swu",
    "twin suns",
    "premier",
    "carbonite",
    "showdown",
    "store showdown",
}

RECURRENCE_STEPS = {
    "DAILY": lambda dt, interval: dt + timedelta(days=interval),
    "WEEKLY": lambda dt, interval: dt + timedelta(weeks=interval),
}


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _strip_html(value: str) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return html.unescape(text)


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def _fetch_calendar_data() -> dict:
    resp = requests.post(
        API_URL,
        data={
            "shop": SHOP_DOMAIN,
            "req_calling_method": "get_front_event_data",
            "calendar_type": "original",
            "calendar_id": CALENDAR_ID,
        },
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        data = json.loads(resp.text)
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected calendar payload type: {type(data).__name__}")
    return data


def _tag_text(raw: dict, tags_by_id: dict[str, str]) -> str:
    tag_id = str(raw.get("tag_id") or "")
    return tags_by_id.get(tag_id, "")


def _extract_game(text: str) -> Optional[str]:
    game = extract_game_from_keywords(text, GAME_KEYWORDS)
    if game:
        return game
    lower = text.lower()
    if any(hint in lower for hint in SWU_HINTS):
        return DEFAULT_GAME
    return None


def _iter_occurrences(raw: dict, today: datetime, cutoff: datetime):
    start = _parse_datetime(raw.get("start_date_time") or "")
    end = _parse_datetime(raw.get("end_date_time") or "")
    if not start:
        return

    if str(raw.get("is_recurring") or "0") != "1":
        yield start, end
        return

    recurrence_type = str(raw.get("recurrence_type") or "").upper()
    step_fn = RECURRENCE_STEPS.get(recurrence_type)
    if not step_fn and recurrence_type != "MONTHLY":
        yield start, end
        return

    interval = int(raw.get("repeat_interval") or 1)
    recurrence_end = _parse_datetime(raw.get("end_recurring_event") or "") or cutoff
    duration = (end - start) if end else None

    current = start
    while current <= cutoff and current <= recurrence_end:
        current_end = current + duration if duration else None
        if current_end is None or current_end >= today:
            yield current, current_end
        if recurrence_type == "MONTHLY":
            current = _add_months(current, interval)
        else:
            current = step_fn(current, interval)


def _parse_event(raw: dict, start: datetime, end: Optional[datetime],
                 tags_by_id: dict[str, str], scraped_at: str) -> Optional[dict]:
    title = html.unescape((raw.get("event_title") or "").strip())
    description = _strip_html(raw.get("event_details") or "")
    tag_text = _tag_text(raw, tags_by_id)
    combined = f"{title} {description} {tag_text}"

    game = _extract_game(combined)
    if not title or not game:
        return None

    fmt, fmt_official = extract_format_for_event(
        title=title,
        description=description,
        category=tag_text,
        game=game,
    )
    start_iso = start.isoformat()
    source_event_id = raw.get("event_id") or raw.get("id") or title

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "format_official": fmt_official,
        "best_of": extract_best_of(combined),
        "title": title,
        "datetime_start": start_iso,
        "datetime_end": end.isoformat() if end and end != start else None,
        "language": LANGUAGE,
        "source_url": PAGE_URL,
        "source_event_id": f"three-stones:{source_event_id}:{start_iso}",
        "scraped_at": scraped_at,
    }


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    today = datetime.now(tz=TZ)
    cutoff = today + timedelta(days=DAYS_AHEAD)

    data = _fetch_calendar_data()
    raw_events = data.get("event_data") or []
    if not isinstance(raw_events, list):
        raise ValueError(f"Unexpected event_data type: {type(raw_events).__name__}")

    tags_by_id = {
        str(tag.get("id") or tag.get("tag_id") or ""): str(
            tag.get("tag_name") or tag.get("name") or ""
        )
        for tag in data.get("tags") or []
        if isinstance(tag, dict)
    }

    events: list[dict] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        for start, end in _iter_occurrences(raw, today, cutoff):
            if start.date() < today.date() or start > cutoff:
                continue
            event = _parse_event(raw, start, end, tags_by_id, scraped_at)
            if event:
                events.append(event)

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
