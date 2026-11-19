"""Configuration for Kraken Scout — daily trade picker for the Kraken Funded challenge.

All sensitive values (API keys) come from keyring via secrets_utils.
Scout is advisory only — no order execution, no cold wallet, no DCA.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


# ── Database ──────────────────────────────────────────────────────────────
def _get_db_url() -> str:
    """Return the connection string, or a local file when none is configured.

    NEVER a baked-in credential. This module used to fall back to a live
    Postgres URL with the password inline, which then shipped inside the
    public bundle; any value of that kind must come from the environment
    (or the deployment's secret store) and nowhere else. With nothing set,
    Scout runs on its own local SQLite file so a customer install starts
    without an account on somebody else's database.
    """
    for key in ("DB195374BC_DATABASE_URL", "DB1005AA9E_DATABASE_URL", "DATABASE_URL"):
        val = os.environ.get(key)
        if val:
            return val
    from pathlib import Path
    return "sqlite:///" + (Path(__file__).resolve().parent / "scout_local.db").as_posix()


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

    # ── Contest entry size (Nov 11) ────────────────────────────────────
    # The fraction of the LIVE balance the scanner's base spend is. Before
    # this knob existed the base was 100% of the balance and only the 3%
    # guard's cap decided the size, so live traded anywhere from 4.7% to
    # 29.7% of the balance depending on the coin's profiled drawdown and no
    # configured number could bind. This is the CEILING: the 3% guard
    # (contest_account.max_entry_usd) and the book-carry limit still cut it
    # when the buffer is thin, so the traded size is
    # min(contest_size_frac * balance, guard cap, book carry, weakened size).
    contest_size_frac: float = 0.09

    # ── THE LIQUID UNIVERSE (Nov 19) — ON ──────────────────────────────
    # The book threw away 95% of its signals at entry because a $2,400 order
    # was more than 1% of the pair's median hourly dollar volume. The rule now
    # runs BEFORE the bracket: a pair that cannot fill the order cannot produce
    # a signal at all. 1% participation, $2,400 order (18% of the $11,200 win
    # line is $2,016, so every order the frozen book places fits a universe
    # built at 2,400). Evidence: CONTEST_BOOK.md.
    liquid_universe_enabled: bool = True
    liquid_universe_order_usd: float = 2400.0

    # ── Entry timing as a decision (Nov 15) ────────────────────────────
    # DEFAULT OFF, and deliberately so: the bracket records a shadow verdict on
    # every fire (what the ranking WOULD do, in the journal's `entry_timing`),
    # but the champion only changes when this flag is on. Flip it only on
    # evidence — ENTRY_TIMING.md carries the replay's numbers and the sample
    # size the effect needs. `entry_timing_window` is the confidence tolerance
    # for "as good a signal"; `entry_timing_min_gain` is how much lower (in the
    # prior-hour range) a rival must have entered before it can replace the
    # champion. Neither touches exits, stops or the death-line guard.
    entry_timing_rank: bool = False
    entry_timing_window: float = 10.0
    entry_timing_min_gain: float = 0.15

    # ── The deep read + what a coin can carry (Nov 15) ──────────────────
    # THE DEEP SCAN: the tournament used to judge a coin on the 200-candle
    # evaluation window and one ticker — a quick check. With this on, each
    # candidate also gets its own deep history (one fetch, `lookback` bars),
    # the empirical worst window drawdown from it, realized volatility, the
    # day's range, volume and spread, and the dollar capacity the 3% guard
    # would allow on it. Data only: nothing here sizes or exits a live order.
    deep_scan_enabled: bool = True
    deep_lookback_candles: int = 1000

    # THE SPEND RANKING, default OFF (same discipline as entry_timing_rank):
    # every fire records whether the bracket would have preferred the coin that
    # can carry the most spend, by how many dollars, and whether it would have
    # changed the champion. `scratch/spend_capacity_study.py` is the evidence
    # gate — flip only on data, never on the idea sounding right.
    spend_rank: bool = False
    spend_rank_window: float = 10.0
    spend_rank_min_usd: float = 25.0

    # BUY LOW, SELL HIGH (Nov 15), default OFF: among near-equal confidence
    # candidates, prefer the coin being bought cheapest right now — low in its
    # prior hour AND low in its day's range, read from the deep per-coin data.
    # Every fire records the comparison (who was compared, their scores, what it
    # would have preferred, whether the champion would have changed) whether or
    # not this flag is on. It runs after the timing and spend rankings and never
    # prices, sizes or exits anything: the dollars stay the guard's.
    buy_low_rank: bool = False
    buy_low_window: float = 10.0
    buy_low_min_gain: float = 0.15

    # ── THE BREAKOUT GATE (Nov 15) — ON, because it cleared a holdout ───
    # `scratch/tournament_study.py` graded the crown rule itself: crowning the
    # cheapest buy in the pool loses (−0.26%/trade OOS), and timing as a
    # ranking key is noise — but a champion sitting at the TOP of its prior
    # hour's range wins 80.6% of the time over 45 days (+0.74%/trade, n=36)
    # against 18.6% below it. So a buy is only fired when its own range
    # position says the move is a breakout, not a fall. No measurement at all
    # (too little history) still fires: the gate only removes fires it can
    # prove are mislocated. Numbers: breakout_gate.BREAKOUT_STUDY.
    champion_breakout_gate: bool = True
    champion_breakout_min: float = 0.85

    # ── Risk management ────────────────────────────────────────────────
    # Risk per trade as % of starting balance.
    risk_per_trade_pct: float = 1.5
    # Risk:reward ratio — take-profit is placed at entry + (risk × R:R)
    # Nov 11 (24-start sweep on the CORRECTED guard model): 2.0/2.0 is the
    # WORST option measured — worst death-line cushion $2.07 vs $5.30 and mean
    # span return -0.66% vs +2.57%. It only won while the lab's guard was
    # silently inert. Geometry stays at the shipped 2.5/1.5; the death-line
    # margin is bought in the guard block below instead.
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
    # (These feed the ADVISORY survival read in scanner.py. The ENFORCED guard
    #  on the live entry path is the survival_guard_* block below.)
    streak_protection_enabled: bool = True
    streak_reduce_after: int = 2           # reduce risk after 2 consecutive losses
    streak_reduce_factor: float = 0.5      # halve risk per trade
    streak_pause_after: int = 3            # stop recommending after 3 losses
    # Survival probability: estimate P(pass) from current balance + win rate
    survival_win_rate: float = 0.5         # assumed win rate for probability calc

    # ── THE SURVIVABILITY GUARD (Nov 10 — enforced, not advisory) ──────
    # The per-entry backtest: 56.8% of starts reach +12%, but 38.8% touch the
    # -3% line. The deaths are a CLUSTER of losses in a bad regime, so the
    # guard brakes the DECISION to trade instead of adding another exit.
    # Trips are evaluated only when a trade CLOSES; a trip pauses new entries
    # (open positions keep their exits); the cooldown reset resumes at the
    # probe size, and the first win restores full size. State lives in
    # survival_guard_state.json so a restart cannot forget a pause.
    # Parameters picked from the harness (Nov 10 sweep + rolling-window check):
    # a 3-loss/12h streak trip and a 24h drawdown pause both COST a contest
    # pass (the 09-18 window stopped passing), so the shipped set is the one
    # that removes every deathline window while keeping both passes.
    survival_guard_enabled: bool = True
    survival_guard_loss_streak: int = 4        # consecutive losses that trip a pause
    # Nov 11, 24-start sweep (corrected guard model): the dd trip at 0.9% of
    # start with a 4h cooldown measures worst cushion $7.05 vs $5.30 and mean
    # span return +3.74% vs +2.57% — better on BOTH axes, and 11/24 paired
    # wins (t=+2.59). The plateau is flat: 0.8 / 1.0 / 1.2% all land at
    # $6.74-$7.07 and +2.6 to +3.9%, so this is not a fitted corner.
    survival_guard_cooldown_hours: float = 4.0  # pause length after a streak trip
    survival_guard_dd_trip_pct: float = 0.9    # drawdown from peak (% of START) that trips
    survival_guard_dd_cooldown_hours: float = 4.0  # pause length after a drawdown trip
    survival_guard_probe_fraction: float = 0.5  # entry size after a reset, until a win

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
