"""test_push_client_envelope.py — THE CLIENT ENVELOPE CONTRACT, EXECUTED
(Sep 26). Also pins the SHARED CLOCK: relTime (the project's one
relative-time renderer) buckets, including the older-than-a-week
absolute-date fallback.

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
  6. THE N-STEP HISTORY: after a write the link is "undo"; clicking it
     applies the other state and the link becomes "redo"; clicking
     THAT applies again and the link is "undo" once more — every step
     riding the version its predecessor applied
  7. a fresh write RE-POINTS the history (the redo of an older toggle
     is voided): the new undo replays the state the last write
     replaced, and it in turn offers "redo"
 13. THE N-STEP WALK (Sep 26 upgrade): mid-walk BOTH links render
     SIMULTANEOUSLY ('undo · redo'); the second undo walks DEEPER to
     the state two writes back (the 2-step history could never); the
     walk's bottom is INERT (no POST past empty); a redo restores
     both links; all writes ride their predecessor versions
 14. THE CAP: MAX_HISTORY bounds the stack — after 22 fresh writes
     exactly 20 undo steps may POST, then the link is gone (the two
     oldest replaced states fell off the bottom)
  8. CROSS-ACCOUNT SAFETY: after xmrjackpot:auth (logout or switch),
     the cleared history is INERT — a lingering undo link can no
     longer replay the previous account's state, and the next write
     rides the version the re-read captured
  9. THE UNDO WRITE ITSELF LOSING THE RACE: an undo that gets 412
     adopts the OTHER device's state (never the undo target), offers
     no link, and the next write rides the conflict's version
 10. the shared clock's buckets, including the older-than-a-week
     absolute fallback
 11. THE SAVE-FAILURE PATH: a 500 on a toggle leaves the attempted
     values on screen, offers NO undo (there is no new confirmed
     state), says the save failed and will retry, and does not adopt
     the error body
 12. THE REENTRANCY GUARD: two changes in one tick fire exactly ONE
     write (one in flight; last state wins on screen)

EVERY scenario's success is gated on the EXECUTED-SOURCE check: the
harness proves the file it ran carries an in-source client marker, so
a mutated or swapped copy fails the drive instead of green-checking a
fiction. No network, no browser: Node is required, everything else is
stubbed.
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

# THE IN-SOURCE CLIENT MARKER: the harness refuses to green-check a
# file that does not carry it (see the JS side of the harness). If a
# deliberate edit ever removes that comment from push_client.js,
# update BOTH this constant and the JS one in the same commit.
CLIENT_MARKER = "SHAPE FIX (Sep 26"

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
    _text: '',
    title: '',
    style: {},
    dataset: {},
    children: [],
    listeners: {},
    appendChild(c) { this.children.push(c); },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
  };
  // DOM-faithful textContent: assigning it REPLACES all children (a
  // plain data property would keep stale anchors around and lie
  // about whether the undo/redo link is still on screen)
  Object.defineProperty(e, 'textContent', {
    get() { return this._text; },
    set(v) { this._text = String(v); this.children.length = 0; },
    configurable: true,
  });
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
// THE EXECUTED-SOURCE CHECK: the drive must run the client the repo
// ships. If this marker is missing from the loaded file, the harness
// is executing a mutated or stale copy — fail loudly (exit 3), never
// green-check a fiction. The Python side asserts the marker exists in
// the repo file; update BOTH deliberately if adopt() is ever reworded.
const CLIENT_MARKER = 'SHAPE FIX (Sep 26';
const executedRealClient = src.includes(CLIENT_MARKER);
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
function stampLink() {
  // the saved line renders [text node, anchor(s)] — with a SINGLE
  // history link it is the last child; assert it exists and return it
  const a = els.prefSaved.children[els.prefSaved.children.length - 1];
  assert(a && a.listeners.click && a.listeners.click.length === 1,
         'a history link is rendered on the saved line');
  return a;
}
function clickStampLink() {
  stampLink().listeners.click[0]({ preventDefault: function () {} });
}
function linksText() {
  // EVERY rendered history link, in DOM order — the N-step walk
  // renders undo and redo SIMULTANEOUSLY, so the line is 'undo,redo'
  return els.prefSaved.children
    .map((c) => c.textContent).filter(Boolean).join(',');
}
function clickLinkNamed(txt) {
  const a = els.prefSaved.children.find((c) => c.textContent === txt);
  assert.ok(a, 'a "' + txt + '" link is rendered on the saved line');
  a.listeners.click[0]({ preventDefault: function () {} });
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

  if (name === 'undo_then_redo') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    // a toggle applies -> the saved line offers UNDO
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:01Z' } };
    fireChange('prefScout', false);
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: false },
                           'toggle A applied');
    assert.strictEqual(stampLink().textContent, 'undo',
                       'after a fresh write the link is undo');
    // UNDO: replays the state the write replaced, protected by the
    // same If-Match machinery (it rides the version toggle A applied)
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: true },
      updated_at: '2026-09-26T10:00:02Z' } };
    clickStampLink();
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: true },
                           'the undo applied the other state');
    assert.strictEqual(stampLink().textContent, 'redo',
                       'after an undo the link is REDO (the 2-step history)');
    // REDO: applies the undone state again -> the link flips back
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:03Z' } };
    clickStampLink();
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: false },
                           'the redo applied the undone state');
    assert.strictEqual(stampLink().textContent, 'undo',
                       'after a redo the link is undo again (a redo write is not an undo)');
    const posts = sentRequests.filter((r) => r.method === 'POST');
    assert.deepStrictEqual(
      posts.map((p) => p.headers['If-Match']),
      ['"2026-09-26T10:00:00Z"', '"2026-09-26T10:00:01Z"', '"2026-09-26T10:00:02Z"'],
      'toggle, undo, and redo each ride the version their predecessor applied');
    assert.deepStrictEqual(posts[1].body, { preferences: { jackpot: false, scout: true } },
                           'the undo POSTS the pre-toggle state');
    assert.deepStrictEqual(posts[2].body, { preferences: { jackpot: false, scout: false } },
                           'the redo POSTS the undone state');
    return 'undo_then_redo ok';
  }

  if (name === 'fresh_write_voids_redo') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:01Z' } };
    fireChange('prefScout', false);
    await flushAll();
    assert.strictEqual(stampLink().textContent, 'undo',
                       'toggle A applied: undo offered');
    // a FRESH write re-points the history: the target becomes the
    // state THIS write replaced (toggle A's result), so there is no
    // redo of anything older
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: true, scout: false },
      updated_at: '2026-09-26T10:00:02Z' } };
    fireChange('prefJackpot', true);
    await flushAll();
    assert.strictEqual(stampLink().textContent, 'undo',
                       'after a fresh write the link is undo, never a stale redo');
    // its undo replays toggle A's state and offers REDO again
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:03Z' } };
    clickStampLink();
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: false },
                           "the undo replayed the state the last write replaced");
    assert.strictEqual(stampLink().textContent, 'redo',
                       'that undo offers redo');
    const posts = sentRequests.filter((r) => r.method === 'POST');
    assert.deepStrictEqual(posts[2].body, { preferences: { jackpot: false, scout: false } },
                           "the new undo targets the last write's replaced state, not an older step");
    return 'fresh_write_voids_redo ok';
  }

  if (name === 'auth_clears_history') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:01Z' } };
    fireChange('prefScout', false);
    await flushAll();
    const postsBefore = sentRequests.filter((r) => r.method === 'POST').length;
    // ACCOUNT SWITCH (logout or login-as-other): the auth handler
    // clears prevPrefs/lastConfirmed and re-reads. The re-read answers
    // bob's fresh envelope (updated_at null, ETag "v0").
    script = { status: 200, headers: { ETag: '"v0"' }, json: {
      user_id: 'bob', preferences: { jackpot: true, scout: true },
      updated_at: null } };
    sandbox.document._listeners['xmrjackpot:auth'][
      sandbox.document._listeners['xmrjackpot:auth'].length - 1]({});
    await flushAll();
    // any undo link still on screen is DEAD: clicking it must send
    // nothing — alice's state must never replay into bob's session
    if (els.prefSaved.children.length) {
      els.prefSaved.children[els.prefSaved.children.length - 1]
        .listeners.click[0]({ preventDefault: function () {} });
      await flushAll();
    }
    assert.strictEqual(sentRequests.filter((r) => r.method === 'POST').length, postsBefore,
                       'a stale undo link after an account switch is inert');
    // bob's first toggle rides the version the re-read captured
    script = { status: 200, headers: {}, json: {
      user_id: 'bob', preferences: { jackpot: false, scout: true },
      updated_at: '2026-09-27T10:00:00Z' } };
    fireChange('prefScout', false);
    await flushAll();
    const posts = sentRequests.filter((r) => r.method === 'POST');
    assert.strictEqual(posts[posts.length - 1].headers['If-Match'], '"v0"',
                       "the next write rides the re-read version, not alice's last one");
    return 'auth_clears_history ok';
  }

  if (name === 'reltime_buckets') {
    // THE SHARED CLOCK itself, executed: relTime is the one renderer
    // every surface must route through after the sweep (Winners list,
    // claim history, admin last-seen, audit log).
    loadClient();                            // the export appears at load
    await flushAll();
    const rt = sandbox.window.XMRJackpotPush.relTime;
    const now = Date.now();
    const isoAgo = (s) => new Date(now - s * 1000).toISOString();
    sandbox.Date.now = () => now;            // pin the clock for buckets
    const cases = [
      [isoAgo(2), 'just now'],
      [isoAgo(30), '30s ago'],
      [isoAgo(125), '2 min ago'],
      [isoAgo(7200), '2 h ago'],
      [isoAgo(3 * 86400), '3 d ago'],
    ];
    for (const [iso, want] of cases) {
      assert.strictEqual(rt(iso), want, 'bucket for ' + iso);
    }
    // older than a week: absolute date, never "365 d ago"
    const old = rt(isoAgo(365 * 86400));
    assert.ok(!/\d+ d ago/.test(old), 'a year-old stamp never reads "365 d ago": ' + old);
    assert.ok(old.length > 0, 'the absolute fallback renders something');
    sandbox.Date.now = Date.now;             // restore
    return 'reltime_buckets ok';
  }

  if (name === 'undo_conflict') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:01Z' } };
    fireChange('prefScout', false);
    await flushAll();
    assert.strictEqual(stampLink().textContent, 'undo',
                       'undo offered after the write');
    // ANOTHER DEVICE advances the server before our undo lands: the
    // UNDO WRITE ITSELF loses the race
    script = { status: 412, headers: { ETag: '"2026-09-26T10:00:07Z"' }, json: {
      user_id: 'alice', preferences: { jackpot: true, scout: false },
      updated_at: '2026-09-26T10:00:07Z',
      error: 'version conflict - re-read and retry',
    } };
    clickStampLink();
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: true, scout: false },
                           "the undo's 412 adopts the OTHER device's state, never the undo target");
    assert.strictEqual(els.prefSaved.children.length, 0,
                       'no undo/redo link after the conflict (our write never applied)');
    assert.ok(els.prefSaved.textContent.indexOf('another device changed these') === 0,
              'the conflict verdict renders: ' + els.prefSaved.textContent);
    // and the next write rides the version the conflict adoption captured
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: true, scout: true },
      updated_at: '2026-09-27T10:00:00Z' } };
    fireChange('prefScout', true);
    await flushAll();
    const posts = sentRequests.filter((r) => r.method === 'POST');
    assert.strictEqual(posts[posts.length - 1].headers['If-Match'], '"2026-09-26T10:00:07Z"',
                       'the next write rides the conflict version');
    assert.deepStrictEqual(checkboxState(), { jackpot: true, scout: true },
                           'the follow-up write adopts its 200 body');
    return 'undo_conflict ok';
  }

  if (name === 'save_failure') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    script = { status: 500, headers: {}, json: { detail: 'boom' } };
    fireChange('prefScout', false);
    await flushAll();
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: false },
                           'the attempted values stand (the screen keeps the user\'s change)');
    assert.strictEqual(els.prefSaved.children.length, 0,
                       'no undo after a failed save (there is no new confirmed state)');
    assert.ok(els.prefSaved.textContent.indexOf('save failed') === 0,
              'the failure verdict renders: ' + els.prefSaved.textContent);
    assert.strictEqual(els.prefSaved.title, '',
                       'nothing was adopted: the failure message carries no stamp, '
                       + 'the error body and the old version alike are gone from the line');
    // the retry: the next toggle fires the write again with the SAME
    // held version (the failed write consumed nothing)
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: true },
      updated_at: '2026-09-26T10:00:09Z' } };
    fireChange('prefScout', true);
    await flushAll();
    const posts = sentRequests.filter((r) => r.method === 'POST');
    assert.strictEqual(posts[1].headers['If-Match'], '"2026-09-26T10:00:00Z"',
                       'the retry rides the same version (a failed write never moved it)');
    assert.deepStrictEqual(checkboxState(), { jackpot: false, scout: true },
                           'the successful write adopts its 200 body');
    return 'save_failure ok';
  }

  if (name === 'reentrancy_guard') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:01Z' } };
    // two changes in ONE tick (the impatient double-click): the guard
    // lets the first write fly and refuses the second until it lands
    fireChange('prefScout', false);
    fireChange('prefJackpot', false);
    await flushAll();
    const posts = sentRequests.filter((r) => r.method === 'POST');
    assert.strictEqual(posts.length, 1,
                       `exactly one write for two same-tick changes (got ${posts.length})`);
    assert.deepStrictEqual(posts[0].body, { preferences: { jackpot: false, scout: false } },
                           'the in-flight write carries the screen state at fire time');
    return 'reentrancy_guard ok';
  }

  if (name === 'nstep_walk') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    // two fresh writes build a 2-deep undo stack
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:01Z' } };
    fireChange('prefScout', false);
    await flushAll();
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: true, scout: false },
      updated_at: '2026-09-26T10:00:02Z' } };
    fireChange('prefJackpot', true);
    await flushAll();
    assert.strictEqual(stampLink().textContent, 'undo',
                       'after two fresh writes the newest link is undo');
    // UNDO #1: walks back one step; BOTH links render simultaneously
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:03Z' } };
    clickLinkNamed('undo');
    await flushAll();
    assert.strictEqual(linksText(), 'undo,redo',
                       'mid-walk BOTH links render simultaneously: ' + linksText());
    const posts = () => sentRequests.filter((r) => r.method === 'POST');
    assert.deepStrictEqual(posts()[posts().length - 1].body,
      { preferences: { jackpot: false, scout: false } },
      'the first undo replayed the last replaced state');
    // UNDO #2: walks DEEPER — the state TWO writes back (the GET
    // state); the old 2-step history could never do this
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: true },
      updated_at: '2026-09-26T10:00:04Z' } };
    clickLinkNamed('undo');
    await flushAll();
    assert.deepStrictEqual(posts()[posts().length - 1].body,
      { preferences: { jackpot: false, scout: true } },
      'the second undo walks DEEPER to the GET state (N-step depth)');
    assert.strictEqual(linksText(), 'redo',
                       'the walk hit the bottom: only redo remains');
    // an undo click at the bottom is INERT: no POST past empty
    const before = posts().length;
    const strayUndo = els.prefSaved.children.find(
      (c) => c.textContent === 'undo');
    if (strayUndo) {
      strayUndo.listeners.click[0]({ preventDefault: function () {} });
      await flushAll();
    }
    assert.strictEqual(posts().length, before, 'no undo POST past the bottom');
    // REDO: walks forward again, and BOTH links return
    script = { status: 200, headers: {}, json: {
      user_id: 'alice', preferences: { jackpot: false, scout: false },
      updated_at: '2026-09-26T10:00:05Z' } };
    clickLinkNamed('redo');
    await flushAll();
    assert.strictEqual(linksText(), 'undo,redo',
                       'after a redo both links render again');
    assert.deepStrictEqual(posts()[posts().length - 1].body,
      { preferences: { jackpot: false, scout: false } },
      'the redo replayed the undone state');
    // every write rode the version its predecessor applied
    assert.deepStrictEqual(
      posts().map((p) => p.headers['If-Match']),
      ['"2026-09-26T10:00:00Z"', '"2026-09-26T10:00:01Z"', '"2026-09-26T10:00:02Z"',
       '"2026-09-26T10:00:03Z"', '"2026-09-26T10:00:04Z"'],
      'all five writes ride their predecessor versions in order');
    return 'nstep_walk ok';
  }

  if (name === 'history_cap') {
    script = { status: 200, headers: { ETag: '"2026-09-26T10:00:00Z"' }, json: ENVELOPE };
    loadClient();
    await flushAll();
    // 22 fresh writes: the stack is capped at MAX_HISTORY — the two
    // oldest replaced states fall off the bottom
    for (let k = 0; k < 22; k++) {
      const scoutNow = (k % 2 === 0) ? false : true;
      script = { status: 200, headers: {}, json: {
        user_id: 'alice', preferences: { jackpot: false, scout: scoutNow },
        updated_at: '2026-09-26T11:00:' + String(k).padStart(2, '0') + 'Z' } };
      fireChange('prefScout', scoutNow);
      await flushAll(4);
    }
    const posts = () => sentRequests.filter((r) => r.method === 'POST');
    assert.strictEqual(posts().length, 22, 'all 22 writes fired');
    // walk back until the stack runs dry: exactly 20 undos may POST
    let walked = 0;
    for (let j = 0; j < 30; j++) {
      const undoLink = els.prefSaved.children.find(
        (c) => c.textContent === 'undo');
      if (!undoLink) { break; }
      const b = posts().length;
      script = { status: 200, headers: {}, json: {
        user_id: 'alice', preferences: { jackpot: false, scout: false },
        updated_at: '2026-09-26T12:'
          + String(Math.floor(walked / 60)).padStart(2, '0') + ':'
          + String(walked % 60).padStart(2, '0') + 'Z' } };
      undoLink.listeners.click[0]({ preventDefault: function () {} });
      await flushAll(4);
      if (posts().length === b) { break; }     // inert at the bottom
      walked += 1;
    }
    assert.strictEqual(walked, 20,
      'the walk goes exactly MAX_HISTORY deep (got ' + walked + ')');
    assert.strictEqual(linksText(), 'redo',
      'at the bottom the undo link is gone; redo still offers the way back');
    return 'history_cap ok';
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
  (ok) => {
    if (!executedRealClient) {
      console.error('EXECUTED-SOURCE CHECK FAILED: the loaded '
        + 'push_client.js lacked the in-source client marker ('
        + 'SHAPE FIX (Sep 26) — the drive is not running the '
        + 'shipped client');
      process.exit(3);
    }
    console.log(ok);
    process.exit(0);
  },
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
    ("6", "undo_then_redo",
     "undo link applies the other state and becomes redo; redo applies again and becomes undo; each step rides its predecessor's version"),
    ("7", "fresh_write_voids_redo",
     "a fresh write re-points the history (no stale redo); its undo replays the last replaced state and offers redo"),
    ("8", "auth_clears_history",
     "after an account switch the cleared history is inert and the next write rides the re-read version"),
    ("9", "reltime_buckets",
     "the shared clock's buckets, including the older-than-a-week absolute fallback"),
    ("10", "undo_conflict",
     "an undo that loses the race adopts the other device's state, offers no link, and the next write rides the conflict version"),
    ("11", "save_failure",
     "a 500 leaves the attempted values, offers no undo, says it will retry, and never adopts the error body"),
    ("12", "reentrancy_guard",
     "two same-tick changes fire exactly one write; the in-flight payload is the first change"),
    ("13", "nstep_walk",
     "the N-step walk: simultaneous undo+redo links, depth past two, inert bottom, redo restores both"),
    ("14", "history_cap",
     "MAX_HISTORY caps the stack: 22 writes, exactly 20 undo steps, then inert"),
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


check("0", CLIENT_MARKER in CLIENT.read_text(encoding="utf-8"),
      "the shipped client carries the executed-source marker — update "
      "CLIENT_MARKER here and in the harness if adopt() was reworded")

for label, scenario, why in SCENARIOS:
    proc = run_scenario(scenario)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    ok = proc.returncode == 0
    check(f"{label}", ok, f"node exit {proc.returncode} — {err[:200] or out[:200]}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
