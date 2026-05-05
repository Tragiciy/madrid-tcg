#!/usr/bin/env python3
"""
Build scraper target report from store_event_audit.json.

Reads store_event_audit.json, classifies stores into actionable groups,
and writes scraper_targets.json with a clear, actionable list of stores to scrape next.
"""

import json

INPUT_FILE = "store_event_audit.json"
OUTPUT_FILE = "scraper_targets.json"

READINESS_TO_ACTION = {
    "ready": "scrape_now",
    "possible": "investigate",
    "manual_review": "manual_review",
    "not_ready": "skip_for_now",
}


def detect_platform(signals: list[str]) -> str:
    """Detect platform from audit signals."""
    for sig in signals:
        if sig == "platform:wordpress":
            return "wordpress"
        if sig == "platform:shopify":
            return "shopify"
    return "unknown"


def count_homepage_event_keywords(signals: list[str]) -> int:
    return sum(1 for s in signals if s.startswith("homepage_event_keyword:"))


def generate_scrape_now_reason(store: dict) -> str:
    """Generate a concise, actionable reason for scrape_now targets."""
    signals = store.get("signals", [])
    game = store.get("game_detected")
    best_page = store.get("best_event_page") or ""

    if game:
        short_game = "MTG" if game == "Magic: The Gathering" else game
        return f"validated event page with {short_game} content"

    if "calendario" in best_page.lower() or "calendar" in best_page.lower():
        return "clean calendar page"

    if count_homepage_event_keywords(signals) >= 2:
        return "multiple event signals"

    return "validated event page"


def build_target(store: dict) -> dict:
    readiness = store.get("scraper_readiness", "manual_review")
    priority = store.get("scraper_priority", "manual_review")

    if readiness == "ready":
        reason = generate_scrape_now_reason(store)
    else:
        reason = store.get("notes", "")

    return {
        "name": store.get("name", ""),
        "website": store.get("website", ""),
        "best_event_page": store.get("best_event_page"),
        "platform": detect_platform(store.get("signals", [])),
        "game_detected": store.get("game_detected"),
        "priority": priority,
        "recommended_action": READINESS_TO_ACTION.get(readiness, "manual_review"),
        "reason": reason,
    }


def sort_targets(targets: list[dict]) -> list[dict]:
    priority_order = {"high": 0, "medium": 1, "manual_review": 2, "low": 3}
    return sorted(
        targets,
        key=lambda t: (priority_order.get(t["priority"], 99), t["name"].lower()),
    )


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        audit_results = json.load(f)

    groups = {
        "scrape_now": [],
        "possible": [],
        "manual_review": [],
        "not_ready": [],
    }

    for store in audit_results:
        readiness = store.get("scraper_readiness", "manual_review")
        target = build_target(store)

        if readiness == "ready":
            groups["scrape_now"].append(target)
        elif readiness == "possible":
            groups["possible"].append(target)
        elif readiness == "manual_review":
            groups["manual_review"].append(target)
        elif readiness == "not_ready":
            groups["not_ready"].append(target)
        else:
            # Fallback for unexpected readiness values
            groups["manual_review"].append(target)

    groups["scrape_now"] = sort_targets(groups["scrape_now"])
    groups["possible"] = sort_targets(groups["possible"])
    groups["manual_review"] = sort_targets(groups["manual_review"])
    groups["not_ready"] = sort_targets(groups["not_ready"])

    output = {
        "scrape_now": groups["scrape_now"],
        "possible": groups["possible"],
        "manual_review": groups["manual_review"],
        "not_ready": groups["not_ready"],
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Wrote {OUTPUT_FILE}")
    print(f"  scrape_now:     {len(groups['scrape_now'])}")
    print(f"  possible:       {len(groups['possible'])}")
    print(f"  manual_review:  {len(groups['manual_review'])}")
    print(f"  not_ready:      {len(groups['not_ready'])}")


if __name__ == "__main__":
    main()
