"""Test THE COOLDOWN'S FRESH EYES (user rule, Sep 4: 'the cooldown should
run a current exit check to make a decision to end the cooldown or stay
the course').

The case that forced it: XRP 16:12:16 — the bot bought at 16:12:11, the
scanner's 1h knife called the exit 5 seconds later, and the reader's
blanket 30-min sell cooldown rejected it ('Cooldown: bought 1 min ago');
the engine knife didn't clean the position out until 16:27. Now a sell
signal inside the window runs a CURRENT exit check: the fresh read still
says get out -> the cooldown ENDS and the sell executes; the position
recovered -> stay the course (the cooldown holds). Broken read fails
cool: the cooldown stands, exactly as before."""
import sys
from pathlib import Path
from types import SimpleNamespace

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


READER = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py")
src = READER.read_text(encoding="utf-8")
fn = src[src.find("def _cooldown_fresh_exit"):src.find("def check_scout_signals")]
cd = src[src.find("# Normal sell — position exists in bot DB"):
          src.find("# Acknowledge and execute")]

# ── 1. the cooldown branch decides with fresh eyes ───────────────────
check("the fresh-check helper exists", "def _cooldown_fresh_exit" in src)
check("the cooldown branch RUNS the current exit check",
      "_cooldown_fresh_exit(" in cd)
check("the old blanket reject is gone (the XRP-16:12 hole)",
      'f"Cooldown: bought {minutes_since_buy:.0f} min ago"' not in src)
check("stay the course: the hold rejection names the fresh check",
      "Cooldown holds:" in cd and "current exit check says hold" in cd)
check("end the cooldown: the log names the fresh check",
      "COOLDOWN ENDED by the current exit check" in cd)
check("the sell executes after the fresh pass (fall-through, no continue)",
      "fall through" in cd and cd.find("COOLDOWN ENDED") < cd.find("continue") - 60)
check("the trade ledger carries the decision (fresh reason attached)",
      'Cooldown ended at' in cd)
check("the 30-minute window itself is unchanged (the rule scopes the check, not the clock)",
      "minutes_since_buy < 30" in cd)

# ── 2. what the fresh check asks (bot questions, current data) ───────
check("the 1h knife is computed FRESH (not the engine's 5-min cache)",
      "_oh60" in fn and "1h knife still reversed" in fn)
check("the engine's own exit stack runs (SL / TP / ATR trail)",
      "_check_position_exits" in fn)
check("the brain read runs at the scanner's own exit bar (sell >=75 / 60 rider)",
      ">= 75" not in fn and "_thresh = 60 if _rider_pos else 75" in fn)
check("exhaustion + rider-exit verdicts count",
      "exhaustion signals detected" in fn and "momentum rider exit triggered" in fn)
check("broken read fails cool (the cooldown stands)",
      "fail cool" in fn)
check("both admitted sell sources still the only channels (two-ledger rule)",
      'source in ("scout_exit_check", "scout_trend_exit")' in src)
check("the live price seeds the fresh read",
      'df.loc[_idx, "close"] = live' in fn)

# ── 3. functional: the helper decides on synthetic data ──────────────
# The bot's copy is the LIVE reader (the scout's 414-line copy is a stale
# leftover) — load it by explicit path. The scout dir stays FIRST on
# sys.path (signal_bridge/strategy resolve from the scout repo, exactly
# like the live process); the bot dir is appended for restricted_pairs.
BOT = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
if str(BOT) not in sys.path:
    sys.path.append(str(BOT))
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("scout_signal_reader", READER)
    ssr = importlib.util.module_from_spec(_spec)
    sys.modules["scout_signal_reader"] = ssr
    _spec.loader.exec_module(ssr)

    def _candles(closes):
        ts = 1_700_000_000_000
        return [[ts + i * 3_600_000, c, c, c, c, 1000.0]
                for i, c in enumerate(closes)]

    _declining = [2.0 - 0.01 * i for i in range(60)]   # 2.00 -> 1.41 knife down
    _rising = [1.40 + 0.01 * i for i in range(60)]     # 1.40 -> 1.99 recovered

    class _FakeKraken:
        def __init__(self, candles, live, fail=False):
            self._candles, self._live, self._fail = candles, live, fail

        def fetch_ohlcv(self, pair, interval_minutes=60, limit=None):
            if self._fail:
                raise RuntimeError("network down")
            return list(self._candles)

        def fetch_price(self, pair):
            if self._fail:
                raise RuntimeError("network down")
            return self._live

    _cfg = SimpleNamespace(candle_interval=60, lookback_candles=60,
                           ema_short=9, ema_long=21)
    _pos = SimpleNamespace(id=1, pair="XRP/USD", entry_price=1.40,
                           entry_volume=41.7, highest_price=1.45)
    _hold_engine = SimpleNamespace(_check_position_exits=lambda pos, px, df: None)

    # A. the knife still reads reversed -> the cooldown ends
    r = ssr._cooldown_fresh_exit(_FakeKraken(_candles(_declining), 1.396),
                                 _cfg, "XRP/USD", _pos, _hold_engine)
    check("knife still reversed -> COOLDOWN ENDS",
          bool(r) and "knife still reversed" in str(r), r)

    import strategy as _strat
    _orig_eval = _strat.evaluate_strategy
    try:
        # B. recovered + the brain says hold -> stay the course
        _strat.evaluate_strategy = lambda df, cfg: SimpleNamespace(
            action="hold", confidence=50, reasons=[])
        r = ssr._cooldown_fresh_exit(_FakeKraken(_candles(_rising), 2.00),
                                     _cfg, "XRP/USD", _pos, _hold_engine)
        check("recovered + brain hold -> STAY THE COURSE (None)", r is None, r)

        # C. the engine exit stack fires on the live read -> cooldown ends
        _sl_engine = SimpleNamespace(
            _check_position_exits=lambda pos, px, df: "Stop-loss (-5.2%)")
        r = ssr._cooldown_fresh_exit(_FakeKraken(_candles(_rising), 2.00),
                                     _cfg, "XRP/USD", _pos, _sl_engine)
        check("engine stack fires -> COOLDOWN ENDS",
              bool(r) and "current check: Stop-loss" in str(r), r)

        # D. fresh brain sell >= 75 -> cooldown ends
        _strat.evaluate_strategy = lambda df, cfg: SimpleNamespace(
            action="sell", confidence=80, reasons=[])
        r = ssr._cooldown_fresh_exit(_FakeKraken(_candles(_rising), 2.00),
                                     _cfg, "XRP/USD", _pos, _hold_engine)
        check("fresh brain sell 80% (bar 75) -> COOLDOWN ENDS",
              bool(r) and "strategy sell 80% (bar 75%)" in str(r), r)

        # E. momentum rider: 65 clears the 60 bar -> cooldown ends
        _strat.evaluate_strategy = lambda df, cfg: SimpleNamespace(
            action="sell", confidence=65, reasons=["MOMENTUM RIDER active"])
        r = ssr._cooldown_fresh_exit(_FakeKraken(_candles(_rising), 2.00),
                                     _cfg, "XRP/USD", _pos, _hold_engine)
        check("rider sell 65% clears the 60 bar -> COOLDOWN ENDS",
              bool(r) and "bar 60%" in str(r), r)

        # F. a plain 65 sell does NOT clear the 75 bar -> stay the course
        _strat.evaluate_strategy = lambda df, cfg: SimpleNamespace(
            action="sell", confidence=65, reasons=[])
        r = ssr._cooldown_fresh_exit(_FakeKraken(_candles(_rising), 2.00),
                                     _cfg, "XRP/USD", _pos, _hold_engine)
        check("plain sell 65% below the 75 bar -> STAY THE COURSE",
              r is None, r)
    finally:
        _strat.evaluate_strategy = _orig_eval

    # G. broken read fails cool -> the cooldown stands
    r = ssr._cooldown_fresh_exit(_FakeKraken(None, 0, fail=True),
                                 _cfg, "XRP/USD", _pos, _hold_engine)
    check("broken read -> fail cool (None = the cooldown holds)",
          r is None, r)
except Exception as exc:
    check("reader imports + functional checks ran", False, exc)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_cooldowncheck_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
if FAIL:
    sys.exit(1)
