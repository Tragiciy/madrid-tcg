"""
Share pages: one tiny static HTML page per event, /e/<id>/, so that a link
shared in WhatsApp/Telegram/social shows a real preview (title, date,
store, game image). Link-preview crawlers don't run JavaScript, so the
app's ?event= URL alone can only ever show the site-wide preview.

Each page carries Open Graph tags and immediately sends people on to the
event in the calendar (meta refresh + location.replace). Pages are marked
noindex — the Spanish SEO pages are what search engines should index.

Pages exist for events from SHARE_BEFORE_DAYS ago to SHARE_AFTER_DAYS
ahead; the API marks those events with `share` (their page path), which
the app uses for its Share/Copy link.
"""

import base64
import hashlib
import logging
import pathlib
from datetime import date, datetime, timedelta
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pipeline.config import TZ

logger = logging.getLogger(__name__)

PUBLIC_DIR = pathlib.Path("public")
SHARE_DIR = "e"
SHARE_BEFORE_DAYS = 14
SHARE_AFTER_DAYS = 60
TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"


def share_id(event_id: str) -> str:
    """Short stable id for an event (its pipeline key, hashed)."""
    return hashlib.sha1(event_id.encode("utf-8")).hexdigest()[:10]


def in_share_window(start_iso: str, today: date) -> bool:
    day = datetime.fromisoformat(start_iso).astimezone(TZ).date()
    return today - timedelta(days=SHARE_BEFORE_DAYS) <= day <= today + timedelta(days=SHARE_AFTER_DAYS)


def share_path(public_event: dict, today: date) -> Optional[str]:
    if not in_share_window(public_event["datetime_start"], today):
        return None
    return f"/{SHARE_DIR}/{share_id(public_event['id'])}/"


def app_url(public_event: dict) -> str:
    """The calendar URL for an event — same encoding as app.js encodeEventParam."""
    from pipeline.publish import iso_week
    token = base64.urlsafe_b64encode(public_event["id"].encode("utf-8")).decode().rstrip("=")
    day = datetime.fromisoformat(public_event["datetime_start"]).astimezone(TZ).date()
    return f"/?event={token}&w={iso_week(day)}"


def build_share_pages(public_events: list[dict], now_iso: str) -> dict[str, str]:
    """{path relative to public/: html} for events in the share window."""
    from pipeline.pages import SITE_URL, fecha_larga, game_slug

    today = datetime.fromisoformat(now_iso).astimezone(TZ).date()
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("share.html")
    pages: dict[str, str] = {}
    for e in public_events:
        path = share_path(e, today)
        if not path:
            continue
        start = datetime.fromisoformat(e["datetime_start"]).astimezone(TZ)
        game = e.get("game")
        image = f"{SITE_URL}/og/{game_slug(game) if game else 'default'}.jpg"
        when = f"{fecha_larga(start.date())}, {start:%H:%M}"
        pages[f"{path.strip('/')}/index.html"] = tpl.render(
            title=e["title"], store=e["store"], game=game or "", when=when,
            description=" · ".join(p for p in (when, e["store"], game) if p),
            image=image, url=SITE_URL + path, target=app_url(e),
        )
    return pages


def write_share_pages(pages: dict[str, str], public_dir: Optional[pathlib.Path] = None) -> int:
    """Write changed pages; remove share pages no longer produced."""
    public_dir = public_dir or PUBLIC_DIR
    keep, written = set(), 0
    for rel, html in pages.items():
        path = public_dir / rel
        keep.add(path.resolve())
        if path.exists() and path.read_text(encoding="utf-8") == html:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        written += 1
    for stale in list((public_dir / SHARE_DIR).glob("*/index.html")):
        if stale.resolve() not in keep:
            stale.unlink()
            try:
                stale.parent.rmdir()
            except OSError:
                pass
    logger.info("Wrote share pages: %d of %d changed", written, len(pages))
    return written
