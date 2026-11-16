"""Checks for timing_loop: the caller exists, and it is inert until ARMED.

    1. while ARMED is False a cycle never builds a client (explodes if it tries)
    2. while ARMED is False it never reaches check_scout_signals
    3. at/below the halt line it refuses even when ARMED
    4. a buffer under the floor refuses even when ARMED
    5. armed + clear gates: exactly one client, one call to check_scout_signals
    6. the client is reused across cycles, never rebuilt
    7. real fills need --live: default config hands timing_live paper_mode=True
    8. the lock refuses a second loop
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import timing_loop as tl

PASS = FAIL = 0


def check(name, ok, detail=None):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"ok   {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}  {detail}")


REAL_GET_CLIENT = tl.get_client   # the real factory, restored for the cache test


# ── harness: record what the cycle touches ──────────────────────────────────
class Boom(Exception):
    pass


calls = {"client": 0, "scan": 0}


def fake_client():
    calls["client"] += 1
    return object()


def fake_check(kraken, cfg):
    calls["scan"] += 1
    calls["paper"] = getattr(cfg, "paper_mode", "missing")
    return {"ok": True}


# the real reader cannot be imported (it needs restricted_pairs), so stub the module
import types                                   # noqa: E402
ssr = types.ModuleType("scout_signal_reader")
ssr.check_scout_signals = fake_check
sys.modules["scout_signal_reader"] = ssr


def with_armed(value, fn):
    old = tl.ot.ARMED
    tl.ot.ARMED = value
    try:
        return fn()
    finally:
        tl.ot.ARMED = old


def with_ctx(balance=None, buffer=None, fn=None):
    old = tl.ot.death_context

    def fake():
        base = {"start": 9700.0 / 0.97, "balance": 9981.96, "death_line": 9700.0,
                "halt_line": 9850.0, "buffer": 281.96, "headroom": 131.96}
        if balance is not None:
            base["balance"] = balance
        if buffer is not None:
            base["buffer"] = buffer
        return base

    tl.ot.death_context = fake
    try:
        return fn()
    finally:
        tl.ot.death_context = old


def reset():
    calls.update({"client": 0, "scan": 0})
    tl._client = None
    tl.get_client = fake_client   # replaced per test


# 1 + 2: disarmed, gates irrelevant
tl.get_client = fake_client
calls.update({"client": 0, "scan": 0})
out = with_ctx(fn=lambda: with_armed(False, lambda: tl.cycle(allow_live=True)))
check("1 disarmed: no client is built", calls["client"] == 0, calls)
check("2 disarmed: check_scout_signals is never reached", calls["scan"] == 0, calls)
check("2b disarmed: the verdict says why",
      out["armed"] is False and out["acted"] is False and "ARMED is False" in out["why"], out)

# 3: armed but at the halt line
calls.update({"client": 0, "scan": 0})
out = with_ctx(balance=9850.0, buffer=131.96,
               fn=lambda: with_armed(True, lambda: tl.cycle(allow_live=True)))
check("3 at the halt line: still no client, no call",
      calls["client"] == 0 and calls["scan"] == 0 and "halt line" in out["why"], out)

# 4: armed but buffer under the floor
calls.update({"client": 0, "scan": 0})
out = with_ctx(buffer=200.0, fn=lambda: with_armed(True, lambda: tl.cycle(allow_live=True)))
check("4 buffer under the floor: still no client, no call",
      calls["client"] == 0 and calls["scan"] == 0 and "floor" in out["why"], out)

# 5: armed + clear gates -> one client, one call, paper by default
calls.update({"client": 0, "scan": 0})
out = with_ctx(fn=lambda: with_armed(True, lambda: tl.cycle(allow_live=False)))
check("5 armed+clear: exactly one client and one scan",
      calls["client"] == 1 and calls["scan"] == 1 and out["acted"], calls)
check("5b without --live the fills stay validated (paper_mode True)",
      calls.get("paper") is True, calls)

# 6: the REAL factory caches — two cycles, one KrakenAPI constructed
import types as _t                          # noqa: E402
kr = _t.ModuleType("keyring")
kr.get_password = lambda service, name: "cred-" + name
built = {"n": 0}
ka = _t.ModuleType("kraken_api")


class _K:
    def __init__(self, *a):
        built["n"] += 1


ka.KrakenAPI = _K
sys.modules["keyring"], sys.modules["kraken_api"] = kr, ka
tl.get_client = REAL_GET_CLIENT
tl._client = None
calls.update({"client": 0, "scan": 0})
with_ctx(fn=lambda: with_armed(True, lambda: (tl.cycle(allow_live=False),
                                              tl.cycle(allow_live=False))))
check("6 two cycles build exactly one KrakenAPI (the cache holds)",
      built["n"] == 1 and calls["scan"] == 2, (built, calls))
tl.get_client = fake_client

# 7: --live flips paper_mode off, explicitly
calls.update({"client": 0, "scan": 0})
tl._client = object()
out = with_ctx(fn=lambda: with_armed(True, lambda: tl.cycle(allow_live=True)))
check("7 --live is the only way to paper_mode False", calls.get("paper") is False, calls)

# 8: the lock refuses a concurrent loop
open(tl.LOCK, "w").write("999999\n")
check("8 a fresh lock blocks a second loop", tl.other_loop_running() is True)
os.utime(tl.LOCK, (time.time() - tl.LOCK_STALE_S - 60,) * 2)
check("8b a stale lock does not", tl.other_loop_running() is False)
os.remove(tl.LOCK)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
