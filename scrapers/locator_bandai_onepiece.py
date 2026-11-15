"""
scrapers/locator_bandai_onepiece.py — One Piece events from the Bandai TCG+
event locator. Client and parsing live in shared/bandai_locator.py.
"""

from shared.bandai_locator import scrape_game

STORE = "Bandai TCG+ One Piece"


def scrape() -> list[dict]:
    return scrape_game("One Piece", STORE)
