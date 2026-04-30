"""
Arte 9 — https://arte9.com/torneos/

The site runs The Events Calendar (WordPress plugin) which exposes a
public REST API at /wp-json/tribe/events/v1/events. The page itself is
just a thin Vue/JS shell over that API.

Strategy:
  - GET pages of 50 events from the API (cheap, no auth, no JS).
  - Map each event to our schema.

Notes:
  - The plugin returns `start_date` already in Europe/Madrid local time
    (the response also carries an explicit `timezone: "Europe/Madrid"`
    field and `utc_start_date` confirms a +2h offset in summer).
    No UTC conversion bug here.
  - Event `title` is consistently empty; the real name lives inside
    the description's first heading. We pull that out with BS4.
  - `categories` often carry both the game and a format hint, e.g.
    "Magic - Commander". We split on " - " when present.
"""

import html as _html
import json
import logging
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

STORE = "Arte 9"
LANGUAGE = "es"
TZ = ZoneInfo("Europe/Madrid")
API_URL = "https://arte9.com/wp-json/tribe/events/v1/events"
PER_PAGE = 50
MAX_PAGES = 20  # safety cap; current dataset uses ~4

from shared.scraper_keywords import FORMAT_KEYWORDS, extract_format_from_keywords

# ── Title-extraction tunables ──────────────────────────────────────────
MAX_TITLE_LEN = 70           # default cap
MAX_TITLE_LEN_STRONG = 110   # raised when the heading clearly names a real event

# Heading rejected if it starts with any of these (lowercase compare).
_REJECT_PREFIXES = (
    "¿", "?",
    "este sábado", "este domingo", "este viernes", "este lunes",
    "este martes", "este miércoles", "este jueves",
    "hoy jugamos", "ven a", "aprende a jugar",
    "si quieres", "para reservar", "recordad",
    "en qué consiste", "qué premios", "qué regalos",
    "cómo funciona", "cómo se", "ya tenemos",
    # Section labels often used as h3 inside a single event page.
    "entradas", "entrada", "premios", "sorteos", "sorteo",
    "calendario", "jornada", "formato de", "acceso", "normas",
    "torneo de mañana",
    # Footnote / catalog headings with no event identity.
    "por participar", "por asistir", "por ganar", "por inscribirte",
    "packs", "regalo", "regalos por",
)

# Reject "<n> Eventos" / "<n> Jornadas" style headings that summarise
# rather than name an event.
_NUMERIC_LABEL_RE = re.compile(r"(?i)^\s*\d+\s+(eventos?|jornadas?)\b")

# Trailing markers that flag a heading as a sub-section rather than a
# real event title (asterisk = footnote ref, ellipsis = teaser).
_TRAILING_MARKER_RE = re.compile(r"[\*…]\s*$")

# Heading rejected if it contains any of these substrings (article copy).
_REJECT_TOKENS = (
    "premios", "regalos", "consiste",
    "reservar", "plazas", "inscripción", "inscripcion",
)

# A heading carrying any of these substrings is treated as a real event
# title — length cap relaxed and reject-token check skipped.
_STRONG_PATTERNS = (
    "liga", "formato", "presentación", "presentacion",
    "prerelease", "torneo", "fnm", "commander", "cedh", "draft",
)

# Bare label headings — appear as one-word section dividers in the
# event description and must not be mistaken for an event title.
_LABEL_HEADINGS = {
    "formato", "liga", "torneo", "commander", "cedh",
    "calendario", "horario", "jornada", "jornadas",
    "premios", "premio", "sorteo", "sorteos",
    "entradas", "entrada", "premier", "modern",
    "standard", "draft", "casual",
}

# Set / product names worth surfacing in synthesised titles. Order
# matters: longer names first so "Modern Horizons 3" wins over "Modern".
_KNOWN_SETS: tuple = (
    "Modern Horizons 3",
    "Outlaws of Thunder Junction",
    "Murders at Karlov Manor",
    "Unleashed",
    "Carbonite",
    "Strixhaven",
    "Bloomburrow",
    "Foundations",
    "Phyrexia",
    "Final Fantasy",
    "Pre-Rift",
)

# Localised label for the synthesised title's format slot.
_SPANISH_FMT_LABEL = {
    "Prerelease": "Presentación",
    "Sealed":     "Sellado",
    "League":     "Liga",
}

# Compact game spelling for synthesised titles.
_SHORT_GAME = {
    "Magic: The Gathering": "Magic",
    "Star Wars: Unlimited": "SWU",
    "Yu-Gi-Oh!":            "Yu-Gi-Oh",
    "Weiß Schwarz":         "Weiss",
}

# Spanish-specific "Formato: <Word>" pattern (case-insensitive).
_FORMATO_RE = re.compile(
    r"(?i)formato\s*[:\-]?\s*([A-Za-zÁÉÍÓÚáéíóúñÑ][\w\-]*)"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://arte9.com/torneos/",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_format(text: str) -> Optional[str]:
    return extract_format_from_keywords(text, FORMAT_KEYWORDS)


def _split_category(name: str) -> tuple:
    """
    Some categories embed a format hint, e.g. "Magic - Commander" or
    "Magic - Modern". Return (game, format_hint) where either may be None.
    Decodes HTML entities — Tribe sometimes returns "Flesh &amp; Blood".
    """
    if not name:
        return None, None
    name = _html.unescape(name)
    if " - " in name:
        head, _, tail = name.partition(" - ")
        return head.strip() or None, tail.strip() or None
    return name.strip() or None, None


def _datetime_iso(details: dict) -> Optional[str]:
    """Build a Madrid-tz ISO 8601 string from start_date_details/end_date_details."""
    if not details:
        return None
    try:
        dt = datetime(
            int(details["year"]), int(details["month"]), int(details["day"]),
            int(details.get("hour") or 0),
            int(details.get("minutes") or 0),
            int(details.get("seconds") or 0),
            tzinfo=TZ,
        )
        return dt.isoformat()
    except Exception as exc:
        logger.debug("%s: bad date_details %r: %s", STORE, details, exc)
        return None


def _smart_case(text: str) -> str:
    """
    Convert SCREAMING text into a readable Spanish-style title:
    capitalise each word, lowercase common articles/prepositions,
    keep recognised acronyms uppercase.
    """
    # NOTE: deliberately do NOT include "LOS" here — it would clash
    # with the Spanish article "los" (e.g. "Todos los Martes").
    UPPER = {"SWU", "MTG", "FNM", "TCG", "BO1", "BO3",
             "EDH", "CEDH", "RCQ", "SOG", "TOR"}
    SMALL = {"de", "del", "y", "en", "con", "la", "el",
             "los", "las", "a", "un", "una"}
    out: list = []
    for i, w in enumerate(re.split(r"(\s+|[–—\-:])", text)):
        if not w or w.isspace() or w in "–—-:":
            out.append(w)
            continue
        u = w.upper()
        if u in UPPER:
            out.append(u)
        elif w.lower() in SMALL and any(o.strip() for o in out):
            out.append(w.lower())
        else:
            # Capitalise; preserve internal casing if mixed.
            if w.isupper():
                out.append(w.capitalize())
            else:
                out.append(w[0].upper() + w[1:])
    result = "".join(out).strip()
    return result


def _is_acceptable_heading(text: str) -> bool:
    """
    Aggressive filter for Arte 9 description headings. A heading is
    accepted only if it looks like a real event title. Rejection rules:
      - empty
      - contains a question mark anywhere
      - starts with any marketing/section prefix
      - contains an article-copy token (premios, plazas, etc.) UNLESS
        a strong event pattern (Liga, Formato, Presentación, …) is
        also present
      - longer than MAX_TITLE_LEN, unless a strong pattern is present
        (in which case the cap relaxes to MAX_TITLE_LEN_STRONG)
      - contains no real word (just punctuation / underscores)
    """
    if not text:
        return False
    t = text.replace("\xa0", " ").strip()
    if not t:
        return False
    low = t.lower()
    # Hard reject: any question mark anywhere.
    if "?" in t or "¿" in t:
        return False
    # Reject by prefix.
    for prefix in _REJECT_PREFIXES:
        if low.startswith(prefix):
            return False
    # Reject if no real word characters.
    if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3,}", t):
        return False
    # Reject single-word label headings ("FORMATO", "PREMIOS:", etc.)
    if low.rstrip(":.").strip() in _LABEL_HEADINGS:
        return False
    # Reject "<n> Eventos" / "<n> Jornadas" summaries.
    if _NUMERIC_LABEL_RE.match(t):
        return False
    # Reject "Showdown de Lorcana*" / "Packs Para Jugadores…" sub-section
    # markers.
    if _TRAILING_MARKER_RE.search(t):
        return False
    has_strong = any(p in low for p in _STRONG_PATTERNS)
    # Article-copy tokens forbidden unless heading is clearly an event.
    if not has_strong and any(tok in low for tok in _REJECT_TOKENS):
        return False
    # Length policy: relax cap when strong pattern present.
    cap = MAX_TITLE_LEN_STRONG if has_strong else MAX_TITLE_LEN
    if len(t) > cap:
        return False
    return True


def _set_from_image_or_text(image_url: Optional[str], text: str) -> Optional[str]:
    """Detect a known set/product name from image filename or description."""
    if image_url:
        fn = image_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        # Strip trailing 'w', '-2026-04', etc. — but we only need a
        # case-insensitive substring match against _KNOWN_SETS.
        for s in _KNOWN_SETS:
            if s.replace(" ", "").lower() in fn.replace("-", "").replace("_", "").lower():
                return s
            if s.lower() in fn.lower():
                return s
    if text:
        upper = text.lower()
        for s in _KNOWN_SETS:
            if s.lower() in upper:
                return s
    return None


def _synthesise_title(game: Optional[str], fmt: Optional[str],
                      set_name: Optional[str]) -> Optional[str]:
    """Build a title from game + format + set when no clean heading exists."""
    parts: list = []
    if fmt:
        parts.append(_SPANISH_FMT_LABEL.get(fmt, fmt))
    if game:
        parts.append(_SHORT_GAME.get(game, game))
    if set_name:
        parts.append(set_name)
    return " ".join(parts) if parts else None


def _refine_title(title: Optional[str], game: Optional[str]) -> Optional[str]:
    """
    Post-process a heading-derived title to drop low-signal segments.

    Rules (applied to en-dash-separated parts after the first):
      - "<n> Jornadas?"  → drop (structural, weak)
      - "Calendario" / "Horario" → drop
      - "Formato X"      → reduce to "X" (canonical format wins)
      - bare trailing game token (SWU, Magic, …) → drop, but only when
        the first segment already names a real event (liga/torneo/…)
        and `event.game` is set, so we don't lose meaning.

    The first segment is always preserved verbatim (it carries the
    primary identity).
    """
    if not title:
        return title
    # Split on en-dash, em-dash, or hyphen separator with surrounding spaces.
    parts = [p.strip() for p in re.split(r"\s+[–—\-]\s+", title) if p.strip()]
    if len(parts) <= 1:
        return title

    head = parts[0]
    head_low = head.lower()
    head_has_strong = any(s in head_low for s in _STRONG_PATTERNS)

    redundant_re = re.compile(r"(?i)^\s*\d+\s+jornadas?\s*$")
    calendario_re = re.compile(r"(?i)^\s*(calendario|horario)\s*$")
    formato_re = re.compile(r"(?i)^\s*formato\s+([\wÁÉÍÓÚáéíóúñÑ\-]+)\s*$")

    game_tail_tokens = {
        "swu", "magic", "magic the gathering", "mtg", "lorcana",
        "one piece", "yu-gi-oh", "yu gi oh", "yugioh", "pokémon", "pokemon",
        "digimon", "riftbound", "weiss", "weiß", "weiß schwarz", "naruto",
        "star wars", "star wars unlimited",
    }

    out = [head]
    for seg in parts[1:]:
        low = seg.lower()
        if redundant_re.match(low) or calendario_re.match(low):
            continue
        m = formato_re.match(seg)
        if m:
            # "Formato Premier" → "Premier"
            out.append(m.group(1).capitalize())
            continue
        if game and head_has_strong and low in game_tail_tokens:
            # e.g. "Gran Liga Carbonite – SWU" → drop "SWU"
            continue
        out.append(seg)
    return " – ".join(out)


def _extract_title(html: str,
                   raw_title: str,
                   slug: Optional[str],
                   game: Optional[str],
                   fmt: Optional[str],
                   image_url: Optional[str],
                   description_text: str) -> Optional[str]:
    """
    Layered title selection:
      1. Walk h1..h4 in document order; first heading that passes
         _is_acceptable_heading wins.
      2. Synthesise from game + format + set (image filename / text).
      3. Use the API's raw title if it happens to be short and clean.
      4. Humanised slug (only if it's not numeric).
    """
    # 1. Heading scan
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
            text = tag.get_text(" ", strip=True).replace("\xa0", " ")
            # Decode any leftover HTML entities (e.g. double-encoded "&amp;amp;").
            text = _html.unescape(text)
            if _is_acceptable_heading(text):
                # Smart-case if mostly uppercase.
                letters = [c for c in text if c.isalpha()]
                if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
                    text = _smart_case(text)
                text = text.rstrip(" .–—-:")
                # Drop low-signal trailing segments ("9 Jornadas",
                # "Calendario", redundant game tokens) and reduce
                # "Formato X" → "X".
                return _refine_title(text, game)

    # 2. Synthesise from category + format + set
    set_name = _set_from_image_or_text(image_url, description_text)
    synth = _synthesise_title(game, fmt, set_name)
    if synth and _is_acceptable_heading(synth):
        return synth

    # 3. Raw title from API (rarely populated, but use if clean)
    if raw_title and _is_acceptable_heading(raw_title):
        return raw_title

    # 4. Humanised slug — only useful if Arte 9 used a real slug, not
    #    just the numeric event id.
    if slug and not slug.isdigit():
        return slug.replace("-", " ").title().strip()
    return None


def _parse_event(raw: dict, scraped_at: str) -> Optional[dict]:
    """Convert one /wp-json/tribe/events/v1/events item into our schema."""
    datetime_start = _datetime_iso(raw.get("start_date_details") or {})
    if not datetime_start:
        return None

    end_iso = _datetime_iso(raw.get("end_date_details") or {})
    # Tribe sets end == start when no end is configured — treat as None.
    datetime_end = end_iso if end_iso and end_iso != datetime_start else None

    raw_title = (raw.get("title") or "").strip()
    description_html = raw.get("description") or ""
    description_text = (
        BeautifulSoup(description_html, "html.parser").get_text(" ", strip=True)
        if description_html else ""
    )

    # ── Game + format from category, then text ──
    cats = raw.get("categories") or []
    cat_name = cats[0].get("name") if cats else None
    game, fmt_hint = _split_category(cat_name)

    # Format pipeline. Only canonical values from FORMAT_KEYWORDS land
    # in `format`; non-canonical hints fall through to None.
    fmt = None
    candidates: list[str] = []
    if fmt_hint:
        candidates.append(fmt_hint)
    m = _FORMATO_RE.search(description_text)
    if m:
        candidates.append(m.group(0))
    candidates.append(f"{raw_title} {description_text}")
    for c in candidates:
        fmt = _extract_format(c)
        if fmt:
            break

    # Title pipeline. Resolved AFTER format because the synthesised
    # fallback uses fmt + set_name + game.
    image = raw.get("image") or {}
    image_url = image.get("url") if isinstance(image, dict) else None
    title = _extract_title(
        description_html, raw_title, raw.get("slug"),
        game, fmt, image_url, description_text,
    )
    if not title:
        return None

    return {
        "store":          STORE,
        "game":           game,
        "format":         fmt,
        "title":          title,
        "datetime_start": datetime_start,
        "datetime_end":   datetime_end,
        "language":       LANGUAGE,
        "source_url":     raw.get("url") or None,
        "scraped_at":     scraped_at,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def scrape() -> list[dict]:
    scraped_at = datetime.now(tz=TZ).isoformat()
    events: list[dict] = []

    page = 1
    while page <= MAX_PAGES:
        try:
            resp = requests.get(
                API_URL,
                params={"per_page": PER_PAGE, "page": page},
                headers=HEADERS,
                timeout=30,
            )
        except Exception as exc:
            logger.error("%s: request failed (page %d): %s", STORE, page, exc)
            break
        if resp.status_code == 400 and page > 1:
            # Tribe returns 400 once you walk past the last page.
            break
        if resp.status_code != 200:
            logger.error("%s: HTTP %d on page %d", STORE, resp.status_code, page)
            break

        data = resp.json() or {}
        items = data.get("events") or []
        if not items:
            break

        for raw in items:
            try:
                parsed = _parse_event(raw, scraped_at)
                if parsed:
                    events.append(parsed)
            except Exception as exc:
                logger.warning("%s: skipping id=%s: %s", STORE, raw.get("id"), exc)

        total_pages = data.get("total_pages") or 0
        logger.info("%s: page %d → %d events (cumulative %d / total %d)",
                    STORE, page, len(items), len(events), data.get("total") or 0)
        if total_pages and page >= total_pages:
            break
        if len(items) < PER_PAGE:
            break
        page += 1

    # Dedup by (title, datetime_start, store).
    seen: dict = {}
    for ev in events:
        key = (ev["title"], ev["datetime_start"], ev["store"])
        seen.setdefault(key, ev)
    deduped = list(seen.values())
    if len(deduped) != len(events):
        logger.info("%s: dedup %d → %d (-%d)", STORE,
                    len(events), len(deduped), len(events) - len(deduped))

    deduped.sort(key=lambda e: e["datetime_start"])
    logger.info("%s: total events returned: %d", STORE, len(deduped))
    return deduped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
