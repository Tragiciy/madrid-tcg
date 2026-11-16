"""Offline parse of a trimmed real Jupiter Juegos Madrid calendar page."""

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scrapers import jupiter_juegos as jj

FIXTURE = Path(__file__).parent / "fixtures" / "scrapers" / "jupiter_madrid_2026-11.html"


class JupiterJuegosParseTests(unittest.TestCase):
    def setUp(self):
        soup = BeautifulSoup(FIXTURE.read_text(encoding="utf-8"), "html.parser")
        self.events = jj._events_for_month(soup, 2026, 11, "2026-11-16T12:00:00+01:00")

    def test_grid_parsed_across_month_boundary(self):
        # 8 day columns, 26 Oct - 2 Nov; the featured block is not counted.
        self.assertEqual(len(self.events), 18)
        starts = [e["datetime_start"] for e in self.events]
        self.assertEqual(starts[0], "2026-10-26T16:30:00+01:00")
        self.assertIn("2026-11-01T11:00:00+01:00", starts)
        self.assertTrue(starts[-1].startswith("2026-11-02T"))

    def test_event_fields(self):
        rift = next(e for e in self.events if e["title"] == "RIFTBOUND Nexus Night")
        self.assertEqual(rift["store"], "Jupiter Juegos")
        self.assertEqual(rift["game"], "Riftbound")
        self.assertEqual(rift["datetime_start"], "2026-11-01T11:00:00+01:00")
        self.assertTrue(rift["source_url"].startswith("https://jupiterjuegos.com/tiendas/actividad/"))
        for key in ("format", "format_official", "best_of", "datetime_end",
                    "language", "scraped_at"):
            self.assertIn(key, rift)

    def test_page_without_calendar_raises(self):
        # The old ?tienda= URL now returns the branch index with no grid.
        soup = BeautifulSoup("<html><body>Nuestras tiendas</body></html>", "html.parser")
        with self.assertRaises(ValueError):
            jj._events_for_month(soup, 2026, 11, "x")


if __name__ == "__main__":
    unittest.main()
