"""
shared/scraper_keywords.py — shared constants for scraper modules.

This module has NO imports from aggregator or any scraper, so it
cannot create circular dependencies.

Each scraper may import from here and extend locally as needed.
"""

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
# List of (keyword, canonical_format) tuples sorted longest-first so that
# more specific phrases match before shorter substrings.
#
# Mirrors the ALLOWED_FORMATS set in aggregator.py, expressed as a
# list-of-tuples so scrapers can iterate in order without needing the
# aggregator's set.
#
# Includes the core English terms shared by all scrapers plus the
# Spanish variants adopted by 4+ scrapers.  Store-specific additions
# (e.g. "pre-release", "armory", "formato X") are included here because
# they map to canonical values already in ALLOWED_FORMATS and cause no
# harm when scanned against text from other stores.
FORMAT_KEYWORDS: list[tuple[str, str]] = [
    # Longest / most specific first
    ("competitive elder dragon highlander", "cEDH"),
    ("store championship",                  "Store Championship"),
    ("formato premier",                     "Premier"),
    ("formato standard",                    "Standard"),
    ("formato pioneer",                     "Pioneer"),
    ("formato modern",                      "Modern"),
    ("presentaciones",                      "Prerelease"),
    ("presentación",                        "Prerelease"),
    ("presentacion",                        "Prerelease"),
    ("pre-release",                         "Prerelease"),
    ("prerelease",                          "Prerelease"),
    ("commander",                           "Commander"),
    ("sellados",                            "Sealed"),
    ("sellado",                             "Sealed"),
    ("standard",                            "Standard"),
    ("pioneer",                             "Pioneer"),
    ("premier",                             "Premier"),
    ("modern",                              "Modern"),
    ("legacy",                              "Legacy"),
    ("pauper",                              "Pauper"),
    ("sealed",                              "Sealed"),
    ("weekly",                              "Weekly"),
    ("casual",                              "Casual"),
    ("league",                              "League"),
    ("armory",                              "Armory"),
    ("draft",                               "Draft"),
    ("cedh",                                "cEDH"),
    ("liga",                                "League"),
    ("bo3",                                 "BO3"),
    ("bo1",                                 "BO1"),
    ("rcq",                                 "Store Championship"),
]


def extract_format_from_keywords(text: str, keywords: list[tuple[str, str]]) -> Optional[str]:
    """
    Scan *text* for the first matching format keyword (longest-first).
    Returns the canonical format name or None.
    """
    lower = (text or "").lower()
    for keyword, canonical in keywords:
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
