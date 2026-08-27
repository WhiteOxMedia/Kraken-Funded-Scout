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

# Liquidity floors for the rotation-aware filter (module-level: the console's
# FAST $20 board uses the same floors — one source of truth).
LIQ_ROT_FLOOR_WEEKDAY = 15_000.0
LIQ_ROT_FLOOR_WEEKEND = 10_000.0
LIQ_DAILY_BACKSTOP = 250_000.0


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
GIVEBACK_WARN_USD = 50   # fast drop from the peak gets a voice (Aug 24: PUMP
                         # gave back ~$70 in minutes after the $140 take — silent)
LOSS_WARN_USD = 50       # a bleeding position gets called out too (PENGU
                         # bled -$84 with no call — the light only guarded winners)
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
# 1h trend line (EMA21). Correlation means everything rises together — and
# SINKS together. The tide sets the tempo: RISING carries greens, FLAT
# demands pickiness, OUT means don't enter anywhere — all ships sink
# together. Computed from the engine's candle cache (no live API storm),
# refreshed at most every 5 minutes.
TIDE_RISING_MIN = 70.0
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


def _save_take_payout(pair: str, amount: int):
    """Persist big TAKE IT payouts so the console's QC can block re-entries
    while the move is exhausted (atomic write, survives restarts)."""
    import json
    try:
        doc = {}
        try:
            with open(TAKE_PAYOUTS_JSON, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:
            pass
        doc[pair] = {"amount": amount, "ts": _now_mst().isoformat()}
        tmp = TAKE_PAYOUTS_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
        os.replace(tmp, TAKE_PAYOUTS_JSON)
    except Exception as exc:
        logger.warning("take payout save failed: %s", exc)


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
    # phone push: her text + (once the WAV renders) her actual voice
    _push_to_phone(text, wav_path=_wav_path)


def _push_topic() -> str | None:
    """Private ntfy topic (from push_topic.txt next to this file)."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_topic.txt")
        return open(p, "r", encoding="utf-8").read().strip() or None
    except Exception:
        return None


def _push_to_phone(text: str, wav_path: str | None = None):
    """Every spoken line lands on the phone as a TEXT notification (user
    rule, Aug 27: no WAV attachment — 'I'd rather read it myself'). ntfy.sh:
    free, no account; the phone subscribes once. Priority escalates with
    the message: GET OUT / TAKE IT / WARNING push URGENT (alarm + banner +
    vibration), picks push HIGH."""
    topic = _push_topic()
    if not topic:
        return
    try:
        import urllib.request, urllib.parse
        _t = text.lower()
        if any(k in _t for k in ("get out", "take it", "warning", "stopped out",
                                 "tide", "trade failed")):
            prio = "urgent"   # max: alarm + banner + vibration
        elif any(k in _t for k in ("new pick", "buy ", "go!")):
            prio = "high"    # heads-up banner
        else:
            prio = "default"
        req = urllib.request.Request(
            f"https://ntfy.sh/{urllib.parse.quote(topic)}",
            data=text.encode("utf-8"),
            headers={"Title": "Kraken Scout", "Priority": prio,
                     "Tags": "rotating_light" if prio == "urgent" else "speaker"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
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


def _hours_to_scalp(pair: str, entry):
    """Hours to reach THE USER'S TARGET: +$20 on $5,000 = +0.4% at this
    coin's rotation pace. This is what 'resolve within a few hours' means
    in their system — they scalp $20s, they don't hold to TP. The old
    TP-based gate blocked every 92% breakout (far TP, immediate scalp)."""
    try:
        from volatility_engine import load_rotation_thresholds, ROTATIONS
        doc = load_rotation_thresholds(max_age_s=300)
        if not doc or not entry:
            return None
        n = _now_mst()
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


def _max_resolve_hours() -> float:
    """How long a contest trade may take to hit its goal:
    base 3h, flexible by the rotation's real pace (engine data —
    strong rotations earn patience), NEVER past midnight (never the
    next day). Uses the rotation × weekday/weekend schedule."""
    n = _now_mst()
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
    except Exception:
        pass
    return min(pace_cap, hours_to_midnight)


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
    except Exception:
        pass

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

        # ── 1b. hard stop: five twenties underwater = the trade failed ──
        # (user: red needs to be MORE sensitive — a -$100 hole on a $20
        # scalp game is a failed trade, not a dip)
        if not should_exit and entry > 0 and live > 0:
            _usd_now = (live / entry - 1.0) * size
            if _usd_now <= -100.0:
                should_exit = True
                reason = "STOPPED OUT — down $100. The trade failed — get out, wait for the next one."

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
            logger.info("🚨 CONTEST LIGHT: SELL %s — %s", pair, reason)
            continue

        # ── 3. THE $20 TAKE-IT CALL (user's scalp system) ──────────────
        # They all-in on green, watch ~5 min, out at +$20, re-enter small,
        # ride and take $20 every chance. She counts the $20s out loud:
        # every +$20 threshold crossed = "TAKE IT — N dollars up".
        # Resets when back near entry (re-entry = fresh count).
        pct = (live / entry - 1) * 100
        usd = pct / 100.0 * size
        level = int(usd // TAKE_IT_USD) if usd > 0 else 0
        if usd < 10.0:
            _take_level.pop(pair, None)  # back near entry — fresh count
        elif level > _take_level.get(pair, 0):
            _take_level[pair] = level
            _amt = level * TAKE_IT_USD
            if voice:
                _speak_server(f"Take it! {_amt} dollars up!")
            logger.info("💰 TAKE IT: %s +$%d (%.1f%%)", pair, _amt, pct)
            # big payout = mature move: tell the console so its QC blocks
            # re-entries while the wiggle is exhausted (Aug 24: user took
            # the $140, re-scanned green 20 min later, rode the giveback)
            if _amt >= BIG_PAYOUT_USD:
                _save_take_payout(pair, _amt)

        # ── 4. GIVEBACK WARNING — fast drops from the peak get a voice ──
        # The take-it calls are one-way (up only); the giveback used to be
        # silent. PUMP peaked +$156, gave back ~$70 in minutes — no call.
        peak_usd = (high / entry - 1) * size if high > entry else 0.0
        if usd > 0 and peak_usd - usd >= GIVEBACK_WARN_USD:
            step = int((peak_usd - usd) // GIVEBACK_WARN_USD)
            if step > _giveback_level.get(pair, 0):
                _giveback_level[pair] = step
                _amt = int(step * GIVEBACK_WARN_USD)
                if voice:
                    _speak_server(f"Warning! {asset} gave back {_amt} dollars! Take it or ride!", rate=1)
                logger.info("⚠️ GIVEBACK: %s -$%d from peak +$%.0f (now +$%.0f)",
                            pair, _amt, peak_usd, usd)
        elif peak_usd - usd < GIVEBACK_WARN_USD / 2:
            _giveback_level.pop(pair, None)  # recovered near the peak — re-arm

        # ── 5. LOSS WARNING — a bleeding position gets called out too ──
        # The old light only guarded winners (trail) and hard trend breaks;
        # a slow bleed below entry was silent (PENGU bled to -$84, no call).
        if usd < 0 and usd <= -LOSS_WARN_USD:
            step = int(-usd // LOSS_WARN_USD)
            if step > _loss_level.get(pair, 0):
                _loss_level[pair] = step
                _amt = int(step * LOSS_WARN_USD)
                if voice:
                    _speak_server(f"Warning! {asset} down {_amt} dollars! Get out or hold!", rate=1)
                logger.info("⚠️ LOSS WARNING: %s -$%d", pair, _amt)
        elif usd > -LOSS_WARN_USD / 2:
            _loss_level.pop(pair, None)  # recovered — re-arm

        # ── 6. THE CLOCK (user directive: green must say how long to hold)
        # out-by = entry + 2x this coin's usual $20 time (min 45 min).
        # Near a take or short on time -> "stay close"; else "walk around".
        try:
            _eta = _hours_to_scalp(pair, entry) or 0.75
            _out_by = None
            if w.get("added"):
                _added = datetime.fromisoformat(w["added"])
                _out_by = _added + timedelta(minutes=max(2.0 * _eta * 60.0, 45.0))
            if _out_by:
                _rem = (_out_by - datetime.now(_out_by.tzinfo)).total_seconds() / 60.0
                _next20 = ((level + 1) * TAKE_IT_USD - usd) if usd > 0 else (TAKE_IT_USD - usd)
                _near = (_next20 <= 10) or (_rem <= 15.0)
                _tail = f", about {max(_rem, 0):.0f} minutes left"
                _tail += ", stay close" if _near else ", go walk around"
            else:
                _tail = ""
        except Exception:
            _tail = ""

        sign = "plus" if usd >= 0 else "minus"
        voice_updates.append(f"{asset}, {sign} {abs(usd):.0f} dollars{_tail}")

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

        # ── THE DEEP PRE-CHECK (user directive, Aug 25: "do it for the
        # bridge too") — every signal, bot-bound or desktop-bound, gets the
        # same deep scan the user's QC runs before it's allowed to matter.
        # The bot trades REAL money on bridge writes: deep read must still
        # say BUY ≥60, no falling knives, no just-paid exhausted movers.
        # (The bot's breakout-appropriate chase rules — stale 2% / blowoff
        # 5% — live reader-side; this is the subset right for ANY buy.)
        # Result is CACHED in _deep for the desktop stage — one read, two
        # consumers. Fail-open: a broken check never silently starves
        # signals; the QC still gates everything.
        _deep = None

        def _do_deep_read():
            import pandas as _pd
            from strategy import evaluate_strategy as _eval_deep, \
                enable_deep_analysis, disable_deep_analysis
            _ohl = exchange.fetch_ohlcv(pair, timeframe="1h", limit=200)
            if not _ohl or len(_ohl) < 30:
                return None, None
            _df = _pd.DataFrame(_ohl, columns=["timestamp", "open", "high", "low", "close", "volume"])
            _df["timestamp"] = _pd.to_datetime(_df["timestamp"], unit="ms")
            _df.set_index("timestamp", inplace=True)
            enable_deep_analysis()
            _sig = _eval_deep(_df, cfg)
            disable_deep_analysis()
            return _sig, _df

        if is_buy and not gate_notes:
            try:
                # TIDE OUT = no entries anywhere — the bridge included. All
                # ships sink together; the bot doesn't buy a sinking fleet.
                _tp0, _ts0 = _fleet_tide()
                if _ts0 == "OUT":
                    logger.info("  TIDE OUT (fleet %.0f%%) — no bridge write; all ships sink together", _tp0)
                    return
                _deep, _df = _do_deep_read()
                if _deep is not None:
                    if _deep.action != "buy" or _deep.confidence < 60:  # QC_SCALP_FLOOR
                        logger.info("  DEEP VETO (bridge): %s deep-reads %s %.0f%% — not written; the bot and the QC would both say no",
                                    asset, _deep.action, _deep.confidence)
                        return
                    # NO DOWNWARD TREND PICKS (user directive, Aug 25) — same
                    # EMA knife test as the QC, on the bot's money too
                    from indicators import ema as _ema2
                    _es = float(_ema2(_df["close"], cfg.ema_short).iloc[-1])
                    _el = float(_ema2(_df["close"], cfg.ema_long).iloc[-1])
                    if _es < _el and float(_df["close"].iloc[-1]) < _el:
                        logger.info("  DEEP VETO (bridge): %s downward trend — no downward trend picks (user rule)", asset)
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
        # the user's system: green light → all in. The deep scan is the
        # confirmation; when it's green they go 100%. Say what they do.
        contest_msg = f"  SPEND: 100% = ${_bal:,.0f}\n" if _bal > 0 else ""
        # how long to THE USER'S TAKE (+$20 = +0.4%) at this rotation's pace
        # — and the RESOLVE RULE: the $20 must be reachable within the
        # rotation's allowance (pace-flexible, never past midnight). Slow
        # trades never hit the desktop. (TP distance is display info only.)
        _hours = _hours_to_scalp(pair, signal.entry_price)
        _cap = _max_resolve_hours()
        if _hours is not None and _hours > _cap:
            logger.info("  TOO SLOW: %s ~%.0fh to the $20 (cap %.0fh) — desktop suppressed, logged to pool",
                        asset, _hours, _cap)
            return

        # ── THE DOUBLE-CHECK RULE (user directive, Aug 25): a desktop signal
        # must already pass the SAME deep scan the user will run in QUICK
        # CHECK — their check becomes a confirmation, not a surprise veto.
        # (This morning's PUMP: the quick read said BUY 77, the deep read
        # said HOLD 40 — the alert fired on the wrong one.) The deep read
        # ran at the bridge stage for bot-bound signals; bot-declined
        # (contest-only) signals get theirs here. Fail-open on errors.
        if is_buy:
            try:
                # TIDE OUT = no entries anywhere (all ships sink together)
                _tp2, _ts2 = _fleet_tide()
                if _ts2 == "OUT":
                    logger.info("  TIDE OUT (fleet %.0f%%) — desktop suppressed; all ships sink together",
                                _tp2)
                    return
                if _deep is None:
                    _deep, _df = _do_deep_read()
                    if _deep is not None:
                        if _deep.action != "buy" or _deep.confidence < 60:
                            logger.info("  DEEP VETO (desktop): %s deep-reads %s %.0f%% — the user's QC would say no",
                                        asset, _deep.action, _deep.confidence)
                            return
                        from indicators import ema as _ema3
                        _es = float(_ema3(_df["close"], cfg.ema_short).iloc[-1])
                        _el = float(_ema3(_df["close"], cfg.ema_long).iloc[-1])
                        if _es < _el and float(_df["close"].iloc[-1]) < _el:
                            logger.info("  DEEP VETO (desktop): %s downward trend — no downward trend picks (user rule)", asset)
                            return
            except Exception as _exc:
                logger.debug("desktop pre-check unavailable for %s: %s (alerting anyway — QC still gates)", asset, _exc)

        hold_txt = "a few hours"
        if _hours is not None:
            if _hours < 1:
                hold_txt = "under an hour"
            elif _hours < 2:
                hold_txt = "about an hour"
            elif _hours < 36:
                hold_txt = f"~{_hours:.0f} hours"
            else:
                hold_txt = f"~{_hours / 24:.0f} days"
        # HOW LONG, in the card's voice: patience = 2x the usual $20 time
        # (min 45 minutes), snapped to 5-minute marks — "out by 11:15"
        try:
            _pat = max(2.0 * (_hours or 0.75) * 60.0, 45.0)
            _out_by = (_now_mst() + timedelta(minutes=round(_pat / 5.0) * 5)).strftime("%I:%M").lstrip("0")
        except Exception:
            _out_by = None
        _how_long = f"until you're up $20 — or she says out. If it hasn't paid by {_out_by}, get out."
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
        except Exception:
            pass
        # ── BOOK CARRY (small coin / big coin — user rule, Aug 26) ──────
        # Can this book carry the CONTEST money? Reuses the liquidity
        # snapshot fetched earlier (the book depth is what matters).
        # Sizes the SPEND down to the biggest contest chunk the book
        # carries; if even 25% doesn't fit, the user can't trade it —
        # no alert at all.
        _spend = float(_bal) if _bal > 0 else 5000.0
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
            return
        if _carry is not None and _spend < (float(_bal) if _bal > 0 else 5000.0):
            contest_msg = (f"  SPEND: {_spend / (float(_bal) if _bal > 0 else 5000.0):.0%} = "
                           f"${_spend:,.0f} — SMALL COIN: this book carries ${_spend:,.0f}, "
                           f"not ${_bal:,.0f}\n")
        elif _carry is not None:
            contest_msg = f"  SPEND: 100% = ${_bal:,.0f} — big book, carries all-in\n"
        if is_buy:
            # THE BRAIN'S NUMBER (user rule, Aug 27: "still saying 100%
            # confident without using the brain"): the gate has used the
            # deep read since Aug 25 — but the NUMBER kept quoting the
            # shallow tournament read (quick 100% while the deep brain
            # said 66). The alert now speaks and shows the DEEP
            # confidence — the same number the user's own Quick Check
            # would print seconds later. No more lying brags.
            _dc = float(_deep.confidence) if _deep is not None else float(signal.confidence)
            title = f"🟢 BUY {asset} — {_dc:.0f}% SURE"
            _small_coin = _carry is not None and _spend < (float(_bal) if _bal > 0 else 5000.0)
            _spoken = (f"Buy {asset}! {_dc:.0f} percent sure! "
                       f"The twenty comes in {hold_txt}! "
                       + (f"Small size only — {_spend:,.0f} dollars! " if _small_coin else
                          ("Big book — all in fine! " if _carry is not None else ""))
                       + _tide_spoken
                       + (f"If it hasn't paid by {_out_by}, get out!" if _out_by else
                          "Hold until you're up twenty dollars!"))
            message = (
                f"BUY {asset}\n"
                f"WHY: the $20 comes in {hold_txt} at this coin's speed, and the deep scan reads the setup at {_dc:.0f}%\n"
                f"WHEN: right now — this is fresh\n"
                f"HOW LONG: {_how_long}\n"
                f"{_tide_line}"
                f"{contest_msg}"
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

        notify_big(title, message, spoken=_spoken if is_buy else None)
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
    last_flatten_date = _load_scanner_state().get("last_flatten_date", "")  # persisted — restarts don't re-flatten

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
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("speech queue drain failed: %s", exc)

    while True:
        now = _now_mst()
        hour = now.hour
        dow = now.weekday()  # 0=Mon, 6=Sun
        block_name, full_interval, min_conf, auto_trade_threshold, block_desc, no_buy, rot_key = get_time_block(hour, dow)

        # the ONE mouth: console lines get spoken here, always
        _drain_speech_queue()

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

                            # desktop: 89% floor + resolve rule, same as everything else
                            _b_hours = _hours_to_scalp(pair, signal.entry_price)
                            if signal.confidence >= 89 and not (_b_hours is not None and _b_hours > _max_resolve_hours()):
                                notify_big(
                                    f"🔄 BOUNCE {asset} ({signal.confidence:.0f}%)",
                                    f"Yesterday's loser turning — bounce play\n"
                                    f"Price: ${signal.price:.4f}\n"
                                    f"Stop: ${signal.stop_loss:.4f}\n"
                                    f"TP: ${signal.take_profit:.4f}\n"
                                    f"Time: {_fmt_mst(_now_mst())}"
                                )
                            else:
                                if _b_hours is not None and _b_hours > _max_resolve_hours():
                                    logger.info("  Bounce %s TOO SLOW (~%.0fh to the $20) — logged only", asset, _b_hours)
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

        # ── CONTEST LIGHT (every loop pass ~30s): the scalp is a 5-minute
        #    game — TAKE IT at +$20 must fire fast, not on a slow cadence.
        #    The all-good voice inside keeps its own 60s cadence.
        if (now.timestamp() - last_contest_light) >= 25:
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
