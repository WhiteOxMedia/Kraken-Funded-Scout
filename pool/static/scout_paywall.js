/* scout_paywall.js — first-impression UX module (Oct 17):
   donation/unlock flow, Scout activation + one-active-
   identity limiter, miner polling, XMR wallet status, Kraken referral
   tracking, and the admin toggles.

   API hooks:
     POST /scout/purchase        — donate & unlock flow (spec hook)
     POST /api/scout/activate    — Activate Scout (the pool's real route)
     GET  /api/scout/status      — miner-proof polling / active state
     GET  /api/jackpot/status    — read by app.js's cycle panel
     GET  /api/auth/me           — the connected payout wallet address

   Contract: this module owns ONLY the surfaces it renders itself; the
   shared buttons are bound in app.js (which renders the panels first),
   and both modules meet at window.XMRJackpotScoutPaywall so no click
   ever fires twice. It LISTENS for xmrjackpot:auth like every other
   per-user surface, so a subject switch never leaves stale copy. */
(function () {
  "use strict";

  var BLOCKER_COPY =
    "Only one Scout can be active per identity in each jackpot cycle. You already have an active Scout for this cycle.";

  // DOM element accessor — delegates to the shared scout_ids module
  // (which maps legacy ids to wf* ids and provides the same $() contract).
  var scoutIds = window.scoutIds;
  function $(id) {
    if (scoutIds && typeof scoutIds.$ === "function") {
      return scoutIds.$(id);
    }
    return document.getElementById(id);
  }

  /* ── analytics events ──────────────────────────────────────────
     Emits (spec list): miner_download_clicked, miner_started,
     scout_purchased, scout_activated, scout_activation_blocked,
     kraken_referral_clicked, xmr_wallet_connected.
     Delivery: window.XMRJackpotAnalytics.track if a host installed
     one; otherwise events are queued on window.XMRJackpotQueuedEvents
     and, once an admin points window.XMRJ_ANALYTICS_URL at a live
     endpoint, beaconed there (the /api/analytics/event endpoint is an
     admin TODO — nothing 404s by default). Kraken referral clicks are
     ALSO counted by the real ledger through data-cta-slot. */
  function track(name, detail) {
    var payload = { event: name, detail: detail || {}, ts: Date.now() };
    try {
      if (window.XMRJackpotAnalytics && window.XMRJackpotAnalytics.track) {
        window.XMRJackpotAnalytics.track(name, payload.detail);
      } else if (navigator.sendBeacon && window.XMRJ_ANALYTICS_URL) {
        navigator.sendBeacon(
          window.XMRJ_ANALYTICS_URL,
          new Blob([JSON.stringify(payload)], { type: "application/json" }));
      } else {
        window.XMRJackpotQueuedEvents = window.XMRJackpotQueuedEvents || [];
        window.XMRJackpotQueuedEvents.push(payload);
      }
    } catch (e) { /* quiet — analytics never blocks a UX flow */ }
  }

  /* ── the one-active-Scout-per-cycle limiter (acceptance 2) ──── */
  function showActivationLimiter(message) {
    return;
      "<h3>Activation blocked</h3><p>" + (message || BLOCKER_COPY) + "</p>" +
      "<p class=\"micro\">You can activate this Scout in the next jackpot cycle.</p>" +
      "<button id=\"btn-see-next-cycle\" type=\"button\">See next cycle</button>";
    var b = $("btn-see-next-cycle");
    if (b) {
      b.addEventListener("click", function () {
        var p = $("jackpot-cycle-panel");
        if (p) { p.scrollIntoView({ behavior: "smooth" }); }
      });
    }
  }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
  }

  function fieldValue(el) {
    if (!el) return "";
    return String(el.value != null ? el.value : (el.textContent || "")).trim();
  }

  /* ── Activate Scout ───────────────────────────────────────────
     Payload: identity_token — the pool's actual lookup key — plus
     identity_wallet and scout_id. Answers handled: the spec's
     AlreadyActiveScoutForCycle error, the real envelope's
     already_active flag, and the one-active-scout detail string.
     
     DOM reads: the template does not render scout-id / identity-wallet
     inputs, so read from window locals set by the
     scout_paywall_wire_patch module (which owns those fields). For the
     status line, use the wf-scout panel's status element. */
  function activateScoutImpl() {
    // Read from window locals (set by wire patch) when DOM inputs absent
    var scoutIdEl = $("scout-id");
    var walletEl = $("identity-wallet");
    var us = $("unlock-status");

    var scoutId = scoutIdEl ? fieldValue(scoutIdEl) : (window.selectedScoutId != null ? String(window.selectedScoutId) : "");
    var wallet = walletEl ? fieldValue(walletEl) : (window.identityWallet || "");

    if (!wallet) { track("scout_activated_error", { reason: "missing_fields" }); return; }
    track("scout_activated", { scout_id: scoutId });    post("/api/scout/activate", {
      identity_token: wallet,
      scout_id: scoutId,
      identity_wallet: wallet
    })
      // A 409 AlreadyActiveScoutForCycle carries the limiter message in its
      // body, so parse every JSON response instead of dropping non-2xx ones.
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (d) {
        var blocked = !!d && (
          d.error === "AlreadyActiveScoutForCycle" ||
          d.code === "AlreadyActiveScoutForCycle" ||
          d.already_active === true ||
          (d.ok === false && String(d.detail || "").indexOf("one-active-scout") === 0)
        );
        if (blocked) {
          showActivationLimiter(d.message || d.detail || BLOCKER_COPY);
          track("scout_activation_blocked", { reason: "already_active" });
        } else if (d && d.ok !== false && (d.active === true || d.ok === true)) {
          if (us) { us.textContent = "Your Scout is active for this jackpot cycle. Mining begins now."; }
          track("scout_activated_success", { identity_id: d.identity_id });
        } else {
          if (us) { us.textContent = "Activation could not be completed. Please try again."; }
          track("scout_activated_error", { reason: d && d.detail ? d.detail : "unexpected" });
        }
      })
      .catch(function () {
        if (us) { us.textContent = "Activation could not be completed. Please try again."; }
        track("scout_activated_error", { reason: "network" });
      });
  }

  /* ── Donate and unlock Scout Pro (spec hook: POST /scout/purchase)
     Opens the donation modal instantly (no dead click), then asks the
     purchase endpoint for a checkout URL; an unreachable endpoint says
     so honestly instead of pretending to take money. */
  function openDonateFlowImpl() {
    track("donate_unlock_clicked");
    if (typeof openDonateFlowExternal === "function") { openDonateFlowExternal(); return; }
    var st = $("donate-status");
    if (st) { st.textContent = "Starting checkout…"; }
    post("/scout/purchase", { action: "donate_unlock" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.checkout_url) {
          track("scout_purchased", { checkout: d.checkout_url });
          if (st) { st.textContent = "Checkout ready — follow the prompts to donate and unlock Scout Pro."; }
        } else if (st) {
          track("donate_unlock_error");
          st.textContent =
            (d && d.message) || "Checkout is unavailable right now — nothing was charged. Try again shortly.";
        }
      })
      .catch(function () {
        track("donate_unlock_error");
        if (st) {
          st.textContent =
            "Checkout is unavailable right now — nothing was charged. Try again shortly.";
        }
      });
  }

  /* ── I started mining → poll the real pool route ─────────────── */
  function pollScoutStatus() {
    track("miner_started");
    var el = $("unlock-status");
    if (el) { el.textContent = "Polling /api/scout/status for your miner…"; }
    var tries = 0;
    var id = setInterval(function () {
      tries += 1;
      fetch("/api/scout/status", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          var active = d && ((d.scout && d.scout.has_active_cycle === true) || d.active === true);
          if (active) {
            if (el) { el.textContent = "Your Scout is active for this jackpot cycle. Mining begins now."; }
            clearInterval(id);
            track("scout_activated_success", { via: "miner_proof" });
          } else if (tries >= 40) {
            clearInterval(id);
            if (el) { el.textContent = "Still waiting — keep the miner running and check back shortly."; }
          } else if (d) {
            if (el) { el.textContent = "Waiting for miner proof…"; }
          }
        })
        .catch(function () { /* quiet — next tick retries */ });
    }, 8000);
  }

  /* ── XMR wallet payout status (the real connected address) ───── */
  function refreshWalletStatus() {
    var el = $("xmr-wallet-status");
    if (!el) {
      // wf* template uses wfWalletStatus instead
      el = document.getElementById("wfWalletStatus");
    }
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (me) {
        var w = (me && me.wallet_address) || "";
        if (el) {
          el.textContent = w
            ? "Connected: " + w + " — winnings are paid here."
            : "No XMR wallet on this account yet — connect one so winnings can be paid out.";
        }
      })
      .catch(function () { /* quiet */ });
  }

  function connectXmrWallet() {
    track("xmr_wallet_connected");
    refreshWalletStatus();
    post("/scout/purchase", { action: "connect_xmr_wallet" }).catch(function () {
      /* spec hook — the wallet itself lives in the account profile */
    });
  }

  /* ── the trackable Kraken referral link (also counted by the
        real ledger through data-cta-slot → /api/cta/kraken-click) ── */
  function trackReferralClick() {
    var link = $("kraken-referral-link");
    if (link && !link.dataset.wfWired) {
      link.dataset.wfWired = "1";
      link.addEventListener("click", function () {
        track("kraken_referral_clicked", { referrer: link.href });
      });
    }
  }

  /* ── admin toggles (defined inline in index.html; defaults keep
        social links hidden and the referral block last) ─────────── */
  function applyToggles() {
    var t = window.XMRJackpotToggles || {};
    var share = $("ref-share-row");
    if (share) { share.style.display = t.showSocialLinks ? "" : "none"; }
    var rb = $("referral-block");
    if (rb && t.showReferralBlock === false) { rb.style.display = "none"; }
    if (t.enableMinerDownload === false) {
      ["btn-download-miner", "btn-miner-download"].forEach(function (id) {
        var e = $(id);
        if (e) { e.disabled = true; }
        if (e && e.tagName === "A") { e.classList.add("disabled"); }
      });
    }
    var dex = $("cta-buy-xmrj-dex");
    if (dex && t.dexUrl) { dex.href = t.dexUrl; }
  }

  document.addEventListener("DOMContentLoaded", function () {
    trackReferralClick();
    var dc = $("btn-donate-close");
    if (dc) {
      dc.addEventListener("click", function () {
      });
    }
    applyToggles();
    refreshWalletStatus();
  });

  /* subject switch: a login/logout re-reads the payout wallet so the
     wallet CTA can never show the previous identity's address */
  document.addEventListener("xmrjackpot:auth", refreshWalletStatus);

  /* cross-module surface — app.js delegates here */
  window.XMRJackpotScoutPaywall = {
    track: track,
    showActivationLimiter: showActivationLimiter,
    openDonateFlow: openDonateFlowImpl,
    activateScout: activateScoutImpl,
    pollScoutStatus: pollScoutStatus,
    connectXmrWallet: connectXmrWallet,
    trackReferralClick: trackReferralClick
  };
})();