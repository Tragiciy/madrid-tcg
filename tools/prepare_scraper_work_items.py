#!/usr/bin/env python3
"""
Prepare implementation backlog for new scraper targets.

Reads scraper_targets.json, extracts scrape_now targets, and writes
scraper_work_items.json with the fields needed to implement scrapers in
small, reviewable chunks.
"""

import json
import pathlib
import re
import unicodedata
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).parent.parent

INPUT_FILE = ROOT / "data" / "scraper_targets.json"
OUTPUT_FILE = ROOT / "data" / "scraper_work_items.json"

SCRAPERS_DIR = ROOT / "scrapers"

SCRAPER_FILENAME_OVERRIDES = {
    "GenexComics": "genexcomics",
    "Metamorfo (Santiago Palacios)": "metamorfo",
    "Next Dice": "next_dice",
    "OZ JUEGOS": "oz_juegos",
    "Replay Boardgame Cafe": "replay_boardgame_cafe",
    "Three Stones Games": "three_stones_games",
}


def slugify_store_name(name: str) -> str:
    """Return a stable Python module slug from a store name."""
    without_parenthetical = re.sub(r"\s*\([^)]*\)", "", name).strip()
    normalized = unicodedata.normalize("NFKD", without_parenthetical)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name.lower()).strip("_")
    return slug or "store_scraper"


def recommended_scraper_file(store_name: str) -> str:
    slug = SCRAPER_FILENAME_OVERRIDES.get(store_name) or slugify_store_name(store_name)
    return f"scrapers/{slug}.py"


def target_kind(target_url: str) -> str:
    path = urlparse(target_url).path.lower()
    if "/event-details/" in path:
        return "single_event_page"
    if any(part in path for part in ("eventos", "events", "calendario", "calendar")):
        return "event_listing"
    return "unknown"


def build_implementation_notes(target: dict, scraper_file: str) -> list[str]:
    platform = target.get("platform") or "unknown"
    target_url = target.get("best_event_page") or ""
    kind = target_kind(target_url)
    scraper_path = ROOT / scraper_file

    notes = []
    if scraper_path.exists():
        notes.append("Recommended scraper file already exists; verify coverage before changing it.")

    if platform == "wordpress":
        notes.append(
            "Try shared.wordpress_events.fetch_wp_events first; fall back to page HTML parsing if the events API is empty."
        )
    elif platform == "shopify":
        notes.append(
            "Parse the event page HTML directly; Shopify stores may expose events as pages rather than products."
        )
    else:
        notes.append("Inspect the page structure and prefer structured data or embedded JSON when available.")

    if kind == "single_event_page":
        notes.append("Target URL looks like a single event page, so find a listing source before relying on it.")
    elif kind == "event_listing":
        notes.append("Target URL looks like an event listing or calendar page.")

    game = target.get("game_detected")
    if game:
        notes.append(f"Default or filter detected game as {game} when the page omits an explicit game label.")

    return notes


def assess_risk(target: dict) -> str:
    platform = target.get("platform") or "unknown"
    target_url = target.get("best_event_page") or ""
    scraper_path = ROOT / recommended_scraper_file(target.get("name", ""))

    if target_kind(target_url) == "single_event_page":
        return "high: discovered URL may not enumerate future events"
    if scraper_path.exists():
        return "low: scraper module already exists, validate behavior against this target"
    if platform == "unknown":
        return "medium: platform is unknown, page structure needs inspection"
    if platform == "shopify":
        return "medium: Shopify event pages often need custom HTML extraction"
    return "low: WordPress target should be compatible with shared helpers or simple HTML parsing"


def build_work_item(target: dict) -> dict:
    scraper_file = recommended_scraper_file(target.get("name", ""))
    target_url = target.get("best_event_page") or target.get("website") or ""

    return {
        "store_name": target.get("name", ""),
        "game": target.get("game_detected"),
        "website": target.get("website", ""),
        "target_url": target_url,
        "platform": target.get("platform", "unknown"),
        "recommended_scraper_file": scraper_file,
        "priority": target.get("priority", "medium"),
        "reason": target.get("reason", ""),
        "implementation_notes": build_implementation_notes(target, scraper_file),
        "risk": assess_risk(target),
    }


def main() -> None:
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        scraper_targets = json.load(f)

    scrape_now = [
        target
        for target in scraper_targets.get("scrape_now", [])
        if target.get("recommended_action") == "scrape_now"
    ]
    work_items = [build_work_item(target) for target in scrape_now]

    output = {
        "source": "data/scraper_targets.json",
        "work_items": work_items,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {OUTPUT_FILE}")
    print(f"  work_items: {len(work_items)}")


if __name__ == "__main__":
    main()
