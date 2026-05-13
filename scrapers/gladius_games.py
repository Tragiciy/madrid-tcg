"""
Gladius Games — https://gladiusgames.net/150-eventos

PrestaShop site. Plain HTTP returns 403 (LiteSpeed WAF); Playwright required.
Events are product cards at /150-eventos. Dates are embedded in product titles:
  "1º TORNEO PAUPER GLADIUS GAMES - 21/05/2026"
  "Liga Nexus Night - 17/05/26"
  "Torneo Star Wars Unlimited 15/05/26"

Date format: DD/MM/YYYY or DD/MM/YY.

Location: Calle Cánovas del Castillo 5, 28807 Alcalá de Henares (Madrid)
"""

import json
import logging
import re
import sys
from datetime import datetime, date
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

STORE = "Gladius Games"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
PAGE_URL = "https://gladiusgames.net/150-eventos"
BASE_URL = "https://gladiusgames.net"

NAV_TIMEOUT = 30_000
PAGE_WAIT_MS = 3_000

# "21/05/2026" or "21/05/26"
_DATE_RE = re.compile(r'(\d{1,2})/(\d{2})/(\d{2,4})')
# Optional time "HH:MM" or "HHh"
_TIME_RE = re.compile(r'\b(\d{1,2})[hH:](\d{2})?\b')


def _extract_game(text: str) -> Optional[str]:
    return extract_game_from_keywords(text, GAME_KEYWORDS)


def _parse_datetime(title: str) -> Optional[str]:
    m = _DATE_RE.search(title)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    tm = _TIME_RE.search(title)
    hour = int(tm.group(1)) if tm else 12
    minute = int(tm.group(2)) if (tm and tm.group(2)) else 0
    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=TZ)
        return dt.isoformat()
    except ValueError:
        return None


def _parse_event(article, scraped_at: str) -> Optional[dict]:
    # Title from h2/h3 element or img alt
    title_el = article.select_one("h2, h3, .product-title")
    if title_el:
        title = title_el.get_text(strip=True)
    else:
        img = article.select_one("img[alt]")
        title = img.get("alt", "").strip() if img else ""
    if not title:
        return None

    datetime_start = _parse_datetime(title)
    if not datetime_start:
        return None

    href_el = article.select_one("a.thumbnail.product-thumbnail, h2 a, h3 a, a[href*='/torneos']")
    href = href_el.get("href") if href_el else None

    game = _extract_game(title)
    fmt, fmt_official = extract_format_for_event(title=title, game=game)

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "format_official": fmt_official,
        "best_of": extract_best_of(title),
        "title": title,
        "datetime_start": datetime_start,
        "datetime_end": None,
        "language": LANGUAGE,
        "source_url": href or PAGE_URL,
        "scraped_at": scraped_at,
    }


def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
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
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        context.set_default_navigation_timeout(NAV_TIMEOUT)
        page = context.new_page()

        try:
            page.goto(PAGE_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(PAGE_WAIT_MS)

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            articles = soup.select("article.product-miniature, .js-product-miniature")
            logger.info("%s: found %d product cards", STORE, len(articles))

            for article in articles:
                try:
                    parsed = _parse_event(article, scraped_at)
                    if parsed:
                        events.append(parsed)
                except Exception as exc:
                    logger.warning("%s: skipping product: %s", STORE, exc)

            logger.info("%s: parsed %d events", STORE, len(events))

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
