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

  /* THE TWO DASHBOARD TOGGLES (user order, Sep 25 evening VII;
     bulk-envelope rewire, Sep 26): Scout Notifications and Jackpot
     Notifications opt out independently; the server enforces the
     choice at send time. Every toggle POSTs the FULL bulk envelope
     {"preferences": {...}} with If-Match: "<updated_at>" from the
     last read — optimistic concurrency, so two devices cannot
     silently clobber each other (a stale version answers 412 and
     this client re-reads and re-applies the server's truth). The
     card shows the server's updated_at stamp after every save. */
  var prefsEtag = "";

  /* RELATIVE STAMPS (Sep 26): "saved 2 min ago" instead of a raw
     clock reading, re-rendered every 30s while the message is still
     the one displayed. The stamp IS the version — prefsEtag keeps
     the ISO string; only the human-facing rendering ages. Older
     than a week falls back to an absolute date: a year-old stamp
     must not read "365 d ago". */
  var stampNote = null;   // the stamp message currently displayed

  function relTime(iso) {
    var d = new Date(iso);
    if (isNaN(d)) { return String(iso); }
    var s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 5) { return "just now"; }
    if (s < 60) { return s + "s ago"; }
    var m = Math.floor(s / 60);
    if (m < 60) { return m + " min ago"; }
    var h = Math.floor(m / 60);
    if (h < 24) { return h + " h ago"; }
    var days = Math.floor(h / 24);
    if (days < 7) { return days + " d ago"; }
    return d.toLocaleDateString(undefined,
      { month: "short", day: "numeric" });
  }

  function noteSaved(text, warn) {
    stampNote = null;              // a non-stamp message takes over
    var el = document.getElementById("prefSaved");
    if (el) {
      el.textContent = text || "";
      el.style.color = warn ? "#c9a227" : "var(--text-mute)";
    }
  }

  function noteStamp(prefix, iso, warn, suffix) {
    var el = document.getElementById("prefSaved");
    if (!el || !iso) { return; }
    suffix = suffix || "";
    stampNote = { prefix: prefix, iso: iso, warn: !!warn, suffix: suffix };
    el.textContent = prefix + " " + relTime(iso) + suffix;
    el.style.color = warn ? "#c9a227" : "var(--text-mute)";
  }

  setInterval(function () {
    if (!stampNote) { return; }
    var el = document.getElementById("prefSaved");
    // refresh only while this stamp message is still what's shown
    if (el && el.textContent.indexOf(stampNote.prefix) === 0) {
      el.textContent = stampNote.prefix + " " + relTime(stampNote.iso)
        + stampNote.suffix;
    }
  }, 30000);

  function wirePrefs() {
    var j = document.getElementById("prefJackpot");
    var s = document.getElementById("prefScout");
    if (!j || !s || j.dataset.wired) { return; }
    j.dataset.wired = "1";
    s.dataset.wired = "1";
    function adopt(p) {
      if (!p) { return; }
      j.checked = p.jackpot !== false;
      s.checked = p.scout !== false;
      if (p.updated_at) {
        prefsEtag = '"' + p.updated_at + '"';   // the version we now hold
        noteStamp("saved", p.updated_at);
      }
    }
    function readServer() {
      return fetch("/api/push/preferences", { credentials: "same-origin" })
        .then(function (r) {
          prefsEtag = r.headers.get("ETag") || prefsEtag;
          return r.json();
        })
        .then(adopt)
        .catch(function () { /* defaults stand */ });
    }
    readServer();
    // after login/register the subject changes — re-read so the ETag
    // belongs to THIS user before the first toggle races on it
    document.addEventListener("xmrjackpot:auth", readServer);
    var posting = false;
    function postBulk() {
      if (posting) { return; }            // one in flight; last state wins
      posting = true;
      var headers = { "Content-Type": "application/json" };
      if (prefsEtag) { headers["If-Match"] = prefsEtag; }
      noteSaved("saving…", true);
      fetch("/api/push/preferences", {
        method: "POST",
        credentials: "same-origin",
        headers: headers,
        body: JSON.stringify({ preferences: {
          jackpot: j.checked, scout: s.checked } }),
      }).then(function (r) {
        posting = false;
        if (r.status === 412) {
          // lost the race: another device wrote first — take the
          // server's version (its envelope + new ETag) and re-apply
          return r.json().then(function (cur) {
            adopt(cur);
            noteStamp("another device changed these — showing its version (",
                      cur.updated_at, true, ")");
          });
        }
        return r.json().then(function (res) {
          if (res && res.updated_at) {
            adopt(res);                   // 200 body IS the new version
          } else if (!r.ok) {
            noteSaved("save failed — will retry on next toggle", true);
          }
        });
      }).catch(function () {
        posting = false;
        noteSaved("could not reach the server — try again", true);
      });
    }
    j.addEventListener("change", postBulk);
    s.addEventListener("change", postBulk);
  }

  /* THE PIPE TEST (Sep 26, user request): each category box gets a
     small "Test notification" button — one REAL push of that family
     through the same path a live event takes (opt-out gate, rate
     limiter, audit, VAPID). No fake success: the sender's verdict is
     passed through verbatim and mapped to plain language — including
     "your toggle is OFF", which is itself proof the gate works. The
     verdict stays in #prefSaved until the next action uses it. */
  var TEST_MSGS = {
    "jackpot notifications opted out":
      "your Jackpot toggle is OFF — this is the gate working; turn it back on to receive these",
    "scout notifications opted out":
      "your Scout toggle is OFF — this is the gate working; turn it back on to receive these",
    "no subscription":
      "no device registered yet — press Enable Notifications first",
    "VAPID key not provisioned":
      "push delivery is not provisioned yet — the operator must add the keys first",
    "rate limited": "one just went out — try again in a minute",
    "webpush library not installed":
      "push delivery is not configured on the server — the operator must install it",
    "no deliverable endpoint":
      "your device did not accept it — press Enable Notifications and try again"
  };

  function wireTestButtons() {
    var j = document.getElementById("btnTestJackpot");
    var s = document.getElementById("btnTestScout");
    if (!j || !s || j.dataset.wired) { return; }
    j.dataset.wired = "1";
    s.dataset.wired = "1";
    function fire(btn, category) {
      if (btn.disabled) { return; }
      btn.disabled = true;
      var prev = btn.textContent;
      btn.textContent = "testing…";
      noteSaved("sending a real " + category + " test push…", true);
      fetch("/api/push/test", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: category }),
      }).then(function (r) {
        return r.json().then(function (res) {
          if (r.status === 401) {
            noteSaved("log in first — then test the pipe", true);
            return;
          }
          if (res && res.ok && res.sent) {
            noteSaved("sent — check your device 🔔");
            return;
          }
          var reason = res && res.reason;
          noteSaved(TEST_MSGS[reason] ||
                    ("could not send it — " + (reason || "http " + r.status)),
                    true);
        }, function () {
          noteSaved("could not send it — http " + r.status, true);
        });
      }).catch(function () {
        noteSaved("could not reach the server — try again", true);
      }).then(function () {
        btn.disabled = false;
        btn.textContent = prev;
      });
    }
    j.addEventListener("click", function () { fire(j, "jackpot"); });
    s.addEventListener("click", function () { fire(s, "scout"); });
  }

  function wire() {
    var btn = document.getElementById("btnEnablePush");
    if (!btn) { return; }
    btn.addEventListener("click", enable);
    wirePrefs();
    wireTestButtons();
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
