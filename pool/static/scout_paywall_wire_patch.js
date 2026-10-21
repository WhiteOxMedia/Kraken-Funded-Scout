// ============================================================================
// pool/static/scout_paywall_wire_patch.js
// Wiring patch targeted to wf* ids present in the template.
// Replace or add this file and load it after scout_paywall.js.
// ============================================================================
/* eslint-disable no-alert */
(function () {
  "use strict";

  // ── global state the agent asked for (window-scoped) ────────────────
  window.identityWallet = window.identityWallet || null;
  window.selectedScoutId = window.selectedScoutId || null;
  window.xmrjUnlockProof = window.xmrjUnlockProof || null;
  window.xmrWalletAddress = window.xmrWalletAddress || null;

  // ── safe delegate (fallback if scout_paywall.js has not exposed yet)
  const callIf = (fnName, fallback) => {
    return typeof window[fnName] === "function" ? window[fnName] : fallback;
  };

  const log = (...args) => console.log("[scout-paywall-wire]", ...args);

  const paywall = () => window.XMRJackpotScoutPaywall || null;

  function setUnlockStatus(message) {
    const el = document.getElementById(ids.unlockStatus);
    if (el) el.textContent = message;
  }

  function setPurchaseNote(message) {
    const el = document.getElementById("wfScoutPurchaseBlocker");
    if (el) el.textContent = message;
  }

  // scout_paywall.activateScout() reads window.identityWallet; the real
  // identity token is the signed-in account's payout wallet.
  function ensureIdentityWallet() {
    if (window.identityWallet) return Promise.resolve(window.identityWallet);
    let request;
    try {
      request = fetch("/api/auth/me", { credentials: "same-origin" });
    } catch (_) {
      return Promise.resolve(null);
    }
    return Promise.resolve(request)
      .then((r) => (r.ok ? r.json() : null))
      .then((me) => {
        const w = me && me.wallet_address ? String(me.wallet_address).trim() : "";
        if (w) window.identityWallet = w;
        return w || null;
      })
      .catch(() => null);
  }

  // auth-state changed (login/register/logout all happen without a page
  // reload): the cached identity wallet belongs to the session that just
  // ended, so drop it and re-resolve it for the session that is now
  // current. Without this the purchase/activate CTA would carry a stale
  // (or signed-out) wallet into the next checkout.
  document.addEventListener("xmrjackpot:auth", () => {
    window.identityWallet = null;
    ensureIdentityWallet();
  });

  // ── ID map + element accessor imported from the shared module ──────
  const scoutIds = window.scoutIds || {};
  const ids = Object.assign({
    ctaGetScout: scoutIds.wirePatch ? scoutIds.wirePatch.ctaGetScout : "btnWfGetScout",
    btnActivateScout: scoutIds.wirePatch ? scoutIds.wirePatch.btnActivateScout : "btnWfScoutPurchaseActivate",
    connectXmrWallet: scoutIds.wirePatch ? scoutIds.wirePatch.connectXmrWallet : "btnConnectWallet",
    purchasePanel: scoutIds.wirePatch ? scoutIds.wirePatch.purchasePanel : "wfScoutPurchasePanel",
    unlockPanel: scoutIds.wirePatch ? scoutIds.wirePatch.unlockPanel : "wfScoutUnlockPanel",
    unlockStatus: scoutIds.wirePatch ? scoutIds.wirePatch.unlockStatus : "wfScoutUnlockStatus",
    jackpotPanel: scoutIds.wirePatch ? scoutIds.wirePatch.jackpotPanel : "wfJackpotCyclePanel",
    activationLimiter: scoutIds.wirePatch ? scoutIds.wirePatch.activationLimiter : "wfScoutActivationLimiter",
    seeNextCycle: scoutIds.wirePatch ? scoutIds.wirePatch.seeNextCycle : "wfBtnSeeNextCycle",
    btnDownloadMiner: scoutIds.wirePatch ? scoutIds.wirePatch.btnDownloadMiner : "wfBtnDownloadMiner",
    btnDonateUnlock: scoutIds.wirePatch ? scoutIds.wirePatch.btnDonateUnlock : "wfBtnDonateUnlock",
    btnHowItWorks: scoutIds.wirePatch ? scoutIds.wirePatch.btnHowItWorks : "wfCtaHowItWorks",
    btnMinerStarted: scoutIds.wirePatch ? scoutIds.wirePatch.btnMinerStarted : "wfBtnMinerStarted",
    scoutArea: scoutIds.wirePatch ? scoutIds.wirePatch.scoutArea : "wfScoutArea",
    myScoutsList: scoutIds.wirePatch ? scoutIds.wirePatch.myScoutsList : "wfMyScoutsList",
    scoutModals: scoutIds.wirePatch ? scoutIds.wirePatch.scoutModals : "wfScoutModals"
  }, scoutIds.wirePatch || {});

  function $(id) {
    const wf = scoutIds.legacyToWf && scoutIds.legacyToWf[id];
    if (wf) {
      const el = document.getElementById(wf);
      if (el) return el;
    }
    return document.getElementById(id);
  }

  // ── Block stale handlers on any legacy ids that may still exist ──────
  function blockStaleScoutClicks() {
    const BLOCKER_IDS = [
      "cta-get-scout",
      "btn-download-miner",
      "btn-donate-unlock",
      "connect-xmr-wallet",
      "btn-activate-scout",
      "btn-unlock-xmrj",
      "btn-miner-started"
    ];
    for (const id of BLOCKER_IDS) {
      const el = document.getElementById(id);
      if (!el) continue;
      try {
        el.removeEventListener("click", blockStaleScoutClicks.staleHandler);
      } catch (_) {}
      el.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        log("blocked stale CTA: " + id);
      });
    }
  }

  // ── Purchase panel buttons: Download Miner + Donate and Unlock ──────
  function wirePurchasePanel() {
    const dl = document.getElementById(ids.btnDownloadMiner);
    if (dl) {
      dl.onclick = (e) => {
        e.preventDefault();
        const p = paywall();
        // The wallet is prefetched at mount so window.open stays inside the
        // click gesture (an awaited fetch here would be popup-blocked).
        const wallet = window.identityWallet;
        if (p && p.track) {
          p.track("miner_download_clicked", { wallet_bound: !!wallet });
        }
        // scout_checkout owns the route decision (bound vs unbound installer).
        const download = (window.ScoutCheckout &&
          typeof window.ScoutCheckout.openMinerDownload === "function")
          ? window.ScoutCheckout.openMinerDownload
          : window.openMinerDownload;
        if (typeof download === "function") {
          download();
        } else {
          window.open(
            wallet
              ? "/api/miner-installer/" + encodeURIComponent(wallet)
              : "/api/miner-download",
            "_blank"
          );
        }
        setPurchaseNote(
          wallet
            ? "Wallet-bound miner installer started — it mines to your saved XMR wallet (" +
              wallet.slice(0, 8) + "…)."
            : "Miner installer started without a wallet binding — sign in with an XMR wallet to bind it."
        );
      };
    }

    const dn = document.getElementById(ids.btnDonateUnlock);
    if (dn) {
      dn.onclick = (e) => {
        e.preventDefault();
        const p = paywall();
        // Both real entry points exist: ScoutCheckout owns the modal/order,
        // and the paywall exposes the same flow for older builds.
        const openDonate =
          (window.ScoutCheckout && typeof window.ScoutCheckout.open === "function")
            ? window.ScoutCheckout.open
            : (p && typeof p.openDonateFlow === "function" ? p.openDonateFlow : null);
        if (!openDonate) {
          setPurchaseNote("Reload the page to open the donation checkout — nothing was charged.");
          return;
        }
        openDonate();
      };
    }
  }

  // ── Primary hero CTAs: Get a Scout, How it works ────────────────────
  function wireHero() {
    const getScout = $(ids.ctaGetScout);
    if (getScout) {
      getScout.onclick = (e) => {
        e.preventDefault();
        // Scout needs an account: use the app's real router, not a missing
        // global. The nav button is the fallback when navigateTo is absent.
        if (typeof window.navigateTo === "function") {
          window.navigateTo("signup");
          return;
        }
        const nav = document.querySelector('.nav-links button[data-page="signup"]');
        if (nav) nav.click();
      };
    }

    const how = $(ids.btnHowItWorks);
    if (how) {
      how.onclick = (e) => {
        e.preventDefault();
        const target = $(ids.unlockPanel) || $(ids.purchasePanel);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      };
    }
  }

  // ── XMR wallet connect CTA ───────────────────────────────────────────
  function wireXmrWallet() {
    const btn = $(ids.connectXmrWallet);
    if (!btn) return;

    btn.onclick = async (e) => {
      e.preventDefault();
      if (typeof window.connectXmrWallet === "function") {
        try {
          const addr = await window.connectXmrWallet();
          window.xmrWalletAddress = addr || null;
          log("XMR wallet connected", addr);
        } catch (err) {
          log("connectXmrWallet rejected", err);
        }
        return;
      }

      const addr = prompt("Paste your XMR wallet address for testing:");
      if (addr && String(addr).trim()) {
        window.xmrWalletAddress = String(addr).trim();
        log("XMR wallet set (test fallback)");
      }
    };
  }

  // ── Miner modal: button that starts polling ──────────────────────────
  function ensureMinerStartedHook() {
    const btn = $(ids.btnMinerStarted) || document.querySelector("#btn-miner-started, .wf-btn-miner-started");
    if (!btn) return;
    if (btn.__scout_miner_hooked) return;
    btn.__scout_miner_hooked = true;
    btn.onclick = async (e) => {
      e.preventDefault();
      const modal = $(ids.scoutModals) || $("#miner-modal");
      if (modal) modal.classList.add("hidden");

      const p = paywall();
      const poll = (p && typeof p.pollScoutStatus === "function")
        ? p.pollScoutStatus.bind(p)
        : (typeof window.pollScoutStatus === "function" ? window.pollScoutStatus : null);
      if (poll) return poll();

      // No module poller on this build: run the same check inline.
      log("using inline miner-proof poll");
      let tries = 0;
      const id = setInterval(async () => {
        tries += 1;
        try {
          const r = await fetch("/scout/status", { credentials: "same-origin" });
          const d = r.ok ? await r.json() : null;
          const active =
            d != null &&
            ((d.scout && d.scout.has_active_cycle === true) || d.active === true);
          if (active) {
            clearInterval(id);
            const us = document.getElementById(ids.unlockStatus);
            if (us) us.textContent = "Your Scout is active for this jackpot cycle. Mining begins now.";
            log("miner proof detected via fallback poll");
            return;
          }
          if (tries >= 15) {
            clearInterval(id);
            const us = document.getElementById(ids.unlockStatus);
            if (us) us.textContent = "Still waiting — keep the miner running and check back shortly.";
          }
        } catch (err) {
          log("fallback poll tick failed", err);
        }
      }, 8000);
    };
  }

  // ── Activate Scout: delegate to scout_paywall's activateScout() ──────
  function wireActivateScout() {
    const btn = document.getElementById(ids.btnActivateScout);
    if (!btn) return;
    btn.onclick = (e) => {
      e.preventDefault();
      const p = paywall();
      if (!p || typeof p.activateScout !== "function") {
        return;
      }
      ensureIdentityWallet().then((wallet) => {
        if (!wallet) {
          setUnlockStatus("Sign in and connect an XMR wallet to activate your Scout.");
          return;
        }
        try {
          p.activateScout();
        } catch (err) {
          log("activate delegate threw", err);
        }
      });
    };
  }

  // ── Template's own "See next cycle" CTA ─────────────────────────────
  function wireSeeNextCycle() {
    const btn = document.getElementById(ids.seeNextCycle);
    if (!btn) return;
    btn.onclick = (e) => {
      e.preventDefault();
      const target =
        document.getElementById(ids.jackpotPanel) ||
        document.getElementById(ids.purchasePanel);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  }

  // ── refresh on load per the agent checklist ──────────────────────────
  function wireRefreshOnLoad() {
    callIf("refreshScoutsAndJackpot", () => {})();
  }

  // ── mount everything as soon as DOM is ready ─────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    blockStaleScoutClicks();
    wirePurchasePanel();
    wireHero();
    wireXmrWallet();
    ensureMinerStartedHook();
    ensureIdentityWallet();
    wireActivateScout();
    wireSeeNextCycle();
    wireRefreshOnLoad();
  });

  window.__scout_wire_patch_installed = true;
})();
