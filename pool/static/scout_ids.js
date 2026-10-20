// pool/static/scout_ids.js
// Shared ID mappings and DOM accessor for scout-related modules.
// Both scout_paywall.js and scout_paywall_wire_patch.js import from here
// to avoid duplicating ID-to-element resolution logic.
//
// Template reality: the current index.html uses wf* IDs. The legacy
// scout_paywall.js code was written for a different HTML structure that
// used non-wf IDs. This module provides both mappings so each consumer
// can resolve the IDs it cares about.
(function () {
  "use strict";

  // ── wf* IDs that exist in pool/templates/index.html ──────────────
  // Confirmed present (grep verified):
  //   btnWfGetScout, btnWfScoutPurchaseActivate, btnConnectWallet,
  //   wfScoutPurchasePanel, wfScoutUnlockPanel, wfJackpotCyclePanel,
  //   wfScoutActivationLimiter, wfScoutUnlockStatus
  const WF_IDS = {
    btnWfGetScout: "btnWfGetScout",
    btnWfScoutPurchaseActivate: "btnWfScoutPurchaseActivate",
    btnConnectWallet: "btnConnectWallet",
    wfScoutPurchasePanel: "wfScoutPurchasePanel",
    wfScoutUnlockPanel: "wfScoutUnlockPanel",
    wfJackpotCyclePanel: "wfJackpotCyclePanel",
    wfScoutActivationLimiter: "wfScoutActivationLimiter",
    wfScoutUnlockStatus: "wfScoutUnlockStatus"
  };

  // ── wf* IDs used by scout_paywall_wire_patch.js (logical → actual) ──
  const WIRE_PATCH_IDS = {
    ctaGetScout: "btnWfGetScout",
    btnActivateScout: "btnWfScoutPurchaseActivate",
    connectXmrWallet: "btnConnectWallet",
    purchasePanel: "wfScoutPurchasePanel",
    unlockPanel: "wfScoutUnlockPanel",
    unlockStatus: "wfScoutUnlockStatus",
    jackpotPanel: "wfJackpotCyclePanel",
    activationLimiter: "wfScoutActivationLimiter",
    seeNextCycle: "wfBtnSeeNextCycle",
    btnDownloadMiner: "wfBtnDownloadMiner",
    btnDonateUnlock: "wfBtnDonateUnlock",
    btnHowItWorks: "wfCtaHowItWorks",
    btnMinerStarted: "wfBtnMinerStarted",
    scoutArea: "wfScoutArea",
    myScoutsList: "wfMyScoutsList",
    scoutModals: "wfScoutModals"
  };

  // ── Legacy IDs that scout_paywall.js used (legacy → wf* where present) ──
  // Some legacy IDs do not exist in the current template at all; consumers
  // that still reference them should fall back to window locals or skip.
  const LEGACY_TO_WF = {
    "scout-activation-limiter": "wfScoutActivationLimiter",
    "btn-see-next-cycle": "wfBtnSeeNextCycle",
    "jackpot-cycle-panel": "wfJackpotCyclePanel",
    "unlock-status": "wfScoutUnlockStatus",
    "xmr-wallet-status": "wfWalletStatus"
  };

  // ── Safe accessor: tries wf* id first, then legacy id ──────────────
  function $(id) {
    // If it's a legacy id with a wf* mapping, try the wf* id first
    var wf = LEGACY_TO_WF[id];
    if (wf) {
      var el = document.getElementById(wf);
      if (el) return el;
    }
    return document.getElementById(id);
  }

  // ── Expose a rich API so both consumers get what they need ─────────
  window.scoutIds = {
    // All known wf* IDs
    wf: WF_IDS,
    // Wire patch logical→actual mapping
    wirePatch: WIRE_PATCH_IDS,
    // Legacy→wf* mapping (for scout_paywall.js's $() behavior)
    legacyToWf: LEGACY_TO_WF,
    // DOM accessor: tries wf* mapping first, then legacy id
    $: $,
    // Raw getElementById (in case consumers want it)
    getElementById: function (id) { return document.getElementById(id); }
  };
})();
