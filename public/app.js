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

// A design token from :root (styles.css), with a fallback for old browsers.
function cssToken(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// Smooth scrolling, unless the visitor asked for reduced motion.
function scrollBehavior() {
  return window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
}

// A game's colour, read once from the stylesheet (.game-* { --card-accent })
// so the map, cards and legend share one palette. Only #hex values pass:
// the result goes into an inline style.
const gameColors = new Map();
function gameColor(game) {
  if (!gameColors.has(game)) {
    const probe = document.createElement('span');
    probe.className = GAME_CLASS_MAP[game] || 'game-other';
    probe.style.display = 'none';
    document.body.appendChild(probe);
    const c = getComputedStyle(probe).getPropertyValue('--card-accent').trim();
    probe.remove();
    gameColors.set(game, /^#[0-9a-f]{3,8}$/i.test(c) ? c : '#9ca3af');
  }
  return gameColors.get(game);
}

// Pin ring: one slice per game, sized by its share of the events.
// mix = [{ game, n }], largest first.
function donutBackground(mix) {
  const total = mix.reduce((t, p) => t + p.n, 0);
  if (!total) return gameColor('Unknown');
  if (mix.length === 1) return gameColor(mix[0].game);
  let at = 0;
  const stops = mix.map((p) => {
    const from = at;
    at += (p.n / total) * 360;
    return `${gameColor(p.game)} ${from.toFixed(1)}deg ${at.toFixed(1)}deg`;
  });
  return `conic-gradient(${stops.join(', ')})`;
}

function mergeMixes(mixes) {
  const sum = new Map();
  for (const mix of mixes) for (const p of mix) sum.set(p.game, (sum.get(p.game) || 0) + p.n);
  return [...sum].map(([game, n]) => ({ game, n })).sort((a, b) => b.n - a.n);
}

function pinHtml(count, mix, extraClass) {
  // Only a number and #hex colours: no scraped text in this HTML.
  return `<span class="map-pin${extraClass}" style="background:${donutBackground(mix)}">`
    + `<span class="map-pin-n">${Number(count)}</span></span>`;
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
      // A cluster shows its total events and the game mix of its stores.
      iconCreateFunction: (cluster) => {
        const children = cluster.getAllChildMarkers();
        const n = children.reduce((sum, m) => sum + (m.options.eventCount || 0), 0);
        return L.divIcon({
          className: 'map-pin-wrap',
          html: pinHtml(n, mergeMixes(children.map(m => m.options.mix || [])), ' map-cluster'),
          iconSize: [40, 40],
          iconAnchor: [20, 20],
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
      html: pinHtml(s.count, s.mix, isSel ? ' is-selected' : ''),
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
    const label = document.createElement('span');
    label.textContent = s.name;  // textContent: store names are data
    const marker = L.marker([s.lat, s.lng], {
      icon, zIndexOffset: isSel ? 1000 : s.count, keyboard: true, title: s.name, eventCount: s.count, mix: s.mix,
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
      radius: (radiusKm || 0) * 1000, color: cssToken('--c-map-me', '#2563eb'), weight: 1, fillOpacity: 0.06, interactive: false,
    }).addTo(MAP.me);
    L.circleMarker([me.lat, me.lng], {
      radius: 7, color: cssToken('--c-map-me-ring', '#fff'), weight: 2, fillColor: cssToken('--c-map-me', '#2563eb'), fillOpacity: 1, interactive: false,
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
const CITY_SCOPED_PARAMS = ['event', 'w', 'game', 'store', 'format', 'type', 'time', 'q'];

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

/* ── Rendering helpers ─────────────────────────────────────────────── */

// Everything scraped goes through esc() before it reaches x-html.
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Line icons (24×24, stroke = currentColor). Directions is a pin, an
// outside link is a diagonal arrow: the two are never the same glyph.
const ICONS = {
  pin:    '<path d="M12 21s7-6.2 7-11.5A7 7 0 005 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.4"/>',
  star:   '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
  cal:    '<rect x="3.5" y="5" width="17" height="15" rx="3"/><path d="M8 3v4M16 3v4M3.5 10h17"/>',
  ext:    '<path d="M7 17L17 7M9 7h8v8"/>',
  share:  '<path d="M12 15V4M8 8l4-4 4 4M5 13v6h14v-6"/>',
  up:     '<path d="M6 14l6-6 6 6"/>',
  down:   '<path d="M6 10l6 6 6-6"/>',
  close:  '<path d="M6 6l12 12M18 6L6 18"/>',
  file:   '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/>',
  clock:  '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  warn:   '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5.5M12 16.2v.3"/>',
};
function icon(name, cls = '') {
  return `<svg class="ico${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONS[name]}</svg>`;
}

function gameLabel(game) {
  return GAME_SHORT[game] || game || 'Unknown';
}

// Derived lists, computed once per change of their inputs. Kept outside
// Alpine's reactive state: writing to it from a getter would re-trigger
// the very effect that is reading it.
const MEMO = new Map();
function memo(name, key, fn) {
  const hit = MEMO.get(name);
  if (hit && hit.key === key) return hit.value;
  const value = fn();
  MEMO.set(name, { key, value });
  return value;
}

// Types are derived from the title, so they are worth caching per event.
const TYPE_CACHE = new Map();
function typeOf(e) {
  const k = eventKey(e);
  if (!TYPE_CACHE.has(k)) TYPE_CACHE.set(k, TCG.eventType(e));
  return TYPE_CACHE.get(k);
}

// DOM nodes the UI remembers (who opened a sheet, an ongoing swipe).
// Never on Alpine's state: it would wrap them in proxies, and DOM APIs
// such as document.contains() reject a proxy.
const UI = { sheetOpener: null, filtersOpener: null, drag: null, toastTimer: null, undo: null };

// Focus an element as soon as it can take focus: a sheet shown by x-show
// may become visible a frame after the state change.
function focusSoon(find, tries = 6) {
  const el = find();
  if (el && el.offsetParent !== null) { el.focus({ preventScroll: true }); if (document.activeElement === el) return; }
  if (tries > 0) setTimeout(() => focusSoon(find, tries - 1), 50);
}

const PHONE_MQ = '(max-width: 720px)';
// Columns of the Day view's card grid; the same breakpoints as styles.css
// (.cards: 3 columns from 1100px, 2 from 721px, 1 on phones).
const COLS_MQ = { three: '(min-width: 1100px)', two: '(min-width: 721px)' };
function gridCols() {
  if (window.matchMedia(COLS_MQ.three).matches) return 3;
  return window.matchMedia(COLS_MQ.two).matches ? 2 : 1;
}
const VIEW_STORAGE_KEY = 'tcg-view-v3';
const DISTANCES = [2, 5, 10, 25];
const GAME_CHIPS_SHOWN = 6;   // desktop row; the rest behind "+N more"

function startView() {
  const v3 = localStorage.getItem(VIEW_STORAGE_KEY);
  if (['day', 'week', 'map'].includes(v3)) return v3;
  // The previous layout's choice: the grid is Week now; the list is Day.
  const v2 = localStorage.getItem('tcg-view-v2');
  if (v2 === 'horizontal') return 'week';
  if (v2 === 'map') return 'map';
  return 'day';
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
    // Filters. Lists: game / store / format / type (TCG.EVENT_TYPES keys) /
    // time (TCG.TIME_SLOTS keys). All of them are kept in the URL.
    filters:      { search: '', game: [], store: [], format: [], type: [], time: [] },
    savedPresets: [],
    defaultPresetId: null,
    onboardingOpen: false,
    onboardingCompleted: false,
    onboarding: { game: [], store: [] },
    TIME_SLOTS: TCG.TIME_SLOTS,
    EVENT_TYPES: TCG.EVENT_TYPES,
    DAY_SECTIONS: TCG.DAY_SECTIONS,
    DISTANCES,
    favorites:         [],    // array of eventKey strings
    showFavoritesOnly: false,
    // "Near me": the visitor's position (memory only — never stored or
    // sent anywhere) and the distance filter in km (null = anywhere).
    nearMe:       null,
    km:           null,
    geoStatus:    '',      // '', 'locating', or an error message
    welcomeAllGames: false,   // welcome screen: "+N more" games opened
    WELCOME_TOP_GAMES: 8,
    // Map view: the chosen day (null = the whole week), the selected store
    // (display name) and a load/error message for the map itself.
    mapDay:       null,
    mapStore:     null,
    mapStatus:    '',
    mapFailed:    false,
    mapAttempt:   0,     // bumped by Retry / coming back online: re-runs drawMap
    offline:      !navigator.onLine,
    // Reactive Europe/Madrid clock — refreshed every minute.
    nowMadrid: readMadridNow(),
    viewMode:  startView(),
    isPhone:   window.matchMedia(PHONE_MQ).matches,
    cols:      gridCols(),

    // Day view
    selectedDay:  null,   // ISO date; null = today if in the week, else Monday
    secOpen:      {},     // 'iso:section' → true/false, set by the user
    openKey:      null,   // card expanded in place (Day view, desktop)
    // Week view: rows folded by the user.
    collapsedRows: {},
    // Detail sheet (phones; Week and Map views on desktop)
    sheetEvent:   null,
    // Filters panel
    filtersOpen:  false,
    storePickerOpen: false,
    storeQuery:   '',
    gamesExpanded: false,
    searchOpen:   false,  // phones: the search field under the header
    toast:        null,   // { text, undo } — the status line at the bottom
    fpMore:       false,  // Filters body has content below its footer
    gamesPanelExpanded: false,  // Filters: games without events this week

    /* ── Init ──────────────────────────────────────────────────────── */
    async init() {
      // Alpine 3 calls init() itself; guard against a second call.
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
      for (const q of Object.values(COLS_MQ)) window.matchMedia(q).addEventListener('change', () => { this.cols = gridCols(); });
      const mq = window.matchMedia(PHONE_MQ);
      mq.addEventListener('change', () => {
        this.isPhone = mq.matches;
        // An in-place card becomes a sheet on a phone, and back.
        if (this.isPhone && this.openKey) { const e = this.eventByKey(this.openKey); this.openKey = null; if (e) this.openSheet(e); }
      });
      this.loadPresets();
      this.loadDefaultPreset();
      this.loadOnboardingState();
      // First visit to the bare site: the welcome screen, shown at once (it
      // fills in its numbers as the data arrives). Links with parameters
      // (an event, a filter, a city) go straight to what they point at.
      if (!window.location.search && this.shouldShowOnboarding(false)) this.openOnboarding();

      window.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (this.sheetEvent) this.closeSheet();
        else if (this.filtersOpen) this.closeFilters();
        else if (this.openKey) this.collapseCard();
        else if (this.onboardingOpen) this.skipOnboarding();
        else if (this.searchOpen) this.searchOpen = false;
      });

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
        // but the default keeps ?city= in the URL.
        if (this.city !== DEFAULT_CITY || new URLSearchParams(window.location.search).has('city')) {
          this.rememberCity(this.city);
        }
        this.syncTitle();
        this.cleanupFavorites();
        const params = new URLSearchParams(window.location.search);
        const hasUrlFilters = TCG.URL_LISTS.concat('q').some(f => params.get(f));
        if (hasUrlFilters) this.applyFiltersFromUrl();
        else this.applyDefaultPreset();
        this.cleanupFilters({ syncUrl: false });
        const rawEventParam = params.get('event');
        if (rawEventParam) {
          const decoded = decodeEventParam(rawEventParam);
          const key = decoded !== null ? decoded : rawEventParam;
          const find = () => this.events.find(ev => eventKey(ev) === key)
            || this.events.find(ev => legacyEventKey(ev) === key);
          let event = find();
          if (!event) {
            // Outside upcoming.json: load the event's week (from ?w=, or the
            // date inside older keys) and look again.
            const m = key.match(/\d{4}-\d{2}-\d{2}T/);
            const week = params.get('w') || (m ? weekOfIso(m[0]) : null);
            if (week) {
              await this.loadWeek(week);
              event = find();
            }
          }
          if (event) {
            this.loading = false;
            this.showEvent(event);
          }
        }
      } catch (err) {
        console.error('Failed to load events', err);
        this.loadError = 'Could not load events. Check your connection and try again.';
      } finally {
        this.loading = false;
      }

      if (this.viewMode === 'week') this.scrollWeekToDay();
      this.$watch('filters.search', () => this.cleanupFilters({ syncUrl: true }));
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
      // URLs must match the preloads index.html injects.
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
      // STORE_META is not reactive: fill it before the events land.
      for (const k of Object.keys(STORE_META)) delete STORE_META[k];
      for (const s of stores.stores || []) STORE_META[s.name] = s;
      this._loadedWeeks = new Set();
      this.events = upcoming.events;
      this.markWeeksLoaded(upcoming.from, upcoming.to);
      this.weekIndex = index.weeks || null;
    },

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
    // previous city stays fully in place.
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
      this.closeSheet();
      this.openKey = null;
      this.city = id;
      this.applyCityData(data);
      this.rememberCity(id);
      // Stores are per city; games and formats carry over.
      this.filters.store = [];
      this.mapStore = null;
      this.mapDay = null;
      this.nearMe = null;
      this.km = null;
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
      try { localStorage.setItem(VIEW_STORAGE_KEY, mode); } catch { /* storage blocked */ }
      this.openKey = null;
      if (mode === 'map') MAP.fitKey = null;  // reopening the map shows every result again
      if (mode === 'week') this.scrollWeekToDay();
    },

    // Week grid on phones shows two columns: start on the selected day.
    scrollWeekToDay() {
      this.$nextTick(() => requestAnimationFrame(() => {
        const wrap = document.querySelector('.weekview');
        const head = wrap && wrap.querySelector(`.whead[data-iso="${this.currentDay}"]`);
        if (wrap && head && wrap.scrollWidth > wrap.clientWidth) {
          wrap.scrollLeft = head.offsetLeft - wrap.firstElementChild.offsetLeft;
        }
      }));
    },

    /* ── Navigation ────────────────────────────────────────────────── */
    async shiftWeek(n) {
      const weekday = this.selectedDayIndex;
      this.weekStart = addDays(this.weekStart, 7 * n);
      // Same weekday in the new week, today when coming back to this week.
      const days = this.weekIsos;
      this.selectedDay = days.includes(this.nowMadrid.iso) ? this.nowMadrid.iso : days[weekday];
      this.mapDay = null;
      this.openKey = null;
      await this.loadWeek(isoWeekOf(this.weekStart));
      this.cleanupFilters();
    },
    prevWeek() { return this.shiftWeek(-1); },
    nextWeek() { return this.shiftWeek(+1); },

    goToday() {
      this.weekStart = weekMonday(madridToday());
      this.selectedDay = this.nowMadrid.iso;
      this.mapDay = null;
      this.openKey = null;
      this.cleanupFilters();
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

    get weekIsos() {
      return Array.from({ length: 7 }, (_, i) => isoDay(addDays(this.weekStart, i)));
    },

    get currentDay() {
      const days = this.weekIsos;
      return days.includes(this.selectedDay) ? this.selectedDay : TCG.defaultDay(days, this.nowMadrid.iso);
    },

    get selectedDayIndex() {
      return Math.max(0, this.weekIsos.indexOf(this.currentDay));
    },

    pickDay(iso) {
      this.selectedDay = iso;
      this.openKey = null;
    },

    /* ── Filtering ─────────────────────────────────────────────────── */

    // The current week's events (inactive future ones dropped: they await
    // deletion), before any filter.
    get weekBase() {
      const from = isoDay(this.weekStart), to = isoDay(addDays(this.weekStart, 7));
      return memo('weekBase', `${this._dataGen}|${this.events.length}|${from}|${this.nowMadrid.iso}`, () =>
        this.events.filter(e => {
          const day = e.datetime_start.slice(0, 10);
          return day >= from && day < to && !(e.is_active === false && !this.isEventPast(e));
        }));
    },

    // Formats per game this week: the Format block's options.
    get byGame() {
      const base = this.weekBase;
      return memo('byGame', base, () => TCG.formatsByGame(base));
    },

    // Everything a match depends on, read once per filter change.
    get matchCtx() {
      const f = this.filters;
      const byGame = this.byGame;   // first: refreshes weekBase, whose key is part of ours
      const key = JSON.stringify([f, this.showFavoritesOnly && this.favorites, this.km, this.nearMe, MEMO.get('weekBase').key]);
      return memo('matchCtx', key, () => ({
        q: f.search.toLowerCase().trim(),
        game: f.game.slice(), store: f.store.slice(), format: f.format.slice(),
        type: f.type.slice(), time: f.time.slice(),
        fav: this.showFavoritesOnly ? new Set(this.favorites) : null,
        km: this.km && this.nearMe ? this.km : null,
        byGame,
        key,
      }));
    },

    // Does an event pass every filter (but `ignore`, used for counts)?
    passes(ctx, e, ignore) {
      if (ctx.q && ignore !== 'search'
        && !e.title.toLowerCase().includes(ctx.q) && !(e.store || '').toLowerCase().includes(ctx.q)) return false;
      if (ctx.fav && !ctx.fav.has(eventKey(e))) return false;
      if (ctx.km && ignore !== 'km') {
        const km = this.storeDistanceKm(e.store);
        if (km === null || km > ctx.km) return false;
      }
      if (ignore !== 'game') {
        const g = TCG.gameOf(e);
        if (ctx.game.length ? !ctx.game.includes(g) : g === 'Unknown') return false;
      }
      if (ignore !== 'store' && ctx.store.length && !ctx.store.includes(e.store)) return false;
      if (ignore !== 'format' && !TCG.passesFormat(e, ctx.format, ctx.byGame)) return false;
      if (ignore !== 'type' && ctx.type.length && !ctx.type.includes(typeOf(e))) return false;
      if (ignore !== 'time' && !TCG.passesTime(e, ctx.time)) return false;
      return true;
    },

    // This week's events after every filter.
    get filteredWeek() {
      const ctx = this.matchCtx;
      return memo('filteredWeek', ctx, () =>
        this.weekBase.filter(e => this.passes(ctx, e))
          .sort((a, b) => a.datetime_start.localeCompare(b.datetime_start)));
    },

    // Events passing every filter but one, for that filter's counts.
    except(field) {
      const ctx = this.matchCtx;
      return memo('except:' + field, ctx, () => this.weekBase.filter(e => this.passes(ctx, e, field)));
    },

    get weekEventCount() {
      return this.filteredWeek.length;
    },

    /* ── 7 days of the current week ────────────────────────────────── */
    get weekDays() {
      const list = this.filteredWeek;
      const today = this.nowMadrid.iso;
      return memo('weekDays', `${MEMO.get('matchCtx').key}|${today}|${isoDay(this.weekStart)}`, () => {
        const byDay = {};
        for (const e of list) (byDay[e.datetime_start.slice(0, 10)] || (byDay[e.datetime_start.slice(0, 10)] = [])).push(e);
        return this.weekIsos.map((iso, i) => {
          const d = addDays(this.weekStart, i);
          const events = byDay[iso] || [];
          return {
            iso, events,
            dow: d.toLocaleDateString('en-GB', { weekday: 'short' }),
            num: d.getDate(),
            long: d.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'short' }),
            isToday: iso === today,
            isPast: iso < today,
            sections: TCG.daySections(events),
          };
        });
      });
    },

    // The strip above the Day view: count + load bar per day.
    get dayStrip() {
      const days = this.weekDays;
      const strip = TCG.dayStrip(days.map(d => ({ iso: d.iso, count: d.events.length })), this.nowMadrid.iso);
      return strip.map((s, i) => Object.assign(s, { dow: days[i].dow, num: days[i].num, long: days[i].long }));
    },

    get day() {
      return this.weekDays[this.selectedDayIndex];
    },

    isSecOpen(sec) {
      const k = this.day.iso + ':' + sec.key;
      if (k in this.secOpen) return this.secOpen[k];
      if (this.filters.search.trim()) return true;  // searching: show every hit
      return sec.key === TCG.defaultOpenSection(this.day.sections, {
        isToday: this.day.isToday, nowMinutes: this.nowMadrid.minutesSinceMidnight,
      });
    },

    toggleSec(sec) {
      this.secOpen[this.day.iso + ':' + sec.key] = !this.isSecOpen(sec);
    },

    secTitle(sec) {
      return sec.words ? `${sec.label} · ${sec.words}` : sec.label;
    },

    secHint(sec) {
      return this.isSecOpen(sec) ? 'Hide' : 'Show';
    },

    // Week view rows: the day sections that have events this week.
    get weekRows() {
      const days = this.weekDays;
      return TCG.DAY_SECTIONS.filter(s => days.some(d => d.sections.some(x => x.key === s.key)));
    },

    get weekCells() {
      const rows = this.weekRows, days = this.weekDays, out = [];
      rows.forEach((row, r) => days.forEach((day, c) => {
        const sec = day.sections.find(x => x.key === row.key);
        const events = sec ? sec.events : [];
        out.push({
          // Changes with the events, so Alpine rebuilds the cell (x-html
          // inside a reused x-for clone can otherwise keep stale cards).
          renderKey: row.key + day.iso + ':' + events.map(e => e.id).join(','),
          row, day, r, c, events,
        });
      }));
      return out;
    },

    toggleRow(key) {
      this.collapsedRows[key] = !this.collapsedRows[key];
    },

    /* ── Chips and the Filters panel ───────────────────────────────── */

    // Game chips: this week's games in a stable order (busiest first,
    // before filters), counts after the other filters.
    get gameChips() {
      const base = this.weekBase;
      const order = memo('gameOrder', base, () => {
        const n = {};
        for (const e of base) n[TCG.gameOf(e)] = (n[TCG.gameOf(e)] || 0) + 1;
        return n;
      });
      const counts = {};
      for (const e of this.except('game')) counts[TCG.gameOf(e)] = (counts[TCG.gameOf(e)] || 0) + 1;
      const sel = this.filters.game;
      const games = new Set(Object.keys(order).filter(g => g !== 'Unknown').concat(sel));
      const chips = [...games].sort((a, b) => a.localeCompare(b)).map(g => ({
        game: g, label: gameLabel(g), n: counts[g] || 0, w: order[g] || 0, on: sel.includes(g), cls: this.getGameClass(g),
      }));
      return TCG.orderGameChips(chips)
        .map((c, i) => Object.assign(c, { extra: i >= GAME_CHIPS_SHOWN && !c.on }));
    },

    // Filters panel: picked games first, then this week's games (most
    // events first), then the city's other games behind "+N more".
    get panelGames() {
      const chips = this.gameChips;                     // picked + this week's, busiest first
      const week = new Set(chips.map(c => c.game));
      const others = {};
      for (const e of this.events) {
        const g = TCG.gameOf(e);
        if (g !== 'Unknown' && !week.has(g)) others[g] = (others[g] || 0) + 1;
      }
      const sel = chips.filter(c => c.on);
      const active = chips.filter(c => !c.on && c.w > 0);   // already busiest-first
      const rest = chips.filter(c => !c.on && c.w === 0)
        .concat(Object.keys(others).sort((a, b) => others[b] - others[a])
          .map(g => ({ game: g, label: gameLabel(g), n: 0, w: 0, on: false, cls: this.getGameClass(g) })));
      return { shown: sel.concat(active), rest };
    },

    get hiddenGameCount() {
      return this.gameChips.filter(c => c.extra).length;
    },

    isGameOn(game) {
      return this.filters.game.includes(game);
    },

    toggleGame(game) {
      const games = this.filters.game.slice();
      const i = games.indexOf(game);
      if (i >= 0) games.splice(i, 1); else games.push(game);
      this.filters.game = games;
      // Formats only the removed game had go with it.
      this.filters.format = TCG.pruneFormats(this.filters.format, games, this.byGame);
      this.openKey = null;
      this.cleanupFilters({ syncUrl: true });
    },

    // The Format block (three states, see model.js formatFacet). Games in
    // chip order, so groups read in the same order as the chips.
    get formatFacet() {
      const order = this.gameChips.map(c => c.game);
      const games = this.filters.game.slice().sort((a, b) => order.indexOf(a) - order.indexOf(b));
      return TCG.formatFacet({
        games, byGame: this.byGame, countable: this.except('format'), selected: this.filters.format,
      });
    },

    toggleList(field, value) {
      const list = this.filters[field].slice();
      const i = list.indexOf(value);
      if (i >= 0) list.splice(i, 1); else list.push(value);
      this.filters[field] = list;
      this.openKey = null;
      this.cleanupFilters({ syncUrl: true });
    },

    // [{ key, label, n, on }] for type / time chips.
    countChips(field, options) {
      const counts = {};
      const keyOf = field === 'type' ? typeOf : TCG.timeSlotOf;
      for (const e of this.except(field)) { const k = keyOf(e); counts[k] = (counts[k] || 0) + 1; }
      return options.map(o => ({ key: o.key, label: o.label, words: o.words || '', n: counts[o.key] || 0, on: this.filters[field].includes(o.key) }));
    },

    get typeChips() { return this.countChips('type', TCG.EVENT_TYPES); },
    get timeChips() { return this.countChips('time', TCG.TIME_SLOTS); },

    // Stores: every registry store plus any store seen in the loaded weeks.
    get allStores() {
      const values = new Set(Object.keys(STORE_META));
      for (const e of this.events) if (e.store) values.add(e.store);
      return [...values].sort((a, b) => a.localeCompare(b));
    },

    get storeOptions() {
      const counts = {};
      for (const e of this.except('store')) counts[e.store] = (counts[e.store] || 0) + 1;
      const q = this.storeQuery.toLowerCase().trim();
      const sel = this.filters.store;
      return this.allStores
        .filter(s => !q || s.toLowerCase().includes(q))
        .map(s => ({ name: s, n: counts[s] || 0, on: sel.includes(s) }))
        // Selected first, then stores with events this week, then the rest.
        .sort((a, b) => (b.on - a.on) || ((b.n > 0) - (a.n > 0)) || a.name.localeCompare(b.name));
    },

    get storeSummary() {
      const sel = this.filters.store;
      if (!sel.length) return `All stores (${this.allStores.length})`;
      return sel.length === 1 ? sel[0] : `${sel.length} stores`;
    },

    get activeFilterCount() {
      return TCG.activeFilterCount(this.filters, this.km && this.nearMe ? this.km : null);
    },

    // "Filters: One Piece, Magic · Evening" next to the day title.
    get filterSummary() {
      const parts = this.filters.game.map(gameLabel)
        .concat(this.filters.format, this.filters.type.map(t => TCG.TYPE_LABEL[t]),
                this.filters.time.map(t => TCG.TIME_SLOTS.find(s => s.key === t).label),
                this.filters.store);
      if (this.km && this.nearMe) parts.push(`within ${this.km} km`);
      if (this.showFavoritesOnly) parts.push('saved');
      if (!parts.length) return '';
      return 'Filters: ' + (parts.length > 4 ? parts.slice(0, 3).join(', ') + ` +${parts.length - 3}` : parts.join(', '));
    },

    openFilters() {
      this.filtersOpen = true;
      this.storePickerOpen = false;
      UI.filtersOpener = document.activeElement;
      this.lockScroll();
      this.$nextTick(() => focusSoon(() => document.getElementById('filters-title')));
    },

    closeFilters() {
      this.filtersOpen = false;
      this.unlockScroll();
      const back = UI.filtersOpener;
      UI.filtersOpener = null;
      if (back && document.contains(back)) back.focus();
    },

    // Distance: picking one asks for the location the first time.
    setDistance(km) {
      this.km = km;
      if (km && !this.nearMe) this.locate();
      this.cleanupFilters();
    },

    clearAll() {
      this.km = null;
      this.geoStatus = this.nearMe ? '' : this.geoStatus;
      this.filters = { search: '', game: [], store: [], format: [], type: [], time: [] };
      this.showFavoritesOnly = false;
      this.storeQuery = '';
      this.openKey = null;
      this.cleanupFilters({ syncUrl: true });
    },

    toggleSaved() {
      this.showFavoritesOnly = !this.showFavoritesOnly;
      this.openKey = null;
    },

    /* ── Presets ────────────────────────────────────────────────────── */
    presetFilters(p) {
      const list = (v) => (Array.isArray(v) ? v.filter(x => typeof x === 'string' && x) : []);
      const f = (p && p.filters) || {};
      return { game: list(f.game), store: list(f.store), format: list(f.format), type: list(f.type), time: list(f.time) };
    },

    loadPresets() {
      try {
        const parsed = JSON.parse(localStorage.getItem(PRESETS_STORAGE_KEY) || '[]');
        this.savedPresets = (Array.isArray(parsed) ? parsed : [])
          .filter(p => p && typeof p === 'object')
          .map(p => ({ id: String(p.id || ''), name: String(p.name || '').trim(), filters: this.presetFilters(p) }))
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

    currentPresetFilters() {
      const f = this.filters;
      return { game: f.game.slice(), store: f.store.slice(), format: f.format.slice(), type: f.type.slice(), time: f.time.slice() };
    },

    get canSavePreset() {
      return Object.values(this.currentPresetFilters()).some(v => v.length);
    },

    saveCurrentPreset() {
      if (!this.canSavePreset) return;
      const name = window.prompt('Name this preset');
      const trimmed = name ? name.trim() : '';
      if (!trimmed) return;
      const filters = this.currentPresetFilters();
      const existing = this.savedPresets.find(p => p.name === trimmed);
      if (existing) {
        if (!window.confirm(`Replace preset "${trimmed}"?`)) return;
        existing.filters = filters;
      } else {
        this.savedPresets.push({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8), name: trimmed, filters });
      }
      this.persistPresets();
      this.flash(`Preset “${trimmed}” saved`);
    },

    isPresetActive(p) {
      return JSON.stringify(this.presetFilters(p)) === JSON.stringify(this.currentPresetFilters());
    },

    applyPreset(preset) {
      if (!preset) return;
      Object.assign(this.filters, this.presetFilters(preset));
      this.openKey = null;
      this.cleanupFilters({ syncUrl: true });
    },

    deletePreset(id) {
      const p = this.savedPresets.find(x => x.id === id);
      if (p && !window.confirm(`Delete preset "${p.name}"?`)) return;
      this.savedPresets = this.savedPresets.filter(x => x.id !== id);
      this.persistPresets();
      if (this.defaultPresetId === id) {
        this.defaultPresetId = null;
        this.persistDefaultPreset();
      }
    },

    loadDefaultPreset() {
      try {
        const parsed = JSON.parse(localStorage.getItem(DEFAULT_PRESET_STORAGE_KEY) || 'null');
        this.defaultPresetId = (parsed && parsed.id) ? String(parsed.id) : null;
      } catch (err) {
        console.warn('Failed to load default preset', err);
        this.defaultPresetId = null;
      }
    },

    persistDefaultPreset() {
      try {
        if (this.defaultPresetId) localStorage.setItem(DEFAULT_PRESET_STORAGE_KEY, JSON.stringify({ id: this.defaultPresetId }));
        else localStorage.removeItem(DEFAULT_PRESET_STORAGE_KEY);
      } catch (err) {
        console.warn('Failed to persist default preset', err);
      }
    },

    setDefaultPreset(id) {
      this.defaultPresetId = this.defaultPresetId === id ? null : id;
      this.persistDefaultPreset();
    },

    // On a fresh visit (no filters in the URL) the default preset applies.
    // The URL is left alone: the preset is this visitor's, not the link's.
    applyDefaultPreset() {
      if (!this.defaultPresetId) return;
      const preset = this.savedPresets.find(p => p.id === this.defaultPresetId);
      if (!preset) {
        this.defaultPresetId = null;
        this.persistDefaultPreset();
        return;
      }
      Object.assign(this.filters, this.presetFilters(preset));
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
      return this.favorites.includes(eventKey(e));
    },

    toggleFavorite(keyOrEvent) {
      const key = typeof keyOrEvent === 'string' ? keyOrEvent : eventKey(keyOrEvent);
      const idx = this.favorites.indexOf(key);
      const week = (this._favWeeks || {})[key]
        || (typeof keyOrEvent !== 'string' && keyOrEvent.datetime_start ? weekOfIso(keyOrEvent.datetime_start) : null);
      if (idx >= 0) {
        this.favorites.splice(idx, 1);
        this.persistFavorites();
        // Removing is one tap: offer the way back.
        this.flash('Removed from saved', () => {
          if (this.favorites.includes(key)) return;
          this.favorites.push(key);
          if (week) { if (!this._favWeeks) this._favWeeks = {}; this._favWeeks[key] = week; }
          this.persistFavorites();
        });
        return;
      }
      this.favorites.push(key);
      if (week) {
        if (!this._favWeeks) this._favWeeks = {};
        this._favWeeks[key] = week;
      }
      this.persistFavorites();
    },

    get favoritesCount() {
      return this.favorites.length;
    },

    // Prune keys for events no longer present in the data.
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

    /* ── Onboarding (welcome screen) ───────────────────────────────── */
    loadOnboardingState() {
      try {
        const parsed = JSON.parse(localStorage.getItem(ONBOARDING_STORAGE_KEY) || 'null');
        this.onboardingCompleted = !!(parsed && parsed.completed === true);
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
      // Reopened from the brand / footer: start from the games already chosen.
      this.onboarding = { game: this.filters.game.slice(), store: [] };
    },

    // Welcome screen: the city's games, most events first.
    get welcomeGames() {
      const counts = {};
      for (const e of this.events) {
        if (e.game && e.game !== 'Unknown' && !this.isEventPast(e)) counts[e.game] = (counts[e.game] || 0) + 1;
      }
      return Object.keys(counts).sort((a, b) => counts[b] - counts[a])
        .map(game => ({ game, n: counts[game], color: gameColor(game) }));
    },

    get welcomeGamesShown() {
      const all = this.welcomeGames;
      if (this.welcomeAllGames) return all;
      return all.filter((g, i) => i < this.WELCOME_TOP_GAMES || this.onboarding.game.includes(g.game));
    },

    get welcomeStats() {
      const today = this.nowMadrid.iso;
      const weekEnd = isoDay(addDays(madridToday(), 7));
      const next7 = this.events.filter(e => {
        const d = e.datetime_start.slice(0, 10);
        return d >= today && d < weekEnd && !this.isEventPast(e);
      });
      return {
        events: next7.length,
        stores: new Set(this.events.filter(e => !this.isEventPast(e)).map(e => e.store)).size,
        games: this.welcomeGames.length,
      };
    },

    async welcomeCity(id) {
      if (id === this.city) return;
      this.onboarding.game = [];  // games differ per city
      await this.setCity(id);
    },

    skipOnboarding() {
      this.onboardingOpen = false;
      this.persistOnboardingComplete();
    },

    // The games picked here become the game chips (and the default preset,
    // so the next visit opens on them too).
    completeOnboarding(view) {
      if (view === 'map') {
        if (this.viewMode !== 'map') this.setView('map');
      } else if (this.viewMode === 'map') {
        this.setView('day');
      }
      const games = this.onboarding.game.slice();
      const removed = this.filters.game.filter(g => !games.includes(g));
      this.filters.game = games;
      if (removed.length) this.filters.format = TCG.pruneFormats(this.filters.format, games, this.byGame);
      this.cleanupFilters({ syncUrl: false });
      if (games.length) {
        const filters = Object.assign(this.currentPresetFilters(), { game: games });
        // Reopened later: update the default preset, don't pile up copies.
        let preset = this.savedPresets.find(p => p.id === this.defaultPresetId);
        if (preset) preset.filters = filters;
        else {
          preset = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8), name: this.generateDefaultPresetName(), filters };
          this.savedPresets.push(preset);
        }
        this.persistPresets();
        this.defaultPresetId = preset.id;
        this.persistDefaultPreset();
      }
      this.skipOnboarding();
    },

    toggleOnboardingValue(field, value) {
      const arr = this.onboarding[field];
      const idx = arr.indexOf(value);
      if (idx >= 0) arr.splice(idx, 1);
      else arr.push(value);
    },

    generateDefaultPresetName() {
      const existingNames = new Set(this.savedPresets.map(p => p.name));
      const name = 'My games';
      if (!existingNames.has(name)) return name;
      let counter = 2;
      while (existingNames.has(`${name} ${counter}`)) counter++;
      return `${name} ${counter}`;
    },

    /* ── URL ───────────────────────────────────────────────────────── */
    syncFiltersToUrl() {
      const url = new URL(window.location.href);
      TCG.writeFilterParams(url.searchParams, this.filters);
      history.replaceState(null, '', url.toString());
    },

    applyFiltersFromUrl() {
      const f = TCG.readFilterParams(new URLSearchParams(window.location.search));
      const stores = new Set(this.allStores);
      f.store = f.store.filter(s => stores.has(s));
      Object.assign(this.filters, f);
    },

    // Keeps the selection coherent: formats need a game, stores must exist.
    cleanupFilters({ syncUrl = false } = {}) {
      if (!this.filters.game.length && this.filters.format.length) this.filters.format = [];
      if (this.filters.store.length) {
        const stores = new Set(this.allStores);
        const next = this.filters.store.filter(s => stores.has(s));
        if (next.length !== this.filters.store.length) this.filters.store = next;
      }
      // Map: a day chip the filters have emptied falls back to the week.
      if (this.mapDay && !this.mapEvents.length) this.mapDay = null;
      if (syncUrl) this.syncFiltersToUrl();
    },

    /* ── Time ──────────────────────────────────────────────────────── */
    // True if the event already started (Europe/Madrid wall clock).
    isEventPast(e) {
      if (!e || !e.datetime_start) return false;
      const dayPart = e.datetime_start.slice(0, 10);
      if (dayPart < this.nowMadrid.iso) return true;
      if (dayPart > this.nowMadrid.iso) return false;
      const h = parseInt(e.datetime_start.slice(11, 13), 10);
      const m = parseInt(e.datetime_start.slice(14, 16), 10);
      return (h * 60 + m) < this.nowMadrid.minutesSinceMidnight;
    },

    get weekRangeLabel() {
      const sun = addDays(this.weekStart, 6);
      const sameMonth = sun.getMonth() === this.weekStart.getMonth();
      const from = this.weekStart.toLocaleDateString('en-GB', sameMonth ? { day: 'numeric' } : { day: 'numeric', month: 'short' });
      const to = sun.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
      return `${from} – ${to}`;
    },

    get isThisWeek() {
      return this.weekIsos.includes(this.nowMadrid.iso);
    },

    formatTime(iso) {
      if (!iso) return '';
      return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Madrid' });
    },

    // "17:00", or "Time TBC" for the midnight placeholder sources send.
    cardTime(e) {
      return TCG.hasTime(e) ? this.formatTime(e.datetime_start) : 'Time TBC';
    },

    formatDayShort(iso) {
      const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
      return new Date(y, m - 1, d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric' });
    },

    formatDayMonth(iso) {
      const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
      return new Date(y, m - 1, d).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
    },

    formatDomain(url) {
      if (!url || typeof url !== 'string') return '';
      try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
    },

    /* ── Event cards (x-html: one builder for Day, Week and the sheet) ─ */

    eventByKey(key) {
      return this.events.find(e => eventKey(e) === key) || null;
    },

    // Game pill, format, type (when not a plain tournament), spots.
    tagsHtml(e) {
      const fmt = TCG.eventFormat(e);
      const type = typeOf(e);
      const bits = [];
      if (fmt) bits.push(esc(fmt));
      if (type !== 'tournament') bits.push(esc(TCG.TYPE_LABEL[type]));
      const spots = attendanceBadge(e);
      return `<span class="pill ${this.getGameClass(e.game)}">${esc(gameLabel(e.game))}</span>`
        + (bits.length ? `<span class="ev-fmt">${bits.join(' · ')}</span>` : '')
        + (spots ? `<span class="ev-spots${e.full && !e.participants ? ' is-full' : ''}">${esc(spots)}</span>` : '');
    },

    storeLine(e, { full = false } = {}) {
      const km = this.storeDistanceLabel(e.store);
      return `<span class="ev-store">${icon('pin')}<span class="ev-store-name">${esc(e.store)}</span>`
        + (km ? `<span class="ev-km">· ${esc(full ? km + ' from you' : km)}</span>` : '') + `</span>`;
    },

    // A card. The open one (Day view, desktop) stays in its row, marked
    // active; its details are a separate panel after the row (detailHtml).
    cardHtml(e, { compact = false } = {}) {
      const key = eventKey(e);
      const active = !compact && this.openKey === key;
      const fav = this.favorites.includes(key);
      const cal = buildGoogleCalendarUrl(e);
      const cls = ['ev', compact ? 'ev-compact' : '', this.isEventPast(e) ? 'is-past' : '', fav ? 'is-saved' : '',
        active ? 'is-active ' + this.getGameClass(e.game) : ''].filter(Boolean).join(' ');
      const aria = compact ? ' aria-haspopup="dialog"'
        : ` aria-expanded="${active}"` + (active ? ` aria-controls="${this.detailId(key)}"` : '');
      return `<article class="${cls}" data-key="${esc(key)}">`
        + `<div class="ev-time">${esc(this.cardTime(e))}${this.timeFixIcon(e)}</div>`
        + `<div class="ev-main">`
        +   `<h4 class="ev-title"><button type="button" class="ev-open" data-act="open"${aria}>${esc(e.title)}</button></h4>`
        +   this.storeLine(e)
        +   `<div class="ev-tags">${this.tagsHtml(e)}</div>`
        + `</div>`
        + `<div class="ev-acts">`
        +   `<button type="button" class="ib${fav ? ' is-on' : ''}" data-act="fav" aria-pressed="${fav}" aria-label="${fav ? 'Saved — remove' : 'Save'}: ${esc(e.title)}" title="${fav ? 'Remove from saved' : 'Save'}">${icon('star')}</button>`
        +   (cal ? `<button type="button" class="ib" data-act="gcal" aria-label="Add to Google Calendar: ${esc(e.title)}" title="Add to Google Calendar">${icon('cal')}</button>` : '')
        + `</div>`
        + `</article>`;
    },

    detailId(key) {
      return 'evx-' + key.replace(/[^a-zA-Z0-9]/g, '').slice(-24);
    },

    // When / Where / Players / Format. Rows without data are left out.
    factsHtml(e) {
      const rows = [];
      const end = e.datetime_end && e.datetime_end.slice(0, 10) === e.datetime_start.slice(0, 10)
        ? ' – ' + this.formatTime(e.datetime_end) : '';
      const when = TCG.hasTime(e)
        ? `${this.formatDayMonth(e.datetime_start)}, ${this.formatTime(e.datetime_start)}${end}`
        : `${this.formatDayMonth(e.datetime_start)} · time not listed`;
      const cal = buildIcsContent(e) ? `<button type="button" class="linkish" data-act="ics">${icon('file')}Other calendar (.ics)</button>` : '';
      rows.push(['When', esc(when), (this.isEventPast(e) ? 'Already started · ' : '') + cal]);
      const addr = storeAddress(e);
      const km = this.storeDistanceLabel(e.store);
      const whereSub = [addr ? esc(addr) : '', km ? esc(km + ' from you') : ''].filter(Boolean).join(' · ');
      rows.push(['Where', esc(e.store), whereSub]);
      const players = attendanceDetail(e);
      if (players) rows.push(['Players', esc(players[0].toUpperCase() + players.slice(1)), '']);
      const fmt = [TCG.eventFormat(e), e.format_official && e.format_official !== e.format ? e.format_official : null,
        e.best_of ? `best of ${e.best_of}` : null].filter(Boolean);
      if (fmt.length) rows.push(['Format', esc(fmt.join(' · ')), '']);
      return `<dl class="facts">` + rows.map(([k, v, sub]) =>
        `<dt>${k}</dt><dd>${v}${sub ? `<small>${sub}</small>` : ''}</dd>`).join('') + `</dl>`;
    },

    // Up to three more events at the same store this week (still to come).
    moreAtStore(e) {
      const key = eventKey(e);
      const all = this.weekBase.filter(x => x.store === e.store)
        .sort((a, b) => a.datetime_start.localeCompare(b.datetime_start));
      const next = all.filter(x => eventKey(x) !== key && !this.isEventPast(x)).slice(0, 3);
      return { next, total: all.length };
    },

    moreHtml(e) {
      const { next, total } = this.moreAtStore(e);
      if (!next.length) return '';
      return `<aside class="more"><h4>More at ${esc(e.store)} this week</h4><ul>`
        + next.map(x => `<li><button type="button" class="more-row" data-act="goto" data-target="${esc(eventKey(x))}">`
          + `<b>${esc(this.formatDayShort(x.datetime_start))}</b><span>${esc(x.title)}</span>`
          + `<i class="dot ${this.getGameClass(x.game)}" title="${esc(gameLabel(x.game))}"></i></button></li>`).join('')
        + `</ul>`
        + (total > 1 ? `<button type="button" class="more-all" data-act="store-all">All ${total} events at this store →</button>` : '')
        + `</aside>`;
    },

    // Notes about a start time, shown next to the source link ('' when none):
    // corrected by the pipeline (pipeline/time_fixes.py, time_corrected) →
    // what the source said; kept but doubtful (time_uncertain) → a warning.
    sourceTimeNote(e) {
      if (!e) return '';
      if (e.time_corrected && e.datetime_start_source) return 'Source says ' + this.formatTime(e.datetime_start_source);
      if (e.time_uncertain) return 'Time may be wrong · Source says ' + this.formatTime(e.datetime_start);
      return '';
    },

    sourceNoteHtml(e, { block = false } = {}) {
      const note = this.sourceTimeNote(e);
      if (!note) return '';
      const tag = block ? 'p' : 'span';
      const uncertain = !e.time_corrected;
      return `<${tag} class="src-note${uncertain ? ' is-uncertain' : ''}">${icon(uncertain ? 'warn' : 'clock')}${esc(note)}</${tag}>`;
    },

    // On the card: only the time and this small mark.
    timeFixIcon(e) {
      const note = this.sourceTimeNote(e);
      if (!note) return '';
      const uncertain = !e.time_corrected;
      const label = uncertain ? note.replace(' · ', '. ') : `Time corrected. ${note} (a missing PM)`;
      return `<span class="time-fix${uncertain ? ' is-uncertain' : ''}" role="img" aria-label="${esc(label)}" title="${esc(label)}">${icon(uncertain ? 'warn' : 'clock')}</span>`;
    },

    sourceHtml(e, { row = false } = {}) {
      const url = safeUrl(e.source_url);
      if (!url) return '';
      const domain = esc(this.formatDomain(url));
      return row
        ? `<a class="src-row" href="${esc(url)}" target="_blank" rel="noopener"><span>Source</span><b>${domain}</b>${icon('ext')}</a>`
        : `<a class="src" href="${esc(url)}" target="_blank" rel="noopener">Source: ${domain} ${icon('ext')}</a>`;
    },

    // Details of the open card (Day view, desktop): a full-width panel
    // inserted after the card's row, so no cell above it is left empty.
    detailHtml(e) {
      const key = eventKey(e);
      const fav = this.favorites.includes(key);
      const cal = buildGoogleCalendarUrl(e);
      return `<section class="evx ${this.getGameClass(e.game)}${fav ? ' is-saved' : ''}" id="${this.detailId(key)}" data-key="${esc(key)}" aria-label="${esc(e.title)}">`
        + `<header class="evx-head" data-act="close">`
        +   `<div class="evx-time">${esc(this.cardTime(e))}${this.timeFixIcon(e)}</div>`
        +   `<div class="ev-main">`
        +     `<h4 class="ev-title evx-title" tabindex="-1">${esc(e.title)}</h4>`
        +     this.storeLine(e, { full: true })
        +     `<div class="ev-tags">${this.tagsHtml(e)}</div>`
        +   `</div>`
        +   `<button type="button" class="ib" data-act="close" aria-label="Collapse" title="Collapse (Esc)">${icon('up')}</button>`
        + `</header>`
        + `<div class="evx-body" data-act="noop">`
        +   `<div>${this.factsHtml(e)}</div>`
        +   this.moreHtml(e)
        + `</div>`
        + `<footer class="evx-acts" data-act="noop">`
        +   (cal ? `<button type="button" class="btn btn-cta" data-act="gcal">${icon('cal')}Add to Google Calendar</button>` : '')
        +   `<a class="btn" href="${esc(this.eventDirectionsUrl(e))}" target="_blank" rel="noopener">${icon('pin')}Directions</a>`
        +   `<button type="button" class="btn${fav ? ' is-on' : ''}" data-act="fav" aria-pressed="${fav}">${icon('star')}${fav ? 'Saved' : 'Save'}</button>`
        +   `<button type="button" class="btn" data-act="share">${icon('share')}Share</button>`
        +   `<span class="sp"></span>${this.sourceNoteHtml(e)}${this.sourceHtml(e)}`
        + `</footer>`
        + `</section>`;
    },

    // The sheet's scrolling body (phones; Week/Map views on desktop).
    sheetBodyHtml(e) {
      if (!e) return '';
      return `<div class="sh-when"><b>${esc(this.cardTime(e))}</b> ${esc(this.formatDayMonth(e.datetime_start))}</div>`
        + `<h2 class="sh-title" id="sheet-title" tabindex="-1">${esc(e.title)}</h2>`
        + `<div class="ev-tags">${this.tagsHtml(e)}</div>`
        + this.factsHtml(e)
        + this.sourceHtml(e, { row: true })
        + this.sourceNoteHtml(e, { block: true })
        + this.moreHtml(e);
    },

    // Cards of one section; the open card's details follow the last card
    // of its row (rows = this.cols cards).
    cardsHtml(events, opts = {}) {
      const at = opts.compact || !this.openKey ? -1 : events.findIndex(e => eventKey(e) === this.openKey);
      const rowEnd = at < 0 ? -1 : Math.min(events.length - 1, (Math.floor(at / this.cols) + 1) * this.cols - 1);
      return events.map((e, i) => this.cardHtml(e, opts) + (i === rowEnd ? this.detailHtml(events[at]) : '')).join('');
    },

    // Delegated clicks on any card container (data-act on the target).
    onCards(ev, { compact = false } = {}) {
      if (ev.target.closest('a')) return;                      // links do their thing
      const card = ev.target.closest('[data-key]');
      const actEl = ev.target.closest('[data-act]');
      const inSheet = !card && ev.target.closest('.sheet');
      const key = card ? card.dataset.key : (inSheet && this.sheetEvent ? eventKey(this.sheetEvent) : null);
      const e = key && this.eventByKey(key);
      if (!e) return;
      const act = actEl ? actEl.dataset.act : 'open';
      switch (act) {
        case 'noop': return;
        case 'fav':
          this.toggleFavorite(e);
          this.refocus(key, 'fav', ev.target.closest('.evx') ? '.evx' : '.ev');
          return;
        case 'gcal': this.openCalendar(e); return;
        case 'ics': downloadIcs(e); return;
        case 'share': this.shareEvent(e); return;
        case 'close': this.collapseCard(); return;
        case 'store-all':
          this.filters.store = [e.store];
          this.closeSheet();
          this.collapseCard({ focus: false });
          this.cleanupFilters({ syncUrl: true });
          this.flash(`Showing ${e.store} only · Clear in Filters`);
          return;
        case 'goto': {
          const other = this.eventByKey(actEl.dataset.target);
          if (other) this.showEvent(other);
          return;
        }
        default:
          if (compact || this.isPhone || this.viewMode !== 'day') this.openSheet(e);
          else if (this.openKey === key) this.collapseCard();   // second click on the active card
          else this.expandCard(e);
      }
    },

    // After an x-html re-render the clicked button is a new node: give the
    // new one focus so keyboard users don't fall back to <body>.
    refocus(key, act, scope = '') {
      this.$nextTick(() => {
        if (document.activeElement && document.activeElement !== document.body && document.contains(document.activeElement)) return;
        const el = document.querySelector(`${scope}[data-key="${CSS.escape(key)}"] [data-act="${act}"]`)
          || (this.sheetEvent && document.querySelector(`.sheet [data-act="${act}"]`));
        if (el) el.focus();
      });
    },

    // Show an event wherever it lives: its day in the Day view (expanded
    // in place on desktop), or the sheet.
    showEvent(e) {
      const day = e.datetime_start.slice(0, 10);
      const [y, m, d] = day.split('-').map(Number);
      this.weekStart = weekMonday(new Date(y, m - 1, d));
      this.selectedDay = day;
      if (this.viewMode === 'day' && !this.isPhone) {
        this.closeSheet();
        this.expandCard(e);
      } else {
        this.openSheet(e);
      }
    },

    expandCard(e) {
      const key = eventKey(e);
      // Its section must be open for the card to show.
      this.secOpen[e.datetime_start.slice(0, 10) + ':' + TCG.sectionOf(e)] = true;
      this.openKey = key;
      this.syncEventParam(e);
      this.$nextTick(() => {
        const panel = document.querySelector(`.evx[data-key="${CSS.escape(key)}"]`);
        if (!panel) return;
        panel.querySelector('.evx-title').focus({ preventScroll: true });
        const r = panel.getBoundingClientRect();
        if (r.top < 0 || r.bottom > window.innerHeight) panel.scrollIntoView({ block: 'nearest', behavior: scrollBehavior() });
      });
    },

    collapseCard({ focus = true } = {}) {
      const key = this.openKey;
      if (!key) return;
      this.openKey = null;
      this.clearEventParam();
      if (focus) this.$nextTick(() => {
        const btn = document.querySelector(`.ev[data-key="${CSS.escape(key)}"] .ev-open`);
        if (btn) btn.focus({ preventScroll: true });
      });
    },

    /* ── Sheet ─────────────────────────────────────────────────────── */
    openSheet(e) {
      if (!this.sheetEvent) UI.sheetOpener = document.activeElement;
      this.sheetEvent = e;
      this.syncEventParam(e);
      this.lockScroll();
      this.$nextTick(() => {
        const body = document.querySelector('.sheet-body');
        if (body) body.scrollTop = 0;
        focusSoon(() => document.getElementById('sheet-title'));
      });
    },

    closeSheet() {
      if (!this.sheetEvent) return;
      const key = eventKey(this.sheetEvent);
      this.sheetEvent = null;
      this.clearEventParam();
      this.unlockScroll();
      const back = UI.sheetOpener;
      UI.sheetOpener = null;
      this.$nextTick(() => {
        const el = (back && document.contains(back) && back !== document.body) ? back
          : document.querySelector(`[data-key="${CSS.escape(key)}"] .ev-open`);
        if (el) el.focus({ preventScroll: true });
      });
    },

    // Page scroll stays put under a sheet or the filters panel.
    lockScroll() { document.documentElement.classList.add('sheet-open'); },
    unlockScroll() {
      if (!this.sheetEvent && !this.filtersOpen) document.documentElement.classList.remove('sheet-open');
    },

    // Swipe down to close (sheet and filters on phones). Starts only from
    // the grip, or from the body when it is scrolled to the top.
    dragStart(ev, el) {
      const body = el.querySelector('.sheet-body, .fp-body');
      const fromGrip = ev.target.closest('.grip');
      if (!fromGrip && body && body.scrollTop > 0) return;
      UI.drag = { el, y: ev.touches[0].clientY, dy: 0 };
    },
    dragMove(ev) {
      const d = UI.drag;
      if (!d) return;
      d.dy = Math.max(0, ev.touches[0].clientY - d.y);
      if (d.dy > 4) d.el.style.transform = `translateY(${d.dy}px)`;
    },
    dragEnd(close) {
      const d = UI.drag;
      UI.drag = null;
      if (!d) return;
      d.el.style.transform = '';
      if (d.dy > 90) close();
    },

    // Status line. With `undo`, it offers Undo for 5 seconds (kept open
    // while the pointer or focus is on it).
    flash(text, undo = null) {
      UI.undo = undo;
      this.toast = { text, undo: !!undo };
      this.armToast(undo ? 5000 : 2600);
    },
    armToast(ms) {
      clearTimeout(UI.toastTimer);
      UI.toastTimer = setTimeout(() => { this.toast = null; UI.undo = null; }, ms);
    },
    holdToast() { clearTimeout(UI.toastTimer); },
    undoToast() {
      const undo = UI.undo;
      this.toast = null;
      UI.undo = null;
      clearTimeout(UI.toastTimer);
      if (undo) undo();
    },

    // Updates the shadow on the Filters footer: on while content is below it.
    updateFpMore() {
      const b = document.querySelector('.fp-body');
      this.fpMore = !!b && b.scrollTop + b.clientHeight < b.scrollHeight - 1;
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
          gameLabels: ranked.map(gameLabel),
          mix: ranked.map(game => ({ game, n: games[game] })),
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

    // Games in the map's results, most events first: the pin colour key.
    get mapLegend() {
      const counts = {};
      for (const e of this.mapEvents) counts[e.game] = (counts[e.game] || 0) + 1;
      return Object.keys(counts).sort((a, b) => counts[b] - counts[a])
        .map(game => ({ game, label: gameLabel(game), n: counts[game], color: gameColor(game) }));
    },

    pinStyle(mix) {
      return 'background:' + donutBackground(mix);
    },

    get mapSelected() {
      return this.mapStore ? this.mapStores.find(s => s.name === this.mapStore) || null : null;
    },

    // Changes only when the question changes; see renderStoreMap.
    get mapFitKey() {
      return JSON.stringify([this.city, isoDay(this.weekStart), this.mapDay, this.filters,
        this.showFavoritesOnly, this.nearMe, this.km, this.events.length]);
    },

    // Called from x-effect. Every reactive input is read synchronously so the
    // effect re-runs when any of them changes; Leaflet work waits a frame so
    // x-show has made the container visible and measurable.
    drawMap() {
      const args = {
        stores: this.mapStores.map(s => ({ name: s.name, count: s.count, mix: s.mix, lat: s.lat, lng: s.lng })),
        selected: this.mapStore,
        me: this.nearMe ? { lat: this.nearMe.lat, lng: this.nearMe.lng } : null,
        radiusKm: this.km,
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
      const { name, lat } = s;
      if (lat != null) MAP.focus = name;
      this.mapStore = name;
      if (lat != null && this.$refs.mapEl) this.$refs.mapEl.scrollIntoView({ block: 'start' });
    },

    directionsUrl(s) {
      const dest = s.lat != null ? `${s.lat},${s.lng}` : (s.address || s.name);
      return 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(dest);
    },

    // The event's own venue address first (chains have branches), then the
    // store registry's coordinates, then the name.
    eventDirectionsUrl(e) {
      const meta = STORE_META[e.store] || {};
      const dest = e.store_address ? `${e.store}, ${e.store_address}`
        : (meta.lat != null ? `${meta.lat},${meta.lng}` : (meta.address || `${e.store}, ${this.cityName}`));
      return 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(dest);
    },

    /* ── Calendar, links, sharing ──────────────────────────────────── */
    canAddToCalendar(e) {
      return Boolean(buildGoogleCalendarUrl(e));
    },

    openCalendar(e) {
      const url = buildGoogleCalendarUrl(e);
      if (url) window.open(url, '_blank', 'noopener');
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
      url.searchParams.set('event', encodeEventParam(eventKey(e)));
      url.searchParams.set('w', weekOfIso(e.datetime_start));  // which week file has it
      return url.toString();
    },

    syncEventParam(e) {
      if (!e) return;
      const url = new URL(window.location.href);
      url.searchParams.set('event', encodeEventParam(eventKey(e)));
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
      const done = () => this.flash('Link copied');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, () => { this.fallbackCopy(text); done(); });
      } else {
        this.fallbackCopy(text);
        done();
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
      if (navigator.share && this.isPhone) {
        navigator.share({ title: e.title || 'Event', url }).catch(() => {});
      } else {
        this.copyEventLink(e);
      }
    },

    scrollBehavior,
    icon,
    gameLabel,

    getGameClass(game) {
      return GAME_CLASS_MAP[game] || 'game-other';
    },

    /* ── Near me ───────────────────────────────────────────────────── */
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

    locate() {
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

    // Map: "Near me" sorts by distance and shows you on the map.
    toggleNearMe() {
      if (this.nearMe) {
        this.nearMe = null;
        this.km = null;
        this.geoStatus = '';
        this.cleanupFilters();
        return;
      }
      this.locate();
    },
  };
}
