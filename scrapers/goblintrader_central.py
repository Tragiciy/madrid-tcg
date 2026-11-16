"""
Goblintrader Central — https://goblintrader.es/gb/content/12-madrid

goblintrader.es shop page with a server-rendered event list, parsed by
shared.goblintrader. The old URL /gb/madrid redirects here.

Note: this page is the shop the site calls "Madrid Sur" (Ronda de
Toledo 28). It is the page this scraper has always read; the site's own
"Central" shop is in Fuenlabrada (/gb/content/9-central).
"""

import json
import logging

from shared.goblintrader import scrape_store_page

logger = logging.getLogger(__name__)

STORE = "Goblintrader Central"
PAGE_URL = "https://goblintrader.es/gb/content/12-madrid"


def scrape() -> list[dict]:
    return scrape_store_page(PAGE_URL, STORE)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
