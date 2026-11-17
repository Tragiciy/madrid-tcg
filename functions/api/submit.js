/**
 * Cloudflare Pages Function: POST /api/submit
 *
 * Receives the "Is this your store?" form (public/submit/) and files it as
 * a GitHub issue labelled `submission` for a human to review. Approving it
 * (label `approved`) runs .github/workflows/submissions.yml, which adds the
 * event to data/manual_events.json; the daily pipeline then publishes it.
 *
 * Environment (Cloudflare Pages → Settings → Environment variables):
 *   GITHUB_TOKEN  fine-grained token, this repo only, Issues: read & write
 *   GITHUB_REPO   optional, defaults to "Tragiciy/madrid-tcg"
 *
 * Nothing is stored here; the issue is the record. Spam guards: a hidden
 * honeypot field, a minimum fill time, length limits and strict validation.
 */
const GAMES = [
  'Magic: The Gathering', 'Pokémon', 'One Piece', 'Digimon', 'Lorcana',
  'Star Wars: Unlimited', 'Yu-Gi-Oh!', 'Flesh and Blood', 'Riftbound',
  'Weiß Schwarz', 'Final Fantasy TCG', 'Naruto Mythos', 'Other',
];
const MIN_FILL_MS = 4000;

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

const clean = (v, max) => String(v ?? '').replace(/[\u0000-\u001f]/g, ' ').trim().slice(0, max);

export function validate(f, now = Date.now()) {
  if (clean(f.company, 50)) return { spam: true };                     // honeypot
  const started = Number(f.started_at);
  if (!Number.isFinite(started) || now - started < MIN_FILL_MS) return { spam: true };

  const kind = f.kind === 'info' ? 'info' : 'event';
  const out = {
    kind,
    store: clean(f.store, 120),
    contact: clean(f.contact, 160),
    link: clean(f.link, 400),
    notes: clean(f.notes, 2000),
  };
  const errors = [];
  if (!out.store) errors.push('store');
  if (!out.contact) errors.push('contact');
  if (out.link && !/^https?:\/\//i.test(out.link)) errors.push('link');
  if (kind === 'event') {
    out.title = clean(f.title, 160);
    out.game = GAMES.includes(f.game) ? f.game : '';
    out.date = clean(f.date, 10);
    out.time = clean(f.time, 5);
    out.weekly_until = clean(f.weekly_until, 10);
    if (!out.title) errors.push('title');
    if (!out.game) errors.push('game');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(out.date)) errors.push('date');
    if (!/^\d{2}:\d{2}$/.test(out.time)) errors.push('time');
    if (out.weekly_until && !/^\d{4}-\d{2}-\d{2}$/.test(out.weekly_until)) errors.push('weekly_until');
  } else if (!out.notes) {
    errors.push('notes');
  }
  return errors.length ? { errors } : { data: out };
}

function issueBody(d) {
  const lines = d.kind === 'event'
    ? [`**Event submission** for **${d.store}**`, '',
       `- Title: ${d.title}`, `- Game: ${d.game}`, `- When: ${d.date} ${d.time} (Madrid)`,
       d.weekly_until ? `- Repeats weekly until ${d.weekly_until}` : '- One-off',
       d.link ? `- Link: ${d.link}` : '', `- Contact: ${d.contact}`]
    : [`**Store info** for **${d.store}**`, '', `- Contact: ${d.contact}`, d.link ? `- Link: ${d.link}` : ''];
  if (d.notes) lines.push('', '> ' + d.notes.replace(/\n/g, '\n> '));
  lines.push('', 'Review it, then add the label `approved` to publish an event (store-info requests are applied by hand).',
             '', '```json', JSON.stringify(d, null, 2), '```');
  return lines.filter((l) => l !== '').join('\n').replace('\n- ', '\n\n- ');
}

export async function onRequestPost({ request, env }) {
  let form;
  try {
    const ct = request.headers.get('content-type') || '';
    form = ct.includes('application/json') ? await request.json()
      : Object.fromEntries(await request.formData());
  } catch {
    return json(400, { ok: false, error: 'bad_request' });
  }
  const result = validate(form);
  if (result.spam) return json(200, { ok: true });                     // look successful to bots
  if (result.errors) return json(422, { ok: false, error: 'invalid', fields: result.errors });
  if (!env.GITHUB_TOKEN) return json(503, { ok: false, error: 'not_configured' });

  const d = result.data;
  const title = d.kind === 'event'
    ? `[submission] ${d.store}: ${d.title} (${d.date})`
    : `[submission] ${d.store}: store info`;
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO || 'Tragiciy/madrid-tcg'}/issues`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: 'application/vnd.github+json',
      'user-agent': 'madrid-tcg-submissions',
    },
    body: JSON.stringify({ title: title.slice(0, 200), body: issueBody(d), labels: ['submission'] }),
  });
  if (!res.ok) return json(502, { ok: false, error: 'upstream' });
  return json(200, { ok: true });
}

export function onRequest() {
  return json(405, { ok: false, error: 'method_not_allowed' });
}
