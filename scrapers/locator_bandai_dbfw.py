"""
scrapers/locator_bandai_dbfw.py — Dragon Ball Super Card Game Fusion World events from the Bandai TCG+
event locator. Client and parsing live in shared/bandai_locator.py.
"""

from shared.bandai_locator import scrape_game

STORE = "Bandai TCG+ Dragon Ball Fusion World"


def scrape() -> list[dict]:
    return scrape_game("Dragon Ball Fusion World", STORE)
