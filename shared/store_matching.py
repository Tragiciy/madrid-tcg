"""
shared/store_matching.py — store name/address normalization and fuzzy matching.

No imports from aggregator or discoverers to avoid circular dependencies.
No network requests on import.
"""

import json
import pathlib
import re
import sys
import unicodedata
from difflib import SequenceMatcher


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

LEGAL_SUFFIX_PATTERNS = [
    r"\bs\.l\.\b",
    r"\bsl\b",
    r"\bs\.a\.\b",
    r"\bsa\b",
    r"\bs\.l\.l\.\b",
    r"\bsll\b",
    r"\bs\.l\.u\.\b",
    r"\bslu\b",
    r"\bc\.v\.\b",
    r"\bcv\b",
    r"\bltda\b",
    r"\bltd\b",
]

SPANISH_ADDRESS_TERMS = {
    "calle": "c",
    "avenida": "av",
    "plaza": "pl",
    "paseo": "pas",
    "carretera": "ctra",
    "urbanizacion": "urb",
    "poligono": "pol",
    "numero": "n",
    "local": "loc",
    "bajo": "bj",
    "puerta": "pta",
    "escalera": "esc",
    "planta": "pl",
    "torre": "tr",
    "edificio": "edif",
}


def _remove_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", value)
        if unicodedata.category(c) != "Mn"
    )


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def normalize_name(value: str) -> str:
    """
    Lowercase, remove accents, strip legal suffixes, remove punctuation,
    and collapse whitespace.
    """
    if not value:
        return ""
    v = value.lower()
    v = _remove_accents(v)
    for pat in LEGAL_SUFFIX_PATTERNS:
        v = re.sub(pat, " ", v)
    v = re.sub(r"[^\w\s]", " ", v)
    v = _collapse_whitespace(v)
    return v.strip()


def normalize_address(value: str) -> str:
    """
    Lowercase, remove accents, remove punctuation, collapse whitespace,
    and normalize common Spanish address terms.
    """
    if not value:
        return ""
    v = value.lower()
    v = _remove_accents(v)
    v = re.sub(r"[^\w\s]", " ", v)
    v = _collapse_whitespace(v)
    tokens = v.split()
    normalized = [SPANISH_ADDRESS_TERMS.get(t, t) for t in tokens]
    return " ".join(normalized)


# ---------------------------------------------------------------------------
# Existing-store loading
# ---------------------------------------------------------------------------

def _extract_store_meta(text: str) -> list[dict]:
    """Extract {name, address} entries from STORE_META in config.js."""
    stores: list[dict] = []
    pattern = re.compile(r"var\s+STORE_META\s*=\s*\{(.*?)\};", re.DOTALL)
    m = pattern.search(text)
    if not m:
        return stores
    inner = m.group(1)
    entry_pat = re.compile(r"'([^']+)'\s*:\s*\{([^{}]*)\}", re.DOTALL)
    for em in entry_pat.finditer(inner):
        name = em.group(1)
        block = em.group(2)
        addr_m = re.search(r"address\s*:\s*'([^']*)'", block)
        address = addr_m.group(1).strip() if addr_m else ""
        stores.append({"name": name, "address": address})
    return stores


def _extract_string_map(text: str, var_name: str) -> dict[str, str]:
    """Extract simple 'key': 'value' mappings from a JS var declaration."""
    result: dict[str, str] = {}
    pattern = re.compile(
        rf"var\s+{re.escape(var_name)}\s*=\s*\{{(.*?)\}};", re.DOTALL
    )
    m = pattern.search(text)
    if not m:
        return result
    inner = m.group(1)
    for km in re.finditer(r"'([^']*)'\s*:\s*'([^']*)'", inner):
        result[km.group(1)] = km.group(2)
    return result


def load_existing_stores() -> list[dict]:
    """
    Load existing store references from public/config.js (STORE_META +
    STORE_ADDRESSES fallback) or, if that fails, from public/events.json.
    """
    config_path = pathlib.Path("public/config.js")
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        stores = _extract_store_meta(text)
        if stores:
            addresses = _extract_string_map(text, "STORE_ADDRESSES")
            for s in stores:
                if not s["address"] and s["name"] in addresses:
                    s["address"] = addresses[s["name"]]
            return stores

    events_path = pathlib.Path("public/events.json")
    if events_path.exists():
        try:
            events = json.loads(events_path.read_text(encoding="utf-8"))
            seen: set[str] = set()
            stores: list[dict] = []
            for e in events:
                name = e.get("store")
                if name and name not in seen:
                    seen.add(name)
                    stores.append({"name": name, "address": ""})
            return stores
        except Exception:
            pass

    return []


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_existing_store(
    candidate: dict, existing_stores: list[dict], debug: bool = False
) -> dict:
    """
    Compare a discovered candidate against known stores.

    Returns a dict with keys:
      - matched_existing_store (str | None)
      - confidence (float)
      - status (str)
    """
    raw_cand_name = candidate.get("name", "")
    cand_name = normalize_name(raw_cand_name)
    cand_addr = normalize_address(candidate.get("address", ""))

    if not cand_name:
        return {
            "matched_existing_store": None,
            "confidence": 0.0,
            "status": "candidate_new_store",
        }

    if debug:
        print(
            f"[DEBUG] candidate='{raw_cand_name}' norm='{cand_name}' addr='{cand_addr}'",
            file=sys.stderr,
        )

    # Phase 1 — exact normalized name match (short-circuit)
    for store in existing_stores:
        ex_name = normalize_name(store.get("name", ""))
        if cand_name == ex_name:
            if debug:
                print(
                    f"[DEBUG] exact name match → '{store['name']}' (0.9)",
                    file=sys.stderr,
                )
            return {
                "matched_existing_store": store["name"],
                "confidence": 0.9,
                "status": "matched_existing_store",
            }

    # Phase 2 — exact normalized address match (short-circuit)
    if cand_addr and len(cand_addr) > 5:
        for store in existing_stores:
            ex_addr = normalize_address(store.get("address", ""))
            if ex_addr and cand_addr == ex_addr:
                if debug:
                    print(
                        f"[DEBUG] exact address match → '{store['name']}' (0.95)",
                        file=sys.stderr,
                    )
                return {
                    "matched_existing_store": store["name"],
                    "confidence": 0.95,
                    "status": "matched_existing_store",
                }

    # Phase 3 — safe fuzzy best-match
    best_store_name: str | None = None
    best_score = 0.0

    for store in existing_stores:
        ex_name = normalize_name(store.get("name", ""))
        if not ex_name:
            continue
        score = SequenceMatcher(None, cand_name, ex_name).ratio()
        if score > best_score:
            best_score = score
            best_store_name = store["name"]

    if debug:
        print(
            f"[DEBUG] best fuzzy='{best_store_name}' score={best_score:.3f}",
            file=sys.stderr,
        )

    if best_score >= 0.85:
        if debug:
            print(f"[DEBUG] fuzzy >= 0.85 → matched (0.80)", file=sys.stderr)
        return {
            "matched_existing_store": best_store_name,
            "confidence": 0.80,
            "status": "matched_existing_store",
        }

    if best_score >= 0.60:
        if debug:
            print(f"[DEBUG] fuzzy 0.60-0.84 → possible_duplicate (0.65)", file=sys.stderr)
        return {
            "matched_existing_store": best_store_name,
            "confidence": 0.65,
            "status": "possible_duplicate",
        }

    if debug:
        print(f"[DEBUG] no reliable match → candidate_new_store (0.0)", file=sys.stderr)
    return {
        "matched_existing_store": None,
        "confidence": 0.0,
        "status": "candidate_new_store",
    }
