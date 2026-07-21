import unittest

from aggregator import _normalize_game
from scrapers.itaca import _extract_game
from shared.scraper_keywords import (
    GAME_KEYWORDS,
    extract_format_for_event,
    extract_game_from_keywords,
)


class PreRiftVendettaDetectionTests(unittest.TestCase):
    def test_pre_rift_vendetta_is_riftbound_prerelease(self):
        title = "PRE RIFT VENDETTA"

        self.assertEqual(
            extract_game_from_keywords(title, GAME_KEYWORDS), "Riftbound"
        )
        self.assertEqual(_extract_game(title), "Riftbound")
        self.assertEqual(_normalize_game("Magic", title), "Riftbound")
        self.assertEqual(
            extract_format_for_event(title=title, game="Riftbound"),
            ("Prerelease", None),
        )

    def test_hyphenated_pre_rift_is_also_supported(self):
        title = "Pre-Rift Vendetta"

        self.assertEqual(
            extract_game_from_keywords(title, GAME_KEYWORDS), "Riftbound"
        )
        self.assertEqual(
            extract_format_for_event(title=title, game="Riftbound"),
            ("Prerelease", None),
        )


if __name__ == "__main__":
    unittest.main()
