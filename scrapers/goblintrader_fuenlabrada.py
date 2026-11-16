"""
Goblintrader Central (Fuenlabrada), Calle Almanzor 10 —
https://goblintrader.es/gb/content/9-central

goblintrader.es shop page with a server-rendered event list, parsed by
shared.goblintrader.
"""

import json
import logging

from shared.goblintrader import scrape_store_page

logger = logging.getLogger(__name__)

STORE = "Goblintrader Central (Fuenlabrada)"
PAGE_URL = "https://goblintrader.es/gb/content/9-central"


def scrape() -> list[dict]:
    return scrape_store_page(PAGE_URL, STORE)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = scrape()
    print(json.dumps(out[:5], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(out)}")
