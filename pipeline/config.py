"""Pipeline settings: paths, timezone and thresholds shared by every stage."""

import os
import pathlib
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Madrid")
SCRAPERS_DIR = pathlib.Path("scrapers")
# Full event history: the pipeline's persistent store. Not deployed —
# clients read the slim per-week files in public/api/v1 (pipeline/publish.py).
EVENTS_FILE = pathlib.Path("data/events.json")
# Where the history lived before the static API; read once, then removed.
LEGACY_EVENTS_FILE = pathlib.Path("public/events.json")
STATS_FILE = pathlib.Path("public/events_stats.json")

# Sentinel for events that existed before lifecycle fields were added.
EPOCH_ISO = "1970-01-01T00:00:00+00:00"

# Threshold for the "count dropped sharply" warning (validation report).
COUNT_DROP_THRESHOLD = 0.7
# Per-store anomaly threshold: scraped raw count < 70% of previous run
# triggers a "sharp_drop" flag. Same threshold, separate constant so the
# two policies can drift later if needed.
SHARP_DROP_THRESHOLD = 0.7

# A store whose scraper keeps returning 0 events with no upcoming events left
# on record is flagged "stale" once its last sighting is this old. Nothing is
# protected (there is nothing left to delete), it only raises an alert.
STALE_AFTER_DAYS = 14

# Append-only per-source run log: one JSON line per scraper per run. The
# baseline for source health over time (and the future `source_runs` table).
SOURCE_RUNS_FILE = pathlib.Path("data/source_runs.jsonl")
RETIRED_STORES_FILE = pathlib.Path("data/retired_stores.json")
STORE_RENAMES_FILE = pathlib.Path("data/store_renames.json")
# Hold a city whose upcoming active events fall below this share of the
# previous run's: history-inclusive totals hide losing every future event.
# Checked per city once it had at least UPCOMING_GUARD_MIN, so Barcelona's
# volume cannot mask Madrid's and vice versa. A held city keeps its previous
# events and raises a health issue; the other cities still publish
# (reconcile.merge_holding_cities).
UPCOMING_DROP_THRESHOLD = 0.5
UPCOMING_GUARD_MIN = 50

# Each scraper runs in its own subprocess with this wall-clock budget.
SCRAPER_TIMEOUT_S = int(os.environ.get("SCRAPER_TIMEOUT_S", "300"))
# Scrapers are I/O bound and hit different hosts, so run several at once.
SCRAPER_WORKERS = int(os.environ.get("SCRAPER_WORKERS", "6"))
