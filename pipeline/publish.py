"""
Publish the static read API (public/api/v1) from the full event history.

This is the contract every client reads — the website today, a PWA or a
native app later. See docs/api-v1.md. Files are plain JSON served from the
CDN; nothing is computed per request.

    api/v1/cities.json                   every city in shared/cities.py
    api/v1/<city>/index.json             weeks available + upcoming range
    api/v1/<city>/stores.json            store registry for the city
    api/v1/<city>/events/upcoming.json   previous week … +UPCOMING_WEEKS
    api/v1/<city>/events/<YYYY-Www>.json one ISO week (Madrid dates)

Pure apart from write_api(); no network.
"""

import json
import logging
import pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from pipeline.config import TZ
from pipeline.reconcile import city_of, event_key
from pipeline.share import share_path
from shared.cities import CITIES, City

logger = logging.getLogger(__name__)

API_DIR = pathlib.Path("public/api/v1")
API_VERSION = 1
UPCOMING_WEEKS = 4  # weeks after the current one in upcoming.json

# Fields every client gets, in the shared event schema (§8).
EVENT_FIELDS = (
    "title", "store", "store_id", "store_address", "game", "format",
    "format_official", "best_of", "datetime_start", "datetime_end", "source_url",
    "capacity", "registered", "participants", "full",
    # Set when pipeline/time_fixes.py corrected the start time.
    "time_corrected", "datetime_start_source",
)
STORE_FIELDS = ("id", "name", "address", "lat", "lng", "website", "notes")


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(week: str) -> tuple[date, date]:
    """'2026-W47' → (Monday, next Monday)."""
    monday = date.fromisocalendar(int(week[:4]), int(week[6:]), 1)
    return monday, monday + timedelta(days=7)


def _madrid(value: Optional[str]) -> Optional[str]:
    """Normalise to the Madrid offset, so the date in the string is always
    the Madrid calendar date that decides the week file (clients bucket by
    the string's date)."""
    if not value:
        return value
    try:
        return datetime.fromisoformat(value).astimezone(TZ).isoformat()
    except ValueError:
        return value


def _public_event(e: dict) -> dict:
    out = {"id": event_key(e)}
    out.update({k: e[k] for k in EVENT_FIELDS if e.get(k) not in (None, "")})
    for k in ("datetime_start", "datetime_end"):
        if k in out:
            out[k] = _madrid(out[k])
    return out


def _visible(e: dict, now: datetime) -> bool:
    """Past events are history (shown dimmed); future ones only while active."""
    try:
        start = datetime.fromisoformat(e["datetime_start"])
    except (KeyError, TypeError, ValueError):
        return False
    return start <= now or e.get("is_active", True)


def public_events(events: list[dict], now_iso: str) -> list[dict]:
    """Visible events in the public schema (what the API and pages show)."""
    now = datetime.fromisoformat(now_iso)
    return [_public_event(e) for e in events if _visible(e, now)]


def events_in(events: list[dict], city: str) -> list[dict]:
    """The events of one city (event `city`, set from the store's registry city)."""
    return [e for e in events if city_of(e) == city]


def stores_in(registry, city: str) -> list[dict]:
    """The registry stores of one city."""
    return [s for s in registry.stores if registry.city_of(s) == city]


def build_api(events: list[dict], registry, now_iso: str,
              cities: tuple[City, ...] = CITIES) -> dict[str, dict]:
    """{relative path: JSON document}: every city's files plus cities.json.
    A city with no events yet still gets its index, stores and upcoming
    files, so every city listed in cities.json can be loaded."""
    docs: dict[str, dict] = {}
    store_counts: dict[str, int] = {}
    for c in cities:
        city_docs = _build_city(events_in(events, c.id), stores_in(registry, c.id), now_iso, c.id)
        store_counts[c.id] = len(city_docs[f"{c.id}/stores.json"]["stores"])
        docs.update(city_docs)
    docs["cities.json"] = {
        "api_version": API_VERSION, "updated_at": now_iso,
        "cities": [{"id": c.id, "name": c.name, "timezone": c.timezone,
                    "lat": c.lat, "lng": c.lng, "stores": store_counts[c.id]}
                   for c in cities],
    }
    return docs


def _build_city(events: list[dict], city_stores: list[dict], now_iso: str,
                city: str) -> dict[str, dict]:
    """{relative path: JSON document} for one city's events and stores."""
    now = datetime.fromisoformat(now_iso)
    today = now.astimezone(TZ).date()
    this_monday = today - timedelta(days=today.weekday())
    up_from = this_monday - timedelta(days=7)
    up_to = this_monday + timedelta(days=7 * (UPCOMING_WEEKS + 1))

    by_week: dict[str, list[dict]] = defaultdict(list)
    upcoming: list[dict] = []
    for e in sorted(events, key=lambda e: (e.get("datetime_start") or "", event_key(e))):
        if not _visible(e, now):
            continue
        day = datetime.fromisoformat(e["datetime_start"]).astimezone(TZ).date()
        pe = _public_event(e)
        path = share_path(pe, today)
        if path:
            pe["share"] = path  # static page with a link preview (pipeline/share.py)
        by_week[iso_week(day)].append(pe)
        if up_from <= day < up_to:
            upcoming.append(pe)

    meta = {"api_version": API_VERSION, "city": city, "updated_at": now_iso}
    docs: dict[str, dict] = {}
    for week, evs in by_week.items():
        start, end = week_bounds(week)
        # No updated_at in week files: a past week then stays byte-identical
        # run to run (same ETag → 304s, no git churn).
        docs[f"{city}/events/{week}.json"] = {
            "api_version": API_VERSION, "city": city, "week": week,
            "from": start.isoformat(), "to": end.isoformat(), "events": evs,
        }
    docs[f"{city}/events/upcoming.json"] = {
        **meta, "from": up_from.isoformat(), "to": up_to.isoformat(), "events": upcoming,
    }
    stores = [
        {k: s.get(k) for k in STORE_FIELDS if s.get(k) is not None}
        for s in sorted(city_stores, key=lambda s: s["name"].lower())
    ]
    docs[f"{city}/stores.json"] = {**meta, "stores": stores}
    docs[f"{city}/index.json"] = {
        **meta,
        "upcoming": {"from": up_from.isoformat(), "to": up_to.isoformat(), "events": len(upcoming)},
        "weeks": {w: len(v) for w, v in sorted(by_week.items())},
    }
    return docs


def write_api(docs: dict[str, dict], api_dir: Optional[pathlib.Path] = None) -> int:
    """Write every document; delete week files no longer produced. Returns #files."""
    api_dir = api_dir or API_DIR
    keep = set()
    written = 0
    for rel, doc in docs.items():
        path = api_dir / rel
        keep.add(path.resolve())
        # Compact: these files are what every visitor downloads.
        text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue  # unchanged: keep the file (and its ETag) as is
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        written += 1
    # Remove week files no longer produced — only for the cities published here.
    cities = {rel.split("/", 1)[0] for rel in docs if "/events/" in rel}
    for city in cities:
        for stale in list((api_dir / city / "events").glob("*.json")):
            if stale.resolve() not in keep:
                stale.unlink()
    logger.info("Wrote API: %s (%d of %d files changed)", api_dir, written, len(docs))
    return written
