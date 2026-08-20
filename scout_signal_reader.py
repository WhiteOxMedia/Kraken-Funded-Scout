"""Scout signal reader for the Kraken XRP Trading Bot.

This module reads trade signals from the Kraken Scout's signal bridge file
and executes them on the regular Kraken spot account.

Add this to the XRP bot's tick loop:

    from scout_signal_reader import check_scout_signals
    check_scout_signals(kraken, config)

Place the import and call at the TOP of bot_engine.run_tick(), before the
existing scan logic. This way scout signals are checked first, and the
bot's own scanning only runs if there are no pending scout signals.

Installation:
    Copy this file to: C:\\Users\\birds\\Workspace\\kraken_xrp_trading_bot\\
    Copy signal_bridge.py to: C:\\Users\\birds\\Workspace\\kraken_xrp_trading_bot\\

    Then in bot_engine.py, add at the top of run_tick():
        from scout_signal_reader import check_scout_signals
        scout_result = check_scout_signals(self.kraken, self.config)
        if scout_result:
            result["action_taken"] = scout_result.get("action", "none")
            result["signals"].append(scout_result.get("summary", ""))
            return result  # skip normal scanning this tick
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Add the scout project to the path so we can import the signal bridge
SCOUT_PATH = Path(r"C:\Users\birds\Workspace\kraken_scout")
if str(SCOUT_PATH) not in sys.path:
    sys.path.insert(0, str(SCOUT_PATH))

from signal_bridge import (
    init_db, get_pending_signals, acknowledge, mark_executed, mark_rejected,
    mark_closed, mark_closed_by_pair, write_exit_signal,
    is_kill_switch_active, get_kill_switch_reason, trigger_kill_switch,
    get_daily_pnl, get_consecutive_losses, is_already_holding,
    get_open_position_count, MAX_CONCURRENT_POSITIONS,
)

MST = timezone(timedelta(hours=-7))

logger = logging.getLogger(__name__)


def _find_weakest_position(kraken, config) -> dict | None:
    """Find the weakest LOSING position to sell for rebalancing.

    Only considers positions that are currently at a LOSS (pnl_pct < 0).
    Profitable positions are never sold for rebalancing.

    Evaluates each losing position by:
    1. P&L percentage (worst loser first)
    2. Current strategy signal (sell/hold = weaker than buy)

    Returns {"pair": str, "pnl_pct": float, "position_id": int} or None.
    """
    import db as bot_db
    positions = bot_db.get_open_positions()
    if not positions:
        return None

    weakest = None
    worst_score = float("inf")

    for pos in positions:
        try:
            live_price = kraken.fetch_price(pos.pair)
            pnl_pct = ((live_price - pos.entry_price) / pos.entry_price) * 100

            # ONLY consider losing positions — never sell a winner for rebalancing
            if pnl_pct >= 0:
                continue

            # Run strategy to check if trend is dying
            signal_penalty = 0
            try:
                import pandas as pd
                from strategy import evaluate_strategy
                ohlcv = kraken.fetch_ohlcv(pos.pair, timeframe="1h", limit=200)
                if ohlcv and len(ohlcv) >= 30:
                    df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                    df.set_index("timestamp", inplace=True)
                    sig = evaluate_strategy(df, config)
                    if sig.action == "sell":
                        signal_penalty = -5.0  # trend already reversed
                    elif sig.action == "hold" and sig.confidence < 30:
                        signal_penalty = -2.0  # momentum fading
            except Exception:
                pass  # if strategy fails, just use P&L

            # Score: P&L + signal penalty (lower = weaker)
            score = pnl_pct + signal_penalty

            if score < worst_score:
                worst_score = score
                weakest = {"pair": pos.pair, "pnl_pct": pnl_pct, "position_id": pos.id}

        except Exception as exc:
            logger.debug("Could not evaluate %s: %s", pos.pair, exc)
            continue

    if weakest:
        logger.info("  Weakest loser: %s (%.1f%% P&L)", weakest["pair"], weakest["pnl_pct"])
    else:
        logger.info("  No losing positions to rebalance from")

    return weakest


def check_scout_signals(kraken, config) -> dict | None:
    """Check for pending scout signals and execute them.

    Call this at the top of the bot's tick loop. If a signal is executed,
    returns a result dict and the bot should skip its normal scanning.

    Returns None if no pending signals.
    """
    init_db()
    pending = get_pending_signals()
    if not pending:
        return None

    # ── Kill switch check ────────────────────────────────────────────
    if is_kill_switch_active():
        reason = get_kill_switch_reason()
        logger.warning("🛑 Kill switch active: %s — no new trades", reason)
        # Still process sell signals (exits) but not buys
        pending = [s for s in pending if s.get("action") == "sell"]
        if not pending:
            return None

    # ── Circuit breaker check ────────────────────────────────────────
    daily_pnl = get_daily_pnl()
    consec_losses = get_consecutive_losses()
    if daily_pnl < -30 or consec_losses >= 5:  # 3% of $1000 = $30
        trigger_kill_switch(f"Circuit breaker: daily P&L=${daily_pnl:.2f}, consecutive losses={consec_losses}")
        logger.error("🛑 Circuit breaker triggered: daily P&L=$%.2f, %d consecutive losses", daily_pnl, consec_losses)
        # Still allow exits
        pending = [s for s in pending if s.get("action") == "sell"]
        if not pending:
            return None

    now = datetime.now(MST)
    result = {"action": "none", "summary": ""}

    for signal in pending:
        signal_id = signal.get("signal_id")
        pair = signal.get("pair")
        action = signal.get("action")
        source = signal.get("source", "scout")

        # ── Handle BUY signals ────────────────────────────────────────
        if action == "buy" and source in ("scout_auto", "scout_tournament"):
            # Check if we already hold this asset (in scout bridge)
            if is_already_holding(pair):
                logger.info("Scout BUY %s skipped — already holding (bridge)", pair)
                mark_rejected(signal_id, "Already holding this asset")
                continue

            # Also check XRP bot's own position DB
            try:
                import db as bot_db
                base = pair.split("/")[0] if "/" in pair else ""
                existing = bot_db.get_position_for_asset(base)
                if existing and existing.is_open:
                    logger.info("Scout BUY %s skipped — already holding (bot DB)", pair)
                    mark_rejected(signal_id, "Already holding in bot DB")
                    continue
            except Exception:
                pass

            # Check max concurrent positions — don't spread too thin
            open_count = get_open_position_count()
            if open_count >= MAX_CONCURRENT_POSITIONS:
                logger.info("Scout BUY %s skipped — %d open positions (max %d)", pair, open_count, MAX_CONCURRENT_POSITIONS)
                mark_rejected(signal_id, f"Max positions reached ({open_count}/{MAX_CONCURRENT_POSITIONS})")
                continue

            # Check available balance — don't open trades under $50
            try:
                available_usd = kraken.get_available_quote("USD")
                if available_usd < 50:
                    logger.info("Scout BUY %s skipped — only $%.2f available (min $50)", pair, available_usd)
                    mark_rejected(signal_id, f"Insufficient funds: ${available_usd:.2f} (min $50)")
                    continue
            except Exception:
                pass

            # Acknowledge the signal
            acknowledge(signal_id)

            # Execute the buy
            try:
                from strategy import Signal
                scout_signal = Signal(
                    action="buy",
                    confidence=signal.get("confidence", 0),
                    price=signal.get("entry_price"),
                    reasons=signal.get("reasons", []) if isinstance(signal.get("reasons"), list) else [signal.get("reason", "")],
                )

                # Use the bot engine's buy execution
                # We need to create a temporary BotEngine or call directly
                from bot_engine import BotEngine
                engine = BotEngine(kraken, config)

                # Temporarily set the pair
                old_pair = config.pair
                config.pair = pair

                buy_result = engine._execute_buy(scout_signal, pair=pair)

                # If insufficient funds, sell the weakest LOSING position and retry
                if "skipped" in buy_result and "Insufficient" in buy_result.get("skipped", ""):
                    logger.info("💰 Insufficient funds — scanning for weakest LOSING position to sell...")
                    try:
                        weakest = _find_weakest_position(kraken, config)
                        if weakest:
                            weak_pair = weakest["pair"]
                            weak_pnl = weakest["pnl_pct"]
                            weak_asset = weak_pair.split("/")[0]
                            logger.info("  Weakest loser: %s (%.1f%% P&L) — selling to free funds", weak_asset, weak_pnl)

                            # Verify the position still exists and is open
                            weak_position = bot_db.get_position_for_asset(weak_asset)
                            if not weak_position or not weak_position.is_open:
                                logger.warning("  Position %s no longer open — skipping", weak_asset)
                            else:
                                # Fetch live price for the sell signal
                                try:
                                    weak_live_price = kraken.fetch_price(weak_pair)
                                except Exception:
                                    weak_live_price = weak_position.entry_price

                                sell_signal = Signal(
                                    action="sell", confidence=0,
                                    price=weak_live_price,
                                    reasons=[f"Rebalance: sold to fund stronger signal {pair.split('/')[0]}"],
                                )

                                try:
                                    sell_result = engine._execute_sell(
                                        sell_signal,
                                        reason=f"Rebalance: sold {weak_asset} ({weak_pnl:+.1f}%) to fund {pair.split('/')[0]}",
                                        pair=weak_pair, position=weak_position,
                                    )

                                    if "error" not in sell_result and "skipped" not in sell_result:
                                        logger.info("  Sold %s @ %s — funds freed", weak_asset, sell_result.get("price"))
                                        mark_closed_by_pair(weak_pair, sell_result.get("pnl", 0))

                                        # Wait for funds to settle
                                        import time
                                        time.sleep(2)

                                        # Retry the buy with freed funds
                                        buy_result = engine._execute_buy(scout_signal, pair=pair)
                                    else:
                                        logger.warning("  Sell failed: %s", sell_result.get("error") or sell_result.get("skipped"))
                                except Exception as exc:
                                    logger.error("  Sell execution error: %s", exc)
                        else:
                            logger.info("  No losing positions available to rebalance from")
                    except Exception as exc:
                        logger.error("  Rebalance scan failed: %s", exc)

                config.pair = old_pair

                if "error" not in buy_result and "skipped" not in buy_result:
                    fill_price = buy_result.get("price", signal.get("entry_price", 0))
                    order_id = buy_result.get("order_id", "")
                    mark_executed(signal_id, order_id, fill_price)

                    result["action"] = "buy"
                    result["summary"] = f"Scout signal executed: BUY {pair} @ {fill_price}"
                    logger.info("🚀 Scout BUY executed: %s @ %s (signal %s)", pair, fill_price, signal_id)
                    return result
                else:
                    fail_reason = buy_result.get("error") or buy_result.get("skipped") or "Unknown"
                    logger.warning("Scout BUY failed: %s", fail_reason)
                    mark_rejected(signal_id, fail_reason)
                    continue

            except Exception as exc:
                logger.error("Scout buy execution failed: %s", exc)
                continue

        # ── Handle SELL signals (from exit check) ─────────────────────
        elif action == "sell" and source == "scout_exit_check":
            try:
                import db as bot_db
                base = pair.split("/")[0] if "/" in pair else ""
                position = bot_db.get_position_for_asset(base)

                # If no position in bot DB, check actual Kraken balance
                # (user may have bought manually — e.g. gainer signals)
                if not position or not position.is_open:
                    try:
                        actual_balance = kraken.get_available_base(base)
                    except Exception:
                        actual_balance = 0

                    if actual_balance and actual_balance > 0:
                        # User holds this asset on Kraken but it's not in the bot's DB
                        # Create a fake position for the sell
                        logger.info("Scout SELL %s — not in bot DB but Kraken balance=%s, selling directly", pair, actual_balance)

                        acknowledge(signal_id)
                        from strategy import Signal
                        from bot_engine import BotEngine
                        engine = BotEngine(kraken, config)

                        live_price = kraken.fetch_price(pair)
                        scout_signal = Signal(
                            action="sell", confidence=0,
                            price=live_price,
                            reasons=[signal.get("reason", "Scout exit signal")],
                        )

                        # Sell directly using the bot's market sell
                        try:
                            volume = round(actual_balance, 6)
                            order = kraken.place_market_sell(pair, volume, validate=config.paper_mode)
                            fill_price = float(order.get("average") or order.get("price") or live_price)
                            order_id = order.get("id", "")

                            # Log the trade in bot's DB
                            trade_id = bot_db.log_trade(
                                pair=pair, side="sell", ordertype="market",
                                volume=volume, price=fill_price,
                                cost=volume * fill_price, fee=0,
                                status="filled", kraken_txid=order_id,
                                paper_mode=config.paper_mode,
                                reason=signal.get("reason", "Scout exit signal"),
                                confidence=0,
                            )

                            # Mark in bridge
                            mark_executed(signal_id, order_id, fill_price)
                            mark_closed(signal_id, 0)  # P&L unknown for manual buys

                            result["action"] = "sell"
                            result["summary"] = f"Scout exit: SELL {pair} @ {fill_price} (manual position)"
                            logger.info("📤 Scout SELL executed: %s @ %s (manual position, signal %s)", pair, fill_price, signal_id)
                            return result
                        except Exception as exc:
                            logger.error("Direct sell failed for %s: %s", pair, exc)
                            mark_rejected(signal_id, f"Sell failed: {exc}")
                            continue
                    else:
                        logger.info("Scout SELL %s skipped — no open position and no Kraken balance", pair)
                        mark_rejected(signal_id, "No position or balance")
                        continue

                # Normal sell — position exists in bot DB
                # COOLDOWN: Don't sell within 30 minutes of buying
                if position.entry_timestamp:
                    entry_ts = position.entry_timestamp
                    if entry_ts.tzinfo is None:
                        entry_ts = entry_ts.replace(tzinfo=timezone.utc)
                    minutes_since_buy = (datetime.now(timezone.utc) - entry_ts).total_seconds() / 60
                    if minutes_since_buy < 30:
                        logger.info("Scout SELL %s skipped — bought %.0f min ago (30 min cooldown)", pair, minutes_since_buy)
                        mark_rejected(signal_id, f"Cooldown: bought {minutes_since_buy:.0f} min ago")
                        continue

                # Acknowledge and execute
                acknowledge(signal_id)

                from strategy import Signal
                from bot_engine import BotEngine
                engine = BotEngine(kraken, config)

                scout_signal = Signal(
                    action="sell",
                    confidence=0,
                    price=kraken.fetch_price(pair),
                    reasons=[signal.get("reason", "Scout exit signal")],
                )

                sell_result = engine._execute_sell(
                    scout_signal,
                    reason=signal.get("reason", "Scout exit signal"),
                    pair=pair,
                    position=position,
                )

                if "error" not in sell_result:
                    pnl = sell_result.get("pnl", 0)
                    mark_executed(signal_id, sell_result.get("order_id", ""), sell_result.get("price", 0))
                    mark_closed(signal_id, pnl)
                    result["action"] = "sell"
                    result["summary"] = f"Scout exit executed: SELL {pair} (PnL: {pnl:+.2f})"
                    logger.info("📤 Scout SELL executed: %s (PnL: %.2f, signal %s)", pair, pnl, signal_id)
                    return result
                else:
                    fail_reason = sell_result.get("error", "Unknown")
                    logger.warning("Scout SELL failed: %s", fail_reason)
                    mark_rejected(signal_id, fail_reason)
                    continue

            except Exception as exc:
                logger.error("Scout sell execution failed: %s", exc)
                continue

    return None
