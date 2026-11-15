"""Game/format vocabulary, game normalisation and the per-event shape check."""

import logging
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
}

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


def _normalize_game(game: Optional[str], title: str = "") -> Optional[str]:
    """Resolve to a canonical game name, prioritising explicit title signals."""
    lower_title = title.lower()
    for keyword in sorted(GAME_CANONICAL, key=len, reverse=True):
        if keyword in lower_title:
            canonical = GAME_CANONICAL[keyword]
            return canonical if canonical in ALLOWED_GAMES else None

    canonical = extract_game_from_patterns(title)
    if canonical:
        return canonical if canonical in ALLOWED_GAMES else None

    if game:
        canonical = GAME_CANONICAL.get(game.lower())
        if canonical:
            return canonical if canonical in ALLOWED_GAMES else None

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
