"""conftest.py — THE POOL TEST HARNESS, IN ONE PLACE (Oct 29).

Every house pool script (test_*.py here at the root) grades the same stack
the same way: a sandbox jackpot.db, a TestClient over pool/server with the
wallet RPC refused, a stubbed fetch, XMR_PRICE_LIVE=0, and
    check(n, cond, why)  ->  RESULT: N passed, M failed  ->  sys.exit
Until Oct 29 each script carried its own copy of that boilerplate; the copies
had already drifted (rate_label's check asserts `cond is True`, xmr_price's
asserts bare truthiness). This file is now the ONE copy: test scripts import
the helpers from it house-style, and pytest picks the same fixtures up for
new-style tests written later.

Read this file like the definitions chapter of house style:

  check / report      the grade + the RESULT line + the exit code
  sandbox_db()        tempfile.mkdtemp, database.DB_PATH pointed into it, init_db
  fake_fetch(rate)    the stub standing in for a network: records what it is
                      asked for into CALLS and returns rate / raises rate
  CALLS               the shared readback for "did anything reach the wire?"
  make_client()       TestClient(server.app) with xmr.rpc_available -> False
  env(**values)       a context manager that sets / restores environment keys
  hermetic()          XMR_PRICE_LIVE=0 and a reset of pool/xmr_price's cache

Both halves must stay import-safe everywhere: pytest loads this file as a
conftest for ANY test run under the repo (deployctl/tests included), and the
house scripts import it with `python test_rate_label.py` from a plain shell.
That is why the pytest import is optional and the pool imports are lazy,
inside the helpers, never at module top.

Console note: house scripts print arrows and other non-ASCII glyphs. On a
Windows console that restricts output to cp1252 (no UTF-8) they die with a
UnicodeEncodeError; run them with PYTHONIOENCODING=utf-8.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The house scripts grade the pool as flat modules (`import database`,
# `import xmr_price`), exactly as pool/server.py itself imports its
# siblings — stand up the same import path once, here, for everyone.
_POOL = str(HERE / "pool")
if _POOL not in sys.path:
    sys.path.insert(0, _POOL)

passed = failed = 0


def check(n, cond, why):
    """One grade: PASS/FAIL line + counters. The strict form (`is True`) so a
    truthy-but-wrong value (x = "x" in an if-grade) can never leak a PASS
    through this harness — that exact leak is why the copies were unified."""
    global passed, failed
    cond = cond is True
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


def report():
    """The RESULT line + the exit code the house runs read."""
    print(f"\nRESULT: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


# ── the sandbox DB ──────────────────────────────────────────────────────

def sandbox_db(prefix="house_pool_"):
    """Point database.DB_PATH at a fresh sandbox dir and create the schema.

    Returns the sandbox dir TEXT (the house pattern: scripts str()-match
    against it, `str(db.DB_PATH).startswith(sandbox)`), so callers keep
    handling it as a string the way they always did. The live jackpot.db is
    never touched: every write lands in the sandbox.
    """
    import database as dbm
    sandbox = Path(tempfile.mkdtemp(prefix=prefix))
    dbm.DB_PATH = sandbox / "jackpot.db"
    dbm.init_db()
    return str(sandbox)


def _sandbox_db_ctx():
    """Body of the pytest fixture: install a sandbox, then RESTORE the DB
    path and remove the sandbox dir, so a pytest run leaves nothing behind."""
    import database as dbm
    sandbox = Path(tempfile.mkdtemp(prefix="house_pool_"))
    saved_path = dbm.DB_PATH
    dbm.DB_PATH = sandbox / "jackpot.db"
    dbm.init_db()
    try:
        yield sandbox
    finally:
        dbm.DB_PATH = saved_path
        shutil.rmtree(sandbox, ignore_errors=True)


# ── the fake fetch ──────────────────────────────────────────────────────

CALLS: list = []


def fake_fetch(rate):
    """A stub standing in for the ticker/pool feed — every house check that
    would otherwise touch the wire uses one of these. Raises if `rate` is an
    Exception, returns it otherwise. Readback via the shared CALLS list.
    """
    def _f():
        CALLS.append(rate)
        if isinstance(rate, Exception):
            raise rate
        return rate
    return _f


# ── the TestClient ──────────────────────────────────────────────────────

def make_client(**transport_kwargs):
    """TestClient(pool/server.app) with the wallet RPC refused.

    There is no wallet on a CI box: `srv.xmr.rpc_available` returns False in
    this harness, so grades of /stats and /wallet run the "offline" branch a
    CI runner always sees — deterministic, fast, no wallet. Returns the
    client AND the server module, because the scripts that stamp
    `srv.xmr.rpc_available` (and then grade the stamp, rate_label 2.6)
    need both handles. Extra kwargs go straight to TestClient — the admin
    sweeps need a QUIET client with raise_server_exceptions=False.
    """
    from fastapi.testclient import TestClient

    import server as srv
    srv.xmr.rpc_available = lambda *a, **k: False
    return TestClient(srv.app, **transport_kwargs), srv


# ── the hermetic env ────────────────────────────────────────────────────

@contextmanager
def env(**values):
    """Environment keys set just for the block, restored (or dropped) after.
    The house pattern beyond XMR_PRICE_LIVE: operator pins like
    XMRJ_ADMIN_TOKEN must not rescue a negative grade from the shell that
    runs the script."""
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield values
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def hermetic():
    """XMR_PRICE_LIVE=0 for the rest of this process, and xmr_price.reset()
    so a module imported earlier in the run holds no cached rate/attempt
    stamp from a fetch that may have hit the wire. The kill switch every
    offline grade leans on: with it on, nothing in pool/xmr_price (nor
    pool/wallet.py, which checks the same switch) ever fetches."""
    os.environ["XMR_PRICE_LIVE"] = "0"
    import xmr_price
    xmr_price.reset()


"""
── the pytest fixtures (half two of the file) ──

The same four things, as fixtures, for tests written house-new-style later:

  sandbox_db    replaces sandbox_db()       fresh per test, restored after
  hermetic_env  replaces hermetic()         XMR_PRICE_LIVE=0, cleaned after
  fake_fetch    replaces fake_fetch()+CALLS fresh record per test
  client        replaces make_client()      TestClient, wallet refused

pytest loads this file for ANY pytest run under the repo, including
deployctl/tests — whose fixtures (migrated_database_url, config,
app_client) do not clash with these four names, and xmrjackpot-seo, whose
house scripts run under plain python and never import this file.

The fixture bodies are privately named and registered under the house
names via pytest.fixture(name=...): a `def sandbox_db()` under @fixture
would overwrite the PLAIN helper this module's scripts import, and the
house scripts must keep getting the plain function.
"""

try:  # pytest is absent from the air-gapped boxes the house scripts run on
    import pytest
except ImportError:                                   # pragma: no cover
    pytest = None

if pytest is not None:

    @pytest.fixture(name="sandbox_db")
    def _sandbox_db_fixture():
        """A fresh sandbox jackpot.db per test, restored afterwards."""
        gen = _sandbox_db_ctx()
        try:
            yield next(gen)
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

    @pytest.fixture(name="hermetic_env")
    def _hermetic_env_fixture():
        """XMR_PRICE_LIVE=0 for the test, popped afterwards."""
        with env(XMR_PRICE_LIVE="0"):
            import xmr_price
            xmr_price.reset()
            yield xmr_price
            xmr_price.reset()

    @pytest.fixture(name="fake_fetch")
    def _fake_fetch_fixture():
        """A stub fetch like the script helper, with a FRESH CALLS record.
        The record rides the STUB itself (stub.calls), not the factory."""
        calls = []
        def _make(rate):
            def _f():
                calls.append(rate)
                if isinstance(rate, Exception):
                    raise rate
                return rate
            _f.calls = calls
            return _f
        return _make

    @pytest.fixture(name="client")
    def _client_fixture():
        """TestClient over pool/server with the wallet RPC refused."""
        c, _srv = make_client()
        return c
