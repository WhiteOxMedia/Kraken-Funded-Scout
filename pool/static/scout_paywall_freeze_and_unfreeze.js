/* pool/static/scout_paywall.js — freeze/unfreeze contract
   Purpose: once a checkout window is expired, disable the whole payment surface
   until the active cycle resumes. This is the thin contract layer on top of the
   wiring patch — it does not itself reimplement checkout; it only gates and
   surfaces the freeze state.
   */

(function () {
  "use strict";

  var FREEZE_STORAGE_KEY = "scout_paywall_freeze_until";
  var STORAGE_CLASS_FREEZE = "scout-paywall-frozen";
  var BASE_SELECTORS = [
    "#scout-purchase-panel",
    "#scout-unlock-panel",
    "#jackpot-cycle-panel",
    "#scout-activation-limiter",
    "#donate-modal",
    "#miner-modal",
    "#connect-xmr-wallet",
    "#btn-connect-xmr-wallet",
    "#btn-download-miner",
    "#btn-donate-unlock",
    "#btn-activate-scout",
    "#btn-get-scout",
    "#cta-get-scout",
    "#cta-how-it-works",
    "#referral-block",
    "#ref-share-row",
    "#kraken-referral-link",
    "#cta-buy-xmrj-dex",
    "#xmr-wallet-status"
  ];

  // ── helpers ────────────────────────────────────────────────────────────
  // Delegate to the shared scout ID resolver so all three scout modules
  // use one ID resolution path (wf* preferred, legacy fallback).
  var scoutIds = window.scoutIds;
  function $(id) {
    return (scoutIds && typeof scoutIds.$ === "function")
      ? scoutIds.$(id)
      : document.getElementById(id);
  }

  function readFreezeUntil() {
    try {
      var raw = localStorage.getItem(FREEZE_STORAGE_KEY);
      if (!raw) return null;
      var v = Number(raw);
      return (v > 0 && !isNaN(v)) ? v : null;
    } catch (e) {
      return null;
    }
  }

  function writeFreezeUntil(ts) {
    try {
      localStorage.setItem(FREEZE_STORAGE_KEY, String(ts));
    } catch (e) {
      // ignore quota / private-mode failures; freeze can still live in memory
    }
  }

  function clearFreezeUntil() {
    try { localStorage.removeItem(FREEZE_STORAGE_KEY); } catch (e) {}
  }

  function isActiveFreezeUntil() {
    var until = readFreezeUntil();
    if (!until) return false;
    return Date.now() < until;
  }

  function activeFreezeSecondsRemaining() {
    var until = readFreezeUntil();
    if (!until) return 0;
    var remain = Math.ceil((until - Date.now()) / 1000);
    return Math.max(0, remain);
  }

  // ── freeze application ─────────────────────────────────────────────────
  function applyFreeze(forceNow) {
    var frozen = isActiveFreezeUntil();
    var remaining = activeFreezeSecondsRemaining();
    var root = document.getElementById("scout-paywall-root") || document.body;

    // toggle a root-level marker so CSS can hide/show the whole surface
    root.classList.toggle(STORAGE_CLASS_FREEZE, frozen);

    var panels = ["scout-purchase-panel","scout-unlock-panel","jackpot-cycle-panel","scout-activation-limiter"];
    panels.forEach(function(id){
      var el = $(id);
      if (!el) return;
      el.style.pointerEvents = frozen ? "none" : "";
      el.style.opacity = frozen && id !== "scout-activation-limiter" ? "0.55" : "";
    });

    // disable buttons
    var buttons = [
      "btn-download-miner",
      "btn-donate-unlock",
      "btn-activate-scout",
      "btn-get-scout",
      "cta-get-scout",
      "cta-how-it-works",
      "connect-xmr-wallet",
      "btn-connect-xmr-wallet",
      "cta-buy-xmrj-dex"
    ];
    buttons.forEach(function(id){
      var el = $(id);
      if (!el) return;
      if (frozen) {
        el.disabled = true;
        el.setAttribute("aria-disabled","true");
      } else {
        el.disabled = false;
        el.removeAttribute("aria-disabled");
      }
    });

    // surface the freeze message once (idempotent)
    var freezeMsg = $("scout-paywall-freeze-msg");
    if (frozen) {
      if (!freezeMsg) {
        freezeMsg = document.createElement("div");
        freezeMsg.id = "scout-paywall-freeze-msg";
        freezeMsg.className = "scout-paywall-freeze-msg";
        var anchor = $("scout-purchase-panel") || root;
        anchor.parentNode.insertBefore(freezeMsg, anchor);
      }
      var remain = activeFreezeSecondsRemaining();
      var msg = "Checkout is paused until the active cycle resumes" +
        (remain > 0 ? " — about " + remain + "s remaining." : "") +
        " Donations and unlocks are disabled until then.";
      freezeMsg.textContent = msg;
      freezeMsg.style.display = "";
    } else if (freezeMsg) {
      freezeMsg.style.display = "none";
    }
  }

  // ── freeze API (called by admin toggles / checkout lifecycle) ─────────
  window.scoutPaywallFreezeUntil = function (timestampSeconds) {
    if (!timestampSeconds || timestampSeconds <= 0) {
      clearFreezeUntil();
    } else {
      writeFreezeUntil(Math.floor(Number(timestampSeconds) || 0));
    }
    applyFreeze(true);
  };

  window.scoutPaywallUnfreeze = function () {
    clearFreezeUntil();
    applyFreeze(true);
  };

  window.scoutPaywallFreezeStatus = function () {
    return {
      frozen: isActiveFreezeUntil(),
      until: readFreezeUntil(),
      remainingSeconds: activeFreezeSecondsRemaining()
    };
  };

  // ── lifecycle: re-evaluate on auth change and on load ──────────────────
  function refreshFreeze() {
    applyFreeze(false);
  }

  document.addEventListener("DOMContentLoaded", function () {
    refreshFreeze();
  });

  // If the host fires auth subject switches, re-evaluate the freeze surface.
  document.addEventListener("xmrjackpot:auth", function () {
    refreshFreeze();
  });

  // Expose a polling-safe refresh in case the freeze timestamp is changed by
  // another tab / server toggle while this page is open.
  window.scoutPaywallRefreshFreeze = refreshFreeze;
})();
