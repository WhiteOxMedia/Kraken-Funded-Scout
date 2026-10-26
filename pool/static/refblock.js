// refblock.js (Oct 12, trimmed Oct 24, reframed Oct 25) — one job for the
// hero partner block: fills its recorded-bonus counter from the pool's
// OWN funding-ledger data (/api/kraken/rewards-today). Never scraped
// from Kraken; a failed fetch leaves the neutral placeholder and never
// blocks the page. The block is a partner offer, so the line says what
// the ledger holds — bonuses the operator recorded — not what Kraken
// promises.
//
// The invite link and code are no longer share targets — the operator
// removed the four "Share on …" buttons and put each value behind its
// own <details> button, which needs no script. This file used to build
// native share intents from #refBlockLink; that block is gone with the
// buttons, so the panel's only network call is the counter below.
(function () {
  var el = document.getElementById('refBlockClaimed');
  if (!el) return;
  fetch('/api/kraken/rewards-today', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || typeof d.rewards_claimed !== 'number') return;
      var n = d.rewards_claimed;
      if (n === 0) {
        el.textContent = '0 partner bonuses recorded into the jackpot today — none paid out yet';
      } else {
        var usd = (typeof d.total_usd === 'number' ? d.total_usd : 0).toFixed(2);
        el.textContent = n + ' partner bonus' + (n === 1 ? '' : 'es') +
          ' recorded today — $' + usd + ' credited into the jackpot (last 24h)';
      }
    })
    .catch(function () { /* leave the placeholder line */ });
})();
