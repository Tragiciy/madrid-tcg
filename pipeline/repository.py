"""Reading and writing the persistent JSON files (events, stats, run log, data maps)."""

import json
import logging
import os
import pathlib
from typing import Optional

from pipeline.config import (
    EPOCH_ISO, EVENTS_FILE, RETIRED_STORES_FILE, SOURCE_RUNS_FILE, STATS_FILE,
    STORE_RENAMES_FILE,
)
from pipeline.reconcile import is_locator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class CorruptEventsFile(RuntimeError):
    """events.json exists but cannot be parsed. Never overwrite it."""


def load_existing() -> list[dict]:
    """
    Read events.json (if present) and backfill missing lifecycle fields.

    Raises CorruptEventsFile if the file exists but is unreadable: starting
    from an empty list would silently replace the full history with only
    this run's events.
    """
    if not EVENTS_FILE.exists():
        return []
    try:
        data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CorruptEventsFile(f"Could not read {EVENTS_FILE}: {exc}") from exc
    if not isinstance(data, list):
        raise CorruptEventsFile(f"{EVENTS_FILE} is not a JSON list")

    for e in data:
        # Pre-refactor records had only scraped_at; use it as a sensible
        # backfill for first_seen_at/last_seen_at so we don't pretend
        # everything was discovered today.
        e.setdefault("first_seen_at", e.get("scraped_at") or EPOCH_ISO)
        e.setdefault("last_seen_at", e.get("scraped_at") or e["first_seen_at"])
        e.setdefault("is_active", True)
    return data


def drop_retired_stores(events: list[dict],
                        path: Optional[pathlib.Path] = None) -> list[dict]:
    """Remove events of stores listed in data/retired_stores.json."""
    path = path or RETIRED_STORES_FILE
    if not path.exists():
        return events
    retired = {k for k in json.loads(path.read_text(encoding="utf-8")) if not k.startswith("_")}
    kept = [e for e in events if e.get("store") not in retired]
    if len(kept) != len(events):
        logger.info("Dropped %d events of retired stores %s", len(events) - len(kept), sorted(retired))
    return kept


def apply_store_renames(events: list[dict],
                        path: Optional[pathlib.Path] = None) -> list[dict]:
    """Rename stores per data/store_renames.json so history stays under one name."""
    path = path or STORE_RENAMES_FILE
    if not path.exists():
        return events
    renames = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
               if not k.startswith("_")}
    n = 0
    for e in events:
        new = renames.get(e.get("store"))
        if new:
            e["store"] = new
            n += 1
    if n:
        logger.info("Renamed store on %d existing events", n)
    return events


def write_json_atomic(path: pathlib.Path, data) -> None:
    """Write JSON to a temp file in the same directory, then rename over path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_events(events: list[dict]) -> None:
    write_json_atomic(EVENTS_FILE, events)
    logger.info("Wrote: %s (%d events)", EVENTS_FILE, len(events))


def append_source_runs(scraper_stats: dict, run_at: str,
                       path: Optional[pathlib.Path] = None) -> None:
    """Append one line per source for this run to SOURCE_RUNS_FILE."""
    path = path or SOURCE_RUNS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for name in sorted(scraper_stats):
            ms = scraper_stats[name]
            fh.write(json.dumps({
                "run_at": run_at,
                "source": name,
                "kind": "locator" if is_locator(name) else "store_site",
                "store": ms.get("store"),
                "raw": ms.get("raw"),
                "valid": ms.get("valid"),
                "dropped": ms.get("dropped"),
                "duration_s": ms.get("duration_s"),
                "error": ms.get("error"),
                "anomaly": ms.get("anomaly"),
            }, ensure_ascii=False) + "\n")


def load_previous_stats() -> dict:
    """Read the previous events_stats.json (pre-overwrite). {} on miss."""
    if not STATS_FILE.exists():
        return {}
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read previous %s: %s", STATS_FILE, exc)
        return {}


def _previous_raw_for_store(prev_stats: dict, store: str) -> Optional[int]:
    """
    Pull the previous run's raw_this_run for a given store, tolerating
    older flat-counter shape ({store: int}) too.
    """
    by_store = prev_stats.get("by_store") or {}
    row = by_store.get(store)
    if isinstance(row, dict):
        for key in ("raw_this_run", "previous_raw", "total"):
            v = row.get(key)
            if isinstance(v, int):
                return v
        return None
    if isinstance(row, int):
        return row
    return None


def save_store_registry(registry) -> None:
    """data/stores.json (full registry) + public/stores.json (frontend view)."""
    from shared.store_registry import PUBLIC_FILE, REGISTRY_FILE
    write_json_atomic(REGISTRY_FILE, registry.to_json())
    write_json_atomic(PUBLIC_FILE, registry.public_view())
    logger.info("Wrote: %s (%d stores), %s", REGISTRY_FILE, len(registry.stores), PUBLIC_FILE)
