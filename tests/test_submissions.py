import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from datetime import date

from pipeline.runner import flag_from_source
from scrapers import manual_submissions
from tools.apply_submission import apply, extract
from tools.sync_health_issues import unhealthy_stores

SUB = {"kind": "event", "store": "Arte 9", "title": "Liga Lorcana", "game": "Lorcana",
       "date": "2026-11-18", "time": "18:30", "weekly_until": "2026-12-09",
       "link": "javascript:alert(1)", "contact": "x@y.z", "notes": ""}
BODY = "**Event submission**\n\n```json\n" + json.dumps(SUB) + "\n```\n"


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "manual.json"
        self.path.write_text('{"events": []}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_is_idempotent_and_drops_unsafe_link_and_contact(self):
        self.assertTrue(apply(BODY, 42, self.path))
        self.assertFalse(apply(BODY, 42, self.path))
        [entry] = json.loads(self.path.read_text())["events"]
        self.assertEqual((entry["id"], entry["link"]), ("42", None))
        self.assertNotIn("contact", entry)          # private: never published

    def test_store_info_and_bad_data_are_rejected(self):
        with self.assertRaises(ValueError):
            apply(BODY.replace('"event"', '"info"'), 1, self.path)
        with self.assertRaises(ValueError):
            extract("no json here")
        with self.assertRaises(ValueError):
            apply(BODY.replace("2026-12-09", "2026-11-01"), 2, self.path)

    def test_weekly_entries_expand_inside_the_window(self):
        apply(BODY, 7, self.path)
        entries = json.loads(self.path.read_text())["events"]
        events = manual_submissions.parse(entries, date(2026, 11, 20), "now")
        self.assertEqual([e["datetime_start"][:10] for e in events], ["2026-11-25", "2026-12-02", "2026-12-09"])
        self.assertEqual(events[0]["datetime_start"], "2026-11-25T18:30:00+01:00")
        self.assertEqual(events[0]["source_event_id"], "manual:7:2026-11-25")


class HealthTests(unittest.TestCase):
    def test_empty_submissions_source_is_not_an_alert(self):
        self.assertTrue(flag_from_source("scrapers.manual_submissions", "EMPTY_IS_NORMAL"))
        self.assertFalse(flag_from_source("scrapers.arte9", "EMPTY_IS_NORMAL"))
        stats = {"scrapers": {"scrapers.manual_submissions": {"store": "Store submissions", "raw": 0, "empty_ok": True}},
                 "by_store": {}}
        self.assertEqual(unhealthy_stores(stats), {})


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FunctionValidationTests(unittest.TestCase):
    def test_validate(self):
        # Copy to .mjs so any Node version treats it as an ES module.
        tmp = tempfile.mkdtemp()
        mod = pathlib.Path(tmp) / "submit.mjs"
        shutil.copy(pathlib.Path(__file__).resolve().parents[1] / "functions/api/submit.js", mod)
        script = """
        import(process.argv[1]).then(({ validate }) => {
          const now = 1_000_000;
          const ok = { kind: 'event', store: 'S', contact: 'c', title: 'T', game: 'Lorcana',
                       date: '2026-11-18', time: '18:30', started_at: now - 10_000 };
          console.log(JSON.stringify([
            validate(ok, now).data ? 'ok' : 'fail',
            validate({ ...ok, company: 'bot' }, now).spam ? 'spam' : 'fail',
            validate({ ...ok, started_at: now - 100 }, now).spam ? 'spam' : 'fail',
            (validate({ ...ok, game: 'Chess', link: 'ftp://x' }, now).errors || []).join(','),
          ]));
        });"""
        out = subprocess.run(["node", "--input-type=module", "-e", script, mod.as_uri()],
                             capture_output=True, text=True)
        shutil.rmtree(tmp)
        self.assertEqual(json.loads(out.stdout), ["ok", "spam", "spam", "link,game"])


if __name__ == "__main__":
    unittest.main()
