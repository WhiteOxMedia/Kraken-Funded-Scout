"""Pure feature extractors for trend analysis.

All functions take a DataFrame + indicator dict and return scalar values.
No side effects, no state — just math.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExhaustionSignals:
    """Signals that a trend is exhausting."""
    rsi_divergence: bool = False        # price higher highs, RSI lower highs
    macd_divergence: bool = False       # price higher highs, MACD hist lower highs
    volume_divergence: bool = False     # price rising, volume falling for 5+ candles
    reasons: list[str] = field(default_factory=list)


@dataclass
class FeatureBundle:
    """All computed features for one asset at one point in time.

    Computed once per evaluation, passed to all mode detectors.
    """
    # Trend angle / smoothness
    ema_slope_deg: float = 0.0          # EMA short slope in degrees (0 = flat, 45 = strong)
    smoothness_pct: float = 0.0         # % of last 20 candles that closed above EMA
    # Pullback quality (0-100)
    pullback_quality: float = 0.0
    pullback_depth_pct: float = 0.0
    # Breakout failure
    breakout_failed: bool = False
    breakout_fail_reasons: list[str] = field(default_factory=list)
    # Exhaustion
    exhaustion: ExhaustionSignals = field(default_factory=ExhaustionSignals)
    is_exhausted: bool = False
    # Trend rebirth
    is_rebirth: bool = False
    rebirth_reasons: list[str] = field(default_factory=list)
    # Phase info (set by phases.py)
    phase: str = "baseline"
    phase_stable: bool = False
    phase_history: list[str] = field(default_factory=list)
    # Mode memory (recomputed from DataFrame, not persistent state)
    recent_modes: list[str] = field(default_factory=list)
    was_momentum_recently: bool = False
    # Signal confidence (set by scoring.py)
    signal_confidence: float = 0.0


def compute_ema_slope_angle(df: pd.DataFrame, ema_short_series: pd.Series, lookback: int = 10) -> float:
    """Convert EMA slope into degrees.

    15-35° = healthy trend
    >40° = blowoff risk
    <10° = weak/flat

    Uses the EMA short series slope over the last `lookback` candles,
    normalized by price to get a percentage, then converted to degrees.
    """
    if ema_short_series is None or len(ema_short_series) < lookback:
        return 0.0

    recent = ema_short_series.tail(lookback).dropna()
    if len(recent) < 2:
        return 0.0

    # Slope as percentage change per candle
    start_val = recent.iloc[0]
    end_val = recent.iloc[-1]
    if start_val <= 0:
        return 0.0

    pct_change = (end_val - start_val) / start_val * 100
    # Convert to degrees: atan of the percentage slope
    # 1% per candle ≈ ~1° (roughly), scaled for readability
    angle = math.degrees(math.atan(pct_change / lookback))

    return round(angle, 1)


def compute_smoothness(df: pd.DataFrame, ema_short_series: pd.Series, macd_hist_series: pd.Series, lookback: int = 20) -> float:
    """Measure trend smoothness as % of candles that are "well-behaved".

    A candle is well-behaved if:
      - Close above EMA short (in an uptrend)
      - MACD histogram positive

    Returns percentage (0-100). Momentum Rider requires >= 60%.
    """
    if len(df) < lookback or ema_short_series is None:
        return 0.0

    recent_close = df["close"].tail(lookback)
    recent_ema = ema_short_series.tail(lookback)
    recent_macd = macd_hist_series.tail(lookback) if macd_hist_series is not None else pd.Series(dtype=float)

    good = 0
    total = 0
    for i in range(len(recent_close)):
        if pd.isna(recent_ema.iloc[i]):
            continue
        total += 1
        candle_good = recent_close.iloc[i] > recent_ema.iloc[i]
        if i < len(recent_macd) and not pd.isna(recent_macd.iloc[i]):
            candle_good = candle_good and recent_macd.iloc[i] > 0
        if candle_good:
            good += 1

    return round(good / total * 100, 1) if total > 0 else 0.0


def compute_pullback_quality(df: pd.DataFrame, ind: dict[str, Any], ema_short_val: float | None) -> float:
    """Score the quality of a pullback for dip buying (0-100).

    Factors:
      - Depth: 1-4% = good, >6% = risky
      - Volume: lower than trend volume = good (orderly pullback)
      - RSI: 35-50 = ideal
      - Wick size: small wicks = stable
      - EMA touch vs break: touch = good, break = risky
    """
    price = ind.get("current_price")
    if not price or not ema_short_val:
        return 0.0

    score = 0.0

    # Depth: how far below recent high?
    recent_high = df["high"].tail(20).max()
    depth_pct = (recent_high - price) / recent_high * 100 if recent_high > 0 else 0

    if 1.0 <= depth_pct <= 4.0:
        score += 25
    elif depth_pct < 1.0:
        score += 10  # shallow, might not be a real pullback
    elif depth_pct <= 6.0:
        score += 15
    else:
        score += 5  # too deep, could be a breakdown

    # Volume: lower than average = orderly
    vol_ma5 = ind.get("volume_ma5")
    vol_ma20 = ind.get("volume_ma20")
    if vol_ma5 and vol_ma20:
        if vol_ma5 < vol_ma20 * 0.9:
            score += 20  # volume declining = good
        elif vol_ma5 < vol_ma20 * 1.1:
            score += 10  # neutral
        else:
            score += 0  # volume rising on pullback = bad

    # RSI
    rsi_val = ind.get("rsi")
    if rsi_val:
        if 35 <= rsi_val <= 50:
            score += 25
        elif 30 <= rsi_val <= 55:
            score += 15
        else:
            score += 5

    # Wick size (small = stable)
    wick_pct = ind.get("intraday_wick_pct", 0)
    if wick_pct < 30:
        score += 15
    elif wick_pct < 50:
        score += 8
    else:
        score += 0

    # EMA touch vs break
    price_vs_ema = (price - ema_short_val) / ema_short_val * 100
    if -1.0 <= price_vs_ema <= 1.0:
        score += 15  # touching EMA = ideal
    elif price_vs_ema < -1.0:
        score += 5   # broke below = riskier
    else:
        score += 8   # above EMA but pulled back

    return min(100, score)


def detect_breakout_failure(df: pd.DataFrame, ind: dict[str, Any]) -> tuple[bool, list[str]]:
    """Detect if a recent breakout failed (breakout → rug pull pattern).

    Checks if a breakout occurred in the last 3 candles AND:
      - Price is now below the breakout candle's low
      - Volume is declining
      - RSI dropped below 50
    """
    bb_pct = ind.get("bollinger_percent_b")
    rsi_val = ind.get("rsi")
    vol_ma5 = ind.get("volume_ma5")
    vol_ma20 = ind.get("volume_ma20")
    price = ind.get("current_price")

    if not price or len(df) < 5:
        return False, []

    # Check if any of the last 3 candles had BB%B > 0.8 (breakout)
    from indicators import bollinger_bands
    from config import TradingConfig
    cfg = TradingConfig()
    bb_df = bollinger_bands(df, cfg.bollinger_period, cfg.bollinger_std)
    recent_bb = bb_df["percent_b"].tail(4).values

    had_breakout = any(v > 0.8 for v in recent_bb if not pd.isna(v))
    if not had_breakout:
        return False, []

    # Find the breakout candle (the one with highest BB%B in last 4)
    breakout_idx = -1
    max_bb = 0
    for i in range(len(recent_bb) - 1):
        if not pd.isna(recent_bb[i]) and recent_bb[i] > max_bb:
            max_bb = recent_bb[i]
            breakout_idx = i

    if breakout_idx < 0:
        return False, []

    # Get the breakout candle's low
    breakout_candle = df.iloc[-(len(recent_bb) - breakout_idx)]
    breakout_low = breakout_candle["low"]

    reasons = []
    failed = False

    # Price below breakout candle low
    if price < breakout_low:
        failed = True
        reasons.append(f"Price {_fmt(price)} below breakout low {_fmt(breakout_low)}")

    # Volume declining
    if vol_ma5 and vol_ma20 and vol_ma5 < vol_ma20 * 0.8:
        failed = True
        reasons.append("Volume declining after breakout")

    # RSI dropped below 50
    if rsi_val and rsi_val < 50:
        failed = True
        reasons.append(f"RSI {rsi_val:.1f} < 50 after breakout")

    return failed, reasons


def detect_exhaustion(df: pd.DataFrame, ind: dict[str, Any], lookback: int = 20) -> ExhaustionSignals:
    """Detect trend exhaustion via divergences.

    A. Bearish RSI divergence: price makes higher highs, RSI makes lower highs
    B. Bearish MACD divergence: price higher highs, MACD histogram lower highs
    C. Volume divergence: price rising, volume falling for 5+ candles
    """
    signals = ExhaustionSignals()
    price = ind.get("current_price")
    if not price or len(df) < lookback:
        return signals

    recent = df.tail(lookback)
    closes = recent["close"].values
    highs = recent["high"].values
    volumes = recent["volume"].values

    # Find recent highs (split into two halves)
    mid = len(closes) // 2
    first_half_high = max(highs[:mid]) if mid > 0 else highs[0]
    second_half_high = max(highs[mid:]) if mid < len(highs) else highs[-1]

    # Price making higher highs?
    price_higher_highs = second_half_high > first_half_high

    # A. RSI divergence
    from indicators import rsi as calc_rsi
    from config import TradingConfig
    cfg = TradingConfig()
    rsi_series = calc_rsi(df, cfg.rsi_period)
    rsi_recent = rsi_series.tail(lookback).dropna()
    if len(rsi_recent) >= mid + 2:
        rsi_first_high = max(rsi_recent.iloc[:mid]) if mid > 0 else rsi_recent.iloc[0]
        rsi_second_high = max(rsi_recent.iloc[mid:]) if mid < len(rsi_recent) else rsi_recent.iloc[-1]
        if price_higher_highs and rsi_second_high < rsi_first_high:
            signals.rsi_divergence = True
            signals.reasons.append(f"RSI divergence: price HH ({second_half_high:.4f} > {first_half_high:.4f}) but RSI LH ({rsi_second_high:.1f} < {rsi_first_high:.1f})")

    # B. MACD divergence
    from indicators import macd as calc_macd
    macd_df = calc_macd(df, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    hist_series = macd_df["histogram"].tail(lookback).dropna()
    if len(hist_series) >= mid + 2:
        macd_first_high = max(hist_series.iloc[:mid]) if mid > 0 else hist_series.iloc[0]
        macd_second_high = max(hist_series.iloc[mid:]) if mid < len(hist_series) else hist_series.iloc[-1]
        if price_higher_highs and macd_second_high < macd_first_high:
            signals.macd_divergence = True
            signals.reasons.append(f"MACD divergence: price HH but histogram LH ({macd_second_high:.6f} < {macd_first_high:.6f})")

    # C. Volume divergence (5+ candles of falling volume while price rises)
    vol_ma5 = ind.get("volume_ma5")
    vol_ma20 = ind.get("volume_ma20")
    if vol_ma5 and vol_ma20 and vol_ma5 < vol_ma20 * 0.85:
        # Check if price is still rising
        recent_5 = df["close"].tail(5)
        if recent_5.iloc[-1] > recent_5.iloc[0]:
            signals.volume_divergence = True
            signals.reasons.append("Volume divergence: price rising but volume falling")

    return signals


def detect_trend_rebirth(df: pd.DataFrame, ind: dict[str, Any], recent_modes: list[str]) -> tuple[bool, list[str]]:
    """Detect trend rebirth — a second leg after a pullback.

    Conditions:
      - Was in momentum or exhaustion phase recently
      - Pullback touched EMA (price came back to EMA short)
      - Trend Health recovering (> 60)
      - ADX rising again
      - MACD crosses positive
    """
    reasons = []

    # Must have been in momentum recently
    if "momentum" not in recent_modes and "exhaustion" not in recent_modes:
        return False, []

    price = ind.get("current_price")
    ema_short_val = ind.get("ema_short")
    macd_hist = ind.get("macd_histogram")
    adx_slope = ind.get("adx_slope")
    adx_val = ind.get("adx")

    if not price or not ema_short_val:
        return False, []

    # Price came back to EMA (within 2%) then bounced
    price_vs_ema = (price - ema_short_val) / ema_short_val * 100
    if price_vs_ema > 3.0:
        return False, []  # already extended again, not a rebirth

    # MACD positive
    if macd_hist is None or macd_hist <= 0:
        return False, []

    # ADX rising or still strong
    adx_ok = (adx_val and adx_val > 18) or (adx_slope and adx_slope > 0)
    if not adx_ok:
        return False, []

    reasons.append("Trend rebirth: was momentum → pullback → recovering")
    reasons.append(f"  MACD positive ({macd_hist:.6f})")
    if adx_slope and adx_slope > 0:
        reasons.append(f"  ADX rising (slope {adx_slope:.2f})")
    reasons.append(f"  Price {price_vs_ema:+.2f}% vs EMA (pulled back and bouncing)")

    return True, reasons


def compute_recent_modes(df: pd.DataFrame, config: Any, n: int = 5) -> list[str]:
    """Recompute what mode the strategy would have emitted on recent candles.

    Instead of persistent state, we re-evaluate the strategy on the last
    N candle windows to determine recent mode history.

    IMPORTANT: Temporarily disables deep analysis to prevent infinite recursion
    (evaluate_strategy → compute_recent_modes → evaluate_strategy → ...).
    """
    import strategy as _strat

    # Disable deep analysis to prevent recursion
    was_deep = _strat._DEEP_ANALYSIS
    _strat._DEEP_ANALYSIS = False

    try:
        modes = []
        for offset in range(1, min(n + 1, len(df) - 30)):
            window = df.iloc[:-offset]
            if len(window) < 30:
                break
            sig = _strat.evaluate_strategy(window, config)
            modes.append(sig.action)
        return modes
    finally:
        _strat._DEEP_ANALYSIS = was_deep


def _fmt(v: float) -> str:
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.4f}"
    return f"{v:.8f}"
