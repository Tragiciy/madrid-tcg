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


def _cal(*events):
    return "\r\n".join(["BEGIN:VCALENDAR", *events, "END:VCALENDAR"])


class IcsRobustnessTests(unittest.TestCase):
    def test_bad_rrule_skips_only_that_series(self):
        text = _cal(
            "BEGIN:VEVENT\r\nUID:bad\r\nSUMMARY:Bad\r\nDTSTART;TZID=Europe/Madrid:20261118T170000\r\n"
            "RRULE:FREQ=WEEKLY;X-FOO=1\r\nEND:VEVENT",
            "BEGIN:VEVENT\r\nUID:ok\r\nSUMMARY:Ok\r\nDTSTART:20261120T170000Z\r\nEND:VEVENT",
        )
        self.assertEqual([e.summary for e in parse_ics(text, W0, W1)], ["Ok"])

    def test_date_only_until_is_accepted(self):
        text = _cal("BEGIN:VEVENT\r\nUID:u\r\nSUMMARY:W\r\nDTSTART;TZID=Europe/Madrid:20261118T170000\r\n"
                    "RRULE:FREQ=WEEKLY;UNTIL=20261125\r\nEND:VEVENT")
        self.assertEqual(len(parse_ics(text, W0, W1)), 2)

    def test_valarm_does_not_overwrite_event_fields(self):
        text = _cal("BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Liga\r\nDESCRIPTION:Lorcana weekly\r\n"
                    "DTSTART:20261120T170000Z\r\nBEGIN:VALARM\r\nACTION:EMAIL\r\nSUMMARY:Alarm\r\n"
                    "DESCRIPTION:This is an event reminder\r\nEND:VALARM\r\nEND:VEVENT")
        [e] = parse_ics(text, W0, W1)
        self.assertEqual((e.summary, e.description), ("Liga", "Lorcana weekly"))

    def test_override_without_usable_start_still_suppresses_occurrence(self):
        text = _cal(
            "BEGIN:VEVENT\r\nUID:s\r\nSUMMARY:W\r\nDTSTART;TZID=Europe/Madrid:20261118T170000\r\n"
            "RRULE:FREQ=WEEKLY;COUNT=2\r\nEND:VEVENT",
            "BEGIN:VEVENT\r\nUID:s\r\nRECURRENCE-ID;TZID=Europe/Madrid:20261125T170000\r\n"
            "STATUS:CANCELLED\r\nEND:VEVENT",
        )
        self.assertEqual([e.start.day for e in parse_ics(text, W0, W1)], [18])

    def test_date_valued_recurrence_id_and_lowercase_tzid(self):
        text = _cal(
            "BEGIN:VEVENT\r\nUID:d\r\nSUMMARY:W\r\nDTSTART;tzid=Europe/Madrid:20261118T170000\r\n"
            "RRULE:FREQ=WEEKLY;COUNT=2\r\nEND:VEVENT",
            "BEGIN:VEVENT\r\nUID:d\r\nRECURRENCE-ID;VALUE=DATE:20261125\r\nSUMMARY:Moved\r\n"
            "DTSTART;TZID=Europe/Madrid:20261126T190000\r\nEND:VEVENT",
        )
        got = [(e.summary, e.start.strftime("%d %H"), e.occurrence_of.day) for e in parse_ics(text, W0, W1)]
        self.assertEqual(got, [("W", "18 17", 18), ("Moved", "26 19", 25)])


class WargenTests(unittest.TestCase):
    def test_only_tcg_events_and_unique_ids(self):
        events = wargen_wargames.parse_events(ICS, datetime(2026, 11, 15, tzinfo=timezone.utc))
        self.assertEqual({e["game"] for e in events}, {"Lorcana"})  # W40K dropped
        ids = [e["source_event_id"] for e in events]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(e["datetime_start"].endswith("+01:00") for e in events))

    def test_moved_occurrence_keeps_its_id(self):
        events = wargen_wargames.parse_events(ICS, datetime(2026, 11, 15, tzinfo=timezone.utc))
        moved = next(e for e in events if e["title"] == "Liga Lorcana (moved)")
        self.assertIn("2026-12-02T17:00:00+01:00", moved["source_event_id"])

    def test_nakama_weekly_is_one_piece(self):
        from shared.scraper_keywords import GAME_KEYWORDS, extract_game_from_keywords
        self.assertEqual(extract_game_from_keywords("Nakama Weekly OP", GAME_KEYWORDS), "One Piece")


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
