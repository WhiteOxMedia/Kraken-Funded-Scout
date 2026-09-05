"""Test the LONE WOLF exception (Aug 31): flat-tide solo movers get the
threshold discount; rising/out tides get nothing; the XMR case passes."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


class FakeExchange:
    def __init__(self, pct):
        self.pct = pct
        self.fetch_calls = 0

    def fetch_ticker(self, pair):
        self.fetch_calls += 1
        return {"percentage": self.pct, "last": 1.0}


def reset(pct=0.0, pair=None):
    """Fresh cache + a fake exchange returning pct."""
    bs._lone_wolf_cache.update(pair=None, pct=0.0, ts=0.0)
    return FakeExchange(pct)


# ── 1. THE XMR CASE: FLAT tide + big own move → 35 points ─────────────
bs._tide_cache.update(ts=time.time(), pct=37.0, state="FLAT")
fx = reset(pct=11.2)
pts, note = bs.lone_wolf_discount(fx, "XMR/USD")
check("XMR case: 35 points in FLAT tide", pts == 35, f"pts={pts}")
check("note names the move", "+11%" in note, note)
check("note names the fleet", "37%" in note, note)
check("fetched the ticker once", fx.fetch_calls == 1, fx.fetch_calls)

# ── 2. mid-size mover: FLAT + 6% → 25 points ──────────────────────────
fx = reset(pct=6.0)
pts, note = bs.lone_wolf_discount(fx, "ZEC/USD")
check("FLAT +6% -> 25 points", pts == 25, f"pts={pts}")

# ── 3. small move: no discount ────────────────────────────────────────
fx = reset(pct=2.0)
pts, note = bs.lone_wolf_discount(fx, "BTC/USD")
check("FLAT +2% -> no discount", pts == 0 and note == "", f"pts={pts} note={note!r}")

# ── 4. negative move: no discount (falling knife is no lone wolf) ─────
fx = reset(pct=-7.0)
pts, note = bs.lone_wolf_discount(fx, "DOGE/USD")
check("FLAT -7% -> no discount", pts == 0, f"pts={pts}")

# ── 5. RISING tide: no discount — the fleet already carries greens ────
bs._tide_cache.update(ts=time.time(), pct=78.0, state="RISING")
fx = reset(pct=11.2)
pts, note = bs.lone_wolf_discount(fx, "XMR/USD")
check("RISING +11% -> no discount", pts == 0, f"pts={pts}")

# ── 6. OUT tide: THE LONE-WOLF CARVE-OUT (user rule, Sep 4, the POPCAT
#    lesson — the blanket OUT block buried 12 crowns while the market
#    paid): a TRUE solo runner moving against a sinking fleet earns the
#    SAME discount as FLAT (RISING still gets nothing — the fleet carries).
bs._tide_cache.update(ts=time.time(), pct=20.0, state="OUT")
fx = reset(pct=11.2)
pts, note = bs.lone_wolf_discount(fx, "XMR/USD")
check("OUT +11% -> the carve-out discount (Sep 4 rule)",
      pts == bs.LONE_WOLF_PTS_BIG and "OUT tide" in note, f"pts={pts} note={note!r}")
fx = reset(pct=5.5)
pts, note = bs.lone_wolf_discount(fx, "XMR/USD")
check("OUT +5.5% -> the small-tier carve-out",
      pts == bs.LONE_WOLF_PTS, f"pts={pts}")
fx = reset(pct=2.0)
pts, note = bs.lone_wolf_discount(fx, "DOGE/USD")
check("OUT +2% -> no discount (a bounce is no solo runner)",
      pts == 0, f"pts={pts}")

# ── 7. broken tide read: fail-closed to the normal thresholds ─────────
# (a poisoned cache just forces a recompute — a BROKEN read is
# _fleet_tide returning (None, None): DB missing / fleet too small)
_orig_fleet_tide = bs._fleet_tide
bs._fleet_tide = lambda max_age_s=300: (None, None)
fx = reset(pct=11.2)
pts, note = bs.lone_wolf_discount(fx, "XMR/USD")
check("broken tide -> no discount", pts == 0, f"pts={pts}")
bs._fleet_tide = _orig_fleet_tide

# ── 8. broken ticker: no discount, no crash ───────────────────────────
bs._tide_cache.update(ts=time.time(), pct=40.0, state="FLAT")
bs._lone_wolf_cache.update(pair=None, pct=0.0, ts=0.0)


class BrokenExchange:
    def fetch_ticker(self, pair):
        raise RuntimeError("ticker down")


pts, note = bs.lone_wolf_discount(BrokenExchange(), "XMR/USD")
check("broken ticker -> no discount, no crash", pts == 0, f"pts={pts}")

# ── 9. cache: same pair twice within TTL = ONE ticker fetch ───────────
bs._tide_cache.update(ts=time.time(), pct=37.0, state="FLAT")
fx = reset(pct=9.0)
bs.lone_wolf_discount(fx, "XMR/USD")
bs.lone_wolf_discount(fx, "XMR/USD")
check("cached pair -> one fetch", fx.fetch_calls == 1, fx.fetch_calls)

# ── 10. THE BAR MATH (Gate 2): XMR's 56% vs a relaxed 80 bar ─────────
# R2 bar ~80, 35 pts off -> max(50, 45) = 50; 56 >= 50 -> PASSES
bar = 80
thr = max(bs.LONE_WOLF_BAR_FLOOR, bar - 35)
check("bar math: 80 - 35 -> 50 (floor holds)", thr == 50, thr)
check("XMR's 56% clears the relaxed bar", 56 >= thr)

# a normal coin's bar never drops through the floor to absurdity
thr_low = max(bs.LONE_WOLF_BAR_FLOOR, 60 - 35)
check("even a 60 bar stops at the 50 floor", thr_low == 50, thr_low)

# ── 11. THE DESKTOP FLOOR MATH: 89 - 35 -> floor 55; 56% passes ──────
desk = max(bs.LONE_WOLF_DESKTOP_FLOOR, 89 - 35)
check("desktop math: 89 - 35 -> 55 (floor holds)", desk == 55, desk)
check("XMR's 56% clears the relaxed desktop floor", 56 >= desk)

# small discount case: 89 - 25 = 64, floor not needed
desk2 = max(bs.LONE_WOLF_DESKTOP_FLOOR, 89 - 25)
check("desktop math: 89 - 25 -> 64", desk2 == 64, desk2)

# ── 12. constants sanity ──────────────────────────────────────────────
check("big move > min move", bs.LONE_WOLF_MOVE_BIG > bs.LONE_WOLF_MOVE_MIN)
check("big pts > pts", bs.LONE_WOLF_PTS_BIG > bs.LONE_WOLF_PTS)
check("bar floor below desktop floor", bs.LONE_WOLF_BAR_FLOOR < bs.LONE_WOLF_DESKTOP_FLOOR)

# cleanup
bs._lone_wolf_cache.update(pair=None, pct=0.0, ts=0.0)
bs._tide_cache.update(ts=0.0, pct=None, state=None)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_lonewolf_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
