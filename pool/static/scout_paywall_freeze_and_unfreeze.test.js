// pool/static/scout_paywall_freeze_and_unfreeze.test.js
// Run against a real DOM runtime that can stub localStorage and window.
// This file is meant for a small Node harness (or a browser console snippet).

(function () {
  "use strict";

  var passed = 0;
  var failed = 0;

  function assert(condition, message) {
    if (condition) {
      console.log("PASS:", message);
      passed += 1;
    } else {
      console.error("FAIL:", message);
      failed += 1;
    }
  }

  function assertEqual(a, b, message) {
    var ok = a === b;
    if (ok) {
      console.log("PASS:", message);
      passed += 1;
    } else {
      console.error("FAIL:", message, "expected", b, "got", a);
      failed += 1;
    }
  }

  // --- fake DOM ---------------------------------------------------------
  var ids = {};
  var attrs = {};
  var classes = {};
  var hidden = {};
  var disabled = {};
  var ariaDisabled = {};
  var style = {};
  var freezeMsgs = {};
  // one element instance per id: a browser hands back the SAME node every
  // time, so property writes (disabled, style, textContent) must be visible
  // to the assertions that read the element again afterwards.
  var elCache = {};
  var detachedParent = { insertBefore: function () {}, appendChild: function () {} };

  // the contract marks the ROOT element with its storage class; a browser
  // always has document.documentElement, so the harness supplies one rather
  // than making the shipped module defend against an impossible DOM.
  var rootClasses = [];
  var rootClassList = {
    add: function (c) { if (rootClasses.indexOf(c) === -1) rootClasses.push(c); },
    remove: function (c) {
      rootClasses = rootClasses.filter(function (x) { return x !== c; });
    },
    toggle: function (c, on) {
      var want = on === undefined ? rootClasses.indexOf(c) === -1 : !!on;
      if (want && rootClasses.indexOf(c) === -1) rootClasses.push(c);
      if (!want) rootClasses = rootClasses.filter(function (x) { return x !== c; });
      return want;
    },
    contains: function (c) { return rootClasses.indexOf(c) !== -1; },
    toString: function () { return rootClasses.join(" "); }
  };

  global.document = {
    documentElement: { className: "", classList: rootClassList },
    addEventListener: function (evt, fn) {
      this["_listeners_" + evt] = this["_listeners_" + evt] || [];
      this["_listeners_" + evt].push(fn);
    },
    getElementById: function (id) {
      if (!ids[id]) return null;
      if (elCache[id]) return elCache[id];
      var base = {
        id: id,
        parentNode: detachedParent,
        className: classes[id] || "",
        textContent: ids[id].text || "",
        innerHTML: ids[id].html || "",
        style: style[id] || {},
        classList: {
          _c: classes[id] ? classes[id].split(" ") : [],
          add: function (c) {
            if (this._c.indexOf(c) === -1) this._c.push(c);
            classes[id] = this._c.join(" ");
          },
          remove: function (c) {
            this._c = this._c.filter(function (x) { return x !== c; });
            classes[id] = this._c.join(" ");
          },
          toggle: function (c) {
            if (this._c.indexOf(c) === -1) {
              this._c.push(c); classes[id] = this._c.join(" ");
            }
            return this._c.indexOf(c) !== -1;
          },
          contains: function (c) { return this._c.indexOf(c) !== -1; },
          toString: function () { return this._c.join(" "); }
        },
        disabled: !!disabled[id],
        setAttribute: function (k, v) { (attrs[id] = attrs[id] || {})[k] = v; },
        removeAttribute: function (k) { if (attrs[id]) delete attrs[id][k]; }
      };
      base.style = Object.create(style[id] || null);
      base.style.pointerEvents = style[id] && style[id].pointerEvents || "";
      base.style.opacity = style[id] && style[id].opacity || "";
      if (hidden[id]) base.style.display = "none";
      elCache[id] = base;
      return base;
    },
    createElement: function (tag) {
      var el = {
        tagName: tag.toUpperCase(),
        _id: "",
        className: "",
        textContent: "",
        style: { display: "none" },
        parentNode: detachedParent,
        setAttribute: function () {},
        removeAttribute: function () {},
        classList: { add: function(){}, remove:function(){}, toggle:function(){return false;} }
      };
      Object.defineProperty(el, "id", {
        get: function () { return this._id; },
        set: function (v) {
          this._id = v;
          if (!ids[v]) ids[v] = { text: "" };
          elCache[v] = this;
        }
      });
      return el;
    },
    body: {
      className: "",
      classList: rootClassList,
      insertBefore: function () {},
      appendChild: function () {}
    }
  };

  global.window = {
    document: global.document,
    identityWallet: null,
    selectedScoutId: null,
    xmrWalletAddress: null,
    refreshScoutsAndJackpot: function () { return true; },
    localStorage: {
      _data: {},
      getItem: function (k) { return this._data[k] || null; },
      setItem: function (k, v) { this._data[k] = String(v); },
      removeItem: function (k) { delete this._data[k]; }
    }
  };

  // ...and the bare globals a browser resolves through window: the contract
  // reads window.scoutIds and calls localStorage unqualified.
  global.localStorage = global.window.localStorage;
  global.window.scoutIds = undefined;

  function setById(id, opts) {
    ids[id] = opts || {};
    opts.cls && (classes[id] = opts.cls);
    opts.text && (ids[id].text = opts.text);
    opts.html && (ids[id].html = opts.html);
    opts.hidden && (hidden[id] = true);
    opts.disabled !== undefined && (disabled[id] = !!opts.disabled);
    opts.ariaDisabled !== undefined && (ariaDisabled[id] = opts.ariaDisabled ? "true" : null);
    opts.style && (style[id] = opts.style);
  }

  // --- load the freeze contract into the harness -------------------------
  var fs = require("fs");
  var path = require("path");
  var contractText = fs.readFileSync(path.resolve(__dirname, "scout_paywall_freeze_and_unfreeze.js"), "utf8");
  eval(contractText); // exposes scoutPaywallFreezeUntil / Unfreeze / Status etc.

  // Trigger the DOMContentLoaded handler once.
  if (document["_listeners_DOMContentLoaded"]) {
    document["_listeners_DOMContentLoaded"].forEach(function (fn) { fn({preventDefault:function(){}}); });
  }

  console.log("");
  console.log("--- freeze contract assertions ---");

  // 1) initial: not frozen
  var status0 = window.scoutPaywallFreezeStatus();
  assertEqual(status0.frozen, false, "initial not frozen");
  assertEqual(status0.until, null, "initial until is null");
  assertEqual(status0.remainingSeconds, 0, "initial remaining 0");

  // 2) freeze until a future timestamp -> frozen surfaces disabled
  setById("scout-purchase-panel", { text: "panel" });
  setById("btn-download-miner", { cls: "btn" });
  setById("btn-donate-unlock", { cls: "btn" });
  setById("btn-activate-scout", { cls: "btn" });
  setById("cta-get-scout", { cls: "btn" });
  setById("connect-xmr-wallet", { cls: "btn" });
  setById("cta-buy-xmrj-dex", { cls: "btn" });
  setById("scout-paywall-freeze-msg", { text: "" });

  var future = Date.now() + 5000;
  window.scoutPaywallFreezeUntil(future);

  var status1 = window.scoutPaywallFreezeStatus();
  assertEqual(status1.frozen, true, "after freezeUntil -> frozen true");
  assert(status1.until > 0, "after freezeUntil -> until set");
  assert(status1.remainingSeconds > 0, "after freezeUntil -> remaining > 0");

  var panel = document.getElementById("scout-purchase-panel");
  assert(panel && panel.style.pointerEvents === "none", "frozen panel pointerEvents none");
  assert(panel && panel.style.opacity === "0.55", "frozen panel opacity 0.55");

  var dl = document.getElementById("btn-download-miner");
  assert(dl && dl.disabled, "frozen btn-download-miner disabled");
  assert(dl && attrs["btn-download-miner"] && attrs["btn-download-miner"]["aria-disabled"] === "true",
    "frozen btn-download-miner aria-disabled true");

  var cta = document.getElementById("cta-get-scout");
  assert(cta && cta.disabled, "frozen cta-get-scout disabled");

  var freezeMsg = document.getElementById("scout-paywall-freeze-msg");
  assert(freezeMsg && freezeMsg.style.display !== "none" && freezeMsg.textContent.length > 0,
    "frozen freeze message visible with text");

  // 3) unfreeze -> surfaces re-enabled
  window.scoutPaywallUnfreeze();
  var status2 = window.scoutPaywallFreezeStatus();
  assertEqual(status2.frozen, false, "after unfreeze -> frozen false");
  assertEqual(status2.until, null, "after unfreeze -> until null");

  var panel2 = document.getElementById("scout-purchase-panel");
  assert(panel2 && panel2.style.pointerEvents === "", "unfrozen panel pointerEvents empty");
  assert(panel2 && panel2.style.opacity === "", "unfrozen panel opacity empty");

  var dl2 = document.getElementById("btn-download-miner");
  assert(dl2 && !dl2.disabled, "unfrozen btn-download-miner not disabled");
  assert(dl2 && (!attrs["btn-download-miner"] || !attrs["btn-download-miner"]["aria-disabled"]),
    "unfrozen btn-download-miner aria-disabled removed");

  var msg2 = document.getElementById("scout-paywall-freeze-msg");
  assert(msg2 && msg2.style.display === "none", "unfrozen freeze message hidden");

  // 4) freezeStatus is idempotent and safe when no freeze stored
  window.scoutPaywallFreezeUntil(0);
  var status3 = window.scoutPaywallFreezeStatus();
  assertEqual(status3.frozen, false, "freezeUntil(0) -> not frozen");
  assertEqual(status3.until, null, "freezeUntil(0) -> until null");

  console.log("");
  console.log("=== results: %d passed, %d failed ===", passed, failed);
  process.exitCode = failed ? 1 : 0;
})();
