const CACHE_NAME = "f1-eink-v1";
const STATIC_ASSETS = [
    "/static/css/tailwind.min.css",
    "/static/css/styles.css",
    "/static/js/common.js",
    "/static/fonts/SpaceMono-Regular.ttf",
    "/static/fonts/SpaceMono-Bold.ttf",
    "/static/favicon/favicon.svg",
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
        event.respondWith(
            caches.match(event.request).then((cached) => {
                if (cached) return cached;
                return fetch(event.request).then((response) => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(event.request, clone);
                        });
                    }
                    return response;
                });
            }),
        );
        return;
    }

    event.respondWith(fetch(event.request));
});
