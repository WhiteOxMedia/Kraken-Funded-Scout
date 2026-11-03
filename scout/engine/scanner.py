"""Multi-pair scanner for Kraken Scout.

Fetches OHLCV data for all configured pairs, runs the strategy on each,
ranks by confidence, and returns the top trade opportunities.

Enhanced with:
  - Multi-timeframe confluence (3 timeframes must agree)
  - Signal stability confirmation (signal must persist across candles)
  - Liquidity / spread filters (skip illiquid assets)
  - Challenge survival probability + streak protection mode
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from config import TradingConfig
from kraken_api import KrakenAPI
from strategy import Signal, evaluate_strategy

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result of scanning a single pair."""

    pair: str
    signal: Signal
    ohlcv_df: pd.DataFrame | None = None
    error: str | None = None
    # Multi-timeframe confluence
    mtf_signals: dict[str, str] = field(default_factory=dict)  # {"4h": "buy", "1h": "buy", "15m": "hold"}
    mtf_agreement: int = 0          # how many timeframes agree (0-3)
    mtf_confluent: bool = False     # True if >= mtf_min_agreement agree
    # Signal stability
    stability_score: float = 0.0    # 0-100, higher = more stable
    stable: bool = False
    # Liquidity
    spread_pct: float | None = None
    volume_24h_usd: float | None = None
    liquid: bool = True

    @property
    def actionable(self) -> bool:
        """Whether this result has an actionable, filtered signal."""
        return (
            self.signal.action in ("buy", "sell")
            and self.error is None
            and self.liquid
            and (not self.signal.confidence or True)  # confidence checked by caller
        )


@dataclass
class PositionSizing:
    """Position sizing recommendation for a trade."""

    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    position_size_usd: float
    risk_amount_usd: float
    risk_pct: float
    potential_profit_usd: float


@dataclass
class SurvivalProbability:
    """Challenge survival / pass probability estimate."""

    current_balance: float
    starting_balance: float
    target_balance: float
    max_loss_balance: float
    loss_streak: int
    risk_per_trade_pct: float
    win_rate: float
    risk_reward_ratio: float
    # Estimated probability of passing (0-1)
    pass_probability: float
    # Recommended action: "trade", "reduce_risk", "pause"
    recommendation: str
    # Adjusted risk per trade (after streak protection)
    adjusted_risk_pct: float
    # Max trades remaining before max loss
    trades_remaining: int

    @property
    def pass_probability_pct(self) -> float:
        return round(self.pass_probability * 100, 1)


# ── Helpers ──────────────────────────────────────────────────────────────

def _ohlcv_to_df(ohlcv: list) -> pd.DataFrame:
    """Convert raw OHLCV list to a DataFrame with datetime index."""
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def _check_signal_stability(
    df: pd.DataFrame,
    config: TradingConfig,
    target_action: str,
) -> tuple[float, bool]:
    """Check if the signal has been stable across recent candle windows.

    Re-runs evaluate_strategy on windows ending 1, 2, 3 candles ago.
    If the action was consistent (same as target_action), the signal is stable.

    Returns (stability_score 0-100, is_stable).
    """
    if not config.stability_enabled or len(df) < 35:
        return 50.0, False

    consistent = 0
    total = config.stability_lookback

    for offset in range(1, total + 1):
        if len(df) <= offset + 30:
            continue
        window = df.iloc[:-offset]
        if len(window) < 30:
            continue
        sig = evaluate_strategy(window, config)
        if sig.action == target_action:
            consistent += 1
        elif sig.action == "hold":
            consistent += 0.5  # neutral = partially stable

    score = (consistent / total) * 100 if total > 0 else 0
    is_stable = consistent >= total * 0.7  # 70%+ consistency = stable

    return round(score, 1), is_stable


def _check_liquidity(
    kraken: KrakenAPI,
    pair: str,
    config: TradingConfig,
) -> tuple[float | None, float | None, bool]:
    """Check spread and volume for a pair.

    Returns (spread_pct, volume_24h_usd, is_liquid).
    """
    if not config.liquidity_filter_enabled:
        return None, None, True

    try:
        ticker = kraken.fetch_ticker(pair)
        bid = float(ticker.get("bid", 0))
        ask = float(ticker.get("ask", 0))
        volume = float(ticker.get("quoteVolume", 0))

        spread_pct = None
        if bid > 0 and ask > 0:
            spread_pct = ((ask - bid) / bid) * 100

        is_liquid = True
        if spread_pct is not None and spread_pct > config.max_spread_pct:
            is_liquid = False
        if volume < config.min_volume_24h_usd:
            is_liquid = False

        return spread_pct, volume, is_liquid
    except Exception as exc:
        logger.debug("Liquidity check failed for %s: %s", pair, exc)
        return None, None, True  # don't block on ticker errors


# ── Tournament scanner ───────────────────────────────────────────────────

def _compare_signals(a: ScanResult, b: ScanResult, long_only: bool = True) -> tuple[ScanResult, ScanResult, str]:
    """Compare two scan results head-to-head. Returns (winner, loser, reason).

    long_only=True  → BOT/BRIDGE mode: a buy ALWAYS beats a sell (we never
                      short, so sells can't win a match against any long).
    long_only=False → CONTEST mode (the original rules): actionable beats
                      hold, then pure confidence — a strong sell CAN win the
                      bracket, which displays as EXIT / AVOID ("market's bad,
                      don't enter"). Honest signal, never a short call.
    """
    # Actionable beats non-actionable
    if a.actionable and not b.actionable:
        return a, b, f"{_asset(a.pair)} {a.signal.action}({a.signal.confidence:.0f}%) beats {_asset(b.pair)} hold"
    if b.actionable and not a.actionable:
        return b, a, f"{_asset(b.pair)} {b.signal.action}({b.signal.confidence:.0f}%) beats {_asset(a.pair)} hold"
    if not a.actionable and not b.actionable:
        # Both hold — higher confidence wins
        if a.signal.confidence >= b.signal.confidence:
            return a, b, f"{_asset(a.pair)} hold({a.signal.confidence:.0f}%) beats {_asset(b.pair)} hold({b.signal.confidence:.0f}%)"
        return b, a, f"{_asset(b.pair)} hold({b.signal.confidence:.0f}%) beats {_asset(a.pair)} hold({a.signal.confidence:.0f}%)"

    # Both actionable — LONG-ONLY (bot mode): a buy always beats a sell.
    if long_only:
        a_buy, b_buy = a.signal.action == "buy", b.signal.action == "buy"
        if a_buy and not b_buy:
            return a, b, f"{_asset(a.pair)} buy({a.signal.confidence:.0f}%) beats {_asset(b.pair)} short — longs only"
        if b_buy and not a_buy:
            return b, a, f"{_asset(b.pair)} buy({b.signal.confidence:.0f}%) beats {_asset(a.pair)} short — longs only"

    # Both actionable — higher confidence wins (original contest rule)
    if a.signal.confidence > b.signal.confidence:
        return a, b, f"{_asset(a.pair)} {a.signal.action}({a.signal.confidence:.0f}%) beats {_asset(b.pair)} {b.signal.action}({b.signal.confidence:.0f}%)"
    if b.signal.confidence > a.signal.confidence:
        return b, a, f"{_asset(b.pair)} {b.signal.action}({b.signal.confidence:.0f}%) beats {_asset(a.pair)} {a.signal.action}({a.signal.confidence:.0f}%)"

    # Tie on confidence — better R:R wins
    rr_a = a.signal.risk_reward or 0
    rr_b = b.signal.risk_reward or 0
    if rr_a >= rr_b:
        return a, b, f"{_asset(a.pair)} wins tiebreak (R:R {rr_a:.1f} vs {rr_b:.1f})"
    return b, a, f"{_asset(b.pair)} wins tiebreak (R:R {rr_b:.1f} vs {rr_a:.1f})"


def _asset(pair: str) -> str:
    return pair.split("/")[0] if "/" in pair else pair


def tournament_scan(
    kraken: KrakenAPI,
    config: TradingConfig,
    progress_callback=None,
    long_only: bool = True,
) -> tuple[ScanResult, list[dict]]:
    """Run a single-elimination tournament across all assets.

    1. Shuffle the asset list (different bracket each time)
    2. Round 1: 1v1 matches, fetch + evaluate both, winner advances
    3. Later rounds: cached results, instant comparisons
    4. Final winner is THE best asset to trade

    long_only=True  → bot/bridge mode (buys always beat sells)
    long_only=False → contest mode (original rules: honest bracket, a strong
                      sell can win and displays as EXIT / AVOID)

    Returns (winner, match_history).
    progress_callback(round_num, match_num, total_matches, result_str) for UI updates.
    """
    import random

    pairs = list(config.trading_pairs)
    random.shuffle(pairs)

    # Round 1: fetch + evaluate all assets (2 at a time)
    # Store results in a dict for caching
    evaluated: dict[str, ScanResult] = {}
    match_history: list[dict] = []

    # Evaluate all assets first (needed for round 1)
    # Do it in pairs so we can show progress
    bracket = list(pairs)

    round_num = 1
    while len(bracket) > 1:
        next_round = []
        total_matches = len(bracket) // 2

        for i in range(0, len(bracket) - 1, 2):
            pair_a = bracket[i]
            pair_b = bracket[i + 1]
            match_num = i // 2 + 1

            # Evaluate A if not cached
            if pair_a not in evaluated:
                try:
                    evaluated[pair_a] = _evaluate_single(kraken, pair_a, config)
                except Exception:
                    evaluated[pair_a] = ScanResult(
                        pair=pair_a, signal=Signal(action="hold", confidence=0.0, price=None, reasons=["Scan error"]),
                        error="error",
                    )

            # Evaluate B if not cached
            if pair_b not in evaluated:
                try:
                    evaluated[pair_b] = _evaluate_single(kraken, pair_b, config)
                except Exception:
                    evaluated[pair_b] = ScanResult(
                        pair=pair_b, signal=Signal(action="hold", confidence=0.0, price=None, reasons=["Scan error"]),
                        error="error",
                    )

            result_a = evaluated[pair_a]
            result_b = evaluated[pair_b]

            # Compare
            winner, loser, reason = _compare_signals(result_a, result_b, long_only=long_only)
            next_round.append(winner.pair)

            match_info = {
                "round": round_num,
                "match": match_num,
                "total_matches": total_matches,
                "pair_a": pair_a, "pair_b": pair_b,
                "winner": winner.pair, "loser": loser.pair,
                "reason": reason,
                "a_action": result_a.signal.action, "a_conf": result_a.signal.confidence,
                "b_action": result_b.signal.action, "b_conf": result_b.signal.confidence,
                "winner_action": winner.signal.action, "winner_conf": winner.signal.confidence,
            }
            match_history.append(match_info)

            if progress_callback:
                progress_callback(match_info)

        # Odd bye — last asset auto-advances
        if len(bracket) % 2 == 1:
            bye = bracket[-1]
            next_round.append(bye)
            match_history.append({
                "round": round_num, "match": total_matches + 1, "total_matches": total_matches,
                "pair_a": bye, "pair_b": "(bye)", "winner": bye, "loser": "",
                "reason": f"{_asset(bye)} gets a bye",
            })
            if progress_callback:
                # Bye asset may not have been evaluated yet — use placeholder values
                bye_result = evaluated.get(bye)
                bye_action = bye_result.signal.action if bye_result else "hold"
                bye_conf = bye_result.signal.confidence if bye_result else 0.0
                progress_callback({
                    "round": round_num, "match": total_matches + 1, "total_matches": total_matches,
                    "pair_a": bye, "pair_b": "(bye)", "winner": bye, "loser": "",
                    "reason": f"{_asset(bye)} gets a bye",
                    "a_action": bye_action, "a_conf": bye_conf,
                    "b_action": "bye", "b_conf": 0,
                    "winner_action": bye_action, "winner_conf": bye_conf,
                })

        bracket = next_round
        round_num += 1

    # Champion
    champion_pair = bracket[0]
    champion = evaluated[champion_pair]

    return champion, match_history


def _evaluate_single(kraken: KrakenAPI, pair: str, config: TradingConfig) -> ScanResult:
    """Fetch OHLCV and evaluate a single asset."""
    ohlcv = kraken.fetch_ohlcv(
        pair=pair, interval_minutes=config.candle_interval,
        limit=config.lookback_candles,
    )
    if not ohlcv or len(ohlcv) < 30:
        return ScanResult(
            pair=pair,
            signal=Signal(action="hold", confidence=0.0, price=None, reasons=["Insufficient data"]),
            error="Insufficient data",
        )

    df = _ohlcv_to_df(ohlcv)
    signal = evaluate_strategy(df, config)

    result = ScanResult(pair=pair, signal=signal, ohlcv_df=df)

    # Liquidity check
    if config.liquidity_filter_enabled:
        spread, volume, is_liquid = _check_liquidity(kraken, pair, config)
        result.spread_pct = spread
        result.volume_24h_usd = volume
        result.liquid = is_liquid
        if not is_liquid:
            signal.action = "hold"
            signal.reasons.append("Illiquid — filtered")

    return result


# ── Core scan ────────────────────────────────────────────────────────────

def scan_pairs(
    kraken: KrakenAPI,
    config: TradingConfig,
) -> list[ScanResult]:
    """Scan all configured pairs with multi-timeframe confluence,
    signal stability, and liquidity filters.

    Returns all scan results sorted by (actionable, confidence).
    """
    results: list[ScanResult] = []

    for pair in config.trading_pairs:
        try:
            # ── Primary timeframe ──────────────────────────────────────
            ohlcv = kraken.fetch_ohlcv(
                pair=pair,
                interval_minutes=config.candle_interval,
                limit=config.lookback_candles,
            )
            if not ohlcv or len(ohlcv) < 30:
                results.append(ScanResult(
                    pair=pair,
                    signal=Signal(action="hold", confidence=0.0, price=None,
                                  reasons=["Insufficient data"]),
                    error="Insufficient OHLCV data",
                ))
                continue

            df = _ohlcv_to_df(ohlcv)
            signal = evaluate_strategy(df, config)

            result = ScanResult(pair=pair, signal=signal, ohlcv_df=df)

            # ── Multi-timeframe confluence ─────────────────────────────
            if config.mtf_enabled and signal.action in ("buy", "sell"):
                mtf_signals = {f"{config.candle_interval}m": signal.action}
                agreement = 1  # primary TF agrees with itself

                for tf_label, tf_minutes in [
                    (f"{config.mtf_long_interval}m", config.mtf_long_interval),
                    (f"{config.mtf_short_interval}m", config.mtf_short_interval),
                ]:
                    try:
                        tf_ohlcv = kraken.fetch_ohlcv(
                            pair=pair, interval_minutes=tf_minutes,
                            limit=config.lookback_candles,
                        )
                        if tf_ohlcv and len(tf_ohlcv) >= 30:
                            tf_df = _ohlcv_to_df(tf_ohlcv)
                            tf_signal = evaluate_strategy(tf_df, config)
                            mtf_signals[tf_label] = tf_signal.action
                            if tf_signal.action == signal.action:
                                agreement += 1
                            elif tf_signal.action == "hold":
                                agreement += 0.5
                    except Exception:
                        mtf_signals[tf_label] = "error"

                result.mtf_signals = mtf_signals
                result.mtf_agreement = agreement
                result.mtf_confluent = agreement >= config.mtf_min_agreement

                # Only penalize if an OPPOSITE signal was found (not just "hold")
                opposite_found = any(
                    v in ("buy", "sell") and v != signal.action
                    for v in mtf_signals.values()
                )
                if opposite_found:
                    signal.confidence *= 0.6
                    signal.reasons.append(
                        f"MTF: opposite signal on another timeframe — confluence warning"
                    )
                else:
                    signal.reasons.append(
                        f"MTF: no conflicting signals across {len(mtf_signals)} timeframes"
                    )

            # ── Signal stability ───────────────────────────────────────
            if config.stability_enabled and signal.action in ("buy", "sell"):
                stab_score, is_stable = _check_signal_stability(df, config, signal.action)
                result.stability_score = stab_score
                result.stable = is_stable

                if is_stable:
                    signal.confidence = min(100, signal.confidence + config.stability_bonus)
                    signal.reasons.append(f"Signal stable ({stab_score:.0f}% consistency over {config.stability_lookback} candles) +{config.stability_bonus:.0f}")
                else:
                    signal.confidence = max(0, signal.confidence - config.stability_penalty)
                    signal.reasons.append(f"Signal unstable ({stab_score:.0f}% consistency) −{config.stability_penalty:.0f}")

            # ── Liquidity filter ───────────────────────────────────────
            spread, volume, is_liquid = _check_liquidity(kraken, pair, config)
            result.spread_pct = spread
            result.volume_24h_usd = volume
            result.liquid = is_liquid

            if not is_liquid:
                reasons = []
                if spread and spread > config.max_spread_pct:
                    reasons.append(f"Spread {spread:.3f}% > {config.max_spread_pct}% — illiquid")
                if volume is not None and volume < config.min_volume_24h_usd:
                    reasons.append(f"24h volume ${volume:,.0f} < ${config.min_volume_24h_usd:,.0f}")
                signal.reasons.extend(reasons)
                # Demote to hold if illiquid
                signal.action = "hold"

            results.append(result)

        except Exception as exc:
            logger.warning("Failed to scan %s: %s", pair, exc)
            results.append(ScanResult(
                pair=pair,
                signal=Signal(action="hold", confidence=0.0, price=None,
                              reasons=[f"Scan error: {exc}"]),
                error=str(exc),
            ))

    # Sort: actionable + liquid first by confidence, then holds, then errors
    results.sort(key=lambda r: (
        r.error is not None,
        r.signal.action == "hold",
        -(r.signal.confidence),
    ))

    return results


def get_top_picks(
    kraken: KrakenAPI,
    config: TradingConfig,
) -> list[ScanResult]:
    """Scan all pairs and return only the top actionable, filtered picks."""
    all_results = scan_pairs(kraken, config)
    actionable = [
        r for r in all_results
        if r.actionable
        and r.signal.confidence >= config.min_confidence
        and (not config.mtf_enabled or r.mtf_confluent)
        and (not config.stability_enabled or r.stable)
    ]
    return actionable[:config.top_n]


# ── Position sizing ──────────────────────────────────────────────────────

def compute_position_sizing(
    signal: Signal,
    config: TradingConfig,
    adjusted_risk_pct: float | None = None,
) -> PositionSizing | None:
    """Compute position sizing. Optionally use an adjusted risk % (streak protection)."""
    if signal.entry_price is None or signal.stop_loss is None or signal.take_profit is None:
        return None

    entry = signal.entry_price
    stop = signal.stop_loss
    tp = signal.take_profit
    rr = signal.risk_reward or 0

    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return None

    risk_pct = adjusted_risk_pct if adjusted_risk_pct is not None else config.risk_per_trade_pct
    risk_budget = config.challenge_balance * (risk_pct / 100.0)
    position_size_usd = risk_budget * entry / risk_per_unit
    potential_profit_usd = position_size_usd * (abs(tp - entry) / entry)

    return PositionSizing(
        entry_price=entry, stop_loss=stop, take_profit=tp, risk_reward=rr,
        position_size_usd=round(position_size_usd, 2),
        risk_amount_usd=round(risk_budget, 2),
        risk_pct=risk_pct,
        potential_profit_usd=round(potential_profit_usd, 2),
    )


def estimate_timeframe(signal: Signal, config: TradingConfig) -> str:
    """Estimate how long the trade might take to reach take-profit."""
    atr_val = signal.indicators.get("atr")
    if not atr_val or atr_val <= 0:
        return "unknown"
    if signal.entry_price is None or signal.take_profit is None:
        return "unknown"

    tp_distance = abs(signal.take_profit - signal.entry_price)
    candles_to_tp = tp_distance / atr_val
    total_minutes = candles_to_tp * config.candle_interval

    if total_minutes < 60:
        return f"~{int(total_minutes)} minutes"
    hours = total_minutes / 60
    if hours < 24:
        return f"~{hours:.0f} hours"
    days = hours / 24
    if days < 7:
        return f"~{days:.0f} days"
    weeks = days / 7
    return f"~{weeks:.0f} weeks"


# ── Historical price lookup for trade logging ────────────────────────────

def fetch_price_at_time(
    kraken: KrakenAPI,
    pair: str,
    timestamp_ms: int,
) -> float | None:
    """Fetch the market price of a pair at a specific timestamp.

    Uses Kraken's OHLC data with the `since` parameter to find the
    candle closest to the requested time, then returns its close price.
    Uses 1-minute candles for the best accuracy.

    Parameters
    ----------
    kraken : KrakenAPI
        Kraken API client.
    pair : str
        Trading pair, e.g. "SOL/USD".
    timestamp_ms : int
        Unix timestamp in milliseconds.

    Returns
    -------
    float | None
        Price at that time, or None if data couldn't be fetched.
    """
    try:
        # Fetch 1-minute candles starting from the timestamp
        # Kraken's `since` param returns candles AFTER the timestamp
        # so we subtract 60s to include the candle containing the timestamp
        since = max(0, timestamp_ms - 60_000)
        ohlcv = kraken.exchange.fetch_ohlcv(
            pair, timeframe="1m", since=since, limit=5,
        )
        if not ohlcv:
            return None

        # Find the candle closest to the requested timestamp
        best_candle = None
        best_diff = float("inf")
        for candle in ohlcv:
            diff = abs(candle[0] - timestamp_ms)
            if diff < best_diff:
                best_diff = diff
                best_candle = candle

        if best_candle:
            # Use close price of the closest candle
            return float(best_candle[4])  # close price
        return None
    except Exception as exc:
        logger.warning("Failed to fetch price at time for %s: %s", pair, exc)
        return None


def auto_log_trade(
    kraken: KrakenAPI,
    pair: str,
    side: str,
    amount_usd: float,
    timestamp_ms: int | None = None,
    notes: str = "",
) -> dict:
    """Log a trade with auto-fetched execution price.

    The user provides what they bought/sold and how much (in dollars).
    The scout looks up the actual market price at the trade timestamp
    from Kraken's historical data.

    Returns a dict with the trade details including the fetched price.
    """
    from datetime import datetime, timezone

    if timestamp_ms is None:
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Fetch the price at the trade time
    price = fetch_price_at_time(kraken, pair, timestamp_ms)

    if price is None:
        # Fallback: try fetching current price
        try:
            price = kraken.fetch_price(pair)
        except Exception:
            return {"error": f"Could not fetch price for {pair}"}

    # Compute the base currency volume
    volume = amount_usd / price if price > 0 else 0

    # Log to database
    try:
        import db
        trade_id = db.log_challenge_trade(
            pair=pair, side=side, amount_usd=amount_usd,
            price=price, notes=notes or f"Auto-priced from market data",
        )
        # Recompute challenge balance
        db.recompute_challenge_balance()

        # If this is a sell, check if it was a win or loss for streak tracking
        if side == "sell":
            trades = db.get_challenge_trades(limit=1000)
            trades = list(reversed(trades))  # chronological
            open_buys: dict[str, list[tuple[float, float]]] = {}
            for t in trades:
                if t.side == "buy":
                    open_buys.setdefault(t.pair, []).append((t.amount_usd, t.price))
                elif t.side == "sell":
                    buys = open_buys.get(t.pair, [])
                    if buys:
                        buy_amount, buy_price = buys.pop(0)
                        pnl = (t.price - buy_price) / buy_price * buy_amount
                        db.update_streak(is_win=(pnl > 0))

        return {
            "trade_id": trade_id,
            "pair": pair,
            "side": side,
            "amount_usd": amount_usd,
            "price": price,
            "volume": round(volume, 8),
            "timestamp": datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {"error": f"Database error: {exc}"}


# ── Survival probability + streak protection ────────────────────────────

def compute_survival_probability(
    config: TradingConfig,
    current_balance: float,
    loss_streak: int,
) -> SurvivalProbability:
    """Estimate the probability of passing the challenge.

    Uses a simplified model:
      - Each trade wins with probability P(win) = win_rate
      - A win gains risk_pct * risk_reward_ratio of starting balance
      - A loss loses risk_pct of starting balance
      - Simulate: how likely to reach +12% before hitting -5%?

    Also applies streak protection:
      - After streak_reduce_after losses: halve risk
      - After streak_pause_after losses: pause (recommend no new trades)
    """
    starting = config.challenge_balance
    target = starting * (1 + config.profit_target_pct / 100)
    max_loss_balance = starting * (1 - config.max_loss_pct / 100)

    # Streak protection
    recommendation = "trade"
    adjusted_risk_pct = config.risk_per_trade_pct

    if config.streak_protection_enabled:
        if loss_streak >= config.streak_pause_after:
            recommendation = "pause"
            adjusted_risk_pct = 0.0
        elif loss_streak >= config.streak_reduce_after:
            recommendation = "reduce_risk"
            adjusted_risk_pct = config.risk_per_trade_pct * config.streak_reduce_factor

    # Trades remaining before max loss (at adjusted risk)
    remaining_distance = current_balance - max_loss_balance
    risk_per_trade_usd = starting * (adjusted_risk_pct / 100.0) if adjusted_risk_pct > 0 else 1.0
    trades_remaining = max(0, int(remaining_distance / risk_per_trade_usd)) if risk_per_trade_usd > 0 else 999

    # Simplified pass probability using a random walk approximation
    # Expected value per trade = P(win) * gain - P(loss) * loss
    win_rate = config.survival_win_rate
    gain_per_trade = (adjusted_risk_pct / 100) * config.risk_reward_ratio  # % of starting balance
    loss_per_trade = adjusted_risk_pct / 100  # % of starting balance

    ev_per_trade = win_rate * gain_per_trade - (1 - win_rate) * loss_per_trade

    # Distance to target in % of starting balance
    distance_to_target = (target - current_balance) / starting * 100

    # Rough probability: if EV > 0 and we have enough trades, P(pass) is decent
    # This is a simplified heuristic, not a full Monte Carlo
    if recommendation == "pause":
        pass_prob = 0.0
    elif ev_per_trade <= 0:
        pass_prob = 0.15  # negative EV — low chance
    else:
        # Number of trades we can afford (constrained by max loss)
        max_affordable_trades = trades_remaining
        # Expected trades to reach target
        if ev_per_trade > 0:
            expected_trades_to_target = distance_to_target / ev_per_trade if ev_per_trade > 0 else 999
        else:
            expected_trades_to_target = 999

        if expected_trades_to_target <= max_affordable_trades * 0.5:
            pass_prob = 0.75  # plenty of room
        elif expected_trades_to_target <= max_affordable_trades * 0.75:
            pass_prob = 0.55
        elif expected_trades_to_target <= max_affordable_trades:
            pass_prob = 0.35
        else:
            pass_prob = 0.15  # unlikely to reach target before max loss

        # Penalize for losing streak
        if loss_streak >= 2:
            pass_prob *= max(0.5, 1 - loss_streak * 0.1)

        pass_prob = min(0.95, max(0.0, pass_prob))

    return SurvivalProbability(
        current_balance=current_balance,
        starting_balance=starting,
        target_balance=target,
        max_loss_balance=max_loss_balance,
        loss_streak=loss_streak,
        risk_per_trade_pct=config.risk_per_trade_pct,
        win_rate=config.survival_win_rate,
        risk_reward_ratio=config.risk_reward_ratio,
        pass_probability=pass_prob,
        recommendation=recommendation,
        adjusted_risk_pct=adjusted_risk_pct,
        trades_remaining=trades_remaining,
    )
