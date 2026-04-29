# Madrid TCG Events

A static, single-page calendar of trading card game (TCG) tournaments and game
nights at Madrid's specialty stores.

Live site: <https://tragiciy.github.io/madrid-tcg/>

## What it does

Aggregates event listings from several Madrid TCG stores and presents them in a
weekly calendar. Visitors can scan a week at a glance, filter by game, store,
format and time-of-day, and add any event to Google Calendar with one click.

## Key features

- **Two week views** — horizontal grid (default) and vertical per-day list.
- **Faceted filters** — search by title, multi-select game / store / format.
- **Time-segment chips** — Morning (<12), Afternoon (12–16), Evening (16–19),
  Late (19+); chips can be toggled to hide a segment, and segment headers in
  the horizontal grid can be collapsed to compact a busy week.
- **Per-game color accents** — each card carries a tint and accent based on its
  game (Magic, Pokémon, One Piece, Lorcana, …).
- **Week navigation** — Prev / Today / Next, with the current week range
  displayed.
- **Today highlighting** — today's column is tinted; past segments and past
  events on today are dimmed (never hidden).
- **Add to Google Calendar** — every event card has a calendar button that
  opens a pre-filled `calendar.google.com/calendar/render` template.

## Tech stack

- **Plain HTML** — everything ships as a single `public/index.html`.
- **Alpine.js v3.14.1** (loaded from CDN) — provides reactivity. The whole UI
  is one Alpine component (`x-data="app()"` on the `<html>` element); state,
  derived getters, and event handlers all live inside the `app()` factory.
- **Vanilla CSS** — written inline inside a `<style>` block in `index.html`.
  There is no separate `styles.css`.
- **Static JSON data** — the frontend's only data source is
  `public/events.json`. No API, no backend, no database.
- **Python pipeline** — `aggregator.py` plus per-store scrapers under
  `scrapers/` regenerate `public/events.json` on a daily GitHub Actions cron
  (`.github/workflows/update.yml`).
- **Hosting** — Cloudflare Pages, serving the `public/` directory as static
  assets.

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
2. Alpine reactively derives the active week (`weekDays`) from the events plus
   the current `weekStart`.
3. Search + game/store/format facets + segment chips combine into
   `filteredEvents`.
4. Each day is bucketed into the four time segments. The horizontal view
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

## Deploy

The site is hosted on **Cloudflare Pages** at
<https://tragiciy.github.io/madrid-tcg/>, serving `public/` as static assets
with no build step.

Data updates flow through GitHub Actions: the
[`update.yml`](.github/workflows/update.yml) workflow runs daily at 06:00 UTC,
re-runs every scraper, and commits a refreshed `public/events.json` to `main`,
which then triggers a Cloudflare redeploy.

## Constraints

- **Single-page architecture.** All HTML, CSS, and JavaScript live in
  `public/index.html`.
- **No build step.** Alpine.js is loaded via CDN, no bundler, no transpiler.
- **No backend.** The frontend only ever reads `events.json`; it never calls
  any API at runtime.
- **No database.** `events.json` is the entire persistence layer.
