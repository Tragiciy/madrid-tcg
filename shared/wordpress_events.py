"""
shared/wordpress_events.py — shared helper for WordPress event pages.

No scrape() here. No network on import.
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Madrid")


class ScraperFetchError(Exception):
    """Raised when both REST API and HTML fallback fail to yield events."""


# ---------------------------------------------------------------------------
# Tribe REST API
# ---------------------------------------------------------------------------


def _tribe_api_url(base_url: str, days_ahead: int) -> str:
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=days_ahead)).isoformat()
    base = base_url.rstrip("/")
    return (
        f"{base}/wp-json/tribe/events/v1/events"
        f"?per_page=50&start_date={today}&end_date={end}&status=publish"
    )


def _parse_tribe_event(raw: dict) -> Optional[dict]:
    """Convert one Tribe API event dict to raw format."""
    title = _html.unescape((raw.get("title") or "").strip())
    if not title:
        return None

    sd = raw.get("start_date_details") or {}
    try:
        dt_start = datetime(
            int(sd.get("year", 0)),
            int(sd.get("month", 0)),
            int(sd.get("day", 0)),
            int(sd.get("hour", 0)),
            int(sd.get("minutes", 0)),
            int(sd.get("seconds", 0)),
            tzinfo=TZ,
        )
    except (ValueError, TypeError):
        try:
            dt_start = datetime.strptime(
                raw["start_date"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=TZ)
        except Exception:
            return None

    end_iso: Optional[str] = None
    ed = raw.get("end_date_details") or {}
    try:
        dt_end = datetime(
            int(ed.get("year", 0)),
            int(ed.get("month", 0)),
            int(ed.get("day", 0)),
            int(ed.get("hour", 0)),
            int(ed.get("minutes", 0)),
            int(ed.get("seconds", 0)),
            tzinfo=TZ,
        )
        if dt_end != dt_start:
            end_iso = dt_end.isoformat()
    except (ValueError, TypeError):
        pass

    return {
        "title": title,
        "start_iso": dt_start.isoformat(),
        "end_iso": end_iso,
        "url": raw.get("url") or None,
        "description": _html.unescape(raw.get("description") or "").strip() or None,
    }


def _fetch_tribe_rest(base_url: str, days_ahead: int) -> list[dict]:
    """Try Tribe REST API. Return events or raise on failure."""
    url = _tribe_api_url(base_url, days_ahead)
    headers = {
        "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected API response type: {type(data).__name__}")
    items = data.get("events") or []
    if not isinstance(items, list):
        raise ValueError(f"Unexpected 'events' type: {type(items).__name__}")
    events = []
    for raw in items:
        parsed = _parse_tribe_event(raw)
        if parsed:
            events.append(parsed)
    return events


# ---------------------------------------------------------------------------
# HTML fallback
# ---------------------------------------------------------------------------

_TRIBE_EVENT_SELECTORS = [
    ".tribe-event",
    ".tribe-events-calendar-list__event",
    ".tribe-events-calendar-month__calendar-event-title",
    ".tribe-events-single-event-title",
    ".tribe-events-content",
]

_MEC_EVENT_SELECTORS = [
    ".mec-event-title",
    ".mec-single-event",
]

_TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})")
_DATE_RE = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})")


def _extract_text_from_element(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _parse_date_from_text(text: str) -> Optional[date]:
    m = _DATE_RE.search(text)
    if not m:
        return None
    d, mon, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return date(y, mon, d)
    except ValueError:
        return None


def _parse_time_from_text(text: str) -> Optional[tuple[int, int]]:
    m = _TIME_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _extract_tribe_html_events(soup: BeautifulSoup) -> list[dict]:
    events = []
    for article in soup.select(
        ".tribe-events-calendar-list__event-row, "
        ".tribe-events-calendar-day__event, "
        "article.type-tribe_events"
    ):
        title_el = article.select_one(
            ".tribe-events-calendar-list__event-title a, "
            ".tribe-events-calendar-day__event-title a, "
            ".tribe-event-url, "
            "a.tribe-event-url"
        )
        title = _html.unescape(_extract_text_from_element(title_el))
        href = title_el.get("href") if title_el else None

        time_el = article.select_one(
            ".tribe-events-calendar-list__event-datetime, "
            ".tribe-event-schedule-details, "
            ".tribe-events-schedule"
        )
        time_text = _extract_text_from_element(time_el)

        date_obj = _parse_date_from_text(time_text) or _parse_date_from_text(title)
        time_t = _parse_time_from_text(time_text)

        if not date_obj:
            continue
        dt = datetime(
            date_obj.year,
            date_obj.month,
            date_obj.day,
            time_t[0] if time_t else 11,
            time_t[1] if time_t else 0,
            tzinfo=TZ,
        )

        events.append(
            {
                "title": title,
                "start_iso": dt.isoformat(),
                "end_iso": None,
                "url": href,
                "description": None,
            }
        )

    if not events:
        for sel in _TRIBE_EVENT_SELECTORS:
            for el in soup.select(sel):
                title = _html.unescape(_extract_text_from_element(el))
                href = (
                    el.get("href")
                    if el.name == "a"
                    else (el.select_one("a") or {}).get("href")
                )
                date_obj = _parse_date_from_text(title)
                if not date_obj:
                    continue
                time_t = _parse_time_from_text(title)
                dt = datetime(
                    date_obj.year,
                    date_obj.month,
                    date_obj.day,
                    time_t[0] if time_t else 11,
                    time_t[1] if time_t else 0,
                    tzinfo=TZ,
                )
                events.append(
                    {
                        "title": title,
                        "start_iso": dt.isoformat(),
                        "end_iso": None,
                        "url": href,
                        "description": None,
                    }
                )

    return events


def _extract_mec_html_events(soup: BeautifulSoup) -> list[dict]:
    events = []
    for el in soup.select(", ".join(_MEC_EVENT_SELECTORS)):
        title = _html.unescape(_extract_text_from_element(el))
        href = (
            el.get("href")
            if el.name == "a"
            else (el.select_one("a") or {}).get("href")
        )
        date_obj = _parse_date_from_text(title)
        if not date_obj:
            continue
        time_t = _parse_time_from_text(title)
        dt = datetime(
            date_obj.year,
            date_obj.month,
            date_obj.day,
            time_t[0] if time_t else 11,
            time_t[1] if time_t else 0,
            tzinfo=TZ,
        )
        events.append(
            {
                "title": title,
                "start_iso": dt.isoformat(),
                "end_iso": None,
                "url": href,
                "description": None,
            }
        )
    return events


def _fetch_html_fallback(base_url: str) -> list[dict]:
    """Fetch base_url HTML and parse for Tribe/MEC event structures."""
    headers = {
        "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
        "Accept": "text/html,application/xhtml+xml",
    }
    resp = requests.get(base_url, headers=headers, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    events = _extract_tribe_html_events(soup)
    if not events:
        events = _extract_mec_html_events(soup)

    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_wp_events(base_url: str, days_ahead: int = 90) -> list[dict]:
    """
    Fetch events from a WordPress site.

    1. Try Tribe REST API
    2. Fallback to HTML parsing of common structures
    3. Raise ScraperFetchError if both fail
    """
    try:
        events = _fetch_tribe_rest(base_url, days_ahead)
        logger.debug("Tribe REST succeeded for %s: %d events", base_url, len(events))
        return events
    except Exception as exc:
        logger.debug("Tribe REST failed for %s: %s", base_url, exc)

    try:
        events = _fetch_html_fallback(base_url)
        logger.debug("HTML fallback succeeded for %s: %d events", base_url, len(events))
        return events
    except Exception as exc:
        logger.debug("HTML fallback failed for %s: %s", base_url, exc)

    raise ScraperFetchError(
        f"Could not fetch events from {base_url} via REST or HTML"
    )
