import unittest
from datetime import datetime, timedelta
from unittest import mock

from discoverers import cyberpunk_locator
from scrapers import locator_melee
from shared.locator_common import TZ, StoreResolver
from shared.store_registry import StoreRegistry


def _registry():
    return StoreRegistry({
        "ignore": {"names": [], "external": []},
        "stores": [
            {"id": "topdeck", "name": "TopDeck", "city": "madrid", "address": "Calle del Cardenal Silíceo, 17, 28002 Madrid",
             "aliases": {"names": [], "external": ["melee:13797", "wizards:1"]}},
            {"id": "jupiter-central", "name": "Júpiter Central", "city": "madrid", "address": "",
             "aliases": {"names": [], "external": ["melee:9488"]}},
        ],
    })


def _in_days(days: int, hour_utc: int = 17) -> str:
    d = datetime.now(TZ).date() + timedelta(days=days)
    return f"{d.isoformat()}T{hour_utc:02d}:00:00Z"


def _row(**kw):
    row = {"id": 497162, "name": "Torneo Local Picking MARTES", "gameDescription": "Cyberpunk TCG",
           "startDate": _in_days(3), "organizationName": "TopDeckjuegos", "formatString": "",
           "enrolledPlayerCount": 12}
    row.update(kw)
    return row


class OrganizationIdsTests(unittest.TestCase):
    def test_only_melee_aliases(self):
        self.assertEqual(locator_melee.organization_ids(_registry()), ["13797", "9488"])


class ParseTests(unittest.TestCase):
    def parse(self, rows, org_id="13797"):
        return locator_melee.parse_tournaments(rows, org_id, StoreResolver(_registry()))

    def test_tournament_becomes_event_in_madrid_time(self):
        [e] = self.parse([_row()])
        self.assertEqual(e["store"], "TopDeck")  # by the melee: alias, not the organizer's name
        self.assertEqual(e["game"], "Cyberpunk TCG")
        self.assertTrue(e["datetime_start"].endswith(("T18:00:00+01:00", "T19:00:00+02:00")))
        self.assertEqual(e["source_url"], "https://melee.gg/Tournament/View/497162")
        self.assertEqual(e["source_event_id"], "melee:497162")
        self.assertEqual(e["registered"], 12)

    def test_game_names(self):
        rows = [_row(id=1, gameDescription="STAR WARS: Unlimited"),
                _row(id=2, gameDescription="Disney Lorcana"),  # its official locator has it
                _row(id=3, gameDescription="Magic: The Gathering"),
                _row(id=4, gameDescription="Marvel Snap")]
        self.assertEqual([e["game"] for e in self.parse(rows)], ["Star Wars: Unlimited"])

    def test_midnight_utc_placeholder_is_skipped(self):
        rows = [_row(id=1, startDate=_in_days(3, hour_utc=0)), _row(id=2, startDate=_in_days(3, hour_utc=17))]
        self.assertEqual([e["source_event_id"] for e in self.parse(rows)], ["melee:2"])

    def test_cancelled_and_finished_are_skipped(self):
        rows = [_row(id=1, status="Registration"), _row(id=2, status="Canceled"), _row(id=3, status="Ended")]
        self.assertEqual([e["source_event_id"] for e in self.parse(rows)], ["melee:1"])

    def test_outside_window_and_incomplete_rows_are_skipped(self):
        rows = [_row(id=1, startDate=_in_days(-2)), _row(id=2, startDate=_in_days(400)),
                _row(id=3, name=""), _row(id=4, startDate="")]
        self.assertEqual(self.parse(rows), [])


class CyberpunkLocatorTests(unittest.TestCase):
    def test_candidates(self):
        payload = {"stores": [
            {"id": "a", "storeName": "Jupiter Madrid", "addressFormatted": "Calle de Manuel Silvela, 8, 28010 Madrid",
             "lat": 40.43, "lng": -3.70, "socialWebsite": "www.jupiterjuegos.com", "country": "ES"},
            {"id": "b", "storeName": "", "addressFormatted": "x"},
        ]}
        resp = mock.Mock(json=lambda: payload, raise_for_status=lambda: None)
        with mock.patch.object(cyberpunk_locator.requests, "get", return_value=resp):
            [c] = cyberpunk_locator.discover()
        self.assertEqual(c["name"], "Jupiter Madrid")
        self.assertEqual(c["games"], ["Cyberpunk TCG"])
        self.assertEqual(c["website"], "https://www.jupiterjuegos.com")
        self.assertEqual(c["evidence"][0]["coordinates"], {"lat": 40.43, "lng": -3.70})

    def test_network_error_returns_nothing(self):
        with mock.patch.object(cyberpunk_locator.requests, "get",
                               side_effect=cyberpunk_locator.requests.ConnectionError()):
            self.assertEqual(cyberpunk_locator.discover(), [])


if __name__ == "__main__":
    unittest.main()
