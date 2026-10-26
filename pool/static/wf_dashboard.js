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

  /* EPOCH rows (Sep 26): the signals feed ages its row stamps with
     the SAME shared renderer as every other surface — epoch seconds
     converted to ISO for the shared call, exact UTC stamp on hover. */
  function relEpoch(ts) {
    var d = new Date((Number(ts) || 0) * 1000);
    if (!d.getTime()) { return ""; }
    return rel(d.toISOString());
  }

  function renderPrefsStamp(p) {
    var el = $("wfPrefsStamp");
    if (!el) return;
    /* THE NULL ANSWER (Sep 26, logout hygiene): a null ANSWER (a
       failed or 401 read) clears the stamp — the session's prefs are
       gone, not "unchanged", and the previous user's stamp must not
       linger until some future successful read overwrites it. A real
       envelope WITHOUT updated_at is still a fresh account and keeps
       saying so honestly. */
    if (p === null || p === undefined) {
      el.textContent = "";
      el.title = "";
      return;
    }
    var iso = p.updated_at;
    el.textContent = iso
      ? "prefs updated " + rel(iso)
      : "prefs never changed (defaults stand)";
    el.title = iso || "";
  }

  function refreshPrefsStamp() {
    var el = $("wfPrefsStamp");
    if (!el) return;
    fetch("/api/push/preferences", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(renderPrefsStamp)
      .catch(function () { /* quiet — the card stays without a stamp */ });
  }

  /* INSTANT STAMP (Sep 26): push_client announces every applied
     envelope (toggle writes, 412 adoptions, the auth re-read) —
     render from the event's detail the moment it lands instead of
     waiting for the 60s poll. xmrjackpot:auth ALSO triggers a fresh
     fetch so a subject switch to a fresh account clears the stamp
     honestly (no updated_at on the new envelope yet). */
  document.addEventListener("xmrjackpot:prefs", function (ev) {
    if (ev && ev.detail) { renderPrefsStamp(ev.detail); }
  });
  /* INSTANT STATUS ROWS (Sep 26): the auth event re-renders the
     wallet/miner dots the moment the subject switches — the same
     instant contract the prefs stamp has; without this they lagged
     up to 60s showing the PREVIOUS user's row state. */
  document.addEventListener("xmrjackpot:auth", refreshStatus);
  document.addEventListener("xmrjackpot:auth", refreshJackpotCycle);
  document.addEventListener("xmrjackpot:auth", refreshPrefsStamp);

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
        if (d && d.scout_pro_required) {
          // THE SERVER SAID NO (Oct 24): the feed is the paid product's
          // history, so name the lock instead of pretending it is empty.
          feed.innerHTML = '<span class="wf-feed-empty">Scout signals are ' +
            'part of Kraken Scout Pro — unlock from the panel below to ' +
            'receive them.</span>';
          return;
        }
        if (!rows.length) {
          feed.innerHTML = '<span class="wf-feed-empty">No signals pushed yet — ' +
            'they appear here the moment Scout fires.</span>';
          return;
        }
        feed.innerHTML = rows.map(function (s) {
          var d = new Date((Number(s.ts) || 0) * 1000);
          return '<div class="wf-feed-row"><span>' + esc(s.title) +
            '</span><time title="' + (d.getTime() ? d.toISOString() : "") +
            '">' + esc(relEpoch(s.ts)) + '</time></div>';
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


  /* ── the Jackpot Cycle panel: /api/jackpot/status (auth) ──────────
     One read per minute; the remaining-time row ticks locally from
     the server's next_drawing_epoch so the countdown is smooth and
     costs no extra requests. */
  var cycleDeadline = 0;

  function cycleInt(n) {
    var v = Number(n);
    return isFinite(v) ? v : null;
  }

  function pad2(v) { return (v < 10 ? "0" : "") + v; }

  function fmtRemaining(sec) {
    var t = Math.max(0, Math.floor(sec));
    var h = Math.floor(t / 3600);
    var m = Math.floor((t % 3600) / 60);
    var s = t % 60;
    return h > 0
      ? h + "h " + pad2(m) + "m " + pad2(s) + "s"
      : m + "m " + pad2(s) + "s";
  }

  function setCycle(id, text) {
    var el = $(id);
    if (el) { el.textContent = text; }
  }

  function tickCycle() {
    if (!cycleDeadline) { return; }
    var sec = Math.max(0, Math.round((cycleDeadline - Date.now()) / 1000));
    setCycle("wfJackpotCycleRemaining", fmtRemaining(sec));
    if (sec <= 0) {
      cycleDeadline = 0;
      refreshJackpotCycle();
    }
  }

  function renderJackpotCycle(d) {
    var epoch = cycleInt(d.next_drawing_epoch) || 0;
    var remain = cycleInt(d.next_drawing_seconds_remaining);
    cycleDeadline = epoch > 0
      ? epoch * 1000
      : Date.now() + (remain && remain > 0 ? remain * 1000 : 0);

    setCycle(
      "wfJackpotCycleNext",
      epoch > 0 ? new Date(epoch * 1000).toLocaleString() : "Not scheduled"
    );
    tickCycle();
    if (!cycleDeadline) { setCycle("wfJackpotCycleRemaining", "—"); }

    var pending = cycleInt(d.total_pending_entries);
    setCycle(
      "wfJackpotCyclePending",
      pending == null ? "—" : pending.toLocaleString()
    );

    var mine = cycleInt(d.your_pending_tickets);
    setCycle("wfJackpotCycleYourTickets", mine == null ? "—" : mine.toLocaleString());

    var total = cycleInt(d.your_total_tickets);
    setCycle("wfJackpotCycleYourTotal", total == null ? "—" : total.toLocaleString());

    var scout = d.scout || null;
    setCycle(
      "wfJackpotCycleScoutActive",
      !scout ? "No Scout identity" : (scout.has_active_cycle ? "Active in this cycle" : "Not active")
    );

    var jackpot = cycleInt(d.jackpot_xmr);
    var usd = cycleInt(d.jackpot_usd);
    // THE CONVERSION IS NAMED (Oct 26): "(~$645)" without a rate hides
    // the fact that it is a conversion, and hides a stale or fallback
    // rate behind a confident-looking dollar sign.
    var ri = d.xmr_usd_rate_info || null;
    var rateNote = "";
    if (ri && ri.label) {
      var line = String(ri.label);
      rateNote = " " + line.charAt(0).toUpperCase() + line.slice(1) + ".";
    }
    setCycle(
      "wfJackpotCycleExplain",
      jackpot == null
        ? ""
        : "Current jackpot: " + jackpot.toLocaleString() + " XMR" +
          (usd && usd > 0 ? " (~$" + usd.toLocaleString() + ")" : "") +
          "." + rateNote
    );

    setCycle("wfJackpotCycleBlocker", "");
  }

  function refreshJackpotCycle() {
    fetch("/api/jackpot/status", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) {
          setCycle("wfJackpotCycleBlocker", "Sign in to load your cycle and ticket counts.");
          setCycle("wfJackpotCycleRemaining", "—");
          cycleDeadline = 0;
          return;
        }
        renderJackpotCycle(d);
      })
      .catch(function () {
        setCycle("wfJackpotCycleBlocker", "Could not load cycle status.");
      });
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
    refreshJackpotCycle();
    refreshSignals();
    refreshPrefsStamp();
    setInterval(refreshJackpotCycle, 60000);
    setInterval(tickCycle, 1000);
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
