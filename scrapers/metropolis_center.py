"""
Скрапер для Metropolis Center — https://metropolis-center.com/events/calendar

Сайт защищён cookie-челленджем (JS устанавливает cookie dhd2=…),
поэтому для первого запроса нужен Playwright. После обхода защиты
все данные лежат в JS-переменной window.evcalEvents на странице —
AJAX не нужен.

Стратегия:
1. Playwright загружает страницу (meta refresh обходит cookie-челлендж).
2. Из JS-контекста читаем window.evcalEvents (текущий месяц).
3. Кликаем «следующий месяц» 2 раза → собираем ещё 2 месяца.
4. Итого ~3 месяца событий, что покрывает 90-дневный горизонт.

Оптимизации:
- Блокируем image/font/stylesheet/media/analytics — ускоряет загрузку в 3-5×.
- Headless + disable-gpu/no-sandbox.
- Таймауты на каждую операцию, чтобы скрапер не висел.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)

STORE = "Metropolis Center"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
BASE_URL = "https://metropolis-center.com/events/calendar"

# Сколько раз кликать «следующий месяц» (0 = только текущий)
EXTRA_MONTHS = 2

# Таймауты (мс)
NAV_TIMEOUT = 30_000
SELECTOR_TIMEOUT = 15_000
EVAL_TIMEOUT = 5_000
CLICK_TIMEOUT = 5_000
AFTER_CLICK_WAIT = 800

# Типы ресурсов, которые нам не нужны для чтения window.evcalEvents
_BLOCKED_RESOURCE_TYPES = {"image", "font", "stylesheet", "media", "imageset"}
# Аналитика/трекинг
_BLOCKED_DOMAINS = (
    "googletagmanager.com",
    "google-analytics.com",
    "doofinder.com",
    "facebook.net",
    "facebook.com/tr",
    "hotjar.com",
    "cloudflareinsights.com",
)

from shared.scraper_keywords import FORMAT_KEYWORDS, extract_format_from_keywords

# Per-event detail fetch timeout (ms). Detail pages are tiny once
# resources are blocked; 12s is generous.
DETAIL_FETCH_TIMEOUT = 12_000


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_format(title: str) -> Optional[str]:
    return extract_format_from_keywords(title, FORMAT_KEYWORDS)


def _make_iso(date_str: str, time_str: str) -> Optional[str]:
    """Combine 'YYYY-MM-DD' + 'HH:MM' into a Madrid-tz ISO 8601 string."""
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=TZ).isoformat()
    except Exception:
        return None


def _parse_event(raw: dict, scraped_at: str) -> Optional[dict]:
    """Convert one entry from evcalEvents into our schema."""
    title = (raw.get("title") or "").strip()
    if not title:
        return None

    date_str = raw.get("date", "")
    datetime_start = _make_iso(date_str, raw.get("start") or "00:00")
    if not datetime_start:
        return None

    datetime_end = _make_iso(date_str, raw.get("end")) if raw.get("end") else None

    return {
        "store":          STORE,
        "game":           raw.get("game") or None,
        "format":         _extract_format(title),
        "title":          title,
        "datetime_start": datetime_start,
        "datetime_end":   datetime_end,
        "language":       LANGUAGE,
        "source_url":     raw.get("link") or None,
        "scraped_at":     scraped_at,
    }


def _fetch_format_from_detail(page, url: str) -> Optional[str]:
    """
    Open the event detail page in the same Playwright context (cookie
    challenge already cleared) and run the keyword scan against the
    rendered text.

    The detail page reliably contains a "Formato Modern" /
    "Formato cEDH" line which our keyword list catches as a substring.
    """
    if not url:
        return None
    try:
        page.goto(url, timeout=DETAIL_FETCH_TIMEOUT, wait_until="domcontentloaded")
        html = page.content()
    except PWTimeout:
        logger.debug("%s: detail timeout %s", STORE, url)
        return None
    except Exception as exc:
        logger.debug("%s: detail fetch failed %s: %s", STORE, url, exc)
        return None

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return _extract_format(text)


def _route_handler(route):
    """Abort unnecessary requests to speed up page load."""
    req = route.request
    try:
        if req.resource_type in _BLOCKED_RESOURCE_TYPES:
            return route.abort()
        url = req.url
        if any(d in url for d in _BLOCKED_DOMAINS):
            return route.abort()
        return route.continue_()
    except Exception:
        # Если route уже обработан — молча игнорируем
        try:
            route.continue_()
        except Exception:
            pass


def _collect_month(page) -> dict:
    """Extract window.evcalEvents from the current page state."""
    try:
        data = page.evaluate(
            "() => window.evcalEvents || {}",
        )
        return data or {}
    except PWTimeout:
        logger.warning("%s: evaluate() timed out", STORE)
        return {}
    except Exception as exc:
        logger.warning("%s: could not read evcalEvents: %s", STORE, exc)
        return {}


# ── Main scrape function ──────────────────────────────────────────────────────

def scrape() -> list[dict]:
    """
    Returns upcoming events for Metropolis Center (next ~3 months).
    Requires Playwright + Chromium.
    """
    scraped_at = datetime.now(tz=TZ).isoformat()
    today_str = date.today().isoformat()
    all_raw: dict = {}
    events: list[dict] = []

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
        # Глобальный таймаут на все операции страницы
        context.set_default_timeout(SELECTOR_TIMEOUT)
        context.set_default_navigation_timeout(NAV_TIMEOUT)

        # Блокируем ненужные ресурсы на уровне контекста
        context.route("**/*", _route_handler)

        page = context.new_page()

        try:
            # Load page — meta refresh (3 s) handles the cookie challenge
            page.goto(BASE_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_selector("h1", timeout=SELECTOR_TIMEOUT)

            # Collect current month
            month_data = _collect_month(page)
            all_raw.update(month_data)
            logger.info("%s: month 1 — %d events across %d days",
                        STORE, sum(len(v) for v in month_data.values()), len(month_data))

            # Navigate forward and collect next months
            for i in range(EXTRA_MONTHS):
                try:
                    nav = page.locator(".evnav-right")
                    if nav.count() == 0:
                        logger.info("%s: .evnav-right not found, stopping", STORE)
                        break
                    nav.first.click(timeout=CLICK_TIMEOUT)
                    page.wait_for_timeout(AFTER_CLICK_WAIT)
                except PWTimeout:
                    logger.warning("%s: month %d click timed out", STORE, i + 2)
                    break
                except Exception as exc:
                    logger.warning("%s: month %d click failed: %s", STORE, i + 2, exc)
                    break

                month_data = _collect_month(page)
                for day, day_evs in month_data.items():
                    if day not in all_raw:
                        all_raw[day] = day_evs
                logger.info("%s: month %d — %d events across %d days",
                            STORE, i + 2, sum(len(v) for v in month_data.values()), len(month_data))

            # ── Detail-page format enrichment ──────────────────────
            # Parse events first, then fetch the detail page only for
            # those whose title didn't already yield a format. The
            # browser context stays open so the cookie challenge isn't
            # re-triggered.
            detail_cache: dict = {}
            for date_str, day_events in all_raw.items():
                if date_str < today_str:
                    continue
                for raw in day_events:
                    try:
                        parsed = _parse_event(raw, scraped_at)
                        if not parsed:
                            continue
                        if parsed.get("format") is None and parsed.get("source_url"):
                            url = parsed["source_url"]
                            if url not in detail_cache:
                                detail_cache[url] = _fetch_format_from_detail(page, url)
                            parsed["format"] = detail_cache[url]
                        events.append(parsed)
                    except Exception as exc:
                        logger.warning("%s: skipping event %r: %s",
                                       STORE, raw.get("title"), exc)

            logger.info("%s: detail fetches=%d  (events without title-format)",
                        STORE, sum(1 for v in detail_cache))

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

    events.sort(key=lambda e: e["datetime_start"])
    logger.info("%s: total events returned: %d", STORE, len(events))
    return events


if __name__ == "__main__":
    import json
    import time
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    t0 = time.time()
    events = scrape()
    elapsed = time.time() - t0
    print(json.dumps(events[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(events)}")
    print(f"Elapsed: {elapsed:.1f}s")
