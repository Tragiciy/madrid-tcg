"""Source-specific corrections of start times.

Bandai TCG+ (One Piece, Digimon, Dragon Ball, Gundam): some organizers type
their evening start in 12-hour form without "PM", and Bandai stores it as
is. The raw API says `"start_datetime": "2026-11-21T05:00:00"` with
`"timezone": "Europe/Madrid"` for a store tournament that the same shop runs
at 16:30 or 17:30 in other weeks. Across every Spanish city and week
(checked 2026-11-18, 3154 Bandai events) starts cluster at 04:30–06:59
(78 events), nothing starts 07:00–08:59, and real mornings begin at 09:00.
So a Bandai start from 01:00 to 06:59 is read as PM: +12 hours.

00:00 is left alone: sources send midnight for "no time given".

A corrected event keeps what the source said in `datetime_start_source`
and carries `time_corrected: true`; `datetime_start` (used for sorting, the
day sections, the time filter, calendars and the SEO pages) is the fixed
time. The rule is idempotent: an event already corrected is not touched.
"""

import logging
from datetime import datetime, timedelta
from typing import Iterable

from pipeline.config import TZ

logger = logging.getLogger(__name__)

# Local start hours read as a missing "PM": [first, last] inclusive.
BANDAI_PM_HOURS = (1, 6)
BANDAI_HOST = "bandai-tcg-plus.com"


def is_bandai(e: dict) -> bool:
    return (str(e.get("source_event_id") or "").startswith("bandai:")
            or BANDAI_HOST in str(e.get("source_url") or ""))


def _plus_12h(iso: str) -> str:
    """The same wall-clock day, 12 hours later, with that time's own offset
    (correct across a daylight-saving change)."""
    local = datetime.fromisoformat(iso).astimezone(TZ).replace(tzinfo=None)
    return (local + timedelta(hours=12)).replace(tzinfo=TZ).isoformat()


def correct_start_time(e: dict) -> bool:
    """Apply the rule to one event in place. True if it changed."""
    if e.get("time_corrected") or not is_bandai(e):
        return False
    start = e.get("datetime_start")
    if not start:
        return False
    hour = datetime.fromisoformat(start).astimezone(TZ).hour
    first, last = BANDAI_PM_HOURS
    if not first <= hour <= last:
        return False
    e["datetime_start_source"] = start
    e["datetime_start"] = _plus_12h(start)
    end = e.get("datetime_end")
    if end and datetime.fromisoformat(end) <= datetime.fromisoformat(e["datetime_start"]):
        e["datetime_end"] = _plus_12h(end)
    e["time_corrected"] = True
    return True


def apply_time_fixes(events: Iterable[dict], label: str = "events") -> int:
    n = sum(1 for e in events if correct_start_time(e))
    if n:
        logger.info("Corrected the start time of %d %s (Bandai, hour without PM)", n, label)
    return n
