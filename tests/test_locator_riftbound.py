import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from pipeline.reconcile import drop_locator_duplicates, event_key
from scrapers import locator_riftbound, nikochan_arena
from shared import cities
from shared.cities import get as city
from shared.locator_common import TZ, StoreResolver
from shared.store_registry import StoreRegistry

FIXTURES = Path(__file__).parent / "fixtures"
# Trimmed real CompeteTournamentSearch responses (2026-11-17): Madrid
# (Júpiter Central, Ítaca, Júpiter Alcobendas, Zacatrus, two Pre-Rifts) and
# Barcelona (Monstercards, El Encuentro at 06:00, Nikochan Arena).
SEARCH = json.loads((FIXTURES / "riftbound_search.json").read_text())
NOW = datetime(2026, 11, 17, 22, 0, tzinfo=TZ)
UNTIL = datetime(2027, 1, 31, tzinfo=TZ)
JUPITER_ORG = "019fb060-8c68-70a5-8d55-9fc66a460074"


def _nodes(city_id):
    return [e["node"] for e in SEARCH[city_id]["data"]["competeTournamentSearch"]["edges"]]


def _resolver():
    return StoreResolver(StoreRegistry({"ignore": {"names": [], "external": []}, "stores": [
        {"id": "jupiter-central", "name": "Júpiter Central", "city": "madrid",
         "address": "C / Manuel Silvela, 8 , Madrid, M, 28010, Spain",
         "aliases": {"names": ["Júpiter Juegos Madrid"], "external": []}},
        {"id": "itaca", "name": "Ítaca", "city": "madrid",
         "address": "Calle Blasco de Garay, 15, Local 1, 28015 Madrid",
         "aliases": {"names": ["Itaca"], "external": []}},
        {"id": "nikochan-arena", "name": "Nikochan Arena", "city": "barcelona",
         "address": "32 Plaça del Triomf, Terrassa, CT, 08225, Spain",
         "aliases": {"names": [], "external": []}},
    ]}))


def _parse(city_id, nodes=None):
    return locator_riftbound.parse_events(
        _nodes(city_id) if nodes is None else nodes, _resolver(), city(city_id), UNTIL, NOW)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.madrid = {e["source_event_id"]: e for e in _parse("madrid")}

    def test_start_is_utc(self):
        # Nikochan's page and card both say Sunday 29 Nov 10:00.
        [niko] = [e for e in _parse("barcelona") if e["store"] == "Nikochan Arena"]
        self.assertEqual(niko["datetime_start"], "2026-11-29T10:00:00+01:00")
        self.assertEqual(niko["source_event_id"], "playriftbound:117468466850407382")
        self.assertEqual(niko["source_url"], "https://playriftbound.com/es-ES/events/117468466850407382")

    def test_store_hint_and_resolution(self):
        e = self.madrid["playriftbound:117580282538002702"]
        self.assertEqual(e["store"], "Júpiter Central")
        self.assertEqual(e["store_hint"], {
            "external": f"playriftbound:{JUPITER_ORG}", "name": "Júpiter Juegos Madrid",
            "address": "Calle de Manuel Silvela, 8, Chamberí, 28010 Madrid, Spain",
            "lat": 40.4292735, "lng": -3.6984561, "city": "madrid",
        })
        self.assertEqual(e["store_address"], e["store_hint"]["address"])
        self.assertEqual(self.madrid["playriftbound:117538296645432422"]["store"], "Ítaca")

    def test_attendance(self):
        past = self.madrid["playriftbound:117580282538002702"]    # 10 Nov, 4 played
        self.assertEqual((past["capacity"], past["participants"]), (16, 4))
        self.assertNotIn("registered", past)
        full = self.madrid["playriftbound:117246419484762089"]    # 25 Nov, 8 of 8
        self.assertEqual((full["capacity"], full["registered"], full["full"]), (8, 8, True))
        self.assertNotIn("participants", full)
        unopened = self.madrid["playriftbound:117580639045928516"]  # 0 of 64 so far
        self.assertEqual(unopened["capacity"], 64)
        self.assertNotIn("registered", unopened)
        self.assertNotIn("full", unopened)

    def test_formats(self):
        nexus = self.madrid["playriftbound:117580282538002702"]
        self.assertEqual((nexus["game"], nexus["format"], nexus["format_official"]),
                         ("Riftbound", "Standard", "Nexus Night"))
        # Pre-Rift (sealed pool) is the prerelease, whatever the title says.
        for sid in ("playriftbound:117580639045928516", "playriftbound:117551619118456224"):
            self.assertEqual((self.madrid[sid]["format"], self.madrid[sid]["format_official"]),
                             ("Prerelease", None))
        self.assertIsNone(nexus["datetime_end"])

    def test_implausible_hour_skipped(self):
        # El Encuentro's Sunday Nexus Night is at 06:00 here; its own calendar has 17:00.
        with self.assertLogs(locator_riftbound.logger, "WARNING"):
            stores = [e["store"] for e in _parse("barcelona")]
        self.assertNotIn("El Encuentro Infrecuentes S.L.", stores)
        self.assertEqual(sorted(stores), ["Monstercards", "Nikochan Arena"])

    def test_double_listing_kept_once(self):
        nodes = _nodes("madrid")
        twin = copy.deepcopy(nodes[0])
        twin["tournament"]["id"] = "117580282538009999"
        events = _parse("madrid", nodes + [twin])
        self.assertEqual(len(events), len(nodes))
        self.assertNotIn("playriftbound:117580282538009999", {e["source_event_id"] for e in events})

    def test_window_end_and_incomplete_nodes(self):
        nodes = _nodes("madrid")
        late = copy.deepcopy(nodes[0])
        late["tournament"].update(id="late", startsAt="2027-03-01T17:00:00Z")
        broken = copy.deepcopy(nodes[0])
        broken["organizer"] = None
        events = _parse("madrid", nodes + [late, broken])
        self.assertEqual(len(events), len(nodes))


class RealRegistryTests(unittest.TestCase):
    """Riftbound organizers that are known stores under another name."""

    def test_aliases(self):
        resolver = StoreResolver()
        cases = {
            ("barcelona", "019fb060-8d6b-7668-a948-e3bf20b66d65", "Monstercards"): "Monster Cards",
            ("barcelona", "01a082ab-25a7-7b3d-a7f2-a5fe8de3ee34", "El Encuentro Infrecuentes S.L."): "El Encuentro",
            ("barcelona", "01a023da-4f0b-7307-b1a3-af81039677c2", "BPG Trade and Publishing SL"): "Infernal Forge",
            ("barcelona", "019fb060-9413-7508-ba8f-1fa2c20cd2bf", "Kaburi Rol & Games"): "Kaburi",
            ("barcelona", "01a15680-ffd5-7c2a-9d1b-80aad4c102e8", "Neolab TCG"): "NeoLab TCG S.L.",
            ("madrid", "01a04515-967d-7dd4-874e-16cf3f397836", "TCGAcademymad"): "TCG Academy Madrid",
            ("madrid", "019fd809-80dc-7222-a8db-2f56684808bd", "Júpiter Alcobendas"): "Júpiter Madrid Norte",
            ("madrid", "019fb060-8faa-708f-a5be-956aa0a5587e", "Juegos Crisis"): "Crisis Alcorcón Juegos",
            ("madrid", "01a0e893-6118-7c0c-a851-3f06c081b16d", "SimonV Tcg Store"): "SimonV TCG",
        }
        for (city_id, org, name), store in cases.items():
            self.assertEqual(resolver.resolve("playriftbound", org, name, city=city(city_id)), store, name)

    def test_store_scraper_stores_resolve(self):
        events = locator_riftbound.parse_events(_nodes("barcelona"), StoreResolver(),
                                                city("barcelona"), UNTIL, NOW)
        self.assertIn("Nikochan Arena", {e["store"] for e in events})


class DedupTests(unittest.TestCase):
    """Nikochan's cards link playriftbound events: one event, the store's copy wins."""

    def setUp(self):
        html = (FIXTURES / "nikochan_calendario.html").read_text()
        self.niko = nikochan_arena.parse_page(html, datetime(2026, 11, 17, 12, 0, tzinfo=TZ))
        for e in self.niko:
            e["source"] = "scrapers.nikochan_arena"
        self.loc = [e for e in _parse("barcelona") if e["store"] == "Nikochan Arena"]
        for e in self.loc:
            e["source"] = "scrapers.locator_riftbound"

    def test_same_id_shares_key_and_is_dropped(self):
        [loc] = self.loc
        [card] = [e for e in self.niko if e["source_event_id"] == loc["source_event_id"]]
        self.assertEqual(event_key(card), event_key(loc))
        kept, dropped = drop_locator_duplicates(self.niko + self.loc, [], NOW.isoformat())
        self.assertEqual(dropped, 1)
        self.assertEqual(kept, self.niko)

    def test_same_id_dropped_even_when_times_disagree(self):
        [loc] = self.loc
        loc["datetime_start"] = "2026-11-29T18:00:00+01:00"   # 8 h off: no window match
        kept, dropped = drop_locator_duplicates(self.niko + self.loc, [], NOW.isoformat())
        self.assertEqual(dropped, 1)

    def test_same_id_on_disk_blocks_copy_when_store_scraper_failed(self):
        existing = [dict(e, is_active=True) for e in self.niko]
        kept, dropped = drop_locator_duplicates(self.loc, existing, NOW.isoformat())
        self.assertEqual((kept, dropped), ([], 1))


class SearchTests(unittest.TestCase):
    def _page(self, starts, more):
        edges = [{"cursor": f"c{i}", "node": {"tournament": {"id": str(i), "startsAt": s}}}
                 for i, s in enumerate(starts)]
        return {"competeTournamentSearch": {"edges": edges, "pageInfo": {
            "endCursor": edges[-1]["cursor"] if edges else None, "hasNextPage": more}}}

    def test_pages_until_past_window(self):
        pages = [self._page(["2026-11-20T17:00:00Z"], True),
                 self._page(["2027-02-01T17:00:00Z"], True),
                 self._page(["2027-03-01T17:00:00Z"], False)]
        with mock.patch.object(locator_riftbound, "_graphql", side_effect=pages) as gql:
            nodes = locator_riftbound._search(None, "h", city("madrid"), "2026-11-10T00:00:00Z", UNTIL)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(gql.call_count, 2)
        first = gql.call_args_list[0].args[2]
        self.assertEqual(first["filter"]["rb"]["distanceMeters"], 50000)
        self.assertEqual(first["filter"]["rb"]["startDate"], "2026-11-10T00:00:00Z")
        self.assertEqual(gql.call_args_list[1].args[2]["after"], "c0")

    def test_too_many_pages_raises(self):
        page = self._page(["2026-11-20T17:00:00Z"], True)
        with mock.patch.object(locator_riftbound, "_graphql", return_value=page):
            with self.assertRaises(RuntimeError):
                locator_riftbound._search(None, "h", city("madrid"), "2026-11-10T00:00:00Z", UNTIL)

    def test_stale_query_refreshes_id_once(self):
        calls = []

        def search(_sess, h, c, _since, _until):
            calls.append((h, c.id))
            if h == locator_riftbound.SEARCH_HASH:
                raise locator_riftbound.StaleQueryError("not in list")
            return []

        with mock.patch.object(locator_riftbound, "_search", side_effect=search), \
             mock.patch.object(locator_riftbound, "discover_search_hash", return_value="new") as disc, \
             mock.patch.object(locator_riftbound, "StoreResolver"):
            self.assertEqual(locator_riftbound.scrape(), [])
        self.assertEqual(disc.call_count, 1)
        self.assertEqual(calls, [(locator_riftbound.SEARCH_HASH, "madrid"),
                                 *(("new", c.id) for c in cities.CITIES)])

    def test_graphql_errors_raise(self):
        stale = mock.Mock(status_code=404)
        stale.json.return_value = {"errors": [{"message": "Persisted query 'x' not found",
                                               "extensions": {"code": "PERSISTED_QUERY_NOT_IN_LIST"}}]}
        import requests
        with mock.patch.object(locator_riftbound, "request_json",
                               side_effect=requests.HTTPError(response=stale)):
            with self.assertRaises(locator_riftbound.StaleQueryError):
                locator_riftbound._graphql(None, "x", {})
        with mock.patch.object(locator_riftbound, "request_json",
                               return_value={"errors": [{"message": "No client headers set"}]}):
            with self.assertRaises(RuntimeError):
                locator_riftbound._graphql(None, "x", {})


class ManifestTests(unittest.TestCase):
    # Trimmed real snippets of the site's Turbopack chunks (2026-11-17).
    CLIENT = ('let tq=new tI({...(0,tN.generatePersistedQueryIdsFromManifest)({loadManifest:()=>W.A(47828)}),'
              'useGETForHashedQueries:!0,disable:()=>!tV()})')
    LAZY = ('},47828,e=>{e.v(t=>Promise.all(["static/chunks/2mbwzmf-p8djo.js"].map(t=>e.l(t)))'
            '.then(()=>t(972560)))}]);')
    MANIFEST = ('e.exports=JSON.parse(\'{"format":"apollo-persisted-query-manifest","version":1,"operations":['
                '{"id":"868030f676276bec926c740aada7129713d4097f6e2dd7c3a0451c4fb43852eb",'
                '"name":"CompeteTournamentSearch","type":"query","body":"query CompeteTournamentSearch"}]}\')')

    def test_finds_id_through_lazy_manifest_chunk(self):
        fetched = []

        def fetch(path):
            fetched.append(path)
            return self.MANIFEST

        found = locator_riftbound.find_manifest_id(iter(["noise", self.CLIENT, self.LAZY]), fetch)
        self.assertEqual(found, locator_riftbound.SEARCH_HASH)
        self.assertEqual(fetched, ["static/chunks/2mbwzmf-p8djo.js"])

    def test_missing_manifest_is_none(self):
        self.assertIsNone(locator_riftbound.find_manifest_id(iter(["noise"]), lambda p: ""))


if __name__ == "__main__":
    unittest.main()
