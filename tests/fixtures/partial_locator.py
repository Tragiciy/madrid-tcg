"""A locator whose Bilbao search fails (tests/test_multi_city.py)."""
from shared.locator_common import for_each_city

STORE = "Partial Locator"


def scrape():
    def fetch(city):
        if city.id == "bilbao":
            raise TimeoutError("read timed out")
        return [{"title": "Liga", "store": f"{city.id} shop", "source_event_id": f"x:{city.id}",
                 "datetime_start": "2026-11-20T19:00:00+01:00"}]
    return for_each_city(STORE, fetch)
