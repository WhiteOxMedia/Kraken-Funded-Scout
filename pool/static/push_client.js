/* push_client.js — THE NATIVE PUSH CLIENT (Sep 25, charter).
   Registers this browser with xmrjackpot.com's own push service
   (pool/push_service.py): Notifications permission + a service-worker
   subscription posted to /api/push/subscribe. Self-contained and
   fail-soft — if the browser lacks support, registration is refused
   locally and nothing is sent. Loaded after app.js; it owns ONLY the
   push elements it adds (#btnEnablePush, #pushStatus). */
(function () {
  "use strict";

  var SUPPORTED = "serviceWorker" in navigator && "PushManager" in window
    && "Notification" in window;

  function setStatus(msg, dim) {
    var el = document.getElementById("pushStatus");
    if (el) { el.textContent = msg; }
    void dim;
  }

  function b64ToUint8(base64) {
    var pad = "=".repeat((4 - (base64.length % 4)) % 4);
    var raw = window.atob((base64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) { out[i] = raw.charCodeAt(i); }
    return out;
  }

  function subscribe(sw) {
    return fetch("/api/push/public-key").then(function (r) {
      if (!r.ok) { throw new Error("key http " + r.status); }
      return r.json();
    }).then(function (k) {
      return sw.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: b64ToUint8(k.public_key),
      });
    }).then(function (sub) {
      return fetch("/api/push/subscribe", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription: sub.toJSON() }),
      });
    }).then(function (r) {
      if (!r.ok) { throw new Error("subscribe http " + r.status); }
      setStatus("Notifications on — jackpot wins, claim reminders, and signals will land here.");
    });
  }

  function enable() {
    if (!SUPPORTED) {
      setStatus("This browser does not support web push — the jackpot plate and the console keep working without it.", true);
      return;
    }
    setStatus("asking the browser…", true);
    Notification.requestPermission().then(function (perm) {
      if (perm !== "granted") {
        setStatus("Permission not granted — you can enable it later in the browser settings.", true);
        return;
      }
      navigator.serviceWorker.ready.then(function (reg) {
        return subscribe(reg);
      }).catch(function () {
        return navigator.serviceWorker.register("/static/push_sw.js?v=1")
          .then(function (reg) {
            return navigator.serviceWorker.ready;
          }).then(function (reg) {
            return subscribe(reg);
          });
      }).catch(function () {
        setStatus("Could not reach the push service — try again shortly.", true);
      });
    });
  }

  function wire() {
    var btn = document.getElementById("btnEnablePush");
    if (!btn) { return; }
    btn.addEventListener("click", enable);
    if (SUPPORTED && Notification.permission === "granted") {
      navigator.serviceWorker.getRegistration("/static/push_sw.js")
        .then(function (reg) {
          setStatus(reg ? "Notifications are on for this device."
                        : "Notifications are granted but not yet registered — press Enable.");
        }).catch(function () { /* keep the default label */ });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
}());
