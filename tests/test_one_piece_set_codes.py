import unittest

from aggregator import _normalize_game
from scrapers.itaca import _extract_game
from shared.scraper_keywords import GAME_KEYWORDS, extract_game_from_keywords


class OnePieceSetCodeDetectionTests(unittest.TestCase):
    def test_itaca_presentation_with_set_code_is_one_piece(self):
        title = "PRESENTACION OP18"

        self.assertEqual(_extract_game(title), "One Piece")
        self.assertEqual(_normalize_game("Magic", title), "One Piece")

    def test_set_code_variants(self):
        for title in ("Liga OP16", "Presentación de Op-17", "Torneo OP 15"):
            with self.subTest(title=title):
                self.assertEqual(
                    extract_game_from_keywords(title, GAME_KEYWORDS), "One Piece"
                )
                self.assertEqual(_normalize_game(None, title), "One Piece")

    def test_words_containing_op_are_not_set_codes(self):
        for title in ("Top 16 Modern", "Shop 12 aniversario", "Opción 20"):
            with self.subTest(title=title):
                self.assertIsNone(_normalize_game(None, title))

    def test_explicit_game_name_still_wins(self):
        self.assertEqual(_normalize_game(None, "Magic OP16 Commander"), "Magic: The Gathering")


if __name__ == "__main__":
    unittest.main()
