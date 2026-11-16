"""
Goblintrader Madrid Sur (Ronda de Toledo 28) —
https://goblintrader.es/gb/content/12-madrid

goblintrader.es shop page with a server-rendered event list, parsed by
shared.goblintrader. The old URL /gb/madrid redirects here.

Until 2026-11 this store was mislabelled "Goblintrader Central"; its
history is renamed via data/store_renames.json. The site's real "Central"
shop is in Fuenlabrada (scrapers/goblintrader_fuenlabrada.py).
"""

import json
import logging

from shared.goblintrader import scrape_store_page

logger = logging.getLogger(__name__)

STORE = "Goblintrader Madrid Sur"
PAGE_URL = "https://goblintrader.es/gb/content/12-madrid"


def scrape() -> list[dict]:
    return scrape_store_page(PAGE_URL, STORE)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
