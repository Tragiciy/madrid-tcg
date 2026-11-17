"""Offline tests for the Barcelona-area store scrapers (trimmed real pages,
saved 2026-11-17): El Encuentro (EventON), Origen Juegos (Astro island) and
Nikochan Arena (server-rendered HTML). Plus where their events land: the
Barcelona registry records, and the locator copies they replace."""

import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from pipeline.reconcile import attach_store_ids, drop_locator_duplicates
from scrapers import el_encuentro, nikochan_arena, origen_juegos
from shared import eventon
from shared.store_registry import StoreRegistry

MAD = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=MAD)
FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _month() -> dict:
    return json.loads(_read("eventon_el_encuentro_month.json"))


class EventonTests(unittest.TestCase):
    def test_calendar_page_gives_shortcode_and_nonces(self):
        sc, params = eventon.parse_calendar_page(_read("eventon_el_encuentro_page.html"))
        self.assertEqual((sc["fixed_month"], sc["fixed_year"]), ("11", "2026"))
        self.assertTrue(params["n"] and params["nonce"])

    def test_page_without_calendar_raises(self):
        with self.assertRaises(eventon.EventonError):
            eventon.parse_calendar_page("<html><body>Mantenimiento</body></html>")

    def test_answer_uses_real_utc_epochs_and_permalinks(self):
        evs = eventon.parse_events(_month())
        rift = next(e for e in evs if e.id == 7877)
        self.assertEqual(rift.title, "Riftbound – Nexus Nights")   # &#8211; unescaped
        # unix_start is UTC; the site shows 17:30 (event_start_unix, the
        # wall clock written as UTC, would give 18:30).
        self.assertEqual(rift.start.isoformat(), "2026-11-18T17:30:00+01:00")
        self.assertEqual(rift.end.isoformat(), "2026-11-18T21:00:00+01:00")
        self.assertEqual(rift.url, "https://elencuentro.infrecuentes.es/evento/riftbound-nexus-nights-4/")
        self.assertIn("Nexus", rift.description)

    @staticmethod
    def _one(local: str, utc_gap_h: float) -> dict:
        """An answer with one event at wall clock `local`, whose unix_start
        is `utc_gap_h` hours before the wall clock written as UTC."""
        wall = int(datetime.fromisoformat(local + "+00:00").timestamp())
        gap = int(utc_gap_h * 3600)
        return {"status": "GOOD", "html": "", "json": [{
            "ID": 1, "ri": "0", "_ID": "1_0", "event_title": "Riftbound – Nexus Nights",
            "event_start_unix": wall, "event_end_unix": wall + 3 * 3600,
            "unix_start": wall - gap, "unix_end": wall + 3 * 3600 - gap}]}

    def test_wall_clock_in_winter_and_summer(self):
        winter = eventon.parse_events(self._one("2026-11-18T17:30:00", 1))[0]
        summer = eventon.parse_events(self._one("2027-04-14T17:30:00", 2))[0]
        self.assertEqual(winter.start.isoformat(), "2026-11-18T17:30:00+01:00")
        self.assertEqual(summer.start.isoformat(), "2027-04-14T17:30:00+02:00")
        self.assertEqual(summer.end.isoformat(), "2027-04-14T20:30:00+02:00")

    def test_stale_gmt_offset_across_dst_keeps_the_wall_clock(self):
        # A March scrape (gmt_offset +1) of an April event: unix_start is
        # an hour off; the store's wall clock still wins.
        ev = eventon.parse_events(self._one("2027-04-14T17:30:00", 1))[0]
        self.assertEqual(ev.start.isoformat(), "2027-04-14T17:30:00+02:00")
        # An October scrape (+2) of a November event, likewise.
        ev = eventon.parse_events(self._one("2026-11-18T17:30:00", 2))[0]
        self.assertEqual(ev.start.isoformat(), "2026-11-18T17:30:00+01:00")

    def test_non_madrid_offset_raises(self):
        for gap in (0, 5, -1):  # a site set to UTC, to America, …
            with self.assertRaises(eventon.EventonError):
                eventon.parse_events(self._one("2026-11-18T17:30:00", gap))

    def test_bad_answer_raises(self):
        with self.assertRaises(eventon.EventonError):
            eventon.parse_events({"status": "BAD", "msg": "nonce"})

    def test_fetch_posts_current_then_next_month(self):
        page = mock.Mock(text=_read("eventon_el_encuentro_page.html"), cookies={})
        answers = [_month(), {"status": "GOOD", "json": [], "html": "", "SC": {"fixed_month": "12"}}]
        posted = []

        def post(url, data, **kw):
            posted.append((url, data["direction"], data["nonce"], data["shortcode[fixed_month]"]))
            return mock.Mock(json=lambda: answers[len(posted) - 1], raise_for_status=lambda: None)

        with mock.patch("requests.Session.post", side_effect=post):
            evs = eventon.fetch_eventon_events(el_encuentro.CALENDAR_URL, months=2,
                                               get=lambda *a, **k: page, sleep=lambda s: None)
        self.assertEqual(len(evs), len(_month()["json"]))
        self.assertEqual([p[1] for p in posted], ["none", "next"])
        self.assertTrue(all(p[0] == "https://elencuentro.infrecuentes.es/?evo-ajax=eventon_get_events" for p in posted))
        self.assertTrue(posted[0][2])  # the page's nonce is sent back


class ElEncuentroTests(unittest.TestCase):
    def setUp(self):
        self.events = el_encuentro.to_events(eventon.parse_events(_month()), NOW)

    def test_tcg_events_from_yesterday_on(self):
        got = [(e["datetime_start"], e["game"], e["title"]) for e in self.events]
        self.assertEqual(got, [
            ("2026-11-18T17:30:00+01:00", "Magic: The Gathering", "MTG Commander (Bracket 2)"),
            ("2026-11-18T17:30:00+01:00", "Riftbound", "Riftbound – Nexus Nights"),
            ("2026-11-18T18:00:00+01:00", "Gundam Card Game", "Gundam TCG – Store Tournament"),
            ("2026-11-19T18:00:00+01:00", "Yu-Gi-Oh!", "Yu-Gi-Oh! – Formato EDISON"),
            ("2026-11-19T18:00:00+01:00", "Pokémon", "Pokémon League – Standard"),
        ])
        # Skipped: the 4 Nov Riftbound night (history) and Dragon Ball
        # Fusion World (not a tracked game).

    def test_schema(self):
        ev = self.events[0]
        self.assertEqual(ev["store"], el_encuentro.STORE)
        self.assertEqual(ev["format"], "Commander")
        self.assertEqual(ev["source_event_id"], "elencuentro:7920:0")
        self.assertEqual(ev["language"], "es")
        self.assertEqual(datetime.fromisoformat(ev["datetime_start"]).utcoffset().total_seconds(), 3600)

    def test_empty_calendar_fails_loudly(self):
        with mock.patch.object(el_encuentro, "fetch_eventon_events", return_value=[]):
            with self.assertRaises(RuntimeError):
                el_encuentro.scrape()


class OrigenJuegosTests(unittest.TestCase):
    def setUp(self):
        self.html = _read("origen_juegos_home.html")
        self.events = origen_juegos.parse_page(self.html, NOW)

    def test_island_events_as_madrid_wall_clock(self):
        got = [(e["datetime_start"], e["game"], e["title"]) for e in self.events]
        self.assertEqual(got, [
            ("2026-11-19T17:00:00+01:00", "Pokémon", "Liga Pokémon"),
            ("2026-11-19T18:00:00+01:00", "Digimon", "Torneo 4fun Digimon"),
            ("2026-11-20T17:30:00+01:00", "Star Wars: Unlimited", "Torneo semanal SWU"),
            ("2026-11-21T10:00:00+01:00", "Riftbound", "Vilemaw's Cup V - Origen"),
            ("2026-11-21T17:00:00+01:00", "Lorcana", "Torneo semanal Lorcana"),
            ("2026-11-21T17:30:00+01:00", "One Piece", "Presentación OP-18"),
            ("2026-11-28T17:00:00+01:00", "Yu-Gi-Oh!", "Torneo semanal Genesys"),  # code "ygo"
        ])
        # Skipped: October's Pre-Rift Radiance (history).

    def test_stable_ids_and_prerelease_type(self):
        op = next(e for e in self.events if e["game"] == "One Piece")
        self.assertEqual(op["source_event_id"], "origen:2940869c-e69a-4211-bfd7-21690ac62eb2")
        self.assertEqual(op["format"], "Prerelease")
        self.assertEqual(op["store"], origen_juegos.STORE)

    def test_board_games_and_unknown_codes_are_skipped(self):
        props = origen_juegos.calendar_props(self.html)
        props["events"][0]["game"] = "board"      # "Juegos de mesa"
        props["events"][1]["game"] = "dbfw"       # not in props.games
        with mock.patch.object(origen_juegos, "calendar_props", return_value=props):
            titles = [e["title"] for e in origen_juegos.parse_page(self.html, NOW)]
        self.assertEqual(len(titles), len(self.events) - 1)  # [0] was already history

    def test_missing_games_list_fails_loudly(self):
        props = origen_juegos.calendar_props(self.html)
        for broken in ({k: v for k, v in props.items() if k != "games"},   # prop gone
                       dict(props, games=[{"slug": g["id"], "label": g["name"]} for g in props["games"]])):  # keys renamed
            with mock.patch.object(origen_juegos, "calendar_props", return_value=broken):
                with self.assertRaises(RuntimeError):
                    origen_juegos.parse_page(self.html, NOW)

    def test_most_codes_unresolved_fails_loudly(self):
        # Codes renamed ("rift" → "riftbound", …) while the games list keeps
        # the old ids: only a minority would still resolve.
        props = origen_juegos.calendar_props(self.html)
        for ev in props["events"][:6]:
            ev["game"] = ev["game"] + "-v2"
        with mock.patch.object(origen_juegos, "calendar_props", return_value=props):
            with self.assertRaises(RuntimeError):
                origen_juegos.parse_page(self.html, NOW)

    def test_page_without_calendar_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            origen_juegos.parse_page("<html><body><astro-island props='{}'></astro-island></body></html>", NOW)


class NikochanArenaTests(unittest.TestCase):
    def setUp(self):
        self.events = nikochan_arena.parse_page(_read("nikochan_calendario.html"), NOW)

    def test_cards_become_events(self):
        got = [(e["datetime_start"], e["game"], e["title"]) for e in self.events]
        self.assertEqual(got, [
            ("2026-11-17T18:00:00+01:00", "Riftbound", "Riftbound – NEXUS NIGHT"),
            ("2026-11-18T17:15:00+01:00", "Digimon", "Torneo Digimon"),       # game from the logo
            ("2026-11-20T17:15:00+01:00", "One Piece", "Torneo One Piece"),
            ("2026-11-29T10:00:00+01:00", "Riftbound", "Riftbound – NEXUS NIGHT"),
            ("2026-11-29T17:15:00+01:00", "One Piece", "Torneo One Piece"),
            ("2026-12-12T10:00:00+01:00", "Riftbound", "Riftbound – NEXUS NIGHT"),
            ("2027-01-23T10:00:00+01:00", "Yu-Gi-Oh!", "Torneo YuGiOh!"),
            ("2027-01-23T10:00:00+01:00", "Riftbound", "Riftbound – PRE-RIFT LEGACY"),
        ])

    def test_wizards_join_cards_and_untracked_games_are_skipped(self):
        # Magic cards linking a Wizards join code are the Wizards locator's;
        # Dragon Ball is not a tracked game.
        self.assertNotIn("Magic: The Gathering", {e["game"] for e in self.events})
        self.assertFalse(any("Dragon" in e["title"] for e in self.events))

    def test_sign_up_link_is_the_source_url(self):
        self.assertEqual(self.events[3]["source_url"], "https://playriftbound.com/events/117468466850407382")
        self.assertEqual(self.events[1]["source_url"], nikochan_arena.PAGE_URL)
        self.assertEqual(self.events[-1]["format"], "Prerelease")

    def test_page_without_days_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            nikochan_arena.parse_page("<html><body>Error 500</body></html>", NOW)

    def test_placeholder_5am_time_is_skipped_and_logged(self):
        # 12 Dec "SELLADO - MAGIC PRESENTS: MARVEL DOUBLE ISSUE" says
        # "Hora: 5:00" (Wizards: 17:00). Its card has no Wizards link yet.
        html = _read("nikochan_calendario.html")
        self.assertIn("MARVEL DOUBLE ISSUE", html)
        with self.assertLogs(nikochan_arena.logger, "WARNING") as logs:
            events = nikochan_arena.parse_page(html, NOW)
        self.assertFalse(any(e["datetime_start"][11:13] < "07" for e in events))
        self.assertFalse(any("MARVEL" in e["title"] for e in events))
        self.assertTrue(any("placeholder time" in line for line in logs.output))

    def _mangled(self, old: str, new: str) -> str:
        html = _read("nikochan_calendario.html")
        self.assertIn(old, html)
        return html.replace(old, new)

    def test_unreadable_cards_fail_loudly(self):
        # The time wording changes: day blocks and cards are still there,
        # but no card has a start time any more.
        with self.assertRaises(RuntimeError):
            nikochan_arena.parse_page(self._mangled("<strong>Hora:</strong>", "<strong>Horario:</strong>"), NOW)
        # The logos' alt texts change: 9 of 13 cards unreadable, while the
        # 4 Magic cards (h3 label) still parse.
        html = self._mangled('alt="Digimon"', 'alt="logo"').replace('alt="One Piece"', 'alt="logo"')
        html = html.replace('alt="Riftbound"', 'alt="logo"').replace('alt="YuGiOh!"', 'alt="logo"')
        html = html.replace('alt="Dragon Ball"', 'alt="logo"')
        with self.assertRaises(RuntimeError):
            nikochan_arena.parse_page(html, NOW)

    def test_ids_survive_renames(self):
        # A Riftbound card keeps its playriftbound id; other cards are keyed by
        # their slot, so "[PENDIENTE]" → a real name keeps the same event.
        by_start = {(e["datetime_start"], e["game"]): e["source_event_id"] for e in self.events}
        self.assertEqual(by_start[("2026-11-29T10:00:00+01:00", "Riftbound")], "playriftbound:117468466850407382")
        self.assertEqual(by_start[("2027-01-23T10:00:00+01:00", "Yu-Gi-Oh!")], "nikochan:2027-01-23T10:00:yu-gi-oh")
        renamed = nikochan_arena.parse_page(self._mangled("[PENDIENTE]", "GENESYS CUP"), NOW)
        ygo = next(e for e in renamed if e["game"] == "Yu-Gi-Oh!")
        self.assertEqual(ygo["title"], "YuGiOh! – GENESYS CUP")
        self.assertEqual(ygo["source_event_id"], "nikochan:2027-01-23T10:00:yu-gi-oh")
        self.assertEqual(len({e["source_event_id"] for e in self.events}), len(self.events))


class BarcelonaWiringTests(unittest.TestCase):
    """The scrapers' events take their store's registry city: Barcelona."""

    def test_every_new_store_is_a_barcelona_registry_record(self):
        registry = StoreRegistry.load()
        for mod in (el_encuentro, origen_juegos, nikochan_arena):
            record = registry.find(mod.STORE)
            self.assertIsNotNone(record, mod.STORE)
            self.assertEqual(record["name"], mod.STORE)
            self.assertEqual(registry.city_of(record), "barcelona", mod.STORE)

    def test_origen_events_land_in_barcelona(self):
        registry = StoreRegistry.load()
        fresh = origen_juegos.parse_page(_read("origen_juegos_home.html"), NOW)
        for e in fresh:
            e["source"] = "scrapers.origen_juegos"
        before = len(registry.stores)
        attach_store_ids(fresh, [], registry)
        self.assertEqual(len(registry.stores), before)  # no new "auto" store
        self.assertEqual({(e["store_id"], e["city"]) for e in fresh}, {("origen-juegos", "barcelona")})

    def test_store_listing_replaces_the_locator_copy(self):
        registry = StoreRegistry.load()
        fresh = origen_juegos.parse_page(_read("origen_juegos_home.html"), NOW)
        for e in fresh:
            e["source"] = "scrapers.origen_juegos"
        # Bandai TCG+ lists the same OP-18 release 15 minutes earlier.
        locator = {
            "store": "Origen Juegos", "game": "One Piece", "source": "scrapers.locator_bandai_onepiece",
            "title": "ONE PIECE CARD GAME OP-18 Release Event",
            "datetime_start": "2026-11-21T17:15:00+01:00", "source_event_id": "bandai:999",
        }
        events = fresh + [locator]
        attach_store_ids(events, [], registry)
        kept, dropped = drop_locator_duplicates(events, [], NOW.isoformat())
        self.assertEqual(dropped, 1)
        self.assertNotIn(locator, kept)
        self.assertEqual(len(kept), len(fresh))


if __name__ == "__main__":
    unittest.main()
