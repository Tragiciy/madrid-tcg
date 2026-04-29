"""
scrapers/shared.py — shared constants for scraper modules.

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
