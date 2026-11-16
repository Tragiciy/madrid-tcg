import pathlib
import tempfile
import unittest

from bs4 import BeautifulSoup

from pipeline.publish import build_api
from pipeline.share import app_url, build_share_pages, share_id, write_share_pages
from shared.store_registry import StoreRegistry

NOW = "2026-11-18T12:00:00+01:00"


def _pe(start, title="Torneo <One> & Piece", game="One Piece", id_="id:bandai:1"):
    return {"id": id_, "title": title, "store": "Arte 9", "game": game,
            "datetime_start": start, "source_url": "https://x"}


class ShareTests(unittest.TestCase):
    def test_pages_only_inside_window_with_preview_tags(self):
        pages = build_share_pages([_pe("2026-11-21T17:00:00+01:00"),
                                   _pe("2027-03-01T17:00:00+01:00", id_="far"),
                                   _pe("2026-10-01T17:00:00+02:00", id_="old")], NOW)
        self.assertEqual(list(pages), [f"e/{share_id('id:bandai:1')}/index.html"])
        soup = BeautifulSoup(next(iter(pages.values())), "html.parser")
        og = {m["property"]: m["content"] for m in soup.select("meta[property^=og]")}
        self.assertEqual(og["og:title"], "Torneo <One> & Piece")          # text, escaped in HTML
        self.assertEqual(og["og:image"], "https://madrid-tcg.pages.dev/og/one-piece.jpg")
        self.assertIn("Sábado 21 de noviembre, 17:00", og["og:description"])
        self.assertIn("noindex", str(soup.select_one("meta[name=robots]")))

    def test_redirect_target_matches_app_encoding(self):
        # Same as app.js encodeEventParam: UTF-8 → base64url without padding.
        url = app_url(_pe("2026-11-21T17:00:00+01:00", id_="Arte 9|Liga ñ|2026-11-21T17:00:00+01:00|"))
        self.assertEqual(url, "/?event=QXJ0ZSA5fExpZ2Egw7F8MjAyNi0xMS0yMVQxNzowMDowMCswMTowMHw&w=2026-W47")

    def test_api_marks_shareable_events(self):
        events = [dict(_pe("2026-11-21T17:00:00+01:00"), is_active=True),
                  dict(_pe("2027-03-01T17:00:00+01:00", id_="far"), source_event_id="x:far", is_active=True)]
        up = build_api(events, StoreRegistry(), NOW)
        shares = {e["datetime_start"][:10]: e.get("share") for d in up.values() if "events" in d for e in d["events"]}
        self.assertTrue(shares["2026-11-21"].startswith("/e/"))
        self.assertIsNone(shares["2027-03-01"])

    def test_write_removes_expired_pages(self):
        pages = build_share_pages([_pe("2026-11-21T17:00:00+01:00")], NOW)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            old = root / "e/deadbeef00/index.html"
            old.parent.mkdir(parents=True)
            old.write_text("x")
            self.assertEqual(write_share_pages(pages, root), 1)
            self.assertFalse(old.exists())
            self.assertEqual(write_share_pages(pages, root), 0)


if __name__ == "__main__":
    unittest.main()
