// Bump the cache version whenever routing-critical frontend assets change.
// This forces clients to drop stale locale-switching logic from previous releases.
const CACHE_NAME = "f1-eink-v4";
const STATIC_ASSETS = [
    "/static/css/tailwind.min.css",
    "/static/css/styles.css",
    "/static/js/common.js",
    "/static/fonts/SpaceMono-Regular.ttf",
    "/static/fonts/SpaceMono-Bold.ttf",
    "/static/favicon/favicon-96x96.png",
    "/static/images/og-preview.png",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        }),
    );
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys
                    .filter((key) => key !== CACHE_NAME)
                    .map((key) => caches.delete(key)),
            );
        }),
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);

    if (url.pathname.startsWith("/static/")) {
        // Stale-while-revalidate: serve the cached asset immediately, but always re-fetch in the
        // background so an updated common.js / stylesheet reaches returning visitors on their next
        // load even when CACHE_NAME wasn't bumped. Pure cache-first served stale assets forever.
        event.respondWith(
            caches.open(CACHE_NAME).then((cache) =>
                cache.match(event.request).then((cached) => {
                    const network = fetch(event.request)
                        .then((response) => {
                            if (response.ok) {
                                cache.put(event.request, response.clone());
                            }
                            return response;
                        })
                        .catch(() => cached);
                    return cached || network;
                }),
            ),
        );
        return;
    }

    event.respondWith(fetch(event.request));
});
