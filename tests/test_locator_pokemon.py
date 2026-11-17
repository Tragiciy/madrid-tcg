import copy
import json
import unittest
from pathlib import Path

from unittest import mock

from scrapers import locator_pokemon
from shared import cities
from shared.cities import CITIES, City
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
            "city": "madrid",  # the city whose search found the venue
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


def _at(item, guid, lat, lng, name="SHOP"):
    """A copy of a fixture item at another venue."""
    x = copy.deepcopy(item)
    x["Events"]["Guid"] = guid
    x["Events"]["Address"].update(Guid=f"venue-{guid}", Name=name, Latitude=str(lat), Longitude=str(lng))
    return x


class SearchAreaTests(unittest.TestCase):
    def test_every_city_in_exactly_one_area_within_budget(self):
        areas = locator_pokemon.search_areas()
        ids = [c.id for a in areas for c in a.cities]
        self.assertEqual(sorted(ids), sorted(c.id for c in CITIES))
        self.assertEqual([a.label for a in areas],
                         ["madrid", "barcelona+valencia", "sevilla+malaga", "bilbao+zaragoza"])
        self.assertLessEqual(locator_pokemon.DISCOVERY_CALLS + len(areas), locator_pokemon.CALL_BUDGET)
        self.assertLessEqual(max(a.radius_km for a in areas), locator_pokemon.MAX_RANGE_KM)

    def test_area_encloses_its_cities(self):
        for area in locator_pokemon.search_areas():
            if len(area.cities) == 1:
                continue  # searched alone: the city's own circle, no margin
            for c in area.cities:
                reach = cities.distance_km(area.lat, area.lng, c.lat, c.lng) + c.radius_km
                self.assertLessEqual(reach + locator_pokemon.AREA_MARGIN_KM, area.radius_km, area.label)

    def test_a_city_searched_alone_sends_the_one_city_request(self):
        # Madrid's request is byte-for-byte what it was before areas existed.
        mad = cities.get("madrid")
        app = {"moduleVersion": "M", "apiVersion": "A", "csrf": "t"}
        body = locator_pokemon._body(app, *locator_pokemon.window(), locator_pokemon.search_areas()[0])
        self.assertEqual(body["screenData"]["variables"]["latitude"], "40.4168")
        self.assertEqual(body["screenData"]["variables"]["range"], "50")
        self.assertEqual((body["clientVariables"]["Range"], body["clientVariables"]["LocationName"]),
                         (50, "Madrid, Spain"))
        self.assertEqual(locator_pokemon.SearchArea.of((mad,)).radius_km, mad.radius_km)

    def test_split_by_distance_to_each_city(self):
        area = locator_pokemon.SearchArea.of((cities.get("barcelona"), cities.get("valencia")))
        base = _items()[0]
        items = [_at(base, "bcn", 41.40, 2.17), _at(base, "vlc", 39.47, -0.37),
                 _at(base, "tarragona", 41.12, 1.25),    # in the area, in no city
                 _at(base, "nopin", "", "")]
        got = {k: [i["Events"]["Guid"] for i in v] for k, v in area.split(items).items()}
        self.assertEqual(got, {"barcelona": ["bcn"], "valencia": ["vlc"]})

    def test_ungrouped_new_city_gets_its_own_area(self):
        lisboa = City(id="lisboa", name="Lisboa", timezone="Europe/Madrid", lat=38.72, lng=-9.14,
                      radius_km=30, country="ES", search_terms=("Lisboa",))
        areas = locator_pokemon.search_areas(CITIES + (lisboa,))
        self.assertEqual(areas[-1].label, "lisboa")


class ScrapeBudgetTests(unittest.TestCase):
    """scrape() end to end on a fake server: discovery, one POST per area."""

    def _server(self, post):
        routes = DiscoveryTests()._routes()[:3]
        sess = _Session(routes + [(locator_pokemon.ACTION_URL, None)])
        real = sess.request

        def request(method, url, **kw):
            if url == locator_pokemon.ACTION_URL:
                sess.calls.append((method, url, kw))
                return _Resp(json.dumps({"data": {"EventList": {"List": post(kw["json"])}}}),
                             "application/json")
            return real(method, url, **kw)

        sess.request = request
        return sess

    def _scrape(self, sess):
        with mock.patch.object(locator_pokemon, "session", return_value=sess), \
             mock.patch.object(locator_pokemon, "StoreResolver", return_value=_resolver()):
            return locator_pokemon.scrape()

    def test_seven_calls_and_each_city_gets_its_venues(self):
        base = _items()[0]
        by_lat = {"40.4168": _items(),  # Madrid fixture
                  "40.4287": [_at(base, "bcn", 41.40, 2.17, "BCN SHOP"),
                              _at(base, "vlc", 39.47, -0.37, "VLC SHOP"),
                              _at(base, "tgn", 41.12, 1.25, "TARRAGONA")],
                  "37.0552": [_at(base, "mlg", 36.72, -4.42, "MLG SHOP")],
                  "42.4559": []}
        sess = self._server(lambda body: by_lat[body["screenData"]["variables"]["latitude"]])
        events = self._scrape(sess)
        self.assertEqual(len(sess.calls), 7)
        hints = {e["store_hint"]["name"]: e["store_hint"]["city"] for e in events}
        self.assertEqual({k: v for k, v in hints.items() if v != "madrid"},
                         {"BCN SHOP": "barcelona", "VLC SHOP": "valencia", "MLG SHOP": "malaga"})
        self.assertEqual(sum(1 for v in hints.values() if v == "madrid"), 4)  # fixture's TCG venues

    def test_capped_response_fails_loudly_within_budget(self):
        base = _items()[0]
        capped = [_at(base, f"g{i}", 40.42, -3.70) for i in range(locator_pokemon.MAX_RECORDS)]
        sess = self._server(lambda body: capped)  # every window stays capped
        with self.assertRaises(RuntimeError):
            self._scrape(sess)
        self.assertLessEqual(len(sess.calls), locator_pokemon.CALL_BUDGET)

    def test_plan_over_budget_fails_before_any_call(self):
        sess = self._server(lambda body: [])
        ungrouped = locator_pokemon.search_areas(CITIES, groups=())  # 3 + 7 calls > 7
        with mock.patch.object(locator_pokemon, "search_areas", lambda: ungrouped):
            with self.assertRaises(locator_pokemon.BudgetExceeded):
                self._scrape(sess)
        self.assertEqual(sess.calls, [])

    def test_too_wide_area_fails_before_any_call(self):
        sess = self._server(lambda body: [])
        wide = locator_pokemon.search_areas(CITIES, groups=(("madrid", "barcelona"),))
        with mock.patch.object(locator_pokemon, "search_areas", lambda: wide):
            with self.assertRaisesRegex(RuntimeError, "wider than"):
                self._scrape(sess)
        self.assertEqual(sess.calls, [])

    def test_call_budget_refuses_the_extra_call(self):
        sess = _Session([("https://", _Resp("{}", "application/json"))])
        budget = locator_pokemon.CallBudget(sess, limit=2)
        budget.request("GET", "https://a")
        budget.request("GET", "https://b")
        with self.assertRaises(locator_pokemon.BudgetExceeded):
            budget.request("GET", "https://c")
        self.assertEqual(len(sess.calls), 2)


if __name__ == "__main__":
    unittest.main()
