import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scrapers import goblintrader_central, goblintrader_madrid_norte
from shared.goblintrader import parse_store_page

MAD = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=MAD)
FIXTURES = Path(__file__).parent / "fixtures"

SCHEMA = {
    "title", "store", "game", "format", "format_official", "best_of",
    "datetime_start", "datetime_end", "language", "source_url", "scraped_at",
}


def _parse(fixture: str, module) -> list[dict]:
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    return parse_store_page(html, module.STORE, module.PAGE_URL, NOW)


class GoblintraderCentralTest(unittest.TestCase):
    def setUp(self):
        self.events = _parse("goblintrader_12-madrid.html", goblintrader_central)

    def test_parses_every_item_with_times(self):
        self.assertEqual(len(self.events), 3)
        demo, gundam, op = self.events
        self.assertEqual(demo["title"], "Demo Warhammer 40k Aprende a jugar (PLAZAS COMPLETADAS)")
        self.assertEqual(demo["datetime_start"], "2026-11-28T16:30:00+01:00")
        self.assertEqual(demo["datetime_end"], "2026-11-28T20:00:00+01:00")
        self.assertIsNone(demo["game"])
        self.assertIsNone(gundam["game"])  # not a supported game
        self.assertEqual(op["title"], "Torneo One Piece Card Game")
        self.assertEqual(op["game"], "One Piece")
        self.assertEqual(op["datetime_start"], "2026-12-20T11:00:00+01:00")

    def test_schema(self):
        for ev in self.events:
            self.assertEqual(set(ev), SCHEMA)
            self.assertEqual(ev["store"], "Goblintrader Central")
            self.assertEqual(ev["source_url"], goblintrader_central.PAGE_URL)
            self.assertEqual(ev["language"], "es")


class GoblintraderNorteTest(unittest.TestCase):
    def test_event_without_time_uses_fallback(self):
        (ev,) = _parse("goblintrader_11-norte.html", goblintrader_madrid_norte)
        self.assertEqual(ev["title"], "Liga para Novatos!")
        self.assertEqual(ev["store"], "Goblintrader Madrid-Norte")
        self.assertEqual(ev["datetime_start"], "2026-10-04T12:00:00+02:00")
        self.assertIsNone(ev["datetime_end"])


class GoblintraderParserTest(unittest.TestCase):
    def test_missing_event_block_raises(self):
        with self.assertRaises(RuntimeError):
            parse_store_page("<html><body>maintenance</body></html>", "X", "https://x", NOW)

    def test_star_wars_miniatures_are_not_unlimited(self):
        html = """<div class="gts-store-events"><ul>
          <li class="gts-store-events__item">
            <time class="gts-store-events__date" datetime="2026-12-01"></time>
            <p class="gts-store-events__meta"><span>Tournament</span><span>Star Wars: Armada</span></p>
            <h3 class="gts-store-events__name">Torneo Star Wars: Armada</h3>
            <ul class="gts-store-events__details"><li>17:00 - 21:00</li></ul>
          </li>
          <li class="gts-store-events__item">
            <time class="gts-store-events__date" datetime="2026-12-02"></time>
            <p class="gts-store-events__meta"><span>Tournament</span><span>Star Wars Unlimited</span></p>
            <h3 class="gts-store-events__name">Torneo SWU Premier</h3>
          </li>
        </ul></div>"""
        armada, swu = parse_store_page(html, "X", "https://x", NOW)
        self.assertIsNone(armada["game"])
        self.assertEqual(swu["game"], "Star Wars: Unlimited")


if __name__ == "__main__":
    unittest.main()
