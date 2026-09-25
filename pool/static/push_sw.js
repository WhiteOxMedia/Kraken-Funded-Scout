/* push_sw.js — THE PUSH SERVICE WORKER (Sep 25, charter).
   Receives native push events from xmrjackpot.com's own push service
   and shows the notification. The payload's text comes only from the
   server's whitelisted event templates (pool/push_service.py); the
   worker never evaluates, fetches on click beyond the origin, or
   stores secrets. */
self.addEventListener("install", function (e) {
  self.skipWaiting();
});

self.addEventListener("activate", function (e) {
  e.waitUntil(self.clients.claim());
});

self.addEventListener("push", function (event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = {};
  }
  var title = String(data.title || "XMR Jackpot").slice(0, 80);
  var body = String(data.body || "").slice(0, 240);
  var url = String(data.url || "/");
  if (url.charAt(0) !== "/") { url = "/"; }   /* same-origin only */
  event.waitUntil(self.registration.showNotification(title, {
    body: body,
    tag: String(data.tag || "xmrjackpot").slice(0, 40),
    data: { url: url },
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(self.clients.matchAll({ type: "window" })
    .then(function (clientList) {
      for (var i = 0; i < clientList.length; i += 1) {
        var c = clientList[i];
        if ("focus" in c) { c.navigate(target); return c.focus(); }
      }
      return self.clients.openWindow(target);
    }));
});
