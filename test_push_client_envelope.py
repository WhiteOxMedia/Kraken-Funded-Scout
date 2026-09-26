"""test_push_client_envelope.py — THE CLIENT ENVELOPE CONTRACT, EXECUTED
(Sep 26).

push_client.js is the only JS that turns server envelopes into UI
state, and its history is the sync-shape bug class: adopt() once read
p.jackpot off the ENVELOPE instead of p.preferences.jackpot, and every
adopt snapped both checkboxes ON regardless of the server's state. A
Python re-implementation of adopt() would not catch the next such bug —
this drive EXECUTES the real pool/static/push_client.js in Node with a
stubbed DOM + fetch and asserts the checkbox outcomes:

  1. GET envelope {jackpot:false,scout:true} -> checkboxes OFF/ON,
     ETag captured and ridden as If-Match on the next toggle POST,
     200 body adoption
  2. 412 conflict body (nested) -> the client ADOPTS the server's
     version: checkboxes snap to the 412's preferences, the stamp
     tooltip moves to the 412's updated_at, the "another device"
     verdict renders, and NO undo link is offered (our write never
     applied)
  3. the sync-shape regression class: flat and nested bodies both
     adopt their real values (never the both-ON artifact)
  4. the xmrjackpot:prefs announcement carries the full envelope
  5. after a 412, the next write rides the CONFLICT's version and its
     200 body is adopted

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
CLIENT = HERE / "pool" / "static" / "push_client.js"

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


node = shutil.which("node")
if not node or not CLIENT.exists():
    print("SKIP: node not available or push_client.js missing — "
          "the fleet stays green; install Node to run this drive")
    print("RESULT: 0 passed, 0 failed")
    sys.exit(0)

# ── THE HARNESS: minimal DOM + fetch stubs, then the REAL client ────
# The client is an IIFE that wires #prefJackpot/#prefScout and calls
# readServer() the moment it loads, so each scenario PRE-SCRIPTS the
# GET response, then loads the client (loadClient). `script` is the
# currently scripted response; assertions read the stub elements.
HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const prefsBox = { jackpot: true, scout: true };   // browser default: ON
const dispatched = [];
const sentRequests = [];
let script = null;             // the currently scripted fetch response

function el(id) {
  const e = {
    id,
    textContent: '',
    title: '',
    style: {},
    dataset: {},
    children: [],
    listeners: {},
    appendChild(c) { this.children.push(c); },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
  };
  Object.defineProperty(e, 'checked', {
    get() { return prefsBox[id === 'prefJackpot' ? 'jackpot' : 'scout']; },
    set(v) { prefsBox[id === 'prefJackpot' ? 'jackpot' : 'scout'] = !!v; },
    configurable: true,
  });
  return e;
}
const els = {
  btnEnablePush: el('btnEnablePush'),   // wire() bails without it — the
  // whole prefs wiring hangs off this element's presence
  prefJackpot: el('prefJackpot'),
  prefScout: el('prefScout'),
  prefSaved: el('prefSaved'),
};
els.prefJackpot.checked = true;
els.prefScout.checked = true;

const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  Date, Math, JSON, Promise, Uint8Array, Object, Array, String, Number, Boolean, Error,
  document: {
    readyState: 'complete',
    getElementById: (id) => els[id] || null,
    createElement: (tag) => el('_' + tag + Math.random()),
    createTextNode: (t) => ({ text: t }),
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

sandbox.fetch = function (path, opts) {
  opts = opts || {};
  const method = (opts.method || 'GET').toUpperCase();
  const headers = opts.headers || {};
  const body = opts.body ? JSON.parse(opts.body) : null;
  sentRequests.push({ method, path, headers, body });
  const resp = script || { status: 200, headers: {}, json: {} };
  return Promise.resolve({
    ok: resp.status >= 200 && resp.status < 300,
    status: resp.status,
    headers: { get: (n) => (resp.headers || {})[n]
               || (resp.headers || {})[String(n).toLowerCase()] || null },
    json: () => Promise.resolve(resp.json),
  });
};
sandbox.window = { atob: (s) => Buffer.from(s, 'base64').toString('binary') };
sandbox.navigator = { serviceWorker: undefined, PushManager: undefined, Notification: undefined };

const ctx = vm.createContext(sandbox);
const src = fs.readFileSync(process.env.CLIENT_PATH, 'utf8');
function loadClient() {
  vm.runInContext(src, ctx, { filename: 'push_client.js' });
}

function flush() {
  return new Promise((res) => { setTimeout(() => res(), 5); });
}
function flushAll(times) {
  let p = Promise.resolve();
  for (let i = 0; i < (times || 6); i++) { p = p.then(flush); }
  return p;
}
function checkboxState() {
  return { jackpot: prefsBox.jackpot, scout: prefsBox.scout };
}
function fireChange(box, value) {
  // browser-realistic: a click FLIPS the checkbox, then change fires —
  // postBulk reads the screen state at fire time
  const e = els[box];
  assert(e && e.listeners.change && e.listeners.change.length === 1,
         'toggle is wired exactly once');
  if (value !== undefined) { e.checked = value; }
  e.listeners.change[0]();
}

const ENVELOPE = {
  user_id: 'alice',
  preferences: { jackpot: false, scout: true },
  updated_at: '2026-09-26T10:00:00Z',
};

async function scenario(name) {
  if (name === 'get_envelope') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: true },
                           'GET envelope drives checkboxes OFF/ON');
    // toggle -> the captured version must ride the If-Match
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:01Z' } };
    fireChange('prefScout', false);
    await flushAll();
    const post = sentRequests.filter((r) => r.method === 'POST')[0];
    assert.strictEqual(post.headers['If-Match'], '"2026-09-26T10:00:00Z"',
                       "the GET's ETag rides the next POST as If-Match");
    assert.deepStrictEqual(post.body, { preferences: { jackpot: false, scout: false } },
                           'the toggle POSTs the FULL bulk envelope');
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: false },
                           '200 body adoption drives the checkboxes');
    return 'get_envelope ok';
  }

  if (name === 'conflict_412') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    // the conflict body's values are deliberately BOTH-OFF: the
    // historical sync-shape bug snapped every adopt to both-ON, so a
    // {T,T} body could never tell correct adoption from the artifact
    script = { status: 412, headers: { ETag: '"2026-09-26T10:00:05Z"' }, json: {
      user_id: 'alice',
      preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:05Z',
      error: 'version conflict - re-read and retry',
    } };
    fireChange('prefJackpot');               // our write lost the race
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: false },
                           'the 412 body IS adopted (checkboxes follow the server)');
    assert.ok(els.prefSaved.title.indexOf('2026-09-26T10:00:05Z') === 0,
              'the 412 stamp is adopted: ' + els.prefSaved.title);
    assert.ok(els.prefSaved.textContent.indexOf('another device changed these') === 0,
              'the conflict verdict renders: ' + els.prefSaved.textContent);
    assert.strictEqual(els.prefSaved.children.length, 0,
                       'no undo link after a conflict (our write never applied)');
    return 'conflict_412 ok';
  }

  if (name === 'shape_neutral') {
    // THE SYNC-SHAPE REGRESSION CLASS: nested body -> values come from
    // .preferences; a flat body adopts its own values; a later nested
    // body with DIFFERENT values is not flattened away. The historical
    // bug read p.jackpot off the envelope -> undefined !== false -> a
    // both-ON snap after any sync.
    script = { status: 200, headers: { ETag: '"v"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: true },
                           'nested body: values come from .preferences');
    script = { status: 200, headers: {}, json: {
      jackpot: false, scout: false, updated_at: '2026-09-26T10:00:09Z' } };
    fireChange('prefScout');
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: false },
                           'flat body: values are read off the body itself');
    script = { status: 200, headers: {}, json: {
      preferences: { jackpot: true, scout: false },
      updated_at: '2026-09-26T10:00:10Z' } };
    fireChange('prefScout');
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: true, scout: false },
                           'nested body with different values wins');
    return 'shape_neutral ok';
  }

  if (name === 'announcement') {
    script = { status: 200, headers: { ETag: '"v"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    assert.ok(dispatched.some((ev) => ev.type === 'xmrjackpot:prefs'
                              && ev.detail && ev.detail.updated_at === ENVELOPE.updated_at
                              && ev.detail.preferences
                              && ev.detail.preferences.jackpot === false),
              'the xmrjackpot:prefs announcement carries the full envelope');
    return 'announcement ok';
  }

  if (name === 'retry_after_412') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    script = { status: 412, headers: { ETag: '"2026-09-26T10:00:05Z"' }, json: {
      user_id: 'alice',
      preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:05Z',
      error: 'version conflict - re-read and retry',
    } };
    fireChange('prefJackpot', true);
    await flushAll();
    // the held version must now be the 412's: the next toggle rides it
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: true, scout: false },
      updated_at: '2026-09-27T10:00:00Z' } };
    fireChange('prefScout', false);
    await flushAll();
    const posts = sentRequests.filter((r) => r.method === 'POST');
    assert.strictEqual(posts[1].headers['If-Match'], '"2026-09-26T10:00:05Z"',
                       "after a 412 the next write rides the CONFLICT's version");
    assert.deepStrictEqual(checkboxState(), { jackpot: true, scout: false },
                           'the retried write adopts the 200 body');
    return 'retry_after_412 ok';
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
    ("1", "get_envelope",
     "GET envelope -> checkboxes OFF/ON; the captured ETag rides the next POST; 200 body adoption"),
    ("2", "conflict_412",
     "412 body IS adopted: checkboxes snap to the server's version, verdict renders, no undo offered"),
    ("3", "shape_neutral",
     "the sync-shape regression class: flat and nested bodies adopt their real values, never both-ON"),
    ("4", "announcement",
     "the xmrjackpot:prefs announcement carries the full envelope"),
    ("5", "retry_after_412",
     "after a 412, the next write rides the CONFLICT's version and its 200 body is adopted"),
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
