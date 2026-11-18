// Run: node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const M = require('../../public/model.js');

const ev = (game, format, extra = {}) => ({
  id: Math.random().toString(36).slice(2),
  game, format, title: extra.title || `${game} ${format || ''}`,
  datetime_start: extra.at || '2026-11-18T18:00:00+01:00',
  ...extra,
});

/* ── Type / format split ─────────────────────────────────────────── */

test('Prerelease in `format` is a type, not a format', () => {
  const e = ev('One Piece', 'Prerelease', { title: 'Prerelease OP18' });
  assert.equal(M.eventType(e), 'prerelease');
  assert.equal(M.eventFormat(e), null);
});

test('type comes from format_official, then the title', () => {
  assert.equal(M.eventType(ev('Pokémon', 'Standard', { format_official: 'Learn to Play' })), 'learn');
  assert.equal(M.eventType(ev('Riftbound', 'Standard', { format_official: 'Open Play' })), 'casual');
  assert.equal(M.eventType(ev('Magic: The Gathering', 'Commander', { title: 'Casual commander' })), 'casual');
  assert.equal(M.eventType(ev('Lorcana', 'Standard', { title: 'Demo Day - Next Dice' })), 'learn');
  assert.equal(M.eventType(ev('One Piece', 'Standard', { title: 'ONE PIECE CARD GAME OP-18 Release Event' })), 'release');
  assert.equal(M.eventType(ev('Magic: The Gathering', 'Prerelease', { title: 'PRESENTACION 2HG' })), 'prerelease');
  assert.equal(M.eventType(ev('One Piece', 'Standard', { title: 'OP Store Tournament Event' })), 'tournament');
  assert.equal(M.eventFormat(ev('Magic: The Gathering', 'Commander')), 'Commander');
  // A competition mislabelled "Prerelease" by its source is a tournament.
  assert.equal(M.eventType(ev('Riftbound', 'Prerelease', { title: 'Liga de Riftbound – 12 Jornadas + Top' })), 'tournament');
  assert.equal(M.eventType(ev('Pokémon', 'Prerelease', { title: 'League Cup Pokémon' })), 'tournament');
  assert.equal(M.eventType(ev('One Piece', 'Prerelease', { title: 'TCG One Piece PRERELEASE OP18 BO3' })), 'prerelease');
  assert.equal(M.eventFormat(ev('Magic: The Gathering', undefined)), null);
});

/* ── Format facet: the three game-selection states ───────────────── */

const week = [
  ev('Magic: The Gathering', 'Commander'), ev('Magic: The Gathering', 'Commander'),
  ev('Magic: The Gathering', 'Standard'), ev('Magic: The Gathering', 'Pauper'),
  ev('Magic: The Gathering', 'Prerelease'),
  ev('One Piece', 'Standard'), ev('One Piece', 'Standard'), ev('One Piece', 'Sealed'),
  ev('Pokémon', 'Standard'),
  ev('Lorcana', 'Infinity'),
];
const byGame = M.formatsByGame(week);

test('formats come from the data, per game', () => {
  assert.deepEqual([...byGame['Magic: The Gathering']].sort(), ['Commander', 'Pauper', 'Standard']);
  assert.deepEqual([...byGame['One Piece']].sort(), ['Sealed', 'Standard']);
});

test('state 1: no game selected → no format block', () => {
  assert.deepEqual(M.formatFacet({ games: [], byGame, countable: week, selected: [] }), { mode: 'none' });
});

test('state 2: one game → flat list with counts, most events first', () => {
  const f = M.formatFacet({ games: ['Magic: The Gathering'], byGame, countable: week, selected: [] });
  assert.equal(f.mode, 'single');
  assert.deepEqual(f.chips.map(c => [c.format, c.count]), [['Commander', 2], ['Pauper', 1], ['Standard', 1]]);
});

test('state 3: several games → shared formats merged, the rest grouped by game', () => {
  const games = ['One Piece', 'Magic: The Gathering', 'Pokémon'];
  const f = M.formatFacet({ games, byGame, countable: week, selected: ['Commander'] });
  assert.equal(f.mode, 'multi');
  assert.equal(f.shared.length, 1);
  assert.deepEqual(f.shared[0].games, games);       // Standard: all three
  assert.equal(f.shared[0].count, 4);               // 1 + 2 + 1
  assert.deepEqual(f.groups.map(g => g.game), ['One Piece', 'Magic: The Gathering']); // Pokémon: nothing of its own
  const magic = f.groups[1].chips;
  assert.deepEqual(magic.map(c => c.format), ['Commander', 'Pauper']);
  assert.equal(magic[0].selected, true);
});

test('counts follow the other filters; 0 disables unless selected', () => {
  // Other filters left only One Piece Standard events.
  const countable = week.filter(e => e.game === 'One Piece' && e.format === 'Standard');
  const f = M.formatFacet({ games: ['One Piece'], byGame, countable, selected: [] });
  const sealed = f.chips.find(c => c.format === 'Sealed');
  assert.equal(sealed.count, 0);
  assert.equal(sealed.disabled, true);
  const f2 = M.formatFacet({ games: ['One Piece'], byGame, countable, selected: ['Sealed'] });
  assert.equal(f2.chips.find(c => c.format === 'Sealed').disabled, false);
});

test('a selected format absent this week stays visible to be removed', () => {
  const f = M.formatFacet({ games: ['Pokémon'], byGame, countable: week, selected: ['Modern'] });
  assert.ok(f.chips.some(c => c.format === 'Modern' && c.selected && c.count === 0));
});

/* ── Pruning and matching ────────────────────────────────────────── */

test('removing a game drops its own formats, keeps shared ones', () => {
  const selected = ['Commander', 'Standard', 'Sealed'];
  // Magic removed, One Piece stays: Commander goes, Standard + Sealed stay.
  assert.deepEqual(M.pruneFormats(selected, ['One Piece'], byGame), ['Standard', 'Sealed']);
  // Everything removed: no formats.
  assert.deepEqual(M.pruneFormats(selected, [], byGame), []);
});

test('a format restricts only the games that have it', () => {
  const sel = ['Commander'];
  assert.equal(M.passesFormat(ev('Magic: The Gathering', 'Commander'), sel, byGame), true);
  assert.equal(M.passesFormat(ev('Magic: The Gathering', 'Pauper'), sel, byGame), false);
  assert.equal(M.passesFormat(ev('Magic: The Gathering', 'Prerelease'), sel, byGame), false);
  assert.equal(M.passesFormat(ev('One Piece', 'Standard'), sel, byGame), true);
  assert.equal(M.passesFormat(ev('One Piece', 'Sealed'), ['Standard'], byGame), false);
});

/* ── Time of day ─────────────────────────────────────────────────── */

const at = (hm) => ev('One Piece', 'Standard', { at: `2026-11-18T${hm}:00+01:00` });

test('slots and sections', () => {
  assert.equal(M.timeSlotOf(at('10:00')), 'morning');
  assert.equal(M.timeSlotOf(at('13:59')), 'morning');
  assert.equal(M.timeSlotOf(at('16:30')), 'afternoon');
  assert.equal(M.timeSlotOf(at('17:00')), 'evening');
  assert.equal(M.timeSlotOf(at('20:30')), 'late');
  assert.equal(M.sectionOf(at('20:30')), 'evening');
  assert.equal(M.timeSlotOf(at('00:00')), 'unlisted');
  assert.equal(M.passesTime(at('20:30'), ['evening']), false);
  assert.equal(M.passesTime(at('20:30'), ['late']), true);
  assert.equal(M.passesTime(at('00:00'), []), true);
});

test('daySections: ordered, non-empty, late counted inside Evening', () => {
  const s = M.daySections([at('00:00'), at('10:00'), at('18:00'), at('21:00')]);
  assert.deepEqual(s.map(x => [x.key, x.count]), [['unlisted', 1], ['morning', 1], ['evening', 2]]);
  assert.equal(s[2].late, 1);
  assert.equal(s[2].words, 'from 17:00');
});

test('open section: next event today, Evening on other days', () => {
  const s = M.daySections([at('10:00'), at('15:00'), at('18:00')]);
  assert.equal(M.defaultOpenSection(s, { isToday: true, nowMinutes: 9 * 60 }), 'morning');
  assert.equal(M.defaultOpenSection(s, { isToday: true, nowMinutes: 12 * 60 }), 'afternoon');
  assert.equal(M.defaultOpenSection(s, { isToday: true, nowMinutes: 23 * 60 }), 'evening');
  assert.equal(M.defaultOpenSection(s, { isToday: false, nowMinutes: 0 }), 'evening');
  const noEvening = M.daySections([at('10:00')]);
  assert.equal(M.defaultOpenSection(noEvening, { isToday: false, nowMinutes: 0 }), 'morning');
  assert.equal(M.defaultOpenSection([], { isToday: false, nowMinutes: 0 }), null);
});

/* ── Day strip ───────────────────────────────────────────────────── */

test('dayStrip: load relative to the busiest day, past and today', () => {
  const days = [{ iso: '2026-11-16', count: 10 }, { iso: '2026-11-18', count: 40 }, { iso: '2026-11-21', count: 0 }];
  const s = M.dayStrip(days, '2026-11-18');
  assert.deepEqual(s.map(d => d.load), [0.25, 1, 0]);
  assert.deepEqual(s.map(d => [d.isPast, d.isToday]), [[true, false], [false, true], [false, false]]);
  assert.deepEqual(M.dayStrip([{ iso: 'x', count: 0 }], 'y')[0].load, 0);
  assert.equal(M.defaultDay(['2026-11-16', '2026-11-18'], '2026-11-18'), '2026-11-18');
  assert.equal(M.defaultDay(['2026-11-23', '2026-11-24'], '2026-11-18'), '2026-11-23');
});

/* ── URL ─────────────────────────────────────────────────────────── */

test('filters round-trip through the URL; unknown type/time values dropped', () => {
  const p = new URLSearchParams('game=One%20Piece,Lorcana&format=Standard&type=casual,bogus&time=late,noon&q=op18&city=bilbao');
  const f = M.readFilterParams(p);
  assert.deepEqual(f, { search: 'op18', game: ['One Piece', 'Lorcana'], store: [], format: ['Standard'], type: ['casual'], time: ['late'] });
  const out = M.writeFilterParams(new URLSearchParams('city=bilbao&store=X'), f);
  assert.equal(out.get('city'), 'bilbao');
  assert.equal(out.has('store'), false);
  assert.deepEqual(M.readFilterParams(out), f);
  assert.equal(M.activeFilterCount(f, 5), 4);  // format + type + time + distance
});
