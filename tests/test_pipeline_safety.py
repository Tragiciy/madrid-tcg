import json
import pathlib
import tempfile
import unittest
from unittest import mock

import pipeline.repository as repository
from pipeline.runner import _run_scraper
from pipeline.health import build_stats
from pipeline.normalize import _shape_drop_reason
from pipeline.reconcile import find_suspect_zero_sources, merge_events
from pipeline.repository import (
    CorruptEventsFile,
    append_source_runs,
    apply_store_renames,
    drop_retired_stores,
    load_existing,
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
            _event("Alpha", "Modern Weekly", "2026-11-20T19:00:00+01:00",
                   source="scrapers.alpha"),
            _event("Beta", "Old Event", "2026-11-01T19:00:00+01:00",
                   source="scrapers.beta"),
        ]

    def test_zero_events_with_upcoming_history_is_suspect(self):
        stats = {
            "scrapers.alpha": _scraper_stats("Alpha", 0),
            "scrapers.beta": _scraper_stats("Beta", 0),
        }
        # Beta only has past events, so a zero there is plausible.
        self.assertEqual(
            find_suspect_zero_sources(stats, self.existing, NOW), {"scrapers.alpha"}
        )

    def test_failed_scraper_is_not_double_counted(self):
        stats = {"scrapers.alpha": _scraper_stats("Alpha", 0, error="boom")}
        self.assertEqual(find_suspect_zero_sources(stats, self.existing, NOW), set())

    def test_suspect_zero_store_keeps_upcoming_events_across_runs(self):
        events = self.existing
        for _ in range(5):
            events = merge_events(events, [], NOW, protected_sources={"scrapers.alpha"})
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
            suspect_zero_sources={"scrapers.alpha"},
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

    def test_scraper_that_never_produced_is_stale_even_if_locator_covers_store(self):
        events = [_event("Gone", "W", "2026-11-20T19:00:00+01:00",
                         last_seen_at=NOW, source="scrapers.locator_w")]
        stats = build_stats(events, {"scrapers.gone": _scraper_stats("Gone", 0),
                                     "scrapers.locator_w": _scraper_stats("W Locator", 5)}, [], NOW)
        self.assertEqual(stats["by_store"]["Gone"]["anomaly"], "stale")

    def test_locator_only_store_is_never_stale(self):
        events = [_event("Locator Only", "W", "2026-09-01T19:00:00+02:00", is_active=False,
                         last_seen_at="2026-09-01T07:00:00+02:00", source="scrapers.locator_w")]
        stats = build_stats(events, {"scrapers.locator_w": _scraper_stats("W Locator", 5)}, [], NOW)
        self.assertIsNone(stats["by_store"]["Locator Only"]["anomaly"])

    def test_recently_seen_store_is_not_stale(self):
        row = self._stats("2026-11-10T07:00:00+01:00")["by_store"]["Gone"]
        self.assertIsNone(row["anomaly"])


class RescheduleTests(unittest.TestCase):
    def test_new_week_does_not_swallow_past_occurrence(self):
        past = _event("Alpha", "Modern Weekly", "2026-11-10T19:00:00+01:00", is_active=False)
        fresh = [_event("Alpha", "Modern Weekly", "2026-11-17T19:00:00+01:00")]
        merged = merge_events([past], fresh, NOW)
        self.assertEqual(sorted(e["datetime_start"] for e in merged),
                         ["2026-11-10T19:00:00+01:00", "2026-11-17T19:00:00+01:00"])

    def test_future_record_moved_is_a_reschedule(self):
        old = _event("Alpha", "Modern Weekly", "2026-11-17T19:00:00+01:00",
                     first_seen_at="2026-11-01T00:00:00+01:00")
        fresh = [_event("Alpha", "Modern Weekly", "2026-11-18T19:00:00+01:00")]
        [e] = merge_events([old], fresh, NOW)
        self.assertEqual(e["first_seen_at"], "2026-11-01T00:00:00+01:00")

    def test_record_still_listed_is_not_taken_as_reschedule(self):
        a = _event("Alpha", "Weekly", "2026-11-17T19:00:00+01:00", first_seen_at="2026-11-01T00:00:00+01:00")
        fresh = [_event("Alpha", "Weekly", "2026-11-24T19:00:00+01:00"),
                 _event("Alpha", "Weekly", "2026-11-17T19:00:00+01:00")]
        merged = {e["datetime_start"]: e for e in merge_events([a], fresh, NOW)}
        self.assertEqual(merged["2026-11-17T19:00:00+01:00"]["first_seen_at"], "2026-11-01T00:00:00+01:00")
        self.assertEqual(merged["2026-11-24T19:00:00+01:00"]["first_seen_at"], NOW)

    def test_naive_datetime_is_rejected_by_shape_check(self):
        e = _event("Alpha", "X", "2026-11-17T19:00:00")
        self.assertEqual(_shape_drop_reason(e), "bad_datetime")


class RetiredAndRunLogTests(unittest.TestCase):
    def test_retired_store_events_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "retired.json"
            path.write_text('{"_comment": "x", "Gone": "dup"}', encoding="utf-8")
            events = [_event("Gone", "A", NOW), _event("Kept", "B", NOW)]
            self.assertEqual([e["store"] for e in drop_retired_stores(events, path)], ["Kept"])

    def test_store_renames_apply_to_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "renames.json"
            path.write_text('{"_comment": "x", "Itaca (28015)": "Ítaca"}', encoding="utf-8")
            events = [_event("Itaca (28015)", "A", NOW), _event("Other", "B", NOW)]
            self.assertEqual([e["store"] for e in apply_store_renames(events, path)], ["Ítaca", "Other"])

    def test_source_runs_appends_one_line_per_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "runs.jsonl"
            stats = {"scrapers.b": _scraper_stats("B", 2), "scrapers.locator_w": _scraper_stats("W", 9)}
            append_source_runs(stats, NOW, path)
            append_source_runs(stats, NOW, path)
            rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[1]["kind"], "locator")


class PersistenceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "events.json"
        self.legacy = pathlib.Path(self.tmp.name) / "legacy_events.json"
        for name, value in (("EVENTS_FILE", self.path), ("LEGACY_EVENTS_FILE", self.legacy)):
            patcher = mock.patch.object(repository, name, value)
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

    def test_legacy_history_is_migrated_then_removed(self):
        self.legacy.write_text('[{"title": "A", "store": "S", "datetime_start": "%s"}]' % NOW,
                               encoding="utf-8")
        events = load_existing()
        self.assertEqual(len(events), 1)
        repository.write_events(events)
        self.assertTrue(self.path.exists())
        self.assertFalse(self.legacy.exists())

    def test_missing_file_is_empty(self):
        self.assertEqual(load_existing(), [])

    def test_atomic_write_replaces_and_leaves_no_temp_file(self):
        self.path.write_text("[]", encoding="utf-8")
        write_json_atomic(self.path, [{"a": 1}])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [{"a": 1}])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class RunScraperTests(unittest.TestCase):
    def test_import_error_is_reported_not_raised(self):
        events, error, store = _run_scraper("scrapers.does_not_exist")
        self.assertEqual(events, [])
        self.assertIn("ModuleNotFoundError", error)
        self.assertIsNone(store)


if __name__ == "__main__":
    unittest.main()
