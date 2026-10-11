/* bundle_card.js — THE DOWNLOAD CARD FILLER (Sep 26).

   The dashboard's Kraken Scout download card ships with the stable
   href (/downloads/KrakenScoutBundle.zip); this filler loads the
   build-time manifest from /api/downloads/bundle-info and paints the
   PUBLISHED SHA256 + size/build info, so the UI can only ever show
   the same checksum the .sha256 file publishes. Fail-soft: if the
   bundle is not built yet, the card says so instead of showing a
   stale hash. */
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function mount() {
    var shaEl = document.getElementById("bundleSha");
    var metaEl = document.getElementById("bundleMeta");
    var linkEl = document.getElementById("bundleLink");
    if (!shaEl || !metaEl) { return false; }

    fetch("/api/downloads/bundle-info", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (info) {
        if (!info) {
          shaEl.textContent = "bundle not published yet — check back shortly";
          linkEl.removeAttribute("href");
          linkEl.style.opacity = "0.5";
          linkEl.style.pointerEvents = "none";
          return;
        }
        shaEl.textContent = info.sha256;
        metaEl.textContent =
          (info.size_human || Math.round((info.bytes || 0) / 1048576) + " MB") +
          " · built " + (info.built_utc || "?") +
          " · monero " + (info.monero_head || "?") +
          " · scout " + (info.scout_head || "?");
      })
      .catch(function () {
        shaEl.textContent = "checksum unavailable — verify later at /downloads/KrakenScoutBundle.zip.sha256";
      });
    return true;
  }

  if (!mount()) {
    var tries = 0;
    var timer = setInterval(function () {
      tries += 1;
      if (mount() || tries > 60) { clearInterval(timer); }
    }, 1000);
  }
})();
