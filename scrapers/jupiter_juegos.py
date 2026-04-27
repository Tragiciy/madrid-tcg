"""
Jupiter Juegos — https://www.jupiterjuegos.com/tiendas/?tienda=jupiter-madrid

The Madrid store calendar is rendered in the initial HTML. Month navigation
is handled by the same page with ?tienda=jupiter-madrid&fecha=DD-MM-YYYY.

Strategy:
  - GET each month in the next DAYS_AHEAD window.
  - Parse the calendar grid with BeautifulSoup.
  - Use the category legend + title text to keep supported TCG events only.
  - Do not fetch activity detail pages: they describe recurring activities,
    while the calendar grid is the per-occurrence source of truth.
"""

import html as _html
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:
    from aggregator import ALLOWED_GAMES, GAME_CANONICAL
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aggregator import ALLOWED_GAMES, GAME_CANONICAL

logger = logging.getLogger(__name__)

STORE = "Jupiter Juegos"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
PAGE_URL = "https://www.jupiterjuegos.com/tiendas/"
SOURCE_URL = "https://www.jupiterjuegos.com/tiendas/?tienda=jupiter-madrid"
DAYS_AHEAD = 90

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Referer": SOURCE_URL,
}

MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

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
    ("commander nights", "Commander"),
    ("commander", "Commander"),
    ("formato premier", "Premier"),
    ("premier", "Premier"),
    ("formato standard", "Standard"),
    ("standard", "Standard"),
    ("formato pioneer", "Pioneer"),
    ("pioneer", "Pioneer"),
    ("formato modern", "Modern"),
    ("premodern", "Modern"),
    ("modern 2015", "Modern"),
    ("modern", "Modern"),
    ("legacy", "Legacy"),
    ("pauper", "Pauper"),
    ("sealed", "Sealed"),
    ("draft", "Draft"),
    ("weekly", "Weekly"),
    ("casual", "Casual"),
    ("bo3", "BO3"),
    ("bo1", "BO1"),
    ("liga", "League"),
    ("league", "League"),
]

_TRAILING_SCHEDULE = re.compile(
    r"\s+(?:lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)"
    r"(?:\s+(?:mañana|manana|tarde|noche))?\s*$",
    re.I,
)


def _now_madrid() -> datetime:
    return datetime.now(tz=TZ)


def _month_starts(start: datetime, end: datetime) -> list[datetime]:
    months: list[datetime] = []
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur <= last:
        months.append(cur)
        year = cur.year + (cur.month // 12)
        month = 1 if cur.month == 12 else cur.month + 1
        cur = cur.replace(year=year, month=month)
    return months


def _add_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month0 = month - 1 + delta
    return year + month0 // 12, month0 % 12 + 1


def _fetch_month(session: requests.Session, month_start: datetime) -> str:
    resp = session.get(
        PAGE_URL,
        params={
            "tienda": "jupiter-madrid",
            "fecha": month_start.strftime("%d-%m-%Y"),
        },
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.text


def _extract_display_month(soup: BeautifulSoup) -> Optional[tuple[int, int]]:
    text = soup.get_text(" ", strip=True).lower()
    m = re.search(
        r"\b(" + "|".join(MONTHS_ES) + r")\s+(\d{4})\b",
        text,
    )
    if not m:
        return None
    return int(m.group(2)), MONTHS_ES[m.group(1)]


def _extract_game(text: str) -> Optional[str]:
    lower = (text or "").lower()
    for keyword in sorted(GAME_CANONICAL, key=len, reverse=True):
        if keyword in lower:
            canonical = GAME_CANONICAL[keyword]
            return canonical if canonical in ALLOWED_GAMES else None
    return None


def _extract_format(text: str) -> Optional[str]:
    lower = (text or "").lower()
    for keyword, canonical in FORMAT_KEYWORDS:
        if keyword in lower:
            return canonical
    return None


def _smart_case(text: str) -> str:
    keep_upper = {"MTG", "TCG", "SWU", "RCQ", "BO1", "BO3"}
    small = {
        "de", "del", "y", "en", "con", "la", "el", "los", "las", "a",
        "of", "the",
    }
    out: list[str] = []
    for part in re.split(r"(\s+|[–—\-:/])", text):
        if not part or part.isspace() or re.fullmatch(r"[–—\-:/]", part):
            out.append(part)
            continue
        upper = part.upper()
        lower = part.lower()
        if upper in keep_upper:
            out.append(upper)
        elif lower in small and any(x.strip() for x in out):
            out.append(lower)
        elif part.isupper():
            out.append(part.capitalize())
        else:
            out.append(part[0].upper() + part[1:])
    return "".join(out).strip()


def _clean_title(raw: str) -> Optional[str]:
    title = _html.unescape(raw or "").replace("\xa0", " ")
    title = re.sub(r"\s+", " ", title).strip(" .:-–—")
    if not title:
        return None

    title = _TRAILING_SCHEDULE.sub("", title).strip(" .:-–—")
    title = re.sub(r"(?i)\bpresentacion\b", "Presentación", title)
    title = re.sub(r"(?i)\bmtg\b", "MTG", title)
    title = re.sub(r"(?i)\brtcg\b", "TCG", title)

    title = _smart_case(title)
    return title if len(title) <= 120 else None


def _category_map(soup: BeautifulSoup) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {}
    for node in soup.select(".act2_0[data-cual]"):
        key = node.get("data-cual")
        text = node.get_text(" ", strip=True)
        if key:
            out[key] = _extract_game(text)
    return out


def _event_category(anchor) -> Optional[str]:
    for cls in anchor.get("class") or []:
        m = re.fullmatch(r"act_(\d+)", cls)
        if m and m.group(1) != "0":
            return m.group(1)
    return None


def _day_cells(soup: BeautifulSoup) -> list[tuple[int, object]]:
    cells: list[tuple[int, object]] = []
    for container in soup.select(".calendario_igualar_alturas"):
        parent = container.parent
        if not parent:
            continue
        header = None
        for child in parent.find_all("div", recursive=False):
            if child is not container:
                header = child
                break
        if not header:
            continue
        first = header.find("div")
        if not first:
            continue
        try:
            day = int(first.get_text(" ", strip=True))
        except ValueError:
            continue
        cells.append((day, container))
    return cells


def _parse_event(anchor, date: datetime, categories: dict[str, Optional[str]],
                 scraped_at: str) -> Optional[dict]:
    parts = [
        d.get_text(" ", strip=True)
        for d in anchor.find_all("div", recursive=False)
    ]
    parts = [p for p in parts if p and p != "..."]
    if len(parts) < 2:
        return None

    title = _clean_title(parts[0])
    if not title:
        return None

    time_match = re.search(r"(\d{1,2}):(\d{2})", parts[1])
    if not time_match:
        return None
    hour, minute = int(time_match.group(1)), int(time_match.group(2))

    category = _event_category(anchor)
    game = categories.get(category) if category else None
    game = game or _extract_game(title)
    if not game:
        return None

    dt = date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    combined = f"{title} {game}"

    return {
        "store": STORE,
        "game": game,
        "format": _extract_format(combined),
        "title": title,
        "datetime_start": dt.isoformat(),
        "datetime_end": None,
        "language": LANGUAGE,
        "source_url": urljoin(SOURCE_URL, anchor.get("href") or SOURCE_URL),
        "scraped_at": scraped_at,
    }


def _parse_month(html: str, scraped_at: str, window_start: datetime,
                 window_end: datetime) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    display = _extract_display_month(soup)
    cells = _day_cells(soup)
    if not display or not cells:
        logger.warning("%s: no calendar grid found", STORE)
        return []

    display_year, display_month = display
    first_day = cells[0][0]
    starts_previous_month = first_day > 1 and any(day == 1 for day, _ in cells)
    year, month = _add_month(display_year, display_month, -1) if starts_previous_month else display
    prev_day = first_day
    categories = _category_map(soup)

    events: list[dict] = []
    skipped = 0
    for day, container in cells:
        if day < prev_day:
            year, month = _add_month(year, month, 1)
        prev_day = day
        try:
            date = datetime(year, month, day, tzinfo=TZ)
        except ValueError:
            continue

        for anchor in container.select("a[href]"):
            try:
                event = _parse_event(anchor, date, categories, scraped_at)
                if not event:
                    skipped += 1
                    continue
                dt = datetime.fromisoformat(event["datetime_start"])
                if window_start <= dt <= window_end:
                    events.append(event)
            except Exception as exc:
                skipped += 1
                logger.warning("%s: skipping event card: %s", STORE, exc)

    if skipped:
        logger.info("%s: skipped %d unsupported/unparseable cards", STORE, skipped)
    return events


def scrape() -> list[dict]:
    now = _now_madrid()
    scraped_at = now.isoformat()
    window_end = now + timedelta(days=DAYS_AHEAD)

    session = requests.Session()
    events: list[dict] = []
    for month_start in _month_starts(now, window_end):
        try:
            html = _fetch_month(session, month_start)
            parsed = _parse_month(html, scraped_at, now, window_end)
            logger.info(
                "%s: %04d-%02d → %d events",
                STORE, month_start.year, month_start.month, len(parsed),
            )
            events.extend(parsed)
        except Exception as exc:
            logger.warning(
                "%s: month %04d-%02d failed: %s",
                STORE, month_start.year, month_start.month, exc,
            )

    seen: dict[tuple[str, str, str], dict] = {}
    for event in events:
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
