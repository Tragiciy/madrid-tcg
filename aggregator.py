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

import json
import logging
import sys
from collections import Counter
from datetime import datetime

from pipeline.config import (
    COUNT_DROP_THRESHOLD, STATS_FILE, TZ, UPCOMING_DROP_THRESHOLD, UPCOMING_GUARD_MIN,
)
from pipeline.health import build_stats, print_report, print_scraper_health, validate_events
from pipeline.normalize import _normalize_game, _shape_drop_reason
from pipeline.reconcile import (
    _upcoming_active, attach_store_ids, backfill_sources, drop_locator_duplicates,
    find_store_name_variants, find_suspect_zero_sources, is_locator, merge_events,
)
from pipeline.repository import (
    CorruptEventsFile, append_source_runs, apply_store_renames, drop_retired_stores,
    load_existing, load_previous_stats, save_store_registry, write_events,
    write_json_atomic,
)
from pipeline.pages import build_pages, write_pages
from pipeline.publish import build_api, write_api
from pipeline.runner import _discover_scrapers, run_all_scrapers
from shared.scraper_keywords import extract_format_for_event
from shared.store_registry import StoreRegistry

logger = logging.getLogger(__name__)


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
    registry = StoreRegistry.load()
    new_stores = attach_store_ids(fresh, existing, registry)
    if new_stores:
        logger.info("Store registry: %d new store(s) learned from this run", new_stores)
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
    save_store_registry(registry)
    write_api(build_api(merged, registry, now_iso))
    write_pages(build_pages(merged, registry, now_iso))

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
