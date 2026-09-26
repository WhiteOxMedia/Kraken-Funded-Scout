/* push_decisions_admin.js — THE SUPPRESSION/SEND LEDGER (Sep 26).

   Live view of the push sender's decisions, straight from the audit
   trail: send_allowed / send_blocked / rate_limited / broadcast /
   subscribe / unsubscribe — newest first. Filters: action (the two
   send-decision rows preselected via quick buttons), category
   (jackpot/scout), user. Auto-refreshes every 20s; a NEW row since
   the last poll pulses so live suppressions are visible at a glance.

   Non-admins: 401 -> the card removes itself. */
(function () {
  "use strict";

  var CARD_ID = "pushDecisionsCard";
  var tries = 0;
  var lastSeenId = 0;
  var state = { action: "", category: "" };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function ts(t) {
    var d = new Date((t || 0) * 1000);
    return d.toISOString().replace("T", " ").slice(5, 19) + "Z";
  }

  function badge(item) {
    var color = { push_send_allowed: "var(--emerald-bright,#2ecc71)",
                  push_send_blocked: "#e05555",
                  push_rate_limited: "#c9a227",
                  push_broadcast: "var(--lapis,#7aa2f7)",
                  push_subscribe: "var(--emerald-bright,#2ecc71)",
                  push_unsubscribe: "#c9a227" }[item.action] || "#9a9a9a";
    var label = item.action.replace("push_", "");
    return '<span style="color:' + color + ';font-weight:700;">' + label + "</span>";
  }

  function rowHtml(item) {
    var fresh = item.id > lastSeenId;
    var bits = [badge(item)];
    if (item.event) { bits.push(esc(item.event)); }
    if (item.category) { bits.push('<span style="color:var(--text-mute,#9a9a9a);">[' + esc(item.category) + "]</span>"); }
    if (item.scope) { bits.push("(" + esc(item.scope) + ")"); }
    if (item.reason) { bits.push('<span style="color:#e05555;">' + esc(item.reason) + "</span>"); }
    if (item.sent != null) { bits.push("sent " + esc(item.sent) + (item.dead ? " · dead " + esc(item.dead) : "")); }
    return '<tr' + (fresh ? ' style="background:rgba(46,204,113,.08);"' : "") + ">" +
      '<td style="padding:3px 6px;white-space:nowrap;font-size:0.72em;color:var(--text-mute,#9a9a9a);">' + ts(item.ts) + "</td>" +
      '<td style="padding:3px 6px;font-size:0.75em;">' + esc(item.username) + "</td>" +
      '<td style="padding:3px 6px;font-size:0.75em;">' + bits.join(" ") + "</td></tr>";
  }

  function render(items) {
    var opts = function (list, cur) {
      return list.map(function (v) {
        return '<option value="' + v[0] + '"' + (cur === v[0] ? " selected" : "") + ">" + v[1] + "</option>";
      }).join("");
    };
    return (
      '<div class="card" style="margin-bottom:24px;">' +
      '<div class="card-header">🛡️ Push Decisions — live ledger</div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;font-size:0.78em;">' +
      '<select id="pdAction" style="padding:4px 6px;background:var(--bg-deep,#0d1117);color:var(--text,#eee);border:1px solid var(--hairline,rgba(255,255,255,.14));border-radius:4px;">' +
      opts([["", "all actions"], ["push_send_blocked", "send_blocked"], ["push_send_allowed", "send_allowed"],
            ["push_rate_limited", "rate_limited"], ["push_broadcast", "broadcast"]], state.action) + "</select>" +
      '<select id="pdCategory" style="padding:4px 6px;background:var(--bg-deep,#0d1117);color:var(--text,#eee);border:1px solid var(--hairline,rgba(255,255,255,.14));border-radius:4px;">' +
      opts([["", "all categories"], ["jackpot", "jackpot"], ["scout", "scout"]], state.category) + "</select>" +
      '<button id="pdRefresh" class="btn btn-sm" style="margin-left:auto;">↻ refresh</button>' +
      '<span style="font-size:0.72em;color:var(--text-mute,#9a9a9a);align-self:center;">auto 20s</span>' +
      "</div>" +
      '<div style="max-height:340px;overflow:auto;">' +
      '<table style="width:100%;border-collapse:collapse;">' +
      (items.length
        ? items.map(rowHtml).join("")
        : '<tr><td style="padding:10px;color:var(--text-mute,#9a9a9a);font-size:0.8em;">no decision rows match</td></tr>') +
      "</table></div>" +
      '<div style="margin-top:6px;font-size:0.7em;color:var(--text-mute,#9a9a9a);">' +
      "suppressions are decisions, not silence: every block names its " +
      "category and reason · audit-derived · newest first</div>" +
      "</div>");
  }

  function wire(items) {
    var maxId = items.reduce(function (m, i) { return Math.max(m, i.id); }, lastSeenId);
    lastSeenId = maxId;
    document.getElementById("pdAction").addEventListener("change", function () {
      state.action = this.value; fetchRender();
    });
    document.getElementById("pdCategory").addEventListener("change", function () {
      state.category = this.value; fetchRender();
    });
    document.getElementById("pdRefresh").addEventListener("click", fetchRender);
  }

  function fetchRender() {
    var wrap = document.getElementById(CARD_ID);
    var q = "/api/admin/push/decisions?limit=60" +
      (state.action ? "&action=" + state.action : "") +
      (state.category ? "&category=" + state.category : "");
    fetch(q, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) { return; }
        wrap.innerHTML = render(j.decisions);
        wire(j.decisions);
      })
      .catch(function () { /* fail-soft */ });
  }

  function mount() {
    if (document.getElementById(CARD_ID)) { return true; }
    var page = document.getElementById("page-admin");
    if (!page) { return false; }
    var anchor = page.querySelector(".card");
    if (!anchor) { return false; }
    var wrap = document.createElement("div");
    wrap.id = CARD_ID;
    wrap.innerHTML = "loading push decisions…";
    anchor.parentNode.insertBefore(wrap, anchor);
    fetch("/api/admin/push/decisions?limit=60", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) { wrap.remove(); return; }   // 401/403: not an admin
        wrap.innerHTML = render(j.decisions);
        wire(j.decisions);
        setInterval(fetchRender, 20000);      // live
      })
      .catch(function () { wrap.remove(); });
    return true;
  }

  var timer = setInterval(function () {
    tries += 1;
    if (mount() || tries > 120) { clearInterval(timer); }
  }, 1000);
})();
