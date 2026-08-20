"""Kraken Scout — Background Auto-Scanner (Adaptive Time-Aware Edition).

Adjusts scan frequency and sensitivity based on time of day (MST):
  - Morning rush (6-10 AM):   Scan every 5 min, high sensitivity
  - Midday      (10 AM-2 PM): Scan every 10 min, normal
  - Afternoon   (2-6 PM):     Scan every 7 min, high sensitivity
  - Evening     (6-10 PM):    Scan every 10 min, normal
  - Night       (10 PM-6 AM): Scan every 15 min, lower sensitivity

Also runs a quick ticker pre-scan between tournaments — if any asset
moved >2% since last check, immediately deep-scans that asset.

Uses parallel fetching for 3x faster tournaments.

Usage:
    Double-click "Background Scanner.bat"
    Or: python background_scanner.py
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

MST = timezone(timedelta(hours=-7))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%I:%M %p")
logger = logging.getLogger("kraken_scout")


def _now_mst() -> datetime:
    return datetime.now(MST)


def _fmt_mst(dt: datetime) -> str:
    return dt.strftime("%I:%M %p MST")


# ── Time-of-day schedule ─────────────────────────────────────────────────
TIME_BLOCKS = [
    # (start_hour, end_hour, name, scan_interval_min, min_confidence, auto_trade_threshold, description)
    # Morning = HIGH threshold (95%) — volatile, lots of false signals, only trade the best
    # Night = LOW threshold (65%) — calm market, slow movers are reliable
    (6, 10, "🌅 Morning Rush", 5, 50, 95, "High volatility — only trade signals above 95% confidence"),
    (10, 14, "☀️ Midday", 10, 50, 85, "Settling down — moderate bar at 85%"),
    (14, 18, "🌆 Afternoon", 7, 50, 80, "Reversals common — 80% threshold"),
    (18, 22, "🌙 Evening", 10, 50, 72, "Asian session — steady moves, 72% threshold"),
    (22, 6, "🌑 Night", 15, 50, 65, "Calm market — slow movers are reliable, 65% threshold"),
]


def get_time_block(hour: int) -> tuple[str, int, int, int, str]:
    """Return (name, scan_interval_min, min_confidence, auto_trade_threshold, description) for the current hour."""
    for start, end, name, interval, conf, auto_thr, desc in TIME_BLOCKS:
        if start < end:
            if start <= hour < end:
                return name, interval, conf, auto_thr, desc
        else:  # wraps midnight (e.g., 22-6)
            if hour >= start or hour < end:
                return name, interval, conf, auto_thr, desc
    return "☀️ Midday", 10, 55, 82, "Default"


# ── Parallel OHLCV fetching ─────────────────────────────────────────────

def fetch_ohlcv_parallel(exchange, pairs: list[str], timeframe: str = "1h", limit: int = 200, max_workers: int = 10) -> dict:
    """Fetch OHLCV for multiple pairs in parallel. Returns {pair: dataframe}."""
    results = {}

    def _fetch(pair):
        try:
            ohlcv = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 30:
                return pair, None
            import pandas as pd
            df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return pair, df
        except Exception as exc:
            logger.debug("Fetch failed for %s: %s", pair, exc)
            return pair, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch, p): p for p in pairs}
        for future in as_completed(futures):
            pair, df = future.result()
            if df is not None:
                results[pair] = df

    return results


# ── Quick ticker pre-scan ────────────────────────────────────────────────

def quick_ticker_scan(exchange, pairs: list[str], threshold_pct: float = 2.0) -> list[str]:
    """Fetch all tickers in one call, return pairs that moved >threshold in 24h."""
    try:
        tickers = exchange.fetch_tickers(pairs)
        movers = []
        for pair, ticker in tickers.items():
            change = ticker.get("percentage", 0)
            if change and abs(change) > threshold_pct:
                movers.append(pair)
        return movers
    except Exception:
        return []


def fetch_kraken_gainers(exchange, top_n: int = 20, min_volume_usd: float = 100_000) -> list[dict]:
    """Fetch top gainers across ALL Kraken pairs (not just the 54 challenge assets)."""
    try:
        tickers = exchange.fetch_tickers()
        gainers = []
        for symbol, t in tickers.items():
            if not symbol.endswith("/USD"):
                continue
            pct = t.get("percentage")
            if not pct or pct <= 0:
                continue
            volume = t.get("quoteVolume", 0) or 0
            if volume < min_volume_usd:
                continue
            price = t.get("last", 0) or 0
            if price <= 0:
                continue
            gainers.append({"pair": symbol, "asset": symbol.split("/")[0], "pct": pct, "price": price, "volume": volume})
        gainers.sort(key=lambda x: x["pct"], reverse=True)
        return gainers[:top_n]
    except Exception as exc:
        logger.warning("Gainers fetch failed: %s", exc)
        return []


def fetch_kraken_losers(exchange, top_n: int = 20, min_volume_usd: float = 100_000) -> list[dict]:
    """Fetch top losers across ALL Kraken pairs — today's losers are tomorrow's bounce candidates."""
    try:
        tickers = exchange.fetch_tickers()
        losers = []
        for symbol, t in tickers.items():
            if not symbol.endswith("/USD"):
                continue
            pct = t.get("percentage")
            if not pct or pct >= 0:
                continue
            volume = t.get("quoteVolume", 0) or 0
            if volume < min_volume_usd:
                continue
            price = t.get("last", 0) or 0
            if price <= 0:
                continue
            losers.append({"pair": symbol, "asset": symbol.split("/")[0], "pct": pct, "price": price, "volume": volume})
        losers.sort(key=lambda x: x["pct"])  # most negative first
        return losers[:top_n]
    except Exception as exc:
        logger.warning("Losers fetch failed: %s", exc)
        return []


# ── Bounce Hunter: losers tournament + champion vs champion ─────────────

def bounce_tournament(exchange, cfg, loser_pairs: list[str]) -> tuple[Any, list[dict]]:
    """Run a tournament on yesterday's losers to find the best bounce candidate.

    Returns (champion, match_history).
    """
    from scanner import tournament_scan, _evaluate_single, _compare_signals, ScanResult
    from strategy import Signal as _Signal
    Signal = _Signal  # make available in this scope

    if len(loser_pairs) < 2:
        return None, []

    import random
    pairs = list(loser_pairs)
    random.shuffle(pairs)

    evaluated: dict = {}
    match_history: list = []

    bracket = list(pairs)
    round_num = 1

    while len(bracket) > 1:
        next_round = []
        total_matches = len(bracket) // 2

        for i in range(0, len(bracket) - 1, 2):
            pair_a = bracket[i]
            pair_b = bracket[i + 1]

            if pair_a not in evaluated:
                try:
                    evaluated[pair_a] = _evaluate_single(exchange, pair_a, cfg)
                except Exception:
                    evaluated[pair_a] = ScanResult(pair=pair_a, signal=Signal(action="hold", confidence=0, price=None, reasons=["Error"]), error="error")

            if pair_b not in evaluated:
                try:
                    evaluated[pair_b] = _evaluate_single(exchange, pair_b, cfg)
                except Exception:
                    evaluated[pair_b] = ScanResult(pair=pair_b, signal=Signal(action="hold", confidence=0, price=None, reasons=["Error"]), error="error")

            winner, loser, reason = _compare_signals(evaluated[pair_a], evaluated[pair_b])
            next_round.append(winner.pair)

            match_history.append({
                "round": round_num, "match": i // 2 + 1, "total_matches": total_matches,
                "pair_a": pair_a, "pair_b": pair_b, "winner": winner.pair, "loser": loser.pair,
                "reason": reason,
            })

        if len(bracket) % 2 == 1:
            next_round.append(bracket[-1])

        bracket = next_round
        round_num += 1

    champion = evaluated.get(bracket[0]) if bracket else None
    return champion, match_history


def champion_vs_champion(momentum_champion, bounce_champion) -> tuple[Any, str]:
    """Head-to-head: momentum tournament winner vs bounce tournament winner.

    Returns (winner, reason).
    """
    from scanner import _compare_signals

    if not bounce_champion:
        return momentum_champion, "Bounce champion missing — momentum champion wins by default"

    if not momentum_champion:
        return bounce_champion, "Momentum champion missing — bounce champion wins by default"

    winner, loser, reason = _compare_signals(momentum_champion, bounce_champion)
    is_bounce = winner.pair == bounce_champion.pair
    label = "BOUNCE" if is_bounce else "MOMENTUM"
    return winner, f"{label} champion wins: {reason}"


# ── Main scanner loop ───────────────────────────────────────────────────

def main():
    import ccxt
    import pandas as pd
    from config import TradingConfig
    from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
    from scanner import compute_position_sizing

    logger.info("=" * 60)
    logger.info("  Kraken Scout — Adaptive Background Scanner")
    logger.info("  Time zone: Mountain Standard Time (MST)")
    logger.info("  Schedule adapts by time of day")
    logger.info("  Press Ctrl+C to stop")
    logger.info("=" * 60)

    cfg = TradingConfig()
    cfg.mtf_enabled = False
    cfg.stability_enabled = False

    exchange = ccxt.kraken({"enableRateLimit": True})
    all_pairs = list(cfg.trading_pairs)

    # ── Notification ──────────────────────────────────────────────────
    def notify_big(title: str, message: str):
        """Send desktop notification. Non-blocking, crash-safe."""
        # Method 1: Windows toast via PowerShell (most reliable on Win10+)
        try:
            import subprocess
            # Escape quotes for PowerShell
            safe_title = title.replace("'", "''").replace('"', '`"')
            safe_msg = message.replace("'", "''").replace('"', '`"')
            ps_cmd = (
                f"[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null; "
                f"$notify = New-Object System.Windows.Forms.NotifyIcon; "
                f"$notify.Icon = [System.Drawing.SystemIcons]::Information; "
                f"$notify.Visible = $true; "
                f"$notify.ShowBalloonTip(10000, '{safe_title}', '{safe_msg}', "
                f"[System.Windows.Forms.ToolTipIcon]::Info); "
                f"Start-Sleep -Seconds 10; "
                f"$notify.Dispose()"
            )
            subprocess.Popen(
                ['powershell', '-WindowStyle', 'Hidden', '-Command', ps_cmd],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.debug("Toast notification failed: %s", exc)

        # Method 2: Console log (always works)
        logger.info("🔔 ALERT: %s", title)
        for line in message.split("\n"):
            if line.strip():
                logger.info("   %s", line.strip())

    # ── Evaluate a single asset and check checklist ──────────────────
    def evaluate_and_check(pair, df, min_conf):
        cfg.min_confidence = min_conf
        signal = evaluate_strategy(df, cfg)

        if signal.action not in ("buy", "sell"):
            return None
        if signal.confidence < min_conf:
            return None
        if not signal.entry_price or not signal.stop_loss or not signal.take_profit:
            return None
        if not signal.risk_reward or signal.risk_reward < 2.0:
            return None
        if any("exhaustion" in r.lower() for r in signal.reasons):
            return None
        if any("breakout failure" in r.lower() for r in signal.reasons):
            return None

        return signal

    # ── Send alert + write signal to bridge (only ≥ 89% confidence) ──
    def send_alert(pair, signal, auto_trade_threshold=89):
        asset = pair.split("/")[0]
        sizing = compute_position_sizing(signal, cfg)
        is_buy = signal.action == "buy"

        # Only alert + write signal if confidence ≥ auto_trade_threshold (adaptive by time of day)
        if signal.confidence < auto_trade_threshold:
            logger.info("  %s %s (%.0f%%) — below %d%% threshold (%s), no alert",
                        signal.action.upper(), asset, signal.confidence, auto_trade_threshold, block_name)
            return

        # Write signal to the bridge file for the XRP bot to pick up
        signal_written = False
        try:
            from signal_bridge import write_buy_signal
            signal_written = write_buy_signal(
                pair=pair, action=signal.action,
                confidence=signal.confidence,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                risk_reward=signal.risk_reward,
                position_size_usd=sizing.position_size_usd if sizing else None,
                reasons=signal.reasons,
                source="scout_auto",
                min_confidence=auto_trade_threshold,
            )
            if signal_written:
                logger.info("📡 Signal written to bridge for XRP bot: %s %s (%.0f%%)", signal.action.upper(), asset, signal.confidence)
            else:
                logger.info("  Signal not written (duplicate or already pending)")
        except Exception as exc:
            logger.warning("Could not write to signal bridge: %s", exc)

        # Only send desktop alert if signal was actually written to the XRP bot
        if not signal_written:
            return

        # Desktop notification
        if is_buy:
            title = f"🟢🚀 BUY {asset} NOW — {signal.confidence:.0f}% CONFIDENCE 🚀🟢"
            message = (
                f"🏆 TOURNAMENT CHAMPION\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  ACTION:    BUY {asset}\n"
                f"  CONFIDENCE: {signal.confidence:.0f}%\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  ENTRY:     ${signal.entry_price:.4f}\n"
                f"  STOP:      ${signal.stop_loss:.4f}\n"
                f"  TARGET:    ${signal.take_profit:.4f}\n"
                f"  RISK:REWARD: 1:{signal.risk_reward:.1f}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  BUY AMOUNT: ${sizing.position_size_usd:.0f}\n"
                f"  RISK:       ${sizing.risk_amount_usd:.0f}\n"
                f"  PROFIT IF TP: ${sizing.potential_profit_usd:.0f}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  Time: {_fmt_mst(_now_mst())}\n"
                f"  Open Kraken Scout dashboard for full analysis"
            )
        else:
            title = f"🔴 SELL {asset} — {signal.confidence:.0f}% CONFIDENCE"
            message = (
                f"🏆 TOURNAMENT CHAMPION\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  ACTION:    SELL {asset}\n"
                f"  CONFIDENCE: {signal.confidence:.0f}%\n"
                f"  ENTRY:     ${signal.entry_price:.4f}\n"
                f"  STOP:      ${signal.stop_loss:.4f}\n"
                f"  TARGET:    ${signal.take_profit:.4f}\n"
                f"  RISK:REWARD: 1:{signal.risk_reward:.1f}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  Time: {_fmt_mst(_now_mst())}"
            )

        notify_big(title, message)
        logger.info("🚀 BIG ALERT: %s %s — %.0f%% — entry $%.4f — TP $%.4f",
                    signal.action.upper(), asset, signal.confidence, signal.entry_price, signal.take_profit)

    # ── Log winner to DB ─────────────────────────────────────────────
    def log_winner(pair, signal):
        try:
            import db
            db.init_db()
            db.log_tournament_winner(
                pair=pair, action=signal.action,
                confidence=signal.confidence, price=signal.price,
                entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                take_profit=signal.take_profit, risk_reward=signal.risk_reward,
                regime=signal.regime, reasons=[r[:200] for r in signal.reasons[:5]],
                scan_type="auto",
            )
        except Exception as exc:
            logger.warning("DB log failed: %s", exc)

    # ── Main loop ────────────────────────────────────────────────────
    last_ticker_scan = 0
    last_full_scan = 0
    last_exit_check = 0
    last_loser_fetch = ""  # date string — fetch losers once per day
    cached_loser_pairs = []
    alerted_recently = {}

    def _read_all_signals():
        """Read all signals from the SQLite bridge."""
        try:
            from signal_bridge import get_recent_signals
            return get_recent_signals(100)
        except Exception:
            return []

    while True:
        now = _now_mst()
        hour = now.hour
        block_name, full_interval, min_conf, auto_trade_threshold, block_desc = get_time_block(hour)

        # ── Ticker pre-scan (every 2 minutes) ────────────────────────
        if (now.timestamp() - last_ticker_scan) >= 120:  # 2 min
            last_ticker_scan = now.timestamp()
            logger.info("%s — Quick ticker scan...", block_name)
            try:
                movers = quick_ticker_scan(exchange, all_pairs, threshold_pct=2.0)
                if movers:
                    logger.info("  Movers (>2%%): %s", ", ".join(m.split("/")[0] for m in movers))

                    # Deep-scan movers immediately (parallel)
                    mover_data = fetch_ohlcv_parallel(exchange, movers, max_workers=5)
                    for pair, df in mover_data.items():
                        # Skip if alerted this asset in last 10 min
                        if pair in alerted_recently:
                            if (now.timestamp() - alerted_recently[pair]) < 600:
                                continue

                        signal = evaluate_and_check(pair, df, min_conf)
                        if signal:
                            send_alert(pair, signal, auto_trade_threshold)
                            log_winner(pair, signal)
                            alerted_recently[pair] = now.timestamp()
                else:
                    logger.info("  No significant movers in challenge assets")

                # ── Also scan full Kraken gainers list ───────────────
                gainers = fetch_kraken_gainers(exchange, top_n=20, min_volume_usd=100_000)
                # Filter out assets we already track or already hold
                tracked_assets = set(p.split("/")[0] for p in all_pairs)
                new_gainers = [g for g in gainers if g["asset"] not in tracked_assets]

                if new_gainers:
                    logger.info("  Top Kraken gainers (not in challenge list):")
                    for g in new_gainers[:5]:
                        logger.info("    %s +%s%% vol=$%sM", g["asset"], f"{g['pct']:.1f}", f"{g['volume']/1e6:.1f}")

                    # Deep-scan the top 3 new gainers
                    new_pairs = [g["pair"] for g in new_gainers[:3]]
                    new_data = fetch_ohlcv_parallel(exchange, new_pairs, max_workers=3)
                    for pair, df in new_data.items():
                        if pair in alerted_recently:
                            if (now.timestamp() - alerted_recently[pair]) < 600:
                                continue

                        signal = evaluate_and_check(pair, df, min_conf)
                        if signal:
                            asset = pair.split("/")[0]
                            gainer_pct = [g["pct"] for g in new_gainers if g["pair"] == pair]
                            gainer_pct = gainer_pct[0] if gainer_pct else 0

                            logger.info("  🆕 NEW GAINER SIGNAL: %s %s (%.0f%%) — +%.1f%% 24h gain",
                                        signal.action.upper(), asset, signal.confidence, gainer_pct)

                            # Write to bridge as "filled" — assume user bought it manually
                            # This makes the exit check monitor it for SELL signals
                            try:
                                from signal_bridge import write_buy_signal
                                sid = write_buy_signal(
                                    pair=pair,
                                    action=signal.action,
                                    confidence=signal.confidence,
                                    entry_price=signal.price,
                                    stop_loss=signal.stop_loss,
                                    take_profit=signal.take_profit,
                                    risk_reward=signal.risk_reward,
                                    position_size_usd=0,
                                    reasons=[f"New gainer +{gainer_pct:.1f}% 24h", *signal.reasons],
                                    source="scout_gainer",
                                    reason_summary=f"New gainer +{gainer_pct:.1f}% 24h",
                                    min_confidence=50,  # gainers get a lower bar — momentum is real
                                )
                                if sid:
                                    logger.info("  Gainer signal written as 'pending' — Trader will execute %s", asset)
                                else:
                                    logger.info("  Gainer signal not written (cooldown/duplicate/max positions)")
                            except Exception as exc:
                                logger.warning("  Could not write gainer to bridge: %s", exc)

                            # Desktop notification
                            notify_big(
                                f"NEW GAINER: {signal.action.upper()} {asset} ({signal.confidence:.0f}%)",
                                f"Trending hard — signal sent to Trader for auto-buy\n"
                                f"24h gain: +{gainer_pct:.1f}%\n"
                                f"Signal: {signal.action} ({signal.confidence:.0f}%)\n"
                                f"Price: ${signal.price:.4f}\n"
                                f"Stop: ${signal.stop_loss:.4f}\n"
                                f"TP: ${signal.take_profit:.4f}\n"
                                f"Time: {_fmt_mst(_now_mst())}\n"
                                f"Trader will buy and exit check will monitor"
                            )
                            alerted_recently[pair] = now.timestamp()
                else:
                    pass  # No new gainers or all already tracked
            except Exception as exc:
                logger.warning("Ticker scan error: %s", exc)

        # ── Exit check (every 5 minutes on open positions) ────────────
        if (now.timestamp() - last_exit_check) >= 300:  # 5 min
            last_exit_check = now.timestamp()
            try:
                from signal_bridge import get_pending_signals
                # Check for pending exit signals from the dashboard's exit check
                pending = get_pending_signals()
                pending_exits = [s for s in pending if s.get("action") == "sell" and s.get("source") == "scout_exit_check"]
                if pending_exits:
                    logger.info("📤 %d pending exit signal(s) from dashboard", len(pending_exits))

                # Also proactively check open positions (status="filled" in SQLite)
                for s in _read_all_signals():
                    if s.get("status") != "filled":
                        continue
                    if s.get("action") != "buy":
                        continue

                    pair = s.get("pair")
                    if not pair:
                        continue

                    # COOLDOWN: Don't exit-check an asset for 30 minutes after buying it
                    executed_at = s.get("executed_at", "")
                    if executed_at:
                        try:
                            from datetime import datetime as _dt
                            exec_time = _dt.fromisoformat(executed_at)
                            minutes_since_buy = (now.replace(tzinfo=None) - exec_time.replace(tzinfo=None)).total_seconds() / 60
                            if minutes_since_buy < 30:
                                logger.debug("  %s bought %.0f min ago — exit check on cooldown (30 min)", pair.split("/")[0], minutes_since_buy)
                                continue
                        except Exception:
                            pass

                    # Skip if already alerted recently
                    if pair in alerted_recently and (now.timestamp() - alerted_recently[pair]) < 300:
                        continue

                    # Fetch fresh data and run strategy
                    try:
                        import pandas as pd
                        from strategy import evaluate_strategy as _eval
                        ohlcv = exchange.fetch_ohlcv(pair, timeframe="1h", limit=200)
                        if not ohlcv or len(ohlcv) < 30:
                            continue
                        df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
                        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                        df.set_index("timestamp", inplace=True)

                        exit_sig = _eval(df, cfg)

                        should_exit = False
                        exit_reason = ""

                        # STRICTER exit rules — must be clearly reversing, not just a soft hold
                        if exit_sig.action == "sell" and exit_sig.confidence >= 75:
                            should_exit = True
                            exit_reason = f"Exit check: strategy SELL ({exit_sig.confidence:.0f}%)"
                        elif any("exhaustion" in r.lower() for r in exit_sig.reasons) and exit_sig.action != "buy":
                            should_exit = True
                            exit_reason = "Exit check: exhaustion signals detected"
                        elif any("exit signal" in r.lower() for r in exit_sig.reasons):
                            should_exit = True
                            exit_reason = "Exit check: momentum rider exit triggered"

                        if should_exit:
                            from signal_bridge import write_exit_signal
                            write_exit_signal(pair, exit_reason)
                            alerted_recently[pair] = now.timestamp()
                            logger.info("📤 Exit signal written: SELL %s — %s", pair.split("/")[0], exit_reason)

                            # Also notify
                            asset = pair.split("/")[0]
                            notify_big(
                                f"🔴 EXIT SIGNAL: SELL {asset}",
                                f"Trend reversed on {asset}\n"
                                f"Reason: {exit_reason}\n"
                                f"Time: {_fmt_mst(_now_mst())}\n"
                                f"Signal written to bridge for XRP bot to execute"
                            )
                    except Exception as exc:
                        logger.debug("Exit check failed for %s: %s", pair, exc)

            except Exception as exc:
                logger.warning("Exit check error: %s", exc)

        # ── Full tournament ──────────────────────────────────────────
        if (now.timestamp() - last_full_scan) >= full_interval * 60:
            last_full_scan = now.timestamp()
            scan_start = _now_mst()
            logger.info("%s — Full tournament (%d assets, min_conf=%d)...", block_name, len(all_pairs), min_conf)
            logger.info("  %s", block_desc)

            try:
                # Parallel fetch all assets
                all_data = fetch_ohlcv_parallel(exchange, all_pairs, max_workers=10)
                logger.info("  Fetched %d/%d assets", len(all_data), len(all_pairs))

                # Evaluate all in parallel
                disable_deep_analysis()
                results = {}
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {executor.submit(evaluate_strategy, df, cfg): pair for pair, df in all_data.items()}
                    for future in as_completed(futures):
                        pair = futures[future]
                        try:
                            results[pair] = future.result()
                        except Exception:
                            pass

                # Find best signal
                best_pair = None
                best_signal = None
                best_conf = 0
                for pair, sig in results.items():
                    if sig.action not in ("buy", "sell"):
                        continue
                    if sig.confidence < min_conf:
                        continue
                    if sig.confidence > best_conf:
                        best_conf = sig.confidence
                        best_pair = pair
                        best_signal = sig

                if best_pair and best_signal:
                    asset = best_pair.split("/")[0]
                    logger.info("🏆 Momentum Champion: %s %s (%.0f%%)", best_signal.action, asset, best_signal.confidence)

                    # ── Bounce Hunter: losers tournament + champion vs champion ─
                    today_str = _now_mst().strftime("%Y-%m-%d")
                    if last_loser_fetch != today_str:
                        # Fetch today's top losers (once per day)
                        losers = fetch_kraken_losers(exchange, top_n=20, min_volume_usd=100_000)
                        cached_loser_pairs = [l["pair"] for l in losers if l["pair"] not in all_pairs]
                        last_loser_fetch = today_str
                        if cached_loser_pairs:
                            logger.info("  📉 Today's top losers (bounce candidates): %s",
                                        ", ".join(l["asset"] + f"({l['pct']:.0f}%)" for l in losers[:5]))

                    # Run bounce tournament on yesterday's losers
                    bounce_champion = None
                    if len(cached_loser_pairs) >= 2:
                        try:
                            logger.info("  🔄 Running bounce tournament on %d losers...", len(cached_loser_pairs))
                            bounce_champion, bounce_history = bounce_tournament(exchange, cfg, cached_loser_pairs)
                            if bounce_champion and bounce_champion.signal.action in ("buy", "sell"):
                                logger.info("  🔄 Bounce Champion: %s %s (%.0f%%)",
                                            bounce_champion.signal.action,
                                            bounce_champion.pair.split("/")[0],
                                            bounce_champion.signal.confidence)
                            else:
                                logger.info("  Bounce champion did not produce a signal")
                                bounce_champion = None
                        except Exception as exc:
                            logger.warning("  Bounce tournament failed: %s", exc)

                    # Champion vs Champion: momentum winner vs bounce winner
                    if bounce_champion and bounce_champion.signal.action in ("buy", "sell"):
                        momentum_champion = best_signal  # from the regular tournament
                        # Create a ScanResult-like object for the momentum champion
                        from scanner import ScanResult
                        mom_result = ScanResult(pair=best_pair, signal=momentum_champion)

                        overall_winner, cvc_reason = champion_vs_champion(mom_result, bounce_champion)
                        winner_sig = overall_winner.signal
                        winner_pair = overall_winner.pair
                        winner_asset = winner_pair.split("/")[0]
                        is_bounce = overall_winner.pair == bounce_champion.pair

                        logger.info("  ⚔️ Champion vs Champion: %s wins — %s",
                                    "BOUNCE " + winner_asset if is_bounce else "MOMENTUM " + winner_asset,
                                    cvc_reason)

                        # Only alert if winner ≥ 89% confidence
                        if winner_sig.confidence >= 89:
                            # Check checklist
                            passes = (
                                winner_sig.entry_price and winner_sig.stop_loss
                                and winner_sig.take_profit and winner_sig.risk_reward
                                and winner_sig.risk_reward >= 2.0
                                and not any("exhaustion" in r.lower() for r in winner_sig.reasons)
                                and not any("breakout failure" in r.lower() for r in winner_sig.reasons)
                            )

                            if passes:
                                if winner_pair in alerted_recently and (now.timestamp() - alerted_recently[winner_pair]) < 600:
                                    logger.info("  Already alerted %s recently — skipping", winner_asset)
                                else:
                                    tag = "🔄 BOUNCE" if is_bounce else "🚀 MOMENTUM"
                                    logger.info("  🏆 OVERALL CHAMPION: %s %s %s (%.0f%%) — ALERTING",
                                                tag, winner_sig.action.upper(), winner_asset, winner_sig.confidence)
                                    send_alert(winner_pair, winner_sig, auto_trade_threshold)
                                    log_winner(winner_pair, winner_sig)
                                    alerted_recently[winner_pair] = now.timestamp()
                            else:
                                logger.info("  Overall champion did not pass checklist")
                        else:
                            logger.info("  Overall champion (%.0f%%) below 89%% threshold — no alert", winner_sig.confidence)
                    else:
                        # No bounce champion — just use momentum champion as before
                        # Check checklist
                        passes = (
                            best_signal.entry_price and best_signal.stop_loss
                            and best_signal.take_profit and best_signal.risk_reward
                            and best_signal.risk_reward >= 2.0
                            and not any("exhaustion" in r.lower() for r in best_signal.reasons)
                            and not any("breakout failure" in r.lower() for r in best_signal.reasons)
                        )

                        if passes:
                            if best_pair in alerted_recently and (now.timestamp() - alerted_recently[best_pair]) < 600:
                                logger.info("  Already alerted %s recently — skipping", asset)
                            else:
                                send_alert(best_pair, best_signal, auto_trade_threshold)
                                log_winner(best_pair, best_signal)
                                alerted_recently[best_pair] = now.timestamp()
                        else:
                            logger.info("  Champion did not pass checklist")
                else:
                    logger.info("  No qualifying signals (min_conf=%d)", min_conf)

                elapsed = (_now_mst() - scan_start).total_seconds()
                logger.info("  Tournament done in %.0fs", elapsed)

            except Exception as exc:
                logger.error("Tournament failed: %s", exc)

        # ── Sleep until next check (30 seconds) ──────────────────────
        next_block_name, next_interval, _, _, _ = get_time_block(_now_mst().hour)
        logger.info("  Next check in 30s | %s | full scan every %dm", next_block_name, next_interval)
        time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
