"""
Júpiter Central (Calle Manuel Silvela 8, Madrid) — https://jupiterjuegos.com/tiendas/madrid/

The chain calls this branch "Júpiter Madrid"; it is the store the official
locators list as "Júpiter Central", so its events share that name and the
store's own listing absorbs the locator copies. (Until 2026-11 this
scraper was labelled "Jupiter Juegos" with a wrong Calle de la Cruz address
— the chain has no branch there; history renamed via data/store_renames.json.)

The store page renders a static monthly calendar grid, no JS required.

Since mid-2026 each branch has its own path (/tiendas/<slug>/). The old
`/tiendas/?tienda=jupiter-madrid` URL now serves the branch index with no
calendar, which is why the scraper silently returned 0 events from July
2026. A page without a calendar now raises instead of returning [].

Each calendar cell in the rendered HTML follows the shape:

    <div style="font-size:30px">N</div>            <!-- day number -->
    <div class="calendario_igualar_alturas">
      <a class="act_0 act_X" href="https://www.jupiterjuegos.com/tiendas/actividad/<id>/">
        <div style="font-size:13px">EVENT TITLE</div>
        <div style="font-size:12px">HH:MMh</div>
        <div style="font-size:30px">…</div>         <!-- decorative ellipsis -->
      </a>
      ...
    </div>

Strategy:
- Walk the document linearly. Day cells are introduced by a font-size:30px
  div containing a real day number (1..31). The `…` 30px divs sit inside
  event blocks and are skipped.
- Month/year come from a header div ("Abril 2026").
- We rotate the year/month when day numbers reset (30 → 1 indicates a
  month boundary).
- Pagination via `/tiendas/madrid/?fecha=01-MM-YYYY`. We pull the current month plus the
  next two so the ~90-day horizon matches other scrapers.

No Playwright. No price extraction. Output schema matches existing
scrapers; aggregator stamps lifecycle fields.
"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from shared.http_retry import get_with_retry

from shared.scraper_keywords import FORMAT_KEYWORDS as SHARED_FORMAT_KEYWORDS, extract_format_from_keywords, GAME_KEYWORDS, extract_game_from_keywords, extract_format_for_event, extract_best_of

logger = logging.getLogger(__name__)

STORE = "Júpiter Central"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
BASE_URL = "https://jupiterjuegos.com/tiendas/madrid/"
EXTRA_MONTHS = 2  # current month + 2 → ~90 days, matches the other scrapers

# Spanish month name → number, used to parse the page header.
_SPANISH_MONTHS = {
    "enero":      1, "febrero":  2, "marzo":      3, "abril":   4,
    "mayo":       5, "junio":    6, "julio":      7, "agosto":  8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}



HEADERS = {
    "User-Agent": "MadridTCGEventsBot/1.0 (+https://github.com/Tragiciy/madrid-tcg)",
    "Referer": BASE_URL,
}

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*h?")
_HEADER_MONTH_RE = re.compile(
    r"\b(" + "|".join(_SPANISH_MONTHS) + r")\s+(\d{4})\b",
    re.IGNORECASE,
)
_FONT_30 = "font-size:30px"
_FONT_13 = "font-size:13px"
_FONT_12 = "font-size:12px"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_game(title: str) -> Optional[str]:
    return extract_game_from_keywords(title, GAME_KEYWORDS)


def _extract_format(title: str) -> Optional[str]:
    return extract_format_from_keywords(title, SHARED_FORMAT_KEYWORDS)


def _has_style(tag, fragment: str) -> bool:
    """True if the tag's inline style contains the given fragment."""
    style = tag.get("style") or ""
    return fragment in style.replace(" ", "")


def _parse_header_month(soup: BeautifulSoup) -> Optional[tuple]:
    """Return (year, month_number) or None."""
    text = soup.get_text(" ", strip=True)
    m = _HEADER_MONTH_RE.search(text)
    if not m:
        return None
    return int(m.group(2)), _SPANISH_MONTHS[m.group(1).lower()]


def _walk_calendar(soup: BeautifulSoup, header_year: int, header_month: int) -> list:
    """
    Walk the soup in document order. Every font-size:30px div whose text is
    a numeric day starts a new "day cell". Each <a class="act_*"> link
    encountered after that belongs to the most recent day until a new day
    is opened.

    The grid wraps with prev-month tail and next-month head, so we rotate
    year/month when the day number drops below the previous one.
    """
    # Initialise as if we're starting in the header month. If the first
    # day number is high (>15) we infer the prev month is leading the
    # grid and shift back accordingly.
    items: list = []
    cur_year, cur_month = header_year, header_month
    last_day: Optional[int] = None
    inferred_start = False

    for tag in soup.find_all(True):
        # Day-number divs (30px, integer text)
        if tag.name == "div" and _has_style(tag, _FONT_30):
            text = tag.get_text(strip=True)
            if text.isdigit():
                n = int(text)
                if not (1 <= n <= 31):
                    continue
                # First day seen? If it's a high number, we're in the prev
                # month tail of the grid; shift the cursor back one month.
                if last_day is None:
                    if n > 15 and not inferred_start:
                        prev_m = cur_month - 1 or 12
                        prev_y = cur_year if cur_month != 1 else cur_year - 1
                        cur_year, cur_month = prev_y, prev_m
                        inferred_start = True
                elif n < last_day:
                    # Rollover into the next month.
                    cur_month += 1
                    if cur_month > 12:
                        cur_month = 1
                        cur_year += 1
                last_day = n
                items.append({
                    "type": "day",
                    "date": date(cur_year, cur_month, n),
                })
        # Event anchors (act_0 act_N)
        elif tag.name == "a":
            classes = tag.get("class") or []
            if any(c.startswith("act_") for c in classes):
                items.append({"type": "event", "tag": tag})
    return items


def _parse_event_anchor(a, day_iso: str, scraped_at: str) -> Optional[dict]:
    title_div = a.find("div", style=lambda s: s and _FONT_13 in (s.replace(" ", "") if s else ""))
    time_div = a.find("div", style=lambda s: s and _FONT_12 in (s.replace(" ", "") if s else ""))
    if not title_div:
        return None
    title = title_div.get_text(" ", strip=True)
    if not title:
        return None

    time_str = (time_div.get_text(" ", strip=True) if time_div else "") or ""
    m = _TIME_RE.search(time_str)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        hour, minute = 0, 0

    try:
        dt = datetime.strptime(day_iso, "%Y-%m-%d").replace(
            hour=hour, minute=minute, tzinfo=TZ,
        )
    except Exception:
        return None

    href = a.get("href") or None

    _game = _extract_game(title)
    _fmt, _fmt_official = extract_format_for_event(title=title, game=_game)
    return {
        "store":          STORE,
        "game":           _game,
        "format":         _fmt,
        "format_official": _fmt_official,
        "best_of":        extract_best_of(title),
        "title":          title,
        "datetime_start": dt.isoformat(),
        "datetime_end":   None,
        "language":       LANGUAGE,
        "source_url":     href,
        "scraped_at":     scraped_at,
    }


def _fetch_month(year: int, month: int) -> BeautifulSoup:
    """Fetch the calendar page for a given month. Raises on any failure."""
    fecha = f"01-{month:02d}-{year:04d}"
    # The site has served a page without the calendar for a minute and then
    # recovered (2026-11-17): retry that before calling the layout changed.
    resp = get_with_retry(
        BASE_URL,
        params={"fecha": fecha},
        headers=HEADERS,
        timeout=20,
        check=_require_calendar,
    )
    return BeautifulSoup(resp.text, "html.parser")


def _require_calendar(resp) -> None:
    if "calendario_igualar_alturas" not in resp.text:
        raise ValueError(f"{STORE}: no calendar grid on page (site structure changed?)")


def _events_for_month(soup: BeautifulSoup, fallback_year: int, fallback_month: int,
                      scraped_at: str) -> list:
    """Return a list of parsed event dicts for the given month soup.

    Raises ValueError when the page has no calendar grid: that means the
    site changed again, and must not be reported as "no events".
    """
    if not soup.select(".calendario_igualar_alturas"):
        raise ValueError(f"{STORE}: no calendar grid on page (site structure changed?)")
    header = _parse_header_month(soup)
    if header is None:
        header_year, header_month = fallback_year, fallback_month
    else:
        header_year, header_month = header

    items = _walk_calendar(soup, header_year, header_month)
    out: list = []
    current_iso: Optional[str] = None
    for it in items:
        if it["type"] == "day":
            current_iso = it["date"].isoformat()
        elif it["type"] == "event" and current_iso:
            try:
                ev = _parse_event_anchor(it["tag"], current_iso, scraped_at)
                if ev:
                    out.append(ev)
            except Exception as exc:
                logger.warning("%s: skipping event: %s", STORE, exc)
    return out


# ── Main scrape function ─────────────────────────────────────────────────────

def scrape() -> list:
    scraped_at = datetime.now(tz=TZ).isoformat()
    today = datetime.now(tz=TZ).date()
    cutoff = today + timedelta(days=90)

    months_to_fetch: list = []
    cy, cm = today.year, today.month
    months_to_fetch.append((cy, cm))
    for _ in range(EXTRA_MONTHS):
        cm += 1
        if cm > 12:
            cm = 1
            cy += 1
        months_to_fetch.append((cy, cm))

    all_events: list = []
    for year, month in months_to_fetch:
        soup = _fetch_month(year, month)
        evs = _events_for_month(soup, year, month, scraped_at)
        logger.info("%s: %04d-%02d → %d events", STORE, year, month, len(evs))
        all_events.extend(evs)
        time.sleep(0.3)

    # Deduplicate by (title, datetime_start, store) — adjacent month
    # fetches can repeat events on the boundary days.
    seen: dict = {}
    for ev in all_events:
        key = (ev["title"], ev["datetime_start"], ev["store"])
        seen.setdefault(key, ev)
    deduped = list(seen.values())

    # Restrict to today..cutoff. The current month's grid leaks the last
    # few days of the previous month, which would otherwise flicker
    # active/inactive across runs.
    in_window: list = []
    for ev in deduped:
        try:
            d = datetime.fromisoformat(ev["datetime_start"]).date()
        except Exception:
            continue
        if today <= d <= cutoff:
            in_window.append(ev)

    in_window.sort(key=lambda e: e["datetime_start"])
    logger.info(
        "%s: total events returned: %d (raw %d, deduped %d)",
        STORE, len(in_window), len(all_events), len(deduped),
    )
    return in_window


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
