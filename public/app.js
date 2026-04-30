/* ── Helpers ───────────────────────────────────────────────────────── */

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

  const location = STORE_ADDRESSES[e.store] || e.store || '';
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
    segmentFilter: { morning: true, afternoon: true, evening: true, late: true },
    openFacet: null,
    // Independent of segmentFilter: collapsed segments still occupy a
    // grid row (alignment preserved) but their cards are hidden.
    collapsedSegments: { morning: false, afternoon: false, evening: false, late: false },
    SEGMENTS,
    showBackToTop: false,
    // Reactive Europe/Madrid clock — refreshed every minute. Used to
    // mark past segments and past events on today.
    nowMadrid: readMadridNow(),

    // Default to horizontal; respect localStorage if user previously
    // switched to vertical. Key is versioned so that the old default-
    // vertical era's persisted 'vertical' values don't carry over.
    viewMode: localStorage.getItem('tcg-view-v2') || 'horizontal',

    /* ── Init ──────────────────────────────────────────────────────── */
    async init() {
      // Show Back to top after 400px scroll
      window.addEventListener('scroll', () => {
        this.showBackToTop = window.scrollY > 400;
      }, { passive: true });
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') this.openFacet = null;
      });

      if (!localStorage.getItem('tcg-view-v2') && window.matchMedia('(max-width: 900px)').matches) {
        this.viewMode = 'vertical';
      }

      // Run once immediately, then refresh the Madrid clock every minute
      // so past-segment / past-event styling progresses without a reload.
      this.nowMadrid = readMadridNow();
      setInterval(() => { this.nowMadrid = readMadridNow(); }, 60_000);

      if (this.viewMode === 'vertical') {
        this.$nextTick(() => this.scrollToCurrentSegment());
      }

      try {
        const res = await fetch('events.json');
        this.events = await res.json();
        this.cleanupFilters();
      } catch (err) {
        console.error('Failed to load events.json', err);
      } finally {
        this.loading = false;
      }

      this.$watch('filters.search', () => this.cleanupFilters());
    },

    setView(mode) {
      this.viewMode = mode;
      localStorage.setItem('tcg-view-v2', mode);
      if (mode === 'vertical') {
        this.$nextTick(() => this.scrollToCurrentSegment());
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
      const el = document.getElementById('seg-' + targetSeg.key);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
    get gameOptions() {
      return this.availableOptions('game');
    },
    get storeOptions() {
      return this.availableOptions('store');
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
      this.cleanupFilters();
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

    cleanupFilters() {
      for (const field of ['game', 'store', 'format']) {
        const allowed = new Set(this.availableOptions(field));
        const next = this.selectedValues(field).filter(v => allowed.has(v));
        if (next.length !== this.selectedValues(field).length) {
          this.filters[field] = next;
        }
      }
    },

    /* ── Toggle a filter chip (game/store/format) ───────────────────── */
    // If selected, remove it; otherwise add it to the facet group.
    toggleFilter(field, value) {
      if (!value) return;
      const values = this.selectedValues(field).slice();
      const idx = values.indexOf(value);
      if (idx >= 0) values.splice(idx, 1);
      else values.push(value);
      this.filters[field] = values;
      this.cleanupFilters();
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

    openEvent(e) {
      if (e.source_url) window.open(e.source_url, '_blank', 'noopener');
    },

    canAddToCalendar(e) {
      return Boolean(buildGoogleCalendarUrl(e));
    },

    openCalendar(e) {
      const url = buildGoogleCalendarUrl(e);
      if (url) window.open(url, '_blank', 'noopener');
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
              `<span>Cal</span>` +
            `</button>`
          : '';
        out.push(
          `<div class="ev-card ${gameClass}${past ? ' is-past' : ''}" data-url="${esc(url)}">` +
            `<div class="ev-time">${esc(time)}</div>` +
            `<div class="ev-title">${esc(e.title)}</div>` +
            `<div class="ev-footer">` +
              `<div class="ev-tags">` +
                `<span class="tag game${this.isSelected('game', game) ? ' active' : ''}" data-filter="game" data-value="${esc(game)}">${esc(game)}</span>` +
                `<span class="tag store${this.isSelected('store', e.store) ? ' active' : ''}" data-filter="store" data-value="${esc(e.store)}">${esc(e.store)}</span>` +
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
         Card click (anywhere else inside .ev-card) → open source_url. */
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
      const card = ev.target.closest('.ev-card[data-url]');
      if (card && card.dataset.url) {
        window.open(card.dataset.url, '_blank', 'noopener');
      }
    },

    getGameClass(game) {
      return GAME_CLASS_MAP[game] || 'game-unknown';
    },

    resetFilters() {
      this.filters = { search: '', game: [], store: [], format: [] };
      this.openFacet = null;
    },
  };
}
