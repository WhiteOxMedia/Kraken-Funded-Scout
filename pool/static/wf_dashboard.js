/* wf_dashboard.js — THE WORKFLOW WIRING (Sep 25 redesign phase 2).
   Populates the Master-Key workflow layer's live surfaces: the
   dashboard's status row, the Scout Signals feed (the caller's own
   push history via /api/push/signals), and the Kraken Funded
   Challenge panel's referral total. ADDITIVE BY CONTRACT: this file
   owns ONLY the wf-* elements it adds — it never touches app.js's
   DOM, bindings, or behavior, and every fetch fails quiet. */
(function () {
  "use strict";

  var API = "";
  try { API = window.APP_API_BASE || ""; } catch (e) { API = ""; }
  if (!API) {
    /* app.js keeps its API base internal; derive from page origin */
    API = "";
  }

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function timeAgo(ts) {
    try {
      var s = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)));
      if (s < 60) return s + "s ago";
      if (s < 3600) return Math.floor(s / 60) + "m ago";
      if (s < 86400) return Math.floor(s / 3600) + "h ago";
      return Math.floor(s / 86400) + "d ago";
    } catch (e) { return ""; }
  }

  /* THE SHARED STAMP (Sep 26): the Scout panel shows the SAME
     notification-preferences updated_at the Notifications card
     shows, through the project's ONE relative-time renderer
     (push_client exports it) so both cards agree word for word.
     Fallback to the local timeAgo only if push_client failed to
     load. A fresh account (updated_at null) says so honestly. */
  function rel(iso) {
    try {
      if (window.XMRJackpotPush && window.XMRJackpotPush.relTime) {
        return window.XMRJackpotPush.relTime(iso);
      }
    } catch (e) { /* fall through */ }
    var d = new Date(iso);
    return isNaN(d) ? "" : timeAgo(d.getTime() / 1000);
  }

  function refreshPrefsStamp() {
    var el = $("wfPrefsStamp");
    if (!el) return;
    fetch("/api/push/preferences", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (p) {
        var iso = p && p.updated_at;
        el.textContent = iso
          ? "prefs updated " + rel(iso)
          : "prefs never changed (defaults stand)";
        el.title = iso || "";
      })
      .catch(function () { /* quiet — the card stays without a stamp */ });
  }

  /* the status row: miner / scout / wallet states from the endpoints
     app.js already uses — read-only, no mutation, no new calls */
  function refreshStatus() {
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (me) {
        var authed = !!(me && me.authenticated);
        if ($("wfDotWallet") && $("wfWalletStatus")) {
          var w = (me && me.wallet_address) || "";
          $("wfDotWallet").className = "wf-dot" + (w ? " on" : "");
          $("wfWalletStatus").textContent = w ? "Connected (read-only)" : "One-directional";
        }
        fetch("/api/profile/stats", { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
            var miner = (d && d.miner) || {};
            var active = authed && !!miner.hashrate;
            if ($("wfDotMiner") && $("wfMinerStatus")) {
              $("wfDotMiner").className = "wf-dot" + (active ? " on" : "");
              $("wfMinerStatus").textContent = active
                ? "On — " + (miner.shares_valid || 0) + " shares"
                : (authed ? "Off" : "Login to start");
            }
            if ($("wfDotScout") && $("wfScoutStatus")) {
              $("wfDotScout").className = "wf-dot" + (authed ? " on" : "");
              $("wfScoutStatus").textContent = authed
                ? "Connected"
                : "Not Connected";
            }
            if (authed) { refreshChallenge(d); }
          })
          .catch(function () { /* quiet */ });
      })
      .catch(function () { /* quiet */ });
  }

  /* the signals feed: ONLY the caller's own whitelisted rows */
  function refreshSignals() {
    var feed = $("wfSignalFeed");
    if (!feed) return;
    fetch("/api/push/signals", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var rows = (d && d.signals) || [];
        if (!rows.length) {
          feed.innerHTML = '<span class="wf-feed-empty">No signals pushed yet — ' +
            'they appear here the moment Scout fires.</span>';
          return;
        }
        feed.innerHTML = rows.map(function (s) {
          return '<div class="wf-feed-row"><span>' + esc(s.title) +
            '</span><time>' + esc(timeAgo(s.ts)) + '</time></div>';
        }).join("");
      })
      .catch(function () {
        feed.innerHTML = '<span class="wf-feed-empty">Could not load signal history.</span>';
      });
  }

  /* the challenge panel: entry-free status + the referral total */
  function refreshChallenge(d) {
    if ($("wfChallengeStatus")) {
      $("wfChallengeStatus").textContent = "Open — jackpot win not required to view tiers";
    }
    if ($("wfChallengeProgress")) {
      $("wfChallengeProgress").textContent = "Awaiting entry";
    }
    var el = $("wfReferralEarned");
    if (el && d && d.referral) {
      el.textContent = String(d.referral.count || 0) + " referrals";
    }
  }

  /* CTA wiring — ids added in this redesign; no existing hooks touched */
  function go(page, then) {
    var btn = document.querySelector('.nav-links button[data-page="' + page + '"]');
    if (btn) { btn.click(); }
    if (then) { setTimeout(then, 350); }
  }

  function wire() {
    var map = [
      ["btnHeroStartFree", function () { go("signup"); }],
      ["btnHeroJoinJackpot", function () { go("dashboard"); }],
      ["btnWfJoinJackpot", function () { go("dashboard"); }],
      ["btnWfGetScout", function () { go("signup"); }],
      ["btnWfEnterChallenge", function () { go("signup"); }],
      ["btnWfUseReferral", function () { go("signup"); }],
      ["btnWfTurnMinerOn", function () { go("dashboard"); }],
      ["btnWfScoutNotify", function () {
        var ep = $("btnEnablePush");
        if (ep) { ep.click(); } else {
          go("signup", function () {
            var e2 = $("btnEnablePush");
            if (e2) { e2.click(); }
          });
        }
      }],
      ["btnWfEnterChallengeDash", function () { go("signup"); }],
      ["btnWfCollectWinnings", function () { go("dashboard"); }]
    ];
    map.forEach(function (p) {
      var el = $(p[0]);
      if (el && !el.dataset.wfWired) {
        el.dataset.wfWired = "1";
        el.addEventListener("click", p[1]);
      }
    });
  }

  function boot() {
    wire();
    refreshStatus();
    refreshSignals();
    refreshPrefsStamp();
    setInterval(refreshSignals, 60000);
    setInterval(refreshStatus, 60000);
    setInterval(refreshPrefsStamp, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
