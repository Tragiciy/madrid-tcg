"""
Collectorage — https://collectorage.com/

WordPress site behind Cloudflare Bot Fight Mode. Both plain HTTP requests and
headless Playwright are currently blocked by the CF managed challenge (the page
stays on "Un momento…" and the REST API returns 403).

Strategy attempted:
  1. Playwright navigates the calendar page with stealth flags to get CF clearance.
  2. Execute a fetch() to the Tribe REST API from within the browser context.
  3. Fallback: parse the rendered page HTML for Tribe event selectors.

If CF blocks the browser the scraper returns an empty list gracefully. This may
work on some IP ranges (e.g. GitHub Actions) even if it fails locally.
"""

import html as _html
import json
import logging
import sys
from datetime import date, datetime, timedelta
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

STORE = "Collectorage"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
BASE_URL = "https://collectorage.com"
CALENDAR_URL = "https://collectorage.com/calendario"
DEFAULT_GAME = "Star Wars: Unlimited"
DAYS_AHEAD = 90

NAV_TIMEOUT = 30_000
SELECTOR_TIMEOUT = 15_000
CF_WAIT_MS = 8_000  # Cloudflare "I'm Under Attack" needs up to ~6s to resolve


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS) or DEFAULT_GAME


def _parse_tribe_rest_event(raw: dict, scraped_at: str) -> Optional[dict]:
    title = _html.unescape((raw.get("title") or "").strip())
    if not title:
        return None
    sd = raw.get("start_date_details") or {}
    try:
        dt_start = datetime(
            int(sd["year"]), int(sd["month"]), int(sd["day"]),
            int(sd.get("hour", 0)), int(sd.get("minutes", 0)),
            tzinfo=TZ,
        )
    except (KeyError, ValueError, TypeError):
        try:
            dt_start = datetime.strptime(raw["start_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        except Exception:
            return None

    end_iso: Optional[str] = None
    ed = raw.get("end_date_details") or {}
    try:
        dt_end = datetime(
            int(ed["year"]), int(ed["month"]), int(ed["day"]),
            int(ed.get("hour", 0)), int(ed.get("minutes", 0)),
            tzinfo=TZ,
        )
        if dt_end != dt_start:
            end_iso = dt_end.isoformat()
    except (KeyError, ValueError, TypeError):
        pass

    combined = f"{title} {_html.unescape(raw.get('description') or '')}"
    game = _extract_game(combined)
    fmt, fmt_official = extract_format_for_event(title=combined, game=game)
    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "format_official": fmt_official,
        "best_of": extract_best_of(combined),
        "title": title,
        "datetime_start": dt_start.isoformat(),
        "datetime_end": end_iso,
        "language": LANGUAGE,
        "source_url": raw.get("url") or CALENDAR_URL,
        "scraped_at": scraped_at,
    }


def _parse_html_events(html: str, scraped_at: str) -> list[dict]:
    """Parse Tribe Events Calendar HTML rendered by Playwright."""
    import re
    soup = BeautifulSoup(html, "html.parser")
    events = []

    _DATE_RE = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})")
    _TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})")

    def parse_date(text: str) -> Optional[date]:
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

    def parse_time(text: str) -> Optional[tuple]:
        m = _TIME_RE.search(text)
        return (int(m.group(1)), int(m.group(2))) if m else None

    for article in soup.select(
        "article.type-tribe_events, "
        ".tribe-events-calendar-list__event-row, "
        ".tribe-events-calendar-day__event"
    ):
        title_el = article.select_one(
            ".tribe-events-calendar-list__event-title a, "
            ".tribe-event-url, a.tribe-event-url, "
            "h2 a, h3 a"
        )
        title = _html.unescape((title_el.get_text(strip=True) if title_el else ""))
        href = title_el.get("href") if title_el else None

        time_el = article.select_one(
            ".tribe-events-calendar-list__event-datetime, "
            ".tribe-event-schedule-details, "
            ".tribe-events-schedule"
        )
        time_text = time_el.get_text(" ", strip=True) if time_el else ""

        date_obj = parse_date(time_text) or parse_date(title)
        if not date_obj or not title:
            continue
        t = parse_time(time_text)
        dt = datetime(date_obj.year, date_obj.month, date_obj.day,
                      t[0] if t else 11, t[1] if t else 0, tzinfo=TZ)

        combined = title
        game = _extract_game(combined)
        fmt, fmt_official = extract_format_for_event(title=combined, game=game)
        events.append({
            "store": STORE,
            "game": game,
            "format": fmt,
            "format_official": fmt_official,
            "best_of": extract_best_of(combined),
            "title": title,
            "datetime_start": dt.isoformat(),
            "datetime_end": None,
            "language": LANGUAGE,
            "source_url": href or CALENDAR_URL,
            "scraped_at": scraped_at,
        })

    return events


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    today = date.today().isoformat()
    end_date = (date.today() + timedelta(days=DAYS_AHEAD)).isoformat()
    api_url = (
        f"{BASE_URL}/wp-json/tribe/events/v1/events"
        f"?per_page=50&start_date={today}&end_date={end_date}&status=publish"
    )

    events: list[dict] = []

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
        # Hide webdriver flag from JavaScript
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        context.set_default_navigation_timeout(NAV_TIMEOUT)
        page = context.new_page()

        try:
            page.goto(CALENDAR_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            # Wait for Cloudflare challenge to resolve (up to ~6s)
            page.wait_for_timeout(CF_WAIT_MS)
            # If still on CF challenge page, wait for redirect
            if "just a moment" in page.title().lower():
                page.wait_for_timeout(5_000)

            # Try Tribe REST API via browser fetch (CF cookie is set)
            data = page.evaluate(f"""async () => {{
                try {{
                    const r = await fetch({json.dumps(api_url)}, {{
                        headers: {{Accept: 'application/json'}}
                    }});
                    if (!r.ok) return null;
                    return await r.json();
                }} catch(e) {{ return null; }}
            }}""")

            if data and isinstance(data.get("events"), list):
                logger.info("%s: Tribe REST returned %d events", STORE, len(data["events"]))
                for raw in data["events"]:
                    try:
                        parsed = _parse_tribe_rest_event(raw, scraped_at)
                        if parsed:
                            events.append(parsed)
                    except Exception as exc:
                        logger.warning("%s: skipping event %r: %s", STORE, raw.get("title"), exc)
            else:
                logger.info("%s: Tribe REST not available, falling back to HTML", STORE)
                html = page.content()
                events = _parse_html_events(html, scraped_at)

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
