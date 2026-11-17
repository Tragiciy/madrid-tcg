import copy
import json
import unittest
from pathlib import Path

from scrapers import locator_pokemon
from shared.locator_common import StoreResolver
from shared.store_registry import StoreRegistry

# Trimmed real DataActionGetEventList response (Madrid, 2026-11-15..28):
# an Arte 9 League Cup (tournament), MADAKIBA / TopDeck / Alcalá league
# nights (play sessions), a VG-only tournament and a GO-only session.
FIXTURE = Path(__file__).parent / "fixtures" / "pokemon_event_list.json"
ARTE9_VENUE = "32b3dd36-918e-46a0-b776-5d2f21cdc680"


def _items():
    return json.loads(FIXTURE.read_text())["data"]["EventList"]["List"]


def _resolver():
    return StoreResolver(StoreRegistry({"ignore": {"names": [], "external": []}, "stores": [
        {"id": "arte-9", "name": "Arte 9", "city": "madrid",
         "address": "Calle de Francisco Silvela, 21, 28028 Madrid",
         "aliases": {"names": [], "external": []}},
    ]}))


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.events = locator_pokemon.parse_events(_items(), _resolver())
        self.by_store = {e["store"]: e for e in self.events}

    def test_only_tcg_products(self):
        # VG-only Padis tournament and GO-only Ego Ludo session are dropped;
        # the TopDeck session lists tcg+vg+pgo and stays.
        self.assertEqual(sorted(self.by_store), ["ALCALÁ CÓMICS", "Arte 9", "MADAKIBA", "TOPDECK"])

    def test_tournament_start_is_venue_wall_clock(self):
        # "2026-11-15T10:30:00Z" is shown as 10:30 by the page; arte9.com agrees.
        self.assertEqual(self.by_store["Arte 9"]["datetime_start"], "2026-11-15T10:30:00+01:00")

    def test_play_session_start_is_utc(self):
        # "2026-11-22T09:30:00Z" → 10:30 Madrid; MADAKIBA's own calendar agrees.
        self.assertEqual(self.by_store["MADAKIBA"]["datetime_start"], "2026-11-22T10:30:00+01:00")
        self.assertEqual(self.by_store["ALCALÁ CÓMICS"]["datetime_start"], "2026-11-16T18:30:00+01:00")

    def test_store_hint_and_resolution(self):
        e = self.by_store["Arte 9"]
        self.assertEqual(e["store_hint"], {
            "external": f"pokemon:{ARTE9_VENUE}", "name": "ARTE 9",
            "address": "C/ DE FRANCISCO SILVELA, 21, SALAMANCA, 28028 MADRID, SPAIN",
            "lat": e["store_hint"]["lat"], "lng": e["store_hint"]["lng"],
        })
        self.assertAlmostEqual(e["store_hint"]["lat"], 40.43, places=1)
        self.assertAlmostEqual(e["store_hint"]["lng"], -3.67, places=1)
        self.assertEqual(e["store_address"], e["store_hint"]["address"])

    def test_schema(self):
        cup, league = self.by_store["Arte 9"], self.by_store["MADAKIBA"]
        for e in self.events:
            self.assertEqual(e["game"], "Pokémon")
            self.assertTrue(e["source_event_id"].startswith("pokemon:"))
            self.assertEqual(e["source_url"], locator_pokemon.LOCATOR_PAGE)
            for key in ("title", "store", "datetime_start", "format", "language", "scraped_at"):
                self.assertTrue(e[key], key)
        self.assertEqual(cup["source_event_id"], "pokemon:c9a60640-8a29-4007-8ff7-beed8f52f005")
        self.assertEqual(cup["title"], "CUP ARTE 9 - NOVIEMBRE 26")
        self.assertEqual((cup["format"], cup["format_official"]), ("Standard", "League Cup"))
        self.assertEqual(league["title"], "Liga Pokémon")  # unnamed play session
        self.assertEqual((league["format"], league["format_official"]), ("Standard", "Liga Pokémon"))
        self.assertNotIn("capacity", cup)

    def test_far_and_venueless_events_dropped(self):
        items = _items()
        far, online = copy.deepcopy(items[0]), copy.deepcopy(items[1])
        far["Events"].update(Guid="far")
        far["Events"]["Address"].update(Latitude="41.3874", Longitude="2.1686")  # Barcelona
        online["Events"].update(Guid="online")
        online["Events"]["Address"].update(Name="", Latitude="", Longitude="")
        self.assertEqual(locator_pokemon.parse_events([far, online], _resolver()), [])


class _Resp:
    def __init__(self, text, ctype, status=200, url="u"):
        self.text, self.status_code, self.url = text, status, url
        self.headers = {"content-type": ctype}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(r for prefix, r in self.routes if url.startswith(prefix))


class DiscoveryTests(unittest.TestCase):
    def _routes(self):
        p = locator_pokemon
        manifest = {"manifest": {"versionToken": "MODV", "urlVersions": {
            p.RUNTIME_SCRIPT: "?rt", p.HOME_SCRIPT: "?home"}}}
        return [
            (p.MODULEINFO_URL, _Resp(json.dumps(manifest), "application/json")),
            (p.HOST + p.RUNTIME_SCRIPT + "?rt", _Resp(
                'e.CSRFHeader="X-CSRFToken",e.AnonymousCSRFToken="T0k+n=",e.getCSRFToken=n',
                "application/javascript")),
            (p.HOST + p.HOME_SCRIPT + "?home", _Resp(
                'callDataAction("DataActionGetEventList", '
                '"screenservices/EventLocator/MainFlow/Home/DataActionGetEventList", "APIV", function (b) {',
                "application/javascript")),
            (p.ACTION_URL, _Resp(FIXTURE.read_text(), "application/json")),
        ]

    def test_versions_and_token_come_from_page_assets(self):
        sess = _Session(self._routes())
        self.assertEqual(locator_pokemon.discover(sess),
                         {"moduleVersion": "MODV", "apiVersion": "APIV", "csrf": "T0k+n="})
        items = locator_pokemon._fetch_events(sess)
        self.assertEqual(len(items), 6)
        method, _, kwargs = sess.calls[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(kwargs["headers"]["X-CSRFToken"], "T0k+n=")
        self.assertEqual(kwargs["json"]["versionInfo"], {"moduleVersion": "MODV", "apiVersion": "APIV"})

    def test_imperva_interstitial_raises(self):
        page = "<html><head><title>Pardon Our Interruption</title></head></html>"
        sess = _Session([("https://", _Resp(page, "text/html; charset=UTF-8"))])
        with self.assertRaises(locator_pokemon.BlockedError):
            locator_pokemon.discover(sess)


if __name__ == "__main__":
    unittest.main()
