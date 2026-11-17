"""
scrapers/locator_bandai_gundam.py — Gundam Card Game events from the Bandai TCG+
event locator. Client and parsing live in shared/bandai_locator.py.
"""

from shared.bandai_locator import scrape_game

STORE = "Bandai TCG+ Gundam"


def scrape() -> list[dict]:
    return scrape_game("Gundam Card Game", STORE)
