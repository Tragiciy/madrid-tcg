"""
Скрапер для Micelion Games — https://miceliongames.com/calendario-de-torneos/

Сайт использует WordPress-плагин WCS Timetable (Vue.js-календарь).
Данные не в HTML — приходят через AJAX-запрос к /wp-admin/admin-ajax.php.
Playwright не нужен: достаточно requests + прямой вызов API.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
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

# Словарь игр для извлечения из title (порядок важен: проверяем сверху вниз)
GAME_KEYWORDS = [
    ("one piece", "One Piece"),
    ("yu-gi-oh", "Yu-Gi-Oh"),
    ("yugioh", "Yu-Gi-Oh"),
    ("pokemon", "Pokémon"),
    ("pokémon", "Pokémon"),
    ("digimon", "Digimon"),
    ("lorcana", "Lorcana"),
    ("magic", "Magic"),
]

# Словарь форматов: ключ — что ищем (без учёта регистра), значение — каноничная форма
# Порядок важен: более длинные/специфичные фразы — раньше
FORMAT_KEYWORDS = [
    ("store championship", "Store Championship"),
    ("prerelease", "Prerelease"),
    ("commander", "Commander"),
    ("standard", "Standard"),
    ("pioneer", "Pioneer"),
    ("modern", "Modern"),
    ("legacy", "Legacy"),
    ("pauper", "Pauper"),
    ("sealed", "Sealed"),
    ("draft", "Draft"),
    ("league", "League"),
    ("weekly", "Weekly"),
    ("casual", "Casual"),
    ("bo3", "BO3"),
    ("bo1", "BO1"),
]

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
    Конвертирует ISO 8601 строку из API (UTC или с offset) в Europe/Madrid.
    Возвращает строку вида '2026-04-25T12:00:00+02:00'.
    """
    # API отдаёт +00:00, но фактически это уже Madrid-время (offset=0 зимой / +2 летом)
    # На самом деле сервер возвращает UTC значения — конвертируем явно
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).isoformat()


def _extract_price(text: str) -> Optional[float]:
    """
    Поддерживает форматы: 5€  5 €  5.50€  5,50€  5.50 €
    Запятая заменяется на точку перед конвертацией. При любой ошибке — None.
    """
    match = re.search(r"(\d+[.,]\d+|\d+)\s*€", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _extract_game(wcs_name: Optional[str], title: str) -> Optional[str]:
    """
    Возвращает название игры.
    1. Берём wcs_name из API, если это не обобщённая категория.
    2. Иначе ищем по словарю GAME_KEYWORDS в title.
    3. Если не нашли — None.
    """
    if wcs_name and wcs_name.lower() not in _GENERIC_WCS_TYPES:
        return wcs_name

    lower_title = title.lower()
    for keyword, canonical in GAME_KEYWORDS:
        if keyword in lower_title:
            return canonical

    return None


def _extract_format(text: str) -> Optional[str]:
    """
    Ищет форматы TCG в тексте (title + excerpt).
    Проверяет FORMAT_KEYWORDS по порядку — возвращает первое совпадение.
    """
    lower = text.lower()
    for keyword, canonical in FORMAT_KEYWORDS:
        if keyword in lower:
            return canonical
    return None


def _parse_event(raw: dict, scraped_at: str) -> dict:
    """Преобразует один элемент API-ответа в словарь по итоговой схеме."""

    # --- game ---
    title = raw.get("title", "").strip()
    wcs_types = raw.get("terms", {}).get("wcs_type", [])
    wcs_name = wcs_types[0].get("name") if wcs_types else None
    game = _extract_game(wcs_name, title)

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
    combined_text = f"{raw.get('title', '')} {excerpt_text}"

    # --- price ---
    price_eur = _extract_price(combined_text)

    # --- format ---
    fmt = _extract_format(combined_text)

    # --- source_url ---
    source_url = raw.get("permalink") or None

    return {
        "store": STORE,
        "game": game,
        "format": fmt,
        "title": title,
        "datetime_start": datetime_start,
        "datetime_end": datetime_end,
        "price_eur": price_eur,
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
