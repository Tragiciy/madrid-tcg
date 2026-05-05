"""
Asedio Gaming — https://asediogaming.com/pages/eventos

Shopify site embedding EventCalendarApp (FullCalendar widget).
Events are rendered client-side; the initial HTML contains no event data.

Strategy:
  - Playwright loads the page and dismisses the cookie banner.
  - Waits for .fc-event elements inside the FullCalendar grid.
  - Extracts event title and date (from parent .fc-daygrid-day[data-date]).
  - Navigates forward through calendar months to collect upcoming events.

Notes:
  - The calendar grid shows only the event title; time of day is NOT
    displayed. We default to 12:00:00 Europe/Madrid (midday) so the
    event appears sensibly in a daily calendar view instead of midnight.
    This is clearly flagged in the scraper comments.
  - Each month navigation triggers a network request; we cap at
    MAX_EXTRA_MONTHS to keep the scraper fast.
"""

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

try:
    from shared.scraper_keywords import (
        FORMAT_KEYWORDS,
        GAME_KEYWORDS,
        extract_format_from_keywords,
        extract_game_from_keywords,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import (
        FORMAT_KEYWORDS,
        GAME_KEYWORDS,
        extract_format_from_keywords,
        extract_game_from_keywords,
    )

logger = logging.getLogger(__name__)

STORE = "Asedio Gaming"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
BASE_URL = "https://asediogaming.com/pages/eventos"

# Calendar navigation
MAX_EXTRA_MONTHS = 3
NAV_TIMEOUT = 30_000
SELECTOR_TIMEOUT = 15_000
CLICK_TIMEOUT = 5_000
AFTER_CLICK_WAIT = 1_500

# Fallback time when the grid does not expose an event start time.
FALLBACK_HOUR = 12
FALLBACK_MINUTE = 0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Referer": BASE_URL,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_format(text: str) -> Optional[str]:
    return extract_format_from_keywords(text, FORMAT_KEYWORDS)


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS)


def _make_iso(date_str: str) -> Optional[str]:
    """Combine 'YYYY-MM-DD' with the fixed fallback time into Madrid-tz ISO."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=FALLBACK_HOUR, minute=FALLBACK_MINUTE, second=0, microsecond=0, tzinfo=TZ
        )
        return dt.isoformat()
    except Exception:
        return None


def _parse_event(title: str, date_str: str, scraped_at: str) -> Optional[dict]:
    """Convert a single calendar-grid event into our schema."""
    title = (title or "").strip()
    if not title:
        return None

    datetime_start = _make_iso(date_str)
    if not datetime_start:
        return None

    game = _extract_game(title)
    fmt = _extract_format(title)

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "title": title,
        "datetime_start": datetime_start,
        "datetime_end": None,
        "language": LANGUAGE,
        "source_url": BASE_URL,
        "scraped_at": scraped_at,
    }


def _collect_month_events(page) -> list[tuple[str, str]]:
    """
    Return a list of (date_str, title) for the currently-rendered month.
    """
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    for day_cell in soup.select("td.fc-daygrid-day"):
        date_str = day_cell.get("data-date")
        if not date_str:
            continue
        for ev in day_cell.select(".fc-event"):
            title = ev.get_text(strip=True)
            if title:
                out.append((date_str, title))
    return out


# ---------------------------------------------------------------------------
# Main scrape function
# ---------------------------------------------------------------------------


def scrape() -> list[dict]:
    """
    Returns upcoming events for Asedio Gaming.
    Requires Playwright + Chromium.
    """
    scraped_at = datetime.now(tz=TZ).isoformat()
    raw_events: list[tuple[str, str]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            java_script_enabled=True,
            viewport={"width": 1280, "height": 900},
        )
        context.set_default_timeout(SELECTOR_TIMEOUT)
        context.set_default_navigation_timeout(NAV_TIMEOUT)

        page = context.new_page()

        try:
            page.goto(BASE_URL, timeout=NAV_TIMEOUT, wait_until="networkidle")
            page.wait_for_timeout(1_500)

            # Dismiss cookie banner if present
            try:
                page.locator("text=Aceptar").first.click(timeout=2_000)
                page.wait_for_timeout(500)
            except PWTimeout:
                pass
            except Exception:
                pass

            # Collect current month
            month_events = _collect_month_events(page)
            raw_events.extend(month_events)
            logger.info("%s: month 1 — %d events", STORE, len(month_events))

            # Navigate forward
            for i in range(MAX_EXTRA_MONTHS):
                try:
                    btn = page.locator(".calendar-list-view__navigate-button--right").first
                    btn.click(timeout=CLICK_TIMEOUT)
                    page.wait_for_timeout(AFTER_CLICK_WAIT)
                except PWTimeout:
                    logger.info("%s: month %d navigation timed out, stopping", STORE, i + 2)
                    break
                except Exception as exc:
                    logger.info("%s: month %d navigation failed: %s", STORE, i + 2, exc)
                    break

                month_events = _collect_month_events(page)
                raw_events.extend(month_events)
                logger.info("%s: month %d — %d events", STORE, i + 2, len(month_events))

        except PWTimeout as exc:
            logger.error("%s: scrape timed out: %s", STORE, exc)
        except Exception as exc:
            logger.error("%s: scrape failed: %s", STORE, exc)
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    # Parse raw tuples
    events: list[dict] = []
    for date_str, title in raw_events:
        try:
            parsed = _parse_event(title, date_str, scraped_at)
            if parsed:
                events.append(parsed)
        except Exception as exc:
            logger.warning("%s: skipping event %r: %s", STORE, title, exc)

    # Deduplicate by (title, datetime_start, store)
    seen: dict = {}
    for ev in events:
        key = (ev["title"], ev["datetime_start"], ev["store"])
        seen.setdefault(key, ev)
    deduped = list(seen.values())
    if len(deduped) != len(events):
        logger.info("%s: dedup %d → %d (-%d)", STORE,
                    len(events), len(deduped), len(events) - len(deduped))

    deduped.sort(key=lambda e: e["datetime_start"])
    logger.info("%s: total events returned: %d", STORE, len(deduped))
    return deduped


if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    t0 = time.time()
    events = scrape()
    elapsed = time.time() - t0
    print(json.dumps(events[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(events)}")
    print(f"Elapsed: {elapsed:.1f}s")
