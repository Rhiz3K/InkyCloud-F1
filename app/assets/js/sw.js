// Bump the cache version whenever routing-critical frontend assets change.
// This forces clients to drop stale locale-switching logic from previous releases.
const CACHE_NAME = "f1-eink-v5";
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
        // Only versioned font files are cache-first. CSS, scripts, and unversioned images use
        // stale-while-revalidate so asset replacements reach returning visitors.
        const revalidate = !url.pathname.startsWith("/static/fonts/");
        event.respondWith(
            caches.open(CACHE_NAME).then((cache) =>
                cache.match(event.request).then((cached) => {
                    if (cached && !revalidate) return cached;
                    const network = fetch(event.request)
                        .then((response) => {
                            if (!response.ok) return response;
                            // Chain the cache write so the promise below settles only after
                            // the body is fully stored — waitUntil must cover the put, or the
                            // browser may kill the idle SW mid-write and keep the stale asset.
                            return cache
                                .put(event.request, response.clone())
                                .then(() => response, () => response);
                        })
                        // On network failure fall back to the cached copy; with no cache either,
                        // resolve to a network-error Response so respondWith never gets undefined.
                        .catch(() => cached ?? Response.error());
                    if (cached) {
                        event.waitUntil(network);
                        return cached;
                    }
                    return network;
                }),
            ),
        );
        return;
    }

    event.respondWith(fetch(event.request));
});
