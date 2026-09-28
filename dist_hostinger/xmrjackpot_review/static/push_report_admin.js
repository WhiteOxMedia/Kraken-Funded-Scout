/* push_report_admin.js — THE DAILY PUSH REPORT CARD (Sep 26).

   Admin-panel card showing yesterday's push traffic per category
   (sent / dead / blocked / rate-limited), subscription motion, a
   7-day trend sparkline of sends, and today's live counters. All
   numbers come from the audit-derived rollups the scheduler rolls
   up after midnight — nothing sampled, nothing estimated.

   Non-admins: 401 -> the card removes itself. */
(function () {
  "use strict";

  var CARD_ID = "pushReportCard";
  var tries = 0;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function catRow(name, m, accent) {
    return '<tr style="border-top:1px solid var(--hairline,rgba(255,255,255,.1));">' +
      '<td style="padding:4px 6px;color:' + accent + ';font-weight:700;">' + name + "</td>" +
      '<td style="padding:4px 6px;text-align:right;">' + m.sent + "</td>" +
      '<td style="padding:4px 6px;text-align:right;color:var(--text-mute,#9a9a9a);">' + m.dead + "</td>" +
      '<td style="padding:4px 6px;text-align:right;color:#c9a227;">' + m.blocked + "</td>" +
      '<td style="padding:4px 6px;text-align:right;color:#c9a227;">' + m.rate_limited + "</td></tr>";
  }

  function spark(days) {
    // sends per day, last N days, as tiny bars
    var max = 1;
    days.forEach(function (d) {
      var t = d.categories.jackpot.sent + d.categories.scout.sent;
      if (t > max) { max = t; }
    });
    return days.map(function (d) {
      var t = d.categories.jackpot.sent + d.categories.scout.sent;
      var h = Math.max(2, Math.round(t / max * 26));
      return '<div title="' + d.day + ": " + t + ' sends" style="display:inline-block;' +
        'width:10px;height:' + h + 'px;background:var(--emerald-bright,#2ecc71);' +
        'margin-right:2px;border-radius:2px 2px 0 0;vertical-align:bottom;"></div>';
    }).join("");
  }

  function render(rep) {
    var y = rep.days[0] || rep.today_live;
    var t = rep.today_live;
    var yj = y.categories.jackpot, ys = y.categories.scout, yu = y.categories.uncategorized;
    var tj = t.categories.jackpot, ts = t.categories.scout;
    var subs = y.subscriptions;
    var hist = rep.days.slice().reverse().concat([rep.today_live]);
    return (
      '<div class="card" style="margin-bottom:24px;">' +
      '<div class="card-header">📊 Push Report — daily</div>' +
      '<div style="font-size:0.78em;color:var(--text-mute);margin-bottom:8px;">' +
      "yesterday " + esc(y.day) + " · live today " + esc(t.day) +
      " · audit-derived, never sampled</div>" +
      '<table style="width:100%;border-collapse:collapse;font-size:0.82em;">' +
      "<tr style=\"color:var(--text-mute,#9a9a9a);text-align:right;\">" +
      '<th style="text-align:left;padding:4px 6px;">category</th>' +
      "<th>sent</th><th>dead</th><th>blocked</th><th>limited</th></tr>" +
      catRow("jackpot", yj, "var(--gold,#c9a227)") +
      catRow("scout", ys, "var(--emerald-bright,#2ecc71)") +
      catRow("other", yu, "var(--lapis,#7aa2f7)") +
      "</table>" +
      '<div style="font-size:0.78em;color:var(--text-dim,#bbb);margin-top:8px;">' +
      "subscriptions: " + subs.subscribed + " new · " + subs.unsubscribed +
      " removed · " + subs.current_count + " devices on file</div>" +
      '<div style="margin-top:10px;font-size:0.75em;color:var(--text-mute);">' +
      "sends, last 7 days<br><span style=\"line-height:28px;\">" + spark(hist) + "</span></div>" +
      '<div style="margin-top:8px;font-size:0.78em;color:var(--text-dim);">' +
      "today so far: jackpot " + tj.sent + "/" + tj.blocked + " · scout " +
      ts.sent + "/" + ts.blocked + " (sent/blocked)</div>" +
      '<div style="margin-top:6px;font-size:0.7em;color:var(--text-mute);">' +
      "rolled up daily by the scheduler · 90-day retention · " +
      '<a href="/api/admin/push/report?days=30" target="_blank" rel="noopener" style="color:var(--lapis,#7aa2f7);">30-day JSON ↗</a></div>' +
      "</div>");
  }

  function mount() {
    if (document.getElementById(CARD_ID)) { return true; }
    var page = document.getElementById("page-admin");
    if (!page) { return false; }
    var anchor = page.querySelector(".card");
    if (!anchor) { return false; }
    var wrap = document.createElement("div");
    wrap.id = CARD_ID;
    wrap.innerHTML = "loading push report…";
    anchor.parentNode.insertBefore(wrap, anchor);
    fetch("/api/admin/push/report?days=7", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (rep) {
        if (!rep) { wrap.remove(); return; }
        wrap.innerHTML = render(rep);
      })
      .catch(function () { wrap.remove(); });
    return true;
  }

  var timer = setInterval(function () {
    tries += 1;
    if (mount() || tries > 120) { clearInterval(timer); }
  }, 1000);
})();
