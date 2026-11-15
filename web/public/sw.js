/* Memo service worker: push notifications only. No offline caching of API data. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// Payload: {title, body, url, urgent, actions:[{action,title}], tag}
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Memo";
  const options = {
    body: data.body || "",
    tag: data.tag || undefined,
    renotify: Boolean(data.tag),
    requireInteraction: Boolean(data.urgent),
    icon: "/icons/icon-192.png",
    badge: "/icons/badge-96.png",
    data: { url: data.url || "/" },
    actions: Array.isArray(data.actions) ? data.actions.slice(0, 2) : [],
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const base = (event.notification.data && event.notification.data.url) || "/";
  const url = new URL(base, self.location.origin);
  if (event.action) url.searchParams.set("action", event.action);
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of all) {
        if (new URL(client.url).origin === url.origin && "focus" in client) {
          await client.focus();
          if ("navigate" in client) return client.navigate(url.href);
          return undefined;
        }
      }
      return self.clients.openWindow(url.href);
    })(),
  );
});
