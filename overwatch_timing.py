"""OVERWATCH TIMING — the trend-ride engine: cut the loser fast, ride the winner.

    buy the lower-band pullback inside an uptrend   (entry)
    a trade that goes against you leaves at the stop (bad entry -> out quick)
    a trade that works is NOT capped                  (stay and scalp)
    the stop ratchets behind the peak                 (keep the profit)
    the trend breaking ends it                        (out before it turns)

This is the shape the user asked for, and it is the only shape measured so far that
reaches the 80% win rate they asked for:

  e3_band, stop 2.0%, trail arms at +0.4%, gives back 0.15%, no trend-break exit
    May 14 - Nov 13, 30 pairs, 8,304 trades
    win rate        82.3%
    expectancy      +0.0178% per trade  (+148% summed)
    November        +0.0379% per trade, 80.8% wins, n=442   (t = 0.79)

and it is **not armed for real orders**, because the same run says:

    max drawdown  $2,676.66  on $2,000 tickets, against a $300 cushion  (8.9x)
    worst streak  13 consecutive losers
    months        June -0.064%, October -0.051% (5 of 7 positive)
    November t    0.79 -- the positive expectancy is not distinguishable from zero

That is the whole trade-off in one line: the win rate is real, the edge is a rounding
error, and the drawdown is nine times the cushion that must never be touched. Arming
it with money would break the one rule this mission cannot break to chase a number.

So the engine runs LIVE in shadow: real 5m bars, real entry/exit decisions, recorded
to contest_timing_shadow.jsonl, no orders, no balance. `ARMED` flips only when the
shadow record itself clears the bars in `ARM_CRITERIA`, and overwatch reads
`should_arm()` every cycle.
"""
from __future__ import annotations

# ── the measured shape ────────────────────────────────────────────────────────
STOP_PCT = 2.00          # a bad entry leaves here, fast
ACTIVATE_PCT = 0.40      # the trail exists only after this much profit
GIVE_PCT = 0.15          # then it gives back at most this much from the peak
TREND_BREAK = False      # measured worse than the pure ratchet (see study)
BB_SPAN = 20             # lower Bollinger band, 20 bars
BB_MULT = 2.0
TREND_SPAN = 200         # EMA200 on 5m = the 16-hour trend
COST_PCT = 0.080         # the contest's round trip

#: Real orders stay off. The shadow run is what EARNs this flag.
ARMED = False

ARM_CRITERIA = {
    "shadow_trades": 200,        # enough closed shadow trades to judge
    "min_win_pct": 80.0,         # the user's bar
    "min_mean_net_pct": 0.05,    # and it must actually clear the cost
    "max_drawdown_usd": 150.0,   # half the cushion, never more
}

STUDY = {
    "measured_at": "2026-11-15",
    "source": "scratch/trendride_verdict.py",
    "tape": "30 pairs, 1,544,445 5m bars, May 14 - Nov 13",
    "config": "e3_band|S2.0|A0.4|G0.15",
    "trades": 8304,
    "win_pct": 82.3,
    "mean_net_pct": 0.0178,
    "november": {"n": 442, "win_pct": 80.8, "mean_net_pct": 0.0379, "t": 0.79},
    "monthly_positive": "5/7",
    "worst_month_pct": -0.0637,
    "worst_loss_streak": 13,
    "max_drawdown_usd_2k": 2676.66,
    "cushion_usd": 300.0,
    "verdict": "82.3% wins and positive expectancy, but the drawdown is 8.9x the "
               "cushion and November's t is 0.79: shadow-only until it proves itself",
    "why_not_armed": "the death-line outranks the win rate",
}


def _ema(values, span):
    a = 2.0 / (span + 1.0)
    out = values[0]
    for x in values[1:]:
        out = a * x + (1.0 - a) * out
    return out


def _ema_series(values, span):
    a = 2.0 / (span + 1.0)
    out = [values[0]]
    for x in values[1:]:
        out.append(a * x + (1.0 - a) * out[-1])
    return out


def band_levels(closes, span: int = BB_SPAN, mult: float = BB_MULT):
    """Lower Bollinger band for the last bar. Returns (lower, mid) or None."""
    if len(closes) < span:
        return None
    win = closes[-span:]
    mid = sum(win) / span
    var = sum((x - mid) ** 2 for x in win) / span
    return mid - mult * (var ** 0.5), mid


def entry_ok(closes, highs, lows, opens, *, verbose=False):
    """THE ENTRY (e3_band): lower-band pullback inside an uptrend.

    All inputs are 5m bars in order, the last one being the bar that just closed.
    The trade would be taken at the following open. Long only.
    """
    n = len(closes)
    if n < TREND_SPAN + 2:
        return False, "not enough history"
    trend = _ema(closes[-TREND_SPAN * 2:] if n > TREND_SPAN * 2 else closes,
                 min(TREND_SPAN, n - 1))
    c, o, lo = closes[-1], opens[-1], lows[-1]
    if c <= trend:
        return False, "below the 200-EMA trend"
    band = band_levels(closes)
    if band is None:
        return False, "no band"
    lower, _mid = band
    if not (lo <= lower and c > lower):
        return False, "no lower-band touch"
    if c <= o:
        return False, "bar did not close up"
    return True, "band pullback in uptrend"


def manage(*, entry, peak, stop, bar_high, bar_low, bar_close):
    """One bar for an open position. Returns (new_peak, new_stop, exit_price|None).

    The rules, in order: the stop is hit -> out; otherwise the peak advances and,
    once the trade has earned ACTIVATE_PCT, the stop ratchets to peak*(1-GIVE_PCT)
    and never moves down. Nothing caps the upside.
    """
    if bar_low <= stop:
        return peak, stop, stop
    peak = max(peak, bar_high)
    gain = (peak / entry - 1.0) * 100.0
    if gain >= ACTIVATE_PCT:
        cand = peak * (1.0 - GIVE_PCT / 100.0)
        if cand > stop:
            stop = cand
    if TREND_BREAK and bar_close > entry:
        # (disabled by default; the study found the pure ratchet better)
        pass
    return peak, stop, None


def initial_stop(entry: float) -> float:
    return entry * (1.0 - STOP_PCT / 100.0)


def net_pct(entry: float, exit_px: float) -> float:
    return (exit_px / entry - 1.0) * 100.0 - COST_PCT


def should_arm(shadow: dict | None = None) -> tuple[bool, str]:
    """Overwatch asks this every cycle. Shadow record is the only key."""
    if ARMED:
        return True, "armed"
    s = shadow or {}
    n = int(s.get("trades", 0))
    win = float(s.get("win_pct", 0.0))
    net = float(s.get("mean_net_pct", 0.0))
    dd = float(s.get("max_drawdown_usd", 0.0))
    c = ARM_CRITERIA
    gaps = []
    if n < c["shadow_trades"]:
        gaps.append(f"{n}/{c['shadow_trades']} shadow trades")
    if win < c["min_win_pct"]:
        gaps.append(f"win {win:.1f}% < {c['min_win_pct']}%")
    if net < c["min_mean_net_pct"]:
        gaps.append(f"net {net:+.3f}% < {c['min_mean_net_pct']}%")
    if dd > c["max_drawdown_usd"]:
        gaps.append(f"DD ${dd:.0f} > ${c['max_drawdown_usd']:.0f}")
    if gaps:
        return False, "shadow gate: " + "; ".join(gaps)
    return False, "shadow gate met — arming needs a human decision (set ARMED)"


def shadow_verdict(shadow: dict | None = None) -> dict:
    """The numbers overwatch prints on its plate."""
    s = shadow or {}
    return {
        "state": "shadow",
        "trades": int(s.get("trades", 0)),
        "win_pct": round(float(s.get("win_pct", 0.0)), 1),
        "mean_net_pct": round(float(s.get("mean_net_pct", 0.0)), 4),
        "max_drawdown_usd": round(float(s.get("max_drawdown_usd", 0.0)), 2),
        "armed": ARMED,
        "why": should_arm(shadow)[1],
        "measured": {"win_pct": STUDY["win_pct"],
                     "nov_net_pct": STUDY["november"]["mean_net_pct"]},
    }
