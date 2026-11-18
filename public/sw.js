/* Service worker: installable app + offline fallback.
 *
 * Strategy: network first, cache as fallback — for every same-origin GET.
 * The site's files are revalidated on each visit anyway (Cloudflare sends
 * max-age=0 + ETag, so a check costs a 304), so going to the network first
 * never shows stale data while online; the cache answers when the network
 * fails, returns a server error, or takes longer than NETWORK_TIMEOUT_MS
 * (only if a cached copy exists). Alpine from jsDelivr (version-pinned,
 * loaded with SRI + CORS) is served cache first, and only successful
 * responses are ever cached.
 *
 * Bump VERSION when this file's logic changes; old caches are deleted.
 * To remove the service worker entirely, see docs/PROJECT_CONTEXT.md
 * ("PWA and offline" → kill switch). Deleting this file is NOT enough.
 */
const VERSION = 'v3';
const CACHE = `tcg-${VERSION}`;
const NETWORK_TIMEOUT_MS = 4000;
const SHELL = [
  '/', '/app.js', '/model.js', '/config.js', '/styles.css',
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

function timeout(ms) {
  return new Promise((resolve) => setTimeout(() => resolve(null), ms));
}

async function networkFirst(event) {
  const { request } = event;
  const cache = await caches.open(CACHE);
  // Pages are cached by path only: every shared ?event=… link would
  // otherwise add its own copy of the same HTML.
  const key = request.mode === 'navigate' ? new URL(request.url).pathname : request;
  const cached = await cache.match(key);
  const network = fetch(request).then((response) => {
    if (response.ok) event.waitUntil(cache.put(key, response.clone()));
    return response;
  });
  network.catch(() => {});  // a late failure after a timeout is not an error
  try {
    // With a cached copy, don't wait forever on a hanging network.
    const response = cached
      ? await Promise.race([network, timeout(NETWORK_TIMEOUT_MS)])
      : await network;
    if (response && response.status < 500) return response;
    if (cached) return cached;
    if (response) return response;
    return await network;                       // timed out and nothing cached
  } catch (err) {
    if (cached) return cached;
    if (request.mode === 'navigate') {
      // Offline and this page was never visited: open the app shell (its
      // URLs are absolute, so it works under any path).
      const shell = await cache.match('/');
      if (shell) return shell;
    }
    throw err;
  }
}

async function cacheFirst(event) {
  const { request } = event;
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) event.waitUntil(cache.put(request, response.clone()));  // never opaque/errors
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin === self.location.origin) {
    if (url.pathname === '/sw.js') return;
    event.respondWith(networkFirst(event));
  } else if (url.hostname === 'cdn.jsdelivr.net') {
    event.respondWith(cacheFirst(event));
  }
});
