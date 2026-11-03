"""Technical analysis indicators for OHLCV candlestick data.

All functions are pure calculators that take a pandas DataFrame (with columns:
open, high, low, close, volume) and return computed values.  Only pandas and
numpy are used — no external TA libraries.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from config import TradingConfig


def _validate_ohlcv(df: pd.DataFrame) -> None:
    """Ensure the DataFrame has the required OHLCV columns."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average.

    Parameters
    ----------
    series : pd.Series
        Input price series.
    period : int
        Look-back window.

    Returns
    -------
    pd.Series
        SMA values (NaN for the first *period* - 1 rows).
    """
    if period <= 0:
        raise ValueError("period must be positive")
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average.

    Parameters
    ----------
    series : pd.Series
        Input price series.
    period : int
        Span for the EMA.

    Returns
    -------
    pd.Series
        EMA values.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data (close column is used).
    period : int, optional
        RSI look-back period (default 14).

    Returns
    -------
    pd.Series
        RSI values in the range 0–100.
    """
    _validate_ohlcv(df)
    if period <= 0:
        raise ValueError("period must be positive")

    close = df["close"]
    delta = close.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series


def macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Moving Average Convergence Divergence.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data (close column is used).
    fast : int, optional
        Fast EMA period (default 12).
    slow : int, optional
        Slow EMA period (default 26).
    signal : int, optional
        Signal-line EMA period (default 9).

    Returns
    -------
    pd.DataFrame
        Columns: ``macd``, ``signal``, ``histogram``.
    """
    _validate_ohlcv(df)
    if fast <= 0 or slow <= 0 or signal <= 0:
        raise ValueError("all periods must be positive")
    if fast >= slow:
        raise ValueError("fast period must be smaller than slow period")

    close = df["close"]
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line

    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
        }
    )


def bollinger_bands(
    df: pd.DataFrame,
    period: int = 20,
    std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands with bandwidth and %B.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data (close column is used).
    period : int, optional
        SMA look-back period (default 20).
    std : float, optional
        Number of standard deviations (default 2.0).

    Returns
    -------
    pd.DataFrame
        Columns: ``upper``, ``middle``, ``lower``, ``bandwidth``, ``percent_b``.
    """
    _validate_ohlcv(df)
    if period <= 0:
        raise ValueError("period must be positive")
    if std <= 0:
        raise ValueError("std must be positive")

    close = df["close"]
    middle = sma(close, period)
    rolling_std = close.rolling(window=period, min_periods=period).std()

    upper = middle + (rolling_std * std)
    lower = middle - (rolling_std * std)

    bandwidth = (upper - lower) / middle
    percent_b = (close - lower) / (upper - lower)

    return pd.DataFrame(
        {
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "bandwidth": bandwidth,
            "percent_b": percent_b,
        }
    )


def stochastic(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Stochastic Oscillator (%K and %D).

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data (high, low, close columns are used).
    period : int, optional
        Look-back period (default 14).

    Returns
    -------
    pd.DataFrame
        Columns: ``k`` (%K), ``d`` (%D, 3-period SMA of %K).
    """
    _validate_ohlcv(df)
    if period <= 0:
        raise ValueError("period must be positive")

    low_min = df["low"].rolling(window=period, min_periods=period).min()
    high_max = df["high"].rolling(window=period, min_periods=period).max()

    k = 100 * (df["close"] - low_min) / (high_max - low_min)
    d = sma(k, 3)

    return pd.DataFrame({"k": k, "d": d})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data.
    period : int, optional
        ATR look-back period (default 14).

    Returns
    -------
    pd.Series
        ATR values.
    """
    _validate_ohlcv(df)
    if period <= 0:
        raise ValueError("period must be positive")

    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return atr_series


def volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Simple Moving Average of volume.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data (volume column is used).
    period : int, optional
        SMA look-back period (default 20).

    Returns
    -------
    pd.Series
        Volume SMA values.
    """
    _validate_ohlcv(df)
    if period <= 0:
        raise ValueError("period must be positive")
    return sma(df["volume"], period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength (not direction).

    ADX > 25 = strong trend, ADX < 20 = weak/no trend.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data.
    period : int, optional
        Look-back period (default 14).

    Returns
    -------
    pd.Series
        ADX values (0-100).
    """
    _validate_ohlcv(df)
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    # Wilder's smoothing (same as EMA with alpha = 1/period)
    atr_w = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / atr_w)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False, min_periods=period).mean() / atr_w)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
    adx_series = dx.ewm(alpha=1/period, adjust=False, min_periods=period).mean()

    return adx_series


def detect_higher_lows(df: pd.DataFrame, lookback: int = 30, order: int = 2) -> dict:
    """Detect higher-low structure — proof of a real uptrend.

    Finds local minima in the low column and checks if the most recent
    ones are ascending (each higher than the previous).

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data.
    lookback : int
        Number of candles to examine.
    order : int
        How many candles on each side must be higher to confirm a local min.

    Returns
    -------
    dict
        {"has_higher_lows": bool, "count": int, "lows": list[float]}
    """
    _validate_ohlcv(df)
    if len(df) < lookback:
        return {"has_higher_lows": False, "count": 0, "lows": []}

    recent = df.tail(lookback)
    lows_idx = []
    vals = recent["low"].values
    n = len(vals)

    for i in range(order, n - order):
        left = vals[i - order:i]
        right = vals[i + 1:i + 1 + order]
        if vals[i] <= np.min(left) and vals[i] <= np.min(right):
            lows_idx.append(i)

    lows = [float(vals[i]) for i in lows_idx]

    # Check if last 2+ lows are ascending
    higher_count = 0
    for i in range(1, len(lows)):
        if lows[i] > lows[i - 1]:
            higher_count += 1
        else:
            higher_count = 0  # reset on break

    has_higher_lows = higher_count >= 1 and len(lows) >= 2

    return {
        "has_higher_lows": has_higher_lows,
        "count": higher_count,
        "lows": lows[-4:] if len(lows) >= 4 else lows,
    }


def _latest(series: pd.Series) -> float | None:
    """Return the latest non-NaN value, or None if all are NaN."""
    val = series.dropna().iloc[-1] if not series.dropna().empty else None
    return float(val) if val is not None and not np.isnan(val) else None


def _rsi_signal(rsi_val: float | None, oversold: float, overbought: float) -> str:
    if rsi_val is None:
        return "neutral"
    if rsi_val <= oversold:
        return "oversold"
    if rsi_val >= overbought:
        return "overbought"
    return "neutral"


def _macd_signal_type(
    macd_val: float | None,
    signal_val: float | None,
    histogram: float | None,
) -> str:
    if macd_val is None or signal_val is None or histogram is None:
        return "neutral"

    # Crossover detection: histogram changes sign between last two bars
    # We only have the latest values here, so we infer from histogram sign
    # and relative position of MACD vs signal.
    if histogram > 0 and macd_val > signal_val:
        return "bullish"
    if histogram < 0 and macd_val < signal_val:
        return "bearish"
    return "neutral"


def _bollinger_signal(percent_b: float | None) -> str:
    if percent_b is None:
        return "neutral"
    if percent_b <= 0.05:
        return "oversold"
    if percent_b >= 0.95:
        return "overbought"
    return "neutral"


def _ema_signal(ema_short: float | None, ema_long: float | None) -> str:
    if ema_short is None or ema_long is None:
        return "neutral"
    if ema_short > ema_long:
        return "bullish"
    if ema_short < ema_long:
        return "bearish"
    return "neutral"


def _stoch_signal(k: float | None, oversold: float, overbought: float) -> str:
    if k is None:
        return "neutral"
    if k <= oversold:
        return "oversold"
    if k >= overbought:
        return "overbought"
    return "neutral"


def compute_all_indicators(df: pd.DataFrame, config: TradingConfig) -> dict:
    """Compute all indicators and return the latest values as a flat dict.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns: open, high, low, close, volume.
    config : TradingConfig
        Configuration object holding indicator periods and thresholds.

    Returns
    -------
    dict
        Dictionary with latest indicator values and signal strings.
    """
    if df.empty:
        return {
            "rsi": None, "rsi_signal": "neutral",
            "macd": None, "macd_signal": None, "macd_histogram": None, "macd_signal_type": "neutral",
            "bollinger_upper": None, "bollinger_lower": None, "bollinger_middle": None,
            "bollinger_percent_b": None, "bollinger_signal": "neutral",
            "ema_short": None, "ema_long": None, "ema_signal": "neutral",
            "stoch_k": None, "stoch_d": None, "stoch_signal": "neutral",
            "atr": None, "current_price": None,
            "adx": None, "adx_slope": None,
            "volume_ma5": None, "volume_ma20": None, "volume_trend": "neutral",
            "higher_lows": False, "higher_lows_count": 0,
            "macd_hist_slope": None,
            "intraday_wick_pct": None,
        }

    # RSI
    rsi_series = rsi(df, config.rsi_period)
    rsi_val = _latest(rsi_series)

    # MACD
    macd_df = macd(df, config.macd_fast, config.macd_slow, config.macd_signal)
    macd_val = _latest(macd_df["macd"])
    macd_signal_val = _latest(macd_df["signal"])
    macd_hist_val = _latest(macd_df["histogram"])
    # MACD histogram slope (last 3 candles)
    hist_series = macd_df["histogram"].tail(3)
    macd_hist_slope = float(hist_series.iloc[-1] - hist_series.iloc[0]) if len(hist_series) >= 3 else None

    # Bollinger Bands
    bb_df = bollinger_bands(df, config.bollinger_period, config.bollinger_std)
    bb_upper = _latest(bb_df["upper"])
    bb_lower = _latest(bb_df["lower"])
    bb_middle = _latest(bb_df["middle"])
    bb_percent_b = _latest(bb_df["percent_b"])

    # EMAs
    ema_short_series = ema(df["close"], config.ema_short)
    ema_long_series = ema(df["close"], config.ema_long)
    ema_short_val = _latest(ema_short_series)
    ema_long_val = _latest(ema_long_series)

    # Stochastic
    stoch_df = stochastic(df, config.stochastic_period)
    stoch_k_val = _latest(stoch_df["k"])
    stoch_d_val = _latest(stoch_df["d"])

    # ATR
    atr_series = atr(df, config.atr_period)
    atr_val = _latest(atr_series)

    # ADX (trend strength)
    adx_series = adx(df, config.atr_period)
    adx_val = _latest(adx_series)
    adx_slope = None
    if adx_series is not None and len(adx_series) >= 3:
        recent_adx = adx_series.tail(3).dropna()
        if len(recent_adx) >= 2:
            adx_slope = float(recent_adx.iloc[-1] - recent_adx.iloc[0])

    # Volume trend
    vol_ma5 = _latest(sma(df["volume"], 5))
    vol_ma20 = _latest(sma(df["volume"], 20))
    vol_trend = "neutral"
    if vol_ma5 and vol_ma20:
        if vol_ma5 > vol_ma20 * 1.1:
            vol_trend = "rising"
        elif vol_ma5 < vol_ma20 * 0.9:
            vol_trend = "falling"

    # Higher-low structure
    hl = detect_higher_lows(df, lookback=30, order=2)

    # Intraday wick percentage (detect manipulation / news spikes)
    recent_candle = df.iloc[-1]
    candle_range = recent_candle["high"] - recent_candle["low"]
    body = abs(recent_candle["close"] - recent_candle["open"])
    intraday_wick_pct = ((candle_range - body) / candle_range * 100) if candle_range > 0 else 0

    # Current price
    current_price = _latest(df["close"])

    return {
        "rsi": rsi_val,
        "rsi_signal": _rsi_signal(rsi_val, config.rsi_oversold, config.rsi_overbought),
        "macd": macd_val,
        "macd_signal": macd_signal_val,
        "macd_histogram": macd_hist_val,
        "macd_signal_type": _macd_signal_type(macd_val, macd_signal_val, macd_hist_val),
        "bollinger_upper": bb_upper,
        "bollinger_lower": bb_lower,
        "bollinger_middle": bb_middle,
        "bollinger_percent_b": bb_percent_b,
        "bollinger_signal": _bollinger_signal(bb_percent_b),
        "ema_short": ema_short_val,
        "ema_long": ema_long_val,
        "ema_signal": _ema_signal(ema_short_val, ema_long_val),
        "stoch_k": stoch_k_val,
        "stoch_d": stoch_d_val,
        "stoch_signal": _stoch_signal(stoch_k_val, config.stochastic_oversold, config.stochastic_overbought),
        "atr": atr_val,
        "current_price": current_price,
        "adx": adx_val,
        "adx_slope": adx_slope,
        "volume_ma5": vol_ma5,
        "volume_ma20": vol_ma20,
        "volume_trend": vol_trend,
        "higher_lows": hl["has_higher_lows"],
        "higher_lows_count": hl["count"],
        "macd_hist_slope": macd_hist_slope,
        "intraday_wick_pct": float(intraday_wick_pct),
    }
