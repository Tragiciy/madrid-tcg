#!/usr/bin/env python3
"""
Audit candidate store websites for event/tournament/calendar pages.

Reads candidate_stores.json, audits qualifying candidates, and writes
store_event_audit.json with priority rankings for scraper development.
"""

import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from shared.scraper_keywords import (
    FORMAT_KEYWORDS,
    GAME_KEYWORDS,
    extract_format_from_keywords,
    extract_game_from_keywords,
)

CANDIDATE_FILE = "candidate_stores.json"
OUTPUT_FILE = "store_event_audit.json"

REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

STRONG_EVENT_INTENT_KEYWORDS = [
    "event",
    "events",
    "evento",
    "eventos",
    "torneo",
    "torneos",
    "calendar",
    "calendario",
    "agenda",
    "actividades",
    "prerelease",
    "presentación",
    "presentacion",
]

WEAK_EVENT_INTENT_KEYWORDS = [
    "liga",
]

_WORD_BOUNDARY_CHARS = r"a-záéíóúñüç0-9"


def has_event_intent_keyword(text: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in text with word-boundary matching."""
    lower = text.lower()
    for kw in keywords:
        pattern = rf"(?<![{_WORD_BOUNDARY_CHARS}]){re.escape(kw)}(?![{_WORD_BOUNDARY_CHARS}])"
        if re.search(pattern, lower):
            return True
    return False


def find_event_intent_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return all matching keywords found in text (word-boundary matching)."""
    found = []
    lower = text.lower()
    for kw in keywords:
        pattern = rf"(?<![{_WORD_BOUNDARY_CHARS}]){re.escape(kw)}(?![{_WORD_BOUNDARY_CHARS}])"
        if re.search(pattern, lower):
            found.append(kw)
    return found


NOISE_PATTERNS = [
    "producto",
    "product",
    "tienda",
    "comprar",
    "carrito",
    "cart",
    "checkout",
    "account",
    "mi-cuenta",
    "categoria-producto",
    "accesorios",
    "sobres",
    "display",
    "booster",
    "mazos",
    "privacidad",
    "privacy",
    "cookies",
    "contact",
    "contacto",
    "blog",
    "tag",
    "category",
    "preventa",
    "preventas",
]

GENERIC_WIZARDS_PATTERNS = [
    "wpn.wizards.com",
    "magic.wizards.com",
]

SOCIAL_PLATFORMS = [
    "instagram.com",
    "facebook.com",
    "fb.me",
    "linktr.ee",
    "linktree",
]

PLATFORM_HINTS = {
    "wordpress": "wordpress",
    "wp-content": "wordpress",
    "shopify": "shopify",
    "myshopify": "shopify",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "fb.me": "facebook",
    "linktr.ee": "linktree",
    "linktree": "linktree",
    "discord": "discord",
    "discord.gg": "discord",
    "google.com/calendar": "google_calendar",
    "calendar.google": "google_calendar",
    "tabletop.wizards.com": "wizards_tabletop",
    "companion": "wizards_companion",
    "eventlink": "wizards_eventlink",
}


def is_generic_wizards_url(url: str) -> bool:
    if not url:
        return True
    lower = url.lower()
    return any(pattern in lower for pattern in GENERIC_WIZARDS_PATTERNS)


def is_social_only_url(url: str) -> bool:
    lower = url.lower()
    return any(platform in lower for platform in SOCIAL_PLATFORMS)


def is_noise_url(url: str) -> bool:
    lower = url.lower()
    return any(pattern in lower for pattern in NOISE_PATTERNS)


def fetch_page(url: str):
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text, resp.url
    except Exception as exc:
        return None, str(exc)


def normalize_url(base: str, href: str) -> str:
    if not href:
        return ""
    href_lower = href.lower().strip()
    if href_lower.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    absolute = urljoin(base, href)
    parsed = urlparse(absolute)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def classify_event_intent(href: str) -> str:
    """
    Classify event intent based on the raw href only.
    We avoid checking link text because navigation menus often label
    product/ecommerce links with event-related text (e.g. "Eventos")
    while the href points to a product page.
    """
    if has_event_intent_keyword(href, STRONG_EVENT_INTENT_KEYWORDS):
        return "strong"
    if has_event_intent_keyword(href, WEAK_EVENT_INTENT_KEYWORDS):
        return "weak"
    return "none"


def detect_platform_signals(html_text: str, page_url: str) -> list[str]:
    signals = []
    lower_html = html_text.lower()
    lower_url = page_url.lower()
    for hint, platform_name in PLATFORM_HINTS.items():
        if hint in lower_html or hint in lower_url:
            signal = f"platform:{platform_name}"
            if signal not in signals:
                signals.append(signal)
    return signals


def validate_event_page(url: str) -> dict:
    """Fetch and validate an event page. Returns dict with game/format detection."""
    result = {
        "url": url,
        "game": None,
        "format": None,
        "reachable": False,
        "error": None,
    }
    html_text, final_url = fetch_page(url)
    if html_text is None:
        result["error"] = final_url
        return result

    result["reachable"] = True
    soup = BeautifulSoup(html_text, "html.parser")
    visible_text = soup.get_text(separator=" ", strip=True)

    result["game"] = extract_game_from_keywords(visible_text, GAME_KEYWORDS)
    result["format"] = extract_format_from_keywords(visible_text, FORMAT_KEYWORDS)

    return result


def audit_store(store: dict) -> dict:
    name = store.get("name", "")
    website = store.get("website", "")
    address = store.get("address", "")
    external_id = store.get("external_id", "")

    result = {
        "name": name,
        "website": website,
        "address": address,
        "external_id": external_id,
        "event_pages": [],
        "best_event_page": None,
        "signals": [],
        "game_detected": None,
        "format_detected": None,
        "scraper_priority": "manual_review",
        "scraper_readiness": "manual_review",
        "notes": "",
    }

    if is_social_only_url(website):
        result["signals"].append("platform:social_only")
        result["notes"] = "Social-only page; not a straightforward scraper target."
        result["scraper_priority"] = "manual_review"
        result["scraper_readiness"] = "manual_review"
        return result

    html_text, final_url = fetch_page(website)
    if html_text is None:
        result["notes"] = f"Homepage unreachable: {final_url}"
        result["scraper_priority"] = "manual_review"
        result["scraper_readiness"] = "manual_review"
        return result

    soup = BeautifulSoup(html_text, "html.parser")
    visible_text = soup.get_text(separator=" ", strip=True)

    # Homepage keyword signals
    homepage_strong_keywords = find_event_intent_keywords(
        visible_text, STRONG_EVENT_INTENT_KEYWORDS
    )
    for kw in homepage_strong_keywords:
        result["signals"].append(f"homepage_event_keyword:{kw}")

    homepage_weak_keywords = find_event_intent_keywords(
        visible_text, WEAK_EVENT_INTENT_KEYWORDS
    )
    for kw in homepage_weak_keywords:
        result["signals"].append(f"homepage_event_keyword:{kw}")

    # Collect candidate event links
    strong_candidates = set()
    weak_candidates = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        text = tag.get_text(strip=True)

        # Check intent on raw href before normalization so query params etc.
        # are preserved for keyword matching.
        intent = classify_event_intent(href)

        normalized = normalize_url(final_url, href)
        if not normalized or is_noise_url(normalized):
            continue

        if intent == "strong":
            strong_candidates.add(normalized)
        elif intent == "weak":
            weak_candidates.add(normalized)

    # Validate event pages
    validated_pages = []

    for url in sorted(strong_candidates):
        validated = validate_event_page(url)
        validated_pages.append(validated)
        if validated["reachable"]:
            result["event_pages"].append(url)

    for url in sorted(weak_candidates):
        validated = validate_event_page(url)
        if validated["reachable"] and (validated["game"] or validated["format"]):
            validated_pages.append(validated)
            result["event_pages"].append(url)

    # Determine best event page
    for vp in validated_pages:
        if vp["url"] in result["event_pages"] and (vp["game"] or vp["format"]):
            result["best_event_page"] = vp["url"]
            result["game_detected"] = vp["game"]
            result["format_detected"] = vp["format"]
            break
    if result["best_event_page"] is None and result["event_pages"]:
        result["best_event_page"] = result["event_pages"][0]

    # Add game/format signals if best page has them
    if result["game_detected"]:
        result["signals"].append(f"event_page_game:{result['game_detected']}")
    if result["format_detected"]:
        result["signals"].append(f"event_page_format:{result['format_detected']}")

    # Platform hints
    platform_signals = detect_platform_signals(html_text, final_url)
    for sig in platform_signals:
        if sig not in result["signals"]:
            result["signals"].append(sig)

    # Determine priority and readiness
    has_validated_game_or_format = bool(
        result["game_detected"] or result["format_detected"]
    )
    has_event_pages = bool(result["event_pages"])
    has_homepage_intent = bool(homepage_strong_keywords or homepage_weak_keywords)

    if has_event_pages:
        if has_validated_game_or_format:
            result["scraper_priority"] = "high"
            result["scraper_readiness"] = "ready"
            result["notes"] = (
                f"Found {len(result['event_pages'])} clean event page(s); "
                "best page validates with game/format content."
            )
        else:
            result["scraper_priority"] = "medium"
            result["scraper_readiness"] = "possible"
            result["notes"] = (
                f"Found {len(result['event_pages'])} clean event page(s) "
                "but could not validate game/format content."
            )
    elif has_homepage_intent:
        result["scraper_priority"] = "medium"
        result["scraper_readiness"] = "possible"
        result["notes"] = (
            "Homepage has event intent keywords but no clean event page found."
        )
    else:
        result["scraper_priority"] = "low"
        result["scraper_readiness"] = "not_ready"
        result["notes"] = "Reachable website but no event intent signals detected."

    return result


def sort_results(results: list[dict]) -> list[dict]:
    readiness_order = {"ready": 0, "possible": 1, "manual_review": 2, "not_ready": 3}
    priority_order = {"high": 0, "medium": 1, "manual_review": 2, "low": 3}
    return sorted(
        results,
        key=lambda r: (
            readiness_order.get(r["scraper_readiness"], 99),
            priority_order.get(r["scraper_priority"], 99),
            r["name"].lower(),
        ),
    )


def main():
    with open(CANDIDATE_FILE, "r", encoding="utf-8") as f:
        stores = json.load(f)

    candidates = [
        s
        for s in stores
        if s.get("status") == "candidate_new_store"
        and s.get("website")
        and not is_generic_wizards_url(s.get("website"))
    ]

    print(f"Auditing {len(candidates)} candidate store(s)...\n")

    results = []
    failed = 0
    for store in candidates:
        name = store.get("name", "")
        print(f"  → {name} ...", end=" ", flush=True)
        result = audit_store(store)
        results.append(result)
        if (
            result["scraper_readiness"] == "manual_review"
            and "unreachable" in result["notes"]
        ):
            failed += 1
            print("UNREACHABLE")
        else:
            print(
                f"{result['scraper_readiness'].upper()} / {result['scraper_priority'].upper()}"
            )

    sorted_results = sort_results(results)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_results, f, ensure_ascii=False, indent=2)

    counts = {"high": 0, "medium": 0, "manual_review": 0, "low": 0}
    readiness_counts = {"ready": 0, "possible": 0, "manual_review": 0, "not_ready": 0}
    for r in results:
        counts[r["scraper_priority"]] = counts.get(r["scraper_priority"], 0) + 1
        readiness_counts[r["scraper_readiness"]] = (
            readiness_counts.get(r["scraper_readiness"], 0) + 1
        )

    print(f"\nAudit complete. Wrote {OUTPUT_FILE}")
    print(f"  Audited stores: {len(results)}")
    print(f"  High:           {counts['high']}")
    print(f"  Medium:         {counts['medium']}")
    print(f"  Manual review:  {counts['manual_review']}")
    print(f"  Low:            {counts['low']}")
    print(f"  Failed/unreachable: {failed}")
    print(f"\n  Ready:          {readiness_counts['ready']}")
    print(f"  Possible:       {readiness_counts['possible']}")
    print(f"  Manual review:  {readiness_counts['manual_review']}")
    print(f"  Not ready:      {readiness_counts['not_ready']}")


if __name__ == "__main__":
    main()
