"""
Скрапер для Micelion Games — https://miceliongames.com/calendario-de-torneos/

Сайт использует WordPress-плагин WCS Timetable (Vue.js-календарь).
Данные не в HTML — приходят через AJAX-запрос к /wp-admin/admin-ajax.php.
Playwright не нужен: достаточно requests + прямой вызов API.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

STORE = "Micelion Games"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
AJAX_URL = "https://miceliongames.com/wp-admin/admin-ajax.php"
DAYS_AHEAD = 90

# Категории WCS, которые не являются названием игры
_GENERIC_WCS_TYPES = {"destacados", "featured", "general"}

from shared.scraper_keywords import GAME_KEYWORDS, FORMAT_KEYWORDS, extract_game_from_keywords, extract_format_from_keywords

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://miceliongames.com/calendario-de-torneos/",
}


def _now_madrid() -> datetime:
    return datetime.now(tz=TZ)


def _to_madrid_iso(iso_str: str) -> str:
    """
    The WCS plugin stamps Madrid wall-clock time with a fake "+00:00"
    offset (e.g. "2026-04-26T10:00:00+00:00" actually means 10:00 in
    Madrid, not 10:00 UTC — verified against the source page's "10:00h"
    label and the event's wcs_timestamp URL parameter).

    So we *reinterpret* the offset as Europe/Madrid rather than
    converting. Returns e.g. '2026-04-26T10:00:00+02:00'.
    """
    dt = datetime.fromisoformat(iso_str)
    # Drop whatever offset the API claims (+00:00 in practice) and
    # re-stamp the value as Europe/Madrid local time.
    dt = dt.replace(tzinfo=TZ)
    return dt.isoformat()


def _extract_game(wcs_name: Optional[str], title: str) -> Optional[str]:
    """
    Возвращает название игры.
    1. Берём wcs_name из API, если это не обобщённая категория.
    2. Иначе ищем по словарю GAME_KEYWORDS в title.
    3. Если не нашли — None.
    """
    if wcs_name and wcs_name.lower() not in _GENERIC_WCS_TYPES:
        return wcs_name

    return extract_game_from_keywords(re.sub(r"[-_/]+", " ", title.lower()), GAME_KEYWORDS)


def _extract_format(text: str) -> Optional[str]:
    return extract_format_from_keywords(text, FORMAT_KEYWORDS)


def _clean_title(raw_title: str, source_url: Optional[str]) -> str:
    """Keep Micelion titles short and event-like when the slug has context."""
    title = (raw_title or "").strip()

    slug_text = re.sub(r"[-_/]+", " ", (source_url or "").lower())
    if (
        "riftbound" in slug_text
        and "unleashed" in title.lower()
        and "riftbound" not in title.lower()
    ):
        title = re.sub(r"\s*·\s*micelion games\s*$", "", title, flags=re.I)
        title = re.sub(
            r"(?i)\bmaster\s+unleashed\b",
            "Riftbound Unleashed",
            title,
        )
        title = re.sub(r"(?i)\bpresentaci[oó]n\b", "Presentación", title)
    return title.strip()


def _parse_event(raw: dict, scraped_at: str) -> dict:
    """Преобразует один элемент API-ответа в словарь по итоговой схеме."""

    # --- game ---
    raw_title = raw.get("title", "").strip()
    source_url = raw.get("permalink") or None
    title = _clean_title(raw_title, source_url)
    wcs_types = raw.get("terms", {}).get("wcs_type", [])
    wcs_name = wcs_types[0].get("name") if wcs_types else None
    game = _extract_game(wcs_name, f"{title} {source_url or ''}")

    # --- datetime_start: обязательное поле, при ошибке бросаем — caller пропустит ---
    datetime_start = _to_madrid_iso(raw["start"])

    # --- datetime_end: необязательное, при любой ошибке → None ---
    try:
        raw_end = raw.get("end")
        datetime_end = _to_madrid_iso(raw_end) if raw_end else None
    except Exception:
        datetime_end = None

    # --- plain text из excerpt для regex ---
    excerpt_html = raw.get("excerpt") or ""
    excerpt_text = BeautifulSoup(excerpt_html, "html.parser").get_text(" ", strip=True)
    combined_text = f"{raw_title} {title} {excerpt_text} {source_url or ''}"

    # --- format ---
    fmt = _extract_format(combined_text)

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "title": title,
        "datetime_start": datetime_start,
        "datetime_end": datetime_end,
        "language": LANGUAGE,
        "source_url": source_url,
        "scraped_at": scraped_at,
    }


def scrape() -> list[dict]:
    """
    Запрашивает события Micelion Games на ближайшие 90 дней.
    Возвращает список словарей по итоговой схеме.
    Ошибка одного события логируется как warning — остальные продолжают парситься.
    """
    now = _now_madrid()
    start = now.date()
    end = start + timedelta(days=DAYS_AHEAD)
    scraped_at = now.isoformat()

    response = requests.post(
        AJAX_URL,
        data={
            "action": "wcs_get_events_json",
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    raw_events: list[dict] = response.json()

    events = []
    for raw in raw_events:
        try:
            events.append(_parse_event(raw, scraped_at))
        except Exception as exc:
            logger.warning("Пропускаем событие id=%s: %s", raw.get("id"), exc)

    total_before = len(events)

    # Дедупликация: ключ = (title, datetime_start, store)
    # dict сохраняет порядок вставки — оставляем первое вхождение
    seen: dict = {}
    for event in events:
        key = (event["title"], event["datetime_start"], event["store"])
        if key not in seen:
            seen[key] = event
    events = list(seen.values())

    total_after = len(events)
    if total_before != total_after:
        logger.info(
            "Дедупликация: было %d → стало %d (удалено %d)",
            total_before, total_after, total_before - total_after,
        )

    events.sort(key=lambda e: e["datetime_start"])
    return events


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    events = scrape()
    print(json.dumps(events[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(events)}")
