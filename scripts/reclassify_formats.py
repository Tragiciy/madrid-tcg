"""
One-shot script to reclassify formats, populate format_official, and add
best_of to existing events.json using the new game-aware extraction logic.

Strategy: only touch events whose `format` is deprecated (Weekly, League,
Casual, Store Championship, BO1, BO3, Premier, Armory) or null. Existing
valid formats (Standard, Commander, Modern, Sealed, Prerelease, etc.) are
preserved — we don't have description/category in events.json to safely
re-derive them.

Special migrations:
  - "BO1" / "BO3" → best_of = 1 / 3, format = re-extracted (often Standard)
  - "Premier" → format=Standard, format_official="Premier"
  - "Armory" → format=Standard, format_official="Armory"
  - "Store Championship" → format=Standard, format_official="Store Championship"
  - "Weekly", "League", "Casual" → re-extracted from title or default
  - null → re-extracted from title or default

Usage:
    python scripts/reclassify_formats.py --dry-run
    python scripts/reclassify_formats.py
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.scraper_keywords import (  # noqa: E402
    GAME_KEYWORDS,
    extract_game_from_keywords,
    extract_format_for_event,
    extract_best_of,
)


EVENTS_PATH = Path(__file__).resolve().parents[1] / "public" / "events.json"

# Old `format` values that need migration. Anything not in this set
# (and not None) is left untouched.
DEPRECATED_FORMATS = {
    "Weekly", "League", "Casual",
    "Store Championship",
    "BO1", "BO3",
    "Premier", "Armory",
    "Battle Hardened",
}

# Old format → (new unified format, new format_official, best_of)
# When fixed, we don't even need to re-extract from title.
DIRECT_MIGRATIONS: dict[str, tuple] = {
    "Premier":            ("Standard", "Premier",            None),
    "Armory":             ("Standard", "Armory",             None),
    "Store Championship": ("Standard", "Store Championship", None),
    "Battle Hardened":    ("Standard", "Battle Hardened",    None),
    "BO1":                (None,       None,                 1),  # format re-extracted below
    "BO3":                (None,       None,                 3),
}


def main(dry_run: bool) -> int:
    events = json.loads(EVENTS_PATH.read_text())
    print(f"Loaded {len(events)} events from {EVENTS_PATH}")

    stats: Counter = Counter()
    transitions: Counter = Counter()

    # Always re-apply Prerelease-in-title priority — it fixes old data
    # where the WordPress category took precedence over the title.
    from shared.scraper_keywords import _PRERELEASE_TITLE_RE  # noqa: PLC2701

    for e in events:
        old_game = e.get("game")
        old_fmt = e.get("format")
        old_official = e.get("format_official")
        old_bo = e.get("best_of")

        new_game = old_game
        new_fmt = old_fmt
        new_official = old_official
        new_bo = old_bo if old_bo is not None else extract_best_of(e.get("title", ""))

        # Game re-extraction: if title now matches a different game, correct it.
        # This fixes events scraped before new GAME_KEYWORDS entries were added
        # (e.g. "UNLEASHED" events at Ítaca that were falling back to "Magic").
        kw_game = extract_game_from_keywords(e.get("title", ""), GAME_KEYWORDS)
        if kw_game and kw_game != old_game:
            new_game = kw_game
            stats["game_corrected"] += 1
            e["game"] = new_game
            # Re-extract format with corrected game so DEFAULT_FORMAT_BY_GAME
            # for the new game applies. Do this when format is uncertain.
            if old_fmt is None or old_fmt in DEPRECATED_FORMATS:
                rfmt, roff = extract_format_for_event(
                    title=e.get("title", ""), game=new_game,
                )
                # Update both old_fmt (controls branch selection below) and
                # new_fmt (holds the value we will write) with the re-extracted
                # result so the downstream logic sees a consistent state.
                old_fmt = rfmt
                new_fmt = rfmt
                if roff:
                    old_official = roff
                    new_official = roff

        # Universal fix: title with "presentación" / "prerelease" always wins
        if old_fmt != "Prerelease" and _PRERELEASE_TITLE_RE.search(e.get("title", "")):
            new_fmt = "Prerelease"
            stats["title_prerelease_fix"] += 1
            transitions[(old_fmt, "Prerelease")] += 1
            e["format"] = new_fmt
            e["format_official"] = new_official
            e["best_of"] = new_bo
            continue

        if old_fmt in DIRECT_MIGRATIONS:
            mig_fmt, mig_official, mig_bo = DIRECT_MIGRATIONS[old_fmt]
            if mig_fmt is not None:
                # Premier/Armory/Store Championship: known mapping
                new_fmt = mig_fmt
                new_official = mig_official
            else:
                # BO1/BO3: format unknown, extract from title; force best_of
                rfmt, roff = extract_format_for_event(
                    title=e.get("title", ""), game=e.get("game"),
                )
                new_fmt = rfmt
                if roff and not new_official:
                    new_official = roff
                new_bo = mig_bo
        elif old_fmt in DEPRECATED_FORMATS:
            # Weekly / League / Casual — re-extract or fall back to default
            rfmt, roff = extract_format_for_event(
                title=e.get("title", ""), game=e.get("game"),
            )
            new_fmt = rfmt
            if roff and not new_official:
                new_official = roff
        elif old_fmt is None:
            # Null → try to fill via default
            rfmt, roff = extract_format_for_event(
                title=e.get("title", ""), game=e.get("game"),
            )
            new_fmt = rfmt
            if roff and not new_official:
                new_official = roff

        if new_fmt != old_fmt:
            stats["format_changed"] += 1
            transitions[(old_fmt, new_fmt)] += 1
            if old_fmt is None and new_fmt is not None:
                stats["null_filled"] += 1

        if new_official != old_official:
            if new_official is not None:
                stats["format_official_added"] += 1

        if new_bo != old_bo:
            if new_bo is not None:
                stats["best_of_added"] += 1

        e["format"] = new_fmt
        e["format_official"] = new_official
        e["best_of"] = new_bo

    # ── Summary ──
    print("\n=== Reclassification summary ===")
    for k, v in stats.most_common():
        print(f"  {k:30s} {v:5d}")

    print("\n=== Top format transitions ===")
    for (old, new), n in transitions.most_common(20):
        print(f"  {str(old):20s} → {str(new):20s} {n:5d}")

    # Final format distribution
    final_dist = Counter(e.get("format") for e in events)
    print("\n=== Final format distribution ===")
    for fmt, n in final_dist.most_common():
        print(f"  {str(fmt):25s} {n:5d}")

    null_pct = 100.0 * final_dist.get(None, 0) / len(events)
    print(f"\nNull format: {final_dist.get(None, 0)} / {len(events)} ({null_pct:.1f}%)")

    if dry_run:
        print("\n(dry-run, events.json NOT written)")
    else:
        EVENTS_PATH.write_text(
            json.dumps(events, ensure_ascii=False, indent=2) + "\n"
        )
        print(f"\n✅ events.json updated ({EVENTS_PATH})")

    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Print stats without writing events.json")
    args = p.parse_args()
    sys.exit(main(args.dry_run))
