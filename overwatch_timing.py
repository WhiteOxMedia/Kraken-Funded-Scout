"""OVERWATCH TIMING — the trend-ride engine: cut the loser fast, ride the winner.

    buy the lower-band pullback inside an uptrend    (entry)
    a trade that goes against you leaves at the stop  (bad entry -> out quick)
    a trade that works is NOT capped                  (stay and scalp)
    the stop ratchets behind the peak                 (keep the profit)

There is deliberately no target and no trend-break exit: the study measured the
EMA20 trend-break exit as worse than the pure ratchet on every entry variant
(`scratch/trendride_study.py`), so it is not in the engine at all rather than sitting
dead behind a flag.

The shape, measured on the code that actually runs (`scratch/remeasure_live_config.py`:
`entry_ok` on the 320-bar window, `manage` with no hold cap, 30 pairs, 1,544,445 5m
bars, May 14 - Nov 13):

    trades          15,877
    win rate        84.1%          (train May-Oct 84.4%, November 77.2%)
    expectancy      +0.0269% per trade  (train +0.0355%, t +4.58)
    November        -0.1294% per trade, n=821, t -3.40
    monthly         4 of 7 positive; worst loss streak 19

November is not noise here: t = -3.40 says this config loses money out of sample.
The death line agrees — walked forward from the live balance with the repo's own
sizing rule, every size tested reaches the halt line:

    size      profit over the sample    lowest balance    room above the halt line
    1%        +$435.30                  $9,817.48         -$32.52   halted
    2%        +$867.54                  $9,700.21         -$149.79  halted (at the line)
    5%        +$795.09                  $9,700.00         -$150.00  halted

So the engine is measured, not armable. `STUDY` previously advertised the study's
band (EMA20 as the mid, sd around the SMA, full-series EMA, 288-bar cap) at 8,304
trades / 82.3% / November +0.0379% — a band this code never implemented. The parity
harness (`scratch/trendride_parity.py`) proved the difference by firing 8,304 vs
15,877 on the same tape; that record is kept under `STUDY["superseded"]` rather than
deleted, so the history cannot quietly resurrect.

OWNERSHIP: this module restates none of the death-line or sizing arithmetic. The
death line, halt line, buffer and headroom come from `contest_account` and
`death_floor`; a ticket comes from `contest_account.sized_entry_usd`; the round-trip
cost comes from `contest_account.COST_ROUNDTRIP_PCT`.
"""
from __future__ import annotations

import contest_account as ca      # COST_ROUNDTRIP_PCT, buffer(), sized_entry_usd()
import death_floor as df          # death_line(), halt_line() and the state that holds them

# ── the measured shape ────────────────────────────────────────────────────────
STOP_PCT = 2.00          # a bad entry leaves here, fast
ACTIVATE_PCT = 0.40      # the trail exists only after this much profit
GIVE_PCT = 0.15          # then it gives back at most this much from the peak
BB_SPAN = 20             # lower Bollinger band, 20 bars
BB_MULT = 2.0
TREND_SPAN = 200         # EMA200 on 5m = the 16-hour trend

#: The contest's round-trip cost, owned by contest_account — never restated here.
COST_PCT = ca.COST_ROUNDTRIP_PCT

#: One ticket as a fraction of the balance. Measured: even 1% breaches the halt
#: line on this tape, so this is the smallest size that is worth a fill, not a safe one.
SIZE_FRAC = 0.01

#: Real orders stay off. The shadow record is what EARNS this flag.
ARMED = False

ARM_CRITERIA = {
    "shadow_trades": 200,        # enough closed shadow trades to judge
    "min_win_pct": 80.0,         # the user's bar
    "min_mean_net_pct": 0.05,    # and it must actually clear the cost
    "min_headroom_usd": 100.0,   # the record's lowest point vs the halt line
    "min_buffer_usd": 250.0,     # live buffer now; it grows with every win
}

STUDY = {
    "measured_at": "2026-11-15",
    "source": "scratch/remeasure_live_config.py",
    "tape": "30 pairs, 1,544,445 5m bars, May 14 - Nov 13",
    "config": ("e3_band|SMA20 mid (the live band)|S2.0|A0.4|G0.15|"
               "320-bar window|no hold cap"),
    "trades": 15877,
    "win_pct": 84.1,
    "mean_net_pct": 0.0269,
    "t": 3.54,
    "train": {"n": 15056, "win_pct": 84.4, "mean_net_pct": 0.0355, "t": 4.58},
    "november": {"n": 821, "win_pct": 77.2, "mean_net_pct": -0.1294, "t": -3.4},
    "monthly_positive": "4/7",
    "worst_month_pct": -0.1294,
    "worst_loss_streak": 19,
    # recorded values at measurement time (a record, never used as arithmetic)
    "death_line_usd": 9700.0,
    "halt_line_usd": 9850.0,
    "paths": {
        "1pct": {"profit_usd": 435.30, "lowest_usd": 9817.48, "room_usd": -32.52,
                 "halted": True},
        "2pct": {"profit_usd": 867.54, "lowest_usd": 9700.21, "room_usd": -149.79,
                 "halted": True},
        "5pct": {"profit_usd": 795.09, "lowest_usd": 9700.00, "room_usd": -150.00,
                 "halted": True},
    },
    "superseded": {
        "config": "EMA20-mid band, sd around the SMA, full-series EMA, 288-bar cap",
        "source": "scratch/trendride_verdict.py",
        "trades": 8304,
        "win_pct": 82.3,
        "november_net_pct": 0.0379,
        "why": "the study's band, never implemented by entry_ok/band_lower; kept for "
               "provenance after trendride_parity.py showed 8,304 vs 15,877",
    },
    "verdict": "not armable: 84.1% wins and a positive train, but November is "
               "-0.1294%/trade at t -3.40 and even a 1% ticket breaches the halt line "
               "on this tape",
    "next": "the difference is the band definition; deciding whether the live SMA band "
            "or the study's EMA-mid band is the intended indicator is a design call, "
            "not a tuning step",
}


def ema(values, span: int) -> float:
    """Last value of an EMA over `values` — the whole series, as the study did."""
    a = 2.0 / (span + 1.0)
    out = values[0]
    for x in values[1:]:
        out = a * x + (1.0 - a) * out
    return out


def band_lower(closes) -> float | None:
    """Lower Bollinger band of the last bar, or None if there is not a full window."""
    if len(closes) < BB_SPAN:
        return None
    win = closes[-BB_SPAN:]
    mid = sum(win) / BB_SPAN
    var = sum((x - mid) ** 2 for x in win) / BB_SPAN
    return mid - BB_MULT * (var ** 0.5)


def entry_ok(closes, highs, lows, opens) -> tuple[bool, str]:
    """THE ENTRY (e3_band): a lower-band pullback inside an uptrend. Long only.

    The last bar of each series is the one that just closed; the trade would be taken
    at the following open.
    """
    if len(closes) < TREND_SPAN + 2:
        return False, "not enough history"
    if closes[-1] <= ema(closes, TREND_SPAN):
        return False, "below the 200-EMA trend"
    lower = band_lower(closes)
    if lower is None:
        return False, "no band"
    if not (lows[-1] <= lower < closes[-1]):
        return False, "no lower-band touch"
    if closes[-1] <= opens[-1]:
        return False, "bar did not close up"
    return True, "band pullback in uptrend"


def manage(*, entry, peak, stop, bar_high, bar_low):
    """One bar for an open position: (peak, stop, exit_price, reason).

    The stop is hit first -> out. Otherwise the peak advances and, once the trade has
    earned ACTIVATE_PCT, the stop ratchets to peak*(1-GIVE_PCT) and never moves down.
    Nothing caps the upside. reason is None while the position stays open.
    """
    if bar_low <= stop:
        reason = "stop" if stop <= initial_stop(entry) else "trail"
        return peak, stop, stop, reason
    peak = max(peak, bar_high)
    if (peak / entry - 1.0) * 100.0 >= ACTIVATE_PCT:
        stop = max(stop, peak * (1.0 - GIVE_PCT / 100.0))
    return peak, stop, None, None


def initial_stop(entry: float) -> float:
    return entry * (1.0 - STOP_PCT / 100.0)


def net_pct(entry: float, exit_px: float) -> float:
    return (exit_px / entry - 1.0) * 100.0 - COST_PCT


def death_context(acct: dict | None = None) -> dict:
    """The live numbers, as death_floor and contest_account define them.

    `buffer` is `contest_account.buffer()` — the money between the balance and the
    -3% death line. It is $300 at the start and grows with every winning trade, which
    is why risk is measured against the line and never against a fixed figure.
    """
    st = acct or ca.load_state()
    floor = df.state(st)
    return {"start": float(st["start_balance"]),
            "balance": float(st["current_balance"]),
            "death_line": floor["death_line"],
            "halt_line": floor["halt_line"],
            "buffer": float(ca.buffer(st)),
            "headroom": floor["headroom_to_halt_usd"]}


def ticket_usd(balance: float, start: float) -> float:
    """What one trade may spend: the repo's sizing rule, capped at SIZE_FRAC."""
    return ca.sized_entry_usd(balance, ca.DEFAULT_WINDOW_DD_PCT, start, SIZE_FRAC)


def should_arm(shadow: dict | None = None,
               ctx: dict | None = None) -> tuple[bool, str]:
    """Overwatch asks this every cycle: the floor first, then the shadow record."""
    ctx = ctx or death_context()
    if ctx["balance"] <= ctx["halt_line"]:
        return False, (f"at/below the halt line ${ctx['halt_line']:,.2f} — "
                       f"nothing new is risked there, ever")
    if ctx["buffer"] < ARM_CRITERIA["min_buffer_usd"]:
        return False, (f"buffer ${ctx['buffer']:,.2f} is under the "
                       f"${ARM_CRITERIA['min_buffer_usd']:,.0f} floor — the room "
                       f"above the death line pays for the next loss")
    if ARMED:
        return True, f"armed at {SIZE_FRAC * 100:.0f}% of balance"

    s, c = shadow or {}, ARM_CRITERIA
    n, win = int(s.get("trades", 0)), float(s.get("win_pct", 0.0))
    net, room = float(s.get("mean_net_pct", 0.0)), float(s.get("min_headroom_usd", 0.0))
    gaps = []
    if n < c["shadow_trades"]:
        gaps.append(f"{n}/{c['shadow_trades']} shadow trades")
    if win < c["min_win_pct"]:
        gaps.append(f"win {win:.1f}% < {c['min_win_pct']}%")
    if net < c["min_mean_net_pct"]:
        gaps.append(f"net {net:+.3f}% < {c['min_mean_net_pct']}%")
    if room and room < c["min_headroom_usd"]:
        gaps.append(f"room ${room:.0f} < ${c['min_headroom_usd']:.0f}")
    if gaps:
        return False, "shadow gate: " + "; ".join(gaps)
    return False, ("shadow gate met — arming needs the single execution path "
                   "(one writer per API key), not a second trader")


def shadow_verdict(shadow: dict | None = None, ctx: dict | None = None) -> dict:
    """What overwatch prints on its plate."""
    s = shadow or {}
    ctx = ctx or death_context()
    return {
        "state": "live" if ARMED else "shadow",
        "trades": int(s.get("trades", 0)),
        "win_pct": round(float(s.get("win_pct", 0.0)), 1),
        "mean_net_pct": round(float(s.get("mean_net_pct", 0.0)), 4),
        "record_low_room_usd": round(float(s.get("min_headroom_usd", 0.0)), 2),
        "buffer_usd": round(ctx["buffer"], 2),
        "death_line_usd": round(ctx["death_line"], 2),
        "halt_line_usd": round(ctx["halt_line"], 2),
        "headroom_usd": round(ctx["headroom"], 2),
        "size_frac": SIZE_FRAC,
        "armed": ARMED,
        "why": should_arm(shadow, ctx)[1],
        "measured": {"win_pct": STUDY["win_pct"],
                     "nov_net_pct": STUDY["november"]["mean_net_pct"]},
    }
