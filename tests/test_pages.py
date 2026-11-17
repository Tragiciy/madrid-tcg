import json
import pathlib
import re
import tempfile
import unittest

from bs4 import BeautifulSoup

from pipeline.pages import build_pages, game_slug, write_pages
from shared.store_registry import StoreRegistry

NOW = "2026-11-18T12:00:00+01:00"  # Wednesday
REG = StoreRegistry({"stores": [
    {"id": "arte-9", "name": "Arte 9", "city": "madrid", "address": "C/ X 1, 28028 Madrid",
     "lat": 40.4, "lng": -3.6, "origin": "curated", "aliases": {"names": [], "external": []}},
    {"id": "quiet", "name": "Quiet Store", "city": "madrid", "origin": "auto",
     "aliases": {"names": [], "external": []}},
]})


def _e(start, title="Liga", game="Lorcana", **kw):
    e = {"title": title, "store": "Arte 9", "store_id": "arte-9", "game": game,
         "datetime_start": start, "source_url": "https://example.com/e", "is_active": True}
    e.update(kw)
    return e


EVENTS = [
    _e("2026-11-18T19:00:00+01:00", title="Liga <b>Lorcana</b> & más"),  # today
    _e("2026-11-21T11:00:00+01:00", game="One Piece"),                    # Saturday
    _e("2026-12-10T18:00:00+01:00"),                                      # 3 weeks ahead
    _e("2026-11-10T18:00:00+01:00", is_active=False),                     # past
]


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.pages = build_pages(EVENTS, REG, NOW)

    def test_expected_pages_exist(self):
        for path in ("madrid/index.html", "madrid/hoy/index.html", "madrid/fin-de-semana/index.html",
                     "madrid/lorcana/index.html", "madrid/one-piece/index.html",
                     "madrid/tiendas/arte-9/index.html", "madrid/tiendas/quiet/index.html",
                     "404.html", "sitemap.xml", "robots.txt"):
            self.assertIn(path, self.pages)

    def test_windows(self):
        text = lambda p: BeautifulSoup(self.pages[p], "html.parser").get_text()
        self.assertIn("Liga <b>Lorcana</b> & más", text("madrid/hoy/index.html"))  # escaped, shown as text
        self.assertNotIn("<b>Lorcana</b>", self.pages["madrid/hoy/index.html"].split("ld+json")[0])
        self.assertIn("One Piece", text("madrid/fin-de-semana/index.html"))
        self.assertNotIn("Liga <b>", text("madrid/fin-de-semana/index.html"))      # Wednesday is not weekend
        self.assertEqual(text("madrid/lorcana/index.html").count("Liga"), 2)        # both Lorcana events within 28 days

    def test_jsonld_is_valid_events(self):
        html = self.pages["madrid/index.html"]
        raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
        items = json.loads(raw)
        self.assertEqual({i["@type"] for i in items}, {"Event"})
        self.assertTrue(all(i["name"] and i["startDate"] and i["location"]["name"] for i in items))
        self.assertEqual(items[0]["location"]["geo"]["latitude"], 40.4)

    def test_quiet_store_page_kept_with_empty_note(self):
        self.assertIn("no tiene torneos programados", self.pages["madrid/tiendas/quiet/index.html"])

    def test_sitemap_and_canonical(self):
        self.assertIn("<loc>https://madrid-tcg.pages.dev/madrid/lorcana/</loc>", self.pages["sitemap.xml"])
        self.assertIn('rel="canonical" href="https://madrid-tcg.pages.dev/madrid/"', self.pages["madrid/index.html"])
        self.assertIn('content="noindex"', self.pages["404.html"])

    def test_write_skips_unchanged_and_removes_stale_city_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            stale = root / "madrid/tiendas/closed-store/index.html"
            stale.parent.mkdir(parents=True)
            stale.write_text("old")
            other = root / "index.html"
            other.write_text("app")
            self.assertEqual(write_pages(self.pages, root), len(self.pages))
            self.assertFalse(stale.exists())
            self.assertTrue(other.exists())
            self.assertEqual(write_pages(self.pages, root), 0)

    def test_only_http_links_are_rendered(self):
        pages = build_pages([_e("2026-11-18T19:00:00+01:00", source_url="javascript:alert(1)")], REG, NOW)
        self.assertNotIn("javascript:", pages["madrid/hoy/index.html"])

    def test_english_pages_mirror_spanish_with_hreflang(self):
        for path in ("en/madrid/index.html", "en/madrid/today/index.html", "en/madrid/weekend/index.html",
                     "en/madrid/lorcana/index.html", "en/madrid/stores/arte-9/index.html"):
            self.assertIn(path, self.pages)
        en = BeautifulSoup(self.pages["en/madrid/stores/arte-9/index.html"], "html.parser")
        es = BeautifulSoup(self.pages["madrid/tiendas/arte-9/index.html"], "html.parser")
        self.assertEqual(en.html["lang"], "en")
        self.assertEqual(es.html["lang"], "es")
        alt = lambda soup: {l["hreflang"]: l["href"] for l in soup.select("link[rel=alternate]")}
        expected = {"es": "https://madrid-tcg.pages.dev/madrid/tiendas/arte-9/",
                    "en": "https://madrid-tcg.pages.dev/en/madrid/stores/arte-9/",
                    "x-default": "https://madrid-tcg.pages.dev/madrid/tiendas/arte-9/"}
        self.assertEqual(alt(en), expected)
        self.assertEqual(alt(es), expected)
        self.assertIn("Upcoming card game tournaments at Arte 9", en.get_text())
        self.assertEqual(en.select_one("a.lang")["href"], "/madrid/tiendas/arte-9/")
        self.assertEqual(es.select_one("a.lang")["href"], "/en/madrid/stores/arte-9/")
        # internal links stay in the page's language
        self.assertTrue(all(a["href"].startswith("/en/madrid/") for a in en.select("li.ev span.store a, .tags a.tag")))
        self.assertIn("Wednesday 18 November", en.get_text())
        self.assertIn("<loc>https://madrid-tcg.pages.dev/en/madrid/weekend/</loc>", self.pages["sitemap.xml"])

    def test_game_slugs(self):
        self.assertEqual(game_slug("Magic: The Gathering"), "magic")
        self.assertEqual(game_slug("Some New Game!"), "some-new-game")


if __name__ == "__main__":
    unittest.main()
