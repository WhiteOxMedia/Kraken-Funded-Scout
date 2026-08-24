"""Kraken Scout — Background Auto-Scanner (Data-Driven Rotation Edition).

4-hour rotations (America/Denver, DST-aware), thresholds calibrated by the
Volatility Engine (rotation_thresholds.json) from 12 weeks of real data:
  - R1 00-04 🌏 Asia:          scan every 15 min
  - R2 04-08 🌅 Pre-US:        scan every 15 min
  - R3 08-12 ☀️ EU/US Overlap: scan every 7 min (peak liquidity)
  - R4 12-16 🌤 US Afternoon:  scan every 10 min
  - R5 16-20 🌆 US Late:       scan every 10 min
  - 20-24  🌙 Cooldown:        NO buys (flatten at 20:00, exit checks only)

Every buy signal must clear BOTH gates:
  1. Confidence bar (quality filter — data-calibrated per rotation/asset)
  2. Expected-move gate (size filter — projected move ≥ Avg Move + 0.5σ)

Also runs a quick ticker pre-scan between tournaments — if any asset
moved >2% since last check, immediately deep-scans that asset.

Usage:
    Double-click "Launch Kraken.bat"
    Or: python background_scanner.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from zoneinfo import ZoneInfo
MST = ZoneInfo("America/Denver")  # Auto DST: MDT (UTC-6) summer, MST (UTC-7) winter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%I:%M %p")
logger = logging.getLogger("kraken_scout")


def _now_mst() -> datetime:
    return datetime.now(MST)


def _fmt_mst(dt: datetime) -> str:
    return dt.strftime("%I:%M %p MST")


# ── Rotation schedule (4h blocks, Denver local, DST-aware) ────────────────
# R1 00-04 Asia | R2 04-08 Pre-US | R3 08-12 EU/US Overlap | R4 12-16 US Afternoon
# R5 16-20 US Late | 20-24 Cooldown — NO TRADING (flatten at 20:00, no buys until 00:00)
#
# Confidence bars are DATA-CALIBRATED by the Volatility Engine
# (volatility_engine.py → rotation_thresholds.json): 60 + 35 × false-breakout
# rate, computed per rotation from 12 weeks of 1h candles, split weekday /
# weekend. The fallback bars below only apply when that file is missing.
ROTATIONS = [
    # (start_hour, end_hour, key, name, scan_interval_min, min_confidence, fallback_bar, description)
    (0, 4,   "00-04", "R1 🌏 Asia",          15, 50, 65, "Asia session — low liquidity, slow movers are reliable"),
    (4, 8,   "04-08", "R2 🌅 Pre-US",        15, 50, 80, "Asia close / Europe open — building volume"),
    (8, 12,  "08-12", "R3 ☀️ EU/US Overlap",  7, 50, 90, "Peak liquidity — EU/US overlap, best moves"),
    (12, 16, "12-16", "R4 🌤 US Afternoon",  10, 50, 82, "US afternoon — reversals common"),
    (16, 20, "16-20", "R5 🌆 US Late",       10, 50, 76, "US late session — steady moves"),
    (20, 24, "20-24", "🌙 Cooldown",         30, 50, 99, "NO TRADING — cooldown/analysis window (exit checks only)"),
]
ROTATION_KEYS = [r[2] for r in ROTATIONS]
COOLDOWN_KEY = "20-24"


def rotation_key_for_hour(hour: int) -> str:
    """4h rotation key for a Denver-local hour (00-04 ... 20-24)."""
    return ROTATION_KEYS[min(hour // 4, 5)]


def _schedule_for_dow(dow: int) -> str:
    """Volatility Engine schedule: weekday (Mon-Fri) or weekend (Sat-Sun)."""
    return "weekend" if dow in (5, 6) else "weekday"


# ── Day-of-week adjustment (FALLBACK ONLY) ────────────────────────────────
# Used only when rotation_thresholds.json is missing/stale — the Volatility
# Engine's weekday/weekend schedules replace this when data is available.
# Crypto markets have documented day-of-week patterns:
#   Tue-Thu = peak volume, most institutional flow, signals more reliable
#   Sat-Sun = low volume, retail-driven, weekend pumps that reverse
WEEKDAY_ADJUST = {
    0: -5,   # Monday — slightly loose (positioning)
    1: -15,  # Tuesday — peak volume (loose)
    2: -15,  # Wednesday — peak volume (loose)
    3: -15,  # Thursday — peak volume (loose)
    4: -10,  # Friday — moderate (unwinding before weekend)
    5: +5,   # Saturday — low volume (slightly tight)
    6: 0,    # Sunday — neutral (recovering)
}

WEEKDAY_NAMES = {
    0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun",
}


def get_time_block(hour: int, dow: int = -1) -> tuple[str, int, int, int, str, bool, str]:
    """Return (name, scan_interval_min, min_confidence, auto_trade_threshold,
    description, no_buy, rotation_key) for the current hour.

    The auto_trade_threshold comes from the Volatility Engine's calibrated
    data (rotation_thresholds.json, weekday/weekend schedules) when
    available; otherwise falls back to the hardcoded bars + WEEKDAY_ADJUST.
    no_buy=True during the 20:00-24:00 cooldown window.
    """
    for start, end, key, name, interval, conf, fallback_bar, desc in ROTATIONS:
        if start <= hour < end:
            no_buy = key == COOLDOWN_KEY
            bar = fallback_bar
            if dow >= 0:
                data_bar = None
                try:
                    from volatility_engine import get_confidence_bar
                    data_bar = get_confidence_bar(None, key, _schedule_for_dow(dow))
                except Exception:
                    data_bar = None
                if data_bar is not None:
                    bar = data_bar  # data-calibrated (universe median for this rotation)
                else:
                    bar = _apply_dow(fallback_bar, dow)
            return name, interval, conf, bar, desc, no_buy, key
    # 0-24 is fully covered above — this only fires on bad input
    return "R3 ☀️ EU/US Overlap", 7, 55, _apply_dow(90, dow), "Default", False, "08-12"


def _apply_dow(threshold: int, dow: int) -> int:
    """Apply day-of-week adjustment to a threshold. Clamped to 50-99."""
    if dow < 0 or dow > 6:
        return threshold
    adjusted = threshold + WEEKDAY_ADJUST[dow]
    return max(50, min(99, adjusted))


def is_weekend(dow: int) -> bool:
    """Return True if Saturday or Sunday."""
    return dow in (5, 6)


# ── Liquidity filter ─────────────────────────────────────────────────────

def filter_illiquid(data: dict, min_volume_usd: float) -> tuple[dict, list]:
    """Drop assets below the 24h USD volume floor.

    Low-volume meme coins whipsaw and the spread eats small positions —
    the audit showed them as pure churn. Volume column is in base units,
    so quote volume = sum(volume x close) over the last 24 hourly candles.
    Returns (kept_data, excluded_list_of_(pair, volume)).
    """
    kept = {}
    excluded = []
    for pair, df in data.items():
        try:
            recent = df.tail(24)
            quote_vol = float((recent["volume"] * recent["close"]).sum())
            if quote_vol >= min_volume_usd:
                kept[pair] = df
            else:
                excluded.append((pair, quote_vol))
        except Exception:
            kept[pair] = df  # can't compute — keep rather than drop
    return kept, excluded


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

# ── THE CONTEST LIGHT (server-side, ALWAYS ON — works with the browser
#    closed): every-minute Zira voice on contest positions. ALL GOOD with
#    dollars / GET OUT NOW on exits / scalp alerts. The console visual
#    popups are separate; this is the voice that never sleeps.
CONTEST_WATCH_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contest_watch.json")
CONSOLE_STATE_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console_state.json")
CONTEST_TRAIL_PCT = 4.0   # matches the console's contest profile
CONTEST_SELL_BAR = 50
VOICE_PREFIX = "Mic check one, two. Testing, testing. "
_scalp_hist = {}
_scalp_alerted = {}
_last_trade_done = 0.0


def _speak_server(text: str, rate: int = 2):
    """Zira out loud from the SCANNER process — runs whether or not any
    browser is open. Same amplified speak script the console uses."""
    import subprocess
    try:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speak_loud.ps1")
        if not os.path.exists(script):
            return
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", script, "-Text", VOICE_PREFIX + text, "-Rate", str(rate)],
                         creationflags=0x08000000)
    except Exception as exc:
        logger.debug("speak failed: %s", exc)


def _load_contest_watch() -> dict:
    import json
    try:
        with open(CONTEST_WATCH_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_contest_watch(watch: dict):
    import json
    try:
        tmp = CONTEST_WATCH_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(watch, fh, indent=2)
        os.replace(tmp, CONTEST_WATCH_JSON)
    except Exception as exc:
        logger.warning("contest watch save failed: %s", exc)


def _voice_enabled() -> bool:
    """Console toggle (console_state.json voice_on) — shared file."""
    import json
    try:
        with open(CONSOLE_STATE_JSON, "r", encoding="utf-8") as fh:
            return bool(json.load(fh).get("voice_on", True))
    except Exception:
        return True


def contest_light(exchange, cfg):
    """One light cycle on every contest position: 4% profit guard + 50% sell
    bar + scalp watch. Speaks the dollars every minute. Exited positions are
    removed from the watch (Kraken's dashboard owns the history)."""
    import pandas as pd
    from strategy import evaluate_strategy

    global _last_trade_done
    watch = _load_contest_watch()
    if not watch:
        return
    voice = _voice_enabled()

    exited_pairs = []
    voice_updates = []
    dirty = False
    for pair, w in list(watch.items()):
        asset = pair.split("/")[0]
        try:
            live = float(exchange.fetch_ticker(pair)["last"])
        except Exception:
            continue
        entry = float(w.get("entry") or 0)
        size = float(w.get("size") or 2500.0)
        if not entry:
            continue

        # track high in the watch file (survives restarts)
        old_high = float(w.get("high") or 0)
        high = max(old_high, live)
        if high > old_high:
            w["high"] = high
            dirty = True

        # ── 1. profit guard: 4% off the peak ──────────────────────────
        should_exit = False
        reason = ""
        if high > entry and live <= high * (1 - CONTEST_TRAIL_PCT / 100):
            should_exit = True
            reason = f"PROFIT GUARD FIRED — dropped {(high - live) / high * 100:.1f}% from the peak"

        # ── 2. strategy sell bar (contest profile: 50%) ────────────────
        if not should_exit:
            try:
                ohlcv = exchange.fetch_ohlcv(pair, timeframe="1h", limit=200)
                if ohlcv and len(ohlcv) >= 30:
                    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                    df.set_index("timestamp", inplace=True)
                    sig = evaluate_strategy(df, cfg)
                    if sig.action == "sell" and sig.confidence >= CONTEST_SELL_BAR:
                        should_exit = True
                        reason = f"Trend broke — {sig.confidence:.0f}% bearish, past the {CONTEST_SELL_BAR}% bar"
                    elif any("exhaustion" in r.lower() for r in sig.reasons) and sig.action != "buy":
                        should_exit = True
                        reason = "Climax candle — this move looks done"
            except Exception:
                pass

        if should_exit:
            exited_pairs.append(pair)
            if voice:
                _speak_server(f"Get out now! Sell {asset}! Get out now!", rate=1)
            logger.info("🚨 CONTEST LIGHT: SELL %s — %s", pair, reason)
            continue

        # ── 3. scalp alert: +$10 AND +0.5% in ~5 min ───────────────────
        pct = (live / entry - 1) * 100
        usd = pct / 100.0 * size
        ts = time.time()
        h = _scalp_hist.setdefault(pair, [])
        h.append((ts, live))
        _scalp_hist[pair] = [(t, p) for t, p in h if ts - t <= 360][-12:]
        if pair in _scalp_alerted and usd < 7.0:
            _scalp_alerted.pop(pair)
        ref5 = next((p for t, p in _scalp_hist[pair] if ts - t >= 240), None)
        rise = (live / ref5 - 1) * 100 if ref5 else 0.0
        if voice and usd >= 10.0 and rise >= 0.5 and pair not in _scalp_alerted:
            _scalp_alerted[pair] = True
            _speak_server(f"Scalp alert! {asset} is up {usd:.0f} dollars and rising fast!")
            logger.info("💰 CONTEST SCALP: %s up $%.0f (+%.1f%% in 5 min)", pair, usd, rise)

        sign = "plus" if usd >= 0 else "minus"
        voice_updates.append(f"{asset}, {sign} {abs(usd):.0f} dollars")

    if exited_pairs:
        for p in exited_pairs:
            watch.pop(p, None)
        _save_contest_watch(watch)
        if not watch and time.time() - _last_trade_done > 300:
            _last_trade_done = time.time()
            if voice:
                _speak_server("Trade done! Let's find the next one!")
        return

    if dirty:
        _save_contest_watch(watch)
    if voice and voice_updates:
        _speak_server("All good. " + ". ".join(voice_updates) + ".")


def main():
    import ccxt
    import pandas as pd
    from config import TradingConfig
    from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
    from scanner import compute_position_sizing

    logger.info("=" * 60)
    logger.info("  Kraken Scout — Rotation Scanner (data-driven)")
    logger.info("  Time zone: Mountain Time (MST/MDT, auto DST)")
    logger.info("  R1 00-04 Asia | R2 04-08 Pre-US | R3 08-12 EU/US")
    logger.info("  R4 12-16 US PM | R5 16-20 US Late | 20-24 cooldown")
    logger.info("  Gates: calibrated confidence bar + expected-move floor")
    logger.info("  Press Ctrl+C to stop")
    logger.info("=" * 60)

    cfg = TradingConfig()
    cfg.mtf_enabled = False
    cfg.stability_enabled = False

    exchange = ccxt.kraken({"enableRateLimit": True})
    all_pairs = list(cfg.trading_pairs)

    # ── Notification ──────────────────────────────────────────────────
    def notify_big(title: str, message: str):
        """Desktop alert that STAYS until you read it — plus Zira says it out
        loud. The old balloon toast died in ~5 seconds; this popup holds the
        screen up to 3 minutes (or until you click OK)."""
        import subprocess
        safe_title = title.replace("'", "''").replace('"', '`"')
        safe_msg = message.replace("'", "''").replace('"', '`"')

        # 1) Zira announces it (amplified — heard across the room)
        try:
            if _voice_enabled():
                spoken = "".join(ch for ch in title if 32 <= ord(ch) < 127).strip()
                spoken = spoken.replace("%", " percent")
                if spoken:
                    _speak_server(spoken)
        except Exception:
            pass

        # 2) Persistent popup — stays up to 3 minutes or until clicked
        try:
            ps = (
                f"$w = New-Object -ComObject WScript.Shell; "
                f"$null = $w.Popup('{safe_msg}', 180, '{safe_title}', 48)"
            )
            subprocess.Popen(['powershell', '-WindowStyle', 'Hidden', '-Command', ps],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            logger.debug("Popup notification failed: %s", exc)

        # 3) Console log (always works)
        logger.info("🔔 ALERT: %s", title)
        for line in message.split("\n"):
            if line.strip():
                logger.info("   %s", line.strip())

    # ── Evaluate a single asset and check checklist ──────────────────
    def evaluate_and_check(pair, df, min_conf):
        cfg.min_confidence = min_conf
        signal = evaluate_strategy(df, cfg)

        # LONGS ONLY — we never short; sell signals never become alerts.
        if signal.action != "buy":
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

    # ── Send alert + write signal to bridge ───────────────────────────
    def send_alert(pair, signal, auto_trade_threshold=89, move_level="standard"):
        asset = pair.split("/")[0]
        sizing = compute_position_sizing(signal, cfg)
        is_buy = signal.action == "buy"

        # ── LONG-ONLY mandate: the tournament hunts bull runs. Short signals
        # never alert and never reach the bridge — we never short an asset.
        if signal.action != "buy":
            logger.info("  %s %s signal suppressed — longs only (never short)", asset, signal.action.upper())
            return

        # Rotation context for the data-driven gates
        gate_now = _now_mst()
        gate_rot = rotation_key_for_hour(gate_now.hour)
        gate_sched = _schedule_for_dow(gate_now.weekday())

        # ── SEPARATED GATES: the DESKTOP alert (contest) uses the original
        # criteria — champion ≥ threshold + checklist, both checked upstream.
        # The BRIDGE write (bot's money) keeps the full new stack: move floor
        # + per-asset confidence bar. A gate failure = contest-only alert with
        # the reason spelled out; the bot never sees a gated pick.
        gate_notes = []

        # ── Gate 1: EXPECTED MOVE (bot's size filter) ────────────────────
        try:
            from volatility_engine import get_move_threshold
            move_thr = get_move_threshold(pair, gate_rot, gate_sched, level=move_level)
            if move_thr is not None and signal.entry_price and signal.take_profit:
                projected = (signal.take_profit - signal.entry_price) / signal.entry_price * 100
                if projected < move_thr:
                    gate_notes.append(f"move's too small for this hour")
                    logger.info("  MOVE GATE: %s %s projected %.2f%% < %.2f%% noise floor (%s %s) — contest-only",
                                signal.action.upper(), asset, projected, move_thr, gate_sched, gate_rot)
        except Exception as exc:
            logger.debug("Move gate check failed: %s", exc)

        # ── Gate 2: CONFIDENCE BAR (bot's quality filter) ────────────────
        effective_thr = auto_trade_threshold
        try:
            from volatility_engine import get_confidence_bar
            asset_bar = get_confidence_bar(pair, gate_rot, gate_sched)
            if asset_bar is not None:
                effective_thr = asset_bar
        except Exception:
            pass

        if signal.confidence < effective_thr:
            gate_notes.append(f"{signal.confidence:.0f}% sure — bot needs {effective_thr}% for this coin")
            logger.info("  %s %s (%.0f%%) — below %d%% bar (%s %s) — contest-only",
                        signal.action.upper(), asset, signal.confidence, effective_thr, block_name, gate_rot)

        # ── Liquidity advisory (execution_tiers): blacklisted markets get no
        # signal at all; healthy ones get the tier/health/slip line annotated.
        # Enforcement stays bot-side — this snapshot is advisory + noise filter.
        liq_line = ""
        try:
            from execution_tiers import liquidity_snapshot
            snap = liquidity_snapshot(exchange, pair, sizing.position_size_usd if sizing else 400.0)
            if snap.get("blacklisted"):
                logger.info("  LIQUIDITY: %s blacklisted (%s) — no signal written",
                            asset, snap.get("blacklist_reason"))
                return
            if snap.get("ok"):
                _tn, _tname, _tcons = snap["tier"]
                _slip_s = f"{snap['slippage_pct']:.2f}%" if snap.get("slippage_pct") is not None else "—"
                liq_line = f"EASY TO SELL: {snap['score']}/100"
                logger.info("  %s", liq_line)
        except Exception as exc:
            logger.debug("Liquidity snapshot failed for %s: %s", pair, exc)

        # Write signal to the bridge file for the XRP bot to pick up —
        # ONLY if the bot's gates passed. Gated picks stay contest-only.
        # min_confidence=0 because we already checked above — bridge still checks:
        # kill switch, cooldown, duplicates, position limits, R:R, exhaustion, breakout failure
        signal_written = False
        if not gate_notes:
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
                    min_confidence=0,  # Already gated in send_alert — skip redundant check
                )
                if signal_written:
                    logger.info("📡 Signal written to bridge for XRP bot: %s %s (%.0f%%)", signal.action.upper(), asset, signal.confidence)
                else:
                    logger.info("  Signal not written (duplicate or already pending)")
            except Exception as exc:
                logger.warning("Could not write to signal bridge: %s", exc)
        else:
            logger.info("  Bot gates declined %s — desktop alert fires as CONTEST-ONLY", asset)

        # ── Desktop alert policy ─────────────────────────────────────────
        # The 89% confidence floor: only champion-grade signals get your
        # screen + Zira. Below 89% the pick still logs to the winners pool
        # (log_winner runs after send_alert returns) and the bot may still
        # take it via the bridge — but the desktop stays quiet. This was
        # the original guard rail from the winning $251 run.
        DESKTOP_ALERT_MIN = 89
        if signal.confidence < DESKTOP_ALERT_MIN:
            logger.info("  Desktop suppressed: %s %.0f%% < %d%% floor (still logged to winners pool)",
                        asset, signal.confidence, DESKTOP_ALERT_MIN)
            return

        # The signal already PASSED the contest criteria (champion + checklist,
        # checked upstream). Why the bot didn't take it gets spelled out in
        # plain English so the user can judge the call themselves.
        bot_note = ""
        if gate_notes:
            bot_note = "BOT'S OUT: " + " / ".join(gate_notes) + "\n"
        elif not signal_written:
            bot_note = "BOT'S OUT: bot's full or already holds it.\n"

        # Desktop notification
        liq_msg = f"  {liq_line}\n" if liq_line else ""
        # Contest chunk (25/50/75/100% — the contest app only takes those):
        # snap the strategy size UP, reading the console's tier setting.
        contest_msg = ""
        try:
            import json as _json
            from pathlib import Path as _P
            _cs = _json.loads((_P(__file__).resolve().parent / "console_state.json").read_text())
            _bal = float(str(_cs.get("tier", "$5,000")).replace("$", "").replace(",", ""))
        except Exception:
            _bal = 5000.0
        if _bal > 0 and sizing and sizing.position_size_usd > 0:
            _frac = sizing.position_size_usd / _bal
            _chunk = next((c for c in (0.25, 0.50, 0.75, 1.00) if _frac <= c + 1e-9), 1.00)
            contest_msg = (f"  SPEND: {_chunk:.0%} = ${_chunk * _bal:,.0f}\n")
        # how long, from the engine's own pace data
        hold_txt = "a few hours"
        try:
            from volatility_engine import load_rotation_thresholds, ROTATIONS
            doc = load_rotation_thresholds(max_age_s=300)
            if doc:
                n = _now_mst()
                rot = next((k for s, e, k, *_ in ROTATIONS if s <= n.hour < e), None)
                sched = "weekend" if n.weekday() >= 5 else "weekday"
                cell = (doc.get("universe", {}).get(pair, {}).get(sched, {}).get(rot)
                        or doc.get("median", {}).get(sched, {}).get(rot))
                avg = float(cell.get("avg_move") or 0) if cell else 0
                if avg > 0 and signal.entry_price and signal.take_profit:
                    _dist = (signal.take_profit - signal.entry_price) / signal.entry_price * 100
                    _hours = (_dist / avg) * 4.0
                    if _hours < 1:
                        hold_txt = "under an hour"
                    elif _hours < 2:
                        hold_txt = "about an hour"
                    elif _hours < 36:
                        hold_txt = f"~{_hours:.0f} hours"
                    else:
                        hold_txt = f"~{_hours / 24:.0f} days"
        except Exception:
            pass
        if is_buy:
            title = f"🟢 BUY {asset} — {signal.confidence:.0f}% SURE"
            message = (
                f"BUY {asset}\n"
                f"SPEND: {_chunk:.0%} = ${_chunk * _bal:,.0f}\n"
                f"FOR: {hold_txt}\n"
                f"HOW SURE: {signal.confidence:.0f}%\n"
                f"{'-' * 30}\n"
                f"get out at ${signal.stop_loss:.4f} (wrong)\n"
                f"cash out at ${signal.take_profit:.4f} (win)\n"
                f"{'-' * 30}\n"
                f"{bot_note}"
                f"  {_fmt_mst(_now_mst())}"
            )
        else:
            title = f"🔴 SELL {asset} — {signal.confidence:.0f}% SURE"
            message = (
                f"SELL {asset} — {signal.confidence:.0f}% sure\n"
                f"{'-' * 30}\n"
                f"  {_fmt_mst(_now_mst())}"
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
    last_contest_light = 0
    last_loser_fetch = ""  # date string — fetch losers once per day
    cached_loser_pairs = []
    alerted_recently = {}
    current_block_name = None  # track time block changes for position reset

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
        dow = now.weekday()  # 0=Mon, 6=Sun
        block_name, full_interval, min_conf, auto_trade_threshold, block_desc, no_buy, rot_key = get_time_block(hour, dow)

        # ── Time block rotation: sell everything and reset ────────────
        # Skip rotation reset on weekends — low volume, let positions ride
        if current_block_name is not None and block_name != current_block_name:
            if is_weekend(dow):
                logger.info("  Weekend — skipping rotation reset (low volume, holding positions)")
            else:
                logger.info("=" * 60)
                logger.info("  TIME BLOCK ROTATION: %s -> %s", current_block_name, block_name)
                logger.info("  Resetting positions for fresh market session")
                logger.info("=" * 60)

                # 1. Find WINNERS to keep — positions up >5% ride through the
                #    rotation; the trailing stop handles their exit.
                keep_pairs = []
                try:
                    from signal_bridge import get_open_positions
                    open_positions = get_open_positions()
                    for pos in open_positions:
                        pair = pos.get("pair")
                        entry = pos.get("execution_price") or pos.get("entry_price")
                        if not pair or not entry:
                            continue
                        try:
                            live = exchange.fetch_ticker(pair)
                            price = float(live.get("last", 0))
                            if price > entry * 1.05:
                                gain = (price - entry) / entry * 100
                                keep_pairs.append(pair)
                                logger.info("  🏆 WINNER KEPT: %s up +%.1f%% — riding through rotation",
                                            pair.split("/")[0], gain)
                        except Exception:
                            continue
                except Exception as exc:
                    logger.warning("  Winner scan failed: %s", exc)

                # 2. Run reset_positions.py from the XRP bot (sells everything
                #    on Kraken except kept winners)
                try:
                    import subprocess
                    bot_dir = r"C:\Users\birds\Workspace\kraken_xrp_trading_bot"
                    bot_python = os.path.join(bot_dir, ".venv", "Scripts", "python.exe")
                    if not os.path.exists(bot_python):
                        bot_python = "python"  # fallback to system python
                    cmd = [bot_python, "reset_positions.py"]
                    if keep_pairs:
                        cmd.append("--keep")
                        cmd.append(",".join(keep_pairs))
                    result = subprocess.run(
                        cmd,
                        cwd=bot_dir,
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().split("\n"):
                            if line.strip():
                                logger.info("  reset: %s", line.strip())
                    else:
                        logger.error("  reset_positions.py failed: %s", result.stderr.strip()[:200])
                except Exception as exc:
                    logger.error("  Could not run reset_positions.py: %s", exc)

                # 3. Clear the signal bridge DB (winners stay open)
                try:
                    from signal_bridge import reset_all_positions
                    counts = reset_all_positions(keep_pairs=keep_pairs)
                    logger.info("  Bridge reset: closed %d, cancelled %d pending, kept %d winners",
                                counts["closed"], counts["cancelled"], counts["kept"])
                except Exception as exc:
                    logger.warning("  Bridge reset failed: %s", exc)

                # 4. Clear alert history and force immediate tournament
                alerted_recently = {}
                last_full_scan = 0
                last_exit_check = 0

                # NO desktop popup for rotations — Windows alerts are
                # BUY-NOW calls only (user rule). Rotation details stay
                # in the scanner log + the console NOW strip.
                logger.info("Market rotation %s -> %s | losers sold | kept winners: %s",
                            current_block_name, block_name,
                            ", ".join(p.split("/")[0] for p in keep_pairs) or "none")

        current_block_name = block_name

        # ── Ticker pre-scan (every 2 minutes, trading rotations only) ──
        # During the 20:00-24:00 cooldown there are no buys — exit checks below
        # keep running so trailing stops still protect kept winners.
        if not no_buy and (now.timestamp() - last_ticker_scan) >= 120:  # 2 min
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

                # ── Bounce fast path: yesterday's losers, scanned every tick ──
                # The gainer path is retired (chasing pumps = bad fills). Instead:
                # the daily bounce list (yesterday's top losers) gets deep-scanned
                # here at ticker speed, so a bounce setup fires immediately
                # instead of waiting for the next full tournament.
                if cached_loser_pairs:
                    for pair in cached_loser_pairs:
                        if pair in alerted_recently:
                            if (now.timestamp() - alerted_recently[pair]) < 600:
                                continue
                        try:
                            ohlcv = exchange.fetch_ohlcv(pair, timeframe="1h", limit=200)
                            if not ohlcv or len(ohlcv) < 30:
                                continue
                            df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
                            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                            df.set_index("timestamp", inplace=True)

                            signal = evaluate_and_check(pair, df, min_conf)
                            if not signal:
                                continue
                            asset = pair.split("/")[0]

                            # bounce move gate: aggressive bar (avg move) —
                            # a bounce off yesterday's low needs room to travel
                            try:
                                from volatility_engine import get_move_threshold
                                gm_thr = get_move_threshold(pair, rot_key, _schedule_for_dow(dow), level="aggressive")
                                if gm_thr is not None and signal.entry_price and signal.take_profit:
                                    gm_proj = (signal.take_profit - signal.entry_price) / signal.entry_price * 100
                                    if gm_proj < gm_thr:
                                        continue
                            except Exception:
                                pass

                            logger.info("  🔄 BOUNCE SIGNAL: %s %s (%.0f%%) — yesterday's loser turning",
                                        signal.action.upper(), asset, signal.confidence)

                            # persist to the volatility universe so the engine
                            # learns this coin's stats for future thresholds
                            try:
                                from volatility_engine import remember_gainer
                                remember_gainer(pair, -5.0)  # was a loser, now bouncing
                            except Exception:
                                pass

                            # write to the bridge — bot may take it
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
                                    reasons=[f"Bounce: yesterday's loser turning", *signal.reasons],
                                    source="scout_gainer",
                                    reason_summary="Bounce: yesterday's loser turning",
                                    min_confidence=50,
                                )
                                if sid:
                                    logger.info("  Bounce signal written — Trader will evaluate %s", asset)
                            except Exception as exc:
                                logger.warning("  Could not write bounce to bridge: %s", exc)

                            # desktop: 89% floor, same as everything else
                            if signal.confidence >= 89:
                                notify_big(
                                    f"🔄 BOUNCE {asset} ({signal.confidence:.0f}%)",
                                    f"Yesterday's loser turning — bounce play\n"
                                    f"Price: ${signal.price:.4f}\n"
                                    f"Stop: ${signal.stop_loss:.4f}\n"
                                    f"TP: ${signal.take_profit:.4f}\n"
                                    f"Time: {_fmt_mst(_now_mst())}"
                                )
                            else:
                                logger.info("  Bounce %s %.0f%% < 89%% floor — logged only", asset, signal.confidence)
                            log_winner(pair, signal)
                            alerted_recently[pair] = now.timestamp()
                        except Exception as exc:
                            logger.debug("Bounce scan failed for %s: %s", pair, exc)
                else:
                    pass  # no bounce list yet today
            except Exception as exc:
                logger.warning("Ticker scan error: %s", exc)

        # ── CONTEST LIGHT (every 60s): server-side Zira voice on contest
        #    positions — ALL GOOD with dollars / GET OUT NOW / scalp
        #    alerts. Runs whether or not the browser is open.
        if (now.timestamp() - last_contest_light) >= 60:
            last_contest_light = now.timestamp()
            try:
                contest_light(exchange, cfg)
            except Exception as exc:
                logger.debug("contest light error: %s", exc)

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

                        # ── DCA tranche check: dipped but trend intact — add to position ──
                        # Mimics the XRP bot's DCA: a pullback in an uptrend is an
                        # entry, not an exit. Max 1 DCA per position, min 2.5% dip,
                        # strategy must still say buy.
                        try:
                            entry = s.get("execution_price") or s.get("entry_price")
                            live = float(df["close"].iloc[-1])
                            if entry and live:
                                dip_pct = (entry - live) / entry * 100
                                from signal_bridge import count_dca_signals, write_buy_signal
                                if (not no_buy and dip_pct >= 2.5 and exit_sig.action == "buy"
                                        and exit_sig.confidence >= 50
                                        and count_dca_signals(pair) == 0
                                        and exit_sig.entry_price and exit_sig.stop_loss
                                        and exit_sig.take_profit):
                                    dca_sid = write_buy_signal(
                                        pair=pair,
                                        action="buy",
                                        confidence=exit_sig.confidence,
                                        entry_price=exit_sig.entry_price,
                                        stop_loss=exit_sig.stop_loss,
                                        take_profit=exit_sig.take_profit,
                                        risk_reward=exit_sig.risk_reward,
                                        position_size_usd=0,
                                        reasons=[f"DCA tranche: dipped {dip_pct:.1f}% from entry, trend intact", *exit_sig.reasons],
                                        source="scout_dca",
                                        reason_summary=f"DCA tranche: dipped {dip_pct:.1f}%, strategy still buy",
                                        min_confidence=50,
                                        is_dca=True,
                                    )
                                    if dca_sid:
                                        alerted_recently[pair] = now.timestamp()
                                        logger.info("  ⤵️ DCA tranche signal: %s dipped %.1f%% from $%.4f, "
                                                    "strategy still buy (%.0f%%) — adding to position",
                                                    pair.split("/")[0], dip_pct, entry, exit_sig.confidence)
                                        notify_big(
                                            f"DCA TRANCHE: BUY MORE {pair.split('/')[0]}",
                                            f"Position dipped {dip_pct:.1f}% but trend intact\n"
                                            f"Strategy: BUY ({exit_sig.confidence:.0f}%)\n"
                                            f"Adding a tranche to lower the entry\n"
                                            f"Time: {_fmt_mst(_now_mst())}"
                                        )
                                        continue  # skip exit logic this round — we just added
                        except Exception as exc:
                            logger.debug("DCA check failed for %s: %s", pair, exc)

                        should_exit = False
                        exit_reason = ""

                        # ── Trailing stop check (runs FIRST — protects winners) ──
                        live_price = float(df["close"].iloc[-1])
                        try:
                            from signal_bridge import update_trailing_high, check_trailing_stop
                            high = update_trailing_high(pair, live_price)
                            trail_hit, _ = check_trailing_stop(pair, live_price)
                            if trail_hit:
                                drop_pct = (high - live_price) / high * 100
                                should_exit = True
                                exit_reason = (f"Trailing stop: ${live_price:.4f} is {drop_pct:.1f}% "
                                               f"below high ${high:.4f}")
                                logger.info("  🎯 TRAILING STOP hit: %s high=$%.4f now=$%.4f (-%.1f%%)",
                                            pair.split("/")[0], high, live_price, drop_pct)
                        except Exception as exc:
                            logger.debug("Trailing stop check failed for %s: %s", pair, exc)

                        # Momentum rider positions get a LOWER exit bar (60%)
                        # because they're momentum plays — any momentum loss is a sell
                        is_momentum_pos = any("MOMENTUM RIDER" in r for r in exit_sig.reasons)

                        sell_threshold = 60 if is_momentum_pos else 75

                        if not should_exit:
                            if exit_sig.action == "sell" and exit_sig.confidence >= sell_threshold:
                                should_exit = True
                                exit_reason = f"Exit check: strategy SELL ({exit_sig.confidence:.0f}%, threshold {sell_threshold}%)"
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

                            # NO desktop popup: Windows alerts are BUY-NOW
                            # calls only (user rule — never a sell popup).
                            # The bot executes the exit automatically; the
                            # scanner log keeps the full record.
                    except Exception as exc:
                        logger.debug("Exit check failed for %s: %s", pair, exc)

            except Exception as exc:
                logger.warning("Exit check error: %s", exc)

        # ── Full tournament (trading rotations only — cooldown has no buys) ──
        if not no_buy and (now.timestamp() - last_full_scan) >= full_interval * 60:
            last_full_scan = now.timestamp()
            scan_start = _now_mst()
            logger.info("%s — Full tournament (%d assets, min_conf=%d)...", block_name, len(all_pairs), min_conf)
            logger.info("  %s", block_desc)

            try:
                # Parallel fetch all assets
                all_data = fetch_ohlcv_parallel(exchange, all_pairs, max_workers=10)
                logger.info("  Fetched %d/%d assets", len(all_data), len(all_pairs))

                # Liquidity filter — drop low-volume meme coins before the bracket
                all_data, illiquid = filter_illiquid(all_data, cfg.min_volume_usd_24h)
                if illiquid:
                    logger.info("  Liquidity filter: excluded %d assets (< $%.0fM 24h vol): %s",
                                len(illiquid), cfg.min_volume_usd_24h / 1e6,
                                ", ".join(f"{p.split('/')[0]} (${v/1e6:.1f}M)" for p, v in illiquid))

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

                # Find best signal — LONGS ONLY: the tournament hunts bull runs.
                # Sell signals are never crowned (we never short; exits are
                # handled separately by the exit-check path).
                best_pair = None
                best_signal = None
                best_conf = 0
                for pair, sig in results.items():
                    if sig.action != "buy":
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

                    # ── Bounce Hunter: yesterday's losers → bounce candidates ─
                    # Refresh once per day (first scan after midnight = yesterday's
                    # closing state). List stays stable all day — consistent hunt.
                    # Failed fetch keeps the old list and retries next scan.
                    today_str = _now_mst().strftime("%Y-%m-%d")
                    if last_loser_fetch != today_str:
                        losers = fetch_kraken_losers(exchange, top_n=20, min_volume_usd=5_000_000)
                        if losers:
                            cached_loser_pairs = [l["pair"] for l in losers if l["pair"] not in all_pairs]
                            last_loser_fetch = today_str
                            logger.info("  📉 Yesterday's losers (bounce candidates): %s",
                                        ", ".join(l["asset"] + f"({l['pct']:.0f}%)" for l in losers[:5]))
                        else:
                            logger.info("  Losers fetch failed — keeping previous bounce list")

                    # Run bounce tournament on yesterday's losers
                    bounce_champion = None
                    if len(cached_loser_pairs) >= 2:
                        try:
                            logger.info("  🔄 Running bounce tournament on %d losers...", len(cached_loser_pairs))
                            bounce_champion, bounce_history = bounce_tournament(exchange, cfg, cached_loser_pairs)
                            if bounce_champion and bounce_champion.signal.action == "buy":
                                logger.info("  🔄 Bounce Champion: BUY %s (%.0f%%)",
                                            bounce_champion.pair.split("/")[0],
                                            bounce_champion.signal.confidence)
                            else:
                                logger.info("  Bounce champion did not produce a signal")
                                bounce_champion = None
                        except Exception as exc:
                            logger.warning("  Bounce tournament failed: %s", exc)

                    # Champion vs Champion: momentum winner vs bounce winner
                    # (both are buys — longs only, the bot never shorts)
                    if bounce_champion and bounce_champion.signal.action == "buy":
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
        _n = _now_mst()
        next_block_name, next_interval, _, _, _, next_no_buy, _ = get_time_block(_n.hour, _n.weekday())
        _day_label = WEEKDAY_NAMES.get(_n.weekday(), "?")
        if next_no_buy:
            logger.info("  Next check in 30s | %s %s | COOLDOWN — no buys until 00:00",
                        next_block_name, _day_label)
        else:
            logger.info("  Next check in 30s | %s %s | threshold %d%% | full scan every %dm",
                        next_block_name, _day_label, auto_trade_threshold, next_interval)
        time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
