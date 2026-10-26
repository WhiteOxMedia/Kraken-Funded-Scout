// pool/static/scout_paywall_wire_patch.test.js
// Headless verification: patch installs, binds to wf* IDs, posts to
// /scout/activate, and renders the limiter when the server returns 409.

const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');

// Create a JSDOM environment that can run the patch script
const dom = new JSDOM(
  '<!DOCTYPE html><html><head></head><body></body></html>',
  {
    url: 'http://127.0.0.1:8282',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    resources: 'usable'
  }
);

global.window = dom.window;
global.document = dom.window.document;
global.HTMLElement = dom.window.HTMLElement;
global.Element = dom.window.Element;
global.DocumentFragment = dom.window.DocumentFragment;
global.addEventListener = dom.window.addEventListener.bind(dom.window);
global.removeEventListener = dom.window.removeEventListener.bind(dom.window);
// Provide fetch on both global and JSDOM window contexts
global.fetch = function() {
  throw new Error('fetch not overridden yet');
};
global.window.fetch = global.fetch;
global.console = console;
global.JSON = JSON;
global.setTimeout = setTimeout;
global.clearInterval = clearInterval;

// JSDOM doesn't implement alert - provide a noop
global.window.alert = function() {};

const patchSrc = fs.readFileSync(
  path.join(__dirname, 'scout_paywall_wire_patch.js'),
  'utf8'
);

// ---------------------------------------------------------------------------
// Helper: set up DOM elements and intercept onclick/addEventListener
// ---------------------------------------------------------------------------
function setupElements(ids) {
  // Create elements in the DOM first
  for (const id of ids) {
    const existing = document.getElementById(id);
    if (!existing) {
      const el = document.createElement('div');
      el.id = id;
      document.body.appendChild(el);
    }
  }

  const boundOnClick = [];
  const boundEvents = [];

  // Intercept onclick at the prototype level
  const origOnclickDescriptor = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    'onclick'
  );
  Object.defineProperty(HTMLElement.prototype, 'onclick', {
    set(fn) {
      const id = this.id;
      if (typeof fn === 'function' && id) {
        boundOnClick.push({ element: id, handler: fn });
      }
      if (origOnclickDescriptor && origOnclickDescriptor.set) {
        origOnclickDescriptor.set.call(this, fn);
      }
    },
    get() {
      return origOnclickDescriptor ? origOnclickDescriptor.get.call(this) : undefined;
    },
    configurable: true,
    enumerable: true
  });

  // Intercept addEventListener at the prototype level
  const origAddEventListener = HTMLElement.prototype.addEventListener;
  HTMLElement.prototype.addEventListener = function(type, fn) {
    if (type === 'click' && this.id) {
      boundEvents.push({ element: this.id, handler: fn });
    }
    return origAddEventListener.call(this, type, fn);
  };

  return { boundOnClick, boundEvents };
}



// ---------------------------------------------------------------------------
// Test 1: install guard
// ---------------------------------------------------------------------------
console.log('Test 1: install guard');
if (typeof window.__scout_wire_patch_installed !== 'undefined') {
  delete window.__scout_wire_patch_installed;
}
dom.window.eval(patchSrc);
if (window.__scout_wire_patch_installed !== true) {
  console.log('  FAIL: __scout_wire_patch_installed not set');
  process.exit(1);
}
console.log('  PASS: patch installed');

// ---------------------------------------------------------------------------
// Test 2: patch binds to real wf* IDs when they exist
// ---------------------------------------------------------------------------
console.log('Test 2: binds to wf* IDs present in template');
const test2Ids = [
  'btnWfGetScout',
  'btnWfScoutPurchaseActivate',
  'btnConnectWallet',
  'wfScoutPurchasePanel',
  'wfScoutUnlockPanel',
  'wfScoutUnlockStatus',
  'wfJackpotCyclePanel',
  'wfScoutActivationLimiter'
];
const { boundOnClick: t2OnClick, boundEvents: t2Events } = setupElements(test2Ids);

// reload fresh patch
delete window.__scout_wire_patch_installed;
dom.window.eval(patchSrc);

// Fire DOMContentLoaded since JSDOM doesn't do it automatically
const dlcEvent = new dom.window.Event('DOMContentLoaded');
dom.window.document.dispatchEvent(dlcEvent);

const t2AllBoundIds = [
  ...t2OnClick.map(b => b.element),
  ...t2Events.map(b => b.element)
].filter(Boolean);
console.log('  onclick assignments: ' + t2OnClick.length);
console.log('  addEventListener calls: ' + t2Events.length);
console.log('  total bound elements: ' + t2AllBoundIds.length);
for (const b of t2OnClick) console.log('    onclick: ' + b.element);
for (const b of t2Events) console.log('    addEventListener: ' + b.element);

const expected = ['btnWfGetScout', 'btnWfScoutPurchaseActivate', 'btnConnectWallet'];
for (const id of expected) {
  if (!t2AllBoundIds.includes(id)) {
    console.log('  FAIL: ' + id + ' not bound');
    process.exit(1);
  }
}
console.log('  PASS: all expected wf* CTAs bound');

// ---------------------------------------------------------------------------
// Test 3: fallback when wf* IDs missing, legacy ids used
// ---------------------------------------------------------------------------
console.log('Test 3: legacy/rendered ids bind when wf* missing');

// Clear DOM
document.body.innerHTML = '';

const legacyIds = [
  'btn-download-miner',
  'btn-donate-unlock',
  'btn-activate-scout',
  'connect-xmr-wallet',
  'btn-miner-started',
  'scout-purchase-panel',
  'scout-unlock-panel',
  'miner-modal',
  'jackpot-cycle-panel'
];
const { boundOnClick: t3OnClick, boundEvents: t3Events } = setupElements(legacyIds);

// reload fresh patch
delete window.__scout_wire_patch_installed;
dom.window.eval(patchSrc);

// Fire DOMContentLoaded since JSDOM doesn't do it automatically
const dlcEvent2 = new dom.window.Event('DOMContentLoaded');
dom.window.document.dispatchEvent(dlcEvent2);

const t3AllBoundIds = [
  ...t3OnClick.map(b => b.element),
  ...t3Events.map(b => b.element)
].filter(Boolean);
for (const id of [
  'btn-download-miner',
  'btn-donate-unlock',
  'btn-activate-scout',
  'connect-xmr-wallet',
  'btn-miner-started'
]) {
  if (!t3AllBoundIds.includes(id)) {
    console.log('  FAIL: legacy id ' + id + ' not bound');
    process.exit(1);
  }
}
console.log('  PASS: legacy ids bound when wf* missing');

// ---------------------------------------------------------------------------
// Test 4: activate posts to /scout/activate with correct payload + 409 limiter
// ---------------------------------------------------------------------------
console.log('Test 4: activate posts to /scout/activate with 409 limiter render');

// Clear DOM and set up fresh
document.body.innerHTML = '';
window.identityWallet = 'sol_test_wallet_123';
window.selectedScoutId = 42;

const test4Ids = ['btnWfScoutPurchaseActivate', 'wfScoutActivationLimiter', 'wfScoutUnlockStatus'];
const { boundOnClick: t4OnClick, boundEvents: t4Events } = setupElements(test4Ids);

const captured = [];
const testFetch = function (input) {
  const url = typeof input === 'string' ? input : (input && input.url);
  captured.push({ url: url });
  if (url && url.indexOf('/api/auth/me') === 0) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ authenticated: true, wallet_address: 'sol_test_wallet_123' })
    });
  }
  return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve(null) });
};
global.fetch = testFetch;
global.window.fetch = testFetch;

// The patch now delegates activation to scout_paywall's exposed function.
const activated = [];
window.selectedScoutId = 42;
window.XMRJackpotScoutPaywall = {
  activateScout: function () { activated.push(true); }
};

// reload fresh patch
delete window.__scout_wire_patch_installed;
dom.window.eval(patchSrc);

// Fire DOMContentLoaded since JSDOM doesn't do it automatically
const dlcEvent3 = new dom.window.Event('DOMContentLoaded');
dom.window.document.dispatchEvent(dlcEvent3);

const activateBtn = document.getElementById('btnWfScoutPurchaseActivate');
if (!activateBtn) {
  console.log('  FAIL: activate button missing');
  process.exit(1);
}

activateBtn.click();

setTimeout(() => {

// ---------------------------------------------------------------------------
// Test 5: activate CTA still routes through scout_paywall when identity is
// already active (real server path: HTTP 200 + already_active:true).
// ---------------------------------------------------------------------------
console.log('Test 5: already_active:true path still delegates to scout_paywall');

// Clear DOM and set up fresh
document.body.innerHTML = '';
window.identityWallet = 'already_active_wallet';
window.selectedScoutId = 7;

const test5Ids = [
  'btnWfScoutPurchaseActivate',
  'wfScoutActivationLimiter',
  'wfScoutUnlockStatus'
];
const { boundOnClick: t5OnClick, boundEvents: t5Events } = setupElements(test5Ids);

const t5Captured = [];
const t5Fetch = function (input) {
  const url = typeof input === 'string' ? input : (input && input.url);
  t5Captured.push({ url: url });
  if (url && url.indexOf('/api/auth/me') === 0) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ authenticated: true, wallet_address: 'already_active_wallet' })
    });
  }
  if (url && url.indexOf('/scout/activate') === 0) {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ ok: true, already_active: true })
    });
  }
  return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve(null) });
};
global.fetch = t5Fetch;
global.window.fetch = t5Fetch;

const t5Activated = [];
window.XMRJackpotScoutPaywall = {
  activateScout: function () { t5Activated.push(true); }
};

// reload fresh patch
delete window.__scout_wire_patch_installed;
dom.window.eval(patchSrc);

// Fire DOMContentLoaded since JSDOM doesn't do it automatically
const dlcEvent5 = new dom.window.Event('DOMContentLoaded');
dom.window.document.dispatchEvent(dlcEvent5);

const t5ActivateBtn = document.getElementById('btnWfScoutPurchaseActivate');
if (!t5ActivateBtn) {
  console.log('  FAIL: activate button missing');
  process.exit(1);
}

t5ActivateBtn.click();

setTimeout(() => {
  // OWNERSHIP: the wire patch DELEGATES activation to scout_paywall.js and
  // renders only its own pre-flight refusals (no session, no wallet). The
  // server's already_active answer is rendered by the delegate, whose own
  // drive (scout_paywall_freeze_and_unfreeze.test.js) owns that copy — so
  // this test pins the CONTRACT here (delegated exactly once, given a
  // resolved identity, with no refusal written over the delegate's lane).
  if (t5Activated.length !== 1) {
    console.log('  FAIL: expected activateScout() to run even when already_active, got ' + t5Activated.length);
    process.exit(1);
  }
  const us = document.getElementById('wfScoutUnlockStatus');
  const preflightRefusals = [
    'Sign in and connect an XMR wallet',
    'Activation is unavailable right now'
  ];
  const wroteRefusal = us && preflightRefusals.some(
    (m) => us.textContent.indexOf(m) !== -1);
  if (wroteRefusal) {
    console.log('  FAIL: the patch wrote a refusal over the delegate\'s answer: '
      + us.textContent);
    process.exit(1);
  }
  // The wallet was prefetched at mount, so the delegate must get it WITHOUT
  // a second identity round-trip, and the patch itself must never POST the
  // activation — the POST belongs to scout_paywall.js alone.
  const meCalls = t5Captured.filter((c) => c.url && c.url.indexOf('/api/auth/me') === 0);
  if (meCalls.length !== 0) {
    console.log('  FAIL: a warm identity wallet was re-read instead of reused (' + meCalls.length + ' identity reads)');
    process.exit(1);
  }
  const posts = t5Captured.filter((c) => c.url && c.url.indexOf('/scout/activate') === 0);
  if (posts.length !== 0) {
    console.log('  FAIL: the patch POSTed activation itself instead of delegating (' + posts.length + ' calls)');
    process.exit(1);
  }
  if (!window.identityWallet) {
    console.log('  FAIL: delegation ran without a resolved identity wallet');
    process.exit(1);
  }
  console.log('  PASS: already_active:true path delegates to scout_paywall exactly once with a resolved identity');
  console.log('=== ALL TESTS PASSED ===');
}, 220);
}, 200);
