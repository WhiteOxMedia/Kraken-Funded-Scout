"""Trading strategy: momentum + mean reversion for 5-minute XMR trading.

Two modes depending on market conditions:
  - TRENDING market: Buy into momentum (EMA cross + MACD + volume)
  - RANGING market: Buy dips (RSI oversold + Bollinger lower band)

Indicators:
  - RSI (momentum + mean reversion)
  - Bollinger Bands (mean reversion + breakout)
  - MACD histogram (momentum direction)
  - EMA crossover (trend direction)
  - Volume spike (confirmation)

Weights sum to 100 for each direction (buy / sell).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from config import TradingConfig
from indicators import compute_all_indicators, ema as calc_ema
from patterns import detect_all_patterns

logger = logging.getLogger(__name__)

# Deep analysis flag — when True, runs expensive features (recent_modes, phase_stability)
# Disabled during bulk scans for speed, enabled for deep analysis on top picks only.
_DEEP_ANALYSIS = False


def enable_deep_analysis():
    """Enable expensive features (recent_modes, phase_stability)."""
    global _DEEP_ANALYSIS
    _DEEP_ANALYSIS = True


def disable_deep_analysis():
    """Disable expensive features for fast scanning."""
    global _DEEP_ANALYSIS
    _DEEP_ANALYSIS = False


@dataclass
class Signal:
    """The result of a strategy evaluation.

    For actionable signals (buy/sell), entry_price / stop_loss / take_profit
    are computed from ATR and support/resistance levels so the scout can
    display concrete entry and exit zones.
    """

    action: str  # "buy", "sell", "hold"
    confidence: float  # 0–100
    price: float | None
    reasons: list[str] = field(default_factory=list)
    indicators: dict[str, Any] = field(default_factory=dict)
    patterns: dict[str, Any] = field(default_factory=dict)
    # Entry / exit levels (populated by compute_entry_exit)
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None
    # Regime detected at signal time (for display + multi-timeframe logic)
    regime: str = "unknown"       # "bull_trend", "bear_trend", "ranging", "high_vol", "low_vol"
    volatility_rank: float = 0.0  # ATR as % of price, percentile rank (0-100)


# ── Regime-specific weight models ────────────────────────────────────────
# Different market regimes favour different indicators.  In a strong trend,
# momentum (EMA cross, MACD) should dominate.  In a range, mean reversion
# (RSI, Bollinger) should dominate.  High volatility widens stops; low vol
# tightens them and favours breakout signals.

REGIME_WEIGHTS = {
    "bull_trend": {
        "rsi": 15, "bollinger": 10, "macd": 30, "ema_cross": 30, "volume": 15,
    },
    "bear_trend": {
        "rsi": 15, "bollinger": 10, "macd": 30, "ema_cross": 30, "volume": 15,
    },
    "ranging": {
        "rsi": 30, "bollinger": 30, "macd": 10, "ema_cross": 10, "volume": 20,
    },
    "high_vol": {
        "rsi": 20, "bollinger": 25, "macd": 20, "ema_cross": 15, "volume": 20,
    },
    "low_vol": {
        "rsi": 15, "bollinger": 30, "macd": 15, "ema_cross": 20, "volume": 20,
    },
}

# Default weights (used when regime detection is uncertain)
DEFAULT_BUY_WEIGHTS = {
    "rsi": 20,
    "bollinger": 20,
    "macd": 20,
    "ema_cross": 20,
    "volume": 20,
}

DEFAULT_SELL_WEIGHTS = {
    "rsi": 20,
    "bollinger": 20,
    "macd": 20,
    "ema_cross": 20,
    "volume": 20,
}


def _detect_regime(df: pd.DataFrame, ind: dict[str, Any], config: TradingConfig) -> tuple[str, float, dict[str, float]]:
    """Detect the current market regime and return appropriate scoring weights.

    Returns (regime_name, volatility_rank, weights_dict).
    The volatility_rank is ATR/price expressed as a percentile (0-100)
    across the lookback window — high = volatile, low = quiet.
    """
    ema_short_val = ind.get("ema_short")
    ema_long_val = ind.get("ema_long")
    atr_val = ind.get("atr")
    price = ind.get("current_price")

    # EMA spread for trend detection
    ema_spread = 0.0
    if ema_short_val and ema_long_val:
        ema_spread = abs(ema_short_val - ema_long_val) / max(ema_long_val, 1) * 100

    trending_up = ema_short_val is not None and ema_long_val is not None and ema_short_val > ema_long_val and ema_spread > 0.5
    trending_down = ema_short_val is not None and ema_long_val is not None and ema_short_val < ema_long_val and ema_spread > 0.5

    # Volatility rank: ATR as % of price, compared across the window
    vol_rank = 50.0  # default neutral
    if atr_val and price and price > 0 and len(df) >= config.atr_period * 3:
        # Compute ATR/price ratio for each candle in the lookback
        import numpy as np
        from indicators import atr as calc_atr
        atr_series = calc_atr(df, config.atr_period)
        close_series = df["close"]
        vol_ratios = (atr_series / close_series * 100).dropna()
        if len(vol_ratios) >= 10:
            current_ratio = (atr_val / price * 100)
            vol_rank = float(np.percentile(vol_ratios, [0])) if False else 0.0
            # Simpler: what percentile is the current ATR/price ratio?
            vol_rank = float((vol_ratios < current_ratio).sum() / len(vol_ratios) * 100)

    # Determine regime
    is_high_vol = vol_rank >= 75
    is_low_vol = vol_rank <= 25

    if trending_up:
        regime = "bull_trend"
    elif trending_down:
        regime = "bear_trend"
    elif is_high_vol:
        regime = "high_vol"
    elif is_low_vol:
        regime = "low_vol"
    else:
        regime = "ranging"

    # Get regime-specific weights, or fall back to defaults
    weights = REGIME_WEIGHTS.get(regime, DEFAULT_BUY_WEIGHTS)

    return regime, round(vol_rank, 1), weights


def _detect_breakout(df: pd.DataFrame, ind: dict[str, Any], config: TradingConfig) -> dict[str, Any]:
    """Detect early-stage breakouts before they become big moves.

    A breakout is when:
      - Price breaks above Bollinger upper band OR recent high
      - Volume is elevated (confirming the move)
      - The move is just starting (1-3% gain, not 6%+)
      - RSI is rising but not yet overbought (< 75)

    Returns a dict with breakout info, or empty dict if no breakout.
    """
    bb_upper = ind.get("bollinger_upper")
    bb_pct = ind.get("bollinger_percent_b")
    price = ind.get("current_price")
    rsi_val = ind.get("rsi")
    macd_hist = ind.get("macd_histogram")
    ema_short_val = ind.get("ema_short")
    ema_long_val = ind.get("ema_long")

    if not price or not bb_upper:
        return {}

    # Recent price change (5 candles)
    recent_5 = df["close"].tail(5)
    if len(recent_5) < 5:
        return {}
    change_5 = ((recent_5.iloc[-1] - recent_5.iloc[0]) / recent_5.iloc[0]) * 100

    # Recent high (20 candles)
    recent_20 = df["high"].tail(20)
    if len(recent_20) < 20:
        return {}
    recent_high = recent_20.max()
    recent_low = df["low"].tail(20).min()

    # Volume confirmation
    recent_vol = float(df["volume"].iloc[-1])
    avg_vol = float(df["volume"].tail(20).mean())
    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

    # Breakout conditions — catch earlier (BB%B 0.7 instead of 0.8)
    breaks_bb = bb_pct is not None and bb_pct > 0.7
    breaks_high = price >= recent_high * 0.995  # within 0.5% of 20-candle high (was 0.998)
    vol_surge = vol_ratio > 1.2  # volume 20%+ above average (was 1.3)

    # Catch breakouts up to 6% (was 4%) — don't miss moves that already started
    early_stage = abs(change_5) < 6.0

    # RSI rising but not extreme — wider window to catch earlier
    rsi_ok = rsi_val is not None and rsi_val < 78 and rsi_val > 40

    # EMA alignment (trend support)
    ema_bullish = ema_short_val and ema_long_val and ema_short_val > ema_long_val

    # MACD momentum
    macd_bullish = macd_hist is not None and macd_hist > 0

    # Score the breakout
    breakout_score = 0.0
    breakout_reasons = []

    if breaks_bb:
        breakout_score += 30
        breakout_reasons.append(f"Breaking Bollinger upper band (BB%B={bb_pct:.2f})")
    if breaks_high:
        breakout_score += 25
        breakout_reasons.append(f"At 20-candle high ({recent_high:.4f})")
    if vol_surge:
        breakout_score += 20
        breakout_reasons.append(f"Volume surge {vol_ratio:.1f}x average")
    if macd_bullish:
        breakout_score += 15
        breakout_reasons.append(f"MACD bullish (hist={macd_hist:.6f})")
    if ema_bullish:
        breakout_score += 10
        breakout_reasons.append("EMA bullish (short > long)")

    # Must have at least: price breakout + one confirmation
    is_breakout = (breaks_bb or breaks_high) and vol_surge and early_stage and rsi_ok

    return {
        "is_breakout": is_breakout,
        "score": min(100, breakout_score),
        "reasons": breakout_reasons,
        "change_5": change_5,
        "vol_ratio": vol_ratio,
        "recent_high": recent_high,
        "recent_low": recent_low,
    }


def _compute_trend_health(ind: dict[str, Any], df: pd.DataFrame) -> float:
    """Composite trend health score (0-100).

    Combines: EMA spread, ADX, volume trend, higher-low count,
    MACD histogram slope, RSI trend.
    """
    score = 0.0

    # EMA spread (trend direction strength)
    ema_s = ind.get("ema_short")
    ema_l = ind.get("ema_long")
    if ema_s and ema_l:
        spread = abs(ema_s - ema_l) / max(ema_l, 1) * 100
        if ema_s > ema_l:
            score += min(20, spread * 4)  # up to 20 pts for wide bullish spread

    # ADX (trend strength, direction-agnostic)
    adx_val = ind.get("adx")
    if adx_val:
        if adx_val > 25:
            score += 20
        elif adx_val > 20:
            score += 15
        elif adx_val > 15:
            score += 8
        # ADX rising = strengthening
        if ind.get("adx_slope") and ind["adx_slope"] > 0:
            score += 5

    # Volume trend
    vol_trend = ind.get("volume_trend", "neutral")
    if vol_trend == "rising":
        score += 15
    elif vol_trend == "neutral":
        score += 5

    # Higher-low structure
    hl_count = ind.get("higher_lows_count", 0)
    if ind.get("higher_lows"):
        score += min(15, hl_count * 8)

    # MACD histogram slope
    hist_slope = ind.get("macd_hist_slope")
    if hist_slope and hist_slope > 0:
        score += 10
    elif hist_slope and hist_slope == 0:
        score += 3

    # RSI trend (value in momentum zone)
    rsi_val = ind.get("rsi")
    if rsi_val:
        if 50 < rsi_val < 70:
            score += 10
        elif 45 < rsi_val < 75:
            score += 5

    return min(100, score)


def _detect_trend_phase(ind: dict[str, Any], df: pd.DataFrame, breakout: dict, momentum: dict) -> str:
    """Classify the current trend phase (0-5).

    Phase 0 — Baseline / No trend
    Phase 1 — Breakout (just starting to move)
    Phase 2 — Momentum Expansion (riding the trend)
    Phase 3 — Momentum Exhaustion (trend fading)
    Phase 4 — Pullback / Dip Buyer Zone
    Phase 5 — Breakdown / Avoid
    """
    rsi_val = ind.get("rsi")
    ema_s = ind.get("ema_short")
    ema_l = ind.get("ema_long")
    adx_val = ind.get("adx")
    macd_hist = ind.get("macd_histogram")
    hist_slope = ind.get("macd_hist_slope")
    price = ind.get("current_price")

    ema_bearish = ema_s and ema_l and ema_s < ema_l
    ema_bullish = ema_s and ema_l and ema_s > ema_l

    # Phase 5: Breakdown
    if ema_bearish and (adx_val or 0) > 20 and (macd_hist or 0) < 0:
        return "breakdown"

    # Phase 1: Breakout
    if breakout.get("is_breakout"):
        return "breakout"

    # Phase 3: Momentum Exhaustion (MACD positive but declining, RSI high)
    if (macd_hist or 0) > 0 and (hist_slope or 0) < 0 and rsi_val and rsi_val > 65:
        return "exhaustion"

    # Phase 2: Momentum Expansion
    if momentum.get("is_momentum"):
        return "momentum"

    # Phase 4: Pullback / Dip Zone (price pulled back to EMA in uptrend)
    if ema_bullish and price and ema_s and price <= ema_s * 1.005:
        return "pullback"

    # Phase 0: Baseline
    return "baseline"


def _detect_false_trend(ind: dict[str, Any], df: pd.DataFrame) -> dict:
    """Detect fake momentum from news spikes, manipulation, or low liquidity.

    Returns dict with rejection reasons (empty if trend is legit).
    """
    rejections = []

    # Reject assets with >20% intraday wicks (manipulation/news spike)
    wick_pct = ind.get("intraday_wick_pct", 0)
    if wick_pct > 20:
        rejections.append(f"Wick {wick_pct:.0f}% of candle — possible news spike")

    # Reject volume spikes > 4x average unless sustained
    recent_vol = float(df["volume"].iloc[-1]) if len(df) > 0 else 0
    vol_ma20 = ind.get("volume_ma20")
    if vol_ma20 and vol_ma20 > 0 and recent_vol > vol_ma20 * 4:
        # Check if sustained (previous 2 candles also elevated)
        if len(df) >= 3:
            prev_vols = df["volume"].iloc[-3:-1]
            sustained = all(v > vol_ma20 * 1.5 for v in prev_vols)
            if not sustained:
                rejections.append(f"Volume {recent_vol/vol_ma20:.1f}x avg but not sustained — one-candle hype")

    return {"rejected": len(rejections) > 0, "reasons": rejections}


def _detect_momentum(df: pd.DataFrame, ind: dict[str, Any], config: TradingConfig) -> dict[str, Any]:
    """Detect assets in a confirmed uptrend riding momentum.

    Enhanced with:
      - ADX > 20 (or rising ADX slope)
      - Higher-low structure (2+ consecutive higher lows)
      - Volume consistency (MA5 > MA20, or MA20 rising)
      - Late trend warnings (RSI > 78, price > 5% above EMA, MACD flattening)
      - Breakout afterburner (boost if breakout was recent)
      - False trend detection
      - Trend health score requirement
    """
    price = ind.get("current_price")
    rsi_val = ind.get("rsi")
    macd_hist = ind.get("macd_histogram")
    macd_hist_slope = ind.get("macd_hist_slope")
    ema_short_val = ind.get("ema_short")
    ema_long_val = ind.get("ema_long")
    bb_pct = ind.get("bollinger_percent_b")
    adx_val = ind.get("adx")
    adx_slope = ind.get("adx_slope")
    vol_ma5 = ind.get("volume_ma5")
    vol_ma20 = ind.get("volume_ma20")
    vol_trend = ind.get("volume_trend")
    has_higher_lows = ind.get("higher_lows", False)

    if not price or not ema_short_val or not ema_long_val:
        return {"is_momentum": False, "score": 0, "reasons": []}

    # ── False trend detector ──────────────────────────────────────────
    false_trend = _detect_false_trend(ind, df)
    if false_trend["rejected"]:
        return {
            "is_momentum": False, "score": 0,
            "reasons": [f"REJECTED: {r}" for r in false_trend["reasons"]],
        }

    # ── Core conditions ────────────────────────────────────────────────
    ema_bullish = ema_short_val > ema_long_val
    ema_spread = abs(ema_short_val - ema_long_val) / max(ema_long_val, 1) * 100
    strong_trend = ema_bullish and ema_spread > 0.3

    # Catch momentum earlier — wider EMA window (4% instead of 3%)
    price_vs_ema = (price - ema_short_val) / ema_short_val * 100
    riding_trend = 0 < price_vs_ema < 4.0

    # Lower RSI entry zone — catch momentum before it's fully established (45-78 instead of 50-75)
    rsi_momentum = rsi_val is not None and 45 < rsi_val < 78
    macd_bullish = macd_hist is not None and macd_hist > 0

    # Recent performance (20 candles)
    recent_20 = df["close"].tail(20)
    if len(recent_20) < 20:
        return {"is_momentum": False, "score": 0, "reasons": []}
    change_20 = ((recent_20.iloc[-1] - recent_20.iloc[0]) / recent_20.iloc[0]) * 100
    # MOMENTUM CEILING: Don't buy if already up more than 6% in 20 candles —
    # that's late-stage momentum, likely buying the peak
    if change_20 > 6.0:
        return {
            "is_momentum": False, "score": 0,
            "reasons": [f"BLOCKED: up {change_20:.1f}% in 20 candles — momentum ceiling (6%) — buying the peak"],
            "change_20": change_20,
        }
    uptrend_confirmed = 1.0 < change_20 < 6.0

    # Volume consistency: MA5 > MA20 (sustained interest, not one candle)
    vol_consistent = vol_ma5 and vol_ma20 and vol_ma5 > vol_ma20 * 0.9

    # ADX: trend strength > 20, or rising slope
    adx_ok = (adx_val and adx_val > 20) or (adx_slope and adx_slope > 0)

    # Higher-low structure
    hl_ok = has_higher_lows

    # Not at BB upper extreme
    not_overextended = bb_pct is None or bb_pct < 0.95

    # ── Late trend warnings (soft, not hard blocks) ───────────────────
    late_penalty = 0.0
    late_reasons = []

    if rsi_val and rsi_val > 78:
        late_penalty += 15
        late_reasons.append(f"RSI {rsi_val:.0f} > 78 — late trend warning")
    if price_vs_ema > 5:
        late_penalty += 15
        late_reasons.append(f"Price {price_vs_ema:.1f}% above EMA — stretched")
    if macd_hist_slope is not None and macd_hist > 0 and macd_hist_slope < 0:
        late_penalty += 10
        late_reasons.append("MACD histogram declining — momentum fading")

    # ── Breakout afterburner: boost if breakout conditions were recent ─
    afterburner = 0.0
    if len(df) >= 5:
        # Check if any of the last 3 candles had a breakout (BB%B > 0.8 or near 20-high)
        from indicators import bollinger_bands as _bb_fn
        bb_df = _bb_fn(df, config.bollinger_period, config.bollinger_std)
        recent_bb = bb_df["percent_b"].tail(4)
        had_breakout = any(v > 0.8 for v in recent_bb if pd.notna(v))
        if had_breakout:
            afterburner = 10.0
            late_reasons.append("Breakout afterburner: recent breakout detected, trend continuation")

    # ── Trend health score ────────────────────────────────────────────
    health = _compute_trend_health(ind, df)

    # ── All conditions must be met (with relaxed ADX if health is high) ─
    is_momentum = (
        strong_trend and riding_trend and rsi_momentum and
        macd_bullish and uptrend_confirmed and vol_consistent and
        not_overextended and
        (adx_ok or health > 50) and  # ADX or high health score
        (hl_ok or health > 60)        # higher lows or very high health
    )

    # ── Score ──────────────────────────────────────────────────────────
    score = 0.0
    reasons = []

    if strong_trend:
        score += 20
        reasons.append(f"Strong uptrend (EMA spread {ema_spread:.2f}%)")
    if riding_trend:
        score += 15
        reasons.append(f"Riding trend ({price_vs_ema:+.2f}% above EMA)")
    if rsi_momentum:
        score += 10
        reasons.append(f"RSI momentum zone ({rsi_val:.1f})")
    if macd_bullish:
        score += 10
        reasons.append(f"MACD bullish (hist={macd_hist:.6f})")
    if uptrend_confirmed:
        score += 10
        reasons.append(f"Up {change_20:+.1f}% over 20 candles")
    if vol_consistent:
        score += 10
        reasons.append(f"Volume consistent (MA5 {'>' if vol_ma5 > vol_ma20 else '>='} MA20)")
    if adx_ok:
        score += 10
        reasons.append(f"ADX {adx_val:.1f}" + (f" (rising)" if adx_slope and adx_slope > 0 else ""))
    if hl_ok:
        score += 10
        reasons.append(f"Higher-low structure ({ind.get('higher_lows_count', 0)} consecutive)")
    if health > 60:
        score += 5
        reasons.append(f"Trend health score {health:.0f}/100")

    # Apply afterburner boost
    score += afterburner

    # Apply late trend penalty
    score -= late_penalty
    for lr in late_reasons:
        reasons.append(f"  ⚠️ {lr}")

    return {
        "is_momentum": is_momentum,
        "score": max(0, min(100, score)),
        "reasons": reasons,
        "change_20": change_20,
        "vol_ratio": vol_ma5 / vol_ma20 if vol_ma5 and vol_ma20 else 1.0,
        "ema_spread": ema_spread,
        "price_vs_ema": price_vs_ema,
        "trend_health": health,
        "late_penalty": late_penalty,
        "afterburner": afterburner,
    }


def _check_momentum_exit(ind: dict[str, Any], df: pd.DataFrame) -> list[str]:
    """Check if a momentum position should be exited.

    Returns list of exit trigger reasons (empty = hold).
    """
    exits = []

    rsi_val = ind.get("rsi")
    macd_hist = ind.get("macd_histogram")
    price = ind.get("current_price")
    ema_short_val = ind.get("ema_short")
    adx_val = ind.get("adx")

    # RSI crosses below 55
    if rsi_val and rsi_val < 55:
        exits.append(f"RSI {rsi_val:.1f} < 55 — momentum cooling")

    # MACD histogram turns negative
    if macd_hist is not None and macd_hist < 0:
        exits.append("MACD histogram negative — momentum reversed")

    # Price closes below EMA short
    if price and ema_short_val and price < ema_short_val:
        exits.append(f"Price below EMA ({ema_short_val:.4f}) — trend broken")

    # ADX drops below 18
    if adx_val and adx_val < 18:
        exits.append(f"ADX {adx_val:.1f} < 18 — trend dissolving")

    # Lower-high structure (check last 20 candles)
    if len(df) >= 20:
        recent = df.tail(20)
        highs = recent["high"].values
        # Simple check: is the most recent high lower than the previous high?
        recent_highs = []
        for i in range(2, len(highs) - 2):
            if highs[i] >= max(highs[i-2:i]) and highs[i] >= max(highs[i+1:i+3]):
                recent_highs.append(highs[i])
        if len(recent_highs) >= 2 and recent_highs[-1] < recent_highs[-2]:
            exits.append("Lower-high structure — trend weakening")

    return exits


def evaluate_strategy(df: pd.DataFrame, config: TradingConfig) -> Signal:
    """Evaluate the combined momentum + mean reversion strategy.

    The strategy adapts to market conditions:
      - When EMAs are aligned bullish (short > long), it buys on momentum
        even if RSI is high (riding the trend).
      - When EMAs are neutral/bearish, it looks for oversold dips.
    """
    if df.empty or len(df) < 30:
        return Signal(
            action="hold",
            confidence=0.0,
            price=None,
            reasons=["Insufficient data for analysis"],
        )

    # ── Compute indicators ───────────────────────────────────────────────
    ind = compute_all_indicators(df, config)
    pats = detect_all_patterns(df, lookback=config.lookback_candles)
    price = ind.get("current_price")

    # ── Detect regime → select scoring weights ──────────────────────────
    regime, vol_rank, regime_weights = _detect_regime(df, ind, config)
    buy_weights = regime_weights
    sell_weights = regime_weights  # same weight distribution for buy & sell

    buy_score = 0.0
    sell_score = 0.0
    reasons: list[str] = []

    # 0. Log the detected regime
    reasons.append(f"Regime: {regime} (volatility rank {vol_rank:.0f}/100)")

    # ── Build FeatureBundle ────────────────────────────────────────────
    # In fast mode, skip expensive feature computations (exhaustion divergences,
    # breakout failure, smoothness, EMA slope, pullback quality).
    # These are only computed in deep analysis mode for the top pick.
    from trend.features import (
        FeatureBundle, ExhaustionSignals, compute_ema_slope_angle, compute_smoothness,
        compute_pullback_quality, detect_breakout_failure,
        detect_exhaustion, detect_trend_rebirth, compute_recent_modes,
    )
    from trend.phases import detect_phase, check_phase_stability, PHASE_LABELS
    from trend.scoring import compute_signal_confidence, get_confidence_label

    if _DEEP_ANALYSIS:
        # Always compute all features — tournament only evaluates 2 at a time
        from indicators import ema as calc_ema, macd as calc_macd
        ema_short_series = calc_ema(df["close"], config.ema_short)
        macd_df = calc_macd(df, config.macd_fast, config.macd_slow, config.macd_signal)
        macd_hist_series = macd_df["histogram"]

        ema_slope_deg = compute_ema_slope_angle(df, ema_short_series)
        smoothness = compute_smoothness(df, ema_short_series, macd_hist_series)
        pullback_quality = compute_pullback_quality(df, ind, ind.get("ema_short"))
        breakout_failed, breakout_fail_reasons = detect_breakout_failure(df, ind)
        exhaustion = detect_exhaustion(df, ind)
        is_exhausted = exhaustion.rsi_divergence or exhaustion.macd_divergence or exhaustion.volume_divergence
    else:
        # Fast mode — minimal features, no expensive recomputation
        ema_slope_deg = 0.0
        smoothness = 50.0
        pullback_quality = 0.0
        breakout_failed = False
        breakout_fail_reasons = []
        exhaustion = None
        is_exhausted = False

    # Recent modes — only computed in deep analysis mode (expensive: re-runs evaluate_strategy)
    if _DEEP_ANALYSIS:
        recent_modes = []
        try:
            recent_modes = compute_recent_modes(df, config, n=3)
        except Exception:
            pass
        was_momentum_recently = "buy" in recent_modes[:2]
    else:
        recent_modes = []
        was_momentum_recently = False

    is_rebirth, rebirth_reasons = detect_trend_rebirth(df, ind, recent_modes)

    features = FeatureBundle(
        ema_slope_deg=ema_slope_deg,
        smoothness_pct=smoothness,
        pullback_quality=pullback_quality,
        pullback_depth_pct=0,  # computed inside pullback_quality
        breakout_failed=breakout_failed,
        breakout_fail_reasons=breakout_fail_reasons,
        exhaustion=exhaustion or ExhaustionSignals(),
        is_exhausted=is_exhausted,
        is_rebirth=is_rebirth,
        rebirth_reasons=rebirth_reasons,
        recent_modes=recent_modes,
        was_momentum_recently=was_momentum_recently,
    )

    # ── Breakout detection (runs before anti-top filters) ──────────────
    # If a breakout is detected, score it directly as a momentum buy and
    # skip the dip-buying filters below.  This lets us catch assets that
    # are just starting to move (1-3% gain with volume) before they become
    # the 6%+ top gainers.
    breakout = _detect_breakout(df, ind, config)
    if breakout.get("is_breakout"):
        buy_score = breakout["score"]
        reasons.append(f"🔥 BREAKOUT — {PHASE_LABELS.get('breakout', 'breakout')} — {breakout['change_5']:+.1f}% move with {breakout['vol_ratio']:.1f}x volume")
        for br in breakout["reasons"]:
            reasons.append(f"  {br}")
        if breakout_failed:
            reasons.append("  ⚠️ Breakout failure risk detected:")
            for r in breakout_fail_reasons:
                reasons.append(f"    {r}")
            buy_score -= 20

        # Compute signal confidence
        health = _compute_trend_health(ind, df)
        conf, conf_reasons = compute_signal_confidence(buy_score, features, health, ind)
        reasons.append(f"  Signal confidence: {conf:.0f}/100 ({get_confidence_label(conf)})")
        reasons.extend(conf_reasons)

        signal = Signal(
            action="buy" if conf >= config.min_confidence else "hold",
            confidence=conf,
            price=price,
            reasons=reasons,
            indicators=ind,
            patterns=pats,
            regime=regime,
            volatility_rank=vol_rank,
        )
        if signal.action in ("buy", "sell") and price is not None:
            _compute_entry_exit(signal, ind, pats, config)
        return signal

    # ── Momentum rider detection ────────────────────────────────────────
    momentum = _detect_momentum(df, ind, config)

    # ── Unified trend phase model ──────────────────────────────────────
    phase = detect_phase(ind, df, breakout, momentum, features)
    # Phase stability only in deep analysis (expensive: re-computes indicators 3x)
    if _DEEP_ANALYSIS:
        phase_stable, phase_history = check_phase_stability(df, config, phase, lookback=3)
    else:
        phase_stable = True
        phase_history = [phase]
    features.phase = phase
    features.phase_stable = phase_stable
    features.phase_history = phase_history

    # ── Trend rebirth (Phase 2b — second leg) ──────────────────────────
    if features.is_rebirth and not breakout.get("is_breakout"):
        rebirth_score = max(momentum.get("score", 0), 50) + 15  # afterburner bonus
        reasons.append(f"🔥 TREND REBIRTH — {PHASE_LABELS.get(phase, phase)}")
        for rr in features.rebirth_reasons:
            reasons.append(f"  {rr}")
        reasons.append(f"  Trend smoothness: {features.smoothness_pct:.0f}%")
        reasons.append(f"  EMA slope: {features.ema_slope_deg:.1f}°")
        if not phase_stable:
            reasons.append("  ⚠️ Phase transitional")

        # Compute signal confidence
        health = _compute_trend_health(ind, df)
        conf, conf_reasons = compute_signal_confidence(rebirth_score, features, health, ind)
        reasons.append(f"  Signal confidence: {conf:.0f}/100 ({get_confidence_label(conf)})")
        reasons.extend(conf_reasons)

        signal = Signal(
            action="buy" if conf >= config.min_confidence else "hold",
            confidence=conf,
            price=price,
            reasons=reasons,
            indicators=ind,
            patterns=pats,
            regime=regime,
            volatility_rank=vol_rank,
        )
        if signal.action in ("buy", "sell") and price is not None:
            _compute_entry_exit(signal, ind, pats, config)
        return signal

    # ── Momentum rider signal ─────────────────────────────────────────
    if momentum.get("is_momentum"):
        buy_score = momentum["score"]
        reasons.append(f"🚀 MOMENTUM RIDER — {PHASE_LABELS.get(phase, phase)} — up {momentum['change_20']:+.1f}% over 20 candles")
        for mr in momentum["reasons"]:
            reasons.append(f"  {mr}")
        if momentum.get("trend_health"):
            reasons.append(f"  Trend health: {momentum['trend_health']:.0f}/100")
        reasons.append(f"  Trend smoothness: {features.smoothness_pct:.0f}% | EMA slope: {features.ema_slope_deg:.1f}°")
        if not phase_stable:
            reasons.append("  ⚠️ Phase transitional (unstable)")

        # Check exit conditions
        exits = _check_momentum_exit(ind, df)
        if exits:
            reasons.append("  ⚠️ Exit signals (if holding):")
            for ex in exits:
                reasons.append(f"    - {ex}")

        # Compute signal confidence
        health = momentum.get("trend_health", _compute_trend_health(ind, df))
        conf, conf_reasons = compute_signal_confidence(buy_score, features, health, ind)
        reasons.append(f"  Signal confidence: {conf:.0f}/100 ({get_confidence_label(conf)})")
        reasons.extend(conf_reasons)

        signal = Signal(
            action="buy" if conf >= config.min_confidence else "hold",
            confidence=conf,
            price=price,
            reasons=reasons,
            indicators=ind,
            patterns=pats,
            regime=regime,
            volatility_rank=vol_rank,
        )
        if signal.action in ("buy", "sell") and price is not None:
            _compute_entry_exit(signal, ind, pats, config)
        return signal

    # ── Trend dominance lockout ───────────────────────────────────────
    # If momentum score is high but didn't fully trigger (borderline),
    # suppress dip buyer to prevent buying micro-dips inside a runaway trend.
    momentum_dominant = (
        momentum.get("score", 0) > 50 and
        phase in ("momentum", "exhaustion")
    )

    # ── Anti-top filters (only for dip-buying, not breakouts/momentum) ─
    # 1. Don't buy if price rose >2% in last 5 candles (buying a spike)
    recent_5 = df["close"].tail(5)
    if len(recent_5) >= 5:
        price_change_5 = ((recent_5.iloc[-1] - recent_5.iloc[0]) / recent_5.iloc[0]) * 100
        # Raised spike threshold to 4% (was 2%) — don't block early momentum
        if price_change_5 > 4.0 and not momentum_dominant:
            reasons.append(f"BLOCKED: price +{price_change_5:.1f}% in 5 candles — buying a spike, not a dip")
            # Force hold — return early
            return Signal(
                action="hold",
                confidence=0.0,
                price=price,
                reasons=reasons,
                indicators=ind,
                patterns=pats,
            )

    # ── Detect market regime (needed by the entry gates below) ─────────
    rsi_val = ind.get("rsi")
    ema_short_val = ind.get("ema_short")
    ema_long_val = ind.get("ema_long")
    # Use a 0.5% tolerance band to avoid flipping on noise
    ema_spread = abs(ema_short_val - ema_long_val) / max(ema_long_val, 1) * 100 if ema_short_val and ema_long_val else 0
    trending_up = ema_short_val is not None and ema_long_val is not None and ema_short_val > ema_long_val and ema_spread > 0.5
    trending_down = ema_short_val is not None and ema_long_val is not None and ema_short_val < ema_long_val and ema_spread > 0.5

    # 2. RSI gate — regime-aware. Outside an uptrend, only buy genuine dips
    # (RSI < 62.8). In a CONFIRMED uptrend, RSI naturally lives above 62.8 for
    # days; demanding oversold there means never participating in the trend.
    # Allow up to 70 in uptrends — the pullback gate (#3) still requires price
    # to have retraced to trend support before any buy.
    # Raised ceilings — let momentum run longer before blocking
    rsi_ceiling = 78.0 if trending_up else 68.0
    if rsi_val is not None and rsi_val > rsi_ceiling and not momentum_dominant:
        reasons.append(
            f"RSI {rsi_val:.1f} above {rsi_ceiling} "
            f"({'overbought even for an uptrend' if trending_up else 'waiting for dip (not buying strength)'})"
        )
        return Signal(
            action="hold",
            confidence=0.0,
            price=price,
            reasons=reasons,
            indicators=ind,
            patterns=pats,
        )

    # 3. Pullback gate — don't buy at a premium above the short EMA. In an
    # uptrend this is THE entry: price retracing to the rising EMA is trend
    # support, not a top.
    # Raised pullback gate to 1.5% (was 0.5%) — allow buying slightly above EMA
    if ema_short_val and price and price > ema_short_val * 1.015:
        reasons.append(f"Price ${price:.2f} above EMA ${ema_short_val:.2f} — waiting for pullback to EMA")
        return Signal(
            action="hold",
            confidence=0.0,
            price=price,
            reasons=reasons,
            indicators=ind,
            patterns=pats,
        )

    # 4. Don't catch falling knives: in a sustained downtrend, "oversold" is the
    # default state, not a buy signal. Only allow a dip-buy if MACD momentum has
    # already turned positive (an actual reversal), otherwise stand aside and let
    # the uptrending assets compete for the capital.
    macd_hist_now = ind.get("macd_histogram")
    if trending_down and (macd_hist_now is None or macd_hist_now <= 0):
        reasons.append(
            f"BLOCKED: downtrend (EMA spread -{ema_spread:.2f}%) with no MACD reversal "
            f"(hist={macd_hist_now if macd_hist_now is not None else 'n/a'}) — not catching a falling knife"
        )
        return Signal(
            action="hold",
            confidence=0.0,
            price=price,
            reasons=reasons,
            indicators=ind,
            patterns=pats,
        )

    # ── RSI ─────────────────────────────────────────────────────────────
    rsi_sig = ind.get("rsi_signal", "neutral")

    if trending_up:
        # In an uptrend, buy pullbacks. Gate #3 already guarantees price has
        # retraced to the short EMA (trend support) before we get here.
        if rsi_val is not None:
            if rsi_val < 40:
                # Deep pullback in uptrend = strongest buy
                buy_score += buy_weights["rsi"]
                reasons.append(f"RSI pullback in uptrend ({rsi_val:.1f}) — dip buy")
            elif rsi_val <= 62.8:
                # Ordinary pullback to trend support — the bread-and-butter entry
                buy_score += buy_weights["rsi"] * 0.7
                reasons.append(f"Pullback to EMA support in uptrend (RSI {rsi_val:.1f}) — trend entry")
            elif rsi_val > 65:
                # Strong rally = take profit signal, not buy
                sell_score += sell_weights["rsi"]
                reasons.append(f"RSI overbought in uptrend ({rsi_val:.1f}) — take profit")
    elif trending_down:
        if rsi_val is not None:
            if rsi_val < 50:
                sell_score += sell_weights["rsi"] * min(1.0, (50 - rsi_val) / 20)
                reasons.append(f"RSI weak in downtrend ({rsi_val:.1f})")
            elif rsi_val > 60:
                sell_score += sell_weights["rsi"]
                reasons.append(f"RSI rally in downtrend ({rsi_val:.1f}) — sell into strength")
    else:
        # Ranging market — classic mean reversion
        if rsi_sig == "oversold":
            buy_score += buy_weights["rsi"]
            reasons.append(f"RSI oversold ({rsi_val:.1f})")
        elif rsi_sig == "overbought":
            sell_score += sell_weights["rsi"]
            reasons.append(f"RSI overbought ({rsi_val:.1f})")
        elif rsi_val is not None:
            if rsi_val < 48:
                buy_score += buy_weights["rsi"] * (48 - rsi_val) / 8
            elif rsi_val > 52:
                sell_score += sell_weights["rsi"] * (rsi_val - 52) / 8

    # ── Bollinger Bands ─────────────────────────────────────────────────
    bb_sig = ind.get("bollinger_signal", "neutral")
    bb_pct = ind.get("bollinger_percent_b")

    if trending_up:
        # In uptrend, buy dips toward the lower half of the band
        if bb_pct is not None:
            if bb_pct < 0.3:
                # Dip to lower band in uptrend = buy
                buy_score += buy_weights["bollinger"]
                reasons.append(f"BB dip in uptrend (%B={bb_pct:.2f}) — buy the dip")
            elif bb_pct < 0.5:
                # Below mid-band on a pullback — decent trend entry
                buy_score += buy_weights["bollinger"] * 0.6
                reasons.append(f"BB below midline in uptrend (%B={bb_pct:.2f}) — pullback entry")
            elif bb_pct > 0.8:
                # Price at upper band = take profit
                sell_score += sell_weights["bollinger"]
                reasons.append(f"BB upper band in uptrend (%B={bb_pct:.2f}) — take profit")
    elif trending_down:
        if bb_pct is not None:
            if bb_pct < 0.5:
                sell_score += sell_weights["bollinger"] * min(1.0, (1 - bb_pct))
                reasons.append(f"Price riding lower BB in downtrend (%B={bb_pct:.2f})")
            elif bb_pct > 0.7:
                sell_score += sell_weights["bollinger"]
                reasons.append(f"BB rally in downtrend (%B={bb_pct:.2f}) — sell into strength")
    else:
        # Ranging — classic mean reversion
        if bb_sig == "oversold":
            buy_score += buy_weights["bollinger"]
            reasons.append(f"BB oversold (%B={bb_pct:.2f})")
        elif bb_sig == "overbought":
            sell_score += sell_weights["bollinger"]
            reasons.append(f"BB overbought (%B={bb_pct:.2f})")
        elif bb_pct is not None:
            if bb_pct < 0.35:
                buy_score += buy_weights["bollinger"] * (0.35 - bb_pct) / 0.35
            elif bb_pct > 0.65:
                sell_score += sell_weights["bollinger"] * (bb_pct - 0.65) / 0.35

    # ── Oversold/overbought boost: both RSI + BB agree = strong signal ──
    if not trending_down and rsi_sig == "oversold" and bb_sig == "oversold":
        boost = 15
        buy_score += boost
        reasons.append(f"Double oversold (RSI + BB) — strong dip buy (+{boost})")
    if not trending_up and rsi_sig == "overbought" and bb_sig == "overbought":
        boost = 15
        sell_score += boost
        reasons.append(f"Double overbought (RSI + BB) — strong sell signal (+{boost})")

    # ── Deep oversold/overbought: extreme RSI levels get extra boost ────
    if rsi_val is not None and not trending_down:
        if rsi_val < 30:
            buy_score += 10
            reasons.append(f"RSI deeply oversold ({rsi_val:.1f}) — extra buy boost")
    if rsi_val is not None and not trending_up:
        if rsi_val > 70:
            sell_score += 10
            reasons.append(f"RSI deeply overbought ({rsi_val:.1f}) — extra sell boost")

    # ── MACD (momentum) ─────────────────────────────────────────────────
    macd_sig = ind.get("macd_signal_type", "neutral")
    macd_hist = ind.get("macd_histogram")
    # When RSI is deeply oversold, MACD is lagging — reduce its sell weight
    macd_sell_weight = sell_weights["macd"] * 0.3 if (rsi_val and rsi_val < 40) else sell_weights["macd"]
    if macd_sig == "bullish":
        buy_score += buy_weights["macd"]
        reasons.append(f"MACD bullish (hist={macd_hist:.4f})")
    elif macd_sig == "bearish":
        sell_score += macd_sell_weight
        reasons.append(f"MACD bearish (hist={macd_hist:.4f})")
    elif macd_hist is not None:
        if macd_hist > 0:
            buy_score += buy_weights["macd"] * 0.5
        elif macd_hist < 0:
            sell_score += macd_sell_weight * 0.5

    # ── EMA Crossover (trend) ───────────────────────────────────────────
    ema_sig = ind.get("ema_signal", "neutral")
    # When RSI is deeply oversold, EMA is lagging — reduce its sell weight
    ema_sell_weight = sell_weights["ema_cross"] * 0.3 if (rsi_val and rsi_val < 40) else sell_weights["ema_cross"]
    if ema_sig == "bullish":
        buy_score += buy_weights["ema_cross"]
        reasons.append("EMA bullish (short > long)")
    elif ema_sig == "bearish":
        sell_score += ema_sell_weight
        reasons.append("EMA bearish (short < long)")

    # ── Trend-pullback confluence bonus ─────────────────────────────────
    # Audit showed pullback entries in uptrends win ~75% (XRP bot style)
    # while chasing momentum loses. Reward the textbook setup:
    # uptrend + price at EMA support + RSI in pullback zone + MACD intact.
    if trending_up and ema_sig == "bullish" and rsi_val is not None and 35 <= rsi_val <= 55:
        price_near_ema = ema_short_val and price and abs(price - ema_short_val) / ema_short_val < 0.01
        macd_intact = ind.get("macd_histogram") is not None and ind.get("macd_histogram") > 0
        if price_near_ema and macd_intact:
            buy_score += 10
            reasons.append("Trend-pullback confluence (+10): uptrend + EMA support + RSI zone + MACD intact")

    # ── Volume confirmation ─────────────────────────────────────────────
    if len(df) >= 20:
        recent_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].tail(20).mean())
        if avg_vol > 0:
            vol_ratio = recent_vol / avg_vol
            if vol_ratio > 1.3:
                if buy_score > sell_score:
                    buy_score += buy_weights["volume"]
                    reasons.append(f"Volume spike ({vol_ratio:.1f}x avg) confirms buy")
                elif sell_score > buy_score:
                    sell_score += sell_weights["volume"]
                    reasons.append(f"Volume spike ({vol_ratio:.1f}x avg) confirms sell")

    # ── Decision ─────────────────────────────────────────────────────────
    buy_score = min(100, buy_score)
    sell_score = min(100, sell_score)

    # Pullback quality gate for dip buyer
    if buy_score > sell_score and buy_score >= config.min_confidence:
        if phase == "pullback" and pullback_quality < 60:
            action = "hold"
            confidence = buy_score * 0.5
            reasons.append(f"Pullback quality too low ({pullback_quality:.0f}/100)")
        elif is_exhausted:
            action = "hold"
            confidence = min(40, buy_score)
            reasons.append("Exhaustion signals present — not buying dips")
        else:
            action = "buy"
            confidence = buy_score
    elif sell_score > buy_score and sell_score >= config.min_confidence:
        action = "sell"
        confidence = sell_score
    else:
        action = "hold"
        confidence = max(buy_score, sell_score)
        reasons.append(f"Below min_confidence ({config.min_confidence:.0f}) — holding")

    if not reasons:
        reasons.append("No significant signals detected")

    # Compute signal confidence
    health = _compute_trend_health(ind, df)
    final_conf, conf_reasons = compute_signal_confidence(confidence, features, health, ind)
    if action in ("buy", "sell"):
        reasons.append(f"Signal confidence: {final_conf:.0f}/100 ({get_confidence_label(final_conf)})")
        reasons.extend(conf_reasons)
        reasons.append(f"Phase: {PHASE_LABELS.get(phase, phase)} ({'stable' if phase_stable else 'transitional'})")
        if pullback_quality > 0:
            reasons.append(f"Pullback quality: {pullback_quality:.0f}/100")

    signal = Signal(
        action=action,
        confidence=final_conf if action in ("buy", "sell") else round(confidence, 1),
        price=price,
        reasons=reasons,
        indicators=ind,
        patterns=pats,
        regime=regime,
        volatility_rank=vol_rank,
    )

    # Compute entry / stop-loss / take-profit for actionable signals
    if action in ("buy", "sell") and price is not None:
        _compute_entry_exit(signal, ind, pats, config)

    return signal


def _compute_entry_exit(
    signal: Signal,
    ind: dict[str, Any],
    pats: dict[str, Any],
    config: TradingConfig,
) -> None:
    """Populate entry_price, stop_loss, take_profit, and risk_reward on the signal.

    Uses ATR for stop distance, with support/resistance from pattern detection
    as a fallback / tightening mechanism.  Take-profit is placed at
    entry + (risk × risk_reward_ratio).
    """
    price = signal.price
    if price is None or price <= 0:
        return

    atr_val = ind.get("atr")
    sr = pats.get("support_resistance")

    if signal.action == "buy":
        # Stop-loss: the tighter of (ATR-based) or (just below nearest support)
        atr_stop = price - (atr_val * config.atr_multiplier) if atr_val else None
        sr_stop = None
        if sr and sr.get("nearest_support"):
            # Place stop just below support (1% buffer below the support level)
            sr_stop = sr["nearest_support"] * 0.99

        # Use the tighter stop (less risk) if both are available
        candidates = [s for s in (atr_stop, sr_stop) if s is not None and s < price]
        stop = max(candidates) if candidates else (atr_stop or price * 0.97)

        risk = price - stop
        tp = price + (risk * config.risk_reward_ratio)

    elif signal.action == "sell":
        # Mirror for shorts: stop above, TP below
        atr_stop = price + (atr_val * config.atr_multiplier) if atr_val else None
        sr_stop = None
        if sr and sr.get("nearest_resistance"):
            sr_stop = sr["nearest_resistance"] * 1.01

        candidates = [s for s in (atr_stop, sr_stop) if s is not None and s > price]
        stop = min(candidates) if candidates else (atr_stop or price * 1.03)

        risk = stop - price
        tp = price - (risk * config.risk_reward_ratio)

    else:
        return

    rr = abs(tp - price) / abs(price - stop) if abs(price - stop) > 0 else 0

    signal.entry_price = round(price, 6)
    signal.stop_loss = round(stop, 6)
    signal.take_profit = round(tp, 6)
    signal.risk_reward = round(rr, 2)
