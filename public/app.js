/* ── Helpers ───────────────────────────────────────────────────────── */

const PRESETS_STORAGE_KEY = 'tcg-presets-v1';

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

  const location = (STORE_META[e.store] && STORE_META[e.store].address) || STORE_ADDRESSES[e.store] || e.store || '';
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

  const location = (STORE_META[e.store] && STORE_META[e.store].address) || STORE_ADDRESSES[e.store] || e.store || '';

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

  const location = (STORE_META[e.store] && STORE_META[e.store].address) || STORE_ADDRESSES[e.store] || e.store || '';
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

function eventKey(e) {
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

/* ── Alpine app ────────────────────────────────────────────────────── */
function app() {
  return {
    events:       [],
    loading:      true,
    weekStart:    weekMonday(new Date()),
    filters:      { search: '', game: [], store: [], format: [] },
    savedPresets: [],
    segmentFilter: { morning: true, afternoon: true, evening: true, late: true },
    openFacet: null,
    // Independent of segmentFilter: collapsed segments still occupy a
    // grid row (alignment preserved) but their cards are hidden.
    collapsedSegments: { morning: false, afternoon: false, evening: false, late: false },
    SEGMENTS,
    showBackToTop: false,
    selectedEvent: null,
    panelOpen: false,
    calendarMenuOpen: false,
    // Reactive Europe/Madrid clock — refreshed every minute. Used to
    // mark past segments and past events on today.
    nowMadrid: readMadridNow(),

    // Default to horizontal; respect localStorage if user previously
    // switched to vertical. Key is versioned so that the old default-
    // vertical era's persisted 'vertical' values don't carry over.
    viewMode: localStorage.getItem('tcg-view-v2') || 'horizontal',

    /* ── Init ──────────────────────────────────────────────────────── */
    async init() {
      this.loadPresets();

      // Show Back to top after 400px scroll
      window.addEventListener('scroll', () => {
        this.showBackToTop = window.scrollY > 400;
      }, { passive: true });
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          if (this.calendarMenuOpen) {
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
      
      try {
        const res = await fetch('events.json');
        this.events = await res.json();
        this.applyFiltersFromUrl();
        this.cleanupFilters({ syncUrl: false });
        const rawEventParam = new URLSearchParams(window.location.search).get('event');
        if (rawEventParam) {
          const decoded = decodeEventParam(rawEventParam);
          const eventKey = decoded !== null ? decoded : rawEventParam;
          const event = this.events.find(ev => this.eventKey(ev) === eventKey);
          if (event) this.openPanel(event);
        }
      } catch (err) {
        console.error('Failed to load events.json', err);
      } finally {
        this.loading = false;
      }

      if (this.viewMode === 'vertical') {
        this.$nextTick(() => {
          requestAnimationFrame(() => this.scrollToCurrentSegment());
        });
      }

      this.$watch('filters.search', () => this.cleanupFilters());
    },

    setView(mode) {
      this.viewMode = mode;
      localStorage.setItem('tcg-view-v2', mode);
      if (mode === 'vertical') {
        this.$nextTick(() => {
          requestAnimationFrame(() => this.scrollToCurrentSegment());
        });
      }
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

    /* ── Navigation ────────────────────────────────────────────────── */
    prevWeek() {
      this.weekStart = addDays(this.weekStart, -7);
      this.cleanupFilters();
    },
    nextWeek() {
      this.weekStart = addDays(this.weekStart, +7);
      this.cleanupFilters();
    },
    goToday() {
      this.weekStart = weekMonday(new Date());
      this.cleanupFilters();
      // In vertical view scroll to today after Alpine re-renders the week
      if (this.viewMode === 'vertical') {
        this.$nextTick(() => this.scrollToDay(isoDay(new Date())));
      }
    },

    /* ── Faceted filter options ─────────────────────────────────────── */
    get allStores() {
      const values = new Set();
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

    applyPreset(preset) {
      if (!preset || !preset.filters) return;
      this.filters.game = Array.isArray(preset.filters.game) ? preset.filters.game.slice() : [];
      this.filters.store = Array.isArray(preset.filters.store) ? preset.filters.store.slice() : [];
      this.filters.format = Array.isArray(preset.filters.format) ? preset.filters.format.slice() : [];
      this.openFacet = null;
      this.cleanupFilters({ syncUrl: true });
    },

    deletePreset(id) {
      this.savedPresets = this.savedPresets.filter(p => p.id !== id);
      this.persistPresets();
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
        if (this.eventMatches(e, { ignore: field, includeWeek: true })) {
          values.add(this.valueFor(field, e));
        }
      }
      return [...values].sort();
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
      const valid = { game: new Set(), store: new Set(), format: new Set() };
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

    toggleFilter(field, value) {
      if (!value) return;
      const values = this.selectedValues(field).slice();
      const idx = values.indexOf(value);
      if (idx >= 0) values.splice(idx, 1);
      else values.push(value);
      this.filters[field] = values;
      this.cleanupFilters({ syncUrl: true });
    },

    /* ── Toggle a time-segment chip ─────────────────────────────────── */
    toggleSegment(key) {
      this.segmentFilter[key] = !this.segmentFilter[key];
      this.cleanupFilters();
    },

    /* ── Toggle a segment's collapsed state (cards hidden, header
         visible). Independent of segmentFilter — collapse keeps the
         row in the grid so alignment is preserved. */
    toggleSegmentCollapse(key) {
      this.collapsedSegments[key] = !this.collapsedSegments[key];
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

    isInCurrentWeek(e) {
      const d = new Date(e.datetime_start);
      const weekEnd = addDays(this.weekStart, 7);
      return d >= this.weekStart && d < weekEnd;
    },

    eventMatches(e, opts = {}) {
      const ignore = opts.ignore || null;
      const includeSegment = opts.includeSegment !== false;
      const includeWeek = opts.includeWeek === true;
      const s = this.filters.search.toLowerCase().trim();
      if (s && !e.title.toLowerCase().includes(s)) return false;
      if (includeWeek && !this.isInCurrentWeek(e)) return false;
      if (includeSegment && !this.segmentFilter[this.segmentOf(e.datetime_start)]) return false;
      for (const field of ['game', 'store', 'format']) {
        if (ignore === field) continue;
        const selected = this.selectedValues(field);
        if (selected.length && !selected.includes(this.valueFor(field, e))) return false;
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
      const todayISO = isoDay(new Date());
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
          isToday: iso === todayISO,
          events: evs,
          segments,
          segmentMap: buckets,
        });
      }
      return days;
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

    eventKey(e) {
      return eventKey(e);
    },

    eventUrl(e) {
      const url = new URL(window.location.href);
      url.searchParams.set('event', this.eventKey(e));
      return url.toString();
    },

    syncEventParam(e) {
      const url = new URL(window.location.href);
      url.searchParams.set('event', this.eventKey(e));
      history.replaceState(null, '', url.toString());
    },

    clearEventParam() {
      const url = new URL(window.location.href);
      url.searchParams.delete('event');
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

    buildShareText(e) {
      if (!e) return '';
      const lines = [
        e.title,
        e.game || 'Unknown game',
        e.format || 'Unknown format',
      ];
      if (e.datetime_start) {
        const date = this.formatDateLong(e.datetime_start);
        const time = this.formatTime(e.datetime_start);
        lines.push(`${date} at ${time}`);
      }
      lines.push(e.store || 'Unknown store');
      const address = (STORE_META[e.store] && STORE_META[e.store].address) || STORE_ADDRESSES[e.store];
      if (address) lines.push(address);
      if (e.source_url) lines.push(e.source_url);
      return lines.join('\n');
    },

    shareEvent(e) {
      if (!e) return;
      const url = this.eventUrl(e);
      const text = this.buildShareText(e);
      if (navigator.share) {
        navigator.share({
          title: e.title || 'Event',
          text: text,
          url: url,
        }).catch(() => {
          this.copyEventLink(e);
        });
      } else {
        this.copyEventLink(e);
      }
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

    storeMeta(storeName) {
      return STORE_META[storeName] || null;
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
      const url = new URL(window.location.href);
      url.searchParams.set('event', encodeEventParam(this.eventKey(e)));
      return url.toString();
    },

    syncEventParam(e) {
      if (!e) return;
      const url = new URL(window.location.href);
      url.searchParams.set('event', encodeEventParam(this.eventKey(e)));
      history.replaceState(null, '', url.toString());
    },

    clearEventParam() {
      const url = new URL(window.location.href);
      url.searchParams.delete('event');
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

    buildShareText(e) {
      if (!e) return '';
      const lines = [
        e.title,
        e.game || 'Unknown game',
        e.format || 'Unknown format',
      ];
      if (e.datetime_start) {
        const date = this.formatDateLong(e.datetime_start);
        const time = this.formatTime(e.datetime_start);
        lines.push(`${date} at ${time}`);
      }
      lines.push(e.store || 'Unknown store');
      const address = (STORE_META[e.store] && STORE_META[e.store].address) || STORE_ADDRESSES[e.store];
      if (address) lines.push(address);
      if (e.source_url) lines.push(e.source_url);
      return lines.join('\n');
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
        const url = e.source_url || '';
        const calendarUrl = buildGoogleCalendarUrl(e);
        const calendarButton = calendarUrl
          ? `<button class="calendar-btn" type="button" data-calendar-url="${esc(calendarUrl)}" title="Add to Google Calendar" aria-label="Add to Google Calendar">` +
              `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
                `<path d="M8 2v4"></path><path d="M16 2v4"></path><rect x="3" y="4" width="18" height="18" rx="2"></rect><path d="M3 10h18"></path>` +
              `</svg>` +
              `<span>GCal</span>` +
            `</button>`
          : '';
        out.push(
          `<div class="ev-card ${gameClass}${past ? ' is-past' : ''}" data-event-key="${esc(eventKey(e))}">` +
            `<div class="ev-card-top">` +
              `<div class="ev-time">${esc(time)}</div>` +
              `<div class="ev-store-top${this.isSelected('store', e.store) ? ' active' : ''}" data-filter="store" data-value="${esc(e.store)}" title="${esc(e.store)}">${esc(e.store)}</div>` +
            `</div>` +
            `<div class="ev-title">${esc(e.title)}</div>` +
            `<div class="ev-footer">` +
              `<div class="ev-tags">` +
                `<span class="tag game${this.isSelected('game', game) ? ' active' : ''}" data-filter="game" data-value="${esc(game)}">${esc(game)}</span>` +
                `<span class="tag fmt${this.isSelected('format', fmt) ? ' active' : ''}" data-filter="format" data-value="${esc(fmt)}">${esc(fmt)}</span>` +
              `</div>` +
              calendarButton +
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

    resetFilters() {
      this.filters = { search: '', game: [], store: [], format: [] };
      this.openFacet = null;
      this.cleanupFilters({ syncUrl: true });
    },
  };
}
