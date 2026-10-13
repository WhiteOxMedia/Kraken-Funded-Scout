// refblock.js (Oct 12) — two jobs for the hero referral block:
//   1. fills its 'rewards claimed today' counter from the pool's OWN
//      recorded funding-ledger data (/api/kraken/rewards-today).
//      Never scraped from Kraken; a failed fetch leaves the neutral
//      placeholder and never blocks the page.
//   2. upgrades the share row's four anchors to native share intents,
//      built from the tracked invite link ON THE PAGE (#refBlockLink)
//      so that element is the single source of truth for the URL.
//      Until the upgrade lands, every anchor falls back to the tracked
//      link itself — a click still reaches Kraken, still counted.
(function () {
  var el = document.getElementById('refBlockClaimed');
  if (!el) return;
  fetch('/api/kraken/rewards-today', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || typeof d.rewards_claimed !== 'number') return;
      var n = d.rewards_claimed;
      if (n === 0) {
        el.textContent = '0 rewards claimed today — yours could be the first';
      } else {
        var usd = (typeof d.total_usd === 'number' ? d.total_usd : 0).toFixed(2);
        el.textContent = n + ' reward' + (n === 1 ? '' : 's') +
          ' claimed today — $' + usd + ' into the jackpot in the last 24h';
      }
    })
    .catch(function () { /* leave the placeholder line */ });

  try {
    var linkEl = document.getElementById('refBlockLink');
    if (!linkEl) return;
    var u = encodeURIComponent(linkEl.href);
    var msg = encodeURIComponent('Get up to $300 on Kraken with this invite link — ' + linkEl.href);
    var upg = {
      refShareFb: 'https://www.facebook.com/sharer/sharer.php?u=' + u + '&quote=' + msg,
      refShareWa: 'https://wa.me/?text=' + msg,
      refShareTg: 'https://t.me/share/url?url=' + u + '&text=' + msg,
      refShareX: 'https://twitter.com/intent/tweet?url=' + u + '&text=' + msg
    };
    for (var id in upg) {
      if (Object.prototype.hasOwnProperty.call(upg, id)) {
        var a = document.getElementById(id);
        if (a) { a.href = upg[id]; }
      }
    }
  } catch (e) { /* share rows keep their tracked-link fallback */ }
})();
