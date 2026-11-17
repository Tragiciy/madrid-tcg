import unittest

from pipeline.reconcile import drop_locator_duplicates, event_key, find_store_name_variants, merge_events
from scrapers import locator_ravensburger, locator_wizards
from shared import bandai_locator as locator_bandai
from pipeline.reconcile import attach_store_ids
from shared.locator_common import StoreResolver, store_hint, to_madrid_iso
from shared.store_registry import StoreRegistry, slugify

NOW = "2026-11-15T12:00:00+01:00"
def _registry():
    return StoreRegistry({
        "ignore": {"names": ["Online Shop"], "external": []},
        "stores": [
            {"id": "jupiter-juegos", "name": "Jupiter Juegos", "city": "madrid",
             "address": "Calle de la Cruz, 10, 28012 Madrid",
             "aliases": {"names": ["Júpiter Central"], "external": []}},
            {"id": "itaca", "name": "Ítaca", "city": "madrid", "address": "",
             "aliases": {"names": [], "external": ["wizards:8422"]}},
        ],
    })


def _resolver():
    return StoreResolver(_registry())


def _ev(store, game, start, source, title="Event"):
    return {"title": title, "store": store, "game": game, "datetime_start": start,
            "source": source, "source_url": "u", "is_active": True}


class DatetimeTests(unittest.TestCase):
    def test_wizards_utc_with_seven_digit_fraction(self):
        self.assertEqual(to_madrid_iso("2026-11-15T15:30:00.0000000Z"),
                         "2026-11-15T16:30:00+01:00")

    def test_bandai_naive_local_time_with_zone(self):
        self.assertEqual(to_madrid_iso("2026-07-15T11:00:00", "Europe/Madrid"),
                         "2026-07-15T11:00:00+02:00")

    def test_naive_without_zone_is_rejected(self):
        self.assertIsNone(to_madrid_iso("2026-11-15T11:00:00"))


class ResolverTests(unittest.TestCase):
    def test_override_then_meta_match_then_own_name(self):
        r = _resolver()
        self.assertEqual(r.resolve("wizards", 1, "JÚPITER CENTRAL"), "Jupiter Juegos")
        self.assertEqual(r.resolve("wizards", 2, "Itaca"), "Ítaca")
        self.assertEqual(r.resolve("wizards", 3, "Brand New Store"), "Brand New Store")
        self.assertIs(r.resolve("wizards", 4, "Online Shop"), StoreResolver.SKIP)
        self.assertEqual(r.resolve("wizards", 8422, "Totally Different Name"), "Ítaca")  # by external id


class ParseTests(unittest.TestCase):
    def test_wizards(self):
        raw = [{"id": "1", "title": "Commander Night", "scheduledStartTime": "2026-11-20T18:00:00.0000000Z",
                "eventFormat": {"name": "Commander"}, "organization": {"id": "8420", "name": "Júpiter Central"}}]
        [e] = locator_wizards.parse_events(raw, {"8420": "Calle de la Cruz, 10"}, _resolver())
        self.assertEqual(e["store"], "Jupiter Juegos")
        self.assertEqual(e["game"], "Magic: The Gathering")
        self.assertEqual(e["datetime_start"], "2026-11-20T19:00:00+01:00")
        self.assertEqual(e["source_event_id"], "wizards:1")
        self.assertEqual(e["store_address"], "Calle de la Cruz, 10")

    def test_bandai_strips_month_prefix_and_skips_cancelled(self):
        base = {"organizer_id": 9, "organizer_name": "Brand New Store", "start_datetime": "2026-11-20T17:00:00",
                "timezone": "Europe/Madrid", "event_series_title": "[November 2026] OP Store Tournament Event",
                "street_address": "Calle 1", "postcode": "28001", "city_code": "Madrid"}
        raw = [dict(base, id=1), dict(base, id=2, is_canceled=True)]
        events = locator_bandai.parse_events(raw, "One Piece", _resolver())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "OP Store Tournament Event")
        self.assertEqual(events[0]["store_address"], "Calle 1, 28001, Madrid")

    def test_ravensburger(self):
        raw = [{"id": 5, "name": "Lorcana Weekly", "start_datetime": "2026-11-20T17:00:00+00:00",
                "is_end_datetime_estimated": True, "event_status": "SCHEDULED",
                "store": {"id": 1, "name": "Ítaca", "full_address": "X"}},
               {"id": 6, "name": "Gone", "start_datetime": "2026-11-21T17:00:00+00:00",
                "event_status": "CANCELED", "store": {"id": 1, "name": "Ítaca"}}]
        [e] = locator_ravensburger.parse_events(raw, _resolver())
        self.assertEqual((e["store"], e["game"], e["datetime_end"]), ("Ítaca", "Lorcana", None))


class DedupTests(unittest.TestCase):
    def test_one_scraper_event_absorbs_only_one_locator_event(self):
        fresh = [
            _ev("Ítaca", "Magic: The Gathering", "2026-11-20T18:00:00+01:00", "scrapers.itaca", "Commander"),
            _ev("Ítaca", "Magic: The Gathering", "2026-11-20T18:00:00+01:00", "scrapers.locator_wizards", "Commander"),
            _ev("Ítaca", "Magic: The Gathering", "2026-11-20T18:00:00+01:00", "scrapers.locator_wizards", "Modern"),
        ]
        kept, dropped = drop_locator_duplicates(fresh, [], NOW)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(kept), 2)

    def test_recent_past_scraper_history_blocks_locator_lookback_copies(self):
        past = _ev("Ítaca", "Lorcana", "2026-11-13T18:00:00+01:00", "scrapers.itaca")
        past["is_active"] = False                      # finished, kept as history
        fresh = [_ev("Ítaca", "Lorcana", "2026-11-13T18:00:00+01:00", "scrapers.locator_ravensburger")]
        kept, dropped = drop_locator_duplicates(fresh, [past], NOW)
        self.assertEqual((kept, dropped), ([], 1))

    def test_scraper_event_on_disk_and_fresh_claims_once(self):
        e = _ev("Ítaca", "Magic: The Gathering", "2026-11-20T18:00:00+01:00", "scrapers.itaca")
        fresh = [dict(e),
                 _ev("Ítaca", "Magic: The Gathering", "2026-11-20T18:00:00+01:00", "scrapers.locator_wizards", "A"),
                 _ev("Ítaca", "Magic: The Gathering", "2026-11-20T18:30:00+01:00", "scrapers.locator_wizards", "B")]
        kept, dropped = drop_locator_duplicates(fresh, [dict(e)], NOW)
        self.assertEqual(dropped, 1)

    def test_store_scraper_wins_over_locator_copy(self):
        fresh = [
            _ev("Ítaca", "Magic: The Gathering", "2026-11-20T19:00:00+01:00", "scrapers.itaca"),
            _ev("Ítaca", "Magic: The Gathering", "2026-11-20T19:30:00+01:00", "scrapers.locator_wizards"),
            _ev("Ítaca", "Magic: The Gathering", "2026-11-20T11:00:00+01:00", "scrapers.locator_wizards"),
            _ev("Ítaca", "Lorcana", "2026-11-20T19:00:00+01:00", "scrapers.locator_ravensburger"),
        ]
        kept, dropped = drop_locator_duplicates(fresh, [], NOW)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(kept), 3)

    def test_protected_scraper_events_still_block_locator_copies(self):
        existing = [_ev("Ítaca", None, "2026-11-20T19:00:00+01:00", "scrapers.itaca")]
        fresh = [_ev("Ítaca", "Pokémon", "2026-11-20T19:15:00+01:00", "scrapers.locator_x")]
        kept, dropped = drop_locator_duplicates(fresh, existing, NOW)
        self.assertEqual((kept, dropped), ([], 1))

    def test_failed_locator_keeps_its_events(self):
        existing = [_ev("New Store", "Lorcana", "2026-11-20T19:00:00+01:00",
                        "scrapers.locator_ravensburger")]
        events = existing
        for _ in range(4):
            events = merge_events(events, [], NOW, protected_sources={"scrapers.locator_ravensburger"})
        self.assertEqual(len(events), 1)


class RegistryTests(unittest.TestCase):
    def test_learn_adds_store_alias_external_and_coordinates(self):
        reg = _registry()
        rec = reg.learn("Medusa Cómics", store_hint("wizards", 7, "MEDUSA COMICS", "C/ X 1, 28001", 40.1, -3.1),
                        today="2026-11-15")
        self.assertEqual((rec["id"], rec["origin"], rec["lat"]), ("medusa-comics", "auto", 40.1))
        self.assertEqual(rec["aliases"], {"names": ["MEDUSA COMICS"], "external": ["wizards:7"]})
        self.assertIs(reg.find(external="wizards:7"), rec)
        # learning again is idempotent
        reg.learn("Medusa Cómics", store_hint("wizards", 7, "MEDUSA COMICS"))
        self.assertEqual(len(reg.stores), 3)

    def test_spelling_of_another_store_is_not_added_as_alias(self):
        reg = _registry()
        branch = reg.learn("Júpiter Madrid Norte", store_hint("bandai", 354, "Jupiter Juegos", "x, 28100"))
        self.assertEqual(branch["aliases"]["names"], [])
        self.assertEqual(reg.find("Jupiter Juegos")["id"], "jupiter-juegos")

    def test_ids_are_stable_slugs_without_collisions(self):
        reg = _registry()
        a = reg.add("Ítaca", origin="auto")  # same slug as the existing record
        self.assertEqual((slugify("Zacatrus! Madrid Chamberí"), a["id"]), ("zacatrus-madrid-chamberi", "itaca-2"))

    def test_attach_store_ids_learns_new_store(self):
        reg = _registry()
        fresh = [dict(_ev("Ítaca", "Lorcana", "2026-11-20T19:00:00+01:00", "scrapers.itaca")),
                 dict(_ev("New Place", "Lorcana", "2026-11-20T19:00:00+01:00", "scrapers.locator_x"),
                      store_hint=store_hint("x", 1, "New Place", "Calle Y, 28002", 40.0, -3.0))]
        self.assertEqual(attach_store_ids(fresh, [], reg), 1)
        self.assertEqual([e["store_id"] for e in fresh], ["itaca", "new-place"])
        self.assertNotIn("store_hint", fresh[1])
        rec = reg.find("New Place")
        self.assertEqual((rec["address"], rec["lat"], rec["lng"]), ("Calle Y, 28002", 40.0, -3.0))


class RegistryReviewTests(unittest.TestCase):
    def test_two_spellings_of_a_new_store_in_one_run_make_one_record(self):
        reg = _registry()
        fresh = [dict(_ev("Dragon Den TCG", "Lorcana", "2026-11-20T19:00:00+01:00", "scrapers.locator_a"),
                      store_hint=store_hint("a", 1, "Dragon Den TCG", "C/ Z 3, 28005")),
                 dict(_ev("DRAGON DEN TCG", "One Piece", "2026-11-21T19:00:00+01:00", "scrapers.locator_b"),
                      store_hint=store_hint("b", 2, "DRAGON DEN TCG", "C/ Z 3, 28005"))]
        self.assertEqual(attach_store_ids(fresh, [], reg), 1)
        self.assertEqual({e["store_id"] for e in fresh}, {"dragon-den-tcg"})
        self.assertEqual({e["store"] for e in fresh}, {"Dragon Den TCG"})
        self.assertEqual(sorted(reg.find("Dragon Den TCG")["aliases"]["external"]), ["a:1", "b:2"])

    def test_unconfirmed_match_is_used_but_not_persisted(self):
        reg = _registry()
        rec = reg.learn("Jupiter Juegos", store_hint("wizards", 999, "Jupiter Juegos Sur", ""))
        self.assertEqual(rec["id"], "jupiter-juegos")
        self.assertEqual(rec["aliases"], {"names": ["Júpiter Central"], "external": []})

    def test_same_spelling_other_postcode_is_a_separate_store(self):
        reg = _registry()
        rec = reg.learn("JUPITER JUEGOS", store_hint("bandai", 354, "JUPITER JUEGOS", "x, 28100"))
        self.assertNotEqual(rec["id"], "jupiter-juegos")

    def test_missing_registry_file_raises(self):
        import pathlib, tempfile
        from shared.store_registry import RegistryMissing
        with self.assertRaises(RegistryMissing):
            StoreRegistry.load(pathlib.Path(tempfile.mkdtemp()) / "stores.json")


class RealRegistryTests(unittest.TestCase):
    """Validates the committed data/stores.json — catches bad hand edits in PRs."""

    def test_registry_is_consistent(self):
        import collections, re
        from shared.store_matching import normalize_name
        reg = StoreRegistry.load()
        ids = [s["id"] for s in reg.stores]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids")
        self.assertTrue(all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i) for i in ids), "bad id slug")
        names = collections.Counter(normalize_name(s["name"]) for s in reg.stores)
        self.assertEqual([n for n, c in names.items() if c > 1], [], "duplicate display names")
        ext = collections.Counter(x for s in reg.stores for x in s["aliases"]["external"])
        self.assertEqual([x for x, c in ext.items() if c > 1], [], "external id on two stores")
        self.assertTrue(all(s.get("city") for s in reg.stores))
        from shared.cities import BY_ID
        self.assertEqual({s["id"]: s["city"] for s in reg.stores if s["city"] not in BY_ID}, {},
                         "store city not in shared/cities.py")


class NameVariantTests(unittest.TestCase):
    def test_variants_are_reported_not_renamed(self):
        fresh = [_ev("MEDUSA COMICS", "Lorcana", "2026-11-20T19:00:00+01:00", "a"),
                 _ev("Medusa Cómics", "Magic: The Gathering", "2026-11-21T19:00:00+01:00", "b")]
        self.assertEqual(find_store_name_variants(fresh), [["MEDUSA COMICS", "Medusa Cómics"]])
        self.assertEqual({e["store"] for e in fresh}, {"MEDUSA COMICS", "Medusa Cómics"})


class BranchTests(unittest.TestCase):
    def test_same_brand_other_postcode_is_a_separate_store(self):
        r = _resolver()
        self.assertEqual(
            r.resolve("bandai", 354, "Júpiter Juegos", "Calle Marques de la Valdavia 95, 28100, ALCOBENDAS"),
            "Júpiter Juegos (28100)",
        )
        self.assertEqual(
            r.resolve("bandai", 1, "Jupiter Juegos", "Calle de la Cruz 10, 28012, Madrid"),
            "Jupiter Juegos",
        )


class KeyTests(unittest.TestCase):
    def test_namespaced_ids_ignore_store_name(self):
        a = {"store": "Medusa Comics", "source_event_id": "wizards:1"}
        b = {"store": "Medusa Cómics", "source_event_id": "wizards:1"}
        self.assertEqual(event_key(a), event_key(b))
        self.assertEqual(event_key({"store": "X", "source_event_id": "77"}), "X|id:77")


if __name__ == "__main__":
    unittest.main()
