"""
scrapers/locator_bandai_dbmasters.py — Dragon Ball Super Card Game Masters (the paper game) events from the Bandai TCG+
event locator. Client and parsing live in shared/bandai_locator.py.
"""

from shared.bandai_locator import scrape_game

STORE = "Bandai TCG+ Dragon Ball Masters"


def scrape() -> list[dict]:
    return scrape_game("Dragon Ball Super Masters", STORE)
