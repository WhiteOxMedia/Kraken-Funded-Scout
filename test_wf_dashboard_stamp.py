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
  7. a FLAT announcement re-renders too: push_client announces
     WHATEVER envelope it applied and is shape-neutral (flat bodies
     occur — the envelope drive's shape_neutral scenario); updated_at
     is top-level in both real shapes, so the stamp must not care
  8. the 412-conflict envelope (the server's current shape plus an
     extra error field, announced after an auto-adoption) re-renders
     the stamp and the extras are ignored
  9. an envelope-less announcement is a NO-OP: the detail guard drops
     it and the previous stamp survives (no clobber, no crash)
 10. THE SIGNALS FEED: rows render in order with titles escaped, the
     epoch stamp routed through the SHARED relTime exporter, and the
     <time> title attribute carrying the exact UTC moment
 11. THE ESC CONTRACT against an XSS payload: hostile titles land in
     the feed ESCAPED (all five HTML-specials) — no raw <img/<script
     markup can reach feed.innerHTML — and a garbage ts leaves the
     unescaped time-title attribute EMPTY (its own guard)
 12. THE FEED'S THREE STATES: an empty list renders the honest
     empty-state line, a NULL answer (the 401 after logout, fired
     through the captured 60s poll) CLEARS the previous user's rows,
     and a network rejection renders the failure line

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
  // REAL DOM SEMANTICS: a dispatched event reaches EVERY listener of
  // the type (the old last-listener-only firing hid exactly the
  // multi-listener wiring this drive pins)
  ls.forEach((fn) => fn({ detail }));
}
function dispatchNoDetail(type) {
  const ls = sandbox.document._listeners[type] || [];
  assert.ok(ls.length >= 1, 'a ' + type + ' listener is registered');
  ls.forEach((fn) => fn({}));
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

  if (name === 'announce_flat_shape') {
    // push_client announces WHATEVER envelope it applied — and it is
    // shape-neutral (flat bodies occur end to end). A flat envelope
    // carries updated_at top-level exactly like the nested shape, so
    // the Scout stamp must re-render from it with no fetch.
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [] } } };
    loadNow();
    await flushAll();
    const afterBoot = gets.filter((p) => p === '/api/push/preferences').length;
    dispatch('xmrjackpot:prefs', { jackpot: false, scout: true,
                                   updated_at: '2026-09-26T13:00:00Z' });
    assert.ok(stampText().indexOf('SHARED[2026-09-26T13:00:00Z]') >= 0,
              'the FLAT announcement re-rendered the stamp: ' + stampText());
    assert.strictEqual(stampTitle(), '2026-09-26T13:00:00Z',
                       'the tooltip carries the flat envelope\'s stamp');
    assert.strictEqual(gets.filter((p) => p === '/api/push/preferences').length,
                       afterBoot, 'the flat render fired NO fetch');
    return 'announce_flat_shape ok';
  }

  if (name === 'announce_conflict_envelope') {
    // the 412 auto-adoption announces the SERVER's current envelope:
    // the nested shape PLUS an extra error field. The stamp renders
    // its two fields and ignores the rest.
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [] } } };
    loadNow();
    await flushAll();
    dispatch('xmrjackpot:prefs', {
      user_id: 'alice',
      preferences: { jackpot: true, scout: true },
      updated_at: '2026-09-26T14:00:00Z',
      error: 'version conflict - re-read and retry',
    });
    assert.ok(stampText().indexOf('SHARED[2026-09-26T14:00:00Z]') >= 0,
              'the conflict envelope re-rendered the stamp: ' + stampText());
    assert.strictEqual(stampTitle(), '2026-09-26T14:00:00Z',
                       'the conflict envelope\'s moment is the tooltip');
    assert.ok(stampText().indexOf('version conflict') < 0,
              'the error field never leaks into the stamp text');
    return 'announce_conflict_envelope ok';
  }

  if (name === 'signals_rows_render') {
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [
                  { title: 'Scout fired', ts: 1790000000 },
                  { title: 'Second signal', ts: 1790000060 } ] } } };
    loadNow();
    await flushAll();
    const html = els.wfSignalFeed.innerHTML;
    assert.ok(html.indexOf('wf-feed-row') >= 0 && html.indexOf('<time ') >= 0,
              'rows render as wf-feed-row markup with <time> elements');
    assert.ok(html.indexOf('Scout fired') >= 0 && html.indexOf('Second signal') >= 0
              && html.indexOf('Scout fired') < html.indexOf('Second signal'),
              'both titles render, feed order preserved');
    assert.ok(html.indexOf('SHARED[2026-09-21T14:13:20.000Z]') >= 0,
              'the epoch stamp routes through the SHARED relTime exporter ' +
              '(epoch seconds converted to ISO first): ' + html.slice(0, 160));
    assert.ok(html.indexOf('title="2026-09-21T14:13:20.000Z"') >= 0,
              'the <time> hover carries the exact UTC moment');
    return 'signals_rows_render ok';
  }

  if (name === 'signals_xss_escaping') {
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [
                  { title: '<img src=x onerror=alert(1)>', ts: 1790000000 },
                  { title: '\"<script>alert(1)</script>', ts: 1790000060 },
                  { title: "it's a & < test", ts: 'not-a-number' } ] } } };
    loadNow();
    await flushAll();
    const html = els.wfSignalFeed.innerHTML;
    assert.strictEqual(html.indexOf('<img'), -1, 'no raw <img reaches the feed');
    assert.strictEqual(html.indexOf('<script'), -1, 'no raw <script reaches the feed');
    assert.ok(html.indexOf('&lt;img src=x onerror=alert(1)&gt;') >= 0,
              'the img payload lands ESCAPED');
    assert.ok(html.indexOf('&quot;&lt;script&gt;alert(1)&lt;/script&gt;') >= 0,
              'the quote-wrapped script payload lands ESCAPED (all five specials)');
    assert.ok(html.indexOf("it&#39;s a &amp; &lt; test") >= 0,
              'ampersand and single quote escape too, and a safe-ish string ' +
              'still renders its text');
    assert.ok(html.indexOf('title=""') >= 0,
              'a garbage ts leaves the UNESCAPED time-title attribute EMPTY ' +
              '(its own guard — toISOString is the only raw interpolation)');
    assert.ok(html.indexOf('SHARED[') >= 0,
              'escaped rows still route their stamps through the shared exporter');
    return 'signals_xss_escaping ok';
  }

  if (name === 'signals_empty_and_failure') {
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [
                  { title: 'old user signal', ts: 1790000000 } ] } } };
    loadNow();
    await flushAll();
    assert.ok(els.wfSignalFeed.innerHTML.indexOf('old user signal') >= 0,
              'rows present before the transitions');
    // THE POLL: the harness captured boot's setIntervals — boot order
    // is signals, status, prefs-stamp, so timers[0] fires refreshSignals
    assert.ok(timers.length >= 3, 'the three 60s polls are captured');
    const nGets = () => gets.filter((p) => p === '/api/push/signals').length;
    const fire = () => { const b = nGets(); timers[0][0]();
                         assert.ok(nGets() === b + 1, 'timers[0] IS the ' +
                         'signals poll (boot-order drift would fail here)'); };
    // 1. empty list -> the honest empty-state line
    answers['/api/push/signals'] = { status: 200, json: { signals: [] } };
    fire();
    await flushAll();
    assert.ok(els.wfSignalFeed.innerHTML.indexOf('No signals pushed yet') >= 0,
              'an empty list renders the honest empty-state line');
    // 2. NULL answer (the 401 after logout) CLEARS the previous user's rows
    answers['/api/push/signals'] = { status: 401, json: {} };
    fire();
    await flushAll();
    assert.ok(els.wfSignalFeed.innerHTML.indexOf('old user signal') === -1,
              "a null answer CLEARS the previous user's rows (logout hygiene)");
    assert.ok(els.wfSignalFeed.innerHTML.indexOf('No signals pushed yet') >= 0,
              'the cleared feed shows the empty state, not the stale rows');
    // 3. network rejection -> the failure line
    answers['/api/push/signals'] = null;
    fire();
    await flushAll();
    assert.ok(els.wfSignalFeed.innerHTML.indexOf('Could not load signal history.') >= 0,
              'a network rejection renders the failure line');
    return 'signals_empty_and_failure ok';
  }

  if (name === 'auth_refreshes_status') {
    // THE STATUS ROWS ON AUTH (the 60s-lag fix): the wallet/miner
    // dots must switch subjects the moment xmrjackpot:auth fires —
    // same instant contract the prefs stamp already has.
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: {
                  authenticated: true, wallet_address: '4' + 'a' * 94 } },
                '/api/profile/stats': { status: 200, json: {
                  miner: { hashrate: 123, shares_valid: 5 } } },
                '/api/push/signals': { status: 200, json: { signals: [] } } };
    loadNow();
    await flushAll();
    assert.strictEqual(els.wfWalletStatus.textContent,
                       'Connected (read-only)',
                       "boot rendered alice's wallet row");
    assert.strictEqual(els.wfDotWallet.className, 'wf-dot on',
                       'the wallet dot is ON for a connected wallet');
    assert.ok(els.wfMinerStatus.textContent.indexOf('On') === 0,
              'the miner row shows active for a hashing miner: '
              + els.wfMinerStatus.textContent);
    // THE SUBJECT SWITCH: the next auth/me answers bob (no wallet,
    // not mining) — the auth event must refetch and re-render NOW
    const nAuthMe = () => gets.filter((p) => p === '/api/auth/me').length;
    const before = nAuthMe();
    answers['/api/auth/me'] = { status: 200, json: {
      authenticated: true, wallet_address: '' } };
    answers['/api/profile/stats'] = { status: 200, json: {
      miner: { hashrate: 0, shares_valid: 0 } } };
    dispatchNoDetail('xmrjackpot:auth');
    await flushAll();
    assert.strictEqual(nAuthMe(), before + 1,
                       'the auth event refetched /api/auth/me instantly');
    assert.strictEqual(els.wfWalletStatus.textContent, 'One-directional',
                       'the wallet row switched subjects INSTANTLY ' +
                       '(no 60s lag)');
    assert.strictEqual(els.wfDotWallet.className, 'wf-dot',
                       'the wallet dot went OFF for the wallet-less subject');
    assert.strictEqual(els.wfMinerStatus.textContent, 'Off',
                       'the miner row switched too');
    return 'auth_refreshes_status ok';
  }

  if (name === 'announce_without_detail') {
    // a detail-less announcement (defensive: push_client wraps the
    // dispatch in try/catch, but a malformed event must never clobber
    // the card) is a NO-OP — the guard drops it, the stamp survives.
    answers = { '/api/push/preferences': { status: 200, json: ENV_A },
                '/api/auth/me': { status: 200, json: { authenticated: false } },
                '/api/profile/stats': { status: 401, json: {} },
                '/api/push/signals': { status: 200, json: { signals: [] } } };
    loadNow();
    await flushAll();
    assert.ok(stampText().indexOf('SHARED[' + ENV_A.updated_at + ']') >= 0,
              'stamp present before the malformed announcement');
    dispatchNoDetail('xmrjackpot:prefs');
    assert.ok(stampText().indexOf('SHARED[' + ENV_A.updated_at + ']') >= 0,
              'the previous stamp SURVIVES an envelope-less announcement');
    assert.strictEqual(stampTitle(), ENV_A.updated_at,
                       'the tooltip survives with it');
    return 'announce_without_detail ok';
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
    ("5", "announce_flat_shape",
     "a FLAT announcement (shape-neutral client) re-renders the stamp with no fetch"),
    ("6", "announce_conflict_envelope",
     "the 412-conflict envelope (extra error field) re-renders; extras ignored"),
    ("7", "announce_without_detail",
     "an envelope-less announcement is a no-op — the previous stamp survives"),
    ("8", "signals_rows_render",
     "feed rows render in order, epoch stamps through the SHARED exporter, exact UTC hover"),
    ("9", "signals_xss_escaping",
     "hostile titles land ESCAPED (five specials), no raw markup reaches innerHTML; garbage ts leaves the raw time-title empty"),
    ("10", "signals_empty_and_failure",
     "empty list renders the empty line; a null answer clears the prior user's rows; a network rejection renders the failure line"),
    ("11", "auth_refreshes_status",
     "xmrjackpot:auth refetches and re-renders the wallet/miner dots instantly — no 60s subject lag"),
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
