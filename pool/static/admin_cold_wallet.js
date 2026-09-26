/* admin_cold_wallet.js - THE OPERATOR'S COLD-WALLET CARD (Sep 25 VI)

   Renders /api/admin/cold-wallet as one card on the Admin page,
   injected at runtime so the admin panel HTML itself stays untouched
   (it carries another thread's uncommitted work). Admin-gated by the
   same session the rest of the dashboard uses; the card simply fails
   quiet for non-admins (the API answers 403 and the card never
   appears - no content, no error noise).

   Money picture, per the user's order:
     - Pool wallet balance   (monero-wallet-rpc, live)
     - Kraken total          (house-tax payouts SENT to Kraken)
     - House fees collected  (accumulated + lifetime sent)
*/
(function () {
  "use strict";

  var CARD_ID = "coldWalletCard";
  var tries = 0;

  function fmt(xmr) {
    var n = Number(xmr || 0);
    return n.toFixed(6) + " XMR";
  }

  function row(label, value) {
    return '<div style="display:flex;justify-content:space-between;' +
      'gap:12px;padding:4px 0;border-bottom:1px solid var(--hairline);">' +
      '<span style="color:var(--text-mute);font-size:0.85em;">' + label +
      '</span><span style="font-weight:700;color:var(--text);">' + value +
      "</span></div>";
  }

  function render(d) {
    var state = d.rpc_available
      ? '<span style="color:var(--emerald-bright);font-weight:700;">&#9679; wallet RPC live</span>'
      : '<span style="color:var(--text-mute);">&#9675; wallet RPC offline - ledger totals only</span>';
    var krakenLine = fmt(d.kraken_total_sent);
    if (d.kraken_pending > 0) {
      krakenLine += ' <span style="color:var(--text-mute);font-weight:400;">(+' +
        fmt(d.kraken_pending) + ' pending)</span>';
    }
    if (d.kraken_onchain_total !== null && d.kraken_onchain_total !== undefined) {
      krakenLine += '<div style="font-size:0.75em;color:var(--text-mute);font-weight:400;">' +
        "on-chain to Kraken address: " + fmt(d.kraken_onchain_total) + "</div>";
    }
    var html =
      '<div class="card" style="margin-bottom:24px;">' +
      '<div class="card-header">Cold Wallet - Operator View</div>' +
      '<div style="font-size:0.8em;color:var(--text-mute);margin-bottom:8px;">' +
      state + " - wallet " + (d.wallet_address_masked || "?") + "</div>" +
      row("Pool wallet balance", fmt(d.wallet_balance)) +
      row("Wallet unlocked", fmt(d.wallet_unlocked)) +
      row("Kraken total (house-tax sent)", krakenLine) +
      row("House fees accumulated", fmt(d.house_fees_accumulated)) +
      row("House fees collected - total", fmt(d.house_fees_collected_total)) +
      "</div>";
    return html;
  }

  function mount() {
    if (document.getElementById(CARD_ID)) return true;
    var page = document.getElementById("page-admin");
    if (!page) return false;
    // the panel content is injected by app.js after the auth check;
    // wait until it has actually rendered, then lead with our card
    var anchor = page.querySelector(".card");
    if (!anchor) return false;
    var wrap = document.createElement("div");
    wrap.id = CARD_ID;
    wrap.innerHTML = "loading...";
    anchor.parentNode.insertBefore(wrap, anchor);
    fetch("/api/admin/cold-wallet", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { wrap.remove(); return; }
        wrap.innerHTML = render(d);
      })
      .catch(function () { wrap.remove(); });
    return true;
  }

  // the admin page renders after login; poll briefly for it
  var timer = setInterval(function () {
    tries += 1;
    if (mount() || tries > 120) clearInterval(timer);
  }, 1000);
})();
