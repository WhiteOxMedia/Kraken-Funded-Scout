"""Backtesting module — test strategy against historical data.

Downloads historical OHLCV candles from Kraken and runs the strategy
against them, simulating trades with the same logic as the live bot.
Returns performance metrics: win rate, total PnL, max drawdown, etc.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from config import TradingConfig
from indicators import compute_all_indicators
from strategy import evaluate_strategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Results of a backtest run."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_pct: float = 0.0
    total_pnl_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_profit_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


def run_backtest(
    df: pd.DataFrame,
    config: TradingConfig,
    initial_balance: float = 100.0,
) -> BacktestResult:
    """Run a backtest on historical data.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns: open, high, low, close, volume.
    config : TradingConfig
        Trading configuration.
    initial_balance : float
        Starting USD balance for the simulation.

    Returns
    -------
    BacktestResult
        Performance metrics and trade history.
    """
    result = BacktestResult()
    balance = initial_balance
    in_position = False
    entry_price = 0.0
    entry_volume = 0.0
    highest_price = 0.0
    peak_balance = initial_balance

    # Need at least 50 candles for indicators
    warmup = 50

    for i in range(warmup, len(df)):
        window = df.iloc[:i + 1]

        # Evaluate strategy on the window
        signal = evaluate_strategy(window, config)
        current_price = float(window["close"].iloc[-1])

        if not in_position and signal.action == "buy" and signal.confidence >= config.min_confidence:
            # Buy
            spend = balance * (config.trade_pct / 100.0)
            entry_price = current_price
            entry_volume = spend / current_price
            balance -= spend
            in_position = True
            highest_price = current_price
            result.trades.append({
                "type": "buy", "price": current_price, "volume": entry_volume,
                "index": i, "balance": balance + entry_volume * current_price,
            })

        elif in_position:
            # Update highest price for trailing stop
            if current_price > highest_price:
                highest_price = current_price

            # Check exits
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            should_sell = False
            reason = ""

            # Stop-loss
            if pnl_pct <= -config.stop_loss_pct:
                should_sell = True
                reason = "stop-loss"

            # Take-profit
            elif pnl_pct >= config.take_profit_pct:
                should_sell = True
                reason = "take-profit"

            # Trailing stop
            elif config.trailing_stop_enabled and highest_price > entry_price:
                trail_pct = ((current_price - highest_price) / highest_price) * 100
                if trail_pct <= -config.trailing_stop_pct:
                    should_sell = True
                    reason = "trailing-stop"

            # Strategy sell signal (with min profit)
            elif signal.action == "sell" and pnl_pct >= config.min_profit_to_sell_pct:
                should_sell = True
                reason = "strategy-sell"

            if should_sell:
                sell_revenue = entry_volume * current_price
                balance += sell_revenue
                trade_pnl = sell_revenue - (entry_volume * entry_price)
                trade_pnl_pct = pnl_pct

                result.total_trades += 1
                result.total_pnl_usd += trade_pnl
                result.total_pnl_pct += trade_pnl_pct

                if trade_pnl > 0:
                    result.wins += 1
                else:
                    result.losses += 1

                result.best_trade_pct = max(result.best_trade_pct, trade_pnl_pct)
                result.worst_trade_pct = min(result.worst_trade_pct, trade_pnl_pct)

                result.trades.append({
                    "type": "sell", "price": current_price, "volume": entry_volume,
                    "pnl": trade_pnl, "pnl_pct": trade_pnl_pct, "reason": reason,
                    "index": i, "balance": balance,
                })

                in_position = False
                entry_price = 0.0
                entry_volume = 0.0
                highest_price = 0.0

        # Track equity curve and drawdown
        equity = balance + (entry_volume * current_price if in_position else 0)
        result.equity_curve.append(equity)
        if equity > peak_balance:
            peak_balance = equity
        drawdown = ((equity - peak_balance) / peak_balance) * 100
        if drawdown < result.max_drawdown_pct:
            result.max_drawdown_pct = drawdown

    # Close any open position at the end
    if in_position:
        final_price = float(df["close"].iloc[-1])
        sell_revenue = entry_volume * final_price
        balance += sell_revenue
        trade_pnl = sell_revenue - (entry_volume * entry_price)
        result.total_trades += 1
        result.total_pnl_usd += trade_pnl
        result.total_pnl_pct += ((final_price - entry_price) / entry_price) * 100
        if trade_pnl > 0:
            result.wins += 1
        else:
            result.losses += 1

    # Calculate averages
    if result.total_trades > 0:
        result.avg_profit_pct = result.total_pnl_pct / result.total_trades

    return result


def format_backtest_report(result: BacktestResult, initial: float = 100.0) -> str:
    """Format a backtest result as a readable report."""
    win_rate = (result.wins / result.total_trades * 100) if result.total_trades > 0 else 0
    final_balance = initial + result.total_pnl_usd

    lines = [
        "=== BACKTEST RESULTS ===",
        f"Initial balance:  ${initial:.2f}",
        f"Final balance:    ${final_balance:.2f}",
        f"Total PnL:        ${result.total_pnl_usd:+.2f} ({result.total_pnl_pct:+.1f}%)",
        f"Total trades:     {result.total_trades}",
        f"Wins:             {result.wins}",
        f"Losses:           {result.losses}",
        f"Win rate:         {win_rate:.1f}%",
        f"Avg profit/trade: {result.avg_profit_pct:+.2f}%",
        f"Best trade:       {result.best_trade_pct:+.2f}%",
        f"Worst trade:      {result.worst_trade_pct:+.2f}%",
        f"Max drawdown:     {result.max_drawdown_pct:.1f}%",
    ]
    return "\n".join(lines)
