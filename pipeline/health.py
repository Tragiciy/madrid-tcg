"""Validation report, per-store stats/anomalies and console health output."""

import logging
from collections import Counter
from datetime import datetime
from typing import Optional

from pipeline.config import (
    COUNT_DROP_THRESHOLD, SHARP_DROP_THRESHOLD, STALE_AFTER_DAYS, TZ,
)
from pipeline.normalize import ALLOWED_FORMATS, ALLOWED_GAMES, _parse_dt
from pipeline.reconcile import event_key
from pipeline.repository import _previous_raw_for_store

logger = logging.getLogger(__name__)

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
        # "<module>@<city>": the source worked, but returned nothing for one
        # city (reconcile.find_suspect_zero_cities). Same alert, per city.
        zero_cities = sorted(s.split("@", 1)[1] for s in suspect_zero_sources
                             if s.startswith(f"{name}@"))
        if zero_cities:
            ms["suspect_zero_cities"] = zero_cities
        ms["anomaly"] = ("suspect_zero" if name in suspect_zero_sources or zero_cities
                         else info.get("anomaly"))

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
