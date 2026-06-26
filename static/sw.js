// Service worker Maltai - cache le shell statique uniquement.
// Les requetes /api/ ne sont JAMAIS mises en cache (donnees fraiches + auth).
const CACHE = "maltai-shell-v32";
const SHELL = [
  "/",
  "/app",
  "/billing",
  "/static/site.css",
  "/static/style.css",
  "/static/js/compat.js",
  "/static/js/app.js",
  "/static/manifest.json",
  "/static/icon-192.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Tout ce qui touche l'API ou l'auth passe directement au reseau.
  if (url.pathname.startsWith("/api/") || e.request.method !== "GET") {
    return; // laisse le navigateur gerer normalement
  }
  // Shell statique : cache d'abord, reseau en secours.
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});
