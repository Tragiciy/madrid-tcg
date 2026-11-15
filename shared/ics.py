"""
shared/ics.py — minimal iCalendar (RFC 5545) reader for public store calendars.

Covers what Google Calendar exports actually use: line unfolding, VEVENT,
DTSTART/DTEND with TZID, UTC ("Z") or all-day dates, RRULE expansion
(via dateutil), EXDATE, and RECURRENCE-ID overrides (moved or cancelled
single occurrences). No network requests and no I/O on import.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr


@dataclass
class IcsEvent:
    uid: str
    summary: str
    start: datetime          # timezone-aware
    end: Optional[datetime]
    location: str
    description: str
    url: str


_ESCAPES = {"\\n": "\n", "\\N": "\n", "\\,": ",", "\\;": ";", "\\\\": "\\"}
_ESCAPE_RE = re.compile(r"\\[nN,;\\]")


def _unescape(value: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group(0)], value)


def _unfold(text: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _split(line: str) -> tuple[str, dict[str, str], str]:
    """'DTSTART;TZID=Europe/Madrid:2026…' → ('DTSTART', {'TZID': …}, '2026…')."""
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), dict(p.split("=", 1) for p in params if "=" in p), value


def _parse_dt(value: str, params: dict[str, str], default_tz) -> Optional[datetime]:
    """Aware datetime, or None for all-day dates (no start time to show)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return None
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    tz = default_tz
    if "TZID" in params:
        try:
            tz = ZoneInfo(params["TZID"].strip('"'))
        except (ZoneInfoNotFoundError, ValueError):
            tz = default_tz
    return naive.replace(tzinfo=tz)


def _blocks(lines: list[str]) -> Iterator[list[tuple[str, dict, str]]]:
    current: Optional[list] = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = []
        elif line == "END:VEVENT":
            if current is not None:
                yield current
            current = None
        elif current is not None and ":" in line:
            current.append(_split(line))


def parse_ics(text: str, window_start: datetime, window_end: datetime,
              default_tz: str = "Europe/Madrid") -> list[IcsEvent]:
    """
    Every timed occurrence starting within [window_start, window_end).
    All-day events are skipped (a tournament calendar needs a start time).
    """
    tz = ZoneInfo(default_tz)
    masters: list[dict] = []
    overrides: dict[tuple[str, datetime], dict] = {}

    for props in _blocks(_unfold(text)):
        ev: dict = {"exdates": set(), "rrule": None}
        for name, params, value in props:
            if name in ("DTSTART", "DTEND"):
                ev[name] = _parse_dt(value, params, tz)
            elif name == "RECURRENCE-ID":
                ev["recurrence_id"] = _parse_dt(value, params, tz)
            elif name == "EXDATE":
                for part in value.split(","):
                    dt = _parse_dt(part, params, tz)
                    if dt:
                        ev["exdates"].add(dt)
            elif name == "RRULE":
                ev["rrule"] = value
            elif name in ("UID", "SUMMARY", "LOCATION", "DESCRIPTION", "URL", "STATUS"):
                ev[name] = _unescape(value)
        if not ev.get("DTSTART"):
            continue
        if ev.get("recurrence_id"):
            overrides[(ev.get("UID", ""), ev["recurrence_id"])] = ev
        else:
            masters.append(ev)

    out: list[IcsEvent] = []

    def emit(ev: dict, start: datetime, duration: Optional[timedelta]) -> None:
        if ev.get("STATUS", "").upper() == "CANCELLED":
            return
        if not (window_start <= start < window_end):
            return
        out.append(IcsEvent(
            uid=ev.get("UID", ""),
            summary=ev.get("SUMMARY", "").strip(),
            start=start,
            end=start + duration if duration else None,
            location=ev.get("LOCATION", ""),
            description=ev.get("DESCRIPTION", ""),
            url=ev.get("URL", ""),
        ))

    for ev in masters:
        start = ev["DTSTART"]
        duration = (ev["DTEND"] - start) if ev.get("DTEND") else None
        if not ev["rrule"]:
            emit(ev, start, duration)
            continue
        rule = rrulestr(ev["rrule"], dtstart=start)
        # Overrides are keyed by the occurrence's original start.
        for occ in rule.between(window_start - timedelta(days=1), window_end, inc=True):
            if occ in ev["exdates"]:
                continue
            override = overrides.pop((ev.get("UID", ""), occ), None)
            if override is not None:
                o_start = override["DTSTART"]
                o_dur = (override["DTEND"] - o_start) if override.get("DTEND") else duration
                emit({**ev, **override}, o_start, o_dur)
            else:
                emit(ev, occ, duration)

    # Overrides whose master is outside the expansion (still valid one-offs).
    for ov in overrides.values():
        if ov.get("DTSTART"):
            emit(ov, ov["DTSTART"], (ov["DTEND"] - ov["DTSTART"]) if ov.get("DTEND") else None)

    return sorted(out, key=lambda e: e.start)
