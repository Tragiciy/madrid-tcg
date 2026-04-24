"""
aggregator.py — собирает события от всех скраперов, валидирует и сохраняет.

Запуск:
    python aggregator.py
"""

import importlib
import json
import logging
import pathlib
import sys
from collections import Counter
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Madrid")
SCRAPERS_DIR = pathlib.Path("scrapers")
EVENTS_FILE = pathlib.Path("public/events.json")
STATS_FILE = pathlib.Path("public/events_stats.json")


# ---------------------------------------------------------------------------
# Загрузка скраперов
# ---------------------------------------------------------------------------

def _discover_scrapers() -> list[str]:
    """
    Возвращает список имён модулей из папки scrapers/, кроме __init__.py.
    Например: ['scrapers.micelion_games']
    """
    return [
        f"scrapers.{p.stem}"
        for p in sorted(SCRAPERS_DIR.glob("*.py"))
        if p.stem != "__init__"
    ]


def _run_scraper(module_name: str) -> tuple:
    """
    Импортирует модуль и вызывает scrape().
    Возвращает (события, None) при успехе или ([], сообщение_об_ошибке) при сбое.
    """
    try:
        module = importlib.import_module(module_name)
        events = module.scrape()
        logger.info("%s → %d событий", module_name, len(events))
        return events, None
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.error("Скрапер %s упал: %s", module_name, msg)
        return [], msg


# ---------------------------------------------------------------------------
# Валидация
# ---------------------------------------------------------------------------

def _parse_dt(value: str) -> Optional[datetime]:
    """Парсит ISO 8601 строку в datetime. При ошибке возвращает None."""
    try:
        dt = datetime.fromisoformat(value)
        # Если нет timezone — считаем Madrid
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def _validate(event: dict, now: datetime) -> bool:
    """
    Проверяет обязательные поля и что событие не в прошлом.
    Возвращает True если событие валидно.
    """
    for field in ("store", "title", "datetime_start"):
        if not event.get(field):
            logger.debug("Пропуск: отсутствует поле '%s' — %s", field, event)
            return False

    dt = _parse_dt(event["datetime_start"])
    if dt is None:
        logger.warning(
            "Пропуск: datetime_start не парсится — %s / %s",
            event.get("title"), event.get("datetime_start"),
        )
        return False

    if dt < now:
        return False

    return True


# ---------------------------------------------------------------------------
# Нормализация game
# ---------------------------------------------------------------------------

# Ключи — все известные варианты написания (в нижнем регистре).
# Значения — каноническое название.
GAME_CANONICAL: dict = {
    # Magic
    "magic":              "Magic: The Gathering",
    "magic: the gathering": "Magic: The Gathering",
    "mtg":                "Magic: The Gathering",
    # Pokémon
    "pokemon":            "Pokémon",
    "pokémon":            "Pokémon",
    # One Piece
    "one piece":          "One Piece",
    # Digimon
    "digimon":            "Digimon",
    # Lorcana
    "lorcana":            "Lorcana",
    # Star Wars
    "star wars":          "Star Wars: Unlimited",
    "star wars unlimited": "Star Wars: Unlimited",
    "swu":                "Star Wars: Unlimited",
    # Yu-Gi-Oh
    "yu-gi-oh":           "Yu-Gi-Oh!",
    "yugioh":             "Yu-Gi-Oh!",
    "yu-gi-oh!":          "Yu-Gi-Oh!",
    # Flesh and Blood
    "flesh and blood":    "Flesh and Blood",
    "fab":                "Flesh and Blood",
    # Weiss Schwarz
    "weiss":              "Weiß Schwarz",
    "weiss schwarz":      "Weiß Schwarz",
    "weiß schwarz":       "Weiß Schwarz",
    # Star Wars (дополнительные варианты из title)
    "liga star wars":          "Star Wars: Unlimited",
    "star wars unlimited":     "Star Wars: Unlimited",
    "star wars":               "Star Wars: Unlimited",
    # Magic (RCQ — Regional Championship Qualifier)
    "rcq":                     "Magic: The Gathering",
    # Riftbound
    "riftbound":               "Riftbound",
    "nexus nights":            "Riftbound",
    "nexus night":             "Riftbound",
    "noob nexus night":        "Riftbound",
    # Naruto
    "naruto mythos":           "Naruto Mythos",
}


ALLOWED_GAMES: set = {
    "Magic: The Gathering",
    "Pokémon",
    "One Piece",
    "Digimon",
    "Yu-Gi-Oh!",
    "Lorcana",
    "Star Wars: Unlimited",
    "Weiß Schwarz",
    "Riftbound",
    "Naruto Mythos",
}


def _normalize_game(game: Optional[str], title: str = "") -> Optional[str]:
    """
    1. Ищет точное совпадение game.lower() в GAME_CANONICAL.
    2. Если не нашло — ищет любой ключ GAME_CANONICAL как подстроку в title.lower().
       Ключи проверяются от длинных к коротким (специфичные раньше общих).
    3. Если результат не в ALLOWED_GAMES — возвращает None.
    """
    # Шаг 1: точное совпадение по значению game из скрапера
    if game:
        canonical = GAME_CANONICAL.get(game.lower())
        if canonical:
            return canonical if canonical in ALLOWED_GAMES else None

    # Шаг 2: поиск подстрокой в title (длинные ключи проверяем первыми)
    lower_title = title.lower()
    for keyword in sorted(GAME_CANONICAL, key=len, reverse=True):
        if keyword in lower_title:
            canonical = GAME_CANONICAL[keyword]
            return canonical if canonical in ALLOWED_GAMES else None

    return None


# ---------------------------------------------------------------------------
# Дедупликация
# ---------------------------------------------------------------------------

def _deduplicate(events: list[dict]) -> list[dict]:
    """Оставляет первое вхождение по ключу (store, title, datetime_start)."""
    seen: dict = {}
    for event in events:
        key = (event["store"], event["title"], event["datetime_start"])
        if key not in seen:
            seen[key] = event
    return list(seen.values())


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    now = datetime.now(tz=TZ)
    generated_at = now.isoformat()

    # --- Сбор событий ---
    all_raw: list[dict] = []
    failed_scrapers: list[str] = []

    for module_name in _discover_scrapers():
        events, error = _run_scraper(module_name)
        if error:
            failed_scrapers.append(module_name)
        all_raw.extend(events)

    logger.info("Собрано сырых событий: %d", len(all_raw))

    # --- Валидация ---
    valid = [e for e in all_raw if _validate(e, now)]
    logger.info("После валидации: %d (отброшено %d)", len(valid), len(all_raw) - len(valid))

    # --- Нормализация game ---
    by_game_before = Counter(e.get("game") or "Unknown" for e in valid)
    for event in valid:
        event["game"] = _normalize_game(event.get("game"), event.get("title", ""))
    by_game_after = Counter(e.get("game") or "Unknown" for e in valid)

    # --- Дедупликация ---
    deduped = _deduplicate(valid)
    if len(deduped) != len(valid):
        logger.info(
            "После дедупликации: %d (удалено %d дублей)",
            len(deduped), len(valid) - len(deduped),
        )

    # --- Сортировка ---
    deduped.sort(key=lambda e: e["datetime_start"])

    # --- Сохранение events.json ---
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.write_text(
        json.dumps(deduped, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Записано: %s (%d событий)", EVENTS_FILE, len(deduped))

    # --- Статистика ---
    by_store = Counter(e["store"] for e in deduped)
    by_game  = Counter(e.get("game") or "Unknown" for e in deduped)

    stats = {
        "total_events": len(deduped),
        "by_store": dict(by_store.most_common()),
        "by_game": dict(by_game.most_common()),
        "failed_scrapers": failed_scrapers,
        "generated_at": generated_at,
    }
    STATS_FILE.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Записано: %s", STATS_FILE)

    # --- Итог в stdout ---
    print(f"\nСобрано событий: {len(deduped)}")
    none_after = sum(1 for e in valid if e.get("game") is None)
    print("\nby_game ДО нормализации:")
    print(json.dumps(dict(by_game_before.most_common()), ensure_ascii=False, indent=2))
    print("\nby_game ПОСЛЕ нормализации + фильтрации:")
    print(json.dumps(dict(by_game_after.most_common()), ensure_ascii=False, indent=2))
    print(f"\ngame=None после фильтрации: {none_after} из {len(valid)}")
    print("\nevents_stats.json:")
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    if failed_scrapers:
        print(f"\nВНИМАНИЕ: упавшие скраперы: {failed_scrapers}", file=sys.stderr)


if __name__ == "__main__":
    main()
