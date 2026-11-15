"""
shared/store_registry.py — the store registry (data/stores.json).

One record per physical store, with a stable `id` that never changes once
assigned:

    {"id": "medusa-comics", "name": "Medusa Cómics", "city": "madrid",
     "address": "...", "lat": 40.4, "lng": -3.7, "website": null, "notes": null,
     "origin": "curated" | "auto", "added_at": "2026-11-15",
     "aliases": {"names": ["MEDUSA COMICS"], "external": ["wizards:1234"]}}

`name` is the display name events carry in `store` today. Aliases map the
spellings and upstream ids that official locators use onto the record.
Top-level "ignore" lists names / external ids to drop entirely.

Read-only users (scraper subprocesses) load it through StoreResolver in
shared/locator_common.py. Only the aggregator — a single process — writes
it: StoreRegistry.learn() adds new stores and aliases seen in a run.

The shape maps directly onto future Postgres tables `stores` and
`store_aliases(store_id, kind, value UNIQUE)`.

No network requests and no I/O on import.
"""

import json
import pathlib
import re
import unicodedata
from datetime import date
from typing import Optional

from shared.store_matching import normalize_name

REGISTRY_FILE = pathlib.Path("data/stores.json")
PUBLIC_FILE = pathlib.Path("public/stores.json")
DEFAULT_CITY = "madrid"


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "store"


class StoreRegistry:
    def __init__(self, data: Optional[dict] = None):
        data = data or {}
        self.stores: list[dict] = data.get("stores", [])
        self.ignore = data.get("ignore", {"names": [], "external": []})
        self._reindex()

    # ── loading / saving ──────────────────────────────────────────────
    @classmethod
    def load(cls, path: Optional[pathlib.Path] = None) -> "StoreRegistry":
        path = path or REGISTRY_FILE
        if not path.exists():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def to_json(self) -> dict:
        stores = sorted(self.stores, key=lambda s: (s.get("city") or "", s["id"]))
        for s in stores:
            s["aliases"]["names"] = sorted(set(s["aliases"].get("names", [])))
            s["aliases"]["external"] = sorted(set(s["aliases"].get("external", [])))
        return {
            "_comment": __doc__.strip().split("\n\n")[1],
            "ignore": self.ignore,
            "stores": stores,
        }

    def public_view(self) -> dict:
        """{display name: {id, address, lat, lng, website, notes}} for the frontend."""
        return {
            s["name"]: {k: s.get(k) for k in ("id", "address", "lat", "lng", "website", "notes")
                        if s.get(k) is not None}
            for s in sorted(self.stores, key=lambda s: s["name"].lower())
        }

    # ── lookups ───────────────────────────────────────────────────────
    def _reindex(self) -> None:
        self.by_id = {s["id"]: s for s in self.stores}
        self.by_name = {s["name"]: s for s in self.stores}
        self.by_external: dict[str, dict] = {}
        self.by_norm: dict[str, dict] = {}
        for s in self.stores:
            s.setdefault("aliases", {}).setdefault("names", [])
            s["aliases"].setdefault("external", [])
            for ext in s["aliases"]["external"]:
                self.by_external[ext] = s
            for n in [s["name"], *s["aliases"]["names"]]:
                self.by_norm.setdefault(normalize_name(n), s)
        self.ignored_external = set(self.ignore.get("external", []))
        self.ignored_norm = {normalize_name(n) for n in self.ignore.get("names", [])}

    def find(self, name: str = "", external: Optional[str] = None) -> Optional[dict]:
        if external and external in self.by_external:
            return self.by_external[external]
        if name in self.by_name:
            return self.by_name[name]
        return self.by_norm.get(normalize_name(name or ""))

    def is_ignored(self, name: str = "", external: Optional[str] = None) -> bool:
        return (external in self.ignored_external) or (normalize_name(name or "") in self.ignored_norm)

    def known_stores(self) -> list[dict]:
        """[{name, address}] — the shape shared.store_matching expects."""
        return [{"name": s["name"], "address": s.get("address") or ""} for s in self.stores]

    # ── writes (aggregator only) ──────────────────────────────────────
    def _new_id(self, name: str) -> str:
        base = slugify(name)
        candidate, n = base, 2
        while candidate in self.by_id:
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    def add(self, name: str, *, origin: str, address: Optional[str] = None,
            lat: Optional[float] = None, lng: Optional[float] = None,
            website: Optional[str] = None, notes: Optional[str] = None,
            city: str = DEFAULT_CITY, today: Optional[str] = None) -> dict:
        record = {
            "id": self._new_id(name), "name": name, "city": city,
            "address": address or None, "lat": lat, "lng": lng,
            "website": website, "notes": notes, "origin": origin,
            "added_at": today or date.today().isoformat(),
            "aliases": {"names": [], "external": []},
        }
        self.stores.append(record)
        self._reindex()
        return record

    def learn(self, store_name: str, hint: Optional[dict] = None,
              today: Optional[str] = None) -> dict:
        """
        Record for `store_name`, creating it (origin "auto") if unknown, and
        absorbing what a locator told us: its external id, its spelling of
        the name, and address/coordinates where ours are missing.
        """
        hint = hint or {}
        record = self.by_name.get(store_name) or self.add(
            store_name, origin="auto", address=hint.get("address"),
            lat=hint.get("lat"), lng=hint.get("lng"), today=today,
        )
        changed = False
        ext = hint.get("external")
        if ext and ext not in record["aliases"]["external"] and ext not in self.by_external:
            record["aliases"]["external"].append(ext)
            changed = True
        raw = (hint.get("name") or "").strip()
        owner = self.by_norm.get(normalize_name(raw)) if raw else None
        # Never add a spelling that already names a *different* store (chain
        # branches share brand names: "Júpiter Juegos" ≠ our Jupiter Juegos).
        if (raw and raw != record["name"] and raw not in record["aliases"]["names"]
                and (owner is None or owner is record)):
            record["aliases"]["names"].append(raw)
            changed = True
        for field in ("address", "lat", "lng"):
            if record.get(field) in (None, "") and hint.get(field) not in (None, ""):
                record[field] = hint[field]
                changed = True
        if changed:
            self._reindex()
        return record
