/* referral_admin.js — THE KRAKEN PAYOUT RECORDER (Oct 25).

   One admin card, injected at runtime like admin_cold_wallet.js (the
   admin panel HTML is served separately and stays untouched). It is the
   manual leg of Program A: Kraken pays a referral bonus into the
   AFFILIATE's Kraken account, never into the pool, and no Kraken feed
   tells us it happened — so the operator verifies the payout and records
   it here. The pool keeps its share (pool_share_pct, default 50) as a
   funding-ledger receipt; the referrer keeps the rest from Kraken
   directly. This card never pays a user out of pool funds.

   It also carries the two referral settings:
     - XMRJ per successful referral (Program B's rate; 0 = not set)
     - the pool's share of a recorded Kraken payout

   Non-admins get a 403 from both APIs and the card removes itself — no
   content, no error noise. */
(function () {
  "use strict";

  var CARD_ID = "referralAdminCard";
  var tries = 0;
  var DATA = null;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function money(v) {
    return "$" + Number(v || 0).toFixed(2);
  }

  function field(label, id, value, ph) {
    return '<div style="margin-bottom:8px;">' +
      '<label style="display:block;color:var(--text-mute);font-size:0.78em;' +
      'margin-bottom:2px;">' + label + "</label>" +
      '<input id="' + id + '" type="number" min="0" step="1" ' +
      'placeholder="' + esc(ph || "") + '" value="' + esc(value) + '" ' +
      'style="width:100%;padding:7px 9px;background:var(--bg-deep,#0d1117);' +
      'border:1px solid var(--hairline,rgba(255,255,255,.14));' +
      'border-radius:6px;color:var(--text,#eee);"></div>';
  }

  function textField(label, id, ph) {
    return '<div style="margin-bottom:8px;">' +
      '<label style="display:block;color:var(--text-mute);font-size:0.78em;' +
      'margin-bottom:2px;">' + label + "</label>" +
      '<input id="' + id + '" type="text" placeholder="' + esc(ph || "") + '" ' +
      'style="width:100%;padding:7px 9px;background:var(--bg-deep,#0d1117);' +
      'border:1px solid var(--hairline,rgba(255,255,255,.14));' +
      'border-radius:6px;color:var(--text,#eee);"></div>';
  }

  function rows(list) {
    if (!list || !list.length) {
      return '<div style="font-size:0.8em;color:var(--text-mute);">' +
        "No payouts recorded yet.</div>";
    }
    return list.map(function (p) {
      return '<div style="display:flex;justify-content:space-between;gap:10px;' +
        'padding:4px 0;border-bottom:1px solid var(--hairline);font-size:0.8em;">' +
        '<span>' + esc(p.referred_username || ("user " + p.referred_user_id)) +
        ' <span style="color:var(--text-mute);">→ ' +
        esc(p.referrer_username || ("user " + p.referrer_id)) + "</span></span>" +
        '<span style="color:var(--text-mute);">' + esc(p.period) + " · " +
        money(p.gross_usd) + " gross · " + money(p.pool_usd) +
        " to pot (" + esc(p.pct) + "%)</span></div>";
    }).join("");
  }

  function render() {
    var d = DATA || {};
    return '<div class="card" style="margin-bottom:24px;">' +
      '<div class="card-header">Kraken Referral Payouts</div>' +
      '<div style="font-size:0.8em;color:var(--text-mute);line-height:1.6;' +
      'margin-bottom:10px;">Kraken pays the bonus into the affiliate\'s own ' +
      'Kraken account — record what you actually received. The pool keeps ' +
      'its share below; the referrer keeps the rest from Kraken directly.</div>' +
      '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px;">' +
      '<div style="flex:1 1 160px;">' +
      field("XMRJ per referral", "referralXmrjRate",
            d.xmrj_per_referral || 0, "0 = not set") + "</div>" +
      '<div style="flex:1 1 160px;">' +
      field("Pool share of a payout (%)", "referralPoolPct",
            d.pool_share_pct, "50") + "</div>" +
      '<div style="display:flex;align-items:flex-end;margin-bottom:8px;">' +
      '<button class="btn btn-gold btn-sm" id="referralSaveConfig">Save settings</button>' +
      "</div></div>" +
      '<div style="font-size:0.82em;color:var(--text-dim);margin-bottom:10px;">' +
      "Recorded so far: <b>" + esc(d.count || 0) + "</b> payout(s) · " +
      money(d.gross_usd) + " gross · <b>" + money(d.pool_usd) +
      "</b> credited to the pot</div>" +
      '<div style="display:flex;gap:12px;flex-wrap:wrap;">' +
      '<div style="flex:1 1 160px;">' +
      textField("Referred account (username or id)", "referralPayoutUser", "friend1") +
      "</div>" +
      '<div style="flex:1 1 120px;">' +
      field("Kraken payout (USD, gross)", "referralPayoutAmount", "", "25") + "</div>" +
      '<div style="flex:1 1 160px;">' +
      textField("Note (optional)", "referralPayoutNote", "Kraken ref id …") + "</div>" +
      '<div style="display:flex;align-items:flex-end;margin-bottom:8px;">' +
      '<button class="btn btn-gold btn-sm" id="referralRecordPayout">Record payout</button>' +
      "</div></div>" +
      '<div id="referralAdminMsg" style="font-size:0.8em;color:var(--text-mute);' +
      'margin:6px 0 10px;min-height:1em;"></div>' +
      rows(d.payouts) +
      "</div>";
  }

  function say(msg, ok) {
    var el = document.getElementById("referralAdminMsg");
    if (!el) return;
    el.style.color = ok ? "var(--emerald-bright,#3ddc97)" : "var(--text-mute)";
    el.textContent = msg;
  }

  // The re-render replaces #referralAdminMsg, so a message written before
  // load() would be thrown away by the very refresh that confirms the save.
  // Take the message here and apply it AFTER the new body is in the DOM.
  function load(msg, ok) {
    fetch("/api/admin/referral/payouts",
          { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        DATA = d;
        var card = document.getElementById(CARD_ID);
        if (card) card.innerHTML = render();
        if (msg) say(msg, ok);
      })
      .catch(function () { /* leave the last render in place */ });
  }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; })
        .then(function (d) { return { ok: r.ok, status: r.status, body: d }; });
    });
  }

  function saveConfig() {
    var rate = document.getElementById("referralXmrjRate");
    var pct = document.getElementById("referralPoolPct");
    post("/api/admin/referral/config", {
      xmrj_award: rate ? rate.value : "",
      pool_share_pct: pct ? pct.value : ""
    }).then(function (res) {
      if (!res.ok) { say(res.body.detail || "save failed", false); return; }
      var bits = [];
      if (res.body.xmrj_award !== undefined) {
        bits.push("XMRJ rate " + res.body.xmrj_award);
      }
      if (res.body.pool_share_pct !== undefined) {
        bits.push("pool share " + res.body.pool_share_pct + "%");
      }
      load("Saved — " + bits.join(" · ") + ".", true);
    }).catch(function () { say("save failed — network error", false); });
  }

  function recordPayout() {
    var who = document.getElementById("referralPayoutUser");
    var amt = document.getElementById("referralPayoutAmount");
    var note = document.getElementById("referralPayoutNote");
    var target = (who ? who.value : "").trim();
    if (!target) { say("enter the referred account", false); return; }
    var body = { amount_usd: amt ? amt.value : "", note: note ? note.value : "" };
    if (/^\d+$/.test(target)) body.user_id = parseInt(target, 10);
    else body.username = target;
    post("/api/admin/referral/payout", body).then(function (res) {
      if (!res.ok) { say(res.body.detail || "refused", false); return; }
      var msg = "Recorded " + money(res.body.gross_usd) + " — " +
          money(res.body.pool_usd) + " to the pot; " +
          (res.body.referrer_username || "the referrer") + " keeps " +
          money(res.body.referrer_keeps_usd) + " from Kraken.";
      if (amt) amt.value = "";
      if (note) note.value = "";
      load(msg, true);
    }).catch(function () { say("record failed — network error", false); });
  }

  function mount() {
    if (document.getElementById(CARD_ID)) return true;
    var page = document.getElementById("page-admin");
    if (!page) return false;
    var anchor = page.querySelector(".card");
    if (!anchor) return false;
    var wrap = document.createElement("div");
    wrap.id = CARD_ID;
    wrap.innerHTML = "loading...";
    anchor.parentNode.insertBefore(wrap, anchor);
    fetch("/api/admin/referral/payouts",
          { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { wrap.remove(); return; }
        DATA = d;
        wrap.innerHTML = render();
      })
      .catch(function () { wrap.remove(); });
    return true;
  }

  // DELEGATED, not bound per node: load() re-renders the card body, which
  // would drop any listener attached to the old buttons.
  document.addEventListener("click", function (ev) {
    var card = document.getElementById(CARD_ID);
    if (!card || !ev.target || !card.contains(ev.target)) return;
    if (ev.target.id === "referralSaveConfig") saveConfig();
    else if (ev.target.id === "referralRecordPayout") recordPayout();
  });

  var timer = setInterval(function () {
    tries += 1;
    if (mount() || tries > 120) clearInterval(timer);
  }, 1000);
})();
