# Static read API — v1

Every client reads the same JSON files: the website today, and a PWA or
native app later. The files are plain JSON on the CDN (Cloudflare Pages).
Nothing is computed per request. The daily pipeline (`pipeline/publish.py`)
regenerates them from the full history in `data/events.json` and the store
registry in `data/stores.json`.

Base path: `/api/v1/`. Files are compact JSON, UTF-8. Responses carry an
`ETag`, so clients should revalidate rather than cache-bust.

## Compatibility rules

- Fields may be **added** within v1. Clients must ignore unknown fields.
- Fields are never renamed or removed, and their meaning never changes,
  within v1. A breaking change ships as `/api/v2/` next to v1.
- Every document has `api_version` (1) and `updated_at`, the pipeline run
  time in ISO 8601 with offset.

## Files

### `cities.json`
The list of cities. Clients discover cities here instead of hard-coding
them: every listed `id` has the `<city>/…` files below, even before its
first event. The order is stable, and the first city is the one the site
root shows. `stores` is the number of stores in the city's `stores.json`.
```json
{"api_version": 1, "updated_at": "…",
 "cities": [{"id": "madrid", "name": "Madrid", "timezone": "Europe/Madrid",
             "lat": 40.4168, "lng": -3.7038, "stores": 77},
            {"id": "barcelona", "name": "Barcelona", "timezone": "Europe/Madrid",
             "lat": 41.3874, "lng": 2.1686, "stores": 77},
            {"id": "malaga", "name": "Málaga", "timezone": "Europe/Madrid",
             "lat": 36.7213, "lng": -4.4214, "stores": 21}, …]}
```
Cities today: `madrid`, `barcelona`, `valencia`, `sevilla`, `malaga`,
`bilbao`, `zaragoza`. A city `id` is a plain ASCII slug and also its URL
segment (`/api/v1/malaga/`, `/malaga/`), and never changes; `name` keeps
the accents (`Málaga`). Each event and store belongs to exactly one city, the
one recorded for its store in the registry. Events therefore have no `city`
field: the file they come from is their city. The pipeline's city list
lives in `shared/cities.py`.

### `<city>/index.json`
Lists which weeks have events, with a count per week, and the range
`upcoming.json` covers. A week not listed has no events, so there is no
need to fetch it.
```json
{"api_version": 1, "city": "madrid", "updated_at": "…",
 "upcoming": {"from": "2026-11-09", "to": "2026-12-21", "events": 1400},
 "weeks": {"2026-W44": 310, "2026-W45": 298}}
```

### `<city>/events/upcoming.json`
Holds the previous ISO week through four weeks after the current one
(`from` inclusive, `to` exclusive, Madrid dates). It is what a client loads
first. All current cities use `Europe/Madrid`. Dates and offsets follow the
city's `timezone`, and the pipeline refuses to publish a city in any other
zone until that is supported.
```json
{"api_version": 1, "city": "madrid", "updated_at": "…",
 "from": "2026-11-09", "to": "2026-12-21", "events": [Event, …]}
```

### `<city>/events/<YYYY-Www>.json`
Holds one ISO-8601 week, Monday to Sunday by Madrid calendar date, for
example `2026-W47.json`. It has the same shape as `upcoming.json`, plus
`week`.

### `<city>/stores.json`
```json
{"api_version": 1, "city": "madrid", "updated_at": "…",
 "stores": [{"id": "arte-9", "name": "Arte 9", "address": "…",
             "lat": 40.43, "lng": -3.67, "website": "…", "notes": "…"}]}
```

## Event

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable identity, the same as the pipeline's `event_key`. Use it for favorites and links |
| `title` | string | |
| `store` | string | Display name; matches `stores[].name` |
| `store_id` | string? | Matches `stores[].id` |
| `store_address` | string? | Venue address from the source. Prefer it over the store's address |
| `game` | string? | Canonical game name. Missing means unknown |
| `format`, `format_official` | string? | Unified format, plus the publisher's own name |
| `best_of` | 1 \| 3? | |
| `datetime_start` | string | ISO 8601 with the Madrid offset |
| `datetime_end` | string? | |
| `time_corrected` | true? | `datetime_start` was corrected from what the source said (`pipeline/time_fixes.py`: Bandai TCG+ starts from 01:00 to 06:59 are read as PM) |
| `datetime_start_source` | string? | With `time_corrected`: the start time as the source gave it |
| `source_url` | string | Official event or store page |
| `capacity` | int? | Player cap reported by the source (Wizards, Ravensburger, Bandai, GEM) |
| `registered` | int? | Sign-ups so far, for upcoming events (Ravensburger, sometimes Wizards) |
| `participants` | int? | Players who actually took part, for finished events. Locators are re-read 7 days back to fill it in (Ravensburger, Wizards) |
| `full` | true? | The source says no spots are left (Bandai) |
| `share` | string? | Path of the event's share page (`/e/<id>/`). Use it when sharing a link, because it gives chat apps a real preview. It is present for events from 14 days ago to 60 days ahead |

Absent optional fields are omitted, not sent as `null`. Week files include
past events as history. Future events that disappeared from their source are
not listed.

## Website usage
The site's flow:
1. Pick the city: `?city=<id>` if present, else the first city in
   `cities.json` (Madrid).
2. Load that city's `upcoming.json`, `stores.json` and `index.json`.
3. Fetch a week file when the user navigates outside `upcoming`.
4. Share links as `?event=<base64url id>&w=<week>`, so the right week file can
   be fetched directly. Links to an event outside the first city also carry
   `city=<id>`: `?city=barcelona&event=…&w=…`.

The generated pages use the same parameter: the "open the calendar" links on
`/barcelona/…` pages go to `/?city=barcelona[&game=…|&store=…]`. Share pages
redirect Barcelona events to `/?city=barcelona&event=…`. Madrid links have
no `city` parameter, as before.
