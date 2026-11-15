"""OVERWATCH TIMING — the fast buy-the-dip / sell-into-strength engine (Nov 15).

The user's rule: enter on a timed dip and get out fast, so that most trades are
profitable by a little rather than few by a lot. This module is that engine, and
it is also the measurement of why it is **not armed**.

What it does
  entry  the coin is above its 4h and 24h means (uptrend), the last N closes made
         a fresh N-bar low (the dip), and the entry bar turns back up;
  exit   sell at a small target, stop at the stop, or leave on the candle timeout.

What the tape says (scratch/timing_geometry.py, 216 configurations, 55 symbols,
Sep 3 - Nov 15, 0.080% round trip):
  * 80% wins IS reachable — `momentum24, dip 2, target 0.15%, stop 2.0%, 24 candles`
    wins **80.5%** of 6,046 trades;
  * and it loses **-$726.90** (-$0.120 per trade). Every one of the 216 shapes is
    negative, the best of them -$514 over 4,365 trades. None is positive on the
    train months, none on November.

Why that is structural, not bad luck
  A win rate p with a target T and a stop S breaks even when p*T = (1-p)*S. For
  p = 80% that requires S = 4T — four units of loss for one of gain. On a move
  with no directional edge, the touch order of two barriers is a martingale, so
  the shape that wins 80% of the time by construction also breaks even before
  costs on average. The 0.080% round trip is then paid every time, and the entry
  has no measured edge to pay it with. **Win rate is a shape, not an edge.**

So the parameters live here, tested and documented, behind `ARMED = False`.
Arming requires one thing the studies have not produced: an entry with positive
directional edge at this cost. Until then, this module answers questions and never
places or closes anything.
"""
from __future__ import annotations

# ── the shape, and where it comes from ────────────────────────────────────────
DIP_BARS = 2                 # a fresh 2-bar low is the dip
TARGET_PCT = 1.50            # sell high: the best net of the shapes measured
STOP_PCT = 1.20              # tighter than the contest's 3%: this is a fast trade
TIMEOUT_BARS = 8             # 40 minutes on the 5m book
TREND_HOURS = 24             # the uptrend filter the study used
COST_PCT = 0.080

#: ARMED IS THE SWITCH THAT WOULD PUT THIS IN THE LIVE LOOP. It stays False until a
#: study shows positive out-of-sample expectancy at this cost. 216 measured shapes
#: were all negative, so arming it today would spend the $300 cushion at roughly
#: -$0.12 per trade — the contest would be lost in days, not weeks.
ARMED = False

#: The measured rejection, kept beside the switch so nobody has to go looking.
STUDY = {
    "measured_at": "2026-11-15",
    "source": "scratch/timing_geometry.py",
    "configurations": 216,
    "symbols": 55,
    "window": ["2026-09-03", "2026-11-15"],
    "cost_pct": COST_PCT,
    "best_80pct_shape": {"name": "momentum24_d2_t0.15_s2_k24", "win_pct": 80.5,
                         "trades": 6046, "net": -726.90, "per_trade": -0.120},
    "best_net_any_shape": {"name": "momentum24_d3_t0.3_s1_k24", "net": -514.40,
                           "win_pct": 65.3, "trades": 4365},
    "positive_on_train": 0,
    "positive_on_november": 0,
    "verdict": "rejected: an 80% win shape over a driftless move is break-even "
               "before cost and negative after it",
    # the follow-up search for an entry signal with actual directional edge
    "orderflow_search": {
        "source": "scratch/orderflow_study.py",
        "tape": "30 pairs, 1,544,445 5m bars, May 14 - Nov 13, volume on 100%",
        "signals": ["ofi3", "ofi12", "vdelta12", "volz", "absorb", "dipvol",
                    "wick3", "vwapdev", "rangex", "ret6", "ret12"],
        "horizons_min": [30, 60],
        "holdout": "Nov 1 - Nov 13, 105,328 bars, edges fitted on May-Oct only",
        "clearing": 0,
        "largest_quintile_spread_pct": 0.03,
        "verdict": "rejected: every quintile of every signal nets -0.07 to -0.19%, "
                   "and top-minus-bottom quintile separation is <=0.03% and "
                   "inconsistent in sign between train and November - no edge, "
                   "so nothing to wire",
        "absent_from_repo": ["funding", "open interest", "spread history",
                             "book/depth history"],
    },
}


def breakeven_target(stop_pct: float, win_rate: float) -> float:
    """The gross target that just breaks even at this win rate and stop."""
    p = max(1e-6, min(1.0, float(win_rate)))
    return float(stop_pct) * (1.0 - p) / p


def required_stop(target_pct: float, win_rate: float) -> float:
    """The stop that goes with a target at this win rate (80% -> 4x the target)."""
    p = max(1e-6, min(1.0, float(win_rate)))
    return float(target_pct) * p / (1.0 - p)


def entry_ok(closes, lows, *, dip_bars: int = DIP_BARS,
             trend_bars: int = TREND_HOURS * 12) -> bool:
    """The entry, on plain lists of 5m closes/lows ending at the entry bar.

    Above both means, a fresh `dip_bars` low inside the current bar, and the entry
    bar closing up. Pure: no I/O, no state, no order.
    """
    n = len(closes)
    if n < max(trend_bars, 4 * 12, dip_bars + 2):
        return False
    price = float(closes[-1])
    if price <= 0:
        return False
    m4h = sum(closes[-4 * 12:]) / (4 * 12)
    m24h = sum(closes[-trend_bars:]) / trend_bars
    if price <= m4h or price <= m24h:
        return False
    prior_low = min(float(x) for x in lows[-(dip_bars + 1):-1])
    if not float(lows[-1]) < prior_low:      # the bar must make a NEW low
        return False
    return float(closes[-1]) > float(closes[-2])


def exit_pct(*, held_bars: int, entry: float, high: float, low: float, close: float,
             target_pct: float = TARGET_PCT, stop_pct: float = STOP_PCT,
             timeout_bars: int = TIMEOUT_BARS) -> float | None:
    """None = hold. Otherwise the percentage this bar would bank, net of cost."""
    if entry <= 0:
        return None
    if high >= entry * (1.0 + target_pct / 100.0):
        return target_pct - COST_PCT
    if low <= entry * (1.0 - stop_pct / 100.0):
        return -stop_pct - COST_PCT
    if held_bars >= timeout_bars:
        return (close / entry - 1.0) * 100.0 - COST_PCT
    return None


def should_arm() -> tuple[bool, str]:
    """Why the live loop may or may not use this. Overwatch reads this."""
    if ARMED:
        return True, "armed by config"
    return False, (f"held: {STUDY['positive_on_train']} of {STUDY['configurations']} "
                   f"shapes were positive on train and "
                   f"{STUDY['positive_on_november']} on November; "
                   f"80.5% wins still lost ${-STUDY['best_80pct_shape']['net']:.2f}; "
                   f"{STUDY['orderflow_search']['clearing']} of "
                   f"{len(STUDY['orderflow_search']['signals'])} order-flow signals "
                   f"cleared the November holdout")
