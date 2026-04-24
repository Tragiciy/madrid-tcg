# Madrid TCG Aggregator

## Goal
Static website aggregating TCG tournament schedules from Madrid game stores.
Auto-updated daily via GitHub Actions → deployed on GitHub Pages.

## Architecture
```
scrapers/
  __init__.py
  micelion_games.py    # WCS Timetable AJAX API, requests only
  metropolis_center.py # Playwright — JS cookie challenge + window.evcalEvents
aggregator.py          # Discovers scrapers/, validates, normalises, deduplicates, writes JSON
public/
  index.html           # Alpine.js weekly calendar SPA (no build step)
  events.json          # Output: list of normalised events
  events_stats.json    # Output: totals by store/game + failed scrapers
.github/workflows/     # (not yet created) GitHub Actions for scheduled runs
requirements.txt       # requests, beautifulsoup4, playwright
```

## Data schema
```json
{
  "store":          "Micelion Games",
  "game":           "Magic: The Gathering | null",
  "format":         "Modern | null",
  "title":          "MAGIC: MODERN 2015",
  "datetime_start": "2026-04-24T19:00:00+02:00",
  "datetime_end":   "2026-04-24T20:00:00+02:00 | null",
  "price_eur":      5.0,
  "language":       "es",
  "source_url":     "https://...",
  "scraped_at":     "2026-04-24T18:28:24+02:00"
}
```

## Current state
- ✅ Scraper: `micelion_games.py` — WCS AJAX API, no Playwright needed
- ✅ Scraper: `metropolis_center.py` — Playwright for cookie challenge, `window.evcalEvents` JSON
- ✅ Aggregator: dynamic scraper discovery, validation (future-only), dedup, sort
- ✅ Game normalisation: canonical names + ALLOWED_GAMES allowlist
- ✅ Title-based fallback for game when wcs_type is generic ("Destacados")
- ✅ Frontend: dark-theme Alpine.js weekly calendar, horizontal + vertical views
- ✅ Filters: search, game, store, format (top bar + clickable chips in cards)
- ✅ Active chip highlight synced with top-bar selects
- ✅ Vertical UX: Today button scrolls to current day, Back to top button
- ✅ localStorage persists view mode preference
- ✅ Combined output: 181 events, 2 stores, 11 games
- ❌ GitHub Actions workflow not yet created
- ❌ GitHub Pages not yet configured

## Important decisions
- **No Playwright for Micelion**: site uses WCS plugin AJAX (`wcs_get_events_json`); plain `requests.post` is sufficient
- **Normalisation in aggregator, not scrapers**: `_normalize_game()` + `ALLOWED_GAMES` applied centrally so future scrapers benefit automatically
- **Two-step game resolution**: exact match on `wcs_type` name → substring search in title via `GAME_CANONICAL` keyword dict
- **events.json sorted by datetime_start**: aggregator always sorts before writing; frontend relies on this
- **No build step**: vanilla HTML + Alpine.js CDN only — keeps GitHub Pages deployment trivial

## Known issues / future improvements
- `game=None` for ~7 events: Warhammer/Gundam from Metropolis not in ALLOWED_GAMES; Catan & presentaciones from Micelion (intentional)
- Micelion: server returns events max ~3 months ahead regardless of date range
- Metropolis: only 2 months collected (current + next); adding month 3 requires one more `.evnav-right` click
- FORMAT_KEYWORDS duplicated in both scrapers — candidate for `scrapers/utils.py`
- GitHub Actions workflow still needs to be created

## Next task
Create `.github/workflows/update.yml` — scheduled GitHub Actions workflow that runs `aggregator.py` and commits updated `public/events.json` + `public/events_stats.json`.
