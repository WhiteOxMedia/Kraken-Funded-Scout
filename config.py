"""Configuration for Kraken Scout — daily trade picker for the Kraken Funded challenge.

All sensitive values (API keys) come from keyring via secrets_utils.
Scout is advisory only — no order execution, no cold wallet, no DCA.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


# ── Database ──────────────────────────────────────────────────────────────
def _get_db_url() -> str:
    """Return the pooled Postgres connection string."""
    for key in ("DB195374BC_DATABASE_URL", "DB1005AA9E_DATABASE_URL", "DATABASE_URL"):
        val = os.environ.get(key)
        if val:
            return val
    return "postgresql://neondb_owner:npg_SgvAOU1buC8q@ep-crimson-wave-a69csseq-pooler.us-west-2.aws.neon.tech/neondb?channel_binding=require&sslmode=require"


def _get_db_direct_url() -> str:
    """Return the direct (non-pooled) Postgres connection string for migrations."""
    for key in ("DB195374BC_DIRECT_URL", "DB1005AA9E_DIRECT_URL", "DIRECT_URL"):
        val = os.environ.get(key)
        if val:
            return val
    return _get_db_url()


DB_URL = _get_db_url()
DB_DIRECT_URL = _get_db_direct_url()


# ── Scout parameters ──────────────────────────────────────────────────────
@dataclass
class TradingConfig:
    """Scout parameters for scanning pairs and generating trade signals.

    Kraken Funded challenge rules:
      - Grow starting balance by +12% to pass
      - Max loss is -5% from starting balance (fail instantly)
      - No trading fees during challenge
      - Buy/sell in dollar amounts, no leverage, no advanced orders
      - Mobile app only — scout is advisory, user executes manually
    """

    # ── Assets eligible for the Kraken Funded challenge ────────────────
    # The Funded app lets you buy/sell single assets in dollar amounts
    # (no pairs, no leverage).  We fetch /USD chart data for each asset
    # to run technical analysis, then present picks by asset name.
    # Full list of challenge-eligible assets as of Aug 2026.
    trading_pairs: list = field(default_factory=lambda: [
        "BTC/USD", "ETH/USD", "LINK/USD", "BCH/USD", "DOGE/USD",
        "ARB/USD", "AAVE/USD", "AVAX/USD", "BNB/USD", "CRV/USD",
        "DOT/USD", "FIL/USD", "GRASS/USD", "ADA/USD", "HBAR/USD",
        "INJ/USD", "KAITO/USD", "LDO/USD", "LTC/USD", "MOODENG/USD",
        "NEAR/USD", "PNUT/USD", "SOL/USD", "S/USD", "TRUMP/USD",
        "UNI/USD", "WLD/USD", "XRP/USD", "ALGO/USD", "APT/USD",
        "ATOM/USD", "ETC/USD", "JTO/USD", "JUP/USD", "ONDO/USD",
        "OP/USD", "POL/USD", "POPCAT/USD", "RENDER/USD", "STX/USD",
        "SUI/USD", "TAO/USD", "TIA/USD", "TRX/USD", "WIF/USD",
        "AIXBT/USD", "ASTER/USD", "FARTCOIN/USD", "HYPE/USD",
        "PENGU/USD", "PUMP/USD", "VIRTUAL/USD", "XPL/USD", "ZEC/USD",
    ])

    # ── Kraken Funded challenge parameters ─────────────────────────────
    challenge_balance: float = 1000.0      # starting balance ($1K tier)
    max_loss_pct: float = 5.0              # max drawdown from starting balance
    profit_target_pct: float = 12.0        # profit target to pass challenge
    round_trip_fee_pct: float = 0.0        # no fees in Funded challenge

    # ── Risk management ────────────────────────────────────────────────
    # Risk per trade as % of starting balance.
    risk_per_trade_pct: float = 1.5
    # Risk:reward ratio — take-profit is placed at entry + (risk × R:R)
    risk_reward_ratio: float = 2.5
    # ATR multiplier for stop-loss distance (1.5 × ATR below entry for buys)
    atr_multiplier: float = 1.5
    # Number of top picks to surface in the dashboard
    top_n: int = 5
    # Minimum confidence to include in picks (0–100)
    min_confidence: float = 40.0
    # Liquidity floor: skip assets below this 24h USD volume in tournaments.
    # NOTE: measured from Kraken OHLCV only — Kraken is a smaller venue, so
    # legit mid-caps (DOT, AVAX, NEAR...) do just $1-4M/day here. $1M removes
    # Daily floor for UNKNOWN coins (no engine history yet — fresh gainers).
    # Known coins use the rotation-aware filter instead (per-rotation volume
    # medians + a $250k daily backstop) — see filter_illiquid.
    min_volume_usd_24h: float = 750_000

    # ── OHLC data ──────────────────────────────────────────────────────
    candle_interval: int = 60              # 1h candles — less noise than 15min
    lookback_candles: int = 200            # candles to fetch for indicators

    # ── Multi-timeframe confluence ─────────────────────────────────────
    # Scan on 3 timeframes: long (trend), medium (signal), short (entry).
    # "hold" on other timeframes = neutral (not disagreement).
    # Only an opposite action (buy vs sell) counts as disagreement.
    mtf_enabled: bool = False
    mtf_long_interval: int = 240           # 4h — trend direction
    mtf_short_interval: int = 15           # 15m — entry timing
    # Minimum number of timeframes that must agree (1 = primary only, 2 = need one confirmation)
    mtf_min_agreement: int = 1

    # ── Signal stability confirmation ──────────────────────────────────
    # Re-run the strategy on the last N candle windows.  If the signal
    # was consistent (same action) across all windows, boost confidence.
    # If it just flipped, penalize — a one-candle signal is unreliable.
    stability_enabled: bool = True
    stability_lookback: int = 3            # check last 3 candle windows
    stability_bonus: float = 10.0          # confidence boost if stable
    stability_penalty: float = 10.0        # confidence cut if unstable

    # ── Liquidity / spread filters ─────────────────────────────────────
    # Wide spreads eat into the tight 5% max loss budget.  Filter out
    # illiquid assets where the bid-ask spread exceeds this threshold.
    liquidity_filter_enabled: bool = True
    max_spread_pct: float = 1.0            # max bid-ask spread as % of price
    min_volume_24h_usd: float = 100_000    # min 24h volume ($100K)

    # ── Challenge survival + streak protection ─────────────────────────
    # After consecutive losses, automatically reduce risk per trade.
    # If the losing streak deepens, pause recommendations entirely.
    streak_protection_enabled: bool = True
    streak_reduce_after: int = 2           # reduce risk after 2 consecutive losses
    streak_reduce_factor: float = 0.5      # halve risk per trade
    streak_pause_after: int = 3            # stop recommending after 3 losses
    # Survival probability: estimate P(pass) from current balance + win rate
    survival_win_rate: float = 0.5         # assumed win rate for probability calc

    # ── Indicator periods (shared by strategy + indicators) ────────────
    # Faster RSI — reacts to price changes sooner (9 instead of 14)
    # Higher oversold (35 instead of 30) — catches dips before they're deeply oversold
    # Higher overbought (75 instead of 70) — lets momentum run longer before exit
    rsi_period: int = 9
    rsi_oversold: float = 35.0
    rsi_overbought: float = 75.0
    # Faster MACD — earlier crossover detection (8/21/5 instead of 12/26/9)
    macd_fast: int = 8
    macd_slow: int = 21
    macd_signal: int = 5
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    ema_short: int = 9
    ema_long: int = 21
    stochastic_period: int = 14
    stochastic_oversold: float = 20.0
    stochastic_overbought: float = 80.0
    atr_period: int = 14
    lookback_candles_pattern: int = 50     # for pattern detection lookback


# Default config instance
DEFAULT_CONFIG = TradingConfig()
