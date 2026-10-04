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
              /* Oct 3 review major: an EXISTING subscription must be
                 re-POSTed before the status reports success — an
                 account switch on this browser, a server-side prune,
                 or a VAPID rotation can leave the server without this
                 endpoint's row while the browser still holds the
                 subscription ("Notifications on" that notifies no
                 one). The write is idempotent per endpoint, so
                 re-POSTing is always safe; failure surfaces exactly
                 like a fresh subscribe (the shared catch below). */
              return fetch("/api/push/subscribe", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ subscription: sub.toJSON() }),
              }).then(function (r) {
                if (!r.ok) { throw new Error("subscribe http " + r.status); }
                setStatus("Notifications on — jackpot wins, claim reminders, and signals will land here.");
              });
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
      el.title = "";   // verdicts carry no stamp tooltip — never stale
    }
  }

  /* THE N-STEP HISTORY (Sep 26, upgraded from the 2-step walk): two
     client-side stacks. undoStack holds the confirmed states to walk
     BACK through (most recent last), redoStack the states to walk
     FORWARD through. A fresh write pushes the state it replaced onto
     undoStack and VOIDS the redo branch; an undo pops its own stack
     and pushes the state it walked away from onto redoStack (a redo
     mirrors it) — so MID-WALK BOTH LINKS RENDER SIMULTANEOUSLY and
     the walk goes N deep (MAX_HISTORY caps it; the oldest state
     falls off). Both links replay their target through the SAME
     conditional bulk write, so If-Match / 412 protects every step
     exactly like a toggle. Stacks mutate only at APPLY time (the
     in-flight guard serializes writes, so the top of a stack is still
     the target that was clicked); a 412 voids the whole walk (our
     write never applied); an account switch clears both stacks — one
     account's walk must never replay into another's. */
  var MAX_HISTORY = 20;          // N: how deep the walk may go
  var undoStack = [];
  var redoStack = [];
  var undoHandler = null;        // assigned by wirePrefs
  var redoHandler = null;        // assigned by wirePrefs

  function noteStamp(prefix, iso, warn, suffix, withUndo, withRedo) {
    var el = document.getElementById("prefSaved");
    if (!el || !iso) { return; }
    stampNote = { prefix: prefix, iso: iso, warn: !!warn,
                  suffix: suffix || "", withUndo: !!withUndo,
                  withRedo: !!withRedo };
    renderStampLine();
  }

  function renderStampLine() {
    var el = document.getElementById("prefSaved");
    if (!el || !stampNote) { return; }
    /* HOVER = THE EXACT MOMENT (Sep 26): the relative text answers
       "how old"; the title answers "when exactly" — the raw UTC ISO
       stamp, same convention as the Scout panel's stamp. */
    el.title = stampNote.iso || "";
    var label = stampNote.prefix + " " + relTime(stampNote.iso)
      + stampNote.suffix;
    if (stampNote.withUndo || stampNote.withRedo) {
      el.textContent = "";
      el.appendChild(document.createTextNode(label + " \u00b7 "));
      var mkLink = function (txt, fn) {
        var a = document.createElement("a");
        a.href = "#";
        a.textContent = txt;
        a.style.color = "var(--gold-bright, #c9a227)";
        a.addEventListener("click", function (ev) {
          ev.preventDefault();
          fn();
        });
        return a;
      };
      /* SIMULTANEOUS LINKS (Sep 26, the N-step walk): mid-walk BOTH
         render — "undo · redo" — each walking its own stack. */
      if (stampNote.withUndo) {
        el.appendChild(mkLink("undo", function () {
          if (undoHandler) { undoHandler(); }
        }));
      }
      if (stampNote.withUndo && stampNote.withRedo) {
        el.appendChild(document.createTextNode(" \u00b7 "));
      }
      if (stampNote.withRedo) {
        el.appendChild(mkLink("redo", function () {
          if (redoHandler) { redoHandler(); }
        }));
      }
    } else {
      el.textContent = label;
    }
    el.style.color = stampNote.warn ? "#c9a227" : "var(--text-mute)";
  }

  setInterval(function () {
    if (!stampNote) { return; }
    var el = document.getElementById("prefSaved");
    // refresh only while this stamp message is still what's shown
    if (el && (stampNote.withUndo || stampNote.withRedo ||
               el.textContent.indexOf(stampNote.prefix) === 0)) {
      renderStampLine();
    }
  }, 30000);

  function wirePrefs() {
    var j = document.getElementById("prefJackpot");
    var s = document.getElementById("prefScout");
    if (!j || !s || j.dataset.wired) { return; }
    j.dataset.wired = "1";
    s.dataset.wired = "1";
    // UNDO BOOKKEEPING (fixed after live verification): the screen
    // already carries the user's change by the time a write applies,
    // so the undo target must be snapshotted AT WRITE TIME from the
    // state the server last CONFIRMED — never from the checkboxes.
    var lastConfirmed = null;   // state as last confirmed by the server
    var pendingPrev = null;     // the state a FRESH write will replace
    var pendingKind = null;     // fresh | undo | redo (the in-flight write)
    var pendingReturn = null;   // the state an undo/redo walks away from
    function adopt(p) {
      if (!p) { return; }
      /* SHAPE FIX (Sep 26, found by the undo live check): every
         envelope — GET body, 200 body, 412 body — nests the values in
         `preferences`; reading p.jackpot off the envelope returned
         undefined !== false = true, so every adopt snapped BOTH
         checkboxes to ON regardless of the server's real state (the
         card showed both-ON after any sync). Normalize once. */
      var pr = (p && p.preferences && typeof p.preferences === "object")
        ? p.preferences : p;
      j.checked = pr.jackpot !== false;
      s.checked = pr.scout !== false;
      if (p.updated_at) {
        prefsEtag = '"' + p.updated_at + '"';   // the version we now hold
        lastConfirmed = { jackpot: pr.jackpot !== false,
                          scout: pr.scout !== false };
        noteStamp("saved", p.updated_at, false, "",
                  undoStack.length > 0, redoStack.length > 0);
        /* ANNOUNCE (Sep 26): every applied envelope is broadcast so
           other surfaces (the Scout panel's prefs stamp) update the
           same instant — no waiting on their 60s poll. Carries the
           full envelope; listeners render from it without a fetch. */
        try {
          document.dispatchEvent(new CustomEvent("xmrjackpot:prefs",
                                                 { detail: p }));
        } catch (e) { /* ancient browser: their poll still works */ }
      }
    }
    function readServer() {
      return fetch("/api/push/preferences", { credentials: "same-origin" })
        .then(function (r) {
          prefsEtag = r.headers.get("ETag") || prefsEtag;
          return r.json();
        })
        .then(function (p) { adopt(p); })
        .catch(function () { /* defaults stand */ });
    }
    readServer();
    // after login/register the subject changes — re-read so the ETag
    // belongs to THIS user before the first toggle races on it, and
    // drop the undo target: one account's undo must never replay
    // another account's values
    document.addEventListener("xmrjackpot:auth", function () {
      undoStack = [];
      redoStack = [];
      lastConfirmed = null;
      readServer();
    });
    var posting = false;
    function sendPrefs(prefs, busyMsg, kind) {
      if (posting) { return; }            // one in flight; last state wins
      posting = true;
      pendingKind = kind || "fresh";
      pendingPrev = lastConfirmed;        // what a fresh write will replace
      pendingReturn = (pendingKind === "fresh") ? null : lastConfirmed;
      var headers = { "Content-Type": "application/json" };
      if (prefsEtag) { headers["If-Match"] = prefsEtag; }
      noteSaved(busyMsg || "saving…", true);
      fetch("/api/push/preferences", {
        method: "POST",
        credentials: "same-origin",
        headers: headers,
        body: JSON.stringify({ preferences: prefs }),
      }).then(function (r) {
        posting = false;
        if (r.status === 412) {
          // lost the race: another device wrote first — take the
          // server's version (its envelope + new ETag) and re-apply;
          // OUR write never applied, so the whole walk is void
          undoStack = [];
          redoStack = [];
          return r.json().then(function (cur) {
            adopt(cur);
            noteStamp("another device changed these — showing its version (",
                      cur.updated_at, true, ")");
          });
        }
        return r.json().then(function (res) {
          if (res && res.updated_at) {
            // STACK MUTATIONS AT APPLY TIME ONLY (the posting guard
            // serialized the writes: the top of the stack is still the
            // target that was clicked)
            if (pendingKind === "fresh") {
              if (pendingPrev) {
                undoStack.push(pendingPrev);
                if (undoStack.length > MAX_HISTORY) { undoStack.shift(); }
              }
              redoStack = [];              // a fresh write voids the redo branch
            } else if (pendingKind === "undo") {
              undoStack.pop();
              if (pendingReturn) { redoStack.push(pendingReturn); }
            } else if (pendingKind === "redo") {
              redoStack.pop();
              if (pendingReturn) { undoStack.push(pendingReturn); }
            }
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
    function postBulk() {
      sendPrefs({ jackpot: j.checked, scout: s.checked });
    }
    undoHandler = function () {
      if (undoStack.length) {
        sendPrefs(undoStack[undoStack.length - 1], "restoring…", "undo");
      }
    };
    redoHandler = function () {
      if (redoStack.length) {
        sendPrefs(redoStack[redoStack.length - 1], "restoring…", "redo");
      }
    };
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

  /* EXPORT (Sep 26): relTime is the project's ONE relative-time
     renderer — the Scout signals panel reuses it so every card ages
     stamps with the same buckets ("2 min ago", never "2m ago" here
     and "2 min ago" there). Load order is safe: this IIFE runs
     before wf_dashboard's boot (both classic scripts, DOMContentLoaded
     fires after). */
  window.XMRJackpotPush = { relTime: relTime };
}());
