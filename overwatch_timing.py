"""OVERWATCH TIMING — the trend-ride engine: cut the loser fast, ride the winner.

    buy the lower-band pullback inside an uptrend    (entry)
    a trade that goes against you leaves at the stop  (bad entry -> out quick)
    a trade that works is NOT capped                  (stay and scalp)
    the stop ratchets behind the peak                 (keep the profit)

There is deliberately no target and no trend-break exit: the study measured the
EMA20 trend-break exit as worse than the pure ratchet on every entry variant
(`scratch/trendride_study.py`), so it is not in the engine at all rather than sitting
dead behind a flag.

The shape, measured (`scratch/trendride_verdict.py`, 30 pairs, 1,544,445 5m bars,
May 14 - Nov 13, stop 2.0%, trail arms at +0.4%, gives back 0.15%):

    win rate        82.3% of 8,304 trades
    expectancy      +0.0178% per trade
    November        +0.0379% per trade, 80.8% wins, n=442   (t = 0.79)

The death line decides the size. It is `start * 0.97` = $9,700, it never moves, and
the buffer above it ($300 at the start, $281.96 now) GROWS with every winning trade —
so the risk question is the lowest point the balance path reaches, never a drawdown
measured from a peak:

    size      profit over the sample    lowest balance    room above the halt line
    1%        +$148.45                  $9,910.66         +$60.66   never halted
    2%        +$298.33                  $9,839.67         -$10.33   halted
    5%        +$700.95                  $9,701.20         -$148.80  halted ($1.20 of life)

Sizing, not the stop, is what keeps the win rate affordable, so SIZE_FRAC is 1%.

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

#: One ticket as a fraction of the balance: the risk control that makes 82.3% affordable.
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
    # recorded values at measurement time (a record, never used as arithmetic)
    "death_line_usd": 9700.0,
    "halt_line_usd": 9850.0,
    "paths": {
        "1pct": {"profit_usd": 148.45, "lowest_usd": 9910.66, "room_usd": 60.66,
                 "halted": False},
        "2pct": {"profit_usd": 298.33, "lowest_usd": 9839.67, "room_usd": -10.33,
                 "halted": True},
        "5pct": {"profit_usd": 700.95, "lowest_usd": 9701.20, "room_usd": -148.80,
                 "halted": True},
    },
    "verdict": "82.3% wins, positive out of sample, and safe on the -3% death line "
               "only at ~1% of the balance: about $148 over six months, never closer "
               "than $60 to the halt line",
    "why_1pct": "sizing is the only thing between this shape and the death line - at "
                "5% it ends +$701 but passes within $1.20 of dying",
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
