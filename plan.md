# Madrid TCG Events — Product Plan

## Project Purpose

City-level event aggregator for trading card games (TCGs).
Core user question: **"What can I play this week in my city?"**

---

## Current State (Baseline)

### Frontend
- Static site: Alpine.js, no build step
- Event cards: horizontal and vertical views
- Event Detail Panel
- Filters: game, store, format
- Saved filter presets via `localStorage`
- Calendar export: Google Calendar, iCalendar (.ics), Outlook

### Data Pipeline
- Scraper-based: `scrapers/*.py`
- Aggregator outputs `events.json`
- Shared keyword classification: `GAME_KEYWORDS`, `FORMAT_KEYWORDS`

---

## Priorities

### Priority 1 — Event Sharing (Now)

**Goal:** Let users share specific events.

#### 1.1 Copy Event Link
- Add "Copy link" button in Event Detail Panel
- URL format: `?event=<eventKey>`
- On page load: parse param → find event → open panel automatically

#### 1.2 Native Share
- Add "Share" button
- Use `navigator.share()` with clipboard fallback
- Share payload: title, game, format, date, time, store, address, source URL

---

### Priority 2 — Bookmarkable Filter URLs (Short-Term)

**Goal:** Shareable filtered views.

- URL format: `?game=mtg&format=commander&store=arte9`
- Sync params: `game`, `store`, `format`, optionally date/week
- Do NOT sync: presets, panel state, UI state
- On filter change → `history.replaceState`
- On load → apply filters from URL

---

### Priority 3 — Store Discovery Pipeline (Next 2–4 Weeks)

**Goal:** Automate finding new stores.

#### Directory Structure
```
discoverers/
  wizards_locator.py
  pokemon_locator.py
  swu_locator.py
discover_stores.py
```

#### Output Schema
```json
{
  "name": "Store Name",
  "address": "Madrid",
  "source": "wizards_locator",
  "games": ["MTG"],
  "website": "...",
  "matched_existing_store": null,
  "confidence": 0.82,
  "status": "candidate_new_store"
}
```

#### Matching Logic
- Normalize: lowercase, remove accents, strip punctuation, normalize addresses
- Same address → high confidence
- Similar name + same city → medium confidence
- Otherwise → candidate

#### Status Values
| Status | Meaning |
|---|---|
| `matched_existing_store` | Already in system |
| `candidate_new_store` | New, ready for review |
| `possible_duplicate` | Likely duplicate |
| `needs_manual_review` | Ambiguous |

#### Workflow
```
discover → review → create scraper → integrate
```

---

### Priority 4 — Scraper System Improvements (Mid-Term)

#### Standardization
- Move all keyword logic to `shared/`
- Normalize date parsing across all scrapers
- Reduce per-scraper custom logic

#### Health Monitoring

Output file: `reports/scraper_health.json`

```json
{
  "scraper": "itaca",
  "last_run": "...",
  "events_count": 12,
  "status": "ok"
}
```

#### Failure Detection
- 0 events returned → warning
- Sudden drop in event count → flag
- Repeated failures → manual review trigger

---

## Data Quality

### Deduplication
- Current key: `source_url + datetime_start`
- Future: fuzzy match on title + date + store

### Classification
- Expand `GAME_KEYWORDS` and `FORMAT_KEYWORDS`
- Add store-specific overrides where needed

---

## Geographic Expansion

| Phase | Scope | Changes |
|---|---|---|
| Phase 1 | Madrid — full coverage | Stable scrapers, all stores |
| Phase 2 | Spain — major cities | Add `city` field, city filter |
| Phase 3 | Multi-city UI | `/madrid`, `/barcelona` routes or city selector |

**Cities for Phase 2:** Madrid, Barcelona, Valencia, Sevilla, Málaga, Bilbao

---

## Backend Migration (Future)

### Trigger Conditions
Introduce backend when **any** of these are true:
- 20+ stores
- Multiple cities active
- Need for search / API
- User account features

### Target Stack
```
scrapers → raw_events → normalize → events → API → frontend
```

### Services
- PostgreSQL
- Backend API
- Scraper workers
- Scheduler / queue

### Data Model Tables
`stores`, `events`, `sources`, `scraper_runs`, `errors`, `candidate_stores`, `duplicates`, `reviews`

---

## User Features (Post-Stability)

Implement only after data is stable and coverage is solid:
- Favourite stores
- Personal event tracking
- Notifications (high value)
- User accounts (optional)

---

## What NOT To Do

- Do not migrate to backend prematurely
- Do not over-engineer the UI before data is solid
- Do not run fully automatic scraping without a human review step
- Do not expand globally before Spain coverage is stable

---

## Development Timeline

| Horizon | Tasks |
|---|---|
| **Now (1–2 weeks)** | Event sharing (link + native share), UX polish, stability checks |
| **Next (2–4 weeks)** | Store discovery pipeline, expand Madrid coverage |
| **Mid-term** | Spain expansion, scraper standardization and health monitoring |
| **Long-term** | Backend migration, multi-city UI, user features |

---

## Core Strategy

> **Data → Coverage → Reliability → Scale**

Not the reverse. The most impactful next action is always expanding data coverage, not improving architecture or UI.
