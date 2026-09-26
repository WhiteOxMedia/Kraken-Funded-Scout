/* kraken_referral_admin.js — THE KRAKEN REFERRAL CONFIG CARD (Sep 26).

   Injects one admin-panel card (runtime injection, same pattern as
   admin_cold_wallet.js) for provisioning the referral provider:

     - KRAKEN_API_KEY / KRAKEN_API_SECRET (write-only; blanks keep
       the stored values; the API only ever echoes masked tails)
     - KRAKEN_REFERRAL_ENDPOINT
     - simulate-mode toggle (clearly-labeled fixture provider)
     - THE CONNECTION TEST: one live fetch for a referral id the
       operator types, showing the RAW provider response + the
       normalized interpretation + a failure-mode hint

   Non-admins get a 403 from the API and the card simply removes
   itself — no content, no error noise. */
(function () {
  "use strict";

  var CARD_ID = "krakenReferralCard";
  var tries = 0;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function field(label, id, type, ph, value) {
    return '<div style="margin-bottom:8px;">' +
      '<label style="display:block;color:var(--text-mute);font-size:0.8em;' +
      'margin-bottom:2px;">' + label + "</label>" +
      '<input id="' + id + '" type="' + type + '" placeholder="' + esc(ph) +
      '" value="' + esc(value || "") + '" style="width:100%;padding:7px 9px;' +
      'background:var(--bg-deep,#0d1117);border:1px solid var(--hairline,' +
      'rgba(255,255,255,.14));border-radius:6px;color:var(--text,#eee);">' +
      "</div>";
  }

  function render(cfg) {
    var modeBadge = {
      live: '<span style="color:var(--emerald-bright,#2ecc71);font-weight:700;">&#9679; LIVE provider</span>',
      simulate: '<span style="color:#c9a227;font-weight:700;">&#9678; SIMULATE mode (fixture data, labeled kraken_referral_sim_v1)</span>',
      unconfigured: '<span style="color:var(--text-mute,#9a9a9a);">&#9675; not configured — live fetches are refused</span>'
    }[cfg.mode] || esc(cfg.mode);
    return (
      '<div class="card" style="margin-bottom:24px;">' +
      '<div class="card-header">Kraken Referral Projection — Provider Config</div>' +
      '<div style="font-size:0.8em;color:var(--text-mute);margin-bottom:10px;">' +
      modeBadge +
      (cfg.api_key.set ? " · key on file " + esc(cfg.api_key.tail) : " · no key") +
      (cfg.api_secret.set ? " · secret on file " + esc(cfg.api_secret.tail) : " · no secret") +
      "</div>" +
      field("Kraken API key (blank = keep stored)", "krApiKey", "password",
            cfg.api_key.set ? "stored …" + cfg.api_key.tail : "paste API key") +
      field("Kraken API secret (blank = keep stored)", "krApiSecret", "password",
            cfg.api_secret.set ? "stored …" + cfg.api_secret.tail : "paste API secret") +
      field("Referral status endpoint", "krEndpoint", "text",
            "https://… (POST, API-Key/API-Sign headers)", cfg.endpoint) +
      '<label style="display:flex;gap:8px;align-items:center;margin:6px 0 12px;' +
      'font-size:0.85em;color:var(--text-dim,#bbb);">' +
      '<input type="checkbox" id="krSimulate"' +
      (cfg.simulate_flag ? " checked" : "") + "> simulate mode (deterministic " +
      "fixtures — never presented as live Kraken data)</label>" +
      '<button id="krSave" class="btn btn-emerald" style="width:100%;">' +
      "Save provider config</button>" +
      '<div style="margin-top:14px;padding-top:10px;' +
      'border-top:1px solid var(--hairline,rgba(255,255,255,.12));">' +
      '<div style="font-size:0.85em;font-weight:700;margin-bottom:6px;">' +
      "Connection test — one live fetch, raw response shown</div>" +
      field("referral id to probe", "krTestId", "text", "e.g. the referral code on file") +
      '<button id="krTest" class="btn" style="width:100%;">Test connection</button>' +
      '<pre id="krTestOut" style="display:none;margin-top:8px;padding:10px;' +
      'background:var(--bg-deep,#0d1117);border:1px solid var(--hairline,' +
      'rgba(255,255,255,.14));border-radius:6px;font-size:0.75em;' +
      'white-space:pre-wrap;word-break:break-all;max-height:260px;overflow:auto;"></pre>' +
      "</div>" +
      '<div style="margin-top:12px;font-size:0.75em;color:var(--text-mute);">' +
      "Config lives in the settings table (write-only secrets). Every save " +
      "and every test is audited (kraken_referral.*). The projection worker " +
      "reads this config on its next run.</div>" +
      "</div>");
  }

  function show(out, obj) {
    out.style.display = "block";
    out.textContent = typeof obj === "string"
      ? obj : JSON.stringify(obj, null, 2);
  }

  function wire(cfg) {
    document.getElementById("krSave").addEventListener("click", function () {
      var btn = this;
      btn.disabled = true;
      fetch("/api/admin/kraken-referral/config", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_key: document.getElementById("krApiKey").value,
          api_secret: document.getElementById("krApiSecret").value,
          endpoint: document.getElementById("krEndpoint").value,
          simulate: document.getElementById("krSimulate").checked
        })
      }).then(function (r) { return r.json().then(function (j) {
        return { ok: r.ok, j: j };
      }); }).then(function (res) {
        btn.disabled = false;
        var note = res.ok
          ? "saved: " + (res.j.updated || []).join(", ") +
            " — mode is now " + res.j.mode + " (reloading…)"
          : "save failed: " + (res.j.detail || "unknown");
        alert(note);
        if (res.ok) { setTimeout(function () { location.reload(); }, 900); }
      }).catch(function () { btn.disabled = false; });
    });

    document.getElementById("krTest").addEventListener("click", function () {
      var btn = this;
      var out = document.getElementById("krTestOut");
      var rid = document.getElementById("krTestId").value.trim();
      if (!rid) { show(out, "type a referral id to probe first"); return; }
      btn.disabled = true;
      show(out, "probing " + rid + " …");
      fetch("/api/admin/kraken-referral/test", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ referral_id: rid })
      }).then(function (r) { return r.json(); })
        .then(function (j) { show(out, j); btn.disabled = false; })
        .catch(function (e) {
          show(out, "request failed: " + e);
          btn.disabled = false;
        });
    });
  }

  function mount() {
    if (document.getElementById(CARD_ID)) { return true; }
    var page = document.getElementById("page-admin");
    if (!page) { return false; }
    var anchor = page.querySelector(".card");
    if (!anchor) { return false; }
    var wrap = document.createElement("div");
    wrap.id = CARD_ID;
    wrap.innerHTML = "loading referral config…";
    anchor.parentNode.insertBefore(wrap, anchor);
    fetch("/api/admin/kraken-referral/config", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (cfg) {
        if (!cfg) { wrap.remove(); return; }   // 401/403: not an admin
        wrap.innerHTML = render(cfg);
        wire(cfg);
      })
      .catch(function () { wrap.remove(); });
    return true;
  }

  var timer = setInterval(function () {
    tries += 1;
    if (mount() || tries > 120) { clearInterval(timer); }
  }, 1000);
})();
