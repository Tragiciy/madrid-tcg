"""
Run one scraper in its own process and write the result as JSON.

    python -m tools.run_scraper scrapers.arte9 /tmp/out.json

Output: {"events": [...], "error": str | null, "store": str | null,
         "city_errors": {city id: error}}  (locators: cities whose search failed)

aggregator.py launches this per scraper so a hang, crash or leaked browser
in one scraper cannot take down the whole run. Kept outside scrapers/
because it has no scrape().
"""

import json
import logging
import sys

from pipeline.runner import _run_scraper
from shared import locator_common


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    module_name, out_path = argv[1], argv[2]
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s %(levelname)s [{module_name}] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    events, error, store = _run_scraper(module_name)
    city_errors = dict(locator_common.CITY_ERRORS)  # filled by for_each_city
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"events": events, "error": error, "store": store, "city_errors": city_errors},
            fh, ensure_ascii=False, default=str,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
