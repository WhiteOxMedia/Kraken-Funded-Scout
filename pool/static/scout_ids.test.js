// pool/static/scout_ids.test.js
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');

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
global.fetch = function () {
  throw new Error('fetch not overridden yet');
};
global.window.fetch = global.fetch;
global.console = console;
global.JSON = JSON;
global.setTimeout = setTimeout;
global.clearInterval = clearInterval;
global.window.alert = function () {};

const idsSrc = fs.readFileSync(
  path.join(__dirname, 'scout_ids.js'),
  'utf8'
);

console.log('Test 1: scout_ids.js exports window.scoutIds');

if (typeof window.scoutIds !== 'undefined') {
  delete window.scoutIds;
}

dom.window.eval(idsSrc);

if (typeof window.scoutIds !== 'object' || window.scoutIds === null) {
  console.log('  FAIL: window.scoutIds not installed');
  process.exit(1);
}
console.log('  PASS: window.scoutIds installed');

console.log('Test 2: wirePatch contains expected keys');

const wirePatchExpected = [
  'ctaGetScout',
  'btnActivateScout',
  'connectXmrWallet',
  'purchasePanel',
  'unlockPanel',
  'unlockStatus',
  'jackpotPanel',
  'activationLimiter',
  'seeNextCycle',
  'btnDownloadMiner',
  'btnDonateUnlock',
  'btnHowItWorks',
  'btnMinerStarted',
  'scoutArea',
  'myScoutsList',
  'scoutModals'
];

const wirePatch = window.scoutIds.wirePatch;
if (!wirePatch || typeof wirePatch !== 'object') {
  console.log('  FAIL: window.scoutIds.wirePatch missing or not an object');
  process.exit(1);
}

const wirePatchKeys = Object.keys(wirePatch);
for (const key of wirePatchExpected) {
  if (!wirePatchKeys.includes(key)) {
    console.log('  FAIL: wirePatch missing key ' + key);
    process.exit(1);
  }
}
console.log('  PASS: wirePatch has ' + wirePatchKeys.length + ' keys including expected ones');

console.log('Test 3: legacyToWf contains expected mappings');

const legacyToWfExpected = {
  'scout-activation-limiter': 'wfScoutActivationLimiter',
  'btn-see-next-cycle': 'wfBtnSeeNextCycle',
  'jackpot-cycle-panel': 'wfJackpotCyclePanel',
  'unlock-status': 'wfScoutUnlockStatus',
  'xmr-wallet-status': 'wfWalletStatus'
};

const legacyToWf = window.scoutIds.legacyToWf;
if (!legacyToWf || typeof legacyToWf !== 'object') {
  console.log('  FAIL: window.scoutIds.legacyToWf missing or not an object');
  process.exit(1);
}

for (const [legacy, expectedWf] of Object.entries(legacyToWfExpected)) {
  if (legacyToWf[legacy] !== expectedWf) {
    console.log(
      '  FAIL: legacyToWf[' + JSON.stringify(legacy) + '] = ' +
      JSON.stringify(legacyToWf[legacy]) + ', expected ' + JSON.stringify(expectedWf)
    );
    process.exit(1);
  }
}
console.log('  PASS: legacyToWf entries match expected');

console.log('Test 4: $() maps legacy ids onto existing wf* elements');

const legacyToElement = {
  'scout-activation-limiter': 'wfScoutActivationLimiter',
  'btn-see-next-cycle': 'wfBtnSeeNextCycle',
  'jackpot-cycle-panel': 'wfJackpotCyclePanel',
  'unlock-status': 'wfScoutUnlockStatus',
  'xmr-wallet-status': 'wfWalletStatus'
};

for (const [legacy, wfId] of Object.entries(legacyToElement)) {
  let el = document.getElementById(wfId);
  if (!el) {
    el = document.createElement('div');
    el.id = wfId;
    document.body.appendChild(el);
  }
}

for (const [legacy, expectedWf] of Object.entries(legacyToElement)) {
  const resolved = window.scoutIds.$(legacy);
  if (!resolved) {
    console.log('  FAIL: $( ' + JSON.stringify(legacy) + ' ) returned null');
    process.exit(1);
  }
  if (resolved.id !== expectedWf) {
    console.log(
      '  FAIL: $( ' + JSON.stringify(legacy) + ' ).id = ' +
      JSON.stringify(resolved.id) + ', expected ' + JSON.stringify(expectedWf)
    );
    process.exit(1);
  }
}
console.log('  PASS: $() resolved legacy ids to correct wf* elements');

console.log('Test 5: $() returns element by raw id when no wf* mapping exists');

const rawId = 'some-raw-legacy-element';
const rawEl = document.createElement('div');
rawEl.id = rawId;
document.body.appendChild(rawEl);

const resolvedRaw = window.scoutIds.$(rawId);
if (!resolvedRaw || resolvedRaw.id !== rawId) {
  console.log(
    '  FAIL: $(' + JSON.stringify(rawId) + ') did not resolve to raw element; got ' +
    (resolvedRaw ? JSON.stringify(resolvedRaw.id) : 'null')
  );
  process.exit(1);
}
console.log('  PASS: $() falls back to raw getElementById when no mapping exists');

console.log('');
console.log('=== ALL TESTS PASSED ===');
