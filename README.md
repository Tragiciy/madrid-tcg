# Madrid TCG Events

A static, single-page calendar of trading card game (TCG) tournaments and game
nights at Madrid's specialty stores.

Live site: <https://tragiciy.github.io/madrid-tcg/>

## What it does

Aggregates event listings from several Madrid TCG stores and presents them in a
weekly calendar. Visitors can scan a week at a glance, filter by game, store,
and format, and add any event to Google Calendar, iCalendar, or Outlook with
one click.

## Key features

- **Two week views** — horizontal grid (default) and vertical per-day list.
- **Faceted filters** — search by title, multi-select game / store / format.
  Filters are bookmarkable via URL parameters.
- **Filter presets** — save and restore named filter combinations.
- **Time-segment chips** — Morning (<12), Afternoon (12–16), Evening (16–19),
  Late (19+); chips can be toggled to hide a segment, and segment headers in
  the horizontal grid can be collapsed to compact a busy week.
- **Per-game color accents** — each card carries a tint and accent based on its
  game (Magic, Pokémon, One Piece, Lorcana, …).
- **Week navigation** — Prev / Today / Next, with the current week range
  displayed.
- **Today highlighting** — today's column is tinted; past segments and past
  events on today are dimmed (never hidden).
- **Add to Calendar** — every event card has a calendar button that opens a
  pre-filled Google Calendar, iCalendar (.ics), or Outlook template.
- **Event Detail Panel** — click any event for a full-detail slide-in panel
  with store address and Google Maps link.

## Tech stack

- **Four static frontend files** in `public/`:
  - `index.html` — markup and Alpine.js directives (no inline CSS or JS)
  - `styles.css` — all styles: CSS custom properties, per-game palette, layout,
    responsive breakpoints
  - `app.js` — the Alpine `app()` factory, all runtime logic, state, and
    renderers
  - `config.js` — static extension points: `STORE_META`, `STORE_ADDRESSES`,
    `SEGMENTS`, `GAME_CLASS_MAP`
- **Alpine.js v3.14.1** (loaded from CDN) — provides reactivity. The whole UI
  is one Alpine component (`x-data="app()"` on the `<html>` element).
- **Static JSON data** — the frontend's only runtime data source is
  `public/events.json`. No API, no backend, no database.
- **Python pipeline** — `aggregator.py` plus per-store scrapers under
  `scrapers/` regenerate `public/events.json` on a daily GitHub Actions cron
  (`.github/workflows/update.yml`).
- **Hosting** — GitHub Pages, serving the `public/` directory as static assets.

## How it works (high level)

```
scrapers/*.py  ──▶  aggregator.py  ──▶  public/events.json
                                                │
                                                ▼
                                  fetch() in Alpine init()
                                                │
                                                ▼
                              this.events  ─▶  filters
                                                │
                                                ▼
                                  weekDays (7-day buckets)
                                                │
                                                ▼
                       horizontal grid   or   vertical list
```

1. The browser fetches `events.json` once at load.
2. URL parameters (game, store, format, event) are applied immediately after
   load and override any saved state.
3. Alpine reactively derives the active week (`weekDays`) from the events plus
   the current `weekStart`.
4. Search + game/store/format facets + segment chips combine into
   `filteredEvents`.
5. Each day is bucketed into the four time segments. The horizontal view
   renders one CSS grid row per segment so cards line up vertically across
   days; the vertical view stacks per-day cards.

## Run locally

Just open the page — there is no build step.

```bash
# Option A — serve the public folder
python -m http.server -d public 8000
# then open http://localhost:8000

# Option B — open the file directly
open public/index.html
```

To regenerate `public/events.json` from the scrapers locally:

```bash
python -m venv .venv
source .venv/bin/activate          # macOS/Linux
pip install -r requirements.txt
python -m playwright install chromium
python aggregator.py
```

## How to add a scraper

1. Create `scrapers/<name>.py` exposing a `scrape() -> list[dict]` function.
   `aggregator.py` auto-discovers every `*.py` in `scrapers/` — no
   registration needed.
2. Import `GAME_KEYWORDS` / `FORMAT_KEYWORDS` from `shared.scraper_keywords`
   for game/format detection. Put any shared helpers in `shared/`, not in
   `scrapers/`.
3. Add an entry to `STORE_META` in `public/config.js` with at least an
   `address` field. This is required for the Event Detail Panel store card and
   for the "Add to Calendar" location field.
4. Run `python aggregator.py` and verify the new store appears in
   `public/events_stats.json`.

See [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for architecture rules and the
full audit → `scraper_targets.json` → scraper expansion workflow.

## Deploy

The site is hosted on **GitHub Pages**, serving `public/` as static assets
with no build step.

Data updates flow through GitHub Actions: the
[`update.yml`](.github/workflows/update.yml) workflow runs daily at 06:00 UTC,
re-runs every scraper, and commits a refreshed `public/events.json` (and
`public/events_stats.json`) to `main`, which triggers a Pages redeploy.

## Constraints

- **No build step.** Alpine.js is loaded via CDN; no bundler, no transpiler.
- **No backend.** The frontend only ever reads `events.json`; it never calls
  any API at runtime.
- **No database.** `events.json` is the entire persistence layer.
- **Four-file frontend.** HTML, CSS, JS, and config are split into four
  separate files — do not merge them. See `PROJECT_CONTEXT.md` §7 for why.
