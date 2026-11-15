import json
import pathlib
import tempfile
import unittest
from unittest import mock

import aggregator
from aggregator import (
    CorruptEventsFile,
    build_stats,
    find_suspect_zero_stores,
    load_existing,
    merge_events,
    write_json_atomic,
)

NOW = "2026-11-15T12:00:00+01:00"


def _event(store, title, start, **extra):
    e = {
        "title": title,
        "store": store,
        "datetime_start": start,
        "source_url": f"https://example.com/{store}/{title}",
        "is_active": True,
    }
    e.update(extra)
    return e


def _scraper_stats(store, valid, error=None):
    return {
        "store": store,
        "raw": valid,
        "valid": valid,
        "dropped": 0,
        "drop_reasons": {"missing_fields": 0, "bad_datetime": 0},
        "error": error,
    }


class SuspectZeroTests(unittest.TestCase):
    def setUp(self):
        self.existing = [
            _event("Alpha", "Modern Weekly", "2026-11-20T19:00:00+01:00"),
            _event("Beta", "Old Event", "2026-11-01T19:00:00+01:00"),
        ]

    def test_zero_events_with_upcoming_history_is_suspect(self):
        stats = {
            "scrapers.alpha": _scraper_stats("Alpha", 0),
            "scrapers.beta": _scraper_stats("Beta", 0),
        }
        # Beta only has past events, so a zero there is plausible.
        self.assertEqual(
            find_suspect_zero_stores(stats, self.existing, NOW), {"Alpha"}
        )

    def test_failed_scraper_is_not_double_counted(self):
        stats = {"scrapers.alpha": _scraper_stats("Alpha", 0, error="boom")}
        self.assertEqual(find_suspect_zero_stores(stats, self.existing, NOW), set())

    def test_suspect_zero_store_keeps_upcoming_events_across_runs(self):
        events = self.existing
        for _ in range(5):
            events = merge_events(events, [], NOW, failed_stores={"Alpha"})
        alpha = [e for e in events if e["store"] == "Alpha"]
        self.assertEqual(len(alpha), 1)
        self.assertTrue(alpha[0]["is_active"])

    def test_without_protection_upcoming_events_are_deleted(self):
        events = self.existing
        for _ in range(3):
            events = merge_events(events, [], NOW)
        self.assertFalse([e for e in events if e["store"] == "Alpha"])

    def test_stats_flag_suspect_zero(self):
        stats = build_stats(
            self.existing,
            {"scrapers.alpha": _scraper_stats("Alpha", 0)},
            [],
            NOW,
            suspect_zero_stores={"Alpha"},
        )
        self.assertEqual(stats["by_store"]["Alpha"]["anomaly"], "suspect_zero")
        self.assertEqual(stats["scrapers"]["scrapers.alpha"]["anomaly"], "suspect_zero")


class StaleStoreTests(unittest.TestCase):
    def _stats(self, last_seen):
        events = [_event("Gone", "Weekly", "2026-09-01T19:00:00+02:00",
                         is_active=False, last_seen_at=last_seen)]
        return build_stats(events, {"scrapers.gone": _scraper_stats("Gone", 0)}, [], NOW)

    def test_long_silent_store_is_stale(self):
        row = self._stats("2026-10-01T07:00:00+02:00")["by_store"]["Gone"]
        self.assertEqual(row["anomaly"], "stale")
        self.assertEqual(row["last_seen_at"], "2026-10-01T07:00:00+02:00")

    def test_recently_seen_store_is_not_stale(self):
        row = self._stats("2026-11-10T07:00:00+01:00")["by_store"]["Gone"]
        self.assertIsNone(row["anomaly"])


class PersistenceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "events.json"
        patcher = mock.patch.object(aggregator, "EVENTS_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_corrupt_file_raises_instead_of_starting_empty(self):
        self.path.write_text("[{not json", encoding="utf-8")
        with self.assertRaises(CorruptEventsFile):
            load_existing()

    def test_non_list_file_raises(self):
        self.path.write_text('{"events": []}', encoding="utf-8")
        with self.assertRaises(CorruptEventsFile):
            load_existing()

    def test_missing_file_is_empty(self):
        self.assertEqual(load_existing(), [])

    def test_atomic_write_replaces_and_leaves_no_temp_file(self):
        self.path.write_text("[]", encoding="utf-8")
        write_json_atomic(self.path, [{"a": 1}])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"a": 1}])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class RunScraperTests(unittest.TestCase):
    def test_import_error_is_reported_not_raised(self):
        events, error, store = aggregator._run_scraper("scrapers.does_not_exist")
        self.assertEqual(events, [])
        self.assertIn("ModuleNotFoundError", error)
        self.assertIsNone(store)


if __name__ == "__main__":
    unittest.main()
