"""
scrapers/locator_melee.py — tournaments from Melee.gg organizers.

Melee is the official event platform for Cyberpunk TCG and is also used by
stores for Star Wars: Unlimited (only games without an official locator
are read; see GAMES). Its global search has no
location filter and stops at 250 results, so we read each Madrid store's
organizer page instead.

Which organizers to read: every "melee:<organization id>" alias in the store
registry (data/stores.json). tools/find_melee_organizers.py proposes them.
Times come in UTC ("...Z").
"""

import logging
import re
from typing import Optional

from shared.locator_common import (
    StoreResolver, make_event, request_json, session, store_hint, to_madrid_iso, window,
)
from shared.store_registry import StoreRegistry

logger = logging.getLogger(__name__)

STORE = "Melee Organizers"
SOURCE = "melee"
BASE_URL = "https://melee.gg"
SCHEDULED_URL = BASE_URL + "/Organization/GetScheduledTournaments/{org_id}"
ORGANIZATIONS_URL = BASE_URL + "/Organization/GetOrganizations"
PAGE_SIZE = 100
MAX_PAGES = 10

# Melee's game names → ours. Games we don't list are skipped — including
# Magic, Lorcana, Pokémon, One Piece and FaB: their official locators already
# list these events, and drop_locator_duplicates only lets store scrapers
# claim a slot, so a Melee copy of a locator event would be published twice.
GAMES = {
    "cyberpunk tcg": "Cyberpunk TCG",
    "cyberpunktcg": "Cyberpunk TCG",
    "star wars: unlimited": "Star Wars: Unlimited",
    "starwarsunlimited": "Star Wars: Unlimited",
    "riftbound": "Riftbound",
}

# Rows whose status mentions any of these are not upcoming events.
SKIP_STATUSES = ("cancel", "ended", "complete", "finished")

# Organizers sometimes save a tournament with no time: it comes back as
# exactly midnight UTC (01:00/02:00 in Madrid), which is never a real start
# here. Such rows are dropped rather than published at 01:00 next to the
# store's own listing of the same event.
_PLACEHOLDER_TIME = re.compile(r"T00:00:00(\.0+)?Z?$")

# DataTables form fields Melee's endpoints expect.
_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def _game(description: Optional[str]) -> Optional[str]:
    return GAMES.get((description or "").strip().lower())


def organization_ids(registry: Optional[StoreRegistry] = None) -> list[str]:
    """Melee organization ids aliased to stores in the registry."""
    registry = registry if registry is not None else StoreRegistry.load()
    prefix = SOURCE + ":"
    return sorted({ext[len(prefix):] for ext in registry.by_external if ext.startswith(prefix)})


def fetch_scheduled(sess, org_id: str) -> list[dict]:
    """An organizer's upcoming tournaments (Melee's 'Scheduled' tab)."""
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        data = request_json(sess, "POST", SCHEDULED_URL.format(org_id=org_id), headers=_HEADERS,
                            data={"draw": page + 1, "start": page * PAGE_SIZE, "length": PAGE_SIZE})
        batch = data.get("data") or []
        rows += batch
        if not batch or len(rows) >= int(data.get("recordsTotal") or 0):
            break
    return rows


def search_organizations(sess, query: str, limit: int = 10) -> list[dict]:
    """Organizers whose name contains `query` (for tools/find_melee_organizers.py)."""
    columns = {}
    for i, name in enumerate(("TournamentCount", "Name")):
        columns.update({
            f"columns[{i}][data]": name, f"columns[{i}][name]": name,
            f"columns[{i}][searchable]": "true" if name == "Name" else "false",
            f"columns[{i}][orderable]": "true",
            f"columns[{i}][search][value]": "", f"columns[{i}][search][regex]": "false",
        })
    data = request_json(sess, "POST", ORGANIZATIONS_URL, headers=_HEADERS, pause_s=0.5, data={
        "draw": 1, "start": 0, "length": limit, **columns,
        "order[0][column]": 0, "order[0][dir]": "desc",
        "search[value]": query, "search[regex]": "false",
    })
    return data.get("data") or []


def parse_tournaments(rows: list[dict], org_id: str, resolver: StoreResolver) -> list[dict]:
    start, end = window()
    out: list[dict] = []
    for t in rows:
        game = _game(t.get("gameDescription"))
        raw_start = t.get("startDate") or ""
        start_iso = to_madrid_iso(raw_start)
        if not game or not start_iso or not t.get("name") or not t.get("id"):
            continue
        status = (t.get("status") or "").lower()
        if any(word in status for word in SKIP_STATUSES):
            continue
        if _PLACEHOLDER_TIME.search(raw_start):
            logger.info("%s: skipping %s (no start time set: %s)", STORE, t.get("id"), raw_start)
            continue
        if not (start.isoformat() <= start_iso[:10] <= end.isoformat()):
            continue
        org_name = (t.get("organizationName") or "").strip()
        store = resolver.resolve(SOURCE, org_id, org_name)
        if store is StoreResolver.SKIP:
            continue
        out.append(make_event(
            title=t["name"].strip(),
            store=store,
            game=game,
            start_iso=start_iso,
            source_event_id=f"{SOURCE}:{t['id']}",
            source_url=f"{BASE_URL}/Tournament/View/{t['id']}",
            format_hint=t.get("formatString") or "",
            registered=t.get("enrolledPlayerCount"),
            hint=store_hint(SOURCE, org_id, org_name),
        ))
    return out


def scrape() -> list[dict]:
    registry = StoreRegistry.load()
    resolver = StoreResolver(registry)
    sess = session()
    events: list[dict] = []
    org_ids = organization_ids(registry)
    # A wrong or moved organizer id answers 200 with no rows, exactly like an
    # organizer with nothing scheduled — list them so a bad alias is visible.
    empty: list[str] = []
    for org_id in org_ids:
        rows = fetch_scheduled(sess, org_id)
        if not rows:
            empty.append(org_id)
        events += parse_tournaments(rows, org_id, resolver)
    logger.info("%s: %d organizers → %d events; nothing scheduled at %d: %s",
                STORE, len(org_ids), len(events), len(empty), ", ".join(empty) or "-")
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
