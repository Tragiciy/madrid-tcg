# TCG Events: Product Plan

## Purpose

This is a city-level calendar of trading card game (TCG) events. It started
with Madrid and aims to cover many cities and countries.

It answers one question for players: **"What can I play this week (tonight,
this weekend) near me?"** The answer covers every game and every store,
including events that never appear in the official publisher locators.

Value hypothesis, the things official locators don't offer:
1. **All games in one place.** Each locator covers one publisher and one game.
2. **Off-locator events.** Many stores post only on their own sites or on
   Instagram and WhatsApp.
3. **Time and place first.** Players can ask "tonight near me" instead of
   searching game by game.
4. **Calendar subscription.** ICS feeds work per filter.

---

## Decisions (2026-11-15 review)

| Topic | Decision |
|---|---|
| Ambition | Multi-city and multinational. This is unproven, so a week-12 gate (below) decides. |
| Horizon | Deeper Madrid coverage **and** a data-chosen pilot city. |
| Constraints | The old rules (no build, no backend, four-file frontend) are lifted. |
| Order | Reliability first, then the new stack, then distribution, then the pilot city. |
| Data | Official locators are the base layer (Wizards, Bandai TCG+, Ravensburger, GEM, plus public Google Calendar ICS). About 10 own-site scrapers fill the gaps. Store self-submission covers the rest. |
| Off-locator gap | A "forward us your poster" bot (LLM vision, one-tap human confirm) plus a "Is this your store?" form. |
| Stack | Next.js + Postgres/PostGIS (Neon or Supabase) on Vercel with ISR. The Python pipeline stays in GitHub Actions and writes to Postgres. |
| Accounts | None. Favorites stay in `localStorage`, and ICS feeds handle subscriptions. |
| Languages | English is the code and translation base. Public city pages are indexed in the city's language (`/madrid` in Spanish, `/en/madrid`), with `hreflang`. |
| Monetization | None for now. Store "claim" flows are built later as a funnel. |
| Validation | Passive only: privacy analytics, Search Console, "vote for your city", in-card feedback, ICS subscription counts. |
| Brand/domain | Deferred until the gate passes. Then a neutral name, an own domain, and `/city` paths. |
| Capacity | Solo plus AI agents, 10–20 h/week. |

---

## Current state (verified 2026-11-15)

- **25 scrapers. 14 return 0 events, and only 1 of those is flagged.**
  Scrapers swallow request errors and return `[]`. The aggregator treats that
  as "no events" and hard-deletes the store's future events after 3 runs.
- `public/events.json` is 5,035 events (1,077 active) and **2.8 MB**. The
  frontend fetches it with `no-store` on every visit (190 KB gzip). 79% of it
  is past events.
- Official locators list **~730 Madrid events in 14 days** against our 253.
  41 of 43 surveyed stores appear in at least one locator.
- GenexComics and Generacion X - Elfo are the same shop, and all 62 GenexComics
  events are duplicates.
- The frontend `eventKey` (`source_url + datetime_start`) collides for 419
  events, which breaks favorites and `?event=` links.
- The reschedule logic in `merge_events` can re-key past events.
- Week and "today" in the frontend use the browser timezone, not Madrid's.
- CI never runs tests and sends no alerts. The cron is set to 06:00 UTC, but
  runs actually land at 11:00–15:00 UTC.

---

## Roadmap

### Phase 0: Reliability and coverage on the current site (weeks 1–2)

1. Make scrapers fail loudly instead of returning `[]` on errors. Treat
   `suspect_zero` (0 events while N are active in history) as a failed store
   and do not delete its events.
2. Write JSON atomically. Refuse to write when existing data fails to parse.
   Block the commit when the event count drops more than 30%.
3. Isolate each scraper (timeout, import inside `try`). Auto-open or update one
   GitHub issue per broken store. Add CI `concurrency` and a pytest step.
4. Bug fixes:
   - reschedule matching only against future events;
   - frontend uses the backend event key;
   - naive datetimes are rejected at validation;
   - merge GenexComics into Generacion X;
   - drop `no-store` from the `events.json` fetch.
5. Locator adapters: Wizards `searchEvents`, Bandai TCG+ (One Piece, Digimon),
   Ravensburger `/v2/events/`, GEM, plus the public Google Calendar ICS feeds
   (Wargen, Padis). Deduplicate by store, time and game. Delete the scrapers the
   locators fully cover.

### Phase 1: New stack (weeks 3–6)

6. Next.js + Postgres/PostGIS on Vercel. Tables:
   - `stores`: `city`, `lat/lng`, `platform`, sources;
   - `events`: stable `source_event_id`;
   - `sources`: health time series.
7. The pipeline writes to Postgres. Full history lives there.
8. ISR pages: `/madrid`, `/madrid/hoy`, `/madrid/fin-de-semana`,
   `/madrid/[game]`, `/madrid/[store]`, `/event/[id]`. Each has JSON-LD `Event`,
   Open Graph and `hreflang`.
9. Interactive calendar as a client component: filters, `localStorage`
   favorites, Today/Weekend/Near-me chips, distance sort.
10. ICS feeds per filter, with a subscription counter. Cookieless analytics
    and Search Console.
11. Cut over: `madrid-tcg.pages.dev` returns 301 to the new site.

### Phase 2: Distribution (weeks 6–9)

| Channel | What | Metric |
|---|---|---|
| Google Events and SEO | JSON-LD on every event, sitemap, ~30 long-tail pages | Search Console impressions and clicks |
| WhatsApp/Telegram sharing | Dynamic OG images (game, date, store), a "send to friends" button | `?ref=share` visits |
| Digest bot | Weekly "This weekend in Madrid" auto-post to a Telegram channel and Bluesky | Subscribers, clicks |
| Stores as distributors | Embeddable "our events" widget, printable QR poster, one automated email | Widget installs, QR visits |
| Demand signals | "Vote for your city", "Event wrong?" button, "Is this your store?" form | Votes per city, submissions |
| Launch | One optional post (e.g. r/MagicTCG_ES) | Traffic spike, returning share |

### Phase 3: Pilot city and store-sourced events (weeks 9–12)

12. Pilot city chosen from locator density, search demand and city votes
    (likely Barcelona). With locators this is mostly configuration.
13. "Forward us your poster" bot (Telegram or email): LLM vision extraction,
    one-tap confirm, then publish.

### Week-12 gate

The target is **≥300 weekly unique visitors** and **≥1 inbound store contact**.
- **Passed:** brand and domain, more cities, then store-side monetization.
- **Missed:** stay a strong Madrid product with no further scale investment.

---

## Principles

- Data reliability before UI. A beautiful calendar with missing stores is
  worse than an ugly complete one.
- Prefer sources that scale to every city (official locators) over per-store
  code.
- Every source must fail loudly. Silent zeros are bugs.
- Validate demand with product signals before spending on brand or new cities.

---

## History

The pre-2026-11 plan (PWA, payload trimming, store metadata, discoverers) has
been folded into the roadmap above. It remains in git history.

### Completed milestones (through 2026-05)

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
| ✅ | Store discovery pipeline | `tools/discover_stores.py` + Wizards locator + audit + target classification |
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
| ✅ | Scraper health dashboard | Static `/health` page fetches `events_stats.json`, shows last run, per-store raw/active/dropped counts, anomaly badges, failed scraper alerts, and is linked from the footer |
