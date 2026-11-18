import pathlib
import unittest

from pipeline.publish import EVENT_FIELDS
from pipeline.time_fixes import apply_time_fixes, correct_start_time


def bandai(start, **kw):
    return {"title": "OP Store Tournament Event", "store": "Genex Comics",
            "source_event_id": "bandai:8474037",
            "source_url": "https://www.bandai-tcg-plus.com/event/8474037",
            "datetime_start": start, **kw}


class BandaiPmRuleTests(unittest.TestCase):
    def test_0500_becomes_1700_and_keeps_the_source_time(self):
        e = bandai("2026-11-21T05:00:00+01:00")
        self.assertTrue(correct_start_time(e))
        self.assertEqual(e["datetime_start"], "2026-11-21T17:00:00+01:00")
        self.assertEqual(e["datetime_start_source"], "2026-11-21T05:00:00+01:00")
        self.assertIs(e["time_corrected"], True)

    def test_odd_minutes_and_edges_of_the_window(self):
        for src, fixed in (("2026-12-01T05:14:00+01:00", "2026-12-01T17:14:00+01:00"),
                           ("2026-11-21T04:30:00+01:00", "2026-11-21T16:30:00+01:00"),
                           ("2026-12-02T06:59:00+01:00", "2026-12-02T18:59:00+01:00"),
                           ("2026-12-02T01:00:00+01:00", "2026-12-02T13:00:00+01:00")):
            e = bandai(src)
            self.assertTrue(correct_start_time(e), src)
            self.assertEqual(e["datetime_start"], fixed)

    def test_starts_outside_the_window_are_untouched(self):
        for start in ("2026-11-21T00:00:00+01:00",   # "no time given"
                      "2026-11-21T00:30:00+01:00",
                      "2026-11-21T07:00:00+01:00",
                      "2026-11-21T09:30:00+01:00",
                      "2026-11-21T10:00:00+01:00",
                      "2026-11-21T17:30:00+01:00"):
            e = bandai(start)
            self.assertFalse(correct_start_time(e), start)
            self.assertEqual(e["datetime_start"], start)
            self.assertNotIn("time_corrected", e)
            self.assertNotIn("datetime_start_source", e)

    def test_other_sources_are_untouched(self):
        # Ravensburger's "Thursday Night Weekly Play" on Saturday 03:00 is a
        # different problem: not this rule.
        e = {"title": "Thursday Night Weekly Play (Constructed)", "store": "Kingdom Wargames",
             "source_event_id": "ravensburger:953047",
             "source_url": "https://tcg.ravensburgerplay.com/events/953047",
             "datetime_start": "2026-11-14T03:00:00+01:00"}
        self.assertFalse(correct_start_time(e))
        self.assertEqual(e["datetime_start"], "2026-11-14T03:00:00+01:00")
        store = {"title": "Torneo", "store": "Arte 9", "source_event_id": "122201",
                 "source_url": "https://arte9.com/torneo/122201/", "datetime_start": "2026-11-21T05:00:00+01:00"}
        self.assertFalse(correct_start_time(store))

    def test_idempotent(self):
        e = bandai("2026-11-21T05:00:00+01:00")
        self.assertEqual(apply_time_fixes([e]), 1)
        self.assertEqual(apply_time_fixes([e]), 0)
        self.assertEqual(e["datetime_start"], "2026-11-21T17:00:00+01:00")
        self.assertEqual(e["datetime_start_source"], "2026-11-21T05:00:00+01:00")

    def test_daylight_saving_day_gets_the_evening_offset(self):
        # 29 March 2026: Madrid moves from +01:00 to +02:00 at 02:00.
        e = bandai("2026-03-29T01:30:00+01:00")
        self.assertTrue(correct_start_time(e))
        self.assertEqual(e["datetime_start"], "2026-03-29T13:30:00+02:00")

    def test_end_before_the_fixed_start_moves_too(self):
        e = bandai("2026-11-21T05:00:00+01:00", datetime_end="2026-11-21T09:00:00+01:00")
        correct_start_time(e)
        self.assertEqual(e["datetime_end"], "2026-11-21T21:00:00+01:00")
        later = bandai("2026-11-21T05:00:00+01:00", datetime_end="2026-11-21T21:00:00+01:00")
        correct_start_time(later)
        self.assertEqual(later["datetime_end"], "2026-11-21T21:00:00+01:00")

    def test_published_and_wired(self):
        self.assertIn("time_corrected", EVENT_FIELDS)
        self.assertIn("datetime_start_source", EVENT_FIELDS)
        main = pathlib.Path("aggregator.py").read_text(encoding="utf-8")
        main = main[main.index("def main()"):]
        self.assertIn('apply_time_fixes(existing', main)
        self.assertIn('apply_time_fixes(fresh', main)
        # Fresh events are fixed before the locator/store dedup compares times.
        self.assertLess(main.index('apply_time_fixes(fresh'), main.index('drop_locator_duplicates('))


if __name__ == "__main__":
    unittest.main()
