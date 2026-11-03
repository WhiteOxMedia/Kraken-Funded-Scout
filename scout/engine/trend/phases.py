"""Trend phase model with stability checking.

Phases:
  0 — baseline (no trend)
  1 — breakout (just starting to move)
  2 — momentum (riding the trend)
  3 — exhaustion (trend fading)
  4 — pullback (dip buyer zone)
  5 — breakdown (avoid)

A phase must be stable for 2+ candles before the system commits.
"""
from __future__ import annotations

import pandas as pd
from typing import Any

from trend.features import FeatureBundle


# Phase labels for display
PHASE_LABELS = {
    "baseline": "Phase 0 — Baseline",
    "breakout": "Phase 1 — Breakout",
    "momentum": "Phase 2 — Momentum",
    "exhaustion": "Phase 3 — Exhaustion",
    "pullback": "Phase 4 — Pullback",
    "rebirth": "Phase 2b — Rebirth",
    "breakdown": "Phase 5 — Breakdown",
}

# Mode priority (highest to lowest)
MODE_PRIORITY = ["breakout", "rebirth", "momentum", "dip_buyer", "hold"]


def detect_phase(
    ind: dict[str, Any],
    df: pd.DataFrame,
    breakout: dict,
    momentum: dict,
    features: FeatureBundle,
) -> str:
    """Classify the current trend phase."""
    rsi_val = ind.get("rsi")
    ema_s = ind.get("ema_short")
    ema_l = ind.get("ema_long")
    adx_val = ind.get("adx")
    macd_hist = ind.get("macd_histogram")
    macd_hist_slope = ind.get("macd_hist_slope")
    price = ind.get("current_price")

    ema_bearish = ema_s and ema_l and ema_s < ema_l
    ema_bullish = ema_s and ema_l and ema_s > ema_l

    # Phase 5: Breakdown
    if ema_bearish and (adx_val or 0) > 20 and (macd_hist or 0) < 0:
        return "breakdown"

    # Phase 3: Exhaustion (confirmed by divergence)
    if features.is_exhausted:
        return "exhaustion"

    # Phase 3: Exhaustion (MACD fading + RSI high)
    if (macd_hist or 0) > 0 and (macd_hist_slope or 0) < 0 and rsi_val and rsi_val > 65:
        return "exhaustion"

    # Phase 1: Breakout
    if breakout.get("is_breakout"):
        return "breakout"

    # Phase 2b: Rebirth (second leg after pullback)
    if features.is_rebirth:
        return "rebirth"

    # Phase 2: Momentum
    if momentum.get("is_momentum"):
        return "momentum"

    # Phase 4: Pullback (price back to EMA in uptrend)
    if ema_bullish and price and ema_s and price <= ema_s * 1.005:
        return "pullback"

    # Phase 0: Baseline
    return "baseline"


def check_phase_stability(
    df: pd.DataFrame,
    config: Any,
    current_phase: str,
    lookback: int = 3,
) -> tuple[bool, list[str]]:
    """Check if the current phase has been stable for 2+ recent candles.

    Recomputes the phase on the last `lookback` candle windows and
    checks if the same phase persists.

    Returns (is_stable, phase_history).
    """
    from strategy import evaluate_strategy
    from trend.features import (
        detect_breakout_failure, detect_exhaustion, detect_trend_rebirth,
        compute_recent_modes, FeatureBundle,
    )
    from indicators import compute_all_indicators, ema as calc_ema, bollinger_bands

    phase_history = [current_phase]
    consistent = 1  # current candle counts

    for offset in range(1, min(lookback + 1, len(df) - 30)):
        window = df.iloc[:-offset]
        if len(window) < 30:
            break

        # Lightweight phase detection — just check core conditions
        ind = compute_all_indicators(window, config)
        ema_s = ind.get("ema_short")
        ema_l = ind.get("ema_long")
        price = ind.get("current_price")
        macd_hist = ind.get("macd_histogram")
        rsi_val = ind.get("rsi")
        macd_hist_slope = ind.get("macd_hist_slope")

        ema_bullish = ema_s and ema_l and ema_s > ema_l
        ema_bearish = ema_s and ema_l and ema_s < ema_l

        if ema_bearish and (ind.get("adx") or 0) > 20 and (macd_hist or 0) < 0:
            phase = "breakdown"
        elif (macd_hist or 0) > 0 and (macd_hist_slope or 0) < 0 and rsi_val and rsi_val > 65:
            phase = "exhaustion"
        elif ema_bullish and price and ema_s and price <= ema_s * 1.005:
            phase = "pullback"
        elif ema_bullish and (macd_hist or 0) > 0 and rsi_val and 50 < rsi_val < 75:
            phase = "momentum"
        else:
            phase = "baseline"

        phase_history.append(phase)
        if phase == current_phase:
            consistent += 1

    is_stable = consistent >= 2  # at least 2 of the last N candles agree
    return is_stable, phase_history
