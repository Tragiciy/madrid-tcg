"""
discoverers/riftbound_locator.py — Riftbound store discovery.

Fetches Madrid-area stores from the official Riftbound Play Network locator.
"""

from shared.carde_store_locator import discover_game_stores

SOURCE = "riftbound_locator"
GAME = "Riftbound"
API_URL = "https://api.cloudflare.riftbound.uvsgames.com/hydraproxy/api/v2/game-stores/"
LOCATOR_URL = "https://locator.riftbound.uvsgames.com/stores/search?lang=en"
RIFTBOUND_GAME_ID = 3


def discover() -> list[dict]:
    return discover_game_stores(
        source=SOURCE,
        game=GAME,
        api_url=API_URL,
        locator_url=LOCATOR_URL,
        game_id=RIFTBOUND_GAME_ID,
    )
