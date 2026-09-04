"""Test THE TWO-LEDGER SELL GATE + THE RETIRED TREND-EXIT CHANNEL
(rewritten Sep 4 after the user decision: "keep the simpler Sep 3 bot —
the rotation design replaces the old rules").

What stands:
  - TWO-LEDGER (Aug 30): the reader admits sells from exactly ONE
    source — scout_exit_check (write_exit_signal's default) — the
    scanner's 5-min exit check: trailing stop, strategy sell (>=75,
    >=60 momentum), exhaustion, momentum-rider exits. Every other sell
    source is contest-only and never touches real money.
  - The knife itself (knife_reversed_1h) is kept and still tested below
    — the BOT'S BOOK panel uses it for the informational trend state.

What is retired (Sep 4 decision, code kept per the don't-delete rule):
  - The silent scout_trend_exit channel: the Sep 3 reader never admits
    that source, so the scanner's TREND-EXIT WATCH is disabled behind
    SCOUT_TREND_EXIT_ENABLED=False.
  - The bot's engine knife floor (_trend_reversed_1h): gone from the
    Sep 3 bot_engine; the exit floor is the engine's own
    SL/TP/ATR-trail stack + the 5-min exit check."""
import sys
import time
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


import background_scanner as bs


class _Cfg:
    ema_short = 9
    ema_long = 21


def series(start, end, n=80):
    import time as _t
    step = (end - start) / (n - 1)
    t0 = int(_t.time() * 1000) - n * 3_600_000
    return [[t0 + i * 3_600_000, 1, 1, 1, start + i * step, 100] for i in range(n)]


class FakeExchange:
    def __init__(self, candles):
        self.candles = candles
        self.fetch_calls = 0

    def fetch_ohlcv(self, pair, timeframe="1h", limit=100):
        self.fetch_calls += 1
        return self.candles


def knife(candles):
    bs._trend_exit_cache.pop("TEST/USD", None)
    return bs.knife_reversed_1h(FakeExchange(candles), "TEST/USD", _Cfg())


# ── 1. the knife itself — kept, still correct (the panel uses it) ────
rev, note = knife(series(2.0, 1.0))
check("declining 1h -> reversed", rev is True, note)
check("note names the knife", "EMA9" in note and "EMA21" in note, note)
rev, note = knife(series(1.0, 2.0))
check("rising 1h -> not reversed", rev is False, note)
rev, note = knife([[0, 1, 1, 1, 1.0, 10]] * 10)
check("short data -> fail-closed, not cached", rev is False, note)
fx = FakeExchange(series(2.0, 1.0))
bs._trend_exit_cache.pop("TEST/USD", None)
bs.knife_reversed_1h(fx, "TEST/USD", _Cfg())
bs.knife_reversed_1h(fx, "TEST/USD", _Cfg())
check("knife cached per pair (1 fetch / 5 min)", fx.fetch_calls == 1, fx.fetch_calls)


class RaisingExchange:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("api down")


bs._trend_exit_cache.pop("TEST/USD", None)
rev, note = bs.knife_reversed_1h(RaisingExchange(), "TEST/USD", _Cfg())
check("broken read -> fail-closed (no exit on a broken read)", rev is False and note == "")

# ── 2. the watch is RETIRED behind the flag (Sep 4 decision) ─────────
src = open("background_scanner.py", encoding="utf-8").read()
check("SCOUT_TREND_EXIT_ENABLED exists and is False (channel retired)",
      getattr(bs, "SCOUT_TREND_EXIT_ENABLED", None) is False)
check("the watch loop is gated on the flag",
      "if SCOUT_TREND_EXIT_ENABLED:" in src)
check("the write machinery is kept (don't-delete rule)",
      'source="scout_trend_exit"' in src and "_trend_exit_pace" in src)
_i = src.find("TREND-EXIT WATCH")
check("the retirement is documented at the watch",
      _i >= 0 and "RETIRED" in src[_i:_i + 900])

# ── 3. the bridge contract: dup-safe + default source ────────────────
import signal_bridge as sb2
_sigt = open("signal_bridge.py", encoding="utf-8").read()
check("write_exit_signal is dup-safe (skips while a sell pends)",
      "Exit signal already pending" in _sigt)
check("write_exit_signal defaults to scout_exit_check (the ONE admitted source)",
      'source: str = "scout_exit_check"' in _sigt)

# ── 4. the reader's two-ledger sell gate (THE LIVE copy) ─────────────
rd = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py",
          encoding="utf-8").read()
check("the reader admits sells from exactly ONE source: scout_exit_check",
      'elif action == "sell" and source == "scout_exit_check":' in rd)
check("scout_trend_exit is NOT admitted (retired channel)",
      'scout_trend_exit' not in rd)
check("the kill switch still lets exits through (sell filter, not a halt)",
      "pending = [s for s in pending if s.get(\"action\") == \"sell\"]" in rd)
check("sell path handles manual Kraken positions (not in bot DB)",
      "not in bot DB but Kraken balance" in rd)
check("the 30-min sell cooldown still gates exits",
      "COOLDOWN: Don't sell within 30 minutes of buying" in rd)
check("engine Insufficient is the ONLY other exit-side rotation trigger",
      rd.count("weakest = _find_weakest_position(kraken, config)") == 2)

# ── 5. the console panel (informational knife state) ─────────────────
ap = open("app.py", encoding="utf-8").read()
check("BOT'S BOOK panel exists", "def _render_bots_book" in ap)
check("panel wired next to her words",
      0 < ap.find("_render_her_words()") < ap.find("_render_bots_book(kraken, cfg)"))
check("panel reads the bridge holdings",
      "get_open_positions" in ap[ap.find("def _render_bots_book"):ap.find("def _toast_new_her_words")])
check("panel shows the riding vs reversed LEDs",
      "🟢 riding" in ap and "REVERSED" in ap)
check("panel no longer claims GET OUT SENT (channel retired)",
      "GET OUT SENT" not in ap)
check("panel reuses the scanner's knife (dup-safe reload)",
      "knife_reversed_1h" in ap)

# ── 6. the engine exit floor that remains (no knife) ─────────────────
be = open(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\bot_engine.py",
          encoding="utf-8").read()
check("the engine knife is retired (user decision, Sep 4)",
      "_trend_reversed_1h" not in be and "GET OUT NOW — trend reversed" not in be)
check("the engine's own exit stack remains the floor: SL",
      "stop_loss_pct" in be)
check("the engine's own exit stack remains the floor: TP",
      "take_profit_pct" in be)
check("the engine's own exit stack remains the floor: ATR trail",
      "trailing_stop_atr_mult" in be)
check("the scanner's 5-min exit check still emits: trailing stop",
      "TRAILING STOP hit" in src)
check("the scanner's 5-min exit check still emits: strategy sell threshold",
      "sell_threshold = 60 if is_momentum_pos else 75" in src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_trendbridge_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
