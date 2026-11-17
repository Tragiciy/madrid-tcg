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

from shared.cities import BY_ID as CITY_IDS, DEFAULT_CITY, distance_km
from shared.store_matching import normalize_name

REGISTRY_FILE = pathlib.Path("data/stores.json")
# Two listings with the same name whose postcodes disagree are still one
# store when their pins are this close (a mistyped postcode, a listing that
# kept the old street after a move). Chain branches are kilometres apart.
SAME_PLACE_KM = 1.0


_POSTCODE_RE = re.compile(r"\b(\d{5})\b")


def postcode(address: Optional[str]) -> Optional[str]:
    m = _POSTCODE_RE.search(address or "")
    return m.group(1) if m else None


def near(record: dict, lat, lng) -> bool:
    """Both have coordinates and lie within SAME_PLACE_KM of each other."""
    if None in (record.get("lat"), record.get("lng")) or lat in (None, "") or lng in (None, ""):
        return False
    return distance_km(record["lat"], record["lng"], float(lat), float(lng)) <= SAME_PLACE_KM


def same_place(record: dict, address: Optional[str], lat=None, lng=None) -> bool:
    """False only when both addresses carry postcodes that differ and the
    pins are not within SAME_PLACE_KM. Use it for same-name matches only:
    a fuzzy name match next door may be another shop."""
    theirs, ours = postcode(address), postcode(record.get("address"))
    return not (theirs and ours and theirs != ours) or near(record, lat, lng)


class RegistryMissing(RuntimeError):
    """data/stores.json is absent: never silently start from an empty registry."""


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
        """Raises RegistryMissing if the file is absent and ValueError if it is
        not valid JSON: an empty registry would re-create every store as
        "auto" and lose the curated addresses, websites and notes."""
        path = path or REGISTRY_FILE
        if not path.exists():
            raise RegistryMissing(f"{path} not found")
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

    # ── lookups ───────────────────────────────────────────────────────
    def _reindex(self) -> None:
        self.by_id = {s["id"]: s for s in self.stores}
        self.by_name = {s["name"]: s for s in self.stores}
        self.by_external: dict[str, dict] = {}
        self.by_norm: dict[str, dict] = {}
        self._named: dict[str, list[dict]] = {}
        for s in self.stores:
            s.setdefault("aliases", {}).setdefault("names", [])
            s["aliases"].setdefault("external", [])
            for ext in s["aliases"]["external"]:
                self.by_external[ext] = s
            for n in [s["name"], *s["aliases"]["names"]]:
                norm = normalize_name(n)
                self.by_norm.setdefault(norm, s)
                same = self._named.setdefault(norm, [])
                if not any(x is s for x in same):
                    same.append(s)
        self.ignored_external = set(self.ignore.get("external", []))
        self.ignored_norm = {normalize_name(n) for n in self.ignore.get("names", [])}

    def find(self, name: str = "", external: Optional[str] = None) -> Optional[dict]:
        if external and external in self.by_external:
            return self.by_external[external]
        if name in self.by_name:
            return self.by_name[name]
        return self.by_norm.get(normalize_name(name or ""))

    def named(self, norm: str) -> list[dict]:
        """Every store whose name or an alias normalizes to `norm`; the first
        is by_norm's. Stores in different cities may share a spelling."""
        return self._named.get(norm, [])

    @staticmethod
    def city_of(store: dict) -> str:
        """The store's city id. Records without one predate multi-city
        support and are the default city's (Madrid)."""
        return store.get("city") or DEFAULT_CITY

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

    def _fits(self, record: dict, hint: dict) -> bool:
        """Can `record` be the store a hint describes? It is in the hint's
        city, or located inside that city's radius (overlapping radii: the
        record keeps its own city), and not a different place (same_place).
        The same scope StoreResolver matches names in."""
        city = hint.get("city")
        in_city = (city is None or self.city_of(record) == city
                   or (record.get("lat") is not None and record.get("lng") is not None
                       and CITY_IDS[city].contains(record["lat"], record["lng"])))
        return in_city and same_place(record, hint.get("address"), hint.get("lat"), hint.get("lng"))

    def learn(self, store_name: str, hint: Optional[dict] = None,
              today: Optional[str] = None) -> dict:
        """
        Record for `store_name`, creating it (origin "auto") if unknown, and
        absorbing what a locator told us: its external id, its spelling of
        the name, and address/coordinates where ours are missing.

        Lookup is by exact name, then by normalized spelling — so a new
        store listed as "Dragon Den TCG" by one locator and "DRAGON DEN TCG"
        by another in the same run becomes one record — unless the two
        addresses carry different postcodes (chain branches).

        Aliases (external id, spelling) are only recorded when the hint's
        postcode confirms the record's. A match made without that evidence
        (no address, fuzzy name) is used for this run but never persisted,
        so it is re-decided next run instead of being glued on for good.
        To undo a wrong alias, delete it from `aliases` in data/stores.json.

        Postcodes that disagree still mean the same store when the pins are
        within SAME_PLACE_KM (same_place); the alias is then recorded, so the
        next run resolves the listing by its external id.

        City: a store created here gets the hint's `city` (the city whose
        locator search found it), else DEFAULT_CITY. A known store keeps its
        registry city whatever search found it (overlapping radii). A store
        in another city is never a match, not even by exact name: the new
        store gets the suffix StoreResolver would give it next run,
        "<name> (<postcode or city name>)". The same goes for a same-named
        store in the same city whose postcode and pin both disagree (a
        branch), unless the hint's external id is already that store's.
        """
        hint = hint or {}
        city = hint.get("city")
        if city is not None and city not in CITY_IDS:
            raise ValueError(f"store hint for {store_name!r} has unknown city {city!r}")
        theirs = postcode(hint.get("address"))
        ext = hint.get("external")
        record = self.by_name.get(store_name)
        if (record is not None and not (ext and self.by_external.get(ext) is record)
                and not self._fits(record, hint)):
            suffix = theirs or (CITY_IDS[city].name if city else None)
            if suffix is None:
                raise ValueError(f"{store_name!r}: name taken by another store and no "
                                 f"postcode or city to tell them apart")
            store_name = f"{store_name} ({suffix})"
            record = self.by_name.get(store_name)
        if record is None:
            record = next((s for s in self.named(normalize_name(store_name))
                           if self._fits(s, hint)), None)
        if record is None:
            record = self.add(
                store_name, origin="auto", address=hint.get("address"),
                lat=hint.get("lat"), lng=hint.get("lng"), today=today,
                city=city or DEFAULT_CITY,
            )
        ours = postcode(record.get("address"))
        confirmed = bool(theirs) and (ours is None or ours == theirs
                                      or near(record, hint.get("lat"), hint.get("lng")))
        if not confirmed:
            return record
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
