"""Event identity, merge with history, source protection and deduplication.

Pure functions over lists of event dicts: no file or network I/O.
"""

import copy
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from pipeline.normalize import _parse_dt
from shared.cities import DEFAULT_CITY

logger = logging.getLogger(__name__)

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
      An entry "<module>@<city>" protects only that source's events in that
      city (find_suspect_zero_cities); "*@<city>" protects every event of
      the city (a city held by the upcoming-drop guard).

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
        if (e.get("source") in protected_sources
                or f"{e.get('source')}@{city_of(e)}" in protected_sources
                or f"*@{city_of(e)}" in protected_sources):
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


def upcoming_drops(existing: list[dict], merged: list[dict], now_iso: str,
                   threshold: float, floor: int) -> list[tuple[str, int, int]]:
    """
    (city, before, after) for every city whose upcoming active events fell
    below `threshold` of the previous run's, once it had at least `floor`.
    Checked per city: one city's volume must not mask another losing every
    future event.
    """
    before = Counter(city_of(e) for e in _upcoming_active(existing, now_iso))
    after = Counter(city_of(e) for e in _upcoming_active(merged, now_iso))
    return [(city, n, after[city]) for city, n in sorted(before.items())
            if n >= floor and after[city] < n * threshold]


def merge_holding_cities(existing: list[dict], fresh: list[dict], now_iso: str,
                         protected_sources: set[str], threshold: float,
                         floor: int) -> tuple[list[dict], list[tuple[str, int, int]]]:
    """
    merge_events, then the per-city upcoming guard. A city whose upcoming
    active events fell below `threshold` of the previous run's (upcoming_drops)
    is *held*: the merge is redone with "*@<city>" protected, so none of its
    previous events is hidden or deleted (fresh ones are still added), and
    the other cities are published normally. Returns (merged, held), held
    being [(city, before, after)] as measured on the unheld merge.

    `before` is measured on a copy taken before merging, because
    merge_events marks the unseen records of `existing` inactive in place.
    """
    snapshot = copy.deepcopy(existing)
    merged = merge_events(existing, fresh, now_iso, protected_sources=protected_sources)
    held = upcoming_drops(snapshot, merged, now_iso, threshold, floor)
    if not held:
        return merged, held
    protected = set(protected_sources) | {f"*@{city}" for city, _, _ in held}
    merged = merge_events(copy.deepcopy(snapshot), fresh, now_iso, protected_sources=protected)
    still = upcoming_drops(snapshot, merged, now_iso, threshold, floor)
    if still:  # cannot happen: a held city loses nothing
        raise RuntimeError(f"upcoming events still dropping after holding {held}: {still}")
    return merged, held


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
# Locators are read this many days back (shared.locator_common.LOOKBACK_DAYS
# plus a day of margin); existing scraper events in that range claim slots.
CLAIM_LOOKBACK_DAYS = 8


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
    flip between runs. Aliases in data/stores.json are the single
    place that decides canonical names.
    """
    from shared.store_matching import normalize_name

    groups: dict[str, set[str]] = {}
    for e in fresh:
        groups.setdefault(normalize_name(e["store"]), set()).add(e["store"])
    variants = sorted(sorted(names) for names in groups.values() if len(names) > 1)
    if variants:
        logger.warning(
            "Store name variants — add aliases in data/stores.json: %s",
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

    A locator event with the same event key as a scraper event of the same
    store (a store card linking the locator's event id, e.g.
    "playriftbound:<id>") is that event whatever the times say, and is
    dropped first. A key shared with another store's event is a wrong link
    on the store's card: it is logged and left to the time-window match.
    """
    claims: list[tuple] = []  # (store, date, dt, game)
    seen_keys: set[str] = set()
    claim_by_key: dict[str, int] = {}
    store_by_key: dict[str, Optional[str]] = {}

    def claim(e):
        k = event_key(e)
        if k in seen_keys:
            return
        seen_keys.add(k)
        store_by_key[k] = e.get("store")
        dt = _parse_dt(e.get("datetime_start") or "")
        if dt is not None:
            claim_by_key[k] = len(claims)
            claims.append((e.get("store"), dt.date(), dt, e.get("game")))

    for e in fresh:
        if not is_locator(e.get("source")):
            claim(e)
    # Existing scraper events claim too — upcoming ones (a scraper that failed
    # today keeps its events) and recent past ones, because locators are
    # also re-read LOOKBACK_DAYS back for final player counts and their
    # copies of already-held past events must not become history duplicates.
    now_dt = datetime.fromisoformat(now_iso)
    since = now_dt - timedelta(days=CLAIM_LOOKBACK_DAYS)
    for e in existing:
        if not e.get("source") or is_locator(e.get("source")):
            continue
        dt = _parse_dt(e.get("datetime_start") or "")
        if dt is None:
            continue
        if dt > now_dt and e.get("is_active", True):
            claim(e)
        elif since <= dt <= now_dt:
            claim(e)

    by_day: dict[tuple, list[int]] = {}
    for ci, (store, day, _dt, _g) in enumerate(claims):
        by_day.setdefault((store, day), []).append(ci)

    dropped_idx: set[int] = set()
    used_claims: set[int] = set()
    for li, e in enumerate(fresh):
        k = event_key(e)
        if not is_locator(e.get("source")) or k not in seen_keys:
            continue
        if store_by_key.get(k) != e.get("store"):
            logger.warning("Locator event %s is at %r but a %r card links it; not merged by id",
                           k, e.get("store"), store_by_key.get(k))
            continue
        dropped_idx.add(li)
        if k in claim_by_key:
            ci = claim_by_key[k]
            used_claims.add(ci)
            dt = _parse_dt(e.get("datetime_start") or "")
            if dt is not None and abs((dt - claims[ci][2]).total_seconds()) > DUPLICATE_WINDOW_MIN * 60:
                logger.warning("%s: %s lists %s at %s, the store at %s; keeping the store's time",
                               e.get("store"), k, e.get("source"), dt.isoformat(), claims[ci][2].isoformat())

    pairs: list[tuple] = []  # (unknown_game, seconds_apart, locator_idx, claim_idx)
    for li, e in enumerate(fresh):
        if not is_locator(e.get("source")) or li in dropped_idx:
            continue
        dt = _parse_dt(e.get("datetime_start") or "")
        if dt is None:
            continue
        for ci in by_day.get((e.get("store"), dt.date()), []):
            _s, _d, cdt, cgame = claims[ci]
            gap = abs((dt - cdt).total_seconds())
            if gap <= DUPLICATE_WINDOW_MIN * 60 and (cgame is None or cgame == e.get("game")):
                pairs.append((cgame is None, gap, li, ci))

    for _unknown, _gap, li, ci in sorted(pairs):
        if li in dropped_idx or ci in used_claims:
            continue
        dropped_idx.add(li)
        used_claims.add(ci)

    kept = [e for i, e in enumerate(fresh) if i not in dropped_idx]
    return kept, len(dropped_idx)


def attach_store_ids(fresh: list[dict], existing: list[dict], registry) -> int:
    """
    Give every event its registry `store_id` and `city`. Fresh events'
    `store_hint` (what a locator told us about the store, including the city
    whose search found it) is absorbed into the registry via learn() — new
    stores and aliases — and removed from the event.

    An event's city is always its store's registry city, re-derived every
    run, so correcting a store's city in data/stores.json moves its events.
    An existing event whose store is not in the registry keeps the city it
    had, else gets DEFAULT_CITY (history from before multi-city is Madrid's).
    Returns the number of stores the registry did not know before.
    """
    before = len(registry.stores)
    for e in fresh:
        record = registry.learn(e["store"], e.pop("store_hint", None))
        e["store_id"] = record["id"]
        e["city"] = registry.city_of(record)
        if is_locator(e.get("source")):
            # One display name per registry record. Only for locator events:
            # their keys are namespaced ids, while a scraper event's key
            # contains its store name and must not change.
            e["store"] = record["name"]
    for e in existing:
        e.pop("store_hint", None)
        record = registry.find(e.get("store") or "")
        if record is not None:
            e["store_id"] = record["id"]
            e["city"] = registry.city_of(record)
        else:
            e["city"] = e.get("city") or DEFAULT_CITY
    return len(registry.stores) - before


def city_of(event: dict) -> str:
    """The event's city id (set by attach_store_ids; DEFAULT_CITY before)."""
    return event.get("city") or DEFAULT_CITY


def find_suspect_zero_cities(scraper_stats: dict, fresh: list[dict], existing: list[dict],
                             now_iso: str) -> set[str]:
    """
    "<module>@<city>" for each source that returned events this run, but
    none in a city where we still hold upcoming events from it. A locator
    searches every city; one city's search silently coming back empty must
    not be hidden by the others' volume. Protected like a failed source,
    for that city only (merge_events). Sources that returned nothing at all
    are find_suspect_zero_sources' job.
    """
    returned: set[tuple[str, str]] = {(e.get("source"), city_of(e)) for e in fresh}
    out: set[str] = set()
    for e in _upcoming_active(existing, now_iso):
        src, city = e.get("source"), city_of(e)
        ms = scraper_stats.get(src)
        if not ms or ms.get("error") or ms.get("valid", 0) == 0:
            continue
        if (src, city) not in returned:
            out.add(f"{src}@{city}")
    return out
