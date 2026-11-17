"""
scrapers/locator_melee.py — tournaments from Melee.gg organizers.

Melee is the official event platform for Cyberpunk TCG and is also used by
stores for Star Wars: Unlimited (and some Magic). Its global search has no
location filter and stops at 250 results, so we read each Madrid store's
organizer page instead.

Which organizers to read: every "melee:<organization id>" alias in the store
registry (data/stores.json). tools/find_melee_organizers.py proposes them.
Times come in UTC ("...Z").
"""

import logging
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

# Melee's game names → ours. Games we don't list are skipped.
GAMES = {
    "cyberpunk tcg": "Cyberpunk TCG",
    "cyberpunktcg": "Cyberpunk TCG",
    "star wars: unlimited": "Star Wars: Unlimited",
    "starwarsunlimited": "Star Wars: Unlimited",
    "magic: the gathering": "Magic: The Gathering",
    "magicthegathering": "Magic: The Gathering",
    "riftbound": "Riftbound",
    "disney lorcana": "Lorcana",
    "lorcana": "Lorcana",
    "pokemon": "Pokémon",
    "pokémon": "Pokémon",
    "one piece": "One Piece",
    "flesh and blood": "Flesh and Blood",
}

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
        start_iso = to_madrid_iso(t.get("startDate") or "")
        if not game or not start_iso or not t.get("name") or not t.get("id"):
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
    for org_id in org_ids:
        rows = fetch_scheduled(sess, org_id)
        events += parse_tournaments(rows, org_id, resolver)
    logger.info("%s: %d organizers → %d events", STORE, len(org_ids), len(events))
    return events


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
