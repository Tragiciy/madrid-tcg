# Madrid TCG Aggregator — Agent Handoff Spec

Operational context for a coding agent (Claude / Codex / etc.) picking up
this repo. Optimised for action, not for human reading.

## 1. Goal
- Static Madrid TCG event aggregator.
- Pipeline: `scrapers/*.py` → `aggregator.py` → `public/events.json` → Alpine.js frontend (`public/index.html`).
- Daily refresh via GitHub Actions; site served via GitHub Pages.
- Frontend is a static SPA — no backend, no build step.

## 2. Architecture
```
scrapers/
  __init__.py
  micelion_games.py        # WCS Timetable AJAX, requests only
  metropolis_center.py     # Playwright (cookie challenge + window.evcalEvents) + per-event detail fetch
  arte9.py                 # Tribe Events REST (/wp-json/tribe/events/v1/events), requests + BS4
aggregator.py              # Discovery, validation, merge, normalisation, observability
public/
  index.html               # Alpine.js v3.14.1 (CDN), no build
  events.json              # Output, full historical record
  events_stats.json        # Output, observability + per-store stats
.github/workflows/
  update.yml               # cron daily: runs aggregator.py, commits diffs
  deploy-pages.yml         # publishes public/ to Pages on push to public/**
requirements.txt           # requests, beautifulsoup4, playwright
PROJECT_CONTEXT.md         # this file
```

## 3. Data schema (events.json)
```json
{
  "store":          "Arte 9",
  "game":           "Riftbound | null",
  "format":         "Prerelease | null",
  "title":          "Presentación Riftbound Unleashed",
  "datetime_start": "2026-05-02T10:00:00+02:00",
  "datetime_end":   "ISO-8601 | null",
  "language":       "es",
  "source_url":     "https://...",
  "scraped_at":     "2026-04-26T13:02:28+02:00",
  "first_seen_at":  "2026-04-25T09:00:00+02:00",
  "last_seen_at":   "2026-04-26T13:02:28+02:00",
  "is_active":      true
}
```

Rules:
- `price_eur` was removed. **Do not reintroduce.**
- `datetime_end` may be `null`. Frontend must tolerate.
- All datetimes carry an explicit timezone offset (Europe/Madrid).
- `events.json` is sorted by `datetime_start`.

## 4. Scrapers — current state

### Micelion Games (`scrapers/micelion_games.py`)
- Source: `POST https://miceliongames.com/wp-admin/admin-ajax.php` with `action=wcs_get_events_json`, `start=YYYY-MM-DD`, `end=YYYY-MM-DD`.
- Transport: `requests` only. **No Playwright.**
- TZ fix: API stamps Madrid wall-clock with a fake `+00:00`. `_to_madrid_iso` re-stamps `tzinfo=Europe/Madrid` instead of converting from UTC.
- Format: keyword scan over title + excerpt. Includes `cEDH`, Spanish forms (`sellados → Sealed`, `presentaciones → Prerelease`, `liga → League`).
- Window: ~90 days; server caps at ~3 months regardless.
- Output omits `price_eur`.

### Metropolis Center (`scrapers/metropolis_center.py`)
- Source: `https://metropolis-center.com/events/calendar` — JS cookie challenge (`dhd2`).
- Transport: Playwright (chromium headless). Required.
- Strategy: load calendar → read `window.evcalEvents` → click `.evnav-right` for `EXTRA_MONTHS=2` next months.
- Resource blocking: `image/font/stylesheet/media/imageset` + analytics domains aborted.
- Per-event detail fetch: when title alone yields no format, `_fetch_format_from_detail(page, url)` opens the event page (cookie carries over) and runs the keyword scan against the rendered text. Detail results cached by URL within one run.
- Spanish format extraction supported via the same FORMAT_KEYWORDS list.
- Output omits `price_eur`.

### Arte 9 (`scrapers/arte9.py`)
- Source: `GET https://arte9.com/wp-json/tribe/events/v1/events?per_page=50&page=N` — The Events Calendar REST API.
- Transport: `requests` + BeautifulSoup. **No Playwright.**
- API `start_date` is already Europe/Madrid local (no UTC bug).
- API `title` field is empty; real name lives in description headings or must be synthesised. Title pipeline is the most fragile part of this scraper — see §8.
- HTML entities unescaped (`html.unescape`) on heading text and category names.
- Pagination walks until `< per_page` returned or `MAX_PAGES=20`.

## 5. Aggregator rules (`aggregator.py`)
- **Dynamic discovery**: any `scrapers/*.py` (not `__init__.py`) is auto-loaded; module must expose `scrape() -> list[dict]`.
- **Validation per event**: `_shape_drop_reason` returns `None | "missing_fields" | "bad_datetime"`. Required fields: `store`, `title`, `datetime_start`. Past events are NOT filtered out.
- **Normalisation**:
  - `_normalize_game(game, title)` → canonical via `GAME_CANONICAL` then `ALLOWED_GAMES` allowlist; else `None`.
  - Format normalisation lives in scrapers; aggregator only owns `ALLOWED_FORMATS`.
- **Stable identity** (`event_key(event)`):
  1. `{store}|id:{source_event_id}` if scraper provides it.
  2. Fallback: `{store}|{title}|{datetime_start}|{location}`.
- **Merge** (`merge_events(existing, fresh, now_iso)`):
  - Upsert by `event_key`. Fresh data overrides everything except `first_seen_at`.
  - New event → `first_seen_at = last_seen_at = now`, `is_active = True`.
  - Existing event seen this run → keep `first_seen_at`, refresh `last_seen_at`, `is_active = True`.
  - Existing event NOT in fresh → kept on disk, `is_active = False`.
- **No future-only filter**. Past events live forever in `events.json`.
- **Sort** by `datetime_start` before write.
- **Allowlists**: `ALLOWED_GAMES` (11 entries), `ALLOWED_FORMATS` (18 entries incl. `Premier`, `Armory`).

## 6. Observability (`events_stats.json`)
Per-store entries shape:
```json
"Arte 9": {
  "total":            178,
  "active":           173,
  "raw_this_run":     178,
  "previous_raw":     178,
  "dropped_this_run": 0,
  "drop_reasons":     { "missing_fields": 0, "bad_datetime": 0 },
  "scraper_failed":   false,
  "anomaly":          null
}
```
Top-level: `total_events`, `active_events`, `by_store`, `by_game`, `scrapers` (per-module raw map), `failed_scrapers`, `generated_at`.

Rules:
- `previous_raw` read from prior `events_stats.json` BEFORE overwrite.
- `anomaly = "sharp_drop"` when `raw_this_run < 0.7 * previous_raw`.
- Console block: `=== scraper health ===` lists `[ok|WARN|FAIL]` per module with `raw / prev / valid / dropped / drop reasons`. `[WARN]` lines printed for sharp drops.
- `validate_events()` writes a separate `=== validation ===` report (totals, future/past, duplicates, invalid datetime, missing tz, missing fields, unknown format, unknown game).
- **Frontend does NOT read `events_stats.json`.** Free to evolve.

## 7. Frontend (`public/index.html`)
- Stack: vanilla HTML + Alpine.js v3.14.1 via CDN. **No build step.**
- Default view: **horizontal**. `localStorage["tcg-view"]` overrides; `vertical` retained.
- Layouts:
  - **Horizontal**: single CSS grid, 7 columns × `(1 + activeSegments)` rows. Empty `(day, segment)` cells render dashed gray placeholders. Cards rendered via `x-html` with delegated `@click` on the grid root (works around an Alpine nested-`<template x-for>` quirk).
  - **Vertical**: per-day stack with inline segment groups.
- Time segments (Europe/Madrid hour):
  - Morning < 12
  - Afternoon 12–16
  - Evening 16–19
  - Late ≥ 19
- Filters: search, game, store, format, segment chips. Card chips clickable → toggle that filter. Active filters highlight chips.
- Segment headers in horizontal view collapsible globally (chevron). `focusMode` (only one segment active) hides headers and renders a flat list per day.
- Today highlight: tinted column including empty-cell placeholders.
- Madrid time awareness:
  - `readMadridNow()` via `new Date(new Date().toLocaleString('en-US', { timeZone: 'Europe/Madrid' }))`. Refreshed every 60 s.
  - Past today-segments and past today-events dim to `opacity .55` (`.85` on hover).
  - Past events are **muted, never hidden**.
- Per-game accent: 12 palettes — `getGameClass(event.game)` → `.game-*` class sets `--card-tint` and `--card-accent`. Card border-left + chip pull from these vars. `Premier` recognised as a format value.
- Other UX: week nav (Prev/Next/Today), Today scroll-to-day in vertical, Back to top button after 400 px scroll.

## 8. Arte 9 title pipeline — critical

Title = event identity. Never use marketing/article copy as a title.

### Reject rules — `_is_acceptable_heading`
- Any `?` or `¿` in the text.
- Starts with any reject prefix (lowercase): `este sábado/domingo/viernes/lunes/martes/miércoles/jueves`, `hoy jugamos`, `ven a`, `aprende a jugar`, `si quieres`, `para reservar`, `recordad`, `en qué consiste`, `qué premios`, `qué regalos`, `cómo funciona`, `cómo se`, `ya tenemos`, `entradas`, `entrada`, `premios`, `sorteos`, `sorteo`, `calendario`, `jornada`, `formato de`, `acceso`, `normas`, `torneo de mañana`, `por participar`, `por asistir`, `por ganar`, `por inscribirte`, `packs`, `regalo`, `regalos por`.
- Single-word label (`formato`, `liga`, `torneo`, `commander`, `cedh`, `calendario`, `horario`, `jornada(s)`, `premio(s)`, `sorteo(s)`, `entrada(s)`, `premier`, `modern`, `standard`, `draft`, `casual`).
- Numeric summary `^\d+\s+(eventos?|jornadas?)`.
- Trailing `*` or `…`.
- Article tokens (`premios`, `regalos`, `consiste`, `reservar`, `plazas`, `inscripción/inscripcion`) — UNLESS a strong pattern is also present.
- Length > 70 chars (relaxed to 110 if a strong pattern is present).

### Strong patterns (substring, lowercase)
`liga`, `formato`, `presentación/presentacion`, `prerelease`, `torneo`, `fnm`, `commander`, `cedh`, `draft`. Plus implicit set names.

### Title priority
a) **Heading scan** with rejection rules above; first heading that survives. Mostly-uppercase headings get `_smart_case` (preserves `SWU`, `MTG`, `cEDH`, `RCQ`, `SOG`, `TOR`; lowercases articles `de`, `del`, `y`, `en`, `con`, `la`, `el`, `los`, `las`, `a`, `un`, `una`).
b) **Synthesised** — `_synthesise_title(game, format, set_name)` where `set_name` comes from image filename (e.g. `UNLEASHEDw.jpg → Unleashed`) or description text. Spanish format labels: `Prerelease → Presentación`, `Sealed → Sellado`, `League → Liga`. Short game forms: `Magic: The Gathering → Magic`, `Star Wars: Unlimited → SWU`, `Yu-Gi-Oh! → Yu-Gi-Oh`, `Weiß Schwarz → Weiss`.
c) **Cleaned API title** if acceptable.
d) **Slug** humanised, only when slug is non-numeric.

### Post-pass — `_refine_title`
After heading is selected, split on en-dash/em-dash separator, keep head verbatim, then for each tail segment:
- Drop `\d+\s+jornadas?` (weak structural).
- Drop `Calendario` / `Horario`.
- Reduce `Formato X` → `X`.
- Drop bare game tokens (`SWU`, `Magic`, `Lorcana`, …) when head has a strong pattern AND `event.game` is set.
- Otherwise keep the segment.

### Examples (must hold)
- `¿EN QUÉ CONSISTE UNA PRESENTACIÓN DE COLECCIÓN DE RIFTBOUND?` → rejected → synthesised → `Presentación Riftbound Unleashed`.
- `LIGA DE COMMANDER – 9 JORNADAS` → smart-case → refine → `Liga de Commander`.
- `LIGA DE SWU – 10 JORNADAS – CALENDARIO – FORMATO PREMIER` → smart-case → refine → `Liga de SWU – Premier`.
- `LIGA NACIONAL SWU – CALENDARIO – FORMATO PREMIER` → smart-case → refine → `Liga Nacional SWU – Premier`.
- `Gran Liga Carbonite – SWU` (hypothetical) → refine drops trailing `SWU` → `Gran Liga Carbonite`.

## 9. Format normalisation
Canonical values must be in `aggregator.ALLOWED_FORMATS`:
`Store Championship, Prerelease, cEDH, Commander, Standard, Pioneer, Modern, Legacy, Pauper, Sealed, Draft, League, Weekly, Casual, BO3, BO1, Premier, Armory`.

Mappings (case-insensitive substring):
- `Formato Premier` / `Premier` → `Premier` *(must precede `Liga`/`League`)*
- `Armory` → `Armory`
- `Formato Modern` → `Modern`
- `Formato Standard` → `Standard`
- `Presentación` / `Presentaciones` → `Prerelease`
- `Prerelease` → `Prerelease`
- `Sellado` / `Sellados` → `Sealed`
- `cEDH` / `Competitive Elder Dragon Highlander` → `cEDH` *(must precede `Commander`)*
- `Commander` → `Commander`
- `Liga` → `League` *(fallback only — Premier and other specifics outrank it)*

Rule: in `FORMAT_KEYWORDS`, longer/more-specific tokens come first.

## 10. Agent rules

Do
- Add new stores as independent files under `scrapers/`.
- Inspect the data source first; document the parsing plan before code.
- Prefer `requests` + BeautifulSoup. Reach for Playwright only when JS or a cookie challenge requires it.
- Reuse `aggregator.ALLOWED_GAMES` / `ALLOWED_FORMATS`. Add to allowlists when introducing a genuinely new value.
- After scraper changes, run `python aggregator.py` and report `raw / valid / dropped` per scraper plus 2-3 example parsed events.
- Use the persistent merge model — write fresh events with the existing schema; aggregator stamps lifecycle fields.
- Keep `event_key` stable. Title changes fork keys → if you rename, expect a one-off cleanup pass.

Do not
- Reintroduce `price_eur`.
- Remove `first_seen_at` / `last_seen_at` / `is_active`.
- Filter past events at aggregation. Past events stay in `events.json` forever.
- Add a database, ORM, message queue, or any new dependency unless the task demands it.
- Add a build step. Frontend stays vanilla HTML + Alpine CDN.
- Modify the frontend when adding a scraper unless the schema actually changes.
- Push to `main` or open PRs unless explicitly asked.
- Call APIs from the frontend. `events.json` is the only data source the page consumes.

## 11. Next recommended work
- Add more Madrid stores one at a time. Per store: probe → plan → implement → verify with `aggregator.py`.
- Calendar export: add a static "Add to Google Calendar" link per event using the `https://calendar.google.com/calendar/render?action=TEMPLATE&...` URL template. Do not use the Google Calendar API.
- Optional: collapse the duplicate `FORMAT_KEYWORDS` lists into `scrapers/_common.py` only if a third Spanish-language scraper appears.
