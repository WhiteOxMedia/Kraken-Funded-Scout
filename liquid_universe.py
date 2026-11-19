"""THE LIQUID UNIVERSE — pairs a $2,400 order can actually fill.

The book kept discarding 95% of its signals at entry because the order did not
fit 1% of the pair's median hourly dollar volume: a "signal" the account could
never have bought. This module asks the question BEFORE the signal exists, so
every fire in the book is a fill the venue could take.

THE RULE (unchanged, just moved earlier):

    order_usd <= PARTICIPATION x median(hourly close x volume)

`median hourly dollar volume` is the median of the pair's own hourly bars — the
same measure `contest_book.py` applies per signal, and the same one the contest
simulator's GO BIG capacity reads. Median, not mean, so one liquidation candle
cannot make a dead coin look liquid.

The shipped order is $2,400. 18% of the $11,200 win line is $2,016, so a pair
that can absorb $2,400 absorbs every order the frozen book places at any
balance it can reach before the contest is won.

Read-only and pure: no exchange call, no order, no state. The scanner applies
it to the candles it already fetched.
"""
from __future__ import annotations

import numpy as np

# The order the universe is sized for: 24% of the $10,000 start, which is
# above 18% of the $11,200 win line ($2,016) — the largest order the frozen
# book can place anywhere short of the win line.
ORDER_USD = 2400.0

# The shipped participation rule: an order may be 1% of an hour's dollars.
PARTICIPATION = 0.01

# Fewer bars than this and "median hourly" is not a measurement.
MIN_BARS = 24


def median_hourly_usd(df) -> float | None:
    """Median dollars traded per hourly bar, or None when unmeasurable."""
    try:
        if df is None or len(df) < MIN_BARS:
            return None
        vol = np.asarray(df["volume"], dtype=float)
        px = np.asarray(df["close"], dtype=float)
        usd = px * vol
        usd = usd[np.isfinite(usd)]
        if usd.size < MIN_BARS:
            return None
        return float(np.median(usd))
    except (KeyError, TypeError, ValueError):
        return None


def capacity_usd(df, participation: float = PARTICIPATION) -> float:
    """The biggest order this pair is allowed to take (0.0 if unknown)."""
    med = median_hourly_usd(df)
    return 0.0 if med is None else float(med) * float(participation)


def check(df, order_usd: float = ORDER_USD,
          participation: float = PARTICIPATION) -> tuple[bool, dict]:
    """Does the order fit this pair? Returns (eligible, evidence)."""
    med = median_hourly_usd(df)
    cap = 0.0 if med is None else float(med) * float(participation)
    return cap >= float(order_usd), {
        "median_hourly_usd": None if med is None else round(med, 2),
        "capacity_usd": round(cap, 2),
        "order_usd": float(order_usd),
        "fill_pct": (None if med is None or med <= 0
                     else round(float(order_usd) / (float(med) * float(participation)) * 100.0, 1)),
    }


def eligible(df, order_usd: float = ORDER_USD,
             participation: float = PARTICIPATION) -> bool:
    return check(df, order_usd, participation)[0]


def filter_universe(data: dict, order_usd: float = ORDER_USD,
                    participation: float = PARTICIPATION) -> tuple[dict, list]:
    """Split fetched candles into (fillable, excluded).

    `excluded` carries the evidence per pair, so the log says WHY a coin is out
    instead of quietly shrinking the bracket.
    """
    kept: dict = {}
    excluded: list[dict] = []
    for pair, df in data.items():
        ok, ev = check(df, order_usd, participation)
        if ok:
            kept[pair] = df
        else:
            excluded.append({"pair": pair, **ev})
    excluded.sort(key=lambda r: r["capacity_usd"])
    return kept, excluded


def universe(data: dict, order_usd: float = ORDER_USD,
             participation: float = PARTICIPATION) -> dict:
    """The whole answer as one record, for a journal or a console line."""
    kept, excluded = filter_universe(data, order_usd, participation)
    return {
        "order_usd": float(order_usd),
        "participation": float(participation),
        "pairs_in": len(data),
        "pairs_fillable": len(kept),
        "kept": sorted(kept),
        "excluded": excluded,
    }
