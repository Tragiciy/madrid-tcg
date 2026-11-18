import pathlib
import unittest

from pipeline.publish import EVENT_FIELDS
from pipeline.time_fixes import apply_time_fixes, correct_start_time
from scrapers import locator_ravensburger
from shared.locator_common import StoreResolver, venue_wall_clock
from shared.store_registry import StoreRegistry


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


def rv(start, zone, country="ES", end=None, name="Thursday Night Weekly Play (Constructed)"):
    ev = {"id": 984072, "name": name, "start_datetime": start, "timezone": zone,
          "is_end_datetime_estimated": end is None, "event_status": "SCHEDULED",
          "store": {"id": 1, "name": "Kingdom Wargames", "country": country, "full_address": "12 Calle Arroyo de la Elipa, Madrid"}}
    if end:
        ev["end_datetime"] = end
    return ev


def parse(*raw):
    resolver = StoreResolver(StoreRegistry({"ignore": {"names": [], "external": []}, "stores": []}))
    return locator_ravensburger.parse_events(list(raw), resolver)


class RavensburgerVenueZoneTests(unittest.TestCase):
    """Spanish store, event in another zone: the typed time is Madrid time."""

    def test_new_york_zone_on_a_spanish_store(self):
        # Raw API, Kingdom Wargames: 02:00Z, America/New_York = Friday 21:00 there.
        [e] = parse(rv("2026-11-21T02:00:00+00:00", "America/New_York"))
        self.assertEqual(e["datetime_start"], "2026-11-20T21:00:00+01:00")
        self.assertEqual(e["datetime_start_source"], "2026-11-21T03:00:00+01:00")
        self.assertIs(e["time_corrected"], True)

    def test_port_of_spain_and_end_time(self):
        # Legadoxtudio: Sunday 15:00Z in Port of Spain (UTC-4) = 11:00 there.
        [e] = parse(rv("2026-11-22T15:00:00+00:00", "America/Port_of_Spain", end="2026-11-22T19:00:00+00:00"))
        self.assertEqual(e["datetime_start"], "2026-11-22T11:00:00+01:00")
        self.assertEqual(e["datetime_end"], "2026-11-22T15:00:00+01:00")
        self.assertEqual(e["datetime_start_source"], "2026-11-22T16:00:00+01:00")

    def test_madrid_and_same_clock_zones_are_untouched(self):
        for zone in ("Europe/Madrid", "Europe/Paris", None):
            [e] = parse(rv("2026-11-20T17:00:00+00:00", zone))
            self.assertEqual(e["datetime_start"], "2026-11-20T18:00:00+01:00", zone)
            self.assertNotIn("time_corrected", e)
            self.assertNotIn("datetime_start_source", e)

    def test_store_outside_spain_is_untouched(self):
        [e] = parse(rv("2026-11-21T02:00:00+00:00", "America/New_York", country="US"))
        self.assertEqual(e["datetime_start"], "2026-11-21T03:00:00+01:00")
        self.assertNotIn("time_corrected", e)

    def test_not_touched_again_by_the_bandai_rule(self):
        [e] = parse(rv("2026-11-21T02:00:00+00:00", "America/New_York"))
        self.assertFalse(correct_start_time(e))

    def test_wall_clock_helper(self):
        self.assertEqual(venue_wall_clock("2026-11-24T22:30:00Z", "America/New_York"), "2026-11-24T17:30:00+01:00")
        self.assertEqual(venue_wall_clock("2026-11-24T22:30:00Z", "Not/AZone"), "2026-11-24T23:30:00+01:00")
        # Summer: New York 21:00 EDT → Madrid 21:00 CEST.
        self.assertEqual(venue_wall_clock("2026-07-03T01:00:00Z", "America/New_York"), "2026-07-02T21:00:00+02:00")


if __name__ == "__main__":
    unittest.main()
