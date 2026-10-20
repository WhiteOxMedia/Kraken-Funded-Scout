// Brand: ONERIVER / KRAKEN — helper: replace bg marker tokens in wf_dashboard.js.new values
// Replaces `bg-line` and `bg-brand` marker tokens with real branded CSS values when they
// are set (via `brand.attr`), so the rendered wf-dashboard surfaces carry single-source brand.
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"'"]/g, function (c) {
      return { "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c];
    });
  }
  function fillMarkers(rootEl) {
    var el = rootEl || document;
    if (!el) return;
    var open = el.querySelectorAll ? el.querySelectorAll("[data-fill-marker]") : [];
    for (var i = 0; i < open.length; i++) {
      var node = open[i];
      var v = node.getAttribute("data-fill-marker");
      var val = "";
      if (v === "bg-line") {
        val = brand ? (brand.attr("bg-line") || "") : "";
      } else if (v === "bg-brand") {
        val = brand ? (brand.attr("bg-brand") || "") : "";
      }
      node.style.background = val;
      node.removeAttribute("data-fill-marker");
    }
  }
  function watch(evt) {
    if (!evt || !evt.detail) return;
    if (evt.detail.marker) {
      fillMarkers(evt.detail.marker);
    }
  }
  document.addEventListener("brand:attr-changed", watch);
  if (window.brand && window.brand.attr) {
    fillMarkers();
  }
})();
