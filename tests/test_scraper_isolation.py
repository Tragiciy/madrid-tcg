import unittest
from unittest import mock

import pipeline.runner as runner

class IsolatedRunnerTests(unittest.TestCase):
    def test_successful_scraper_round_trips_events(self):
        events, error, store = runner._run_scraper_isolated("tests.fixtures.ok_scraper")
        self.assertIsNone(error)
        self.assertEqual(store, "Ok Store")
        self.assertEqual(events[0]["title"], "Weekly")

    def test_timeout_becomes_error(self):
        events, error, _ = runner._run_scraper_isolated(
            "tests.fixtures.slow_scraper", timeout_s=2,
        )
        self.assertEqual(events, [])
        self.assertIn("TimeoutError", error)

    def test_hard_crash_becomes_error(self):
        events, error, _ = runner._run_scraper_isolated("tests.fixtures.crashing_scraper")
        self.assertEqual(events, [])
        self.assertIn("ChildProcessError", error)

    def test_store_read_from_source_without_import(self):
        self.assertEqual(runner._store_from_source("scrapers.arte9"), "Arte 9")
        self.assertIsNone(runner._store_from_source("scrapers.does_not_exist"))

    def test_run_all_preserves_order(self):
        with mock.patch.object(
            runner, "_run_scraper_isolated",
            side_effect=lambda m: ([], None, m),
        ):
            results = runner.run_all_scrapers(["a", "b", "c"])
        self.assertEqual([r[2] for r in results], ["a", "b", "c"])

if __name__ == "__main__":
    unittest.main()
