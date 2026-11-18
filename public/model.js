/* ── model.js — pure calendar logic (no DOM, no Alpine) ─────────────
   Everything here is a plain function of its arguments, so it can be
   tested with `node --test tests/js` and reused by app.js:

   - eventType / eventFormat: the scraped `format` field mixes play
     formats (Standard, Commander…) with event types (Prerelease); they
     are split here, and the type is completed from `format_official`
     and the title.
   - formatFacet / pruneFormats / passesFormat: the Format block of the
     Filters panel, whose options depend on the selected games.
   - timeSlotOf / daySections / defaultOpenSection: time of day.
   - dayStrip / defaultDay: the 7-day strip of the Day view.
   - readFilterParams / writeFilterParams: filters ⇄ URL.

   Loaded in the browser as a classic script (window.TCG) and in node
   through module.exports. */
(function (root) {
  'use strict';

  /* ── Event type and format ─────────────────────────────────────── */

  const EVENT_TYPES = [
    { key: 'tournament', label: 'Tournament' },
    { key: 'release',    label: 'Release event' },
    { key: 'prerelease', label: 'Prerelease' },
    { key: 'learn',      label: 'Learn to play' },
    { key: 'casual',     label: 'Casual' },
  ];
  const TYPE_LABEL = Object.fromEntries(EVENT_TYPES.map(t => [t.key, t.label]));

  // `format` values that are event types, not ways to build a deck.
  const TYPE_FORMATS = { prerelease: 'prerelease' };

  // Titles are Spanish, English and Catalan. Order matters: a "Prerelease"
  // title that also says "casual" is still a prerelease.
  const TYPE_RULES = [
    ['prerelease', /\bpre-?(release|lanzamiento)\b|\bpresentaci[oó]n\b/i],
    ['release',    /\brelease (event|tournament)\b|\blanzamiento\b/i],
    ['learn',      /\blearn[- ]to[- ]play\b|\baprende(r)?( a)? jugar\b|\bdemo ?days?\b|\biniciaci[oó]n\b|\btutorial\b/i],
    ['casual',     /\bcasual\b|\bchill\b|\bopen play\b|\bjuego libre\b|\bpartidas libres\b|\bunstructured\b/i],
  ];
  const OFFICIAL_TYPES = { 'learn to play': 'learn', 'open play': 'casual' };
  // One store (Arte 9) labels its leagues and cups "Prerelease"; a title
  // that names a competition wins over that label.
  const COMPETITION_RE = /\b(liga|league|skirmish|cup|challenge|torneo|tournament|championship)\b/i;

  function eventType(e) {
    if (!e) return 'tournament';
    const title = String(e.title || '');
    const fromFormat = TYPE_FORMATS[String(e.format || '').toLowerCase()];
    if (fromFormat && !(COMPETITION_RE.test(title) && !TYPE_RULES[0][1].test(title))) return fromFormat;
    const official = OFFICIAL_TYPES[String(e.format_official || '').toLowerCase()];
    if (official) return official;
    for (const [key, re] of TYPE_RULES) if (re.test(title)) return key;
    return 'tournament';
  }

  // The play format, or null when the source gave none (or gave a type).
  function eventFormat(e) {
    const f = e && e.format;
    if (!f || f === 'Unknown') return null;
    if (TYPE_FORMATS[String(f).toLowerCase()]) return null;
    return f;
  }

  function gameOf(e) {
    return (e && e.game) || 'Unknown';
  }

  /* ── Formats per game ──────────────────────────────────────────── */

  // { game: Set(formats) } over the given events (the current week).
  function formatsByGame(events) {
    const out = {};
    for (const e of events) {
      const f = eventFormat(e);
      if (!f) continue;
      const g = gameOf(e);
      (out[g] || (out[g] = new Set())).add(f);
    }
    return out;
  }

  // A selected format restricts only the games that have it: with Magic +
  // One Piece and "Commander" picked, Magic shows Commander and One Piece
  // stays whole. A game none of whose formats is picked is not filtered.
  function passesFormat(e, selectedFormats, byGame) {
    if (!selectedFormats || !selectedFormats.length) return true;
    const own = byGame[gameOf(e)];
    if (!own || !selectedFormats.some(f => own.has(f))) return true;
    const f = eventFormat(e);
    return f !== null && selectedFormats.includes(f);
  }

  // Formats still offered by at least one selected game. Run when a game is
  // removed: its own formats go, shared ones stay.
  function pruneFormats(selectedFormats, games, byGame) {
    if (!games.length) return [];
    return selectedFormats.filter(f => games.some(g => byGame[g] && byGame[g].has(f)));
  }

  /* The Format block of the Filters panel.
       games     — selected games, in chip order
       byGame    — formatsByGame(current week), the options
       countable — current-week events passing every filter but format
       selected  — selected formats
     Returns one of:
       { mode: 'none' }                                  no game picked
       { mode: 'single', game, chips }
       { mode: 'multi', shared, groups: [{ game, chips }] }
     chip = { format, games, count, selected, disabled } */
  function formatFacet({ games, byGame, countable, selected }) {
    if (!games || !games.length) return { mode: 'none' };
    const counts = {};                      // "game\u0000format" → n
    for (const e of countable) {
      const f = eventFormat(e);
      if (!f) continue;
      const k = gameOf(e) + '\u0000' + f;
      counts[k] = (counts[k] || 0) + 1;
    }
    const gamesWith = {};                   // format → [games]
    for (const g of games) {
      for (const f of byGame[g] || []) (gamesWith[f] || (gamesWith[f] = [])).push(g);
    }
    // A selected format no selected game has this week stays visible
    // (count 0) so it can be switched off.
    for (const f of selected) if (!gamesWith[f]) gamesWith[f] = [];

    const chip = (format, gs) => {
      const count = gs.reduce((n, g) => n + (counts[g + '\u0000' + format] || 0), 0);
      const isSel = selected.includes(format);
      return { format, games: gs, count, selected: isSel, disabled: count === 0 && !isSel };
    };
    const byCount = (a, b) => b.count - a.count || a.format.localeCompare(b.format);

    if (games.length === 1) {
      const chips = Object.keys(gamesWith).map(f => chip(f, gamesWith[f])).sort(byCount);
      return { mode: 'single', game: games[0], chips };
    }
    const shared = [], orphans = [];
    const groups = games.map(g => ({ game: g, chips: [] }));
    for (const [f, gs] of Object.entries(gamesWith)) {
      if (gs.length > 1) shared.push(chip(f, gs));
      else if (gs.length === 1) groups[games.indexOf(gs[0])].chips.push(chip(f, gs));
      else orphans.push(chip(f, gs));
    }
    groups.forEach(gr => gr.chips.sort(byCount));
    return {
      mode: 'multi',
      shared: shared.concat(orphans).sort(byCount),
      groups: groups.filter(gr => gr.chips.length),
    };
  }

  /* ── Time of day ───────────────────────────────────────────────── */

  // Spanish hours: lunch ends the morning at 14:00, evening play starts at
  // 17:00. One set of boundaries for the filter, the day list and the week
  // grid. No separate "Late": events from 20:00 are 1–2% of a week across
  // the cities (Málaga peaks near 10%), too few for a section of their own.
  const TIME_SLOTS = [
    { key: 'morning',   label: 'Morning',   start: 0,  end: 14, words: 'until 14:00' },
    { key: 'afternoon', label: 'Afternoon', start: 14, end: 17, words: '14:00 – 17:00' },
    { key: 'evening',   label: 'Evening',   start: 17, end: 30, words: 'from 17:00' },
  ];
  // Sections of the day list (and rows of the week grid): the slots, plus
  // events whose source gave no time.
  const DAY_SECTIONS = [{ key: 'unlisted', label: 'Time not listed', words: '' }].concat(TIME_SLOTS);

  function hhmm(e) {
    const s = (e && e.datetime_start) || '';
    return [parseInt(s.slice(11, 13), 10), parseInt(s.slice(14, 16), 10)];
  }

  // Midnight sharp is what sources send for "no time given".
  function hasTime(e) {
    const [h, m] = hhmm(e);
    return !(h === 0 && m === 0);
  }

  function timeSlotOf(e) {
    if (!hasTime(e)) return 'unlisted';
    const [h] = hhmm(e);
    return TIME_SLOTS.find(s => h >= s.start && h < s.end).key;
  }

  function sectionOf(e) {
    return timeSlotOf(e);
  }

  function passesTime(e, selectedSlots) {
    if (!selectedSlots || !selectedSlots.length) return true;
    return selectedSlots.includes(timeSlotOf(e));
  }

  // Non-empty sections of one day's (sorted) events.
  function daySections(events) {
    const buckets = {};
    for (const e of events) (buckets[sectionOf(e)] || (buckets[sectionOf(e)] = [])).push(e);
    return DAY_SECTIONS.filter(s => buckets[s.key]).map(s => ({
      key: s.key, label: s.label, words: s.words,
      events: buckets[s.key],
      count: buckets[s.key].length,
    }));
  }

  // Which section starts open. Today: the one with the next event still to
  // start (all done: the last one). Other days: Evening, when it has events.
  function defaultOpenSection(sections, { isToday, nowMinutes }) {
    if (!sections.length) return null;
    if (isToday) {
      for (const s of sections) {
        if (s.key === 'unlisted') continue;
        if (s.events.some(e => { const [h, m] = hhmm(e); return h * 60 + m >= nowMinutes; })) return s.key;
      }
      return sections[sections.length - 1].key;
    }
    const evening = sections.find(s => s.key === 'evening');
    return (evening || sections.find(s => s.key !== 'unlisted') || sections[0]).key;
  }

  /* ── Day strip ─────────────────────────────────────────────────── */

  // days = [{ iso, count }] (7). load = share of the busiest day, 0…1.
  function dayStrip(days, todayIso) {
    const max = Math.max(0, ...days.map(d => d.count));
    return days.map(d => Object.assign({}, d, {
      load: max ? d.count / max : 0,
      isToday: d.iso === todayIso,
      isPast: d.iso < todayIso,
    }));
  }

  // Today when it is in the week, else its Monday.
  function defaultDay(weekDaysIso, todayIso) {
    return weekDaysIso.includes(todayIso) ? todayIso : weekDaysIso[0];
  }

  /* ── URL ───────────────────────────────────────────────────────── */

  // Filter fields kept in the URL (comma lists) plus the search box (q).
  const URL_LISTS = ['game', 'store', 'format', 'type', 'time'];

  function readFilterParams(params) {
    const out = { search: params.get('q') || '' };
    for (const f of URL_LISTS) {
      const raw = params.get(f);
      out[f] = raw ? [...new Set(raw.split(',').map(v => v.trim()).filter(Boolean))] : [];
    }
    out.type = out.type.filter(t => TYPE_LABEL[t]);
    out.time = out.time.filter(t => TIME_SLOTS.some(s => s.key === t));
    return out;
  }

  function writeFilterParams(params, filters) {
    for (const f of URL_LISTS) {
      const v = filters[f] || [];
      if (v.length) params.set(f, v.join(','));
      else params.delete(f);
    }
    const q = String(filters.search || '').trim();
    if (q) params.set('q', q); else params.delete('q');
    return params;
  }

  // Number shown on the Filters button: everything the panel hides
  // (games have their own chips on the main screen).
  function activeFilterCount(filters, km) {
    return ['store', 'format', 'type', 'time'].reduce((n, f) => n + (filters[f] || []).length, 0) + (km ? 1 : 0);
  }

  const api = {
    EVENT_TYPES, TYPE_LABEL, TIME_SLOTS, DAY_SECTIONS, URL_LISTS,
    eventType, eventFormat, gameOf, formatsByGame, passesFormat, pruneFormats, formatFacet,
    hasTime, timeSlotOf, sectionOf, passesTime, daySections, defaultOpenSection,
    dayStrip, defaultDay, readFilterParams, writeFilterParams, activeFilterCount,
  };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.TCG = api;
})(typeof window !== 'undefined' ? window : globalThis);
