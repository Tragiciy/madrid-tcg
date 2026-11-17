"""Game/format vocabulary, game normalisation and the per-event shape check."""

import logging
import re
from datetime import datetime
from typing import Optional

from pipeline.config import TZ
from shared.scraper_keywords import extract_game_from_patterns

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Game / format normalisation
# ---------------------------------------------------------------------------

GAME_CANONICAL: dict = {
    # Magic
    "magic": "Magic: The Gathering",
    "magic: the gathering": "Magic: The Gathering",
    "mtg": "Magic: The Gathering",
    # Pokémon
    "pokemon": "Pokémon",
    "pokémon": "Pokémon",
    # One Piece
    "one piece": "One Piece",
    "nakama": "One Piece",
    # Digimon
    "digimon": "Digimon",
    # Lorcana
    "lorcana": "Lorcana",
    # Star Wars
    "star wars": "Star Wars: Unlimited",
    "star wars unlimited": "Star Wars: Unlimited",
    "swu": "Star Wars: Unlimited",
    "sw unlimited": "Star Wars: Unlimited",
    # Yu-Gi-Oh
    "yu-gi-oh": "Yu-Gi-Oh!",
    "yugioh": "Yu-Gi-Oh!",
    "yu-gi-oh!": "Yu-Gi-Oh!",
    # Flesh and Blood
    "flesh and blood": "Flesh and Blood",
    "flesh & blood": "Flesh and Blood",
    "fab": "Flesh and Blood",
    # Weiss Schwarz
    "weiss": "Weiß Schwarz",
    "weiss schwarz": "Weiß Schwarz",
    "weiß schwarz": "Weiß Schwarz",
    # Final Fantasy TCG. "final fantasy tcg" must come before
    # "final fantasy" so the longer match wins; both normalise to the
    # same canonical name.
    "final fantasy tcg": "Final Fantasy TCG",
    "fftcg":             "Final Fantasy TCG",
    "final fantasy":     "Final Fantasy TCG",
    # Title-driven
    "liga star wars": "Star Wars: Unlimited",
    "rcq": "Magic: The Gathering",
    # "Pre-Rift" is the Riftbound: Vendetta prerelease.
    "pre-rift": "Riftbound",
    "pre rift": "Riftbound",
    "presentacion riftbound": "Riftbound",
    "presentación riftbound": "Riftbound",
    "riftbound unleashed": "Riftbound",
    "riftbound": "Riftbound",
    "nexus nights": "Riftbound",
    "nexus night": "Riftbound",
    "noob nexus night": "Riftbound",
    "naruto mythos": "Naruto Mythos",
    # Cyberpunk TCG: titles say "Cyberpunk", "CyberpunkTCG" or name the
    # "Night City" events (Night City Showdown / Brawl).
    "cyberpunk": "Cyberpunk TCG",
    "night city": "Cyberpunk TCG",
    "nightcity": "Cyberpunk TCG",
    # More Riftbound / One Piece / Naruto event names.
    "noob nexus": "Riftbound",
    "yonkou": "One Piece",
    "grand line": "One Piece",
    "naruto": "Naruto Mythos",
    "shinobi nights": "Naruto Mythos",
    "shinobi legends": "Naruto Mythos",
    # Set names stores use instead of the game (prereleases, leagues).
    "strixhaven": "Magic: The Gathering",
    "marvel super heroes": "Magic: The Gathering",
    "realidad fracturada": "Magic: The Gathering",
    "reality fracture": "Magic: The Gathering",
    "el hobbit": "Magic: The Gathering",
    "star trek": "Magic: The Gathering",
    "hyperia city": "Lorcana",
    "cenizas del imperio": "Star Wars: Unlimited",
    "una época sin ley": "Star Wars: Unlimited",
    "una epoca sin ley": "Star Wars: Unlimited",
    "twin suns": "Star Wars: Unlimited",
    "carbonite": "Star Wars: Unlimited",
    # Gundam Card Game. Gundam Assemble (a miniatures game) and Gunpla
    # (model kits) win as longer matches and resolve to no game.
    "gundam": "Gundam Card Game",
    "gundam assemble": "Gundam Assemble",
    "gunpla": "Gunpla",
    "my little pony": "My Little Pony",
    "mlp": "My Little Pony",
    "dragon ball": "Dragon Ball Fusion World",
    "dragon ball fusion world": "Dragon Ball Fusion World",
    "fusion world": "Dragon Ball Fusion World",
    "dbfw": "Dragon Ball Fusion World",
    "dragon ball super masters": "Dragon Ball Super Masters",
    "dbscg masters": "Dragon Ball Super Masters",
    "palworld": "Palworld",
}

ALLOWED_GAMES: set = {
    "Magic: The Gathering",
    "Pokémon",
    "One Piece",
    "Digimon",
    "Yu-Gi-Oh!",
    "Lorcana",
    "Star Wars: Unlimited",
    "Flesh and Blood",
    "Weiß Schwarz",
    "Riftbound",
    "Naruto Mythos",
    "Final Fantasy TCG",
    "Cyberpunk TCG",
    "Gundam Card Game",
    "Dragon Ball Fusion World",
    "Dragon Ball Super Masters",
    "My Little Pony",
    "Palworld",
}

# Formats only Magic has: an event in one of these with no game is Magic
# (e.g. "COMMANDER NIGHTS", "Commander Casual B2/B3", "TORNEO PAUPER").
MAGIC_ONLY_FORMATS: set = {"Commander", "cEDH", "Pauper"}
_MAGIC_ONLY_TITLE_RE = re.compile(r"\b(commander|c?edh|pauper)\b", re.IGNORECASE)

# Events that are not trading-card games: board games, wargames and
# miniatures, painting and modelling, tabletop RPGs. Only applied when no
# TCG game is detected, so e.g. a Warhammer 40k Commander night stays.
_OFF_TOPIC_TITLE_RE = re.compile(
    r"\bjuegos? de mesa\b|blood on the clocktower|partida demostraci[oó]n"
    r"|warhammer|warhmmer|\bwh\b|\b40k\b|40\.000|kill ?team|age of sigmar|\baos\b|spearhead"
    r"|old world|underworlds|horus heresy|\bherej[ií]a\b|legiones imperialis|blood bowl"
    r"|battletech|warcrow|trench crusade|grimdark|\bopr\b|marvel crisis protocol|\bmcp\b"
    r"|starcraft|mordheim|space gits|hexadome|guerra del anillo|\besdla\b|gundam assemble"
    r"|\bnarrativ[oa]\b"
    r"|pintura|pincel|miniaturas|peanas|escenograf|speedpaint|gunpla"
    r"|\brol\b|d&d|\bdnd\b|dungeons|cthulhu|mausritter|maho shojo|10 candles"
    r"|imperium maledictum",
    re.IGNORECASE,
)
# Store notices posted as calendar entries ("CERRADO", "Feliz Navidad!!!").
# Matched on the whole title and dropped whatever the game.
_NOTICE_TITLE_RE = re.compile(
    r"^\W*(cerrado|cerramos|estamos cerrad[oa]s|feliz navidad|feliz año nuevo"
    r"|felices reyes)\W*$",
    re.IGNORECASE,
)


def is_off_topic(event: dict) -> bool:
    """True for events that are not TCG events and must not be published."""
    title = event.get("title") or ""
    if _NOTICE_TITLE_RE.search(title):
        return True
    if not _OFF_TOPIC_TITLE_RE.search(title):
        return False
    return _normalize_game(event.get("game"), title, event.get("format")) is None


# Keep in sync with FORMAT_KEYWORDS values in scrapers/*.py.
ALLOWED_FORMATS: set = {
    # MTG-rich vocabulary
    "Standard", "Pioneer", "Modern", "Legacy", "Pauper", "Vintage",
    "Commander", "cEDH",
    # Flesh and Blood differentiators
    "Blitz", "Living Legend",
    # Pokémon wider pool
    "Expanded",
    # YGO alternate
    "Speed Duel",
    # One Piece limited pool
    "East Blue",
    # Lorcana wider pool
    "Infinity",
    # SWU 2-leader
    "Twin Suns",
    # Limited / pre-launch
    "Sealed", "Draft", "Prerelease",
    # Note: Weekly / League / Casual / Store Championship / BO1 / BO3 /
    #       Premier / Armory are intentionally NOT allowed as `format`.
    #       Premier/Armory/CC/etc. → format=Standard, format_official=<name>
    #       BO1/BO3 → separate `best_of` field.
    #       Weekly/League/Casual → not formats (event types / level).
}


def _normalize_game(game: Optional[str], title: str = "",
                    fmt: Optional[str] = None) -> Optional[str]:
    """Resolve to a canonical game name, prioritising explicit title signals.

    With no game from the title or the source, a Magic-only format
    (Commander, cEDH, Pauper) in the title or `fmt` means Magic.
    """
    lower_title = title.lower()
    for keyword in sorted(GAME_CANONICAL, key=len, reverse=True):
        if keyword in lower_title:
            canonical = GAME_CANONICAL[keyword]
            return canonical if canonical in ALLOWED_GAMES else None

    canonical = extract_game_from_patterns(title)
    if canonical:
        return canonical if canonical in ALLOWED_GAMES else None

    if game in ALLOWED_GAMES:
        return game
    if game:
        canonical = GAME_CANONICAL.get(game.lower())
        if canonical:
            return canonical if canonical in ALLOWED_GAMES else None

    if fmt in MAGIC_ONLY_FORMATS or _MAGIC_ONLY_TITLE_RE.search(title):
        return "Magic: The Gathering"

    return None


# ---------------------------------------------------------------------------
# Per-event basic shape check (no time filter — past events welcome)
# ---------------------------------------------------------------------------

def _parse_dt(value: str) -> Optional[datetime]:
    """Parse ISO 8601, default tz=Madrid if naive. None on error."""
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def _shape_drop_reason(event: dict) -> Optional[str]:
    """
    Return the reason this event must be dropped, or None if it passes
    the basic shape check. Reasons are buckets, not free-text:
      - 'missing_fields': required field empty/absent
      - 'bad_datetime':   datetime_start unparseable or without a UTC offset
                          (naive values would crash aware comparisons in merge)
    """
    for field in ("store", "title", "datetime_start"):
        if not event.get(field):
            return "missing_fields"
    try:
        has_offset = datetime.fromisoformat(event["datetime_start"]).tzinfo is not None
    except (TypeError, ValueError):
        has_offset = False
    if not has_offset:
        logger.warning(
            "Drop: bad datetime_start — %s / %s",
            event.get("title"), event.get("datetime_start"),
        )
        return "bad_datetime"
    return None
