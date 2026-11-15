"""
scrapers/locator_bandai_digimon.py — Digimon events from the Bandai TCG+
event locator. Client and parsing live in shared/bandai_locator.py.
"""

from shared.bandai_locator import scrape_game

STORE = "Bandai TCG+ Digimon"


def scrape() -> list[dict]:
    return scrape_game("Digimon", STORE)
