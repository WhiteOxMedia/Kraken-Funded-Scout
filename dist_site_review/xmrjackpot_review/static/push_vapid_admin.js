/* push_vapid_admin.js — THE VAPID PROVISIONING CARD (Sep 26).

   Admin-panel card for the web-push signing keys: without a VAPID
   pair the sender early-returns "VAPID key not provisioned" and NO
   phone ever receives anything. This card closes that gap:

     - Ensure: generate the P-256 keypair (py_vapid) when none exists
     - Rotate: deliberate re-key (invalidates subscriptions — the
       response says how many devices must re-enable)
     - Test: one REAL push (event push_test) to the calling admin's
       own subscribed devices — the end of the wire a phone sees

   Non-admins: 401 -> the card removes itself. */
(function () {
  "use strict";

  var CARD_ID = "pushVapidCard";
  var tries = 0;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function render(st) {
    var badge = st.provisioned
      ? '<span style="color:var(--emerald-bright,#2ecc71);font-weight:700;">&#9679; VAPID keys provisioned</span>'
      : '<span style="color:#c9a227;font-weight:700;">&#9678; NOT provisioned — phones cannot receive pushes</span>';
    return (
      '<div class="card" style="margin-bottom:24px;">' +
      '<div class="card-header">Web Push (VAPID) — Delivery Keys</div>' +
      '<div style="font-size:0.8em;color:var(--text-mute);margin-bottom:10px;">' +
      badge +
      (st.provisioned
        ? " · source " + esc(st.source) + " · public key " + esc(st.public_key_tail)
        : "") +
      "</div>" +
      '<div style="font-size:0.78em;color:var(--text-mute);margin-bottom:10px;">' +
      "The public key is what browsers receive as applicationServerKey; " +
      "the private key signs every notification (stored write-only, " +
      "tails only here). Rotate only as part of a re-subscribe " +
      "campaign — old subscriptions stop working.</div>" +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
      '<button id="pvEnsure" class="btn btn-emerald" style="flex:1;">' +
      (st.provisioned ? "Re-check keys" : "Generate VAPID keys") + "</button>" +
      '<button id="pvTest" class="btn" style="flex:1;">Send test push to my devices</button>' +
      '<button id="pvRotate" class="btn" style="flex:1;">Rotate keys…</button>' +
      "</div>" +
      '<pre id="pvOut" style="display:none;margin-top:10px;padding:10px;' +
      'background:var(--bg-deep,#0d1117);border:1px solid var(--hairline,' +
      'rgba(255,255,255,.14));border-radius:6px;font-size:0.75em;' +
      'white-space:pre-wrap;word-break:break-all;max-height:240px;overflow:auto;"></pre>' +
      '<div style="margin-top:10px;font-size:0.75em;color:var(--text-mute);">' +
      "Test delivers through the same sender (rate limits, opt-outs, " +
      "audits) a real notification uses — it lands on YOUR subscribed " +
      "devices only. Every action is audited.</div>" +
      "</div>");
  }

  function act(btn, out, body) {
    btn.disabled = true;
    show(out, "…");
    fetch("/api/admin/push/vapid", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); })
      .then(function (j) { show(out, j); btn.disabled = false; })
      .catch(function (e) { show(out, "request failed: " + e); btn.disabled = false; });
  }

  function show(out, obj) {
    out.style.display = "block";
    out.textContent = typeof obj === "string"
      ? obj : JSON.stringify(obj, null, 2);
  }

  function wire() {
    var out = document.getElementById("pvOut");
    document.getElementById("pvEnsure").addEventListener("click", function () {
      act(this, out, { action: "ensure" });
    });
    document.getElementById("pvTest").addEventListener("click", function () {
      act(this, out, { action: "test" });
    });
    document.getElementById("pvRotate").addEventListener("click", function () {
      if (!window.confirm("Rotate the VAPID keys? Every existing push " +
                          "subscription stops working until each device " +
                          "re-enables notifications.")) { return; }
      act(this, out, { action: "rotate" });
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
    wrap.innerHTML = "loading push key status…";
    anchor.parentNode.insertBefore(wrap, anchor);
    fetch("/api/admin/push/vapid", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (st) {
        if (!st) { wrap.remove(); return; }   // 401/403: not an admin
        wrap.innerHTML = render(st);
        wire();
      })
      .catch(function () { wrap.remove(); });
    return true;
  }

  var timer = setInterval(function () {
    tries += 1;
    if (mount() || tries > 120) { clearInterval(timer); }
  }, 1000);
})();
