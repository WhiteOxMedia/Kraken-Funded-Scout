"""Test THE PER-MARKET GOAL + ROTATION-BOUNDED WINDOW + THE BOUNDARY
EXCEPTION (user rule, Sep 4: "target each market separately — what were
those assets doing at this time all week? use 24 hour candles, scan 1
week, the goal should reach before the rotation happens ideally. If
exception is made a warning fires with the rotation if still tracking
asset, full exit scan occurs to trigger a warning light if necessary").

  - GOAL: the asset's OWN week — 7 daily candles, the MEDIAN DAILY RANGE
    (the room the coin actually gives), clamped [1, 3]. The engine's
    per-rotation cell is the fallback (young assets, broken fetches).
  - WINDOW: bounded by the NEXT ROTATION BOUNDARY — min(180, max(60,
    minutes to the boundary)). Entering near a boundary makes the
    EXCEPTION (the window crosses into the next rotation).
  - THE EXCEPTION: on the rotation rollover, every still-tracked asset
    (bridge positions + contest watch) gets the warning in the one
    voice, and the FULL exit scan is forced immediately (not the 5-min
    cadence) — its verdicts are the warning light."""
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

MST = ZoneInfo("America/Denver")
src = Path("background_scanner.py").read_text(encoding="utf-8")
ap = Path("app.py").read_text(encoding="utf-8")


def day_candles(ranges):
    """7 daily candles whose (high-low)/open daily RANGE is the given %."""
    out = []
    t0 = 1_700_000_000_000
    for i, r in enumerate(ranges):
        o = 100.0
        half = o * (r / 200.0)          # (h-l)/o = r%  ->  half-range each side
        out.append([t0 + i * 86_400_000, o, o + half, o - half, o, 1000])
    return out


class FakeExchange:
    def __init__(self, candles):
        self.candles = candles
        self.fetch_calls = 0

    def fetch_ohlcv(self, pair, timeframe="1d", limit=8):
        self.fetch_calls += 1
        return self.candles


def goal(candles):
    bs._market_goal_cache.pop("T/USD", None)
    return bs._market_goal_pct(FakeExchange(candles), "T/USD")


# the ENGINE side for the test pair — read live (the universe median for
# this rotation when the asset has no own cell); both sides of the min()
# use the same value, so the assertions are exact
ENG = bs._window_goal_pct("T/USD")
check("the engine side resolves (the universe median for unknown pairs)",
      1.0 <= ENG <= 3.0, ENG)

# ── 1. the goal: the ENGINE number, capped by the asset's week ───────
check("a generous week (4%/day) keeps the ENGINE number (keep what won)",
      abs(goal(day_candles([4.0] * 7)) - ENG) < 0.01, goal(day_candles([4.0] * 7)))
check("a quiet week (0.8%/day) caps the goal at 0.8 -> the 1% floor",
      abs(goal(day_candles([0.8] * 7)) - 1.0) < 0.01)
check("the week caps a stale engine ask: 1.2%/day week -> 1.2 goal",
      abs(goal(day_candles([1.2] * 7)) - 1.2) < 0.01)
check("the MEDIAN day rules (one wild day doesn't lift the cap)",
      abs(goal(day_candles([1.2, 1.2, 1.2, 30.0, 1.2, 1.2, 1.2])) - 1.2) < 0.01)
check("the cap never exceeds the engine number (a 2.5%/day week, engine 1.5 -> 1.5)",
      abs(goal(day_candles([2.5] * 7)) - ENG) < 0.01)


class BrokenExchange:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("api down")


bs._market_goal_cache.pop("T/USD", None)
_g = bs._market_goal_pct(BrokenExchange(), "T/USD")
check("a broken week read leaves the ENGINE number alone (yesterday's logic, unchanged)",
      abs(_g - ENG) < 0.01, _g)
fx = FakeExchange(day_candles([1.2] * 7))
bs._market_goal_cache.pop("T/USD", None)
bs._market_goal_pct(fx, "T/USD")
bs._market_goal_pct(fx, "T/USD")
check("cached 15 min per pair (1 fetch for 2 calls)", fx.fetch_calls == 1, fx.fetch_calls)
check("the old derivation survives untouched as the engine side",
      "def _window_goal_pct(pair: str) -> float:" in src)

# ── 2. the rotation-bounded window ───────────────────────────────────
def win(h, m):
    return bs._rotation_window_min(datetime(2026, 9, 4, h, m, tzinfo=MST))


check("rotation start (12:00) -> the full 180 cap", win(12, 0) == 180.0)
check("12:30 -> 180 (210 min left, capped)", win(12, 30) == 180.0)
check("mid-rotation 10:56 -> 64 (the remaining time)", win(10, 56) == 64.0)
check("mid-rotation 09:15 -> 165", win(9, 15) == 165.0)
check("near the boundary 11:50 -> 60 (the EXCEPTION: crosses into the next rotation)",
      win(11, 50) == 60.0)
check("03:20 -> 60 (exception before the 04:00 boundary)", win(3, 20) == 60.0)
check("23:30 -> 60 (exception across midnight)", win(23, 30) == 60.0)
check("the window never exceeds the standing 3h cap",
      all(win(h, 0) <= 180.0 for h in range(24)))
check("the window never drops below the 1h floor",
      all(win(h, 59) >= 60.0 for h in range(24)))

# ── 3. the wiring: every goal clock uses the market number ───────────
check("the champion popup: the engine goal capped by the week + the rotation-bounded window",
      "_goal_pct = _market_goal_pct(exchange, pair)" in src
      and "_win_min = _rotation_window_min()" in src)
check("the mover popup: same math",
      "_m_goal = _market_goal_pct(exchange, pair)" in src
      and "_m_win = _rotation_window_min()" in src)
check("the bot synthesis: the goal is the capped engine number; the window is the ENTRY's rotation deadline",
      "_market_goal_pct(exchange, pair)" in src
      and "_rotation_window_min(_ad)" in src)
check("the QC green light derives the market goal",
      "_bs._market_goal_pct(kraken, pair)" in ap)
check("the QC watch record stores the rotation-bounded window",
      "_bs._rotation_window_min()" in ap)
check("_derive_goal prefers the market read when an exchange is available",
      "def _derive_goal(pair: str, kraken=None)" in ap
      and "_bs._market_goal_pct(kraken, pair)" in ap)
check("_derive_goal's no-exchange fallback keeps the engine cell (the entry control)",
      "g = _bs._window_goal_pct(pair)" in ap)
check("the card shows the market goal",
      "_bsg._market_goal_pct(kraken, f\"{h['asset']}/USD\")" in ap)

# ── 4. the boundary exception ────────────────────────────────────────
_i0 = src.find("ROTATION-BOUNDARY EXCEPTION")
_blk = src[_i0:_i0 + 2600] if _i0 >= 0 else ""
check("the boundary exception block exists at the rotation rollover",
      _i0 >= 0 and "Rotation %s -> %s" in src[:_i0])
check("still-tracked BOT positions force the FULL exit scan immediately",
      "last_exit_check = 0" in _blk and "get_open_positions" in _blk)
check("still-tracked CONTEST watch assets get the warning too",
      "_load_contest_watch()" in _blk)
check("the warning fires in the ONE voice",
      "_speak_server(" in _blk and "Rotation passed" in _blk)
check("the boundary handler sits in the block-change branch (fires once per rollover)",
      0 < src.find("ROTATION-BOUNDARY EXCEPTION") - src.find("if current_block_name is not None and block_name != current_block_name:") < 400)

# ── 5. behavior: the exception window crosses the boundary ───────────
def crossed(h, m):
    """Does a window opened at h:m cross the next rotation boundary?"""
    w = win(h, m)
    mins_left = (4 - (h % 4)) * 60 - m
    return w > mins_left


check("11:50 entry: the window crosses the 12:00 boundary (the exception)",
      crossed(11, 50))
check("10:56 entry: the window fits before 12:00 (the ideal)",
      not crossed(10, 56))
check("12:00 entry: 180 min ends exactly at the 15:00 boundary (the ideal)",
      not crossed(12, 0))
check("03:30 entry: crosses into the 04:00 rotation",
      crossed(3, 30))

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_marketgoal_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
