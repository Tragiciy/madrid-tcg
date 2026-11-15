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
        ]
        actions = plan_actions(_stats(), open_issues)
        ops = {(a["op"], a.get("title") or a["number"]) for a in actions}
        self.assertEqual(ops, {
            ("create", "[health] Alpha"),
            ("create", "[health] Eps"),
            ("create", "[health] Zeta"),
            ("update", 10),
            ("close", 11),
        })

    def test_error_text_is_in_body(self):
        create = next(a for a in plan_actions(_stats(), []) if a["op"] == "create" and "Alpha" in a["title"])
        self.assertIn("HTTP 404", create["body"])


if __name__ == "__main__":
    unittest.main()
