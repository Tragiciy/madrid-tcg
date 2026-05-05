#!/usr/bin/env python3
"""
discover_stores.py — run all discoverers, match against existing stores,
 deduplicate, and write candidate_stores.json for human review.

Usage:
    python3 discover_stores.py
"""

import importlib
import json
import pathlib
import sys
from collections import Counter

from shared.store_matching import (
    load_existing_stores,
    match_existing_store,
    normalize_address,
    normalize_name,
)

DISCOVERERS_DIR = pathlib.Path("discoverers")
STATUS_ORDER = {
    "matched_existing_store": 0,
    "candidate_new_store": 1,
    "possible_duplicate": 2,
    "needs_manual_review": 3,
}


def _discover_modules() -> list[str]:
    """Module names of every *.py inside discoverers/ except __init__."""
    return [
        f"discoverers.{p.stem}"
        for p in sorted(DISCOVERERS_DIR.glob("*.py"))
        if p.stem != "__init__"
    ]


def main() -> None:
    existing = load_existing_stores()
    print(f"Loaded {len(existing)} existing stores", file=sys.stderr)

    all_candidates: list[dict] = []
    errors: list[str] = []

    for module_name in _discover_modules():
        try:
            mod = importlib.import_module(module_name)
            discover_fn = getattr(mod, "discover", None)
            if discover_fn is None:
                err = f"{module_name}: no discover() function"
                errors.append(err)
                print(f"ERROR: {err}", file=sys.stderr)
                continue

            discovered = discover_fn()
            count = len(discovered) if isinstance(discovered, list) else 0
            print(f"{module_name}: discovered {count} stores", file=sys.stderr)

            if not isinstance(discovered, list):
                err = f"{module_name}: discover() did not return a list"
                errors.append(err)
                print(f"ERROR: {err}", file=sys.stderr)
                continue

            for cand in discovered:
                if not isinstance(cand, dict):
                    continue
                if not cand.get("name") or not cand.get("address"):
                    continue

                match_result = match_existing_store(cand, existing)
                candidate = {
                    "name": cand["name"],
                    "address": cand["address"],
                    "source": cand.get("source", module_name.replace("discoverers.", "")),
                    "games": cand.get("games", []),
                    "website": cand.get("website"),
                    "external_id": cand.get("external_id"),
                    "matched_existing_store": match_result.get("matched_existing_store"),
                    "confidence": match_result.get("confidence", 0.0),
                    "status": match_result.get("status", "candidate_new_store"),
                }
                all_candidates.append(candidate)
        except Exception as exc:
            err = f"{module_name}: {type(exc).__name__}: {exc}"
            errors.append(err)
            print(f"ERROR: {err}", file=sys.stderr)

    # Deduplicate by normalized (name, address)
    seen: dict[tuple[str, str], dict] = {}
    for cand in all_candidates:
        key = (normalize_name(cand["name"]), normalize_address(cand["address"]))
        if key in seen:
            if cand["confidence"] > seen[key]["confidence"]:
                seen[key] = cand
        else:
            seen[key] = cand

    candidates = list(seen.values())

    # Deterministic sort: status order asc, confidence desc, name asc
    candidates.sort(
        key=lambda c: (
            STATUS_ORDER.get(c["status"], 99),
            -c["confidence"],
            c["name"],
        )
    )

    output_path = pathlib.Path("candidate_stores.json")
    output_path.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    counts = Counter(c["status"] for c in candidates)
    total = len(candidates)

    print("\nStore Discovery Summary")
    print("=======================")
    print(f"Total discovered:     {total}")
    print(f"Matched existing:     {counts.get('matched_existing_store', 0)}")
    print(f"Candidate new stores: {counts.get('candidate_new_store', 0)}")
    print(f"Possible duplicates:  {counts.get('possible_duplicate', 0)}")
    print(f"Needs manual review:  {counts.get('needs_manual_review', 0)}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")

    print(f"\nWrote {output_path}")


if __name__ == "__main__":
    main()
