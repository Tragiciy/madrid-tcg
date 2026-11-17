"""Multi-city: one city list, every event in its store's city, per-city
publish output, and per-city failure guards."""

import json
import pathlib
import re
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from pipeline.health import build_stats
from pipeline.pages import build_pages, calendar_url, write_pages
from pipeline.publish import build_api
from pipeline.reconcile import (
    attach_store_ids, find_suspect_zero_cities, merge_events, merge_holding_cities,
    upcoming_drops,
)
from pipeline.share import app_url, build_share_pages
from scrapers import locator_gem
from shared import bandai_locator, cities
from shared.cities import CITIES, City
from shared.locator_common import StoreResolver, for_each_city, store_hint
from shared.store_registry import StoreRegistry
from tools.sync_health_issues import issue_body, unhealthy_stores

NOW = "2026-11-18T12:00:00+01:00"  # Wednesday
MAD, BCN = cities.get("madrid"), cities.get("barcelona")


def _registry():
    return StoreRegistry({"stores": [
        {"id": "dragon-den", "name": "Dragon Den", "city": "madrid", "address": "",
         "lat": 40.42, "lng": -3.70, "aliases": {"names": [], "external": ["wizards:1"]}},
        {"id": "arte-9", "name": "Arte 9", "city": "madrid", "address": "C/ X 1, 28028 Madrid",
         "lat": 40.43, "lng": -3.67, "aliases": {"names": [], "external": []}},
        {"id": "kaburi", "name": "Kaburi", "city": "barcelona", "address": "C/ Y 2, 08001 Barcelona",
         "lat": 41.38, "lng": 2.17, "aliases": {"names": [], "external": ["bandai:9"]}},
    ]})


def _e(start, store="Arte 9", store_id="arte-9", city=None, source="scrapers.x", **kw):
    e = {"title": "Liga", "store": store, "store_id": store_id, "game": "Lorcana",
         "datetime_start": start, "source_url": "https://example.com/e", "is_active": True,
         "source": source}
    if city:
        e["city"] = city
    e.update(kw)
    return e


class CityConfigTests(unittest.TestCase):
    def test_single_source_of_truth(self):
        self.assertEqual([c.id for c in CITIES],
                         ["madrid", "barcelona", "valencia", "sevilla", "malaga", "bilbao", "zaragoza"])
        self.assertEqual(cities.get("malaga").name, "Málaga")  # ASCII id, accented name
        self.assertEqual(cities.default().id, "madrid")
        self.assertEqual(BCN.radius_km, 30)
        self.assertEqual(BCN.location_name, "Barcelona, Spain")
        self.assertTrue(BCN.contains(41.45, 2.25))       # Badalona-ish
        self.assertFalse(BCN.contains(40.42, -3.70))     # Madrid
        self.assertFalse(MAD.contains(41.38, 2.17))

    def test_unsupported_timezone_is_refused(self):
        other = City(id="lisboa", name="Lisboa", timezone="Europe/Lisbon", lat=38.7, lng=-9.1,
                     radius_km=30, country="ES", search_terms=("Lisboa",))
        with self.assertRaises(ValueError):
            cities._check(CITIES + (other,))

    def test_overlapping_radii_are_refused(self):
        alcala = City(id="alcala", name="Alcalá", timezone="Europe/Madrid", lat=40.48, lng=-3.36,
                      radius_km=20, country="ES", search_terms=("Alcalá",))
        with self.assertRaisesRegex(ValueError, "overlap"):
            cities._check(CITIES + (alcala,))

    def test_duplicate_ids_are_refused(self):
        with self.assertRaises(ValueError):
            cities._check(CITIES + (MAD,))


class ForEachCityTests(unittest.TestCase):
    def test_runs_every_city_in_order_and_drops_repeats(self):
        seen = []

        def fetch(city):
            seen.append(city.id)
            return [{"source_event_id": "x:1", "c": city.id},
                    {"source_event_id": f"x:{city.id}", "c": city.id}]

        out = for_each_city("T", fetch)
        self.assertEqual(seen, [c.id for c in CITIES])
        self.assertEqual([(e["source_event_id"], e["c"]) for e in out],
                         [("x:1", "madrid"), *((f"x:{c.id}", c.id) for c in CITIES)])

    def test_one_city_failing_fails_the_source(self):
        def fetch(city):
            if city.id == "barcelona":
                raise RuntimeError("HTTP 500")
            return [{"source_event_id": "x:1"}]

        with self.assertRaises(RuntimeError):
            for_each_city("T", fetch)


class ResolverCityTests(unittest.TestCase):
    def setUp(self):
        self.r = StoreResolver(_registry())

    def test_same_name_in_another_city_never_merges(self):
        # "Dragon Den" exists in Madrid with no address: without city scoping
        # a Barcelona shop of that name would be glued onto it.
        self.assertEqual(self.r.resolve("bandai", 77, "Dragon Den", "C/ Z 3, 08002 Barcelona", city=BCN),
                         "Dragon Den (08002)")
        self.assertEqual(self.r.resolve("bandai", 78, "DRAGON DEN", "", city=BCN), "DRAGON DEN (Barcelona)")
        self.assertEqual(self.r.resolve("bandai", 79, "Dragon Den", "", city=MAD), "Dragon Den")

    def test_external_id_matches_in_any_city(self):
        self.assertEqual(self.r.resolve("wizards", 1, "Whatever", "", city=BCN), "Dragon Den")

    def test_store_inside_the_radius_is_in_scope(self):
        # Overlapping radii: a Madrid-registered store located inside the
        # searched city's radius still matches by name.
        reg = _registry()
        reg.by_id["arte-9"].update(lat=41.40, lng=2.16)
        r = StoreResolver(reg)
        self.assertEqual(r.resolve("gem", "a9", "Arte 9", "", city=BCN), "Arte 9")

    def test_without_city_keeps_global_matching(self):
        self.assertEqual(self.r.resolve("bandai", 80, "Kaburi", ""), "Kaburi")


class LearnCityTests(unittest.TestCase):
    def test_new_store_gets_the_search_city(self):
        reg = _registry()
        rec = reg.learn("Nova Store", store_hint("bandai", 5, "Nova Store", "C/ N 1, 08003 Barcelona",
                                                 41.39, 2.18, city=BCN))
        self.assertEqual((rec["city"], rec["lat"], rec["address"]), ("barcelona", 41.39, "C/ N 1, 08003 Barcelona"))

    def test_known_store_keeps_its_registry_city(self):
        reg = _registry()
        # By external id, whichever city's search found it.
        rec = reg.learn("Dragon Den", store_hint("wizards", 1, "Dragon Den", "", city=BCN))
        self.assertEqual((rec["id"], rec["city"]), ("dragon-den", "madrid"))
        # Overlapping radii: a Madrid store located inside Barcelona's radius.
        reg.by_id["arte-9"].update(lat=41.40, lng=2.16)
        rec = reg.learn("Arte 9", store_hint("bandai", 6, "Arte 9", "", city=BCN))
        self.assertEqual((rec["id"], rec["city"]), ("arte-9", "madrid"))

    def test_exact_name_in_another_city_is_never_a_match(self):
        # Two new stores called "Nexus" in one run, first Madrid then Barcelona.
        reg = _registry()
        mad = reg.learn("Nexus", store_hint("bandai", 31, "Nexus", "C/ A 1, 28004 Madrid",
                                            40.42, -3.70, city=MAD))
        bcn = reg.learn("Nexus", store_hint("bandai", 32, "Nexus", "C/ B 2, 08005 Barcelona",
                                            41.40, 2.19, city=BCN))
        self.assertNotEqual(mad["id"], bcn["id"])
        self.assertEqual((bcn["name"], bcn["city"]), ("Nexus (08005)", "barcelona"))
        # The Madrid record is untouched: no Barcelona id, address or pin.
        self.assertEqual((mad["address"], mad["lat"], mad["aliases"]["external"]),
                         ("C/ A 1, 28004 Madrid", 40.42, ["bandai:31"]))
        self.assertEqual(bcn["aliases"]["external"], ["bandai:32"])
        # Next run the resolver names it the same way, so nothing new is created.
        r = StoreResolver(reg)
        self.assertEqual(r.resolve("bandai", 32, "Nexus", "C/ B 2, 08005 Barcelona", city=BCN),
                         "Nexus (08005)")
        self.assertEqual(r.resolve("bandai", 99, "NEXUS", "C/ B 2, 08005 Barcelona", city=BCN,
                                   lat=41.40, lng=2.19), "Nexus (08005)")

    def test_without_postcode_the_city_name_tells_them_apart(self):
        reg = _registry()
        rec = reg.learn("Dragon Den", store_hint("gem", "dd", "Dragon Den", "", city=BCN))
        self.assertEqual((rec["name"], rec["city"]), ("Dragon Den (Barcelona)", "barcelona"))
        self.assertEqual(StoreResolver(reg).resolve("gem", "dd", "Dragon Den", "", city=BCN),
                         "Dragon Den (Barcelona)")


class SamePlaceTests(unittest.TestCase):
    """Same name, postcodes disagree: one store if the pins are within 1 km
    (Geekkaos 08302/08304 in Mataró, Homoludicus 08402/08401 in Granollers)."""

    def _reg(self):
        return StoreRegistry({"stores": [
            {"id": "geekkaos", "name": "Geekkaos", "city": "barcelona",
             "address": "Jaume isern 69, Mataro, barcelona, 08302, Spain",
             "lat": 41.5405, "lng": 2.4369, "aliases": {"names": [], "external": ["wizards:18404"]}}]})

    def test_close_pins_are_one_store_and_the_alias_sticks(self):
        reg = self._reg()
        args = ("ravensburger", 23621, "Geekkaos", "55, Ronda Mossèn Jacint Verdaguer, Mataró, CT, 08304, ES")
        self.assertEqual(StoreResolver(reg).resolve(*args, city=BCN, lat=41.5444, lng=2.4366), "Geekkaos")
        # First run: the store was created by another locator in the same run.
        rec = reg.learn("Geekkaos", store_hint(*args, 41.5444, 2.4366, city=BCN))
        self.assertEqual(rec["id"], "geekkaos")
        self.assertIn("ravensburger:23621", rec["aliases"]["external"])
        # Second run resolves by external id: no "Geekkaos (08304)".
        self.assertEqual(StoreResolver(reg).resolve(*args, city=BCN), "Geekkaos")

    def test_far_pins_with_other_postcode_are_a_branch(self):
        reg = self._reg()
        args = ("ravensburger", 7, "Geekkaos", "C/ Major 1, 08401, Granollers")
        self.assertEqual(StoreResolver(reg).resolve(*args, city=BCN, lat=41.60, lng=2.29),
                         "Geekkaos (08401)")
        rec = reg.learn("Geekkaos", store_hint(*args, 41.60, 2.29, city=BCN))
        self.assertEqual(rec["name"], "Geekkaos (08401)")
        self.assertEqual(StoreResolver(reg).resolve(*args, city=BCN, lat=41.60, lng=2.29),
                         "Geekkaos (08401)")

    def test_known_external_id_is_kept_even_if_the_postcode_changed(self):
        reg = self._reg()
        rec = reg.learn("Geekkaos", store_hint("wizards", 18404, "Geekkaos", "C/ Nova 9, 08390, Montgat",
                                               41.47, 2.28, city=BCN))
        self.assertEqual(rec["id"], "geekkaos")

    def test_spelling_match_is_limited_to_the_hint_city(self):
        reg = _registry()
        rec = reg.learn("DRAGON DEN (Barcelona)", store_hint("bandai", 7, "DRAGON DEN", "", city=BCN))
        self.assertNotEqual(rec["id"], "dragon-den")
        self.assertEqual(rec["city"], "barcelona")
        # the Madrid store's spelling is not taken over as an alias
        self.assertEqual(rec["aliases"]["names"], [])

    def test_no_hint_city_defaults_to_madrid(self):
        self.assertEqual(_registry().learn("Brand New")["city"], "madrid")

    def test_unknown_city_is_refused(self):
        with self.assertRaises(ValueError):
            _registry().learn("X", {"city": "atlantis"})


class LocatorCityTests(unittest.TestCase):
    def test_gem_filters_by_the_searched_city(self):
        start = datetime.now(ZoneInfo("Europe/Madrid")).replace(hour=17, minute=0, second=0, microsecond=0)
        base = {"status": "PLANNED", "tournament_type": "Armory Event", "organiser_name": "FaB BCN",
                "address": "C/ Q, 08005 Barcelona",
                "start_time": start.strftime("%Y-%m-%dT%H:%M:%S") + "+13:00"}
        bcn = dict(base, id=1, lat=41.40, lon=2.19)
        mad = dict(base, id=2, lat=40.43, lon=-3.67, organiser_name="FaB MAD")
        resolver = StoreResolver(StoreRegistry())
        [e] = locator_gem.parse_events([bcn, mad], resolver, BCN)
        self.assertEqual(e["source_event_id"], "gem:1")
        self.assertEqual(e["store_hint"]["city"], "barcelona")

    def test_bandai_place_geo_x_is_latitude(self):
        raw = [{"id": 1, "organizer_name": "Kaburi", "organizer_id": 9,
                "event_series_title": "[Nov] OP Store Tournament", "start_datetime": "2026-11-20T18:00:00",
                "timezone": "Europe/Madrid", "country_code": "ES",
                "place_geo": {"x": 41.38, "y": 2.17}}]
        [e] = bandai_locator.parse_events(raw, "One Piece", StoreResolver(_registry()), BCN)
        self.assertEqual((e["store_hint"]["lat"], e["store_hint"]["lng"]), (41.38, 2.17))
        self.assertEqual(e["store"], "Kaburi")


class EventCityTests(unittest.TestCase):
    def test_event_city_is_the_store_registry_city(self):
        reg = _registry()
        fresh = [
            _e("2026-11-20T18:00:00+01:00", store="Kaburi", store_id=None, source="scrapers.locator_x",
               store_hint=store_hint("bandai", 9, "KABURI", "", city=MAD)),  # found by Madrid's search
            _e("2026-11-20T18:00:00+01:00", store="New BCN", store_id=None, source="scrapers.locator_x",
               store_hint=store_hint("bandai", 10, "New BCN", "C/ 1, 08001", 41.38, 2.17, city=BCN)),
            _e("2026-11-20T18:00:00+01:00"),  # per-store scraper, Madrid store
        ]
        existing = [_e("2026-10-01T18:00:00+01:00", store="Gone Store", store_id=None),
                    _e("2026-10-01T18:00:00+01:00", store="Gone BCN", store_id=None, city="barcelona")]
        attach_store_ids(fresh, existing, reg)
        self.assertEqual([e["city"] for e in fresh], ["barcelona", "barcelona", "madrid"])
        self.assertEqual(reg.find("New BCN")["city"], "barcelona")
        self.assertEqual([e["city"] for e in existing], ["madrid", "barcelona"])


class PerCityPublishTests(unittest.TestCase):
    def setUp(self):
        self.reg = _registry()
        self.events = [
            _e("2026-11-20T19:00:00+01:00", city="madrid", source_event_id="wizards:1"),
            _e("2026-11-21T11:00:00+01:00", store="Kaburi", store_id="kaburi", city="barcelona",
               game="One Piece", source_event_id="bandai:1"),
            _e("2026-11-22T11:00:00+01:00"),  # no city: history before multi-city → Madrid
        ]
        self.docs = build_api(self.events, self.reg, NOW)

    def test_each_city_gets_only_its_events(self):
        mad = self.docs["madrid/events/upcoming.json"]["events"]
        bcn = self.docs["barcelona/events/upcoming.json"]["events"]
        self.assertEqual([e["datetime_start"][:10] for e in mad], ["2026-11-20", "2026-11-22"])
        self.assertEqual([e["id"] for e in bcn], ["id:bandai:1"])
        self.assertEqual(self.docs["barcelona/events/upcoming.json"]["city"], "barcelona")
        self.assertEqual(self.docs["barcelona/index.json"]["weeks"], {"2026-W47": 1})
        self.assertNotIn("city", bcn[0])  # the city is the path, not an event field

    def test_stores_and_cities_counts(self):
        self.assertEqual([s["id"] for s in self.docs["barcelona/stores.json"]["stores"]], ["kaburi"])
        self.assertEqual([s["id"] for s in self.docs["madrid/stores.json"]["stores"]], ["arte-9", "dragon-den"])
        self.assertEqual(
            [(c["id"], c["name"], c["stores"]) for c in self.docs["cities.json"]["cities"]],
            [("madrid", "Madrid", 2), ("barcelona", "Barcelona", 1), ("valencia", "Valencia", 0),
             ("sevilla", "Sevilla", 0), ("malaga", "Málaga", 0), ("bilbao", "Bilbao", 0),
             ("zaragoza", "Zaragoza", 0)])
        bcn = self.docs["cities.json"]["cities"][1]
        self.assertEqual((bcn["lat"], bcn["lng"], bcn["timezone"]), (41.3874, 2.1686, "Europe/Madrid"))

    def test_a_city_without_events_still_has_its_files(self):
        docs = build_api([], StoreRegistry(), NOW)
        for f in ("barcelona/index.json", "barcelona/stores.json", "barcelona/events/upcoming.json"):
            self.assertIn(f, docs)
        self.assertEqual(docs["barcelona/index.json"]["weeks"], {})

    def test_pages_per_city(self):
        pages = build_pages(self.events, self.reg, NOW)
        for p in ("barcelona/index.html", "barcelona/one-piece/index.html",
                  "barcelona/tiendas/kaburi/index.html", "en/barcelona/stores/kaburi/index.html"):
            self.assertIn(p, pages)
        self.assertNotIn("madrid/one-piece/index.html", pages)       # only Barcelona plays it
        self.assertNotIn("madrid/tiendas/kaburi/index.html", pages)
        self.assertNotIn("Kaburi", pages["madrid/index.html"])
        bcn = pages["barcelona/index.html"]
        self.assertIn('href="/?city=barcelona"', bcn)                  # calendar link selects the city
        self.assertIn('rel="canonical" href="https://madrid-tcg.pages.dev/barcelona/"', bcn)
        self.assertIn("TCG Barcelona", bcn)
        raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', bcn, re.S).group(1)
        self.assertEqual(json.loads(raw)[0]["location"]["address"]["addressLocality"], "Barcelona")
        self.assertIn("<loc>https://madrid-tcg.pages.dev/barcelona/</loc>", pages["sitemap.xml"])
        self.assertIn("<loc>https://madrid-tcg.pages.dev/madrid/</loc>", pages["sitemap.xml"])
        self.assertEqual(calendar_url("madrid", "game=Lorcana"), "/?game=Lorcana")
        self.assertEqual(calendar_url("barcelona", "game=Lorcana"), "/?city=barcelona&game=Lorcana")

    def test_stale_pages_are_removed_in_every_city(self):
        pages = build_pages(self.events, self.reg, NOW)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            stale = root / "barcelona/tiendas/closed/index.html"
            stale.parent.mkdir(parents=True)
            stale.write_text("old")
            write_pages(pages, root)
            self.assertFalse(stale.exists())

    def test_share_links_select_the_city(self):
        pe = {"id": "id:bandai:1", "title": "T", "store": "Kaburi", "game": "One Piece",
              "datetime_start": "2026-11-21T11:00:00+01:00", "source_url": "https://x"}
        self.assertTrue(app_url(pe).startswith("/?event="))
        self.assertTrue(app_url(pe, "barcelona").startswith("/?city=barcelona&event="))
        [html] = build_share_pages([pe], NOW, BCN).values()
        self.assertIn("/?city=barcelona&amp;event=", html)
        self.assertIn('content="TCG Barcelona"', html)


class PerCityGuardTests(unittest.TestCase):
    @staticmethod
    def _upcoming(n, city, source="scrapers.locator_x"):
        return [_e(f"2026-12-{1 + i % 28:02d}T18:00:00+01:00", city=city, source=source,
                   source_event_id=f"{city}:{i}") for i in range(n)]

    def test_city_losing_its_events_is_caught_despite_the_other_growing(self):
        existing = self._upcoming(400, "madrid") + self._upcoming(100, "barcelona")
        merged = self._upcoming(400, "madrid") + self._upcoming(300, "barcelona")
        merged[:300] = [dict(e, is_active=False) for e in merged[:300]]  # Madrid loses 300
        # Globally 500 → 700 upcoming: a global guard would pass.
        self.assertEqual(upcoming_drops(existing, merged, NOW, 0.5, 50), [("madrid", 400, 100)])

    def test_small_city_dropping_to_zero_is_caught(self):
        existing = self._upcoming(1000, "madrid") + self._upcoming(60, "barcelona")
        merged = self._upcoming(1000, "madrid")
        self.assertEqual(upcoming_drops(existing, merged, NOW, 0.5, 50), [("barcelona", 60, 0)])

    def test_dropping_city_is_held_and_the_others_publish(self):
        src = "scrapers.locator_x"
        existing = self._upcoming(100, "madrid", src) + self._upcoming(100, "barcelona", src)
        # Madrid: 99 of 100 seen (one ordinary miss). Barcelona: a partial
        # result, 20 of 100 plus one new event.
        fresh = [dict(e) for e in existing[:99]] + [dict(e) for e in existing[100:120]] \
            + [_e("2026-12-05T18:00:00+01:00", city="barcelona", source=src, source_event_id="new:1")]
        merged, held = merge_holding_cities(existing, fresh, NOW, set(), 0.5, 50)
        self.assertEqual(held, [("barcelona", 100, 21)])
        bcn = [e for e in merged if e["city"] == "barcelona"]
        self.assertEqual(len(bcn), 101)                                  # nothing hidden, new one added
        self.assertTrue(all(e.get("is_active", True) and "missing_runs" not in e for e in bcn))
        mad_missing = [e for e in merged if e["city"] == "madrid" and e.get("missing_runs")]
        self.assertEqual(len(mad_missing), 1)                            # Madrid merged normally

        stats = {src: {"store": "X Locator", "raw": 120, "valid": 120, "dropped": 0,
                       "drop_reasons": {}, "error": None}}
        report = build_stats(merged, stats, [], NOW, held_cities=held)
        self.assertEqual(report["held_cities"],
                         [{"city": "barcelona", "upcoming_before": 100, "upcoming_after": 21}])
        info = unhealthy_stores(report)["city:barcelona"]
        body = issue_body("city:barcelona", info, NOW)
        self.assertIn("from 100 to 21", body)
        self.assertIn("kept all of barcelona's previous events", body)
        self.assertNotIn("held_cities", build_stats(merged, stats, [], NOW))  # only when held

    def test_city_issue_closes_as_recovered(self):
        from tools.sync_health_issues import plan_actions
        actions = plan_actions({"generated_at": NOW, "scrapers": {}, "by_store": {}},
                               [{"number": 7, "title": "[health] city:barcelona", "state": "OPEN"}])
        self.assertEqual(actions[0]["op"], "close")
        self.assertIn("Recovered", actions[0]["comment"])

    def test_guard_counts_before_on_the_unmerged_history(self):
        # merge_events marks unseen records of `existing` inactive in place;
        # counting "before" afterwards would hide the drop.
        existing = self._upcoming(100, "madrid")
        merged, held = merge_holding_cities(existing, [], NOW, set(), 0.5, 50)
        self.assertEqual(held, [("madrid", 100, 0)])
        self.assertEqual(sum(1 for e in merged if e.get("is_active", True)), 100)

    def test_below_the_floor_is_not_guarded(self):
        existing = self._upcoming(10, "barcelona")
        self.assertEqual(upcoming_drops(existing, [], NOW, 0.5, 50), [])

    def test_source_silent_in_one_city_is_protected_there_only(self):
        src = "scrapers.locator_x"
        existing = self._upcoming(3, "madrid", src) + self._upcoming(3, "barcelona", src)
        fresh = self._upcoming(2, "madrid", src)  # Barcelona's search came back empty
        stats = {src: {"store": "X Locator", "raw": 2, "valid": 2, "dropped": 0,
                       "drop_reasons": {}, "error": None}}
        zero = find_suspect_zero_cities(stats, fresh, existing, NOW)
        self.assertEqual(zero, {f"{src}@barcelona"})
        merged = merge_events(existing, fresh, NOW, protected_sources=zero)
        by_city = lambda city: [e.get("missing_runs") for e in merged if e["city"] == city]
        self.assertEqual(by_city("barcelona"), [None, None, None])     # untouched
        self.assertEqual(sorted(by_city("madrid"), key=str), [1, None, None])  # normal miss counting

        report = build_stats(merged, stats, [], NOW, suspect_zero_sources=zero)
        self.assertEqual(report["scrapers"][src]["anomaly"], "suspect_zero")
        self.assertEqual(report["scrapers"][src]["suspect_zero_cities"], ["barcelona"])
        info = unhealthy_stores(report)["X Locator"]
        self.assertIn("0 events for barcelona", issue_body("X Locator", info, NOW))

    def test_failed_or_empty_source_is_not_a_city_zero(self):
        src = "scrapers.locator_x"
        existing = self._upcoming(3, "barcelona", src)
        for ms in ({"valid": 0, "error": None}, {"valid": 5, "error": "boom"}):
            self.assertEqual(find_suspect_zero_cities({src: ms}, [], existing, NOW), set())


if __name__ == "__main__":
    unittest.main()
