import unittest
from datetime import datetime, timedelta

from scrapers import locator_ravensburger, locator_wizards
from shared import bandai_locator
from shared.locator_common import TZ, StoreResolver, make_event, positive_int
from shared.store_registry import StoreRegistry


def _resolver():
    return StoreResolver(StoreRegistry())


def _iso(days):
    return (datetime.now(TZ) + timedelta(days=days)).replace(microsecond=0).isoformat()


class AttendanceTests(unittest.TestCase):
    def test_make_event_keeps_only_known_values(self):
        e = make_event(title="T", store="S", game="Lorcana", start_iso=_iso(1), source_event_id="x:1",
                       source_url="u", capacity=24, registered=0, participants=None, full=False)
        self.assertEqual(e["capacity"], 24)
        for absent in ("registered", "participants", "full"):
            self.assertNotIn(absent, e)
        self.assertEqual([positive_int(v) for v in ("12", 0, None, "x", -3)], [12, None, None, None, None])

    def test_wizards_players_are_signups_before_and_participants_after(self):
        def raw(days, players):
            start = (datetime.now(TZ) + timedelta(days=days)).astimezone().strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
            return {"id": str(days), "title": "FNM", "scheduledStartTime": start, "capacity": 32,
                    "numberOfPlayers": players, "organization": {"id": "1", "name": "Store"}}
        future, past = locator_wizards.parse_events([raw(3, 5), raw(-2, 18)], {}, _resolver())
        self.assertEqual((future.get("registered"), future.get("participants"), future["capacity"]), (5, None, 32))
        self.assertEqual((past.get("registered"), past.get("participants")), (None, 18))

    def test_ravensburger_complete_events_report_players(self):
        def raw(i, days, reg, start_count):
            return {"id": i, "name": "Weekly", "start_datetime": _iso(days), "event_status": "SCHEDULED",
                    "capacity": 16, "registered_user_count": reg, "starting_player_count": start_count,
                    "store": {"id": 1, "name": "Store"}}
        future, past = locator_ravensburger.parse_events([raw(1, 2, 3, 0), raw(2, -1, 9, 8)], _resolver())
        self.assertEqual((future["registered"], future.get("participants")), (3, None))
        self.assertEqual((past.get("registered"), past["participants"]), (None, 8))

    def test_bandai_capacity_and_full_flag(self):
        base = {"organizer_id": 1, "organizer_name": "Store", "start_datetime": "2026-12-01T17:00:00",
                "timezone": "Europe/Madrid", "event_series_title": "OP Store Tournament", "max_join_count": 32}
        shown, hidden = bandai_locator.parse_events(
            [dict(base, id=1, is_over_max_join_count=True), dict(base, id=2, is_capacity_hidden=True)],
            "One Piece", _resolver())
        self.assertEqual((shown["capacity"], shown["full"]), (32, True))
        self.assertNotIn("capacity", hidden)


if __name__ == "__main__":
    unittest.main()
