"""Signal confidence scoring and contextual boosts.

Aggregates all feature contributions into a single 0-100 confidence
score with a list of human-readable reasons.
"""
from __future__ import annotations

from typing import Any

from trend.features import FeatureBundle


def compute_signal_confidence(
    mode_score: float,
    features: FeatureBundle,
    trend_health: float,
    ind: dict[str, Any],
) -> tuple[float, list[str]]:
    """Compute a unified signal confidence score (0-100).

    This is separate from the mode's raw score — it adjusts based on
    phase stability, trend smoothness, volume consistency, false trend
    detection, higher-low structure, EMA slope, and divergences.

    Returns (confidence, reasons).
    """
    conf = mode_score
    reasons = []

    # Phase stability boost
    if features.phase_stable:
        conf += 5
        reasons.append("Phase stable (2+ candles)")
    else:
        conf -= 5
        reasons.append("Phase transitional (unstable)")

    # Trend smoothness
    if features.smoothness_pct >= 70:
        conf += 8
        reasons.append(f"Smooth trend ({features.smoothness_pct:.0f}% well-behaved candles)")
    elif features.smoothness_pct >= 60:
        conf += 3
    else:
        conf -= 8
        reasons.append(f"Jagged trend ({features.smoothness_pct:.0f}% smooth — below 60% threshold)")

    # EMA slope angle
    angle = features.ema_slope_deg
    if 15 <= angle <= 35:
        conf += 5
        reasons.append(f"Healthy trend angle ({angle:.1f}°)")
    elif angle > 40:
        conf -= 8
        reasons.append(f"Blowoff risk ({angle:.1f}° > 40°)")
    elif angle < 10:
        conf -= 3
        reasons.append(f"Weak trend angle ({angle:.1f}° < 10°)")

    # Higher-low structure
    if ind.get("higher_lows"):
        conf += 5
    else:
        conf -= 3
        reasons.append("No higher-low structure")

    # Volume consistency
    vol_trend = ind.get("volume_trend", "neutral")
    if vol_trend == "rising":
        conf += 3
    elif vol_trend == "falling":
        conf -= 5
        reasons.append("Volume trend falling")

    # Exhaustion penalties
    if features.is_exhausted:
        conf -= 15
        reasons.append("⚠️ Exhaustion signals detected:")
        for r in features.exhaustion.reasons:
            reasons.append(f"  {r}")

    # Breakout failure penalty
    if features.breakout_failed:
        conf -= 20
        reasons.append("⚠️ Breakout failure detected:")
        for r in features.breakout_fail_reasons:
            reasons.append(f"  {r}")

    # Trend rebirth bonus
    if features.is_rebirth:
        conf += 15
        reasons.append("🔥 Trend rebirth — second leg detected (+15)")

    # Trend health contextual boost
    if trend_health > 70:
        conf += 5
    elif trend_health < 40:
        conf -= 5
        reasons.append(f"Low trend health ({trend_health:.0f}/100)")

    conf = max(0, min(100, conf))
    return round(conf, 1), reasons


def get_confidence_label(confidence: float) -> str:
    """Return a colored confidence label for the UI."""
    if confidence >= 70:
        return "🟢 High confidence"
    elif confidence >= 50:
        return "🟡 Medium confidence"
    else:
        return "🔴 Low confidence"
