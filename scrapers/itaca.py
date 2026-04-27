"""
Ítaca — https://itaca.gg/tournament/month

Itaca is an MTG-specialty Madrid store. The /tournament/month page is
server-rendered HTML, no JS, no auth, no cookie challenge. A single GET
returns ~2 months of calendar (current + next), rendered as nested
`<div class="diasemana">` day cells, each holding zero or more
`<div class="divtorneo">` tournament blocks.

Per-tournament markup:

    <div class="diasemana ...">
      <span class="numdia">N</span>            <!-- day number -->
      <div class="cajatorneos hayNtorneos">
        <div class="divtorneo primerdivtorneo">
          <span class="torneodia">
            <img data-tippy-content="IQ|Dos Cabezas|Estrella" .../>   <!-- optional -->
            <a href="/tournament/detail/<id>" class="torneodia">EVENT TITLE</a>
          </span>
          <span class="torneohora">HH:MM</span>
        </div>
        ...
      </div>
    </div>

Strategy:
- Read `<title>` for the starting month/year ("Torneos de Abril 2026").
- Walk day cells in document order, tracking the current (year, month).
  When the numeric day drops below the previous, rotate to next month.
- For each tournament: pull title (anchor text), time (torneohora),
  href, build datetime_start in Europe/Madrid.
- Default game = "Magic" because Itaca is MTG-specialty; aggregator
  normalises to "Magic: The Gathering". Title-keyword scan still wins
  if the title explicitly names another game.
- Filter scraped events to today..today+90 (matches other scrapers'
  horizon and prevents prev-month tails from churning the merge).

No Playwright. No price extraction. Output schema matches existing
scrapers; aggregator stamps lifecycle fields.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

STORE = "Ítaca"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
BASE_URL = "https://itaca.gg/tournament/month"
SITE_ROOT = "https://itaca.gg"

# Spanish month name → number (used to parse the page title).
_SPANISH_MONTHS = {
    "enero":      1, "febrero":  2, "marzo":      3, "abril":   4,
    "mayo":       5, "junio":    6, "julio":      7, "agosto":  8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_TITLE_RE = re.compile(
    r"\b(" + "|".join(_SPANISH_MONTHS) + r")\s+(\d{4})\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

# Title-driven game classification (same shape as other scrapers).
GAME_KEYWORDS = [
    ("one piece",   "One Piece"),
    ("yu-gi-oh",    "Yu-Gi-Oh"),
    ("yugioh",      "Yu-Gi-Oh"),
    ("pokemon",     "Pokémon"),
    ("pokémon",     "Pokémon"),
    ("digimon",     "Digimon"),
    ("lorcana",     "Lorcana"),
    ("riftbound",   "Riftbound"),
    ("nexus night", "Riftbound"),
    ("star wars",   "Star Wars: Unlimited"),
    ("swu",         "Star Wars: Unlimited"),
    ("flesh and blood", "Flesh and Blood"),
    ("fab",         "Flesh and Blood"),
    ("final fantasy", "Final Fantasy TCG"),
    ("fftcg",       "Final Fantasy TCG"),
    ("mtg",         "Magic"),
    ("magic",       "Magic"),
]

FORMAT_KEYWORDS = [
    ("store championship", "Store Championship"),
    ("prerelease", "Prerelease"),
    ("presentaciones", "Prerelease"),
    ("presentación", "Prerelease"),
    ("sellados", "Sealed"),
    ("sellado", "Sealed"),
    # cEDH / Premier must precede the broader keywords below.
    ("competitive elder dragon highlander", "cEDH"),
    ("cedh", "cEDH"),
    ("formato premier", "Premier"),
    ("premier", "Premier"),
    ("commander", "Commander"),
    ("standard", "Standard"),
    ("pioneer", "Pioneer"),
    ("modern", "Modern"),
    ("legacy", "Legacy"),
    ("pauper", "Pauper"),
    ("sealed", "Sealed"),
    ("draft", "Draft"),
    ("league", "League"),
    ("liga", "League"),
    ("weekly", "Weekly"),
    ("casual", "Casual"),
    ("bo3", "BO3"),
    ("bo1", "BO1"),
    ("rcq", "Store Championship"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": SITE_ROOT,
}

# Default game when the title gives no other hint (Itaca is MTG-only).
_DEFAULT_GAME = "Magic"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_game(title: str) -> Optional[str]:
    low = title.lower()
    for keyword, canonical in GAME_KEYWORDS:
        if keyword in low:
            return canonical
    return _DEFAULT_GAME


def _extract_format(title: str) -> Optional[str]:
    low = title.lower()
    for keyword, canonical in FORMAT_KEYWORDS:
        if keyword in low:
            return canonical
    return None


def _parse_header_month(soup: BeautifulSoup) -> Optional[tuple]:
    """Return (year, month_number) from the <title> tag or None."""
    t = soup.find("title")
    if not t:
        return None
    m = _TITLE_RE.search(t.get_text(" ", strip=True))
    if not m:
        return None
    return int(m.group(2)), _SPANISH_MONTHS[m.group(1).lower()]


def _absolute_url(href: str) -> Optional[str]:
    if not href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return SITE_ROOT + href
    return None


def _parse_tournament_div(div, day_iso: str, scraped_at: str) -> Optional[dict]:
    """Convert one <div class="divtorneo"> to our event schema."""
    link = div.find("a", class_="torneodia")
    if not link:
        return None
    title = link.get_text(" ", strip=True)
    if not title:
        return None
    href = _absolute_url(link.get("href"))

    time_span = div.find("span", class_="torneohora")
    time_text = time_span.get_text(" ", strip=True) if time_span else ""
    m = _TIME_RE.search(time_text)
    hour, minute = (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    try:
        dt = datetime.strptime(day_iso, "%Y-%m-%d").replace(
            hour=hour, minute=minute, tzinfo=TZ,
        )
    except Exception:
        return None

    return {
        "store":          STORE,
        "game":           _extract_game(title),
        "format":         _extract_format(title),
        "title":          title,
        "datetime_start": dt.isoformat(),
        "datetime_end":   None,
        "language":       LANGUAGE,
        "source_url":     href,
        "scraped_at":     scraped_at,
    }


# ── Main scrape function ─────────────────────────────────────────────────────

def scrape() -> list:
    scraped_at = datetime.now(tz=TZ).isoformat()
    today = datetime.now(tz=TZ).date()
    cutoff = today + timedelta(days=90)

    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=20)
    except Exception as exc:
        logger.error("%s: request failed: %s", STORE, exc)
        return []
    if resp.status_code != 200:
        logger.error("%s: HTTP %d", STORE, resp.status_code)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    header = _parse_header_month(soup)
    if header is None:
        # Fall back to the current Madrid month/year if the title is missing.
        header = (today.year, today.month)
    cur_year, cur_month = header

    events: list = []
    last_day: Optional[int] = None

    for cell in soup.find_all("div", class_=re.compile(r"\bdiasemana\b")):
        num_span = cell.find("span", class_="numdia")
        if not num_span:
            continue
        text = num_span.get_text(strip=True)
        if not text.isdigit():
            continue
        n = int(text)
        if not (1 <= n <= 31):
            continue
        # Rollover to next month when the day number drops.
        if last_day is not None and n < last_day:
            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1
        last_day = n

        try:
            day_iso = date(cur_year, cur_month, n).isoformat()
        except ValueError:
            continue

        for div in cell.find_all("div", class_=re.compile(r"\bdivtorneo\b")):
            try:
                ev = _parse_tournament_div(div, day_iso, scraped_at)
                if ev:
                    events.append(ev)
            except Exception as exc:
                logger.warning("%s: skipping a tournament: %s", STORE, exc)

    # Dedup by (title, datetime_start, store).
    seen: dict = {}
    for ev in events:
        key = (ev["title"], ev["datetime_start"], ev["store"])
        seen.setdefault(key, ev)
    deduped = list(seen.values())

    # Restrict to today..cutoff.
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
        STORE, len(in_window), len(events), len(deduped),
    )
    return in_window


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
