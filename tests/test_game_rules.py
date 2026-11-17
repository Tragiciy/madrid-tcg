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


class NewGamesAndSetNamesTests(unittest.TestCase):
    def test_new_games(self):
        cases = {
            "Torneo Gundam Card Game Construido": "Gundam Card Game",
            "ENTRADA AL TORNEO 4 Sobres GD 04 PHANTOM ARIA": "Gundam Card Game",
            "VEN A JUGAR MY LITTLE PONY by ZAFYX": "My Little Pony",
            "MLP LUNES 16/11": "My Little Pony",
            "🔫 🐑Palworld Official Card Game🐑🔫": "Palworld",
            "🍥⚔️ Shinobi Nights ⚔️🍥": "Naruto Mythos",
            "Presentación Naruto TCG": "Naruto Mythos",
        }
        for title, game in cases.items():
            self.assertEqual(_normalize_game(None, title), game, title)

    def test_set_and_event_names(self):
        cases = {
            "PRESENTACIÓN SECRETOS DE STRIXHAVEN": MTG,
            "Entrada STAR TREK PRERELEASE - 14/11/2026": MTG,
            "Presentación de el Hobbit": MTG,
            "TORNEO PAUPER CHAMPIONS": MTG,
            "PRESENTACION HYPERIA CITY": "Lorcana",
            "Store Showdown - Una Época sin Ley": "Star Wars: Unlimited",
            "Torneo Premier Sábado Carbonite + Playset weekly TWI": "Star Wars: Unlimited",
            "Clash of Yonkou Clasificatorio": "One Piece",
            "Sunday Noob Nexus by IreneRAWR": "Riftbound",
        }
        for title, game in cases.items():
            self.assertEqual(_normalize_game(None, title), game, title)

    def test_gundam_assemble_and_gunpla_are_not_the_card_game(self):
        self.assertIsNone(_normalize_game(None, "GUNDAM ASSEMBLE FRIENDLY BATTLE Vol.1"))
        self.assertIsNone(_normalize_game(None, "taller iniciación de gunpla"))

    def test_canonical_source_game_is_kept(self):
        # Three Stones sets SWU for "Torneo Premier Martes"; the name used to
        # be looked up as an alias and lost.
        self.assertEqual(_normalize_game("Star Wars: Unlimited", "Torneo Premier Martes"),
                         "Star Wars: Unlimited")


class OffTopicTests(unittest.TestCase):
    def test_board_game_nights(self):
        for title in ("MARTES TROLES Juegos de Mesa", "Juegos de Mesa con ASOCIACIÓN PANDADO",
                      "JUERNES DE MESA Juegos de Mesa"):
            self.assertTrue(is_off_topic({"title": title}), title)

    def test_wargames_painting_rpg_and_notices(self):
        for title in ("TORNEO KILL TEAM - 12/12/2026", "Liga AoS 2026 otoño", "Demo MCP en GTS Central",
                      "Warhammer The Old World", "Mesa Abierta de Pintura", "Curso de pincel básico",
                      "Partida Dungeons & Dragons | One Shot", "Rol All Day!", "Cthulhu 7 Edición – Oneshot",
                      "Blood on the Clocktower", "GUNDAM ASSEMBLE FRIENDLY BATTLE", "CERRADO",
                      "Estamos cerrados!", "Feliz Navidad!!!!!!"):
            self.assertTrue(is_off_topic({"title": title}), title)

    def test_off_topic_words_in_a_tcg_event_keep_it(self):
        self.assertFalse(is_off_topic({"title": "Commander Warhammer 40k precons"}))
        self.assertFalse(is_off_topic({"title": "Magic: torneo cerrado por plazas"}))
        self.assertFalse(is_off_topic({"title": "Partida de control"}))

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
