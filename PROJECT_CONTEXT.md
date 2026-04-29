# PROJECT_CONTEXT

Technical reference for AI agents and developers working on this repository.
Describes the project as it currently exists — not as it could be.

The project has two halves that meet at one file:

1. A **Python data pipeline** (`scrapers/` + `aggregator.py`) that produces
   `public/events.json`.
2. A **single-page Alpine.js frontend** (`public/index.html`) that consumes
   `public/events.json`.

Most of this document is about the frontend, because that is where almost all
of the runtime logic, state, and rendering complexity lives.

---

## 1. File structure

| Path | Purpose |
| --- | --- |
| `public/index.html` | The entire frontend: HTML structure, inlined CSS in a `<style>` block, and JavaScript including the Alpine `app()` factory. ~1648 lines. |
| `public/events.json` | The frontend's only data source. Array of event objects, sorted by `datetime_start`. Loaded once via `fetch('events.json')`. |
| `public/events_stats.json` | Observability output from `aggregator.py` (per-store counts, anomaly flags). **The frontend never reads this file.** |
| `aggregator.py` | Discovers `scrapers/*.py`, calls each `scrape()`, validates, normalises (`GAME_CANONICAL`, `ALLOWED_GAMES`, `ALLOWED_FORMATS`), merges with the previous `events.json` using a stable `event_key`, writes both `events.json` and `events_stats.json`. |
| `scrapers/__init__.py` | Empty marker; makes `scrapers/` a package. |
| `scrapers/<store>.py` | One module per store. Each exposes `scrape() -> list[dict]`. Currently: `arte9`, `itaca`, `jupiter_juegos`, `la_guarida_juegos`, `metropolis_center`, `micelion_games`. |
| `requirements.txt` | Python deps: `requests`, `beautifulsoup4`, `playwright`. |
| `.github/workflows/update.yml` | Cron at `0 6 * * *` (daily 06:00 UTC). Runs `python aggregator.py` and commits diffs to `public/events.json` / `public/events_stats.json`. |
| `.github/workflows/deploy-pages.yml` | GitHub-Pages-style publish workflow that uploads `public/` as a Pages artifact on push to `main`. The active deployment target is **Cloudflare Pages** at `https://tragiciy.github.io/madrid-tcg/`. |
| `.gitignore` | Excludes `__pycache__/`, virtualenvs, Playwright local browsers, OS junk, and `.claude/`. |

---

## 2. Architecture overview

The frontend is a **single Alpine.js component** scoped to the `<html>`
element via `x-data="app()"` and initialised by `x-init="init()"`.

Everything — primitive state, the events array, derived getters, formatters,
event handlers, and the cell-card HTML builder — is a property on the same
object returned by `app()` (`public/index.html:1159`). There is no separation
between view logic and state, no component tree, no module boundary:

- HTML is in the same file.
- CSS is in a `<style>` block in the same file.
- JavaScript helpers and the `app()` factory are in a `<script>` block in the
  same file.

This is intentional — it is what allows the project to ship without a build
step. It also means any change to the frontend touches a single, large file
and must respect tight coupling between the markup, the CSS class names, and
the data model on `app()`.

---

## 3. Data flow

```
fetch('events.json')
        │
        ▼
   this.events  ◀────────────────────────────────────────┐
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
  listener (for Back-to-top), an Escape-key listener (closes facet panels),
  starts a 60-second `setInterval` that refreshes `nowMadrid`, fetches
  `events.json`, then runs `cleanupFilters()` and a `$watch` on
  `filters.search`.
- `cleanupFilters()` prunes selected facet values that are no longer present
  in the current visible set, so e.g. switching weeks doesn't leave a stale
  filter that hides everything.
- The frontend never POSTs anything. The only network call is `fetch('events.json')`.

---

## 4. Core concepts

These are the load-bearing pieces of frontend logic. Read them carefully
before changing anything.

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

### Time segments

A fixed array `SEGMENTS` (`public/index.html:1124`):

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
- Cards in this view use Alpine bindings directly (`@click="openEvent(e)"`,
  `@click.stop="toggleFilter(...)"`, `x-show="canAddToCalendar(e)"`).

### Cell rendering — `cells`, `cellCardsHtml`, the `x-html` workaround

This is the trickiest part of the frontend.

The horizontal grid wants nested iteration: outer iteration over cells, inner
iteration over each cell's events. The natural Alpine expression for that is a
`<template x-for>` inside another `<template x-for>`. **This does not work in
this app.** The inner template stayed unprocessed and never re-fired after
`events.json` finished loading — see the comments at
`public/index.html:1418` and around `cellCardsHtml` at line 1548.

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
- `.ev-card[data-url]` → opens `source_url` in a new tab.

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
  facet option lists, segment filter chips.
- `x-show` — loading state, view switching, segment chip visibility,
  facet panels, calendar button (vertical view), Back-to-top button,
  individual segment headers (`!focusMode`), card list collapse.
- `x-if` — the two view-mode templates (`horizontal` vs `vertical`) so only
  one tree exists at a time.
- `x-html` — `cellCardsHtml(cell)` for horizontal cells.
- `x-text` — labels, counters, week range, day-of-week, day-of-month, etc.
- `x-model.debounce.250ms` — search input.
- `x-transition:enter*` / `x-transition:leave*` — Back-to-top button fade.

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

Defined at the top of the `<style>` block (`public/index.html:12`):

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

`getGameClass(game)` (`public/index.html:1622`) maps a canonical game name to
its class. The map is the source of truth for this list.

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
  what the vertical mode is for).
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

- **Single-file coupling.** `public/index.html` contains markup, styles,
  helpers, and the entire `app()` factory. Splitting into separate files
  would require replacing the inline `<script>` with one or more loaded
  scripts, taking care to preserve Alpine's load-order behaviour.
- **Nested `x-for` is broken in this app's pattern.** Do not "refactor"
  `cellCardsHtml` back into a nested `<template x-for>` without verifying
  cards actually appear after `events.json` finishes loading. The current
  workaround is deliberate.
- **`x-html` cards are not Alpine-reactive at the card level.** Card-level
  flags like `is-past`, chip-active states, and the `data-*` attributes that
  drive `onGridClick` are all baked into the string at build time. They only
  update when `cells` recomputes — which happens because `cellCardsHtml`
  reads `nowMadrid`, `filters`, and the underlying events.
- **`onGridClick` depends on `data-*` attribute names.** Renaming `data-url`,
  `data-filter`, `data-value`, or `data-calendar-url` silently breaks click
  handling without any error.
- **Madrid-time correctness is by construction, not by Date math.**
  `readMadridNow()` builds a `Date` whose internal time matches Madrid
  wall-clock by round-tripping through `toLocaleString('en-US', { timeZone:
  'Europe/Madrid' })`. ISO strings are sliced (chars 11–13 for hour, 14–16
  for minute) rather than parsed. Both rely on `datetime_start` carrying an
  explicit Madrid offset.
- **`viewMode` is persisted under a versioned key.** `tcg-view-v2` exists
  specifically because changing the default once before stranded users on
  the old persisted choice. If the default changes again, bump the key.

---

## 8. Rules for future changes

These rules are for any agent or contributor planning a change.

### Don't break filtering

`eventMatches`, `filteredEvents`, `availableOptions`, `facetOptionCount`,
`cleanupFilters`, and the `filters` shape are interlocked. A change to one
must keep all of them consistent. In particular:

- `eventMatches(e, opts)` is called from three places (filtering display,
  computing facet options, computing facet counts) with different `opts`
  combinations (`ignore`, `includeSegment`, `includeWeek`). Don't simplify
  the signature.
- `cleanupFilters()` runs after every state change that could narrow the
  visible set, to drop selected facet values that no longer match anything.

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

The single-file frontend looks like it could be tidied — split CSS into a
file, extract helpers, decompose `app()` — but every part of it was placed
where it is for a specific reason (load order, no-build-step constraint,
Alpine quirks). Touch with a clear intent and verify the result in the
browser before claiming a refactor is safe.

---

## 9. Extension points

### Adding a new store / scraper

1. Add `scrapers/<name>.py` exposing `scrape() -> list[dict]` returning events
   matching the schema in §8.
2. `aggregator.py` auto-discovers any `scrapers/*.py` (other than
   `__init__.py`) — no registration required.
3. If the store should appear in the Google Calendar `location` field, add an
   entry to `STORE_ADDRESSES` (`public/index.html:1052`).
4. Run `python aggregator.py` once and verify the new store appears under
   `by_store` in `public/events_stats.json`.

### Adding a new game

1. Add the lower-case form to `GAME_CANONICAL` in `aggregator.py` and the
   canonical form to `ALLOWED_GAMES`.
2. Add a CSS class `.game-<key>` setting `--card-tint` and `--card-accent`
   in the per-game palette block of `public/index.html`.
3. Add an entry to the map inside `getGameClass` (`public/index.html:1622`)
   mapping the canonical name to the new class.

### Adding a new format

1. Add the canonical form to `ALLOWED_FORMATS` in `aggregator.py`.
2. Update scraper-side `FORMAT_KEYWORDS` lists so existing scrapers can detect
   it. Longer / more specific tokens come first.
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
