# Madrid TCG Events — Product Plan

## Project Purpose

City-level event aggregator for trading card games (TCGs).
Core user question: **"What can I play this week in my city?"**

---

## Current State (as of 2026-05-14)

### Data coverage
- **20 scrapers** — Arte 9, Ítaca, Jupiter Juegos, Micelion Games,
  La Guarida Juegos, Metropolis Center, Asedio Gaming,
  Generacion X - Elfo, Goblintrader Madrid-Norte, Goblintrader Central,
  Kamikaze Freak Shop, The Big Bang Games, Panda Games, Metamorfo,
  Collectorage, TopDeck, Gladius Games, MADAKIBA, Mundicomics, Padis
- **~1,200 events** in `events.json` (~660 active; updated daily)
- **13 games** — Magic, Pokémon, One Piece, Digimon, Lorcana, Star Wars:
  Unlimited, Yu-Gi-Oh!, Flesh and Blood, Weiß Schwarz, Riftbound,
  Final Fantasy TCG, Naruto Mythos, plus Unknown
- **0 `scrape_now` targets** remaining in `scraper_targets.json`

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
| ✅ | WordPress scraper batch (4 stores) | `shared/wordpress_events.py` + Kamikaze Freak Shop, The Big Bang Games, Panda Games, Metamorfo |
| ✅ | Filter search in dropdowns | `filterSearch` per facet narrows long option lists |
| ✅ | Hide unknown games by default | Unknown-game events filtered out unless user explicitly selects "Unknown" |
| ✅ | Default filter preset | Mark a preset as default; auto-applied on fresh visits; `tcg-default-preset-v1` |
| ✅ | First-run onboarding | Modal on first visit (no default, no URL params) to pick initial game/store; `tcg-onboarding-v1` |
| ✅ | Smart-tap / focus mode on auto-apply | Chip behavior tweaks when default preset is auto-applied |
| ✅ | Improved preset management UX | Save / edit / delete preset flow polished |
| ✅ | Cancelled-event cleanup | `aggregator.py` hard-deletes future events missing for 3 consecutive runs (`MISSING_RUNS_BEFORE_DELETE = 3`) |
| ✅ | Undo filter button | `filterHistory` stack (max 5); `undoFilterAction()` reverses the most recent filter change |
| ✅ | Single-event favorites | ★ on cards + panel, `tcg-favorites-v1` localStorage, "Show saved" toggle, gold visual highlight |
| ✅ | Format unification + format_official + best_of | Unified `format` vocabulary (Premier/Armory/CC → Standard), per-game default for non-MTG, `format_official` for original names, `best_of` field for BO1/BO3, title-priority Prerelease fixes Sealed bug. Backfill via `scripts/reclassify_formats.py` reduced null format from 27.5% → 10.5% |
| ✅ | TopDeck + Collectorage scrapers | WordPress batch via `shared/wordpress_events.py`; 2 new stores; 0 WordPress targets remaining (#61) |
| ✅ | 5 unknown-platform scrapers | Padis (PrestaShop), MADAKIBA, Goblintrader Central, Gladius Games, Mundicomics; 0 `scrape_now` targets remaining (#62) |

---

## Completed sprint

Both tracks closed as of 2026-05-14.

- **Track A** — all `scrape_now` targets shipped: WordPress batch (Collectorage,
  TopDeck) in #61; unknown-platform stores (Padis, MADAKIBA, Goblintrader Central,
  Gladius Games, Mundicomics) in #62. 20 active scrapers, 0 remaining targets.
- **Track B** — Favorites implemented: `tcg-favorites-v1` localStorage,
  star buttons in vertical + horizontal views and detail panel, "Show saved"
  toggle in filter bar, gold highlight.

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

**Constraints:** SW must be served from the same origin — Cloudflare Pages
supports this out of the box. The `events.json` cache strategy means users
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
- 20+ active scrapers ← **reached (20 scrapers as of 2026-05-14)**
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

| Horizon | Work |
|---|---|
| **Now** | P1 scraper health dashboard; A2 discovery automation |
| **Next 2–4 weeks** | P2 PWA; P4 store metadata expansion |
| **Mid-term** | P3 payload management; P5 new game discoverers |
| **Long-term** | Phase 2 geographic expansion; backend migration |

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
