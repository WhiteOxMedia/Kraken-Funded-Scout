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
      (cfg.api_key.set ? " · key on file (" + esc(cfg.api_key.source || "settings") + ") " + esc(cfg.api_key.tail) : " · no key") +
      (cfg.api_secret.set ? " · secret on file (" + esc(cfg.api_secret.source || "settings") + ") " + esc(cfg.api_secret.tail) : " · no secret") +
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
      '<button id="krKeyCheck" class="btn" style="width:100%;margin-top:6px;">Check API key validity (read-only Balance)</button>' +
      '<pre id="krKeyCheckOut" style="display:none;margin-top:8px;padding:10px;' +
      'background:var(--bg-deep,#0d1117);border:1px solid var(--hairline,' +
      'rgba(255,255,255,.14));border-radius:6px;font-size:0.75em;' +
      'white-space:pre-wrap;word-break:break-all;max-height:200px;overflow:auto;"></pre>' +
      '<pre id="krTestOut" style="display:none;margin-top:8px;padding:10px;' +
      'background:var(--bg-deep,#0d1117);border:1px solid var(--hairline,' +
      'rgba(255,255,255,.14));border-radius:6px;font-size:0.75em;' +
      'white-space:pre-wrap;word-break:break-all;max-height:260px;overflow:auto;"></pre>' +
      "</div>" +
      '<div style="margin-top:12px;font-size:0.75em;color:var(--text-mute);">' +
      "Config lives in the settings table (write-only secrets). Every save " +
      "and every test is audited (kraken_referral.*). The projection worker " +
      "reads this config on its next run.</div>" +
      schedSection(cfg) +
      '<div id="krMapMount"></div>' +
      "</div>");
  }

  function schedSection(cfg) {
    var s = cfg.scheduler || {};
    var w = s.window || {};
    var alive = s.thread_alive;
    var hb = (s.heartbeat_age_s == null)
      ? "no heartbeat yet"
      : "heartbeat " + s.heartbeat_age_s + "s ago";
    var lastW = s.last_worker || {};
    var lastTxt = lastW.ts
      ? (lastW.reason === "unconfigured"
         ? "last cycle skipped: provider unconfigured"
         : "worker last ran " + Math.round((Date.now() / 1000 - lastW.ts)) + "s ago")
      : "never run";
    var winTxt = w.configured
      ? ("expires " + esc(w.expires_iso) + " (" +
         (w.expired ? "EXPIRED" : Math.round(w.seconds_remaining / 3600) + "h left") + ")")
      : "no expiry on the operator calendar";
    return (
      '<div style="margin-top:14px;padding-top:10px;' +
      'border-top:1px solid var(--hairline,rgba(255,255,255,.12));">' +
      '<div style="font-size:0.85em;font-weight:700;margin-bottom:6px;">' +
      "Scheduler — hourly worker + expiry notice</div>" +
      '<div style="font-size:0.78em;color:var(--text-mute);margin-bottom:8px;">' +
      (alive ? '<span style="color:var(--emerald-bright,#2ecc71);">&#9679; thread alive</span> '
             : '<span style="color:var(--text-mute,#9a9a9a);">&#9675; thread not running</span> ') +
      esc(hb) + " · " + esc(lastTxt) + "</div>" +
      '<label style="display:flex;gap:8px;align-items:center;margin:6px 0;' +
      'font-size:0.85em;color:var(--text-dim,#bbb);">' +
      '<input type="checkbox" id="krSchedEnabled"' +
      (s.enabled ? " checked" : "") + "> run worker hourly + fire the " +
      "expiry notice</label>" +
      field("Interval seconds (min 300)", "krSchedInterval", "number",
            "3600", String(s.interval_s || 3600)) +
      field("Referral window expiry (local time — the notice fires " +
            "once, inside the final 48h)", "krExpiry", "datetime-local", "", "") +
      '<div style="font-size:0.78em;color:var(--text-mute);margin:4px 0 8px;">' +
      winTxt + "</div>" +
      '<div style="display:flex;gap:8px;">' +
      '<button id="krSchedSave" class="btn btn-emerald" style="flex:1;">' +
      "Save scheduler + window</button>" +
      '<button id="krExpiryClear" class="btn" style="flex:0 0 auto;">' +
      "Clear expiry</button></div>" +
      '<button id="krTick" class="btn" style="width:100%;margin-top:8px;">' +
      "Run one scheduler cycle now</button>" +
      '<pre id="krSchedOut" style="display:none;margin-top:8px;padding:10px;' +
      'background:var(--bg-deep,#0d1117);border:1px solid var(--hairline,' +
      'rgba(255,255,255,.14));border-radius:6px;font-size:0.75em;' +
      'white-space:pre-wrap;word-break:break-all;max-height:220px;overflow:auto;"></pre>' +
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

    // THE KEY CHECK (Sep 26): one read-only Balance call — the verdict
    // is about the exact credential pair the projection will run under.
    document.getElementById("krKeyCheck").addEventListener("click", function () {
      var btn = this;
      var out = document.getElementById("krKeyCheckOut");
      btn.disabled = true;
      show(out, "checking the API key (one read-only Balance call) …");
      fetch("/api/admin/kraken-referral/key-check", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      }).then(function (r) { return r.json().then(function (j) {
        return { ok: r.ok, status: r.status, j: j };
      }); }).then(function (res) {
        btn.disabled = false;
        if (!res.ok) {
          show(out, "key check refused: " + (res.j.detail || "http " + res.status));
          return;
        }
        var j = res.j;
        var line = "verdict: " + j.verdict.toUpperCase()
          + (j.credentials_tier ? "  (tier: " + j.credentials_tier + ")" : "");
        if (j.verdict === "valid") {
          line += "\nassets visible: " + j.assets_visible
            + "\n" + j.detail;
        } else if (j.kraken_error) {
          line += "\nKraken said: " + j.kraken_error;
        } else if (j.detail) {
          line += "\n" + j.detail;
        }
        show(out, line);
      }).catch(function (e) {
        show(out, "request failed: " + e);
        btn.disabled = false;
      });
    });
  }

  function wireSched() {
    var out = document.getElementById("krSchedOut");
    document.getElementById("krSchedSave").addEventListener("click", function () {
      var btn = this;
      btn.disabled = true;
      var body = {
        scheduler_enabled: document.getElementById("krSchedEnabled").checked,
        interval_s: parseInt(document.getElementById("krSchedInterval").value, 10) || 3600
      };
      var lv = document.getElementById("krExpiry").value;
      if (lv) { body.expires_at = new Date(lv).getTime() / 1000; }
      fetch("/api/admin/kraken-referral/config", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) { return r.json().then(function (j) {
        return { ok: r.ok, j: j };
      }); }).then(function (res) {
        btn.disabled = false;
        show(out, res.ok ? "saved: " + (res.j.updated || []).join(", ")
                         : { error: res.j.detail || "save failed" });
        if (res.ok) { setTimeout(function () { location.reload(); }, 900); }
      }).catch(function (e) { btn.disabled = false; show(out, "request failed: " + e); });
    });
    document.getElementById("krExpiryClear").addEventListener("click", function () {
      fetch("/api/admin/kraken-referral/config", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clear_window: true })
      }).then(function (r) { return r.json(); })
        .then(function (j) { show(out, j); setTimeout(function () { location.reload(); }, 900); });
    });
    document.getElementById("krTick").addEventListener("click", function () {
      var btn = this;
      btn.disabled = true;
      show(out, "running one scheduler cycle …");
      fetch("/api/admin/scheduler/tick", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: "{}"
      }).then(function (r) { return r.json(); })
        .then(function (j) { show(out, j); btn.disabled = false; })
        .catch(function (e) { show(out, "request failed: " + e); btn.disabled = false; });
    });
  }

  function mapSection(data) {
    return (
      '<div style="margin-top:14px;padding-top:10px;' +
      'border-top:1px solid var(--hairline,rgba(255,255,255,.12));">' +
      '<div style="font-size:0.85em;font-weight:700;margin-bottom:6px;">' +
      "User mapping — attach real Kraken referral ids</div>" +
      '<div style="font-size:0.78em;color:var(--text-mute);margin-bottom:8px;">' +
      data.mapped + " of " + data.total + " accounts mapped — the worker " +
      "fetches only mapped accounts; the site\u2019s own invite codes are " +
      "never sent to Kraken.</div>" +
      '<select id="krMapUser" style="width:100%;padding:7px 9px;margin-bottom:8px;' +
      'background:var(--bg-deep,#0d1117);border:1px solid var(--hairline,' +
      'rgba(255,255,255,.14));border-radius:6px;color:var(--text,#eee);">' +
      data.users.map(function (u) {
        return '<option value="' + u.id + '" data-rid="' + esc(u.kraken_referral_id) + '">' +
          u.id + " \u2014 " + esc(u.username) +
          (u.kraken_referral_id ? " \u00b7 " + esc(u.kraken_referral_id)
                                : " \u00b7 (unmapped)") +
          (u.email_masked ? " \u00b7 " + esc(u.email_masked) : "") +
          "</option>";
      }).join("") +
      "</select>" +
      field("Kraken referral id for the selected account", "krMapRid", "text",
            "paste the id the operator source returns") +
      '<div style="display:flex;gap:8px;">' +
      '<button id="krMapSave" class="btn btn-emerald" style="flex:1;">' +
      "Save mapping</button>" +
      '<button id="krMapClear" class="btn" style="flex:0 0 auto;">' +
      "Clear</button></div>" +
      '<pre id="krMapOut" style="display:none;margin-top:8px;padding:10px;' +
      'background:var(--bg-deep,#0d1117);border:1px solid var(--hairline,' +
      'rgba(255,255,255,.14));border-radius:6px;font-size:0.75em;' +
      'white-space:pre-wrap;word-break:break-all;max-height:200px;overflow:auto;"></pre>' +
      "</div>");
  }

  function wireMap(data) {
    var sel = document.getElementById("krMapUser");
    var rid = document.getElementById("krMapRid");
    var out = document.getElementById("krMapOut");
    var count = sel.parentNode.querySelector("div[style*='margin-bottom:8px']");
    function sync() {
      var opt = sel.options[sel.selectedIndex];
      rid.value = opt ? (opt.getAttribute("data-rid") || "") : "";
    }
    sel.addEventListener("change", sync);
    sync();
    function post(body, btn) {
      btn.disabled = true;
      fetch("/api/admin/kraken-referral/mappings", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) { return r.json().then(function (j) {
        return { ok: r.ok, j: j };
      }); }).then(function (res) {
        btn.disabled = false;
        show(out, res.ok ? res.j : { error: res.j.detail || "failed" });
        if (res.ok) {
          var opt = sel.options[sel.selectedIndex];
          var v = res.j.kraken_referral_id || "";
          opt.setAttribute("data-rid", v);
          opt.text = opt.text.replace(/ \u00b7 (\(unmapped\)|[^\u00b7]+)$/, "") +
            (v ? " \u00b7 " + v : " \u00b7 (unmapped)");
          var mapped = 0;
          for (var i = 0; i < sel.options.length; i++) {
            if (sel.options[i].getAttribute("data-rid")) { mapped += 1; }
          }
          if (count) {
            count.textContent = mapped + " of " + data.total +
              " accounts mapped — the worker fetches only mapped accounts; " +
              "the site\u2019s own invite codes are never sent to Kraken.";
          }
          sync();
        }
      }).catch(function (e) { btn.disabled = false; show(out, "request failed: " + e); });
    }
    document.getElementById("krMapSave").addEventListener("click", function () {
      post({ user_id: parseInt(sel.value, 10),
             kraken_referral_id: rid.value }, this);
    });
    document.getElementById("krMapClear").addEventListener("click", function () {
      post({ user_id: parseInt(sel.value, 10), kraken_referral_id: "" }, this);
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
        wireSched();
        fetch("/api/admin/kraken-referral/mappings", { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (data) {
            if (!data) { return; }
            document.getElementById("krMapMount").innerHTML = mapSection(data);
            wireMap(data);
          })
          .catch(function () { /* mapping sheet optional */ });
      })
      .catch(function () { wrap.remove(); });
    return true;
  }

  var timer = setInterval(function () {
    tries += 1;
    if (mount() || tries > 120) { clearInterval(timer); }
  }, 1000);
})();
