# Madrid TCG Events

A static, single-page calendar of trading card game (TCG) tournaments and game
nights at Madrid's specialty stores.

Live site: <https://madrid-tcg.pages.dev/>

## What it does

Aggregates event listings from several Madrid TCG stores and presents them in a
weekly calendar. Visitors can scan a week at a glance, filter by game, store,
and format, and add any event to Google Calendar, iCalendar, or Outlook with
one click.

## Key features

- **Day, Week and Map views** — Day (default): a strip of the week's 7 days, each with its event count
  and a load bar, then the chosen day's events in Morning / Afternoon / Evening sections (the one with
  the next event starts open). Week: the 7-column grid. Map: one pin per store.
- **Game chips** — one chip per game this week, with a count; the games picked on the welcome screen
  arrive here already selected.
- **Filters panel** — store, format, event type, time of day, distance, presets. Formats come from the
  data and depend on the games selected (shared formats merge into one chip). Format and event type are
  separate: `public/model.js` splits the scraped `format` field (which mixes in "Prerelease") and derives
  the type from `format_official` and the title. Every filter is kept in the URL.
- **Filter presets** — save and restore named filter combinations. Mark one as default to have it applied automatically on fresh visits.
- **First-run onboarding** — wizard on the first visit that sets initial game/store filters.
- **Favorites** — star any event to save it locally (persisted in `localStorage`). A "Saved"
  toggle shows only them; saved cards carry a gold bar.
- **Per-game colours** — each game has one colour (`--game-<slug>` in `styles.css`), used once per
  card: the fill of its label.
- **Week navigation** — Prev / Today / Next, with the current week range
  displayed.
- **Today** — marked in teal with a "today" label; events that already started are dimmed (never hidden).
- **Add to Calendar** — every event card has a Google Calendar button; an opened card also offers
  an .ics file (Apple Calendar, Outlook).
- **Near me** — the Map's Near me button, or a distance in Filters, asks for the browser location. It filters to stores within 2, 5, 10 or 25 km,
  using the store registry's coordinates, and shows the distance on each card. The position stays in
  memory only and is never stored or sent anywhere.
- **Installable app (PWA)** — a manifest, icons and a service worker (`public/sw.js`). The site can be
  added to the home screen and opens offline with the last data it saved. The service worker always
  tries the network first, so it never shows stale data while online.
- **Link previews** — shared event links (`/e/<id>/`) show the event title, date, store and a
  game image in WhatsApp, Telegram and social apps, then open the event in the calendar.
- **Store submissions** — stores add their events at `/submit/`. Each submission becomes a
  GitHub issue, and approving it with the `approved` label publishes it with the next update.
- **Event cards** — a click opens the card in place on desktop (When / Where / Format, more events at
  the same store, Google Calendar, directions, share, source link) and as a bottom sheet on phones.

## Tech stack

- **Five static frontend files** in `public/`:
  - `index.html` — markup and Alpine.js directives (no inline CSS or JS)
  - `styles.css` — all styles: CSS custom properties, per-game palette, layout,
    responsive breakpoints
  - `app.js` — the Alpine `app()` factory, all runtime logic, state, and
    renderers
  - `model.js` — pure calendar logic (format/type split, the Format block,
    time of day, the day strip, filters ⇄ URL), tested with
    `node --test 'tests/js/*.test.js'` (no dependencies)
  - `config.js` — static extension points: `GAME_SHORT`, `GAME_CLASS_MAP`,
    and `STORE_META`, which is filled at runtime from `public/stores.json`
- **Alpine.js v3.14.1** (loaded from CDN) — provides reactivity. The whole UI
  is one Alpine component (`x-data="app()"` on the `<html>` element).
- **Static JSON data** — the frontend's only runtime data source is
  the static JSON API in `public/api/v1/` (see `docs/api-v1.md`). There is
  no server and no database.
- **Python pipeline** — `aggregator.py` plus per-store scrapers under
  `scrapers/` regenerate the history (`data/events.json`) and the API on a
  daily GitHub Actions cron
  (`.github/workflows/update.yml`).
- **Hosting** — Cloudflare Pages, serving the `public/` directory as static assets.

## Search-engine pages

The pipeline also writes plain HTML pages for search engines, in Spanish and in English (under `/en/`). They
need no JavaScript and are generated from the same data:
- `/madrid/`, `/madrid/hoy/` and `/madrid/fin-de-semana/`;
- one page per game, such as `/madrid/one-piece/`;
- one page per store, such as `/madrid/tiendas/arte-9/`;
- `sitemap.xml`, `robots.txt` and a real `404.html`.

The event lists carry schema.org `Event` data, so they can appear in Google's
event results.

## How it works (high level)

```
scrapers/*.py  ──▶  aggregator.py  ──▶  data/events.json ──▶ public/api/v1/
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

1. The browser fetches `api/v1/madrid/events/upcoming.json` (the previous
   week plus the next four weeks, ~60 KB gzip), `stores.json` and `index.json`.
   Other weeks load on demand.
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

To regenerate the data and the API from the scrapers locally:

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
3. Add an entry to the store registry `data/stores.json` (`name`, `address`; the `id` is a stable slug). This is required for the Event Detail Panel store card and
   for the "Add to Calendar" location field.
4. Run `python aggregator.py` and verify the new store appears in
   `public/events_stats.json`.

See [PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) for architecture rules and the
full audit → `data/scraper_targets.json` → scraper expansion workflow.

## Deploy

The site is hosted on **Cloudflare Pages**, serving `public/` as static assets
with no build step.

Data updates flow through GitHub Actions: the
[`update.yml`](.github/workflows/update.yml) workflow runs daily at 04:37 UTC,
runs the tests, re-runs every scraper (isolated, in parallel), and commits the refreshed history, registry and API (and
`public/events_stats.json`) to `main`, which triggers a Cloudflare Pages
redeploy. Broken scrapers are reported as `scraper-health` GitHub issues.

## Constraints

- **No build step.** Alpine.js is loaded via CDN; no bundler, no transpiler.
- **No backend.** The frontend only ever reads the static `api/v1` files; it never calls
  any API at runtime.
- **No database.** `data/events.json` and `data/stores.json` in git are the entire persistence layer.
- **Four-file frontend.** HTML, CSS, JS, and config are split into four
  separate files — do not merge them. See `docs/PROJECT_CONTEXT.md` §7 for why.
