/* Service worker: installable app + offline fallback.
 *
 * Strategy: network first, cache as fallback — for every same-origin GET.
 * The site's files are revalidated on each visit anyway (Cloudflare sends
 * max-age=0 + ETag, so a check costs a 304), so going to the network first
 * never shows stale data while online; the cache only answers when the
 * network fails. Third-party scripts (Alpine on jsDelivr, version-pinned)
 * are served cache first.
 *
 * Bump VERSION when this file's logic changes; old caches are deleted.
 */
const VERSION = 'v1';
const CACHE = `tcg-${VERSION}`;
const SHELL = [
  '/', '/index.html', '/app.js', '/config.js', '/styles.css',
  '/manifest.webmanifest', '/icons/icon-192.png',
  '/api/v1/madrid/events/upcoming.json', '/api/v1/madrid/stores.json', '/api/v1/madrid/index.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => {})            // offline install: cache fills on use instead
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('tcg-') && k !== CACHE)
                                      .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  // Pages are cached by path only: every shared ?event=… link would
  // otherwise add its own copy of the same HTML.
  const key = request.mode === 'navigate' ? new URL(request.url).pathname : request;
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(key, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(key);
    if (cached) return cached;
    if (request.mode === 'navigate') {
      const shell = await cache.match('/');
      if (shell) return shell;
    }
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok || response.type === 'opaque') cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin === self.location.origin) {
    if (url.pathname === '/sw.js') return;
    event.respondWith(networkFirst(request));
  } else if (url.hostname === 'cdn.jsdelivr.net') {
    event.respondWith(cacheFirst(request));
  }
});
