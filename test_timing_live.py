"""test_timing_live.py — the live wiring: one client, gates in front, one order path.

These checks are about the properties that matter when real money is behind the code:
  * timing_live cannot create a Kraken client (there is no second client, ever);
  * while overwatch_timing.ARMED is False it touches nothing at all — no fetch, no
    sizing, no order;
  * at/below the halt line or under the buffer floor it refuses before fetching;
  * when it does enter, the ticket is 1% of the balance and the order goes through
    execution_tiers, the only module that places orders;
  * exits go through execution_tiers' sell.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import overwatch_timing as ot
import timing_live as tl

PASS = FAIL = 0
lines = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        lines.append(f"PASS  {name}")
    else:
        FAIL += 1
        lines.append(f"FAIL  {name}  {detail}")


class Explodes:
    """Any interaction with this object fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"touched the client: {name}")


def bars(n=320, price=100.0):
    return [[i * 300, price, price * 1.001, price * 0.999, price, 10.0]
            for i in range(n)]


# captured once: the tests patch ot.death_context, and calling through the patch
# would recurse instead of reading the repo's real numbers
_real_death_context = ot.death_context


def good_ctx(balance=9981.95855906339):
    return _real_death_context({"start_balance": 10000.0, "current_balance": balance})


def patched_state():
    """Point the module at throwaway files so a test can never touch the real ones."""
    tmp = Path(tempfile.mkdtemp())
    return mock.patch.multiple(tl, STATE=str(tmp / "state.json"),
                               JOURNAL=str(tmp / "journal.jsonl"))


# ── 1. no second client ─────────────────────────────────────────────────────
src = Path(tl.__file__).read_text(encoding="utf-8")
check("1.1 the module never imports a client library",
      "import ccxt" not in src and "KrakenAPI" not in src)
check("1.2 and never constructs one",
      "ccxt.kraken(" not in src and "KrakenAPI(" not in src)

# ── 2. inert while disarmed ─────────────────────────────────────────────────
with patched_state():
    with mock.patch.object(ot, "ARMED", False):
        out = tl.tick(Explodes(), None)
check("2.1 disarmed: it does not fetch, size or order", out["armed"] is False, out)
check("2.2 disarmed: the reason still names the gate",
      "shadow" in out["why"] or "buffer" in out["why"] or "halt" in out["why"], out["why"])

# ── 3. the gates run BEFORE the fetch ──────────────────────────────────────
with patched_state():
    with mock.patch.object(ot, "ARMED", True), \
         mock.patch.object(ot, "death_context", lambda *a: good_ctx(9850.0)), \
         mock.patch.object(tl, "scorecard", lambda s, c: {"trades": 250, "win_pct": 90.0,
                                                          "mean_net_pct": 0.1,
                                                          "min_headroom_usd": 300.0}):
        out = tl.tick(Explodes(), None)
check("3.1 armed but at the halt line: refuses without touching the client",
      out["armed"] is False and "halt" in out["why"], out)

with patched_state():
    with mock.patch.object(ot, "ARMED", True), \
         mock.patch.object(ot, "death_context", lambda *a: good_ctx(9930.0)), \
         mock.patch.object(tl, "scorecard", lambda s, c: {"trades": 250, "win_pct": 90.0,
                                                          "mean_net_pct": 0.1,
                                                          "min_headroom_usd": 300.0}):
        out = tl.tick(Explodes(), None)
check("3.2 armed but under the buffer floor: refuses without touching the client",
      out["armed"] is False and "buffer" in out["why"], out)

# ── 4. an entry: 1% of balance, through execution_tiers ────────────────────
class FakeKraken:
    def __init__(self, price=100.0, low=None):
        self.price = price
        self.low = low
        self.fetches = 0

    def fetch_ohlcv(self, pair, interval_minutes=5, limit=200):
        self.fetches += 1
        b = bars(price=self.price)
        if self.low is not None:
            b[-1] = [b[-1][0], self.price, self.price * 1.001, self.low,
                     self.price, 10.0]
        return b


calls = []
with patched_state():
    with mock.patch.object(ot, "ARMED", True), \
         mock.patch.object(ot, "death_context", lambda *a: good_ctx()), \
         mock.patch.object(ot, "entry_ok", lambda *a: (True, "test")), \
         mock.patch.object(tl, "_pairs", lambda: ["BTC/USD"]), \
         mock.patch.object(tl, "scorecard", lambda s, c: {"trades": 250, "win_pct": 85.0,
                                                          "mean_net_pct": 0.1,
                                                          "min_headroom_usd": 300.0}), \
         mock.patch.object(tl.xt, "execute_tiered_buy",
                           lambda k, p, v, ref_price=0.0, validate=False:
                           calls.append(("buy", p, v, validate)) or {"average": ref_price}), \
         mock.patch.object(tl.xt, "execute_tiered_sell",
                           lambda k, p, v, ref_price=0.0, validate=False:
                           calls.append(("sell", p, v, validate)) or {}):
        kraken = FakeKraken()
        out = tl.tick(kraken, None)
check("4.1 the entry is placed once, through execution_tiers",
      len(calls) == 1 and calls[0][0] == "buy", calls)
ticket = ot.ticket_usd(9981.95855906339, 10000.0)
check("4.2 the ticket is 1% of the balance",
      abs(calls[0][2] * 100.0 - ticket) < 0.01, (calls[0][2] * 100.0, ticket))
check("4.3 and it was sized by contest_account's rule, not a local number",
      abs(ticket - 99.8195855906339) < 1e-6, ticket)

# ── 5. an exit goes through the sell path ──────────────────────────────────
with patched_state():
    st = tl.load_state()
    st["positions"] = {"BTC/USD": {"entry": 100.0, "peak": 100.0, "stop": 98.0,
                                   "bars": 1, "opened": 0, "volume": 1.0,
                                   "usd": 100.0, "paper": True}}
    tl.save_state(st)
    calls.clear()
    with mock.patch.object(ot, "ARMED", True), \
         mock.patch.object(ot, "death_context", lambda *a: good_ctx()), \
         mock.patch.object(tl, "_pairs", lambda: ["BTC/USD"]), \
         mock.patch.object(tl.xt, "execute_tiered_sell",
                           lambda k, p, v, ref_price=0.0, validate=False:
                           calls.append(("sell", p, v, validate)) or {}):
        out = tl.tick(FakeKraken(price=100.0, low=90.0), None)
check("5.1 a stopped trade is sold through execution_tiers",
      len(calls) == 1 and calls[0][0] == "sell", calls)
check("5.2 and the close is journalled with its reason",
      out["closed"] == 1, out)

print("\n".join(lines))
print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
