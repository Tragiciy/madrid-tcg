"""
shared/scraper_keywords.py — shared constants for scraper modules.

This module has NO imports from aggregator or any scraper, so it
cannot create circular dependencies.

Each scraper may import from here and extend locally as needed.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Game keyword detection
# ---------------------------------------------------------------------------
# List of (keyword, canonical_name) tuples sorted longest-first so that
# more specific phrases match before shorter substrings.
#
# Mirrors the GAME_CANONICAL mapping in aggregator.py, expressed as a
# list-of-tuples so scrapers can iterate in order without needing the
# aggregator's dict.
GAME_KEYWORDS: list[tuple[str, str]] = [
    # Title-driven / multi-word phrases (longest first)
    ("competitive elder dragon highlander", "Magic: The Gathering"),
    ("magic: the gathering",                "Magic: The Gathering"),
    ("presentacion riftbound",              "Riftbound"),
    ("presentación riftbound",              "Riftbound"),
    ("flesh and blood",                     "Flesh and Blood"),
    ("star wars unlimited",                 "Star Wars: Unlimited"),
    ("flesh & blood",                       "Flesh and Blood"),
    ("weiss schwarz",                       "Weiß Schwarz"),
    ("weiß schwarz",                        "Weiß Schwarz"),
    ("final fantasy tcg",                   "Final Fantasy TCG"),
    ("noob nexus night",                    "Riftbound"),
    ("nexus nights",                        "Riftbound"),
    ("nexus night",                         "Riftbound"),
    ("liga star wars",                      "Star Wars: Unlimited"),
    ("riftbound unleashed",                 "Riftbound"),
    ("unleashed",                           "Riftbound"),
    ("final fantasy",                       "Final Fantasy TCG"),
    ("star wars",                           "Star Wars: Unlimited"),
    ("naruto mythos",                       "Naruto Mythos"),
    ("yu-gi-oh!",                           "Yu-Gi-Oh!"),
    ("yu-gi-oh",                            "Yu-Gi-Oh!"),
    ("one piece",                           "One Piece"),
    ("weiss",                               "Weiß Schwarz"),
    ("pokémon",                             "Pokémon"),
    ("pokemon",                             "Pokémon"),
    ("digimon",                             "Digimon"),
    ("riftbound",                           "Riftbound"),
    ("lorcana",                             "Lorcana"),
    ("magic",                               "Magic: The Gathering"),
    ("yugioh",                              "Yu-Gi-Oh!"),
    ("fftcg",                               "Final Fantasy TCG"),
    ("fab",                                 "Flesh and Blood"),
    ("mtg",                                 "Magic: The Gathering"),
    ("swu",                                 "Star Wars: Unlimited"),
    ("rcq",                                 "Magic: The Gathering"),
]


# ---------------------------------------------------------------------------
# Format keyword detection
# ---------------------------------------------------------------------------
# Each entry is (keyword, unified_format, format_official_or_None).
#
# - `unified_format` is the canonical value that lands in `event.format`.
#   It is intentionally a small, game-agnostic vocabulary so the format
#   facet on the frontend stays readable.
# - `format_official` (when not None) is the original per-game name
#   (e.g. "Premier", "Armory", "Classic Constructed", "Liga Pokémon").
#   It is preserved for display in the event detail panel but does NOT
#   participate in filtering.
#
# Sorted longest-first so more specific phrases match before shorter
# substrings.
FORMAT_KEYWORDS: list[tuple[str, str, Optional[str]]] = [
    # ── Most specific / longest first ──────────────────────────────────
    ("competitive elder dragon highlander", "cEDH",     None),

    # Store Championship / RCQ → Standard + official label (event tier,
    # not a distinct card pool).
    ("store championship",                   "Standard", "Store Championship"),
    ("rcq",                                  "Standard", "Store Championship"),

    # MTG-specific Spanish phrases
    ("formato standard",                     "Standard", None),
    ("formato pioneer",                      "Pioneer",  None),
    ("formato modern",                       "Modern",   None),
    ("formato premier",                      "Standard", "Premier"),

    # Presentaciones / Prerelease — extract_format_for_event also has
    # an explicit title-level priority for these (Sealed bug fix).
    ("presentaciones",                       "Prerelease", None),
    ("presentación",                         "Prerelease", None),
    ("presentacion",                         "Prerelease", None),
    ("pre-release",                          "Prerelease", None),
    ("prerelease",                           "Prerelease", None),

    # Per-game "default tournament" — unified=Standard, official=local
    ("classic constructed",                  "Standard",      "Classic Constructed"),  # FAB
    ("core constructed",                     "Standard",      "Core Constructed"),     # Lorcana
    ("battle hardened",                      "Standard",      "Battle Hardened"),      # FAB
    ("liga pokémon",                         "Standard",      "Liga Pokémon"),
    ("league cup",                           "Standard",      "League Cup"),
    ("league challenge",                     "Standard",      "League Challenge"),
    ("advanced format",                      "Standard",      "Advanced"),             # YGO
    ("tournament kit",                       "Prerelease",    "Tournament Kit"),       # One Piece

    # FAB-specific differentiators
    ("living legend",                        "Living Legend", None),
    ("blitz",                                "Blitz",         None),

    # MTG single-word formats
    ("commander",                            "Commander",     None),
    ("pioneer",                              "Pioneer",       None),
    ("modern",                               "Modern",        None),
    ("legacy",                               "Legacy",        None),
    ("pauper",                               "Pauper",        None),
    ("vintage",                              "Vintage",       None),

    # SWU
    ("twin suns",                            "Twin Suns",     None),
    ("premier",                              "Standard",      "Premier"),

    # FAB Armory (event level — unified=Standard)
    ("armory",                               "Standard",      "Armory"),

    # Pokémon wider pool
    ("expanded",                             "Expanded",      None),

    # YGO alternate
    ("speed duel",                           "Speed Duel",    None),

    # One Piece limited pool
    ("east blue",                            "East Blue",     None),

    # Lorcana wider pool
    ("infinity",                             "Infinity",      None),

    # Standard (generic, must come after game-specific aliases above)
    ("standard",                             "Standard",      None),

    # Limited
    ("sellados",                             "Sealed",        None),
    ("sellado",                              "Sealed",        None),
    ("sealed",                               "Sealed",        None),
    ("draft",                                "Draft",         None),

    # cEDH / EDH shortcuts
    ("cedh",                                 "cEDH",          None),
    ("edh",                                  "Commander",     None),

    # ── REMOVED (intentionally) ────────────────────────────────────────
    # "weekly"            — это расписание, не формат
    # "league" / "liga"   — расписание
    # "casual"            — competitiveness level, not a card pool
    # "bo1" / "bo3"       — extracted into a separate `best_of` field
]


# Default format applied when no keyword matched AND the game is known.
# MTG / Flesh and Blood intentionally omitted: их форматы достаточно
# разнообразны, default лучше оставить None.
DEFAULT_FORMAT_BY_GAME: dict[str, str] = {
    "Lorcana":              "Standard",
    "Star Wars: Unlimited": "Standard",
    "Riftbound":            "Standard",
    "One Piece":            "Standard",
    "Pokémon":              "Standard",
    "Yu-Gi-Oh!":            "Standard",
    "Digimon":              "Standard",
    "Final Fantasy TCG":    "Standard",
    "Naruto Mythos":        "Standard",
    "Weiß Schwarz":         "Standard",
    # FAB: liga / unnamed events are always Classic Constructed → Standard.
    # Blitz, Living Legend, Sealed, Draft still match via keywords above.
    "Flesh and Blood":      "Standard",
}


# Title-level Prerelease pattern — wins over keyword matches for the
# Sealed/Prerelease ambiguity (e.g. Arte 9 «Presentación de 2 Cabezas»
# with WordPress category "Magic - Sealed" used to come out as Sealed).
_PRERELEASE_TITLE_RE = re.compile(
    r"\b(presentaci[oó]n(?:es)?|pre[-\s]?release|prerelease)\b",
    re.IGNORECASE,
)

# BO1/BO3 — explicit best-of marker. Never inferred.
_BEST_OF_RE = re.compile(
    r"\bbo([13])\b|\bbest[\s-]?of[\s-]?([13])\b", re.IGNORECASE
)


def _match_keywords(text: str) -> Optional[tuple[str, Optional[str]]]:
    """Internal: longest-first scan returning (unified, official) or None."""
    lower = (text or "").lower()
    for keyword, unified, official in FORMAT_KEYWORDS:
        if keyword in lower:
            return (unified, official)
    return None


def extract_format_for_event(
    title: str,
    description: str = "",
    category: str = "",
    game: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Game-aware format extraction.

    Returns (unified_format, format_official).

    Order of precedence:
      1. Prerelease in title always wins (Sealed/Prerelease ambiguity fix).
      2. Keyword match in title.
      3. Keyword match in description.
      4. Keyword match in category.
      5. DEFAULT_FORMAT_BY_GAME for `game` (unified=Standard, official=None).
      6. (None, None).
    """
    if _PRERELEASE_TITLE_RE.search(title or ""):
        return ("Prerelease", None)

    for text in (title, description, category):
        result = _match_keywords(text or "")
        if result:
            return result

    if game and game in DEFAULT_FORMAT_BY_GAME:
        return (DEFAULT_FORMAT_BY_GAME[game], None)

    return (None, None)


def extract_best_of(text: str) -> Optional[int]:
    """Return 1, 3, or None — only when BO1/BO3 is explicitly mentioned."""
    m = _BEST_OF_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1) or m.group(2))


# ---------------------------------------------------------------------------
# Legacy helpers — preserved for backward compatibility while scrapers
# migrate to extract_format_for_event.
# ---------------------------------------------------------------------------
def extract_format_from_keywords(
    text: str,
    keywords: list,
) -> Optional[str]:
    """
    Legacy: scan *text* for the first matching format keyword.
    Returns the canonical (unified) format name or None.

    Accepts both 2-tuple (legacy) and 3-tuple (new) keyword lists.
    """
    lower = (text or "").lower()
    for entry in keywords:
        keyword = entry[0]
        canonical = entry[1]
        if keyword in lower:
            return canonical
    return None


def extract_game_from_keywords(text: str, keywords: list[tuple[str, str]]) -> Optional[str]:
    """
    Scan *text* for the first matching game keyword (longest-first).
    Returns the canonical game name or None.
    """
    lower = (text or "").lower()
    for keyword, canonical in keywords:
        if keyword in lower:
            return canonical
    return None
