/* bundle_card.js — THE DOWNLOAD CARD FILLER (Oct 23).

   The dashboard's Kraken Scout download card ships with the stable
   href (/downloads/KrakenScoutBundle.zip); this filler loads the
   build-time manifest from /api/downloads/bundle-info and paints the
   PUBLISHED version, SHA256 and size/build info, so the UI can only
   ever show the same build the .sha256 file and the Product markup on
   /download describe. Fail-soft: if the bundle is not built yet, the
   card says so instead of showing a stale hash or a stale version. */
(function () {
  "use strict";

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) { el.textContent = text; }
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
          setText("bundleVersion", "not published yet");
          linkEl.removeAttribute("href");
          linkEl.style.opacity = "0.5";
          linkEl.style.pointerEvents = "none";
          return;
        }
        setText("bundleVersion", info.version || "unavailable");
        shaEl.textContent = info.sha256;
        metaEl.textContent =
          (info.size_human || Math.round((info.bytes || 0) / 1048576) + " MB") +
          " · built " + (info.built_utc || "?") +
          " · monero " + (info.monero_head || "?") +
          " · scout " + (info.scout_head || "?");
      })
      .catch(function () {
        setText("bundleVersion", "unavailable");
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
