// ==============================================================================
// Airutil Service Worker (Phase 7 PWA)
// Offline shell caching & resilient network fallback
// ==============================================================================

const CACHE_NAME = 'airutil-shell-v1';
const STATIC_ASSETS = [
    '/offline',
    '/static/css/tokens.css',
    '/static/css/style.css',
    '/static/js/shell.js',
    '/static/favicon.svg',
    '/static/vendor/xterm/xterm.css',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    // Only handle GET requests
    if (event.request.method !== 'GET') return;

    // For HTML navigation requests, try network first, fallback to offline page
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request).catch(() => {
                return caches.match('/offline') || new Response("Offline", { status: 503 });
            })
        );
        return;
    }

    // For static assets, try cache first, fallback to network
    if (event.request.url.includes('/static/')) {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                return (
                    cached ||
                    fetch(event.request).then((resp) => {
                        if (resp.status === 200) {
                            const copy = resp.clone();
                            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                        }
                        return resp;
                    })
                );
            })
        );
    }
});
