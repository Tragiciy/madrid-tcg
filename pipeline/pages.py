"""
Generate static, crawlable HTML pages for search engines (Spanish), from the
same data as the API. Plain HTML + one small CSS file; no JavaScript needed.

    /madrid/                      next 7 days, all games
    /madrid/hoy/                  today
    /madrid/fin-de-semana/        this Friday–Sunday
    /madrid/<game>/               next 28 days for one game
    /madrid/tiendas/<store-id>/   next 28 days at one store
    /sitemap.xml  /robots.txt  /404.html

Every list embeds schema.org Event JSON-LD so events can appear in Google's
event results. Pages for games and stores are kept even when nothing is
scheduled (a short "nothing scheduled" note), so their URLs never flap
between 200 and 404.
"""

import json
import logging
import pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pipeline.config import TZ
from pipeline.publish import _public_event, _visible
from shared.store_registry import slugify

logger = logging.getLogger(__name__)

PUBLIC_DIR = pathlib.Path("public")
TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"
SITE_URL = "https://madrid-tcg.pages.dev"
CITY = "madrid"
CITY_NAME = "Madrid"
JSONLD_LIMIT = 50  # events embedded per page

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

GAME_SLUGS = {
    "Magic: The Gathering": "magic",
    "Pokémon": "pokemon",
    "One Piece": "one-piece",
    "Digimon": "digimon",
    "Lorcana": "lorcana",
    "Star Wars: Unlimited": "star-wars-unlimited",
    "Yu-Gi-Oh!": "yu-gi-oh",
    "Flesh and Blood": "flesh-and-blood",
    "Riftbound": "riftbound",
    "Weiß Schwarz": "weiss-schwarz",
    "Final Fantasy TCG": "final-fantasy",
    "Naruto Mythos": "naruto-mythos",
}


def game_slug(game: str) -> str:
    return GAME_SLUGS.get(game) or slugify(game)


def fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()].capitalize()} {d.day} de {MESES[d.month - 1]}"


def safe_url(url: Optional[str]) -> Optional[str]:
    """Only http(s) links from scraped data reach our HTML (no javascript:)."""
    return url if isinstance(url, str) and url.lower().startswith(("https://", "http://")) else None


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                      autoescape=select_autoescape(["html", "xml"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["game_slug"] = game_slug
    env.filters["safe_url"] = safe_url
    return env


def _jsonld(events: list[dict], stores_by_id: dict) -> str:
    items = []
    for e in events[:JSONLD_LIMIT]:
        store = stores_by_id.get(e.get("store_id")) or {}
        address = e.get("store_address") or store.get("address") or CITY_NAME
        place = {"@type": "Place", "name": e["store"],
                 "address": {"@type": "PostalAddress", "streetAddress": address,
                             "addressLocality": CITY_NAME, "addressCountry": "ES"}}
        if store.get("lat") is not None:
            place["geo"] = {"@type": "GeoCoordinates", "latitude": store["lat"], "longitude": store["lng"]}
        item = {
            "@context": "https://schema.org", "@type": "Event",
            "name": e["title"],
            "startDate": e["datetime_start"],
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "eventStatus": "https://schema.org/EventScheduled",
            "location": place,
            "description": f"{e.get('game') or 'Juego de cartas'} · {e['store']}",
            "url": safe_url(e.get("source_url")) or SITE_URL,
            "organizer": {"@type": "Organization", "name": e["store"]},
        }
        if e.get("datetime_end"):
            item["endDate"] = e["datetime_end"]
        items.append(item)
    # "</" must not appear inside a <script> element.
    return json.dumps(items, ensure_ascii=False).replace("</", "<\\/")


def _by_day(events: list[dict]) -> list[tuple[str, list[dict]]]:
    days: dict[date, list[dict]] = defaultdict(list)
    for e in events:
        days[datetime.fromisoformat(e["datetime_start"]).date()].append(e)
    return [(fecha_larga(d), evs) for d, evs in sorted(days.items())]


def build_pages(events: list[dict], registry, now_iso: str) -> dict[str, str]:
    """{path relative to public/: file content}."""
    now = datetime.fromisoformat(now_iso).astimezone(TZ)
    today = now.date()
    env = _env()
    page_tpl, nf_tpl = env.get_template("list.html"), env.get_template("404.html")

    stores = [s for s in registry.stores if (s.get("city") or CITY) == CITY]
    stores_by_id = {s["id"]: s for s in stores}
    store_ids_by_name = {s["name"]: s["id"] for s in stores}

    # Upcoming = not finished today and still listed by its source.
    upcoming = []
    for e in sorted(events, key=lambda e: e.get("datetime_start") or ""):
        if not _visible(e, now):
            continue
        pe = _public_event(e)
        pe.setdefault("store_id", store_ids_by_name.get(pe.get("store")))
        start = datetime.fromisoformat(pe["datetime_start"])
        if start.date() >= today and (start >= now - timedelta(hours=3) or start.date() > today):
            upcoming.append(pe)

    def within(days: int, pred=lambda e: True) -> list[dict]:
        end = today + timedelta(days=days)
        return [e for e in upcoming if datetime.fromisoformat(e["datetime_start"]).date() < end and pred(e)]

    games = sorted({e["game"] for e in events if e.get("game")})
    nav = {"games": [(g, game_slug(g)) for g in games]}
    pages: dict[str, str] = {}
    urls: list[str] = []

    def render(path: str, *, title: str, h1: str, description: str, evs: list[dict],
               empty: str, intro: Optional[str] = None, store: Optional[dict] = None,
               calendar_query: str = "") -> None:
        url = f"/{path}"
        pages[f"{path}index.html"] = page_tpl.render(
            title=title, h1=h1, description=description, intro=intro, store=store,
            days=_by_day(evs), count=len(evs), empty=empty, nav=nav,
            canonical=SITE_URL + url, jsonld=_jsonld(evs, stores_by_id),
            calendar_url="/" + (f"?{calendar_query}" if calendar_query else ""),
            updated=fecha_larga(today), city=CITY_NAME,
        )
        urls.append(url)

    base = f"{CITY}/"
    render(base, title=f"Torneos de cartas en {CITY_NAME} — próximos 7 días",
           h1=f"Torneos TCG en {CITY_NAME}",
           description=f"Todos los torneos y eventos de juegos de cartas coleccionables en {CITY_NAME}: "
                       "Magic, Pokémon, One Piece, Lorcana, Digimon y más, en un solo calendario.",
           evs=within(7), empty="No hay torneos programados en los próximos 7 días.")
    render(f"{base}hoy/", title=f"Torneos de cartas hoy en {CITY_NAME}",
           h1=f"Hoy en {CITY_NAME}",
           description=f"Torneos de juegos de cartas hoy, {fecha_larga(today).lower()}, en {CITY_NAME}.",
           evs=within(1), empty="Hoy no quedan torneos programados.")
    wd = today.weekday()  # Mon=0 … Sun=6
    friday = today + timedelta(days=4 - wd) if wd <= 4 else today  # Sat/Sun: from today
    weekend_end = today + timedelta(days=7 - wd)                     # next Monday
    weekend = [e for e in upcoming
               if friday <= datetime.fromisoformat(e["datetime_start"]).date() < weekend_end]
    render(f"{base}fin-de-semana/", title=f"Torneos de cartas este fin de semana en {CITY_NAME}",
           h1=f"Este fin de semana en {CITY_NAME}",
           description=f"Torneos de juegos de cartas de viernes a domingo en {CITY_NAME}.",
           evs=weekend, empty="No hay torneos programados este fin de semana.")
    for g in games:
        render(f"{base}{game_slug(g)}/", title=f"Torneos de {g} en {CITY_NAME}",
               h1=f"{g} en {CITY_NAME}",
               description=f"Próximos torneos y eventos de {g} en tiendas de {CITY_NAME}.",
               evs=within(28, lambda e, g=g: e.get("game") == g),
               empty=f"No hay torneos de {g} programados en las próximas 4 semanas.",
               calendar_query=f"game={g}")
    for s in stores:
        render(f"{base}tiendas/{s['id']}/", title=f"{s['name']} — torneos de cartas en {CITY_NAME}",
               h1=s["name"],
               description=f"Próximos torneos de juegos de cartas en {s['name']}"
                           + (f", {s['address']}" if s.get("address") else "") + ".",
               evs=within(28, lambda e, sid=s["id"]: e.get("store_id") == sid),
               empty="Esta tienda no tiene torneos programados en las próximas 4 semanas.",
               store=s, calendar_query=f"store={s['name']}")

    pages["404.html"] = nf_tpl.render(nav=nav, city=CITY_NAME)
    pages["sitemap.xml"] = env.get_template("sitemap.xml").render(
        urls=[SITE_URL + "/"] + [SITE_URL + u for u in urls], lastmod=today.isoformat())
    pages["robots.txt"] = (
        "User-agent: *\nAllow: /\nDisallow: /health\n\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    return pages


def write_pages(pages: dict[str, str], public_dir: Optional[pathlib.Path] = None) -> int:
    """Write changed files; remove generated city pages that are no longer
    produced (only under public/<city>/). Returns #files written."""
    public_dir = public_dir or PUBLIC_DIR
    keep, written = set(), 0
    for rel, text in pages.items():
        path = public_dir / rel
        keep.add(path.resolve())
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        written += 1
    # list(): never delete while rglob is iterating (fails on Python 3.11).
    for stale in list((public_dir / CITY).rglob("index.html")):
        if stale.resolve() not in keep:
            stale.unlink()
            try:
                stale.parent.rmdir()
            except OSError:
                pass
    logger.info("Wrote pages: %d of %d files changed", written, len(pages))
    return written
