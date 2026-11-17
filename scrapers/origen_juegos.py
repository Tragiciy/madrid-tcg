"""
scrapers/origen_juegos.py — Origen Juegos (Carrer Ample 65, Terrassa,
Barcelona area), from the events calendar on its home page
(https://www.origenjuegos.com/).

The site is built with Astro. The calendar is an island whose `props`
attribute holds every event as Astro-serialised JSON (`[0, value]` for a
value, `[1, [...]]` for an array): id (a UUID, kept as source_event_id),
date, time, title, game code, type and price. `props.games` maps each game
code to its name, so new codes resolve through the shared keywords. The
same page also has schema.org Event JSON-LD, but that has no game, and its
offsets are a fixed +02:00 even in winter; the island's wall-clock
date and time are read as Madrid time instead.

The calendar has games no locator lists (Riftbound, Star Wars: Unlimited,
Yu-Gi-Oh!), next to One Piece, Digimon, Pokémon and Lorcana nights that the
locators also carry; those copies are dropped by the aggregator's dedup.
Board games (code "board") and any game we don't track are skipped. A
missing `games` list, or most non-board events with a code that resolves to
no game, raises instead of quietly publishing a fraction of the calendar.

robots.txt disallows /api/ only; this reads the public home page.
"""

import json
import logging
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

STORE = "Origen Juegos"
LANGUAGE = "es"
PAGE_URL = "https://www.origenjuegos.com/"
EVENTS_URL = "https://www.origenjuegos.com/#eventos"
ISLAND = "EventsCalendar"
BOARD_CODES = {"board"}  # "Juegos de mesa": not a TCG, an intended skip


def _astro(value):
    """Decode Astro's island-props serialisation."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        tag, inner = value
        if tag == 1:
            return [_astro(v) for v in inner]
        if isinstance(inner, dict):
            return {k: _astro(v) for k, v in inner.items()}
        return inner
    if isinstance(value, dict):
        return {k: _astro(v) for k, v in value.items()}
    return value


def calendar_props(page_html: str) -> dict:
    soup = BeautifulSoup(page_html, "html.parser")
    for island in soup.find_all("astro-island"):
        if ISLAND in (island.get("component-url") or "") and island.get("props"):
            props = {k: _astro(v) for k, v in json.loads(island["props"]).items()}
            if not isinstance(props.get("events"), list):
                break
            return props
    raise RuntimeError(f"{STORE}: no {ISLAND} island with events on {PAGE_URL}")


def parse_page(page_html: str, now: datetime) -> list[dict]:
    props = calendar_props(page_html)
    names = {g.get("id"): g.get("name") or "" for g in props.get("games") or []
             if isinstance(g, dict) and g.get("id")}
    if not names:
        raise RuntimeError(f"{STORE}: the calendar has no games list; game codes can't be resolved")
    # Codes resolve through props.games only; a bare code is never guessed
    # ("swu" would match, "rift" or "op" would not, which hides breakage).
    games = {code: extract_game_from_keywords(name, GAME_KEYWORDS) for code, name in names.items()}
    tcg = [r for r in props["events"] if r.get("game") not in BOARD_CODES]
    unresolved = [r.get("game") for r in tcg if not games.get(r.get("game"))]
    if tcg and len(unresolved) * 2 > len(tcg):
        raise RuntimeError(f"{STORE}: {len(unresolved)} of {len(tcg)} events have a game code that "
                           f"resolves to no game: {sorted(set(map(str, unresolved)))}")
    scraped_at = now.astimezone(TZ).isoformat()
    since = (now - timedelta(days=1)).astimezone(TZ)
    out: list[dict] = []
    for raw in props["events"]:
        title = (raw.get("title") or "").strip()
        game = games.get(raw.get("game"))
        if not title or not game:
            continue  # board games, untracked games
        start = datetime.strptime(f"{raw['date']} {raw['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        if start < since:
            continue
        kind = raw.get("type") or ""
        detail = raw.get("meta") or ""
        # `meta` is the prize line ("Sobre de draft a picking…"), not the
        # format, so only the title and the event type are read.
        fmt, fmt_official = extract_format_for_event(
            title=title, category="prerelease" if kind == "prerelease" else "", game=game,
        )
        out.append({
            "title": title,
            "store": STORE,
            "game": game,
            "format": fmt,
            "format_official": fmt_official,
            "best_of": extract_best_of(f"{title} {detail}"),
            "datetime_start": start.isoformat(),
            "datetime_end": None,
            "language": LANGUAGE,
            "source_url": EVENTS_URL,
            "source_event_id": f"origen:{raw['id']}",
            "scraped_at": scraped_at,
        })
    out.sort(key=lambda e: e["datetime_start"])
    return out


def scrape() -> list[dict]:
    resp = get_with_retry(PAGE_URL, timeout=30, headers={"User-Agent": USER_AGENT})
    # The server sends no charset; the page is UTF-8 (<meta charset>).
    events = parse_page(resp.content.decode("utf-8"), datetime.now(TZ))
    logger.info("%s: %d TCG events", STORE, len(events))
    return events


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
