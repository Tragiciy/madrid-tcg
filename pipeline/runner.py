"""Discover scraper modules and run each in its own subprocess, in parallel."""

import importlib
import json
import logging
import pathlib
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pipeline.config import SCRAPER_TIMEOUT_S, SCRAPER_WORKERS, SCRAPERS_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scraper discovery
# ---------------------------------------------------------------------------

def _discover_scrapers() -> list[str]:
    """Module names of every *.py inside scrapers/ except __init__."""
    return [
        f"scrapers.{p.stem}"
        for p in sorted(SCRAPERS_DIR.glob("*.py"))
        if p.stem != "__init__"
    ]


def _run_scraper(module_name: str) -> tuple[list[dict], Optional[str], Optional[str]]:
    """
    Import and call scrape(). Returns (events, error, store_constant).

    Import errors are caught here too, so a broken scraper module can never
    abort the whole run. store_constant is the module's STORE attribute
    (None if the module failed to import or doesn't define it).
    """
    store = None
    try:
        module = importlib.import_module(module_name)
        store = getattr(module, "STORE", None)
        events = module.scrape()
        logger.info("%s → %d events", module_name, len(events))
        return events, None, store
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.error("Scraper %s failed: %s", module_name, msg)
        return [], msg, store


_STORE_RE = re.compile(r'^STORE\s*=\s*["\'](.+?)["\']', re.MULTILINE)


def _store_from_source(module_name: str) -> Optional[str]:
    """Read the STORE constant without importing (for crashed/timed-out runs)."""
    path = SCRAPERS_DIR / f"{module_name.rsplit('.', 1)[-1]}.py"
    try:
        m = _STORE_RE.search(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def _run_scraper_isolated(
    module_name: str, timeout_s: int = SCRAPER_TIMEOUT_S,
) -> tuple[list[dict], Optional[str], Optional[str]]:
    """
    Run one scraper via tools.run_scraper in a child process.

    Same return shape as _run_scraper. A timeout, crash or unreadable result
    becomes an error, so the store is protected from deletion like any other
    failed scraper.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "result.json"
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "tools.run_scraper", module_name, str(out)],
                timeout=timeout_s,
                stdout=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            msg = f"TimeoutError: no result after {timeout_s}s"
            logger.error("Scraper %s failed: %s", module_name, msg)
            return [], msg, _store_from_source(module_name)
        try:
            result = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            msg = f"ChildProcessError: exit {proc.returncode}, no result ({exc})"
            logger.error("Scraper %s failed: %s", module_name, msg)
            return [], msg, _store_from_source(module_name)
    return (
        result.get("events") or [],
        result.get("error"),
        result.get("store") or _store_from_source(module_name),
    )


def run_all_scrapers(module_names: list[str]) -> list[tuple]:
    """
    Run scrapers concurrently. Returns (events, error, store, duration_s)
    per module, in input order.
    """
    def timed(module_name: str) -> tuple:
        t0 = time.monotonic()
        events, error, store = _run_scraper_isolated(module_name)
        return events, error, store, round(time.monotonic() - t0, 1)

    with ThreadPoolExecutor(max_workers=SCRAPER_WORKERS) as pool:
        return list(pool.map(timed, module_names))
