"""
Generate static, crawlable HTML pages for search engines (Spanish), from the
same data as the API. Plain HTML + one small CSS file; no JavaScript needed.
One set per city in shared/cities.py (<city> = madrid, barcelona, …), each
with an English mirror under /en/<city>/:

    /<city>/                      next 7 days, all games
    /<city>/hoy/                  today
    /<city>/fin-de-semana/        this Friday–Sunday
    /<city>/<game>/               next 28 days for one game
    /<city>/tiendas/<store-id>/   next 28 days at one store
    /sitemap.xml  /robots.txt  /404.html   (site-wide)

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
from pipeline.publish import _public_event, _visible, events_in, stores_in
from shared.cities import CITIES, DEFAULT_CITY, City, default as default_city
from shared.store_registry import slugify

logger = logging.getLogger(__name__)

PUBLIC_DIR = pathlib.Path("public")
TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"
SITE_URL = "https://madrid-tcg.pages.dev"
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
    "Cyberpunk TCG": "cyberpunk",
    "Gundam Card Game": "gundam",
    "My Little Pony": "my-little-pony",
    "Palworld": "palworld",
}


def game_slug(game: str) -> str:
    return GAME_SLUGS.get(game) or slugify(game)


MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


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


def _jsonld(events: list[dict], stores_by_id: dict, game_fallback: str = "Juego de cartas",
            city: Optional[City] = None) -> str:
    city = city or default_city()
    items = []
    for e in events[:JSONLD_LIMIT]:
        store = stores_by_id.get(e.get("store_id")) or {}
        address = e.get("store_address") or store.get("address") or city.name
        place = {"@type": "Place", "name": e["store"],
                 "address": {"@type": "PostalAddress", "streetAddress": address,
                             "addressLocality": city.name, "addressCountry": city.country}}
        if store.get("lat") is not None:
            place["geo"] = {"@type": "GeoCoordinates", "latitude": store["lat"], "longitude": store["lng"]}
        item = {
            "@context": "https://schema.org", "@type": "Event",
            "name": e["title"],
            "startDate": e["datetime_start"],
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
            "eventStatus": "https://schema.org/EventScheduled",
            "location": place,
            "description": f"{e.get('game') or game_fallback} · {e['store']}",
            "url": safe_url(e.get("source_url")) or SITE_URL,
            "organizer": {"@type": "Organization", "name": e["store"]},
        }
        if e.get("datetime_end"):
            item["endDate"] = e["datetime_end"]
        items.append(item)
    # "</" must not appear inside a <script> element.
    return json.dumps(items, ensure_ascii=False).replace("</", "<\\/")


def _days(events: list[dict]) -> list[tuple[date, list[dict]]]:
    days: dict[date, list[dict]] = defaultdict(list)
    for e in events:
        days[datetime.fromisoformat(e["datetime_start"]).date()].append(e)
    return sorted(days.items())


def long_date_en(d: date) -> str:
    return f"{DAYS_EN[d.weekday()]} {d.day} {MONTHS_EN[d.month - 1]}"


# Page copy per language. Spanish lives at the root (/madrid/…), English
# under /en/ (/en/madrid/…); every page links its counterpart via hreflang.
LANGS = {
    "es": {
        "prefix": "", "html_lang": "es", "og_locale": "es_ES", "date": fecha_larga,
        "seg_today": "hoy", "seg_weekend": "fin-de-semana", "seg_stores": "tiendas",
        "nav_today": "Hoy", "nav_weekend": "Fin de semana", "nav_calendar": "Calendario completo",
        "games": "Juegos", "footer": "Datos de las tiendas y de los localizadores oficiales de cada juego, actualizados a diario.",
        "events": lambda n: f"{n} evento{'' if n == 1 else 's'}", "updated": "actualizado el",
        "open_calendar": "Abrir en el calendario interactivo", "map": "Ver en el mapa", "website": "Web de la tienda",
        "switch": ("English", "en"),
        "city_title": "Torneos de cartas en {c} — próximos 7 días", "city_h1": "Torneos TCG en {c}",
        "city_desc": "Todos los torneos y eventos de juegos de cartas coleccionables en {c}: Magic, Pokémon, One Piece, Lorcana, Digimon y más, en un solo calendario.",
        "city_empty": "No hay torneos programados en los próximos 7 días.",
        "today_title": "Torneos de cartas hoy en {c}", "today_h1": "Hoy en {c}",
        "today_desc": "Torneos de juegos de cartas hoy, {d}, en {c}.", "today_empty": "Hoy no quedan torneos programados.",
        "weekend_title": "Torneos de cartas este fin de semana en {c}", "weekend_h1": "Este fin de semana en {c}",
        "weekend_desc": "Torneos de juegos de cartas de viernes a domingo en {c}.",
        "weekend_empty": "No hay torneos programados este fin de semana.",
        "game_title": "Torneos de {g} en {c}", "game_h1": "{g} en {c}",
        "game_desc": "Próximos torneos y eventos de {g} en tiendas de {c}.",
        "game_empty": "No hay torneos de {g} programados en las próximas 4 semanas.",
        "store_title": "{s} — torneos de cartas en {c}", "store_desc": "Próximos torneos de juegos de cartas en {s}{a}.",
        "store_empty": "Esta tienda no tiene torneos programados en las próximas 4 semanas.",
        "jsonld_game": "Juego de cartas", "submit": "¿Es tu tienda? Añade tus torneos",
    },
    "en": {
        "prefix": "en/", "html_lang": "en", "og_locale": "en_GB", "date": long_date_en,
        "seg_today": "today", "seg_weekend": "weekend", "seg_stores": "stores",
        "nav_today": "Today", "nav_weekend": "Weekend", "nav_calendar": "Full calendar",
        "games": "Games", "footer": "Data from the stores and the official event locators of each game, updated daily.",
        "events": lambda n: f"{n} event{'' if n == 1 else 's'}", "updated": "updated",
        "open_calendar": "Open in the interactive calendar", "map": "View on map", "website": "Store website",
        "switch": ("Español", "es"),
        "city_title": "Card game tournaments in {c} — next 7 days", "city_h1": "TCG tournaments in {c}",
        "city_desc": "Every trading card game tournament and event in {c}: Magic, Pokémon, One Piece, Lorcana, Digimon and more, in one calendar.",
        "city_empty": "No tournaments scheduled in the next 7 days.",
        "today_title": "Card game tournaments today in {c}", "today_h1": "Today in {c}",
        "today_desc": "Card game tournaments today, {d}, in {c}.", "today_empty": "No more tournaments scheduled today.",
        "weekend_title": "Card game tournaments this weekend in {c}", "weekend_h1": "This weekend in {c}",
        "weekend_desc": "Card game tournaments from Friday to Sunday in {c}.",
        "weekend_empty": "No tournaments scheduled this weekend.",
        "game_title": "{g} tournaments in {c}", "game_h1": "{g} in {c}",
        "game_desc": "Upcoming {g} tournaments and events at {c} stores.",
        "game_empty": "No {g} tournaments scheduled in the next 4 weeks.",
        "store_title": "{s} — card game tournaments in {c}", "store_desc": "Upcoming card game tournaments at {s}{a}.",
        "store_empty": "This store has no tournaments scheduled in the next 4 weeks.",
        "jsonld_game": "Card game", "submit": "Is this your store? Add your events",
    },
}


def page_path(lang: str, key: tuple, city: str = DEFAULT_CITY) -> str:
    """Path (relative to public/, ending in '/') of a city's page in a language."""
    L = LANGS[lang]
    base = f"{L['prefix']}{city}/"
    kind = key[0]
    if kind == "city":
        return base
    if kind == "today":
        return f"{base}{L['seg_today']}/"
    if kind == "weekend":
        return f"{base}{L['seg_weekend']}/"
    if kind == "game":
        return f"{base}{key[1]}/"
    if kind == "store":
        return f"{base}{L['seg_stores']}/{key[1]}/"
    raise ValueError(key)


def calendar_url(city: str, query: str = "") -> str:
    """Link into the interactive calendar. The site root shows DEFAULT_CITY;
    other cities are selected with ?city=<id>."""
    params = [p for p in ((f"city={city}" if city != DEFAULT_CITY else ""), query) if p]
    return "/" + (f"?{'&'.join(params)}" if params else "")


def build_pages(events: list[dict], registry, now_iso: str,
                cities: tuple[City, ...] = CITIES) -> dict[str, str]:
    """{path relative to public/: file content} — every city's pages in
    every language, plus the site-wide sitemap, robots.txt and 404."""
    now = datetime.fromisoformat(now_iso).astimezone(TZ)
    today = now.date()
    env = _env()
    pages: dict[str, str] = {}
    urls: list[str] = []
    for city in cities:
        city_pages, city_urls = _city_pages(
            env, events_in(events, city.id), stores_in(registry, city.id), now, city)
        pages.update(city_pages)
        urls += city_urls

    # The 404 page links the default city (the one the site root shows).
    home = default_city()
    home_games = sorted({e["game"] for e in events_in(events, home.id) if e.get("game")})
    es_nav = {"games": [(g, game_slug(g)) for g in home_games], "base": f"/{home.id}/",
              "today": "/" + page_path("es", ("today",), home.id),
              "weekend": "/" + page_path("es", ("weekend",), home.id)}
    pages["404.html"] = env.get_template("404.html").render(
        t=LANGS["es"], lang="es", nav=es_nav, city=home.name)
    pages["sitemap.xml"] = env.get_template("sitemap.xml").render(
        urls=[SITE_URL + "/"] + [SITE_URL + u for u in urls], lastmod=today.isoformat())
    pages["robots.txt"] = (
        "User-agent: *\nAllow: /\nDisallow: /health\n\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    return pages


def _city_pages(env: Environment, events: list[dict], stores: list[dict], now: datetime,
                city: City) -> tuple[dict[str, str], list[str]]:
    """One city's pages in every language, and their URLs for the sitemap."""
    today = now.date()
    page_tpl = env.get_template("list.html")
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

    wd = today.weekday()  # Mon=0 … Sun=6
    friday = today + timedelta(days=4 - wd) if wd <= 4 else today  # Sat/Sun: from today
    weekend_end = today + timedelta(days=7 - wd)                     # next Monday
    weekend = [e for e in upcoming
               if friday <= datetime.fromisoformat(e["datetime_start"]).date() < weekend_end]

    games = sorted({e["game"] for e in events if e.get("game")})

    # (key, events, store record, calendar query, og image) — shared by all languages.
    specs = [(("city",), within(7), None, "", "default"),
             (("today",), within(1), None, "", "default"),
             (("weekend",), weekend, None, "", "default")]
    specs += [(("game", game_slug(g), g), within(28, lambda e, g=g: e.get("game") == g), None,
               f"game={g}", game_slug(g)) for g in games]
    specs += [(("store", s["id"]), within(28, lambda e, sid=s["id"]: e.get("store_id") == sid), s,
               f"store={s['name']}", "default") for s in stores]

    path_of = lambda lang, key: page_path(lang, key, city.id)  # noqa: E731
    pages: dict[str, str] = {}
    urls: list[str] = []
    for lang, L in LANGS.items():
        date_fmt = L["date"]
        nav = {"games": [(g, game_slug(g)) for g in games],
               "base": f"/{L['prefix']}{city.id}/", "today": "/" + path_of(lang, ("today",)),
               "weekend": "/" + path_of(lang, ("weekend",))}
        for key, evs, store, query, og in specs:
            kind, c = key[0], city.name
            if kind == "game":
                g = key[2]
                title, h1 = L["game_title"].format(g=g, c=c), L["game_h1"].format(g=g, c=c)
                desc, empty = L["game_desc"].format(g=g, c=c), L["game_empty"].format(g=g)
            elif kind == "store":
                a = f", {store['address']}" if store.get("address") else ""
                title, h1 = L["store_title"].format(s=store["name"], c=c), store["name"]
                desc, empty = L["store_desc"].format(s=store["name"], a=a), L["store_empty"]
            else:
                title, h1 = L[f"{kind}_title"].format(c=c), L[f"{kind}_h1"].format(c=c)
                desc = L[f"{kind}_desc"].format(c=c, d=date_fmt(today).lower())
                empty = L[f"{kind}_empty"]
            path = path_of(lang, key)
            alternates = [(lg, SITE_URL + "/" + path_of(lg, key)) for lg in LANGS]
            pages[f"{path}index.html"] = page_tpl.render(
                t=L, lang=lang, title=title, h1=h1, description=desc, store=store,
                days=[(date_fmt(d), es) for d, es in _days(evs)], count=len(evs), empty=empty,
                nav=nav, canonical=SITE_URL + "/" + path, alternates=alternates,
                x_default=SITE_URL + "/" + path_of("es", key),
                switch_url="/" + path_of(L["switch"][1], key),
                jsonld=_jsonld(evs, stores_by_id, L["jsonld_game"], city),
                calendar_url=calendar_url(city.id, query),
                updated=date_fmt(today), city=city.name, og_image=f"{SITE_URL}/og/{og}.jpg",
            )
            urls.append("/" + path)
    return pages, urls


def write_pages(pages: dict[str, str], public_dir: Optional[pathlib.Path] = None,
                cities: tuple[City, ...] = CITIES) -> int:
    """Write changed files; remove generated city pages that are no longer
    produced (only under public/<city>/ and public/<lang>/<city>/ for the
    configured cities). Returns #files written."""
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
    roots = [public_dir / f"{L['prefix']}{c.id}" for c in cities for L in LANGS.values()]
    for stale in [p for root in roots for p in root.rglob("index.html")]:
        if stale.resolve() not in keep:
            stale.unlink()
            try:
                stale.parent.rmdir()
            except OSError:
                pass
    logger.info("Wrote pages: %d of %d files changed", written, len(pages))
    return written
