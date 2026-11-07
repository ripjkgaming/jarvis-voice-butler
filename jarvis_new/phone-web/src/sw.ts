/** Service worker (PLAN.md §9.3). App shell precached as link-<BUILD_ID>;
 * navigations network-first (3 s) then cached index.html; /api and /voice
 * are never cached; static assets cache-first. A new version waits until the
 * page sends SKIP_WAITING (UPD lamp tap or the next cold launch). */
/* eslint-disable @typescript-eslint/no-explicit-any */
type Ev = Event & { waitUntil(p: Promise<unknown>): void; respondWith(r: Promise<Response> | Response): void; request: Request; data: unknown };
const sw = self as unknown as {
  addEventListener(type: string, fn: (e: Ev) => void): void;
  skipWaiting(): Promise<void>;
  clients: { claim(): Promise<void> };
  location: Location;
};
declare const __BUILD_ID__: string;

const PRECACHE = '__PRECACHE__' as unknown as string[];
const CACHE = `link-${__BUILD_ID__}`;
const NAV_TIMEOUT_MS = 3000;

sw.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)));
});

sw.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) if (key !== CACHE && key.startsWith('link-')) await caches.delete(key);
      await sw.clients.claim();
    })(),
  );
});

sw.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') void sw.skipWaiting();
});

function never(url: URL) {
  return url.pathname.startsWith('/api/') || url.pathname === '/api' || url.pathname.startsWith('/voice');
}

async function navigate(req: Request): Promise<Response> {
  const cache = await caches.open(CACHE);
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), NAV_TIMEOUT_MS);
    const res = await fetch(req, { signal: ctrl.signal });
    clearTimeout(timer);
    return res;
  } catch {
    return (await cache.match('./')) || (await cache.match('index.html')) || Response.error();
  }
}

sw.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== sw.location.origin || never(url)) return; // network only
  if (req.mode === 'navigate') {
    event.respondWith(navigate(req));
    return;
  }
  event.respondWith(
    caches.match(req).then(
      (hit) =>
        hit ||
        fetch(req).then((res) => {
          if (res.ok && res.type === 'basic') {
            const copy = res.clone();
            void caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        }),
    ),
  );
});
