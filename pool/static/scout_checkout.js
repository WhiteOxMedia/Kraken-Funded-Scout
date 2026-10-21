/* scout_checkout.js — the Scout Pro donation checkout surface.
   Owns #donate-modal + #donate-status so scout_paywall's openDonateFlow
   delegates here via window.openDonateFlowExternal. Creates/reuses an
   order on /scout/purchase, shows the address and exact amount, then
   polls the order until the backend sees the on-chain payment. */
(function () {
  "use strict";

  var modal = null;
  var pollTimer = null;
  var expiryTimer = null;
  var current = null;

  function $(id) { return document.getElementById(id); }

  function money(v) {
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return (Math.round(n * 1e8) / 1e8).toString();
  }

  function when(ts) {
    try {
      return new Date(Number(ts) * 1000).toLocaleString();
    } catch (e) { return ""; }
  }

  function setStatus(text) {
    var el = $("donate-status");
    if (el) el.textContent = text;
  }

  function note(text) {
    var el = $("wfScoutPurchaseBlocker");
    if (el) el.textContent = text;
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (expiryTimer) { clearInterval(expiryTimer); expiryTimer = null; }
  }

  function closeModal() {
    stopPolling();
    if (modal) modal.style.display = "none";
  }

  function buildModal() {
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "donate-modal";
    modal.style.cssText = "display:none;position:fixed;inset:0;z-index:9999;" +
      "background:rgba(0,0,0,0.62);align-items:center;justify-content:center;padding:16px;";
    modal.innerHTML =
      '<div style="max-width:430px;width:100%;background:#fff;color:#1c1c1e;' +
      'border-radius:12px;padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.35);">' +
      '<h3 style="margin:0 0 6px;font-size:1.1em;">Donate &amp; unlock Scout Pro</h3>' +
      '<p id="donate-status" style="margin:8px 0;font-size:.9em;color:#555;">Starting checkout…</p>' +
      '<div id="donate-details" style="display:none;margin-top:10px;">' +
      '<div style="font-size:.78em;color:#777;text-transform:uppercase;letter-spacing:.04em;">Send exactly</div>' +
      '<div id="donate-amount" style="font-size:1.5em;font-weight:700;margin:2px 0 10px;"></div>' +
      '<div style="font-size:.78em;color:#777;text-transform:uppercase;letter-spacing:.04em;">To this address</div>' +
      '<code id="donate-address" style="display:block;word-break:break-all;background:#f4f4f5;' +
      'padding:8px;border-radius:6px;font-size:.78em;margin:4px 0 10px;"></code>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
      '<button id="btn-donate-copy" type="button" style="cursor:pointer;">Copy address</button>' +
      '<a id="donate-pay-link" href="#" style="padding:2px 8px;border:1px solid #bbb;' +
      'border-radius:4px;text-decoration:none;color:#1c1c1e;font-size:.85em;">Open in wallet</a>' +
      '</div>' +
      '<p id="donate-expiry" style="margin:10px 0 0;font-size:.75em;color:#888;"></p>' +
      '</div>' +
      '<div style="display:flex;justify-content:flex-end;margin-top:16px;">' +
      '<button id="btn-donate-close" type="button" style="cursor:pointer;">Close</button>' +
      '</div></div>';
    document.body.appendChild(modal);

    $("btn-donate-close").addEventListener("click", closeModal);
    modal.addEventListener("click", function (e) {
      if (e.target === modal) closeModal();
    });
    $("btn-donate-copy").addEventListener("click", function () {
      var text = current && current.address ? current.address : "";
      if (!text) return;
      try {
        navigator.clipboard.writeText(text).then(function () {
          setStatus("Address copied. Send the exact amount shown above.");
        }, function () {});
      } catch (e) {}
    });
    return modal;
  }

  function showExpiry() {
    if (!current) return;
    var el = $("donate-expiry");
    if (!el) return;
    function tick() {
      var left = Math.max(0, Math.round(Number(current.expires_at) - Date.now() / 1000));
      if (left <= 0) {
        el.textContent = "Checkout expired — click Donate again for a fresh address.";
        return;
      }
      var m = Math.floor(left / 60);
      var s = left % 60;
      el.textContent = "Expires in " + m + "m " + (s < 10 ? "0" : "") + s + "s";
    }
    tick();
    if (expiryTimer) clearInterval(expiryTimer);
    expiryTimer = setInterval(tick, 1000);
  }

  function renderOrder(order) {
    current = order;
    var details = $("donate-details");
    if (details) details.style.display = "block";
    $("donate-amount").textContent = money(order.amount_xmr) + " XMR";
    $("donate-address").textContent = order.address || "";
    var link = $("donate-pay-link");
    if (link) link.href = order.checkout_url || ("monero:" + (order.address || ""));
    setStatus("Waiting for your payment — this window checks the chain automatically.");
    showExpiry();
  }

  function succeeded(order) {
    stopPolling();
    var details = $("donate-details");
    if (details) details.style.display = "none";
    var until = order.scout_pro && order.scout_pro.until;
    setStatus("Payment confirmed — Scout Pro unlocked" +
      (until ? " until " + when(until) + "." : "."));
    note("Scout Pro unlocked" + (until ? " until " + when(until) + "." : "."));
  }

  function pollStatus(orderId) {
    return fetch("/scout/purchase", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "status", order_id: orderId })
    }).then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (d) {
        if (!d || d.status === "error") return null;
        if (d.status === "paid") succeeded(d);
        else if (d.status === "expired") {
          stopPolling();
          setStatus("Checkout expired — click Donate again for a fresh address.");
        }
        return d;
      })
      .catch(function () { return null; });
  }

  function startPolling(orderId) {
    stopPolling();
    pollTimer = setInterval(function () { pollStatus(orderId); }, 5000);
  }

  function openCheckout() {
    // Every alert entry point goes through the unified router; the router
    // enforces the Scout Pro gate, so a locked visitor gets the checkout
    // shell and no notification.
    if (typeof routeAlert === "function") { routeAlert("BUY_NOW"); }
    buildModal();
    if (modal) { modal.style.display = "flex"; }
    stopPolling();
    current = null;
    var details = $("donate-details");
    if (details) details.style.display = "none";
    setStatus("Opening checkout…");

    fetch("/scout/purchase", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "donate_unlock",
        identity_wallet: window.identityWallet || ""
      })
    }).then(function (r) {
      return r.json().catch(function () { return null; })
        .then(function (d) { return { ok: r.ok, data: d }; });
    }).then(function (res) {
      var d = res.data;
      if (!res.ok || !d || d.status === "error") {
        setStatus((d && d.message) || "Checkout is unavailable right now — nothing was charged.");
        return;
      }
      renderOrder(d);
      startPolling(d.order_id);
      pollStatus(d.order_id);
    }).catch(function () {
      setStatus("Checkout is unavailable right now — nothing was charged.");
    });
  }

  /* Download CTA entry point. Uses the account's saved wallet when one is
     already known (bound installer), otherwise the unbound installer. Keeping
     the URL decision here means every Download Miner button calls the same
     real function instead of guessing a route or a missing global. */
  function minerInstallerUrl() {
    var wallet = window.identityWallet || "";
    return wallet
      ? "/api/miner-installer/" + encodeURIComponent(wallet)
      : "/api/miner-download";
  }

  function openMinerDownload() {
    var url = minerInstallerUrl();
    try { window.open(url, "_blank", "noopener"); } catch (e) {}
    return url;
  }

  window.openDonateFlowExternal = openCheckout;
  window.openMinerDownload = openMinerDownload;
  window.ScoutCheckout = {
    open: openCheckout,
    close: closeModal,
    openMinerDownload: openMinerDownload
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildModal);
  } else {
    buildModal();
  }
})();
