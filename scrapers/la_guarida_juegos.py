"""
La Guarida Juegos — https://laguaridajuegos.com/calendario-de-evento/

The site embeds the Simple Calendar WordPress plugin. The initial page
contains the current month as HTML, and month navigation calls
/wp-admin/admin-ajax.php with action=simcal_default_calendar_draw_grid.

Strategy:
  - GET the calendar page to discover the Simple Calendar id.
  - POST the same AJAX action the frontend uses for each month in the
    next DAYS_AHEAD window.
  - Parse the returned HTML with BeautifulSoup. No Playwright needed.
"""

import html as _html
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:
    from shared.scraper_keywords import GAME_KEYWORDS, extract_game_from_keywords
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from shared.scraper_keywords import GAME_KEYWORDS, extract_game_from_keywords

logger = logging.getLogger(__name__)

STORE = "La Guarida Juegos"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
PAGE_URL = "https://laguaridajuegos.com/calendario-de-evento/"
AJAX_URL = "https://laguaridajuegos.com/wp-admin/admin-ajax.php"
DAYS_AHEAD = 90
DEFAULT_CALENDAR_ID = "1678"

FORMAT_KEYWORDS: list[tuple[str, str]] = [
    ("store championship", "Store Championship"),
    ("pre-release", "Prerelease"),
    ("prerelease", "Prerelease"),
    ("presentaciones", "Prerelease"),
    ("presentación", "Prerelease"),
    ("presentacion", "Prerelease"),
    ("sellados", "Sealed"),
    ("sellado", "Sealed"),
    ("competitive elder dragon highlander", "cEDH"),
    ("cedh", "cEDH"),
    ("commander", "Commander"),
    ("armory", "Armory"),
    ("formato premier", "Premier"),
    ("premier", "Premier"),
    ("formato standard", "Standard"),
    ("standard", "Standard"),
    ("formato pioneer", "Pioneer"),
    ("pioneer", "Pioneer"),
    ("formato modern", "Modern"),
    ("modern", "Modern"),
    ("legacy", "Legacy"),
    ("pauper", "Pauper"),
    ("sealed", "Sealed"),
    ("draft", "Draft"),
    ("weekly", "Weekly"),
    ("casual", "Casual"),
    ("bo3", "BO3"),
    ("bo1", "BO1"),
    ("rcq", "Store Championship"),
    # League is intentionally late, and "League of Legends" is removed
    # before scanning so Riftbound's product subtitle is not a format.
    ("liga", "League"),
    ("league", "League"),
]

_DROP_TITLES = {
    "cerrado",
    "cerrados",
    "cerrada",
    "cerradas",
}

_REJECT_PREFIXES = (
    "¿", "?",
    "ven a", "aprende a jugar", "si quieres", "para reservar",
    "recordad", "calendario", "jornada", "horario", "formato de",
)

_REJECT_TOKENS = (
    "premios", "regalos", "consiste", "reservar", "plazas",
    "inscripción", "inscripcion",
)

_STRONG_PATTERNS = (
    "liga", "formato", "presentación", "presentacion",
    "prerelease", "pre-release", "torneo", "fnm", "commander",
    "cedh", "draft", "skirmish", "armory", "nexus nights",
)

_SPANISH_FMT_LABEL = {
    "Prerelease": "Presentación",
    "Sealed": "Sellado",
    "League": "Liga",
}

_SHORT_GAME = {
    "Magic: The Gathering": "Magic",
    "Star Wars: Unlimited": "SWU",
    "Yu-Gi-Oh!": "Yu-Gi-Oh",
    "Weiß Schwarz": "Weiss",
}

_KNOWN_SETS = (
    ("omens of the third age", "Omens of the Third Age"),
    ("strixhaven", "Strixhaven"),
    ("unleashed", "Unleashed"),
    # Current calendar has a typo in several Riftbound entries.
    ("unleased", "Unleashed"),
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json",
    "Referer": PAGE_URL,
}


def _now_madrid() -> datetime:
    return datetime.now(tz=TZ)


def _month_starts(start: datetime, end: datetime) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        month += 1
        if month == 13:
            month = 1
            year += 1
    return months


def _discover_calendar_id(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    cal = soup.select_one(".simcal-calendar[data-calendar-id]")
    return cal.get("data-calendar-id") if cal else DEFAULT_CALENDAR_ID


def _fetch_month(session: requests.Session, calendar_id: str,
                 year: int, month: int) -> str:
    resp = session.post(
        AJAX_URL,
        data={
            "action": "simcal_default_calendar_draw_grid",
            "month": month,
            "year": year,
            "id": calendar_id,
        },
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data") or "" if data.get("success") else ""


def _to_madrid_iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        else:
            dt = dt.astimezone(TZ)
        return dt.isoformat()
    except Exception:
        return None


def _text_for_scan(text: str) -> str:
    # Avoid detecting "League" as a format in "Riftbound - League of Legends".
    return re.sub(r"(?i)league\s+of\s+legends", " ", text or "")


def _extract_format(text: str) -> Optional[str]:
    lower = _text_for_scan(text).lower()
    for keyword, canonical in FORMAT_KEYWORDS:
        if keyword in lower:
            return canonical
    return None


def _smart_case(text: str) -> str:
    upper = {"SWU", "MTG", "FNM", "TCG", "BO1", "BO3",
             "EDH", "CEDH", "RCQ", "FAB"}
    small = {"de", "del", "y", "en", "con", "la", "el",
             "los", "las", "a", "un", "una", "of", "the"}
    out: list[str] = []
    for part in re.split(r"(\s+|[–—\-:])", text):
        if not part or part.isspace() or part in "–—-:":
            out.append(part)
            continue
        u = part.upper()
        low = part.lower()
        if u in upper:
            out.append(u if u != "CEDH" else "cEDH")
        elif low in small and any(o.strip() for o in out):
            out.append(low)
        elif part.isupper():
            out.append(part.capitalize())
        else:
            out.append(part[0].upper() + part[1:])
    return "".join(out).strip()


def _is_acceptable_title(text: str) -> bool:
    if not text:
        return False
    t = text.replace("\xa0", " ").strip()
    if not t:
        return False
    low = t.lower().strip(" .:-–—")
    if low in _DROP_TITLES:
        return False
    if "?" in t or "¿" in t:
        return False
    if any(low.startswith(prefix) for prefix in _REJECT_PREFIXES):
        return False
    if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3,}", t):
        return False
    has_strong = any(p in low for p in _STRONG_PATTERNS)
    if not has_strong and any(tok in low for tok in _REJECT_TOKENS):
        return False
    return len(t) <= (110 if has_strong else 70)


def _normalise_set(text: str) -> Optional[str]:
    lower = (text or "").lower()
    for needle, label in _KNOWN_SETS:
        if needle in lower:
            return label
    return None


def _synthesise_title(game: Optional[str], fmt: Optional[str],
                      set_name: Optional[str]) -> Optional[str]:
    parts: list[str] = []
    if fmt:
        parts.append(_SPANISH_FMT_LABEL.get(fmt, fmt))
    if game:
        parts.append(_SHORT_GAME.get(game, game))
    if set_name:
        parts.append(set_name)
    return " ".join(parts) if parts else None


def _drop_game_prefix(parts: list[str], game: Optional[str]) -> list[str]:
    if not parts:
        return parts
    first = parts[0].lower()
    game_tokens = {
        "mtg", "magic", "magic the gathering", "fab", "flesh and blood",
        "riftbound", "pokemon", "pokémon", "one piece", "digimon",
        "lorcana", "swu", "star wars", "star wars unlimited",
        "yu-gi-oh", "yugioh", "weiss", "weiß schwarz",
    }
    if game and first in game_tokens:
        return parts[1:]
    return parts


def _clean_title(raw_title: str, game: Optional[str],
                 fmt: Optional[str]) -> Optional[str]:
    raw = _html.unescape(raw_title or "").replace("\xa0", " ").strip()
    raw = re.sub(r"\s+", " ", raw).strip(" .")
    if not _is_acceptable_title(raw):
        return None

    set_name = _normalise_set(raw)
    low = raw.lower()
    if fmt == "Prerelease" and (game or set_name):
        synth = _synthesise_title(game, fmt, set_name)
        if synth:
            return synth

    parts = [p.strip() for p in re.split(r"\s+[–—\-]\s+", raw) if p.strip()]
    parts = _drop_game_prefix(parts, game)
    cleaned: list[str] = []
    for part in parts:
        p_low = part.lower().strip(" .:-")
        if not p_low:
            continue
        if p_low in {"calendario", "horario"}:
            continue
        if re.match(r"(?i)^\d+\s+jornadas?$", part):
            continue
        if p_low == "league of legends" and game == "Riftbound":
            continue
        m = re.match(r"(?i)^formato\s+([\wÁÉÍÓÚáéíóúñÑ\-]+)$", part)
        if m:
            cleaned.append(m.group(1).capitalize())
            continue
        if game and p_low in {"mtg", "magic", "fab", "riftbound", "swu"}:
            continue
        cleaned.append(part)

    title = " – ".join(cleaned).strip(" .–—-:") if cleaned else raw
    if title.lower() in _DROP_TITLES:
        return None
    if title == raw and any(c.isalpha() for c in title):
        letters = [c for c in title if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            title = _smart_case(title)
    else:
        title = _smart_case(title)

    title = re.sub(r"(?i)\bpresentacion\b", "Presentación", title)
    title = re.sub(r"(?i)\bunleased\b", "Unleashed", title)
    if not _is_acceptable_title(title):
        return None
    if fmt and game and title.lower() in {fmt.lower(), game.lower()}:
        return _synthesise_title(game, fmt, set_name)
    # If only low-signal pieces remain, use the stronger synthesized form.
    if set_name and fmt and game and low.startswith(("mtg", "riftbound", "fab")):
        return _synthesise_title(game, fmt, set_name)
    return title


def _parse_event(node, scraped_at: str) -> Optional[dict]:
    title_node = node.select_one(":scope > .simcal-event-title")
    raw_title = title_node.get_text(" ", strip=True) if title_node else ""
    details = node.select_one(".simcal-event-details") or node
    detail_text = details.get_text(" ", strip=True)
    combined_text = f"{raw_title} {detail_text}"

    game = extract_game_from_keywords(combined_text, GAME_KEYWORDS)
    fmt = _extract_format(combined_text)
    title = _clean_title(raw_title, game, fmt)
    if not title:
        return None

    start_node = details.select_one(".simcal-event-start-date[itemprop='startDate']")
    if not start_node:
        start_node = details.select_one("[itemprop='startDate'][content]")
    datetime_start = _to_madrid_iso(start_node.get("content") if start_node else None)
    if not datetime_start:
        return None

    end_node = details.select_one("[itemprop='endDate'][content]")
    datetime_end = _to_madrid_iso(end_node.get("content") if end_node else None)
    if datetime_end == datetime_start:
        datetime_end = None

    link = details.select_one("a[href]")
    source_url = link.get("href") if link else PAGE_URL

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


def _parse_events(html: str, scraped_at: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    events: list[dict] = []
    for node in soup.select("li.simcal-event"):
        try:
            event = _parse_event(node, scraped_at)
            if event:
                events.append(event)
        except Exception as exc:
            logger.warning("%s: skipping event: %s", STORE, exc)
    return events


def scrape() -> list[dict]:
    now = _now_madrid()
    scraped_at = now.isoformat()
    end = now.replace(day=1)
    for _ in range(4):
        month = end.month + 1
        year = end.year
        if month == 13:
            month = 1
            year += 1
        end = end.replace(year=year, month=month)
    months = _month_starts(now, end)

    session = requests.Session()
    page_resp = session.get(PAGE_URL, headers=HEADERS, timeout=15)
    page_resp.raise_for_status()
    calendar_id = _discover_calendar_id(page_resp.text)

    events: list[dict] = []
    for year, month in months:
        try:
            html = _fetch_month(session, calendar_id, year, month)
        except Exception as exc:
            logger.warning("%s: month %04d-%02d failed: %s", STORE, year, month, exc)
            continue
        parsed = _parse_events(html, scraped_at)
        logger.info("%s: %04d-%02d → %d events", STORE, year, month, len(parsed))
        events.extend(parsed)

    seen: dict[tuple[str, str, str], dict] = {}
    for event in events:
        if event["datetime_start"] < now.isoformat():
            continue
        key = (event["title"], event["datetime_start"], event["store"])
        seen.setdefault(key, event)

    deduped = list(seen.values())
    deduped.sort(key=lambda e: e["datetime_start"])
    logger.info("%s: total events returned: %d", STORE, len(deduped))
    return deduped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
