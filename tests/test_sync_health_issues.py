import unittest

from tools.sync_health_issues import plan_actions, unhealthy_stores


def _stats():
    def sc(store, raw, error=None):
        return {"store": store, "raw": raw, "valid": raw, "error": error}

    return {
        "generated_at": "2026-11-16T07:00:00+01:00",
        "scrapers": {
            "scrapers.alpha": sc("Alpha", 0, error="RuntimeError: HTTP 404"),
            "scrapers.beta": sc("Beta", 0),
            "scrapers.gamma": sc("Gamma", 12),
            "scrapers.delta": sc("Delta", 0),
            "scrapers.eps": sc("Eps", 0),
            "scrapers.zeta": sc("Zeta", 0),  # no by_store row at all
        },
        "by_store": {
            "Alpha": {"active": 4, "raw_this_run": 0, "anomaly": None},
            "Beta": {"active": 7, "raw_this_run": 0, "anomaly": "suspect_zero"},
            "Gamma": {"active": 12, "raw_this_run": 12, "anomaly": None},
            # Delta: 0 events and nothing upcoming on record -> plausible, not broken.
            "Delta": {"active": 0, "raw_this_run": 0, "anomaly": None},
            "Eps": {"active": 0, "raw_this_run": 0, "anomaly": "stale"},
        },
    }


class SyncHealthIssuesTests(unittest.TestCase):
    def test_failed_and_suspect_zero_stores_are_unhealthy(self):
        broken = unhealthy_stores(_stats())
        self.assertEqual(set(broken), {"Alpha", "Beta", "Eps", "Zeta"})
        self.assertEqual(broken["Zeta"]["reason"], "never_seen")
        self.assertEqual(broken["Eps"]["reason"], "stale")
        self.assertEqual(broken["Alpha"]["reason"], "scraper_failed")
        self.assertEqual(broken["Beta"]["reason"], "suspect_zero")

    def test_creates_missing_updates_open_and_closes_recovered(self):
        open_issues = [
            {"number": 10, "title": "[health] Beta"},
            {"number": 11, "title": "[health] Gamma"},
            {"number": 12, "title": "[health] Removed Store"},
            {"number": 13, "title": "Unrelated issue"},
        ]
        actions = plan_actions(_stats(), open_issues)
        ops = {(a["op"], a.get("title") or a["number"]) for a in actions}
        self.assertEqual(ops, {
            ("create", "[health] Alpha"),
            ("create", "[health] Eps"),
            ("create", "[health] Zeta"),
            ("update", 10),
            ("close", 11),
            ("close", 12),
        })

    def test_locator_suspect_zero_lives_on_scraper_entry(self):
        stats = _stats()
        stats["scrapers"]["scrapers.locator_x"] = {
            "store": "X Locator", "raw": 0, "valid": 0, "error": None, "anomaly": "suspect_zero",
        }
        self.assertEqual(unhealthy_stores(stats)["X Locator"]["reason"], "suspect_zero")

    def test_closed_issue_is_reopened_not_duplicated(self):
        issues = [{"number": 5, "title": "[health] Alpha", "state": "CLOSED"}]
        ops = {(a["op"], a.get("number")) for a in plan_actions(_stats(), issues)}
        self.assertIn(("reopen", 5), ops)
        self.assertNotIn(("create", None), {(a["op"], None) for a in plan_actions(_stats(), issues)
                                            if a.get("title") == "[health] Alpha"})

    def test_failed_pipeline_only_touches_pipeline_issue(self):
        issues = [{"number": 10, "title": "[health] Beta", "state": "OPEN"}]
        actions = plan_actions({}, issues, pipeline_status="failure", run_url="u")
        self.assertEqual([(a["op"], a.get("title")) for a in actions], [("create", "[health] pipeline")])

    def test_successful_pipeline_closes_pipeline_issue(self):
        issues = [{"number": 9, "title": "[health] pipeline", "state": "OPEN"}]
        self.assertIn(("close", 9), {(a["op"], a.get("number")) for a in plan_actions(_stats(), issues)})

    def test_gh_failure_is_reported_and_others_continue(self):
        import subprocess
        from tools.sync_health_issues import apply_actions
        calls = []

        def gh(args):
            calls.append(args[1])
            if args[1] == "create":
                raise subprocess.CalledProcessError(1, "gh", stderr="boom")
            return ""
        failures = apply_actions([{"op": "create", "title": "t", "body": "b"},
                                  {"op": "close", "number": 1, "comment": "c"}], gh)
        self.assertEqual((failures, calls), (1, ["create", "close"]))

    def test_error_text_is_in_body(self):
        create = next(a for a in plan_actions(_stats(), []) if a["op"] == "create" and "Alpha" in a["title"])
        self.assertIn("HTTP 404", create["body"])


if __name__ == "__main__":
    unittest.main()
