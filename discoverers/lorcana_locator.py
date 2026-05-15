"""
discoverers/lorcana_locator.py — Disney Lorcana store discovery.

Fetches Madrid-area stores from the official Ravensburger Play API used by
the Disney Lorcana store locator.
"""

from shared.carde_store_locator import discover_game_stores

SOURCE = "lorcana_locator"
GAME = "Lorcana"
API_URL = "https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/game-stores/"
LOCATOR_URL = "https://tcg.ravensburgerplay.com/stores/search"
LORCANA_GAME_ID = 1


def discover() -> list[dict]:
    return discover_game_stores(
        source=SOURCE,
        game=GAME,
        api_url=API_URL,
        locator_url=LOCATOR_URL,
        game_id=LORCANA_GAME_ID,
    )
