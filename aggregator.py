"""
aggregator.py — discover scrapers, run them, merge with persisted events.

Storage model
-------------
events.json holds the **full historical record**. Each daily run:

  1. Loads existing events from disk.
  2. Runs every scraper.
  3. Merges fresh events onto the existing index by a stable key.
     - Existing event seen again: fields refreshed, first_seen_at kept.
     - New event: inserted with first_seen_at = last_seen_at = now.
     - Existing event NOT seen this run: marked is_active=False and hidden
       from the frontend. If a future event is absent for 3 consecutive runs
       it is hard-deleted (likely cancelled). Past events are kept as history.
     - Reschedule detection: if a fresh event matches an existing record by
       store+title+location (but different datetime), the record is re-keyed
       and first_seen_at is preserved.
  4. Writes the merged list (sorted by datetime_start).
  5. Prints a lightweight validation report (counts only, no extra IO).

Run:
    python aggregator.py
"""

import importlib
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from shared.scraper_keywords import (
    extract_format_for_event,
    extract_game_from_patterns,
)

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Madrid")
SCRAPERS_DIR = pathlib.Path("scrapers")
EVENTS_FILE = pathlib.Path("public/events.json")
STATS_FILE = pathlib.Path("public/events_stats.json")

# Sentinel for events that existed before lifecycle fields were added.
EPOCH_ISO = "1970-01-01T00:00:00+00:00"

# Threshold for the "count dropped sharply" warning (validation report).
COUNT_DROP_THRESHOLD = 0.7
# Per-store anomaly threshold: scraped raw count < 70% of previous run
# triggers a "sharp_drop" flag. Same threshold, separate constant so the
# two policies can drift later if needed.
SHARP_DROP_THRESHOLD = 0.7

# A store whose scraper keeps returning 0 events with no upcoming events left
# on record is flagged "stale" once its last sighting is this old. Nothing is
# protected (there is nothing left to delete), it only raises an alert.
STALE_AFTER_DAYS = 14

# Append-only per-source run log: one JSON line per scraper per run. The
# baseline for source health over time (and the future `source_runs` table).
SOURCE_RUNS_FILE = pathlib.Path("data/source_runs.jsonl")
RETIRED_STORES_FILE = pathlib.Path("data/retired_stores.json")
STORE_RENAMES_FILE = pathlib.Path("data/store_renames.json")
# Abort if upcoming active events fall below this share of the previous
# run's: history-inclusive totals hide losing every future event.
UPCOMING_DROP_THRESHOLD = 0.5
UPCOMING_GUARD_MIN = 50

# Each scraper runs in its own subprocess with this wall-clock budget.
SCRAPER_TIMEOUT_S = int(os.environ.get("SCRAPER_TIMEOUT_S", "300"))
# Scrapers are I/O bound and hit different hosts, so run several at once.
SCRAPER_WORKERS = int(os.environ.get("SCRAPER_WORKERS", "6"))


# ---------------------------------------------------------------------------
# Scraper discovery
# ---------------------------------------------------------------------------

def _discover_scrapers() -> list[str]:
    """Module names of every *.py inside scrapers/ except __init__."""
    return [
        f"scrapers.{p.stem}"
        for p in sorted(SCRAPERS_DIR.glob("*.py"))
        if p.stem != "__init__"
    ]


def _run_scraper(module_name: str) -> tuple[list[dict], Optional[str], Optional[str]]:
    """
    Import and call scrape(). Returns (events, error, store_constant).

    Import errors are caught here too, so a broken scraper module can never
    abort the whole run. store_constant is the module's STORE attribute
    (None if the module failed to import or doesn't define it).
    """
    store = None
    try:
        module = importlib.import_module(module_name)
        store = getattr(module, "STORE", None)
        events = module.scrape()
        logger.info("%s → %d events", module_name, len(events))
        return events, None, store
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.error("Scraper %s failed: %s", module_name, msg)
        return [], msg, store


_STORE_RE = re.compile(r'^STORE\s*=\s*["\'](.+?)["\']', re.MULTILINE)


def _store_from_source(module_name: str) -> Optional[str]:
    """Read the STORE constant without importing (for crashed/timed-out runs)."""
    path = SCRAPERS_DIR / f"{module_name.rsplit('.', 1)[-1]}.py"
    try:
        m = _STORE_RE.search(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def _run_scraper_isolated(
    module_name: str, timeout_s: int = SCRAPER_TIMEOUT_S,
) -> tuple[list[dict], Optional[str], Optional[str]]:
    """
    Run one scraper via tools.run_scraper in a child process.

    Same return shape as _run_scraper. A timeout, crash or unreadable result
    becomes an error, so the store is protected from deletion like any other
    failed scraper.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "result.json"
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "tools.run_scraper", module_name, str(out)],
                timeout=timeout_s,
                stdout=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            msg = f"TimeoutError: no result after {timeout_s}s"
            logger.error("Scraper %s failed: %s", module_name, msg)
            return [], msg, _store_from_source(module_name)
        try:
            result = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            msg = f"ChildProcessError: exit {proc.returncode}, no result ({exc})"
            logger.error("Scraper %s failed: %s", module_name, msg)
            return [], msg, _store_from_source(module_name)
    return (
        result.get("events") or [],
        result.get("error"),
        result.get("store") or _store_from_source(module_name),
    )


def run_all_scrapers(module_names: list[str]) -> list[tuple]:
    """
    Run scrapers concurrently. Returns (events, error, store, duration_s)
    per module, in input order.
    """
    def timed(module_name: str) -> tuple:
        t0 = time.monotonic()
        events, error, store = _run_scraper_isolated(module_name)
        return events, error, store, round(time.monotonic() - t0, 1)

    with ThreadPoolExecutor(max_workers=SCRAPER_WORKERS) as pool:
        return list(pool.map(timed, module_names))


# ---------------------------------------------------------------------------
# Game / format normalisation
# ---------------------------------------------------------------------------

GAME_CANONICAL: dict = {
    # Magic
    "magic": "Magic: The Gathering",
    "magic: the gathering": "Magic: The Gathering",
    "mtg": "Magic: The Gathering",
    # Pokémon
    "pokemon": "Pokémon",
    "pokémon": "Pokémon",
    # One Piece
    "one piece": "One Piece",
    # Digimon
    "digimon": "Digimon",
    # Lorcana
    "lorcana": "Lorcana",
    # Star Wars
    "star wars": "Star Wars: Unlimited",
    "star wars unlimited": "Star Wars: Unlimited",
    "swu": "Star Wars: Unlimited",
    # Yu-Gi-Oh
    "yu-gi-oh": "Yu-Gi-Oh!",
    "yugioh": "Yu-Gi-Oh!",
    "yu-gi-oh!": "Yu-Gi-Oh!",
    # Flesh and Blood
    "flesh and blood": "Flesh and Blood",
    "flesh & blood": "Flesh and Blood",
    "fab": "Flesh and Blood",
    # Weiss Schwarz
    "weiss": "Weiß Schwarz",
    "weiss schwarz": "Weiß Schwarz",
    "weiß schwarz": "Weiß Schwarz",
    # Final Fantasy TCG. "final fantasy tcg" must come before
    # "final fantasy" so the longer match wins; both normalise to the
    # same canonical name.
    "final fantasy tcg": "Final Fantasy TCG",
    "fftcg":             "Final Fantasy TCG",
    "final fantasy":     "Final Fantasy TCG",
    # Title-driven
    "liga star wars": "Star Wars: Unlimited",
    "rcq": "Magic: The Gathering",
    # "Pre-Rift" is the Riftbound: Vendetta prerelease.
    "pre-rift": "Riftbound",
    "pre rift": "Riftbound",
    "presentacion riftbound": "Riftbound",
    "presentación riftbound": "Riftbound",
    "riftbound unleashed": "Riftbound",
    "riftbound": "Riftbound",
    "nexus nights": "Riftbound",
    "nexus night": "Riftbound",
    "noob nexus night": "Riftbound",
    "naruto mythos": "Naruto Mythos",
}

ALLOWED_GAMES: set = {
    "Magic: The Gathering",
    "Pokémon",
    "One Piece",
    "Digimon",
    "Yu-Gi-Oh!",
    "Lorcana",
    "Star Wars: Unlimited",
    "Flesh and Blood",
    "Weiß Schwarz",
    "Riftbound",
    "Naruto Mythos",
    "Final Fantasy TCG",
}

# Keep in sync with FORMAT_KEYWORDS values in scrapers/*.py.
ALLOWED_FORMATS: set = {
    # MTG-rich vocabulary
    "Standard", "Pioneer", "Modern", "Legacy", "Pauper", "Vintage",
    "Commander", "cEDH",
    # Flesh and Blood differentiators
    "Blitz", "Living Legend",
    # Pokémon wider pool
    "Expanded",
    # YGO alternate
    "Speed Duel",
    # One Piece limited pool
    "East Blue",
    # Lorcana wider pool
    "Infinity",
    # SWU 2-leader
    "Twin Suns",
    # Limited / pre-launch
    "Sealed", "Draft", "Prerelease",
    # Note: Weekly / League / Casual / Store Championship / BO1 / BO3 /
    #       Premier / Armory are intentionally NOT allowed as `format`.
    #       Premier/Armory/CC/etc. → format=Standard, format_official=<name>
    #       BO1/BO3 → separate `best_of` field.
    #       Weekly/League/Casual → not formats (event types / level).
}


def _normalize_game(game: Optional[str], title: str = "") -> Optional[str]:
    """Resolve to a canonical game name, prioritising explicit title signals."""
    lower_title = title.lower()
    for keyword in sorted(GAME_CANONICAL, key=len, reverse=True):
        if keyword in lower_title:
            canonical = GAME_CANONICAL[keyword]
            return canonical if canonical in ALLOWED_GAMES else None

    canonical = extract_game_from_patterns(title)
    if canonical:
        return canonical if canonical in ALLOWED_GAMES else None

    if game:
        canonical = GAME_CANONICAL.get(game.lower())
        if canonical:
            return canonical if canonical in ALLOWED_GAMES else None

    return None


# ---------------------------------------------------------------------------
# Per-event basic shape check (no time filter — past events welcome)
# ---------------------------------------------------------------------------

def _parse_dt(value: str) -> Optional[datetime]:
    """Parse ISO 8601, default tz=Madrid if naive. None on error."""
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def _shape_drop_reason(event: dict) -> Optional[str]:
    """
    Return the reason this event must be dropped, or None if it passes
    the basic shape check. Reasons are buckets, not free-text:
      - 'missing_fields': required field empty/absent
      - 'bad_datetime':   datetime_start unparseable or without a UTC offset
                          (naive values would crash aware comparisons in merge)
    """
    for field in ("store", "title", "datetime_start"):
        if not event.get(field):
            return "missing_fields"
    try:
        has_offset = datetime.fromisoformat(event["datetime_start"]).tzinfo is not None
    except (TypeError, ValueError):
        has_offset = False
    if not has_offset:
        logger.warning(
            "Drop: bad datetime_start — %s / %s",
            event.get("title"), event.get("datetime_start"),
        )
        return "bad_datetime"
    return None


# ---------------------------------------------------------------------------
# Stable identity + merge
# ---------------------------------------------------------------------------

def event_key(event: dict) -> str:
    """
    Stable per-event identity used for merging across runs.

    Priority:
      1. {store}|id:{source_event_id}            (if scraper provides id)
      2. {store}|{title}|{datetime_start}|{location}    (fallback)

    The fallback is the same triple already used by the per-scraper
    deduplication, so existing events.json keys remain stable.
    """
    src = event.get("store") or ""
    sid = event.get("source_event_id")
    if sid and ":" in str(sid):
        # Namespaced ids ("wizards:123") are globally unique; leaving the
        # store name out means a store rename never re-keys the event.
        return f"id:{sid}"
    if sid:
        return f"{src}|id:{sid}"
    return "|".join([
        src,
        event.get("title") or "",
        event.get("datetime_start") or "",
        event.get("location") or "",
    ])


def merge_events(
    existing: list[dict],
    fresh: list[dict],
    now_iso: str,
    protected_sources: Optional[set[str]] = None,
) -> list[dict]:
    """
    Upsert fresh events onto existing with reschedule detection and deferred cleanup.

    Lifecycle fields written:
      - first_seen_at:  preserved from prior record (or now if new)
      - last_seen_at:   always now for active events
      - is_active:      True if seen this run, False otherwise
      - missing_runs:   incremented each run a future event is absent;
                        removed when the event reappears

    Reschedule detection:
      If a fresh event has no exact key match but shares store+title+location
      with exactly one existing future event, it is treated as a reschedule:
      the old record is re-keyed under the new datetime and first_seen_at is
      preserved.

    Failed scraper handling:
      If a source (scraper module) failed in this run, existing events from
      that source (protected_sources) are left untouched. A temporary upstream timeout should not hide or
      delete future events as if the site had intentionally removed them.

    Deferred cleanup:
      Future events missing for MISSING_RUNS_BEFORE_DELETE consecutive runs
      are hard-deleted. Past events are kept indefinitely as history.
    """
    MISSING_RUNS_BEFORE_DELETE = 3
    protected_sources = protected_sources or set()

    now_dt = datetime.fromisoformat(now_iso)
    by_key: dict = {event_key(e): e for e in existing}

    # Secondary index for reschedule detection: store|title|location → [primary_key, ...]
    fuzzy_index: dict[str, list[str]] = {}
    for k, e in by_key.items():
        fk = "|".join([e.get("store") or "", e.get("title") or "", e.get("location") or ""])
        fuzzy_index.setdefault(fk, []).append(k)

    fresh_keys: set = set()
    all_fresh_keys = {event_key(fe) for fe in fresh}

    def _is_future(key: str) -> bool:
        dt = _parse_dt(by_key[key].get("datetime_start") or "")
        return dt is not None and dt.tzinfo is not None and dt > now_dt

    for fe in fresh:
        k = event_key(fe)
        prev = by_key.get(k)

        # Reschedule: no exact match, but exactly one candidate by store|title|location
        if prev is None and not fe.get("source_event_id"):
            fk = "|".join([fe.get("store") or "", fe.get("title") or "", fe.get("location") or ""])
            # Only a still-upcoming record can have been rescheduled; a past
            # occurrence of a weekly event is history, and a record that is
            # itself in this run's results is not missing.
            candidates = [
                c for c in fuzzy_index.get(fk, [])
                if c in by_key and c not in all_fresh_keys and _is_future(c)
                and by_key[c].get("source") in (None, fe.get("source"))
            ]
            if len(candidates) == 1:
                old_key = candidates[0]
                prev = by_key.pop(old_key)
                fuzzy_index[fk] = []  # consumed — prevent double-matching

        fresh_keys.add(k)
        # Preserve first_seen_at from a prior record; otherwise stamp now.
        fe["first_seen_at"] = (prev.get("first_seen_at") if prev else None) or now_iso
        fe["last_seen_at"] = now_iso
        fe["is_active"] = True
        fe.pop("missing_runs", None)  # reset counter on reappearance
        by_key[k] = fe

    # Events not seen this run: mark inactive; deferred-delete future ones.
    for k in list(by_key.keys()):
        if k in fresh_keys:
            continue
        e = by_key[k]
        if e.get("source") in protected_sources:
            continue
        dt_str = e.get("datetime_start") or ""
        try:
            evt_dt = datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            del by_key[k]
            continue

        if evt_dt <= now_dt:
            # Past event — keep as history, just mark inactive.
            e["is_active"] = False
        else:
            # Future event — count consecutive missing runs.
            misses = e.get("missing_runs", 0) + 1
            if misses >= MISSING_RUNS_BEFORE_DELETE:
                del by_key[k]   # confirmed cancelled — remove permanently
            else:
                e["missing_runs"] = misses
                e["is_active"] = False  # hidden from UI immediately (frontend filters this)

    return list(by_key.values())


def _upcoming_active(events: list[dict], now_iso: str):
    now_dt = datetime.fromisoformat(now_iso)
    for e in events:
        if not e.get("is_active", True):
            continue
        try:
            if datetime.fromisoformat(e.get("datetime_start") or "") > now_dt:
                yield e
        except (ValueError, TypeError):
            continue


def sources_with_upcoming_events(events: list[dict], now_iso: str) -> set[str]:
    """Sources (scraper modules) that have at least one active upcoming event."""
    return {e.get("source") for e in _upcoming_active(events, now_iso) if e.get("source")}


def find_suspect_zero_sources(scraper_stats: dict, existing: list[dict], now_iso: str) -> set[str]:
    """
    Scraper modules that "succeeded" with zero valid events while we still
    hold upcoming events from them. Treated like a failed scraper: a site that
    silently stopped rendering its calendar must not look like a store that
    cancelled everything.
    """
    zero = {
        name for name, ms in scraper_stats.items()
        if not ms.get("error") and ms.get("valid", 0) == 0
    }
    return zero & sources_with_upcoming_events(existing, now_iso)


# ---------------------------------------------------------------------------
# Sources: per-store scrapers vs official locators
# ---------------------------------------------------------------------------

LOCATOR_PREFIX = "scrapers.locator_"
# A locator event duplicates a store-scraper event when both are for the
# same store and game (or the scraper's game is unknown) on the same day,
# starting within this many minutes of each other.
DUPLICATE_WINDOW_MIN = 90


def is_locator(module_name: Optional[str]) -> bool:
    return bool(module_name) and module_name.startswith(LOCATOR_PREFIX)


def backfill_sources(events: list[dict], store_to_module: dict[str, str]) -> int:
    """Tag legacy events (no `source`) with their store's scraper module."""
    n = 0
    for e in events:
        if not e.get("source") and e.get("store") in store_to_module:
            e["source"] = store_to_module[e["store"]]
            n += 1
    return n


def find_store_name_variants(fresh: list[dict]) -> list[list[str]]:
    """
    Report spelling variants of one store ("MEDUSA COMICS" / "Medusa
    Cómics") among this run's events. Detection only: renaming here would
    depend on which sources happened to list the store today and make names
    flip between runs. data/locator_store_overrides.json is the single
    place that decides canonical names.
    """
    from shared.store_matching import normalize_name

    groups: dict[str, set[str]] = {}
    for e in fresh:
        groups.setdefault(normalize_name(e["store"]), set()).add(e["store"])
    variants = sorted(sorted(names) for names in groups.values() if len(names) > 1)
    if variants:
        logger.warning(
            "Store name variants — add them to data/locator_store_overrides.json: %s",
            variants,
        )
    return variants


def drop_locator_duplicates(fresh: list[dict], existing: list[dict],
                            now_iso: str) -> tuple[list[dict], int]:
    """
    Remove locator events that duplicate an event from a store's own
    scraper; the store's own listing wins.

    Matching is one-to-one: each scraper event absorbs at most one locator
    event — the closest in time on the same store and day, within
    DUPLICATE_WINDOW_MIN, same game preferred over an unknown game. So a
    store listing only its Commander night does not hide the locator's
    Modern event at the same hour. Upcoming scraper events already on disk
    count too (once), so a scraper that failed today — its events protected
    — does not let locator copies slip in.
    """
    claims: list[tuple] = []  # (store, date, dt, game)
    seen_keys: set[str] = set()

    def claim(e):
        k = event_key(e)
        if k in seen_keys:
            return
        seen_keys.add(k)
        dt = _parse_dt(e.get("datetime_start") or "")
        if dt is not None:
            claims.append((e.get("store"), dt.date(), dt, e.get("game")))

    for e in fresh:
        if not is_locator(e.get("source")):
            claim(e)
    for e in _upcoming_active(existing, now_iso):
        if e.get("source") and not is_locator(e.get("source")):
            claim(e)

    by_day: dict[tuple, list[int]] = {}
    for ci, (store, day, _dt, _g) in enumerate(claims):
        by_day.setdefault((store, day), []).append(ci)

    pairs: list[tuple] = []  # (unknown_game, seconds_apart, locator_idx, claim_idx)
    for li, e in enumerate(fresh):
        if not is_locator(e.get("source")):
            continue
        dt = _parse_dt(e.get("datetime_start") or "")
        if dt is None:
            continue
        for ci in by_day.get((e.get("store"), dt.date()), []):
            _s, _d, cdt, cgame = claims[ci]
            gap = abs((dt - cdt).total_seconds())
            if gap <= DUPLICATE_WINDOW_MIN * 60 and (cgame is None or cgame == e.get("game")):
                pairs.append((cgame is None, gap, li, ci))

    dropped_idx: set[int] = set()
    used_claims: set[int] = set()
    for _unknown, _gap, li, ci in sorted(pairs):
        if li in dropped_idx or ci in used_claims:
            continue
        dropped_idx.add(li)
        used_claims.add(ci)

    kept = [e for i, e in enumerate(fresh) if i not in dropped_idx]
    return kept, len(dropped_idx)


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


# ---------------------------------------------------------------------------
# Validation report — no extra scraping, just walks the in-memory list
# ---------------------------------------------------------------------------

def validate_events(events: list[dict], previous_events: list[dict]) -> dict:
    """
    Final-state sanity check on the merged list. Returns a report dict;
    never raises and never mutates events.
    """
    REQUIRED = ("title", "store", "datetime_start", "source_url")
    report: dict = {
        "total": len(events),
        "future": 0,
        "past": 0,
        "active": 0,
        "inactive": 0,
        "duplicates": 0,
        "invalid_datetime": 0,
        "missing_tz": 0,
        "missing_fields": 0,
        "unknown_format": 0,
        "unknown_game": 0,
        "unknown_best_of": 0,
        "warnings": [],
    }

    now = datetime.now(tz=TZ)
    seen_keys: set = set()

    for e in events:
        # Required fields
        if any(not e.get(f) for f in REQUIRED):
            report["missing_fields"] += 1

        # ISO datetime + timezone
        iso = e.get("datetime_start") or ""
        dt = _parse_dt(iso)
        if dt is None:
            report["invalid_datetime"] += 1
        else:
            # Reject ISO strings without a tz suffix even if Python parsed them.
            if not (iso.endswith("Z") or "+" in iso[10:] or "-" in iso[10:]):
                report["missing_tz"] += 1
            elif dt >= now:
                report["future"] += 1
            else:
                report["past"] += 1

        # Duplicates
        k = event_key(e)
        if k in seen_keys:
            report["duplicates"] += 1
        else:
            seen_keys.add(k)

        # Format / game allowlist (None is fine; means "unspecified")
        fmt = e.get("format")
        if fmt and fmt not in ALLOWED_FORMATS:
            report["unknown_format"] += 1
        g = e.get("game")
        if g and g not in ALLOWED_GAMES:
            report["unknown_game"] += 1
        # best_of: 1 / 3 / None only
        bo = e.get("best_of")
        if bo is not None and bo not in (1, 3):
            report["unknown_best_of"] += 1

        # Lifecycle
        if e.get("is_active"):
            report["active"] += 1
        else:
            report["inactive"] += 1

    # Count drop detection
    prev_n = len(previous_events or [])
    if prev_n > 0 and report["total"] < prev_n * COUNT_DROP_THRESHOLD:
        report["warnings"].append(
            f"event count dropped sharply: {report['total']} < {COUNT_DROP_THRESHOLD:.0%} "
            f"of previous {prev_n}"
        )
    if report["duplicates"]:
        report["warnings"].append(f"{report['duplicates']} duplicate event_key entries")
    if report["invalid_datetime"]:
        report["warnings"].append(f"{report['invalid_datetime']} unparseable datetimes")
    if report["missing_tz"]:
        report["warnings"].append(f"{report['missing_tz']} datetimes without timezone")
    if report["missing_fields"]:
        report["warnings"].append(f"{report['missing_fields']} events missing required fields")
    if report["unknown_format"]:
        report["warnings"].append(f"{report['unknown_format']} events with unknown format value")
    if report["unknown_best_of"]:
        report["warnings"].append(f"{report['unknown_best_of']} events with invalid best_of (must be 1, 3, or null)")

    return report


def print_report(r: dict) -> None:
    print("\n=== validation ===")
    print(f"Events total:     {r['total']}")
    print(f"Future events:    {r['future']}")
    print(f"Past events:      {r['past']}")
    print(f"Active / Inactive:{r['active']} / {r['inactive']}")
    print(f"Duplicates:       {r['duplicates']}")
    print(f"Invalid datetime: {r['invalid_datetime']}")
    print(f"Missing timezone: {r['missing_tz']}")
    print(f"Missing fields:   {r['missing_fields']}")
    print(f"Unknown format:   {r['unknown_format']}")
    print(f"Unknown game:     {r['unknown_game']}")
    print(f"Invalid best_of:  {r['unknown_best_of']}")
    if r["warnings"]:
        print("Warnings:")
        for w in r["warnings"]:
            print(f"  - {w}")
    else:
        print("Warnings: none")


# ---------------------------------------------------------------------------
# Stats — written to public/events_stats.json (consumed by frontend)
# ---------------------------------------------------------------------------

def _days_between(earlier_iso: Optional[str], later_iso: str) -> float:
    """Days from earlier to later; 0 when earlier is missing or unparsable."""
    try:
        delta = datetime.fromisoformat(later_iso) - datetime.fromisoformat(earlier_iso or "")
    except (ValueError, TypeError):
        return 0.0
    return delta.total_seconds() / 86400


def build_stats(events: list[dict],
                scraper_stats: dict,
                failed_scrapers: list[str],
                generated_at: str,
                previous_stats: Optional[dict] = None,
                suspect_zero_sources: Optional[set[str]] = None) -> dict:
    """
    Build the stats document. by_store is a nested per-store breakdown
    including this-run scraper observability (raw/valid/dropped + reason
    buckets) and an anomaly flag: "suspect_zero" (0 events while upcoming
    events are still held) or "sharp_drop" (raw below the prior run's).
    """
    previous_stats = previous_stats or {}
    suspect_zero_sources = suspect_zero_sources or set()
    suspect_zero_stores = {
        scraper_stats[m]["store"] for m in suspect_zero_sources if m in scraper_stats
    }

    by_store_total = Counter(e["store"] for e in events)
    by_store_active = Counter(e["store"] for e in events if e.get("is_active"))
    # Last sighting per store *by its own scraper*: locator coverage of a
    # store must not hide that the store's scraper has gone quiet.
    store_modules: dict[str, set[str]] = {}
    for name, ms in scraper_stats.items():
        store_modules.setdefault(ms["store"], set()).add(name)
    last_seen: dict[str, str] = {}
    for e in events:
        store = e["store"]
        src = e.get("source")
        if src and store in store_modules and src not in store_modules[store]:
            continue
        ls = e.get("last_seen_at") or ""
        if ls > last_seen.get(store, ""):
            last_seen[store] = ls
    by_game = Counter(e.get("game") or "Unknown" for e in events)

    # Aggregate per-scraper observability into per-store rows. A store
    # might be served by multiple scrapers in principle, so we sum.
    store_scrape_info: dict = {}
    for ms in scraper_stats.values():
        s = ms["store"]
        row = store_scrape_info.setdefault(s, {
            "raw_this_run": 0,
            "dropped_this_run": 0,
            "drop_reasons": {"missing_fields": 0, "bad_datetime": 0},
            "scraper_failed": False,
        })
        row["raw_this_run"] += ms["raw"]
        row["dropped_this_run"] += ms["dropped"]
        for reason, n in ms["drop_reasons"].items():
            row["drop_reasons"][reason] = row["drop_reasons"].get(reason, 0) + n
        if ms.get("error"):
            row["scraper_failed"] = True

    by_store: dict = {}
    # Stable ordering: most events first
    for store, total in by_store_total.most_common():
        info = store_scrape_info.get(store, {})
        raw_now = info.get("raw_this_run", 0)
        prev_raw = _previous_raw_for_store(previous_stats, store)

        # Anomaly detection: a sharp drop is a current raw count below
        # SHARP_DROP_THRESHOLD of the previous run's raw.
        anomaly: Optional[str] = None
        if store in suspect_zero_stores:
            anomaly = "suspect_zero"
        elif store in store_modules and raw_now == 0 and not info.get("scraper_failed") and (
            last_seen.get(store) is None  # own scraper never produced an event
            or _days_between(last_seen.get(store), generated_at) >= STALE_AFTER_DAYS
        ):
            anomaly = "stale"
        elif prev_raw is not None and prev_raw > 0 and raw_now < prev_raw * SHARP_DROP_THRESHOLD:
            anomaly = "sharp_drop"

        by_store[store] = {
            "total":            total,
            "active":           by_store_active.get(store, 0),
            "raw_this_run":     raw_now,
            "previous_raw":     prev_raw,
            "dropped_this_run": info.get("dropped_this_run", 0),
            "drop_reasons":     info.get("drop_reasons",
                                         {"missing_fields": 0, "bad_datetime": 0}),
            "scraper_failed":   info.get("scraper_failed", False),
            "last_seen_at":     last_seen.get(store),
            "anomaly":          anomaly,
        }

    # Propagate anomaly + previous_raw back into the per-scraper map so
    # the console block can print module-name-anchored [WARN] lines.
    for name, ms in scraper_stats.items():
        info = by_store.get(ms["store"]) or {}
        ms["previous_raw"] = info.get("previous_raw")
        ms["anomaly"] = "suspect_zero" if name in suspect_zero_sources else info.get("anomaly")

    total_scrapers = len(scraper_stats)
    failed_count = len(failed_scrapers)

    return {
        "total_events":       len(events),
        "active_events":      sum(1 for e in events if e.get("is_active")),
        "total_scrapers":     total_scrapers,
        "successful_scrapers": total_scrapers - failed_count,
        "failed_scrapers_count": failed_count,
        "by_store":           by_store,
        "by_game":            dict(by_game.most_common()),
        "scrapers":           scraper_stats,
        "failed_scrapers":    failed_scrapers,
        "generated_at":       generated_at,
    }


def print_scraper_health(scraper_stats: dict) -> None:
    """Console block summarising each scraper's run + anomaly warnings."""
    print("\n=== scraper health ===")
    if not scraper_stats:
        print("  (no scrapers ran)")
        return

    # Anomaly warnings first so they're impossible to miss.
    for name in sorted(scraper_stats):
        s = scraper_stats[name]
        if s.get("anomaly") == "sharp_drop":
            prev = s.get("previous_raw")
            print(f"  [WARN] {name} raw dropped from {prev} → {s['raw']}")
        elif s.get("anomaly") == "suspect_zero":
            print(f"  [WARN] {name} returned 0 events but upcoming events exist — kept as-is")
        elif s.get("anomaly") == "stale":
            print(f"  [WARN] {name} has returned 0 events for {STALE_AFTER_DAYS}+ days")

    name_w = max(len(name) for name in scraper_stats) + 2
    for name in sorted(scraper_stats):
        s = scraper_stats[name]
        flag = "FAIL" if s.get("error") else (
            "WARN" if s.get("anomaly") else "ok  "
        )
        prev_raw = s.get("previous_raw")
        prev_str = "n/a" if prev_raw is None else str(prev_raw)
        print(
            f"  [{flag}] {name:<{name_w}}store={s['store']:<22} "
            f"raw={s['raw']:>4}  prev={prev_str:>4}  valid={s['valid']:>4}  "
            f"dropped={s['dropped']:>3}  "
            f"(missing={s['drop_reasons'].get('missing_fields', 0)}, "
            f"bad_dt={s['drop_reasons'].get('bad_datetime', 0)})"
            + (f"  err={s['error']}" if s.get('error') else "")
        )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    now = datetime.now(tz=TZ)
    now_iso = now.isoformat()

    # 1. Load persisted events and the previous run's stats. Stats are
    #    read BEFORE we overwrite the file at the end of this run; they
    #    feed per-store anomaly detection (sharp_drop).
    try:
        existing = load_existing()
    except CorruptEventsFile as exc:
        logger.error("%s — aborting without writing anything", exc)
        sys.exit(1)
    existing = apply_store_renames(drop_retired_stores(existing))
    previous_stats = load_previous_stats()
    logger.info(
        "Existing events on disk: %d  (previous stats run at %s)",
        len(existing), previous_stats.get("generated_at", "n/a"),
    )

    # 2. Run scrapers and accumulate raw events. We do per-scraper
    #    basic-shape checks inline so drops can be attributed back to
    #    the source instead of being aggregated globally.
    fresh: list[dict] = []
    scraper_stats: dict[str, dict] = {}
    failed_scrapers: list[str] = []
    raw_total = 0

    module_names = _discover_scrapers()
    results = run_all_scrapers(module_names)
    for module_name, (events, error, store_constant, duration_s) in zip(module_names, results):
        if error:
            failed_scrapers.append(module_name)

        raw_n = len(events)
        raw_total += raw_n
        drops = {"missing_fields": 0, "bad_datetime": 0}
        valid: list[dict] = []
        for e in events:
            reason = _shape_drop_reason(e)
            if reason is None:
                valid.append(e)
            else:
                drops[reason] = drops.get(reason, 0) + 1

        # Map this scraper to a store name. Prefer a successful event's
        # store; fall back to the first raw event; finally the module
        # name itself if the scraper produced nothing.
        store_name = (
            store_constant
            or (valid[0].get("store") if valid else None)
            or (events[0].get("store") if events else None)
            or module_name
        )
        for e in valid:
            e["source"] = module_name

        scraper_stats[module_name] = {
            "store": store_name,
            "raw": raw_n,
            "valid": len(valid),
            "dropped": raw_n - len(valid),
            "drop_reasons": drops,
            "error": error,
            "duration_s": duration_s,
        }
        logger.info(
            "%s: raw=%d valid=%d dropped=%d (missing=%d, bad_dt=%d)",
            module_name, raw_n, len(valid), raw_n - len(valid),
            drops["missing_fields"], drops["bad_datetime"],
        )
        fresh.extend(valid)

    logger.info(
        "Raw fresh events: %d  (after basic checks: %d, dropped %d)",
        raw_total, len(fresh), raw_total - len(fresh),
    )

    # 3. Normalize game.
    by_game_before = Counter(e.get("game") or "Unknown" for e in fresh)
    for e in fresh:
        # Official locators know the game for certain; title heuristics
        # would only add errors (e.g. MTG "Final Fantasy" prereleases).
        if not is_locator(e.get("source")):
            e["game"] = _normalize_game(e.get("game"), e.get("title", ""))
        # A prerelease marker in the title is more reliable than a stale
        # source/default format. This also backfills events that were first
        # scraped before a new prerelease name was added to a scraper.
        title_format, _ = extract_format_for_event(
            title=e.get("title", ""), game=e["game"],
        )
        if title_format == "Prerelease":
            e["format"] = "Prerelease"
            e["format_official"] = None
    by_game_after = Counter(e.get("game") or "Unknown" for e in fresh)

    # 4. Attribute legacy events to their scraper, unify store spellings
    #    across locators, and let a store's own scraper win over locator
    #    copies of the same event.
    store_to_module = {
        ms["store"]: name for name, ms in scraper_stats.items() if not is_locator(name)
    }
    backfilled = backfill_sources(existing, store_to_module)
    if backfilled:
        logger.info("Backfilled source on %d existing events", backfilled)
    find_store_name_variants(fresh)
    fresh, dup_count = drop_locator_duplicates(fresh, existing, now_iso)
    logger.info("Dropped %d locator events already covered by store scrapers", dup_count)

    # 5. Merge into the persistent record. For failed sources, preserve
    #    existing events instead of treating the absence of fresh events as
    #    cancellations.
    suspect_zero_sources = find_suspect_zero_sources(scraper_stats, existing, now_iso)
    if suspect_zero_sources:
        logger.warning(
            "Suspect-zero sources (0 events, upcoming events kept): %s",
            sorted(suspect_zero_sources),
        )
    merged = merge_events(
        existing, fresh, now_iso,
        protected_sources=set(failed_scrapers) | suspect_zero_sources,
    )
    new_count = len(merged) - len(existing)
    logger.info(
        "After merge: %d total (%+d vs. existing %d)",
        len(merged), new_count, len(existing),
    )

    # Merge only ever deletes future events after repeated misses, so a large
    # drop in the total means something is broken. Refuse to write.
    if existing and len(merged) < len(existing) * COUNT_DROP_THRESHOLD:
        logger.error(
            "Merged total %d is below %.0f%% of existing %d — aborting without writing",
            len(merged), COUNT_DROP_THRESHOLD * 100, len(existing),
        )
        sys.exit(1)

    upcoming_before = sum(1 for _ in _upcoming_active(existing, now_iso))
    upcoming_after = sum(1 for _ in _upcoming_active(merged, now_iso))
    if upcoming_before >= UPCOMING_GUARD_MIN and upcoming_after < upcoming_before * UPCOMING_DROP_THRESHOLD:
        logger.error(
            "Upcoming active events fell from %d to %d (<%.0f%%) — aborting without writing",
            upcoming_before, upcoming_after, UPCOMING_DROP_THRESHOLD * 100,
        )
        sys.exit(1)

    # 5. Sort and persist.
    merged.sort(key=lambda e: (e["datetime_start"], e.get("store", ""), e.get("title", "")))
    write_events(merged)

    # 6. Build stats first (so anomaly flags propagate into scraper_stats),
    #    then print both blocks.
    stats = build_stats(
        merged, scraper_stats, failed_scrapers, now_iso, previous_stats,
        suspect_zero_sources=suspect_zero_sources,
    )
    report = validate_events(merged, existing)
    print_report(report)
    print_scraper_health(scraper_stats)
    write_json_atomic(STATS_FILE, stats)
    logger.info("Wrote: %s", STATS_FILE)
    append_source_runs(scraper_stats, now_iso)

    # 9. Console summary
    print(f"\nMerged events: {len(merged)} (active={report['active']})")
    print("\nby_game before normalisation:")
    print(json.dumps(dict(by_game_before.most_common()), ensure_ascii=False, indent=2))
    print("\nby_game after normalisation + filtering:")
    print(json.dumps(dict(by_game_after.most_common()), ensure_ascii=False, indent=2))
    print("\nevents_stats.json:")
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    if failed_scrapers:
        print(f"\nWARNING: failed scrapers: {failed_scrapers}", file=sys.stderr)


if __name__ == "__main__":
    main()
