# PROJECT_CONTEXT

Technical reference for AI agents and developers working on this repository.
Describes the project as it currently exists — not as it could be.

The project has two halves that meet at one file:

1. A **Python data pipeline** (`scrapers/` + `aggregator.py`) that produces
   the full history in `data/events.json` and the static read API in
   `public/api/v1/` (contract: `docs/api-v1.md`).
2. A **four-file Alpine.js frontend** (`public/index.html`, `public/styles.css`,
   `public/app.js`, `public/config.js`) that consumes `public/api/v1/`.

---

## 1. Backend pipeline

### 1·. Pipeline module layout

`aggregator.py` holds only `main()`, the orchestration of one run. Each stage
lives in `pipeline/`:

| Module | Responsibility |
|---|---|
| `pipeline/config.py` | Paths, timezone and thresholds |
| `pipeline/runner.py` | Discover `scrapers/*.py` and run each in a subprocess (`tools/run_scraper.py`), in parallel |
| `pipeline/normalize.py` | Game/format vocabulary, `_normalize_game` and the per-event shape check |
| `pipeline/reconcile.py` | `event_key`, `merge_events`, source protection (`suspect_zero`), locator dedup and name-variant detection. Pure, with no I/O |
| `pipeline/publish.py` | Generate the static read API `public/api/v1/`: cities, per-city index, stores, `upcoming.json` and one file per ISO week (`docs/api-v1.md`) |
| `pipeline/pages.py` | Static SEO pages in Spanish (root: `/madrid/…`, `/madrid/hoy/`, `/madrid/tiendas/<id>/`) and English (`/en/madrid/…`, `/en/madrid/today/`, `/en/madrid/stores/<id>/`). Every page links its counterpart via `hreflang`, with Spanish as `x-default`, and has a language switch. The copy lives in `LANGS`, and paths come from `page_path(lang, key)`. Static SEO pages from the same data (Jinja2 templates in `pipeline/templates/`): `/madrid/` (7 days), `/madrid/hoy/`, `/madrid/fin-de-semana/`, one page per game and per store (28 days), plus `sitemap.xml`, `robots.txt` and `404.html`. Each list embeds schema.org `Event` JSON-LD. Only http(s) links from scraped data are rendered |
| `pipeline/share.py` | One static share page per event, `/e/<sha1(id)[:10]>/`, for events from 14 days ago to 60 days ahead. Each page has Open Graph tags (title, date, store, a game image from `public/og/`) and redirects to `/?event=…&w=…`. The pages are `noindex`. The API adds `share` to those events, and the app's Share/Copy link uses it, because chat apps can't run JavaScript and would otherwise only see the site-wide preview. Images are rendered once with `python -m tools.render_og_images` and committed |
| `pipeline/repository.py` | Load and save `data/events.json` (migrated once from the legacy `public/events.json`), the stats file, the run log and the `data/*.json` maps (retired stores, renames) |
| `pipeline/health.py` | Validation report, `build_stats` anomalies and console health output |

The JSON-backed `repository` is the seam the Postgres move will replace (#98).

### 1a. Scraper discovery

`aggregator.py` auto-discovers every `*.py` file in `scrapers/` (except
`__init__.py`) and calls its `scrape()` function. **No helper or utility
modules are allowed inside `scrapers/`** — anything without a `scrape()`
must live outside this directory (currently in `shared/`). Do not modify
`aggregator.py` merely to register a new scraper; place the scraper file in
`scrapers/` and it is picked up automatically.

Current per-store scrapers (22): `arte9`, `generacion_x_elfo`,
`gladius_games`, `goblintrader_fuenlabrada`, `goblintrader_madrid_norte`,
`goblintrader_madrid_sur`, `itaca`,
`jupiter_central`, `la_guarida_juegos`, `madakiba`, `metropolis_center`,
`micelion_games`, `mundicomics`, `oz_juegos`, `padis`, `panda_games`,
`replay_boardgame_cafe`, `the_big_bang_games`, `three_stones_games`, `topdeck`,
`wargen_wargames` (public Google Calendar via `shared/ics.py`).

Removed in 2026-11 because the official locators fully cover them, and their
own sites had changed or blocked scraping: `collectorage`, `kamikaze_freak_shop`,
`metamorfo`, `next_dice`. Their registry records stay for the locator
events. `genexcomics` was retired as a duplicate of Generacion X - Elfo.

### 1a′. Official locator sources

`scrapers/locator_*.py` are multi-store sources. They read the official
publisher event locators for every store within `CITY["radius_km"]` (50 km),
for the next `WINDOW_DAYS` (60) days:

| Module | Locator | Games |
|---|---|---|
| `locator_wizards` | Wizards Store & Event Locator (GraphQL `searchEvents`) | Magic |
| `locator_bandai_onepiece`, `locator_bandai_digimon` | Bandai TCG+ (`api/user/event/list`); client in `shared/bandai_locator.py` | One Piece; Digimon. There is one module per game, so health and protection work per game |
| `locator_ravensburger` | Ravensburger Play Hub (hydraproxy `/v2/events/`) | Lorcana |
| `locator_gem` | GEM (`gem.fabtcg.com/api/v1/locator/events/`) | Flesh and Blood |
| `locator_pokemon` | Play! Pokémon Event Locator (OutSystems screen action `DataActionGetEventList` on `events.pokemon.com/EventLocator`) | Pokémon (TCG products only) |
| `locator_melee` | Melee.gg organizer pages (`/Organization/GetScheduledTournaments/{id}`) for every `melee:<id>` alias in the store registry | Cyberpunk TCG, Star Wars: Unlimited (and any listed game a store runs there) |

Melee is the official platform for Cyberpunk TCG events. Its global search
has no location filter and stops at 250 results (a few hours worldwide), so
`locator_melee` reads the organizers we mapped to Madrid stores instead.
`python -m tools.find_melee_organizers` proposes organizers for stores without
a `melee:` alias (strong match: contact email on the store's website domain);
add the confirmed ids to the store's `aliases.external`. Times are UTC.
The official store list at cyberpunktcg.com/stores feeds store discovery
(`discoverers/cyberpunk_locator.py`); it has no events.

GEM ignores its lat/lon parameters, so the adapter searches by city name and
filters by distance itself. GEM also stamps local wall-clock times with a
bogus `+13:00` offset, which the adapter drops.

The Pokémon locator is an OutSystems app. Each run first reads, from the
page's own public assets, the `moduleVersion` (`moduleservices/moduleinfo`
→ `versionToken`), the action's `apiVersion` (in
`EventLocator.MainFlow.Home.mvc.js`) and the anonymous CSRF token (in
`OutSystems.js`, sent as `X-CSRFToken`). The hashes change on every deploy,
so they are never hard-coded. `Start_date` always ends in `Z`, but only play
sessions (weekly "League" nights) are UTC; tournaments carry the venue's
wall-clock time, as in the page's own `FormatDateByLocale`. Play sessions have
no name, so they are titled "Liga Pokémon". The locator has no capacity,
attendance or per-event page, so `source_url` is the locator. The site sits
behind Imperva: an interstitial or 403 raises `BlockedError`, and the source
is then protected like any failed locator.

`shared/locator_common.py` holds the parts they share:
- the search area and time window;
- an HTTP session with an honest User-Agent, timeouts and retries;
- `make_event()`;
- `StoreResolver`, which maps locator store names to canonical store names.

**Store registry (`data/stores.json`, `shared/store_registry.py`).** There is
one record per physical store. Its `id` is a stable slug that is never changed
once assigned. Each record also holds a display `name`, `city`, `address`,
`lat`/`lng`, `website` and `notes`, plus `aliases`:
- `names`: other spellings of the store;
- `external`: upstream ids such as `wizards:8420` or `bandai:354`.

A top-level `ignore` lists names or ids to drop. The aggregator writes
`public/stores.json` from the registry, keyed by display name, and the frontend
loads it into `STORE_META`.

`StoreResolver` reads the registry only. It tries, in order:
1. an external-id alias;
2. a name alias;
3. a confident name/address match. Both 2 and 3 are rejected when the
   postcodes differ, because chain branches are separate stores;
4. the locator's own name, suffixed with its postcode if it would collide
   with a known store.

Scraper subprocesses never write the registry. Locator events carry a
`store_hint` (external id, raw name, address, coordinates). The aggregator,
a single process, then:
- absorbs each hint through `StoreRegistry.learn()`, which adds new stores
  (`origin: "auto"`) and new aliases, and fills in missing coordinates;
- sets `store_id` on every event;
- writes both files.

An alias is never added if that spelling already names a different store.
`learn()` looks a store up by exact name, then by normalized spelling, so two
spellings of a new store in one run become a single record. It only records
an alias (external id or spelling) when the hint's postcode confirms the
record's. A match made without that evidence is used for the current run but
not persisted. To undo a wrong alias, delete it from `aliases` in
`data/stores.json`. `StoreRegistry.load()` raises if the file is missing.
A test validates the committed registry: unique ids, names and external ids.

How the aggregator treats locator events:
- **Source tag.** Every fresh event gets `source` (its scraper module).
  Legacy events without one are backfilled from the store's scraper.
- **Store-scraper priority.** A locator event is dropped when it duplicates
  one of the same store's own scraper events: same game (or an unknown game),
  same day, starting within `DUPLICATE_WINDOW_MIN` (90) minutes. Matching is
  one-to-one and nearest-first, so one scraper event absorbs at most one
  locator event. Existing upcoming scraper events count too, once, so a
  scraper that failed today does not let duplicates in.
- **Name variants.** Spellings that normalize to the same name
  ("MEDUSA COMICS" / "Medusa Cómics") but resolved to different stores are
  only reported. A warning asks for an alias in `data/stores.json`. Nothing is
  renamed automatically, so names never flip between runs.
- **Event key.** Namespaced ids (`wizards:123`) are keyed as `id:<sid>`,
  without the store name. A store rename therefore never re-keys a locator
  event.
- **Game.** Locator events keep the locator's game. Title heuristics in
  `_normalize_game` are skipped for them.
- **Protection.** It works per source. A failed or `suspect_zero` locator
  protects only its own events.

Every event also carries `store_address` from its own venue, and the frontend
`storeAddress(e)` prefers it over the registry address.

### 1b. Shared utilities

`shared/` is for reusable backend/scraper utilities only. Files in `shared/`
must not expose `scrape()`, must not perform network requests on import, and
must not have side effects at import time.

`shared/scraper_keywords.py` provides:
- `GAME_KEYWORDS` — `(keyword, canonical_name)` tuples, longest-first
- `FORMAT_KEYWORDS` — `(keyword, unified_format, format_official_or_None)`
  3-tuples, longest-first. `unified_format` lands in `event.format` (small
  game-agnostic vocabulary); `format_official` (when not None) preserves the
  original per-game name (e.g. "Premier", "Armory", "Liga Pokémon") for
  display in the detail panel.
- `DEFAULT_FORMAT_BY_GAME` — map game → fallback format (Standard for all
  non-MTG games). Applied only when no keyword matched.
- `extract_format_for_event(title, description, category, game)` —
  game-aware extractor with title-priority Prerelease detection; returns
  `(unified_format, format_official)`.
- `extract_best_of(text)` — returns `1`, `3`, or `None` from explicit
  BO1/BO3 mentions.
- `extract_game_from_keywords(text, keywords)` — returns canonical game or None
- `extract_format_from_keywords(text, keywords)` — legacy helper, returns
  unified canonical format or None. Kept for backward compat; new code uses
  `extract_format_for_event` to also get `format_official`.

Scrapers import these and may extend them with store-specific entries.

`shared/store_matching.py` provides store-name and address normalization plus
fuzzy matching. Used by discovery tooling (`tools/discover_stores.py`,
`tools/audit_store_event_pages.py`), not by scrapers.

`shared/carde_store_locator.py` provides a reusable helper for official
Carde/Hydra-style store locators, currently used by the Lorcana and Riftbound
discoverers.

All per-store scrapers use the shared `GAME_KEYWORDS` / `FORMAT_KEYWORDS`
helpers from `shared/scraper_keywords.py`. `arte9` and `metropolis_center`
derive the game from API category fields first.

`shared/wordpress_events.py` fetches The Events Calendar / Modern Events
Calendar feeds (used by `the_big_bang_games`).

`shared/goblintrader.py` parses the server-rendered event list on a
goblintrader.es shop page (`.gts-store-events`). Used by
`goblintrader_madrid_sur`, `goblintrader_madrid_norte` and
`goblintrader_fuenlabrada`; entries without a start time are skipped; raises if the block
is missing.

`shared/ics.py` reads public iCalendar feeds. It handles RRULE expansion,
EXDATE, RECURRENCE-ID overrides and cancelled occurrences, and skips all-day
events.

### 1c. Scraper stats and anomaly detection

`aggregator.py` writes `public/events_stats.json` after every run with a
per-store breakdown. Each store entry contains:

- `raw_this_run` — events fetched by the scraper this run, before validation drops
- `previous_raw` — the same counter from the immediately preceding run's stats file
- `dropped_this_run`, `drop_reasons` — validation drop counts per failure category
- `anomaly` — `"suspect_zero"`, `"sharp_drop"`, or `null`

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

**Suspect zero** (`find_suspect_zero_stores`): a scraper that finishes
without raising but yields 0 valid events, for a store that still has
upcoming active events on disk, is flagged `"suspect_zero"` and treated like a
failed scraper: its existing events are left untouched instead of counting
towards deletion. Unlike `sharp_drop`, it keeps firing on every run until the
scraper returns events again. Scrapers should still raise on fetch errors
rather than return `[]`, so the failure reason shows up in `error`.

**Write safety**: the run aborts (exit 1) without writing in any of these
cases:
- `data/events.json` exists but cannot be parsed;
- the merged total is below `COUNT_DROP_THRESHOLD` (70%) of the existing
  total;
- upcoming active events fall below `UPCOMING_DROP_THRESHOLD` (50%) of the
  previous run's, once there are at least 50. The history-inclusive total
  alone would hide the loss of every future event.

**Retired stores**: events of stores listed in `data/retired_stores.json`
are dropped on load, for example a store entry that turned out to duplicate
another one.

**Run log**: every run appends one line per source to
`data/source_runs.jsonl`: raw, valid, dropped, duration, error and anomaly.
It is the history behind source health, and the future `source_runs` table. Both JSON files are written atomically (temp file + `os.replace`).
Import errors in a scraper module are caught per scraper and never abort the
run.

### 1d. Event identity and merge

Events are merged across daily runs using a stable key:

- `{store}|id:{source_event_id}` — if the scraper provides a stable ID
- `{store}|{title}|{datetime_start}|{location}` — fallback

`merge_events()` upserts fresh events onto the persisted list. Past events not
seen this run are kept with `is_active=False` (preserved as history). **Future
events** absent for `MISSING_RUNS_BEFORE_DELETE = 3` consecutive runs are
hard-deleted (treated as cancelled). Every event carries lifecycle fields:
`first_seen_at`, `last_seen_at`, `is_active`.

### 1e. Execution

```bash
python -m scrapers.<name>    # run a single scraper in isolation
python aggregator.py          # full pipeline (all scrapers + merge + stats)
```

`aggregator.py` runs every scraper in its own subprocess through
`python -m tools.run_scraper <module> <out.json>`. Up to `SCRAPER_WORKERS`
(default 6) run in parallel, each with a `SCRAPER_TIMEOUT_S` (default 300 s)
wall-clock limit. Both are overridable through environment variables. A
timeout, a hard crash or a missing result counts as a failed scraper, so that
store's events are preserved. For a scraper that timed out or crashed, the
store name is read from the `STORE = "..."` literal in its source file.

`build_stats` also flags `"stale"`: the scraper returned 0 events, it did not
error, and the newest `last_seen_at` among the events from the store's own
scraper is `STALE_AFTER_DAYS` (14) or more days old. Only stores with their
own scraper can be stale. Locator coverage of a store does not hide a dead
scraper. No events are protected by this flag (none are left upcoming). It
only raises an alert.

---

## 1f. Store expansion workflow (audit → targets → scrapers)

The pipeline for finding and onboarding new stores follows these steps:

1. **Store discovery** — `tools/discover_stores.py` (and helpers in `discoverers/`)
   finds candidate stores from the Wizards locator and other sources, writing
   `data/candidate_stores.json`.

   Each discoverer module exposes `discover() -> list[dict]`. The minimum
   candidate shape is:

   ```json
   {
     "name": "Store name",
     "address": "Street, city, country",
     "source": "source_name",
     "games": ["Magic: The Gathering"],
     "website": "https://example.com",
     "external_id": "source-specific-id"
   }
   ```

   `tools/discover_stores.py` merges duplicate records across discoverers and
   preserves provenance. The canonical `candidate_stores.json` record keeps
   legacy compatibility fields (`source`, `website`, `external_id`, `status`)
   and also includes:

   - `sources` — all discoverers that found the store.
   - `external_ids` — source name → source-specific ID.
   - `evidence` — source records explaining why the store is a candidate
     (official locator hit, event locator hit, etc.).
   - `games` — canonical games inferred from all sources.
   - `location_precision` — `street` by default; discoverers that only expose
     city-level location data should return `city`.

   New discoverers should return canonical game names where possible. Common
   aliases such as `MTG`, `SWU`, and `FAB` are normalized by
   `tools/discover_stores.py`. Cross-source merge is intentionally limited to
   exact normalized store-name matches; fuzzy candidate review still happens
   through `possible_duplicate`.

   Manual duplicate-review decisions are stored in
   `data/store_discovery_overrides.json` and applied by
   `tools/discover_stores.py` after discovery merge. Use this file for durable
   decisions such as confirmed new stores, confirmed existing-store matches, or
   records that need more information; use `backlog` for deferred candidates
   that should remain in discovery data but stay out of immediate onboarding.
   Do not hand-edit generated statuses only in `data/candidate_stores.json`.

   Current discoverers:

   - `discoverers/lorcana_locator.py` — official Disney Lorcana /
     Ravensburger Play Store Locator, scoped to stores within 25 miles of
     Madrid city centre. It returns street-level addresses from the official
     API.
   - `discoverers/riftbound_locator.py` — official Riftbound Play Network
     locator, scoped to stores within 25 miles of Madrid city centre. It uses
     the same Carde/Hydra helper as the Lorcana locator.
   - `discoverers/one_piece_events.py` — official Bandai TCG+ One Piece event
     locator, scoped to upcoming events within 50 km of Madrid city centre.
     This is event-based: stores are discovered only when they have future
     One Piece events in Bandai TCG+.
   - `discoverers/yugioh_locator.py` — official Konami Europe Yu-Gi-Oh!
     Official Tournament Store locator, scoped to stores within 50 km of
     Madrid city centre.
   - `discoverers/wizards_locator.py` — Wizards Store Locator, scoped to a
     50 km radius around Madrid city centre.
   - `discoverers/swu_calendar.py` — official Star Wars: Unlimited Organized
     Play Calendar, scoped to Spain/Madrid rows. The SWU calendar exposes
     city-level location data rather than street addresses, so its candidates
     are marked with `location_precision: "city"` and mostly serve as
     provenance for matching known stores and review.
   - `discoverers/fab_locator.py` — Flesh and Blood / Carde Play Network
     store locator, scoped to game-approved establishments within 50 km of
     Madrid city centre. Earlier direct `fabtcg.com` / GEM probing returned
     403 or authenticated/admin-style endpoints; the implemented source uses
     the public Carde Play API with the Flesh and Blood `game-id` header.

2. **Event-page audit** — `tools/audit_store_event_pages.py` reads
   `data/candidate_stores.json`, fetches each candidate's website, detects event
   pages and calendar presence, and writes `data/store_event_audit.json`. It uses
   `shared.scraper_keywords` for game/format signal detection.

3. **Target selection** — `tools/build_scraper_targets.py` reads
   `data/store_event_audit.json` and classifies stores into four buckets, writing
   `data/scraper_targets.json`:
   - `scrape_now` — validated event page with clear game content; build a
     scraper immediately.
   - `possible` — event intent detected but no clean event page found; needs
     manual investigation.
   - `manual_review` — social-only presence (Instagram/Facebook); not a
     straightforward scraper target.
   - `not_ready` — reachable website but no event signals detected.

4. **Scraper development** — for each store in `scrape_now`, create
   `scrapers/<name>.py` and add the store to `data/stores.json`.

`data/scraper_targets.json` is the canonical queue of stores awaiting scraper
development. It is updated by re-running steps 2–3 after sites change.
**Do not hand-edit it.**

---

## 1g. Deployment

Cloudflare Pages serves `public/` directly as static files at
<https://madrid-tcg.pages.dev/>. There is no build step, no bundler, and no
environment variables. A push to `main` triggers an automatic redeploy.

`aggregator.py` runs in GitHub Actions on a cron schedule defined in
`.github/workflows/update.yml`. It is scheduled daily at 04:37 UTC (05:37 Madrid
winter, 06:37 summer). The minute is off the hour on purpose: GitHub delays
top-of-hour cron jobs. A `concurrency` group stops runs from overlapping.

The workflow runs these steps:
1. `python -m unittest discover -s tests`.
2. `python aggregator.py`.
3. Commit everything the pipeline wrote under `data/` and `public/`: history,
   registry, run log, stats and the API
   back to `main`. The push triggers a Cloudflare Pages redeploy.
4. `python -m tools.sync_health_issues`. This opens or updates one GitHub issue
   (label `scraper-health`, title `[health] <store>`) per store that failed,
   is `suspect_zero` or `stale`, or has never produced an event. It closes the
   issue when the store recovers, and reopens the same issue if the store
   breaks again. The step runs with `if: always()`. If an earlier step failed,
   it only opens or updates `[health] pipeline`, which closes after the next
   successful run.

The workflow also supports `workflow_dispatch` for manual runs.

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

- **`STORE_META`** — an empty object in `config.js`. `init()` fills it from
  `public/stores.json`, which the pipeline generates from the store registry.
  The panel's store card and the calendar `location` field use it. Edit
  stores in `data/stores.json`, not here.
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
fetch('api/v1/madrid/events/upcoming.json')  // + stores.json, index.json
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
        │  showFavoritesOnly                              │
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

- `init()` is the only side-effect-on-load function. It loads presets,
  favorites, default-preset ID, and onboarding state from localStorage; registers a scroll
  listener (for Back-to-top), an Escape-key listener (closes panels/facets);
  starts a 60-second `setInterval` that refreshes `nowMadrid`; fetches
  `events.json`; then either applies filters from URL params or applies the
  default preset (if one is set); calls `cleanupFilters()`; and registers a
  `$watch` on `filters.search`. If no default preset and no URL params are
  present on a fresh visit, the first-run onboarding modal opens.
- **URL params take precedence over localStorage.** `applyFiltersFromUrl()` is
  called immediately after events load and overwrites filter state from URL
  params (`game`, `store`, `format`, `event`). localStorage is only consulted
  for `viewMode` and saved presets — never for filter state.
- `cleanupFilters()` prunes selected facet values that no longer exist in the
  dataset. For `game` and `format` it uses `availableOptions()` (week-scoped);
  for `store` it uses `allStores` (full dataset, week-independent). This means
  a selected store is **never** removed by week navigation — it is only pruned
  if the store name disappears from `events.json` entirely.
- `resetFilters()` clears the search/facet filters and also resets all
  `segmentFilter` chips to enabled. Segment filters are part of the user-facing
  filter state, not a separate view preference.
- The frontend never POSTs anything. It reads only the static API:
  `upcoming.json`, `stores.json` and `index.json` at load, then one week file
  when the user navigates outside the upcoming range (`loadWeek`). Share links
  carry `&w=<ISO week>`, so the right week file can be fetched for a deep
  link.

### Store submissions ("Is this your store?")

The flow has five steps:
1. **Form.** `public/submit/` is a bilingual form for an event (title, game,
   date, time, optional weekly-until, link) or a store-info note. The
   contact is required and is never published.
2. **Function.** It posts to the Cloudflare Pages Function
   `functions/api/submit.js`. The function guards against spam with a
   honeypot field, a 4-second minimum fill time and strict validation. It
   then creates a GitHub issue labelled `submission`, whose body holds the
   data in a ```json block. Nothing is stored in Cloudflare.
3. **Configuration.** Set `GITHUB_TOKEN` in Cloudflare Pages → Settings →
   Environment variables: a fine-grained token for this repo only, with
   Issues read & write. Without it the function answers 503 and the form
   asks the visitor to try later.
4. **Review.** A maintainer adds the label `approved`.
   `.github/workflows/submissions.yml` then runs `tools/apply_submission.py`,
   which validates the data again and appends it to `data/manual_events.json`
   (idempotent per issue, unsafe links dropped, contact not copied). The
   workflow commits the file and closes the issue. Store-info requests are
   handled by hand.
5. **Publication.** `scrapers/manual_submissions.py` publishes those events
   and expands weekly ones within the window. It is a store source, so it
   outranks locator copies. `EMPTY_IS_NORMAL = True`, so having no
   submissions is not a health alert.

### PWA and offline

- **Files:** `public/manifest.webmanifest`, `public/icons/` (SVG source plus
  PNGs rendered from it), and `public/sw.js`, registered from `init()` on
  https or localhost.
- **Service worker strategy:**
  - **Same-origin GET:** network first. Successful responses are cached; the
    cache answers only when the network fails. Pages are cached by path
    without the query string, so shared `?event=` links do not grow the
    cache.
  - **jsDelivr (Alpine, Leaflet, Leaflet.markercluster; version-pinned):**
    cache first. OpenStreetMap tiles are not intercepted (browser HTTP cache).
  - **Precache:** the app shell plus `upcoming.json`, `stores.json` and
    `index.json`.
- **Versioning:** bump `VERSION` in `sw.js` when its logic changes; older
  `tcg-*` caches are deleted on activate.
- **Offline banner:** `offline` tracks `navigator.onLine`, and the banner tells
  the user they are seeing saved data.
- **Network fallback:** the cache also answers on a 5xx, or when a cached copy
  exists and the network takes more than 4 s. Only successful responses are
  cached. Alpine is loaded with SRI and `crossorigin`, so the worker never
  stores an opaque or error response from the CDN.
- **Absolute URLs:** the shell uses absolute URLs (`/app.js`,
  `/api/v1/...`), so offline it also works as the fallback for any page that
  was never visited.
- **Kill switch:** deleting `sw.js` does not remove an installed worker, because
  browsers keep the old one when the update check gets a 404. To retire it,
  deploy `/sw.js` with this content and remove the `register()` call from
  `app.js`:
  ```js
  self.addEventListener('install', () => self.skipWaiting());
  self.addEventListener('activate', (e) => e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith('tcg-')) await caches.delete(k);
    await self.registration.unregister();
    for (const c of await self.clients.matchAll()) c.navigate(c.url);
  })()));
  ```

### Near me

`toggleNearMe()` asks for `navigator.geolocation`. `nearMe` (`{lat, lng}`)
lives in memory only and is never persisted or sent anywhere. `radiusKm`
(2/5/10/25) filters in `eventMatches` through `storeDistanceKm(store)`, a
haversine distance to `STORE_META[store].lat/lng`. Stores without
coordinates are excluded while the filter is on. The cards and the vertical
view show `storeDistanceLabel`. `resetFilters()` clears the filter.

### Cities

The app at the site root serves every city in `/api/v1/cities.json`.
- **City choice:** `?city=<id>` comes first, then `tcg-city-v1`, then
  `madrid`. An inline script in `index.html` applies the same rule to
  preload that city's `upcoming.json` and `stores.json`, and passes the
  result to `startCity()` in `app.js`.
- **Unknown cities:** a city whose `upcoming.json` returns 404 falls back to
  Madrid.
- **URLs:** the default city has no `?city=`, so Madrid links are unchanged.
- **Switcher:** it appears in the header only when `cities.json` lists more
  than one city.
- **What switching does:** `setCity()`
  - clears the store filter, Near me and the map selection, keeping game and
    format;
  - swaps the favorites key;
  - recentres the map;
  - reloads `STORE_META`, the week index and upcoming events through
    `loadCityData()`.
- **Per-city favorites:** `cleanupFavorites()` drops keys missing from the
  loaded weeks, so one shared list would wipe the other city's stars.
- **Timezone:** every city is assumed to be in Europe/Madrid. The pipeline
  refuses other timezones (`shared/cities.py`).

### Map view

The third view (`viewMode === 'map'`) answers "where can I go". It shows this
week's filtered events that have not started yet, one pin per store with its
event count. A pin takes a game's colour when that game is more than half of
the store's events, and is purple otherwise. Nearby pins cluster, and a
cluster shows the total of its events.

- **Day chips:** `mapDay` (null means the whole week) narrows the week.
  Week navigation resets it.
- **Selecting a store:** tapping a pin or a list row sets `mapStore`. The
  sheet under the map then lists that store's events, and tapping one opens
  the usual detail panel. Without a selection the list shows every store,
  busiest first, or nearest first while Near me is on.
- **Loading:** Leaflet 1.9.4 and Leaflet.markercluster 1.5.3 come from
  jsDelivr with SRI, injected by `loadLeaflet()` the first time the map
  opens. The calendar views never load them.
  Each asset has its own promise, and a failed asset is forgotten, so a
  retry (the Retry button, or coming back online) reloads only what failed.
  `window.L` alone does not mean ready: it exists while the plugin is still
  loading.
- **Tiles:** OpenStreetMap standard tiles (`MAP_TILES` in `config.js`).
  Swap the URL there if traffic outgrows the OSM tile usage policy.
- **Reactivity:** `drawMap()` runs from `x-effect` and reads every input
  synchronously. Leaflet objects live in the module-level `MAP`, never in
  Alpine state. The view is refitted only when `mapFitKey` changes (week,
  day, filters, location, plus the loaded event count so a week fetched after
  navigation refits), so the minute tick or a selection never moves
  the map. Redraws that would change nothing are skipped, because rebuilding
  the pins cancels a running cluster zoom.
- **Gotchas:**
  - Read `x-for` row fields before changing state: after `mapStore`
    changes, `s` can point at another row's data.
  - `STORE_META` is not reactive, so `init()` fills it before assigning
    `events`.
  - A smooth page scroll cancels Leaflet's zoom animation, so list picks
    scroll instantly.

### localStorage usage

| Key | Type | Purpose |
| --- | --- | --- |
| `tcg-view-v2` | `'horizontal'` \| `'vertical'` \| `'map'` | Persisted view mode |
| `tcg-presets-v1` | JSON array | Saved filter presets |
| `tcg-default-preset-v1` | JSON `{id}` | ID of the preset auto-applied on fresh visits |
| `tcg-onboarding-v1` | JSON `{completed}` | Whether first-run onboarding has been completed |
| `tcg-favorites-v1` | JSON array of strings | Starred event keys (Madrid; other cities use `tcg-favorites-v1:<city>`, and the same suffix applies to `tcg-favorite-weeks-v1`) |
| `tcg-city-v1` | city id | Last city chosen; `?city=` overrides it and is then remembered |

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

### Filter undo history

`filterHistory` is a stack (max 5 entries) of `{filters, segmentFilter}`
snapshots, pushed on every `toggleFilter()` call. `undoFilterAction()` pops the
most recent snapshot and restores it, then re-syncs to URL. The snapshot stack
is cleared on `resetFilters()` and on preset application.

### Per-facet search

`filterSearch` is a `{game, store, format}` map of search strings, used to
narrow long facet option lists in their dropdowns. It is purely a UI aid and
does not affect `eventMatches` — only which options are visible in the facet
panels.

### Default preset and first-run onboarding

`defaultPresetId` (loaded from `tcg-default-preset-v1`) marks one saved preset
as default. On fresh visits with no URL filter params, `applyDefaultPreset()`
applies its filters automatically and sets `defaultPresetActive = true`, which
enables the "smart-tap" focus mode for chip interactions. The onboarding modal
(`onboardingOpen`) is shown on the very first visit when no default preset and
no URL params exist, collecting initial game/store selections; completion is
recorded under `tcg-onboarding-v1`.

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

Sixteen game classes — `.game-mtg`, `.game-starwars`, `.game-riftbound`,
`.game-onepiece`, `.game-pokemon`, `.game-lorcana`, `.game-digimon`,
`.game-yugioh`, `.game-fab`, `.game-finalfantasy`, `.game-weiss`,
`.game-naruto`, `.game-cyberpunk`, `.game-gundam`, `.game-mlp`,
`.game-palworld` — plus the fallback `.game-unknown`. Each sets `--card-tint`
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

`eventMatches`, `filteredEvents`, `availableOptions`, `allStores`,
`facetOptionCount`, `cleanupFilters`, and the `filters` shape are interlocked.
A change to one must keep all of them consistent. In particular:

- `eventMatches(e, opts)` is called from three places (filtering display,
  computing facet options, computing facet counts) with different `opts`
  combinations (`ignore`, `includeSegment`, `includeWeek`). Don't simplify
  the signature.
- **`storeOptions` uses `allStores`, not `availableOptions('store')`.** This
  is intentional — the store list must show every store in the dataset
  regardless of the current week. Do not revert this to `availableOptions`.
- **`cleanupFilters()` treats `store` differently from `game` / `format`.**
  For `store` it uses `allStores` as the allowed set; for the other fields it
  uses `availableOptions()` (week-scoped). This ensures a selected store is
  never silently dropped when the user navigates to a week with no events for
  that store. `facetOptionCount('store', ...)` still returns the week-scoped
  count (zero is allowed and expected for stores with no current-week events).

### Don't change the event schema

The frontend reads these fields from each event:

- `store`, `game`, `format`, `title`
- `format_official` (nullable string) — original per-game name (Premier,
  Armory, Liga Pokémon, …), shown italic in detail panel under the chips.
  Does NOT participate in filtering.
- `best_of` (nullable: 1, 3, or null) — BO1/BO3 indicator extracted from
  title. Rendered as a small grey chip after the format chip.
- `datetime_start` (must carry an explicit Madrid offset),
  `datetime_end` (nullable; not currently displayed)
- `language` (currently unused by the frontend, but present on every event)
- `source_url`
- `source` — the scraper module that produced the event (`scrapers.itaca`,
  `scrapers.locator_wizards`). Set by the aggregator, and used for
  protection and deduplication.
- `source_event_id` (nullable) — a stable upstream id. When present, it makes
  the event key `{store}|id:{source_event_id}`.
- `store_address` (nullable) — set by locator sources. It is the frontend's
  fallback when the store has no `STORE_META` address.
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

### Every scraper store belongs in the store registry

The aggregator adds unknown stores to `data/stores.json` automatically
(`origin: "auto"`), but they have no address until a locator or a human
fills one in. When shipping a scraper, add the store's registry record
(`name` equal to the scraper's `STORE`, plus `address`).

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
4. Add an entry to the store registry `data/stores.json` (`name`, `address`; the `id` is a stable slug). This populates the store card in the Event Detail Panel
   and the `location` field in calendar links.
5. Run `python aggregator.py` once and verify the new store appears under
   `by_store` in `public/events_stats.json`.

### Using the audit workflow to find candidate stores

1. Ensure `data/candidate_stores.json` is up to date (re-run
   `tools/discover_stores.py` if it is stale).
2. Run `python tools/audit_store_event_pages.py` → writes `data/store_event_audit.json`.
3. Run `python tools/build_scraper_targets.py` → writes `data/scraper_targets.json`.
4. Review `data/scraper_targets.json` `"scrape_now"` entries — these are validated
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

The current facets (game, store, format) follow a similar but not identical
pattern — `store` has special handling (see `allStores` / `cleanupFilters`
above). A new facet that should behave like `game` / `format` (week-scoped
options, pruned on navigation) means changing all of:

- `filters` (initial state in `app()`)
- `eventMatches` (matching loop)
- `valueFor`, `availableOptions`, `facetOptionCount`, `cleanupFilters`
- A new markup block mirroring the existing Game / Format panels in the
  `.filters` container, plus a getter for its options
- The two `data-filter` chips inside `cellCardsHtml` (so it shows on cards)
  if you want it on the per-card chip row

If the new facet should behave like `store` (always show all values, never
pruned on week change), add a dedicated `all<Facet>` getter and a special-case
branch in `cleanupFilters` — do not repurpose `allStores`.

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
- [ ] Record in `data/stores.json` with `name` = `STORE` and `address`
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
