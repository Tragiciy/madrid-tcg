"""
TopDeck — https://topdeck.es

WordPress/Elementor site using WP Class Schedule (WCS) plugin for a weekly
timetable. Events are JavaScript-rendered; Playwright is required.

Structure (rendered HTML):
  .wcs-class → one event block
  small.wcs-class__title[title] → event name
  time[datetime] → ISO-8601 UTC timestamp

Strategy:
  - Load calendar page with Playwright (JS rendering).
  - Parse .wcs-class elements for the current week.
  - Click .wcs-btn--next to advance week-by-week (~13 times for 90 days).
"""

import json
import logging
import sys
from datetime import datetime, timezone
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
        extract_format_for_event,
        extract_best_of,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import (
        FORMAT_KEYWORDS,
        GAME_KEYWORDS,
        extract_format_from_keywords,
        extract_game_from_keywords,
        extract_format_for_event,
        extract_best_of,
    )

logger = logging.getLogger(__name__)

STORE = "TopDeck"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
CALENDAR_URL = "https://topdeck.es/calendario-de-torneos/"
DEFAULT_GAME = "One Piece"

# Cover ~13 weeks forward (≈ 90 days)
MAX_WEEKS = 13

NAV_TIMEOUT = 30_000
CLICK_TIMEOUT = 5_000
AFTER_CLICK_WAIT = 1_500


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS) or DEFAULT_GAME


def _collect_week_events(page) -> list[tuple[str, str]]:
    """
    Return (title, datetime_iso_madrid) pairs from the currently visible week.
    """
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    for cls_el in soup.select(".wcs-class"):
        title_el = cls_el.select_one("small.wcs-class__title")
        time_el = cls_el.select_one("time[datetime]")
        if not title_el or not time_el:
            continue
        # Prefer the title attribute (full text) over text content (may be truncated)
        title = (title_el.get("title") or title_el.get_text(strip=True)).strip()
        dt_str = time_el.get("datetime", "")
        if not title or not dt_str:
            continue
        try:
            # datetime is UTC; convert to Madrid timezone
            dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            dt_madrid = dt_utc.astimezone(TZ)
            out.append((title, dt_madrid.isoformat()))
        except ValueError:
            logger.debug("%s: could not parse datetime %r", STORE, dt_str)
    return out


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    raw_events: list[tuple[str, str]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            java_script_enabled=True,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"},
        )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        context.set_default_navigation_timeout(NAV_TIMEOUT)
        page = context.new_page()

        try:
            page.goto(CALENDAR_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            # Wait for WCS widget to finish rendering
            page.wait_for_selector(".wcs-class", timeout=15_000)

            # Collect current week
            week_events = _collect_week_events(page)
            raw_events.extend(week_events)
            logger.info("%s: week 1 — %d events", STORE, len(week_events))

            # Navigate forward week by week
            for i in range(MAX_WEEKS - 1):
                try:
                    btn = page.locator(".wcs-btn--next").first
                    btn.click(timeout=CLICK_TIMEOUT)
                    page.wait_for_timeout(AFTER_CLICK_WAIT)
                except PWTimeout:
                    logger.info("%s: week %d navigation timed out, stopping", STORE, i + 2)
                    break
                except Exception as exc:
                    logger.info("%s: week %d navigation failed: %s", STORE, i + 2, exc)
                    break

                week_events = _collect_week_events(page)
                raw_events.extend(week_events)
                logger.info("%s: week %d — %d events", STORE, i + 2, len(week_events))

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

    # Build event dicts
    events: list[dict] = []
    for title, dt_iso in raw_events:
        game = _extract_game(title)
        fmt, fmt_official = extract_format_for_event(title=title, game=game)
        events.append({
            "store": STORE,
            "game": game,
            "format": fmt,
            "format_official": fmt_official,
            "best_of": extract_best_of(title),
            "title": title,
            "datetime_start": dt_iso,
            "datetime_end": None,
            "language": LANGUAGE,
            "source_url": CALENDAR_URL,
            "scraped_at": scraped_at,
        })

    # Deduplicate
    seen: dict = {}
    for ev in events:
        key = (ev["title"], ev["datetime_start"], ev["store"])
        seen.setdefault(key, ev)
    deduped = list(seen.values())
    deduped.sort(key=lambda e: e["datetime_start"])
    logger.info("%s: total events returned: %d", STORE, len(deduped))
    return deduped


if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    t0 = time.time()
    out = scrape()
    elapsed = time.time() - t0
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
    print(f"Elapsed: {elapsed:.1f}s")
