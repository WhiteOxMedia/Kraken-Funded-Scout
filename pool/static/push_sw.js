\
/* push_sw.js — THE PUSH SERVICE WORKER (Sep 25, charter).
   Receives native push events from xmrjackpot.com's own push service
   and shows the notification. The payload's text comes only from the
   server's whitelisted event templates (pool/push_service.py); the
   worker never evaluates, fetches on click beyond the origin, or
   stores secrets. All real UI alerts are routed through the unified
   NFTY system in push_client.js; the worker only forwards the payload
   to the active page client for routing.
   */

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
  var type = String(data.type || "").trim();
  if (!type) {
    // Malformed or empty payload — render no notification.
    event.waitUntil(Promise.resolve());
    return;
  }
  // Show a minimal visible notification so the user sees something
  // even if the page is not open, but never display rich content.
  event.waitUntil(self.registration.showNotification("XMR Jackpot", {
    body: type === "signal_buy" ? "BUY_NOW" : type === "signal_exit" ? "GET_OUT_NOW" : "Signal",
    tag: "xmrjackpot",
    data: { type: type },
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var type = (event.notification.data && event.notification.data.type) || "";
  // Route the payload into the page's unified NFTY router rather than
  // opening a URL or showing another popup.
  event.waitUntil(
    self.clients.matchAll({ type: "window" }).then(function (clientList) {
      for (var i = 0; i < clientList.length; i++) {
        var c = clientList[i];
        if ("postMessage" in c) {
          c.postMessage({ type: type, source: "push_sw" });
          if ("focus" in c) { c.focus(); }
          return;
        }
      }
      // No open page — schedule a wake by opening the origin (same-origin only).
      if (self.location && self.location.origin) {
        return self.clients.openWindow(self.location.origin + "/");
      }
    }).catch(function () {})
  );
});

// Forward any push-derived messages from the worker to the page's router
// when the page later loads and re-establishes the connection.
self.addEventListener("message", function (event) {
  if (event.data && event.data.source === "push_loader") {
    var type = (event.data && event.data.type) || "";
    if (type) {
      event.waitUntil(
        self.clients.matchAll({ type: "window" }).then(function (clientList) {
          for (var i = 0; i < clientList.length; i++) {
            var c = clientList[i];
            if ("postMessage" in c) {
              c.postMessage({ type: type, source: "push_sw_reload" });
              if ("focus" in c) { c.focus(); }
              return;
            }
          }
        })
      );
    }
  }
});
