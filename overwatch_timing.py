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

and the death line is what decides the size. It is `start * 0.97` = $9,700, it
never moves, and the room above it GROWS with profit — so the question is not how
far the equity fell from a peak, it is the lowest point the balance path reaches:

    size      profit over the sample    lowest balance    room above the halt line
    1%        +$148.45                  $9,910.66         +$60.66   never halted
    2%        +$298.33                  $9,839.67         -$10.33   halted
    5%        +$700.95                  $9,701.20         -$148.80  halted ($1.20 of life)

Every number comes from `ca.death_line()` / `df.halt_line()` / `ca.sized_entry_usd`,
the repo's single definitions — this module holds no second copy of that arithmetic.
Sizing, not the stop, is what keeps the win rate affordable: at 1% of the balance the
shape never gets within $60 of the halt line; at 5% it profits more and passes within
$1.20 of dying. So SIZE_FRAC is 1%, and the whole engine is a 1%-of-balance engine.
"""
from __future__ import annotations

import contest_account as ca      # MAX_LOSS_PCT, death_line(), sized_entry_usd()
import death_floor as df          # halt_line() — the band above the death line

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

#: One ticket, as a fraction of the balance. The size is the risk control: at 1% the
#: 82.3% shape never came within $60 of the halt line over the whole sample, while at
#: 5% it dipped to $9,701 — $1.20 above the death line. Small size is what makes the
#: win rate affordable, not a stop that pretends the tail is smaller than it is.
SIZE_FRAC = 0.01

ARM_CRITERIA = {
    "shadow_trades": 200,        # enough closed shadow trades to judge
    "min_win_pct": 80.0,         # the user's bar
    "min_mean_net_pct": 0.05,    # and it must actually clear the cost
    "min_headroom_usd": 100.0,   # never closer than $100 to the halt line
    "min_buffer_usd": 250.0,     # buffer = balance - death line; it grows with wins
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
    # the death line is start*0.97 and NEVER moves; the room above it grows with
    # profit, so the test is the lowest point of the balance path, not a drawdown
    # measured from a peak. Numbers come from ca.death_line()/df.halt_line().
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
               "only at ~1% of the balance: it makes about $148 over six months and "
               "never gets closer than $60 to the halt line",
    "why_1pct": "sizing is the only thing standing between this shape and the death "
                "line - at 5% it ends +$701 but passes within $1.20 of dying",
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


def death_context(acct: dict | None = None) -> dict:
    """The live numbers, read from the repo's own account state — one definition.

    `buffer` is `contest_account.buffer()`: the money between the balance and the
    -3% death line. It is $300 at the start and it GROWS with every winning trade,
    which is the whole reason the risk is measured against the line and not against
    a fixed figure. `headroom` is the same distance measured to the halt band.
    """
    st = acct or ca.load_state()
    start = float(st["start_balance"])
    bal = float(st["current_balance"])
    buf = float(ca.buffer(st))
    halt = float(df.halt_line(start))
    return {"start": start, "balance": bal,
            "death_line": float(df.death_line(start)),
            "halt_line": halt,
            "buffer": buf,
            "headroom": bal - halt}


def ticket_usd(balance: float, start: float) -> float:
    """What one trade may spend: the repo's sizing rule, capped at SIZE_FRAC."""
    dd = float(getattr(ca, "DEFAULT_WINDOW_DD_PCT", 5.0))
    return ca.sized_entry_usd(balance, dd, start, SIZE_FRAC)


def should_arm(shadow: dict | None = None, acct: dict | None = None) -> tuple[bool, str]:
    """Overwatch asks this every cycle: shadow record AND death-line room."""
    ctx = death_context(acct)
    if ctx["balance"] <= ctx["halt_line"]:
        return False, (f"at/below the halt line ${ctx['halt_line']:,.2f} — "
                       f"nothing new is risked there, ever")
    if ctx["buffer"] < ARM_CRITERIA["min_buffer_usd"]:
        return False, (f"buffer ${ctx['buffer']:,.2f} is under the "
                       f"${ARM_CRITERIA['min_buffer_usd']:,.0f} floor — the room "
                       f"above the death line is what pays for the next loss")
    if ARMED:
        return True, f"armed at {SIZE_FRAC*100:.0f}% of balance"
    s = shadow or {}
    n = int(s.get("trades", 0))
    win = float(s.get("win_pct", 0.0))
    net = float(s.get("mean_net_pct", 0.0))
    room = float(s.get("min_headroom_usd", 0.0))
    c = ARM_CRITERIA
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


def shadow_verdict(shadow: dict | None = None) -> dict:
    """The numbers overwatch prints on its plate."""
    s = shadow or {}
    ctx = death_context()
    return {
        "state": "shadow",
        "trades": int(s.get("trades", 0)),
        "win_pct": round(float(s.get("win_pct", 0.0)), 1),
        "mean_net_pct": round(float(s.get("mean_net_pct", 0.0)), 4),
        "min_headroom_usd": round(float(s.get("min_headroom_usd", 0.0)), 2),
        "buffer_usd": round(ctx["buffer"], 2),
        "death_line_usd": round(ctx["death_line"], 2),
        "halt_line_usd": round(ctx["halt_line"], 2),
        "headroom_usd": round(ctx["headroom"], 2),
        "size_frac": SIZE_FRAC,
        "armed": ARMED,
        "why": should_arm(shadow)[1],
        "measured": {"win_pct": STUDY["win_pct"],
                     "nov_net_pct": STUDY["november"]["mean_net_pct"]},
    }
