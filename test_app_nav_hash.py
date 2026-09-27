"""test_app_nav_hash.py — THE URL/PAGE AGREEMENT DRIVE (Sep 27).

Review triage (batch ebf73e75): opening /#admin and then picking Home
left #admin in the URL, so a reload resurrected the Admin page; and
/winners redirected to a hash route the client did not accept (fixed
Sep 25 evening-V by SPA_SYNONYMS, unpinned until now). This drive pins
BOTH halves of the contract:

  1-2. THE REAL app.js UNDER NODE (executed, not needle-matched): a
       stubbed DOM/history/localStorage sandbox boots the actual file
       and proves the URL hash agrees with the visible page in both
       directions - boot honors a deep link, navigateTo writes the
       hash (replaceState, no history spam), Home cleans a stale hash
       back to the bare /, the hashchange listener reroutes on
       back/forward, the currentPage guard stops double-writes, and an
       unknown hash is ignored, not crashed on. navigateTo must be
       exported on window (the injected stale-miner banner's inline
       onclick resolves there).
  3.   THE SPA ROUTES (FastAPI, hermetic TestClient): /winners maps to
       /#leaderboard (the synonym the review flagged), every real page
       path redirects to its own hash, unknown paths serve the app
       shell - so the redirects can never point at a hash route the
       client would not honor again.

Node is required for sections 1-2 (honest SKIP without it); section 3
is pure Python. Prints "RESULT: N passed, M failed".
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENT = HERE / "pool" / "static" / "app.js"

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


node = shutil.which("node")
if not node or not CLIENT.exists():
    print("SKIP: node not available or app.js missing - "
          "the fleet stays green; install Node to run sections 1-2")
else:
    # ── THE HARNESS: stubbed DOM + history + fetch, then the REAL app.js ──
    HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
// app.js boots every page's fetch chain against 401 stubs; chains that
// lack a .catch must not crash the scenario (their assertions here do
// not depend on them).
process.on('unhandledRejection', () => {});

const PAGES = ['landing', 'signup', 'howto', 'realplay', 'leaderboard',
               'dashboard', 'admin'];

function el(id) {
  const e = { id, textContent: '', innerHTML: '', className: '', title: '',
              style: {}, dataset: {}, children: [], listeners: {}, _cl: [] };
  e.classList = {
    add(c) { if (!e._cl.includes(c)) e._cl.push(c); },
    remove(c) { const i = e._cl.indexOf(c); if (i >= 0) e._cl.splice(i, 1); },
    contains(c) { return e._cl.includes(c); },
  };
  e.addEventListener = (t, fn) => {
    (e.listeners[t] = e.listeners[t] || []).push(fn);
  };
  e.appendChild = (c) => e.children.push(c);
  return e;
}

const els = {};
for (const p of PAGES) els['page-' + p] = el('page-' + p);
els.modeBadge = el('modeBadge');
const navStubs = PAGES.map((p) => { const n = el('nav-' + p); n.dataset.page = p; return n; });

const historyCalls = [];          // every URL handed to replaceState
const loc = { hash: '', origin: 'http://localhost:3094', pathname: '/', search: '' };
const winListeners = {};
const win = {
  location: loc,
  history: {
    replaceState(s, t, u) {
      historyCalls.push(String(u));
      loc.hash = String(u).indexOf('#') >= 0
        ? '#' + String(u).split('#')[1] : '';
    },
  },
  scrollTo: () => {},
  addEventListener(t, fn) { (winListeners[t] = winListeners[t] || []).push(fn); },
};

const storage = {};
const docListeners = {};
const documentStub = {
  readyState: 'complete',
  title: '',
  getElementById: (id) => els[id] || el(id),
  querySelector: (sel) => (typeof sel === 'string' && sel.charAt(0) === '#')
    ? (els[sel.slice(1)] || (els[sel.slice(1)] = el(sel.slice(1))))
    : el('adhoc'),
  querySelectorAll: (sel) => (sel === '.page'
    ? PAGES.map((p) => els['page-' + p])
    : navStubs.slice()),
  addEventListener(t, fn) { (docListeners[t] = docListeners[t] || []).push(fn); },
  dispatchEvent: () => true,
  body: el('body'),
};

const sandbox = {
  console, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {},
  Date, Math, JSON, Promise, Object, Array, String, Number, Boolean, Error,
  isNaN, parseInt, parseFloat,
  document: documentStub,
  window: win,
  location: loc,
  localStorage: {
    getItem: (k) => (k in storage ? storage[k] : null),
    setItem: (k, v) => { storage[k] = String(v); },
    removeItem: (k) => { delete storage[k]; },
  },
  fetch: (path) => {
    return Promise.resolve({ ok: false, status: 401,
                             json: () => Promise.resolve({}) });
  },
  CustomEvent: function (t, o) { this.type = t; this.detail = o && o.detail; },
  URLSearchParams: class { constructor() {} get() { return null; } },
};
win.localStorage = sandbox.localStorage;
sandbox.globalThis = sandbox;

const ctx = vm.createContext(sandbox);
const src = fs.readFileSync(process.env.CLIENT_PATH, 'utf8');
function loadNow() { vm.runInContext(src, ctx, { filename: 'app.js' }); }
function flush() { return new Promise((r) => setTimeout(r, 5)); }
async function flushAll(times) {
  let p = Promise.resolve();
  for (let i = 0; i < (times || 8); i++) { p = p.then(flush); }
  return p;
}
function activePage() {
  return PAGES.find((p) => els['page-' + p].classList.contains('active')) || null;
}

async function scenario(name) {
  if (name === 'admin_desync') {
    // THE REVIEW CASE: /#admin opens Admin, then the visitor picks Home.
    loc.hash = '#admin';
    loadNow();
    await flushAll(12);
    assert.strictEqual(typeof win.navigateTo, 'function',
      'navigateTo is exported on window (inline onclick handlers resolve there)');
    assert.strictEqual(activePage(), 'admin',
      'boot honored the /#admin deep link');
    win.navigateTo('landing');
    assert.strictEqual(activePage(), 'landing', 'Home is the visible page');
    assert.strictEqual(loc.hash, '',
      'the stale #admin is GONE from the URL - a reload now opens Home');
    assert.strictEqual(historyCalls[historyCalls.length - 1], '/',
      'the URL was rewritten to the bare path (pathname+search), not #landing');
    return 'admin_desync ok';
  }

  if (name === 'hash_roundtrip') {
    loadNow();
    await flushAll(12);
    assert.strictEqual(activePage(), 'landing', 'a bare / boots to Home');
    assert.strictEqual(historyCalls.length, 0,
      'a bare / visit stamps nothing (no #landing noise)');
    win.navigateTo('dashboard');
    assert.strictEqual(loc.hash, '#dashboard',
      'navigateTo wrote the hash for the visible page');
    assert.strictEqual(activePage(), 'dashboard',
      'navigateTo both showed the page and wrote the URL');
    // back/forward: the BROWSER changes the hash; the app must follow
    loc.hash = '#leaderboard';
    const ls = winListeners['hashchange'] || [];
    assert.strictEqual(ls.length, 1, 'the hashchange listener is wired');
    ls.forEach((fn) => fn({}));
    assert.strictEqual(activePage(), 'leaderboard',
      'a hashchange rerouted the visible page (shared links, back/forward)');
    assert.strictEqual(historyCalls[historyCalls.length - 1], '#leaderboard',
      'the reroute kept the URL in agreement');
    const before = historyCalls.length;
    ls.forEach((fn) => fn({}));
    assert.strictEqual(historyCalls.length, before,
      'the currentPage guard stops the double-write');
    loc.hash = '#bogus';
    ls.forEach((fn) => fn({}));
    assert.strictEqual(activePage(), 'leaderboard',
      'an unknown hash is ignored, not crashed on');
    return 'hash_roundtrip ok';
  }

  throw new Error('unknown scenario: ' + name);
}

scenario(process.argv[2])
  .then((msg) => { console.log(msg); process.exit(0); })
  .catch((e) => { console.error(String(e && e.message || e)); process.exit(1); });
"""

    def run_scenario(name):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(HARNESS)
            harness_path = f.name
        env = dict(os.environ, CLIENT_PATH=str(CLIENT))
        try:
            proc = subprocess.run(
                [node, harness_path, name],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
                env=env,
            )
        finally:
            try:
                Path(harness_path).unlink(missing_ok=True)
            except OSError:
                pass
        return proc

    print("== 1. THE REVIEW CASE: /#admin then Home leaves a CLEAN URL ==")
    proc = run_scenario("admin_desync")
    ok = proc.returncode == 0
    check(1.1, ok, f"node exit {proc.returncode} - "
          f"{(proc.stderr or proc.stdout or '').strip()[:200]}")

    print("== 2. THE ROUND TRIP: deep link in, hashchange back, guard on ==")
    proc = run_scenario("hash_roundtrip")
    ok = proc.returncode == 0
    check(2.1, ok, f"node exit {proc.returncode} - "
          f"{(proc.stderr or proc.stdout or '').strip()[:200]}")

# ── 3. THE SPA ROUTES: redirects point at hash routes that EXIST ────
print("== 3. THE SPA REDIRECTS: /winners and friends (hermetic) ==")
_tmp = Path(tempfile.mkdtemp(prefix="nav_hash_"))
sys.path.insert(0, str(HERE / "pool"))
import database as db; db.DB_PATH = _tmp / "jackpot.db"; db.init_db()
import server as srv
from fastapi.testclient import TestClient

client = TestClient(srv.app)
r = client.get("/winners", follow_redirects=False)
check(3.1, r.status_code == 302 and r.headers.get("location") == "/#leaderboard",
      f"/winners maps to the leaderboard hash the client honors: "
      f"{r.status_code} -> {r.headers.get('location')}")
r = client.get("/dashboard", follow_redirects=False)
check(3.2, r.status_code == 302 and r.headers.get("location") == "/#dashboard",
      f"every real page path redirects to its own hash: "
      f"{r.status_code} -> {r.headers.get('location')}")
r = client.get("/definitely-not-a-page", follow_redirects=False)
check(3.3, r.status_code == 200 and "text/html" in r.headers.get("content-type", ""),
      f"an unknown path serves the app shell instead of a bogus redirect: {r.status_code}")
r = client.get("/", follow_redirects=False)
check(3.4, r.status_code == 200, f"the root serves the app: {r.status_code}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
