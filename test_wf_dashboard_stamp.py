"""test_wf_dashboard_stamp.py — THE SCOUT PANEL STAMP CONTRACT,
EXECUTED (Sep 26).

wf_dashboard.js's Scout panel shows the SAME notification-preferences
stamp the Notifications card shows. It must update INSTANTLY when the
prefs change (push_client announces every applied envelope on
xmrjackpot:prefs) and on login/register/logout (xmrjackpot:auth), not
just on its 60s poll — and a logout's null answer must CLEAR the stamp
instead of letting the previous user's stamp linger. This drive
executes the REAL pool/static/wf_dashboard.js in Node with a stubbed
DOM + fetch and pins:

  1. boot renders the stamp from the preferences GET
  2. an xmrjackpot:prefs announcement re-renders INSTANTLY (no fetch)
  3. xmrjackpot:auth triggers a fresh fetch and re-render
  4. a null answer (the 401 after logout) CLEARS the stamp
  5. a fresh account (updated_at null) keeps saying "never changed"
  6. the stamp renders through the SHARED relTime exporter
     (window.XMRJackpotPush.relTime), not a private renderer

No network, no browser: Node is required, everything else is stubbed.
Prints "RESULT: N passed, M failed".
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENT = HERE / "pool" / "static" / "wf_dashboard.js"

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


node = shutil.which("node")
if not node or not CLIENT.exists():
    print("SKIP: node not available or wf_dashboard.js missing — "
          "the fleet stays green; install Node to run this drive")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

# ── THE HARNESS: stubbed DOM + fetch, then the REAL wf_dashboard ────
# The IIFE boots immediately (readyState 'complete'): wire(), the
# three refresh fetches, and the two document listeners. `answers`
# scripts per-path responses; assertions read the stub elements.
HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const dispatched = [];
const gets = [];
const timers = [];
let answers = {};          // path -> {status, json} | null (network fail)

function el(id) {
  return {
    id,
    textContent: '',
    title: '',
    style: {},
    dataset: {},
    innerHTML: '',
    className: '',
    children: [],
    listeners: {},
    appendChild(c) { this.children.push(c); },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
  };
}
const els = {};
for (const id of ['wfPrefsStamp', 'wfSignalFeed', 'wfDotWallet', 'wfWalletStatus',
                  'wfDotMiner', 'wfMinerStatus', 'wfDotScout', 'wfScoutStatus',
                  'wfChallengeStatus', 'wfChallengeProgress', 'wfReferralEarned']) {
  els[id] = el(id);
}

const sandbox = {
  console,
  setTimeout, clearTimeout,
  setInterval: (fn, ms) => { timers.push([fn, ms]); return 1; },
  clearInterval: () => {},
  Date, Math, JSON, Promise, Object, Array, String, Number, Boolean, Error,
  document: {
    readyState: 'complete',
    getElementById: (id) => els[id] || null,
    querySelector: () => null,          // nav CTA wiring: nothing to wire
    addEventListener(t, fn) {
      (sandbox.document._listeners[t] = sandbox.document._listeners[t] || []).push(fn);
    },
    _listeners: {},
    dispatchEvent(ev) { dispatched.push(ev); return true; },
  },
  CustomEvent: function (type, opts) {
    this.type = type;
    this.detail = opts && opts.detail;
  },
};
sandbox.window = {};                       // no APP_API_BASE, no exporter yet
sandbox.window.XMRJackpotPush = {
  // a marked fake: every stamp render MUST route through the shared
  // exporter — the text literally carries the mark
  relTime: (iso) => 'SHARED[' + iso + ']',
};
sandbox.fetch = function (path, opts) {
  opts = opts || {};
  const method = (opts.method || 'GET').toUpperCase();
  const a = answers[path];
  if (method === 'GET') { gets.push(path); }
  if (a === null) { return Promise.reject(new Error('network fail')); }
  return Promise.resolve({
    ok: !a || (a.status >= 200 && a.status < 300),
    status: a ? a.status : 200,
    json: () => Promise.resolve(a ? a.json : {}),
  });
};

const ctx = vm.createContext(sandbox);
const src = fs.readFileSync(process.env.CLIENT_PATH, 'utf8');
// LOADED INSIDE THE SCENARIO (after it scripts its answers): the IIFE
// boots immediately and fires its initial fetches, so loading at
// harness top level would consume unscripted responses.
function loadNow() {
  vm.runInContext(src, ctx, { filename: 'wf_dashboard.js' });
}

function flush() {
  return new Promise((res) => { setTimeout(() => res(), 5); });
}
function flushAll(times) {
  let p = Promise.resolve();
  for (let i = 0; i < (times || 6); i++) { p = p.then(flush); }
  return p;
}
function dispatch(type, detail) {
  const ls = sandbox.document._listeners[type] || [];
  assert.ok(ls.length >= 1, 'a ' + type + ' listener is registered');
  ls[ls.length - 1]({ detail });
}
function dispatchNoDetail(type) {
  const ls = sandbox.document._listeners[type] || [];
  assert.ok(ls.length >= 1, 'a ' + type + ' listener is registered');
  ls[ls.length - 1]({});
}
function stampText() { return els.wfPrefsStamp.textContent; }
function stampTitle() { return els.wfPrefsStamp.title; }

const ENV_A = { user_id: 'alice', preferences: { jackpot: true, scout: true },
                updated_at: '2026-09-26T10:00:00Z' };
const ENV_B = { user_id: 'alice', preferences: { jackpot: false, scout: true },
                updated_at: '2026-09-26T11:00:00Z' };
const ENV_C = { user_id: 'alice', preferences: { jackpot: true, scout: false },
                updated_at: '2026-09-26T12:00:00Z' };

async function scenario(name) {
  if (name === 'announce_instant') {
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [] } } };
    loadNow();
    await flushAll();
    const afterBoot = gets.filter((p) => p === '/api/push/preferences').length;
    assert.ok(stampText().indexOf('SHARED[' + ENV_A.updated_at + ']') >= 0,
              'boot renders the stamp through the shared exporter: ' + stampText());
    assert.strictEqual(stampTitle(), ENV_A.updated_at, 'hover carries the exact UTC stamp');
    // THE ANNOUNCEMENT: instant re-render, no new fetch
    dispatch('xmrjackpot:prefs', ENV_B);
    assert.ok(stampText().indexOf('SHARED[' + ENV_B.updated_at + ']') >= 0,
              'the announcement re-rendered the stamp instantly: ' + stampText());
    assert.strictEqual(stampTitle(), ENV_B.updated_at, 'the title moved with it');
    assert.strictEqual(gets.filter((p) => p === '/api/push/preferences').length, afterBoot,
                       'the instant render fired NO fetch (that is the point)');
    return 'announce_instant ok';
  }

  if (name === 'auth_refetch') {
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [] } } };
    loadNow();
    await flushAll();
    const before = gets.filter((p) => p === '/api/push/preferences').length;
    answers['/api/push/preferences'] = { status: 200, json: ENV_C };
    dispatchNoDetail('xmrjackpot:auth');          // login/register
    await flushAll();
    assert.strictEqual(gets.filter((p) => p === '/api/push/preferences').length, before + 1,
                       'xmrjackpot:auth triggered exactly one fresh fetch');
    assert.ok(stampText().indexOf('SHARED[' + ENV_C.updated_at + ']') >= 0,
              'the stamp re-rendered from the fresh envelope: ' + stampText());
    return 'auth_refetch ok';
  }

  if (name === 'logout_clears') {
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [] } } };
    loadNow();
    await flushAll();
    assert.ok(stampText().indexOf('SHARED[') >= 0, 'stamp present before logout');
    // after logout the session is gone: the fresh fetch fails the 401
    // gate and the chain answers null
    answers['/api/push/preferences'] = { status: 401, json: {} };
    dispatchNoDetail('xmrjackpot:auth');
    await flushAll();
    assert.strictEqual(stampText(), '',
                       'a null answer (the 401 after logout) CLEARS the stamp');
    assert.strictEqual(stampTitle(), '', 'the stale exact-moment tooltip goes too');
    // and so does a network failure (the fail-quiet catch answers null)
    answers['/api/push/preferences'] = null;
    dispatchNoDetail('xmrjackpot:auth');
    await flushAll();
    assert.strictEqual(stampText(), '', 'a network failure keeps the stamp cleared');
    return 'logout_clears ok';
  }

  if (name === 'fresh_account_honest') {
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [] } } };
    loadNow();
    await flushAll();
    // switching to a FRESH account: a real envelope, updated_at null
    answers['/api/push/preferences'] = { status: 200, json: {
      user_id: 'bob', preferences: { jackpot: true, scout: true }, updated_at: null } };
    dispatchNoDetail('xmrjackpot:auth');
    await flushAll();
    assert.strictEqual(stampText(), 'prefs never changed (defaults stand)',
                       'a fresh account keeps its honest never-changed line');
    assert.strictEqual(stampTitle(), '', 'no tooltip without a stamp');
    return 'fresh_account_honest ok';
  }

  throw new Error('unknown scenario ' + name);
}

scenario(process.argv[2]).then(
  (ok) => { console.log(ok); process.exit(0); },
  (err) => { console.error(String(err && err.message || err)); process.exit(1); },
);
"""

# ── THE RUNNER: one process per scenario (fresh IIFE state) ──────────
SCENARIOS = [
    ("1", "announce_instant",
     "boot renders the stamp; an announcement re-renders it instantly with no fetch"),
    ("2", "auth_refetch",
     "xmrjackpot:auth triggers exactly one fresh fetch and re-renders"),
    ("3", "logout_clears",
     "a null answer (401 after logout, or network failure) clears the stamp and tooltip"),
    ("4", "fresh_account_honest",
     "a fresh account (updated_at null) keeps saying prefs never changed"),
]


def run_scenario(name):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(HARNESS)
        harness_path = f.name
    env = dict(os.environ, CLIENT_PATH=str(CLIENT))
    try:
        proc = subprocess.run(
            [node, harness_path, name],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            env=env,
        )
    finally:
        try:
            Path(harness_path).unlink(missing_ok=True)
        except OSError:
            pass
    return proc


for label, scenario, why in SCENARIOS:
    proc = run_scenario(scenario)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    ok = proc.returncode == 0
    check(f"{label}", ok, f"node exit {proc.returncode} — {err[:200] or out[:200]}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
