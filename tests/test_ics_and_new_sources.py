import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scrapers import locator_gem, wargen_wargames
from shared.ics import parse_ics
from shared.locator_common import StoreResolver

MAD = ZoneInfo("Europe/Madrid")
W0 = datetime(2026, 11, 15, tzinfo=MAD)
W1 = datetime(2026, 12, 15, tzinfo=MAD)

ICS = "\r\n".join([
    "BEGIN:VCALENDAR",
    "BEGIN:VEVENT",
    "UID:weekly-1",
    "SUMMARY:Liga Lorcana",
    "DTSTART;TZID=Africa/Ceuta:20261118T170000",
    "DTEND;TZID=Africa/Ceuta:20261118T200000",
    "RRULE:FREQ=WEEKLY;UNTIL=20261209T160000Z;BYDAY=WE",
    "EXDATE;TZID=Africa/Ceuta:20261125T170000",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:weekly-1",
    "RECURRENCE-ID;TZID=Africa/Ceuta:20261202T170000",
    "SUMMARY:Liga Lorcana (moved)",
    "DTSTART;TZID=Africa/Ceuta:20261203T180000",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:one-off",
    "SUMMARY:Torneo W40K\\, Especial",
    "DTSTART:20261121T090000Z",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:cancelled",
    "SUMMARY:FNM MAGIC",
    "STATUS:CANCELLED",
    "DTSTART:20261120T163000Z",
    "END:VEVENT",
    "BEGIN:VEVENT",
    "UID:allday",
    "SUMMARY:Riftbound all day",
    "DTSTART;VALUE=DATE:20261122",
    "END:VEVENT",
    "END:VCALENDAR",
])


class IcsTests(unittest.TestCase):
    def test_rrule_exdate_override_cancel_allday(self):
        evs = parse_ics(ICS, W0, W1)
        got = [(e.summary, e.start.astimezone(MAD).strftime("%m-%d %H:%M")) for e in evs]
        self.assertEqual(got, [
            ("Liga Lorcana", "11-18 17:00"),           # 11-25 excluded by EXDATE
            ("Torneo W40K, Especial", "11-21 10:00"),   # unescaped, UTC → Madrid
            ("Liga Lorcana (moved)", "12-03 18:00"),    # 12-02 occurrence moved
            ("Liga Lorcana", "12-09 17:00"),
        ])

    def test_end_kept_for_recurring(self):
        first = parse_ics(ICS, W0, W1)[0]
        self.assertEqual((first.end - first.start).seconds, 3 * 3600)


class WargenTests(unittest.TestCase):
    def test_only_tcg_events_and_unique_ids(self):
        events = wargen_wargames.parse_events(ICS, datetime(2026, 11, 15, tzinfo=timezone.utc))
        self.assertEqual({e["game"] for e in events}, {"Lorcana"})  # W40K dropped
        ids = [e["source_event_id"] for e in events]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(e["datetime_start"].endswith("+01:00") for e in events))


class GemTests(unittest.TestCase):
    def test_fake_offset_dropped_and_distance_filtered(self):
        base = {"status": "PLANNED", "tournament_type": "Armory Event", "format_name": "Silver Age",
                "organiser_name": "Brand New FaB Store", "address": "Calle X, 28028 Madrid"}
        start = datetime.now(MAD).replace(hour=17, minute=0, second=0, microsecond=0)
        near = dict(base, id=1, lat=40.43, lon=-3.67, start_time=start.strftime("%Y-%m-%dT%H:%M:%S") + "+13:00")
        far = dict(base, id=2, lat=41.39, lon=2.17, start_time=near["start_time"])  # Barcelona
        resolver = StoreResolver(known_stores=[], overrides={})
        [e] = locator_gem.parse_events([near, far], resolver)
        self.assertTrue(e["datetime_start"].startswith(start.strftime("%Y-%m-%dT17:00:00")))
        self.assertEqual(e["title"], "Armory Event – Silver Age")
        self.assertEqual(e["source_event_id"], "gem:1")


if __name__ == "__main__":
    unittest.main()
