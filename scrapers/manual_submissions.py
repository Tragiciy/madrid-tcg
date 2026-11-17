"""
scrapers/manual_submissions.py — events that stores submitted through the
/submit/ form and a maintainer approved (data/manual_events.json, written
by tools/apply_submission.py). Weekly entries are expanded within the
usual locator window. A store's own submission outranks locator copies of
the same event (it is not a locator source).
"""

import json
import pathlib
from datetime import date, datetime, timedelta

from shared.locator_common import TZ, WINDOW_DAYS
from shared.scraper_keywords import extract_best_of, extract_format_for_event

STORE = "Store submissions"
# Having no approved submissions is the normal state, not a broken source.
EMPTY_IS_NORMAL = True
DATA_FILE = pathlib.Path("data/manual_events.json")
SITE_URL = "https://madrid-tcg.pages.dev/"


def _occurrences(entry: dict, today: date) -> list[date]:
    first = date.fromisoformat(entry["date"])
    last = date.fromisoformat(entry["weekly_until"]) if entry.get("weekly_until") else first
    end = min(last, today + timedelta(days=WINDOW_DAYS))
    days, d = [], first
    while d <= end:
        if d >= today - timedelta(days=1):
            days.append(d)
        d += timedelta(days=7)
    return days


def parse(entries: list[dict], today: date, scraped_at: str) -> list[dict]:
    out = []
    for entry in entries:
        hh, mm = (int(x) for x in entry["time"].split(":"))
        game = entry.get("game") or None
        for day in _occurrences(entry, today):
            start = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
            fmt, fmt_official = extract_format_for_event(title=entry["title"], game=game)
            out.append({
                "title": entry["title"], "store": entry["store"], "game": game,
                "format": fmt, "format_official": fmt_official,
                "best_of": extract_best_of(entry["title"]),
                "datetime_start": start.isoformat(), "datetime_end": None,
                "language": "es", "source_url": entry.get("link") or SITE_URL,
                "source_event_id": f"manual:{entry['id']}:{day.isoformat()}",
                "scraped_at": scraped_at,
            })
    return out


def scrape() -> list[dict]:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))  # raises if missing/corrupt
    now = datetime.now(TZ)
    return parse(data.get("events", []), now.date(), now.isoformat())


if __name__ == "__main__":
    print(json.dumps(scrape()[:5], indent=2, ensure_ascii=False))
