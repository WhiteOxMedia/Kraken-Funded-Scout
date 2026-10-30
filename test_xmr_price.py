"""test_xmr_price.py — THE LIVE RATE'S CONTRACT (Oct 26).

Grades pool/xmr_price.py, and the wiring that made the pool's dollar
figures stop using jackpot_engine's hardcoded 323:

  1. THE SOURCE IS PINNED: Kraken's public XXMRZUSD ticker, https, a
     3-second timeout — a public read that can never move money
  2. RESOLUTION ORDER: the operator's pin wins, then the live cached
     rate, then the caller's fallback
  3. THE CACHE CONTRACT: one fetch per TTL, a 120-second retry window
     after a failure, single-flight, and a short per-process memo
  4. FAILURE IS NORMAL: a dead feed keeps serving the last good rate,
     records the error, and never raises to a page render
  5. THE WIRING: jackpot_funding.xmr_usd_rate() serves the live rate and
     jackpot_engine.get_pool_stats() converts at it (not at 323)
  6. HERMETIC: every fetch in this file is a stub — no network, and the
     live jackpot.db is untouched (sandbox DB)

House conventions: check/report, sandbox_db, fake_fetch, CALLS — the ONE
copy of that harness lives in conftest.py now, shared with the other
house scripts; this file keeps only its own grades.
"""
import os
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))

from conftest import (                     # the harness, ONE copy: conftest.py
    CALLS,                                 # what reached the "wire"
    check,                                 # one grade: PASS/FAIL + counters
    fake_fetch,                            # the stub standing in for the ticker
    report,                                # the RESULT line + the exit code
    sandbox_db,                            # a fresh jackpot.db in a fresh tempdir
)

SANDBOX = sandbox_db("xmr_price_")

import database as db                                  # noqa: E402
import xmr_price                                       # noqa: E402
import jackpot_funding as funding                      # noqa: E402
import jackpot_engine as engine                        # noqa: E402

FALLBACK = 323.0


def reset_db():
    """Clean slate: no pin, no cache, no attempt, fresh memo."""
    for key in (xmr_price.OVERRIDE_KEY, xmr_price.RATE_KEY, xmr_price.TS_KEY,
                xmr_price.SRC_KEY, xmr_price.ATTEMPT_KEY, xmr_price.ERR_KEY):
        db.set_setting(key, "")
    db.set_setting("jackpot_balance", "0")
    CALLS.clear()
    xmr_price.reset()


print("== 1. THE SOURCE IS PINNED (structural) ==")
check(1.1, xmr_price.TICKER_URL.startswith("https://api.kraken.com/0/public/Ticker"),
      f"public Kraken ticker over https: {xmr_price.TICKER_URL}")
check(1.2, "pair=XXMRZUSD" in xmr_price.TICKER_URL and xmr_price.TICKER_PAIR == "XXMRZUSD",
      "the pair is XMR/USD (XXMRZUSD), stated once in TICKER_PAIR")
check(1.3, xmr_price.TIMEOUT_SECONDS <= 5,
      f"a dead feed costs {xmr_price.TIMEOUT_SECONDS}s, not a hung page")
check(1.4, xmr_price.TTL_SECONDS >= 300 and xmr_price.RETRY_AFTER_SECONDS >= 60,
      f"TTL {xmr_price.TTL_SECONDS:.0f}s, retry {xmr_price.RETRY_AFTER_SECONDS:.0f}s — "
      "a page view can never be one request")

print("\n== 2. RESOLUTION ORDER: pin > live > fallback ==")
reset_db()
xmr_price._fetch = fake_fetch(ValueError("offline"))
_cur = xmr_price.current(fallback=FALLBACK)
check(2.1, _cur["rate"] == FALLBACK and _cur["source"] == "default" and _cur["stale"],
      f"never fetched + dead feed -> the fallback, flagged stale ({_cur['rate']})")

reset_db()
xmr_price._fetch = fake_fetch(593.66)
check(2.2, xmr_price.refresh_now(force=True) == 593.66,
      "a good fetch returns the ticker's last trade")
_cur = xmr_price.current(fallback=FALLBACK)
check(2.3, abs(_cur["rate"] - 593.66) < 1e-9 and _cur["source"] == "kraken_ticker"
      and not _cur["stale"],
      f"the live rate is served fresh ({_cur['rate']}, source {_cur['source']})")
check(2.4, db.get_setting(xmr_price.RATE_KEY) != "" and db.get_setting(xmr_price.TS_KEY) != "",
      "the rate and its timestamp are persisted (survives a restart)")

reset_db()
xmr_price._fetch = fake_fetch(593.66)
xmr_price.refresh_now(force=True)
db.set_setting(xmr_price.OVERRIDE_KEY, "400")
xmr_price.reset()
_cur = xmr_price.current(fallback=FALLBACK)
check(2.5, _cur["rate"] == 400.0 and _cur["source"] == "override",
      "the operator's pin wins over the fetched rate (400)")
check(2.6, xmr_price.refresh_now() is None and not CALLS[1:],
      "…and a pinned rate SUPPRESSES the fetch entirely (no request, no drift)")

print("\n== 3. THE CACHE CONTRACT ==")
reset_db()
xmr_price._fetch = fake_fetch(593.66)
xmr_price.refresh_now(force=True)
_before = len(CALLS)
_served = xmr_price.current(fallback=FALLBACK)          # memo (fresh)
check(3.1, len(CALLS) == _before and not xmr_price._maybe_refresh(time.time()),
      "inside the TTL nothing fetches: the cache is served (one attempt total)")

db.set_setting(xmr_price.TS_KEY, "%.3f" % (time.time() - xmr_price.TTL_SECONDS - 5))
db.set_setting(xmr_price.ATTEMPT_KEY, "%.3f" % (time.time() - xmr_price.RETRY_AFTER_SECONDS - 5))
xmr_price.reset()
check(3.2, xmr_price._maybe_refresh(time.time()) is True,
      "past the TTL a refresh is started (in the background — the reader does not wait)")
time.sleep(0.15)
check(3.3, len(CALLS) > _before,
      "…and exactly that one refresh ran")

reset_db()
xmr_price._fetch = fake_fetch(593.66)
db.set_setting(xmr_price.ATTEMPT_KEY, "%.3f" % time.time())
xmr_price.reset()
check(3.4, xmr_price._maybe_refresh(time.time()) is False and not CALLS,
      "inside the retry window even a stale cache does not refetch (no hammering)")

reset_db()
xmr_price._fetch = fake_fetch(593.66)
xmr_price.refresh_now(force=True)
CALLS.clear()
for _ in range(50):
    xmr_price.current(fallback=FALLBACK)
check(3.5, not CALLS,
      "50 reads in one burst cost ZERO requests — the memo absorbs a loop")

reset_db()
xmr_price._fetch = fake_fetch(593.66)
_started = [xmr_price._maybe_refresh(time.time()) for _ in range(5)]
check(3.6, _started.count(True) == 1,
      f"single-flight: 5 simultaneous readers start 1 refresh ({_started})")
time.sleep(0.15)

print("\n== 4. FAILURE IS NORMAL ==")
reset_db()
xmr_price._fetch = fake_fetch(593.66)
xmr_price.refresh_now(force=True)
# age the good value past the TTL, so the failed refresh is the only
# thing standing between a stale number and a fresh one
db.set_setting(xmr_price.TS_KEY,
               "%.3f" % (time.time() - xmr_price.TTL_SECONDS - 60))
xmr_price._fetch = fake_fetch(RuntimeError("kraken 503"))
_got = xmr_price.refresh_now(force=True)
xmr_price.reset()
_cur = xmr_price.current(fallback=FALLBACK)
check(4.1, _got is None and abs(_cur["rate"] - 593.66) < 1e-9,
      f"a failed fetch KEEPS the last good rate ({_cur['rate']})")
check(4.2, _cur["stale"] is True,
      "…and a failed refresh does not rejuvenate it: the reader says stale")
check(4.3, "kraken 503" in str(db.get_setting(xmr_price.ERR_KEY)),
      f"the failure is written down for the operator: {db.get_setting(xmr_price.ERR_KEY)}")
_attempt = float(db.get_setting(xmr_price.ATTEMPT_KEY))
xmr_price.reset()
check(4.4, xmr_price._maybe_refresh(time.time()) is False,
      "…and the retry window holds, so an outage costs 1 request / 120s")
check(4.5, _attempt > 0, "the attempt itself was stamped before the request")

reset_db()
xmr_price._fetch = fake_fetch(ValueError("bad json"))
db.set_setting("jackpot_balance", "1.0")
check(4.6, isinstance(xmr_price.current(fallback=FALLBACK), dict),
      "a broken feed still renders: current() returns a dict, never an exception")
check(4.7, abs(engine.get_pool_stats()["jackpot_usd"] - 323.0) < 0.01,
      "…and the engine falls back to its documented constant, not to NaN")

print("\n== 5. THE WIRING (nothing is priced at 323 any more) ==")
reset_db()
xmr_price._fetch = fake_fetch(500.0)
xmr_price.refresh_now(force=True)
db.set_setting("jackpot_balance", "1.0")
xmr_price.reset()
check(5.1, abs(funding.xmr_usd_rate() - 500.0) < 1e-9,
      f"jackpot_funding.xmr_usd_rate() serves the live rate ({funding.xmr_usd_rate()})")
_stats = engine.get_pool_stats()
check(5.2, abs(_stats["jackpot_usd"] - round(_stats["jackpot_xmr"] * 500.0, 2)) < 0.01,
      f"the hero pot converts at the live rate: {_stats['jackpot_xmr']} XMR = "
      f"${_stats['jackpot_usd']}")
check(5.3, _stats.get("xmr_usd_rate") == 500.0,
      "the rate rides the stats payload, so a caller can label the conversion")

_server_src = (Path(HERE) / "pool" / "server.py").read_text(encoding="utf-8")
check(5.4, 'jackpot_funding.xmr_usd_rate()' in _server_src
      and '"XMR_USD_RATE", 323.0' not in _server_src,
      "the Scout Pro checkout price derives from the same live rate")

_engine_src = (Path(HERE) / "pool" / "jackpot_engine.py").read_text(encoding="utf-8")
check(5.5, "jackpot * 323" not in _engine_src,
      "the hardcoded 323 conversion is gone from get_pool_stats")

print("\n== 6. HERMETIC ==")
check(6.1, not db.DB_PATH.exists() or str(db.DB_PATH).startswith(SANDBOX),
      "every write in this file landed in the sandbox DB")
check(6.2, "XMR_PRICE_LIVE" in
      (Path(HERE) / "pool" / "xmr_price.py").read_text(encoding="utf-8"),
      "the kill switch exists for offline boxes and network-free test runs")
os.environ["XMR_PRICE_LIVE"] = "0"
reset_db()
xmr_price._fetch = fake_fetch(593.66)
check(6.3, xmr_price.refresh_now() is None and not CALLS,
      "XMR_PRICE_LIVE=0: no automatic fetch, no exception")
os.environ.pop("XMR_PRICE_LIVE", None)

report()
