import unittest

from aggregator import refresh_history
from pipeline.normalize import _normalize_game, is_off_topic

MTG = "Magic: The Gathering"


class CyberpunkTests(unittest.TestCase):
    def test_cyberpunk_titles(self):
        for title in ("⚡ Torneo Cyberpunk TCG", "Liga de Cyberpunk",
                      "Night City Showdown CyberpunkTCG", "CYBERPUNK TCG Night City Brawl",
                      "Cyberpunk: Cyber Rawr", "Presentación Cyberpunk TCG Beta"):
            self.assertEqual(_normalize_game(None, title), "Cyberpunk TCG", title)

    def test_title_beats_a_wrong_store_default(self):
        # TopDeck's scraper tagged this One Piece.
        self.assertEqual(_normalize_game("One Piece", "NightCity Brawl Cyberpunk"), "Cyberpunk TCG")


class CommanderIsMagicTests(unittest.TestCase):
    def test_commander_without_game_is_magic(self):
        self.assertEqual(_normalize_game(None, "COMMANDER NIGHTS"), MTG)
        self.assertEqual(_normalize_game(None, "🏆 Torneo Semanal Commander CEDH"), MTG)
        self.assertEqual(_normalize_game(None, "Liga semanal", "Commander"), MTG)

    def test_source_game_still_wins(self):
        self.assertEqual(_normalize_game("Lorcana", "Commander Budget"), "Lorcana")

    def test_no_signal_stays_unknown(self):
        self.assertIsNone(_normalize_game(None, "Torneo de la Tarde (Side Event)"))
        self.assertIsNone(_normalize_game(None, "Commanders of the Galaxy"))


class OffTopicTests(unittest.TestCase):
    def test_board_game_nights(self):
        for title in ("MARTES TROLES Juegos de Mesa", "Juegos de Mesa con ASOCIACIÓN PANDADO",
                      "JUERNES DE MESA Juegos de Mesa"):
            self.assertTrue(is_off_topic({"title": title}), title)

    def test_tcg_events_stay(self):
        self.assertFalse(is_off_topic({"title": "Torneo Pokémon en la mesa 3"}))
        self.assertFalse(is_off_topic({"title": "COMMANDER NIGHTS"}))


class RefreshHistoryTests(unittest.TestCase):
    def test_purges_off_topic_and_fills_games(self):
        events = [
            {"title": "PANDADO Juegos de Mesa", "source": "scrapers.jupiter_central"},
            {"title": "COMMANDER NIGHTS", "format": "Commander", "source": "scrapers.jupiter_central"},
            {"title": "Liga de Cyberpunk", "source": "scrapers.arte9"},
            {"title": "Torneo Showdown", "source": "scrapers.three_stones_games"},
            {"title": "Pokémon League", "game": "Pokémon", "source": "scrapers.x"},
            {"title": "NightCity Brawl Cyberpunk", "game": "One Piece", "source": "scrapers.topdeck"},
        ]
        out = refresh_history(events)
        self.assertEqual([e["title"] for e in out],
                         ["COMMANDER NIGHTS", "Liga de Cyberpunk", "Torneo Showdown",
                          "Pokémon League", "NightCity Brawl Cyberpunk"])
        self.assertEqual([e.get("game") for e in out],
                         [MTG, "Cyberpunk TCG", None, "Pokémon", "Cyberpunk TCG"])

    def test_locator_events_untouched(self):
        e = {"title": "Commander Party", "source": "scrapers.locator_wizards"}
        refresh_history([e])
        self.assertNotIn("game", e)


if __name__ == "__main__":
    unittest.main()
