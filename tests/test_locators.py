import unittest

from pipeline.reconcile import drop_locator_duplicates, event_key, find_store_name_variants, merge_events
from scrapers import locator_bandai, locator_ravensburger, locator_wizards
from shared.locator_common import StoreResolver, to_madrid_iso

NOW = "2026-11-15T12:00:00+01:00"
KNOWN = [{"name": "Jupiter Juegos", "address": "Calle de la Cruz, 10, 28012 Madrid"},
         {"name": "Ítaca", "address": ""}]


def _resolver():
    return StoreResolver(known_stores=KNOWN, overrides={
        "by_name": {"Júpiter Central": "Jupiter Juegos", "Online Shop": None},
    })


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
