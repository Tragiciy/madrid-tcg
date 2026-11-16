import json
import pathlib
import tempfile
import unittest

from pipeline.publish import build_api, iso_week, week_bounds, write_api
from pipeline.reconcile import event_key
from shared.store_registry import StoreRegistry

NOW = "2026-11-18T12:00:00+01:00"  # Wednesday of 2026-W47


def _e(start, active=True, **kw):
    e = {"title": "T", "store": "Arte 9", "store_id": "arte-9", "game": "Lorcana",
         "datetime_start": start, "source_url": "u", "is_active": active,
         "first_seen_at": "x", "last_seen_at": "y", "scraped_at": "z"}
    e.update(kw)
    return e


REG = StoreRegistry({"stores": [
    {"id": "arte-9", "name": "Arte 9", "city": "madrid", "address": "A", "lat": 1.0, "lng": 2.0,
     "origin": "curated", "aliases": {"names": [], "external": ["wizards:1"]}}]})


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            _e("2026-11-01T18:00:00+01:00", active=False),           # past, 2026-W44 — history
            _e("2026-11-15T23:30:00+01:00"),                         # Sunday night → W46
            _e("2026-11-20T19:00:00+01:00", source_event_id="wizards:9"),
            _e("2026-11-21T19:00:00+01:00", active=False),           # future but inactive → hidden
            _e("2027-01-30T19:00:00+01:00"),                         # beyond upcoming window
        ]
        self.docs = build_api(self.events, REG, NOW)

    def test_weeks_use_madrid_dates_and_skip_inactive_future(self):
        weeks = self.docs["madrid/index.json"]["weeks"]
        self.assertEqual(weeks, {"2026-W44": 1, "2026-W46": 1, "2026-W47": 1, "2027-W04": 1})
        self.assertEqual(week_bounds("2026-W47")[0].isoformat(), "2026-11-16")

    def test_upcoming_covers_previous_week_to_four_ahead(self):
        up = self.docs["madrid/events/upcoming.json"]
        self.assertEqual((up["from"], up["to"]), ("2026-11-09", "2026-12-21"))
        self.assertEqual([e["datetime_start"][:10] for e in up["events"]], ["2026-11-15", "2026-11-20"])

    def test_public_events_are_slim_and_keyed_like_the_pipeline(self):
        [e] = self.docs["madrid/events/2026-W47.json"]["events"]
        self.assertEqual(e["id"], event_key(self.events[2]))
        self.assertEqual(e["id"], "id:wizards:9")
        for private in ("is_active", "first_seen_at", "last_seen_at", "scraped_at"):
            self.assertNotIn(private, e)

    def test_stores_and_cities(self):
        [s] = self.docs["madrid/stores.json"]["stores"]
        self.assertEqual(s, {"id": "arte-9", "name": "Arte 9", "address": "A", "lat": 1.0, "lng": 2.0})
        self.assertEqual(self.docs["cities.json"]["cities"][0]["id"], "madrid")

    def test_write_is_compact_and_removes_stale_weeks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            stale = root / "madrid/events/2020-W01.json"
            stale.parent.mkdir(parents=True)
            stale.write_text("{}")
            write_api(self.docs, root)
            self.assertFalse(stale.exists())
            text = (root / "madrid/events/upcoming.json").read_text(encoding="utf-8")
            self.assertNotIn("\n", text)
            self.assertEqual(json.loads(text)["api_version"], 1)

    def test_iso_week_edges(self):
        from datetime import date
        self.assertEqual(iso_week(date(2027, 1, 1)), "2026-W53")
        self.assertEqual(iso_week(date(2026, 12, 28)), "2026-W53")


if __name__ == "__main__":
    unittest.main()
