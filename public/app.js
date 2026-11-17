/* ── Helpers ───────────────────────────────────────────────────────── */

const PRESETS_STORAGE_KEY = 'tcg-presets-v1';
const DEFAULT_PRESET_STORAGE_KEY = 'tcg-default-preset-v1';
const ONBOARDING_STORAGE_KEY = 'tcg-onboarding-v1';
// Favorites are per city (Madrid keeps the original key): cleanupFavorites
// drops keys missing from loaded weeks, which would wipe another city's.
const FAVORITES_STORAGE_KEY = 'tcg-favorites-v1';
// favorite key → ISO week of its event, so favorites of deleted events can
// be pruned once their week is loaded (events load per week).
const FAVORITE_WEEKS_STORAGE_KEY = 'tcg-favorite-weeks-v1';

// Returns YYYY-MM-DD string for a Date object, in local time
function isoDay(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// Monday of the week containing `d`
function weekMonday(d) {
  const copy = new Date(d);
  const dow = copy.getDay();            // 0=Sun … 6=Sat
  const delta = (dow === 0) ? -6 : 1 - dow; // shift to Monday
  copy.setDate(copy.getDate() + delta);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function addDays(d, n) {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + n);
  return copy;
}

function formatShort(d) {
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

function googleCalendarDate(d) {
  if (!(d instanceof Date) || Number.isNaN(d.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Madrid',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(d).reduce((acc, part) => {
    if (part.type !== 'literal') acc[part.type] = part.value;
    return acc;
  }, {});
  return `${parts.year}${parts.month}${parts.day}T${parts.hour}${parts.minute}${parts.second}`;
}

function calendarTitle(e) {
  const game = e.game || 'TCG';
  const store = e.store || 'Unknown store';
  return e.format ? `${game} — ${e.format} at ${store}` : `${game} event at ${store}`;
}

function calendarDetails(e) {
  const lines = [
    e.title ? `Event: ${e.title}` : null,
    `Game: ${e.game || 'Unknown'}`,
    e.format ? `Format: ${e.format}` : null,
    `Store: ${e.store || 'Unknown'}`,
    e.datetime_start ? `Date: ${new Date(e.datetime_start).toLocaleDateString('en-GB', { dateStyle: 'full', timeZone: 'Europe/Madrid' })}` : null,
    e.datetime_start ? `Time: ${new Date(e.datetime_start).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Madrid' })}` : null,
    e.notes ? `Notes: ${e.notes}` : null,
    e.source_url ? `Source: ${e.source_url}` : null,
  ];
  return lines.filter(Boolean).join('\n');
}

function buildGoogleCalendarUrl(e) {
  if (!e || !e.datetime_start) return null;
  const start = new Date(e.datetime_start);
  if (Number.isNaN(start.getTime())) return null;
  const end = new Date(start.getTime() + 4 * 60 * 60 * 1000);
  const startText = googleCalendarDate(start);
  const endText = googleCalendarDate(end);
  if (!startText || !endText) return null;

  const location = storeAddress(e) || e.store || '';
  const params = [
    ['action', 'TEMPLATE'],
    ['text', calendarTitle(e)],
    ['dates', `${startText}/${endText}`],
    ['ctz', 'Europe/Madrid'],
    ['details', calendarDetails(e)],
    ['location', location],
  ];
  return 'https://calendar.google.com/calendar/render?' +
    params.map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join('&');
}

function buildOutlookUrl(e, base) {
  if (!e || !e.datetime_start) return null;
  const start = new Date(e.datetime_start);
  if (Number.isNaN(start.getTime())) return null;

  let end;
  if (e.datetime_end) {
    end = new Date(e.datetime_end);
    if (Number.isNaN(end.getTime())) {
      end = new Date(start.getTime() + 4 * 60 * 60 * 1000);
    }
  } else {
    end = new Date(start.getTime() + 4 * 60 * 60 * 1000);
  }

  const location = storeAddress(e) || e.store || '';

  const params = [
    ['subject', calendarTitle(e)],
    ['startdt', start.toISOString()],
    ['enddt', end.toISOString()],
    ['location', location],
    ['body', calendarDetails(e)],
  ];

  return base + '?' + params.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
}



function icsEscape(s) {
  return String(s || '')
    .replace(/\\/g, '\\\\')
    .replace(/\n/g, '\\n')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,');
}

function icsUtcDate(d) {
  if (!(d instanceof Date) || Number.isNaN(d.getTime())) return '';
  return d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

function buildIcsContent(e) {
  if (!e || !e.datetime_start) return '';
  const start = new Date(e.datetime_start);
  if (Number.isNaN(start.getTime())) return '';

  let end;
  if (e.datetime_end) {
    end = new Date(e.datetime_end);
    if (Number.isNaN(end.getTime())) {
      end = new Date(start.getTime() + 4 * 60 * 60 * 1000);
    }
  } else {
    end = new Date(start.getTime() + 4 * 60 * 60 * 1000);
  }

  const location = storeAddress(e) || e.store || '';
  const uidBase = eventKey(e).replace(/[^a-zA-Z0-9]/g, '-');
  const uid = `${uidBase}@madrid-tcg-events`;

  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Madrid TCG Events//NONSGML v1.0//EN',
    'BEGIN:VEVENT',
    `UID:${uid}`,
    `DTSTAMP:${icsUtcDate(new Date())}`,
    `DTSTART:${icsUtcDate(start)}`,
    `DTEND:${icsUtcDate(end)}`,
    `SUMMARY:${icsEscape(calendarTitle(e))}`,
    `DESCRIPTION:${icsEscape(calendarDetails(e))}`,
    `LOCATION:${icsEscape(location)}`,
  ];

  if (e.source_url) {
    lines.push(`URL:${icsEscape(e.source_url)}`);
  }

  lines.push('END:VEVENT', 'END:VCALENDAR');
  return lines.join('\r\n');
}

function downloadIcs(e) {
  const content = buildIcsContent(e);
  if (!content) return;
  const blob = new Blob([content], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const filename = (e.title || 'event').replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) + '.ics';
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Great-circle distance in km between two {lat, lng} points.
function haversineKm(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

/* ── Map view (Leaflet) ─────────────────────────────────────────────
   Leaflet is fetched from jsDelivr (version-pinned, SRI) the first time the
   map opens, so the calendar views never pay for it. Leaflet objects live
   here, outside Alpine's reactive state: Alpine would wrap them in proxies. */
const LEAFLET = {
  js:     'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js',
  jsSri:  'sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH',
  css:    'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css',
  cssSri: 'sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H',
  // Leaflet.markercluster: central Madrid has dozens of stores within a
  // few streets; unclustered, their pins hide each other.
  clusterJs:     'https://cdn.jsdelivr.net/npm/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js',
  clusterJsSri:  'sha384-eXVCORTRlv4FUUgS/xmOyr66XBVraen8ATNLMESp92FKXLAMiKkerixTiBvXriZr',
  clusterCss:    'https://cdn.jsdelivr.net/npm/leaflet.markercluster@1.5.3/dist/MarkerCluster.css',
  clusterCssSri: 'sha384-pmjIAcz2bAn0xukfxADbZIb3t8oRT9Sv0rvO+BR5Csr6Dhqq+nZs59P0pPKQJkEV',
};
// One promise per asset URL; a failed one is forgotten (and its tag removed)
// so the next open retries just that asset.
const assetLoads = new Map();
function loadAsset(tag, attrs) {
  const url = attrs.href || attrs.src;
  if (!assetLoads.has(url)) {
    assetLoads.set(url, new Promise((resolve, reject) => {
      const el = Object.assign(document.createElement(tag), attrs, { crossOrigin: 'anonymous' });
      el.onload = resolve;
      el.onerror = () => {
        assetLoads.delete(url);
        el.remove();
        reject(new Error(`Could not load ${url}`));
      };
      document.head.appendChild(el);
    }));
  }
  return assetLoads.get(url);
}

// Resolves once Leaflet AND the cluster plugin (which extends window.L) are
// ready. `window.L` alone is not enough: it exists while the plugin loads.
function loadLeaflet() {
  return Promise.all([
    loadAsset('link', { rel: 'stylesheet', href: LEAFLET.css, integrity: LEAFLET.cssSri }),
    loadAsset('script', { src: LEAFLET.js, integrity: LEAFLET.jsSri }),
  ]).then(() => Promise.all([
    loadAsset('link', { rel: 'stylesheet', href: LEAFLET.clusterCss, integrity: LEAFLET.clusterCssSri }),
    loadAsset('script', { src: LEAFLET.clusterJs, integrity: LEAFLET.clusterJsSri }),
  ])).then(() => window.L);
}

// focus: a store to bring into view once its pin exists (set by the list).
const MAP = { map: null, pins: null, me: null, fitKey: null, fitBounds: null, focus: null, drawn: null, markers: new Map() };

// Bounds for the first view: the stores within CORE_KM of the median store,
// when they are most of the results. A few far-off towns would otherwise
// zoom the map out to the whole region; they stay one zoom-out away.
const CORE_KM = 20;
function coreBounds(L, points) {
  if (points.length < 4) return L.latLngBounds(points);
  const mid = (i) => points.map(p => p[i]).sort((a, b) => a - b)[Math.floor(points.length / 2)];
  const centre = { lat: mid(0), lng: mid(1) };
  const core = points.filter(p => haversineKm(centre, { lat: p[0], lng: p[1] }) <= CORE_KM);
  return L.latLngBounds(core.length >= points.length * 0.7 ? core : points);
}

// Draw one pin per located store. `fitKey` changes when the question changes
// (week, day, filters, location); only then is the view refitted, so a
// minute tick or a selection never moves the map under the user's finger.
function renderStoreMap(L, el, { stores, selected, me, radiusKm, fitKey, onSelect, centre }) {
  if (!MAP.map) {
    // Build everything first and publish MAP.map last: a throw half-way
    // must not leave a map without its layers (later draws would skip setup).
    const map = L.map(el).setView([centre.lat, centre.lng], MAP_DEFAULT_VIEW.zoom);
    L.tileLayer(MAP_TILES.url, { attribution: MAP_TILES.attribution, maxZoom: MAP_TILES.maxZoom }).addTo(map);
    const pins = L.markerClusterGroup({
      maxClusterRadius: 40,
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: true,
      // A cluster shows its total events (like the pins), not its store count.
      iconCreateFunction: (cluster) => {
        const n = cluster.getAllChildMarkers().reduce((sum, m) => sum + (m.options.eventCount || 0), 0);
        return L.divIcon({
          className: 'map-pin-wrap',
          html: `<span class="map-pin map-cluster">${n}</span>`,
          iconSize: [38, 38],
          iconAnchor: [19, 19],
        });
      },
    }).addTo(map);
    const me = L.layerGroup().addTo(map);
    map.on('click', () => MAP.onSelect && MAP.onSelect(null));
    Object.assign(MAP, { pins, me, map });
  }
  MAP.onSelect = onSelect;
  MAP.map.invalidateSize();  // the container may have been hidden (x-show)
  // Rebuilding the pins mid-animation cancels a cluster zoom: skip redraws
  // that would change nothing (e.g. the minute tick).
  const drawn = JSON.stringify([stores, selected, me, radiusKm]);
  if (drawn !== MAP.drawn) {
    MAP.drawn = drawn;
    drawPins(L, { stores, selected, me, radiusKm, onSelect });
  }
  if (fitKey !== MAP.fitKey) {
    MAP.fitKey = fitKey;
    if (MAP.fitBounds) MAP.map.fitBounds(MAP.fitBounds, { padding: [28, 28], maxZoom: 15, animate: false });
  }
  const focus = MAP.focus && MAP.markers.get(MAP.focus);
  MAP.focus = null;
  if (focus) MAP.pins.zoomToShowLayer(focus);  // opens its cluster if needed
}

function drawPins(L, { stores, selected, me, radiusKm, onSelect }) {
  MAP.pins.clearLayers();
  MAP.me.clearLayers();
  MAP.markers.clear();

  const points = [];
  for (const s of stores) {
    if (s.lat == null || s.lng == null) continue;
    const isSel = s.name === selected;
    const icon = L.divIcon({
      className: 'map-pin-wrap',
      // Only a number and class names from GAME_CLASS_MAP: no scraped text.
      html: `<span class="map-pin ${s.gameClass}${isSel ? ' is-selected' : ''}">${Number(s.count)}</span>`,
      iconSize: [30, 30],
      iconAnchor: [15, 15],
    });
    const label = document.createElement('span');
    label.textContent = s.name;  // textContent: store names are data
    const marker = L.marker([s.lat, s.lng], {
      icon, zIndexOffset: isSel ? 1000 : s.count, keyboard: true, title: s.name, eventCount: s.count,
    })
      .bindTooltip(label, { direction: 'top', offset: [0, -14] })
      .on('click', (ev) => { L.DomEvent.stopPropagation(ev); onSelect(s.name); });
    MAP.pins.addLayer(marker);
    MAP.markers.set(s.name, marker);
    points.push([s.lat, s.lng]);
  }

  let fitBounds = points.length ? coreBounds(L, points) : null;
  if (me) {
    const circle = L.circle([me.lat, me.lng], {
      radius: (radiusKm || 0) * 1000, color: '#2563eb', weight: 1, fillOpacity: 0.06, interactive: false,
    }).addTo(MAP.me);
    L.circleMarker([me.lat, me.lng], {
      radius: 7, color: '#fff', weight: 2, fillColor: '#2563eb', fillOpacity: 1, interactive: false,
    }).addTo(MAP.me);
    if (radiusKm) fitBounds = circle.getBounds();
    else if (fitBounds) fitBounds.extend([me.lat, me.lng]);
  }
  MAP.fitBounds = fitBounds;
}


// Short attendance label for a card: "Full", "24 played", "12/24", or ''.
// Capacity alone is left to the detail panel (it is known for most events).
function attendanceBadge(e) {
  if (!e) return '';
  if (e.participants) return `${e.participants} played`;
  if (e.full) return 'Full';
  if (e.registered) return e.capacity ? `${e.registered}/${e.capacity}` : `${e.registered} signed up`;
  // No sign-ups reported yet: the cap alone still tells a player how big
  // the event is. Only past events with no player count stay blank.
  if (e.capacity && new Date(e.datetime_start) > Date.now()) return `${e.capacity} spots`;
  return '';
}

// Detail-panel line: everything the source reported about attendance.
function attendanceDetail(e) {
  if (!e) return '';
  const parts = [];
  if (e.participants) parts.push(`${e.participants} players took part`);
  else if (e.registered) parts.push(`${e.registered} signed up`);
  if (e.capacity) parts.push(`${e.capacity} spots`);
  if (e.full && !e.participants) parts.push('full');
  return parts.join(' · ');
}

function formatKm(km) {
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km < 10 ? km.toFixed(1) : Math.round(km)} km`;
}

// Only http(s) links from scraped data are rendered (never javascript:).
function safeUrl(url) {
  return (typeof url === 'string' && /^https?:\/\//i.test(url)) ? url : null;
}

// Address for an event's store. The locator-supplied address comes first:
// it is the venue of that very event (chains have several branches), then
// the store registry (STORE_META, loaded from api/v1/madrid/stores.json).
function storeAddress(e) {
  if (!e) return '';
  return e.store_address
    || (STORE_META[e.store] && STORE_META[e.store].address)
    || '';
}

// Stable event identity. Mirrors aggregator.event_key() exactly, which is
// unique across events.json by construction (merge is keyed on it).
function eventKey(e) {
  if (e.id) return e.id;  // provided by the API (same algorithm as below)
  const store = e.store || '';
  const sid = e.source_event_id;
  // Namespaced ids ("wizards:123") are globally unique: no store prefix, so a
  // store rename never changes the key.
  if (sid && sid.includes(':')) return `id:${sid}`;
  if (sid) return `${store}|id:${sid}`;
  return [store, e.title || '', e.datetime_start || '', e.location || ''].join('|');
}

// Pre-2026-11 key (source_url + datetime_start). Not unique — listing-URL
// scrapers share one source_url — kept only to migrate saved favorites and
// resolve old shared ?event= links.
function legacyEventKey(e) {
  return (e.source_url || '') + (e.datetime_start || '');
}

function encodeEventParam(value) {
  if (!value) return '';
  const bytes = new TextEncoder().encode(value);
  const base64 = btoa(String.fromCharCode(...bytes));
  return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function decodeEventParam(value) {
  if (!value) return null;
  try {
    const base64 = value.replace(/-/g, '+').replace(/_/g, '/');
    const padding = (4 - (base64.length % 4)) % 4;
    const padded = base64 + '='.repeat(padding);
    const binary = atob(padded);
    const bytes = Uint8Array.from([...binary].map(c => c.charCodeAt(0)));
    return new TextDecoder().decode(bytes);
  } catch {
    return null;
  }
}

function segmentForHour(h) {
  if (h < 12) return 'morning';
  if (h < 16) return 'afternoon';
  if (h < 19) return 'evening';
  return 'late';
}

// Returns { iso, hour, minute, minutesSinceMidnight } in Europe/Madrid.
// Builds a real Date whose internal time matches Madrid wall-clock by
// roundtripping through toLocaleString('en-US', { timeZone: 'Europe/Madrid' }).
// Date methods (getHours, getMinutes, etc.) then read out the Madrid values
// directly — no string parsing or token assembly.
function readMadridNow() {
  const m = new Date(new Date().toLocaleString('en-US', { timeZone: 'Europe/Madrid' }));
  const y  = m.getFullYear();
  const mo = String(m.getMonth() + 1).padStart(2, '0');
  const d  = String(m.getDate()).padStart(2, '0');
  const hour   = m.getHours();
  const minute = m.getMinutes();
  return {
    iso: `${y}-${mo}-${d}`,
    hour,
    minute,
    minutesSinceMidnight: hour * 60 + minute,
  };
}

// Static read API (docs/api-v1.md), one directory per city. upcoming.json
// covers the previous week to four weeks ahead; other weeks are fetched when
// the user navigates there. Absolute, so the app also works when served as
// the offline fallback for another path (the service worker returns the
// cached shell for any page).
const API_ROOT = '/api/v1';
const DEFAULT_CITY = 'madrid';        // the city the site root shows
const CITY_STORAGE_KEY = 'tcg-city-v1';
const CITY_ID_RE = /^[a-z]+(-[a-z]+)*$/;
// Links that point at one event or filter (share pages, SEO pages) carry
// ?city= for every city but the default; without it they mean Madrid, not
// whatever city this visitor last chose.
const CITY_SCOPED_PARAMS = ['event', 'w', 'game', 'store', 'format'];

// The city to open: ?city=, else Madrid for a city-scoped link, else the
// last one chosen, else the default. index.html runs the same rule inline
// (to preload the right files) and leaves its answer in window.TCG_START_CITY.
function startCity() {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get('city');
  if (fromUrl && CITY_ID_RE.test(fromUrl)) return fromUrl;
  if (CITY_SCOPED_PARAMS.some(p => params.has(p))) return DEFAULT_CITY;
  try {
    const saved = localStorage.getItem(CITY_STORAGE_KEY);
    if (saved && CITY_ID_RE.test(saved)) return saved;
  } catch { /* storage blocked */ }
  return DEFAULT_CITY;
}

// ISO-8601 week id ("2026-W47") of a local-midnight Date.
function isoWeekOf(d) {
  const t = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  t.setDate(t.getDate() + 3 - ((t.getDay() + 6) % 7));       // Thursday of this week
  const jan4 = new Date(t.getFullYear(), 0, 4);
  const week = 1 + Math.round(((t - jan4) / 86400000 - 3 + ((jan4.getDay() + 6) % 7)) / 7);
  return `${t.getFullYear()}-W${String(week).padStart(2, '0')}`;
}

// Week id of an ISO datetime string, by its Madrid calendar date.
function weekOfIso(iso) {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return isoWeekOf(new Date(y, m - 1, d));
}

// Today's calendar date in Madrid as a local-midnight Date, so week maths
// matches the Madrid dates in events.json for visitors in any timezone.
function madridToday() {
  const [y, m, d] = readMadridNow().iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

/* ── Alpine app ────────────────────────────────────────────────────── */
function app() {
  return {
    events:       [],
    city:         window.TCG_START_CITY || startCity(),
    cities:       [],     // from api/v1/cities.json; the switcher shows when > 1
    cityError:    '',
    loading:      true,
    loadError:    null,
    weekStart:    weekMonday(madridToday()),
    weekIndex:    null,   // {"2026-W47": count, …} from the API index
    filters:      { search: '', game: [], store: [], format: [] },
    filterSearch: { game: '', store: '', format: '' },
    savedPresets: [],
    defaultPresetId: null,
    defaultPresetActive: false,
    editingPresetId: null,
    editingPresetSnapshot: null,
    editingPresetName: '',
    onboardingOpen: false,
    onboardingCompleted: false,
    onboarding: { game: [], store: [] },
    segmentFilter: { morning: true, afternoon: true, evening: true, late: true },
    openFacet: null,
    // Independent of segmentFilter: collapsed segments still occupy a
    // grid row (alignment preserved) but their cards are hidden.
    collapsedSegments: { morning: false, afternoon: false, evening: false, late: false },
    // Vertical view collapses per day: { '2026-11-17:evening': true }.
    collapsedDaySegs: {},
    SEGMENTS,
    showBackToTop: false,
    // Phone jump bar: the day / segment currently at the top of the
    // viewport (scroll-spy), highlighted in the sticky bar.
    jumpDay: null,
    jumpSeg: null,
    filterHistory: [],       // stack of { filters, segmentFilter } snapshots; max 5
    selectedEvent: null,
    panelOpen: false,
    calendarMenuOpen: false,
    favorites:         [],    // array of eventKey strings
    showFavoritesOnly: false,
    // "Near me": the visitor's position (kept in memory only — never stored
    // or sent anywhere) and the radius filter in km.
    nearMe:       null,
    radiusKm:     5,
    geoStatus:    '',      // '', 'locating', or an error message
    // Map view: the chosen day (null = the whole week), the selected store
    // (display name) and a load/error message for the map itself.
    mapDay:       null,
    mapStore:     null,
    mapStatus:    '',
    mapFailed:    false,
    mapAttempt:   0,     // bumped by Retry / coming back online: re-runs drawMap
    offline:      !navigator.onLine,
    // Reactive Europe/Madrid clock — refreshed every minute. Used to
    // mark past segments and past events on today.
    nowMadrid: readMadridNow(),

    // Default to horizontal; respect localStorage if user previously
    // switched to vertical. Key is versioned so that the old default-
    // vertical era's persisted 'vertical' values don't carry over.
    viewMode: localStorage.getItem('tcg-view-v2') || 'horizontal',

    /* ── Init ──────────────────────────────────────────────────────── */
    async init() {
      // Alpine 3 calls init() itself; guard against a second call (an extra
      // x-init="init()" once ran everything — every fetch — twice).
      if (this._initialized) return;
      this._initialized = true;
      this.loadFavorites();
      window.addEventListener('online',  () => {
        this.offline = false;
        if (this.mapFailed) this.mapAttempt++;
      });
      window.addEventListener('offline', () => { this.offline = true; });
      if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
        navigator.serviceWorker.register('/sw.js').catch(err => console.warn('SW registration failed', err));
      }
      this.loadPresets();
      this.loadDefaultPreset();
      this.loadOnboardingState();

      // Show Back to top after 400px scroll
      window.addEventListener('scroll', () => {
        this.showBackToTop = window.scrollY > 400;
        this.onScrollSpy();
      }, { passive: true });
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          if (this.editingPresetId) {
            this.cancelEditPreset();
          } else if (this.calendarMenuOpen) {
            this.calendarMenuOpen = false;
          } else if (this.selectedEvent) {
            this.closePanel();
          } else if (this.openFacet) {
            this.openFacet = null;
          }
        }
      });

      if (!localStorage.getItem('tcg-view-v2') && window.matchMedia('(max-width: 900px)').matches) {
        this.viewMode = 'vertical';
      }

      // Run once immediately, then refresh the Madrid clock every minute
      // so past-segment / past-event styling progresses without a reload.
      this.nowMadrid = readMadridNow();
      setInterval(() => { this.nowMadrid = readMadridNow(); }, 60_000);
      
      fetch(`${API_ROOT}/cities.json`)
        .then(r => (r.ok ? r.json() : {})).then(d => { this.cities = d.cities || []; this.syncTitle(); })
        .catch(() => {});
      try {
        const data = await this.fetchCityData(this.city).catch((err) => {
          if (err.status === 404 && this.city !== DEFAULT_CITY) return null;  // unknown city
          throw err;
        });
        if (data) {
          this.applyCityData(data);
        } else {
          this.city = DEFAULT_CITY;
          this.applyCityData(await this.fetchCityData(DEFAULT_CITY));
          this.rememberCity(DEFAULT_CITY);
          this.favorites = [];
          this.loadFavorites();
        }
        // A ?city= link also becomes this visitor's city next time. Any city
        // but the default keeps ?city= in the URL: filter and event links
        // built from it would otherwise mean Madrid. Plain Madrid visits leave
        // storage and the URL alone.
        if (this.city !== DEFAULT_CITY || new URLSearchParams(window.location.search).has('city')) {
          this.rememberCity(this.city);
        }
        this.syncTitle();
        this.cleanupFavorites();
        const params = new URLSearchParams(window.location.search);
        const hasUrlFilters = ['game', 'store', 'format'].some(f => params.get(f));
        if (hasUrlFilters) {
          this.applyFiltersFromUrl();
        } else {
          this.applyDefaultPreset();
        }
        this.cleanupFilters({ syncUrl: false });
        if (this.shouldShowOnboarding(hasUrlFilters)) {
          this.openOnboarding();
        }
        const rawEventParam = new URLSearchParams(window.location.search).get('event');
        if (rawEventParam) {
          const decoded = decodeEventParam(rawEventParam);
          const eventKey = decoded !== null ? decoded : rawEventParam;
          const find = () => this.events.find(ev => this.eventKey(ev) === eventKey)
            || this.events.find(ev => legacyEventKey(ev) === eventKey);
          let event = find();
          if (!event) {
            // Outside upcoming.json: load the event's week (from ?w=, or the
            // date inside older keys) and look again.
            const m = eventKey.match(/\d{4}-\d{2}-\d{2}T/);
            const week = params.get('w') || (m ? weekOfIso(m[0]) : null);
            if (week) {
              await this.loadWeek(week);
              event = find();
            }
          }
          if (event) this.openPanel(event);
        }
      } catch (err) {
        console.error('Failed to load events.json', err);
        this.loadError = 'Could not load events. Check your connection and try again.';
      } finally {
        this.loading = false;
      }

      if (this.viewMode === 'vertical') {
        this.$nextTick(() => {
          requestAnimationFrame(() => this.scrollToCurrentSegment());
        });
      } else {
        // Phones see ~2 day columns: start on today's, not on Monday's.
        this.$nextTick(() => requestAnimationFrame(() => {
          this.scrollColumnIntoView(this.nowMadrid.iso, 'auto');
        }));
      }
      this.$nextTick(() => requestAnimationFrame(() => this.updateScrollSpy()));

      this.$watch('filters.search', () => this.cleanupFilters());
    },

    get apiBase() {
      return `${API_ROOT}/${this.city}`;
    },

    get cityName() {
      const c = this.cities.find(x => x.id === this.city);
      if (c) return c.name;
      return this.city.split('-').map(w => w[0].toUpperCase() + w.slice(1)).join(' ');
    },

    // Stores, the week index and upcoming events of one city, fetched but
    // not applied: callers apply them only if that city is still current.
    async fetchCityData(city) {
      // Plain fetches: Cloudflare Pages serves JSON with
      // `max-age=0, must-revalidate` + ETag, so the browser revalidates on
      // every visit and gets a cheap 304 when the data hasn't changed.
      // URLs must match the preloads index.html injects.
      // Stores and the week index are optional: the calendar works without.
      const base = `${API_ROOT}/${city}`;
      const storesReq = fetch(`${base}/stores.json`)
        .then(r => (r.ok ? r.json() : {})).catch(() => ({}));
      const indexReq = fetch(`${base}/index.json`)
        .then(r => (r.ok ? r.json() : {})).catch(() => ({}));
      const res = await fetch(`${base}/events/upcoming.json`);
      if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status });
      return { upcoming: await res.json(), stores: await storesReq, index: await indexReq };
    },

    // Swap in a city's data in one step. _dataGen changes with it, so a week
    // fetch started for the previous data is discarded when it lands.
    applyCityData({ upcoming, stores, index }) {
      this._dataGen = (this._dataGen || 0) + 1;
      // STORE_META is not reactive: fill it before the events land, so
      // nothing computed from the events sees stores without coordinates.
      for (const k of Object.keys(STORE_META)) delete STORE_META[k];
      for (const s of stores.stores || []) STORE_META[s.name] = s;
      this._loadedWeeks = new Set();
      this.events = upcoming.events;
      this.markWeeksLoaded(upcoming.from, upcoming.to);
      this.weekIndex = index.weeks || null;
    },

    // Persist the city and put it in the URL (the default city has no
    // ?city=, so Madrid links stay as they were).
    rememberCity(id) {
      try { localStorage.setItem(CITY_STORAGE_KEY, id); } catch { /* storage blocked */ }
      const url = new URL(window.location.href);
      if (id === DEFAULT_CITY) url.searchParams.delete('city');
      else url.searchParams.set('city', id);
      history.replaceState(null, '', url.toString());
      this.syncTitle();
    },

    syncTitle() {
      document.title = `${this.cityName} TCG Events — tournament calendar`;
    },

    // Nothing changes until the new city's data has arrived: on failure the
    // previous city stays fully in place. The switcher is disabled while
    // loading, and the request counter drops answers that arrive late.
    async setCity(id) {
      if (id === this.city || !CITY_ID_RE.test(id) || this.loading) return;
      const request = (this._cityRequest = (this._cityRequest || 0) + 1);
      this.cityError = '';
      this.loading = true;
      let data;
      try {
        data = await this.fetchCityData(id);
      } catch (err) {
        console.error('Failed to load city', id, err);
        if (request === this._cityRequest) {
          this.cityError = 'Could not load that city. Check your connection and try again.';
          this.loading = false;
        }
        return;
      }
      if (request !== this._cityRequest) return;
      this.closePanel();
      this.city = id;
      this.applyCityData(data);
      this.rememberCity(id);
      // Stores are per city; games and formats carry over.
      this.filters.store = [];
      this.filterHistory = [];
      this.defaultPresetActive = false;
      this.mapStore = null;
      this.mapDay = null;
      this.nearMe = null;
      MAP.fitKey = null;
      const c = this.cities.find(x => x.id === id);
      if (MAP.map && c) MAP.map.setView([c.lat, c.lng], MAP_DEFAULT_VIEW.zoom, { animate: false });
      this.favorites = [];
      this.loadFavorites();
      this.cleanupFavorites();
      this.cleanupFilters({ syncUrl: true });
      this.loading = false;
      // The week on screen may lie outside upcoming.json.
      await this.loadWeek(isoWeekOf(this.weekStart));
    },

    favoritesKey(base = FAVORITES_STORAGE_KEY) {
      return this.city === DEFAULT_CITY ? base : `${base}:${this.city}`;
    },

    setView(mode) {
      this.viewMode = mode;
      localStorage.setItem('tcg-view-v2', mode);
      if (mode === 'map') {
        MAP.fitKey = null;  // reopening the map shows every result again
        return;
      }
      if (mode === 'vertical') {
        this.$nextTick(() => {
          requestAnimationFrame(() => this.scrollToCurrentSegment());
        });
      } else {
        this.$nextTick(() => requestAnimationFrame(() => {
          this.scrollColumnIntoView(this.jumpDay || this.nowMadrid.iso, 'auto');
        }));
      }
      this.$nextTick(() => requestAnimationFrame(() => this.updateScrollSpy()));
    },

    scrollToCurrentSegment() {
      const now = this.nowMadrid;
      const currentHour = now.hour;
      let targetSeg = null;
      for (const seg of SEGMENTS) {
        if (currentHour < seg.end) {
          targetSeg = seg;
          break;
        }
      }
      if (!targetSeg) targetSeg = SEGMENTS[SEGMENTS.length - 1];
      const today = document.getElementById('day-' + now.iso);
      if (!today) return;
      const el = today.querySelector(`[data-seg-key="${targetSeg.key}"]`);
      (el || today).scrollIntoView({ behavior: 'smooth', block: 'start' });
    },

    /* ── Scroll to a day column by ISO date ────────────────────────── */
    scrollToDay(iso) {
      const el = document.getElementById('day-' + iso);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },

    /* ── Phone jump bar ─────────────────────────────────────────────
         Day buttons scroll to that day (horizontally in the grid,
         vertically in the list); segment buttons scroll to that time of
         day (the grid row, or the segment inside the current day). */

    // Height of the sticky jump bar (0 when it is hidden on wide screens),
    // plus a little breathing room, so jump targets land below it.
    jumpOffset() {
      const bar = this.$refs.jumpBar;
      if (!bar || getComputedStyle(bar).position !== 'sticky') return 8;
      return bar.getBoundingClientRect().height + 8;
    },

    scrollWindowTo(el) {
      const top = el.getBoundingClientRect().top + window.scrollY - this.jumpOffset();
      window.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    },

    // Horizontal grid: bring a day's column to the left edge.
    scrollColumnIntoView(iso, behavior = 'smooth') {
      const wrap = this.$refs.calWrap;
      const el = document.getElementById('day-' + iso);
      if (!wrap || !el || this.viewMode !== 'horizontal') return;
      const left = wrap.scrollLeft + el.getBoundingClientRect().left - wrap.getBoundingClientRect().left;
      wrap.scrollTo({ left: Math.max(0, left), behavior });
    },

    // While a jump animates, the scroll-spy would flick through every day
    // it passes; pin the highlight to the target until scrolling has been
    // idle for a moment (each scroll event re-arms the timer), so a second
    // tap mid-animation still sees the day the user picked.
    lockScrollSpy(idleMs = 400) {
      clearTimeout(this._spyUnlock);
      this._spyLocked = true;
      // No recompute on unlock: near the page bottom the target can't reach
      // the top, and the spy would snap the highlight back to what's above.
      // The next manual scroll recomputes it.
      this._spyUnlock = setTimeout(() => { this._spyLocked = false; }, idleMs);
    },

    // Events per segment on the day highlighted in the jump bar; segment
    // buttons with 0 are disabled (nothing to jump to on that day).
    get jumpDaySegCounts() {
      const day = this.weekDays.find(d => d.iso === this.jumpDay);
      const counts = {};
      for (const s of SEGMENTS) counts[s.key] = day ? day.segmentMap[s.key].length : 0;
      return counts;
    },

    jumpToDay(iso) {
      this.jumpDay = iso;
      this.lockScrollSpy();
      if (this.viewMode === 'horizontal') {
        this.scrollColumnIntoView(iso);
        // From the filters above the grid, also bring the grid into view;
        // inside the grid keep the current row (same time, other day).
        const wrap = this.$refs.calWrap;
        if (wrap && wrap.getBoundingClientRect().top > window.innerHeight / 2) {
          this.scrollWindowTo(wrap);
        }
        return;
      }
      const el = document.getElementById('day-' + iso);
      if (el) this.scrollWindowTo(el);
    },

    jumpToSegment(key) {
      this.jumpSeg = key;
      this.lockScrollSpy();
      if (this.viewMode === 'horizontal') {
        const cell = this.$refs.calWrap && this.$refs.calWrap.querySelector(`.seg-cell[data-seg="${key}"]`);
        if (cell) this.scrollWindowTo(cell);
        return;
      }
      // Vertical: that segment in the current day, else the next day with it.
      // A collapsed block is opened, since the user asked to see it.
      const days = this.weekDays;
      const start = Math.max(0, days.findIndex(d => d.iso === (this.jumpDay || this.nowMadrid.iso)));
      for (let i = start; i < days.length; i++) {
        const dayEl = document.getElementById('day-' + days[i].iso);
        const el = dayEl && dayEl.querySelector(`[data-seg-key="${key}"]`);
        if (el) {
          this.jumpDay = days[i].iso;
          this.collapsedDaySegs[days[i].iso + ':' + key] = false;
          this.scrollWindowTo(el);
          return;
        }
      }
    },

    onScrollSpy() {
      if (this._spyLocked) { this.lockScrollSpy(150); return; }
      if (this._spyQueued) return;
      this._spyQueued = true;
      requestAnimationFrame(() => {
        this._spyQueued = false;
        this.updateScrollSpy();
      });
    },

    updateScrollSpy() {
      if (this._spyLocked) return;
      const bar = this.$refs.jumpBar;
      const wrap = this.$refs.calWrap;
      if (!bar || !wrap || getComputedStyle(bar).position !== 'sticky') return;
      const line = this.jumpOffset() + 12;
      // Last element whose top edge has scrolled past the line under the bar.
      const lastAbove = (els) => {
        let hit = els[0] || null;
        for (const el of els) if (el.getBoundingClientRect().top <= line) hit = el;
        return hit;
      };
      if (this.viewMode === 'horizontal') {
        const wl = wrap.getBoundingClientRect().left;
        const day = [...wrap.querySelectorAll('.day-header-cell')]
          .find(h => h.getBoundingClientRect().right - wl > 40);
        if (day) this.jumpDay = day.id.slice(4);
        const rows = this.activeSegments
          .map(s => wrap.querySelector(`.seg-cell[data-seg="${s.key}"]`))
          .filter(Boolean);
        const row = lastAbove(rows);
        if (row) this.jumpSeg = row.dataset.seg;
        return;
      }
      const dayEl = lastAbove([...wrap.querySelectorAll('.day-col')]);
      if (!dayEl) return;
      this.jumpDay = dayEl.id.slice(4);
      const seg = lastAbove([...dayEl.querySelectorAll('[data-seg-key]')]);
      this.jumpSeg = seg ? seg.dataset.segKey : null;
    },

    /* ── Navigation ────────────────────────────────────────────────── */
    // Filters are week-scoped, so they are validated only once the week's
    // events are loaded — otherwise a not-yet-fetched week would wipe them.
    async prevWeek() {
      this.weekStart = addDays(this.weekStart, -7);
      this.mapDay = null;
      await this.loadWeek(isoWeekOf(this.weekStart));
      this.cleanupFilters();
      this.$nextTick(() => requestAnimationFrame(() => this.updateScrollSpy()));
    },
    async nextWeek() {
      this.weekStart = addDays(this.weekStart, +7);
      this.mapDay = null;
      await this.loadWeek(isoWeekOf(this.weekStart));
      this.cleanupFilters();
      this.$nextTick(() => requestAnimationFrame(() => this.updateScrollSpy()));
    },

    // Week ids whose events are already in this.events (not reactive state).
    markWeeksLoaded(fromIso, toIso) {
      if (!this._loadedWeeks) this._loadedWeeks = new Set();
      const [y, m, d] = fromIso.split('-').map(Number);
      for (let day = new Date(y, m - 1, d); isoDay(day) < toIso; day = addDays(day, 7)) {
        this._loadedWeeks.add(isoWeekOf(day));
      }
    },

    // Fetch one week file and merge its events (deduplicated by id).
    async loadWeek(week) {
      if (!this._loadedWeeks) this._loadedWeeks = new Set();
      if (this._loadedWeeks.has(week)) return;
      this._loadedWeeks.add(week);
      if (this.weekIndex && !(week in this.weekIndex)) return;  // no events that week
      const gen = this._dataGen;
      const loaded = this._loadedWeeks;
      try {
        const res = await fetch(`${this.apiBase}/events/${week}.json`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (gen !== this._dataGen) return;  // the city changed meanwhile
        const have = new Set(this.events.map(e => eventKey(e)));
        const fresh = data.events.filter(e => !have.has(eventKey(e)));
        if (fresh.length) this.events = this.events.concat(fresh);
        this.cleanupFavorites();
      } catch (err) {
        loaded.delete(week);  // retry on the next visit to this week
        console.warn('Failed to load week', week, err);
      }
    },
    goToday() {
      this.weekStart = weekMonday(madridToday());
      this.mapDay = null;
      this.cleanupFilters();
      // In vertical view scroll to today after Alpine re-renders the week
      if (this.viewMode === 'vertical') {
        this.$nextTick(() => this.scrollToDay(readMadridNow().iso));
      } else {
        this.$nextTick(() => this.scrollColumnIntoView(readMadridNow().iso));
      }
    },

    /* ── Faceted filter options ─────────────────────────────────────── */
    get allStores() {
      // Registry stores plus any store seen in the loaded weeks.
      const values = new Set(Object.keys(STORE_META));
      for (const e of this.events) {
        const value = this.valueFor('store', e);
        if (value) values.add(value);
      }
      return [...values].sort();
    },
    get gameOptions() {
      return this.availableOptions('game');
    },
    get storeOptions() {
      return this.allStores;
    },
    get formatOptions() {
      return this.availableOptions('format');
    },

    valueFor(field, e) {
      if (field === 'game') return e.game || 'Unknown';
      if (field === 'store') return e.store || 'Unknown';
      if (field === 'format') return e.format || 'Unknown';
      return '';
    },

    selectedValues(field) {
      return Array.isArray(this.filters[field]) ? this.filters[field] : [];
    },

    isSelected(field, value) {
      return this.selectedValues(field).includes(value);
    },

    toggleFacetPanel(field) {
      this.openFacet = this.openFacet === field ? null : field;
    },

    clearFacet(field) {
      this._snapshotFilters();
      this.filters[field] = [];
      this.cleanupFilters({ syncUrl: true });
    },

    loadPresets() {
      try {
        const raw = localStorage.getItem(PRESETS_STORAGE_KEY);
        if (!raw) {
          this.savedPresets = [];
          return;
        }
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) {
          this.savedPresets = [];
          return;
        }
        this.savedPresets = parsed
          .filter(p => p && typeof p === 'object')
          .map(p => ({
            id: String(p.id || ''),
            name: String(p.name || '').trim(),
            filters: {
              game: Array.isArray(p.filters && p.filters.game) ? p.filters.game.filter(Boolean) : [],
              store: Array.isArray(p.filters && p.filters.store) ? p.filters.store.filter(Boolean) : [],
              format: Array.isArray(p.filters && p.filters.format) ? p.filters.format.filter(Boolean) : [],
            },
          }))
          .filter(p => p.id && p.name);
      } catch (err) {
        console.warn('Failed to load saved presets', err);
        this.savedPresets = [];
      }
    },

    persistPresets() {
      try {
        localStorage.setItem(PRESETS_STORAGE_KEY, JSON.stringify(this.savedPresets));
      } catch (err) {
        console.warn('Failed to save presets', err);
      }
    },

    /* ── Favorites ──────────────────────────────────────────────────── */

    loadFavorites() {
      try {
        const weeks = JSON.parse(localStorage.getItem(this.favoritesKey(FAVORITE_WEEKS_STORAGE_KEY)) || '{}');
        this._favWeeks = (weeks && typeof weeks === 'object') ? weeks : {};
      } catch { this._favWeeks = {}; }
      try {
        const raw = localStorage.getItem(this.favoritesKey());
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed))
          this.favorites = parsed.filter(k => typeof k === 'string' && k.length > 0);
      } catch { this.favorites = []; }
    },

    persistFavorites() {
      try {
        const weeks = {};
        for (const k of this.favorites) if (this._favWeeks && this._favWeeks[k]) weeks[k] = this._favWeeks[k];
        this._favWeeks = weeks;
        localStorage.setItem(this.favoritesKey(FAVORITE_WEEKS_STORAGE_KEY), JSON.stringify(weeks));
        localStorage.setItem(this.favoritesKey(), JSON.stringify(this.favorites));
      } catch (err) {
        console.warn('Failed to save favorites', err);
      }
    },

    isFavorite(e) {
      if (!e) return false;
      return this.favorites.includes(this.eventKey(e));
    },

    toggleFavorite(keyOrEvent) {
      const key = typeof keyOrEvent === 'string' ? keyOrEvent : this.eventKey(keyOrEvent);
      const idx = this.favorites.indexOf(key);
      if (idx >= 0) this.favorites.splice(idx, 1);
      else          this.favorites.push(key);
      if (typeof keyOrEvent !== 'string' && keyOrEvent.datetime_start) {
        if (!this._favWeeks) this._favWeeks = {};
        this._favWeeks[key] = weekOfIso(keyOrEvent.datetime_start);
      }
      this.persistFavorites();
    },

    get favoritesCount() {
      return this.favorites.length;
    },

    // Prune keys for events no longer present in events.json
    cleanupFavorites() {
      const allKeys = new Set(this.events.map(e => eventKey(e)));
      const byLegacy = new Map();
      for (const e of this.events) {
        const lk = legacyEventKey(e);
        if (!byLegacy.has(lk)) byLegacy.set(lk, []);
        byLegacy.get(lk).push(eventKey(e));
      }
      // Events load per week, so a key not found may just be in a week not
      // loaded yet. A key is dropped only when its week is known and either
      // loaded (the event is gone) or absent from the index (no events).
      if (!this._favWeeks) this._favWeeks = {};
      const loadedWeeks = this._loadedWeeks || new Set();
      const weekOfKey = (key) => {
        if (this._favWeeks[key]) return this._favWeeks[key];
        const m = key.match(/\d{4}-\d{2}-\d{2}T/);
        return m ? weekOfIso(m[0]) : null;
      };
      const next = new Set();
      for (const key of this.favorites) {
        if (allKeys.has(key)) { next.add(key); continue; }
        if (byLegacy.has(key)) {                              // migrate old keys
          for (const k of byLegacy.get(key)) next.add(k);
          continue;
        }
        const week = weekOfKey(key);
        const gone = week && (loadedWeeks.has(week) || (this.weekIndex && !(week in this.weekIndex)));
        if (!gone) next.add(key);
      }
      for (const e of this.events) {                          // remember weeks
        const k = eventKey(e);
        if (next.has(k)) this._favWeeks[k] = weekOfIso(e.datetime_start);
      }
      const changed = next.size !== this.favorites.length
        || this.favorites.some(k => !next.has(k));
      this.favorites = [...next];
      if (changed) this.persistFavorites();
    },

    saveCurrentPreset() {
      const filters = {
        game: this.selectedValues('game').slice(),
        store: this.selectedValues('store').slice(),
        format: this.selectedValues('format').slice(),
      };
      if (!filters.game.length && !filters.store.length && !filters.format.length) {
        window.alert('Choose at least one game, store, or format before saving a preset.');
        return;
      }

      const name = window.prompt('Preset name');
      const trimmedName = name ? name.trim() : '';
      if (!trimmedName) return;

      const existing = this.savedPresets.find(p => p.name === trimmedName);
      if (existing) {
        if (!window.confirm(`Replace preset "${trimmedName}"?`)) return;
        existing.filters = filters;
        this.persistPresets();
        return;
      }

      this.savedPresets.push({
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
        name: trimmedName,
        filters,
      });
      this.persistPresets();
    },

    startEditPreset(preset) {
      if (this.editingPresetId) return;
      if (!preset || !preset.id) return;

      this.editingPresetSnapshot = {
        filters: {
          search: this.filters.search,
          game: this.filters.game.slice(),
          store: this.filters.store.slice(),
          format: this.filters.format.slice(),
        },
        defaultPresetActive: this.defaultPresetActive,
      };

      this.editingPresetId = preset.id;
      this.editingPresetName = preset.name;

      this.filters.game = Array.isArray(preset.filters.game) ? preset.filters.game.slice() : [];
      this.filters.store = Array.isArray(preset.filters.store) ? preset.filters.store.slice() : [];
      this.filters.format = Array.isArray(preset.filters.format) ? preset.filters.format.slice() : [];
      this.defaultPresetActive = false;
      this.openFacet = null;
      this.cleanupFilters({ syncUrl: true });
    },

    saveEditedPreset() {
      const preset = this.savedPresets.find(p => p.id === this.editingPresetId);
      if (!preset) {
        this.cancelEditPreset();
        return;
      }

      const trimmedName = String(this.editingPresetName || '').trim();
      if (!trimmedName) return;

      preset.name = trimmedName;
      preset.filters = {
        game: this.selectedValues('game').slice(),
        store: this.selectedValues('store').slice(),
        format: this.selectedValues('format').slice(),
      };

      this.persistPresets();
      this.editingPresetId = null;
      this.editingPresetSnapshot = null;
      this.editingPresetName = '';
    },

    cancelEditPreset() {
      if (this.editingPresetSnapshot) {
        this.filters = this.editingPresetSnapshot.filters;
        this.defaultPresetActive = this.editingPresetSnapshot.defaultPresetActive;
      }
      this.editingPresetId = null;
      this.editingPresetSnapshot = null;
      this.editingPresetName = '';
      this.cleanupFilters({ syncUrl: true });
    },

    applyPreset(preset) {
      if (!preset || !preset.filters) return;
      this._snapshotFilters();
      this.filters.game = Array.isArray(preset.filters.game) ? preset.filters.game.slice() : [];
      this.filters.store = Array.isArray(preset.filters.store) ? preset.filters.store.slice() : [];
      this.filters.format = Array.isArray(preset.filters.format) ? preset.filters.format.slice() : [];
      this.openFacet = null;
      this.defaultPresetActive = (preset.id === this.defaultPresetId);
      this.cleanupFilters({ syncUrl: true });
    },

    deletePreset(id) {
      this.savedPresets = this.savedPresets.filter(p => p.id !== id);
      this.persistPresets();
      if (this.defaultPresetId === id) {
        this.defaultPresetId = null;
        this.persistDefaultPreset();
      }
    },

    loadDefaultPreset() {
      try {
        const raw = localStorage.getItem(DEFAULT_PRESET_STORAGE_KEY);
        if (!raw) {
          this.defaultPresetId = null;
          return;
        }
        const parsed = JSON.parse(raw);
        this.defaultPresetId = (parsed && parsed.id) ? String(parsed.id) : null;
      } catch (err) {
        console.warn('Failed to load default preset', err);
        this.defaultPresetId = null;
      }
    },

    persistDefaultPreset() {
      try {
        if (this.defaultPresetId) {
          localStorage.setItem(DEFAULT_PRESET_STORAGE_KEY, JSON.stringify({ id: this.defaultPresetId }));
        } else {
          localStorage.removeItem(DEFAULT_PRESET_STORAGE_KEY);
        }
      } catch (err) {
        console.warn('Failed to persist default preset', err);
      }
    },

    /* ── Onboarding ────────────────────────────────────────────────── */
    loadOnboardingState() {
      try {
        const raw = localStorage.getItem(ONBOARDING_STORAGE_KEY);
        if (!raw) {
          this.onboardingCompleted = false;
          return;
        }
        const parsed = JSON.parse(raw);
        this.onboardingCompleted = parsed && parsed.completed === true;
      } catch (err) {
        console.warn('Failed to load onboarding state', err);
        this.onboardingCompleted = false;
      }
    },

    persistOnboardingComplete() {
      try {
        localStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify({ completed: true }));
        this.onboardingCompleted = true;
      } catch (err) {
        console.warn('Failed to persist onboarding state', err);
      }
    },

    shouldShowOnboarding(hasUrlFilters) {
      if (hasUrlFilters) return false;
      if (this.onboardingCompleted) return false;
      if (this.savedPresets.length > 0) return false;
      if (this.defaultPresetId) return false;
      return true;
    },

    openOnboarding() {
      this.onboardingOpen = true;
      this.onboarding = { game: [], store: [] };
    },

    skipOnboarding() {
      this.onboardingOpen = false;
      this.persistOnboardingComplete();
    },

    completeOnboarding() {
      const hasSelections = this.onboarding.game.length > 0 || this.onboarding.store.length > 0;
      if (!hasSelections) {
        this.skipOnboarding();
        return;
      }

      this.filters.game = this.onboarding.game.slice();
      this.filters.store = this.onboarding.store.slice();
      this.filters.format = [];
      this.openFacet = null;
      this.cleanupFilters({ syncUrl: false });

      const presetName = this.generateDefaultPresetName();
      const preset = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
        name: presetName,
        filters: {
          game: this.onboarding.game.slice(),
          store: this.onboarding.store.slice(),
          format: [],
        },
      };
      this.savedPresets.push(preset);
      this.persistPresets();

      this.defaultPresetId = preset.id;
      this.persistDefaultPreset();
      this.defaultPresetActive = true;

      this.persistOnboardingComplete();
      this.onboardingOpen = false;
    },

    toggleOnboardingValue(field, value) {
      const arr = this.onboarding[field];
      const idx = arr.indexOf(value);
      if (idx >= 0) {
        arr.splice(idx, 1);
      } else {
        arr.push(value);
      }
    },

    generateDefaultPresetName() {
      const existingNames = new Set(this.savedPresets.map(p => p.name));
      let name = 'My default';
      if (!existingNames.has(name)) return name;
      let counter = 2;
      while (existingNames.has(`${name} ${counter}`)) {
        counter++;
      }
      return `${name} ${counter}`;
    },

    get onboardingGameOptions() {
      return this.gameOptions.filter(g => g !== 'Unknown');
    },

    setDefaultPreset(id) {
      if (this.defaultPresetId === id) {
        this.defaultPresetId = null;
      } else {
        this.defaultPresetId = id;
      }
      this.persistDefaultPreset();
    },

    applyDefaultPreset() {
      if (!this.defaultPresetId) return;
      const preset = this.savedPresets.find(p => p.id === this.defaultPresetId);
      if (!preset) {
        this.defaultPresetId = null;
        this.persistDefaultPreset();
        return;
      }
      if (!preset.filters) return;
      this.filters.game = Array.isArray(preset.filters.game) ? preset.filters.game.slice() : [];
      this.filters.store = Array.isArray(preset.filters.store) ? preset.filters.store.slice() : [];
      this.filters.format = Array.isArray(preset.filters.format) ? preset.filters.format.slice() : [];
      this.openFacet = null;
      this.defaultPresetActive = true;
      // Intentionally do NOT sync URL on default preset application.
    },

    facetSummary(field, fallback) {
      const values = this.selectedValues(field);
      if (!values.length) return fallback;
      if (values.length === 1) return values[0];
      if (values.length === 2) return `${values[0]} + ${values[1]}`;
      return `${values.length} ${this.facetPlural(field)}`;
    },

    facetPlural(field) {
      return field === 'game' ? 'games' : field === 'store' ? 'stores' : 'formats';
    },

    availableOptions(field) {
      const values = new Set();
      for (const e of this.events) {
        // Location is a view filter, not a facet: options (and the cleanup of
        // selected values, which uses this list) ignore it, so turning "Near
        // me" on never silently drops the user's game/format selections.
        if (this.eventMatches(e, { ignore: field, includeWeek: true, ignoreFavorites: true, ignoreNearMe: true })) {
          values.add(this.valueFor(field, e));
        }
      }
      return [...values].sort();
    },

    filteredFacetOptions(field) {
      const options = field === 'store' ? this.storeOptions : this.availableOptions(field);
      const query = String(this.filterSearch[field] || '').toLowerCase().trim();
      if (!query) return options;
      return options.filter(opt => String(opt).toLowerCase().includes(query));
    },

    facetOptionCount(field, value) {
      let count = 0;
      for (const e of this.events) {
        if (
          this.valueFor(field, e) === value &&
          this.eventMatches(e, { ignore: field, includeWeek: true })
        ) {
          count++;
        }
      }
      return count;
    },

    /* ── Bookmarkable filter URLs ───────────────────────────────────── */
    syncFiltersToUrl() {
      const url = new URL(window.location.href);
      for (const field of ['game', 'store', 'format']) {
        const values = this.selectedValues(field);
        if (values.length) {
          url.searchParams.set(field, values.join(','));
        } else {
          url.searchParams.delete(field);
        }
      }
      history.replaceState(null, '', url.toString());
    },

    applyFiltersFromUrl() {
      const params = new URLSearchParams(window.location.search);
      // Stores: any registry store; games/formats: what the loaded weeks have.
      const valid = { game: new Set(), store: new Set(this.allStores), format: new Set() };
      for (const e of this.events) {
        valid.game.add(this.valueFor('game', e));
        valid.store.add(this.valueFor('store', e));
        valid.format.add(this.valueFor('format', e));
      }
      for (const field of ['game', 'store', 'format']) {
        const raw = params.get(field);
        if (!raw) {
          this.filters[field] = [];
          continue;
        }
        const values = [...new Set(raw.split(',').map(v => v.trim()).filter(Boolean))];
        this.filters[field] = values.filter(v => valid[field].has(v));
      }
    },

    cleanupFilters({ syncUrl = false } = {}) {
      for (const field of ['game', 'store', 'format']) {
        const allowed = field === 'store'
          ? new Set(this.allStores)
          : new Set(this.availableOptions(field));
        const next = this.selectedValues(field).filter(v => allowed.has(v));
        if (next.length !== this.selectedValues(field).length) {
          this.filters[field] = next;
        }
      }
      // Map: a day chip the filters have emptied falls back to the week.
      if (this.mapDay && !this.mapEvents.length) this.mapDay = null;
      if (syncUrl) this.syncFiltersToUrl();
    },

    /* ── Toggle a filter chip (game/store/format) ───────────────────── */
    // If selected, remove it; otherwise add it to the facet group.
    filterFromPanel(field, value) {
      if (!value) return;
      this.closePanel();
      setTimeout(() => {
        this.toggleFilter(field, value);
      }, 220);
    },

    /* ── Filter undo history ─────────────────────────────────────── */
    _snapshotFilters() {
      this.filterHistory.push({
        filters: {
          search: this.filters.search,
          game: this.filters.game.slice(),
          store: this.filters.store.slice(),
          format: this.filters.format.slice(),
        },
        segmentFilter: { ...this.segmentFilter },
      });
      if (this.filterHistory.length > 5) this.filterHistory.shift();
    },

    undoFilterAction() {
      const snapshot = this.filterHistory.pop();
      if (!snapshot) return;
      this.filters.search = snapshot.filters.search;
      this.filters.game = snapshot.filters.game;
      this.filters.store = snapshot.filters.store;
      this.filters.format = snapshot.filters.format;
      this.segmentFilter = { ...snapshot.segmentFilter };
      this.defaultPresetActive = false;
      this.cleanupFilters({ syncUrl: true });
    },

    toggleFilter(field, value) {
      if (!value) return;
      this._snapshotFilters();
      if (this.defaultPresetActive) {
        this.filters[field] = [value];
        this.defaultPresetActive = false;
        this.cleanupFilters({ syncUrl: true });
        return;
      }
      const values = this.selectedValues(field).slice();
      const idx = values.indexOf(value);
      if (idx >= 0) values.splice(idx, 1);
      else values.push(value);
      this.filters[field] = values;
      this.cleanupFilters({ syncUrl: true });
    },

    /* ── Toggle a time-segment chip ─────────────────────────────────── */
    toggleSegment(key) {
      this._snapshotFilters();
      this.segmentFilter[key] = !this.segmentFilter[key];
      this.cleanupFilters();
    },

    /* ── Toggle a segment's collapsed state (cards hidden, header
         visible). Independent of segmentFilter — collapse keeps the
         row in the grid so alignment is preserved. */
    toggleSegmentCollapse(key) {
      this.collapsedSegments[key] = !this.collapsedSegments[key];
    },

    isDaySegCollapsed(iso, key) {
      return !!this.collapsedDaySegs[iso + ':' + key];
    },
    toggleDaySegCollapse(iso, key) {
      const k = iso + ':' + key;
      this.collapsedDaySegs[k] = !this.collapsedDaySegs[k];
    },

    /* ── For today's column only: a segment is "past" iff its end
         time has already elapsed. Otherwise it's upcoming (no class).
         Returns false for non-today columns. */
    isSegmentPast(segKey, dayIso) {
      if (dayIso !== this.nowMadrid.iso) return false;
      const s = SEGMENTS.find(x => x.key === segKey);
      if (!s) return false;
      return (s.end * 60) <= this.nowMadrid.minutesSinceMidnight;
    },

    /* ── True if the event already started (Europe/Madrid).
         Compares full datetime: any earlier day is past; on the
         same day, falls through to a time-of-day check. */
    isEventPast(e) {
      if (!e || !e.datetime_start) return false;
      const dayPart = e.datetime_start.slice(0, 10);
      if (dayPart < this.nowMadrid.iso) return true;
      if (dayPart > this.nowMadrid.iso) return false;
      const h = parseInt(e.datetime_start.slice(11, 13), 10);
      const m = parseInt(e.datetime_start.slice(14, 16), 10);
      return (h * 60 + m) < this.nowMadrid.minutesSinceMidnight;
    },

    /* ── Bucket an event ISO string into a segment key ──────────────── */
    segmentOf(iso) {
      return segmentForHour(parseInt(iso.slice(11, 13), 10));
    },

    // Compare Madrid calendar dates as strings (same as weekDays bucketing),
    // not instants against the browser's local midnight.
    isInCurrentWeek(e) {
      const day = e.datetime_start.slice(0, 10);
      return day >= isoDay(this.weekStart) && day < isoDay(addDays(this.weekStart, 7));
    },

    eventMatches(e, opts = {}) {
      // Past events are never hidden (only dimmed). Only suppress inactive future events
      // (they are awaiting hard-deletion after 3 missed runs in the aggregator).
      if (e.is_active === false && !this.isEventPast(e)) return false;
      const ignore = opts.ignore || null;
      const includeSegment = opts.includeSegment !== false;
      const includeWeek = opts.includeWeek === true;
      const s = this.filters.search.toLowerCase().trim();
      if (s && !e.title.toLowerCase().includes(s)) return false;
      if (this.showFavoritesOnly && !opts.ignoreFavorites && !this.isFavorite(e)) return false;
      if (this.nearMe && this.radiusKm && !opts.ignoreNearMe) {
        const km = this.storeDistanceKm(e.store);
        if (km === null || km > this.radiusKm) return false;
      }
      if (includeWeek && !this.isInCurrentWeek(e)) return false;
      if (includeSegment && !this.segmentFilter[this.segmentOf(e.datetime_start)]) return false;
      for (const field of ['game', 'store', 'format']) {
        if (ignore === field) continue;
        const selected = this.selectedValues(field);
        const value = this.valueFor(field, e);
        // Default-hide Unknown game events unless the user explicitly selected them.
        if (field === 'game' && value === 'Unknown' && !selected.includes('Unknown')) return false;
        if (selected.length && !selected.includes(value)) return false;
      }
      return true;
    },

    /* ── Filtered event list (search/game/store/format/segment) ─────── */
    get filteredEvents() {
      return this.events.filter(e => this.eventMatches(e));
    },

    /* ── Segments to render as global rows in horizontal view.
         Order is fixed (Morning → Afternoon → Evening → Late).
         A segment is included only if user-enabled AND has at least
         one event in the current week (after non-segment filters). */
    get activeSegments() {
      const totals = this.segmentTotals;
      return SEGMENTS.filter(s => this.segmentFilter[s.key] && totals[s.key] > 0);
    },

    /* ── Focus mode: only one segment is active → drop segment headers
         from cells so the layout reads as a flat list per day. */
    get focusMode() {
      return this.activeSegments.length === 1;
    },

    /* ── Lookup events for a specific (day, segment) cell. ─────────── */
    cellEvents(day, segKey) {
      return (day.segmentMap && day.segmentMap[segKey]) || [];
    },

    /* ── Flat cell list for the horizontal grid.
         Two flat lists are returned by separate getters:
           cells       — one per (segment, day) pair, used to render
                         placeholders/headers placed via grid-row/col.
           cellsAndCards — Alpine refused to wire a nested
                         <template x-for> in the cell content
                         (the inner template stayed unprocessed),
                         so we iterate events at the same outer
                         level and place each card into its
                         corresponding grid cell via inline style.
                         This avoids any template nesting. */
    get cells() {
      const segs = this.activeSegments;
      const days = this.weekDays;
      const out = [];
      for (let s = 0; s < segs.length; s++) {
        for (let d = 0; d < days.length; d++) {
          const seg = segs[s];
          const day = days[d];
          const events = (day.segmentMap && day.segmentMap[seg.key]) || [];
          out.push({
            key:     seg.key + '-' + day.iso,
            // x-for key: changes whenever the cell's events do, so Alpine
            // rebuilds the cell instead of refreshing its scope. A scope
            // refresh alone can leave the x-html cards stale: Alpine 3's
            // scheduler won't re-queue an effect that already ran in the
            // current flush, so when events and filters change in one tick
            // (startup with ?game=… or a default preset) the cards kept
            // showing the unfiltered week.
            renderKey: seg.key + '-' + day.iso + ':' + events.map(e => e.id).join(','),
            seg, day, sIdx: s, dIdx: d,
            events,
            isEmpty: events.length === 0,
            isToday: day.isToday,
            isPast:  this.isSegmentPast(seg.key, day.iso),
          });
        }
      }
      return out;
    },

    /* ── Total events per segment in the current week (any filter
         applied EXCEPT segment filter — so chip counts stay stable) ── */
    get segmentTotals() {
      const totals = { morning: 0, afternoon: 0, evening: 0, late: 0 };
      for (const e of this.events) {
        if (!this.eventMatches(e, { includeSegment: false, includeWeek: true })) continue;
        totals[this.segmentOf(e.datetime_start)]++;
      }
      return totals;
    },

    /* ── 7-day array for the current week ───────────────────────────── */
    get weekDays() {
      const todayISO = this.nowMadrid ? this.nowMadrid.iso : readMadridNow().iso;
      const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

      // Build a map: ISO date → sorted events
      const byDay = {};
      for (const e of this.filteredEvents) {
        const day = e.datetime_start.slice(0, 10);
        if (!byDay[day]) byDay[day] = [];
        byDay[day].push(e);
      }

      const days = [];
      for (let i = 0; i < 7; i++) {
        const d = addDays(this.weekStart, i);
        const iso = isoDay(d);
        const evs = (byDay[iso] || []).slice().sort(
          (a, b) => a.datetime_start.localeCompare(b.datetime_start)
        );

        // Bucket events into segments. The map is exhaustive (one entry
        // per segment, possibly empty) so the horizontal grid can ask
        // "is this (day, segment) cell empty?" without a re-scan.
        const buckets = { morning: [], afternoon: [], evening: [], late: [] };
        for (const e of evs) buckets[this.segmentOf(e.datetime_start)].push(e);
        // Vertical view still renders segments per-day, so we also keep
        // the compact array (empties skipped) it expects.
        const segments = SEGMENTS
          .filter(s => buckets[s.key].length > 0)
          .map(s => ({
            key: s.key,
            label: s.label,
            shortRange: s.shortRange,
            count: buckets[s.key].length,
            events: buckets[s.key],
          }));

        days.push({
          iso,
          dow: DOW_LABELS[i],
          dom: d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }),
          num: d.getDate(),
          isToday: iso === todayISO,
          events: evs,
          segments,
          segmentMap: buckets,
        });
      }
      return days;
    },

    /* ── Map view ─────────────────────────────────────────────────────
         The map answers "where can I go": this week's filtered events that
         haven't started yet, grouped by store. */
    get mapDayChips() {
      return this.weekDays.map(d => ({
        iso: d.iso, dow: d.dow, num: d.num, isToday: d.isToday,
        count: d.events.filter(e => !this.isEventPast(e)).length,
      }));
    },

    get mapEvents() {
      const days = this.weekDays.filter(d => !this.mapDay || d.iso === this.mapDay);
      return days.flatMap(d => d.events).filter(e => !this.isEventPast(e));
    },

    get mapStores() {
      const byStore = new Map();
      for (const e of this.mapEvents) {
        if (!byStore.has(e.store)) byStore.set(e.store, []);
        byStore.get(e.store).push(e);
      }
      const stores = [];
      for (const [name, events] of byStore) {
        const meta = STORE_META[name] || {};
        const games = {};
        for (const e of events) games[e.game] = (games[e.game] || 0) + 1;
        const ranked = Object.keys(games).sort((a, b) => games[b] - games[a]);
        stores.push({
          name,
          events,
          count: events.length,
          games: ranked,
          // One game dominates → the pin takes its colour; a mix stays neutral.
          gameClass: ranked.length && games[ranked[0]] * 2 > events.length ? this.getGameClass(ranked[0]) : 'game-mixed',
          lat: meta.lat ?? null,
          lng: meta.lng ?? null,
          address: meta.address || events[0].store_address || '',
          website: safeUrl(meta.website),
          km: this.storeDistanceKm(name),
        });
      }
      const byKm = (a, b) => (a.km ?? Infinity) - (b.km ?? Infinity);
      return stores.sort((a, b) => (this.nearMe ? byKm(a, b) : 0)
        || b.count - a.count || a.name.localeCompare(b.name));
    },

    get mapSelected() {
      return this.mapStore ? this.mapStores.find(s => s.name === this.mapStore) || null : null;
    },

    get mapUnlocatedCount() {
      return this.mapStores.filter(s => s.lat == null).reduce((n, s) => n + s.count, 0);
    },

    // Changes only when the question changes; see renderStoreMap.
    get mapFitKey() {
      // events.length: a week fetched after navigation must refit when it lands.
      return JSON.stringify([this.city, isoDay(this.weekStart), this.mapDay, this.filters, this.segmentFilter,
        this.showFavoritesOnly, this.nearMe, this.radiusKm, this.events.length]);
    },

    // Called from x-effect. Every reactive input is read synchronously so the
    // effect re-runs when any of them changes; Leaflet work waits a frame so
    // x-show has made the container visible and measurable.
    drawMap() {
      const args = {
        stores: this.mapStores.map(s => ({ name: s.name, count: s.count, gameClass: s.gameClass, lat: s.lat, lng: s.lng })),
        selected: this.mapStore,
        me: this.nearMe ? { lat: this.nearMe.lat, lng: this.nearMe.lng } : null,
        radiusKm: this.radiusKm,
        fitKey: this.mapFitKey,
        onSelect: (name) => { this.mapStore = name; },
        centre: this.cities.find(c => c.id === this.city) || MAP_DEFAULT_VIEW,
      };
      this.mapAttempt;  // dependency only: Retry re-runs this effect
      const fail = () => {
        this.mapFailed = true;
        this.mapStatus = 'Could not load the map. Check your connection.';
      };
      if (!MAP.map) this.mapStatus = 'Loading map…';
      loadLeaflet().then((L) => requestAnimationFrame(() => {
        if (this.viewMode !== 'map' || !this.$refs.mapEl) return;
        try {
          renderStoreMap(L, this.$refs.mapEl, args);
          this.mapFailed = false;
          this.mapStatus = '';
        } catch (err) {
          console.error('Map render failed', err);
          fail();
        }
      })).catch((err) => {
        console.warn(err);
        fail();
      });
    },

    setMapDay(iso) {
      this.mapDay = iso;
      this.mapStore = null;
    },

    // From the list under the map: select, centre the pin, show the map.
    pickMapStore(s) {
      // Read the row first: setting mapStore re-renders the list, and the
      // x-for scope `s` can then point at another row's data.
      const { name, lat } = s;
      if (lat != null) MAP.focus = name;  // the redraw this triggers shows the pin
      this.mapStore = name;
      // Instant: a smooth page scroll cancels Leaflet's zoom animation.
      if (lat != null && this.$refs.mapEl) this.$refs.mapEl.scrollIntoView({ block: 'start' });
    },

    directionsUrl(s) {
      const dest = s.lat != null ? `${s.lat},${s.lng}` : (s.address || s.name);
      return 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(dest);
    },

    formatDayShort(iso) {
      const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
      return new Date(y, m - 1, d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric' });
    },

    /* ── Week header label ──────────────────────────────────────────── */
    get weekRangeLabel() {
      const sun = addDays(this.weekStart, 6);
      const from = this.weekStart.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
      const to   = sun.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
      return `${from} – ${to}`;
    },

    /* ── Events this week (after filters) ───────────────────────────── */
    get weekEventCount() {
      return this.weekDays.reduce((n, d) => n + d.events.length, 0);
    },

    /* ── Formatters ────────────────────────────────────────────────── */
    formatTime(iso) {
      if (!iso) return '';
      return new Date(iso).toLocaleTimeString('en-GB', {
        hour: '2-digit', minute: '2-digit',
        timeZone: 'Europe/Madrid',
      });
    },

    openEvent(e) {
      this.openPanel(e);
    },

    openPanel(e) {
      if (this._panelCloseTimeout) {
        clearTimeout(this._panelCloseTimeout);
        this._panelCloseTimeout = null;
      }
      this.selectedEvent = e;
      this.panelOpen = true;
      this.calendarMenuOpen = false;
      document.body.classList.add('no-scroll');
      this.syncEventParam(e);
    },

    closePanel() {
      this.panelOpen = false;
      this.calendarMenuOpen = false;
      document.body.classList.remove('no-scroll');
      this.clearEventParam();
      if (this._panelCloseTimeout) {
        clearTimeout(this._panelCloseTimeout);
      }
      this._panelCloseTimeout = setTimeout(() => {
        if (!this.panelOpen) {
          this.selectedEvent = null;
        }
        this._panelCloseTimeout = null;
      }, 200);
    },

    formatDomain(url) {
      if (!url || typeof url !== 'string') return '';
      try {
        const u = new URL(url);
        return u.hostname.replace(/^www\./, '');
      } catch {
        return url;
      }
    },

    formatDateLong(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      const datePart = d.toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'short',
        timeZone: 'Europe/Madrid',
      });
      const weekday = d.toLocaleDateString('en-GB', {
        weekday: 'long',
        timeZone: 'Europe/Madrid',
      });
      return datePart + ' · ' + weekday;
    },

    formatTimeRange(e) {
      if (!e || !e.datetime_start) return '';
      const start = this.formatTime(e.datetime_start);
      if (e.datetime_end) return start + ' – ' + this.formatTime(e.datetime_end);
      return '<span class="panel-time-prefix">Starts at</span> ' + start;
    },

    canAddToCalendar(e) {
      return Boolean(buildGoogleCalendarUrl(e));
    },

    openCalendar(e) {
      const url = buildGoogleCalendarUrl(e);
      if (url) window.open(url, '_blank', 'noopener');
    },

    openCalendarProvider(e, provider) {
      this.calendarMenuOpen = false;
      if (!e || !e.datetime_start) return;

      switch (provider) {
        case 'google': {
          const url = buildGoogleCalendarUrl(e);
          if (url) window.open(url, '_blank', 'noopener');
          break;
        }
        case 'apple': {
          downloadIcs(e);
          break;
        }
        case 'outlook': {
          const url = buildOutlookUrl(e, 'https://outlook.live.com/calendar/0/deeplink/compose');
          if (url) window.open(url, '_blank', 'noopener');
          break;
        }
      }
    },

    eventKey(e) {
      return eventKey(e);
    },

    eventUrl(e) {
      if (!e) return '';
      // Prefer the event's static share page: chat apps can't run JS, so
      // only that page gives them a real preview (title, date, image).
      if (e.share) return new URL(e.share, window.location.origin).toString();
      const url = new URL(window.location.href);
      url.searchParams.set('event', encodeEventParam(this.eventKey(e)));
      url.searchParams.set('w', weekOfIso(e.datetime_start));  // which week file has it
      return url.toString();
    },

    syncEventParam(e) {
      if (!e) return;
      const url = new URL(window.location.href);
      url.searchParams.set('event', encodeEventParam(this.eventKey(e)));
      url.searchParams.set('w', weekOfIso(e.datetime_start));
      history.replaceState(null, '', url.toString());
    },

    clearEventParam() {
      const url = new URL(window.location.href);
      url.searchParams.delete('event');
      url.searchParams.delete('w');
      history.replaceState(null, '', url.toString());
    },

    copyEventLink(e) {
      if (!e) return;
      const text = this.eventUrl(e);
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(() => this.fallbackCopy(text));
      } else {
        this.fallbackCopy(text);
      }
    },

    fallbackCopy(text) {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
      } catch (err) {
        console.error('Failed to copy event link', err);
      }
      document.body.removeChild(ta);
    },


    shareEvent(e) {
      if (!e) return;
      const url = this.eventUrl(e);
      if (navigator.share) {
        navigator.share({
          title: e.title || 'Event',
          url: url,
        }).catch(() => {
          this.copyEventLink(e);
        });
      } else {
        this.copyEventLink(e);
      }
    },

    /* ── Card HTML for a horizontal grid cell.
         Built as a string and injected via x-html on the cell's
         .seg-cards div. We do this because Alpine's nested
         <template x-for> inside the per-cell clones produced by the
         outer x-for left the inner template unprocessed (the inner
         x-for ran once with an empty array and never re-fired even
         after events.json finished loading). x-html re-evaluates
         reactively whenever cell.events changes, so the cards stay
         in sync. Click handling is delegated at the grid level. */
    cellCardsHtml(cell) {
      const events = this.cellEvents(cell.day, cell.seg.key);
      if (!events.length) return '';
      const esc = s => String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
      const out = [];
      for (const e of events) {
        const game = e.game || 'Unknown';
        const fmt = e.format || 'Unknown';
        const gameClass = this.getGameClass(e.game);
        const past = this.isEventPast(e);
        const time = this.formatTime(e.datetime_start);
        const url = safeUrl(e.source_url) || '';
        const calendarUrl = buildGoogleCalendarUrl(e);
        const calendarButton = calendarUrl
          ? `<button class="calendar-btn" type="button" data-calendar-url="${esc(calendarUrl)}" title="Add to Google Calendar" aria-label="Add to Google Calendar">` +
              `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
                `<path d="M8 2v4"></path><path d="M16 2v4"></path><rect x="3" y="4" width="18" height="18" rx="2"></rect><path d="M3 10h18"></path>` +
              `</svg>` +
              `<span>GCal</span>` +
            `</button>`
          : '';
        const isFav = this.favorites.includes(eventKey(e));
        const favButton = `<button class="fav-btn${isFav ? ' is-fav' : ''}" type="button" ` +
          `data-fav-key="${esc(eventKey(e))}" ` +
          `title="${isFav ? 'Remove from saved' : 'Save event'}" ` +
          `aria-label="${isFav ? 'Remove from saved' : 'Save event'}">★</button>`;
        out.push(
          `<div class="ev-card ${gameClass}${past ? ' is-past' : ''}${isFav ? ' is-favorite' : ''}" data-event-key="${esc(eventKey(e))}">` +
            `<div class="ev-card-top">` +
              `<div class="ev-time">${esc(time)}</div>` +
              `<div class="ev-store-top${this.isSelected('store', e.store) ? ' active' : ''}" data-filter="store" data-value="${esc(e.store)}" title="${esc(e.store)}">${esc(e.store)}</div>` +
              (this.nearMe && this.storeDistanceKm(e.store) !== null
                ? `<div class="ev-distance">${esc(this.storeDistanceLabel(e.store))}</div>` : '') +
            `</div>` +
            `<div class="ev-title">${esc(e.title)}</div>` +
            `<div class="ev-footer">` +
              `<div class="ev-tags">` +
                `<span class="tag game${this.isSelected('game', game) ? ' active' : ''}" data-filter="game" data-value="${esc(game)}">${esc(game)}</span>` +
                `<span class="tag fmt${this.isSelected('format', fmt) ? ' active' : ''}" data-filter="format" data-value="${esc(fmt)}">${esc(fmt)}</span>` +

              `</div>` +
              // After the tags, not in the top row: grid columns are too
              // narrow there and the store name would shrink to "I…".
              (attendanceBadge(e)
                ? `<div class="ev-attendance${e.full && !e.participants ? ' is-full' : ''}" title="${esc(attendanceDetail(e))}">👥 ${esc(attendanceBadge(e))}</div>` : '') +
              `<div class="ev-actions">` + favButton + calendarButton + `</div>` +
            `</div>` +
          `</div>`
        );
      }
      return out.join('');
    },

    /* ── Delegated click on the horizontal grid.
         Chip click → toggle that filter and stop propagation.
         Card click → open detail panel. */
    onGridClick(ev) {
      const favBtn = ev.target.closest('[data-fav-key]');
      if (favBtn) {
        ev.stopPropagation();
        this.toggleFavorite(favBtn.dataset.favKey);
        return;
      }
      const calendarButton = ev.target.closest('[data-calendar-url]');
      if (calendarButton) {
        ev.stopPropagation();
        window.open(calendarButton.dataset.calendarUrl, '_blank', 'noopener');
        return;
      }
      const tag = ev.target.closest('[data-filter]');
      if (tag) {
        ev.stopPropagation();
        this.toggleFilter(tag.dataset.filter, tag.dataset.value);
        return;
      }
      const card = ev.target.closest('.ev-card');
      if (card) {
        const key = card.dataset.eventKey;
        const event = this.events.find(e => eventKey(e) === key);
        if (event) this.openPanel(event);
      }
    },

    getGameClass(game) {
      return GAME_CLASS_MAP[game] || 'game-unknown';
    },

    // Distance from the visitor to a store, or null (no position / no coords).
    storeDistanceKm(store) {
      const meta = STORE_META[store];
      if (!this.nearMe || !meta || meta.lat == null || meta.lng == null) return null;
      return haversineKm(this.nearMe, { lat: meta.lat, lng: meta.lng });
    },
    storeDistanceLabel(store) {
      const km = this.storeDistanceKm(store);
      return km === null ? '' : formatKm(km);
    },
    toggleNearMe() {
      if (this.nearMe) {
        this.nearMe = null;
        this.geoStatus = '';
        this.cleanupFilters();
        return;
      }
      if (!navigator.geolocation) {
        this.geoStatus = 'Location is not available in this browser.';
        return;
      }
      this.geoStatus = 'locating';
      navigator.geolocation.getCurrentPosition(
        pos => {
          this.nearMe = { lat: pos.coords.latitude, lng: pos.coords.longitude };
          this.geoStatus = '';
          this.cleanupFilters();
        },
        err => {
          this.geoStatus = err.code === err.PERMISSION_DENIED
            ? 'Location permission denied.' : 'Could not get your location.';
        },
        { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 },
      );
    },

    resetFilters() {
      this.nearMe = null;
      this.geoStatus = '';
      this.filterHistory = [];
      this.filters = { search: '', game: [], store: [], format: [] };
      this.openFacet = null;
      this.defaultPresetActive = false;
      this.showFavoritesOnly = false;
      this.cleanupFilters({ syncUrl: true });
    },
  };
}
