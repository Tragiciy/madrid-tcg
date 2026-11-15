"""
shared/ics.py — minimal iCalendar (RFC 5545) reader for public store calendars.

Covers what Google Calendar exports actually use: line unfolding, VEVENT,
DTSTART/DTEND with TZID, UTC ("Z") or all-day dates, RRULE expansion
(via dateutil), EXDATE, and RECURRENCE-ID overrides (moved or cancelled
single occurrences). No network requests and no I/O on import.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

logger = logging.getLogger(__name__)


@dataclass
class IcsEvent:
    uid: str
    summary: str
    start: datetime          # timezone-aware
    end: Optional[datetime]
    location: str
    description: str
    url: str
    # For an occurrence of a recurring series: the original (scheduled)
    # start, which stays stable when that single occurrence is moved.
    occurrence_of: Optional[datetime] = None


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
    parsed = {}
    for p in params:
        if "=" in p:
            k, v = p.split("=", 1)
            parsed[k.upper()] = v
    return name.upper(), parsed, value


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
    """VEVENT property lists. Nested components (VALARM…) are ignored, so a
    reminder's DESCRIPTION/SUMMARY never overwrites the event's."""
    current: Optional[list] = None
    depth = 0
    for line in lines:
        upper = line.upper()
        if current is None:
            if upper == "BEGIN:VEVENT":
                current, depth = [], 0
            continue
        if upper.startswith("BEGIN:"):
            depth += 1
        elif upper.startswith("END:"):
            if depth == 0:  # END:VEVENT
                yield current
                current = None
            else:
                depth -= 1
        elif depth == 0 and ":" in line:
            current.append(_split(line))


def _parse_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value.strip()[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _normalize_rrule(rule: str, dtstart: datetime) -> str:
    """dateutil rejects a date-only or floating UNTIL with an aware DTSTART;
    rewrite it as the end of that day (or that local time) in UTC."""
    def fix(m: re.Match) -> str:
        val = m.group(1)
        if val.endswith("Z"):
            return m.group(0)
        if re.fullmatch(r"\d{8}", val):
            local = datetime.strptime(val, "%Y%m%d").replace(hour=23, minute=59, second=59)
        else:
            try:
                local = datetime.strptime(val, "%Y%m%dT%H%M%S")
            except ValueError:
                return m.group(0)
        utc = local.replace(tzinfo=dtstart.tzinfo).astimezone(timezone.utc)
        return "UNTIL=" + utc.strftime("%Y%m%dT%H%M%SZ")
    return re.sub(r"UNTIL=([0-9TZ]+)", fix, rule)


def parse_ics(text: str, window_start: datetime, window_end: datetime,
              default_tz: str = "Europe/Madrid") -> list[IcsEvent]:
    """
    Every timed occurrence starting within [window_start, window_end).
    All-day events are skipped (a tournament calendar needs a start time).
    A series whose RRULE cannot be parsed is logged and skipped; it never
    fails the whole feed.
    """
    tz = ZoneInfo(default_tz)
    masters: list[dict] = []
    # (uid, original start) and (uid, original date) → override; an override
    # always suppresses its occurrence, even if its own start is unusable.
    overrides: dict[tuple, dict] = {}
    date_overrides: dict[tuple, dict] = {}

    for props in _blocks(_unfold(text)):
        ev: dict = {"exdates": set(), "rrule": None, "SEQUENCE": 0}
        rid_date: Optional[date] = None
        for name, params, value in props:
            if name in ("DTSTART", "DTEND"):
                ev[name] = _parse_dt(value, params, tz)
            elif name == "RECURRENCE-ID":
                ev["recurrence_id"] = _parse_dt(value, params, tz)
                if ev["recurrence_id"] is None:
                    rid_date = _parse_date(value)
            elif name == "EXDATE":
                for part in value.split(","):
                    dt = _parse_dt(part, params, tz)
                    if dt:
                        ev["exdates"].add(dt)
            elif name == "RRULE":
                ev["rrule"] = value
            elif name == "SEQUENCE":
                ev["SEQUENCE"] = int(value) if value.strip().isdigit() else 0
            elif name in ("UID", "SUMMARY", "LOCATION", "DESCRIPTION", "URL", "STATUS"):
                ev[name] = _unescape(value)
        uid = ev.get("UID", "")
        if ev.get("recurrence_id") or rid_date:
            table, key = ((overrides, (uid, ev["recurrence_id"])) if ev.get("recurrence_id")
                          else (date_overrides, (uid, rid_date)))
            if key not in table or ev["SEQUENCE"] >= table[key]["SEQUENCE"]:
                table[key] = ev
        elif ev.get("DTSTART"):
            masters.append(ev)

    out: list[IcsEvent] = []
    used: set[int] = set()

    def emit(ev: dict, start: Optional[datetime], duration: Optional[timedelta],
             occurrence_of: Optional[datetime] = None) -> None:
        if start is None or ev.get("STATUS", "").upper() == "CANCELLED":
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
            occurrence_of=occurrence_of,
        ))

    def emit_occurrence(master: dict, occ: datetime, duration: Optional[timedelta],
                        recurring: bool) -> None:
        uid = master.get("UID", "")
        override = overrides.get((uid, occ)) or date_overrides.get((uid, occ.date()))
        if override is not None:
            used.add(id(override))
            o_start = override.get("DTSTART")
            o_dur = (override["DTEND"] - o_start) if (o_start and override.get("DTEND")) else duration
            merged = {**master, **{k: v for k, v in override.items() if v not in (None, "")}}
            emit(merged, o_start, o_dur, occ if recurring else None)
        else:
            emit(master, occ, duration, occ if recurring else None)

    for ev in masters:
        start = ev["DTSTART"]
        duration = (ev["DTEND"] - start) if ev.get("DTEND") else None
        if not ev["rrule"]:
            emit_occurrence(ev, start, duration, recurring=False)
            continue
        try:
            rule = rrulestr(_normalize_rrule(ev["rrule"], start), dtstart=start)
            occurrences = rule.between(window_start - timedelta(days=1), window_end, inc=True)
        except (ValueError, TypeError) as exc:
            logger.warning("Skipping series %r (%s): bad RRULE %r: %s",
                           ev.get("SUMMARY"), ev.get("UID"), ev["rrule"], exc)
            continue
        for occ in occurrences:
            if occ in ev["exdates"]:
                continue
            emit_occurrence(ev, occ, duration, recurring=True)

    # Overrides of occurrences outside the expansion window (moved into it).
    for (uid, rid), ov in overrides.items():
        if id(ov) not in used and ov.get("DTSTART"):
            o_dur = (ov["DTEND"] - ov["DTSTART"]) if ov.get("DTEND") else None
            emit(ov, ov["DTSTART"], o_dur, rid)

    return sorted(out, key=lambda e: e.start)
