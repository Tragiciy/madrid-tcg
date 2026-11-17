"""
tools/find_melee_organizers.py — propose Melee organizers for our stores.

For every store in data/stores.json without a "melee:" alias, searches
Melee organizers by the store's name and prints candidates. A candidate is
"strong" when its contact email's domain is the store's website domain.

Review the output by hand, then add the right ids to the store's
aliases.external as "melee:<id>" — scrapers/locator_melee.py reads those.

    python -m tools.find_melee_organizers [--all]
"""

import re
import sys
import unicodedata
from urllib.parse import urlparse

from scrapers.locator_melee import search_organizations
from shared.locator_common import session
from shared.store_registry import StoreRegistry

_GENERIC = {"juegos", "games", "game", "store", "tienda", "madrid", "tcg", "comics", "cards",
            "sl", "s.l.", "the", "de", "la", "el", "los", "las", "y"}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def queries_for(name: str) -> list[str]:
    """The full name, then its most distinctive word (Melee matches substrings)."""
    base = re.sub(r"\(.*?\)", "", name).strip()
    words = [w for w in re.findall(r"[\w']+", _ascii(base)) if w not in _GENERIC and len(w) > 3]
    out = [base]
    if words:
        out.append(max(words, key=len))
    return list(dict.fromkeys(out))


def domain(url_or_email) -> str:
    text = (url_or_email or "").strip().lower()
    if "@" in text:
        text = text.split("@", 1)[1]
    elif text:
        text = urlparse(text if "//" in text else "//" + text).netloc
    return text.removeprefix("www.")


def main() -> None:
    show_all = "--all" in sys.argv
    registry = StoreRegistry.load()
    sess = session()
    for store in sorted(registry.known_stores(), key=lambda s: s["name"]):
        record = registry.find(store["name"]) or {}
        aliases = (record.get("aliases") or {}).get("external") or []
        have = [a for a in aliases if a.startswith("melee:")]
        if have and not show_all:
            continue
        site = domain(record.get("website"))
        seen: dict[int, dict] = {}
        for q in queries_for(store["name"]):
            for org in search_organizations(sess, q):
                seen.setdefault(org["ID"], org)
        print(f"\n## {store['name']}  ({site or 'no website'})  {' '.join(have)}")
        for org in seen.values():
            email = org.get("ContactEmail") or ""
            strong = site and domain(email) == site
            print(f"  {'STRONG ' if strong else '       '}melee:{org['ID']:<6} {org['Name']!r:40} "
                  f"{email:35} tournaments={org.get('TournamentCount')}")


if __name__ == "__main__":
    main()
