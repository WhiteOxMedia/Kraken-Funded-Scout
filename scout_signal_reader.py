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
    is_already_holding, get_open_position_count, max_positions_for,
    record_daily_start_balance, get_daily_start_balance, MAX_DAILY_LOSS_PCT,
    _get_db,  # noqa: F401 — used by the ghost sweep
)
from restricted_pairs import is_restricted

from zoneinfo import ZoneInfo
MST = ZoneInfo("America/Denver")  # Auto DST: MDT (UTC-6) summer, MST (UTC-7) winter


def _get_total_account_value(kraken) -> float:
    """Get total account value in USD: all cash + all crypto at market price."""
    try:
        import ccxt
        balance = kraken.exchange.fetch_balance()
        total_usd = 0.0

        # Add cash balances (USD, USDT, EUR, etc.)
        for cur in ("USD", "USDT", "EUR", "GBP"):
            val = balance.get(cur, {})
            if isinstance(val, dict):
                total_usd += float(val.get("total", 0))
            elif isinstance(val, (int, float)):
                total_usd += float(val)

        # Add crypto balances — convert each to USD via ticker
        for cur, info in balance.get("total", {}).items():
            if cur in ("USD", "USDT", "EUR", "GBP", "ZFEE"):
                continue
            amount = float(info) if not isinstance(info, dict) else float(info.get("total", 0))
            if amount <= 0:
                continue
            try:
                pair = f"{cur}/USD"
                ticker = kraken.exchange.fetch_ticker(pair)
                price = float(ticker.get("last", 0))
                total_usd += amount * price
            except Exception:
                # Try USDT pair as fallback
                try:
                    pair = f"{cur}/USDT"
                    ticker = kraken.exchange.fetch_ticker(pair)
                    price = float(ticker.get("last", 0))
                    total_usd += amount * price
                except Exception:
                    continue  # Skip unknown asset

        return total_usd
    except Exception as exc:
        logger.warning("Could not fetch total account value: %s", exc)
        return 0.0


def _check_balance_circuit_breaker(kraken) -> tuple[bool, str]:
    """Check if account has dropped more than MAX_DAILY_LOSS_PCT today.

    Returns (should_trigger, reason).
    Uses actual Kraken balance — snapshots at first check of the day,
    then compares current value against that snapshot.
    """
    current_value = _get_total_account_value(kraken)
    if current_value <= 0:
        return False, ""  # Can't check — don't trigger

    # Get or set today's starting balance
    day_start = get_daily_start_balance()
    if day_start is None:
        # First check of the day — record the starting balance
        record_daily_start_balance(current_value)
        logger.info("📊 Daily balance snapshot: $%.2f (kill switch at -%s%% = $%.2f)",
                    current_value, MAX_DAILY_LOSS_PCT, current_value * (1 - MAX_DAILY_LOSS_PCT / 100))
        return False, ""

    # Check if we've dropped more than 10%
    loss_pct = ((day_start - current_value) / day_start) * 100
    if loss_pct >= MAX_DAILY_LOSS_PCT:
        return True, (f"Account dropped {loss_pct:.1f}% today "
                      f"(start: ${day_start:.2f}, now: ${current_value:.2f}, "
                      f"loss: ${day_start - current_value:.2f})")

    logger.info("📊 Balance check: $%.2f (start $%.2f, %s%.1f%%)",
                current_value, day_start, "+" if current_value >= day_start else "", loss_pct)
    return False, ""

logger = logging.getLogger(__name__)


def _find_weakest_position(kraken, config) -> dict | None:
    """Find the weakest position to sell for rebalancing.

    Evaluates each position by:
    1. P&L percentage (worst first)
    2. Current strategy signal (sell = weakest, hold = medium, buy = strongest)

    A position showing a sell signal is the weakest regardless of PnL —
    momentum has already left. A position showing a strong buy is spared
    (it's recovering).

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
                    elif sig.action == "buy" and sig.confidence >= 50:
                        continue  # recovering — skip this position
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
        logger.info("  Weakest position: %s (%.1f%% PnL)", weakest["pair"], weakest["pnl_pct"])
    else:
        logger.info("  No positions available to rebalance from")

    return weakest


# ── RESTORED GATES (user directive, Sep 4: "restore the gates that were
# deleted and make them work properly, if they were in effect on Sept
# 3rd" — the Sep 3 17:57 rework silently dropped these; the Sep 3 trading
# record they're credited for ran WITH them): the head-to-head challenge,
# the winner-lock, the chase guard, the ghost sweep, the learned pair
# blacklist. Merged onto the current base: the POSITION LADDER v2, the
# >=70 rotation floor (user rule, Sep 3), market orders, scout_mover,
# and the scout_exit_check sell path all stand.
# ── Head-to-head challenge: a new signal can displace the weakest position ──
CHALLENGE_MARGIN = 10.0      # challenger must beat the incumbent by this many points
WINNER_PROTECT_PCT = 5.0     # positions up more than this NEVER get challenged
FRESH_BUY_COOLDOWN_MIN = 120 # positions younger than this can't be challenged
                             # (Aug 24: 30 min allowed same-hour churn — 59 fills,
                             # ~$16 fees; CRV lived 58 minutes, JUP was bought-sold-
                             # rebought inside 63 minutes)
CHASE_STALE_PCT = 2.0        # live > signal entry by this = the signal went stale
CHASE_BLOWOFF_PCT = 5.0      # live > 1h low by this = buying a blowoff spike
GHOST_GRACE_MIN = 35         # bridge record younger than this might be a buy in flight


def _sweep_ghost_bridge_records() -> list[str]:
    """Close bridge 'filled' records whose positions the bot no longer holds.

    Ground truth is the bot's own position DB: if a pair has no open bot
    position and its bridge record is older than GHOST_GRACE_MIN minutes,
    the sell happened without the bridge being notified — a ghost. Ghosts
    occupy the position slots and silently reject every
    new signal (the Aug 24 morning: three ghosts ate six 92% picks).
    Returns the list of swept pairs.
    """
    from datetime import datetime, timezone as _tz
    import db as bot_db

    open_bridge = []
    try:
        # read-only peek at the bridge's filled buys
        conn = _get_db()
        conn.row_factory = None
        rows = conn.execute(
            "SELECT signal_id, pair, executed_at FROM signals "
            "WHERE status='filled' AND action='buy'").fetchall()
        open_bridge = [(r[0], r[1], r[2]) for r in rows]
        conn.close()
    except Exception:
        return []

    if not open_bridge:
        return []

    held_bases = {p.base_currency for p in bot_db.get_open_positions()}
    now = datetime.now(_tz.utc)
    swept = []
    for signal_id, pair, executed_at in open_bridge:
        try:
            base = pair.split("/")[0] if "/" in pair else ""
            if base in held_bases:
                continue  # real position — keep
            if executed_at:
                t = datetime.fromisoformat(executed_at)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=_tz.utc)
                age_min = (now - t).total_seconds() / 60
                if age_min < GHOST_GRACE_MIN:
                    continue  # possibly a buy still being opened — leave it
            mark_closed(signal_id, 0.0)
            swept.append(f"{base}({signal_id})")
        except Exception as exc:
            logger.debug("Ghost sweep skip %s: %s", pair, exc)
    return swept


def _bridge_entry_conf(pair: str) -> float:
    """The confidence the signal carried when we BOUGHT this pair — the
    incumbent's entry conviction. Aug 24 lesson: scoring incumbents by a
    fresh scan returned hold=0% for nearly everything, so ANY challenger
    won the head-to-head (a 72% SOL displaced an 89% CRV after 58 min).
    Entry conviction is the honest strength of the incumbent's case."""
    try:
        conn = _get_db()
        conn.row_factory = None
        row = conn.execute(
            "SELECT confidence FROM signals WHERE pair=? AND action='buy' "
            "AND status='filled' ORDER BY executed_at DESC LIMIT 1",
            (pair,)).fetchone()
        conn.close()
        return float(row[0]) if row and row[0] else 50.0
    except Exception:
        return 50.0


def _chase_guard(kraken, pair: str, entry_price) -> str | None:
    """Reject scout buys whose price already ran. (1) stale signal — live is
    >2% above the signal's entry price; (2) blowoff — live is >5% above the
    last hour's low (buying exhaustion). Aug 24: breakout buys chased +4-5%
    spikes, rode the giveback, then got displaced at a loss. Returns the
    rejection reason or None."""
    try:
        live = float(kraken.fetch_price(pair))
        ep = float(entry_price or 0)
        if ep > 0 and live > ep * (1 + CHASE_STALE_PCT / 100):
            return f"chase guard: +{(live / ep - 1) * 100:.1f}% since the signal"
        ohlcv = kraken.fetch_ohlcv(pair, timeframe="15m", limit=5)
        if ohlcv and len(ohlcv) >= 4:
            base = min(c[3] for c in ohlcv[-4:])
            if base > 0 and live > base * (1 + CHASE_BLOWOFF_PCT / 100):
                return f"chase guard: +{(live / base - 1) * 100:.1f}% off the 1h base (blowoff)"
    except Exception as exc:
        logger.debug("chase guard skip %s: %s", pair, exc)
    return None


def _challenge_weakest(kraken, config, new_pair: str, new_confidence: float) -> dict | None:
    """Head-to-head: the new signal challenges the weakest held position.

    Incumbent strength = its ENTRY conviction (from the bridge record),
    adjusted by a fresh scan: a buy re-confirms, a hold is neutral, a
    bearish read weakens. The challenger must clear the weakest incumbent
    by CHALLENGE_MARGIN points. Fresh entries are exempt. Profitable
    positions are locked (never interrupt a winner) — EXCEPT when their
    own fresh read says SELL (user rule, Sep 2, the ASTER fade: the chart
    decides, not the price trail — a green-but-dead position is a zombie,
    not a winner, and contests its seat).

    Returns the position to sell (with its current read) or None.
    """
    import db as bot_db
    positions = bot_db.get_open_positions()
    if not positions:
        return None

    weakest = None
    weakest_conf = float("inf")

    for pos in positions:
        try:
            if pos.pair == new_pair:
                continue  # already holding the challenger asset

            # fresh-buy protection
            if pos.entry_timestamp:
                entry_ts = pos.entry_timestamp
                if entry_ts.tzinfo is None:
                    entry_ts = entry_ts.replace(tzinfo=timezone.utc)
                minutes_held = (datetime.now(timezone.utc) - entry_ts).total_seconds() / 60
                if minutes_held < FRESH_BUY_COOLDOWN_MIN:
                    continue

            live_price = kraken.fetch_price(pos.pair)
            pnl_pct = ((live_price - pos.entry_price) / pos.entry_price) * 100

            # fresh scan: what does the held asset say RIGHT NOW? — THE
            # DEEP READ (user rule, Sep 2: "'never interrupt a winner'
            # should now trigger a current DEEP scan"): the brain's
            # number decides the lock, not the quick read. try/finally
            # so the deep flag can never leak into other reads.
            scan_conf = 0.0
            scan_action = "hold"
            try:
                import pandas as pd
                from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
                ohlcv = kraken.fetch_ohlcv(pos.pair, timeframe="1h", limit=200)
                if ohlcv and len(ohlcv) >= 30:
                    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                    df.set_index("timestamp", inplace=True)
                    enable_deep_analysis()
                    try:
                        sig = evaluate_strategy(df, config)
                    finally:
                        disable_deep_analysis()
                    scan_action = sig.action
                    scan_conf = float(sig.confidence)
            except Exception:
                scan_conf = 0.0  # can't scan → neutral read

            # WINNER LOCK (Aug 30, user directive): profitable positions
            # are locked — challengers may only displace LOSERS. The real
            # account rides winners to its own exits (trail / TP / mature
            # sell); swap-churning profitable positions was the fee machine
            # (Aug 29: ENA sold at +$1.53 climbing, NEAR at +$0.52 — both
            # displaced, both rode nothing). EXCEPTION (user rule, Sep 2 —
            # the ASTER fade: "it's lost momentum... we don't want to hold
            # weak positions"): a winner whose OWN CHART has quit on it
            # (the fresh read says SELL) is challengeable even in profit —
            # THE CHART DECIDES, not the price trail. A winner riding a
            # pullback with a buy/hold read stays locked; a green-but-dead
            # position contests its seat and the margin decides (the
            # strength math already guts a bearish incumbent: entry_conf
            # − scan_conf). A broken/unreadable scan keeps the lock —
            # never unlock protection on missing data.
            if pnl_pct > 0 and scan_action != "sell":
                continue

            # INCUMBENT STRENGTH = entry conviction, adjusted by the fresh read:
            #   buy  -> re-confirms the thesis (take the better number)
            #   hold -> NEUTRAL (the old x0.5 punishment scored every held
            #           coin at ~0, so any challenger won — the churn bug)
            #   sell -> a bearish read genuinely weakens the hold
            entry_conf = _bridge_entry_conf(pos.pair)
            if scan_action == "buy":
                current_conf = max(entry_conf, scan_conf)
            elif scan_action == "sell":
                current_conf = entry_conf - scan_conf
            else:
                current_conf = entry_conf

            if current_conf < weakest_conf:
                weakest_conf = current_conf
                weakest = {"pair": pos.pair, "pnl_pct": pnl_pct, "position_id": pos.id,
                           "current_conf": current_conf, "current_action": scan_action}
        except Exception as exc:
            logger.debug("Challenge scan failed for %s: %s", pos.pair, exc)
            continue

    if not weakest:
        logger.info("  Challenge: no eligible positions (winners/fresh entries protected)")
        return None

    # the head-to-head verdict
    if new_confidence >= weakest["current_conf"] + CHALLENGE_MARGIN:
        logger.info("  CHALLENGE WIN: %s (%.0f%%) beats %s (strength %.0f%% %s, %.1f%% PnL)",
                    new_pair, new_confidence, weakest["pair"], weakest["current_conf"],
                    weakest["current_action"], weakest["pnl_pct"])
        return weakest

    logger.info("  CHALLENGE LOST: %s (%.0f%%) doesn't clear %s (strength %.0f%% %s) + %d-point margin",
                new_pair, new_confidence, weakest["pair"], weakest["current_conf"],
                weakest["current_action"], int(CHALLENGE_MARGIN))
    return None


def _sell_held_position(kraken, config, weak_pair: str, reason: str) -> bool:
    """Sell a held position via the bot engine (market sell, bookkeeping both DBs)."""
    try:
        import db as bot_db
        from strategy import Signal as _Sig
        from bot_engine import BotEngine as _BE

        weak_asset = weak_pair.split("/")[0]
        position = bot_db.get_position_for_asset(weak_asset)
        if not position or not position.is_open:
            logger.warning("  Challenge target %s no longer open", weak_asset)
            return False

        try:
            weak_price = kraken.fetch_price(weak_pair)
        except Exception:
            weak_price = position.entry_price

        sell_sig = _Sig(action="sell", confidence=0, price=weak_price, reasons=[reason])
        engine = _BE(kraken, config)
        sell_result = engine._execute_sell(sell_sig, reason=reason,
                                           pair=weak_pair, position=position)
        if "error" not in sell_result and "skipped" not in sell_result:
            try:
                mark_closed_by_pair(weak_pair, sell_result.get("pnl", 0))
            except Exception:
                pass
            logger.info("  Sold %s @ %s — funds freed for the challenger",
                        weak_asset, sell_result.get("price"))
            import time as _time
            _time.sleep(2)
            return True
        logger.warning("  Challenge sell failed: %s", sell_result.get("error") or sell_result.get("skipped"))
        return False
    except Exception as exc:
        logger.error("  Challenge sell error: %s", exc)
        return False


def _cooldown_fresh_exit(kraken, config, pair: str, position, engine):
    """THE COOLDOWN'S FRESH EYES (user rule, Sep 4: 'the cooldown should
    run a current exit check to make a decision to end the cooldown or
    stay the course'). Called ONLY when a scanner sell signal (either
    admitted source) lands inside the 30-minute window. Runs a CURRENT
    exit check on fresh data — the live price, the 1h knife computed
    fresh (the engine's copy is cached 5 min; the decision that ends a
    cooldown deserves the current read — the XRP 16:12 case), the
    engine's own exit stack (stop-loss / take-profit / the ATR trail),
    and a fresh brain read at the scanner's own exit-check bar (strategy
    sell >=75, >=60 momentum-rider, exhaustion on a non-buy read, the
    rider's exit signal) — and lets the fresh data decide:
      reason returned -> the cooldown ENDS, the sell executes now
      None            -> the position recovered: STAY THE COURSE
    A broken read fails cool: the cooldown stands, exactly as before."""
    try:
        import pandas as pd
        # fresh candles at the bot's own interval, seeded with the live price
        ohlcv = kraken.fetch_ohlcv(pair, interval_minutes=config.candle_interval,
                                   limit=config.lookback_candles)
        if not ohlcv or len(ohlcv) < 30:
            return None
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        live = float(kraken.fetch_price(pair) or 0) or float(df["close"].iloc[-1])
        _idx = df.index[-1]
        df.loc[_idx, "close"] = live
        df.loc[_idx, "high"] = max(float(df.loc[_idx, "high"]), live)
        df.loc[_idx, "low"] = min(float(df.loc[_idx, "low"]), live)

        # ── (1) the 1h knife, computed FRESH from the current candles ──
        try:
            if int(config.candle_interval) == 60:
                _knife_df = df
            else:
                _oh60 = kraken.fetch_ohlcv(pair, interval_minutes=60, limit=100)
                _knife_df = pd.DataFrame(_oh60, columns=["timestamp", "open", "high", "low", "close", "volume"])
                _knife_df["timestamp"] = pd.to_datetime(_knife_df["timestamp"], unit="ms")
                _knife_df.set_index("timestamp", inplace=True)
                _ki = _knife_df.index[-1]
                _knife_df.loc[_ki, "close"] = live
                _knife_df.loc[_ki, "high"] = max(float(_knife_df.loc[_ki, "high"]), live)
                _knife_df.loc[_ki, "low"] = min(float(_knife_df.loc[_ki, "low"]), live)
            if len(_knife_df) >= 30:
                from indicators import ema as _calc_ema
                _es = float(_calc_ema(_knife_df["close"], config.ema_short).iloc[-1])
                _el = float(_calc_ema(_knife_df["close"], config.ema_long).iloc[-1])
                if _es < _el and live < _el:
                    return (f"1h knife still reversed (EMA{config.ema_short} ${_es:.4g} < "
                            f"EMA{config.ema_long} ${_el:.4g}, live ${live:.4g} under both)")
        except Exception as exc:
            logger.debug("cooldown fresh knife failed for %s: %s", pair, exc)

        # ── (2) the engine's own exit stack on the live price — SL / TP /
        #       the ATR trail (its knife rides the 5-min cache; (1) already
        #       read the knife fresh) ────────────────────────────────────
        try:
            _reason = engine._check_position_exits(position, live, df)
            if _reason:
                return f"current check: {_reason}"
        except Exception as exc:
            logger.debug("cooldown engine-stack check failed for %s: %s", pair, exc)

        # ── (3) the fresh brain read at the scanner's own exit-check bar ──
        try:
            from strategy import evaluate_strategy
            sig = evaluate_strategy(df, config)
            if sig.action != "buy":
                _rider_pos = any("MOMENTUM RIDER" in str(r) for r in sig.reasons)
                _thresh = 60 if _rider_pos else 75
                if sig.action == "sell" and sig.confidence >= _thresh:
                    return f"current brain: strategy sell {sig.confidence:.0f}% (bar {_thresh}%)"
                if any("exit signal" in str(r).lower() for r in sig.reasons):
                    return "current brain: momentum rider exit triggered"
                if any("exhaustion" in str(r).lower() for r in sig.reasons):
                    return "current brain: exhaustion signals detected"
        except Exception as exc:
            logger.debug("cooldown strategy read failed for %s: %s", pair, exc)

        return None  # the fresh read says hold — stay the course
    except Exception as exc:
        logger.debug("cooldown fresh exit check failed for %s: %s", pair, exc)
        return None  # broken read — fail cool: the cooldown stands


def check_scout_signals(kraken, config) -> dict | None:
    """Check for pending scout signals and execute them.

    Call this at the top of the bot's tick loop. If a signal is executed,
    returns a result dict and the bot should skip its normal scanning.

    Returns None if no pending signals.
    """
    init_db()

    # ── GHOST SWEEP: bridge 'filled' records the bot doesn't actually hold ──
    # If the bot DB has no open position for a bridge 'filled' pair (and the
    # record is old enough not to be a buy in flight), the record is a ghost
    # from a sell that didn't notify the bridge. Ghosts squat the position
    # slots and block every new signal — this mop runs every tick so they
    # can never accumulate again. DB-to-DB reconcile; no wallet call needed.
    try:
        swept = _sweep_ghost_bridge_records()
        if swept:
            logger.info("GHOST SWEEP: closed %d stale bridge record(s): %s",
                        len(swept), ", ".join(swept))
    except Exception as exc:
        logger.debug("Ghost sweep failed: %s", exc)

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

    # ── Circuit breaker: balance-based (max 10% daily loss) ──────────
    should_trigger, cb_reason = _check_balance_circuit_breaker(kraken)
    if should_trigger:
        trigger_kill_switch(f"Balance circuit breaker: {cb_reason}")
        logger.error("CIRCUIT BREAKER: %s — trading halted", cb_reason)
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
        if action == "buy" and source in ("scout_auto", "scout_tournament", "scout_gainer", "scout_mover", "scout_dca"):
            is_dca = source == "scout_dca"

            # DCA tranches ADD to an existing position — skip the holding checks.
            # The bot engine folds the buy into the position (averages the entry).
            if is_dca:
                logger.info("Scout DCA BUY %s — adding to existing position", pair)

            # ── TIDE OUT belt-and-braces (user rule, Aug 31: all ships sink
            # together). The scanner refuses to WRITE during an OUT tide —
            # this catches anything that slips through anyway (stale pending
            # signal, scanner bug, hand-written record). UNKNOWN/blank =
            # pre-stamp or broken read → tradeable (fail-open, today's
            # behavior). Applies to DCA adds too — never average into a
            # sinking fleet.
            _sig_tide = str(signal.get("tide_state") or "")
            if _sig_tide == "OUT":
                logger.info("Scout BUY %s rejected — TIDE OUT on the stamp (%.0f%% fleet up) — all ships sink together",
                            pair, signal.get("tide_pct") or 0)
                mark_rejected(signal_id, "Tide OUT at signal time — no entries anywhere")
                continue

            # Check if we already hold this asset (in scout bridge)
            if not is_dca and is_already_holding(pair):
                logger.info("Scout BUY %s skipped — already holding (bridge)", pair)
                mark_rejected(signal_id, "Already holding this asset")
                continue

            # The bot's own position DB — imported UNCONDITIONALLY: the
            # insufficient-funds swap below (line ~767) reads bot_db on the
            # non-hold path too, and a DCA signal would NameError there
            # (CodeRabbit, Sep 8; the import was is_dca-scoped)
            import db as bot_db

            # Also check XRP bot's own position DB
            if not is_dca:
                try:
                    base = pair.split("/")[0] if "/" in pair else ""
                    existing = bot_db.get_position_for_asset(base)
                    if existing and existing.is_open:
                        logger.info("Scout BUY %s skipped — already holding (bot DB)", pair)
                        mark_rejected(signal_id, "Already holding in bot DB")
                        continue
                except Exception:
                    pass

            # ── Learned Kraken restrictions — reject BEFORE the challenge so a
            # pair the exchange refuses can never displace a healthy incumbent.
            if is_restricted(pair):
                logger.info("Scout BUY %s rejected — pair blacklisted (Kraken restriction, learned)", pair)
                mark_rejected(signal_id, f"{pair} restricted for this Kraken account (learned)")
                continue

            # ── Chase guard — never buy a signal whose price already ran ──
            # Runs BEFORE the challenge so we never sell an incumbent to make
            # room for a challenger that then gets rejected as a chase.
            if not is_dca:
                _cg = _chase_guard(kraken, pair, signal.get("entry_price"))
                if _cg:
                    logger.info("Scout BUY %s rejected — %s", pair, _cg)
                    mark_rejected(signal_id, _cg[:120])
                    continue

            # ── THE POSITION LADDER (user rule, respecified Sep 4:
            # "1k-3k = 3 assets; 3k-5k = 5; 5k-10k = 10 and so on") —
            # the CONCURRENCY CEILING scales with the TOTAL account
            # value (free + positions at market). Below $1k it's 1 —
            # concentrate on the single best pick. AT THE CAP a strong
            # challenger runs the HEAD-TO-HEAD CHALLENGE (restored Sep 4,
            # in effect Sep 3): winner-lock + entry conviction + the
            # 10-point margin decide, not a bare reject. Sizing stays
            # all-in (99% of free): later seats fill via challenge or
            # rotation. DCA doesn't open a NEW position, so it's exempt.
            if not is_dca:
                open_count = get_open_position_count()
                max_positions = max_positions_for(_get_total_account_value(kraken))
                if open_count >= max_positions:
                    confidence = signal.get("confidence", 0)
                    logger.info("Scout BUY %s — at max positions (%d/%d, ladder by account value), running the challenge",
                                pair, open_count, max_positions)
                    challenger = _challenge_weakest(kraken, config, pair, confidence)
                    if challenger and _sell_held_position(
                            kraken, config, challenger["pair"],
                            f"Challenge: displaced by stronger signal {pair.split('/')[0]} "
                            f"({confidence:.0f}% vs {challenger['current_conf']:.0f}%)"):
                        logger.info("  CHALLENGE SWAP: sold %s to make room for %s",
                                    challenger["pair"].split("/")[0], pair.split("/")[0])
                    else:
                        logger.info("Scout BUY %s skipped — challenge lost, incumbents hold", pair)
                        mark_rejected(signal_id, "Max positions — challenger lost the head-to-head")
                        continue

            # Check available balance — insufficient funds run the HEAD-TO-HEAD
            # CHALLENGE (restored Sep 4; the Sep 3 rule's >=70 floor stays):
            # a challenger at >=70% tests the weakest incumbent — winner-lock,
            # entry conviction, and the 10-point margin decide. Wins → the
            # weakest sells, the challenger buys. Loses → incumbents hold.
            # Min trade $100: fewer, bigger positions — fee drag drops in %
            # terms and winners actually move the needle (audit: winners were
            # $100-200 positions).
            try:
                available_usd = kraken.get_available_quote("USD")
                if available_usd < 100:
                    confidence = signal.get("confidence", 0)
                    logger.info("Scout BUY %s — only $%.2f available, running the challenge (confidence %.0f%%)",
                                pair, available_usd, confidence)

                    if confidence >= 70:
                        challenger = _challenge_weakest(kraken, config, pair, confidence)
                        if challenger:
                            if _sell_held_position(
                                    kraken, config, challenger["pair"],
                                    f"Challenge: sold {challenger['pair'].split('/')[0]} "
                                    f"(entry conviction {challenger['current_conf']:.0f}%, "
                                    f"{challenger['pnl_pct']:+.1f}%) for stronger signal "
                                    f"{pair.split('/')[0]} ({confidence:.0f}%)"):
                                logger.info("  CHALLENGE SWAP: %s out, %s in",
                                            challenger["pair"].split("/")[0], pair.split("/")[0])
                                # Re-check available USD after selling
                                available_usd = kraken.get_available_quote("USD")

                    if available_usd < 100:
                        logger.info("Scout BUY %s skipped — only $%.2f after the challenge", pair, available_usd)
                        mark_rejected(signal_id, f"Insufficient funds: ${available_usd:.2f} (min $100)")
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

                # If insufficient funds, sell the weakest position and retry
                if "skipped" in buy_result and "Insufficient" in buy_result.get("skipped", ""):
                    logger.info("Insufficient funds — scanning for weakest position to sell...")
                    try:
                        weakest = _find_weakest_position(kraken, config)
                        if weakest:
                            weak_pair = weakest["pair"]
                            weak_pnl = weakest["pnl_pct"]
                            weak_asset = weak_pair.split("/")[0]
                            logger.info("  Weakest: %s (%.1f%% PnL) — selling to free funds", weak_asset, weak_pnl)

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
                            logger.info("  No positions available to rebalance from")
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

        # ── Handle SELL signals ───────────────────────────────────────
        elif action == "sell" and source in ("scout_exit_check", "scout_trend_exit"):
            # TWO-LEDGER EXIT RULE (Aug 30) + THE TWO ADMITTED SOURCES
            # (restored Sep 4 — in effect Sep 3): the scanner's exit brain
            # reaches real money through exactly these two channels —
            #   scout_exit_check  — the 5-min exit check (trailing stop,
            #                      strategy sell >=75/>=60-momentum,
            #                      exhaustion, momentum-rider exits)
            #   scout_trend_exit  — the silent 1h-knife reversal (user
            #                      rule, Aug 31: "get out now")
            # Every other scout SELL source is contest-only and never
            # touches real money.
            if source == "scout_trend_exit":
                logger.info("🔁 TREND-EXIT SELL %s — the scanner's 1h knife reversed; "
                            "real money gets out now", pair)
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
                        # (Sep 4, user rule) THE COOLDOWN DECIDES WITH FRESH
                        # EYES: the blanket 30-min hold once sat on a knife —
                        # XRP 16:12:16, the scanner called the exit seconds
                        # after the fill and the cooldown rejected it; the
                        # engine knife didn't clean it up until 16:27. A sell
                        # inside the window now runs a CURRENT exit check:
                        # the fresh read still says get out -> the cooldown
                        # ENDS and this sell executes; the position recovered
                        # -> stay the course (the cooldown holds, as before).
                        from bot_engine import BotEngine as _BE_CD
                        _fresh = _cooldown_fresh_exit(
                            kraken, config, pair, position, _BE_CD(kraken, config))
                        if _fresh:
                            logger.info("Scout SELL %s — COOLDOWN ENDED by the current exit check "
                                        "(bought %.0f min ago): %s", pair, minutes_since_buy, _fresh)
                            signal["reason"] = (f"Cooldown ended at {minutes_since_buy:.0f} min — "
                                                f"{_fresh}")
                            # fall through: the sell executes below with the
                            # fresh reason on the trade ledger
                        else:
                            logger.info("Scout SELL %s skipped — cooldown HOLDS (bought %.0f min "
                                        "ago; the current exit check says stay the course)",
                                        pair, minutes_since_buy)
                            mark_rejected(signal_id,
                                          f"Cooldown holds: bought {minutes_since_buy:.0f} min ago — "
                                          f"current exit check says hold")
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
