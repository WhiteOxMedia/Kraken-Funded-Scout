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
import logging.handlers
import atexit
import threading
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo
MST = ZoneInfo("America/Denver")  # Auto DST: MDT (UTC-6) summer, MST (UTC-7) winter

def _config_logging() -> None:
    """Logging that survives detachment. The driver threads log through
    `logging` to stderr; when a launcher redirects stderr into a FILE,
    the default console encoding (cp1252) died on the rotation glyphs
    (the \U0001f306 mojibake in the detached logs) and a redirected
    stdout sat block-buffered for minutes. Every record now also lands
    in scanner.log (UTF-8; StreamHandler flushes after each record, so
    the file is live regardless of buffering) with 14 midnight
    rotations, no matter how the process was started. The file/level
    setup only belongs to the scanner's own process: the console
    (streamlit) and the diag scripts import this module too -- in those
    hosts the logging config is left exactly as found."""
    if not (sys.argv and sys.argv[0]
            and sys.argv[0].lower().endswith("background_scanner.py")):
        return
    root = logging.getLogger()
    if any(isinstance(_h, logging.FileHandler) for _h in root.handlers):
        return   # idempotent: a re-call/re-import must not stack handlers
    root.setLevel(logging.INFO)
    _fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%I:%M %p")
    _con = logging.StreamHandler()
    _con.setFormatter(_fmt)
    root.addHandler(_con)
    try:
        _fh = logging.handlers.TimedRotatingFileHandler(
            _LOG_PATH, when="midnight", backupCount=14, encoding="utf-8")
        _fh.setFormatter(_fmt)
        root.addHandler(_fh)
    except Exception as exc:
        logger.warning("log file handler unavailable (%s) — console-only "
                       "logging this boot", exc)   # the console stays


_LOG_PATH = Path(__file__).parent / "scanner.log"
_config_logging()
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

# Liquidity floors for the rotation-aware filter (module-level: the console's
# FAST $20 board uses the same floors — one source of truth).
LIQ_ROT_FLOOR_WEEKDAY = 15_000.0
LIQ_ROT_FLOOR_WEEKEND = 10_000.0
LIQ_DAILY_BACKSTOP = 250_000.0


def rotation_key_for_hour(hour: int) -> str:
    """4h rotation key for a Denver-local hour (00-04 ... 20-24)."""
    return ROTATION_KEYS[min(hour // 4, 5)]


# ── ROTATION MEMORY (user rule, Aug 30: "look at the previous day's market
# at the time of the rotation — help with noise and repeating signals back
# to back. I go in heavy, trend stops, I get out as instructed, then I'm
# told to get back in and double-dip fines eat the profits.") ────────────
# Two rules with the same shape: the reference frame is the ROTATION, not
# the trailing candles.
#   1. LOCKOUT — RETIRED (user rule, Sep 10: "delete the rule"): the
#      one-green-per-rotation cap and the 10-minute per-pair anti-spam are
#      OFF. A coin can fire again the moment it passes every remaining
#      gate — re-entries across rotations were already free; now
#      same-rotation re-fires are too. The gate/mark functions below stay:
#      the profit re-arm disarms the ledger, the popups still SAY
#      "re-entry", and the yesterday's-hour demotion (rule 2) stands.
#   2. YESTERDAY'S HOUR — if this coin's same rotation window ran and
#      dumped on recent days (the PENGU shape), today's signal at this
#      hour is a re-run of a shape that already paid — demoted.
ROTATION_LOCKOUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "rotation_lockout.json")
_OHLCV_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "ohlcv_cache.db")

# Double-dip fine awareness: set to the contest platform's re-entry fine
# (USD) when known. While 0.0, re-entries behave as before; when set,
# re-entry green lights call the fine out loud so the $20 math includes it.
CONTEST_REENTRY_FINE_USD = 0.0

# CONFIRMATION WINDOW (user rule, Aug 31 — the 'Already ran' collision):
# the champion alert marks the rotation when it fires, and the QC + the
# card check the same gate — so the pick the alert JUST made read
# "Already ran! Cold!" minutes later (HYPE 12:42 "Buy!" → 12:48 "cold").
# Within this many minutes of the fire, the QC and the card treat the
# lockout as CONFIRMING THE LIVE ALERT (allowed, reason names the alert's
# time). RETIRED Sep 10 with the lock itself (rule 1 above) — the
# constant stays: the QC's display logic still recognizes a confirming
# green by this wording.
ROTATION_CONFIRM_WINDOW_MIN = 15.0


def _rotation_window(now=None):
    """(start_hour, end_hour, key) for the rotation containing `now` (Denver)."""
    now = now or _now_mst()
    for _start, _end, _key, *_rest in ROTATIONS:
        if _start <= now.hour < _end:
            return _start, _end, _key
    return None


def rotation_gate(asset: str, confirm_window_min: float = 0.0):
    """RETIRED GATE (user rule, Sep 10: "delete the rule"): the
    one-green-per-rotation cap is OFF — this always allows. The call
    sites keep it for what it still returns: a re-entry flag + reason
    when the coin fired earlier TODAY (popups still say so), and the
    confirming-green wording for the QC's display.

    Returns (allowed, reason, is_reentry, fine_usd):
      allowed    always True since the retirement — nothing blocks on the lock
      reason     human explanation when blocked / re-entering
      is_reentry True if the asset fired earlier TODAY in a different rotation
      fine_usd   CONTEST_REENTRY_FINE_USD on a re-entry (0.0 otherwise)

    CONFIRMATION WINDOW (user rule, Aug 31): confirm_window_min > 0 (the
    QC and the card pass ROTATION_CONFIRM_WINDOW_MIN) treats a lockout set
    within that many minutes as CONFIRMING THE LIVE ALERT — allowed, with
    the reason naming the alert's time. The default 0 (the scanner's
    re-fire guard, the mover funnel) stays strict for the whole rotation.
    """
    now = _now_mst()
    win = _rotation_window(now)
    if win is None:
        return True, "", False, 0.0  # cooldown hours have no buys anyway
    _start, _end, rot_key = win
    today = now.strftime("%Y-%m-%d")
    try:
        import json as _json
        state = _json.loads(open(ROTATION_LOCKOUT_FILE, encoding="utf-8").read())
    except Exception:
        state = {}
    rec = state.get(asset)
    if rec and rec.get("date") == today:
        if rec.get("rotation") == rot_key:
            # RETIRED (Sep 10): same-rotation re-fires are ALLOWED. The
            # confirm-window wording survives so the QC can still show
            # the alert's own green light as a confirmation.
            if confirm_window_min > 0:
                try:
                    from datetime import datetime as _dt
                    _fired = _dt.fromisoformat(str(rec.get("fired_at") or ""))
                    _mins = (now - _fired).total_seconds() / 60.0
                except Exception:
                    _fired, _mins = None, None
                if _mins is not None and 0 <= _mins <= confirm_window_min:
                    return True, (f"confirmation window — the alert fired at "
                                  f"{_fired.strftime('%I:%M').lstrip('0')} "
                                  f"({_mins:.0f} min ago); confirming, not a re-fire"), False, 0.0
        # fired earlier TODAY (this rotation or an earlier one) → allowed,
        # flagged as a re-entry so the popup still says so
        return True, f"re-entry (fired {rec.get('rotation')} earlier today)", \
            True, CONTEST_REENTRY_FINE_USD
    return True, "", False, 0.0


def rotation_mark_fired(asset: str):
    """Record a GO on this asset for this rotation (QC green light or
    desktop alert). The lock it fed no longer blocks (retired Sep 10) —
    the ledger stays for the profit re-arm and the re-entry display."""
    now = _now_mst()
    win = _rotation_window(now)
    if win is None:
        return
    _start, _end, rot_key = win
    today = now.strftime("%Y-%m-%d")
    try:
        import json as _json
        try:
            state = _json.loads(open(ROTATION_LOCKOUT_FILE, encoding="utf-8").read())
        except Exception:
            state = {}
        # prune to today only — the file stays tiny
        state = {a: r for a, r in state.items() if r.get("date") == today}
        state[asset] = {"date": today, "rotation": rot_key,
                        "fired_at": now.isoformat()}
        tmp = ROTATION_LOCKOUT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(state, indent=1))
        os.replace(tmp, ROTATION_LOCKOUT_FILE)
    except Exception as exc:
        logger.debug("rotation mark failed: %s", exc)


def rotation_rearm_on_profit(asset: str) -> bool:
    """THE PROFIT RE-ARM (user rule, Sep 6: "can we carve out the winner
    exception if I collected money (profit)? I want to jump back in as
    soon when it sets up again but the rotation lock is armed"): a SOLD
    that BOOKED NET PROFIT disarms the rotation lockout for this asset —
    the coin can fire again the moment a fresh setup passes EVERY gate
    (fire-time scan, direction, stale-move, knife, 3% guard, popup cap —
    this re-opens the door, it does not hold it open). A loss or a
    scratch exit never calls this — you don't re-buy what just beat you.

    Only a lock recorded for the CURRENT rotation is disarmed (an
    earlier-rotation record means the gate is already open — nothing to
    do). The state file is shared with the running scanner (it re-reads
    per gate check), so a console-side disarm takes effect on the very
    next scan — no restart. Returns True if a lock was actually lifted."""
    now = _now_mst()
    win = _rotation_window(now)
    if win is None:
        return False
    _start, _end, rot_key = win
    today = now.strftime("%Y-%m-%d")
    try:
        import json as _json
        try:
            state = _json.loads(open(ROTATION_LOCKOUT_FILE, encoding="utf-8").read())
        except Exception:
            return False
        rec = state.get(asset)
        if not rec or rec.get("date") != today or rec.get("rotation") != rot_key:
            return False  # not locked for THIS rotation — nothing to disarm
        del state[asset]
        tmp = ROTATION_LOCKOUT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(state, indent=1))
        os.replace(tmp, ROTATION_LOCKOUT_FILE)
        logger.info("🔄 PROFIT RE-ARM: %s — profit collected, the rotation "
                    "lock is disarmed (every gate still applies to the "
                    "next fire)", asset)
        return True
    except Exception as exc:
        logger.debug("rotation re-arm failed: %s", exc)
        return False


def rotation_yesterday_context(pair: str, lookback_days: int = 3):
    """What did this pair do in the SAME rotation window on recent days?

    Reads the ohlcv cache for the last `lookback_days` instances of the
    current rotation's hours (e.g. R3 08:00-12:00 on the last 3 days) and
    classifies the window shape:
      blowoff — ran >= 2% and gave >= half of it back (majority of days):
                the PENGU shape; today's signal at this hour is demoted
      trend   — ran >= 2% and held (majority): this hour delivers
      quiet   — rarely runs 2% at this hour
    Returns None when the cache has no data (fail-open, no demotion).
    """
    import sqlite3
    now = _now_mst()
    win = _rotation_window(now)
    if win is None:
        return None
    rot_start, rot_end, _rot_key = win
    # the 20-24 cooldown window ends at hour 24 — datetime.replace can't
    # build that; midnight of the NEXT day is the same instant (Sep 7:
    # the battery first caught this at 20:42 — the function is only
    # reachable in the cooldown hour, which is also the only hour with
    # no buys, which is why it survived every earlier run)
    if rot_end >= 24:
        rot_end = 0
        day_shift = 1
    else:
        day_shift = 0

    shapes = []
    runs = []
    retraces = []
    for back in range(1, lookback_days + 1):
        day = now - timedelta(days=back)
        start_dt = day.replace(hour=rot_start, minute=0, second=0, microsecond=0)
        # rot_end 24 -> hour 0 of the FOLLOWING day (the same instant as
        # "24:00"); the window still spans [start, end)
        _end_day = day + timedelta(days=day_shift)
        end_dt = _end_day.replace(hour=rot_end, minute=0, second=0,
                                  microsecond=0)
        try:
            conn = sqlite3.connect(_OHLCV_CACHE_PATH, timeout=4)
            rows = conn.execute(
                "SELECT open, high, low, close FROM ohlcv "
                "WHERE pair=? AND ts>=? AND ts<? ORDER BY ts",
                (pair, int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)),
            ).fetchall()
            conn.close()
        except Exception:
            continue
        if not rows:
            continue
        o = float(rows[0][0])
        hi = max(float(r[1]) for r in rows)
        cl = float(rows[-1][3])
        if o <= 0 or hi <= 0:
            continue
        gain = (hi - o) / o * 100.0
        retrace = (hi - cl) / hi * 100.0
        runs.append(gain)
        retraces.append(retrace)
        if gain >= 2.0 and retrace >= max(0.6, gain * 0.5):
            shapes.append("blowoff")
        elif gain >= 2.0:
            shapes.append("trend")
        else:
            shapes.append("quiet")

    if not shapes:
        return None
    n = len(shapes)
    blow = shapes.count("blowoff")
    trend_n = shapes.count("trend")
    if blow * 2 > n:
        shape = "blowoff"
    elif trend_n * 2 > n:
        shape = "trend"
    else:
        shape = "quiet"
    avg_run = sum(runs) / len(runs)
    avg_re = sum(retraces) / len(retraces)
    if shape == "blowoff":
        detail = (f"{blow}/{n} recent days ran +{avg_run:.1f}% at this hour "
                  f"and dumped — re-run risk")
    elif shape == "trend":
        detail = f"ran +{avg_run:.1f}% and held at this hour ({trend_n}/{n} days)"
    else:
        detail = f"quiet at this hour recently (+{avg_run:.1f}% avg run)"
    return {"shape": shape, "detail": detail, "days": n,
            "gain_pct": avg_run, "retrace_pct": avg_re}


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
    """Rotation-aware liquidity filter — uses the Volatility Engine's own
    per-asset × rotation × weekday/weekend volume history.

    A coin passes when:
      · its TYPICAL volume in the CURRENT rotation (engine median, from
        real history) clears the rotation floor — weekday $15k, weekend
        $10k per 4h session (everything runs thinner on weekends), AND
      · its actual 24h volume clears a low backstop ($250k) — catches
        coins that recently died but still have fat historical medians.

    Unknown coins (no engine cell yet — fresh gainers) fall back to the
    caller's daily floor (min_volume_usd, default $750k) alone.

    This replaces the flat $1M daily bar, which killed half the universe
    (ALGO does $60-103k PER ROTATION yet sat under $1M daily) while
    never asking whether a coin is active in the window we'd trade it.
    """
    # current rotation × schedule from the same engine tables
    n = _now_mst()
    rot_key = rotation_key_for_hour(n.hour)
    sched = _schedule_for_dow(n.weekday())
    rot_floor = LIQ_ROT_FLOOR_WEEKEND if sched == "weekend" else LIQ_ROT_FLOOR_WEEKDAY

    engine_cells = {}
    try:
        from volatility_engine import load_rotation_thresholds
        doc = load_rotation_thresholds(max_age_s=300)
        if doc:
            engine_cells = doc.get("universe", {})
    except Exception:
        engine_cells = {}

    kept = {}
    excluded = []
    for pair, df in data.items():
        try:
            recent = df.tail(24)
            quote_vol = float((recent["volume"] * recent["close"]).sum())

            cell = engine_cells.get(pair, {}).get(sched, {}).get(rot_key) or {}
            rot_typical = float(cell.get("median_volume_usd") or 0)

            if cell:
                # known coin: active in THIS rotation + alive overall
                if rot_typical >= rot_floor and quote_vol >= LIQ_DAILY_BACKSTOP:
                    kept[pair] = df
                else:
                    excluded.append((pair, quote_vol))
            else:
                # unknown coin: the caller's daily floor is the whole check
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


# Fiat bases that appear as /USD pairs on Kraken (forex, not crypto).
# A US account cannot trade them, so they must never enter the funnel.
# Aug 28 bug: EUR/USD leaked into the losers list → Bounce Hunter spammed
# 9 doomed BUY signals all morning (Kraken: "EUR/USD trading restricted for US").
_FIAT_BASES = {"EUR", "GBP", "CHF", "AUD", "CAD", "JPY", "NZD", "USDT", "USDC"}


def _is_fiat_pair(symbol: str) -> bool:
    base = symbol.split("/")[0] if "/" in symbol else ""
    return base in _FIAT_BASES


# ── THE MOVER FUNNEL (user rule, Aug 31: "widen the gainer funnel for the
# trading bot and leave the contest alone — we don't need both, they share
# the same goal") ───────────────────────────────────────────────────────
# ONE funnel for everything moving OUTSIDE the 54: today's runners (≥+5%
# on 24h across ALL Kraken pairs — the ZORA/XMR case) plus yesterday's
# losers turning (the old bounce list, folded in here). BOT-ONLY: bridge
# writes with source="scout_mover"; the contest path (desktop 89 floor,
# QC, watch) is untouched. Replaces the retired gainer path.
MOVER_MIN_CONF = 55.0       # bot-grade entry floor (NOT the tournament's adaptive bar — XMR's 56% died against ~80)
MOVER_TOP_N = 10            # runners deep-scanned per pass (rate-limit friendly)
MOVER_MIN_PCT = 5.0         # 24h gain that makes a coin a runner (XMR was +11.2%)
MOVER_MIN_VOLUME_USD = 100_000.0
MOVER_MIN_CANDLES = 100     # ≈4 days of 1h candles — a day-old +659% listing is pump risk, not a setup

# ── THE DCA TRANCHE RETIREMENT (user option 2, Sep 2) ──────────────────
# The exit-check loop's buy-the-dip path: wrote scout_dca buys on held
# real-money positions that dipped ≥2.5% with a quick strategy ≥50 — no
# brain bar, no 3% death-line guard, no goal clock — and the bot's reader
# folds those writes into the position (averages down real money). That's
# the wiggle-buy the new game retired ("solid trends that go up, buy
# orders only, no more wiggles"), and it contradicts the bot-side
# dca_enabled=False decision (Aug 30). Code kept (don't-delete rule);
# re-arm ONLY by the user's explicit call.
SCOUT_DCA_ENABLED = False


def fetch_kraken_movers(exchange, tickers=None, top_n: int = MOVER_TOP_N,
                        min_pct: float = MOVER_MIN_PCT,
                        min_volume_usd: float = MOVER_MIN_VOLUME_USD) -> list[dict]:
    """Today's runners across ALL Kraken pairs (not just the 54 challenge
    assets): up ≥min_pct on 24h, non-fiat, volume-floored, top_n by pct."""
    try:
        if tickers is None:
            tickers = exchange.fetch_tickers()
        movers = []
        for symbol, t in tickers.items():
            if not symbol.endswith("/USD"):
                continue
            if _is_fiat_pair(symbol):
                continue
            pct = t.get("percentage")
            if not pct or pct < min_pct:
                continue
            volume = t.get("quoteVolume", 0) or 0
            if volume < min_volume_usd:
                continue
            price = t.get("last", 0) or 0
            if price <= 0:
                continue
            movers.append({"pair": symbol, "asset": symbol.split("/")[0], "pct": pct, "price": price, "volume": volume})
        movers.sort(key=lambda x: x["pct"], reverse=True)
        return movers[:top_n]
    except Exception as exc:
        logger.warning("Movers fetch failed: %s", exc)
        return []


def fetch_kraken_losers(exchange, top_n: int = 20, min_volume_usd: float = 100_000) -> list[dict]:
    """Fetch top losers across ALL Kraken pairs — today's losers are tomorrow's bounce candidates."""
    try:
        tickers = exchange.fetch_tickers()
        losers = []
        for symbol, t in tickers.items():
            if not symbol.endswith("/USD"):
                continue
            if _is_fiat_pair(symbol):
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
CONTEST_TRAIL_PCT = 3.0   # BASE trail off the peak — ratchets tighter on big
                          # runs (see _ratchet_trail_pct): 2% after +$100,
                          # 1.5% after +$200 (user rule, Aug 26: TAO ran nine
                          # twenties and gave back $150 before a flat 3% trail
                          # called the exit — big runs earn tight protection)


def _ratchet_trail_pct(entry: float, high: float, size: float) -> float:
    """Trail width ratchets down as the run grows: 3% base, 2% once the run
    reached +$100, 1.5% after +$200. The more twenties she called, the less
    room the giveback gets."""
    if not entry or not high or high <= entry:
        return CONTEST_TRAIL_PCT
    peak_usd = (high / entry - 1.0) * size
    if peak_usd >= 200.0:
        return 1.5
    if peak_usd >= 100.0:
        return 2.0
    return CONTEST_TRAIL_PCT
CONTEST_SELL_BAR = 50
TAKE_IT_USD = 20  # the user's scalp target: out at +$20, take it every chance
SCALP_TARGET_PCT = 0.4  # +$20 on $5,000 — the take the user actually plays for
VOICE_PREFIX = "Mic check one, two. Testing, testing. "
# GIVEBACK/LOSS WARNINGS — tier-aware since Sep 1 ("everything should auto
# adjust when the contest is selected"): the threshold is 1.5% of the ENTRY
# SIZE, so the warnings are equally loud at every tier (the old fixed $50
# was 10% of a 1k-tier entry and 1.5% of a 10k-tier entry). The old USD
# constants stay defined (retired as thresholds, kept per don't-delete).
GIVEBACK_WARN_USD = 50   # RETIRED as the threshold (Sep 1) — kept defined
LOSS_WARN_USD = 50       # RETIRED as the threshold (Sep 1) — kept defined
GIVEBACK_WARN_PCT_OF_SIZE = 1.5  # giveback threshold: 1.5% of the entry size
LOSS_WARN_PCT_OF_SIZE = 1.5      # loss-warning threshold: same scaling
BIG_PAYOUT_USD = 100     # TAKE IT at/above this = the move is mature; QC blocks
REENTRY_BLOCK_MIN = 60   # re-entries on that pair for this long
_take_level = {}       # last announced $20 multiple per pair
_giveback_level = {}   # last announced giveback step per pair
_loss_level = {}       # last announced loss step per pair
_last_allgood_ts = 0.0 # all-good voice cadence gate (TAKE IT fires anytime)
_last_trade_done = 0.0
TAKE_PAYOUTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "take_payouts.json")


# ── THE TIDE (user rule, Aug 26: "rising tide raises all ships") ──────
# Fleet breadth: what fraction of the tracked universe sits above its own
# 1h trend line (EMA21). The tide sets the tempo: RISING carries greens
# (goal x1.15, the desktop-floor relax), FLAT demands pickiness, OUT is
# priced by the learned bar — NOT a wall ('all ships sink together' was
# priced a myth: _fleetstudy.txt). Computed from the engine's candle
# cache (no live API storm), refreshed at most every 5 minutes.
# RETUNED Sep 11 on the recalibrated spot-native store
# (diag_tideretune.py -> _tideretune.txt): RISING payer rate climbs
# MONOTONICALLY with the bar (60: 20.2% -> 75: 26.9% -> 80: 31.8%), so
# 75 keeps the relax/boost for tides that genuinely pay more (spread
# vs OUT +16.1pp, up from +14.0 at 70). OUT stayed at 35: FLAT vs OUT
# has no payer seam on any cutoff (~10-12% both), so the bar is
# continuity with the learned OUT bar 92 already priced at 35.
TIDE_RISING_MIN = 75.0
TIDE_OUT_MAX = 35.0
_tide_cache = {"ts": 0.0, "pct": None, "state": None}
_tide_last_state = None


def _tide_class(pct: float) -> str:
    """RISING / FLAT / OUT by fleet breadth."""
    if pct >= TIDE_RISING_MIN:
        return "RISING"
    if pct <= TIDE_OUT_MAX:
        return "OUT"
    return "FLAT"


def _fleet_tide(max_age_s: int = 300):
    """(breadth_pct, state) — % of the cached universe above its EMA21."""
    import time as _time
    _now = _time.time()
    if _tide_cache["pct"] is not None and _now - _tide_cache["ts"] < max_age_s:
        return _tide_cache["pct"], _tide_cache["state"]
    try:
        import sqlite3
        import pandas as _pd
        from indicators import ema as _ema
        _cc = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "ohlcv_cache.db"))
        _rows = _cc.execute("SELECT pair, ts, close FROM ohlcv ORDER BY pair, ts").fetchall()
        _cc.close()
        _series = {}
        for _p, _ts, _c in _rows:
            _series.setdefault(_p, []).append(_c)
        _above = _total = 0
        for _p, _closes in _series.items():
            if len(_closes) < 25:
                continue
            _trend = float(_ema(_pd.Series(_closes), 21).iloc[-1])
            _total += 1
            if _closes[-1] > _trend:
                _above += 1
        if _total < 10:
            return None, None
        _pct = 100.0 * _above / _total
        _state = _tide_class(_pct)
        _tide_cache.update(ts=_now, pct=_pct, state=_state)
        return _pct, _state
    except Exception:
        return None, None


# ── LONE WOLF EXCEPTION (user rule, Aug 31: "make an exception in low
# tide to allow movers like XMR through — I set the thresholds before I
# added the tide warning") ─────────────────────────────────────────────
# The confidence thresholds (per-asset bars, the 89 desktop floor) were
# calibrated BEFORE the tide existed — they're tide-blind. In a FLAT tide
# a coin running +5%+ on its OWN is showing idiosyncratic strength the
# confidence number can't see: XMR ran +11.2% overnight in a 37% fleet
# and its 56% bounce signal died below the ~80 bar AND the 89 floor —
# silent through an eleven-percent move. The discount:
#   FLAT tide + own 24h move >= +8%  -> 35 points off the thresholds
#   FLAT tide + own 24h move >= +5%  -> 25 points off
#   RISING tide -> nothing (the fleet already carries greens)
#   OUT tide    -> nothing (all ships sink together — hard block stands)
# The user's own QC floor (deep read >= 60) is NOT discounted — the alert
# and the Quick Check must never disagree (the PUMP lesson).
LONE_WOLF_MOVE_MIN = 5.0      # own 24h % move that earns the discount
LONE_WOLF_MOVE_BIG = 8.0      # own 24h % move that earns the bigger one
LONE_WOLF_PTS = 25            # confidence points off the tide-blind bars
LONE_WOLF_PTS_BIG = 35
LONE_WOLF_BAR_FLOOR = 50      # the bridge bar never drops below this
LONE_WOLF_DESKTOP_FLOOR = 55  # desktop floor never drops below this (XMR's 56% passes)
# TIDE-AWARE DESKTOP FLOOR (user rule, Aug 31: "the thresholds don't know
# the tide — they should self-adjust"): a RISING fleet carries greens, so
# the alert floor relaxes from the pre-tide 89. Evidence (diag_tidehistory
# conf×tide matrix): in RISING tides, ≥89 picks won 48% of decided trades
# while sub-60 picks won 83% — the 89 floor added no quality in rising
# fleets, it just silenced them. FLAT keeps the full 89 (lone wolves
# exempt at 55); OUT blocks upstream; a broken tide read fails to 89.
# The deep-read ≥60 pre-check still applies to EVERY alert.
TIDE_RISING_DESKTOP_FLOOR = 78.0
# TIDE-AWARE ROTATION BAR (user rule, Aug 31: "it knows about the tide and
# the weekday/weekend and coin history"): the engine's data bar already
# carries the weekday/weekend schedules and per-coin false-breakout history
# — the tide was the missing input. A RISING fleet relaxes the bar by the
# same margin as the desktop floor (89→78), never below 60 (the deep-read
# floor — the QC and the alert must never disagree). FLAT keeps the data
# bar (the data decides); OUT blocks upstream; a broken tide read leaves
# the data bar untouched.
TIDE_RISING_BAR_RELAX = 89.0 - TIDE_RISING_DESKTOP_FLOOR   # 11 pts — one source of truth
TIDE_RISING_BAR_FLOOR = 60.0        # the bar never drops below the deep-read floor
# NEAR-MISS DEEP SCAN (user rule, Aug 31: "do a deep scan when it is
# within 5% of passing"): the bar's first look is the tournament's quick
# read — cheap. When a candidate lands within 5 points BELOW the bar, the
# brain is consulted: the deep scan runs and the bar is judged on the
# BRAIN'S number — the same deep conviction the QC, the alert, and the
# bridge write use. A quick-read miss is not a verdict; a brain miss is.
NEAR_MISS_PTS = 5.0
_lone_wolf_cache = {"pair": None, "pct": 0.0, "ts": 0.0}

# ── ROTATION-AWARE POPUP FLOOR (user rule, Sep 4: "the popup floor 89%
# doesn't know about the rotation schedule — it needs to self-adjust
# accordingly") ────────────────────────────────────────────────────────
# Calibration: 199 executed buys → decided outcomes, bucketed by Denver
# rotation × confidence band (diag_popupfloor.py → _popupfloor_check.txt).
# The flat 89 carried no quality in most rotations — it silenced the
# winners and passed the losers:
#   00-04 Asia:    60-69 reads won 71% (7) while 89+ won 23% (13) → relax
#   08-12 EU/US:   70-78 reads won 75% (8) while 89+ won 17% (30) → relax
#   16-20 US Late: 89+ went 0-for-12 (late reversals eat champions)   → tighten
#   04-08 / 12-16: no band beat the rail (all weak / whale-driven)     → stand
# The floor feeds the SAME stack as before: RISING tide relaxes by the
# 11-pt margin (never below 78), lone wolf still discounts to the 55
# floor, the brain's deep read still judges every popup, fail-closed.
ROTATION_POPUP_FLOOR = {
    "00-04": 84,   # R1 Asia — slow reliable movers; mid-band reads win
    "04-08": 89,   # R2 Pre-US — no band wins; the guard rail stands
    "08-12": 84,   # R3 EU/US overlap — best moves; 70-78 reads won 75%
    "12-16": 89,   # R4 US Afternoon — mixed evidence; stands
    "16-20": 93,   # R5 US Late — 89+ went 0/12: late reversals eat champions
    "20-24": 99,   # Cooldown — no buys anyway (upstream hard block)
}
# HEAD-TO-HEAD CHALLENGE (user rule, Sep 4: "the brain should allow it if
# it's close within 5% to challenge head to head"): a brain read within 5
# points BELOW the popup floor isn't buried — the popup fires as a
# CHALLENGER, marked as such. Same philosophy as the bot-side near-miss
# (NEAR_MISS_PTS): a quick miss is not a verdict, and a brain near-miss
# deserves the user's eyes, not silence. Below floor-5 stays suppressed.
POPUP_NEAR_MISS_PTS = 5.0


def popup_floor_for_rotation(rot_key: str | None = None, now=None) -> int:
    """The popup floor's rotation base — one source of truth for the alert
    path and the QC's thin-book rule (they must never disagree). Unknown
    rotation or a broken read fails closed to the old 89 rail."""
    key = rot_key or rotation_key_for_hour((now or _now_mst()).hour)
    return int(ROTATION_POPUP_FLOOR.get(key, 89))


def popup_floor(tide_state: str | None = None, lw_pts: float = 0,
                rot_key: str | None = None,
                pair: str | None = None) -> float:
    """The full popup floor stack: rotation base → tide margin → lone wolf
    → THE JOURNAL (Sep 10: the discipline seam sewn both sides — the
    discipline engine records which gates buried PAYING candidates every
    rotation; gate_relief() converts that hindsight into a few floor
    points, capped and decaying; buried duds claw it back)    → THE COIN'S
    OWN TIDE MEMORY (Sep 10 rebalance: NOT in OUT — there the memory
    personalizes the LEARNED BAR via tide_personal_bar, one tax; in
    FLAT and every state without its own learned bar it still eases
    or tightens THIS floor, the coin's only price there. A coin isn't
    taxed twice for the same tide). Lives beside
    the constants so the alert path and the QC compute the same number
    from the same inputs. Fail-closed: a broken journal or a broken
    memory opens nothing."""
    fl = float(popup_floor_for_rotation(rot_key))
    if tide_state == "RISING":
        fl = max(TIDE_RISING_DESKTOP_FLOOR, fl - TIDE_RISING_BAR_RELAX)
    if lw_pts:
        fl = max(LONE_WOLF_DESKTOP_FLOOR, fl - lw_pts)
    try:
        import discipline as _dc
        _rel = float(_dc.gate_relief("brain-floor"))
        if _rel > 0:
            # RELIEF ONLY LOWERS (fix, Sep 10): the old max(60, fl-rel)
            # RAISED a lone-wolf floor that already sat at 55 — relief at
            # zero must be a no-op. The floor never leaves [55, base];
            # relief can drag it toward 60 at most, never below.
            fl = max(fl - _rel, min(fl, 60.0))
    except Exception as exc:
        logger.warning("gate_relief read failed (%s) — the brain floor "
                       "ships UNRELIEVED (a broken journal closes, "
                       "never opens)", exc)
    # the memory here — the OUT gate prices the coin through
    # tide_personal_bar (the memory rides the LEARNED BAR, one tax);
    # stacking it on the floor too taxed the same coin twice. FLAT (and
    # any state without its own learned bar) still gets it here — the
    # floor is that coin's only price.
    try:
        if pair and tide_state != "OUT" and tide_state in tide_memory_states():
            import tide_memory as _tmem
            _tadj, _twhy = _tmem.tide_edge(pair, tide_state)
            if _tadj:
                fl = max(LONE_WOLF_DESKTOP_FLOOR, fl - _tadj)
    except Exception as exc:
        logger.warning("tide_memory read failed for %s/%s (%s) — the "
                       "coin's personal edge ships UNAPPLIED (a broken "
                       "memory closes, never opens)", pair, tide_state, exc)


# ── THE OUT-TIDE PASS PATHS (Sep 10). OUT is no longer a wall; it is
# priced. A candidate passes in this order: ① THE LONE WOLF (solo
# strength), ② THE LEARNED OUT BAR (tide_learn.py — the deep candle
# store prices the tide from its own payer rate), ③ THE JOURNAL
# RELEASE (the discipline journal releases EVERY champion-grade
# candidate when tide-out has buried payers — uncapped, user rule
# Sep 10). The last absolute gate is the data's own price —
# fail-closed to the blanket block when the learner has no model.
SEAM_TIDE_RELEASE_QUICK = 92.0
SEAM_TIDE_RELEASE_DEEP = 84.0


def tide_memory_states() -> tuple:
    """The tide states the per-coin memory adjusts on the FLOOR and the
    ASSET BAR (ADJUSTED_STATES in tide_memory.py — RISING rides the
    fleet). In OUT the memory rides the learned bar instead
    (tide_personal_bar) — never both."""
    try:
        import tide_memory as _tmem
        return _tmem.ADJUSTED_STATES
    except Exception:
        return ()


def tide_personal_bar(pair: str | None, tide_state: str | None,
                      base_bar: float | None) -> float | None:
    """THE OUT GATE'S BAR FOR THIS COIN (Sep 10 rebalance): the fleet's
    learned price (tide_learn.py) adjusted by the coin's own tide memory
    (tide_memory.py — the same evidence the fleet bar prices, viewed
    per-coin). One bar, one tax: a habitual OUT-tide payer gets a
    PERSONAL bar a few points under the fleet's; a coin that never pays
    in OUT hours pays a few over. Clamped to the learned band [86, 97]
    — the personal bar never leaves the band the data set. Fail-closed:
    no fleet model -> None (the blanket block stands); a broken memory
    or a bare pair -> the fleet bar unchanged."""
    if base_bar is None:
        return None   # no fleet model — nothing to personalize
    if not pair:
        return float(base_bar)
    try:
        import tide_memory as _tmem
        import tide_learn as _tl
        adj, _why = _tmem.tide_edge(pair, tide_state)
        return float(max(_tl.BAR_MIN, min(_tl.BAR_MAX,
                                          float(base_bar) - adj)))
    except Exception:
        return float(base_bar)


def _tide_learn_bar() -> tuple[float | None, str]:
    """(learned OUT bar, meta) — the deep-store learner's confidence bar
    for OUT tides (the payer-rate price, clamped [86, 97]). (None, why)
    when the learner has no model — the caller keeps the blanket block
    (fail-closed). First call refreshes synchronously (~seconds), then
    the model is cached for the TTL."""
    try:
        import tide_learn as _tl
        return _tl.tide_out_floor()
    except Exception as _exc:
        return None, f"the tide learner is unavailable ({_exc})"


def _seam_releases_tide_out(quick, deep, pair: str = "",
                            deep_fetcher=None) -> tuple[bool, str]:
    """(released, why) — the discipline journal releases EVERY
    champion-grade candidate through the OUT-tide block (user rule,
    Sep 10: never cap the champions — at least one is a floor, not a
    ceiling). Requires: gate_relief('tide-out') > 0 (the journal shows
    tide-out burying payers), quick ≥ 92, the brain's deep read ≥ 84 +
    buy (fetched via deep_fetcher if not already cached — the champion
    earns the consult). Fail-closed: a broken journal or a broken deep
    read opens nothing."""
    try:
        import discipline as _dc
        if _dc.gate_relief("tide-out") <= 0:
            return False, "the journal shows no tide-out burials"
        if float(quick or 0) < SEAM_TIDE_RELEASE_QUICK:
            return False, "not champion-grade on the quick read"
        if deep is None and deep_fetcher is not None:
            try:
                deep = deep_fetcher()
            except Exception:
                deep = None
        if deep is None:
            return False, "no deep read — fail-closed"
        if (str(getattr(deep, "action", "") ) != "buy"
                or float(getattr(deep, "confidence", 0) or 0) < SEAM_TIDE_RELEASE_DEEP):
            return False, "the brain does not champion it"
        return True, "the discipline journal opened the tide gate"
    except Exception:
        return False, "the journal is unreadable — fail-closed"

# ── TREND-EXIT WATCH (user rule, Aug 31: "when the trend reverses then
# the bot should 'get out now'" + "the console tracks it and sends the
# signal silently to the bridge side for the bot's trades") ────────────
# RESTORED (user directive, Sep 4: "restore the gates that were deleted
# and make them work properly, if they were in effect on Sept 3rd" —
# the channel was briefly retired when the Sep 3 reader rework dropped
# the carve-out; both are back). The scanner is the brain, the bot is
# the executor: for every pair the bot HOLDS, run the same 1h EMA knife
# that bans entries. Reversed → a SILENT exit signal
# (source="scout_trend_exit") goes to the bridge — no voice, no popup,
# no phone push; the bot sells on its next cycle. The bot's own knife
# (engine-side) is the independent floor if the scanner dies. Read
# cached per pair (the 1h trend doesn't change faster than minutes);
# failed reads are NOT cached; fail-closed: a broken read never
# triggers an exit.
SCOUT_TREND_EXIT_ENABLED = True    # restored Sep 4 — the reader admits it again
_trend_exit_cache: dict = {}
_trend_exit_pace: dict = {}   # pair -> last write attempt ts (15-min pacing)


def knife_reversed_1h(exchange, pair: str, cfg, max_age_s: float = 300.0) -> tuple[bool, str]:
    """(reversed, note) — the 1h EMA knife: EMA_short < EMA_long AND price
    < EMA_long. The SAME structural test the QC, the bridge pre-check and
    the desktop veto use to refuse downward-trend entries — if we wouldn't
    BUY this trend, the bot doesn't RIDE it."""
    import time as _time
    _now = _time.time()
    _c = _trend_exit_cache.get(pair) or {}
    if _now - float(_c.get("ts") or 0.0) < max_age_s:
        return bool(_c.get("reversed")), str(_c.get("note") or "")
    try:
        _ohl = exchange.fetch_ohlcv(pair, timeframe="1h", limit=100)
        if not _ohl or len(_ohl) < 30:
            return False, ""  # not enough data — fail-closed, don't cache
        import pandas as _pd
        from indicators import ema as _ema
        _closes = _pd.DataFrame(_ohl, columns=["timestamp", "open", "high", "low", "close", "volume"])["close"]
        _es = float(_ema(_closes, cfg.ema_short).iloc[-1])
        _el = float(_ema(_closes, cfg.ema_long).iloc[-1])
        _px = float(_closes.iloc[-1])
        _rev = _es < _el and _px < _el
        _note = (f"EMA{cfg.ema_short} {_es:.4f} < EMA{cfg.ema_long} {_el:.4f}, "
                 f"price {_px:.4f} under both") if _rev else ""
        _trend_exit_cache[pair] = {"ts": _now, "reversed": _rev, "note": _note}
        return _rev, _note
    except Exception as exc:
        logger.debug("trend-exit knife failed for %s: %s", pair, exc)
        return False, ""  # fail-closed, don't cache failures


# -- THE CONTEST SIZE (the single source of truth for how big is this
# contest -- every popup path that self-adjusts to the account ticks off
# this one helper, the way the voice/giveback/loss warnings did at Sep 1).
# Priority: (1) contest_account.json current_balance (the live ledger -- the
# TIA close changed the contest window and reset the popups, so the account
# file is the actual game state); (2) console_state.json tier label (the
# user's selection -- $1,000 / $2,500 / $5,000 / $10,000 etc.); (3) the
# config default. Cached per scan so every gate reads the same number.
_CONTEST_SIZE_CACHE = {'ts': 0.0, 'size': 0.0}

def _contest_size(now=None) -> float:
    '''The contest's current balance in USD -- the number every size-sensitive
    popup gate scales off. Fail-closed to 0.0 (no size-sensitive gate
    fires on a broken read; the brain/goal/tide gates still decide).
    Refresh at most every 30s -- the contest ledger moves in trade events,
    not tick-by-tick.
    '''
    import time as _t
    _now = _t.time()
    if _CONTEST_SIZE_CACHE['size'] and _now - _CONTEST_SIZE_CACHE['ts'] < 30.0:
        return _CONTEST_SIZE_CACHE['size']
    _size = 0.0
    try:
        import contest_account as _ca
        _acct = _ca.load_state()
        _cb = float(_acct.get('current_balance') or 0.0)
        if _cb > 0:
            _size = _cb
    except Exception as exc:
        logger.warning("contest_account unreadable (%s) — sizing falls "
                       "to the tier/config ladder (a mis-sized entry "
                       "trades on stale dollars)", exc)
    if _size <= 0.0:
        try:
            import json as _json
            from pathlib import Path as _P
            _cs = _json.loads((_P(__file__).resolve().parent / 'console_state.json').read_text())
            _tier_txt = str(_cs.get('tier', '$1,000')).replace(',', '').replace('$', '')
            _parsed = float(_tier_txt)
            if _parsed > 0:
                _size = _parsed
        except Exception as exc:
            logger.warning("console tier unreadable (%s) — sizing falls "
                           "to the config default", exc)
    if _size <= 0.0:
        try:
            from config import DEFAULT_CONFIG
            _size = float(DEFAULT_CONFIG.challenge_balance)
        except Exception:
            _size = 1000.0
    _CONTEST_SIZE_CACHE.update(ts=_now, size=_size)
    return _size


# ── THE POPUP BRAIN (user rebuild, Sep 1: "the pop ups need a current
# deep scan before they appear and the confidence bar for the popup needs
# to be higher than the bots side to pass... it has eliminated most of
# the risk of the trade for that time window. No more wiggles.") ─────
# The goal: percentage-based (1-3%), set by the market analysis (the
# engine's expected move for this coin/rotation/day), on a 1-3h window.
GOAL_MIN_PCT = 1.0
GOAL_MAX_PCT = 3.0
GOAL_DEFAULT_PCT = 1.5     # young profile / no cell: a moderate default
# ── TIDE-AWARE GOALS (user rule, Sep 4: "the goal should be based on real
# market data + tide knowledge = what the goal should be. Auto set the
# 'goal' every rotation and don't leave money behind. (Low tide =
# realistic lower goals, High tide = realistic higher goals.)"): the
# engine's rotation cell averages ALL tides — a flat fleet realizes less
# than that average, a rising fleet carries moves beyond it. The tide
# multiplier scales the market-capped base: FLAT x0.90 (realistic
# lower), RISING x1.15 (don't leave money behind). OUT never reaches
# the popup path. Applied FRESH at every read (outside the per-pair
# cache) so the goal re-aims the moment the tide turns.
GOAL_TIDE_RISING_MULT = 1.15
GOAL_TIDE_FLAT_MULT = 0.90


def _goal_tide_mult() -> float:
    """The tide's say on the goal (user rule, Sep 4). Fail-open to the
    flat-market multiplier on a broken read (realistic, never greedy)."""
    try:
        _, _ts = _fleet_tide()
        if _ts == "RISING":
            return GOAL_TIDE_RISING_MULT
    except Exception as exc:
        # fail-open is the docstring's contract (never greedy) — but it
        # must say so, or a dead tide read silently deflates the goal
        logger.warning("fleet tide read failed (%s) — the goal ships at "
                       "the FLAT multiplier", exc)
    return GOAL_TIDE_FLAT_MULT

# ── THE WORTH FLOOR (user rule, Sep 2 — "big coins only... less entries
# but more money"; the JUP 14:52 lesson: a guard-capped $233 entry at the
# +2% goal pays $4.66 — noise that only pays the spread): the sized entry
# at its goal must pay at least the dial's share of the daily pace
# ($120 on the 10k tier at the 0.5 code default, $60 at the 0.25 base)
# or the popup is bumped. No alert is better than a worthless alert.
# NOTE: on a FRESH account this also bumps high-drawdown coins whose
# guard cap x goal can't reach the floor — the first trades must be the
# safest ones; as the buffer grows, more coins unlock.
WORTH_FLOOR_PACE_FRAC = 0.5      # the manual-off fallback default
# ── THE AUTO-DIAL (user rule, Sep 4: "auto-dial the worth floor dial to
# the proper location every rotation" — user approved: base 0.25, the
# toggle defaults ON; CORRECTED same day: "catch-up math is causing a
# problem, we don't need to catch up — the goal is a guide not a rule"):
# effective = BASE x pace x tide, clamped [10%, 40%]. The dial's slider
# is the BASE anchor when auto is on; OFF = the old static behavior.
#   pace — this rotation's median expected move / the day's best
#          rotation (the engine's live cells; the floor scales with
#          what the market can actually pay this hour — a static floor
#          goes SILENT in slow hours, killing the day's pace)
#   tide  — RISING carries greens, don't over-filter (0.85); FLAT
#          stands (OUT never reaches the popup)
#   catch-up — RETIRED (user rule, Sep 4): the pace is a guide, not a
#          quota to chase. _worth_catchup_factor is kept per the
#          don't-delete rule but is no longer in the product.
# Calibration: diag_worthdial.py.
WORTH_AUTO_BASE_DEFAULT = 0.25
WORTH_AUTO_MIN = 0.10
WORTH_AUTO_MAX = 0.40
WORTH_TIDE_RISING_FACTOR = 0.85
# RETIRED (user rule, Sep 4 — the catch-up correction above):
WORTH_CATCHUP_BEHIND = 1.15
WORTH_CATCHUP_AHEAD = 0.90
# ── THE ACTUAL FACTOR (user rule, Sep 7: "the auto-dial should know how
# to auto-tune to the ACTUAL floor") — the dial reads the day's REALIZED
# progress, not just the market's room: actual = realized net P&L today /
# the daily pace, folded in as a gentle 10%-swing lever, clamped [0.85,
# 1.15]. A slow morning (actual < planned) RAISES the floor — the day
# needs worthier popups to make the pace. A green morning LOWERS it —
# the pace is being made; let smaller pays count. It is a TILT on the
# dial, never a quota (the Sep 4 catch-up lesson stands: the goal is a
# guide, not a rule — hence the gentle clamp, not 1.15x-on-1.15x).
WORTH_ACTUAL_MIN = 0.85
WORTH_ACTUAL_MAX = 1.15
WORTH_ACTUAL_SWING = 0.10     # ±10% around 1.0 at the extremes
WORTH_ACTUAL_HALFLIFE_USD = 120.0   # the deficit at which the tilt maxes
                              # ($120 = half the 10k tier's daily pace:
                              # $120 behind -> full +10% tilt)
# (RETIRED Sep 9 from the gate — see the BUFFER LEVER below: the buffer
# is the bar; PnL is not an entry factor, it stays a learning signal.)

# ── THE BUFFER LEVER (user rule, Sep 9 — REPLACES the actual factor in
# the gate): the buffer is the realized PnL above the death line, and it
# IS the bar. Fat buffer = low risk = the floor eases (more popups clear
# the bar); thin buffer = the floor tightens. Full range: at the death
# line the factor maxes (x 1.6 → the 40% clamp at the 25% base), at
# break-even (buffer = 3% of start) it bottoms (x 0.4 → the 10% clamp).
# The death line itself NEVER moves. The popup cap is NOT part of the
# design: no money cap, no count cap — the buffer owns the risk.
WORTH_BUFFER_MAX_MULT = 1.60   # at the death line (buffer 0): pickiest
WORTH_BUFFER_MIN_MULT = 0.40   # at break-even and beyond: fully eased


def _worth_pace_factor(now=None) -> float:
    """This rotation's median expected move / the day's best rotation's —
    the floor scales with what the market actually pays this hour. Fail
    open to 1.0 (the base dial stands)."""
    try:
        from volatility_engine import load_rotation_thresholds
        now = now or _now_mst()
        doc = load_rotation_thresholds(max_age_s=900) or {}
        sched = "weekend" if now.weekday() >= 5 else "weekday"
        moves = {}
        for _k in ROTATION_KEYS:
            cell = (doc.get("median", {}).get(sched, {}) or {}).get(_k) or {}
            _mv = float(cell.get("avg_move") or 0)
            if _mv > 0:
                moves[_k] = _mv
        if not moves:
            return 1.0
        _best = max(moves.values())
        _cur = moves.get(rotation_key_for_hour(now.hour)) or _best
        if _best <= 0:
            return 1.0
        return max(0.6, min(1.0, _cur / _best))
    except Exception:
        return 1.0


def _worth_catchup_factor() -> float:
    """RETIRED (user rule, Sep 4: "catch-up math is causing a problem,
    we don't need to catch up — the goal is a guide not a rule"). Kept
    per the don't-delete rule; no longer called by the auto-dial. (Sep 5)
    the daily lock it read is REMOVED — always 1.0 now; the original
    peak-vs-pace math lives in git history."""
    return 1.0


def _worth_actual_factor(now=None) -> float:
    '''RETIRED from the gate (Sep 9: the buffer is the bar — PnL is not
    an entry factor; it stays a learning signal). Kept per the
    don't-delete rule; no longer called by worth_floor_auto_factors.

    The ACTUAL lever (Sep 7 + Sep 9): realized net P&L today vs the day's
    pace, as a gentle 10%-swing tilt whose DOLLAR SENSITIVITY self-adjusts to
    the contest size. deficit = pace - realized; factor = 1 + SWING x
    (deficit / halflife), clamped [0.85, 1.15]. Behind schedule -> >1.0
    (worthier popups); ahead -> <1.0 (smaller pays count).

    The halflife is size-aware (Sep 9):
      halflife = max(WORTH_ACTUAL_HALFLIFE_MIN_USD, contest_size x 0.12)
    so a $1k contest doesn't tilt on a $120 deficit it can't afford, and a
    $10k contest doesn't need a $120 deficit to max out. Fail-open to 1.0
    (the dial stands on pace x tide alone). Open positions carry no realized
    outcome yet: the ledger counts what's banked, which is the point.'''
    try:
        import contest_account as _ca_a
        _acct = _ca_a.load_state()
        _pace = float(_ca_a.daily_pace_usd(_acct))
        if _pace <= 0:
            return 1.0
        _real = float(_ca_a.realized_pnl_today(_acct, now=now))
        _deficit = _pace - _real
        _size = _contest_size(now)
        _halflife = max(WORTH_ACTUAL_HALFLIFE_MIN_USD, _size * WORTH_ACTUAL_HALFLIFE_FRAC)
        _raw = 1.0 + WORTH_ACTUAL_SWING * (_deficit / _halflife)
        return max(WORTH_ACTUAL_MIN, min(WORTH_ACTUAL_MAX, _raw))
    except Exception:
        return 1.0

def _worth_buffer_factor(now=None) -> float:
    '''THE BUFFER LEVER (Sep 9): the buffer — the realized PnL above the
    death line — IS the bar. pos = buffer / (3% of start), clamped [0,1]:
    0 at the line (the account is dying — only worthiest popups clear),
    1 at break-even and beyond (the contest is safe — the floor eases and
    more popups clear). factor = MAX - pos x (MAX - MIN), so at the 25%
    base the full [10%, 40%] clamp is reachable: 0.25 x 1.60 = 40%,
    0.25 x 0.40 = 10%. The death line NEVER moves; nothing here caps
    money — a fatter balance only eases the bar. Fail-open to 1.0.'''
    try:
        import contest_account as _ca_b
        _acct = _ca_b.load_state()
        _buf = float(_ca_b.buffer(_acct))
        _start = float(_acct.get("start_balance") or 0.0)
        if _start <= 0:
            return 1.0
        _pos = max(0.0, min(1.0, _buf / (_start * 0.03)))
        return max(WORTH_BUFFER_MIN_MULT,
                   min(WORTH_BUFFER_MAX_MULT,
                       WORTH_BUFFER_MAX_MULT - _pos * (WORTH_BUFFER_MAX_MULT
                                                       - WORTH_BUFFER_MIN_MULT)))
    except Exception:
        return 1.0


def worth_floor_auto_factors(now=None) -> dict:
    """ONE source of truth for the auto-dial components — the console
    displays the SAME math the scanner gates on. {base, pace, tide,
    buffer, effective, auto}. (Sep 4 correction: NO catch-up — the pace
    is a guide, not a quota. Sep 9: the BUFFER replaces the actual
    factor — the buffer IS the bar.)"""
    _base = WORTH_AUTO_BASE_DEFAULT
    _auto = True
    try:
        import json as _json
        from pathlib import Path as _P
        _cs = _json.loads((_P(__file__).resolve().parent / "console_state.json").read_text())
        _v = float(_cs.get("worth_floor_frac", _base))
        if 0.0 <= _v <= 1.0:
            _base = _v
        _a = _cs.get("worth_floor_auto")
        _auto = bool(_a) if _a is not None else True   # default ON (user, Sep 4)
    except Exception as exc:
        # the defaults ARE the safe shape here (auto ON, base default) —
        # but a dead console_state silently moving the worth floor is
        # exactly the read-before-the-eyes the sweeps exist for
        logger.warning("worth-floor state unreadable (%s) — shipping the "
                       "default base/auto", exc)
    _pace = _worth_pace_factor(now)
    _tide_f = 1.0
    try:
        _, _tstate = _fleet_tide()
        if _tstate == "RISING":
            _tide_f = WORTH_TIDE_RISING_FACTOR
    except Exception as exc:
        logger.warning("fleet tide read failed (%s) — the worth floor "
                       "ships without the RISING lift", exc)
    _buffer = _worth_buffer_factor(now)
    if _base <= 0:
        # the user's explicit OFF (0%) beats the auto-dial — never
        # resurrect a floor the user turned off
        _eff = 0.0
    else:
        _eff = max(WORTH_AUTO_MIN,
                   min(WORTH_AUTO_MAX, _base * _pace * _tide_f * _buffer))
    if not _auto:
        _eff = _base
    return {"auto": _auto, "base": _base, "pace": _pace, "tide": _tide_f,
            "buffer": _buffer,
            # key kept for older consoles/diags that still print 'actual'
            # (Sep 9: it now CARRIES the buffer factor — one source of truth)
            "actual": _buffer, "effective": _eff}


def _worth_floor_frac() -> float:
    """The worth-floor DIAL (user rule, Sep 2: "should be a dial on the
    console — adjustable without Workshop"; Sep 4: auto-dialed every
    rotation): the console's sidebar writes worth_floor_frac (the BASE
    when auto is on) + worth_floor_auto into console_state.json; both
    popup gates read it LIVE at alert time — no scanner restart. AUTO
    (default on): base x pace x tide x buffer, clamped [10%, 40%].
    OFF: the static base. 0.0 = the floor is OFF (every gated popup
    fires). (Sep 9: the BUFFER is the bar — fat balance eases the floor,
    thin tightens it.)"""
    return float(worth_floor_auto_factors()["effective"])

# The stale-move guard (the APT-12:11 lesson): a RE-ENTRY whose move
# peaked hours ago is a sagging top, not a trend. APT peaked 08:15; the
# 12:11 re-entry fired at 100% and sank. Stale = the 4h high is at least
# this old AND price sits at least this far below it.
STALE_MOVE_H = 2.0
STALE_MOVE_DROP_PCT = 1.5


def _window_goal_pct(pair: str) -> float:
    """The trade's % goal: the Volatility Engine's expected move (standard
    threshold) for this coin at this rotation/day — the 'market analysis'
    that sets 1-3% goals. Clamped to the band; moderate default when the
    profile is young."""
    try:
        from volatility_engine import get_move_threshold
        now = _now_mst()
        win = _rotation_window(now)
        rot = win[2] if win else "08-12"
        sched = _schedule_for_dow(now.weekday())
        thr = get_move_threshold(pair, rot, sched, level="standard")
        if thr and float(thr) > 0.2:
            return max(GOAL_MIN_PCT, min(GOAL_MAX_PCT, float(thr)))
    except Exception as exc:
        logger.warning("move_threshold read failed for %s (%s) — the "
                       "goal ships at the moderate default", pair, exc)
    return GOAL_DEFAULT_PCT


# ── THE MARKET CAP ON THE GOAL (user rule, Sep 4: "target each market
# separately — what were those assets doing at this time all week? use
# 24 hour candles, scan 1 week" + user direction, Sep 4: "keep what
# picked yesterday's winners but add to it"): THE ENGINE'S PER-ROTATION
# EXPECTED MOVE STAYS THE GOAL (the derivation behind yesterday's
# winners) — the asset's own week of daily candles is ADDED as a
# reality cap: the median daily range (the room this coin actually
# gives) can only pull the goal DOWN, never up. A stale engine cell
# asking 2.5% of a coin giving 1.2%/day this week gets capped at 1.2%;
# a generous week keeps the engine number untouched. Broken week read:
# the engine number stands alone (fail-open to yesterday's logic).
# Cached 15 min per pair — daily candles don't move faster than that.
# THE SURE THING JOINS IT (user rule, Sep 8: "the breakout should aim at
# what the market can actually give, not a flat default"): the fire path
# calls the SAME machinery — and the per-fire goal_pct stamp keeps
# diag_surething.py accountable for whatever it picks.
# ── THE GOAL TILT (user rule, Sep 8: "let the goal bands calibrate
# back"): the weekly diag measures whether the big asks REACH (the
# aim-gap section); when its verdict says the market goal over-aims for
# breakouts, the seam tilts the pick DOWN by the evidence's own ratio
# (median gave / median asked), clamped [0.80, 1.0] -- gentle, only
# down, never up (a verdict that the goal tracks writes evidence too
# and the seam stands neutral). The evidence expires after 8 days (the
# weekly cadence + slack): a stale squeeze must not outlive its
# measurement -- the machinery's own pick resumes. Fail-open everywhere:
# no file, expired evidence, small sample, or any broken read = 1.0.
ST_GOAL_TILT_MIN = 0.80
ST_GOAL_TILT_TTL_S = 8 * 86400.0
ST_GOAL_TILT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "surething_goal_tilt.json")


def _sure_thing_goal_tilt(path=None) -> float:
    """The diag's aim-gap evidence -> the goal tilt (the SEAM owns the
    policy: the clamp, the freshness, the sample guard -- the file is
    the measurement). 1.0 = the machinery's own pick stands."""
    try:
        import json as _json
        _p = path or ST_GOAL_TILT_FILE
        with open(_p, "r", encoding="utf-8") as _f:
            _d = _json.load(_f)
        _ts = datetime.fromisoformat(str(_d.get("ts")))
        _age_s = (datetime.now(_ts.tzinfo) - _ts).total_seconds()
        if _age_s > ST_GOAL_TILT_TTL_S:
            return 1.0
        if "OVER-AIM" in str(_d.get("verdict") or ""):
            _gave = float(_d.get("median_gave") or 0.0)
            _asked = float(_d.get("median_asked") or 0.0)
            if float(_d.get("big_n") or 0) >= 5 and _asked > 0 and _gave > 0:
                return max(ST_GOAL_TILT_MIN, min(1.0, _gave / _asked))
    except Exception as exc:
        logger.warning("goal-tilt verdict unreadable (%s) — the Sure "
                       "Thing aim ships untilted", exc)
    return 1.0


def _sure_thing_goal(exchange, pair: str) -> float:
    """The Sure Thing fire's % goal: the champion funnel's live market
    goal call (engine rotation cell, capped by the coin's own week,
    x the live tide, clamped [1, 3]), then tilted by the goal bands'
    calibration when the diag's evidence says the big asks over-aim.
    Fail-open to the engine constant -- a broken goal read must never
    stall the fire path (the fire gates are the safety net; the goal
    is an aim)."""
    try:
        _g = float(_market_goal_pct(exchange, pair))
        if _g > 0:
            _tilt = _sure_thing_goal_tilt()
            if _tilt < 1.0:
                logger.info("  SURE THING %s: GOAL TILT %.2f (the aim-gap "
                            "verdict: the big asks came down)", pair, _tilt)
            return max(GOAL_MIN_PCT, min(GOAL_MAX_PCT, _g * _tilt))
    except Exception as exc:
        logger.warning("Sure Thing goal read failed for %s (%s) — "
                       "shipping the constant", pair, exc)
    return st.SURE_THING_GOAL_PCT


MARKET_GOAL_TTL_S = 900.0
_market_goal_cache: dict = {}   # pair -> (ts, goal_pct)


def _market_goal_pct(exchange, pair: str) -> float:
    """The trade's % goal (user rule, Sep 4: "real market data + tide
    knowledge"): the market base — the ENGINE's per-rotation expected
    move (the old derivation, what picked yesterday's winners) CAPPED by
    the asset's own week (median daily range of the last 7 daily
    candles — the cap only pulls DOWN, never up) — multiplied by the
    LIVE tide (FLAT x0.90 realistic-lower / RISING x1.15 don't-leave-
    money-behind), clamped [1, 3]. The base is cached 15 min per pair;
    the tide applies FRESH at every read so the goal re-aims the moment
    the fleet turns. Broken reads fail open to the engine number."""
    import time as _t
    _eng = _window_goal_pct(pair)
    _now = _t.time()
    _c = _market_goal_cache.get(pair)
    _base = None
    if _c and _now - _c[0] < MARKET_GOAL_TTL_S:
        _base = _c[1]
    else:
        try:
            _d = exchange.fetch_ohlcv(pair, timeframe="1d", limit=8)
            if _d and len(_d) >= 4:
                _rngs = []
                for _k in _d[-7:]:
                    _o, _h, _l = float(_k[1]), float(_k[2]), float(_k[3])
                    if _o > 0 and _h >= _l > 0:
                        _rngs.append((_h - _l) / _o * 100.0)
                if _rngs:
                    _rngs.sort()
                    _n = len(_rngs)
                    _med = _rngs[_n // 2] if _n % 2 else (_rngs[_n // 2 - 1] + _rngs[_n // 2]) / 2.0
                    if _med > 0.2:
                        _base = max(GOAL_MIN_PCT, min(GOAL_MAX_PCT, min(_eng, _med)))
                        _market_goal_cache[pair] = (_now, _base)
        except Exception:
            _base = None
    if _base is None:
        _base = _eng   # the engine number alone — yesterday's logic, unchanged
    # the tide's say — applied fresh, outside the cache
    return max(GOAL_MIN_PCT, min(GOAL_MAX_PCT, _base * _goal_tide_mult()))


def _rotation_window_min(now=None) -> float:
    """(user rule, Sep 4: "the goal should reach before the rotation
    happens ideally") The window is bounded by the NEXT rotation
    boundary — the 4h block this entry lives in. min(180, max(60,
    minutes to the boundary)): entering early in a rotation gets the
    standing 3h cap; mid-rotation gets the remaining time; near the
    boundary the 60-min floor makes an EXCEPTION (the window crosses
    into the next rotation — the boundary warning + forced full exit
    scan cover it)."""
    now = now or _now_mst()
    _mins_left = (4 - (now.hour % 4)) * 60 - now.minute
    return min(180.0, max(60.0, float(_mins_left)))


def _stale_move(exchange, pair: str):
    """(hours_since_4h_high, pct_below_high) when the move is STALE — the
    APT shape: the peak is hours old and price has fallen away from it.
    None when the move is fresh. Re-entry popups on stale moves are the
    account-killer (APT 12:11: 100% conf, peaked 08:15, sank on entry)."""
    try:
        _ohl = exchange.fetch_ohlcv(pair, timeframe="1h", limit=5)
        if not _ohl or len(_ohl) < 4:
            return None
        _his = [float(c[2]) for c in _ohl]
        _hi = max(_his)
        _hi_idx = _his.index(_hi)
        _age_h = float(len(_ohl) - 1 - _hi_idx)  # hours since the high candle
        _px = float(_ohl[-1][4])
        _drop = (_hi - _px) / _hi * 100.0 if _hi > 0 else 0.0
        if _age_h >= STALE_MOVE_H and _drop >= STALE_MOVE_DROP_PCT:
            return (_age_h, _drop)
        return None
    except Exception:
        return None


def lone_wolf_discount(exchange, pair: str, max_age_s: float = 900.0) -> tuple[int, str]:
    """(points_off, note) for a coin moving on its own in a dead or sinking
    fleet. FLAT since Aug 31; OUT extended Sep 4 (the POPCAT lesson: the
    blanket OUT block buried 12 crowns while the market paid — a TRUE solo
    runner moving against the whole fleet earns the same discount, and the
    popup fires marked 'against an out tide'). RISING gets nothing (the
    fleet already carries). The 24h move is cached per pair (15 min) — one
    ticker fetch per signal, not one per gate. Fail-closed: a broken tide
    or ticker read means NO discount (the normal thresholds stand)."""
    import time as _time
    _tp, _ts = _fleet_tide()
    if _tp is None or _ts not in ("FLAT", "OUT"):
        return 0, ""
    _now = _time.time()
    if _lone_wolf_cache.get("pair") == pair and _now - _lone_wolf_cache.get("ts", 0.0) < max_age_s:
        own = float(_lone_wolf_cache.get("pct") or 0.0)
    else:
        try:
            _tk = exchange.fetch_ticker(pair)
            own = float(_tk.get("percentage") or 0.0)
        except Exception as exc:
            logger.debug("lone wolf ticker failed for %s: %s", pair, exc)
            return 0, ""
        _lone_wolf_cache.update(pair=pair, pct=own, ts=_now)
    _tide_txt = ("against an OUT tide" if _ts == "OUT"
                 else "while the tide's flat")
    if own >= LONE_WOLF_MOVE_BIG:
        return LONE_WOLF_PTS_BIG, f"+{own:.0f}% on her own {_tide_txt} ({_tp:.0f}% of the fleet up)"
    if own >= LONE_WOLF_MOVE_MIN:
        return LONE_WOLF_PTS, f"+{own:.0f}% on her own {_tide_txt} ({_tp:.0f}% of the fleet up)"
    return 0, ""


def _hail_mary_qualifies(deep, df, carry, balance, floor,
                         ema_short=9, ema_long=21) -> tuple[bool, str]:
    """The candidate side of THE HAIL MARY EXCEPTION (user rule, Sep 4 —
    KEPT when the daily lock was removed, Sep 5: "Keep the 'Hail
    Mary'"): a CHAMPION-grade setup on a MAJOR book — the exception is
    the account-in-danger shot, never the BRAIN's or the BOOK's.
      1. CHAMPION-GRADE: the brain's deep read clears the popup floor
         (the same bar a champion clears — a Hail Mary is never a
         challenger-grade bet)
      2. VERIFIED UPTREND: EMA_short > EMA_long AND close > EMA_long on
         the deep frame (the inverse knife — the same test that bans
         downtrends, reversed: the trend must be UP, not merely not-down)
      3. MAJOR BOOK: the book carries the FULL contest balance (the
         'big book — all in fine' standard — deep liquidity, no slippage
         games on the one shot that matters)
    Freshness: the stale-move guard already ran upstream (a Hail Mary is
    never a sagging top). Momentum: the champion-grade deep read IS the
    momentum evidence. Returns (qualifies, why)."""
    try:
        if deep is None or df is None:
            return False, "no deep read — fail-closed"
        if str(deep.action) != "buy":
            return False, f"the brain reads {deep.action}, not buy"
        _bc = float(deep.confidence)
        if _bc < float(floor):
            return False, (f"brain {_bc:.0f}% < the champion floor "
                           f"{float(floor):.0f}% (a Hail Mary is never challenger-grade)")
        from indicators import ema as _ema
        _es = float(_ema(df["close"], ema_short).iloc[-1])
        _el = float(_ema(df["close"], ema_long).iloc[-1])
        _px = float(df["close"].iloc[-1])
        if not (_es > _el and _px > _el):
            return False, "no verified uptrend (EMA9 must sit above EMA21 with price over both)"
        if carry is None or balance is None or float(carry) < float(balance):
            return False, (f"not a major book (carries ${float(carry or 0):,.0f}, "
                           f"needs the full ${float(balance):,.0f})")
        return True, (f"champion-grade brain {_bc:.0f}% ≥ {float(floor):.0f}%, verified "
                      f"uptrend, major book (carries ${float(carry):,.0f})")
    except Exception as exc:
        return False, f"qualify check failed ({exc}) — fail-closed"


def _save_take_payout(pair: str, amount: int):
    """Persist big TAKE IT payouts so the console's QC can block re-entries
    while the move is exhausted (atomic write, survives restarts)."""
    import json
    try:
        doc = {}
        try:
            with open(TAKE_PAYOUTS_JSON, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            doc = {}   # no payouts file yet — the fresh-map normal
        except Exception as exc:
            # the fresh entry overwrites the map either way; the loss
            # of the PRIOR blocks (re-entry protection) must be visible
            logger.warning("TAKE payouts map unreadable (%s) — prior "
                           "payout blocks are lost for this write", exc)
        doc[pair] = {"amount": amount, "ts": _now_mst().isoformat()}
        tmp = TAKE_PAYOUTS_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
        os.replace(tmp, TAKE_PAYOUTS_JSON)
    except Exception as exc:
        logger.warning("take payout save failed: %s", exc)


# ── HER WORDS LOG (user rule, Aug 30: "put her notifications on the console
# so I can see them if my volume is off") — every line Zira speaks is
# appended to voice_log.jsonl; the console's light panel renders the feed.
# _speak_server is the ONE mouth: hooking here captures every spoken line,
# scanner-side AND console-queued.
VOICE_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_log.jsonl")
VOICE_LOG_MAX = 400  # lines kept on disk (raised for the discipline seam: the
                      # fired-popup ground truth — 150 lines could push a
                      # rotation's "Buy {asset}!" lines out before the rollover)


def _log_her_line(text: str):
    """Append a spoken line to the console-visible her-words log."""
    try:
        import json as _json
        clean = str(text).replace(VOICE_PREFIX, "").strip()[:300]
        if not clean:
            return
        entry = {"ts": _now_mst().isoformat(), "text": clean}
        lines = []
        if os.path.exists(VOICE_LOG_FILE):
            with open(VOICE_LOG_FILE, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
        lines.append(_json.dumps(entry, ensure_ascii=False))
        with open(VOICE_LOG_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-VOICE_LOG_MAX:]))
    except Exception as exc:
        logger.warning("voice log append failed (%s) — the entry is "
                       "lost to the log only", exc)


# ── THE SUPPRESSION LOG (user rule, Sep 4 — the POPCAT lesson: the funnel's
# suppression verdicts printed to the console window only, so a buried
# payer took three forensics scripts to name its gate). Every suppression
# on the popup path appends one line here — which gate, what numbers, at
# what time. The console's MASTER page renders the recent strip.
SUPPRESSION_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "popup_suppressions.jsonl")
SUPPRESSION_LOG_MAX = 1000  # lines kept on disk (raised for the discipline seam —
                            # a busy rotation can bury 40+ candidates; 400 didn't
                            # reliably span the window)


def _log_suppression(pair: str, gate: str, detail: str, **ctx):
    """Persist one popup-funnel suppression verdict — the record that
    answers 'who buried it?' in one line next time. ctx carries the
    numbers (quick, brain, floor, tide, rot) — anything not None rides
    along. Never raises: a broken log write must not block the funnel."""
    try:
        import json as _json
        entry = {"ts": _now_mst().isoformat(), "pair": pair,
                 "gate": gate, "detail": str(detail)[:220]}
        # carry contest_size (one source of truth) if the ctx has it
        if 'contest_size' not in entry and 'contest_size' in ctx:
            entry['contest_size'] = ctx['contest_size']
        for _k, _v in ctx.items():
            if _v is not None:
                entry[_k] = _v
        lines = []
        if os.path.exists(SUPPRESSION_LOG_FILE):
            with open(SUPPRESSION_LOG_FILE, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
        lines.append(_json.dumps(entry, ensure_ascii=False, default=str))
        # ATOMIC SWAP (Sep 11): truncate-and-write left a window where a
        # reader (the tide learner's fit, the weekly hindsight report)
        # saw an EMPTY ledger and silently fit on nothing. Write .tmp,
        # then os.replace — readers see old-complete or new-complete,
        # never empty.
        _tmp = SUPPRESSION_LOG_FILE + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-SUPPRESSION_LOG_MAX:]))
        os.replace(_tmp, SUPPRESSION_LOG_FILE)
    except Exception as exc:
        logger.debug("suppression log write failed: %s", exc)
    # ── THE HINDSIGHT SEAM (user spec, Sep 5): every suppression ALSO
    # lands in the signal-snapshot dataset with its fire-time feature
    # vector, so the scorer learns from what the funnel buried — not just
    # what it let through. Never raises; never blocks.
    try:
        _snap = _SNAPSHOT_CTX.get("pair") == pair and _SNAPSHOT_CTX.get("ctx")
        if _snap and (time.time() - float(_SNAPSHOT_CTX.get("started", 0) or 0)) < 600:
            _rec = dict(_SNAPSHOT_CTX["ctx"])
            _rec.update({"kind": "suppressed", "gate": gate,
                         "detail": str(detail)[:160]})
            for _k, _v in ctx.items():
                if _v is not None:
                    _rec[_k] = _v
            _log_signal_snapshot(_rec)
    except Exception as exc:
        # a suppressed candidate that never reaches the snapshot ledger
        # is invisible money-logic — say so
        logger.warning("suppression snapshot failed (%s) — this "
                       "suppression is absent from the snapshot ledger", exc)


# ── THE SIGNAL-SNAPSHOT DATASET (user spec, Sep 5: "Log every
# fired-and-entered and fired-and-suppressed signal with full feature
# snapshots"). One jsonl line per popup-path event — champion or mover,
# fired or buried — carrying the fire-time features (hindsight_scorer.
# fire_features off 5-min candles) + the verdict. The scorer's backfill
# joins outcomes onto these; extract_rules grades the features; the
# weekly gate report reads them all. Capped at 2000 lines.
SIGNAL_SNAPSHOTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "signal_snapshots.jsonl")
SIGNAL_SNAPSHOTS_MAX = 2000
# the popup cap (user spec: "Cap concurrent fresh entries"): no more than
# this many BUY popups per window — the advisory brain can fire often;
# the watch never floods. Live dial: popup_cap in console_state.json.
POPUP_CAP_WINDOW_MIN = 90.0
POPUP_CAP_DEFAULT = 3


def _log_signal_snapshot(rec: dict):
    """Append one snapshot record. Never raises."""
    try:
        import json as _json
        lines = []
        if os.path.exists(SIGNAL_SNAPSHOTS_FILE):
            with open(SIGNAL_SNAPSHOTS_FILE, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
        rec = dict(rec)
        rec.setdefault("ts", _now_mst().isoformat())
        rec.setdefault("contest_size", _contest_size())
        lines.append(_json.dumps(rec, ensure_ascii=False, default=str))
        # ATOMIC SWAP (Sep 11): same race as the suppression ledger —
        # .tmp then os.replace, readers never see a partial file.
        _tmp = SIGNAL_SNAPSHOTS_FILE + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-SIGNAL_SNAPSHOTS_MAX:]))
        os.replace(_tmp, SIGNAL_SNAPSHOTS_FILE)
    except Exception as exc:
        logger.debug("signal snapshot write failed: %s", exc)


# the CURRENT fire-time context (pair-keyed; set by _begin_fire_snapshot
# at the top of every popup-path evaluation, consumed by the suppression
# hook above and the fired-snapshot writes below)
_SNAPSHOT_CTX = {}


def _begin_fire_snapshot(exchange, pair: str, quick_conf, **extras) -> dict:
    """Compute + stash the fire-time feature vector for this candidate —
    ONE 5-min candle fetch, reused by every downstream verdict (fired or
    buried). Feature failure NEVER blocks the funnel: a broken read logs
    features=None and the gates proceed (the fail-closed rule lives on
    the DEEP SCAN, not on the feature math)."""
    ctx = {"pair": pair, "quick": quick_conf, "contest_size": _contest_size()}
    ctx.update(extras)
    feats = None
    try:
        from hindsight_scorer import fire_features
        rows = exchange.fetch_ohlcv(pair, timeframe="5m", limit=100)
        feats = fire_features(rows or [])
    except Exception as exc:
        logger.debug("fire-time features unavailable for %s: %s", pair, exc)
        ctx["features_error"] = True
    ctx["features"] = feats
    _SNAPSHOT_CTX.clear()
    _SNAPSHOT_CTX.update({"pair": pair, "started": time.time(), "ctx": ctx})
    return ctx


def _deep_read(exchange, pair, cfg):
    """The deep scan: the 1h x 200 strategy read behind every fire-time
    verdict. ONE implementation (Sep 7): the champion path's closure and
    the Sure Thing's pre-fire gate both call this — every popup is
    brain-read BEFORE it fires (the Sep 6 post-entry waiver retired at
    the user's call). Returns (signal, df) or (None, None). Never
    raises — the callers own the fail-closed decision."""
    try:
        import pandas as _pd
        from strategy import evaluate_strategy as _eval_deep, \
            enable_deep_analysis, disable_deep_analysis
        _ohl = exchange.fetch_ohlcv(pair, timeframe="1h", limit=200)
        if not _ohl or len(_ohl) < 30:
            return None, None
        _df = _pd.DataFrame(_ohl, columns=["timestamp", "open", "high",
                                           "low", "close", "volume"])
        _df["timestamp"] = _pd.to_datetime(_df["timestamp"], unit="ms")
        _df.set_index("timestamp", inplace=True)
        enable_deep_analysis()
        try:
            _sig = _eval_deep(_df, cfg)
        finally:
            disable_deep_analysis()
        return _sig, _df
    except Exception:
        return None, None


def _popup_cap() -> int:
    """RETIRED (Sep 9: the popup cap is not part of the design — kept per
    the don't-delete rule, no longer called by any fire path). Was: the
    popup cap dial — read LIVE from console_state.json (the
    sidebar writes it; no scanner restart), default 3, floor 1."""
    try:
        import json as _json
        from pathlib import Path as _P
        _cs = _json.loads((_P(__file__).resolve().parent / "console_state.json").read_text())
        return max(1, int(_cs.get("popup_cap") or POPUP_CAP_DEFAULT))
    except Exception:
        return POPUP_CAP_DEFAULT


# ── THE FLAT SIZING FACTOR (user rule, Aug 31: "collect the data
#    first" — the first stamped week is in, Sep 7): FLAT won 22%
#    (2W/7L of 9 real outcomes) vs RISING 57% (8W/6L of 14) — the
#    pre-registered discount frame met (≥15pt gap + negative FLAT avg),
#    the user picked 0.50×. FLAT trades still flow at half size (the
#    scorer keeps learning); RISING stays full. Re-read the record
#    weekly: diag_tidedata.py.
TIDE_FLAT_SIZE_FACTOR = 0.50



def _popup_cap_dollars() -> float:
    '''RETIRED (Sep 9: no money cap at any contest size — making more
    money is good; kept per the don't-delete rule, no longer called).
    Was: the popup cap's DOLLAR ceiling: the most fresh-entry money the contest
    can have open at once across all current popups. Size-adjusted (Sep 9):
      cap = max($20, contest_size x 0.50)
    so a $1k contest can't open 3 x $240 at once (the count cap would allow
    it, but the dollar cap stops at ~$500 total), while a $10k contest can
    hold up to $5,000 in fresh entries. The cap is checked at fire time: a
    candidate whose spend would push past the ceiling is suppressed as a
    'cap-dollar' gate (distinct from the count cap, which still applies).
    Fail-open to $20 on a broken read (one small trade still fine).'''
    try:
        _sz = _contest_size()
        if _sz <= 0:
            return 20.0
        return max(20.0, _sz * 0.50)
    except Exception:
        return 20.0

def _climb_strictness() -> float:
    """THE CLIMB-OUT DIAL (user rule, Sep 6): the strictness multiplier
    (0.5x-2.0x) scales every climb-out clearance multiplier. Read LIVE
    from console_state.json; default 1.0 (neutral)."""
    try:
        import json as _json
        from pathlib import Path as _P
        _cs = _json.loads((_P(__file__).resolve().parent / "console_state.json").read_text())
        return max(0.5, min(2.0, float(_cs.get("climb_strictness") or 1.0)))
    except Exception:
        return 1.0


def _climb_out_check(exchange, pair: str, entry: float, goal_pct: float,
                     spend: float, is_coil: bool = False,
                     coil_ceiling: float | None = None):
    """The scanner-side climb-out wrapper: reads the live context (tide,
    death-line proximity, balance, the strictness dial) and calls
    climb_out.climb_out_gate. Returns the gate dict; NEVER raises
    (a broken read fails OPEN — the death-line guard is the gate that
    never yields; climb-out protects quality, not existence)."""
    try:
        from climb_out import climb_out_gate
        _tide = "FLAT"
        try:
            _tp, _ts = _fleet_tide()
            _tide = _ts or "FLAT"
        except Exception as exc:
            logger.warning("fleet tide read failed (%s) — the CLIMB-OUT "
                           "gate prices this candidate as FLAT", exc)
        _near_death = False
        _balance = None
        try:
            import contest_account as _ca_co
            _acct = _ca_co.load_state()
            _balance = float(_acct["current_balance"])
            _buf = _balance - _ca_co.death_line(_acct)
            _near_death = _buf < 0.5 * 0.03 * float(_acct["start_balance"])
        except Exception as exc:
            # _near_death stays False = the DEATH GUARD disarms — the
            # riskiest fail-open in the gate; it must be visible
            logger.warning("account read failed (%s) — the climb-out "
                           "gate ships WITHOUT its near-death guard", exc)
        return climb_out_gate(exchange, pair, entry, goal_pct, spend,
                              is_coil=is_coil, coil_ceiling=coil_ceiling,
                              tide_state=_tide, near_death=_near_death,
                              balance=_balance,
                              strictness=_climb_strictness())
    except Exception as exc:
        return {"ok": True, "fail_gate": None, "fail_reason": "",
                "escape_line": "", "output_block":
                    f"CLIMB-OUT: unavailable ({exc}) — fail-open\n",
                "features": {}, "adjustments": [], "degraded": True}


def _climb_features_stamped(climb, tide_state=None, tide_pct=None):
    """The climb features + the tide stamp (Sep 7): the gate report can
    split clearance / ER / floor by tide — the FLAT-vs-RISING record for
    the GATES, not just the sizing factor. Never raises; a None climb
    passes through (no climb check = nothing to stamp)."""
    if not climb:
        return climb
    try:
        f = dict(climb)
        f["climb_tide"] = str(tide_state or "?")
        f["climb_tide_pct"] = (round(float(tide_pct), 1)
                               if tide_pct is not None else None)
        return f
    except Exception:
        return climb


def _recent_fired_popups(window_min=POPUP_CAP_WINDOW_MIN) -> int:
    """How many BUY popups fired inside the cap window (reads the
    snapshot log — the same file the scorer learns from)."""
    try:
        import json as _json
        from datetime import datetime as _dt, timedelta as _td
        if not os.path.exists(SIGNAL_SNAPSHOTS_FILE):
            return 0
        _cutoff = _now_mst() - _td(minutes=window_min)
        _n = 0
        with open(SIGNAL_SNAPSHOTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = _json.loads(line)
                except Exception:
                    continue
                if r.get("kind") != "fired":
                    continue
                try:
                    if _dt.fromisoformat(r.get("ts")) >= _cutoff:
                        _n += 1
                except Exception:
                    continue
        return _n
    except Exception:
        return 0


# ── THE DANGER-ZONE PANEL (user rule, Sep 6: "updated thin book rules:
#    when you're already in a trade the effective buying power shrinks,
#    so any new buy popup needs to show different danger-zone metrics
#    than when you're flat... We already have some of this so just
#    modify it and build only what we need") — the THIN-BOOK OPTIMIZED
#    popup panel. ONE builder, appended to every BUY popup (champion +
#    mover + sure-thing): balance overview (total/available/committed),
#    position status (the "am I already wet?" read), auto-adjusted
#    purchase power (full 25% flat vs reduced in-trade vs effective),
#    danger-zone metrics (drawdown, distance, this trade's impact),
#    the thin-book indicator (signals in the last 10 min + cluster
#    risk), the turnaround profile (the $10/10-minute model), and the
#    final decision snapshot. INFORMS, never gates — the gates stand.
def _recent_fired_popups_dollars(window_min=POPUP_CAP_WINDOW_MIN) -> float:
    '''Total spend (USD) committed by BUY popups that fired inside the cap
    window (reads the snapshot log). Used by the size-adjusted dollar cap: a
    new candidate whose spend + already-committed > the dollar ceiling is
    suppressed as 'cap-dollar'. Fail-open to 0.0 on any error.'''
    try:
        import json as _json
        from datetime import datetime as _dt, timedelta as _td
        if not os.path.exists(SIGNAL_SNAPSHOTS_FILE):
            return 0.0
        _cutoff = _now_mst() - _td(minutes=window_min)
        _tot = 0.0
        with open(SIGNAL_SNAPSHOTS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    r = _json.loads(line)
                except Exception:
                    continue
                if r.get('kind') != 'fired':
                    continue
                _spend = float(r.get('spend') or 0.0)
                if _spend <= 0:
                    continue
                try:
                    if _dt.fromisoformat(r.get('ts')) >= _cutoff:
                        _tot += _spend
                except Exception:
                    continue
        return _tot
    except Exception:
        return 0.0

def _recent_signal_events(window_min: float = 10.0) -> int:
    """Signals (fired + buried) inside the window — the cluster read."""
    try:
        import json as _json
        from datetime import datetime as _dt, timedelta as _td
        if not os.path.exists(SIGNAL_SNAPSHOTS_FILE):
            return 0
        _cutoff = _now_mst() - _td(minutes=window_min)
        _n = 0
        with open(SIGNAL_SNAPSHOTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = _json.loads(line)
                except Exception:
                    continue
                try:
                    if _dt.fromisoformat(r.get("ts")) >= _cutoff:
                        _n += 1
                except Exception:
                    continue
        return _n
    except Exception:
        return 0


def _danger_zone_panel(exchange, pair: str, spend: float, goal_pct: float,
                       win_min: float, mult: float) -> str:
    """The thin-book-optimized danger-zone block for a BUY popup.
    Reads the live contest account + the watch + the book; never raises
    (a broken read yields an honest one-liner, the popup still fires)."""
    try:
        import contest_account as _ca
        _acct = _ca.load_state()
        _bal = float(_acct["current_balance"])
        _start = float(_acct["start_balance"])
        _line = _ca.death_line(_acct)
        _buf = _bal - _line
        _pace = _ca.daily_pace_usd(_acct)
        _wdd = _ca.window_drawdown_pct(pair)

        # 1-2. the watch: committed capital + unrealized P/L
        _committed = 0.0
        _pos_line = "Position: FLAT"
        try:
            _w = _load_contest_watch()
            for _p, _e in (_w or {}).items():
                _sz = float(_e.get("size") or 0.0)
                _committed += _sz
            if _w:
                _upl = 0.0
                for _p, _e in _w.items():
                    try:
                        _t = float((exchange.fetch_ticker(_p) or {}).get("last") or 0.0)
                        _entry = float(_e.get("entry") or 0.0)
                        if _t > 0 and _entry > 0:
                            _upl += (_t - _entry) * (float(_e.get("size") or 0.0) / _entry)
                    except Exception as exc:
                        logger.debug("open-position ticker read failed "
                                      "(%s) — the unrealized line "
                                      "under-reports", exc)
                _exp = "LOW" if _committed < 0.10 * _bal else \
                       "MEDIUM" if _committed < 0.50 * _bal else "HIGH"
                _pos_line = (f"Position: {len(_w)} open · ${_committed:,.0f} "
                             f"(unrealized {'+' if _upl >= 0 else ''}${_upl:,.0f}) "
                             f"· exposure {_exp}")
        except Exception as exc:
            # the position/exposure line is console color; the availability
            # math below still runs on the plain balance
            logger.debug("position line read failed (%s) — availability "
                         "math unaffected", exc)
        _avail = max(0.0, _bal - _committed)
        _pct_comm = (_committed / _bal * 100.0) if _bal > 0 else 0.0

        # 3. purchase power: full 25% flat vs reduced in-trade
        _full25 = 0.25 * _bal
        _red25 = 0.25 * _avail
        _carry = None
        try:
            from execution_tiers import measure_book
            _tob = float((measure_book(exchange, pair) or {}).get("ask_tob") or 0.0)
            if _tob > 0:
                _carry = _tob * 0.20
        except Exception:
            _carry = None
        _eff = min(spend, _red25 if _committed > 0 else _full25)
        if _carry is not None:
            _eff = min(_eff, _carry)

        # 4. danger zone + this trade's impact
        _dd_pct = max(0.0, (_start - _bal) / _start * 100.0) if _start > 0 else 0.0
        _dist_pct = (_buf / _start * 100.0) if _start > 0 else 0.0
        _goal_gain = spend * goal_pct / 100.0
        _worst = spend * _wdd / 100.0
        if _worst >= 0.5 * _buf:
            _impact = "INCREASES DANGER"
        elif _goal_gain >= 0.25 * _pace:
            _impact = "HELPS RECOVERY"
        else:
            _impact = "NEUTRAL"

        # 5. thin book + cluster
        _sig10 = _recent_signal_events(10.0)
        if _carry is None:
            _book = "UNREADABLE"
        elif _carry >= _bal:
            _book = "THICK"
        elif _carry >= 0.5 * _bal:
            _book = "NORMAL"
        else:
            _book = "THIN"
        _cluster = "HIGH" if (_sig10 >= 2 and _book == "THIN") else \
                   "MEDIUM" if (_sig10 >= 2 or _book == "THIN") else "LOW"

        # 6. turnaround profile ($10 / 10-minute model)
        _per10 = 10.0 * goal_pct / 100.0
        if win_min <= 60 or goal_pct <= 1.2:
            _ttype = "QUICK SCALP"
        elif win_min <= 120:
            _ttype = "MEDIUM SWING"
        else:
            _ttype = "SLOW RECOVERY"
        if _book == "THIN":
            _ttype += " (⚠ THIN BOOK)"

        # 7. the final decision snapshot
        _risk = "HIGH" if (_worst >= 0.5 * _buf or mult < 0.5 or _cluster == "HIGH") else \
                "MEDIUM" if (mult < 1.0 or _book == "THIN") else "LOW"
        _recov = "STRONG" if _goal_gain >= 0.5 * _pace else \
                 "MODERATE" if _goal_gain >= 0.25 * _pace else "WEAK"
        _action = "AVOID (danger zone proximity)" if _worst >= 0.6 * _buf else \
                  "AGGRESSIVE ENTRY" if (_risk == "LOW" and _recov == "STRONG") else \
                  "STANDARD ENTRY"

        return (
            f"{'─' * 30}\n"
            f"DANGER ZONE — {'FLAT' if _committed <= 0 else 'IN TRADE'}\n"
            f"  Balance: ${_bal:,.0f} total · ${_avail:,.0f} available · "
            f"{_pct_comm:.0f}% committed\n"
            f"  {_pos_line}\n"
            f"  Buying power: full 25% ${_full25:,.0f} · "
            f"reduced ${_red25:,.0f} · effective ${_eff:,.0f}\n"
            f"  Drawdown: {_dd_pct:.1f}% · distance to line: "
            f"{_dist_pct:.1f}% (${_buf:,.0f})\n"
            f"  This trade: {_impact} (goal +${_goal_gain:,.0f} vs "
            f"worst −${_worst:,.0f})\n"
            f"  Book: {_book}{f' (${_carry:,.0f} carries)' if _carry else ''} · "
            f"signals last 10 min: {_sig10} · cluster {_cluster}\n"
            f"  Turnaround: ~{win_min:.0f} min · $10 unit pays "
            f"${_per10:.2f} · {_ttype}\n"
            f"  RISK {_risk} · recovery {_recov} → {_action}\n")
    except Exception as exc:
        return f"{'─' * 30}\nDANGER ZONE: metrics unavailable ({exc})\n"


def _speak_server(text: str, rate: int = 2):
    """Zira out loud from the SCANNER process — runs whether or not any
    browser is open. Same amplified speak script the console uses.
    Every line ALSO goes to the phone as a push notification (ntfy) with
    HER ACTUAL AMPLIFIED VOICE ATTACHED — tap play on the phone, it's
    Zira, not the phone's substitute voice. One voice, three deliveries:
    PC speakers, browser captions, phone audio push."""
    import subprocess
    _base = os.path.dirname(os.path.abspath(__file__))
    _wav_path = os.path.join(_base, "speech_queue", "last_voice.wav")
    # her-words log: every spoken line is readable on the console
    _log_her_line(text)
    try:
        os.makedirs(os.path.dirname(_wav_path), exist_ok=True)
        script = os.path.join(_base, "speak_loud.ps1")
        if not os.path.exists(script):
            return
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", script, "-Text", VOICE_PREFIX + text, "-Rate", str(rate),
                          "-OutFile", _wav_path],
                         creationflags=0x08000000)
    except Exception as exc:
        logger.debug("speak failed: %s", exc)
    # phone push: BUY NOW / SELL NOW priority alerts only (user rule,
    # Aug 30) — everything else stays on PC voice + the console panel
    _push_to_phone(text, wav_path=_wav_path)


def _push_topic() -> str | None:
    """Private ntfy topic (from push_topic.txt next to this file)."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_topic.txt")
        return open(p, "r", encoding="utf-8").read().strip() or None
    except Exception:
        return None


# phone-push throttle state: identical texts (GET OUT repeats every 60s
# until SOLD) push at most every 5 minutes so one stuck exit can't burn
# the ntfy.sh quota either.
_push_state = {"text": "", "ts": 0.0}


def _push_to_phone(text: str, wav_path: str | None = None):
    """BUY NOW / GET OUT NOW only on the phone (user rule, Aug 30: 'Just
    have the ntfy.sh do the buy now and sell now priority alerts only';
    tightened Aug 31: ".ntfy doesn't need the 'take it' alerts. Just
    'buy now' and 'Get out now'"). Buy calls push HIGH; GET OUT / SELL /
    STOPPED OUT push URGENT. TAKE IT does NOT push — the take-profit
    nudges repeat every minute during a run (Aug 31: the ARB run pushed
    ~8 of them) and there's nothing to act on that the light + her voice
    aren't already saying. Everything else stays on the PC voice and the
    console's HER WORDS panel. This also keeps us far under ntfy.sh's
    ~60/hour rate limit (Aug 30 incident: every-minute position pushes
    burned the quota -> 429s -> the phone went silent)."""
    topic = _push_topic()
    if not topic:
        return
    try:
        import urllib.request, urllib.parse
        _t = str(text).lower().strip()
        is_sell = any(k in _t for k in ("get out", "sell ", "stopped out"))
        is_buy = (not is_sell) and ("buy " in _t or "new pick" in _t or _t.startswith("go!"))
        if not (is_buy or is_sell):
            return  # not a BUY NOW / GET OUT NOW alert — don't push
        # same text (a repeating GET OUT) pushes at most every 5 minutes
        now = time.time()
        if _t == _push_state["text"] and (now - _push_state["ts"]) < 300:
            return
        _push_state["text"] = _t
        _push_state["ts"] = now
        prio = "urgent" if is_sell else "high"
        req = urllib.request.Request(
            f"https://ntfy.sh/{urllib.parse.quote(topic)}",
            data=str(text).encode("utf-8"),
            headers={"Title": "Kraken Scout", "Priority": prio,
                     "Tags": "rotating_light" if prio == "urgent" else "chart_with_upwards_trend"})
    except (OSError, ValueError):
        pass  # push is best-effort — the PC voice already spoke


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


def auto_track_popup(pair: str, entry: float, size: float, goal_pct: float,
                     window_min: float, why: str, source: str) -> bool:
    """THE POPUP IS THE CONTRACT (Sep 21): every popup that fires —
    champion, Sure Thing, mover, DCA — auto-tracks at fire time. One
    writer for every route: never displaces an entry the user already
    has, never resurrects a row the machine itself closed (the GET OUT
    auto-close removes it — a later fire re-adds fresh)."""
    import json
    try:
        w = _load_contest_watch()
        if pair in w:
            return False
        w[pair] = {"entry": float(entry or 0.0),
                   "high": float(entry or 0.0),
                   "added": _now_mst().isoformat(),
                   "size": float(size or 0.0),
                   "goal_pct": float(goal_pct or 0.0),
                   "window_min": float(window_min or 0.0),
                   "why": [why],
                   "source": source}
        _save_contest_watch(w)
        logger.info("AUTO-TRACK: %s watched at fire time (entry $%.4f, "
                    "goal %.1f%%, window %.0fm, %s)",
                    pair.split("/")[0], float(entry or 0.0),
                    float(goal_pct or 0.0), float(window_min or 0.0), source)
        return True
    except Exception as exc:
        logger.warning("fire-time auto-track failed for %s (%s) — "
                       "watch it by hand from the popup",
                       pair.split("/")[0], exc)
        return False


def _load_scanner_state() -> dict:
    """Persistent scanner state (survives restarts) — currently: the morning
    flatten date, so a mid-day restart never re-flattens patient positions."""
    import json
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scanner_state.json")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_scanner_state(state: dict) -> None:
    import json
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scanner_state.json")
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, p)
    except Exception as exc:
        logger.warning("scanner state save failed: %s", exc)


def _voice_enabled() -> bool:
    """Console toggle (console_state.json voice_on) — shared file."""
    import json
    try:
        with open(CONSOLE_STATE_JSON, "r", encoding="utf-8") as fh:
            return bool(json.load(fh).get("voice_on", True))
    except Exception:
        return True


def _hours_to_goal(pair: str, entry, tp):
    """Estimated hours to reach TP at this rotation's pace (engine data).
    Display information — the RESOLVE GATE uses _hours_to_scalp instead."""
    try:
        from volatility_engine import load_rotation_thresholds, ROTATIONS
        doc = load_rotation_thresholds(max_age_s=300)
        if not doc:
            return None
        n = _now_mst()
        rot = next((k for s, e, k, *_ in ROTATIONS if s <= n.hour < e), None)
        if rot is None:
            return None
        sched = "weekend" if n.weekday() >= 5 else "weekday"
        cell = (doc.get("universe", {}).get(pair, {}).get(sched, {}).get(rot)
                or doc.get("median", {}).get(sched, {}).get(rot))
        avg = float(cell.get("avg_move") or 0) if cell else 0
        if avg <= 0 or not entry or not tp or tp <= entry:
            return None
        dist = (tp - entry) / entry * 100
        return (dist / avg) * 4.0
    except Exception:
        return None


def _hours_to_scalp(pair: str, entry, *, now):
    """Hours to reach THE USER'S TARGET: +$20 on $5,000 = +0.4% at this
    coin's rotation pace. This is what 'resolve within a few hours' means
    in their system — they scalp $20s, they don't hold to TP. The old
    TP-based gate blocked every 92% breakout (far TP, immediate scalp).
    (Sep 13) `now` is REQUIRED (fresh-stamp standard): the clock picks
    the rotation and weekday schedule, so a cached plate must pass its
    own (bucketed) time — zero wall-clock reads in this body, or the
    sweep flags the freeze. Live callers pass datetime.now(MST)."""
    try:
        from volatility_engine import load_rotation_thresholds, ROTATIONS
        doc = load_rotation_thresholds(max_age_s=300)
        if not doc or not entry:
            return None
        n = now
        rot = next((k for s, e, k, *_ in ROTATIONS if s <= n.hour < e), None)
        if rot is None:
            return None
        sched = "weekend" if n.weekday() >= 5 else "weekday"
        cell = (doc.get("universe", {}).get(pair, {}).get(sched, {}).get(rot)
                or doc.get("median", {}).get(sched, {}).get(rot))
        avg = float(cell.get("avg_move") or 0) if cell else 0
        if avg <= 0:
            return None
        return (SCALP_TARGET_PCT / avg) * 4.0
    except Exception:
        return None


def _max_resolve_hours(*, now) -> float:
    """How long a contest trade may take to hit its goal:
    base 3h, flexible by the rotation's real pace (engine data —
    strong rotations earn patience), NEVER past midnight (never the
    next day). Uses the rotation × weekday/weekend schedule.
    (Sep 13) `now` is REQUIRED (fresh-stamp standard): rotation and
    weekday selection ride the caller's clock — zero wall-clock reads
    in this body."""
    n = now
    hours_to_midnight = (24 - n.hour) - n.minute / 60.0
    pace_cap = 4.0
    try:
        from volatility_engine import load_rotation_thresholds, ROTATIONS
        doc = load_rotation_thresholds(max_age_s=300)
        rot = next((k for s, e, k, *_ in ROTATIONS if s <= n.hour < e), None)
        sched = "weekend" if n.weekday() >= 5 else "weekday"
        cell = (doc or {}).get("median", {}).get(sched, {}).get(rot) or {}
        avg = float(cell.get("avg_move") or 0)
        if avg >= 1.0:
            pace_cap = 6.0   # strong rotation — a few extra hours of patience
        elif avg >= 0.8:
            pace_cap = 5.0
        else:
            pace_cap = 4.0   # slow market — demand speed
    except Exception as exc:
        logger.warning("rotation pace read failed (%s) — the payout "
                       "clock ships at the default cap", exc)
    return min(pace_cap, hours_to_midnight)


# GET OUT persistence (Aug 30): per-pair timestamp of the last spoken exit
# call — the voice repeats every 60s while the exit condition holds, until
# the user taps SOLD. Never auto-drop a tracked position.
_last_exit_voice: dict[str, float] = {}
_last_lock_floor_usd = 0.0   # RETIRED (Sep 5): the daily lock's voice pacing — kept per don't-delete, nothing writes it now


def contest_light(exchange, cfg):
    """One light cycle on every contest position: 4% profit guard + 50% sell
    bar + scalp watch. Speaks the dollars every minute. A GET OUT call
    PERSISTS — the position stays tracked and the call repeats every ~60s
    until the user taps SOLD (Aug 30: auto-dropping after one alert was
    losing exits — 'I miss my exit')."""
    import pandas as pd
    from strategy import evaluate_strategy

    global _last_trade_done
    watch = _load_contest_watch()
    if not watch:
        return
    voice = _voice_enabled()

    # ── THE TIDE: fleet-wide warning (user rule, Aug 26) — when the tide
    # goes OUT, every ship sinks together; that warning applies to ALL
    # watched positions at once, once per transition.
    global _tide_last_state
    try:
        _tp, _ts = _fleet_tide()
        if _ts:
            if _ts == "OUT" and _tide_last_state != "OUT" and voice:
                _speak_server("Warning! The tide is going out! All ships sink together! "
                              "Consider getting out!", rate=1)
                logger.info("🌊 TIDE OUT: fleet breadth %.0f%% — all-ships warning issued", _tp)
            _tide_last_state = _ts
    except Exception as exc:
        # the tide warning is the fleet's early alarm — a dead read
        # must be visible, not just a silent skip of the announcement
        logger.warning("fleet tide read failed (%s) — the all-ships "
                       "warning may not fire this cycle", exc)

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

        # ── 1. profit guard: trail off the peak, RATCHETED by run size ──
        # (user rule, Aug 26: nine twenties gave back $150 before a flat
        # trail called the exit — the ratchet tightens as the run grows)
        should_exit = False
        reason = ""
        _trail = _ratchet_trail_pct(entry, high, size)
        if high > entry and live <= high * (1 - _trail / 100):
            _peak_usd = (high / entry - 1.0) * size
            should_exit = True
            reason = (f"PROFIT GUARD FIRED — dropped {(high - live) / high * 100:.1f}% from the peak "
                      f"(trail ratcheted to {_trail:.1f}% — the run reached +${_peak_usd:.0f})")

        # ── 1b. THE HARD STOP — tier-aware (user rule, Sep 1: "everything
        # should auto adjust when the contest is selected"): the old fixed
        # -$100 was calibrated to the 5k tier — on the 1k tier ($30 death
        # line) it would fire AFTER the account was already dead. Now the
        # stop = half the remaining death-line buffer (Sep 5: the daily
        # lock removed — the platform line is the only floor) — and it
        # TIGHTENS automatically as the buffer shrinks.
        if not should_exit and entry > 0 and live > 0:
            _usd_now = (live / entry - 1.0) * size
            try:
                import contest_account as _ca_l
                _acct_l = _ca_l.load_state()
                _stop_usd = max(_ca_l.buffer(_acct_l) * 0.5, 5.0)
            except Exception:
                _stop_usd = max((float(cfg.challenge_balance) * 0.03) * 0.5, 5.0)
            if _usd_now <= -_stop_usd:
                should_exit = True
                reason = (f"STOPPED OUT — down ${abs(_usd_now):.0f} (the stop is "
                          f"-${_stop_usd:.0f} — half the platform death-line buffer). "
                          f"The trade failed — get out, wait for the next one.")

        # ── 2. strategy sell bar (contest profile: 50%) ────────────────
        if not should_exit:
            try:
                ohlcv = exchange.fetch_ohlcv(pair, timeframe="1h", limit=200)
                if ohlcv and len(ohlcv) >= 30:
                    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                    df.set_index("timestamp", inplace=True)
                    # REAL-TIME (Sep 3): seed the in-progress candle with the
                    # live price so the sell bar reacts to the move happening
                    # now — the voice and the console must agree.
                    _idx = df.index[-1]
                    df.loc[_idx, "close"] = live
                    df.loc[_idx, "high"] = max(float(df.loc[_idx, "high"]), live)
                    df.loc[_idx, "low"] = min(float(df.loc[_idx, "low"]), live)
                    sig = evaluate_strategy(df, cfg)
                    if sig.action == "sell" and sig.confidence >= CONTEST_SELL_BAR:
                        should_exit = True
                        reason = f"Trend broke — {sig.confidence:.0f}% bearish, past the {CONTEST_SELL_BAR}% bar"
                    elif any("exhaustion" in r.lower() for r in sig.reasons) and sig.action != "buy":
                        should_exit = True
                        reason = "Climax candle — this move looks done"
            except Exception as exc:
                # the strategy's say went unread this cycle — the exit
                # decision falls to the remaining gates; visible, so a
                # stuck hold is explainable
                logger.warning("exit-strategy read failed for %s (%s) — "
                               "this cycle exits on the other gates only", pair, exc)

        if should_exit:
            exited_pairs.append(pair)
            # GET OUT PERSISTS (user rule, Aug 30): repeat the call every
            # ~60s while the exit condition holds — one shout that drops
            # the tracking is a missed exit. SOLD is the only way out.
            _now_ts = time.time()
            if voice and (_now_ts - _last_exit_voice.get(pair, 0)) >= 60:
                _last_exit_voice[pair] = _now_ts
                # voice-first (user rule, Aug 27: the voice IS the contest —
                # the user is on the phone, not at the screen): every call
                # carries its WHY, spoken
                _why = ""
                _r = reason.lower()
                if "profit guard" in _r:
                    _why = "It gave back too much from the top!"
                elif "stopped out" in _r:
                    _why = "Down a hundred dollars — the trade failed!"
                elif "trend broke" in _r:
                    _why = "The trend turned against you!"
                elif "climax" in _r:
                    _why = "The move is done!"
                elif "exhaustion" in _r or "steam" in _r:
                    _why = "It's out of steam!"
                _speak_server(f"Get out now! Sell {asset}! {_why}"
                              f"Give it about an hour to base — I'll call the next one!", rate=1)
            # ── AUTO-CLOSE AT FIRE TIME (Sep 21, user rule: "after the
            # 'Get out Now' fires a popup, it closes the virtual trade and
            # counts the PnL so the brain can learn from the results every
            # time — no more user interference"): the exit call IS the
            # close. The watch row is removed here (the grade below
            # remembers the trade for the learner); the SOLD button stays
            # as the manual override for "the watch didn't close it" and
            # for early profit-takes with re-entry. Grade = the same
            # arithmetic the SOLD path uses (entry/size/live, 0.08% RT
            # cost, own-contest window), journaled to contest_trades.jsonl
            # — the ledger the discipline learner and the hindsight
            # studies read.
            _closed_note = ""
            try:
                import json as _jx
                import contest_account as _ca_x
                _entry_x = float(w.get("entry") or 0.0)
                _size_x = float(w.get("size") or 0.0)
                _live_x = float(live or 0.0)
                if _entry_x > 0 and _size_x > 0 and _live_x > 0:
                    _gross, _cost, _net = _ca_x.pnl_net_usd(
                        _entry_x, _live_x, _size_x)
                    _applied, _new_bal, _note = _ca_x.apply_pnl(
                        _net, w.get("contest_id"),
                        dedupe_key=f"{pair}|{w.get('added')}|getout")
                    _held_min = ""
                    try:
                        _added = w.get("added")
                        if _added:
                            _held_min = f"{(_now_mst() - datetime.fromisoformat(_added)).total_seconds() / 60.0:.0f}"
                    except (TypeError, ValueError):
                        pass
                    with open("contest_trades.jsonl", "a", encoding="utf-8") as _fx:
                        _fx.write(_jx.dumps({
                            "sold_at": _now_mst().isoformat(),
                            "pair": pair, "entry": _entry_x, "exit": _live_x,
                            "size": _size_x,
                            "pnl_gross": round(_gross, 2),
                            "cost_rt": round(_cost, 2),
                            "pnl_net": round(_net, 2),
                            "balance_after": round(_new_bal, 2),
                            "applied": bool(_applied),
                            "note": ("" if _applied else _note),
                            "goal_pct": w.get("goal_pct"),
                            "held_min": _held_min,
                            "closed_by": "getout",
                            "exit_reason": reason[:200],
                        }) + "\n")
                    _sign_x = "+" if _net >= 0 else "-"
                    _closed_note = (f"Closed at fire time — {_sign_x}${abs(_net):,.2f} net "
                                    f"(0.08% RT cost incl.)")
                    logger.info("GET OUT CLOSED: %s — %s%s", pair, _closed_note,
                                "" if _applied else f" (balance NOT applied: {_note})")
                    if _net > 0:
                        # the same profit re-arm the SOLD path grants — a
                        # collected profit can be called again next setup
                        try:
                            rotation_rearm_on_profit(asset)
                        except Exception:
                            pass
                else:
                    _closed_note = "skipped — missing entry/size/live"
                    logger.warning("GET OUT close skipped for %s: entry=%s size=%s live=%s",
                                   pair, _entry_x, _size_x, _live_x)
            except Exception as exc:
                _closed_note = "close failed — watch row kept for SOLD"
                logger.warning("GET OUT auto-close failed for %s (%s) — "
                               "the SOLD button remains the way out", pair, exc)
            watch.pop(pair, None)
            _save_contest_watch(watch)
            logger.info("🚨 CONTEST LIGHT: SELL %s — %s · %s",
                        pair, reason, _closed_note or "watch row removed")
            continue

        # exit condition cleared (user held through it and it recovered) —
        # re-arm the voice so a future exit shouts immediately
        _last_exit_voice.pop(pair, None)

        # ── 3. THE GOAL CALL (the rebuild, Sep 1: "scalping is over with,
        # count the 20's is gone — only solid trends... percentage based
        # goals"). Every watch entry carries its goal — the engine's
        # expected move at entry time (1-3%, the market analysis). She
        # calls the goal reached, and each doubling beyond it. The $20
        # level-counting is retired with the scalp game.
        pct = (live / entry - 1) * 100
        usd = pct / 100.0 * size
        _goal = float(w.get("goal_pct") or 0) or _window_goal_pct(pair)
        if "goal_pct" not in w:
            w["goal_pct"] = _goal
            w["window_min"] = max(60.0, min(180.0, _goal * 60.0))
            dirty = True
        _glevel = int(pct / _goal) if (_goal > 0 and pct > 0) else 0
        if _goal > 0 and pct < _goal * 0.25:
            _take_level.pop(pair, None)  # back near entry — fresh count
        elif _glevel > _take_level.get(pair, 0):
            _take_level[pair] = _glevel
            if voice:
                if _glevel == 1:
                    _speak_server(f"Goal reached! {asset} up {pct:.1f} percent "
                                  f"— take the profit!")
                else:
                    _speak_server(f"{_glevel} times the goal! {asset} "
                                  f"up {pct:.1f} percent!")
            logger.info("🎯 GOAL: %s +%.1f%% (goal %.1f%%, x%d)",
                        pair, pct, _goal, _glevel)
            # mature move = the QC's just-paid guard (re-entry blocks)
            _save_take_payout(pair, round(usd))

        # ── 4. GIVEBACK WARNING — tier-aware (Sep 1: "everything should
        # auto adjust when the contest is selected"): the old fixed $50
        # was 10% of a 1k-tier entry (deafening) and 1.5% of a 10k-tier
        # entry (a whisper). Now the threshold scales with the position:
        # 1.5% of the entry size — the same loudness at every tier.
        peak_usd = (high / entry - 1) * size if high > entry else 0.0
        _gb_thr = max(size * GIVEBACK_WARN_PCT_OF_SIZE / 100.0, 5.0)
        if usd > 0 and peak_usd - usd >= _gb_thr:
            step = int((peak_usd - usd) // _gb_thr)
            if step > _giveback_level.get(pair, 0):
                _giveback_level[pair] = step
                _amt = int(step * _gb_thr)
                if voice:
                    _speak_server(f"Warning! {asset} gave back {_amt} dollars! Take it or ride!", rate=1)
                logger.info("⚠️ GIVEBACK: %s -$%d from peak +$%.0f (now +$%.0f)",
                            pair, _amt, peak_usd, usd)
        elif peak_usd - usd < _gb_thr / 2:
            _giveback_level.pop(pair, None)  # recovered near the peak — re-arm

        # ── 5. LOSS WARNING — tier-aware (same scaling): the threshold is
        # 1.5% of the entry size, not a fixed $50. A slow bleed below
        # entry still gets called out (the PENGU lesson) — at every tier.
        _loss_thr = max(size * LOSS_WARN_PCT_OF_SIZE / 100.0, 5.0)
        if usd < 0 and usd <= -_loss_thr:
            step = int(-usd // _loss_thr)
            if step > _loss_level.get(pair, 0):
                _loss_level[pair] = step
                _amt = int(step * _loss_thr)
                if voice:
                    _speak_server(f"Warning! {asset} down {_amt} dollars! Get out or hold!", rate=1)
                logger.info("⚠️ LOSS WARNING: %s -$%d", pair, _amt)
        elif usd > -_loss_thr / 2:
            _loss_level.pop(pair, None)  # recovered — re-arm

        # ── 6. THE CLOCK (the goal window: 1-3h, set with the goal at
        # entry — the $20-pace clock retired with the scalp game).
        # Near the goal or short on time -> "stay close".
        try:
            _win = float(w.get("window_min") or 0)
            _out_by = None
            if w.get("added") and _win > 0:
                _added = datetime.fromisoformat(w["added"])
                _out_by = _added + timedelta(minutes=_win)
            if _out_by:
                _rem = (_out_by - datetime.now(_out_by.tzinfo)).total_seconds() / 60.0
                _to_goal = (_goal - pct) if pct < _goal else 0.0
                _near = (_to_goal <= _goal * 0.15) or (_rem <= 15.0)
                _tail = f", about {max(_rem, 0):.0f} minutes left"
                _tail += ", stay close" if _near else ", go walk around"
            else:
                _tail = ""
        except Exception:
            _tail = ""

        # ── THE DIRECTION VOICE (the VIRTUAL/TRUMP/RENDER lesson, Sep 5:
        # "a SELL dressed up as a BUY — it thinks the goal is to go
        # down?"): LONG-ONLY — a down move is NEVER progress toward the
        # goal. The up branch says progress; the down branch says the
        # honest drawdown + the full climb to recovery-and-goal, so a
        # losing position can never read like the plan working.
        if usd >= 0:
            voice_updates.append(f"{asset}, up {abs(pct):.1f} percent "
                                 f"toward the {_goal:.1f} percent goal{_tail}")
        else:
            _climb = _goal - pct  # recovery + goal: the honest remaining move
            voice_updates.append(f"{asset}, down {abs(pct):.1f} percent — "
                                 f"{_climb:.1f} percent to go before the "
                                 f"{_goal:.1f} percent goal{_tail}")

    if exited_pairs:
        # Aug 30 fix: GET OUT positions STAY in the watch — the user taps
        # SOLD to clear them. Auto-drop after one alert lost exits.
        if dirty:
            _save_contest_watch(watch)
        return

    if dirty:
        _save_contest_watch(watch)
    # all-good commentary at a ~60s cadence (TAKE IT fires anytime —
    # it's the scalp call and must never wait for the cadence)
    global _last_allgood_ts
    if voice and voice_updates and (time.time() - _last_allgood_ts) >= 60:
        _last_allgood_ts = time.time()
        _speak_server("All good. " + ". ".join(voice_updates) + ".")


def main():
    import ccxt
    import pandas as pd
    from config import TradingConfig
    from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
    from scanner import compute_position_sizing

    # ── PID lock: prevent duplicate scanners ──────────────────────────
    # If a healthy scanner is already running, exit immediately. Makes
    # duplicates impossible no matter what launched this instance.
    # NOTE: os.kill(pid, 0) is WRONG on Windows — it raises WinError 87 for
    # any process that isn't the caller's own child, so a scanner started
    # from another window/parent always looked "dead" and duplicates ran.
    # tasklist is the reliable cross-parent liveness check.
    import sys as _sys
    import subprocess as _sp

    def _pid_alive_python(pid: int) -> bool:
        try:
            result = _sp.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return "python" in result.stdout.lower()
        except Exception:
            return False

    lock_file = Path(__file__).parent / "scanner.lock"
    if lock_file.exists():
        try:
            old_pid = int(lock_file.read_text().strip())
            if _pid_alive_python(old_pid):
                print(f"Scanner already running (pid {old_pid}) — exiting to prevent duplicate.")
                _sys.exit(0)
        except ValueError:
            pass  # corrupt lock file — proceed
    lock_file.write_text(str(os.getpid()))

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

    # ── THE LIVE TIDE MEMORY DRIVER (Sep 10): the memory must learn
    # beyond the candle store. This thread re-scores recent popup-path
    # outcomes (fired AND buried — hindsight_scorer.backfill) every 30
    # min and blends them into the per-coin edge: fired popups that pay
    # strengthen it, buried payers restore it. Daemon — dies with the
    # scanner; a broken refresh never takes the funnel down.
    try:
        import tide_memory as _tmem
        _tmem_live_stop = threading.Event()
        threading.Thread(target=_tmem.live_driver_loop,
                         args=(_tmem_live_stop,), daemon=True,
                         name="tide-memory-live").start()
        logger.info("  Tide memory live driver: ON (popup outcomes "
                    "refresh every %d min)", int(_tmem.LIVE_REFRESH_S / 60))
    except Exception as _tmem_exc:
        logger.info("  Tide memory live driver: OFF (%s)", _tmem_exc)

    # ── THE DEEP STORE DRIVER (Sep 11): the 5m deep store must never go
    # stale — the seam QA only works while the fetch gap is small (the
    # Kraken OHLC endpoint serves the latest 720 candles per call). This
    # thread runs the live-Kraken 5m extender once a day; the 1h layer
    # rebuilds on top via build_deepstore (atomic swaps — readers never
    # see a half-written feather). Daemon; a broken pass never takes the
    # funnel down.
    try:
        import fetch_kraken_5m_history as _fk5m
        _fk5m_stop = threading.Event()
        threading.Thread(target=_fk5m.store_driver_loop,
                         args=(_fk5m_stop,), daemon=True,
                         name="deep-store-5m-driver").start()
        logger.info("  Deep store 5m driver: ON (live-Kraken extension "
                    "every %dh)", int(_fk5m.STORE_REFRESH_S // 3600))
    except Exception as _fk5m_exc:
        logger.info("  Deep store 5m driver: OFF (%s)", _fk5m_exc)

    # ── THE SURE THING AUTO-REFIT DRIVER (Sep 11): the score's weights
    # keep pace with the store — a monthly pass replays the deep store
    # with the real detector, ridge-fits fresh weights under the popup's
    # re-contracted 4h horizon, and SHIPS only when the holdout proves
    # the fresh fit beats legacy AND the incumbent. Degradation (the
    # holdout AUC dropping below the incumbent's or the forward-ranker
    # floor) keeps the incumbent and REPORTS — the report + the weights
    # ledger (_surethingautorefit.txt / sure_thing_autoweights.json)
    # carry the verdicts. A shipped swap is atomic (REFIT_W.update) —
    # the next poll prices with the new weights, no restart. Daemon;
    # a broken pass never takes the funnel down.
    try:
        import surething_autorefit as _star
        _star_stop = threading.Event()
        threading.Thread(target=_star.refit_driver_loop,
                         args=(_star_stop,), daemon=True,
                         name="surething-autorefit").start()
        logger.info("  Sure Thing auto-refit driver: ON (monthly refit, "
                    "first pass in %d min; holdout degradation reported)",
                    int(_star.FIRST_DELAY_S // 60))
    except Exception as _star_exc:
        logger.info("  Sure Thing auto-refit driver: OFF (%s)", _star_exc)

    # ── THE TIDE BAR WEEKLY (Sep 11): hindsight for the per-state
    # outcome bars — every week the ledgers' real candidates are graded
    # under BOTH the in-force bar and the quantile it replaced, per tide
    # state, in dollars (_tidebarweekly.txt, newest section first). The
    # in-sample ship gates prove the fit; this proves the fit PAID.
    # Daemon; a broken pass never takes the funnel down.
    try:
        import tidebar_weekly as _tbw
        _tbw_stop = threading.Event()
        threading.Thread(target=_tbw.weekly_driver_loop,
                         args=(_tbw_stop,), daemon=True,
                         name="tidebar-weekly").start()
        logger.info("  Tide bar weekly driver: ON (outcome-vs-quantile "
                    "hindsight — daily pass, 7-day grade window; "
                    "first pass in %d min)",
                    int(_tbw.FIRST_DELAY_S // 60))
        threading.Thread(target=_tbw.flipgrade_driver_loop,
                         args=(_tbw_stop,), daemon=True,
                         name="flip-grade-driver").start()
        logger.info("  Flip grade driver: ON (the bench machinery judged "
                    "on its own ledger — monthly, plus a fresh grade "
                    "shortly after each weekly tide-bar pass; first pass "
                    "in %d min)",
                    int(_tbw.FLIPGRADE_FIRST_DELAY_S // 60))
    except Exception as _tbw_exc:
        logger.info("  Tide bar weekly driver: OFF (%s)", _tbw_exc)

    # ── Notification ──────────────────────────────────────────────────
    def notify_big(title: str, message: str, spoken: str | None = None):
        """Desktop alert that STAYS until you read it — plus Zira says the
        WHOLE call out loud (user directive: she speaks why/when/how-long,
        not just the coin name — the bare 'BUY PENGU' morning taught us).
        The old balloon toast died in ~5 seconds; this popup holds the
        screen up to 3 minutes (or until you click OK)."""
        import subprocess
        safe_title = title.replace("'", "''").replace('"', '`"')
        safe_msg = message.replace("'", "''").replace('"', '`"')

        # 1) Zira announces it (amplified — heard across the room)
        try:
            if _voice_enabled():
                say = (spoken if spoken else title)
                say = "".join(ch for ch in say if 32 <= ord(ch) < 127).strip()
                say = say.replace("%", " percent")
                if say:
                    _speak_server(say)
        except Exception as exc:
            logger.warning("voice announce failed (%s) — the popup "
                           "surfaces silent", exc)

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

        # ── ROTATION MEMORY (user rule, Aug 30) ─────────────────────────
        # 1. LOCKOUT — RETIRED (user rule, Sep 10: "delete the rule"): the
        #    one-green-per-rotation cap is OFF; a coin can fire again the
        #    moment it passes every remaining gate. The call stays for the
        #    re-entry display + the stale-move exemption; nothing returns
        #    on it.
        _allowed, _lock_reason, _is_reentry, _fine = rotation_gate(asset)
        if not _allowed:  # dead since the Sep 10 retirement — kept honest
            logger.info("  ROTATION LOCK: %s %s — no alert, no bridge write", asset, _lock_reason)
            _log_suppression(pair, "rotation-lock",
                             f"{_lock_reason} (one green per asset per rotation)",
                             quick=float(signal.confidence),
                             rot=rotation_key_for_hour(_now_mst().hour))
            return
        # 2. YESTERDAY'S HOUR: this coin's same rotation window blew off and
        #    dumped on recent days → today's signal at this hour is a re-run
        #    of a shape that already paid. Demote (noise guard).
        try:
            _yctx = rotation_yesterday_context(pair)
        except Exception:
            _yctx = None
        if _yctx and _yctx.get("shape") == "blowoff":
            logger.info("  YESTERDAY BLOWOFF: %s — %s — no alert", asset, _yctx.get("detail", ""))
            _log_suppression(pair, "blowoff-yesterday",
                             f"this hour blew off and dumped on recent days — "
                             f"{_yctx.get('detail', '')}",
                             quick=float(signal.confidence),
                             rot=rotation_key_for_hour(_now_mst().hour))
            return

        # Rotation context for the data-driven gates
        gate_now = _now_mst()
        gate_rot = rotation_key_for_hour(gate_now.hour)
        gate_sched = _schedule_for_dow(gate_now.weekday())

        # ── THE DEEP READ (user directive, Aug 25: "do it for the bridge
        # too"; defined above the gates since Aug 31 for the NEAR-MISS
        # consult) — every signal, bot-bound or desktop-bound, gets the
        # same deep scan the user's QC runs before it's allowed to matter.
        # The result is CACHED in _deep — one read, every consumer: the
        # near-miss gate, the bridge pre-check, the desktop stage.
        # Fail-open: a broken check never silently starves signals; the
        # QC still gates everything.
        _deep = None
        _df = None

        def _do_deep_read():
            # (Sep 7) ONE implementation lives module-level (_deep_read):
            # the Sure Thing's pre-fire gate shares it, so every popup —
            # champion or coil — is brain-read before it fires.
            return _deep_read(exchange, pair, cfg)

        # ── SEPARATED GATES: the DESKTOP alert (contest) uses the original
        # criteria — champion ≥ threshold + checklist, both checked upstream.
        # The BRIDGE write (bot's money) keeps the full new stack: move floor
        # + per-asset confidence bar. A gate failure = contest-only alert with
        # the reason spelled out; the bot never sees a gated pick.
        gate_notes = []

        # ── FIRE-TIME SNAPSHOT (user spec, Sep 5): the feature vector is
        # computed BEFORE any gate verdict so every outcome — fired or
        # buried by ANY gate below — carries the same full features for
        # the hindsight scorer. One 5-min fetch per candidate; a broken
        # feature read never blocks the funnel.
        try:
            _begin_fire_snapshot(exchange, pair, float(signal.confidence),
                                 action=signal.action,
                                 entry=float(signal.entry_price or 0.0))
        except Exception as exc:
            # without the snapshot the popup's hindsight row is a gap
            # in the money-logic — say so
            logger.warning("fire snapshot failed for %s (%s) — this "
                           "popup's hindsight row is lost", pair, exc)

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
        except Exception as exc:
            logger.warning("confidence bar read failed for %s (%s) — "
                           "the auto threshold ships unasset-tuned", pair, exc)
        # ── THE PER-COIN TIDE MEMORY (Sep 10, tide_memory.py): the
        # false-breakout pattern applied to the tide — a coin that pays
        # in OUT/FLAT hours beyond the fleet eases its own bar up to 3
        # pts; a coin that doesn't, pays up to 3 more. RISING rides the
        # fleet (rho +0.24). Never below the lone-wolf floor; fail-closed
        # on a broken store. (BOT SIDE: this is the bot ledger's own
        # price — the DESKTOP path prices OUT once through
        # tide_personal_bar on the learned bar, never here too.)
        try:
            _g0_pct, _g0_state = _fleet_tide()
            if _g0_state in tide_memory_states():
                import tide_memory as _tmem2
                _tadj2, _twhy2 = _tmem2.tide_edge(pair, _g0_state)
                if _tadj2:
                    _new_thr = max(LONE_WOLF_BAR_FLOOR, effective_thr - _tadj2)
                    logger.info("  TIDE MEMORY: %s %+.1f pts in %s — %s (bar %d%% → %d%%)",
                                asset, _tadj2, _g0_state, _twhy2,
                                effective_thr, _new_thr)
                    effective_thr = int(_new_thr)
        except Exception as exc:
            # fail-open is the contract (a broken memory never gates)
            # but the coin's personal relief silently vanishing is a
            # popup-floor question — say so
            logger.warning("tide_memory read failed for %s/%s (%s) — the "
                           "coin's personal bar relief ships UNAPPLIED", pair, _g0_state, exc)

        # ── LONE WOLF (user rule, Aug 31): the bar was calibrated before
        # the tide existed. A coin running on its own in a dead fleet gets
        # the discount (never below the floor). RISING gets nothing (the
        # fleet already carries); OUT earns the same discount since Sep 4
        # (the POPCAT lesson — a TRUE solo runner against a sinking fleet
        # is the strongest lone-wolf case; the bot's ledger still
        # hard-blocks OUT — two ledgers). The deep-read floor is NOT
        # discounted — the QC and the alert must never disagree.
        _lw_pts, _lw_note = lone_wolf_discount(exchange, pair)
        if _lw_pts and effective_thr > LONE_WOLF_BAR_FLOOR:
            _lw_thr = max(LONE_WOLF_BAR_FLOOR, effective_thr - _lw_pts)
            logger.info("  LONE WOLF: %s %s — bar relaxed %d%% → %d%%",
                        asset, _lw_note, effective_thr, _lw_thr)
            effective_thr = _lw_thr

        # ── TIDE-AWARE BAR (user rule, Aug 31: "it knows about the tide"):
        # same evidence as the desktop floor — in RISING fleets the high
        # confidence numbers carried no information (≥89 won 48% while
        # sub-60 won 83%), so the bar relaxes by the same margin, never
        # below the deep-read floor. FLAT keeps the data bar (the data
        # decides); OUT's bot-side write stays blocked downstream (the
        # lone-wolf carve-out is contest-only); a broken tide read leaves
        # the data bar untouched.
        try:
            _g2_pct, _g2_state = _fleet_tide()
        except Exception:
            _g2_pct, _g2_state = None, None
        if _g2_state == "RISING" and effective_thr > TIDE_RISING_BAR_FLOOR:
            _g2_old = effective_thr
            effective_thr = int(max(TIDE_RISING_BAR_FLOOR, effective_thr - TIDE_RISING_BAR_RELAX))
            logger.info("  TIDE RISING (fleet %.0f%%) — rotation bar relaxed %d%% → %d%%",
                        _g2_pct or 0, _g2_old, effective_thr)

        # ── NEAR-MISS DEEP SCAN (user rule, Aug 31: "do a deep scan when
        # it is within 5% of passing"): the quick read is judged first —
        # cheap. A candidate within 5 points BELOW the bar gets the brain's
        # verdict, and the bar is then judged on the BRAIN'S number (the
        # same deep conviction the QC, the alert, and the bridge write
        # use). No bypass — the data bar still applies; it just applies to
        # the honest number.
        if (signal.confidence < effective_thr
                and signal.confidence >= effective_thr - NEAR_MISS_PTS):
            logger.info("  NEAR MISS: %s quick %.0f%% vs bar %d%% — consulting the brain",
                        asset, signal.confidence, effective_thr)
            try:
                _deep, _df = _do_deep_read()
            except Exception as _exc:
                logger.debug("near-miss deep read unavailable for %s: %s (quick number stands)", asset, _exc)
        _brain_conf = (float(_deep.confidence)
                       if _deep is not None and _deep.action == "buy"
                       else float(signal.confidence))
        _brain_used = _deep is not None and _deep.action == "buy"

        if _brain_conf < effective_thr:
            gate_notes.append(f"{_brain_conf:.0f}% sure — bot needs {effective_thr}% for this coin")
            logger.info("  %s %s (%s %.0f%%) — below %d%% bar (%s %s) — contest-only",
                        signal.action.upper(), asset,
                        "brain" if _brain_used else "quick",
                        _brain_conf, effective_thr, block_name, gate_rot)
        elif _brain_used and signal.confidence < effective_thr:
            logger.info("  NEAR MISS RESCUED: %s — the brain reads %.0f%% ≥ bar %d%% (quick said %.0f%%)",
                        asset, _brain_conf, effective_thr, signal.confidence)

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
                _log_suppression(pair, "blacklisted",
                                 f"{snap.get('blacklist_reason', 'learned restriction')}",
                                 quick=float(signal.confidence),
                                 rot=rotation_key_for_hour(_now_mst().hour))
                return
            if snap.get("ok"):
                _tn, _tname, _tcons = snap["tier"]
                _slip_s = f"{snap['slippage_pct']:.2f}%" if snap.get("slippage_pct") is not None else "—"
                liq_line = f"EASY TO SELL: {snap['score']}/100"
                logger.info("  %s", liq_line)
        except Exception as exc:
            logger.debug("Liquidity snapshot failed for %s: %s", pair, exc)

        # ── THE DEEP PRE-CHECK (bridge stage) — the read itself now runs
        # ABOVE the gates (the near-miss consult); this is the bot-money
        # gate: the scan MUST run (fail-closed — no read, no write), it
        # must not contradict the buy, and no falling knives. The brain's
        # NUMBER is advisory (user rule, Sep 5) — it sizes, it doesn't
        # block; the bot's own reader gates carry the real-money veto. A
        # near-miss rescue already has _deep cached — no second read.
        # (The bot's breakout-appropriate chase rules — stale 2% / blowoff
        # 5% — live reader-side; this is the subset right for ANY buy.)
        _bot_out_priced_pass, _bot_out_pbar = False, None
        if is_buy and not gate_notes:
            try:
                # ── THE BOT'S OUT GATE (Sep 10 rebalance: the bot honors
                # the learned bar on ITS OWN ledger). The old wall ("all
                # ships sink together", Sep 4) rested on the 30-day
                # artifact the extended store retired — OUT pays 12.3% on
                # the popup fleet (the fleet study) — and the wall binds
                # exactly the champion-grade cohort (everything here
                # already cleared the bot's engine bar). The bot keeps
                # its discipline: the learned bar with the personal
                # adjustment (fail-closed — a cold learner keeps the
                # wall), every reader veto below. THE LONE-WOLF CARVE-OUT
                # (Sep 4, the POPCAT lesson) stands unchanged: contest
                # popup only, never a bridge write.
                _tp0, _ts0 = _fleet_tide()
                if _ts0 == "OUT":
                    if _lw_pts:
                        gate_notes.append("OUT tide — the bot stays out; the lone-wolf "
                                          "carve-out is contest-only")
                        logger.info("  OUT-TIDE LONE WOLF: %s — %s — no bridge write (the "
                                    "bot's carve is contest-only); the contest popup continues",
                                    asset, _lw_note)
                    else:
                        _bot_lbar, _bot_lmeta = _tide_learn_bar()
                        _bot_out_pbar = tide_personal_bar(pair, "OUT", _bot_lbar)
                        if _bot_out_pbar is not None and float(signal.confidence) >= _bot_out_pbar:
                            _bot_out_priced_pass = True
                            logger.info("  OUT-TIDE BOT PASS: %s — quick %.1f clears the personal bar %.0f (%s) — "
                                        "the bot's ledger prices the tide (the deep read still owes its verdict)",
                                        asset, float(signal.confidence), _bot_out_pbar, _bot_lmeta)
                        else:
                            _bot_why = (f"the OUT tide's price on the bot's ledger: quick "
                                        f"{float(signal.confidence):.1f} vs personal bar "
                                        f"{_bot_out_pbar:.0f} ({_bot_lmeta})"
                                        if _bot_out_pbar is not None else
                                        f"fleet {_tp0:.0f}% up — all ships sink together; no solo move "
                                        f"(the carve-out needs ≥+{LONE_WOLF_MOVE_MIN:.0f}% own 24h move); "
                                        f"the learner is cold — the wall stands")
                            logger.info("  TIDE OUT (fleet %.0f%%) — no bridge write; %s", _tp0, _bot_why)
                            _log_suppression(pair, "tide-out", _bot_why,
                                             quick=float(signal.confidence),
                                             brain=(float(_deep.confidence) if _deep is not None else None),
                                             floor=float(popup_floor_for_rotation(
                                                 rotation_key_for_hour(_now_mst().hour))),
                                             tide=f"OUT {_tp0:.0f}%",
                                             rot=rotation_key_for_hour(_now_mst().hour))
                            return
                if _deep is None:
                    try:
                        _deep, _df = _do_deep_read()
                    except Exception:
                        _deep, _df = None, None
                # ── THE FIRE-TIME RULE (user spec, Sep 5 — the JUP
                # guarantee, made airtight): the deep scan MUST run
                # successfully at fire-time. A failed/null/invalid read
                # = NO popup, NO entry, NO LEDGER WRITE — on BOTH
                # ledgers. Nothing downstream sees an unverified signal.
                if _deep is None:
                    logger.info("  FAIL-CLOSED (bridge): no deep read for %s — "
                                "no write, no popup (the fire-time rule)", asset)
                    _log_suppression(pair, "fail-closed",
                                     "the deep scan could not run — no popup, "
                                     "no entry, no ledger write (the fire-time rule)",
                                     quick=float(signal.confidence),
                                     tide=f"{_ts0} {_tp0:.0f}%" if _ts0 else None,
                                     rot=rotation_key_for_hour(_now_mst().hour))
                    return
                # ── THE ADVISORY BRAIN (user rule, Sep 5): the brain's
                # NUMBER no longer blocks the ledger write — it sizes the
                # entry downstream. What still blocks: the scan's
                # DIRECTION — a deep read that says SELL/HOLD against a
                # buy is an invalid fire-time diagnostic (its confidence
                # measures conviction in the OPPOSITE call), and the bot's
                # own reader gates (ladder, head-to-head, carry cap) stay
                # the real-money protection, fully intact.
                if _deep.action != "buy":
                    logger.info("  DEEP VETO (bridge): %s deep-reads %s %.0f%% — not written; the scan contradicts the buy",
                                asset, _deep.action, _deep.confidence)
                    _log_suppression(pair, "deep-veto",
                                     f"bridge stage: deep-reads {_deep.action} "
                                     f"{_deep.confidence:.0f}% — the scan's direction "
                                     f"contradicts the buy (the number is advisory, "
                                     f"the direction is not)",
                                     quick=float(signal.confidence),
                                     brain=float(_deep.confidence),
                                     tide=f"{_ts0} {_tp0:.0f}%" if _ts0 else None,
                                     rot=rotation_key_for_hour(_now_mst().hour))
                    return
                # NO DOWNWARD TREND PICKS (user directive, Aug 25) — same
                # EMA knife test as the QC, on the bot's money too
                    from indicators import ema as _ema2
                    _es = float(_ema2(_df["close"], cfg.ema_short).iloc[-1])
                    _el = float(_ema2(_df["close"], cfg.ema_long).iloc[-1])
                    if _es < _el and float(_df["close"].iloc[-1]) < _el:
                        logger.info("  DEEP VETO (bridge): %s downward trend — no downward trend picks (user rule)", asset)
                        _log_suppression(pair, "downward-trend",
                                         "bridge stage: EMA9<EMA21 and price<EMA21 — "
                                         "no downward trend picks",
                                         quick=float(signal.confidence),
                                         brain=float(_deep.confidence),
                                         tide=f"{_ts0} {_tp0:.0f}%" if _ts0 else None,
                                         rot=rotation_key_for_hour(_now_mst().hour))
                        return
            except Exception as _exc:
                logger.debug("bridge deep pre-check unavailable for %s: %s (writing anyway — reader gates remain)", asset, _exc)

        # Write signal to the bridge file for the XRP bot to pick up —
        # ONLY if the bot's gates passed. Gated picks stay contest-only.
        # min_confidence=0 because we already checked above — bridge still checks:
        # kill switch, cooldown, duplicates, position limits, R:R, exhaustion, breakout failure
        signal_written = False
        if not gate_notes:
            try:
                from signal_bridge import write_buy_signal
                # TIDE STAMP (user rule, Aug 31: collect the data before
                # picking a FLAT sizing number): record the fleet state at
                # signal time on every bridge write. The bot logs it on
                # execution; diag_tidedata.py turns it into the FLAT-vs-
                # RISING record. Cached read — no extra API call.
                _tpw, _tsw = _fleet_tide()
                _tide_stamp = _tsw or "UNKNOWN"
                if _tide_stamp == "OUT" and _bot_out_priced_pass:
                    # the priced OUT pass rides the stamp — the reader
                    # honors 'OUT|BAR PASSED <bar>' and still rejects a
                    # bare OUT (belt-and-braces, fail-closed)
                    _tide_stamp = f"OUT|BAR PASSED {_bot_out_pbar:.0f}"
                signal_written = write_buy_signal(
                    pair=pair, action=signal.action,
                    # the BRAIN'S number (deep-read confidence when we have
                    # it): the bot's entry-conviction scoring and the QC
                    # should never disagree about what this signal was worth
                    confidence=(float(_deep.confidence) if _deep is not None
                                else float(signal.confidence)),
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    risk_reward=signal.risk_reward,
                    position_size_usd=sizing.position_size_usd if sizing else None,
                    reasons=signal.reasons,
                    source="scout_auto",
                    min_confidence=0,  # Already gated in send_alert — skip redundant check
                    tide_state=_tide_stamp,
                    tide_pct=_tpw,
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
        # The champion-grade confidence floor: only exceptional signals get
        # your screen + Zira. Below the floor the pick still logs to the
        # winners pool (log_winner runs after send_alert returns) and the
        # bot may still take it via the bridge — but the desktop stays
        # quiet. This was the original guard rail from the winning $251
        # run (then a flat 89).
        # ── TIDE-AWARE (user rule, Aug 31: the thresholds don't know the
        # tide — they should self-adjust): a RISING fleet carries greens,
        # so the floor relaxes by the tide margin. Evidence
        # (diag_tidehistory conf×tide): in RISING tides ≥89 picks won 48%
        # of decided trades while sub-60 picks won 83% — the 89 floor
        # added no quality in rising fleets, it just silenced them. FLAT
        # keeps the rotation rail for ordinary coins; OUT reaches here only
        # through the lone-wolf carve-out (Sep 4 — the solo runner's
        # _lw_pts already discounted this floor); a broken tide read fails
        # closed to the rotation rail. The deep-read ≥60 check further down
        # still gates every alert.
        # ── ROTATION-AWARE (user rule, Sep 4: "the popup floor 89% doesn't
        # know about the rotation schedule — it needs to self-adjust
        # accordingly"): the floor starts from THIS rotation's rail
        # (ROTATION_POPUP_FLOOR — outcome-calibrated per rotation by
        # diag_popupfloor.py), then the tide margin and the lone-wolf
        # discount apply as before. One helper (popup_floor) — the QC's
        # thin-book rule reads the same stack so the alert and the QC
        # never disagree.
        try:
            _df_pct, _df_state = _fleet_tide()
        except Exception:
            _df_pct, _df_state = None, None
        _rot_now_key = rotation_key_for_hour(_now_mst().hour)
        _rot_base_floor = popup_floor_for_rotation(_rot_now_key)
        DESKTOP_ALERT_MIN = popup_floor(_df_state, _lw_pts, _rot_now_key, pair)
        if DESKTOP_ALERT_MIN != _rot_base_floor:
            _fl_why = []
            if _df_state == "RISING":
                _fl_why.append(f"tide RISING (fleet {_df_pct or 0:.0f}%)")
            if _lw_pts:
                _fl_why.append(f"lone wolf -{_lw_pts}")
            logger.info("  POPUP FLOOR: rotation %s rail %d → %d (%s)",
                        _rot_now_key, _rot_base_floor, DESKTOP_ALERT_MIN,
                        ", ".join(_fl_why) or "flat")
        else:
            logger.info("  POPUP FLOOR: rotation %s — %d (the rail stands)",
                        _rot_now_key, DESKTOP_ALERT_MIN)
        # ── THE BRAIN-JUDGED POPUP BAR — RETIRED (user rule, Sep 5 — see
        # THE ADVISORY BRAIN below): from Sep 1 to Sep 5 a current deep
        # read ran before the popup and the BRAIN'S number had to clear
        # the popup floor. The Sep 5 hindsight audit (diag_missedmoney)
        # buried the floor: every brain-100 read that day was a dud while
        # the day's biggest runs read 0-39% — the number was
        # anti-correlated with outcomes. The scan STILL runs before every
        # popup (fail-closed); what changed is the number's job: sizer,
        # not gatekeeper. The HEAD-TO-HEAD CHALLENGE (Sep 4) retired with
        # the floor — no floor to challenge.
        # ── THE ADVISORY BRAIN (user rule, Sep 5 — the entry-path
        # rebuild): the brain-judged POPUP FLOOR IS REMOVED. The deep scan
        # still MUST run at fire-time (fail-closed below — the JUP
        # guarantee, kept), and its DIRECTION must agree with the buy —
        # but its NUMBER no longer blocks anything: it SIZES the entry
        # (the bucket table in hindsight_scorer: >=85 -> 1.00x ... <45 ->
        # 0.10x minimum viable). A 40% brain read on a setup that passes
        # the goal/stale/trend/tide/book/rotation gates now FIRES at
        # 0.10x size instead of being buried — the Sep 5 hindsight audit
        # showed the floor was anti-correlated with outcomes (every
        # brain-100 read was a dud; the day's biggest runs read 0-39%),
        # and the scorer can only learn from signals that actually enter.
        # The floor (DESKTOP_ALERT_MIN) is still computed above — it
        # stays the Hail Mary's champion-grade bar and the popup's
        # context line, nothing more. The CHALLENGER mark retired with
        # the floor (no floor to challenge).
        _brain_floor_ok = None
        _challenger = False
        _brain_mult = 1.0
        if is_buy:
            try:
                if _deep is None:
                    try:
                        _deep, _df = _do_deep_read()
                    except Exception:
                        _deep, _df = None, None
                if _deep is not None:
                    _bc = float(_deep.confidence)
                    if _deep.action != "buy":
                        # the scan RAN — but it contradicts the buy: an
                        # invalid fire-time diagnostic (its confidence
                        # measures conviction in the opposite call; a
                        # number you cannot use as a buy sizer)
                        logger.info("  DEEP DIRECTION VETO (desktop): %s deep-reads "
                                    "%s %.0f%% — the scan contradicts the buy",
                                    asset, _deep.action, _bc)
                        _log_suppression(pair, "deep-direction",
                                         f"deep-reads {_deep.action} {_bc:.0f}% — the scan's "
                                         f"direction contradicts the buy (the number is "
                                         f"advisory, the direction is not)",
                                         quick=float(signal.confidence), brain=_bc,
                                         floor=float(DESKTOP_ALERT_MIN),
                                         tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                         rot=_rot_now_key)
                        return
                    _brain_floor_ok = True
                    try:
                        from hindsight_scorer import brain_size_mult
                        _brain_mult = brain_size_mult(_bc)
                    except Exception:
                        _brain_mult = 1.0
                    logger.info("  ADVISORY BRAIN: the brain reads %.0f%% — the number "
                                "SIZES the entry at %.2fx, the floor no longer blocks "
                                "(quick said %.0f%%, rail %d%%)",
                                _bc, _brain_mult, signal.confidence, DESKTOP_ALERT_MIN)
            except Exception:
                _brain_floor_ok = None
        # ── FAIL-CLOSED (user rule, Sep 2 — the JUP 14:52 lesson, KEPT
        # Sep 5 as the FIRE-TIME RULE): NO brain read = NO popup, ever.
        # Availability is mandatory; the value is advisory.
        if is_buy and _brain_floor_ok is None:
            logger.info("  Desktop suppressed: the brain could not be read for %s — "
                        "no unverified popups (fail-closed; quick said %.0f%%)",
                        asset, signal.confidence)
            _log_suppression(pair, "fail-closed",
                             f"the brain could not be read (quick said {signal.confidence:.0f}%)",
                             quick=float(signal.confidence),
                             tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                             rot=_rot_now_key)
            return
        # ── THE FAST FUNNEL (user rule, Sep 6: "we have enough data to
        # act now. Get it done."): route on the fire-time features —
        # FAST = immediate entry with minimal gates (the worth floor
        # WAIVES below; the 3% guard, the popup cap, the death line,
        # too-thin, tide-OUT and the rotation lock all STAND); SLOW =
        # the full stack unchanged. The rule reads LIVE from
        # hindsight_rules.json (the scorer re-extracts weekly) with the
        # provisional data-backed rule (momentum_1h >= +0.4%: 73%
        # winners, zero duds in the first 109 events) until a tercile
        # clears the bar. Fails CLOSED to the slow funnel.
        _fast_route, _fast_why = False, ""
        if is_buy:
            try:
                from hindsight_scorer import fast_route
                _fast_route, _fast_why = fast_route(
                    (_SNAPSHOT_CTX.get("ctx") or {}).get("features"))
            except Exception:
                _fast_route, _fast_why = False, ""
            if _fast_route:
                logger.info("  🚀 FAST ROUTE: %s — minimal gates; the caps manage the risk",
                            _fast_why)
        # ── THE STALE-MOVE GUARD (user rule, Sep 1: "no more wiggles —
        # only solid trends"): a RE-ENTRY whose move peaked hours ago is
        # a sagging top, not a trend (APT: peaked 08:15, the 12:11
        # re-entry fired at 100% and sank). Fresh first-fires are exempt —
        # the system noticing a move is the early catch.
        if _is_reentry:
            try:
                _st = _stale_move(exchange, pair)
                if _st:
                    logger.info("  STALE MOVE: %s — 4h high %.0fh ago, %.1f%% below it "
                                "— re-entry popup skipped (the wiggle trap)",
                                asset, _st[0], _st[1])
                    _log_suppression(pair, "stale-move",
                                     f"4h high {_st[0]:.0f}h ago, {_st[1]:.1f}% below it (re-entry)",
                                     quick=float(signal.confidence),
                                     brain=(float(_deep.confidence) if _deep is not None else None),
                                     tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                     rot=_rot_now_key)
                    return
            except Exception as exc:
                # THE SAGGING-TOP RISK: a stale-move read that failed
                # lets a possibly-dead re-entry through — the guard's
                # absence must be visible against the APT lesson
                logger.warning("stale-move guard read failed for %s (%s) "
                               "— this re-entry popup ships UNGUARDED", pair, exc)

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
        # Contest chunk (25/50/75/100% ? the contest app only takes those):
        # the spend line must reflect the REAL contest size, not the static tier
        # label. The TIA close / window reset changes the contest; the live
        # ledger (contest_account.json current_balance) is the actual game state;
        # the console_state.json tier label is the user's selection fallback.
        # Every size-sensitive popup gate (worth tilt, cap dollar ceiling, death
        # line, danger-zone impact) now scales off _contest_size() so the same
        # signal means a different SIZE at a $1k contest vs a $10k contest.
        # THE CONTEST SIZE (Sep 10 — the unbound-_bal fix): every
        # size-sensitive line below reads the LIVE contest balance via
        # _contest_size() — the ledger (contest_account.json) is the game
        # state; the console tier label is only the spoken fallback. (The
        # dead refactor block that computed this into `_sz` but never
        # bound _bal is gone: _bal was READ at the spend line before any
        # STORE anywhere in send_alert, so the function raised
        # UnboundLocalError on every candidate that got this far — and
        # the book-carry filter never learned the contest's real size.)
        try:
            _bal = float(_contest_size())
        except Exception:
            _bal = 0.0
        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\n" if _bal > 0 else ""
        # ── THE GOAL CLOCK (user rule, Sep 1, retargeted Sep 4: "target
        # each market separately — what were those assets doing at this
        # time all week? use 24 hour candles, scan 1 week; the goal
        # should reach before the rotation happens ideally" + "keep what
        # picked yesterday's winners but add to it"): the goal is the
        # ENGINE's per-rotation expected move (the old derivation,
        # unchanged) CAPPED by the asset's own week (median daily range
        # from 7 daily candles — the cap only pulls DOWN), and the window
        # is bounded by the NEXT ROTATION BOUNDARY (min(180, max(60,
        # time to the boundary)) — crossing it is the exception the
        # boundary warning covers). The $20 pace and its resolve cap are
        # retired on the popup path.
        _goal_pct = _market_goal_pct(exchange, pair)
        _win_min = _rotation_window_min()
        _out_by = (_now_mst() + timedelta(minutes=round(_win_min / 5.0) * 5)).strftime("%I:%M").lstrip("0")
        hold_txt = f"+{_goal_pct:.1f}% in ~{_win_min / 60.0:.0f}h"
        logger.info("  GOAL CLOCK: %s — goal +%.1f%% (engine expected move, capped by "
                    "the asset's week), window %.0fh (to the rotation boundary), out by %s",
                    asset, _goal_pct, _win_min / 60.0, _out_by)

        # ── THE DOUBLE-CHECK RULE (user directive, Aug 25): a desktop signal
        # must already pass the SAME deep scan the user will run in QUICK
        # CHECK — their check becomes a confirmation, not a surprise veto.
        # (This morning's PUMP: the quick read said BUY 77, the deep read
        # said HOLD 40 — the alert fired on the wrong one.) The deep read
        # ran at the bridge stage for bot-bound signals; bot-declined
        # (contest-only) signals get theirs here. Fail-open on errors.
        if is_buy:
            _out_tide_lw = False        # the lone-wolf carve-out path
            _out_tide_learned = False   # the learned-bar / journal-release path
            try:
                # TIDE OUT = no entries anywhere — EXCEPT THE LONE-WOLF
                # CARVE-OUT (user rule, Sep 4, the POPCAT lesson: the
                # blanket block buried 12 crowns of a coin the market then
                # paid +3%). A TRUE solo runner — own 24h move ≥ the
                # lone-wolf tiers, via lone_wolf_discount, which now
                # covers OUT tides — passes the tide gate and the popup
                # fires MARKED 'against an OUT tide' (the same _lw_pts
                # already discounted the floor at the brain bar; the rail
                # is never discounted for anyone else). Bot-side OUT
                # blocks (the bridge write + the reader's stamp check)
                # are UNTOUCHED — two ledgers, different rules.
                _tp2, _ts2 = _fleet_tide()
                if _ts2 == "OUT":
                    if _lw_pts:
                        _out_tide_lw = True
                        logger.info("  OUT-TIDE LONE WOLF: %s — %s — the solo runner passes "
                                    "the tide gate (the brain already cleared the %.0f%% bar)",
                                    asset, _lw_note, DESKTOP_ALERT_MIN)
                    else:
                        # ── THE PERSONAL OUT BAR (Sep 10, rebalanced):
                        # the tide became a PRICE, not a wall. The deep
                        # store prices the tide by its payer rate
                        # (tide_learn.py, clamped [86, 97], fail-closed
                        # to the blanket block when the learner has no
                        # model) and the coin's own tide memory
                        # personalizes it (tide_personal_bar) — ONE tax,
                        # not two. THE LONE WOLF still passes on solo
                        # strength (above); THE JOURNAL RELEASE stays as
                        # the backstop when the model is cold (below).
                        _lbar, _lmeta = _tide_learn_bar()
                        _pbar = tide_personal_bar(pair, "OUT", _lbar)
                        if _pbar is not None and float(signal.confidence) >= _pbar:
                            _out_tide_learned = True   # rides the marked 'against the tide' path
                            logger.info("  OUT-TIDE LEARNED PASS: %s — quick %.1f clears %s",
                                        asset, float(signal.confidence), _lmeta)
                        else:
                            # THE JOURNAL RELEASE backstop (Sep 10):
                            # champion-grade candidates release through
                            # the OUT-tide block when the learner is cold
                            # AND the journal shows tide-out burying
                            # payers (quick >= 92 AND deep >= 84 + buy)
                            # — uncapped, user rule Sep 10.
                            _rel, _rel_why = _seam_releases_tide_out(
                                signal.confidence, _deep, pair,
                                deep_fetcher=lambda: _do_deep_read()[0])
                            if _rel:
                                _out_tide_learned = True  # rides the marked 'against the tide' path
                                logger.info("  OUT-TIDE JOURNAL RELEASE: %s — quick %.0f, brain %.0f — %s",
                                            asset, float(signal.confidence),
                                            float(getattr(_deep, "confidence", 0) or 0), _rel_why)
                            else:
                                _bar_txt = (f"personal bar {_pbar:.0f} ({_lmeta})"
                                            if _pbar is not None
                                            else f"no learned bar ({_lmeta})")
                                _log_suppression(pair, "tide-out",
                                                 f"fleet {_tp2:.0f}% up — the OUT tide's price: "
                                                 f"quick {float(signal.confidence):.1f} vs {_bar_txt}; "
                                                 f"lone wolf needs >= +{LONE_WOLF_MOVE_MIN:.0f}% "
                                                 f"own 24h move; journal: {_rel_why}",
                                                 quick=float(signal.confidence),
                                                 brain=(float(_deep.confidence) if _deep is not None else None),
                                                 floor=float(DESKTOP_ALERT_MIN),
                                                 tide=f"OUT {_tp2:.0f}%", rot=_rot_now_key)
                                logger.info("  TIDE OUT (fleet %.0f%%) — desktop suppressed (%s; journal: %s)",
                                            _tp2, _bar_txt, _rel_why)
                                return
                # The brain rules EVERY alert — whether this stage read it
                # or the near-miss consult already did (one read, cached).
                if _deep is None:
                    _deep, _df = _do_deep_read()
                if _deep is not None:
                    # ADVISORY BRAIN (Sep 5): the DIRECTION still vetoes —
                    # a SELL/HOLD read against a buy is an invalid
                    # diagnostic — but the NUMBER no longer does (it sized
                    # the entry upstream; a 40% read fires at 0.10x, it is
                    # not buried here).
                    if _deep.action != "buy":
                        logger.info("  DEEP VETO (desktop): %s deep-reads %s %.0f%% — the scan contradicts the buy",
                                    asset, _deep.action, _deep.confidence)
                        _log_suppression(pair, "deep-veto",
                                         f"deep-reads {_deep.action} {_deep.confidence:.0f}% "
                                         f"— the scan's direction contradicts the buy "
                                         f"(the number is advisory, the direction is not)",
                                         quick=float(signal.confidence),
                                         brain=float(_deep.confidence),
                                         floor=float(DESKTOP_ALERT_MIN),
                                         tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                         rot=_rot_now_key)
                        return
                    from indicators import ema as _ema3
                    _es = float(_ema3(_df["close"], cfg.ema_short).iloc[-1])
                    _el = float(_ema3(_df["close"], cfg.ema_long).iloc[-1])
                    if _es < _el and float(_df["close"].iloc[-1]) < _el:
                        logger.info("  DEEP VETO (desktop): %s downward trend — no downward trend picks (user rule)", asset)
                        _log_suppression(pair, "downward-trend",
                                         "EMA9<EMA21 and price<EMA21 — no downward trend picks",
                                         quick=float(signal.confidence),
                                         brain=float(_deep.confidence),
                                         floor=float(DESKTOP_ALERT_MIN),
                                         tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                         rot=_rot_now_key)
                        return
            except Exception as _exc:
                logger.debug("desktop pre-check unavailable for %s: %s (alerting anyway — QC still gates)", asset, _exc)

        # (the goal clock above set hold_txt + _out_by; the $20 patience
        # ladder and its "up twenty dollars" phrasing retired with the
        # scalp game — Sep 1)
        _how_long = (f"goal +{_goal_pct:.1f}% by {_out_by} — "
                     f"if it hasn't paid by then, get out.")
        # ── TIDE (fleet context — every alert states it) ────────────────
        _tide_line = ""
        _tide_spoken = ""
        try:
            _tp3, _ts3 = _fleet_tide()
            if _ts3 == "RISING":
                _tide_line = f"TIDE: RISING ({_tp3:.0f}% of the fleet up) — the market carries greens\n"
                _tide_spoken = "The tide is rising! "
            elif _ts3 == "FLAT":
                _tide_line = f"TIDE: FLAT ({_tp3:.0f}% of the fleet up) — pickier, smaller\n"
            elif _ts3 == "OUT" and _lw_pts:
                # the lone-wolf carve-out — the popup fires AGAINST the
                # fleet on the solo runner's own strength
                _tide_line = (f"TIDE: OUT ({_tp3:.0f}% of the fleet up) — LONE WOLF (OUT TIDE) carve-out: "
                              f"this popup fires against the fleet\n")
                _tide_spoken = "A lone wolf against an out tide! "
            elif _ts3 == "OUT" and _out_tide_learned:
                # the learned bar / journal release — the tide is a PRICE
                # the candidate paid, not a wall; say so plainly
                _tide_line = (f"TIDE: OUT ({_tp3:.0f}% of the fleet up) — the learned bar was paid: "
                              f"the data says this tide still prices winners\n")
                _tide_spoken = "Against the out tide — but it paid the learned price! "
        except Exception as exc:
            # the alert renders without its tide line — cosmetic, but a
            # dead fleet read inside send_alert is a pattern worth seeing
            logger.debug("alert tide line read failed (%s) — the popup "
                         "renders without tide context", exc)
        # ── LONE WOLF line (Aug 31): say WHY the alert fired below the
        # usual floor — the call rides solo strength in a dead fleet.
        _lw_line = f"  LONE WOLF: {_lw_note}\n" if _lw_pts else ""
        # ── BOOK CARRY (small coin / big coin — user rule, Aug 26) ──────
        # Can this book carry the CONTEST money? Reuses the liquidity
        # snapshot fetched earlier (the book depth is what matters).
        # Sizes the SPEND down to the biggest contest chunk the book
        # carries; if even 25% doesn't fit, the user can't trade it —
        # no alert at all.
        # (the fallback is the CONFIGURED tier, not a fictional $5k — at
        # the $1k tier a fake 5k blocks small books the real contest fits)
        _spend = float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)
        _carry = None
        try:
            _depth = float(snap.get("book", {}).get("ask_tob") or 0.0)
            if _depth > 0:
                _carry = _depth * 0.20
                if _spend > _carry:
                    _fit = [c * _spend for c in (0.25, 0.50, 0.75, 1.00)
                            if c * _spend <= _carry]
                    _spend = max(_fit) if _fit else 0.0
        except Exception:
            _carry = None
        if _carry is not None and _spend <= 0:
            logger.info("  TOO THIN for contest size (book carries ~$%.0f) — desktop suppressed",
                        _carry)
            _log_suppression(pair, "too-thin",
                             f"the book carries ~${_carry:.0f} — even 25% of the contest "
                             f"size doesn't fit",
                             quick=float(signal.confidence),
                             brain=(float(_deep.confidence) if _deep is not None else None),
                             floor=float(DESKTOP_ALERT_MIN),
                             tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                             rot=_rot_now_key)
            return
        if _carry is not None and _spend < (float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)):
            contest_msg = (f"  SPEND: {_spend / (float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)):.0%} = "
                           f"${_spend:,.0f} — SMALL COIN: this book carries ${_spend:,.0f}, "
                           f"not ${_bal:,.0f}\n")
        elif _carry is not None:
            contest_msg = f"  SPEND: 100% = ${_bal:,.0f} — big book, carries all-in\n"
        # ── THE 3% DEATH-LINE GUARD (Kraken Funded rules, encoded Sep 1 —
        # the drift audit found the -3% line was never modeled): the
        # contest account's real state (tier + current balance from the
        # console) sets the sizing base, and the entry is capped so the
        # coin's WORST NORMAL CASE (its profiled window drawdown) eats at
        # most ~1/3 of the remaining buffer. Buffer gone → NO buy popup
        # at all — the account is at the line and any trade can close it.
        _hail_mary = False   # set inside the guard below (safe default)
        try:
            import contest_account as _ca
            _acct = _ca.load_state()
            _acct_bal = float(_acct["current_balance"])
            if _acct_bal > 0:
                _bal = _acct_bal  # the sizing base = the LIVE contest account
            _wdd = _ca.window_drawdown_pct(pair)
            _risk_cap = _ca.max_entry_usd(_acct_bal, _wdd, float(_acct["start_balance"]))
            # ── THE HAIL MARY EXCEPTION (user rule, Sep 4 — KEPT when the
            #    lock was removed, Sep 5: "Keep the 'Hail Mary'"): when the
            #    account is UNDER break-even (the contest is in danger) with
            #    platform room and no window used this rotation, a
            #    CHAMPION-grade setup (never a challenger) in a VERIFIED
            #    uptrend on a MAJOR book opens the ONE-HOUR window — the
            #    popup is marked, the trade clock bounds by the hour. The
            #    guard (0.34/dd), the knife, the exits, every risk control
            #    STANDS ("no uncontrolled exposure"); THE DEATH LINE NEVER
            #    YIELDS. One window per rotation.
            _hail_mary = _ca.hail_mary_active(_acct)
            if not _hail_mary and _risk_cap > 0:
                _hm_ok, _hm_why = _ca.hail_mary_available(_acct)
                if _hm_ok and not _challenger:
                    _hm_q, _hm_qwhy = _hail_mary_qualifies(
                        deep=_deep, df=_df, carry=_carry,
                        balance=(float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)),
                        floor=float(DESKTOP_ALERT_MIN),
                        ema_short=cfg.ema_short, ema_long=cfg.ema_long)
                    if _hm_q:
                        _acct = _ca.activate_hail_mary(_acct)
                        _hail_mary = True
                        # the trade window: one hour, not the rotation
                        _win_min = min(_win_min, float(_ca.HAIL_MARY_WINDOW_MIN))
                        _out_by = (_now_mst() + timedelta(
                            minutes=round(_win_min / 5.0) * 5)).strftime("%I:%M").lstrip("0")
                        _how_long = (f"goal +{_goal_pct:.1f}% by {_out_by} — "
                                     f"if it hasn't paid by then, get out.")
                        logger.info("  💪 HAIL MARY ACTIVATED: %s — the emergency hour "
                                    "runs; the death line stands; cap $%.0f",
                                    _hm_qwhy, _risk_cap)
                    else:
                        logger.info("  Hail Mary declined — %s", _hm_qwhy)
                elif _hm_ok and _challenger:
                    logger.info("  Hail Mary declined — a challenger never "
                                "gets the emergency window")
                else:
                    logger.debug("  Hail Mary unavailable — %s", _hm_why)
            if _risk_cap <= 0:
                _floor_fl = _ca.death_line(_acct)
                logger.info("  DEATH LINE: buffer gone (%.0f above the line at $%.2f) — no buy popup",
                            _acct_bal - _floor_fl, _floor_fl)
                _log_suppression(pair, "death-line",
                                 f"buffer gone — the account sits at the platform -3% line "
                                 f"(${_floor_fl:.2f}); any trade can close the contest",
                                 quick=float(signal.confidence),
                                 brain=(float(_deep.confidence) if _deep is not None else None),
                                 floor=float(DESKTOP_ALERT_MIN),
                                 tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                 rot=_rot_now_key)
                return
            if _spend > _risk_cap:
                _spend = _risk_cap
                contest_msg = (f"  SPEND: ${_spend:,.0f} — THE 3% GUARD: this coin's worst-case "
                               f"window drawdown is {_wdd:.1f}%; ${_spend:,.0f} is the most the "
                               f"platform line can afford\n")
        except Exception as _exc:
            logger.debug("3%% guard unavailable in send_alert: %s", _exc)  # fail-open
        # ── THE ADVISORY-BRAIN SIZE SCALER (user rule, Sep 5): the brain's
        # number multiplies the guard-capped spend — ≥85 reads full size,
        # a 40% read 0.10x (MINIMUM VIABLE: no trade is ever sized at
        # zero; the scorer needs entries across the whole confidence
        # spectrum to learn where the number actually helps). Applied
        # AFTER the 3% guard (the guard's cap is the death-line truth) and
        # BEFORE the worth floor (a scaled entry still has to pay its
        # pace — the dial governs, the user can zero it).
        if is_buy:
            try:
                if _brain_mult < 1.0:
                    _spend = _spend * _brain_mult
                    logger.info("  ADVISORY BRAIN SIZING: %.2fx → $%.0f",
                                _brain_mult, _spend)
            except Exception as exc:
                # sizing dollars move here — the skipped scaler must be
                # visible (the spend ships at its pre-scaler value)
                logger.warning("brain size scaler failed (%s) — the "
                               "spend ships UNSCALED", exc)
        # ── THE FLAT SIZING FACTOR (user rule, Aug 31: "collect the data
        #    first" — one stamped week later, Sep 7): FLAT 22% win vs
        #    RISING 57% — the discount frame met; the user picked 0.50×.
        #    Rides AFTER the guard + brain sizing, BEFORE the worth floor
        #    (a discounted entry still pays its pace — same order as the
        #    brain multiplier). RISING/OUT untouched.
        _tide_size_mult = 1.0
        if is_buy and (_df_state or "") == "FLAT":
            _tide_size_mult = TIDE_FLAT_SIZE_FACTOR
            _spend = _spend * _tide_size_mult
            logger.info("  FLAT TIDE SIZING: %.2fx → $%.0f (the tide data spoke)",
                        _tide_size_mult, _spend)
        # ── THE CLIMB-OUT GATES (user rule, Sep 6: "Find swing trades
        #    that avoid danger zones: no trades near major resistance,
        #    no trades in chop, no trades when liquidity is thin. Only
        #    show setups with clear escape routes.") — the HARD gate:
        #    clearance (room to the nearest major ceiling), ER (not
        #    chop), the floor (structural support + the escape route),
        #    the bid door. Every metric logs to the snapshot dataset;
        #    the weekly gate report grades them (LOOSEN/KEEP/TIGHTEN);
        #    the thresholds live in climb_rules.json, read LIVE. Fails
        #    OPEN on a broken read (the death-line guard is the gate
        #    that never yields; climb-out protects quality).
        _climb = None
        if is_buy:
            _climb = _climb_out_check(exchange, pair,
                                      float(signal.entry_price or 0.0),
                                      float(_goal_pct or 1.5),
                                      float(_spend or 0.0))
            if not _climb.get("ok"):
                _fg = _climb.get("fail_gate") or "?"
                logger.info("  CLIMB-OUT %s: %s gate — %s", asset, _fg,
                            _climb.get("fail_reason"))
                _log_suppression(pair, f"climb-{_fg}",
                                 f"{_climb.get('fail_reason')} — no clear "
                                 f"escape route (climb-out)",
                                 quick=float(signal.confidence),
                                 brain=(float(_deep.confidence) if _deep is not None else None),
                                 floor=float(DESKTOP_ALERT_MIN),
                                 tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                 rot=_rot_now_key,
                                 climb=(_climb.get("features") or {}))
                return
        # ── THE WORTH FLOOR (user rule, Sep 2: "big coins only... less
        # entries but more money" — the JUP lesson: a $233 sized entry at
        # the +2% goal pays $4.66, noise that only pays the spread): the
        # sized entry at its goal must pay at least half the daily pace
        # ($120 on 10k, $12 on 1k) or the popup is bumped. Fail-open on a
        # broken account read — the sizing above already failed open.
        # (Sep 6) THE FAST FUNNEL WAIVES IT: a fast-route signal trades
        # the pace gate for the data — the 73%-winner momentum class
        # pays quick and often; the 3% guard + the popup cap carry the
        # risk alone. The slow route keeps the floor.
        if is_buy and not _fast_route:
            try:
                import contest_account as _ca_w
                _acct_w = _ca_w.load_state()
                _pace_w = _ca_w.daily_pace_usd(_acct_w)
                _wf_w = _worth_floor_frac()  # the console dial, live
                _worth_w = float(_spend) * (float(_goal_pct) / 100.0)
                if _worth_w < _pace_w * _wf_w:
                    logger.info("  WORTH FLOOR: %s — a $%.0f entry at the +%.1f%% goal pays "
                                "only $%.0f (< $%.0f, %.0f%% of the daily pace) — popup bumped",
                                asset, _spend, _goal_pct, _worth_w,
                                _pace_w * _wf_w, _wf_w * 100.0)
                    _log_suppression(pair, "worth-floor",
                                     f"a ${_spend:.0f} entry at the +{_goal_pct:.1f}% goal pays "
                                     f"${_worth_w:.0f} < the ${_pace_w * _wf_w:.0f} floor "
                                     f"({_wf_w * 100.0:.0f}% of the daily pace)",
                                     quick=float(signal.confidence),
                                     brain=(float(_deep.confidence) if _deep is not None else None),
                                     floor=float(DESKTOP_ALERT_MIN),
                                     tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                     rot=_rot_now_key)
                    return
            except Exception as _exc:
                logger.debug("worth floor unavailable: %s", _exc)  # fail-open
        if is_buy:
            # THE BRAIN'S NUMBER (user rule, Aug 27: "still saying 100%
            # confident without using the brain"): the gate has used the
            # deep read since Aug 25 — but the NUMBER kept quoting the
            # shallow tournament read (quick 100% while the deep brain
            # said 66). The alert now speaks and shows the DEEP
            # confidence — the same number the user's own Quick Check
            # would print seconds later. No more lying brags.
            _dc = float(_deep.confidence) if _deep is not None else float(signal.confidence)
            # THE ADVISORY-BRAIN MARK (user rule, Sep 5): the brain's
            # number SIZED this entry — when it's below full size the
            # title carries the multiplier at a glance and the message +
            # voice say it plainly, so a 0.10x popup is never mistaken
            # for a champion green.
            _brain_line = ""
            _brain_say = ""
            if _brain_mult < 1.0:
                _brain_line = (f"  BRAIN SIZES IT: {_dc:.0f}% → {_brain_mult:.2f}× size = "
                               f"${_spend:,.0f} — the scan ran; the number is advisory "
                               f"(it sizes, it never blocks)\n")
                _brain_say = (f"The brain says {_dc:.0f} percent — "
                              f"sizing at {_brain_mult:.2f}! ")
            _tide_size_line = (f"  TIDE SIZES IT: FLAT — {TIDE_FLAT_SIZE_FACTOR:.2f}× size = "
                               f"${_spend:,.0f} (the stamped week: FLAT 22% win vs "
                               f"RISING 57% — pickier, smaller)\n" if _tide_size_mult < 1.0 else "")
            _reentry_line = ""
            _reentry_say = ""
            if _is_reentry:
                _reentry_line = f"  RE-ENTRY: fired earlier today — fine risk ${_fine:,.0f}\n" if _fine > 0 else "  RE-ENTRY: fired earlier today\n"
                _reentry_say = (f"Re-entry — fine risk {_fine:,.0f} dollars! " if _fine > 0
                                 else "Re-entry! ")
            # THE HAIL MARY MARK (user rule, Sep 4 — kept Sep 5): this
            # popup fired inside the one-hour emergency window — the
            # account-in-danger shot, the death line standing. Never
            # mistaken for an ordinary green.
            _hm_line = ""
            _hm_say = ""
            if _hail_mary:
                try:
                    import contest_account as _ca_hm
                    _hm_left = _ca_hm.hail_mary_minutes_left(_acct)
                    _hm_line = (f"  💪 HAIL MARY: the emergency hour runs for "
                                f"{max(_hm_left, 0):.0f} more min — full allocation = the "
                                f"guard's cap vs the DEATH LINE (never the whole balance); "
                                f"the clock ends with the hour\n")
                    _hm_say = "Hail Mary! The emergency hour is open! "
                except Exception:
                    _hm_line = ("  💪 HAIL MARY: the emergency hour runs — "
                                "the death line stands\n")
                    _hm_say = "Hail Mary! The emergency hour is open! "
            title = (f"🟢 BUY {asset} — {_dc:.0f}% SURE"
                     + (f" — {_brain_mult:.2f}× SIZE" if _brain_mult < 1.0 else "")
                     + (" — FAST ROUTE" if _fast_route else "")
                     + (" — CHALLENGER" if _challenger else "")
                     + (" — AGAINST THE TIDE" if _out_tide_learned else "")
                     + (" — LONE WOLF (OUT TIDE)" if _out_tide_lw else "")
                     + (" — HAIL MARY" if _hail_mary else ""))
            _small_coin = _carry is not None and _spend < (float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance))
            _challenger_say = (f"A challenger — just under the {DESKTOP_ALERT_MIN:.0f} percent floor! "
                               if _challenger else "")
            _challenger_line = (f"  CHALLENGER: the brain reads {_dc:.0f}% vs this rotation's "
                                f"{DESKTOP_ALERT_MIN:.0f}% floor — within {POPUP_NEAR_MISS_PTS:.0f}, "
                                f"allowed to challenge head-to-head. A near-miss, not a champion: "
                                f"size it like one.\n" if _challenger else "")
            _lw_say = (f"Lone wolf! Moving on her own against an out tide! " if _out_tide_lw
                       else "Lone wolf! Moving on her own while the fleet sits still! " if _lw_pts
                       else "")
            _spoken = (f"Buy {asset}! {_dc:.0f} percent sure! "
                       + _challenger_say
                       + _brain_say
                       + ("Flat tide — half size! " if _tide_size_mult < 1.0 else "")
                       + ("Fast route! " if _fast_route else "")
                       + _reentry_say
                       + _hm_say
                       + _lw_say
                       + f"Aim for plus {_goal_pct:.0f} percent! "
                       + (f"Small size only — {_spend:,.0f} dollars! " if _small_coin else
                          (f"Big book — all {(float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)):,.0f} dollars in fine! "
                           if _carry is not None else
                           "Book unreadable — your call on size! "))
                       + _tide_spoken
                       + (f"If it hasn't paid by {_out_by}, get out!" if _out_by else
                          "Hold for the goal!"))
            # ── THE DANGER-ZONE PANEL (user rule, Sep 6 — the thin-book
            #    optimized metrics): computed with the FINAL sized spend,
            #    BEFORE the message build; informs, never gates
            _dz = ""
            try:
                _dz = _danger_zone_panel(exchange, pair, float(_spend or 0.0),
                                         float(_goal_pct or 1.5),
                                         float(_win_min or 60.0), _brain_mult)
            except Exception:
                _dz = ""
            message = (
                f"BUY {asset}\n"
                f"WHY: the engine expects +{_goal_pct:.1f}% in this window at this coin's pace, "
                f"and the brain reads the setup at {_dc:.0f}%\n"
                f"WHEN: right now — this is fresh\n"
                f"HOW LONG: {_how_long}\n"
                f"{_tide_line}"
                f"{_lw_line}"
                f"{_reentry_line}"
                f"{_hm_line}"
                f"{_challenger_line}"
                f"{_brain_line}"
                f"{_tide_size_line}"
                + (f"🚀 FAST ROUTE: {_fast_why} — minimal gates; the 3% guard "
                   f"carries the risk\n" if _fast_route else "")
                + f"{contest_msg}"
                + ("" if _climb is None else str(_climb.get("output_block") or ""))
                + f"{_dz}"
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

        # ── THE POPUP CAP (user spec, Sep 5: "cap concurrent fresh
        # entries"): the advisory brain fires far more often than the old
        # floor did — no more than the cap (sidebar dial, live) may fire
        # inside the window. The BRIDGE WRITE above stands (the bot's own
        # gates govern the bot's money); only the popup is capped.
        # (Sep 9) THE POPUP CAP RETIRED — not part of the design: no
        # money cap (making more money is good), no count cap. The
        # buffer — the realized PnL above the death line — is the risk
        # factor and it owns the worth floor. The bridge write stands.

        notify_big(title, message, spoken=_spoken if is_buy else None)
        if is_buy:
            # ROTATION LOCKOUT: the alert fired — this asset is now cold
            # until the next rotation boundary (no back-to-back re-entry).
            rotation_mark_fired(asset)
            # ── THE FIRED SNAPSHOT (user spec, Sep 5): every popup that
            # fires lands in the scorer's dataset with its full fire-time
            # features — the positive class the suppression records are
            # graded against.
            try:
                _fsnap = dict(_SNAPSHOT_CTX.get("ctx") or {})
                _fsnap.update({"kind": "fired",
                               "route": ("champion-fast" if _fast_route else "champion"),
                               "tide": (f"{_df_state} {_df_pct:.0f}%"
                                        if _df_state and _df_pct is not None else None),
                               "fast_why": _fast_why,
                               "brain": _dc, "mult": _brain_mult,
                               "climb": _climb_features_stamped(
                                   _climb.get("features") if _climb else None,
                                   _df_state, _df_pct),
                               "spend": float(_spend or 0.0),
                               "goal_pct": float(_goal_pct or 0.0),
                               "win_min": float(_win_min or 0.0),
                               "entry": float(signal.entry_price or 0.0),
                               "hail_mary": bool(_hail_mary),
                               "reentry": bool(_is_reentry)})
                _log_signal_snapshot(_fsnap)
            except Exception as exc:
                # a fired popup with no snapshot row is a hole in the
                # hindsight ledger — say so
                logger.warning("fired snapshot failed for %s (%s) — this "
                               "fire's hindsight row is lost", asset, exc)
            # ── AUTO-TRACK AT FIRE TIME (Sep 21, the ATOM gap): the popup
            # is the contract — "if it pops up it should be auto tracking."
            # Tracking used to ride the browser's next rerun, and a signal
            # expiring on the bridge before any rerun (ATOM 06:27, expired
            # 06:51 unwached) left a fired popup untracked. The scanner
            # writes the row NOW from its own fire-time numbers; the
            # console's later rerun merges and adds live highs. Never
            # displaces an entry the user already has.
            # AUTO-TRACK AT FIRE TIME — one writer for every popup route.
            auto_track_popup(pair, float(signal.entry_price or 0.0),
                             float(_spend or 0.0), float(_goal_pct or 0.0),
                             float(_win_min or 0.0),
                             f"BUY {signal.confidence:.0f}% (champion)",
                             "champion")
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
    last_sure_thing_scan = 0     # THE SURE THING: 5-min fleet breakout scan
    last_loser_fetch = ""  # date string — fetch losers once per day
    cached_loser_pairs = []
    alerted_recently = {}
    _mover_scanned = {}  # MOVER FUNNEL pacing: per-pair rescan timestamps (10 min), isolated from the contest path
    current_block_name = None  # track time block changes for position reset
    _scanner_state0 = _load_scanner_state()
    last_flatten_date = _scanner_state0.get("last_flatten_date", "")  # persisted — restarts don't re-flatten
    # SURE THING: asset -> {zh, ts} of the armed coil (one announcement
    # per coil) — PERSISTED (Sep 10): restarts don't re-announce a coil,
    # and the dedup survives a console restart
    _st_armed = dict(_scanner_state0.get("sure_thing_armed") or {})

    # ── THE SURE THING (user rule, Sep 6: "I'm losing this contest, and
    #    that cannot happen. I need the Sure Thing running immediately.")
    #    The breakout-pattern detector: consolidation + ceiling tests +
    #    squeeze + volume shift -> the trigger fires AT THE CRACK, the
    #    popup speaks the countdown. CONTEST-ONLY (no bridge write — the
    #    bot's real money never sees an unverified signal); the deep
    #    scan runs BEFORE the popup (Sep 7: the Sep 6 post-entry waiver
    #    retired at the user's call — the fire-time rule applies: the
    #    read must run, fail-closed, and its direction must agree).
    #    SILENT COILS (user rule, Sep 8: "the breakout watch has been
    #    armed is just noise, no action needed, no popup until it becomes
    #    a buy now"): an armed coil is logged, never announced — THE BUY
    #    ITSELF is the Breakout Alert, and it opens the countdown.
    #    The 3% death-line guard sizes it; the popup cap,
    #    the rotation lock, the knife, the tide and the book gates all
    #    stand. Every fire + every armed coil logs to the scorer's
    #    dataset (route "sure-thing") — there is no sure thing, the
    #    scorer grades the class.
    def _sure_thing_scan(exchange):
        import sure_thing as st
        from hindsight_scorer import brain_size_mult as _st_mult

        def _st_armed_save():
            """Persist the armed-coil memory (restarts keep the dedup)."""
            try:
                _doc = _load_scanner_state()
                _doc["sure_thing_armed"] = _st_armed
                _save_scanner_state(_doc)
            except Exception as exc:
                # the armed-coil memory rides this file — a lost write
                # re-announces an armed coil after a restart
                logger.warning("scanner-state save failed (%s) — the "
                               "armed-coil memory may not survive a "
                               "restart", exc)
        # the scanner's own pair list (cfg.trading_pairs — the 54; the
        # app.py ASSETS list doesn't exist here)
        for pair in list(all_pairs):
            try:
                asset = pair.split("/")[0]
                rows = exchange.fetch_ohlcv(pair, timeframe="5m", limit=720)
                r = st.analyze(rows or [])
                if not r.get("armed"):
                    _st_armed.pop(asset, None)
                    continue
                zh = float(r.get("zone_high") or 0.0)
                # the armed-watch dedup: a NEW zone is logged (diagnostics
                # only — SILENT, user rule Sep 8: "the breakout watch has
                # been armed is just noise, no action needed, no popup
                # until it becomes a buy now"). The announce IS the fire:
                # the Breakout Alert below opens the countdown.
                _prev = _st_armed.get(asset)
                if not _prev or abs(_prev.get("zh", 0.0) - zh) > zh * 0.005:
                    _st_armed[asset] = {"zh": zh, "ts": time.time()}
                    _st_armed_save()
                    logger.info("🧲 SURE THING ARMED (silent): %s — %s", asset, r["detail"])
                if not r.get("fire"):
                    continue
                # ── the fire (pre-breakout OR the crack): the gates that
                #    protect the contest all run ──
                _rot_ok, _rot_reason, _rot_re, _fine = rotation_gate(asset)
                _lw_pts, _lw_note = lone_wolf_discount(exchange, pair)
                _tp, _ts = _fleet_tide()
                # ROTATION LOCK RETIRED (Sep 10) — always allowed now; the
                # re-entry flag still rides the popup.
                if _ts == "OUT" and not _lw_pts:
                    # ── THE LEARNED OUT BAR (Sep 10, the same pass paths
                    #    the champion gate uses): the tide is a PRICE, not
                    #    a wall. The coil's probability IS this path's
                    #    quick score; the brain's deep read below still
                    #    holds the direction veto. ① the learned bar
                    #    (tide_learn.py, the deep store's payer-rate
                    #    price) — ② the journal release (uncapped,
                    #    champion-grade; the fetcher earns the consult,
                    #    fail-closed) — ③ otherwise the suppression is                    # LOGGED (the old silent continue hid coil deaths).
                    _lbar, _lmeta = _tide_learn_bar()
                    _pbar = tide_personal_bar(pair, "OUT", _lbar)
                    if _pbar is not None and float(r.get("score") or 0.0) >= _pbar:
                        logger.info("  SURE THING %s: OUT-TIDE LEARNED PASS — score %.1f clears %s",
                                    asset, float(r.get("score") or 0.0), _lmeta)
                    else:
                        _rel, _rel_why = _seam_releases_tide_out(
                            r.get("score"), None, pair,
                            deep_fetcher=lambda: _deep_read(exchange, pair, cfg)[0])
                        if _rel:
                            logger.info("  SURE THING %s: OUT-TIDE JOURNAL RELEASE — score %.0f — %s",
                                        asset, float(r.get("score") or 0.0), _rel_why)
                        else:
                            _bar_txt = (f"personal bar {_pbar:.0f} ({_lmeta})"
                                        if _pbar is not None
                                        else f"no learned bar ({_lmeta})")
                            logger.info("  SURE THING %s: tide OUT — score %.1f vs %s; journal: %s",
                                        asset, float(r.get("score") or 0.0),
                                        _bar_txt, _rel_why)
                            _log_suppression(pair, "tide-out",
                                             f"breakout coil — the OUT tide's price: "
                                             f"score {float(r.get('score') or 0.0):.1f} vs {_bar_txt}; "
                                             f"lone wolf needs >= +{LONE_WOLF_MOVE_MIN:.0f}% "
                                             f"own 24h move; journal: {_rel_why}",
                                             quick=float(r.get("score") or 0.0),
                                             tide=f"OUT {_tp:.0f}%",
                                             rot=rotation_key_for_hour(_now_mst().hour))
                            continue
                # (Sep 9) the popup cap retired — the worth floor (the
                # buffer is its bar) + the deep read gate this path
                # the knife: a both-bearish 1h trend is a falling wedge, not a coil
                try:
                    _knife, _ = knife_reversed_1h(exchange, pair, cfg)
                    if _knife:
                        logger.info("  SURE THING %s: 1h knife active — falling wedge, skip", asset)
                        continue
                except Exception as exc:
                    # the knife is a SKIP guard — a dead read lets the
                    # wedge through, which must be visible
                    logger.warning("1h knife read failed for %s (%s) — "
                                   "the Sure Thing ships without its "
                                   "falling-wedge guard", asset, exc)
                # ── THE DEEP SCAN BEFORE THE POPUP (Sep 7: the Sep 6
                #    post-entry waiver retired — the user's call). The
                #    fire-time rule applies to the Sure Thing like every
                #    other path: the read MUST run (fail-closed — no
                #    read, no popup) and its DIRECTION must agree (a
                #    deep SELL/HOLD against the breakout is the chart
                #    saying no). The NUMBER stays advisory — the
                #    probability already sized this fire.
                try:
                    _st_deep, _st_df = _deep_read(exchange, pair, cfg)
                except Exception:
                    _st_deep, _st_df = None, None
                if _st_deep is None:
                    logger.info("  SURE THING %s: fail-closed — no deep read, "
                                "no popup (the fire-time rule)", asset)
                    _log_suppression(pair, "sure-thing-fail-closed",
                                     "the deep scan could not run — no popup "
                                     "(the fire-time rule)",
                                     quick=float(r.get("score") or 0.0),
                                     rot=rotation_key_for_hour(_now_mst().hour))
                    # the armed memory STAYS (CodeRabbit, Sep 8): the coil
                    # is announced once per zone — popping here while the
                    # coil is still armed re-announces it every 5-min poll
                    continue
                if _st_deep.action != "buy":
                    logger.info("  SURE THING %s: deep veto — the scan reads %s %.0f%% "
                                "against the breakout", asset, _st_deep.action,
                                _st_deep.confidence)
                    _log_suppression(pair, "sure-thing-deep-veto",
                                     f"deep-reads {_st_deep.action} "
                                     f"{_st_deep.confidence:.0f}% — the scan "
                                     f"contradicts the breakout",
                                     quick=float(r.get("score") or 0.0),
                                     brain=float(_st_deep.confidence),
                                     rot=rotation_key_for_hour(_now_mst().hour))
                    # the armed memory STAYS (CodeRabbit, Sep 8): a vetoed
                    # coil is not a new coil — popping here re-announces
                    # the same zone every poll; the suppression log still
                    # records every veto
                    continue
                # the death-line guard sizes it; the probability sizes it more;
                # the FLAT tide sizes it too (Sep 7: same factor the champion,
                # the mover and the QC already pay — FLAT 22% win vs RISING
                # 57%, the stamped week; the coil is no exception)
                try:
                    import contest_account as _ca_st
                    _acct = _ca_st.load_state()
                    _bal = float(_acct["current_balance"])
                    _wdd = _ca_st.window_drawdown_pct(pair)
                    _cap = _ca_st.max_entry_usd(_bal, _wdd, float(_acct["start_balance"]))
                    if _cap <= 0:
                        logger.info("  SURE THING %s: death line — no entry", asset)
                        continue
                    _mult = _st_mult(r.get("score") or 0.0)
                    _spend = _cap * _mult
                    _st_tide_pct, _st_tide_state = _fleet_tide()
                    _st_tide_mult = 1.0
                    if (_st_tide_state or "") == "FLAT":
                        _st_tide_mult = TIDE_FLAT_SIZE_FACTOR
                        _spend = _spend * _st_tide_mult
                        logger.info("  SURE THING %s: FLAT TIDE SIZING %.2fx → $%.0f",
                                    asset, _st_tide_mult, _spend)
                except Exception:
                    _spend, _mult = 0.0, 0.10
                    _st_tide_mult = 1.0
                # ── THE CLIMB-OUT GATES (Sep 6): the coil case — the
                #    ceiling being crossed IS the level; the check
                #    becomes runway BEYOND it (coil_mult x1.0). The ER
                #    gate passes coils via the squeeze exemption (a
                #    coil is squeezed + low ER by definition).
                _st_goal = _sure_thing_goal(exchange, pair)
                _st_climb = _climb_out_check(
                    exchange, pair, float(r.get("trigger_px") or 0.0),
                    _st_goal, float(_spend or 0.0),
                    is_coil=True, coil_ceiling=float(r.get("zone_high") or 0.0))
                if not _st_climb.get("ok"):
                    logger.info("  SURE THING %s: climb-out %s — %s", asset,
                                _st_climb.get("fail_gate"),
                                _st_climb.get("fail_reason"))
                    continue
                _score = float(r.get("score") or 0.0)
                _trig = str(r.get("trigger") or "crack")
                _est = int(r.get("est_min") or 60)
                _out_by = (_now_mst() + timedelta(
                    minutes=st.SURE_THING_WINDOW_MIN)).strftime("%I:%M").lstrip("0")
                if _trig == "pre-breakout":
                    _title = (f"🧲 BREAKOUT ALERT — BUY {asset} — PRE-BREAKOUT — "
                              f"~{_est} MIN WINDOW — {_score:.0f}% — {_mult:.2f}× SIZE")
                    _spoken = (f"Breakout alert! {asset} is about to break out! "
                               f"The setup scores {_score:.0f} percent — "
                               f"sizing at {_mult:.2f}"
                               + (f", flat tide — half size!" if _st_tide_mult < 1.0 else "!")
                               + f" The countdown starts now: about {_est} minutes, "
                               f"out by {_out_by}. Watch for the ceiling crack!")
                    _why = (f"{r['detail']} — the probability crossed the "
                            f"{st.PRE_BREAKOUT_THRESHOLD:.0f}% threshold while still "
                            f"inside the coil (entering BEFORE the breakout runs)")
                else:
                    _title = (f"🧲 BREAKOUT ALERT — BUY {asset} — BREAKOUT NOW — "
                              f"{_score:.0f}% SETUP — {_mult:.2f}× SIZE")
                    _spoken = (f"Breakout alert! {asset} is breaking out! "
                               f"{r['tests']} tests at the ceiling, the coil cracked! "
                               f"The setup scores {_score:.0f} percent — sizing at {_mult:.2f}"
                               + (f", flat tide — half size!" if _st_tide_mult < 1.0 else "!")
                               + f" Out by {_out_by} — if it hasn't paid, get out!")
                    _why = (f"{r['detail']} — the ceiling at ${r['zone_high']:.4f} cracked "
                            f"on a {r['vol_spike']:.1f}x volume burst")
                _st_tide_line = (f"TIDE SIZES IT: FLAT — {TIDE_FLAT_SIZE_FACTOR:.2f}× size = "
                                 f"${_spend:,.0f} (the stamped week: FLAT 22% win vs "
                                 f"RISING 57% — pickier, smaller)\n" if _st_tide_mult < 1.0 else "")
                _msg = (f"BREAKOUT ALERT — BUY {asset}"
                        + (f" — COUNTDOWN ~{_est} MIN" if _trig == "pre-breakout" else " — NOW")
                        + "\n"
                        f"WHY: {_why}\n"
                        f"WHEN: {'NOW — the crack' if _trig == 'crack' else 'BEFORE the crack — the estimated window is ~' + str(_est) + ' min'}\n"
                        f"HOW LONG: out by {_out_by} (the countdown — "
                        f"the {st.SURE_THING_WINDOW_MIN:.0f}-min contract — "
                        f"the coil's edge is a 4h structure; if it has not "
                        f"paid the goal by then, get out)\n"
                        f"SPEND: ${_spend:,.0f} (the 3% guard cap × {_mult:.2f} probability sizing"
                        + (f" × {_st_tide_mult:.2f} FLAT tide" if _st_tide_mult < 1.0 else "")
                        + f")\n"
                        + _st_tide_line
                        + str(_st_climb.get("output_block") or "")
                        + _danger_zone_panel(exchange, pair, float(_spend or 0.0),
                                             _st_goal, st.SURE_THING_WINDOW_MIN, _mult)
                        + f"{'-' * 30}\n"
                        f"CONTEST-ONLY — no bridge write. The DEEP SCAN ran before "
                        f"this popup (the fire-time rule — the Sep 6 waiver "
                        f"retired Sep 7); run it again now for live tracking. "
                        f"The death line never yields.\n"
                        f"  {_fmt_mst(_now_mst())}")
                notify_big(_title, _msg, spoken=_spoken)
                # AUTO-TRACK AT FIRE TIME — the breakout popup tracks too
                # (one writer, one contract; entry = the trigger level)
                auto_track_popup(pair, float(r.get("trigger_px") or 0.0),
                                 float(_spend or 0.0), float(_st_goal or 0.0),
                                 float(st.SURE_THING_WINDOW_MIN),
                                 f"BUY {_score:.0f}% (breakout {_trig})",
                                 "sure_thing")
                rotation_mark_fired(asset)
                _st_armed.pop(asset, None)
                _st_armed_save()
                _st_tide_pct, _st_tide_state = _fleet_tide()
                try:
                    _snap = {"pair": pair, "quick": _score, "brain": None,
                             "route": "sure-thing", "kind": "fired",
                             "tide": (f"{_st_tide_state} {_st_tide_pct:.0f}%"
                                      if _st_tide_state and _st_tide_pct is not None else None),
                             "trigger": _trig,
                             "detail": r["detail"],
                             "features": {"coil_range_pct": r.get("coil_range_pct"),
                                         "tests": r.get("tests"),
                                         "squeeze_pct": r.get("squeeze_pct"),
                                         "atr_ratio": r.get("atr_ratio"),
                                         "bb_width_pctile": r.get("bb_width_pctile"),
                                         "pullback_trend": r.get("pullback_trend"),
                                         "micro_spikes": r.get("micro_spikes"),
                                         "buy_dominance": r.get("buy_dominance"),
                                         "hist_hit_rate": r.get("hist_hit_rate"),
                                         "vol_spike": r.get("vol_spike"),
                                         "score": _score},
                             "mult": _mult, "spend": float(_spend or 0.0),
                             "goal_pct": _st_goal,
                             "est_min": _est,
                             "climb": _climb_features_stamped(
                                 _st_climb.get("features"),
                                 _st_tide_state, _st_tide_pct),
                             "entry": float(r.get("trigger_px") or 0.0),
                             "win_min": st.SURE_THING_WINDOW_MIN}
                    _log_signal_snapshot(_snap)
                except Exception as exc:
                    logger.warning("Sure Thing snapshot failed for %s (%s) "
                                   "— this fire's hindsight row is lost", asset, exc)
                logger.info("🧲 SURE THING FIRED (%s): %s — %s (prob %.0f, %.2fx, $%.0f)",
                            _trig, asset, r["detail"], _score, _mult, _spend)
                time.sleep(0.4)
            except Exception as exc:
                logger.debug("sure-thing scan failed for %s: %s", asset, exc)
                continue


    def _read_all_signals():
        """Read all signals from the SQLite bridge."""
        try:
            from signal_bridge import get_recent_signals
            return get_recent_signals(100)
        except Exception:
            return []

    # ── ONE VOICE ENGINE (user rule, Aug 27): the console drops every
    # utterance into a speech-queue directory; the scanner — the one
    # process that runs with the browser closed — speaks them. This is
    # the ONLY mouth. Console-origin lines get the same mic-check prefix
    # and the same amplified voice as scanner-origin lines.
    SPEECH_QUEUE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speech_queue")
    _last_speech_drain = 0.0
    SPEECH_STALE_S = 600  # older than 10 min = old news, never say it

    def _drain_speech_queue():
        """Speak and clear queued console lines (oldest first)."""
        nonlocal _last_speech_drain
        import glob as _glob, json as _json, os as _os
        _now = time.time()
        if _now - _last_speech_drain < 2.0:
            return
        _last_speech_drain = _now
        try:
            files = sorted(_glob.glob(os.path.join(SPEECH_QUEUE_DIR, "*.json")))
            for fp in files:
                try:
                    doc = _json.loads(open(fp, "r", encoding="utf-8").read())
                    _os.remove(fp)
                    if _now - float(doc.get("ts") or 0) > SPEECH_STALE_S:
                        continue  # stale — drop silently
                    text = str(doc.get("text") or "").strip()
                    if text and _voice_enabled():
                        _speak_server(VOICE_PREFIX + text, rate=int(doc.get("rate") or 2))
                except Exception:
                    try:
                        _os.remove(fp)
                    except OSError:
                        pass  # why: a temp voice file that refuses deletion is retried next drain
        except Exception as exc:
            logger.debug("speech queue drain failed: %s", exc)

    while True:
        now = _now_mst()
        hour = now.hour
        dow = now.weekday()  # 0=Mon, 6=Sun
        block_name, full_interval, min_conf, auto_trade_threshold, block_desc, no_buy, rot_key = get_time_block(hour, dow)

        # the ONE mouth: console lines get spoken here, always
        _drain_speech_queue()

        # ── TREND-EXIT WATCH (user rule, Aug 31 — RESTORED Sep 4, in
        # effect Sep 3) — ALWAYS runs, cooldown included (cooldown blocks
        # buys, never exits). For every pair the bot holds (bridge filled
        # records), the 1h knife; reversed → a SILENT bridge SELL
        # (source="scout_trend_exit") — no voice, no popup, no phone
        # push. The reader's two-ledger guard admits this ONE source
        # (plus scout_exit_check); the bot's own engine knife remains
        # the independent floor. Dup-safe (write_exit_signal skips while
        # a sell pends). Gated on SCOUT_TREND_EXIT_ENABLED.
        if SCOUT_TREND_EXIT_ENABLED:
            try:
                from signal_bridge import get_open_positions as _bop, write_exit_signal as _wes
                _held = _bop()
            except Exception:
                _held = []
        else:
            _held = []
        for _pos in (_held or []):
            _pair = (_pos or {}).get("pair")
            if not _pair:
                continue
            try:
                # pacing: a rejected/expired exit signal must not be
                # re-written every 30s pass — one attempt per pair per 15 min
                import time as _tt
                if _tt.time() - _trend_exit_pace.get(_pair, 0.0) < 900.0:
                    continue
                _rev, _note = knife_reversed_1h(exchange, _pair, cfg)
                if _rev:
                    _asset = _pair.split("/")[0]
                    _sid = _wes(_pair, reason=f"GET OUT NOW — trend reversed ({_note})",
                                source="scout_trend_exit")
                    _trend_exit_pace[_pair] = _tt.time()
                    if _sid:
                        logger.info("🔁 TREND EXIT: %s — 1h knife reversed — silent SELL "
                                    "written for the bot (signal %s)", _asset, _sid)
                    else:
                        logger.info("🔁 TREND EXIT: %s — knife reversed; exit already pending", _asset)
            except Exception as _exc:
                logger.debug("trend-exit watch failed for %s: %s", _pair, _exc)

        # ── Cooldown flatten: once per day at 20:00 (cooldown start) ────
        # The trading day ends at 20:00 — sell losers/flat (keep winners >5%
        # up), clear the bridge, ride flat through the overnight cooldown.
        # Fresh book + full balance at midnight when R1 Asia opens. The bot
        # has the entire day (00:00-20:00) to be patient.
        _today_flatten = now.strftime("%Y-%m-%d")
        if now.hour >= 20 and last_flatten_date != _today_flatten:
            last_flatten_date = _today_flatten
            _save_scanner_state({"last_flatten_date": _today_flatten})
            logger.info("=" * 60)
            logger.info("  COOLDOWN FLATTEN — trading day closed")
            logger.info("=" * 60)

            # 1. Find WINNERS to keep — positions up >5% ride through;
            #    the trailing stop handles their exit.
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
                            logger.info("  🏆 WINNER KEPT: %s up +%.1f%% — riding",
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

            # 3. Clear the signal bridge DB (winners stay open) — this is
            #    the daily ghost sweep; nothing stale survives the morning.
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

            logger.info("Cooldown flatten done | kept winners: %s",
                        ", ".join(p.split("/")[0] for p in keep_pairs) or "none")

        # rotation changes just log — no selling between morning flattens
        if current_block_name is not None and block_name != current_block_name:
            logger.info("Rotation %s -> %s (positions ride — cooldown flatten owns resets)",
                        current_block_name, block_name)
            # ── ROTATION-BOUNDARY EXCEPTION (user rule, Sep 4: "the goal
            # should reach before the rotation happens ideally. If
            # exception is made a warning fires with the rotation if
            # still tracking asset, full exit scan occurs to trigger a
            # warning light if necessary"): every asset still tracked
            # across the boundary gets the warning in the one voice, and
            # the FULL exit scan runs immediately (not on the 5-min
            # cadence) — its verdicts (BOT RULES: trailing stop / THESIS
            # DEAD / strategy sell) ARE the warning light. Bot positions:
            # the forced scan writes scout_exit_check exits the reader
            # executes. Contest watch: the warning speaks; the light's
            # own goal calls + sell bar carry the verdict from there.
            try:
                _bw = []
                try:
                    from signal_bridge import get_open_positions as _bop_b
                    for _pb in (_bop_b() or []):
                        _pa = (_pb or {}).get("pair")
                        if _pa:
                            _bw.append(_pa.split("/")[0])
                except Exception as exc:
                    # the bot-position names drive the warning light — a
                    # dead bridge read blanks half the light's coverage
                    logger.warning("bridge positions unreadable (%s) — "
                                   "the warning light covers contest "
                                   "watch only this cycle", exc)
                _cw = [str(_k).split("/")[0] for _k in (_load_contest_watch() or {}).keys()]
                if _bw:
                    last_exit_check = 0   # force the FULL exit scan this pass
                    logger.info("  ROTATION BOUNDARY: %d bot position(s) crossed (%s) — "
                                "full exit scan forced now", len(_bw), ", ".join(_bw))
                    _speak_server(f"Rotation passed! Still tracking {', '.join(_bw)}! "
                                  "Full exit scan running!", rate=1)
                for _a in _cw:
                    if _a not in _bw:
                        logger.info("  ROTATION BOUNDARY: contest watch still tracking %s "
                                    "— the light carries the verdict", _a)
                        _speak_server(f"Rotation passed! Still tracking {_a}! "
                                      "Check the exit!", rate=1)
            except Exception as _exc:
                logger.debug("rotation-boundary exception check failed: %s", _exc)
            # ── THE DISCIPLINE SCORE (user spec, Sep 4: "Did I leave
            # money on the table?"): evaluate the CLOSING rotation — its
            # goals vs the scanner's real data vs what the user did —
            # log it, learn from it, and speak the score + the insight
            # at the rollover (the dopamine delivery). The console's
            # MASTER page shows it side-by-side with the balance.
            try:
                import discipline as _disc
                # the closing rotation = the 4h block that ended when the
                # current one began (works across midnight: 00:xx -> 20-24)
                _closing_hour = ((now.hour // 4) * 4 - 4) % 24
                _prev_key = rotation_key_for_hour(_closing_hour)
                _dres = _disc.evaluate_rotation(exchange, _prev_key, speak=True)
                if _dres.get("score") is not None:
                    logger.info("🎓 DISCIPLINE: rotation %s score %.0f — left $%.0f "
                                "on the table (missed $%.0f / giveback $%.0f / no-exit $%.0f)",
                                _prev_key, float(_dres["score"]),
                                float(_dres.get("total_left") or 0),
                                float(_dres["usd_left"].get("missed") or 0),
                                float(_dres["usd_left"].get("giveback") or 0),
                                float(_dres["usd_left"].get("missed_exit") or 0))
                    logger.info("   lesson: %s", _dres.get("lesson"))
            except Exception as _exc:
                logger.debug("discipline evaluation failed: %s", _exc)

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
                        # ANTI-SPAM RETIRED (user rule, Sep 10): the 10-min
                        # per-pair skip is OFF — every pass re-evaluates and
                        # the gates decide.
                        signal = evaluate_and_check(pair, df, min_conf)
                        if signal:
                            send_alert(pair, signal, auto_trade_threshold)
                            log_winner(pair, signal)
                            alerted_recently[pair] = now.timestamp()
                else:
                    logger.info("  No significant movers in challenge assets")

                # ── THE MOVER FUNNEL (user rule, Aug 31: one funnel, bot-
                #    only, widened to all Kraken pairs). Today's runners
                #    (≥+5% on 24h — the ZORA/XMR case) plus yesterday's
                #    losers turning (the old bounce list, folded in here).
                #    Writes bridge signals (source="scout_mover"); the
                #    contest path (desktop 89 floor, QC, watch) is untouched.
                #    Kept gates, on purpose: deep read ≥60 + no downward
                #    trend (the brain's floor), tide OUT, listing age ≥4
                #    days. RETIRED Sep 10: the rotation lockout (re-fires
                #    free) and the 10-min per-pair anti-spam. The bot's own
                #    liquidity gates + carry cap + exit ladder protect the
                #    money.
                _mover_cands = []
                try:
                    _mover_cands = [(m["pair"], m["pct"]) for m in
                                    fetch_kraken_movers(exchange, top_n=MOVER_TOP_N)]
                except Exception as exc:
                    logger.debug("Mover fetch failed: %s", exc)
                for _bp in cached_loser_pairs:
                    if _bp not in [p for p, _ in _mover_cands]:
                        _mover_cands.append((_bp, -5.0))  # was a loser, now bouncing

                for pair, _mv_pct in _mover_cands:
                    # rescan pacing: every 10 min per pair, isolated from
                    # alerted_recently so the contest ticker path is untouched
                    if (now.timestamp() - _mover_scanned.get(pair, 0)) < 600:
                        continue
                    _mover_scanned[pair] = now.timestamp()
                    try:
                        ohlcv = exchange.fetch_ohlcv(pair, timeframe="1h", limit=200)
                        if not ohlcv or len(ohlcv) < MOVER_MIN_CANDLES:
                            continue  # fresh listing / not enough history to trust
                        df = pd.DataFrame(ohlcv, columns=["timestamp","open","high","low","close","volume"])
                        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                        df.set_index("timestamp", inplace=True)

                        # bot-grade floor — NOT the tournament's adaptive bar
                        # (XMR's 56% died against ~80; the funnel takes it)
                        signal = evaluate_and_check(pair, df, MOVER_MIN_CONF)
                        if not signal:
                            continue
                        asset = pair.split("/")[0]

                        # mover move gate: aggressive bar (avg move) —
                        # a runner/bounce needs room to travel
                        try:
                            from volatility_engine import get_move_threshold
                            gm_thr = get_move_threshold(pair, rot_key, _schedule_for_dow(dow), level="aggressive")
                            if gm_thr is not None and signal.entry_price and signal.take_profit:
                                gm_proj = (signal.take_profit - signal.entry_price) / signal.entry_price * 100
                                if gm_proj < gm_thr:
                                    continue
                        except Exception as exc:
                            # the move gate is a SKIP guard — a dead read
                            # lets a short-travel popup through, visibly
                            logger.warning("mover move-gate read failed for "
                                           "%s (%s) — the popup ships "
                                           "without its room-to-travel check", pair, exc)

                        # rotation lockout RETIRED (user rule, Sep 10):
                        # re-fires are free everywhere; the call stays for
                        # the re-entry display on the mover popup
                        _m_allowed, _m_lock, _m_re, _m_fine = rotation_gate(asset)

                        # deep read ≥60 + no downward trend — the quality floor
                        # (the PUMP lesson: never buy what the brain dislikes).
                        # Fail-open on errors, same as the bridge pre-check.
                        try:
                            from strategy import evaluate_strategy as _ev, \
                                enable_deep_analysis, disable_deep_analysis
                            enable_deep_analysis()
                            _deep_sig = _ev(df, cfg)
                            disable_deep_analysis()
                            from indicators import ema as _ema_mv
                            _es_mv = float(_ema_mv(df["close"], cfg.ema_short).iloc[-1])
                            _el_mv = float(_ema_mv(df["close"], cfg.ema_long).iloc[-1])
                            if (_deep_sig.action != "buy" or _deep_sig.confidence < 60
                                    or (_es_mv < _el_mv and float(df["close"].iloc[-1]) < _el_mv)):
                                logger.info("  MOVER VETO: %s deep-reads %s %.0f%% — the brain says no",
                                            asset, _deep_sig.action, _deep_sig.confidence)
                                _log_suppression(pair, "deep-veto",
                                                 f"mover: deep-reads {_deep_sig.action} "
                                                 f"{_deep_sig.confidence:.0f}% — the brain says no",
                                                 quick=float(signal.confidence),
                                                 brain=float(_deep_sig.confidence), rot=rot_key)
                                continue
                        except Exception as _exc:
                            logger.debug("mover deep read unavailable for %s: %s (fail-open)", pair, _exc)

                        # tide: stamp every write; OUT = no entries anywhere
                        # on the BOT ledger — EXCEPT THE LONE-WOLF CARVE-OUT
                        # (user rule, Sep 4, the POPCAT lesson): a TRUE solo
                        # runner (own 24h move ≥ the lone-wolf tier — the
                        # mover's own _mv_pct) in an OUT tide gets the
                        # CONTEST popup, marked; the BRIDGE WRITE stays
                        # blocked (two ledgers — the bot never buys an OUT
                        # tide). Bounce candidates (yesterday's losers,
                        # _mv_pct −5) don't carve — a bounce isn't a solo
                        # runner.
                        _tpw, _tsw = _fleet_tide()
                        _m_out_lw = bool(_tsw == "OUT"
                                         and _mv_pct is not None
                                         and float(_mv_pct) >= LONE_WOLF_MOVE_MIN)
                        if _tsw == "OUT" and not _m_out_lw:
                            logger.info("  MOVER SKIP: %s — tide OUT (fleet %.0f%%) — all ships sink together",
                                        asset, _tpw or 0)
                            _log_suppression(pair, "tide-out",
                                             f"mover: fleet {_tpw or 0:.0f}% up — all ships sink "
                                             f"together; own 24h {_mv_pct:+.0f}% < the "
                                             f"+{LONE_WOLF_MOVE_MIN:.0f}% carve-out",
                                             quick=float(signal.confidence),
                                             brain=(float(_deep_sig.confidence)
                                                    if _deep_sig is not None else None),
                                             tide=f"OUT {_tpw or 0:.0f}%", rot=rot_key)
                            continue
                        if _m_out_lw:
                            logger.info("  OUT-TIDE LONE WOLF: %s — mover %+.0f%% on 24h against "
                                        "an OUT tide (fleet %.0f%%) — no bridge write (the bot stays "
                                        "out); the contest popup fires marked",
                                        asset, _mv_pct, _tpw or 0)

                        logger.info("  🚀 MOVER SIGNAL: %s %s (%.0f%%, 24h %+.0f%%) — mover outside the 54",
                                    signal.action.upper(), asset, signal.confidence, _mv_pct)

                        # persist to the volatility universe so the engine
                        # learns this coin's stats for future thresholds
                        try:
                            from volatility_engine import remember_gainer
                            remember_gainer(pair, _mv_pct)
                        except Exception as exc:
                            logger.debug("remember_gainer failed for %s (%s) "
                                         "— the engine's stats update waits", pair, exc)

                        # write to the bridge — bot may take it (tide-stamped).
                        # OUT tide = NO bridge write ever (the bot's ledger
                        # hard-blocks OUT — the carve-out above is the
                        # CONTEST popup only; two ledgers, different rules).
                        if _tsw != "OUT":
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
                                    reasons=[f"Mover: {_mv_pct:+.0f}% on 24h — running outside the 54", *signal.reasons],
                                    source="scout_mover",
                                    reason_summary=f"Mover: {_mv_pct:+.0f}% on 24h",
                                    min_confidence=50,
                                    tide_state=(_tsw or "UNKNOWN"),
                                    tide_pct=_tpw,
                                )
                                if sid:
                                    logger.info("  Mover signal written — Trader will evaluate %s", asset)
                            except Exception as exc:
                                logger.warning("  Could not write mover to bridge: %s", exc)

                        # CONTEST POPUPS ARE THE 54 (user rule, Sep 9: XMR
                        # popped on the desktop but "popups are for the 54
                        # pairs in the contest only -- the Buy XMR should
                        # have silently gone to the bridge to be
                        # challenged"). The bridge write above already
                        # carried this non-fleet mover to the bot; the
                        # desktop stays silent: the popup gauntlet below
                        # (fire snapshot, brain sizing, cap, voice) is
                        # fleet-only. log_winner/alerted_recently stay
                        # fleet-side too -- no popup, nothing to grade.
                        if pair not in set(cfg.trading_pairs):
                            logger.info("  MOVER %s -- bridge-only: popups are for the 54", asset)
                            continue
                        # desktop: THE NEW-GAME MOVER POPUP (user option 1,
                        # Sep 2: "convert the MOVER popup to the new-game
                        # format — same gauntlet as champion popups").
                        # ADVISORY BRAIN (user rule, Sep 5): the scan must
                        # RUN and its DIRECTION must agree — but the 89
                        # desktop floor is RETIRED; the brain's number
                        # SIZES the popup (the same bucket table as the
                        # champion path). The popup still speaks the
                        # BRAIN'S number (the Aug 27 no-lying-brags rule),
                        # carries the goal clock, the stale-move guard,
                        # the 3% death-line guard sizing, and the book-
                        # carry verdict. The bridge write above is the
                        # bot's ledger — its gates are the bot's own.
                        try:
                            _m_fire_snap = _begin_fire_snapshot(
                                exchange, pair, float(signal.confidence),
                                action=signal.action, route="mover",
                                entry=float(signal.price or 0.0))
                        except Exception as exc:
                            logger.warning("mover fire snapshot failed for %s (%s) "
                                           "— this fire's hindsight row is lost", pair, exc)
                        try:
                            _m_desk_ok = (_deep_sig is not None
                                          and _deep_sig.action == "buy")
                        except Exception:
                            _m_desk_ok = False  # fail-closed: no brain read, no popup (the JUP lesson — KEPT)
                        if not _m_desk_ok:
                            logger.info("  Mover %s %.0f%% — bot-only (the scan "
                                        "didn't run or contradicts the buy)", asset, signal.confidence)
                            _log_suppression(pair, "fail-closed" if _deep_sig is None else "deep-direction",
                                             f"mover: {'the brain could not be read' if _deep_sig is None else 'the scan reads ' + str(_deep_sig.action) + ' — it contradicts the buy'} "
                                             f"(fire-time rule: the scan must run; the number is advisory)",
                                             quick=float(signal.confidence),
                                             brain=(float(_deep_sig.confidence)
                                                    if _deep_sig is not None else None),
                                             floor=89.0,
                                             tide=f"{_tsw} {_tpw or 0:.0f}%", rot=rot_key)
                        else:
                            _m_brain = None
                            try:
                                if _deep_sig is not None:
                                    _m_brain = float(_deep_sig.confidence)
                            except Exception:
                                _m_brain = None
                            _m_dc = _m_brain if _m_brain is not None else float(signal.confidence)
                            # ── THE FAST FUNNEL (Sep 6 — same router as the
                            # champion path): momentum-class movers are the
                            # fast funnel's natural feed; the worth floor
                            # waives, every cap stands
                            _m_fast, _m_fast_why = False, ""
                            try:
                                from hindsight_scorer import fast_route
                                _m_fast, _m_fast_why = fast_route(
                                    (_SNAPSHOT_CTX.get("ctx") or {}).get("features"))
                            except Exception:
                                _m_fast, _m_fast_why = False, ""
                            if _m_fast:
                                logger.info("  🚀 MOVER FAST ROUTE: %s", _m_fast_why)
                            # THE STALE-MOVE GUARD — a runner that peaked
                            # hours ago is a sagging top (the APT shape).
                            # A mover popup is never a true first-fire —
                            # the runner already ran — so EVERY mover popup
                            # gets the wiggle check, not just re-entries.
                            _m_stale = _stale_move(exchange, pair)
                            if _m_stale:
                                logger.info("  MOVER STALE MOVE: %s — 4h high %.0fh ago, "
                                            "%.1f%% below it — popup skipped (the wiggle trap)",
                                            asset, _m_stale[0], _m_stale[1])
                                _log_suppression(pair, "stale-move",
                                                 f"mover: 4h high {_m_stale[0]:.0f}h ago, "
                                                 f"{_m_stale[1]:.1f}% below it (the wiggle trap)",
                                                 quick=float(signal.confidence),
                                                 brain=(float(_deep_sig.confidence)
                                                        if _deep_sig is not None else None),
                                                 floor=89.0,
                                                 tide=f"{_tsw} {_tpw or 0:.0f}%", rot=rot_key)
                            else:
                                # THE GOAL CLOCK — same math as the champion popup
                                # (Sep 4: the asset's OWN week of daily candles;
                                # the window bounded by the rotation boundary)
                                _m_goal = _market_goal_pct(exchange, pair)
                                _m_win = _rotation_window_min()
                                _m_out_by = (_now_mst() + timedelta(
                                    minutes=round(_m_win / 5.0) * 5)).strftime("%I:%M").lstrip("0")
                                # sizing: the LIVE contest account + book
                                # carry + THE 3% DEATH-LINE GUARD (same
                                # math as send_alert; fail-open on a broken
                                # read — the popup then names no size)
                                _m_bal = 0.0
                                _m_spend = 0.0
                                _m_carry = None
                                _m_wdd = None
                                _m_skip = None
                                _m_mult = 1.0
                                _m_guard_capped = False
                                _m_climb = None  # fail-open init: a broken read never NameErrors the popup
                                try:
                                    import contest_account as _ca
                                    _m_acct = _ca.load_state()
                                    _m_bal = float(_m_acct["current_balance"])
                                    _m_spend = _m_bal
                                    try:
                                        from execution_tiers import measure_book
                                        _m_book = measure_book(exchange, pair)
                                        _m_tob = float((_m_book or {}).get("ask_tob") or 0.0)
                                        if _m_tob > 0:
                                            _m_carry = _m_tob * 0.20
                                            if _m_spend > _m_carry:
                                                _m_fit = [c * _m_spend for c in
                                                          (0.25, 0.50, 0.75, 1.00)
                                                          if c * _m_spend <= _m_carry]
                                                _m_spend = max(_m_fit) if _m_fit else 0.0
                                    except Exception:
                                        _m_carry = None
                                    if _m_carry is not None and _m_spend <= 0:
                                        _m_skip = (f"MOVER TOO THIN for contest size "
                                                   f"(book carries ~${_m_carry:,.0f}) — desktop suppressed")
                                    else:
                                        _m_wdd = _ca.window_drawdown_pct(pair)
                                        _m_risk = _ca.max_entry_usd(
                                            _m_bal, _m_wdd, float(_m_acct["start_balance"]))
                                        if _m_risk <= 0:
                                            _m_skip = ("MOVER DEATH LINE: buffer gone "
                                                       f"— no buy popup (the platform -3% line "
                                                       f"sits at ${_ca.death_line(_m_acct):,.2f})")
                                        else:
                                            if _m_spend > _m_risk:
                                                _m_spend = _m_risk
                                                _m_guard_capped = True
                                            # ADVISORY-BRAIN SIZING (user rule, Sep 5 —
                                            # same bucket table as the champion path):
                                            # the brain's number multiplies the guard-
                                            # capped spend; the worth floor reads the
                                            # SCALED spend after.
                                            try:
                                                from hindsight_scorer import brain_size_mult
                                                _m_mult = brain_size_mult(_m_dc)
                                            except Exception:
                                                _m_mult = 1.0
                                            if _m_mult < 1.0:
                                                _m_spend = _m_spend * _m_mult
                                                logger.info("  MOVER ADVISORY BRAIN SIZING: "
                                                            "%.2fx → $%.0f", _m_mult, _m_spend)
                                            # ── THE FLAT SIZING FACTOR (Sep 7): the
                                            #    same discount as the champion path —
                                            #    the tide data spoke (FLAT 22% vs 57%)
                                            if (_tsw or "") == "FLAT":
                                                _m_spend = _m_spend * TIDE_FLAT_SIZE_FACTOR
                                                logger.info("  MOVER FLAT TIDE SIZING: "
                                                            "%.2fx → $%.0f", TIDE_FLAT_SIZE_FACTOR,
                                                            _m_spend)
                                            # ── THE CLIMB-OUT GATES (Sep 6):
                                            #    same hard gate as the champion
                                            #    path — no escape route, no trade
                                            _m_climb = _climb_out_check(
                                                exchange, pair,
                                                float(signal.price or 0.0),
                                                float(_m_goal or 1.5),
                                                float(_m_spend or 0.0))
                                            if not _m_climb.get("ok"):
                                                _mfg = _m_climb.get("fail_gate") or "?"
                                                _m_skip = (f"MOVER CLIMB-OUT {_mfg}: "
                                                           f"{_m_climb.get('fail_reason')}")
                                            # THE WORTH FLOOR (same rule as the champion
                                            # popup — the JUP lesson): the sized entry at
                                            # its goal must pay at least the dial's share
                                            # of the daily pace, or the popup is bumped.
                                            # (Sep 6) THE FAST FUNNEL WAIVES IT (the
                                            # caps carry the risk alone).
                                            _m_pace = _ca.daily_pace_usd(_m_acct)
                                            _m_wf = _worth_floor_frac()  # the console dial, live
                                            _m_worth = _m_spend * (_m_goal / 100.0)
                                            if _m_worth < _m_pace * _m_wf and not _m_fast:
                                                _m_skip = (f"MOVER WORTH FLOOR: ${_m_spend:,.0f} at the "
                                                           f"+{_m_goal:.1f}% goal pays only ${_m_worth:,.0f} "
                                                           f"(< ${_m_pace * _m_wf:,.0f}, {_m_wf:.0%} of "
                                                           f"the daily pace) — popup bumped")
                                except Exception as _exc:
                                    logger.debug("mover 3%% guard unavailable: %s", _exc)  # fail-open
                                if _m_skip:
                                    logger.info("  %s", _m_skip)
                                    _m_gate = ("too-thin" if "TOO THIN" in _m_skip
                                               else "worth-floor" if "WORTH FLOOR" in _m_skip
                                               else "death-line")
                                    _log_suppression(pair, _m_gate, f"mover: {_m_skip}",
                                                     quick=float(signal.confidence),
                                                     brain=(float(_deep_sig.confidence)
                                                            if _deep_sig is not None else None),
                                                     floor=89.0,
                                                     tide=f"{_tsw} {_tpw or 0:.0f}%", rot=rot_key)
                                else:
                                    logger.info("  MOVER POPUP: brain %.0f%% (quick %.0f%%) — "
                                                "goal +%.1f%% by %s",
                                                _m_dc, float(signal.confidence),
                                                _m_goal, _m_out_by)
                                    _m_tide_line = ""
                                    _m_tide_say = ""
                                    if _tsw == "RISING":
                                        _m_tide_line = f"TIDE: RISING ({_tpw:.0f}% of the fleet up) — the market carries greens\n"
                                        _m_tide_say = "The tide is rising! "
                                    elif _tsw == "FLAT":
                                        _m_tide_line = f"TIDE: FLAT ({_tpw:.0f}% of the fleet up) — pickier, smaller\n"
                                    elif _m_out_lw:
                                        # THE OUT-TIDE LONE-WOLF MARK (user rule, Sep 4):
                                        # this popup fired AGAINST the fleet — say so,
                                        # so it's never mistaken for a fleet move.
                                        _m_tide_line = (f"TIDE: OUT ({_tpw:.0f}% up) — LONE WOLF: "
                                                        f"{_mv_pct:+.0f}% on her own against the fleet\n")
                                        _m_tide_say = "A lone wolf against an out tide! "
                                    _m_re_line = "  RE-ENTRY: fired earlier today\n" if _m_re else ""
                                    _m_re_say = "Re-entry! " if _m_re else ""
                                    # the SPEND line (3% guard / small coin)
                                    if _m_guard_capped:
                                        _m_spend_line = (f"  SPEND: ${_m_spend:,.0f} — THE 3% GUARD: this coin's "
                                                         f"worst-case window drawdown is {_m_wdd:.1f}%; "
                                                         f"${_m_spend:,.0f} is the most the death line can afford\n")
                                    elif _m_carry is not None and _m_spend < _m_bal:
                                        _m_spend_line = (f"  SPEND: {_m_spend / _m_bal:.0%} = ${_m_spend:,.0f} — "
                                                         f"SMALL COIN: this book carries ${_m_spend:,.0f}, "
                                                         f"not ${_m_bal:,.0f}\n")
                                    elif _m_carry is not None:
                                        _m_spend_line = f"  SPEND: 100% = ${_m_bal:,.0f} — big book, carries all-in\n"
                                    else:
                                        _m_spend_line = ""
                                    # HER SIZE VOICE (the four verdicts; too thin
                                    # never reaches here — it's suppressed)
                                    if _m_carry is not None and 0 < _m_spend < _m_bal:
                                        _m_size_say = f"Small size only — {_m_spend:,.0f} dollars! "
                                    elif _m_carry is not None:
                                        _m_size_say = f"Big book — all {_m_bal:,.0f} dollars in fine! "
                                    else:
                                        _m_size_say = "Book unreadable — your call on size! "
                                    _m_title = (f"🚀 MOVER BUY {asset} — {_m_dc:.0f}% SURE"
                                                + (f" — {_m_mult:.2f}× SIZE" if _m_mult < 1.0 else "")
                                                + (" — FAST ROUTE" if _m_fast else "")
                                                + (" — LONE WOLF (OUT TIDE)" if _m_out_lw else ""))
                                    _m_spoken = (f"Buy {asset}! {_m_dc:.0f} percent sure! "
                                                 + (f"The brain says {_m_dc:.0f} percent — sizing at {_m_mult:.2f}! "
                                                    if _m_mult < 1.0 else "")
                                                 + ("Fast route! " if _m_fast else "")
                                                 + _m_re_say
                                                 + f"Runner outside the 54 — moving {_mv_pct:+.0f} percent on the day! "
                                                 + f"Aim for plus {_m_goal:.0f} percent! "
                                                 + _m_size_say
                                                 + _m_tide_say
                                                 + f"If it hasn't paid by {_m_out_by}, get out!")
                                    _m_msg = (
                                        f"BUY {asset} — MOVER\n"
                                        f"WHY: runner outside the 54 ({_mv_pct:+.0f}% on 24h), and the brain "
                                        f"reads the setup at {_m_dc:.0f}%\n"
                                        f"HOW LONG: goal +{_m_goal:.1f}% by {_m_out_by} — "
                                        f"if it hasn't paid by then, get out.\n"
                                        + (f"BRAIN SIZES IT: {_m_dc:.0f}% → {_m_mult:.2f}× size = "
                                           f"${_m_spend:,.0f} — the scan ran; the number is advisory "
                                           f"(it sizes, it never blocks)\n" if _m_mult < 1.0 else "")
                                        + (f"TIDE SIZES IT: FLAT — {TIDE_FLAT_SIZE_FACTOR:.2f}× size = "
                                           f"${_m_spend:,.0f} (the stamped week: FLAT 22% win vs "
                                           f"RISING 57% — pickier, smaller)\n" if _tsw == "FLAT" else "")
                                        + (f"🚀 FAST ROUTE: {_m_fast_why} — minimal gates; the "
                                           f"3% guard carries the risk\n" if _m_fast else "")
                                        + f"{_m_tide_line}"
                                        f"{_m_re_line}"
                                        f"{_m_spend_line}"
                                        + str(_m_climb.get("output_block") or "")
                                        + _danger_zone_panel(exchange, pair,
                                                             float(_m_spend or 0.0),
                                                             float(_m_goal or 1.5),
                                                             float(_m_win or 60.0),
                                                             _m_mult)
                                        + f"{'-' * 30}\n"
                                        f"get out at ${signal.stop_loss:.4f} (wrong)\n"
                                        f"cash out at ${signal.take_profit:.4f} (win)\n"
                                        f"{'-' * 30}\n"
                                        f"  {_fmt_mst(_now_mst())}"
                                    )
                                    # the FIRED snapshot — the mover
                                    # rides the same fire rules as the
                                    # champion path (Sep 5)
                                    # (Sep 9) the popup cap retired — the mover fires
                                    # like every path: the worth floor (the buffer is
                                    # its bar) + the deep read gate it
                                    notify_big(_m_title, _m_msg, spoken=_m_spoken)
                                    # AUTO-TRACK AT FIRE TIME — the mover
                                    # popup tracks too (the ZEC lesson: a
                                    # fired mover must never sit unwatched)
                                    auto_track_popup(pair,
                                                     float(signal.price or 0.0),
                                                     float(_m_spend or 0.0),
                                                     float(_m_goal or 0.0),
                                                     float(_m_win or 0.0),
                                                     f"BUY {signal.confidence:.0f}% (mover)",
                                                     "mover")
                                    try:
                                        _mfs = dict(_SNAPSHOT_CTX.get("ctx") or {})
                                        _mfs.update({"kind": "fired",
                                                     "route": ("mover-fast" if _m_fast else "mover"),
                                                     "tide": (f"{_tsw} {_tpw:.0f}%"
                                                              if _tsw and _tpw is not None else None),
                                                     "fast_why": _m_fast_why,
                                                     "brain": _m_dc, "mult": _m_mult,
                                                     "spend": float(_m_spend or 0.0),
                                                     "goal_pct": float(_m_goal or 0.0),
                                                     "win_min": float(_m_win or 0.0),
                                                     "entry": float(signal.price or 0.0),
                                                     "climb": _climb_features_stamped(
                                                         _m_climb.get("features") if _m_climb else None,
                                                         _tsw, _tpw)})
                                        _log_signal_snapshot(_mfs)
                                    except (ValueError, TypeError, KeyError) as exc:
                                        logger.debug("Mover snapshot build failed for %s: %s", pair, exc)
                        log_winner(pair, signal)
                        alerted_recently[pair] = now.timestamp()
                    except Exception as exc:
                        logger.debug("Mover scan failed for %s: %s", pair, exc)
            except Exception as exc:
                logger.warning("Ticker scan error: %s", exc)

        # ── CONTEST LIGHT (every loop pass ~30s): the scalp is a 5-minute
        #    game — TAKE IT at +$20 must fire fast, not on a slow cadence.
        #    The all-good voice inside keeps its own 60s cadence.
        if (now.timestamp() - last_contest_light) >= 25:
            last_contest_light = now.timestamp()
            try:
                contest_light(exchange, cfg)
            except Exception as exc:
                logger.debug("contest light error: %s", exc)

        # ── THE SURE THING (every 5 minutes): the fleet-wide breakout
        #    scan — coils announced once, cracks fired at the moment
        #    (contest-only, the deep scan waived by the user's call)
        if (now.timestamp() - last_sure_thing_scan) >= 300:
            last_sure_thing_scan = now.timestamp()
            try:
                _sure_thing_scan(exchange)
            except Exception as exc:
                logger.debug("sure thing scan error: %s", exc)

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
                        except (ValueError, TypeError):
                            pass  # why: unreadable executed_at falls through to a normal exit check

                    # Skip if already alerted recently — RETIRED (Sep 10):
                    # re-alerts are free; every gate re-decides per pass

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

                        # REAL-TIME (Sep 3): the ticker is the price truth —
                        # seed the in-progress candle and use the live price
                        # for the trailing stop (candle closes lag).
                        _rt_live = float(df["close"].iloc[-1])
                        try:
                            _rt_tk = exchange.fetch_ticker(pair)
                            if _rt_tk and _rt_tk.get("last"):
                                _rt_live = float(_rt_tk["last"])
                                _rt_idx = df.index[-1]
                                df.loc[_rt_idx, "close"] = _rt_live
                                df.loc[_rt_idx, "high"] = max(float(df.loc[_rt_idx, "high"]), _rt_live)
                                df.loc[_rt_idx, "low"] = min(float(df.loc[_rt_idx, "low"]), _rt_live)
                        except Exception as exc:
                            logger.debug("Realtime ticker overlay failed for %s: %s", pair, exc)

                        exit_sig = _eval(df, cfg)

                        # ── DCA tranche check: dipped but trend intact — add to position ──
                        # RETIRED behind SCOUT_DCA_ENABLED=False (user option 2, Sep 2):
                        # the wiggle-buy the new game retired — averaging down a
                        # dipped position on a quick >=50 read with no brain bar,
                        # no 3% death-line guard, no goal clock, and the reader
                        # folds scout_dca writes into REAL money. Code kept per
                        # the don't-delete rule; flip the flag to re-arm (only by
                        # the user's explicit call).
                        try:
                            entry = s.get("execution_price") or s.get("entry_price")
                            live = float(df["close"].iloc[-1])
                            if entry and live:
                                dip_pct = (entry - live) / entry * 100
                                from signal_bridge import count_dca_signals, write_buy_signal
                                if (SCOUT_DCA_ENABLED and not no_buy and dip_pct >= 2.5
                                        and exit_sig.action == "buy"
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
                        live_price = _rt_live
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

                        # ── THE SYNTHESIS, BOT RULES ONLY (user rules, Sep 4:
                        # "every decision should now be based on the real
                        # time data + brain knowledge + scout history" →
                        # "Yes, add that" — then the ledger correction:
                        # "the bot rules are different than the contest
                        # rules ON PURPOSE — the bot is optimized to make
                        # the most money as possible"): the bot's bridge
                        # exit check asks the BOT'S questions on real
                        # money —
                        #   1. REAL-TIME: the ticker already seeds the
                        #      in-progress candle; live_price is the truth
                        #   2. POSITION: entry conviction from the bridge
                        #      record itself (the bot's ledger)
                        #   3. THESIS: a buy thesis is alive only while the
                        #      current read is buy (or an ambiguous hold
                        #      <60) — a sell read or a confident hold kills
                        #      it. The ASTER rule: don't hold weak
                        #      positions; the trail rides the strong ones.
                        # The GOAL CLOCK (GOAL REACHED / WINDOW EXPIRED)
                        # is CONTEST-ONLY — the bot doesn't bank at 1-3%
                        # or exit on a window; it rides winners to its own
                        # exits (SL −5% → TP +10% → knife → ATR trail →
                        # strategy sell). Fail-soft: a record without entry
                        # data keeps only the mechanical gates below.
                        try:
                            _syn_entry = float(s.get("execution_price") or s.get("entry_price") or 0)
                            _syn_conf = float(s.get("confidence") or 0)
                            if _syn_entry > 0 and live_price > 0 and not should_exit:
                                if not (exit_sig.action == "buy"
                                        or (exit_sig.action == "hold" and exit_sig.confidence < 60)):
                                    should_exit = True
                                    exit_reason = (f"THESIS DEAD: entered {_syn_conf:.0f}% on a buy read, now "
                                                   f"reads {exit_sig.action} {exit_sig.confidence:.0f}% — "
                                                   f"the reason we entered isn't alive")
                                    logger.info("  🧠 THESIS DEAD: %s entered %.0f%%, now reads %s %.0f%% — "
                                                "the reason we entered isn't alive",
                                                pair.split("/")[0], _syn_conf,
                                                exit_sig.action, exit_sig.confidence)
                        except Exception as exc:
                            logger.debug("synthesis check failed for %s: %s", pair, exc)

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
                            # TWO-LEDGER (updated Sep 4): write_exit_signal
                            # defaults to source="scout_exit_check" — the
                            # ONE sell source the Sep 3 reader admits on
                            # REAL money (trailing stop / strategy sell /
                            # exhaustion all land on the bot's account).
                            # Every other sell source stays contest-only.
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
                        except Exception as exc:
                            logger.debug("Tournament eval failed for %s: %s", pair, exc)

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
                                # anti-spam gate RETIRED (Sep 10) — fires are
                                # free; the checklist still stands
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
                            # anti-spam gate RETIRED (Sep 10) — fires are
                            # free; the checklist still stands
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
    # THE SINGLE-INSTANCE GUARD (Sep 8): the launch path runs the venv
    # python twice over (the uv trampoline spawns the real interpreter
    # with the same command line), so process-count checks see a
    # phantom second scanner -- and a REAL second one is a real-money
    # risk: two scanners write the bridge, and the spot account does
    # not know a signal was already acted on. Atomic O_EXCL pid file:
    # the first process owns it; any second one refuses to start; a
    # crashed run's stale file (its pid no longer alive) is unlinked
    # and taken over. The guard FAILS CLOSED (Copilot, Sep 8): a guard
    # error means we cannot know whether another scanner is live, and
    # an unguarded duplicate double-signals the bridge -- so the start
    # aborts loudly instead of continuing unprotected. (The popups
    # missing is visible within minutes; a double order is not.)
    _GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".scanner.pid")
    _owned = False
    _gerr = None
    for _attempt in (1, 2):   # one retry: a transient AV hold on the
        try:                  # file must not kill the scanner outright
            if os.path.exists(_GUARD):
                _old = ""
                try:
                    _old = open(_GUARD, "r", encoding="utf-8").read().strip()
                except OSError:
                    pass  # why: an unreadable guard file is treated as stale and taken over
                _alive = False
                if _old.isdigit():
                    try:
                        # ctypes OpenProcess: os.kill(pid, 0) succeeds
                        # on Windows for ANY pid (it is a POSIX no-op
                        # there), so the takeover check needs a real
                        # liveness probe. (Sep 11: alive must ALSO mean
                        # "a python owns the pid" -- after a crash the
                        # OS recycles the pid onto any exe, and a plain
                        # OpenProcess hit then made the guard refuse
                        # forever, a silent no-start. Same rule as
                        # main()'s tasklist probe: any OTHER image on
                        # the pid is a stale lock, take it back.)
                        import ctypes
                        import subprocess as _sp2
                        _k32 = ctypes.windll.kernel32
                        _SYNCHRONIZE = 0x00100000
                        _h = _k32.OpenProcess(_SYNCHRONIZE, False, int(_old))
                        if _h:
                            _k32.CloseHandle(_h)
                            _probe = _sp2.run(
                                ["tasklist", "/FI", f"PID eq {_old}",
                                 "/FO", "CSV", "/NH"],
                                capture_output=True, text=True, timeout=5)
                            _alive = "python" in (_probe.stdout or "").lower()
                        # no handle -> pid dead -> stale, take over
                    except Exception:
                        _alive = True   # unknown -- assume alive, stay down
                else:
                    _alive = False   # empty/corrupt file: nothing owns it
                if _alive:
                    print(f"A scanner is already running (pid {_old}). Not "
                          f"starting a second one -- the single-instance "
                          f"guard refused. Close that one first.")
                    sys.exit(0)
                print(f"Stale scanner pid file ({_old or 'empty'}) -- "
                      f"taking over.")
                try:
                    os.unlink(_GUARD)   # the stale file blocks O_EXCL
                except FileNotFoundError:
                    pass  # why: nothing to unlink means the takeover race resolved itself
            _fd = os.open(_GUARD, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(_fd, str(os.getpid()).encode())
            finally:
                os.close(_fd)
            _owned = True
            break
        except SystemExit:
            raise
        except Exception as _exc:
            _gerr = _exc
            time.sleep(1.0)
    if not _owned:
        print(f"SINGLE-INSTANCE GUARD FAILED ({_gerr}) -- refusing to "
              f"start: cannot verify no other scanner is live, and an "
              f"unguarded duplicate double-signals the bridge (real "
              f"money). Fix the error above, then relaunch.")
        sys.exit(1)
    try:
        atexit.register(lambda: os.path.exists(_GUARD)
                        and open(_GUARD, "r", encoding="utf-8").read().strip()
                        == str(os.getpid())
                        and os.remove(_GUARD))
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
