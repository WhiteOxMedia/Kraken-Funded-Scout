"""Chart pattern detection for OHLCV candlestick data.

Detects classic reversal patterns, support/resistance levels, candlestick
formations, and trend direction.  Only pandas and numpy are used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── Helpers ───────────────────────────────────────────────────────────────

def _local_extrema(series: pd.Series, order: int = 3) -> tuple[list[int], list[int]]:
    """Return indices of local minima and maxima.

    A point is a local min if it's <= *order* neighbours on each side.
    """
    mins: list[int] = []
    maxs: list[int] = []
    vals = series.values
    n = len(vals)
    for i in range(order, n - order):
        window_left = vals[i - order : i]
        window_right = vals[i + 1 : i + 1 + order]
        if vals[i] <= np.min(window_left) and vals[i] <= np.min(window_right):
            mins.append(i)
        if vals[i] >= np.max(window_left) and vals[i] >= np.max(window_right):
            maxs.append(i)
    return mins, maxs


# ── Reversal patterns ─────────────────────────────────────────────────────

def detect_double_bottom(
    df: pd.DataFrame, lookback: int = 30, tolerance: float = 0.02
) -> dict | None:
    """Detect a double-bottom pattern (bullish reversal).

    Two consecutive lows within *tolerance* of each other, separated by a peak.
    """
    if len(df) < lookback:
        return None
    recent = df.tail(lookback)
    lows_idx, highs_idx = _local_extrema(recent["low"], order=2)

    if len(lows_idx) < 2:
        return None

    # Check the last two lows
    idx1, idx2 = lows_idx[-2], lows_idx[-1]
    low1, low2 = recent["low"].iloc[idx1], recent["low"].iloc[idx2]
    avg_low = (low1 + low2) / 2

    if abs(low1 - low2) / avg_low > tolerance:
        return None

    # There must be a peak between the two lows
    between = recent["high"].iloc[idx1 : idx2 + 1]
    if between.empty or idx2 - idx1 < 3:
        return None
    peak = between.max()
    neckline = peak

    # Confidence: higher when lows are closer, peak is more prominent
    low_similarity = 1 - (abs(low1 - low2) / avg_low)
    peak_prominence = (peak - avg_low) / avg_low
    confidence = min(100, (low_similarity * 60 + min(peak_prominence * 500, 40)))

    return {
        "pattern": "double_bottom",
        "confidence": round(confidence, 1),
        "neckline": float(neckline),
        "lows": [float(low1), float(low2)],
    }


def detect_double_top(
    df: pd.DataFrame, lookback: int = 30, tolerance: float = 0.02
) -> dict | None:
    """Detect a double-top pattern (bearish reversal)."""
    if len(df) < lookback:
        return None
    recent = df.tail(lookback)
    lows_idx, highs_idx = _local_extrema(recent["high"], order=2)

    if len(highs_idx) < 2:
        return None

    idx1, idx2 = highs_idx[-2], highs_idx[-1]
    high1, high2 = recent["high"].iloc[idx1], recent["high"].iloc[idx2]
    avg_high = (high1 + high2) / 2

    if abs(high1 - high2) / avg_high > tolerance:
        return None

    between = recent["low"].iloc[idx1 : idx2 + 1]
    if between.empty or idx2 - idx1 < 3:
        return None
    trough = between.min()
    neckline = trough

    high_similarity = 1 - (abs(high1 - high2) / avg_high)
    trough_depth = (avg_high - trough) / avg_high
    confidence = min(100, (high_similarity * 60 + min(trough_depth * 500, 40)))

    return {
        "pattern": "double_top",
        "confidence": round(confidence, 1),
        "neckline": float(neckline),
        "highs": [float(high1), float(high2)],
    }


def detect_head_and_shoulders(df: pd.DataFrame, lookback: int = 40) -> dict | None:
    """Detect a head-and-shoulders pattern (bearish reversal).

    Three peaks: left shoulder, head (highest), right shoulder.
    Right shoulder should be roughly at the same level as the left shoulder.
    """
    if len(df) < lookback:
        return None
    recent = df.tail(lookback)
    _, highs_idx = _local_extrema(recent["high"], order=3)

    if len(highs_idx) < 3:
        return None

    # Take last 3 peaks
    ls_i, head_i, rs_i = highs_idx[-3], highs_idx[-2], highs_idx[-1]
    ls, head, rs = (
        recent["high"].iloc[ls_i],
        recent["high"].iloc[head_i],
        recent["high"].iloc[rs_i],
    )

    # Head must be the highest
    if not (head > ls and head > rs):
        return None

    # Shoulders should be roughly equal (within 5%)
    shoulder_diff = abs(ls - rs) / max(ls, rs)
    if shoulder_diff > 0.05:
        return None

    # Right shoulder must come after head, head after left shoulder
    if not (ls_i < head_i < rs_i):
        return None

    # Neckline: the lows between left-shoulder/head and head/right-shoulder
    neckline_1 = recent["low"].iloc[ls_i : head_i + 1].min()
    neckline_2 = recent["low"].iloc[head_i : rs_i + 1].min()
    neckline = (neckline_1 + neckline_2) / 2

    confidence = min(100, (1 - shoulder_diff) * 50 + min((head - max(ls, rs)) / head * 300, 50))

    return {
        "pattern": "head_and_shoulders",
        "confidence": round(confidence, 1),
        "neckline": float(neckline),
    }


def detect_inverse_head_and_shoulders(df: pd.DataFrame, lookback: int = 40) -> dict | None:
    """Detect an inverse head-and-shoulders pattern (bullish reversal).

    Three troughs: left shoulder, head (lowest), right shoulder.
    """
    if len(df) < lookback:
        return None
    recent = df.tail(lookback)
    lows_idx, _ = _local_extrema(recent["low"], order=3)

    if len(lows_idx) < 3:
        return None

    ls_i, head_i, rs_i = lows_idx[-3], lows_idx[-2], lows_idx[-1]
    ls, head, rs = (
        recent["low"].iloc[ls_i],
        recent["low"].iloc[head_i],
        recent["low"].iloc[rs_i],
    )

    # Head must be the lowest
    if not (head < ls and head < rs):
        return None

    # Shoulders roughly equal (within 5%)
    shoulder_diff = abs(ls - rs) / min(ls, rs)
    if shoulder_diff > 0.05:
        return None

    if not (ls_i < head_i < rs_i):
        return None

    neckline_1 = recent["high"].iloc[ls_i : head_i + 1].max()
    neckline_2 = recent["high"].iloc[head_i : rs_i + 1].max()
    neckline = (neckline_1 + neckline_2) / 2

    confidence = min(100, (1 - shoulder_diff) * 50 + min((min(ls, rs) - head) / min(ls, rs) * 300, 50))

    return {
        "pattern": "inverse_head_and_shoulders",
        "confidence": round(confidence, 1),
        "neckline": float(neckline),
    }


# ── Support / Resistance ──────────────────────────────────────────────────

def detect_support_resistance(
    df: pd.DataFrame, lookback: int = 50, min_touches: int = 3
) -> dict:
    """Detect key support and resistance price levels.

    Clusters recent local highs/lows into zones.
    """
    if len(df) < lookback:
        lookback = len(df)
    recent = df.tail(lookback)

    lows_idx, highs_idx = _local_extrema(recent["low"], order=2)
    _, highs_idx2 = _local_extrema(recent["high"], order=2)

    current_price = float(recent["close"].iloc[-1])

    def cluster_levels(levels: list[float], threshold: float = 0.015) -> list[dict]:
        if not levels:
            return []
        levels_sorted = sorted(levels)
        clusters: list[list[float]] = [[levels_sorted[0]]]
        for lvl in levels_sorted[1:]:
            if abs(lvl - np.mean(clusters[-1])) / np.mean(clusters[-1]) < threshold:
                clusters[-1].append(lvl)
            else:
                clusters.append([lvl])
        result = []
        for c in clusters:
            if len(c) >= min_touches:
                result.append({"price": float(np.mean(c)), "touches": len(c)})
        return result

    support_levels = cluster_levels([float(recent["low"].iloc[i]) for i in lows_idx])
    resistance_levels = cluster_levels([float(recent["high"].iloc[i]) for i in highs_idx2])

    # Nearest support below current price, nearest resistance above
    nearest_support = max(
        (s["price"] for s in support_levels if s["price"] < current_price),
        default=None,
    )
    nearest_resistance = min(
        (r["price"] for r in resistance_levels if r["price"] > current_price),
        default=None,
    )

    return {
        "supports": support_levels,
        "resistances": resistance_levels,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
    }


# ── Candlestick patterns ──────────────────────────────────────────────────

def detect_candlestick_patterns(df: pd.DataFrame, lookback: int = 5) -> list[dict]:
    """Detect common candlestick patterns in the most recent candles."""
    if len(df) < 3:
        return []

    recent = df.tail(lookback).reset_index(drop=True)
    patterns: list[dict] = []

    for i in range(len(recent)):
        row = recent.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        body = abs(c - o)
        rng = h - l
        if rng == 0:
            continue
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        # Hammer: small body at top, long lower wick (bullish)
        if lower_wick > 2 * body and upper_wick < body * 0.5 and body > 0:
            patterns.append({
                "pattern": "hammer",
                "type": "bullish",
                "confidence": min(100, (lower_wick / rng) * 100),
                "candle_index": i,
            })

        # Shooting Star: small body at bottom, long upper wick (bearish)
        if upper_wick > 2 * body and lower_wick < body * 0.5 and body > 0:
            patterns.append({
                "pattern": "shooting_star",
                "type": "bearish",
                "confidence": min(100, (upper_wick / rng) * 100),
                "candle_index": i,
            })

        # Doji: very small body (indecision)
        if body < rng * 0.1:
            patterns.append({
                "pattern": "doji",
                "type": "neutral",
                "confidence": 50.0,
                "candle_index": i,
            })

        # 2-candle patterns (need at least 2 candles)
        if i >= 1:
            prev = recent.iloc[i - 1]
            po, pc = float(prev["open"]), float(prev["close"])
            pbody = abs(pc - po)

            # Bullish Engulfing: prev bearish, current bullish, current body engulfs prev
            if pc < po and c > o and o <= pc and c >= po and body > pbody:
                patterns.append({
                    "pattern": "bullish_engulfing",
                    "type": "bullish",
                    "confidence": min(100, (body / rng) * 100 + 30),
                    "candle_index": i,
                })

            # Bearish Engulfing: prev bullish, current bearish, current body engulfs prev
            if pc > po and c < o and o >= pc and c <= po and body > pbody:
                patterns.append({
                    "pattern": "bearish_engulfing",
                    "type": "bearish",
                    "confidence": min(100, (body / rng) * 100 + 30),
                    "candle_index": i,
                })

        # 3-candle patterns
        if i >= 2:
            prev2 = recent.iloc[i - 2]
            prev1 = recent.iloc[i - 1]
            p2o, p2c = float(prev2["open"]), float(prev2["close"])
            p1o, p1c = float(prev1["open"]), float(prev1["close"])

            # Morning Star: long bearish, small body (gap down), long bullish
            if (
                p2c < p2o  # first candle bearish
                and abs(p1c - p1o) < abs(p2c - p2o) * 0.5  # second candle small body
                and c > o  # third candle bullish
                and c > (p2o + p2c) / 2  # closes above midpoint of first
            ):
                patterns.append({
                    "pattern": "morning_star",
                    "type": "bullish",
                    "confidence": 80.0,
                    "candle_index": i,
                })

            # Evening Star: long bullish, small body (gap up), long bearish
            if (
                p2c > p2o  # first candle bullish
                and abs(p1c - p1o) < abs(p2c - p2o) * 0.5  # second candle small body
                and c < o  # third candle bearish
                and c < (p2o + p2c) / 2  # closes below midpoint of first
            ):
                patterns.append({
                    "pattern": "evening_star",
                    "type": "bearish",
                    "confidence": 80.0,
                    "candle_index": i,
                })

    return patterns


# ── Trend detection ───────────────────────────────────────────────────────

def detect_trend(df: pd.DataFrame, lookback: int = 20) -> dict:
    """Determine if price is in an uptrend, downtrend, or sideways.

    Uses linear regression slope on closing prices + higher-highs/lower-lows.
    """
    if len(df) < lookback:
        lookback = len(df)
    recent = df.tail(lookback)
    closes = recent["close"].values
    n = len(closes)

    if n < 5:
        return {"trend": "sideways", "strength": 0.0, "slope": 0.0}

    # Linear regression slope
    x = np.arange(n)
    slope = np.polyfit(x, closes, 1)[0]
    avg_price = np.mean(closes)
    normalised_slope = slope / avg_price  # relative slope

    # Higher highs / lower lows check
    highs = recent["high"].values
    lows = recent["low"].values
    half = n // 2
    first_half_high = np.max(highs[:half]) if half > 0 else highs[0]
    second_half_high = np.max(highs[half:]) if half < n else highs[-1]
    first_half_low = np.min(lows[:half]) if half > 0 else lows[0]
    second_half_low = np.min(lows[half:]) if half < n else lows[-1]

    hh = second_half_high > first_half_high  # higher highs
    hl = second_half_low > first_half_low  # higher lows
    lh = second_half_high < first_half_high  # lower highs
    ll = second_half_low < first_half_low  # lower lows

    if normalised_slope > 0.001 and (hh or hl):
        trend = "uptrend"
    elif normalised_slope < -0.001 and (lh or ll):
        trend = "downtrend"
    else:
        trend = "sideways"

    strength = min(100, abs(normalised_slope) * 10000)

    return {
        "trend": trend,
        "strength": round(float(strength), 1),
        "slope": round(float(slope), 6),
    }


# ── Convenience ───────────────────────────────────────────────────────────

def detect_all_patterns(df: pd.DataFrame, lookback: int = 50) -> dict:
    """Run all pattern detectors and return a consolidated result.

    Returns a dict with individual pattern results, counts, and overall bias.
    """
    double_bottom = detect_double_bottom(df, lookback=lookback)
    double_top = detect_double_top(df, lookback=lookback)
    hs = detect_head_and_shoulders(df, lookback=lookback)
    ihs = detect_inverse_head_and_shoulders(df, lookback=lookback)
    sr = detect_support_resistance(df, lookback=lookback)
    candles = detect_candlestick_patterns(df, lookback=5)
    trend_info = detect_trend(df, lookback=20)

    bullish_count = 0
    bearish_count = 0

    if double_bottom:
        bullish_count += 1
    if ihs:
        bullish_count += 1
    if double_top:
        bearish_count += 1
    if hs:
        bearish_count += 1

    for c in candles:
        if c["type"] == "bullish":
            bullish_count += 1
        elif c["type"] == "bearish":
            bearish_count += 1

    if trend_info["trend"] == "uptrend":
        bullish_count += 1
    elif trend_info["trend"] == "downtrend":
        bearish_count += 1

    if bullish_count > bearish_count:
        overall_bias = "bullish"
    elif bearish_count > bullish_count:
        overall_bias = "bearish"
    else:
        overall_bias = "neutral"

    return {
        "double_bottom": double_bottom,
        "double_top": double_top,
        "head_and_shoulders": hs,
        "inverse_head_and_shoulders": ihs,
        "support_resistance": sr,
        "candlestick_patterns": candles,
        "trend": trend_info,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "overall_bias": overall_bias,
    }
