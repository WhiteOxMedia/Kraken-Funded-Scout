"""test_clockreco.py — THE CLOCK RECOMMENDATION SEAM, DRIVEN (Nov 20).

The seam is: the scanner RECOMMENDS, the exit check DECIDES. Source-string
checks can prove the code says that; this suite RUNS it.

  1. gate 3b's rule (`background_scanner.clock_recommendation`) is driven
     against a fake watch row and fake live prices: it stamps on a lapsed
     window with the goal unpaid, WITHDRAWS the cycle the goal pays, and
     refuses a second stamp for the same window even after the price falls
     back — while a new window is free to recommend again.
  2. the bot's exit ladder is executed from its own source with a fake
     exchange, a fake strategy and a stubbed bridge: with a recommendation
     present and every gate clear it LOGS the recommendation and writes NO
     exit signal; with a trail hit it writes one sell whose reason carries
     the clock's agreement.

Nothing here touches the network, the bridge database, or the live book.
"""
import sys
import textwrap
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import pandas as pd
import background_scanner as bs
import scratch_space   # scratch policy: nothing volatile lands in the repo root

PAIR = "XRP/USD"
NOW = datetime.now(timezone.utc)
LAPSED = (NOW - timedelta(minutes=481)).isoformat()
FRESH = (NOW - timedelta(minutes=100)).isoformat()


def row(**kw):
    r = {"entry": 1.0, "goal_pct": 1.5, "window_min": 480.0,
         "added": LAPSED, "source": "sure_thing", "size": 400.0,
         "high": 1.0, "goal_pct": 1.5}
    r.update(kw)
    return r


# ── 1. GATE 3b, DRIVEN ─────────────────────────────────────────────────
w = row()
out = bs.clock_recommendation(w, pair=PAIR, live=1.003, entry=1.0, now=NOW)
check("a lapsed window with the goal unpaid STAMPS",
      out.get("action") == "stamp" and w.get("clock_rec"), out)
check("the stamp carries the scanner's own words",
      "OUT — the 480-min window ended without the goal" in
      str((w.get("clock_rec") or {}).get("call")),
      (w.get("clock_rec") or {}).get("call"))
check("the stamp names its window (one per window)",
      str(w.get("clock_rec_window")) == LAPSED, w.get("clock_rec_window"))

at_first = (w.get("clock_rec") or {}).get("at")
out2 = bs.clock_recommendation(w, pair=PAIR, live=1.004, entry=1.0, now=NOW)
check("a second call in the same window does NOT stamp again",
      out2.get("action") == "none"
      and (w.get("clock_rec") or {}).get("at") == at_first, out2)

out3 = bs.clock_recommendation(w, pair=PAIR, live=1.02, entry=1.0, now=NOW)
check("the goal paying WITHDRAWS the recommendation the same cycle",
      out3.get("action") == "withdrawn" and "clock_rec" not in w, out3)
check("the withdrawal keeps the window key (so it cannot re-recommend)",
      str(w.get("clock_rec_window")) == LAPSED, w.get("clock_rec_window"))

out4 = bs.clock_recommendation(w, pair=PAIR, live=1.003, entry=1.0, now=NOW)
check("price falling back under the goal does NOT stamp the same window "
      "twice", out4.get("action") == "none" and "clock_rec" not in w, out4)

new_window = (NOW - timedelta(minutes=500)).isoformat()
w2 = row(added=new_window)
out5 = bs.clock_recommendation(w2, pair=PAIR, live=1.003, entry=1.0, now=NOW)
check("a NEW window is free to recommend again",
      out5.get("action") == "stamp"
      and str(w2.get("clock_rec_window")) == new_window, out5)

w3 = row(clock_rec={"call": "old window"},
         clock_rec_window=(NOW - timedelta(minutes=900)).isoformat())
out6 = bs.clock_recommendation(w3, pair=PAIR, live=1.003, entry=1.0, now=NOW)
check("an earlier window's stamp never rides a re-armed row",
      str((w3.get("clock_rec") or {}).get("window_key")) == LAPSED, out6)

w4 = row(source="champion")
check("another route's row is never stamped",
      bs.clock_recommendation(w4, pair=PAIR, live=1.003, entry=1.0,
                              now=NOW).get("action") == "none"
      and "clock_rec" not in w4)
w5 = row(added=FRESH)
check("a window that has not lapsed is never stamped",
      bs.clock_recommendation(w5, pair=PAIR, live=1.003, entry=1.0,
                              now=NOW).get("action") == "none"
      and "clock_rec" not in w5)
import inspect
_fn_src = inspect.getsource(bs.clock_recommendation)
_body = _fn_src.split('"""')[2] if _fn_src.count('"""') >= 2 else _fn_src
check("gate 3b's helper has no exit decision in it (its DOC may say so)",
      "should_exit" not in _body and "write_exit_signal" not in _body,
      [ln.strip() for ln in _body.splitlines()
       if "should_exit" in ln or "write_exit_signal" in ln])


# ── 2. THE LADDER, EXECUTED FROM ITS OWN SOURCE ────────────────────────
_src = Path(bs.__file__).read_text(encoding="utf-8", errors="replace")
# The block runs from the fresh-data fetch (it builds df, the ticker
# overlay, _rt_live and the DCA gate) through the per-pair except, so the
# slice is executable as it stands — a self-contained per-position pass.
_START = "                    # Fetch fresh data and run strategy"
_END = 'logger.debug("Exit check failed for %s: %s", pair, exc)'
_i = _src.find(_START)
_j = _src.find(_END, _i if _i > 0 else 0)
check("the ladder block is findable in the scanner source",
      _i > 0 and _j > _i, (_i, _j))
_ladder = textwrap.dedent(_src[_i:_j + len(_END)])
check("the ladder calls the seam and never writes a signal itself",
      "weigh_clock_rec(" in _ladder
      and _ladder.count("write_exit_signal(") == 1
      and "should_exit = True" in _ladder, _ladder.count("write_exit_signal("))

_notes = []


class _Log:
    def info(self, msg, *a):
        try:
            _notes.append(msg % a if a else str(msg))
        except Exception:
            _notes.append(f"{msg} {a}")

    def debug(self, msg, *a):
        # swallowed failures would make a "no signal" result meaningless
        try:
            _notes.append("DEBUG " + (msg % a if a else str(msg)))
        except Exception:
            _notes.append(f"DEBUG {msg} {a}")

    def warning(self, *a):
        pass


class _Ex:
    def __init__(self, live):
        self.live = live

    def fetch_ohlcv(self, pair, timeframe="1h", limit=200):
        base = 1_700_000_000_000
        return [[base + i * 3_600_000, 1.0, 1.01, 0.99, 1.0, 10.0]
                for i in range(40)]

    def fetch_ticker(self, pair):
        return {"last": self.live}


def _drive(rec, trail_hit, live=1.003, action="hold", confidence=40):
    """Run the REAL ladder block with fakes; return the bridge's write log."""
    wrote = []
    # the seam's own logging runs on the MODULE logger (weigh_clock_rec is
    # a module-level function), so capture both mouths into one list
    _lg = _Log()
    _saved_logger = bs.logger
    bs.logger = _lg

    sb = types.ModuleType("signal_bridge")
    sb.update_trailing_high = lambda pair, px: 1.01
    sb.check_trailing_stop = lambda pair, px: (trail_hit, None)
    sb.write_exit_signal = (lambda pair, reason, source="scout_exit_check":
                            wrote.append((pair, reason)))
    sb.count_dca_signals = lambda pair: 0
    sb.write_buy_signal = lambda **kw: None
    sb.get_pending_signals = lambda: []
    strat = types.ModuleType("strategy")
    strat.evaluate_strategy = lambda df, cfg: types.SimpleNamespace(
        action=action, confidence=confidence, reasons=[],
        entry_price=None, stop_loss=None, take_profit=None, risk_reward=None)
    _saved = {k: sys.modules.get(k) for k in ("signal_bridge", "strategy")}
    sys.modules["signal_bridge"] = sb
    sys.modules["strategy"] = strat
    g = {
        "__builtins__": __builtins__,
        "exchange": _Ex(live),
        "s": {"pair": PAIR, "status": "filled", "action": "buy",
              "executed_at": (NOW - timedelta(hours=2)).isoformat(),
              "execution_price": 1.0, "entry_price": 1.0,
              "confidence": 92.0},
        "cfg": object(), "no_buy": False, "alerted_recently": {},
        "now": NOW.replace(tzinfo=None), "pair": PAIR,
        "_clock_rec": rec, "logger": _lg, "SCOUT_DCA_ENABLED": False,
        "weigh_clock_rec": bs.weigh_clock_rec,
    }
    # the block carries a `continue` (the DCA branch) — a one-pass loop
    # makes the slice legal without changing what runs
    _wrapped = "for _once in range(1):\n" + textwrap.indent(_ladder, "    ")
    try:
        exec(compile(_wrapped, "<exit-ladder>", "exec"), g)
    finally:
        bs.logger = _saved_logger
        for k, v in _saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return wrote


STAMP = {"call": ("OUT — the 480-min window ended without the goal "
                  "(+0.3% vs +1.5%)"), "pct": 0.3}

_notes.clear()
wrote = _drive(STAMP, trail_hit=False)
check("the ladder pass raised nothing",
      not any("Exit check failed" in n for n in _notes), _notes)
check("with every gate clear the ladder writes NO exit signal",
      wrote == [], wrote)
check("the ladder LOGS the recommendation it declined",
      any("EXIT CHECK keeps XRP" in n for n in _notes), _notes)
check("the declined line says the decision is the check's",
      any("the decision is this check's" in n for n in _notes), _notes)

_notes.clear()
wrote = _drive(STAMP, trail_hit=True)
check("a trail hit still writes exactly one sell", len(wrote) == 1, wrote)
check("the sale's reason carries the clock's agreement",
      bool(wrote) and "the clock also recommended OUT" in wrote[0][1]
      and "the 480-min window ended" in wrote[0][1], wrote)
check("the sale's reason still leads with the gate that decided",
      bool(wrote) and wrote[0][1].startswith("Trailing stop"), wrote)

wrote = _drive(None, trail_hit=True)
check("without a recommendation the reason has no clock clause",
      bool(wrote) and "clock" not in wrote[0][1], wrote)

check("weigh_clock_rec returns the decision unchanged when the gates hold",
      bs.weigh_clock_rec(PAIR, STAMP, False, "why") == (False, "why"))
check("weigh_clock_rec never turns a hold into a sale",
      bs.weigh_clock_rec(PAIR, {"call": "x"}, False, "y")[0] is False)


lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_clockreco_check.txt').write_text(
    "\n".join(lines), encoding="utf-8")
print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
