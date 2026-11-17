"""
shared/eventon.py — read a WordPress EventON calendar (the "EventON"
plugin, post type `ajde_events`).

EventON renders an empty month grid and fills it with one AJAX call. The
page carries the calendar's shortcode (`.evo_cal_data[data-sc]`) and two
nonces (`evo_general_params`); posting them back to
`/?evo-ajax=eventon_get_events` returns that month's events as JSON, one
entry per occurrence (repeats expanded), with real UTC epochs in
`unix_start` / `unix_end`. (`event_start_unix` is the wall-clock time
written as if it were UTC — not used.) direction "next" returns the
following month.

The WordPress REST route `wp/v2/ajde_events` exists too, but carries no
dates, so it is no use here.

No scrape() here. No network on import.
"""

from __future__ import annotations

import html as _html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from shared.http_retry import get_with_retry
from shared.locator_common import TZ, USER_AGENT

_SC_RE = re.compile(r"class='evo_cal_data' data-sc=\"([^\"]*)\"")
_PARAMS_RE = re.compile(r"evo_general_params\s*=\s*(\{.*?\});")


class EventonError(RuntimeError):
    """The calendar page or its AJAX answer is not what EventON returns."""


@dataclass
class EventonEvent:
    id: int
    ri: str                 # repeat-interval index; "0" for one-off events
    title: str
    start: datetime         # tz-aware, Europe/Madrid
    end: Optional[datetime]
    url: Optional[str]
    description: str


def parse_calendar_page(page_html: str) -> tuple[dict, dict]:
    """(shortcode, evo_general_params) from the page holding the calendar."""
    m = _SC_RE.search(page_html)
    p = _PARAMS_RE.search(page_html)
    if not m or not p:
        raise EventonError("no EventON calendar (evo_cal_data / evo_general_params) on the page")
    return json.loads(_html.unescape(m.group(1))), json.loads(p.group(1))


def _permalinks(fragment: str) -> dict[str, tuple[Optional[str], str]]:
    """'<id>_<ri>' → (event url, plain-text description) from the HTML list."""
    out: dict[str, tuple[Optional[str], str]] = {}
    soup = BeautifulSoup(fragment or "", "html.parser")
    for div in soup.select("div.eventon_list_event[id^=event_]"):
        key = div["id"][len("event_"):]
        link = div.select_one("a[itemprop=url]")
        desc = ""
        ld = div.select_one("script[type='application/ld+json']")
        if ld and ld.string:
            m = re.search(r'"description"\s*:\s*"(.*?)"\s*,\s*"location"', ld.string, re.S)
            if m:
                desc = BeautifulSoup(m.group(1), "html.parser").get_text(" ", strip=True)
        out[key] = (link.get("href") if link else None, desc)
    return out


def parse_events(payload: dict) -> list[EventonEvent]:
    """Events from one eventon_get_events answer. Raises if it is not one."""
    if payload.get("status") != "GOOD" or not isinstance(payload.get("json"), list):
        raise EventonError(f"unexpected EventON answer: status={payload.get('status')!r}")
    links = _permalinks(payload.get("html") or "")
    out = []
    for raw in payload["json"]:
        ri = str(raw.get("ri") or "0")
        key = raw.get("_ID") or f"{raw['ID']}_{ri}"
        url, desc = links.get(key, (None, ""))
        start = datetime.fromtimestamp(int(raw["unix_start"]), timezone.utc).astimezone(TZ)
        end = None
        if raw.get("unix_end") and int(raw["unix_end"]) > int(raw["unix_start"]):
            end = datetime.fromtimestamp(int(raw["unix_end"]), timezone.utc).astimezone(TZ)
        out.append(EventonEvent(
            id=int(raw["ID"]), ri=ri,
            title=_html.unescape(raw.get("event_title") or "").strip(),
            start=start, end=end, url=url, description=desc,
        ))
    return out


def fetch_eventon_events(page_url: str, months: int = 2, *,
                         get: Callable[..., requests.Response] = get_with_retry,
                         sleep: Callable[[float], None] = time.sleep,
                         ) -> list[EventonEvent]:
    """
    Every occurrence in the current month and the next `months - 1`, read
    from the calendar on `page_url`. Raises on any fetch or format error.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    page = get(page_url, timeout=30, headers={"User-Agent": USER_AGENT})
    shortcode, params = parse_calendar_page(page.text)
    parts = urlsplit(page_url)
    endpoint = f"{parts.scheme}://{parts.netloc}/?evo-ajax=eventon_get_events"
    events: list[EventonEvent] = []
    for i in range(months):
        sleep(1.0)  # one request at a time, small pause (small shop site)
        data = {"direction": "none" if i == 0 else "next", "ajaxtype": "switchmonth",
                "nonce": params.get("n", ""), "nonceX": params.get("nonce", "")}
        for k, v in shortcode.items():
            data[f"shortcode[{k}]"] = v
        resp = session.post(endpoint, data=data, timeout=60,
                            headers={"X-WP-Nonce": params.get("nonce", ""),
                                     "Referer": page_url},
                            cookies=page.cookies)
        resp.raise_for_status()
        payload = resp.json()
        events.extend(parse_events(payload))
        # The answer's shortcode is the month just returned; "next" from it.
        if isinstance(payload.get("SC"), dict):
            shortcode = payload["SC"]
    return events
