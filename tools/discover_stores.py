#!/usr/bin/env python3
"""
discover_stores.py — run all discoverers, match against existing stores,
 deduplicate, and write candidate_stores.json for human review.

Usage:
    python3 tools/discover_stores.py
"""

import importlib
import json
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

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

GAME_ALIASES = {
    "MTG": "Magic: The Gathering",
    "Magic": "Magic: The Gathering",
    "SWU": "Star Wars: Unlimited",
    "FAB": "Flesh and Blood",
    "Flesh & Blood": "Flesh and Blood",
    "Yugioh": "Yu-Gi-Oh!",
    "Yu-Gi-Oh": "Yu-Gi-Oh!",
    "OnePiece": "One Piece",
}


def _discover_modules() -> list[str]:
    """Module names of every *.py inside discoverers/ except __init__."""
    return [
        f"discoverers.{p.stem}"
        for p in sorted(DISCOVERERS_DIR.glob("*.py"))
        if p.stem != "__init__"
    ]


def _canonical_game(game: str) -> str:
    game = str(game).strip()
    return GAME_ALIASES.get(game, game)


def _unique_sorted(values: list[str]) -> list[str]:
    return sorted({v for v in values if v})


def _candidate_source(cand: dict, module_name: str) -> str:
    return cand.get("source") or module_name.replace("discoverers.", "")


def _candidate_games(cand: dict) -> list[str]:
    games = cand.get("games") or []
    if isinstance(games, str):
        games = [games]
    return _unique_sorted([_canonical_game(g) for g in games])


def _candidate_external_ids(cand: dict, source: str) -> dict[str, str]:
    external_ids = cand.get("external_ids")
    if isinstance(external_ids, dict):
        return {
            str(k): str(v)
            for k, v in external_ids.items()
            if k and v is not None and str(v)
        }

    external_id = cand.get("external_id")
    if external_id is None or str(external_id) == "":
        return {}
    return {source: str(external_id)}


def _candidate_evidence(cand: dict, source: str, games: list[str]) -> list[dict]:
    """
    Return source evidence records for a discovered store.

    Discoverers may provide rich evidence themselves. Older discoverers only
    provide source/games/website/external_id, so we synthesize one evidence
    record to preserve provenance in the new multi-source model.
    """
    evidence = cand.get("evidence")
    if isinstance(evidence, list):
        normalized = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            record.setdefault("source", source)
            if "game" in record:
                record["game"] = _canonical_game(record["game"])
            if isinstance(record.get("games"), list):
                record["games"] = _unique_sorted([
                    _canonical_game(g) for g in record["games"]
                ])
            normalized.append(record)
        if normalized:
            return normalized

    record = {
        "source": source,
        "type": cand.get("evidence_type", "official_store_locator"),
    }
    if games:
        record["games"] = games
    if cand.get("website"):
        record["url"] = cand["website"]
    if cand.get("external_id") is not None:
        record["external_id"] = str(cand["external_id"])
    return [record]


def _merge_key(candidate: dict) -> tuple[str, str]:
    """
    Merge exact duplicate records from different discoverers.

    Keep the key deliberately strict. A loose key based on matched existing
    store can collapse distinct branches of a chain into one candidate when
    fuzzy matching maps them to the same known store. Later dedupe phases can
    add geocoding or safer cross-source fuzzy merge.
    """
    return (
        normalize_name(candidate["name"]),
        normalize_address(candidate["address"]),
    )


def _prefer_primary(current: dict, incoming: dict) -> bool:
    current_score = (
        STATUS_ORDER.get(current["status"], 99),
        -current["confidence"],
        0 if current.get("website") else 1,
    )
    incoming_score = (
        STATUS_ORDER.get(incoming["status"], 99),
        -incoming["confidence"],
        0 if incoming.get("website") else 1,
    )
    return incoming_score < current_score


def _merge_candidate(current: dict, incoming: dict) -> dict:
    if _prefer_primary(current, incoming):
        primary = {
            "name": incoming["name"],
            "address": incoming["address"],
            "source": incoming["source"],
            "website": incoming.get("website") or current.get("website"),
            "external_id": incoming.get("external_id") or current.get("external_id"),
            "matched_existing_store": incoming.get("matched_existing_store"),
            "confidence": incoming.get("confidence", 0.0),
            "status": incoming.get("status", "candidate_new_store"),
        }
    else:
        primary = {
            "name": current["name"],
            "address": current["address"],
            "source": current["source"],
            "website": current.get("website") or incoming.get("website"),
            "external_id": current.get("external_id") or incoming.get("external_id"),
            "matched_existing_store": current.get("matched_existing_store"),
            "confidence": current.get("confidence", 0.0),
            "status": current.get("status", "candidate_new_store"),
        }

    sources = _unique_sorted(current.get("sources", []) + incoming.get("sources", []))
    games = _unique_sorted(current.get("games", []) + incoming.get("games", []))
    external_ids = {
        **current.get("external_ids", {}),
        **incoming.get("external_ids", {}),
    }
    evidence = current.get("evidence", []) + incoming.get("evidence", [])

    return {
        **primary,
        "sources": sources,
        "external_ids": external_ids,
        "games": games,
        "evidence": evidence,
    }


def _build_candidate(cand: dict, module_name: str, match_result: dict) -> dict:
    source = _candidate_source(cand, module_name)
    games = _candidate_games(cand)
    external_ids = _candidate_external_ids(cand, source)
    external_id = external_ids.get(source)

    return {
        "name": cand["name"],
        "address": cand["address"],
        "source": source,
        "sources": [source],
        "games": games,
        "website": cand.get("website"),
        "external_id": external_id,
        "external_ids": external_ids,
        "evidence": _candidate_evidence(cand, source, games),
        "matched_existing_store": match_result.get("matched_existing_store"),
        "confidence": match_result.get("confidence", 0.0),
        "status": match_result.get("status", "candidate_new_store"),
    }


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
                candidate = _build_candidate(cand, module_name, match_result)
                all_candidates.append(candidate)
        except Exception as exc:
            err = f"{module_name}: {type(exc).__name__}: {exc}"
            errors.append(err)
            print(f"ERROR: {err}", file=sys.stderr)

    # Deduplicate and merge repeated stores across discoverers.
    seen: dict[tuple[str, str], dict] = {}
    for cand in all_candidates:
        key = _merge_key(cand)
        if key in seen:
            seen[key] = _merge_candidate(seen[key], cand)
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

    output_path = ROOT / "data" / "candidate_stores.json"
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
