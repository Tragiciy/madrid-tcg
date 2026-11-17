"""
scrapers/nikochan_arena.py — Nikochan Arena (Plaça del Triomf 32, Terrassa,
Barcelona area), from the tournament calendar on its own shop site
(https://nikochancomics.net/calendario, server-rendered HTML).

Layout: one `section.dia-bloque` per day, its date in the heading
("Miércoles 18 de noviembre de 2026"), then one `article.torneo-card` per
event. A card names its game in `h3.torneo-juego` or in its logo's alt
text, its time in `.torneo-info` ("Hora: 17:15h."), and describes the
event in `.torneo-texto`, whose first line is the event name when the
store wrote one ("NEXUS NIGHT", "COMMANDER BRACKET 1 - …"). Cards have no
id of their own.

The calendar's Riftbound nights are on no locator we read; its One Piece,
Pokémon and Digimon events are, and those locator copies are dropped by
the aggregator's dedup. Cards whose sign-up link is a Wizards locator join
code are skipped: they are Wizards locator events by definition, and the
two listings sometimes disagree on the time (a Sunday sealed is 12:05 on
the locator and 17:00 here), which the dedup window would not join. Untracked games (Dragon Ball) are
skipped: an unknown-game event would swallow a locator event in the dedup.
"""

import logging
import re
import unicodedata
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from shared.http_retry import get_with_retry
from shared.locator_common import TZ, USER_AGENT
from shared.scraper_keywords import (
    GAME_KEYWORDS,
    extract_best_of,
    extract_format_for_event,
    extract_game_from_keywords,
)

logger = logging.getLogger(__name__)

STORE = "Nikochan Arena"
LANGUAGE = "es"
PAGE_URL = "https://nikochancomics.net/calendario"
WIZARDS_JOIN = "locator.wizards.com/join"

_MONTHS = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
     "agosto", "septiembre", "octubre", "noviembre", "diciembre"], start=1)}
_DAY_RE = re.compile(r"(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})")
_TIME_RE = re.compile(r"Hora:\s*(\d{1,2})(?:[:.](\d{2}))?")
_PRICE_TAIL_RE = re.compile(r"\s*-\s*\d+(?:[.,]\d+)?\s*€\s*$")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _day(heading: str):
    m = _DAY_RE.search(_fold(heading))
    if not m or m.group(2) not in _MONTHS:
        raise ValueError(f"{STORE}: unreadable day heading {heading!r}")
    return int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))


def _name(text: str) -> str:
    """The store's event name: the description's first line, when it is
    one (upper case, not a prize list or a placeholder)."""
    first = _PRICE_TAIL_RE.sub("", (text.strip().splitlines() or [""])[0]).strip(" :-")
    if (first and first == first.upper() and any(c.isalpha() for c in first)
            and not first.startswith(("+", "[")) and len(first) <= 90):
        return first
    return ""


def parse_page(page_html: str, now: datetime) -> list[dict]:
    soup = BeautifulSoup(page_html, "html.parser")
    days = soup.select("section.dia-bloque")
    if not days:
        raise RuntimeError(f"{STORE}: no day blocks (section.dia-bloque) on {PAGE_URL}")
    scraped_at = now.astimezone(TZ).isoformat()
    since = (now - timedelta(days=1)).astimezone(TZ)
    out: list[dict] = []
    for block in days:
        heading = block.select_one(".dia-titulo h2")
        y, mo, d = _day(heading.get_text(" ", strip=True) if heading else "")
        for card in block.select("article.torneo-card"):
            label_el = card.select_one(".torneo-juego")
            logo = card.select_one("img.torneo-logo")
            label = label_el.get_text(" ", strip=True) if label_el else (logo.get("alt", "") if logo else "")
            game = extract_game_from_keywords(label, GAME_KEYWORDS)
            if not game:
                continue
            info = card.select_one(".torneo-info")
            tm = _TIME_RE.search(info.get_text(" ", strip=True) if info else "")
            if not tm:
                continue  # no start time: nothing to put on the calendar
            start = datetime(y, mo, d, int(tm.group(1)), int(tm.group(2) or 0), tzinfo=TZ)
            if start < since:
                continue
            link = card.select_one("a.btn-inscripcion[href^=http]")
            if link and WIZARDS_JOIN in link["href"]:
                continue  # read from the Wizards locator instead
            text_el = card.select_one(".torneo-texto")
            text = text_el.get_text("\n", strip=True) if text_el else ""
            kind_el = card.select_one(".torneo-tipo")
            kind = (kind_el.get_text(strip=True) if kind_el else "") or "Torneo"
            name = _name(text)
            title = f"{label} – {name}" if name else f"{kind.capitalize()} {label}"
            fmt, fmt_official = extract_format_for_event(title=title, description=text, game=game)
            out.append({
                "title": title,
                "store": STORE,
                "game": game,
                "format": fmt,
                "format_official": fmt_official,
                "best_of": extract_best_of(text),
                "datetime_start": start.isoformat(),
                "datetime_end": None,
                "language": LANGUAGE,
                "source_url": link["href"] if link else PAGE_URL,
                "scraped_at": scraped_at,
            })
    out.sort(key=lambda e: e["datetime_start"])
    return out


def scrape() -> list[dict]:
    resp = get_with_retry(PAGE_URL, timeout=30, headers={"User-Agent": USER_AGENT})
    events = parse_page(resp.text, datetime.now(TZ))
    logger.info("%s: %d TCG events", STORE, len(events))
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
