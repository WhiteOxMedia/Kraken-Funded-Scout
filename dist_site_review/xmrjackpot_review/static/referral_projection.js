/* referral_projection.js — THE POSSIBLE FUTURE JACKPOT CARD (Sep 26).

   Reads /api/jackpot/projection and injects one card under the hero
   jackpot plate (runtime injection, same pattern as
   admin_cold_wallet.js, so index.html's own diff stays minimal).

   Shows, clearly labeled as an ESTIMATE:
     - Possible future jackpot (incl. Kraken referral bonus)
     - Current jackpot + projected referral seed
   A "methodology" link opens a modal explaining the assumptions,
   the source, and the last-updated timestamp. The card NEVER
   presents the projection as the payable pot. */
(function () {
  "use strict";

  var tries = 0;

  function fmtUsd(x) {
    var n = Number(x || 0);
    return "$" + n.toLocaleString("en-US",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function modal(html) {
    var ov = document.createElement("div");
    ov.id = "refProjModal";
    ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.72);" +
      "display:flex;align-items:center;justify-content:center;z-index:9999;";
    var box = document.createElement("div");
    box.style.cssText = "max-width:560px;max-height:80vh;overflow:auto;" +
      "background:var(--panel,#14181c);color:var(--text,#eee);padding:22px;" +
      "border:1px solid var(--gold,#c2a24a);border-radius:10px;";
    box.innerHTML = html;
    ov.appendChild(box);
    ov.addEventListener("click", function (e) {
      if (e.target === ov || e.target.id === "refProjClose") {
        ov.remove();
      }
    });
    document.body.appendChild(ov);
  }

  function methodology(d) {
    var when = d.updated_at
      ? new Date(d.updated_at * 1000).toISOString().replace("T", " ")
          .slice(0, 16) + " UTC"
      : "no projection persisted yet";
    modal(
      "<h3 style='margin-top:0;color:var(--gold,#c2a24a);'>Methodology — " +
      "Possible future jackpot</h3>" +
      "<p><b>What this is:</b> an ESTIMATE. The current jackpot is real " +
      "and unchanged; the referral seed is a projection of what could be " +
      "added if Kraken pays the referral bonuses currently tracked.</p>" +
      "<p><b>Where the numbers come from:</b> each user's Kraken referral " +
      "status (source: <code>" + esc(d.source) + "</code>, provider mode: " +
      "<code>" + esc(d.mode) + "</code>). Explicit Kraken pending amounts " +
      "are used as-is; otherwise a conservative estimate is derived from " +
      "the pool's own 14-day observed referral velocity, scaled by a " +
      "confidence factor (default 0.6). Only Paid, Pending and Eligible " +
      "statuses contribute.</p>" +
      "<p><b>Freshness:</b> the projection is refetched at most hourly " +
      "(configurable); shown data was persisted <b>" + when + "</b> from " +
      "<b>" + Number(d.count_of_users_included || 0) + "</b> user " +
      "statuses. Currency is USD, rounded to cents.</p>" +
      "<p style='color:var(--text-mute,#9a9a9a);'>This is not a payable " +
      "balance. Kraken referral terms apply; nothing here automates any " +
      "exchange action.</p>" +
      "<button id='refProjClose' class='btn' style='width:100%;'>Close" +
      "</button>");
  }

  function render(d) {
    var hero = document.getElementById("heroJackpot");
    if (!hero) { return; }
    var plate = hero.closest(".jackpot-hero") || hero.parentElement;
    if (!plate || document.getElementById("refProjCard")) { return; }
    // HONESTY: with no configured provider the big number is the REAL
    // current jackpot — a persisted (possibly simulated/stale)
    // projection is never displayed as if it were money in play.
    var unconf = d.mode === "unconfigured";
    var big = unconf ? d.current_jackpot_usd
                     : d.possible_future_jackpot_usd;
    var card = document.createElement("div");
    card.id = "refProjCard";
    card.style.cssText = "margin-top:14px;padding-top:12px;" +
      "border-top:1px solid var(--hairline,rgba(255,255,255,.12));" +
      "font-size:0.92em;";
    var anomaly = d.anomaly
      ? " <span style='color:var(--red,#e05555);font-weight:700;'>" +
        "&#9888; flagged: projection jumped &gt; threshold — see ops log" +
        "</span>"
      : "";
    var stale = "";
    if (d.source !== "none" && !d.fresh) {
      stale = " <span style='color:var(--text-mute,#9a9a9a);'>(stale — " +
        "worker has not refreshed inside its TTL)</span>";
    }
    var notConf = "";
    if (unconf) {
      notConf = "<div style='color:var(--text-mute,#9a9a9a);margin-top:6px;'>" +
        "referral projection: not configured — no Kraken API credentials " +
        "on this box; showing current jackpot only</div>";
    }
    card.innerHTML =
      "<div title='Estimate only: the current jackpot is real and " +
      "unchanged. The referral seed projects what Kraken referral " +
      "bonuses could add (Paid + Pending + Eligible statuses, " +
      "conservative formula). Click Methodology for assumptions.'>" +
      "<span style='color:var(--text-mute,#9a9a9a);font-size:0.85em;'>" +
      "Possible future jackpot (incl. Kraken referral bonus)</span><br>" +
      "<span style='font-weight:800;color:var(--gold,#c2a24a);'>" +
      fmtUsd(big) + "</span>" + anomaly + stale +
      "</div>" +
      (unconf ? "" :
      "<div style='margin-top:4px;color:var(--text-dim,#bbb);'>" +
      "current " + fmtUsd(d.current_jackpot_usd) +
      " + projected referral seed " + fmtUsd(d.referral_seed_usd) +
      " <span style='color:var(--text-mute,#9a9a9a);'>(" +
      Number(d.count_of_users_included || 0) + " referral statuses)" +
      "</span></div>") + notConf +
      "<div style='margin-top:6px;'>" +
      "<a href='#' id='refProjMethod' style='color:var(--gold,#c2a24a);'>" +
      "Methodology &amp; assumptions</a></div>";
    plate.appendChild(card);
    card.querySelector("#refProjMethod").addEventListener(
      "click", function (e) { e.preventDefault(); methodology(d); });
  }

  function boot() {
    var hero = document.getElementById("heroJackpot");
    if (!hero) {
      if (tries++ < 40) { setTimeout(boot, 250); }
      return;
    }
    fetch("/api/jackpot/projection")
      .then(function (r) { return r.json(); })
      .then(function (d) { render(d); })
      .catch(function () { /* the plate is complete without us */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
