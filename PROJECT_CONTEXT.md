# PROJECT_CONTEXT

Technical reference for AI agents and developers working on this repository.
Describes the project as it currently exists — not as it could be.

The project has two halves that meet at one file:

1. A **Python data pipeline** (`scrapers/` + `aggregator.py`) that produces
   `public/events.json`.
2. A **four-file Alpine.js frontend** (`public/index.html`, `public/styles.css`,
   `public/app.js`, `public/config.js`) that consumes `public/events.json`.

---

## 1. Backend pipeline

### 1a. Scraper discovery

`aggregator.py` auto-discovers every `*.py` file in `scrapers/` (except
`__init__.py`) and calls its `scrape()` function. **No helper or utility
modules are allowed inside `scrapers/`** — anything without a `scrape()`
must live outside this directory (currently in `shared/`). Do not modify
`aggregator.py` merely to register a new scraper; place the scraper file in
`scrapers/` and it is picked up automatically.

Current scrapers: `arte9`, `itaca`, `jupiter_juegos`, `la_guarida_juegos`,
`metropolis_center`, `micelion_games`.

### 1b. Shared utilities

`shared/` is for reusable backend/scraper utilities only. Files in `shared/`
must not expose `scrape()`, must not perform network requests on import, and
must not have side effects at import time.

`shared/scraper_keywords.py` provides:
- `GAME_KEYWORDS` — `(keyword, canonical_name)` tuples, longest-first
- `FORMAT_KEYWORDS` — `(keyword, canonical_format)` tuples, longest-first
- `extract_game_from_keywords(text, keywords)` — returns canonical game or None
- `extract_format_from_keywords(text, keywords)` — returns canonical format or None

Scrapers import these and may extend them with store-specific entries.

`shared/store_matching.py` provides store-name and address normalization plus
fuzzy matching. Used by discovery tooling (`discover_stores.py`,
`audit_store_event_pages.py`), not by scrapers.

#### Shared keyword migration status

| Scraper | Uses shared GAME_KEYWORDS | Uses shared FORMAT_KEYWORDS |
| --- | --- | --- |
| `arte9` | — (not used) | Yes |
| `itaca` | Yes | Yes |
| `jupiter_juegos` | Yes | Yes |
| `la_guarida_juegos` | Yes | Yes |
| `metropolis_center` | — (not used) | Yes |
| `micelion_games` | Yes | Yes |

Stores with `—` derive game from API category fields, not keyword matching.

### 1c. Scraper stats and anomaly detection

`aggregator.py` writes `public/events_stats.json` after every run with a
per-store breakdown. Each store entry contains:

- `raw_this_run` — events fetched by the scraper this run, before validation drops
- `previous_raw` — the same counter from the immediately preceding run's stats file
- `dropped_this_run`, `drop_reasons` — validation drop counts per failure category
- `anomaly` — `"sharp_drop"` if the raw count fell sharply, or `null`

**Anomaly detection logic** (`aggregator.py:49`, `aggregator.py:484`): the
previous run's stats file is loaded at startup, before being overwritten. For
each store, if `raw_this_run < previous_raw × 0.7` (constant
`SHARP_DROP_THRESHOLD = 0.7`), `anomaly` is set to `"sharp_drop"`. A `[WARN]`
line is printed to the console during the run (`aggregator.py:533`). The check
fires on raw count (before validation drops), so a scraper that suddenly
returns 0 events — due to a site-structure change, for example — triggers the
flag even if the merged events store still contains historical data from prior
runs. When there is no previous stats file to compare against, `previous_raw`
is `null` and no anomaly is flagged.

### 1d. Event identity and merge

Events are merged across daily runs using a stable key:

- `{store}|id:{source_event_id}` — if the scraper provides a stable ID
- `{store}|{title}|{datetime_start}|{location}` — fallback

`merge_events()` upserts fresh events onto the persisted list. Existing events
not seen this run are kept with `is_active=False` (never deleted). Every event
carries lifecycle fields: `first_seen_at`, `last_seen_at`, `is_active`.

### 1e. Execution

```bash
python -m scrapers.<name>    # run a single scraper in isolation
python aggregator.py          # full pipeline (all scrapers + merge + stats)
```

---

## 1f. Store expansion workflow (audit → targets → scrapers)

The pipeline for finding and onboarding new stores follows these steps:

1. **Store discovery** — `discover_stores.py` (and helpers in `discoverers/`)
   finds candidate stores from the Wizards locator and other sources, writing
   `candidate_stores.json`.

2. **Event-page audit** — `audit_store_event_pages.py` reads
   `candidate_stores.json`, fetches each candidate's website, detects event
   pages and calendar presence, and writes `store_event_audit.json`. It uses
   `shared.scraper_keywords` for game/format signal detection.

3. **Target selection** — `build_scraper_targets.py` reads
   `store_event_audit.json` and classifies stores into four buckets, writing
   `scraper_targets.json`:
   - `scrape_now` — validated event page with clear game content; build a
     scraper immediately.
   - `possible` — event intent detected but no clean event page found; needs
     manual investigation.
   - `manual_review` — social-only presence (Instagram/Facebook); not a
     straightforward scraper target.
   - `not_ready` — reachable website but no event signals detected.

4. **Scraper development** — for each store in `scrape_now`, create
   `scrapers/<name>.py` and add the store to `STORE_META` in `config.js`.

`scraper_targets.json` lives at the repo root and is the canonical queue of
stores awaiting scraper development. It is updated by re-running steps 2–3
after sites change. **Do not hand-edit it.**

---

## 1g. Deployment

GitHub Pages serves `public/` directly as static files. There is no build
step, no bundler, and no environment variables. A push to `main` triggers an
automatic redeploy.

`aggregator.py` runs in GitHub Actions on a cron schedule defined in
`.github/workflows/update.yml` — daily at 06:00 UTC (08:00 Madrid summer,
07:00 Madrid winter). The workflow runs `python aggregator.py`, then commits
any changes to `public/events.json` and `public/events_stats.json` back to
`main`, which in turn triggers a Pages redeploy. The workflow also supports
`workflow_dispatch` for manual runs.

---

## 2. Frontend architecture

The frontend is a **single Alpine.js component** scoped to the `<html>`
element via `x-data="app()"` and initialised by `x-init="init()"`.

The component is spread across four static files — no build step required:

- **`public/index.html`** — markup only. Declares the Alpine component scope
  (`x-data="app()"`, `x-init="init()"`) and all Alpine directives. Contains
  no CSS or JavaScript.
- **`public/styles.css`** — all styles. `:root` tokens, per-game palette,
  layout modes, and responsive breakpoints.
- **`public/app.js`** — the Alpine `app()` factory (`app.js:245`) and every
  helper. All runtime logic, state, derived getters, formatters, event
  handlers, and the cell-card HTML builder live here.
- **`public/config.js`** — static data and extension points. See §2a.

Any change to the frontend may touch more than one file and must respect tight
coupling between the markup (Alpine directives in `index.html`), the CSS class
names (`styles.css`), and the data model on `app()` (`app.js`).

### Load order

Alpine is loaded via CDN in `<head>` with `defer`:

```html
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"></script>
```

`config.js` and `app.js` are plain `<script>` tags at the bottom of `<body>`
(no `defer`). Execution order:

1. `config.js` runs synchronously — globals land on `window`.
2. `app.js` runs synchronously — `app()` factory is defined, reads the globals.
3. Alpine's deferred init fires — evaluates `x-data="app()"`, calls `app()`.

This ordering is load-order sensitive. Reordering the `<script>` tags, or
adding `defer` or `type="module"` to either file, silently breaks
initialisation. See §7.

### 2a. `public/config.js` — extension points

`config.js` exposes four `var` globals (deliberately `var`, not `const`/`let`
— see §7):

- **`STORE_META`** — primary per-store metadata object. Each key is a store
  display name; each value is an object with at minimum an `address` field,
  and optionally `notes`, `website`, etc. Used by the Event Detail Panel to
  show the store card and address, and by calendar URL builders for the
  `location` field. **Every new scraper store must have an entry here.**
- **`STORE_ADDRESSES`** (`config.js:1`) — legacy fallback map of store display
  name → address string. Used only when `STORE_META` has no entry for a store.
  Do not add new stores here; use `STORE_META` instead.
- **`SEGMENTS`** (`config.js:10`) — array of 4 time-bucket definitions, each
  with a `key`, hour range, `label`, and `shortRange`.
- **`GAME_CLASS_MAP`** (`config.js:42`) — maps canonical game name → CSS class
  string (e.g. `"Magic: The Gathering"` → `"game-mtg"`).

**Principle:** extension points live in `config.js`, not in `app.js`. When
adding a new store, game, or adjusting segment boundaries, `config.js` is the
first file to edit (see §9).

---

## 3. Data flow

```
fetch('events.json')
        │
        ▼
   this.events  ◀────────────────────────────────────────┐
        │                                                 │
        │  URL params applied first (game, store, format) │
        │  (override any persisted state)                 │
        │                                                 │
        │  filters.search                                 │
        │  filters.game[]   filters.store[]               │
        │  filters.format[] segmentFilter{}               │
        ▼                                                 │
  eventMatches(e)  ─▶  filteredEvents (getter)            │
        │                                                 │
        │  weekStart                                      │
        ▼                                                 │
   weekDays (getter, 7 entries with segmentMap)           │
        │                                                 │
        ├── horizontal:  cells (getter) ─▶ cellCardsHtml ──▶ x-html injects strings
        │                                                       │
        │                                                       └─ delegated @click
        │                                                          handled by onGridClick
        │
        └── vertical:    iterate weekDays → segments → events directly
```

Key things to note:

- `init()` is the only side-effect-on-load function. It registers a scroll
  listener (for Back-to-top), an Escape-key listener (closes panels/facets),
  starts a 60-second `setInterval` that refreshes `nowMadrid`, fetches
  `events.json`, then calls `applyFiltersFromUrl()`, `cleanupFilters()`, and a
  `$watch` on `filters.search`.
- **URL params take precedence over localStorage.** `applyFiltersFromUrl()` is
  called immediately after events load and overwrites filter state from URL
  params (`game`, `store`, `format`, `event`). localStorage is only consulted
  for `viewMode` and saved presets — never for filter state.
- `cleanupFilters()` prunes selected facet values that are no longer present
  in the current visible set (events matching all other active filters in the
  current week). It runs after week navigation, filter changes, and on init.
- `resetFilters()` clears the search/facet filters and also resets all
  `segmentFilter` chips to enabled. Segment filters are part of the user-facing
  filter state, not a separate view preference.
- The frontend never POSTs anything. The only network call is `fetch('events.json')`.

### localStorage usage

| Key | Type | Purpose |
| --- | --- | --- |
| `tcg-view-v2` | `'horizontal'` \| `'vertical'` | Persisted view mode |
| `tcg-presets-v1` | JSON array | Saved filter presets |

localStorage is for user personalization only. Filter state (game/store/format
selections) is never written to localStorage. URL params are the mechanism for
sharing or bookmarking filter state.

### URL parameter behavior

| Param | Type | Applied by |
| --- | --- | --- |
| `game` | comma-separated values | `applyFiltersFromUrl()` on load |
| `store` | comma-separated values | `applyFiltersFromUrl()` on load |
| `format` | comma-separated values | `applyFiltersFromUrl()` on load |
| `event` | base64url-encoded event key | Checked on load; opens panel |

URL params are validated against the actual event data — invalid values are
silently dropped. `syncFiltersToUrl()` writes current filter state to the URL
via `history.replaceState` when filters change; this makes filter combinations
bookmarkable and shareable.

---

## 4. Core concepts

### `weekDays` (getter at the heart of rendering)

A 7-element array, one per day of the current week starting at `weekStart`
(always a Monday — `weekMonday()` does the shift). Each entry has:

- `iso` — `YYYY-MM-DD`
- `dow` — short day-of-week label (`Mon`…`Sun`)
- `dom` — short formatted date (e.g. `29 Apr`)
- `isToday` — boolean, comparing `iso` to local `Date` today
- `events` — `filteredEvents` for this day, sorted ascending by `datetime_start`
- `segments` — **compact** array of segment blocks (only segments that have
  events). Used by the vertical view.
- `segmentMap` — **exhaustive** map `{ morning, afternoon, evening, late }`
  with arrays (possibly empty). Used by the horizontal grid so empty
  `(day, segment)` cells can render placeholders without re-scanning.

Because `weekDays` is a getter, it recomputes on every read. This is fine for
the view layer (Alpine memoises within a render) but means callers should not
treat it as cheap.

`filteredEvents` does NOT apply the week filter — it returns events from any
week that match the current facet/search/segment selections. `weekDays` then
groups by day and picks only events falling in the current 7-day window.

### Time segments

A fixed array `SEGMENTS` (`config.js:10`):

| Key | Hours (Madrid) | Label |
| --- | --- | --- |
| `morning` | `[0, 12)` | Morning |
| `afternoon` | `[12, 16)` | Afternoon |
| `evening` | `[16, 19)` | Evening |
| `late` | `[19, 30)` | Late |

The Madrid hour is read by slicing characters 11–13 of the ISO string. This
works because every event's `datetime_start` carries an explicit
`+02:00`/`+01:00` offset, so the visible hour digits already represent Madrid
wall-clock. `segmentForHour(h)` and `segmentOf(iso)` perform the bucketing.

### `segmentFilter` vs `collapsedSegments`

These look similar and do different things — both are
`{ morning, afternoon, evening, late }` boolean maps:

- `segmentFilter` — filter chips. When false, the segment is **excluded** from
  `eventMatches`, `filteredEvents`, and from `activeSegments`. Its row is
  removed from the horizontal grid entirely.
- `collapsedSegments` — collapse chevrons in the segment headers. When true,
  the segment's cards are hidden via `x-show` but the row **stays in the
  grid** so vertical alignment of the other rows is preserved.

Do not collapse these two flags into one — they are deliberately independent.

### Event Detail Panel — `panelOpen` and `selectedEvent`

Two separate state fields drive the panel:

- `panelOpen` — boolean that controls `x-show` on the backdrop and inner panel
  element. Setting it to `false` starts the CSS leave transition; the panel
  fades out while `selectedEvent` is still populated.
- `selectedEvent` — holds the event object currently displayed. Cleared by a
  `setTimeout` in `closePanel()` only *after* the transition has finished
  (~200 ms), so the panel content doesn't vanish mid-fade.

They are intentionally kept separate. Clearing `selectedEvent` immediately
when `closePanel()` fires would blank the panel content before the CSS
opacity/transform transition completes, producing a jarring empty flash.

`openPanel(e)` sets both fields and adds `.no-scroll` to `<body>`.
`closePanel()` sets `panelOpen = false`, removes `.no-scroll`, and schedules
the `selectedEvent = null` cleanup.

A panel open state is also reflected in the URL via `?event=<base64url key>`,
enabling deep-linking to a specific event.

### Horizontal grid vs vertical list

Both are toggled by `viewMode` (persisted in `localStorage` under
`tcg-view-v2`; default is `horizontal`).

**Horizontal** (`.cal-wrap.view-horizontal` → `.cal-grid-h`):

- One CSS grid: 7 columns × `(1 + activeSegments.length)` rows.
- The first row is the day-header cell row; subsequent rows are segment rows.
- Cells are placed via inline `style="grid-column: …; grid-row: …;"` so a
  single flat `<template x-for>` over `cells` produces the whole grid.
- Empty `(day, segment)` cells render as `.seg-cell.is-empty` — dashed gray
  placeholders that keep the row aligned across days.

**Vertical** (`.cal-wrap` default → `.cal-grid`):

- Per-day stacked layout. Each day has its own `.day-col`, with non-empty
  `seg-block`s (uses the compact `day.segments` array).
- Segment blocks carry `data-seg-key` for DOM targeting. Auto-scroll scopes
  lookup to today's `.day-col` first, then queries that column for the current
  segment; global segment IDs are not relied upon.
- Cards in this view use Alpine bindings directly (`@click="openEvent(e)"`,
  `@click.stop="toggleFilter(...)"`, `x-show="canAddToCalendar(e)"`).
  `openEvent(e)` calls `openPanel(e)`, opening the Event Detail Panel.
- Entering vertical view auto-scrolls to today's current time segment. The
  Today button uses the same behavior rather than only scrolling to the day
  header.

### Vertical auto-scroll

`scrollToCurrentSegment()` computes the current segment from `nowMadrid`, finds
today's day column by `day-YYYY-MM-DD`, and then queries inside that column for
`[data-seg-key="<segment>"]`. This scoped lookup prevents duplicate segment
markers across different days from affecting the target.

The scroll runs only after the vertical DOM exists: `init()`, `setView('vertical')`,
and `goToday()` schedule it with Alpine's `$nextTick()` plus
`requestAnimationFrame()`. Do not replace this with a fixed `setTimeout()`; the
timing depends on Alpine finishing the conditional view render.

### Cell rendering — `cells`, `cellCardsHtml`, the `x-html` workaround

This is the trickiest part of the frontend.

The horizontal grid wants nested iteration: outer iteration over cells, inner
iteration over each cell's events. The natural Alpine expression for that is a
`<template x-for>` inside another `<template x-for>`. **This does not work in
this app.** The inner template stayed unprocessed and never re-fired after
`events.json` finished loading — see the comments at
`app.js:384` and around `cellCardsHtml` at `app.js:517`.

The current solution is:

1. **`cells` getter** — flattens to one entry per `(segment, day)` pair, each
   carrying its `events` array, `isToday`, `isPast`, `isEmpty` flags.
2. A single `<template x-for="cell in cells">` renders the cell wrapper.
3. **`cellCardsHtml(cell)`** builds the cards as an HTML string, manually
   escaping every interpolated value with a local `esc()` helper.
4. The wrapper renders that string via `x-html="cellCardsHtml(cell)"`.

`x-html` re-evaluates whenever its expression's reactive dependencies change,
so the cards stay in sync with state. But the cards themselves are no longer
Alpine-managed — they are inert DOM. Click handling therefore has to be
delegated.

### Delegated click — `onGridClick`

Because cards rendered via `x-html` cannot carry Alpine `@click` handlers, the
horizontal grid root has a single `@click="onGridClick($event)"`. The handler
walks up from the click target:

- `[data-calendar-url]` → opens that URL in a new tab; stops propagation.
- `[data-filter]` → reads `data-filter` (one of `game` / `store` / `format`)
  and `data-value`, calls `toggleFilter(field, value)`; stops propagation.
- `.ev-card[data-event-key]` → reads the `data-event-key` attribute (built by
  the `eventKey(e)` helper as `source_url + datetime_start`), looks up the
  matching event in `this.events` via `find`, and calls `openPanel(event)`.

Every property used by `cellCardsHtml` to compose those `data-*` attributes
must also be HTML-escaped via `esc()`.

The vertical view does **not** use this delegation — its cards are real
Alpine elements with regular `@click.stop` bindings.

---

## 5. Rendering system

### Alpine directives in use

- `x-data="app()"` on `<html>` — the single component scope.
- `x-init="init()"` — bootstrap.
- `x-for` — week-day header cells, the flat `cells` list, vertical
  `weekDays`, vertical per-day `segments`, vertical per-segment `events`,
  facet option lists, segment filter chips, saved preset chips.
- `x-show` — loading state, view switching, segment chip visibility,
  facet panels, calendar button (vertical view), Back-to-top button,
  individual segment headers (`!focusMode`), card list collapse, preset bar.
- `x-if` — the two view-mode templates (`horizontal` vs `vertical`) so only
  one tree exists at a time.
- `x-html` — `cellCardsHtml(cell)` for horizontal cells.
- `x-text` — labels, counters, week range, day-of-week, day-of-month, etc.
- `x-model.debounce.250ms` — search input.
- `x-transition:enter*` / `x-transition:leave*` — Back-to-top button fade,
  Event Detail Panel enter/leave.
- `data-seg-key` — plain DOM metadata on vertical segment blocks, used by
  `scrollToCurrentSegment()` after Alpine has rendered the vertical tree.

### Why `x-html` instead of nested `x-for`

Nested `<template x-for>` over content produced by an outer `x-for` was not
re-firing after the outer iterable was rebuilt (e.g. when `events.json`
finished loading). The first render saw an empty inner array and never
recovered. `x-html` sidesteps the templating system completely.

### Risks of this approach

- **XSS risk on every interpolation.** Anything written into the HTML string
  must go through `esc()`. Today that means `event.title`, `event.game`,
  `event.format`, `event.store`, `event.source_url`, the calendar URL, and
  the formatted time.
- **Cards are not reactive.** They are re-rendered only when `cellCardsHtml`
  is re-evaluated, which happens when its reactive dependencies change. The
  60-second `nowMadrid` tick is what re-renders past-event styling on today;
  changing how `nowMadrid` flows would freeze the past-styling logic.
- **All click behaviour is in `onGridClick`.** Adding a new clickable thing
  inside a horizontal card means adding a `data-*` attribute and a branch in
  `onGridClick`, not a new `@click` directive.

---

## 6. Styling system

### CSS variables (`:root` tokens)

Defined at the top of `styles.css` (`styles.css:4`):

- Surfaces & background: `--bg`, `--surface`, `--surface2`
- Borders: `--border`, `--border-strong`
- Accent (purple): `--accent`, `--accent-dim`, `--accent-soft`
- Today highlight: `--today-bg`, `--today-bdr`
- Text: `--text`, `--text-muted`
- Shape: `--radius`, `--shadow-sm`, `--shadow-md`
- Card defaults (overridden per game): `--card-tint`, `--card-accent`

### Per-game palette

Twelve game classes — `.game-mtg`, `.game-starwars`, `.game-riftbound`,
`.game-onepiece`, `.game-pokemon`, `.game-lorcana`, `.game-digimon`,
`.game-yugioh`, `.game-fab`, `.game-finalfantasy`, `.game-weiss`,
`.game-naruto` — plus the fallback `.game-unknown`. Each sets `--card-tint`
and `--card-accent`. Card backgrounds, the left-border accent, and the game
chip all read those variables, so changing palette is a one-line change per
game.

`getGameClass(game)` (`app.js:581`) maps a canonical game name to its class
by looking up `GAME_CLASS_MAP` (`config.js:42`). The map is the source of
truth for this list.

### Layout modes

- `.cal-wrap.view-horizontal` → `.cal-grid-h` is the single-grid horizontal
  view (`grid-template-columns: repeat(7, minmax(0, 1fr))`, dynamic row
  count via inline `:style`).
- `.cal-wrap` (no modifier) is the vertical view, a flex/grid stack of
  `.day-col` per day.

### Responsive behaviour

- `@media (max-width: 900px)` — horizontal grid switches to
  `minmax(160px, 1fr)` columns and a `min-width: 1140px`, forcing horizontal
  scroll on narrow screens (it does not collapse to a stacked view; that is
  what the vertical mode is for). On first visit with no saved viewMode, narrow
  screens default to `vertical` mode automatically.
- `@media (max-width: 500px)` — site header becomes column layout, filter
  inputs go full width, the search input narrows to 120px.

### Today highlighting

- `.day-cell.is-today` and `.day-header-cell.is-today` use `--today-bg` and
  `--today-bdr`.
- `.seg-cell.is-today.is-empty` overrides the default `.is-empty` background
  so today's empty placeholders still read as part of today's column.
- `.seg-cell.is-today.is-past` dims to opacity .55 (background restated to
  prevent cascade order from stripping the today tint).
- `.ev-card.is-past` dims past events on today to opacity .55, .85 on hover.

---

## 7. Known constraints / fragility

- **Script loading order.** `config.js` must execute before `app.js` (which
  reads `STORE_ADDRESSES`, `SEGMENTS`, and `GAME_CLASS_MAP`), and both must
  execute before Alpine's deferred init fires `app()`. The current placement
  — plain `<script>` tags at the bottom of `<body>`, after Alpine's `defer`
  tag in `<head>` — achieves this. Reordering the `<script>` tags, or adding
  `defer` or `type="module"` to either file, breaks initialisation silently.
- **`config.js` uses `var` deliberately.** All four globals are declared with
  `var` so they are attached to `window` and visible to `app.js`. Changing
  them to `const` or `let` scopes them to the script block; `app.js` then
  throws a `ReferenceError` with no obvious pointer back to `config.js`.
- **No `import` or `require` in any frontend JS file.** `import` is a syntax
  error in a regular (non-module) `<script>` tag; `require` is undefined in
  browsers. Even `type="module"` — which browsers support natively, without a
  bundler — breaks the `var`-global pattern: module scripts have their own
  scope, so `config.js` globals would no longer be visible to `app.js`. Don't
  convert without also introducing a bundler (see §8).
- **Nested `x-for` is broken in this app's pattern.** Do not "refactor"
  `cellCardsHtml` back into a nested `<template x-for>` without verifying
  cards actually appear after `events.json` finishes loading. The current
  workaround is deliberate.
- **`x-html` cards are not Alpine-reactive at the card level.** Card-level
  flags like `is-past`, chip-active states, and the `data-*` attributes that
  drive `onGridClick` are all baked into the string at build time. They only
  update when `cells` recomputes — which happens because `cellCardsHtml`
  reads `nowMadrid`, `filters`, and the underlying events.
- **`onGridClick` depends on `data-*` attribute names.** Renaming `data-event-key`,
  `data-filter`, `data-value`, or `data-calendar-url` silently breaks click
  handling without any error.
- **Do not collapse `panelOpen` and `selectedEvent` into one field.** The close
  animation requires `selectedEvent` to remain set while `panelOpen` is already
  `false`; merging them would clear the panel content before the transition
  finishes.
- **Vertical segment auto-scroll depends on scoped `data-seg-key` lookup.**
  Segment keys repeat across days, so code must first find today's `day-*`
  container and then query inside it. Do not use a global `seg-*` ID lookup
  for vertical scrolling.
- **Vertical auto-scroll timing is Alpine-lifecycle sensitive.** The target
  segment exists only after the `x-if="viewMode === 'vertical'"` tree has been
  created. Use `$nextTick()` plus `requestAnimationFrame()` for init, view
  switches, and Today navigation; fixed delays are brittle.
- **Madrid-time correctness is by construction, not by Date math.**
  `readMadridNow()` builds a `Date` whose internal time matches Madrid
  wall-clock by round-tripping through `toLocaleString('en-US', { timeZone:
  'Europe/Madrid' })`. ISO strings are sliced (chars 11–13 for hour, 14–16
  for minute) rather than parsed. Both rely on `datetime_start` carrying an
  explicit Madrid offset.
- **`viewMode` is persisted under a versioned key.** `tcg-view-v2` exists
  specifically because changing the default once before stranded users on
  the old persisted choice. If the default changes again, bump the key.
- **URL params override localStorage on every load.** `applyFiltersFromUrl()`
  is called after `events.json` loads and after the view mode is read from
  localStorage. Any URL param for `game`, `store`, `format`, or `event` will
  win over anything the user had previously selected.

---

## 8. Rules for future changes

### Don't break filtering

`eventMatches`, `filteredEvents`, `availableOptions`, `facetOptionCount`,
`cleanupFilters`, and the `filters` shape are interlocked. A change to one
must keep all of them consistent. In particular:

- `eventMatches(e, opts)` is called from three places (filtering display,
  computing facet options, computing facet counts) with different `opts`
  combinations (`ignore`, `includeSegment`, `includeWeek`). Don't simplify
  the signature.
- `cleanupFilters()` runs after every state change that could narrow the
  visible set, to drop selected facet values that no longer match anything in
  the current week. On week navigation this can remove selections that have
  no events in the new week.

### Don't change the event schema

The frontend reads these fields from each event:

- `store`, `game`, `format`, `title`
- `datetime_start` (must carry an explicit Madrid offset),
  `datetime_end` (nullable; not currently displayed)
- `language` (currently unused by the frontend, but present on every event)
- `source_url`
- `scraped_at`, `first_seen_at`, `last_seen_at`, `is_active` (read by
  `aggregator.py`'s merge logic; the frontend doesn't use them, but the
  schema must keep them consistent across runs)

`price_eur` was removed and **must not be reintroduced**.

### Don't remove the segment system

`SEGMENTS`, `segmentForHour`, `segmentOf`, `segmentFilter`, `collapsedSegments`,
`activeSegments`, `segmentTotals`, `cells`, `focusMode`, and the per-day
`segmentMap` are interlocked. Removing any one of them collapses the
horizontal grid's column-alignment guarantee, the segment chips, or the focus
mode.

### Be careful with Alpine reactivity

- This app uses Alpine 3.14.1 (CDN-pinned). Don't assume Alpine 3.x quirks
  are version-agnostic.
- `x-html`-rendered content is not Alpine-managed.
- Nested `<template x-for>` over content cloned by an outer `x-for` does not
  re-evaluate reliably in this version. Use the `cellCardsHtml` + delegated
  click pattern.

### Avoid restructuring without full understanding

The four-file split is intentional and each boundary exists for a reason.
Before moving code, understand why it's where it is.

- **Don't merge `config.js` back into `app.js`** for cleanliness. The
  data/logic split is the point: `config.js` is the extension surface (add a
  store, game, or segment by editing one file), while `app.js` is the runtime.
  Merging them collapses that boundary and makes the §9 instructions incorrect.
- **Don't convert to ES modules** (`type="module"`) without introducing a
  bundler. Browsers support ES modules natively, but module scripts have their
  own scope — `var` declarations in `config.js` would stop being visible to
  `app.js`, and `x-data="app()"` would fail with `ReferenceError: app is not
  defined`. The fix would require a bundler, reintroducing build complexity.
- **Don't fragment `app.js`** into sub-modules (`helpers.js`, `filters.js`,
  etc.) without a bundler. Alpine resolves `app()` from global scope at
  `x-init` time. Splitting the factory across files means all pieces must
  either be concatenated into a single global or bundled — neither is free.

### Scrapers directory rule

`scrapers/` must contain **only** modules that expose a `scrape()` function.
No helper, utility, or shared modules are permitted inside `scrapers/`.
Anything without `scrape()` belongs in `shared/` or another package.
This ensures `aggregator.py`'s auto-discovery never picks up non-scraper
modules. Do not modify `aggregator.py` to register new scrapers.

### STORE_META is required for every scraper store

Any store name that appears in `public/events.json` but is absent from
`STORE_META` in `public/config.js` will render without an address or Maps
link in the Event Detail Panel. Add the entry to `STORE_META` — not to the
legacy `STORE_ADDRESSES` — before or at the same time as shipping the scraper.

---

## 9. Extension points

### Adding a new store / scraper

1. Add `scrapers/<name>.py` exposing `scrape() -> list[dict]` returning events
   matching the schema in §8. **Do not add helper modules to `scrapers/`** —
   shared code belongs in `shared/`.
2. `aggregator.py` auto-discovers any `scrapers/*.py` (other than
   `__init__.py`) — no registration required and no changes to `aggregator.py`.
3. Prefer importing `GAME_KEYWORDS` / `FORMAT_KEYWORDS` from
   `shared.scraper_keywords` and extending locally if needed.
4. Add an entry to `STORE_META` in `public/config.js` with at least an
   `address` field. This populates the store card in the Event Detail Panel
   and the `location` field in calendar links.
5. Run `python aggregator.py` once and verify the new store appears under
   `by_store` in `public/events_stats.json`.

### Using the audit workflow to find candidate stores

1. Ensure `candidate_stores.json` is up to date (re-run `discover_stores.py`
   if it is stale).
2. Run `python audit_store_event_pages.py` → writes `store_event_audit.json`.
3. Run `python build_scraper_targets.py` → writes `scraper_targets.json`.
4. Review `scraper_targets.json` `"scrape_now"` entries — these are validated
   stores with known event pages. Build scrapers for them in priority order.

### Adding a new game

1. Add the lower-case form to `GAME_CANONICAL` in `aggregator.py` and the
   canonical form to `ALLOWED_GAMES`.
2. Add a CSS class `.game-<key>` setting `--card-tint` and `--card-accent`
   in the per-game palette block of `styles.css`.
3. Add an entry to `GAME_CLASS_MAP` (`config.js:42`) mapping the canonical
   name to the new class.
4. Add keyword tuples to `GAME_KEYWORDS` in `shared/scraper_keywords.py`
   (longer / more specific tokens first) so scrapers pick it up automatically.

### Adding a new format

1. Add the canonical form to `ALLOWED_FORMATS` in `aggregator.py`.
2. Add the keyword(s) to `FORMAT_KEYWORDS` in `shared/scraper_keywords.py`
   (longer / more specific tokens first). Scrapers that import the shared
   list will pick it up automatically.
3. The frontend renders `event.format` as-is — no allowlist there. New formats
   just appear in the Format facet automatically.

### Adding a new filter facet

The current facets (game, store, format) follow an identical pattern. A new
facet means changing all of:

- `filters` (initial state in `app()`)
- `eventMatches` (matching loop)
- `valueFor`, `availableOptions`, `facetOptionCount`, `cleanupFilters`
- A new markup block mirroring the existing Game / Store / Format panels in
  the `.filters` container, plus a `getter` for its options
- The two `data-filter` chips inside `cellCardsHtml` (so it shows on cards)
  if you want it on the per-card chip row

### Safe UI tweaks

- Theme tokens in `:root` and per-game palettes — these only affect
  appearance, never the data model.
- Static labels and copy — anywhere `x-text` is binding a literal in markup.
- Layout breakpoints in `@media` queries.
- The fixed `SEGMENTS` boundaries (e.g. shifting "evening" to start at 17)
  are safe to edit — but verify both views and the chip counters still read
  correctly afterwards.

### Update checklist for common tasks

**Adding a store:**
- [ ] `scrapers/<name>.py` with `scrape()` returning valid events
- [ ] Entry in `STORE_META` in `public/config.js` with `address`
- [ ] Run `python aggregator.py` and verify in `events_stats.json`

**Adding a game:**
- [ ] Entry in `GAME_CANONICAL` + `ALLOWED_GAMES` in `aggregator.py`
- [ ] CSS class `.game-<key>` in `styles.css`
- [ ] Entry in `GAME_CLASS_MAP` in `config.js`
- [ ] Keyword tuples in `GAME_KEYWORDS` in `shared/scraper_keywords.py`

**Adding a format:**
- [ ] Entry in `ALLOWED_FORMATS` in `aggregator.py`
- [ ] Keyword tuples in `FORMAT_KEYWORDS` in `shared/scraper_keywords.py`

**Adding URL-based personalization (new param):**
- [ ] Read in `applyFiltersFromUrl()` in `app.js`
- [ ] Write in `syncFiltersToUrl()` in `app.js`
- [ ] Document precedence: URL params always override localStorage
