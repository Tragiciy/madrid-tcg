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
     - Existing event NOT seen this run: kept on disk, is_active=False.
  4. Writes the merged list (sorted by datetime_start).
  5. Prints a lightweight validation report (counts only, no extra IO).

Past events are **never deleted** here. The frontend decides what to
show via its weekly view (current week is "future enough" by default).

Run:
    python aggregator.py
"""

import importlib
import json
import logging
import pathlib
import sys
from collections import Counter
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

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


def _run_scraper(module_name: str) -> tuple:
    """Import and call scrape(). Returns (events, None) or ([], error)."""
    try:
        module = importlib.import_module(module_name)
        events = module.scrape()
        logger.info("%s → %d events", module_name, len(events))
        return events, None
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.error("Scraper %s failed: %s", module_name, msg)
        return [], msg


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
    "Store Championship", "Prerelease", "cEDH", "Commander", "Standard",
    "Pioneer", "Modern", "Legacy", "Pauper", "Sealed", "Draft", "League",
    "Weekly", "Casual", "BO3", "BO1",
    # SWU competitive format used by Arte 9.
    "Premier",
    # Flesh and Blood organised-play event type.
    "Armory",
}


def _normalize_game(game: Optional[str], title: str = "") -> Optional[str]:
    """Resolve to canonical name; None if not in ALLOWED_GAMES."""
    if game:
        canonical = GAME_CANONICAL.get(game.lower())
        if canonical:
            return canonical if canonical in ALLOWED_GAMES else None

    lower_title = title.lower()
    for keyword in sorted(GAME_CANONICAL, key=len, reverse=True):
        if keyword in lower_title:
            canonical = GAME_CANONICAL[keyword]
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
      - 'bad_datetime':   datetime_start present but unparseable
    """
    for field in ("store", "title", "datetime_start"):
        if not event.get(field):
            return "missing_fields"
    if _parse_dt(event["datetime_start"]) is None:
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
    if sid:
        return f"{src}|id:{sid}"
    return "|".join([
        src,
        event.get("title") or "",
        event.get("datetime_start") or "",
        event.get("location") or "",
    ])


def merge_events(existing: list[dict], fresh: list[dict], now_iso: str) -> list[dict]:
    """
    Upsert fresh events onto existing, never dropping anything.

    Lifecycle fields written:
      - first_seen_at: preserved from prior record (or now if new)
      - last_seen_at:  always now
      - is_active:     True for events present in this run, False otherwise
    """
    by_key: dict = {event_key(e): e for e in existing}
    fresh_keys: set = set()

    for fe in fresh:
        k = event_key(fe)
        fresh_keys.add(k)
        prev = by_key.get(k)
        # Preserve first_seen_at from a prior record; otherwise stamp now.
        fe["first_seen_at"] = (prev.get("first_seen_at") if prev else None) or now_iso
        fe["last_seen_at"] = now_iso
        fe["is_active"] = True
        by_key[k] = fe

    # Events on disk that didn't show up this run stay, but are flagged.
    for k, e in by_key.items():
        if k not in fresh_keys:
            e["is_active"] = False

    return list(by_key.values())


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_existing() -> list[dict]:
    """Read events.json (if present) and backfill missing lifecycle fields."""
    if not EVENTS_FILE.exists():
        return []
    try:
        data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Could not read %s: %s — starting empty", EVENTS_FILE, exc)
        return []

    for e in data:
        # Pre-refactor records had only scraped_at; use it as a sensible
        # backfill for first_seen_at/last_seen_at so we don't pretend
        # everything was discovered today.
        e.setdefault("first_seen_at", e.get("scraped_at") or EPOCH_ISO)
        e.setdefault("last_seen_at", e.get("scraped_at") or e["first_seen_at"])
        e.setdefault("is_active", True)
    return data


def write_events(events: list[dict]) -> None:
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.write_text(
        json.dumps(events, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Wrote: %s (%d events)", EVENTS_FILE, len(events))


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
    if r["warnings"]:
        print("Warnings:")
        for w in r["warnings"]:
            print(f"  - {w}")
    else:
        print("Warnings: none")


# ---------------------------------------------------------------------------
# Stats — written to public/events_stats.json (consumed by frontend)
# ---------------------------------------------------------------------------

def build_stats(events: list[dict],
                scraper_stats: dict,
                failed_scrapers: list[str],
                generated_at: str,
                previous_stats: Optional[dict] = None) -> dict:
    """
    Build the stats document. by_store is a nested per-store breakdown
    including this-run scraper observability (raw/valid/dropped + reason
    buckets) and a sharp-drop anomaly flag derived from the prior run.
    """
    previous_stats = previous_stats or {}

    by_store_total = Counter(e["store"] for e in events)
    by_store_active = Counter(e["store"] for e in events if e.get("is_active"))
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
        if prev_raw is not None and prev_raw > 0 and raw_now < prev_raw * SHARP_DROP_THRESHOLD:
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
            "anomaly":          anomaly,
        }

    # Propagate anomaly + previous_raw back into the per-scraper map so
    # the console block can print module-name-anchored [WARN] lines.
    for ms in scraper_stats.values():
        info = by_store.get(ms["store"]) or {}
        ms["previous_raw"] = info.get("previous_raw")
        ms["anomaly"] = info.get("anomaly")

    return {
        "total_events":  len(events),
        "active_events": sum(1 for e in events if e.get("is_active")),
        "by_store":      by_store,
        "by_game":       dict(by_game.most_common()),
        "scrapers":      scraper_stats,
        "failed_scrapers": failed_scrapers,
        "generated_at":  generated_at,
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

    name_w = max(len(name) for name in scraper_stats) + 2
    for name in sorted(scraper_stats):
        s = scraper_stats[name]
        flag = "FAIL" if s.get("error") else (
            "WARN" if s.get("anomaly") == "sharp_drop" else "ok  "
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
    existing = load_existing()
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

    for module_name in _discover_scrapers():
        events, error = _run_scraper(module_name)
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
            (valid[0].get("store") if valid else None)
            or (events[0].get("store") if events else None)
            or module_name
        )

        scraper_stats[module_name] = {
            "store": store_name,
            "raw": raw_n,
            "valid": len(valid),
            "dropped": raw_n - len(valid),
            "drop_reasons": drops,
            "error": error,
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
        e["game"] = _normalize_game(e.get("game"), e.get("title", ""))
    by_game_after = Counter(e.get("game") or "Unknown" for e in fresh)

    # 4. Merge into the persistent record.
    merged = merge_events(existing, fresh, now_iso)
    new_count = len(merged) - len(existing)
    logger.info(
        "After merge: %d total (%+d vs. existing %d)",
        len(merged), new_count, len(existing),
    )

    # 5. Sort and persist.
    merged.sort(key=lambda e: e["datetime_start"])
    write_events(merged)

    # 6. Build stats first (so anomaly flags propagate into scraper_stats),
    #    then print both blocks.
    stats = build_stats(
        merged, scraper_stats, failed_scrapers, now_iso, previous_stats,
    )
    report = validate_events(merged, existing)
    print_report(report)
    print_scraper_health(scraper_stats)
    STATS_FILE.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Wrote: %s", STATS_FILE)

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
