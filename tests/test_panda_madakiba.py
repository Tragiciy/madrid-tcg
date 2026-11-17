"""Offline parsing tests for Panda Games and MADAKIBA (trimmed real pages)."""

import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scrapers import madakiba, panda_games

MAD = ZoneInfo("Europe/Madrid")
FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class PandaGamesCalendarTests(unittest.TestCase):
    def setUp(self):
        self.events = panda_games.parse_calendar(
            _read("panda_games_calendar.html"), "2026-11-01T00:00:00+01:00",
        )

    def test_rows_become_events_with_madrid_offset(self):
        got = [(e["datetime_start"], e["game"], e["title"]) for e in self.events]
        self.assertEqual(got, [
            ("2026-11-01T11:00:00+01:00", "Magic: The Gathering", "Magic Academy"),
            ("2026-11-02T18:30:00+01:00", "My Little Pony", "Torneo semanal My Little Pony"),
            ("2026-11-04T18:30:00+01:00", "Riftbound", "Liga Semanal Riftbound Radiance"),
            ("2026-11-21T11:00:00+01:00", "Lorcana", "Liga Semanal Lorcana"),
            ("2026-11-21T17:00:00+01:00", "Magic: The Gathering", "Liga Commander"),
            ("2026-11-21T17:00:00+01:00", "One Piece", "Presentación -OP18- The Dominance of God"),
            ("2026-11-22T12:00:00+01:00", "Lorcana", "Demo Day Lorcana"),  # "12" → 12:00
            ("2026-11-22T17:00:00+01:00", "One Piece", "Presentación -OP18- The Dominance of God"),
        ])

    def test_relabelled_slug_is_not_trusted(self):
        # The "naruto" slug is labelled "My Little Pony" by the store, so its
        # event is My Little Pony, never Naruto. Board games ("other") are
        # not TCGs and may not come out as an event.
        titles = {e["title"] for e in self.events}
        self.assertNotIn("Blood on the Clocktower", titles)
        self.assertNotIn("Naruto Mythos", {e["game"] for e in self.events})

    def test_schema(self):
        ev = self.events[4]
        self.assertEqual(ev["store"], panda_games.STORE)
        self.assertEqual(ev["format"], "Commander")
        self.assertTrue(ev["source_url"].startswith("https://pandagames.es/juego/eventos/liga-commander/"))
        for key in ("format_official", "best_of", "datetime_end", "language", "scraped_at"):
            self.assertIn(key, ev)

    def test_missing_calendar_raises(self):
        with self.assertRaises(RuntimeError):
            panda_games.parse_calendar("<html><body>Mantenimiento</body></html>", "x")


class MadakibaTests(unittest.TestCase):
    NOW = datetime(2026, 11, 16, 9, 0, tzinfo=MAD)  # a Monday

    def test_weekly_leagues_are_projected(self):
        events = madakiba.parse_page(_read("madakiba_eventos.html"), self.NOW)
        got = [(e["datetime_start"], e["datetime_end"], e["game"]) for e in events[:3]]
        self.assertEqual(got, [
            ("2026-11-21T17:00:00+01:00", "2026-11-21T20:00:00+01:00", "One Piece"),
            ("2026-11-22T10:30:00+01:00", "2026-11-22T14:00:00+01:00", "Pokémon"),
            ("2026-11-22T17:00:00+01:00", "2026-11-22T20:00:00+01:00", "Dragon Ball Fusion World"),
        ])
        self.assertEqual(len(events), 3 * madakiba.RECURRING_WEEKS)
        # Full title comes from the image alt, not the truncated <h3>.
        self.assertEqual(events[1]["title"], "POKEMON LIGA DOMINGOS DE 10:30H A 14H")
        self.assertEqual(events[1]["source_url"],
                         "https://madakiba.com/es/p/pokemon-liga-domingos-de-10-30h-a-14h")

    def test_skips_magic_league_unknown_games_and_non_events(self):
        games = {e["game"] for e in madakiba.parse_page(_read("madakiba_eventos.html"), self.NOW)}
        # Magic is left to the Wizards locator; the calendar image and booster
        # box are not events. (Dragon Ball Fusion World is tracked since 2026-11.)
        self.assertEqual(games, {"One Piece", "Pokémon", "Dragon Ball Fusion World"})

    def test_dated_one_off_product(self):
        html = (
            '<div class="product"><a href="/es/p/op18-prerelease">'
            '<img alt="ONE PIECE CARD GAME OP18 PRERELEASE 21.11 A LAS 12:30H"></a>'
            '<h3><a href="/es/p/op18-prerelease">ONE PIECE CARD GAME OP18 PRERELEASE 21.11 A...</a></h3></div>'
        )
        [ev] = madakiba.parse_page(html, self.NOW)
        self.assertEqual(ev["datetime_start"], "2026-11-21T12:30:00+01:00")
        self.assertEqual(ev["game"], "One Piece")
        self.assertEqual(ev["format"], "Prerelease")
        self.assertIsNone(ev["datetime_end"])


if __name__ == "__main__":
    unittest.main()
