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
      /* review triage (Sep 25): `navigator.serviceWorker.ready` never
         resolves on a page whose origin has no active worker — the
         Enable button hung forever on a first visit. REGISTER first,
         then use the registration register() returns. */
      /* e2e-proof fix (Sep 25): the worker MUST be registered from a
         root-path URL — from /static/ its default scope was /static/,
         which can never control pages at / (subscribed, then unable
         to notify). Served at /push_sw.js by the server. */
      navigator.serviceWorker.register("/push_sw.js?v=2")
        .then(function (reg) {
          return reg.pushManager.getSubscription().then(function (sub) {
            if (sub) {
              /* f5 receipt test (Sep 26): reusing a live subscription
                 used to leave the status at "asking the browser…" —
                 the device is ready, say so. */
              setStatus("Notifications on — jackpot wins, claim reminders, and signals will land here.");
              return sub;
            }
            return subscribe(reg);
          });
        }).catch(function () {
          setStatus("Could not reach the push service — try again shortly.", true);
        });
    });
  }

  /* THE TWO DASHBOARD TOGGLES (user order, Sep 25 evening VII):
     Scout Notifications and Jackpot Notifications opt out
     independently; the server enforces the choice at send time. */
  function wirePrefs() {
    var j = document.getElementById("prefJackpot");
    var s = document.getElementById("prefScout");
    if (!j || !s || j.dataset.wired) { return; }
    j.dataset.wired = "1";
    s.dataset.wired = "1";
    fetch("/api/push/preferences", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (p) {
        if (p) {
          j.checked = p.jackpot !== false;
          s.checked = p.scout !== false;
        }
      })
      .catch(function () { /* defaults stand */ });
    function post(cat, on) {
      fetch("/api/push/preferences", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: cat, enabled: on }),
      }).catch(function () { /* the server is the truth anyway */ });
    }
    j.addEventListener("change", function () { post("jackpot", j.checked); });
    s.addEventListener("change", function () { post("scout", s.checked); });
  }

  function wire() {
    var btn = document.getElementById("btnEnablePush");
    if (!btn) { return; }
    btn.addEventListener("click", enable);
    wirePrefs();
    if (SUPPORTED && Notification.permission === "granted") {
      /* second triage (Sep 25): the worker registers at /push_sw.js
         with root scope now — look it up by scope, not old path */
      navigator.serviceWorker.getRegistration("/")
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
