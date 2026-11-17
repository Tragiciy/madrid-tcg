import unittest
from unittest import mock

import pipeline.runner as runner

class IsolatedRunnerTests(unittest.TestCase):
    def test_successful_scraper_round_trips_events(self):
        events, error, store, city_errors = runner._run_scraper_isolated("tests.fixtures.ok_scraper")
        self.assertEqual(city_errors, {})
        self.assertIsNone(error)
        self.assertEqual(store, "Ok Store")
        self.assertEqual(events[0]["title"], "Weekly")

    def test_timeout_becomes_error(self):
        events, error, *_ = runner._run_scraper_isolated(
            "tests.fixtures.slow_scraper", timeout_s=2,
        )
        self.assertEqual(events, [])
        self.assertIn("TimeoutError", error)

    def test_hard_crash_becomes_error(self):
        events, error, *_ = runner._run_scraper_isolated("tests.fixtures.crashing_scraper")
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

    def test_failed_scraper_is_retried_once(self):
        calls = {}
        def run(m):
            calls[m] = calls.get(m, 0) + 1
            if m == "flaky" and calls[m] == 1:
                return [], "ReadTimeout", m
            if m == "broken":
                return [], f"Boom {calls[m]}", m
            return [{"title": m}], None, m
        sleeps = []
        with mock.patch.object(runner, "_run_scraper_isolated", side_effect=run):
            results = runner.run_all_scrapers(["ok", "flaky", "broken"], retry_delay_s=60, sleep=sleeps.append)
        self.assertEqual(sleeps, [60])
        self.assertEqual(calls, {"ok": 1, "flaky": 2, "broken": 2})
        self.assertEqual([(r[0], r[1]) for r in results],
                         [([{"title": "ok"}], None), ([{"title": "flaky"}], None), ([], "Boom 2")])

    def test_sources_flagged_no_runner_retry_are_not_retried(self):
        # Pokémon: a retry comes from the same IP and only spends calls Imperva
        # does not allow (scrapers/locator_pokemon.py NO_RUNNER_RETRY).
        self.assertTrue(runner.flag_from_source("scrapers.locator_pokemon", "NO_RUNNER_RETRY"))
        self.assertFalse(runner.flag_from_source("scrapers.locator_wizards", "NO_RUNNER_RETRY"))
        calls, sleeps = [], []

        def run(m):
            calls.append(m)
            return [], "BlockedError: Imperva", m
        with mock.patch.object(runner, "_run_scraper_isolated", side_effect=run):
            results = runner.run_all_scrapers(["scrapers.locator_pokemon", "scrapers.locator_wizards"],
                                              retry_delay_s=60, sleep=sleeps.append)
        self.assertEqual(sorted(calls), ["scrapers.locator_pokemon", "scrapers.locator_wizards",
                                         "scrapers.locator_wizards"])
        self.assertEqual(results[0][1], "BlockedError: Imperva")

    def test_city_errors_come_back_from_the_child_process(self):
        events, error, store, city_errors = runner._run_scraper_isolated("tests.fixtures.partial_locator")
        self.assertIsNone(error)
        self.assertEqual(sorted(city_errors), ["bilbao"])
        self.assertIn("TimeoutError", city_errors["bilbao"])
        self.assertNotIn("x:bilbao", [e["source_event_id"] for e in events])
        self.assertIn("x:madrid", [e["source_event_id"] for e in events])
        with mock.patch.object(runner, "_run_scraper_isolated", return_value=(events, None, store, city_errors)):
            [result] = runner.run_all_scrapers(["tests.fixtures.partial_locator"])
        self.assertEqual(result[4], city_errors)

    def test_no_retry_when_all_succeed_or_disabled(self):
        sleeps = []
        with mock.patch.object(runner, "_run_scraper_isolated", side_effect=lambda m: ([], None, m)):
            runner.run_all_scrapers(["a"], retry_delay_s=60, sleep=sleeps.append)
        with mock.patch.object(runner, "_run_scraper_isolated", side_effect=lambda m: ([], "x", m)):
            results = runner.run_all_scrapers(["a"], retry_delay_s=0, sleep=sleeps.append)
        self.assertEqual(sleeps, [])
        self.assertEqual(results[0][1], "x")

if __name__ == "__main__":
    unittest.main()
