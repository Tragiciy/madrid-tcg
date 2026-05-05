# Madrid TCG Events — Product Plan

## Project Purpose

City-level event aggregator for trading card games (TCGs).
Core user question: **"What can I play this week in my city?"**

---

## Current State (as of 2026-05-05)

### Data coverage
- **9 scrapers** — Arte 9, Ítaca, Jupiter Juegos, Micelion Games,
  La Guarida Juegos, Metropolis Center, Asedio Gaming,
  Generacion X - Elfo, Goblintrader Madrid-Norte
- **1,127 events** in `events.json` (869 active)
- **13 games** — Magic, Pokémon, One Piece, Digimon, Lorcana, Star Wars:
  Unlimited, Yu-Gi-Oh!, Flesh and Blood, Weiß Schwarz, Riftbound,
  Final Fantasy TCG, Naruto Mythos, plus Unknown
- **11 `scrape_now` targets** remaining in `scraper_targets.json`

### Frontend
- Alpine.js SPA split across 4 files: `index.html`, `styles.css`,
  `app.js`, `config.js`
- Horizontal grid + vertical list views; `viewMode` persisted
- Faceted filters: game, store, format; filter state bookmarkable via URL
  - Store filter lists all stores from the full dataset regardless of
    current week; selecting a store with zero events this week is allowed
  - Selected store is never dropped by week navigation
  - Game / format filters remain week-scoped
- Saved filter presets via `localStorage` (`tcg-presets-v1`)
- Event Detail Panel: title, game, format, store address, Google Maps link
- Calendar export: Google Calendar, iCalendar (.ics), Outlook
- Event sharing: `navigator.share()` + clipboard fallback; `?event=` deep-link
- Time-segment chips (Morning / Afternoon / Evening / Late)
- Responsive: horizontal scroll on mobile, auto-switch to vertical on
  first visit on narrow screens

### Backend pipeline
- `aggregator.py` auto-discovers and runs all `scrapers/*.py`
- Merge model: full historical record, lifecycle fields per event
  (`first_seen_at`, `last_seen_at`, `is_active`)
- Shared keyword classification: `shared/scraper_keywords.py`
  (`GAME_KEYWORDS`, `FORMAT_KEYWORDS`)
- `shared/store_matching.py`: store name/address normalization + fuzzy matching
- Scraper stats + anomaly detection → `public/events_stats.json`
- Discovery pipeline: `discover_stores.py` → `audit_store_event_pages.py`
  → `build_scraper_targets.py` → `scraper_targets.json`
- GitHub Actions: daily `update.yml` cron (06:00 UTC)

---

## Completed milestones

| # | Feature | Notes |
|---|---|---|
| ✅ | Event sharing | `navigator.share()` + clipboard fallback; `?event=` deep-link param |
| ✅ | Bookmarkable filter URLs | `applyFiltersFromUrl()` / `syncFiltersToUrl()` on load and filter change |
| ✅ | Filter presets | Save / apply / delete named filter combos; persisted in `localStorage` |
| ✅ | Three-provider calendar export | Google Calendar, iCalendar (.ics download), Outlook |
| ✅ | Event Detail Panel | Store address, Google Maps link, game color, past badge |
| ✅ | `STORE_META` system | Per-store address / notes / website in `config.js`; used by panel + calendar |
| ✅ | Shared keyword utilities | `GAME_KEYWORDS`, `FORMAT_KEYWORDS`, `extract_*` in `shared/scraper_keywords.py` |
| ✅ | Scraper anomaly detection | `events_stats.json` with `sharp_drop` flag per store |
| ✅ | Store discovery pipeline | `discover_stores.py` + Wizards locator + audit + target classification |
| ✅ | URL params override localStorage | Filter state comes from URL; localStorage is personalization only |
| ✅ | Responsive auto-view | First visit on narrow screen defaults to vertical; saved thereafter |
| ✅ | Past-event dimming | Past segments and past events on today dimmed but not hidden |
| ✅ | Focus mode | Single active segment collapses headers → flat per-day card list |
| ✅ | Store filter always-visible | `allStores` getter; store never dropped by `cleanupFilters` on week change |
| ✅ | 3 new scrapers + STORE_META | Asedio Gaming, Generacion X - Elfo, Goblintrader Madrid-Norte |

---

## Active sprint — two tracks

Two independent tracks. Track A is Python backend. Track B is entirely
frontend. Both can run in parallel.

---

### Track A — Scraper expansion + pipeline automation

#### A1. Build scrapers for remaining `scrape_now` targets

3 scrapers shipped (Asedio Gaming, Generacion X - Elfo, Goblintrader
Madrid-Norte). 11 targets remain. Group by platform.

**Group 1 — WordPress (6 stores remaining)**

Most use The Events Calendar or Modern Events Calendar plugin with a
predictable REST endpoint (`/wp-json/tribe/events/v1/events`) or consistent
HTML structure.

| Store | Event page | Primary game |
|---|---|---|
| Collectorage | /calendario | Star Wars: Unlimited |
| ~~Generacion X - Elfo~~ | ~~shipped~~ | ~~Magic: The Gathering~~ |
| Kamikaze Freak Shop | /eventos/ | Magic: The Gathering |
| Metamorfo | /calendario | Yu-Gi-Oh! |
| Panda Games | /juegos/eventos/ | Magic: The Gathering |
| The Big Bang Games | /eventos | Star Wars: Unlimited |
| TopDeck | /calendario-de-torneos/ | One Piece |

Steps:

1. **`shared/wordpress_events.py`** — no `scrape()`, no network on import:
   - `fetch_wp_events(base_url, days_ahead=90) -> list[dict]`
   - Try Tribe REST endpoint first; fall back to HTML parsing of common
     plugin class patterns (`.tribe-event`, `.mec-event-title`).
   - Returns raw dicts: `title`, `start_iso`, `end_iso`, `url`, `description`.
   - Raises `ScraperFetchError` on total failure.

2. One thin file per store in `scrapers/`, e.g. `scrapers/collectorage.py` —
   calls the shared helper, maps to event schema, uses keyword extraction,
   falls back to `DEFAULT_GAME`.

3. Add each store to `STORE_META` in `public/config.js` with a valid `address`.

**Group 2 — Shopify (✅ shipped)**

| Store | Event page | Primary game |
|---|---|---|
| ~~Asedio Gaming~~ | ~~shipped~~ | ~~Flesh and Blood~~ |

**Group 3 — Unknown platform (5 stores remaining)**

| Store | Event page | Notes |
|---|---|---|
| Goblintrader Central | /gb/eventos | Likely PrestaShop |
| ~~Goblintrader Madrid-Norte~~ | ~~shipped~~ | ~~same codebase as Central~~ |
| Gladius Games | /content/5-calendario-de-eventos | PrestaShop or custom |
| MADAKIBA | /es/c/eventos | Unknown; fetch + inspect |
| Mundicomics | /torneos-tcg | Unknown; fetch + inspect |
| Padis | /284-inscripciones-torneos | PrestaShop category page |

Goblintrader pair: audit together, share a `shared/goblintrader.py` helper
if platform is confirmed. For each unknown: inspect in browser devtools,
identify the data format, then write the scraper.

**Acceptance criteria for every new scraper:**
- `python -m scrapers.<name>` prints events without error.
- All events have `store`, `title`, `datetime_start` (explicit Madrid offset),
  `source_url`.
- `game` populated for ≥ 80 % of events (keyword match or `DEFAULT_GAME`).
- Store appears under `by_store` in `events_stats.json` after
  `python aggregator.py`.
- `STORE_META` entry in `public/config.js` has a valid `address`.

#### A2. Automate the discovery pipeline

**New file: `.github/workflows/discover.yml`** — weekly cron (Monday 08:00 UTC):

```yaml
name: Discover new stores
on:
  schedule:
    - cron: "0 8 * * 1"
  workflow_dispatch:
jobs:
  discover:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11", cache: pip }
      - run: pip install -r requirements.txt
      - name: Run pipeline
        run: |
          python discover_stores.py
          python audit_store_event_pages.py
          python build_scraper_targets.py
      - name: Commit if changed
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          if git diff --quiet -- candidate_stores.json store_event_audit.json scraper_targets.json; then
            exit 0
          fi
          git add candidate_stores.json store_event_audit.json scraper_targets.json
          git commit -m "chore: update store discovery ($(date -u +'%Y-%m-%d'))"
          git push
      - name: Open issue for new targets
        uses: actions/github-script@v7
        with:
          script: |
            const targets = JSON.parse(require('fs').readFileSync('scraper_targets.json'));
            const newOnes = (targets.scrape_now || []).filter(t => t.new === true);
            if (!newOnes.length) return;
            const body = newOnes.map(t =>
              `- **${t.name}** — [${t.best_event_page}](${t.best_event_page}) (${t.game_detected || '?'})`
            ).join('\n');
            await github.rest.issues.create({
              owner: context.repo.owner, repo: context.repo.repo,
              title: `New scraper targets (${newOnes.length})`,
              body: `Weekly discovery found ${newOnes.length} new scrape_now stores:\n\n${body}`,
              labels: ['scraper', 'new-store'],
            });
```

**Required change to `build_scraper_targets.py`:** mark each `scrape_now`
entry as `"new": true/false` by comparing against store names in
`public/events_stats.json` (`by_store` keys = already-scraped stores).

---

### Track B — Favorite events

#### Alpine reactivity constraint
`Set` mutations are not tracked by Alpine's proxy. Favorites must be a plain
**array** in state. `Array.includes` is used for lookups. This keeps
`cellCardsHtml` reactivity working — it reads `this.favorites` (the array)
and re-renders when it changes.

#### Horizontal grid constraint
Cards in the horizontal grid are rendered as HTML strings by `cellCardsHtml()`
and injected via `x-html`. Favorite buttons in that view must follow the
`data-*` + `onGridClick` delegation pattern, not Alpine `@click` bindings.

#### B1. Core state and persistence — `public/app.js`

New constant near `PRESETS_STORAGE_KEY`:
```js
const FAVORITES_STORAGE_KEY = 'tcg-favorites-v1';
```

New state fields in `app()`:
```js
favorites:         [],    // array of eventKey strings
showFavoritesOnly: false,
```

New methods:
```js
loadFavorites() {
  try {
    const raw = localStorage.getItem(FAVORITES_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed))
        this.favorites = parsed.filter(k => typeof k === 'string');
    }
  } catch {}
},
persistFavorites() {
  localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(this.favorites));
},
isFavorite(e) {
  if (!e) return false;
  return this.favorites.includes(this.eventKey(e));
},
toggleFavorite(keyOrEvent) {
  const key = typeof keyOrEvent === 'string' ? keyOrEvent : this.eventKey(keyOrEvent);
  const idx = this.favorites.indexOf(key);
  if (idx >= 0) this.favorites.splice(idx, 1);
  else           this.favorites.push(key);
  this.persistFavorites();
},
get favoritesCount() { return this.favorites.length; },
```

Call `this.loadFavorites()` at the top of `init()`, before `fetch()`.

Add to `eventMatches()` (after search check, before segment check):
```js
if (this.showFavoritesOnly && !this.favorites.includes(this.eventKey(e))) return false;
```

Add `showFavoritesOnly = false` to `resetFilters()`.

#### B2. Star button — vertical view — `public/index.html`

Inside `.ev-footer` alongside `.calendar-btn`:
```html
<button class="fav-btn" :class="{ 'is-fav': isFavorite(e) }" type="button"
        @click.stop="toggleFavorite(e)"
        :title="isFavorite(e) ? 'Remove from saved' : 'Save event'"
        :aria-label="isFavorite(e) ? 'Remove from saved' : 'Save event'">★</button>
```

#### B3. Star button — horizontal view — `public/app.js`

In `cellCardsHtml(cell)`, add to each card's HTML string:
```js
const isFav = this.favorites.includes(eventKey(e));
// in card footer:
`<button class="fav-btn${isFav ? ' is-fav' : ''}" data-fav-key="${esc(eventKey(e))}"
         title="${isFav ? 'Remove from saved' : 'Save event'}">★</button>`
```

In `onGridClick(ev)`, new branch **before** the `.ev-card` branch:
```js
const favBtn = target.closest('[data-fav-key]');
if (favBtn) { this.toggleFavorite(favBtn.dataset.favKey); ev.stopPropagation(); return; }
```

#### B4. Star button — Event Detail Panel — `public/index.html`

In `.panel-actions` alongside "Open Source" and "Share":
```html
<button class="btn fav-btn" :class="{ 'is-fav': isFavorite(selectedEvent) }"
        type="button" @click.stop="toggleFavorite(selectedEvent)"
        x-show="selectedEvent"
        x-text="isFavorite(selectedEvent) ? '★ Saved' : '☆ Save'"></button>
```

#### B5. "Show saved" toggle — `public/index.html`

In the `.filters` bar, after the Format facet:
```html
<button class="btn fav-toggle" :class="{ active: showFavoritesOnly }"
        @click="showFavoritesOnly = !showFavoritesOnly; cleanupFilters()"
        x-show="favoritesCount > 0"
        :title="showFavoritesOnly ? 'Show all events' : 'Show saved events only'">
  <span>★</span>
  <span x-text="favoritesCount"></span>
</button>
```

Hidden until at least one event is saved (`x-show="favoritesCount > 0"`).

#### B6. Styles — `public/styles.css`

```css
.fav-btn { background:none; border:none; cursor:pointer; color:var(--text-muted);
           font-size:1rem; padding:2px 4px; line-height:1;
           border-radius:var(--radius); transition:color .15s, transform .1s; }
.fav-btn:hover  { color:var(--accent); }
.fav-btn.is-fav { color:#f4b942; }
.fav-btn:active { transform:scale(1.25); }
.fav-toggle        { display:flex; align-items:center; gap:4px; }
.fav-toggle.active { background:var(--accent); color:#fff; border-color:var(--accent); }
```

#### Implementation order within Track B

```
B1 (state + persistence)
  ├── B2 (vertical) ──┐
  └── B3 (horizontal) ┤
                      ▼
                  B4 (panel)
                      │
                      ▼
                  B5 (filter toggle)
                      │
                      ▼
                  B6 (styles — can run alongside B2/B3)
```

---

## Upcoming priorities

These are confirmed-valuable items that follow the active sprint. Order roughly
by impact-to-effort ratio.

### P1 — Scraper health dashboard

**Goal:** Visualize scraper status without reading raw JSON.

**Implementation:**
- Static `public/health.html` — a standalone page (no Alpine dependency,
  plain JS) that fetches `events_stats.json` and renders:
  - Per-store table: `raw_this_run`, `active`, `dropped`, `anomaly` badge.
  - Last-run timestamp.
  - Anomaly warnings highlighted in amber.
  - Failed scrapers highlighted in red.
- No backend required; refreshes automatically on each `aggregator.py` run
  since `events_stats.json` is regenerated daily.
- Link from the site footer or a hidden `/health` path for maintainers only.

**Effort:** Small (1 day). **Impact:** Medium — eliminates manual JSON
inspection for ongoing maintenance.

### P2 — Progressive Web App (PWA)

**Goal:** Mobile users can install the site to their home screen; basic
offline fallback when the network is unavailable.

**Implementation:**
- `public/manifest.json` — name, icons, theme color, `display: standalone`.
- `public/sw.js` — service worker:
  - Cache-first for `styles.css`, `app.js`, `config.js`, `index.html`.
  - Network-first with stale-while-revalidate for `events.json`.
  - Offline fallback: serve stale `events.json` from cache if network fails.
- Add `<link rel="manifest">` and SW registration to `index.html`.

**Effort:** Small–Medium (1–2 days). **Impact:** Medium — significantly
improves the mobile experience for repeat users.

**Constraints:** SW must be served from the same origin. Cloudflare Pages /
GitHub Pages both support this. The `events.json` cache strategy means users
see slightly stale data at worst — acceptable for this use case.

### P3 — `events.json` payload management

**Goal:** Prevent `events.json` from growing unbounded as historical events
accumulate across months and years.

**Current state:** 1,099 events / 15,387 lines. At current growth rate the
file will exceed 5,000 events within a year.

**Implementation options (pick one):**

**Option A — Trim on write (simpler):**
`aggregator.py` keeps the full historical record internally but writes only
events from the last 90 days + all future events to `public/events.json`.
A separate `public/events_archive.json` gets the rest (committed monthly,
not daily). Frontend only fetches the main file.

**Option B — Year-split (cleaner for archiving):**
`aggregator.py` writes `public/events_<year>.json`; a `public/events_index.json`
lists available years. The frontend fetches the current year + optionally
prior years on demand.

**Recommendation:** Option A is the simpler change. Implement when the file
exceeds ~3,000 events or 500 KB.

### P4 — Store metadata expansion

**Goal:** Richer per-store information in the Event Detail Panel and
potentially a "Browse stores" section.

**Implementation:**
- Extend `STORE_META` entries in `config.js` to include:
  - `website` (already optional, ensure all stores have it)
  - `instagram` / `twitter` — social links
  - `hours` — opening hours string, displayed in the panel
  - `games` — array of games the store primarily runs
- Update the panel `<div class="panel-store">` section to render `website`
  and social links if present.
- No backend or new files needed — this is purely a `config.js` content
  improvement.

**Effort:** Small per store (data gathering is the bottleneck).

### P5 — Additional game discoverers

**Goal:** Find stores the Wizards locator misses (e.g. Pokémon-only or
One Piece–only stores).

**New discoverer modules:**
- `discoverers/pokemon_locator.py` — Pokémon Play locator API
  (`op.pokemon-card.com` or the Play! Pokémon store finder)
- `discoverers/one_piece_locator.py` — Bandai Namco official store finder
- `discoverers/swu_locator.py` — Star Wars: Unlimited OP locator if available

Each follows the same `discover() -> list[dict]` interface as the existing
`wizards_locator.py`. `discover_stores.py` auto-discovers them.

**Effort:** Medium (researching each API takes time). Run after A1 is done
to maximize value from new stores found.

---

## Future / long-term

### Geographic expansion

| Phase | Scope | New fields / changes |
|---|---|---|
| Phase 2 | Spain — major cities | Add `city` field to events; city facet filter; city selector in header |
| Phase 3 | Multi-city UI | Separate routes or city selector; per-city `events_<city>.json` |

**Cities for Phase 2:** Barcelona, Valencia, Sevilla, Málaga, Bilbao.
**Trigger:** Madrid coverage is stable (> 20 scrapers, < 5 % unknown-store events).

### Backend migration

Introduce a backend only when **any** of the following is true:
- 20+ active scrapers
- Multiple cities active
- User accounts needed (notifications, cross-device favorites)
- Events.json payload management becomes untenable even with Option A/B

Target stack when the time comes:
```
scrapers → normalize → PostgreSQL → REST API → frontend
```
Tables: `stores`, `events`, `scraper_runs`, `anomalies`, `candidate_stores`.

### Notifications (post-backend)

- "Notify me when a new event is posted for [game] at [store]" — requires
  user accounts + Web Push or email.
- Not viable on a static site; schedule for after backend migration.

### Community / self-service store submissions

- Form for store owners to submit or correct event data.
- Requires a submission queue and human review step.
- Not viable until the data pipeline is stable and a maintainer can process
  submissions regularly.

---

## Development timeline

| Horizon | Track | Work |
|---|---|---|
| **Now** | A | Build WordPress scraper batch (7 stores + shared helper) |
| **Now** | B | B1 → B2/B3 → B4 → B5/B6 (favorites, end-to-end) |
| **Next 2–4 weeks** | A | Unknown-platform scrapers (6 stores); discovery automation |
| **Next 2–4 weeks** | — | P1 health dashboard; P2 PWA |
| **Next month** | — | P4 store metadata fill-out |
| **Mid-term** | — | P3 payload management; P5 new discoverers |
| **Long-term** | — | Phase 2 geographic expansion; backend migration triggers |

---

## What NOT to do

- Do not migrate to backend prematurely (static-site constraints force good
  habits; the backend adds ops overhead).
- Do not over-engineer the UI before data coverage is solid — data first.
- Do not run fully automatic scraping without a review step for new stores.
- Do not expand globally before Spain coverage is stable.
- Do not sync favorites to a server — they are personal and offline-local;
  URL sharing (`?event=`) handles the "share this with someone" case.
- Do not add helper modules to `scrapers/` — `shared/` is the place.
- Do not modify `aggregator.py` to register new scrapers — auto-discovery
  handles it.

---

## Core Strategy

> **Data → Coverage → Reliability → Scale**

Not the reverse. The most impactful next action is always expanding data
coverage, not improving architecture or UI.
