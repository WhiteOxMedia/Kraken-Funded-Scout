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

# ── THE PROTECTED 24H EXTENSION (Sep 27, exit_extension.py): the seam the
# exit branch consults before closing a cut-class book. The DIAL is
# SCHEDULER-style — env keys read LIVE on every verdict (no import-time
# freeze), default OFF: flipping EXIT_EXTENSION_ENABLED=1 arms it, 0 (or
# absence) reverts every stamp to the incumbent machinery. The wiring is
# dial-neutral: the machinery always ASKS, the module answers.
import exit_extension as _exx   # noqa: E402  (deliberate stdlib-lazy import)

# the drive asserts these exist on the scanner module (0.1): the seam
# callable + the guard marker that says the wiring is live
exit_verdict = _exx.exit_verdict
exit_extension_guard = True


def _hard_stop_breached(w: dict, live: float) -> tuple[bool, str]:
    """THE HARD-STOP INDEPENDENT JUDGE (the recovered ordering majors,
    Sep 29: CR 09:52 on :3842 and 15:18 on :3969): the gate chain runs
    `if not should_exit`, so a book past its peak carries PROFIT GUARD
    FIRED while price sinks through the death line — gate 1b never
    evaluates and the reason never becomes STOPPED OUT. This judges
    the death line directly from entry/size/live and the platform
    buffer's own half (the gate 1b math, uncached, fail-soft False on
    any unreadable input — the gates above remain the first net).
    Returns (breached, the STOPPED OUT reason)."""
    try:
        entry = float(w.get("entry") or 0.0)
        size = float(w.get("size") or 0.0)
        if entry <= 0 or size <= 0 or live is None or float(live) <= 0:
            return False, ""
        import contest_account as _ca_hs
        _acct = _ca_hs.load_state()
        _stop = float(w.get("hard_stop_usd") or 0.0)
        if _stop <= 0.0:
            _stop = _hard_stop_usd(entry, size, _ca_hs.buffer(_acct))
        _usd = (float(live) / entry - 1.0) * size
        if _usd <= -_stop:
            _stop_note = ("the row's GO BIG hard dollar stop"
                          if w.get("go_big") else
                          "half the platform death-line buffer")
            return True, (
                f"STOPPED OUT — down ${abs(_usd):.0f} (the stop is "
                f"-${_stop:.0f} — {_stop_note}). "
                f"The trade failed — get out, wait for the next one.")
        return False, ""
    except Exception:
        return False, ""
MST = ZoneInfo("America/Denver")  # Auto DST: MDT (UTC-6) summer, MST (UTC-7) winter

# ── THE LOG FLOOD GUARD (rebuilt Sep 23 — the machinery was lost in the
# stash-sweep era and test_floodguard held the full spec): identical
# warnings from ONE call site storm the logs; the quota admits the first
# LOG_FLOOD_QUOTA of a signature per window and refuses the rest, the
# notice rides the LAST ADMITTED line (a swallowed line cannot announce
# itself), ERROR/CRITICAL always pass and never consume quota, and the
# next window's first record carries the +N tally (adjacent windows only).
LOG_FLOOD_QUOTA = 5        # admitted records per signature per window
LOG_FLOOD_WINDOW_S = 60.0  # the rolling window
LOG_FLOOD_PRUNE_S = 900.0  # idle signatures are dropped past this


class _FloodQuota:
    """One shared budget behind every handler: signature = call site +
    arity (200 pairs failing at one site is ONE storm). The clock is
    injectable for deterministic tests."""

    def __init__(self, quota: int = LOG_FLOOD_QUOTA,
                 window: float = LOG_FLOOD_WINDOW_S):
        self.quota = int(quota)
        self.window = float(window)
        self.counts = {}          # sig -> {n, win, sup, last, rec}
        self.clock = time.monotonic

    def _sig(self, record):
        arity = len(record.args) if isinstance(record.args, tuple) else 0
        return (record.pathname, record.lineno, arity)

    def admit(self, record) -> bool:
        if record.levelno >= logging.ERROR:
            return True           # the stop-everything line is never filtered
        now = float(self.clock())
        w = int(now // self.window)
        sig = self._sig(record)
        st = self.counts.get(sig)
        if st is None:            # a new signature prunes the idle ones
            for k in [k for k, v in self.counts.items()
                      if now - v["last"] > LOG_FLOOD_PRUNE_S]:
                del self.counts[k]
            st = self.counts[sig] = {"n": 0, "win": w, "sup": 0,
                                     "last": now, "rec": None, "carry": 0}
        if st["win"] != w:       # the window rolled
            # only ADJACENT windows hand the tally to the new window's
            # first admitted record; a gap expires it honestly
            st["carry"] = st["sup"] if w == st["win"] + 1 else 0
            st.update(n=0, win=w, sup=0)
        st["last"] = now
        st["n"] += 1
        if st["n"] <= self.quota:
            if st["carry"] and st["n"] == 1:
                record.flood_tally = st["carry"]
                st["carry"] = 0
            if st["n"] == self.quota:
                # the quota-filling record IS the last admitted line: it
                # announces the suppression that follows (stamping at
                # refusal would be too late — the line is already flushed)
                record.flood_suppressed = 1
            return True
        st["sup"] += 1
        return False


class _LogFloodFilter(logging.Filter):
    """The quota's mount on a handler. The record's keep/refuse decision
    is made ONCE and cached on the record itself (_flood_keep) — several
    handlers over ONE shared quota count each record exactly once and
    every stream renders identically. ERROR/CRITICAL always keep and
    never consume quota."""

    def __init__(self, quota: _FloodQuota):
        super().__init__()
        self.quota = quota

    def filter(self, record) -> bool:
        if record.levelno >= logging.ERROR:
            return True
        if getattr(record, "_flood_counted", False):
            return bool(getattr(record, "_flood_keep", True))
        record._flood_counted = True
        _keep = self.quota.admit(record)
        record._flood_keep = _keep
        return _keep


class _FloodAwareFormatter(logging.Formatter):
    """Renders the guard's stamps: the suppression notice on the last
    admitted line, the +N tally on the next window's first line.
    IDEMPOTENT — the stamps ride the record, so the same record formats
    identically however many handlers re-render it."""

    _NOTICE = " (flood guard: suppressing further identical lines this window)"

    def format(self, record) -> str:
        text = super().format(record)
        if getattr(record, "flood_suppressed", None) == 1:
            text += self._NOTICE
        tally = getattr(record, "flood_tally", None)
        if tally:
            text += f" [+{tally} suppressed in the previous window]"
        return text


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
    _fmt_spec = "%(asctime)s [%(levelname)s] %(message)s"
    _con = logging.StreamHandler()
    _con.setFormatter(_FloodAwareFormatter(_fmt_spec, datefmt="%I:%M %p"))
    root.addHandler(_con)
    try:
        _fh = logging.handlers.TimedRotatingFileHandler(
            _LOG_PATH, when="midnight", backupCount=14, encoding="utf-8")
        _fh.setFormatter(_FloodAwareFormatter(_fmt_spec, datefmt="%I:%M %p"))
        root.addHandler(_fh)
    except Exception as exc:
        logger.warning("log file handler unavailable (%s) — console-only "
                       "logging this boot", exc)   # the console stays
    # ONE shared quota behind both handlers, mounted by literal name at
    # each: a storm is counted once (the record's _flood_keep decision is
    # cached) and every stream renders identically (test_floodguard e2e)
    _flood = _FloodQuota()
    _con.addFilter(_LogFloodFilter(_flood))
    try:
        _fh.addFilter(_LogFloodFilter(_flood))
    except Exception as _fh_exc:
        # boot-order note: the module-global `logger` does not exist yet
        # when this runs — fetch it by name so the trace can never NameError
        logging.getLogger("kraken_scout").debug(
            "flood filter mount to the file handler skipped (%s) — the "
            "console guard still stands", _fh_exc)


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
      is_reentry True if the asset fired earlier TODAY (a DISPLAY note
                  only — no gate hangs on it since Sep 21)
      fine_usd   always 0.0 — the re-entry fine is RETIRED (Sep 21: "I
                  don't want a re-entry gate; it was replaced with real
                  data already")

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
        # flagged as a re-entry so the popup still says so. NO differential:
        # the fine is retired (Sep 21) — the same real-data gates (stale-
        # move trap, floor, tide, 3% guard) judge every fire identically.
        return True, f"re-entry (fired {rec.get('rotation')} earlier today)", \
            True, 0.0
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
        # THE RETRY CONTRACT (Oct 2): per-writer tmp (pid-suffixed —
        # the console's re-arm writes this file from another process)
        # + atomic_io's bounded backoff; the fail-soft except below
        # keeps the never-raise posture either way
        from atomic_io import replace_atomic
        tmp = ROTATION_LOCKOUT_FILE + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(state, indent=1))
        replace_atomic(tmp, ROTATION_LOCKOUT_FILE)
    except Exception as exc:
        logger.debug("rotation mark failed: %s", exc)


def rotation_rearm_on_profit(asset: str) -> bool:
    """THE PROFIT RE-ARM (user rule, Sep 6; reshaped Sep 21): a SOLD or
    GET OUT that BOOKED NET PROFIT lifts the fired-today record for this
    asset, so the next popup is not flagged as a re-entry — a fresh
    start after collected money. Nothing was ever blocked here since
    the Sep 10 lockout retirement (and the Sep 21 rule removed the
    re-entry differential entirely): every fire passes the same
    real-data gates (fire-time scan, direction, stale-move, knife, 3%
    guard). This only resets the display note.

    Only a record from the CURRENT rotation is lifted (an
    earlier-rotation record means the note is already clear). The state
    file is shared with the running scanner (it re-reads
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
        from atomic_io import replace_atomic
        tmp = ROTATION_LOCKOUT_FILE + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(state, indent=1))
        replace_atomic(tmp, ROTATION_LOCKOUT_FILE)
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
# THE TRADES JOURNAL, RESOLVABLE (the test-pollutes-live-state sweep,
# Sep 29): the realized-P&L readers and the auto-close append go through
# this constant, so a drive rebinds ONE name to a tempdir sink instead
# of opening the LIVE journal by relative name (the last unsealed
# backup/restore windows). Default = the module dir, unchanged in
# production; the env seam is the sandbox shortcut.
CONTEST_TRADES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contest_trades.jsonl")


# ── THE CONTEST STANDING (Nov 11, user rule): pass = the balance reaches
#    +12% of the contest's STARTING balance, at ANY time -- no per-window
#    deadline. The death line is -3% of that SAME start and never moves, so
#    profit becomes buffer. The scout prints this every cycle (the standing is
#    not a console-only truth) and prompts ONCE per contest at the win line.
CONTEST_PAYCHECK_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "contest_paycheck_state.json")


def contest_standing_text(acct=None) -> str:
    """One log line: the contest graded against its FIXED baseline."""
    import contest_account as _ca
    st = _ca.standing(acct)
    tail = "WON — take the paycheck" if st["won"] else "NOT WON"
    return ("CONTEST STANDING: start $%s | win $%s (+%.0f%%) | "
            "death $%s (-%.0f%%, anchored) | now $%s | "
            "progress %+.2f%% of +%.0f%% (%.1f%% there, $%s to go) | %s"
            % (f"{st['start_balance']:,.2f}", f"{st['win_line']:,.2f}",
               st["target_pct"], f"{st['death_line']:,.2f}",
               _ca.MAX_LOSS_PCT, f"{st['current_balance']:,.2f}",
               st["progress_pct"], st["target_pct"], st["percent_to_win"],
               f"{st['to_go_usd']:,.2f}", tail))


def contest_paycheck_check(acct=None) -> dict:
    """THE PAYCHECK PROMPT — exactly once per contest, never below the line.

    Re-anchors FIRST (a failed write leaves the prompt for the next cycle, and
    a successful re-anchor makes the standing below the new line by itself),
    then logs the prompt and the new baseline, then stamps the marker file as
    the belt-and-braces dedupe keyed on the finished contest's id.
    """
    import json as _json          # the scanner's own json import is aliased
    import contest_account as _ca
    s = acct or _ca.load_state()
    st = _ca.standing(s)
    if not st["won"]:
        return {"prompted": False, "won": False, "standing": st}
    cid = str(s.get("contest_id") or "")
    try:
        with open(CONTEST_PAYCHECK_STATE, "r", encoding="utf-8") as _fh:
            seen = _json.loads(_fh.read())
    except Exception:
        seen = {}
    if cid and str(seen.get("contest_id") or "") == cid:
        return {"prompted": False, "won": True, "duplicate": True,
                "standing": st}
    new = _ca.reanchor_after_win(s)
    logger.info("💰 PAYCHECK: CONTEST WON — $%s reached the win line $%s "
                "(+%.0f%% of the $%s contest start). TAKE A PAYCHECK and "
                "start the next contest.",
                f"{st['current_balance']:,.2f}", f"{st['win_line']:,.2f}",
                st["target_pct"], f"{st['start_balance']:,.2f}")
    nst = _ca.standing(new)
    logger.info("  CONTEST RE-ANCHORED: new start $%s | new win $%s (+%.0f%%) "
                "| new death $%s (-%.0f%%, anchored) — the profit is the buffer",
                f"{nst['start_balance']:,.2f}", f"{nst['win_line']:,.2f}",
                nst["target_pct"], f"{nst['death_line']:,.2f}",
                _ca.MAX_LOSS_PCT)
    # THE ALERT (not just a log line): the crossing is the one event the user
    # must act on by hand — take the paycheck. Best-effort phone push through
    # the scanner's own ntfy route; a dead topic must never eat the prompt or
    # the re-anchor, so this cannot raise past here.
    try:
        # NOT _push_to_phone: its classifier forwards only BUY / GET OUT
        # text (user rule, Aug 30) — the paycheck wording matched neither and
        # was dropped before ntfy ever saw it. This is the same ntfy pipe,
        # called directly, because a crossed win line is exactly the alert
        # that must reach the phone.
        _send_ntfy(
            f"CONTEST WON — ${st['current_balance']:,.2f} crossed the "
            f"+{st['target_pct']:.0f}% win line (${st['win_line']:,.2f}). "
            f"TAKE A PAYCHECK now — the next contest starts at "
            f"${nst['start_balance']:,.2f} (win ${nst['win_line']:,.2f}, "
            f"death ${nst['death_line']:,.2f}).",
            "high", "paycheck", tag="moneybag",
            title="Kraken Scout - CONTEST WON - take a paycheck")
    except Exception as _pc_push_exc:
        logger.warning("paycheck phone alert failed (%s) — the log line and "
                       "the re-anchor stand", _pc_push_exc)
    # THE WATCHDOG (user rule, Nov 11): every win is graded out of the ledger
    # — how long it took, how many trades, which shapes paid, which exits
    # bled — so the next contest starts from the lesson instead of a blank
    # page. Best-effort: a watchdog fault can never eat the prompt, the
    # re-anchor or the dedupe marker below.
    try:
        import contest_watchdog as _cw
        _cwrec = _cw.report_win(
            contest_id=cid, start_balance=st["start_balance"],
            final_balance=st["current_balance"],
            won_at=new.get("updated_at"), closes_path=CONTEST_TRADES_FILE)
        logger.info("📊 WATCHDOG REPORT: %s", _cw.report_text(_cwrec))
        logger.info("📊 WATCHDOG PLAYBOOK: %s",
                    _cw.playbook_text(_cwrec.get("playbook")))
        for _lesson in (_cwrec.get("lessons") or [])[:3]:
            logger.info("📊 WATCHDOG LESSON: %s", _lesson)
        for _miss in (_cwrec.get("mistakes") or [])[:2]:
            logger.info("📊 WATCHDOG MISTAKE: %s", _miss)
    except Exception as _cw_exc:
        logger.warning("watchdog contest report failed (%s) — the prompt, "
                       "re-anchor and marker stand", _cw_exc)
    try:
        with open(CONTEST_PAYCHECK_STATE, "w", encoding="utf-8") as _fh:
            _fh.write(_json.dumps(
                {"contest_id": cid,
                 "prompted_at": time.strftime("%Y-%m-%d %H:%M:%S")}))
    except Exception as _pc_exc:
        logger.warning("paycheck marker write failed (%s) — the re-anchor "
                       "is the primary dedupe", _pc_exc)
    return {"prompted": True, "won": True, "standing": nst}


_DEATH_FLOOR_SEEN = {"halted": None}


def death_floor_gate(acct: dict | None = None, watch: dict | None = None,
                     marks: dict | None = None) -> dict:
    """THE DEATH FLOOR (Nov 11) — deterministic, not probabilistic.

    The survival guard pauses after a drawdown and resumes on a clock; the tape
    still produced paths that ended $1.30 and $1.50 above the $9,700 line.
    This gate is arithmetic only.  It is WATCH-ONLY for entries: at/below the
    halt line (1.5% band above the death line, $9,850 on a 10k contest) every
    tracked row is named as an exit/flatten call, but no BUY signal is vetoed
    here.  With live watch rows it uses mark-to-market equity so an unrealized
    loss cannot hide behind the last realized ledger balance.  A broken read
    returns halted=True with an empty flatten list: the monitor says it cannot
    see, and the ordinary guards still stand.
    """
    try:
        import contest_account as _ca_df
        _acct = acct or _ca_df.load_state()
        if watch is not None or marks is not None:
            import live_death_guard as _ldg
            return _ldg.assess(_acct, watch or {}, marks or {})
        import death_floor as _df
        return _df.state(_acct)
    except Exception as exc:
        logger.error("death floor read failed (%s) — refusing (fail closed)", exc)
        return {"halted": True, "why": f"death floor read failed: {exc}",
                "flatten_calls": [], "close_only": True}



def death_floor_refusal(asset: str, acct: dict | None = None,
                        watch: dict | None = None, marks: dict | None = None):
    """The death-floor line the live funnel speaks, or None when clear.

    The name is kept for callers and tests, but the verdict is ADVISORY: it
    never refuses an entry.  When the floor is hot it names the exit watch so
    the operator hears the same GET OUT warning the exit monitor will fire.
    The optional watch/marks pair is the live mark-to-market form; the plain
    ledger form remains for callers that have no watch snapshot.
    """
    st = death_floor_gate(acct, watch, marks)
    if st.get("halted"):
        _exit = st.get("exit_reason") or st.get("why") or "the floor is hot"
        return ("  DEATH FLOOR WATCH: %s — %s. Entries stay open; the exit "
                "monitor owns the GET OUT call and will flatten on risk."
                % (asset, _exit))
    return None


def death_floor_boot() -> dict:
    """Boot: print the floor and the arithmetic that makes it a guarantee."""
    try:
        import death_floor as _df
        import contest_account as _ca
        st = death_floor_gate()
        _acct = _ca.load_state()
        pr = _df.floor_proof(float(_acct.get("start_balance") or 10000.0))
        logger.info(
            "🛑 DEATH FLOOR: halt at $%s (%.1f%% band above the $%s death "
            "line, $%.2f of buffer) | one worst-sized loss from the floor "
            "lands at $%s, $%.2f above death | sizing envelope %.1f%% of "
            "buffer (tape worst) | %s",
            f"{st['halt_line']:,.2f}", st["band_pct"], f"{st['death_line']:,.2f}",
            pr["band_usd"], f"{pr['worst_after_one_loss_usd']:,.2f}",
            pr["margin_usd"], 100.0 * pr["buffer_frac"],
            "GET OUT WATCH" if st["halted"] else "clear")
        if pr["margin_usd"] < 0.25 * pr["band_usd"]:
            logger.warning("🛑 DEATH FLOOR: margin thin ($%.2f) — the sizing "
                           "envelope is eating the band", pr["margin_usd"])
        return st
    except Exception as exc:
        logger.warning("death floor unavailable at boot (%s) — the survival "
                       "guard still stands", exc)
        return {"halted": False, "error": str(exc)}


def death_floor_cycle() -> dict:
    """Once per cycle: speak the exit watch on the edge and name the exits.

    Entries are never vetoed here.  The real-time mark-to-market warning runs
    in contest_light (where every open row already has a live mark); this
    ledger-only cycle is the standing state line and the flatten naming.
    """
    st = death_floor_gate()
    halted = bool(st.get("halted"))
    if _DEATH_FLOOR_SEEN["halted"] != halted:
        _DEATH_FLOOR_SEEN["halted"] = halted
        if halted:
            logger.warning("🛑 DEATH FLOOR WATCH — %s. Entries are NOT "
                           "vetoed; the exit monitor is arming GET OUT.",
                           st.get("why"))
            try:
                import json as _dfj
                _wp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "watch.json")
                with open(_wp, "r", encoding="utf-8") as _fh:
                    _watch = _dfj.load(_fh) or {}
                import death_floor as _df
                for _c in _df.flatten_calls(_watch):
                    logger.warning("🛑 DEATH FLOOR: EXIT WATCH %s (entry $%.6g, "
                                   "size $%.2f) — GET OUT when holding risk "
                                   "reaches the line",
                                   _c["pair"], _c["entry"], _c["size"])
            except Exception as _dfw:
                logger.debug("death floor flatten list unavailable: %s", _dfw)
        else:
            logger.info("🛑 DEATH FLOOR: clear — %s", st.get("why"))
    return st


def contest_watchdog_boot() -> dict:
    """BOOT: the watchdog's standing lessons, so a restart cannot hide them.
    Read-only: a missing playbook is simply the empty record."""
    try:
        import contest_watchdog as _cw
        doc = _cw.playbook()
    except Exception as _cw_exc:
        logger.warning("📊 WATCHDOG unavailable at boot (%s) — each win still "
                       "records its report", _cw_exc)
        return {"ok": False}
    logger.info("📊 WATCHDOG: %s", _cw.playbook_text(doc))
    return {"ok": True, "playbook": doc}
TRADES_ENV = "CONTEST_TRADES_FILE"
POPUPS_JSONL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "popups.jsonl")


_popup_journal_last = {"title": "", "ts": 0.0}


def popup_journal(kind: str, title: str, text: str):
    """THE POPUP LEDGER (Sep 21, user rule: 'All popups are now visible from
    the console'): every BUY NOW / GET OUT NOW the scanner fires lands here,
    one JSON object per line, APPEND-ONLY — nothing (a clobbered watch, a
    misbehaving suite, a crashed process) can erase what fired. The
    console's POPUP HISTORY dropdown reads this file. A repeating GET OUT
    (the 60s persistence call) journals once per 5 minutes — same throttle
    as the phone push — so the history reads as events, not heartbeats."""
    try:
        import json as _pj_json
        now = time.time()
        if str(title) == _popup_journal_last["title"] and \
                (now - _popup_journal_last["ts"]) < 300:
            return
        _popup_journal_last["title"] = str(title)
        _popup_journal_last["ts"] = now
        row = {"ts": _now_mst().isoformat(timespec="seconds"),
               "kind": str(kind), "title": str(title)[:200],
               "text": str(text)[:2000]}
        with open(POPUPS_JSONL, "a", encoding="utf-8") as _pjf:
            _pjf.write(_pj_json.dumps(row) + "\n")
    except Exception:
        logger.debug("popup journal write failed", exc_info=True)
# ── THE POPUP-FAMINE GUARD (Nov 7, user: "flag when no popup has fired
#    for N hours while the death line is the blocking gate") ────────────
# Silence is ambiguous: a dead scanner and a market the gates buried both
# look like an empty popup history. This guard separates them for the one
# cause that has a name — when the silence runs past the bar AND the
# funnel's own ledger says the death line buried the most candidates since
# that last popup, it says so. Silence with NO recorded burial is never
# blamed on the death line: that shape is "the scanner may be down", and
# dressing it as a gated market is the lie this guard exists to avoid.
POPUP_FAMINE_HOURS = 6.0        # silence that earns a check, not an alarm
POPUP_FAMINE_EVERY_S = 900.0    # the check's own cadence (it rides the tick)
POPUP_FAMINE_DEATH_GATES = ("death-line", "death-mandate")
_popup_famine_seen = {"ts": 0.0, "flagged": None, "last": None, "over": None}


def _iso_epoch(stamp):
    """Epoch seconds for one of the ledgers' ISO stamps (None when junk).

    The rows carry local-time ISO strings; a naive stamp is read in the
    local zone, which is exactly what _now_mst() wrote.
    """
    try:
        _dt = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    try:
        if _dt.tzinfo is None:
            _dt = _dt.astimezone()
        return float(_dt.timestamp())
    except (OSError, OverflowError, ValueError):
        return None


def _ledger_rows(path):
    """Parseable rows of an append-only jsonl ledger; torn lines skipped."""
    import json as _lf_json
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as _lf:
            for _line in _lf:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _row = _lf_json.loads(_line)
                except ValueError:
                    continue            # a torn append costs one row, never the read
                if isinstance(_row, dict):
                    rows.append(_row)
    except OSError:
        return []
    return rows


def _popup_famine(now=None, hours=None, popups_path=None,
                  supp_path=None, since=None) -> dict:
    """Is the popup silence explained by the death line? (pure decision)

    Flags only when BOTH hold:
      1. the newest popup in the ledger is older than `hours`
         (default POPUP_FAMINE_HOURS), and
      2. among the burials recorded SINCE that popup, the gate that buried
         the most candidates is a death-line gate (a tie goes to the most
         recent burial — the gate blocking right now).

    Returns {flagged, silent_h, gate, burials, by_gate, why}. Never raises
    on junk input; an unreadable or empty popup ledger is not a famine
    claim either (the guard cannot time a silence it never saw start).
    """
    _now = datetime.now().timestamp() if now is None else float(now)
    _hours = float(POPUP_FAMINE_HOURS if hours is None else hours)
    _popups = POPUPS_JSONL if popups_path is None else popups_path
    _supp = SUPPRESSION_LOG_FILE if supp_path is None else supp_path
    _out = {"flagged": False, "silent_h": None, "gate": None,
            "burials": 0, "by_gate": {}, "why": ""}

    _stamps = [s for s in (_iso_epoch(r.get("ts"))
                           for r in _ledger_rows(_popups)) if s is not None]
    if not _stamps:
        _out["why"] = ("the popup ledger has no timestamped rows — nothing "
                       "to time a silence against")
        return _out
    _last = max(_stamps)
    # A NEW CONTEST RESETS THE CLOCK (Nov 7): the dead window's silence and
    # its burials are not this window's evidence. When the contest began
    # after the last popup, the famine window starts at the contest, never
    # at the old book's final popup — otherwise the fresh window opens on a
    # false "the death line is the blocking gate" coped from the grave.
    _fresh = since is not None and float(since) > _last
    _anchor = float(since) if _fresh else _last
    _silent_h = max(0.0, (_now - _anchor) / 3600.0)
    _out["silent_h"] = round(_silent_h, 2)
    _out["anchored"] = "contest" if _fresh else "popup"
    if _silent_h < _hours:
        _out["why"] = (f"silent {_silent_h:.1f}h"
                       + (" since the contest began" if _fresh else "")
                       + f", under the {_hours:g}h bar")
        return _out

    # every burial SINCE the anchor is the set that explains the silence
    _since = []
    for _row in _ledger_rows(_supp):
        _ts = _iso_epoch(_row.get("ts"))
        if _ts is None or _ts < _anchor:
            continue
        _since.append((_ts, str(_row.get("gate") or "?")))
    _out["burials"] = len(_since)
    if not _since:
        if _fresh:
            _out["why"] = (f"silent {_silent_h:.1f}h since the contest began "
                           f"with NO recorded burial — no blocker is named "
                           f"this early")
        else:
            _out["why"] = (f"silent {_silent_h:.1f}h with NO recorded burial "
                           f"since the last popup — the scanner may be down, "
                           f"not gated")
        return _out

    _by = {}
    for _ts, _gate in _since:
        _by[_gate] = _by.get(_gate, 0) + 1
    _out["by_gate"] = dict(sorted(_by.items(), key=lambda kv: -kv[1]))
    _newest_gate = max(_since, key=lambda t: t[0])[1]
    _top = max(_by.values())
    _leaders = [g for g, n in _by.items() if n == _top]
    _gate = _newest_gate if _newest_gate in _leaders else _leaders[0]
    _out["gate"] = _gate
    _share = f"{_by[_gate]}/{len(_since)} burials since it"
    if _gate in POPUP_FAMINE_DEATH_GATES:
        _out["flagged"] = True
        _out["why"] = (f"NO POPUP IN {_silent_h:.1f}h — the DEATH LINE is the "
                       f"blocking gate ({_share})")
    else:
        _out["why"] = (f"silent {_silent_h:.1f}h — the blocking gate is "
                       f"'{_gate}' ({_share}), not the death line")
    return _out


def _deathline_state():
    """(buffer_usd, line, balance, contest_started_epoch) or None.

    Read-only and fail-soft: a broken read must never invent a breach (the
    guard's other branch would then shout a false 'over'), so an exception
    is simply 'no verdict'. The 4th element is the contest's own start, the
    anchor a fresh window times its silence from.
    """
    try:
        import contest_account as _dl_ca
        _st = _dl_ca.load_state()
        return (_dl_ca.buffer(_st), _dl_ca.death_line(_st),
                float(_st.get("current_balance")),
                _iso_epoch(_st.get("contest_id")))
    except Exception:
        return None


def _popup_famine_guard(now=None, deathline=None):
    """Run the famine check on its own 15-minute clock and FLAG a death-line
    famine once per state change (a warning, not a per-tick chorus). Rides
    the tick body; never raises — a broken read must not stop the scan.

    A buffer at or below zero outranks the famine: once the account is under
    the line NO entry can be sized (max_entry_usd returns 0), so the silence
    is the dead account, not the funnel. Saying 'the death line is the
    blocking gate' there would dress a lost contest up as a gated market —
    the same lie the famine guard exists to prevent, one level up.
    """
    try:
        _now = time.time() if now is None else float(now)
        if _now - float(_popup_famine_seen.get("ts") or 0.0) < POPUP_FAMINE_EVERY_S:
            return _popup_famine_seen.get("last")
        _dl = _deathline_state() if deathline is None else deathline
        if _dl and float(_dl[0]) <= 0:
            _v = {"flagged": False, "silent_h": None, "gate": "death-line",
                  "burials": 0, "by_gate": {}, "contest_over": True,
                  "why": (f"CONTEST OVER — balance ${float(_dl[2]):,.2f} is "
                          f"${abs(float(_dl[0])):,.2f} BELOW the "
                          f"${float(_dl[1]):,.2f} death line; no entry can be "
                          f"sized, so the silence is the dead account, not "
                          f"the funnel. A new contest is the only way back.")}
            if _popup_famine_seen.get("over") is not True:
                logger.warning("☠ %s", _v["why"])
            _popup_famine_seen["over"] = True
            _popup_famine_seen["flagged"] = False
            _popup_famine_seen["ts"] = _now
            _popup_famine_seen["last"] = _v
            return _v
        if _dl and _popup_famine_seen.get("over") is True:
            logger.info("DEATH LINE CLEARED: buffer back to $%.2f above the "
                        "$%.2f line — sizing is live again (new contest)",
                        float(_dl[0]), float(_dl[1]))
        _popup_famine_seen["over"] = False
        _since = _dl[3] if (_dl and len(_dl) > 3) else None
        _v = _popup_famine(now=_now, since=_since)
        _v["contest_over"] = False
        _was = _popup_famine_seen.get("flagged")
        if _v["flagged"] and not _was:
            logger.warning("🚩 POPUP FAMINE: %s", _v["why"])
        elif _was and not _v["flagged"]:
            logger.info("POPUP FAMINE CLEARED: %s", _v["why"])
        _popup_famine_seen["flagged"] = bool(_v["flagged"])
        _popup_famine_seen["ts"] = _now
        _popup_famine_seen["last"] = _v
        return _v
    except Exception as _pf_exc:
        logger.debug("popup-famine guard skipped: %s", _pf_exc)
        return None


CONSOLE_STATE_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console_state.json")
CONTEST_TRAIL_PCT = 3.0   # BASE trail off the peak — ratchets tighter as the
# THE HALF-GOAL ARM (user rule, Sep 22: "trigger the profit guard half the
# way to the goal instead of waiting until the goal is reached"): the
# goal-armed trail LATCHES at half the goal, and the armed trail caps at
# the same half-goal width (floor 1.0%) — the net that used to be a
# goal-reached privilege is a halfway milestone now.
GOAL_TRAIL_ARM_FRAC = 0.5
                          # run grows in PEAK-PERCENT terms (see
                          # _ratchet_trail_pct): 2% once the peak reached
                          # +1% of entry, 1.5% at +2% (user rule, Aug 26:
                          # TAO ran nine twenties and gave back $150 before
                          # a flat 3% trail called the exit — big runs earn
                          # tight protection). RE-BASED Sep 21 from the
                          # tier-blind +$100/+$200 thresholds, which engaged
                          # ZERO times in the 48-trade ledger at the current
                          # tier (the verdict-cost study: +$100 needed a
                          # +21% peak at a $470 entry) — peak-percent terms
                          # engage on any real run at every tier.


def _ratchet_trail_pct(entry: float, high: float, size: float) -> float:
    """Trail width ratchets down as the run grows: 3% base, 2% once the
    peak reached +1% of entry, 1.5% at +2%. Re-based Sep 21 to
    PEAK-PERCENT thresholds — the old +$100/+$200 dollar thresholds were
    tier-blind and never fired at the current tier, while percent terms
    engage on any real run regardless of size. `size` stays in the
    signature for call-site stability; the thresholds are deliberately
    size-independent so the trail width cannot drift when the contest
    tier changes."""
    if not entry or not high or high <= entry:
        return CONTEST_TRAIL_PCT
    peak_pct = (high / entry - 1.0) * 100.0
    if peak_pct >= 2.0:
        return 1.5
    if peak_pct >= 1.0:
        return 2.0
    return CONTEST_TRAIL_PCT


GIVEBACK_TRAIL_FLOOR_FRAC = 0.90  # armed-floor tighten per SPENT give-back
# tier, for the rest of the day (Oct 5, user ask: once the day's tighten
# is spent the armed nets keep tightening — the guard factor moved once;
# the floor keeps moving with the tier count). The floor scales the
# armed trail's half-goal cap base — see _armed_cap_base.


def _spent_giveback_floor() -> float:
    """The armed-trail floor factor for the day's SPENT give-back tiers:
    1.0 while nothing has fired, GIVEBACK_TRAIL_FLOOR_FRAC ** tightens
    after (0.90 after the first, 0.81 after the re-arm). Spent means
    ATTEMPTED, whatever the rails answered — the day's erosion is what
    arms the floor, not the rail's permission. Day-scoped (a stale
    watermark reads loose) and fail-soft to 1.0: the floor never
    tightens on a lie."""
    try:
        import profit_guard as _pg_sf
        _gb_sf = (_pg_sf._state() or {}).get("giveback") or {}
        if str(_gb_sf.get("day") or "") != _now_mst().strftime("%Y-%m-%d"):
            return 1.0
        _n_sf = _gb_sf.get("tightens")
        _spent_sf = (int(_n_sf) if isinstance(_n_sf, (int, float))
                     else (1 if _gb_sf.get("tightened") else 0))
        return GIVEBACK_TRAIL_FLOOR_FRAC ** max(0, _spent_sf)
    except Exception:
        return 1.0


def _armed_cap_base(goal_pct: float) -> float:
    """The armed trail's width base — the ONE expression all three
    trail sites start from (exit enforcement, voice parity, all-good
    lead): half the goal, floored at 1.0%, tightened by the day's
    spent give-back tiers. The guard factor and the lock window scale
    or bound this at each site; the base itself can never drift."""
    _base = max(float(goal_pct or 0.0) * GOAL_TRAIL_ARM_FRAC, 1.0)
    return round(_base * _spent_giveback_floor(), 3)


CONTEST_SELL_BAR = 50
SCALP_TARGET_PCT = 0.4  # legacy pace heuristic for discovery ETA only; it is
                        # NOT a take and never caps an exit (GO BIG has no
                        # fixed dollar take — momentum owns the exit).
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
# THE FRESHNESS GRACE (the TIA bounce cure, Oct 10): a force-exit within
# FRESH_GRACE_EXIT_MIN of entry must show NEW deterioration (exit_mod
# risen EXIT_GRACE_RISE above the fire-time stamp) before it executes —
# standing background hum the position was born into never kills it.
FRESH_GRACE_EXIT_MIN = 15.0   # a fresh position needs NEW exit pressure
EXIT_GRACE_RISE = 0.05        # ...exit_mod this much above the entry stamp
# THE LOST-ROW CLOCK (Nov 6, the nine-day ride): how long a row may go
# unseen before the watch recovers it instead of riding it. `checked_ts`
# is stamped on a SUCCESSFUL price read only, so the gap since it is
# exactly the time this row spent off the watcher's books. 60 min = four
# checks a minute missing for an hour; override with SCOUT_LOST_ROW_MIN.
LOST_ROW_MIN = float(os.environ.get("SCOUT_LOST_ROW_MIN") or 60.0)
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


def _opp_first_fire_reminder_file() -> str:
    """The one-shot marker for the opp class's first-fire reminder (the
    reminders/ idiom: the event fires once, ever). Runtime state — the
    RUNTIME_INVARIANTS lesson — so it rides gitignore's runtime rule
    and a repo operation can never un-fire the reminder."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_opp_first_fire_reminder.done")


def _opp_reminder_done() -> bool:
    try:
        return os.path.exists(_opp_first_fire_reminder_file())
    except Exception:
        return False   # fail OPEN to re-remind: a lost toast is the worse
                       # failure for an accumulation window nobody watches


def _opp_reminder_mark() -> None:
    try:
        with open(_opp_first_fire_reminder_file(), "w",
                  encoding="utf-8") as f:
            f.write(datetime.now().astimezone().isoformat())
    except Exception as exc:
        logger.warning("opp first-fire reminder mark failed: %s", exc)


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
    # THE RETURN (fix, Sep 21): the 6e7aac3 evolution dropped the return
    # statement, so every caller received None — DESKTOP_ALERT_MIN was None,
    # the %d advisory log threw (swallowed), and float(None) at the first
    # floor consumer would hard-crash the signal path. A floor function
    # MUST return its number.
    return fl


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

# ── THE TIDE-OUT CALIBRATED RETUNE (Sep 30, the per-gate study):
# September's refusals, joined to the OHLCV window and priced through
# the bot's fill model (scratch/pergate_calibration.py), said the
# OUT-tide wall was burying its own money: tide-out n=8, 6 payers,
# median payer +$2.11/$100, median dud −$0.09 — the gate's whole dud
# cost a rounding error — and the payers sat at quick 64–87, UNDER the
# champion seam's 92 (the existing valve missed every one of them).
# THE RETUNE: a dial-gated second valve that admits tide-out
# refusals at quick >= 66 (the study's EV-mining), scaled by the fill
# model's floor (ride x CAPTURE_FLOOR must clear the round trip — a
# 66 quick read prices a ~3% ride; the floor demands the same fee
# clearance the bot's entry floor demands at the door). The DEEP READ
# still owes its verdict on both paths — the retune opens the TIDE
# gate, never the brain's. Dial: TIDE_RETUNE_ENABLED=1 in
# exit_extension.dial.json (env first — the _dial seam), read LIVE,
# absence = OFF: the blanket wall stands exactly as before until the
# operator arms it, and flipping it back expires it on the next tick
# (the exit-extension posture: no restart, no hidden state).
TIDE_RETUNE_ENABLED_KEY = "TIDE_RETUNE_ENABLED"
TIDE_RETUNE_QUICK_MIN = 66.0
TIDE_RETUNE_CAP = 3          # popups per day while armed (dial: TIDE_RETUNE_DAILY_CAP)
TIDE_RETUNE_DAY_CAP_KEY = "TIDE_RETUNE_DAILY_CAP"
# The day ledger is DISK, not memory (runtime-state convention: repo
# root, gitignored, never committed — the RUNTIME_INVARIANTS lesson):
# a scanner relaunch rereads the file and finds the day's releases
# still counted, so a restart cannot refill the cap the study priced.
# Reads are live per call (the dial's own posture); writes are atomic
# (.tmp then os.replace, the signal-snapshot idiom); a MISSING file is
# a fresh day, an UNREADABLE one fails CLOSED — an admission valve
# never guesses.
TIDE_RETUNE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "tide_retune_state.json")
# serializes the ledger's read-check-write IN-PROCESS (two scanner
# threads must never interleave load-check-save and double-spend the
# cap); the CROSS-PROCESS half (scanner and the bot both import this
# module and both consume the valve) is the lockfile below, wrapped
# around the same critical section
_TIDE_RETUNE_LOCK = threading.Lock()
# the cross-process lock: a kernel-owned byte-range lock (msvcrt
# .locking on the first byte of a .lock file beside the ledger). The
# lock lives in the OS per HANDLE, not in the file: a contender's
# acquire gets a locking error and retries, nothing can be stolen by
# rewriting or removing the file, and a dead holder's lock evaporates
# with its process — steal-proof AND crash-recovering, with no
# check-then-unlink anywhere (an unlink-takeover lockfile could strip
# a LIVE holder's lock on age alone, and could delete another
# contender's replacement — either race re-opens the double-spend;
# round 2, the 272e283 review)
_TIDE_RETUNE_LK_RETRIES = 8
_TIDE_RETUNE_LK_RETRY_S = 0.05


def _retune_lockfile_path(state_file) -> Path:
    """The lock lives BESIDE the ledger, derived at CALL time — a
    sandboxed (rebound) TIDE_RETUNE_STATE_FILE locks in its own
    sandbox, never at the repo root."""
    return Path(state_file).with_suffix(".json.lock")


class _TideRetuneFileLock:
    """The cross-process gate around the ledger's read-check-write:
    open the .lock file, then win the kernel's byte-range lock on it
    (msvcrt.locking, non-blocking, bounded backoff). `.held` says
    whether it was won; release = unlock + close. The file PERSISTS —
    only the kernel-owned byte range is the lock; the file is just the
    handle's anchor and may sit beside the ledger forever."""

    def __init__(self, path):
        self._path = Path(path)
        self._fd = None
        self.held = False

    def __enter__(self):
        import time as _t
        import msvcrt as _ms
        for _attempt in range(_TIDE_RETUNE_LK_RETRIES + 1):
            try:
                if self._fd is None:
                    self._fd = os.open(str(self._path),
                                       os.O_CREAT | os.O_RDWR)
                _ms.locking(self._fd, _ms.LK_NBLCK, 1)
                self.held = True
                return self
            except OSError:
                # contended (or the open itself failed) — back off and
                # retry on the SAME handle; the holder's critical
                # section is microseconds, so a live contention wins a
                # retry almost immediately, while a dead holder needs
                # no retry at all (the kernel already freed the range)
                _t.sleep(_TIDE_RETUNE_LK_RETRY_S)
        return self            # NOT held — caller MUST check .held

    def __exit__(self, *exc):
        if self.held and self._fd is not None:
            try:
                import msvcrt as _ms
                _ms.locking(self._fd, _ms.LK_UNLCK, 1)
            except OSError:
                pass  # best-effort unlock: the close below drops the
                       # byte range with the handle either way
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass  # already-closed fd — the kernel released the
                       # byte range at close; nothing is left held
            self._fd = None
        self.held = False
        return False


def _tide_retune_dial() -> dict:
    """The retune's knobs, read LIVE (the _dial seam: env first, then
    exit_extension.dial.json, then defaults). Anything but exactly '1'
    on the master key is OFF — the fail-safe read, never a truthy parse."""
    try:
        from exit_extension import _dial as _exx_dial
        armed = str(_exx_dial(TIDE_RETUNE_ENABLED_KEY, "") or "").strip() == "1"
        try:
            qmin = float(_exx_dial("TIDE_RETUNE_QUICK_MIN", "")
                         or TIDE_RETUNE_QUICK_MIN)
        except (TypeError, ValueError):
            qmin = TIDE_RETUNE_QUICK_MIN
        try:
            cap = int(float(_exx_dial(TIDE_RETUNE_DAY_CAP_KEY, "")
                            or TIDE_RETUNE_CAP))
        except (TypeError, ValueError, OverflowError):
            # OverflowError rides ValueError's cure (the commit review's
            # medium): int(float('inf')) is NOT a ValueError — left
            # uncaught it reached the OUTER fallback and DISARMED the
            # whole valve on one junk dial value. A junk knob falls to
            # the documented default, never to OFF.
            cap = TIDE_RETUNE_CAP
        # THE CAP CANARY (Oct 5, the operator's scoped trial): named
        # low-risk pairs may spend up to canary_cap slots/day while the
        # whole funnel stays at the base cap. THREE FAIL-CLOSED DOORS on
        # an expansion knob: an unparseable/empty pair list = no canary;
        # a canary cap under 1 = no canary; an UNTIL time box in the
        # past or unparseable = expired. Rollback is the dial's own
        # posture: flip or remove the TIDE_RETUNE_CANARY_* keys and the
        # widening expires on the next tick — no restart, no hidden
        # state. A base cap of 0 (operator OFF) can never be widened.
        # The pairs leave as a SORTED LIST, not a set: every dial dict
        # lands in the weekly report's JSON sidecar, and a frozenset
        # broke that write (found by the reconciliation feature's own
        # red run) — the dial is JSON-safe at the source.
        _raw_pairs = str(_exx_dial("TIDE_RETUNE_CANARY_PAIRS", "") or "")
        canary_pairs = sorted(
            {p.strip().upper() for p in _raw_pairs.split(",") if p.strip()})
        try:
            canary_cap = int(float(
                _exx_dial("TIDE_RETUNE_CANARY_CAP", "") or 0))
        except (TypeError, ValueError, OverflowError):
            canary_cap = 0    # 'inf' and friends: the canary is inert
        canary_until = str(
            _exx_dial("TIDE_RETUNE_CANARY_UNTIL", "") or "").strip()
        try:
            # AN OMITTED BOX IS AN EXPIRED ONE (the commit review's
            # medium): a canary without a mandate end-date could widen
            # indefinitely — a canary IS its time box, so the box is
            # MANDATORY and fail-closed (absent = expired, like junk).
            _expired = (not canary_until)
            if not _expired:
                _expired = (datetime.now().astimezone().date()
                            > datetime.strptime(canary_until,
                                                "%Y-%m-%d").date())
        except ValueError:
            _expired = True
        if (not canary_pairs or canary_cap < 1 or _expired
                or cap < 1 or canary_cap <= cap):
            # THE DIAL IS THE SINGLE POSTURE (the commit review's
            # medium): a canary that adds nothing (base cap 0, or a
            # canary cap at/under the base) is CLEARED here, so the
            # report and the release logic can never disagree about
            # whether a widening is armed.
            canary_pairs, canary_cap, canary_until = [], None, None
        return {"armed": armed, "quick_min": qmin, "cap": max(0, cap),
                "canary_pairs": canary_pairs,
                "canary_cap": canary_cap, "canary_until": canary_until}
    except Exception:
        return {"armed": False, "quick_min": TIDE_RETUNE_QUICK_MIN,
                "cap": TIDE_RETUNE_CAP, "canary_pairs": [],
                "canary_cap": None, "canary_until": None}


def _tide_retune_load_day(day: str) -> list[str] | None:
    """Today's released pairs from the state file — live per call. A
    missing file, or one stamped with an older day, reads as a fresh
    day (the rollover REPLACES the map — old days never bind). A file
    that exists but cannot be trusted — corrupt json, a non-dict, or
    today's stamp with a pairs field that is not a list — returns None
    and the caller fails CLOSED: a broken ledger must never read as an
    empty day, or a crash would refill the cap by destroying the
    evidence."""
    try:
        import json as _json
        with open(TIDE_RETUNE_STATE_FILE, "r", encoding="utf-8") as f:
            raw = _json.loads(f.read())
        if not isinstance(raw, dict):
            return None
        if raw.get("day") != day:
            return []          # a file from an older day: the rollover
        pairs = raw.get("pairs")
        if not isinstance(pairs, list):
            return None
        # THE CASING BOUNDARY IS THE LOADER (the commit review's
        # medium): entries persisted before the normalization (or by a
        # careless writer) normalize at READ, so a lowercase ledger row
        # dedupes exactly like its uppercase self — old ledgers can
        # never double-spend across the boundary.
        return [str(p).strip().upper() for p in pairs]
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("tide retune day ledger unreadable — failing "
                       "CLOSED (clear %s to re-arm): %s",
                       TIDE_RETUNE_STATE_FILE, exc)
        return None


def _tide_retune_save_day(day: str, pairs: list[str]) -> bool:
    """Persist today's ledger ATOMICALLY (.tmp then os.replace — the
    signal-snapshot idiom: readers never see a partial day). Returns
    False on any failure: the caller refuses the release rather than
    admit a pair a restart could double-count."""
    try:
        import json as _json
        _tmp = TIDE_RETUNE_STATE_FILE + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write(_json.dumps({"day": day, "pairs": list(pairs)}))
        os.replace(_tmp, TIDE_RETUNE_STATE_FILE)
        return True
    except Exception as exc:
        logger.warning("tide retune day ledger write failed — failing "
                       "CLOSED: %s", exc)
        return False


def _tide_retune_releases(quick, pair: str = "",
                          consume: bool = True) -> tuple[bool, str]:
    """(released, why) — the calibrated tide-out valve (the Sep-30
    per-gate study). FAIL-CLOSED at every door: the dial OFF or the
    module unreadable -> the blanket block stands; quick under the
    study's floor -> no release; the pair already released today (or
    the day's cap spent) -> the study priced ONE admission per pair,
    not a flood. The in-process AND cross-process critical section
    (scanner + bot both import this module) is serialized: the
    threading lock inside, an os.link lockfile wrapping the whole
    read-check-write, so two processes can never both spend the last
    slot. The day ledger persists to DISK (tide_retune_state.json)
    so a restart cannot refill the cap, and a ledger that cannot be
    read or written refuses the release — fail-closed. THE QC'S MIRROR
    (Oct 2) consults with consume=False: the same doors, the same
    ledger, but nothing is spent — the console never burns the alert's
    slot, and a pair that ALREADY holds today's admission passes the
    consult (the alert released it; the mirror must agree, never
    contradict or double-spend). This opens the TIDE gate only — every
    caller keeps the deep read's own veto downstream."""
    d = _tide_retune_dial()
    if not d["armed"]:
        return False, "the retune dial is OFF"
    if float(quick or 0) < d["quick_min"]:
        return False, (f"quick {float(quick or 0):.1f} < the study's "
                       f"{d['quick_min']:.0f} floor")
    with _TIDE_RETUNE_LOCK, \
            _TideRetuneFileLock(_retune_lockfile_path(
                TIDE_RETUNE_STATE_FILE)) as _lk:
        if not _lk.held:
            # the admission valve's busy posture: fail CLOSED — refusing
            # a genuinely passing candidate costs one missed release;
            # deciding the cap on an unlocked ledger could double-spend
            # it across processes (the exact bug class this lock kills)
            return False, ("the retune ledger is busy — failing CLOSED, "
                           "the cap is never decided unlocked")
        day = datetime.now().astimezone().strftime("%Y-%m-%d")
        today = _tide_retune_load_day(day)
        if today is None:
            return False, ("the retune day ledger is unreadable — failing "
                           "CLOSED until the operator clears it")
        # THE PAIR IS NORMALIZED AT THE LEDGER BOUNDARY (the commit
        # review's medium): the dedup compares and the ledger stores the
        # UPPERCASED pair, so a casing variant of a released pair can
        # never bypass once-per-pair and spend another canary slot.
        _p = str(pair or "").strip().upper()
        if _p and _p in today:
            if consume:
                return False, "already released today"
            return True, (f"the pair already holds today's retune admission "
                          f"({len(today)}/{d['cap']} today)")
        # THE CAP CANARY'S SCOPE (Oct 5): a canary pair may spend the
        # day's extra slot (base cap + the canary's own), everyone else
        # lives at the base cap. The widening only ever applies to a
        # LIVE base cap (>= 1 — an operator OFF at 0 stays off), and a
        # canary cap at or under the base is inert.
        cap = d["cap"]
        _can = ""
        if (_p and cap >= 1 and _p in d.get("canary_pairs", ())
                and d.get("canary_cap") is not None
                and d["canary_cap"] > cap):
            cap = d["canary_cap"]
            _can = " (the cap canary's pair)"
        if len(today) >= cap:
            return False, f"the day's {cap} release cap is spent{_can}"
        if _p and consume:
            if not _tide_retune_save_day(day, today + [_p]):
                return False, ("the retune day ledger would not persist — "
                               "failing CLOSED (a restart must not refill "
                               "the cap)")
            # THE CANARY'S JOURNAL (Oct 5, the reconciliation ask): the
            # day map rolls over, so every CONSUMING release also lands
            # one append-only row in the weekly journal — the reckoning's
            # canary block grades the week's releases against the popup
            # outcomes from it. The journal path derives from THIS day
            # ledger's own path (the lockfile idiom), so a drive that
            # sandboxes the ledger sandboxes the journal automatically —
            # no drive write can ever leak into the live journal (found
            # the hard way: the first drives polluted the repo-root
            # journal with test rows). Best-effort: a journal failure
            # never blocks the release (the release already happened; the
            # reckoning reports the gap as collecting).
            try:
                import time as _tt
                import tidebar_weekly as _tbw
                _tbw.append_retune_release(_tt.time(), _p,
                                           state_file=TIDE_RETUNE_STATE_FILE)
            except Exception as _jr_exc:
                logger.debug(
                    "canary release journal write skipped: %s", _jr_exc)  # WHY-SWALLOW: the journal is a reconciliation surface, never a trading gate — the release already happened and MUST stand (the valve's fail-open contract); the failure is NAMED here for the reckoning's operator, never silenced
        return True, (f"the calibration study opened the tide gate "
                      f"(quick {float(quick or 0):.0f} >= {d['quick_min']:.0f}, "
                      f"{len(today) + (1 if pair and consume else 0)}/{cap} today"
                      f"{_can})")


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
    in OUT hours pays a few over. Floored at the bar's own SOURCE-matched
    floor (tide_learn.state_bar_floor): a ladder-admitted outcome bar's
    evidence flows through to every coin, the old [86, 97] constant
    floor only for quantile bars. Ceiling BAR_MAX as ever. Fail-closed:
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
        # (Sep 23, the burial-ledger tuning) the floor follows the bar's
        # own source: a ladder-admitted OUTCOME bar may sit below 80, and
        # re-flooring every personal bar to the quantile's 86 here would
        # undo the fleet's evidence coin by coin.
        return float(max(_tl.state_bar_floor(tide_state),
                         min(_tl.BAR_MAX, float(base_bar) - adj)))
    except Exception:
        return float(base_bar)


def _tide_learn_bar() -> tuple[float | None, str]:
    """(learned OUT bar, meta) — the deep-store learner's confidence bar
    for OUT tides (the payer-rate price; the fleet bar floors at the
    state's evidence-gated floor — see tide_learn.state_bar_floor).
    (None, why)
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
                              # $120 behind -> full +10% tilt) — ALSO the
                              # retired factor's halflife FLOOR (Oct 3: its
                              # body referenced two constants that never
                              # existed, so every call died into the
                              # fail-soft except and returned a silent 1.0)
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
      halflife = max(WORTH_ACTUAL_HALFLIFE_USD, contest_size x 0.12)
    so a $1k contest doesn't tilt on a $120 deficit it can't afford, and a
    $10k contest doesn't need a $120 deficit to max out. (Oct 3: the body
    referenced WORTH_ACTUAL_HALFLIFE_MIN_USD/FRAC — never defined anywhere —
    so every call hit the fail-soft except and returned a silent 1.0, a dead
    lever indistinguishable from a live one sitting at neutral; the floor is
    THE $120 constant, the 0.12 inline per this docstring. test_worthdial
    section 9 pins the computed tilt.) Fail-open to 1.0
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
        _halflife = max(WORTH_ACTUAL_HALFLIFE_USD, _size * 0.12)
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
    profile is young. The overwatch's micro goal bias applies to NEW goals
    only, so an already-stamped watch row keeps the target it was fired on."""
    base = GOAL_DEFAULT_PCT
    try:
        from volatility_engine import get_move_threshold
        now = _now_mst()
        win = _rotation_window(now)
        rot = win[2] if win else "08-12"
        sched = _schedule_for_dow(now.weekday())
        thr = get_move_threshold(pair, rot, sched, level="standard")
        if thr and float(thr) > 0.2:
            base = float(thr)
    except Exception as exc:
        logger.warning("move_threshold read failed for %s (%s) — the "
                       "goal ships at the moderate default", pair, exc)
    try:
        import overwatch_governor as _og
        base = float(base) + _og.goal_bias_pct()
    except Exception:
        pass
    return max(GOAL_MIN_PCT, min(GOAL_MAX_PCT, float(base)))


def _operative_goal_pct(goal, pair: str) -> float:
    """The goal a rule works to: the stamp when it is a real goal, else the
    window goal this pair is carrying right now.

    Never a null and never a zero. Both exit readers already use exactly
    `float(stamp or 0) or _window_goal_pct(pair)`, so this is the value they
    act on — not a second derivation that could drift from theirs. The entry
    stamp and the close record both go through here.
    """
    try:
        _g = float(goal or 0.0)
    except (TypeError, ValueError):
        _g = 0.0
    if _g > 0:
        return _g
    try:
        return float(_window_goal_pct(pair) or 0.0)
    except Exception:
        return 0.0


def _close_goal_pct(w: dict, pair: str) -> float:
    """The goal a close records: the entry's own stamp, or — when the watch
    row was never stamped — the goal the exit logic actually manages to.

    A watch entry without a goal stamp used to ship a null goal_pct into the
    ledger, and the studies classify that row as goal_unknown: invisible to
    the clip and to every composition read.
    """
    return _operative_goal_pct(w.get("goal_pct"), pair)


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


def _goal_plan_min(goal_pct, pace_pct_per_hour=None) -> float:
    """The fire's OWN plan: the goal's horizon at the engine's 1%/hour
    assumption (goal x 60 minutes), clamped to the [60, 180] window the
    goal bands can even ask for. NOT the rotation clock.

    (Nov 7) The old derivation was min(180, max(60, minutes to the next
    rotation boundary)) — a pure function of the WALL CLOCK. The same 1.2%
    fire planned 180 minutes at 08:55 and 60 at 11:50, so the death
    mandate's 90-minute hold cap admitted champions only in the last 90
    minutes of every 4-hour block and refused every fire the rest of the
    time: eleven of eleven mandate refusals this morning read "a
    180-minute window rides into the hold band that bled". A verdict that
    turns on when the scanner happened to look is a degenerate interval
    reading as a measurement.

    The Sep 4 rule ("the goal should reach before the rotation happens")
    is answered by the goal's own horizon; a plan that crosses the
    boundary stays the documented exception the boundary warning covers.
    An unreadable goal plans the LONGEST window: a degenerate goal never
    reads as short enough to clear a hold cap.
    """
    try:
        _g = float(goal_pct)
    except (TypeError, ValueError):
        return 180.0
    if not (_g > 0.0) or _g > 1e6:
        return 180.0          # unknown / NaN / infinite — the longest plan
    # THE COIN'S OWN TAPE (Nov 7): the engine's 1%/hour is an ASSUMPTION. When
    # the coin's own hourly closes prove it moves faster, the goal's horizon is
    # the coin's pace, not the constant — a 2.6% goal on a 2.1%/h mover plans 74
    # minutes, not 156, and fits under the mandate's hold cap because the tape,
    # not the calendar, says it gets there. The pace may only ever SHORTEN a
    # plan: a quiet six hours does not prove a breakout cannot happen, and
    # lengthening one against a cap priced at 1%/h would be moving the
    # threshold this plan is judged against.
    _pace = 1.0
    try:
        _p = float(pace_pct_per_hour)
        if _p > _pace:
            _pace = _p
    except (TypeError, ValueError):
        _pace = 1.0
    return max(60.0, min(180.0, (_g / _pace) * 60.0))


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
    fleet already carries). THE ADAPTIVE CARVE-OUT (user spec, Sep 23):
    the fixed +5%/+8% tiers and the 25/35-pt discounts become
    carve_out.py's allowance — the required own move is Base x the five
    regime mods (tide out tightens toward ~2%, strength expands it), the
    points scale with the same reading (clamped 0.5-1.5x), and in an OUT
    tide the hatch only OPENS when LoneWolfMode is granted (the
    permission gate: carve, fleet, tide, rotation all clear, no RCL
    drift, no end-game, no suppression — a dead tide stops buying solo
    runners with a static number). The 24h move is cached per pair
    (15 min) — one ticker fetch per signal, not one per gate. Fail-open:
    an unreadable carve module falls back to the OLD fixed tiers IN FLAT
    (in OUT an unreadable reading keeps the hatch CLOSED — a broken
    regime reader never opens the against-the-fleet gate); a
    broken tide or ticker read means NO discount (the normal thresholds
    stand). (Oct 1 audit: the hatch's own-cause fix — carve_out no
    longer requires a healthy tide to open the OUT hatch; the tide
    taxes the SIZE through the product and MinHatch, one tax.)"""
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
    # ── the adaptive reading (fail-open to the Sep-4 fixed tiers) ──
    try:
        import carve_out as _co
        _cres = _co.gate(pair=pair)
        req = float(_co.required_own_move(_cres))
        _lw_scale = float(_cres.get("lw_scale") or 1.0)
        _mode = bool(_cres.get("lone_wolf"))
    except Exception:
        req, _lw_scale, _mode = LONE_WOLF_MOVE_MIN, 1.0, False
    big = req * 1.6                       # the old 5->8 tier ratio
    if _ts == "OUT" and not _mode:
        return 0, ""                      # the permission gate: hatch closed
    _why_tail = f" ({_tp:.0f}% of the fleet up; carve req {req:.1f}%)"
    if own >= big:
        return int(round(LONE_WOLF_PTS_BIG * _lw_scale)), f"+{own:.0f}% on her own {_tide_txt}{_why_tail}"
    if own >= req:
        return int(round(LONE_WOLF_PTS * _lw_scale)), f"+{own:.0f}% on her own {_tide_txt}{_why_tail}"
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


def _hail_mary_opp_qualifies(deep, df, carry, balance, floor,
                             ema_short=9, ema_long=21,
                             trend_bars=6) -> tuple[bool, str]:
    """The candidate side of THE OPPORTUNISTIC HAIL MARY (user ask, Oct 1;
    TIGHTENED Oct 4 — no longer a challenger-grade bet):
      1. DEEP-SCAN BUY: the deep read (the exit watch's own brain, the
         same read that will judge the exit) must read buy — its
         direction IS the entry authority here, not the tournament bar
      2. ABOVE 90%: the read clears HAIL_MARY_OPP_FLOOR STRICTLY — a
         read AT the floor is not above it (the Oct 4 rule)
      3. VERIFIED UPTREND: EMA_short > EMA_long AND close > EMA_long on
         the deep frame (the classic's bar — the trend must be UP)
      4. NO WIGGLES: every one of the last trend_bars closes is higher
         than the one before — a single down-step in the lookback
         declines (trending up means up, not up-dip-up)
      5. MAJOR BOOK: the book carries the WHOLE contest balance — the
         WINDOW's own size (start_balance: the 1k/5k/10k tier), not the
         live remainder (the 'big book — all in fine' standard)
    THE STRICT CONTROLS (unchanged): a $300 hard spend cap
    (OPP_HARD_SPEND_USD) on top of the 3% guard, and the position's
    watch row carries hm_mode="opportunistic" so the exit watch judges a
    2% hard stop (HAIL_MARY_OPP_HARD_STOP_PCT) — the exit watch's
    trend-sell / profit-guard / hard-stop stack is the authoritative
    EXIT. One window per rotation, shared with the classic door.
    Fail-closed on all reads. Returns (qualifies, why)."""
    try:
        if deep is None:
            return False, "no deep read — fail-closed"
        if str(deep.action) != "buy":
            return False, f"the brain reads {deep.action}, not buy"
        _bc = float(deep.confidence)
        if _bc <= float(floor):
            return False, (f"brain {_bc:.0f}% is not ABOVE the opp floor "
                           f"{float(floor):.0f}% (the Oct 4 rule: the "
                           f"score must be above 90, not at it)")
        if df is None:
            return False, "no deep frame — the uptrend cannot be verified"
        from indicators import ema as _ema
        _es = float(_ema(df["close"], ema_short).iloc[-1])
        _el = float(_ema(df["close"], ema_long).iloc[-1])
        _px = float(df["close"].iloc[-1])
        if not (_es > _el and _px > _el):
            return False, ("no verified uptrend (EMA9 must sit above "
                           "EMA21 with price over both)")
        _closes = [float(c) for c in df["close"].iloc[-int(trend_bars):]]
        _dip = next((i for i in range(1, len(_closes))
                     if _closes[i] <= _closes[i - 1]), None)
        if _dip is not None:
            return False, (f"wiggle in the lookback (bar -{len(_closes) - _dip} "
                           f"stepped down) — the trend must rise with no wiggles")
        if carry is None or balance is None or float(carry) < float(balance):
            return False, (f"not a major book (carries ${float(carry or 0):,.0f}, "
                           f"the window's whole balance is "
                           f"${float(balance or 0):,.0f})")
        return True, (f"viable upside — deep-scan buy {_bc:.0f}% > "
                      f"{float(floor):.0f}%, verified uptrend with no wiggles, "
                      f"major book (carries ${float(carry):,.0f} ≥ the "
                      f"window's ${float(balance):,.0f})")
    except Exception as exc:
        return False, f"opportunistic qualify failed ({exc}) — fail-closed"


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
        # THE RETRY CONTRACT (Oct 2): per-writer tmp + bounded backoff —
        # the prior-blocks loss is visible either way (the warning above)
        from atomic_io import replace_atomic
        tmp = TAKE_PAYOUTS_JSON + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
        replace_atomic(tmp, TAKE_PAYOUTS_JSON)
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
            # the trailing newline is LOAD-BEARING: appenders (and any
            # external test) add one line per write — a file ending
            # mid-line merges the next JSON object into the last line
            # and every tail-reading consumer (the 15s her-words tick,
            # the voice-refresh rerun) throws on the merged line. This
            # silent merge is what froze the console's clocks on Sep 21.
            f.write("\n".join(lines[-VOICE_LOG_MAX:]) + "\n")
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
SUPPRESSION_ARCHIVE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "popup_suppressions.archive.jsonl")   # ARCHIVE-ON-TRIM (Oct 3): the
    # rows the cap drops land here (append + fsync, never rewritten)
    # instead of vanishing — the funnel's history outlives the cap.


def _log_suppression(pair: str, gate: str, detail: str, **ctx):
    """Persist one popup-funnel suppression verdict — the record that
    answers 'who buried it?' in one line next time. ctx carries the
    numbers (quick, brain, floor, tide, rot) — anything not None rides
    along. Never raises: a broken log write must not block the funnel."""
    try:
        import json as _json
        entry = {"ts": _now_mst().isoformat(), "pair": pair,
                 "gate": gate, "detail": str(detail)[:220]}
        # RELEASE PROVENANCE (Sep 22): any burial of the pair voids a
        # stale release mark — provenance rides only a candidate that
        # actually fires (a release past one gate is not a pass past all)
        try:
            _RELEASE_PROV.pop(str(pair), None)
        except Exception as exc:
            logger.debug("burial log: release-mark void failed for %s: %s", pair, exc)
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
        # ARCHIVE-ON-TRIM (Oct 3, red-first): the cap keeps the LEDGER
        # fast for its ~10 readers, but the trim used to DESTROY the
        # dropped prefix silently — a rotation's burial burst erased the
        # funnel's own history (the Oct 2 incident: 2 rows gone live at
        # 999/999). The dropped rows land in the append-only archive
        # FIRST: one O(dropped) append, FLUSHED then fsynced — the rows
        # are durable in the archive before the ledger's trim becomes
        # visible, so no write can lose a row (the flush matters: an
        # fsync that misses bytes still in Python's userspace buffer is
        # not durability — Copilot medium, Oct 3). The archive is never
        # read-and-rewritten here (that cure was O(archive) per burial
        # on the scan thread's hot path, growing for the file's life):
        # appends are safe because the archive reader skips torn lines,
        # so a crash mid-append costs one bad line, never the history.
        _dropped = (lines[:-SUPPRESSION_LOG_MAX]
                    if len(lines) > SUPPRESSION_LOG_MAX else [])
        if _dropped:
            with open(SUPPRESSION_ARCHIVE_FILE, "a", encoding="utf-8") as _af:
                _af.write("\n".join(_dropped) + "\n")
                _af.flush()
                os.fsync(_af.fileno())
        # ATOMIC SWAP (Sep 11) + THE RETRY CONTRACT (Oct 2): truncate-
        # and-write left a window where a reader (the tide learner's
        # fit, the weekly hindsight report) saw an EMPTY ledger and
        # silently fit on nothing; the swap fixed the torn read, and a
        # reader HOLDING the file at the replace instant is the same
        # contention family — per-writer tmp + bounded backoff heals it
        # in-process instead of silently dropping a suppression row.
        from atomic_io import replace_atomic
        _tmp = SUPPRESSION_LOG_FILE + f".tmp.{os.getpid()}"
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-SUPPRESSION_LOG_MAX:]))
        replace_atomic(_tmp, SUPPRESSION_LOG_FILE)
        _tmp = SUPPRESSION_LOG_FILE + f".tmp.{os.getpid()}"
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-SUPPRESSION_LOG_MAX:]))
        replace_atomic(_tmp, SUPPRESSION_LOG_FILE)
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
        # .tmp then replace_atomic (the Oct 2 contract): readers never
        # see a partial file, and the rename retries past a concurrent
        # reader holding the target.
        from atomic_io import replace_atomic
        _tmp = SIGNAL_SNAPSHOTS_FILE + f".tmp.{os.getpid()}"
        with open(_tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-SIGNAL_SNAPSHOTS_MAX:]))
        replace_atomic(_tmp, SIGNAL_SNAPSHOTS_FILE)
    except Exception as exc:
        logger.debug("signal snapshot write failed: %s", exc)


# ── RELEASE PROVENANCE (user spec, Sep 22): a candidate the JOURNAL
# RELEASE saves from a veto must be identifiable on its fired-snapshot
# row — the hindsight ledger grades the release policy, so a released
# fire can never masquerade as a conventional one. Pair-keyed one-shot
# (the honesty contract of _SNAPSHOT_CTX): set ONLY on a release path,
# consumed by the pair's next popup cycle, voided by any burial, and
# expired after RELEASE_PROV_TTL_S so a stale mark never rides a fresh
# fire. Never raises.
RELEASE_PROV_TTL_S = 600
_RELEASE_PROV: dict = {}


def _mark_release(pair: str, gate: str) -> None:
    """Stamp the pair's release provenance (the gate that was eased)."""
    try:
        _RELEASE_PROV[str(pair)] = (str(gate), time.time())
    except Exception as exc:
        logger.debug("release mark %s/%s failed: %s", pair, gate, exc)


def _take_release_prov(pair: str):
    """Consume the mark: the eased gate's name, or None (conventional
    fire / stale / never marked). One-shot either way."""
    try:
        _mark = _RELEASE_PROV.pop(str(pair), None)
    except Exception:
        return None
    if not _mark:
        return None
    _gate, _ts = _mark
    try:
        if (time.time() - float(_ts)) <= RELEASE_PROV_TTL_S:
            return _gate
    except Exception:
        return None
    return None


# ── THE STALE-RELEASE TRIPWIRE (Oct 8, tripwire A's day-latched shape):
# the first time a stale-move JOURNAL RELEASE fires in production, the
# phone learns it — the strand of the funnel that buried 328 candidates
# learning to let one through is the moment to watch. Unlike tripwire A
# (a polling watcher — the penalty has no in-scanner moment), the
# release fires INSIDE the scanner: the emit rides the release sites
# and the latch is a day-key in this tripwire's own gitignored state
# file (the ops-watcher idiom: price_alert_state.json et al). The OPS
# push leg only (high prio; never the classified trade push — this is
# machinery, not a trade). Rails (tripwire A's): a failed push leaves
# the latch UNSET (the trip stays armed, the next release retries); a
# fresh day-key re-arms; only gate "stale-move" trips. Seams: now/push/
# state injectable — the drive runs it pure, production defaults touch
# only their own file.
STALE_RELEASE_ALERT_STATE = (os.path.dirname(os.path.abspath(__file__))
                             + os.sep + "stale_release_alert_state.json")


def _note_release_alert(pair: str, gate: str, pts: float, quick: float,
                        floor: float, now=None,
                        push=None, state=None) -> bool:
    """The trip: fire the phone push on this release, unless the
    day-latch holds. Returns True iff a push WENT OUT this call. Never
    raises — a tripwire failure must never break the release itself."""
    try:
        if gate != "stale-move":
            return False
        import json as _json
        _now = now if now is not None else datetime.now()
        _day = _now.strftime("%Y-%m-%d")
        st = state
        if st is None:
            try:
                st = _json.load(open(STALE_RELEASE_ALERT_STATE,
                                     encoding="utf-8"))
            except Exception:
                st = {}
        if not isinstance(st, dict):
            st = {}
        if str(st.get("stale_release_alert_day") or "") == _day:
            return False                       # the day-latch holds
        try:
            import discipline as _dc_sr
            _slack = int(_dc_sr.SEAM_RELEASE_SLACK)
        except Exception:
            _slack = 5
        _text = (f"🔓 STALE-MOVE JOURNAL RELEASE: {pair} released past the "
                 f"wiggle trap — quick {float(quick):.0f} within "
                 f"{_slack} pts of the floor "
                 f"{float(floor):.0f}, relief {float(pts):.1f} pts. "
                 "The gate that buried 328 let one through (1st: "
                 + ("yes" if not st.get("stale_release_alert_first")
                    else "no") + ")")
        # THE PUSH CHARTER (Oct 8, user spec): the phone carries ONLY the
        # two actionable instructions — BUY ALERT / EXIT ALERT. A gate
        # release is internal logic: it logs to the scanner log and the
        # console renders it; the phone never hears it. The default push
        # is the CONSOLE-ONLY sink: the emit is observable (the trip,
        # the latch, the first-ever marker all still record) without a
        # phone leg. No production caller passes a phone push.
        _push = push if push is not None else (
            lambda t, prio="log", tag=None:
            logger.info("  📲 STALE-RELEASE TRIPWIRE (console-only, the "
                        "push charter): %s", t))
        if not _push(_text):
            return False                       # failed push: stays ARMED
        _mark = {"stale_release_alert_day": _day,
                 "stale_release_alert_pair": str(pair),
                 "stale_release_alert_pts": float(pts)}
        if not st.get("stale_release_alert_first"):
            _mark["stale_release_alert_first"] = _day
        if state is not None:
            state.update(_mark)                # the injected plane
        else:
            # merge into the tripwire's OWN file (the re-read-merge
            # idiom; never scanner_state — this latch is ops-owned)
            try:
                _cur = _json.load(open(STALE_RELEASE_ALERT_STATE,
                                       encoding="utf-8"))
                if not isinstance(_cur, dict):
                    _cur = {}
            except Exception:
                _cur = {}
            _cur.update(_mark)
            _tmp = STALE_RELEASE_ALERT_STATE + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _fh:
                _json.dump(_cur, _fh, indent=2)
            os.replace(_tmp, STALE_RELEASE_ALERT_STATE)
        logger.info("  📲 STALE-RELEASE TRIPWIRE: %s (%s) — the phone "
                    "learned the first spend", pair, _day)
        return True
    except Exception as exc:
        logger.debug("stale-release tripwire failed (non-fatal): %s", exc)
        return False


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
        # THE GOAL IS THE OPERATIVE ONE (Nov 7): every caller on the fire
        # path — the champion signal, the mover — hands
        # this wrapper whatever it happens to carry, and a missing/zero stamp
        # used to become a hard 1.5 inside the gate. Route it the way the
        # entry stamp and the close record already route theirs, so the gate
        # prices clearance/floor/runway against the goal the plan will manage.
        goal_pct = _operative_goal_pct(goal_pct, pair)
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
#    mover): balance overview (total/available/committed),
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

# ── THE RISK ENVELOPE (Nov 6, the getout-cascade review's four majors):
#    the ledger says the BOOK lost the money, not the gates — 19 longs at
#    ~173% of balance flushed in one candle, the $5 stop on an $1,800 row
#    is 0.28% (noise, so every row stopped in the same tick), and every
#    "MIN SIZE" fire booked its MAX because the weaken was a floor, not a
#    clamp. Four dials fix the envelope; all read LIVE at call time and
#    SCOUT_STRICT_FIRES=0 restores the old funnel verbatim.
STRICT_FIRES_DEFAULT = True          # env SCOUT_STRICT_FIRES
MAX_AGGREGATE_EXPOSURE_FRAC = 0.60   # of balance, across every open row
MAX_FIRES_PER_DAY = 8                # daily count (advisory; kept for mandate/probe)
HARD_STOP_MAX_PCT = 2.5              # of position size — its ceiling
HARD_STOP_ABS_FLOOR_USD = 0.50


def _strict_fires() -> bool:
    """The risk-envelope dial (default ON): marginal setups are refused
    instead of fired at minimum size, and the aggregate book + the daily
    fire count are capped. Fail-open to the default on a broken env."""
    try:
        return os.environ.get("SCOUT_STRICT_FIRES", "").strip().lower() not in (
            "0", "false", "off", "no")
    except Exception:
        return STRICT_FIRES_DEFAULT


# ── THE DEATH-LINE LESSON (Nov 6, user rule: "if the death line is reached
#    then the brain needs to learn from that so it goes the complete other
#    direction"): a breach is the one fully-observed feedback event this book
#    produces, so it is allowed to change the policy without a backtest. The
#    autopsy (death_lessons.py) reads the ledger, convicts the size and hold
#    bands that bled, and arms a mandate the fire gate obeys: the condemned
#    shapes are refused outright and the book learns on ONE small, short-window
#    PROBE a day until its own audit confirms or revokes the flip.
_DM_CACHE = {"t": 0.0, "doc": None}
_DM_ARMED_FOR = None


def _death_mandate() -> dict | None:
    """The armed lesson, cached ~30s (the gate runs per candidate). None when
    the book has never breached. An UNREADABLE mandate returns the sentinel
    doc and the gate stands closed — a corrupt lesson must never silently
    restore the behaviour that died."""
    global _DM_CACHE
    try:
        import time as _time
        _now = _time.time()
        if _now - float(_DM_CACHE.get("t") or 0.0) < 30.0:
            return _DM_CACHE.get("doc")
        import death_lessons as _dl
        _doc, _err = _dl.load_mandate()
        if _err:
            logger.warning("death mandate unreadable (%s) — the fire gate "
                           "stands CLOSED until it is fixed", _err)
        _DM_CACHE = {"t": _now, "doc": _doc}
        return _doc
    except Exception as exc:
        logger.debug("death mandate loader unavailable: %s", exc)
        return None


def _audit_death_mandate() -> dict | None:
    """Re-audit the armed lesson against the ledger (boot-time, once per
    process). The flip must pay for its own life: at AUDIT_MIN_CLOSES closes
    it CONFIRMS or REVOKES itself, and the verdict is persisted."""
    global _DM_CACHE
    try:
        import death_lessons as _dl
        _doc = _death_mandate()
        if not isinstance(_doc, dict) or _doc.get("status") != "armed":
            return _doc
        _closes = _dl.load_closes(CONTEST_TRADES_FILE)
        _new = _dl.audit(_doc, closes=_closes)
        # THE WIDENING: re-read which stamped families have earnt the lift.
        _new = _dl.apply_evidence(_new, _closes)
        if _new != _doc:
            _dl.save_mandate(_new)
            _DM_CACHE = {"t": 0.0, "doc": _new}
            logger.warning("  DEATH MANDATE AUDIT: %s", _dl.describe(_new))
        return _new
    except Exception as exc:
        logger.debug("death mandate audit skipped: %s", exc)
        return None


def _arm_death_mandate(state: dict | None) -> dict | None:
    """Run the autopsy at the breach (once per contest per process) and arm
    the flip. Never raises: a failed lesson must not break the refusal that
    carries it — the death line still stands."""
    global _DM_ARMED_FOR, _DM_CACHE
    try:
        import contest_account as _ca
        import death_lessons as _dl
        _cid = str(_ca.current_contest_id(state) or "")
        if _DM_ARMED_FOR == _cid:
            return _DM_CACHE.get("doc")
        _doc, _new = _dl.record_breach(
            state=state, line=_ca.death_line(state), contest_id=_cid,
            closes_path=CONTEST_TRADES_FILE)
        _DM_ARMED_FOR = _cid
        _DM_CACHE = {"t": 0.0, "doc": _doc}      # force the next read fresh
        if _new:
            _f = _doc.get("flip") or {}
            logger.warning("  DEATH MANDATE ARMED: %s", _dl.describe(_doc))
            logger.warning("  THE FLIP: spend under $%s, windows under %s min, "
                           "%s probe(s)/day — the condemned shape is refused "
                           "outright (the lesson is audited after %s closes)",
                           _f.get("size_cap_usd"), _f.get("hold_cap_min"),
                           _f.get("probes_per_day"), _dl.AUDIT_MIN_CLOSES)
        return _doc
    except Exception as exc:
        logger.warning("death mandate could not be armed (%s) — the death "
                       "line still refuses every fire", exc)
        return None


DOLLAR_GOAL_EXIT_KEY = "DOLLAR_GOAL_EXIT"


def _dollar_goal_armed() -> bool:
    """THE DOLLAR-GOAL DIAL, read LIVE (no import freeze — the profit_guard
    lesson). Absence = ARMED: the user's Nov 10 rule is that the dollar goal
    triggers the exit check, replacing the % goal that only announced. "0"
    (or false/off/no) reverts every open row to the incumbent % behaviour on
    the next cycle, no restart."""
    try:
        return str(os.environ.get(DOLLAR_GOAL_EXIT_KEY, "1")).strip().lower() \
            not in ("0", "false", "off", "no", "")
    except Exception:
        return True


KNIFE_BLOCK_KEY = "KNIFE_BLOCK"
# THE DIAL THE RUNNING PROCESS CAN HEAR. os.environ is fixed at spawn, so an
# env var alone cannot be flipped in a live scanner without a restart -- and a
# dial that needs a restart is not a dial. This file is re-read on EVERY knife
# evaluation (cheap: one small stat/read per candidate, and only on the
# candidates that reach the deep pre-check).
KNIFE_BLOCK_DIAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "KNIFE_BLOCK.dial")
_KNIFE_BLOCK_SEEN = {"raw": "\x00unset"}


def _knife_block_raw():
    """(value, source) -- the DIAL FILE wins, then env, then None (armed).

    FILE FIRST (Nov 10): the file is the dial the running process re-reads
    every cycle, so it is the one in force. An env var is frozen at launch;
    letting it outrank the file meant a stale `KNIFE_BLOCK=0` in a launcher
    could keep the gate disarmed no matter what the dial said -- a dial you
    cannot flip back is not a dial."""
    try:
        with open(KNIFE_BLOCK_DIAL_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip(), "file"
    except OSError as exc:
        # NO DIAL FILE IS THE NORMAL ARMED DEFAULT (see the docstring), so the
        # miss is logged once per distinct error instead of once per cycle --
        # a trace exists, and the scanner log stays readable.
        if _KNIFE_BLOCK_SEEN.get("file_err") != str(exc):
            _KNIFE_BLOCK_SEEN["file_err"] = str(exc)
            logger.debug("  KNIFE_BLOCK dial unreadable (%s) -- falling back "
                         "to env/default", exc)
    try:
        v = os.environ.get(KNIFE_BLOCK_KEY)
    except (OSError, ValueError) as exc:
        # an unreadable environment cannot arm or disarm the gate; the
        # default (armed) stands and the reason is named, not swallowed
        v = None
        logger.debug("  KNIFE_BLOCK env unreadable (%s) -- default stands", exc)
    if v is not None and str(v).strip() != "":
        return str(v).strip(), "env"
    return None, "default"


def _knife_blocks_entry() -> bool:
    """THE DOWNTREND KNIFE, ARMED AS A REFUSAL (user decision, Nov 10).

    The Oct 9 unified rule made a broken-trend fire ADVERSITY: it landed at
    0.10x and was never refused. Measured on the three contest windows (net
    of fees, 1 slot at 10%, `_chain_lab`), that policy ends w1 +7.61 /
    w2 +2.43 / **w3 -0.70**; refusing the fire outright ends w1 +5.34 /
    w2 +5.76 / **w3 +2.76** -- the window that kills the account turns green,
    at the cost of ~2.3 points of window-1 upside. The user chose the
    refusal, so this defaults ARMED: no env var and no dial file = block.

    FLIP WITHOUT A RESTART: write "0" (or false/off/no) to KNIFE_BLOCK.dial —
    or set env KNIFE_BLOCK=0 before launch — and the next knife evaluation
    falls back to the Oct 9 min-size behaviour. Write "1" and it re-arms. The
    read is logged when the value CHANGES, so a flip is visible in the scanner
    log on the next cycle even if no candidate reaches the knife that cycle."""
    raw, source = _knife_block_raw()
    armed = str(raw if raw is not None else "1").strip().lower() \
        not in ("0", "false", "off", "no", "")
    if _KNIFE_BLOCK_SEEN.get("raw") != raw:
        _KNIFE_BLOCK_SEEN["raw"] = raw
        try:
            logger.info("  KNIFE_BLOCK dial: %s (source: %s) — %s",
                        "unset" if raw is None else raw, source,
                        "ARMED — a broken-trend fire is REFUSED" if armed
                        else "OFF — a broken-trend fire lands at MINIMUM SIZE (Oct 9 rule)")
        except (TypeError, ValueError) as exc:
            # a broken log call must never disturb the dial's answer; the
            # failure itself is reported, not swallowed
            logger.debug("  KNIFE_BLOCK dial change not logged: %s", exc)
    return armed


def entry_knife_verdict(*, asset, pair, cfg, df=None, floor=None, signal=None,
                        deep=None, df_state=None, df_pct=None, rot=None,
                        fetch_1h=None):
    """THE ENTRY TREND GATE — the one place a fire is judged against its 1h
    trend, and the only place the refusal line and its ledger row are written.

    Returns (verdict, note): 'pass' | 'weaken' | 'block'.

    WHY THIS IS A FUNCTION (Nov 10): the gate used to live inline, inside
    `if _deep is not None:` AND inside a try whose handler said "desktop
    pre-check unavailable ... alerting anyway". That is two fail-open holes in
    a survival gate — a deep read returning None skipped the trend test
    entirely, and any raise in the block let the fire land untested. It now
    runs on its own, fetches its own 1h frame when the deep read never
    produced one, and FAILS CLOSED: an unreadable trend is a refused fire.

    Side effects (on purpose, so a proof can read them): one logger line, and
    one row in the popup-suppression ledger."""
    def _emit(verdict, note):
        try:
            _quick = float(getattr(signal, "confidence", 0) or 0) or None
        except Exception:
            _quick = None
        try:
            _brain = float(getattr(deep, "confidence", 0) or 0) or None
        except Exception:
            _brain = None
        _tide = (f"{df_state} {df_pct:.0f}%" if (df_state and df_pct is not None)
                 else df_state)
        try:
            if verdict == "block":
                logger.info("  KNIFE (block): %s %s — the popup "
                            "STANDS DOWN (no fire into a broken trend)", asset, note)
            else:
                logger.info("  KNIFE (weaken): %s %s — the fire LANDS at minimum size",
                            asset, note)
        except (TypeError, ValueError) as exc:
            # 3795: the popup line is cosmetic; a garbled note must not stop
            # the gate from returning its verdict, but the reason is named
            logger.debug("  KNIFE note line skipped: %s", exc)
        try:
            _log_suppression(pair, "knife",
                             f"{note} — " + ("refused outright (the downtrend knife "
                                             "is a gate, not a size cut; KNIFE_BLOCK=0 reverts)"
                                             if verdict == "block" else
                                             "minimum size (KNIFE_BLOCK off: the Oct 9 rule)"),
                             quick=_quick, brain=_brain,
                             floor=(float(floor) if floor is not None else None),
                             tide=_tide, rot=rot)
        except (OSError, TypeError, ValueError) as exc:
            # 3806: the suppression ledger is a report, not the gate -- a write
            # failure is named here instead of vanishing
            logger.debug("  KNIFE suppression log skipped: %s", exc)
        return verdict, note

    def _closes_from(frame):
        try:
            if frame is None:
                return None
            return frame["close"] if "close" in frame else None
        except Exception:
            return None

    try:
        _short = int(getattr(cfg, "ema_short", 9) or 9)
        _long = int(getattr(cfg, "ema_long", 21) or 21)
    except Exception:
        _short, _long = 9, 21

    closes = _closes_from(df)
    if closes is None or len(closes) < _long + 2:
        # the deep read never produced a frame (or produced a stub): read the
        # trend independently rather than skipping the test
        if fetch_1h is not None:
            try:
                import pandas as _pd
                _ohl = fetch_1h()
                if _ohl and len(_ohl) >= _long + 2:
                    closes = _pd.DataFrame(
                        _ohl, columns=["timestamp", "open", "high", "low",
                                       "close", "volume"])["close"]
            except Exception:
                closes = None
    if closes is None:
        return _emit("block", "trend unreadable (no 1h frame for the gate) — "
                              "fail-closed, the fire is refused")
    try:
        from indicators import ema as _ema
        _es = float(_ema(closes, _short).iloc[-1])
        _el = float(_ema(closes, _long).iloc[-1])
        _px = float(closes.iloc[-1])
    except Exception as _exc:
        return _emit("block", f"trend unreadable ({_exc}) — fail-closed, "
                              "the fire is refused")
    if not (_es < _el and _px < _el):
        return "pass", ""
    note = (f"downward trend (EMA{_short} {_es:.6f} < EMA{_long} {_el:.6f}, "
            f"price {_px:.6f} under both)")
    return _emit("block" if _knife_blocks_entry() else "weaken", note)


def _hard_stop_usd(entry: float, size: float, buffer_usd: float) -> float:
    """THE SMOOTH STOP (the cascade's $5 pathology): the stop is a
    PERCENTAGE of the position (up to HARD_STOP_MAX_PCT), never a flat $5
    that is 0.28% of an $1,800 row — and it still TIGHTENS inside half
    the remaining death-line buffer, so the gradient runs smoothly from
    2.5% of size on a healthy book to $0.50 at the line instead of
    jumping to a $5 cliff. A zero buffer means the account is at its
    floor: the stop is the absolute floor and the row exits on the next
    tick, which is the correct answer there."""
    try:
        size = float(size or 0.0)
        _half = max(float(buffer_usd or 0.0), 0.0) * 0.5
        if size <= 0:
            return max(HARD_STOP_ABS_FLOOR_USD, _half)
        _pct_stop = size * HARD_STOP_MAX_PCT / 100.0
        return max(HARD_STOP_ABS_FLOOR_USD, min(_pct_stop, _half))
    except Exception:
        return 5.0


def configured_base_spend(balance: float, cfg=None) -> float:
    """THE CONFIGURED ENTRY CEILING (Nov 11): `contest_size_frac` of the live
    balance -- the base the scanner sizes from before the book carry, the 3%
    guard cap and the weakened-size rules cut it (see
    contest_account.sized_entry_usd for the whole rule in one place).

    Before this the base was 100% of the balance, so no configured number could
    bind and live's per-trade risk was whatever the guard cap happened to allow.
    A missing/absurd value falls back to 1.0 (the old behaviour) rather than
    silently sizing every entry at zero.
    """
    c = cfg if cfg is not None else DEFAULT_CONFIG
    try:
        frac = float(getattr(c, "contest_size_frac", 1.0) or 1.0)
    except Exception:
        frac = 1.0
    frac = min(max(frac, 0.0), 1.0)
    try:
        bal = float(balance or 0.0)
    except Exception:
        bal = 0.0
    if bal <= 0.0:
        try:
            bal = float(getattr(c, "challenge_balance", 0.0) or 0.0)
        except Exception:
            bal = 0.0
    return bal * frac


def _weakened_spend(spend: float, base_spend: float) -> float:
    """THE MIN-SIZE CLAMP (the label/ledger mismatch): a weakened fire
    books the MINIMUM viable size — min(spend, 10% of the sized base) —
    never the base it was supposed to shrink from. The old max() was a
    floor: it could RAISE a fire to 10% but never lower one, so every
    "MIN SIZE" popup booked its MAX and no calibration layer could learn
    from the sizing it thought it had made."""
    try:
        _spend = float(spend or 0.0)
        _base = float(base_spend or 0.0)
        if _base <= 0:
            return _spend
        _min = _base * 0.10
        return (min(_spend, _min) if _spend > 0 else _min)
    except Exception:
        return spend


def _weakened_fire_size(pair: str, base_spend: float,
                        goal_pct: float) -> dict | None:
    """THE WEAKENED SIZING, WIRED (Nov 7): the live account, the console's
    worth-floor dial, the book already carried and this coin's beta are
    handed to `contest_account.weakened_size_usd`, which returns the
    smallest size that pays the pace bar inside what the death line can
    still lose — or an explicit NO.

    The allowance handed over is what is LEFT of the day's: the open book's
    own stressed worst case is priced out of it exactly as the gap-stress
    gate prices it. Returns None when the account or the watch cannot be
    read — the caller then keeps the 10% floor. Fail-soft in the SAFE
    direction: an unreadable account can never inflate a fire."""
    try:
        import contest_account as _ca_w
        _st_w = _ca_w.load_state()
        _bal_w = float(_st_w["current_balance"])
        _bar_w = _ca_w.daily_pace_usd(_st_w) * _worth_floor_frac()
        _allow_w = _ca_w.stress_allowance_usd(_bal_w)
        _buf_w = _ca_w.stress_allowance_usd(_bal_w, frac=1.0)
        _rows_w = _load_contest_watch() or {}
        _legs_w = []
        for _p_w, _r_w in _rows_w.items():
            if not isinstance(_r_w, dict):
                continue                      # a tombstone is not exposure
            _sz_w = float(_r_w.get("size") or 0.0)
            if _sz_w <= 0:
                continue
            _stp_w = _hard_stop_usd(float(_r_w.get("entry") or 0.0),
                                    _sz_w, _buf_w)
            _legs_w.append((_sz_w, _stp_w / _sz_w * 100.0,
                            _ca_w.stress_beta(_p_w)))
        _used_w = _ca_w.stress_loss_usd(_legs_w)
        _room_w = max(0.0, MAX_AGGREGATE_EXPOSURE_FRAC * _bal_w
                      - _open_exposure_usd(_rows_w))
        return _ca_w.weakened_size_usd(
            base_spend, goal_pct=goal_pct, pace_bar_usd=_bar_w,
            allowance_usd=max(0.0, _allow_w - _used_w),
            beta=_ca_w.stress_beta(pair), book_room_usd=_room_w)
    except Exception as exc:
        logger.debug("weakened sizing unavailable: %s", exc)
        return None


def _economics_plan_for(pair: str, goal_pct: float, stop_pct: float):
    """The shared money verdict for every fire path (champion + Sure Thing).

    Returns the strategy_economics.assess() plan, or None when the account or
    ledger cannot be read (callers fail closed: no new risk). It never writes
    state and never places an order.
    """
    try:
        import contest_account as _ca_e
        import strategy_economics as _econ_e
        _acct = _ca_e.load_state()
        _bal = float(_acct.get("current_balance") or 0.0)
        _win = float(_ca_e.win_line(_acct))
        _remaining = max(0.0, _win - _bal)
        # THE CONTEST HAS NO TIME LIMIT (user rule, Nov 12): +12% is a
        # progressive target, not a seven-day sprint. The economics gate
        # must not refuse a positive edge because a fake deadline makes the
        # pace look impossible; days_left=0 disables only the pace-size
        # check, never the edge or reward/risk check.
        _days = 0.0
        try:
            _cap = float(_ca_e.max_entry_usd(
                _bal, _ca_e.window_drawdown_pct(pair),
                float(_acct.get("start_balance") or _bal)))
        except Exception:
            _cap = None
        _rows = _ca_e._rolling_trade_rows()
        return _econ_e.assess(
            _rows, remaining_usd=_remaining, days_left=_days,
            cadence=_econ_e.recent_cadence(_rows), goal_pct=goal_pct,
            stop_pct=stop_pct, max_size_usd=_cap,
            max_cadence=float(MAX_FIRES_PER_DAY))
    except Exception as exc:
        logger.warning("economics plan unavailable (%s)", exc)
        return None


def _paying_tranche(pair: str, base_spend: float, goal_pct: float,
                    ceiling_usd=None) -> dict | None:
    """THE TRANCHE-TO-PAY, WIRED (Nov 7): the live account, the console's
    worth-floor dial, the book already carried, this coin's beta and the
    -3% guard's own ceiling are handed to
    `contest_account.paying_tranche_usd`, which returns the smallest
    tranche that pays the pace bar inside every cap the death line already
    set — or an explicit NO with the arithmetic.

    The allowance handed over is what is LEFT of the day's, net of the open
    book's stressed worst case, exactly as the gap-stress gate prices it.
    Returns None when the account or the watch cannot be read: the caller
    then leaves the base exactly as the funnel sized it. Fail-soft in the
    SAFE direction — an unreadable account can never inflate a tranche."""
    try:
        import contest_account as _ca_p
        _st_p = _ca_p.load_state()
        _bal_p = float(_st_p["current_balance"])
        _bar_p = _ca_p.daily_pace_usd(_st_p) * _worth_floor_frac()
        _allow_p = _ca_p.stress_allowance_usd(_bal_p)
        _buf_p = _ca_p.stress_allowance_usd(_bal_p, frac=1.0)
        _rows_p = _load_contest_watch() or {}
        _legs_p = []
        for _p_p, _r_p in _rows_p.items():
            if not isinstance(_r_p, dict):
                continue                      # a tombstone is not exposure
            _sz_p = float(_r_p.get("size") or 0.0)
            if _sz_p <= 0:
                continue
            _stp_p = _hard_stop_usd(float(_r_p.get("entry") or 0.0),
                                    _sz_p, _buf_p)
            _legs_p.append((_sz_p, _stp_p / _sz_p * 100.0,
                            _ca_p.stress_beta(_p_p)))
        _used_p = _ca_p.stress_loss_usd(_legs_p)
        _room_p = max(0.0, MAX_AGGREGATE_EXPOSURE_FRAC * _bal_p
                      - _open_exposure_usd(_rows_p))
        return _ca_p.paying_tranche_usd(
            base_spend, goal_pct=goal_pct, pace_bar_usd=_bar_p,
            ceiling_usd=ceiling_usd,
            allowance_usd=max(0.0, _allow_p - _used_p),
            beta=_ca_p.stress_beta(pair), book_room_usd=_room_p)
    except Exception as exc:
        logger.debug("tranche-to-pay unavailable: %s", exc)
        return None


def _hold_window_cap() -> float | None:
    """The armed death mandate's hold cap in minutes, or None when there is
    no lesson to satisfy.

    (Nov 7) The cap is a CONSTRAINT, not a threshold. The funnel reads it
    here to resolve a candidate BEFORE a fire is sized; the gate reads the
    same number afterwards to judge one. A mandate that cannot be read
    returns None — the gate keeps its own fail-closed verdict on that file,
    and the funnel must never invent a window neither of them can verify.
    """
    try:
        _doc = _death_mandate()
        if not isinstance(_doc, dict) or str(_doc.get("status") or "") != "armed":
            return None
        _flip = _doc.get("flip") if isinstance(_doc.get("flip"), dict) else {}
        _cap = float(_flip.get("hold_cap_min"))
        if not (_cap > 0.0):
            return None
        return _cap
    except Exception:
        return None


PACE_TTL_S = 900.0          # a coin's pace is re-read every 15 minutes
_pace_cache: dict = {}


def _pace_basis_label(pace) -> str:
    """How the plan's horizon was chosen, in words.

    One place, so a log line can never disagree with the number it explains —
    or crash the scan on a stray percent sign (the honest label "the goal at
    1%/h" inside a %-format string aborted a whole ticker scan on Nov 7).
    """
    try:
        _p = float(pace)
    except (TypeError, ValueError):
        return "the goal at 1%/h, unmeasured"
    if _p != _p:                                   # NaN is not a pace
        return "the goal at 1%/h, unmeasured"
    if _p >= 1.25:
        return "the coin's own %.2f%%/h" % _p
    return "the goal at 1%%/h (the tape read %.2f%%/h)" % _p


def _pace_pct_per_hour(exchange, pair: str):
    """The coin's OWN measured hourly pace, or None when it cannot be read.

    Median of the last hours' hourly returns (contest_account does the
    arithmetic). Cached per pair for PACE_TTL_S so a scan cycle asks the
    exchange once, not once per gate. None is the honest answer for a thin
    tape or a failed fetch: the caller then plans on the engine's 1%/hour
    assumption, exactly as every fire did before this rule existed.
    """
    try:
        _now = time.time()
        _hit = _pace_cache.get(pair)
        if _hit and (_now - float(_hit[0])) < PACE_TTL_S:
            return _hit[1]
        if exchange is None:
            return None
        _ohl = exchange.fetch_ohlcv(pair, timeframe="1h", limit=8)
        _closes = [float(_k[4]) for _k in (_ohl or [])
                   if _k is not None and len(_k) > 4]
        import contest_account as _ca_pc
        _pace = _ca_pc.measured_pace_pct_per_hour(_closes)
        _pace_cache[pair] = (_now, _pace)
        return _pace
    except Exception as exc:
        logger.debug("pace read failed for %s (%s) — the 1%%/h assumption stands",
                     pair, exc)
        return None


def _goal_clock_decide(*, asset: str, pair: str, exchange, goal_pct: float,
                       hold_cap_min=None) -> dict:
    """THE CHAMPION CLOCK'S DECISION - one function, so the live scan and a
    test drive the SAME arithmetic (the block used to be a closure no test
    could reach).

    The rule the arc is built on: the plan is the coin's own measured hour
    (goal / pace x 60), the 90-minute cap is never relaxed, and a goal that
    does not fit inside the cap is capped to what the window can reach. When
    even the engine's +GOAL_MIN_PCT floor does not fit, the candidate is
    skipped at scan time with the arithmetic on the line - no fire is built
    that the mandate is arithmetically certain to refuse.

    A quiet or unreadable tape can only ever KEEP the 1%/hour assumption; it
    can never lengthen a plan against a cap priced at 1%/h.
    """
    _pace = _pace_pct_per_hour(exchange, pair)
    if _pace is not None and _pace >= 1.25:
        logger.info("  PACE: %s moved %.2f%%/h across its last hours - "
                    "the plan is the coin's own tape, not the 1%%/h assumption",
                    asset, _pace)
    _goal = float(goal_pct or 0.0)
    # the plan the 1%/h assumption demanded for the ORIGINAL goal - kept
    # before the cap so the refusal it caused stays auditable ("not 156")
    _plan_1pct = _goal * 60.0
    _basis_rate = 1.0 if _pace is None else max(1.0, float(_pace))
    _capped_from = None
    _reach = None
    if hold_cap_min is not None:
        try:
            import contest_account as _ca_d
            _reach = _ca_d.hold_reachable_goal_pct(
                hold_cap_min, pace_pct_per_hour=_pace)
            if not (_reach >= GOAL_MIN_PCT):
                logger.info("  HOLD WINDOW: %s skipped at scan time - the armed "
                            "lesson holds under %.0f min, which reaches only "
                            "+%.2f%% at %.2f%%/h, below the engine's +%.1f%% "
                            "floor (no fire is built)",
                            asset, hold_cap_min, _reach, _basis_rate,
                            GOAL_MIN_PCT)
                return {"skipped": True, "reason": "hold-window",
                        "goal_pct": _goal, "reach_pct": _reach,
                        "pace_pct_per_hour": _pace, "hold_cap_min": hold_cap_min,
                        "basis_rate": _basis_rate,
                        "plan_min_at_1pct": _plan_1pct}
            if _goal > _reach:
                logger.info("  GOAL CAPPED TO THE HOLD WINDOW: %s - +%.1f%% -> "
                            "+%.2f%% (plan %.0f min, not %.0f) - the armed lesson "
                            "holds under %.0f min at %.2f%%/h",
                            asset, _goal, _reach, _reach * 60.0, _goal * 60.0,
                            hold_cap_min, _basis_rate)
                _capped_from = _goal
                _goal = _reach
        except Exception as _d_exc:
            logger.debug("hold-window goal cap skipped: %s", _d_exc)
    _win = _goal_plan_min(_goal, pace_pct_per_hour=_pace)
    return {"skipped": False, "goal_pct": _goal, "window_min": _win,
            "pace_pct_per_hour": _pace, "basis": _pace_basis_label(_pace),
            "capped_from": _capped_from, "hold_cap_min": hold_cap_min,
            "reach_pct": _reach, "basis_rate": _basis_rate,
            "plan_min_at_1pct": _plan_1pct}


PLAN_DRIFT_TTL_S = 900.0        # an overdue fire is re-reported every 15 minutes
_plan_drift_state: dict = {}


def plan_drift(row, now=None):
    """How far an open fire has drifted against its OWN planned window.

    Pure, read-only: the row's planned minutes against the minutes it has
    actually spent. Returns None when there is nothing honest to say — no
    window (a legacy row), no timestamp, a garbled row, or a
    negative elapsed time (clock skew) — never a zero or a guess.

    Bands: "on plan" (<75% of the window), "drifting" (75-100%) and
    "overdue" (past it). The report is context for the eye: the exit is
    still the trend watch's call, never the clock's.
    """
    if not isinstance(row, dict):
        return None
    try:
        _planned = float(row.get("window_min") or 0.0)
    except (TypeError, ValueError):
        return None
    if not (_planned > 0.0):
        return None
    _opened = row.get("opened_ts") or row.get("added")
    if not _opened:
        return None
    try:
        _t0 = datetime.fromisoformat(str(_opened))
        _now = now if now is not None else _now_mst()
        _elapsed = (_now - _t0).total_seconds() / 60.0
    except Exception:
        return None
    if _elapsed < 0.0 or _elapsed != _elapsed:     # NaN is not elapsed time
        return None
    _frac = _elapsed / _planned
    if _frac < 0.75:
        _band = "on plan"
    elif _frac <= 1.0:
        _band = "drifting"
    else:
        _band = "overdue"
    return {"planned_min": _planned, "elapsed_min": _elapsed,
            "frac": _frac, "band": _band,
            "left_min": _planned - _elapsed,
            "opened": str(_opened)}


def _report_plan_drift(pair: str, row, pct=None, now=None):
    """Say it out loud, once per band per fire. REPORT ONLY.

    A fire crossing into "drifting" or "overdue" is a fire whose plan is
    being outlived, and that must be visible BEFORE the exit watch has to
    act on a reversal. Nothing here moves a stop, a goal or a row: the
    report is a voice, the exit is hers (the clock is context, never an
    order — the same rule that keeps a winning fire alive).
    """
    _d = plan_drift(row, now=now)
    if not _d:
        return None
    try:
        _st = _plan_drift_state.get(pair) or {}
        _fresh_fire = str(_st.get("opened") or "") != _d["opened"]
        _band_changed = _fresh_fire or _st.get("band") != _d["band"]
        _now_s = time.time()
        _due = (_now_s - float(_st.get("at") or 0.0)) >= PLAN_DRIFT_TTL_S
        _speak = _band_changed or (_d["band"] != "on plan" and _due)
        # The latch's clock runs from the last time it SPOKE. Re-stamping it
        # on a silent call pushes the deadline out on every loop tick and
        # makes the re-report unreachable (the Nov 7 soak: a 900 s TTL with
        # 25 s calls — an overdue fire that held spoke once, then went dark).
        _plan_drift_state[pair] = {
            "opened": _d["opened"], "band": _d["band"],
            "at": _now_s if _speak else float(_st.get("at") or _now_s)}
        if not _speak:
            return _d
        _goal = None
        try:
            _goal = float(row.get("goal_pct") or 0.0) or None
        except (TypeError, ValueError):
            _goal = None
        _where = ("%+.2f%% of +%.2f%%" % (pct, _goal)
                  if (pct is not None and _goal is not None) else "move unread")
        if _d["band"] == "overdue":
            _tail = ("OVERDUE by %.0f min — the plan is spent; the exit is still "
                     "the trend watch's call, never the clock's"
                     % (_d["elapsed_min"] - _d["planned_min"]))
        elif _d["band"] == "drifting":
            _tail = ("%.0f min left — the trend watch calls the exit, this line "
                     "is only the plan" % max(_d["left_min"], 0.0))
        else:
            _tail = "%.0f min left" % max(_d["left_min"], 0.0)
        logger.info("  PLAN vs CLOCK: %s — %.0f/%.0f min (%.0f%%), %s (%s) — %s",
                    pair.split("/")[0], _d["elapsed_min"], _d["planned_min"],
                    _d["frac"] * 100.0, _d["band"].upper(), _where, _tail)
    except Exception as _pd_exc:
        logger.debug("plan-drift report skipped for %s: %s", pair, _pd_exc)
    return _d


PLAN_SPENT_EXIT_MOD = 0.25      # a trend read this damaged, on a SPENT plan, ends the thesis


def plan_spent_exit(row, exit_mod=None, now=None):
    """THE PLAN-SPENT TREND GATE: the clock reports, the TREND closes.

    A fire whose planned window is spent and whose trend the exit watch has
    since written off has no thesis left — holding it is just paying the
    cost of the round trip twice. But this needs BOTH halves, always:

      * the plan must be OVERDUE (plan_drift), so a healthy fire inside its
        window is untouched at 95% of it;
      * the exit watch's OWN read (ExitMod on the row) must be at least
        PLAN_SPENT_EXIT_MOD and at least the read the fire was BORN with —
        new deterioration, not the background hum a fire inherits. A fire
        improving on a bad entry read is never killed by its clock.

    Returns the arithmetic, or None when either half is missing or
    unreadable. Missing/unreadable trend reads do NOT close: the clock alone
    may never end a fire (user rule, Sep 21 — "the window NEVER exits by
    itself"), so the failure mode is always to hold.
    """
    _d = plan_drift(row, now=now)
    if not _d or _d["band"] != "overdue":
        return None
    if exit_mod is None:
        _gate = row.get("exit_watch") if isinstance(row, dict) else None
        exit_mod = _gate.get("exit_mod") if isinstance(_gate, dict) else None
    try:
        _em = float(exit_mod)
    except (TypeError, ValueError):
        return None
    if _em != _em or _em < 0.0:          # NaN / negative read is not a read
        return None
    try:
        _born = float((row or {}).get("entry_exit_mod"))
    except (TypeError, ValueError):
        _born = 0.0
    _thr = max(PLAN_SPENT_EXIT_MOD, _born)
    if _em < _thr:
        return None
    return {"plan": _d, "exit_mod": _em, "threshold": _thr}


SETUP_TAGS_VERSION = 1


def _setup_tags(*, route: str = "", tide_state=None, tide_pct=None,
                conviction=None, brain=None, mult=None, climb=None,
                clearance=None, er=None, weak="", size=None, base=None,
                fast=False, reentry=False, hail=False, probe=False) -> dict:
    """THE FIRE-TIME SETUP STAMP (Nov 6 — the expectancy study's ask).

    The Nov 6 study had to reconstruct every trade's family by joining
    closes to popup TEXT (pair + entry time) and parsing lines like
    "TIDE: OUT 41%". 2,597 of 2,666 closes matched, and the 69 that did
    not were silently unmeasurable. The stamp ends that: the fire writes
    its own family onto the watch row, the close copies it into the
    ledger, and expectancy is a group-by — no join, no text parsing, no
    unmatched rows.

    Pure and total: every field normalizes to a JSON-safe scalar. A None
    is a real answer ("this route never read it"), never a raise — a
    stamp failure must not cost the fire its live tracking."""
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _i(v):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return None

    _verdict, _why = None, ""
    if isinstance(climb, dict):
        _verdict = "PASS" if climb.get("ok") else "FAIL"
        _why = str(climb.get("fail_reason") or climb.get("reason") or "")[:80]
    elif isinstance(climb, str):
        _verdict = climb.strip().upper()[:8] or None

    return {
        "v": SETUP_TAGS_VERSION,
        "route": (str(route)[:24] or None) if route else None,
        "tide": (str(tide_state).strip().upper()[:8]
                 if tide_state else None),
        "tide_pct": _i(tide_pct),
        "conviction": _i(conviction),
        "brain": _i(brain),
        "mult": _f(mult),
        "climb": _verdict,
        "climb_why": _why or None,
        "clearance": _f(clearance),
        "er": _f(er),
        # the weak-fire mark IS the min-size mark (the popup's own flag)
        "weak": (str(weak)[:80] or None) if weak else None,
        "min_size": bool(weak),
        "size": _f(size),
        "base": _f(base),
        "fast": bool(fast),
        "reentry": bool(reentry),
        "hail": bool(hail),
        # a probe is real money but not a full-risk trade; stamp it so the
        # next edge read can separate probe outcomes from full-size ones.
        "probe": bool(probe),
    }


def _setup_family(tags) -> str:
    """The group-by key the expectancy report uses: route, tide and the
    climb verdict — plus a MIN marker, because min-size fires were 94.5%
    of the book and the entire loss. Untagged rows stay visible as their
    own bucket instead of vanishing into a left join.

    ONE IMPLEMENTATION (Nov 6): the key lives in death_lessons.family_key so
    the fire gate's widening and the ledger's stamps can never disagree. The
    inline copy stays as the import-failure fallback, not as a second brain."""
    try:
        import death_lessons as _dl
        return _dl.family_key(tags)
    except (ImportError, AttributeError, TypeError) as exc:
        # 4369: the inline copy below is the documented fallback for a missing
        # or broken death_lessons -- the fallback is the point, the reason is
        # still traced
        logger.debug("  family_key helper unavailable (%s) -- inline fallback", exc)
    if not isinstance(tags, dict) or not tags:
        return "untagged"
    fam = "/".join([str(tags.get("route") or "?"),
                    str(tags.get("tide") or "?"),
                    "climb:" + str(tags.get("climb") or "?")])
    if tags.get("min_size"):
        fam += "/MIN"
    return fam


def _open_exposure_usd(watch: dict) -> float:
    """The whole book's committed dollars — the aggregate the cascade
    review found uncapped (19 rows, ~173% of balance). Rows carry the
    USD cost basis in `size`; tombstones are skipped; any unreadable row
    contributes nothing (fail-open — the per-row gates still stand)."""
    _tot = 0.0
    try:
        for _w in (watch or {}).values():
            if not isinstance(_w, dict):
                continue
            try:
                _tot += max(float(_w.get("size") or 0.0), 0.0)
            except Exception:
                continue
    except Exception:
        return 0.0
    return _tot


def _mandate_probe_ok(spend: float, balance: float, win_min=None,
                      fires_today: int = 0, family=None) -> tuple[bool, str]:
    """Is this fire the armed lesson's ONE probe? (False, why) otherwise.

    Never raises: no mandate, an unreadable one, or a verdict that cannot be
    computed all answer False, so the caller keeps its own refusal.
    """
    try:
        import death_lessons as _dlp
        _doc = _death_mandate()
        if not isinstance(_doc, dict):
            return False, "no armed lesson"
        _okp, _whyp = _dlp.fire_verdict(_doc, spend=spend, balance=balance,
                                        win_min=win_min,
                                        fires_today=fires_today, family=family)
        return (bool(_okp) and "PROBE" in str(_whyp).upper()), str(_whyp)
    except Exception as _pe:
        return False, f"the mandate's verdict could not be computed ({_pe})"


def _mandate_bridge_on() -> bool:
    """THE PROMOTION BRIDGE DIAL: may a fire whose MEASURED worst case fits
    what the death line has left to lose be admitted at its real size, or
    does the armed lesson's size band ($200) keep capping every fire? On by
    default (the band refuses every fire that could pay the contest);
    SCOUT_MANDATE_BRIDGE=0 restores the hard band without touching code.
    Read live on every verdict, never frozen at import."""
    try:
        _v = str(os.environ.get("SCOUT_MANDATE_BRIDGE", "1")).strip().lower()
        return _v not in ("0", "false", "off", "no", "")
    except Exception:
        return True


def _strict_fire_refusal(pair: str, asset: str, spend: float,
                         balance: float, weak_reason: str = "",
                         win_min: float | None = None,
                         family: str | None = None,
                         weak_paid: bool = False,
                         go_big: bool = False) -> bool:
    """THE RISK-ENVELOPE GATE (Nov 6; brakes re-audited Nov 12).
    The remaining hard refusals are the aggregate book cap, the gap-stress
    cap and the death-line mandate.  The daily fire count and the weak-fire
    verdict are advisory now: they are logged, but they no longer suppress
    the fire.  This keeps the measurable risk limits without letting the
    old strict-fire envelope veto entries on churn or a min-size mark.

    `weak_paid` is retained for callers and reporting; it no longer changes
    whether the fire is admitted.  Returns True only when one of the
    remaining hard risk limits refuses.  Never raises: a broken read falls
    through to the ordinary funnel."""
    try:
        if not _strict_fires():
            return False
        _spend = float(spend or 0.0)
        _bal = float(balance or 0.0)
        if _bal <= 0:
            _bal = float(DEFAULT_CONFIG.challenge_balance)
        try:
            _fires_today = int(_recent_fired_popups(window_min=1440.0))
        except Exception:
            _fires_today = 0
        if go_big:
            # GO BIG carries its own hard $30 net stop; the old per-trade
            # envelope is superseded for this qualified entry. The only
            # refusals left are arithmetic: never spend more than the account,
            # and never add a second correlated all-in row to an open book.
            if _spend > _bal + 1e-9:
                logger.info("  GO BIG REFUSED: $%.0f does not fit $%.0f",
                            _spend, _bal)
                return True
            try:
                _gb_book = _open_exposure_usd(_load_contest_watch())
            except Exception:
                _gb_book = 0.0
            if _gb_book > 0.0:
                _log_suppression(pair, "go-big-book",
                                 f"${_gb_book:,.0f} is already open — GO BIG "
                                 "waits for the flat book")
                logger.info("  GO BIG: %s waits — $%.0f already open",
                            asset, _gb_book)
                return True
            logger.info("  GO BIG: %s admitted — $%.0f all-in, hard $30 net "\
                        "stop, momentum exit; the old envelope does not apply",
                        asset, _spend)
            return False
        if _fires_today >= MAX_FIRES_PER_DAY:
            # The daily count is now advisory only.  The count remains for
            # the death-mandate probe, but it no longer suppresses a fire.
            logger.info("  FIRE CAP ADVISORY: %s — %d fires today (cap %d) "
                        "— cap does not refuse",
                        asset, _fires_today, MAX_FIRES_PER_DAY)
        if weak_reason and weak_paid:
            # THE PAID WEAKENED FIRE (Nov 7): the resize above found the
            # size that pays — so the min-size mark is a story about the
            # setup, not a verdict on its worth. Say both out loud and
            # fall through to the book, stress and mandate gates.
            logger.info("  STRICT FIRES: %s — weak (%s) but SIZED TO PAY "
                        "$%.0f: the pace bar is paid and the line can lose "
                        "it — LANDS", asset, weak_reason, _spend)
        if weak_reason and not weak_paid:
            # The weak verdict is now advisory.  The fire keeps the size the
            # sizing path already booked and still faces the book, gap-stress
            # and death-mandate gates below.
            logger.info("  STRICT FIRES ADVISORY: %s — weak (%s) — the fire "
                        "lands at the booked size", asset, weak_reason)
        try:
            _book_usd = _open_exposure_usd(_load_contest_watch())
        except Exception:
            _book_usd = 0.0
        _cap_usd = MAX_AGGREGATE_EXPOSURE_FRAC * _bal
        if _book_usd + _spend > _cap_usd:
            _log_suppression(pair, "aggregate-exposure",
                             f"${_book_usd:,.0f} already open + ${_spend:,.0f} "
                             f"would pass the ${_cap_usd:,.0f} book cap "
                             f"({MAX_AGGREGATE_EXPOSURE_FRAC:.0%} of "
                             f"${_bal:,.0f}) — correlated rows are one trade")
            logger.info("  EXPOSURE CAP: %s refused — book $%.0f + $%.0f > $%.0f",
                        asset, _book_usd, _spend, _cap_usd)
            return True
        # ── THE GAP-STRESS CAP (Nov 7, the Nov 5 flush): the notional cap
        #    above counts DOLLARS of book; a flush does not care about
        #    notional — it asks what each row LOSES when the whole market
        #    moves against it at once. So the book is priced row by row: its
        #    size, its coin's profiled beta (POL rides a flush ~3x as hard
        #    as BTC), and its REAL stop — credited at the 5x slip the Nov 5
        #    cascade measured, which today's 2.5%-of-size stop never beats.
        #    The sum may not exceed half the remaining buffer; the last
        #    contest died to thirteen such rows summing to the whole line.
        try:
            import contest_account as _ca_g
            _gs_buf = _ca_g.stress_allowance_usd(_bal, frac=1.0)
            _gs_allow = _ca_g.stress_allowance_usd(_bal)
            _gs_legs = []
            for _gs_p, _gs_r in (_load_contest_watch() or {}).items():
                if not isinstance(_gs_r, dict):
                    continue          # a tombstone is not exposure
                _gs_sz = float(_gs_r.get("size") or 0.0)
                if _gs_sz <= 0:
                    continue
                _gs_st = _hard_stop_usd(float(_gs_r.get("entry") or 0.0),
                                        _gs_sz, _gs_buf)
                _gs_legs.append((_gs_sz, _gs_st / _gs_sz * 100.0,
                                 _ca_g.stress_beta(_gs_p)))
            _gs_book_loss = _ca_g.stress_loss_usd(_gs_legs)
            _gs_cand_st = (_hard_stop_usd(0.0, _spend, _gs_buf) / _spend * 100.0
                           if _spend > 0 else None)
            _gs_cand_beta = _ca_g.stress_beta(pair)
            _gs_legs.append((_spend, _gs_cand_st, _gs_cand_beta))
            _gs_loss = _ca_g.stress_loss_usd(_gs_legs)
        except Exception as _gs_exc:
            _gs_loss, _gs_allow = None, 0.0
            logger.debug("gap-stress cap unavailable: %s", _gs_exc)
        if _gs_loss is not None and _gs_loss > _gs_allow:
            _log_suppression(pair, "gap-stress",
                             f"the book's worst case ${_gs_loss:,.0f} (this "
                             f"row's beta {_gs_cand_beta:.2f} against a "
                             f"{_ca_g.GAP_STRESS_PCT:g}% flush) passes the "
                             f"${_gs_allow:,.0f} stress allowance — half the "
                             f"buffer left above the death line")
            logger.info("  GAP STRESS: %s refused — book $%.0f + this row "
                        "$%.0f (beta %.2f) = $%.0f > the $%.0f allowance "
                        "(a %.1f%% flush across $%.0f of book)",
                        asset, _gs_book_loss, _gs_loss - _gs_book_loss,
                        _gs_cand_beta, _gs_loss, _gs_allow,
                        _ca_g.GAP_STRESS_PCT, _book_usd + _spend)
            return True
        # ── THE DEATH-LINE MANDATE (Nov 6): after a breach the autopsy's
        #    flip is the admission rule — the condemned shape is refused and
        #    the day's single probe is the only fire that may land.
        #    The bridge (Nov 7): the gap-stress gate above has ALREADY priced
        #    this book, so the fire's own stressed worst case and what is left
        #    of the allowance are known here — the mandate judges the
        #    measurement instead of the size band, and the death line keeps
        #    the final word because the allowance SHRINKS with the buffer.
        _dm = _death_mandate()
        if _dm is not None:
            _dm_stress = _dm_allow = None
            if _mandate_bridge_on():
                try:
                    if _gs_loss is not None:
                        _dm_stress = _gs_loss - _gs_book_loss
                        _dm_allow = _gs_allow - _gs_book_loss
                except Exception:
                    _dm_stress = _dm_allow = None
            try:
                import death_lessons as _dl
                _dm_ok, _dm_why = _dl.fire_verdict(
                    _dm, spend=_spend, balance=_bal, win_min=win_min,
                    fires_today=_fires_today, family=family,
                    stress_usd=_dm_stress, allowance_usd=_dm_allow)
            except Exception as _dm_exc:
                _dm_ok, _dm_why = False, (f"the mandate's verdict could not "
                                          f"be computed ({_dm_exc}) — the "
                                          f"lesson fails CLOSED")
            if not _dm_ok:
                _log_suppression(pair, "death-mandate", _dm_why)
                logger.info("  DEATH MANDATE: %s refused — %s", asset, _dm_why)
                return True
            logger.info("  DEATH MANDATE: %s — %s", asset, _dm_why)
        return False
    except Exception as exc:
        logger.debug("strict-fire gate skipped for %s: %s", asset, exc)
        return False


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
        _units = None
        _ask = 0.0
        _consumed = 0.0
        _carry = None
        _eff = min(spend, _red25 if _committed > 0 else _full25)
        try:
            from execution_tiers import measure_book
            _ob = measure_book(exchange, pair) or {}
            _tob = float(_ob.get("ask_tob") or 0.0)
            if _tob > 0:
                _carry = _tob * 0.20
                _eff = min(_eff, _carry)
            _ask = float(_ob.get("best_ask") or 0.0)
            if _ask > 0 and _eff > 0:
                _budget = _eff
                _carry_units = 0.0
                for _p, _s in (_ob.get("asks") or [])[:25]:
                    if _budget <= 0:
                        break
                    _take = min(_budget, _p * _s)
                    _carry_units += _take / _p
                    _budget -= _take
                _units = min(_eff / _ask, _carry_units)
                _consumed = _eff - max(0.0, _budget)
        except Exception as _exc:
            logger.debug("book read for buying power / recommended "
                         "entry failed (%s) — the popup degrades honestly",
                         _exc)

        # 3b. THE RECOMMENDED ENTRY (user rule, Sep 29: "the popups still
        # need to include the recommended amount to enter with based on
        # the real current market size"): the effective spend translated
        # into UNITS at the live best ask — the real market size — and
        # clipped so the ask book actually carries those units (a 25%
        # slice deeper than the book is fiction; the book's real carry
        # wins). Unknown ask → an honest SKIP line, never a guess.
        _asset_unit = pair.split("/")[0] if "/" in pair else pair
        if _units is not None and _units > 0:
            _enter_line = (f"  ENTER: ~{_units:,.2f} {_asset_unit} at "
                           f"${_ask:,.4f} ≈ ${_consumed:,.0f} "
                           f"(recommended entry — sized to what the "
                           f"book actually carries)\n")
        else:
            _enter_line = ("  ENTER: — (live ask unreadable; size at "
                           "your own risk)\n")

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
            f"{_enter_line}"
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


def _speak_server(text: str, rate: int = 2, push_text: str | None = None,
                  voice: str = ""):
    """Zira out loud from the SCANNER process — runs whether or not any
    browser is open. Same amplified speak script the console uses.
    Every line ALSO goes to the phone as a push notification (ntfy).
    push_text: a richer body for the phone than what she speaks aloud
    (the GET OUT popup's full reasons) — the voice stays short, the
    push carries everything.
    voice (legacy knob, Oct 9 revert: empty for everything now — Mira
    speaks all calls including GET OUT, the "!" carries the urgency).
    Kept so a passed-but-missing name still falls back to the script's
    default female selection instead of breaking."""
    import subprocess
    _base = os.path.dirname(os.path.abspath(__file__))
    _wav_path = os.path.join(_base, "speech_queue", "last_voice.wav")
    _log_her_line(text)
    # THE ATTACH-READY POLL (the wav was always rendered here, but the
    # phone never got it — Oct 9, her ask): the render runs async; the
    # push waits for THIS line's render (OutFile mtime newer than the
    # pre-call stamp) before sending, up to 15s. A missed render ships
    # text-only — the call is never delayed into silence.
    _pre_ts = time.time()
    _spk_proc = None
    _spk_had_pid = False
    try:
        os.makedirs(os.path.dirname(_wav_path), exist_ok=True)
        script = os.path.join(_base, "speak_loud.ps1")
        if not os.path.exists(script):
            return
        _argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", script, "-Text", VOICE_PREFIX + text, "-Rate", str(rate),
                 "-Volume", str(_voice_volume()), "-OutFile", _wav_path]
        if voice:
            _argv += ["-Voice", voice]
        _spk_proc = subprocess.Popen(_argv, creationflags=0x08000000)
        _spk_had_pid = True
    except Exception as exc:
        logger.debug("speak failed: %s", exc)
    _render_fresh = False
    for _ in range(30):  # 30 × 0.5s = 15s cap
        try:
            if os.path.exists(_wav_path) and os.path.getmtime(_wav_path) > _pre_ts:
                _render_fresh = True
                break
        except OSError:
            pass  # why: a stat race on the just-created wav — next 0.5s tick retries
    # THE VOICE LIFELINE (the 17:48 hardening, Oct 9): a spawn that died
    # before its 0.5s first poll used to look like a mere missed render —
    # 20 minutes of log mining to even suspect it. If the file never went
    # fresh AND the child is already gone before tick ONE, the speaker
    # didn't fail quietly — it failed instantly. Log so it shows in ONE
    # grep, then still push (text-only) — the call is never delayed.
    if not _render_fresh and _spk_had_pid:
        _died_early = True
        try:
            if _spk_proc is not None and _spk_proc.poll() is None:
                _died_early = False  # still alive at first poll — not a quick death
        except Exception as _life_exc:
            # never silent (the gate): a probe error must not eat the lifeline
            logger.debug("voice lifeline liveness probe failed: %s", _life_exc)
        if _died_early:
            logger.warning("🔇 VOICE DIED BEFORE SPEAKING: the speak "
                           "spawn exited instantly (pid %s) — no audio on "
                           "the PC; the push goes out text-only.",
                           getattr(_spk_proc, "pid", "?"))
    # phone push: BUY NOW / SELL NOW priority alerts only (user rule,
    # Aug 30) — everything else stays on PC voice + the console panel.
    # push_text (when given) carries the full-reasons body on the phone
    # while the spoken line stays short. The wav attaches when the fresh
    # render landed (the file IS the push body on ntfy's attach route);
    # a stale/absent render ships the text alone.
    _push_to_phone(push_text if push_text else text,
                   wav_path=_wav_path if _render_fresh else None)


def _push_topic() -> str | None:
    """Private ntfy topic (from push_topic.txt next to this file)."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "push_topic.txt")
        return open(p, "r", encoding="utf-8").read().strip() or None
    except Exception:
        return None

_push_no_topic_warned = {"done": False}


def _warn_push_no_topic(where: str = "push") -> None:
    """A missing topic is a BROKEN CHANNEL, not a quiet policy — say so once
    per process at WARNING (greppable: PHONE PUSH DISABLED) instead of
    discovering it at the first paycheck crossing."""
    if _push_no_topic_warned["done"]:
        logger.debug("phone push still disabled (%s) — already warned", where)
        return
    _push_no_topic_warned["done"] = True
    logger.warning("📵 PHONE PUSH DISABLED — push_topic.txt is missing or empty "
                   "next to the scanner (%s). Every BUY / GET OUT / paycheck "
                   "alert will log here and NEVER reach the phone. Fix: write "
                   "the ntfy topic into push_topic.txt.", where)


def _push_channel_boot() -> dict:
    """BOOT-TIME TRUTH about the phone pipe (Nov 11): the scanner can prove it
    SENT (HTTP accept) and nothing more — ntfy has no delivery receipt. So the
    one thing worth printing at every boot is whether the channel is even
    configured, plus the last canary the server accepted."""
    topic = _push_topic()
    st = _canary_load()
    if not topic:
        _warn_push_no_topic("startup")
        return {"ok": False, "topic": None, "last_canary": None}
    last = (f"{st.get('last_sent_day', '?')} {st.get('sent_at', '?')}"
            if st.get("sent_at") else "never")
    logger.info("📲 PUSH CHANNEL: ntfy topic configured (%d chars) | last "
                "server-accepted canary %s | ntfy has NO delivery receipt — "
                "only the phone proves it landed", len(topic), last)
    return {"ok": True, "topic": topic, "last_canary": last}


# phone-push throttle state: identical texts (GET OUT repeats every 60s
# until SOLD) push at most every 5 minutes so one stuck exit can't burn
# the ntfy.sh quota either.
_push_state = {"text": "", "ts": 0.0}

# GET OUT retry queue (Sep 21, the 16:05 lesson: ntfy accepted the push
# with a 200 but the phone app never showed it — ntfy has NO delivery
# receipt, so every GET OUT is "in doubt" and gets exactly one second
# attempt ~60s later. A dropped phone connection is discovered by a
# retry, not by a silence). Processed by _process_push_retries() on the
# main cycle; survives the watch emptying, dies with the process. Since
# Sep 29 it carries BOTH call classes (GET OUT and BUY).
_push_retry_q = []  # [{"text":…, "prio":…, "due": epoch, "why":…}]


def _send_ntfy(text: str, prio: str, attempt: str, tag: str | None = None,
               attach_path: str | None = None,
               title: str | None = None) -> bool:
    """One logged ntfy attempt. True only on an HTTP accept — which is
    delivery to the server, never proof the PHONE received it.
    attach_path: when given and readable, the call rides ntfy's file
    attachment protocol (the body IS the file, sent as octet-stream with
    a Filename header) — the text becomes the notification's title-side
    context and the phone gets the rendered voice as tap-to-play audio
    (the out-of-coverage leg: service returns, the play button is there)."""
    topic = _push_topic()
    if not topic:
        logger.warning("phone push skipped (%s) — no push_topic.txt", attempt)
        return False
    try:
        import urllib.request, urllib.parse, os as _os
        _headers = {"Title": title or "Kraken Scout", "Priority": prio,
                    "Tags": tag or ("rotating_light" if prio == "urgent" else "chart_with_upwards_trend")}
        if attach_path and os.path.exists(attach_path) and os.path.getsize(attach_path) > 0:
            with open(attach_path, "rb") as _af:
                _data = _af.read()
            _headers["Filename"] = "exit_call.wav"
            # Message header must be latin-1-safe per HTTP header rules —
            # the em-dash body fails; the push TEXT rides the title only.
            _safe_ascii = str(text).encode("ascii", "replace").decode("ascii")
            _headers["Message"] = _safe_ascii.replace("\n", " ")[:200]
            req = urllib.request.Request(f"https://ntfy.sh/{urllib.parse.quote(topic)}",
                                         data=_data, headers=_headers,
                                         method="PUT")
        else:
            req = urllib.request.Request(
                f"https://ntfy.sh/{urllib.parse.quote(topic)}",
                data=str(text).encode("utf-8"), headers=_headers)
        with urllib.request.urlopen(req, timeout=20 if attach_path else 10) as _resp:
            _status = int(getattr(_resp, "status", 0) or 0)
            try:
                import json as _json
                _ack = _json.loads(_resp.read().decode("utf-8", "replace") or "{}")
            except Exception as _ack_exc:
                _ack = {}
                logger.debug("ntfy ack unreadable (%s) — status stands", _ack_exc)
        _mid = str((_ack or {}).get("id") or "?")
        if _status and not (200 <= _status < 300):
            logger.warning("phone push REJECTED HTTP %s (%s) — the phone "
                           "missed: %s", _status, attempt, str(text)[:90])
            return False
        logger.info("📲 PUSHED (%s, %s, HTTP %s, id %s, title %r)%s: %s",
                    prio, attempt, _status or "?", _mid,
                    _headers.get("Title", ""),
                    " +voice" if attach_path else "", str(text)[:90])
        return True
    except Exception as exc:
        # push is best-effort — the PC voice already spoke — but it is
        # never SILENT: the scanner log carries every failure
        logger.warning("phone push failed (%s, %s) — the phone missed: %s",
                       exc, attempt, str(text)[:90])
        return False


def _process_push_retries():
    """Called once per main cycle (30s tick): fires due retries — every
    classified call gets ONE second attempt ~60s later (GET OUT since
    Sep 21; BUY since Sep 29 — a missed BUY is the missed entry). Queue
    capped at 8 so a pathological burst can't grow it unbounded."""
    if not _push_retry_q:
        return
    now = time.time()
    for _r in _push_retry_q[:]:
        if now < _r["due"]:
            continue
        _push_retry_q.remove(_r)
        _send_ntfy(_r["text"], _r["prio"],
                   f"attempt 2/2, first sent {now - _r['why']:.0f}s earlier",
                   attach_path=_r.get("attach"))
    if len(_push_retry_q) > 8:
        del _push_retry_q[8:]


# ── THE DAILY CANARY (Sep 21, the 16:05 lesson's last gap) ──────────
# ntfy offers no delivery receipt: the scanner can prove it SENT but
# never that the PHONE received. A dead phone connection used to be
# discovered mid-GET-OUT. The canary flips that: ONE low-stakes push
# every day at/after 09:00 MST proves the pipe end to end while the
# stakes are zero — if the phone shows no canary by mid-morning, the
# connection is dead and FIXING it doesn't wait for an emergency.
# State: push_canary.json (runtime state — gitignored; owned by the
# running scanner like every other live store).
_PUSH_CANARY_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "push_canary.json")
_CANARY_HOUR = 9  # MST
_CANARY_TEXT = ("✅ Daily pipe check — if you can read this, the "
                "GET OUT pipe is alive. No action needed.")


def _canary_load() -> dict:
    import json
    try:
        with open(_PUSH_CANARY_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _canary_save(doc: dict):
    import json
    try:
        with open(_PUSH_CANARY_JSON + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        os.replace(_PUSH_CANARY_JSON + ".tmp", _PUSH_CANARY_JSON)
    except Exception as exc:
        logger.warning("canary state write failed (retry stays armed): %s", exc)


def _process_daily_canary():
    """Called once per main cycle (30s tick), right beside the GET OUT
    retry processor. Once per MST day at/after 09:00: one low-priority
    push through the SAME ntfy pipe the GET OUTs use. Never marked sent
    without an HTTP-accepted send; retries every 5 min until it lands
    (capped at the canary window so a dead pipe can't nag all night)."""
    try:
        if not _push_topic():
            _warn_push_no_topic("daily canary")
            return  # nothing to prove — the startup warning already fired
        _n = datetime.now(MST)
        if _n.hour < _CANARY_HOUR:
            return  # before the window
        _today = _n.strftime("%Y-%m-%d")
        if _n.hour >= 21:  # last fire chance 20:59 — stop nagging after
            return
        st = _canary_load()
        if st.get("last_sent_day") == _today:
            return  # today's canary already landed
        if st.get("day") == _today and \
                (time.time() - float(st.get("last_try_ts", 0.0) or 0.0)) < 300:
            return  # tried <5 min ago — back off, the pipe may be down
        ok = _send_ntfy(_CANARY_TEXT, "low", "canary", tag="white_check_mark")
        doc = {"day": _today, "last_try_ts": time.time()}
        if ok:
            doc["last_sent_day"] = _today
            doc["sent_at"] = _n.strftime("%H:%M")
            logger.info("🐦 Canary sent for %s — if the phone stayed "
                        "silent, the connection is dead and the GET OUT "
                        "pipe with it.", _today)
        else:
            logger.warning("🐦 Canary attempt failed for %s — will retry "
                           "in 5 min until 21:00 MST.", _today)
        _canary_save(doc)
    except Exception as exc:
        logger.warning("canary cycle skipped: %s", exc)


# the phone classifier — word-boundary regexes so EVERY spelling of the
# calls lands in its class (text is lowercased first, so case never
# matters):
#   SELL class (urgent): get out · GET OUT NOW · Get out now · sell now ·
#     SELL 'X' NOW · Sell "x" now · stopped out
#   BUY class  (high):   buy "x" now · buy now · BUY "X" NOW · new pick · go!
# Word boundaries keep "buyer"/"resell" out, while a bare "sell" at the
# very end of a line still matches (the old "sell " substring missed it).
import re as _re_mod  # module-level for the classifier (re is imported
                      # locally elsewhere; this compile runs at import)
_PUSH_SELL_RE = _re_mod.compile(r"\bget out\b|\bsell\b|\bstopped out\b")
_PUSH_BUY_RE = _re_mod.compile(r"\bbuy\b|\bnew pick\b")
# GO BIG NOW (Nov 13): the 5m lane tags a fire that cleared the GO BIG gate.
# Same route, same HIGH priority and same retry queue as a BUY NOW — the
# phrase only has to survive the classifier, which otherwise drops it.
_PUSH_GO_BIG_RE = _re_mod.compile(r"\bgo big now\b")


def _charter_push(event: str) -> bool:
    """THE CHARTER'S NATIVE PUSH (Sep 25): every BUY NOW / GET OUT NOW
    the trading brain fires ALSO rides xmrjackpot.com's own push
    service (no more Ntfy dependency for the new surface). emit()
    tries the pool's authenticated operator route first and otherwise
    spools to monero_project/pool/push_events.jsonl, where the pool
    side's wallet-independent loop (proxy_poller) drains it through
    the rate-limited, audited, templated sender — one brain per layer:
    this hook only NAMES the event, the service owns everything else.
    Fail-soft by contract: a push problem can never touch the trade.
    Called from _push_to_phone AFTER its 5-minute identical-text
    throttle check; returns True when a destination accepted the event
    (the gate un-arms the throttle when NOTHING got the alert)."""
    try:
        import sys as _cs
        import pathlib as _cp
        _scout_dir = str(_cp.Path(__file__).resolve().parent.parent
                         / "monero_project" / "scout")
        if _scout_dir not in _cs.path:
            _cs.path.insert(0, _scout_dir)
        import push_client as _pc
        res = _pc.emit(event)
        return bool(isinstance(res, dict) and res.get("ok"))
    except Exception as exc:
        logger.debug("charter push emit failed (%s): %s", event, exc)
        return False


def _push_to_phone(text: str, wav_path: str | None = None):
    """BUY NOW / GET OUT NOW only on the phone (user rule, Aug 30: 'Just
    have the ntfy.sh do the buy now and sell now priority alerts only';
    tightened Aug 31: ".ntfy doesn't need the 'take it' alerts. Just
    'buy now' and 'Get out now'"). Buy calls push HIGH; GET OUT / SELL /
    STOPPED OUT push URGENT — and every GET OUT-class push arms ONE
    automatic second attempt ~60s later (attempt 2/2), because ntfy's
    200 says nothing about the phone. TAKE IT does NOT push — the
    take-profit nudges repeat every minute during a run (Aug 31: the
    ARB run pushed ~8 of them) and there's nothing to act on that the
    light + her voice aren't already saying. Everything else stays on
    the PC voice and the console's HER WORDS panel. This also keeps us
    far under ntfy.sh's ~60/hour rate limit (Aug 30 incident:
    every-minute position pushes burned the    quota -> 429s -> the phone
    went silent)."""
    # REVIEW TRIAGE (Sep 25): classification+throttle govern both legs; the topic gates only ntfy's.
    _t = str(text).lower().strip()
    if _t.startswith("warning"):
        return  # warnings are voice + console only (user rule, Sep 22:
                # "Get out or hold!" phrasing must not masquerade as a
                # GET OUT call — that is exactly how the 06:41 loss
                # warning pushed URGENT to the phone)
    is_sell = bool(_PUSH_SELL_RE.search(_t))
    is_buy = (not is_sell) and (bool(_PUSH_BUY_RE.search(_t))
                                or bool(_PUSH_GO_BIG_RE.search(_t))
                                or _t.startswith("go!"))
    if not (is_buy or is_sell):
        return  # not a BUY NOW / GET OUT NOW alert — don't push
    # same text (a repeating GET OUT) pushes at most every 5 minutes
    now = time.time()
    if _t == _push_state["text"] and (now - _push_state["ts"]) < 300:
        return
    _push_state["text"] = _t
    _push_state["ts"] = now
    # charter emit (Sep 25): independent of ntfy config; returns whether
    # a destination actually accepted the event
    native_ok = _charter_push("signal_exit" if is_sell else "signal_buy")
    topic = _push_topic()
    if not topic:
        _warn_push_no_topic("_push_to_phone")
        if not native_ok:
            _push_state["text"] = ""   # REVIEW TRIAGE (Sep 25, the hook's own major):
            _push_state["ts"] = 0.0    # reached nowhere — un-arm the throttle, retry allowed
        return  # the ntfy leg needs its topic — the native leg already went
    try:
        prio = "urgent" if is_sell else "high"
        _send_ntfy(str(text), prio, "attempt 1/2", attach_path=wav_path)
        # THE BUY GETS THE SAME DOUBT (Sep 29, her ask): the retry queue
        # used to be GET-OUT-only, but a missed BUY is the missed entry —
        # the one call the desk can least afford to never hear. A missed
        # GET OUT is a position held too long; a missed BUY is the whole
        # trade. From tonight, EVERY classified phone call (BUY and GET
        # OUT alike) arms ONE second attempt ~60s later, whether or not
        # ntfy accepted attempt 1 (a 200 says nothing about the phone;
        # a failed send is the most doubt of all). Not deduped (attempt 2
        # must survive even when the phone DID get attempt 1 — a
        # duplicate alert is the cheap failure); bounded by the 8-deep
        # queue cap.
        _push_retry_q.append({"text": str(text), "prio": prio,
                              "due": now + 60.0,
                              "why": now,
                              "attach": wav_path})
    except Exception as exc:
        # push is best-effort — the PC voice already spoke — but it is
        # never SILENT: the scanner log carries every failure
        logger.warning("phone push failed (%s) — the phone missed: %s",
                       exc, str(text)[:90])


# THE AA-BOX WATERMARK WATCH (Oct 3): grace past the watermark's
# advancement boundary before a silent box heartbeat counts as dead.
# The stamp advances within one ~30s pass of each 4h boundary; the
# grace covers a pass that straddles it. "A few minutes", on the
# record.
_AA_BOX_STALE_GRACE_S = 600


def _aa_box_watermark_watch(now=None, state=None, push=None):
    """Page once when the box's rotation heartbeat dies silently.

    The watermark aa_box_last_rotation is stamped inside the canary
    hook whose except swallows to logger.debug — the scanner can be
    ALIVE (the pulse beats) while its box heartbeat is DEAD: tick and
    expire never run again, the factor drifts unattended, and nothing
    notices. The watermark is the witness: it re-stamps within one
    pass of each 4h boundary (the stamp is key(now-4h), so it advances
    at every boundary), so a watermark still _AA_BOX_STALE_GRACE_S
    stale past its advancement boundary means the heartbeat died.

    Pages HIGH through the OPS leg — _send_ntfy directly plus the retry
    queue, NEVER through the classified trade push (the trade-push rule
    admits only BUY NOW / GET OUT NOW to the phone; this is machinery,
    not a trade, and it must not burn the classification or the rate
    budget).
    ONCE per stale episode: the episode stamp aa_box_stale_paged holds
    the watermark value we paged about and re-arms when the watermark
    finally moves; a further-degraded value is a NEW episode and pages
    again. Fails open on a missing/malformed watermark (the box's own
    guardrails own that shape).

    Seams (the drive pins run this pure): now = a synthetic datetime;
    state = an injected scanner-state dict mutated in place with NO
    disk IO; push = an injected sink. Production defaults read the
    real clock and scanner_state and save any stamp change through the
    re-read-merge writer (never clobber a concurrent watermark write)."""
    from datetime import datetime as _dt, timedelta as _td
    now_dt = now if isinstance(now, _dt) else _dt.now()
    st = _load_scanner_state() if state is None else state
    stored = st.get("aa_box_last_rotation")
    if not isinstance(stored, str):
        return None                      # MISSING: a fresh/wiped state
                                         # before box birth is legitimate
                                         # — absence is not corruption
    stored = stored.strip()
    # PRESENT BUT UNPARSEABLE (Copilot HIGH on 1e0139b): the healthy
    # writer builds keys from real datetimes and can never produce a
    # garbage or calendar-invalid value — corruption is not silence.
    # Page ONCE per value (the same episode stamp; re-arms when the
    # value heals), because a watermark we cannot read is a heartbeat
    # we cannot verify, and this watch exists to end that silence.
    _bad = not _re_mod.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}", stored)
    if not _bad:
        try:
            _y, _mo, _dd, _h1, _h2 = (int(_x) for _x in stored.split("-"))
            _dt(_y, _mo, _dd)          # calendar-invalid: month 99 parses
                                       # no date — same corruption class
        except ValueError:
            _bad = True
    if not _bad and f"{_h1:02d}-{_h2:02d}" not in ROTATION_KEYS:
        _bad = True                      # calendar-valid, rotation-invalid
                                         # (00-99): an hour-99 end would
                                         # shove the deadline days out and
                                         # defer the alert (Copilot medium
                                         # on 716edd9) — the key must be a
                                         # canonical rotation pair
    if _bad:
        if st.get("aa_box_stale_paged") == stored:
            return None                  # this episode already paged
        text = (f"🛠 SCOUT OPS: aa-box watermark UNPARSEABLE "
                f"(aa_box_last_rotation={stored!r}) — the box heartbeat "
                f"cannot be verified until the value is repaired.")
        if push is not None:
            push(text)
        else:
            _send_ntfy(text, "high", "ops 1/2")
            _push_retry_q.append({"text": text, "prio": "high",
                                  "due": time.time() + 60.0,
                                  "why": time.time()})
        st["aa_box_stale_paged"] = stored
        if state is None:
            _fresh = _load_scanner_state()
            _fresh["aa_box_stale_paged"] = stored
            _save_scanner_state(_fresh)
        return text
    # THE STALENESS CLOCK IS THE STAMP'S OWN END, not a probe key
    # (Copilot medium on f3d2fdb, drill 9.13): the watermark names the
    # window that contains now-4h, so it ADVANCES at every boundary —
    # a healthy scanner holds the NEW key within ~30s of the boundary
    # while any probe-key arithmetic lags until boundary+grace; in that
    # window probe != stored and the old rule false-paged every 4h.
    # Honest shape: the stamp freezes when the heartbeat dies, so the
    # deadline is the stored window's end + one full window (the next
    # boundary, where the advance was due) + grace. 24h-safe via
    # timedelta (the 20-24 key's end is hour 24 = next-day 00:00).
    _y, _mo, _dd, _h1, _h2 = (int(_x) for _x in stored.split("-"))
    _end = _dt(_y, _mo, _dd) + _td(hours=_h2)
    _deadline = _end + _td(hours=4) + _td(seconds=_AA_BOX_STALE_GRACE_S)
    _probe = now_dt - _td(hours=4)
    expected = f"{_probe:%Y-%m-%d}-{rotation_key_for_hour(_probe.hour)}"
    if now_dt < _deadline:
        if "aa_box_stale_paged" in st:
            st.pop("aa_box_stale_paged", None)   # re-arm: the watermark moved
            if state is None:
                _fresh = _load_scanner_state()
                _fresh.pop("aa_box_stale_paged", None)
                _save_scanner_state(_fresh)
        return None
    if st.get("aa_box_stale_paged") == stored:
        return None                      # this episode already paged
    text = (f"🛠 SCOUT OPS: aa-box watermark stale — "
            f"aa_box_last_rotation={stored}, expected {expected} "
            f"{_AA_BOX_STALE_GRACE_S}s past its boundary. The box "
            f"heartbeat died silently: tick/expire are not running and "
            f"the factor drifts unattended.")
    if push is not None:
        push(text)
    else:
        _send_ntfy(text, "high", "ops 1/2")
        _push_retry_q.append({"text": text, "prio": "high",
                              "due": time.time() + 60.0, "why": time.time()})
    st["aa_box_stale_paged"] = stored
    if state is None:
        _fresh = _load_scanner_state()
        _fresh["aa_box_stale_paged"] = stored
        _save_scanner_state(_fresh)
    return text


def _load_contest_watch() -> dict:
    import json
    try:
        with open(CONTEST_WATCH_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return {}
        # THE TOMBSTONE-FREE READ (the round-2 cross-writer sweep): a
        # None value is the merge-save's deletion marker, never a live
        # watch — every reader through this loader gets a clean book
        # instead of iterating a tombstone (w.get on None would kill
        # the exit cycle).
        return {k: v for k, v in doc.items() if v is not None}
    except Exception:
        return {}


def _save_contest_watch(watch: dict, fresh_keys=None):
    """MERGE-SAVE (the cross-writer sweep, Sep 28): the scanner and the
    bot both read-modify-write this file — the bot's _upsert_bot_watch_row
    lands a row between the scanner's read and its save, and the old
    whole-file rewrite erased that row (the paper book never noticed the
    real fill; the real side's tombstone vanished). Overlaying `watch`
    onto the file's CURRENT contents keeps both sides' keys.
    DELETIONS: a row the scanner REMOVED (the GET-OUT pop) must be
    marked with watch[pair] = None by the remover — under merge-save an
    absent key is indistinguishable from 'never seen', and pop-then-save
    would resurrect the row. None-means-delete; every other key absent
    from `watch` is preserved. fresh_keys (the round-5 review): keys
    the caller DELIBERATELY (re)adds this tick — a fresh value lands
    even if an on-disk tombstone for the same pair exists, because the
    re-add is the caller's own intent, not a stale carry. Still tmp +
    os.replace: a torn write can never be read."""
    import json
    fresh = {str(k) for k in (fresh_keys or [])}
    drops = {k for k, v in (watch or {}).items() if v is None}
    try:
        # THE RAW TOMBSTONE READ (the round-3 review's resurrection
        # window): the filtered loader HIDES another writer's deletion
        # marker, so a stale writer still carrying the row in its own
        # view would overlay it right back over the None on disk. The
        # marker wins against stale carried rows — UNLESS the caller
        # names the key in fresh_keys: a deliberate re-add (the popup's
        # auto-track after a SOLD, the op scripts after a wipe) must
        # land on the first attempt, not silently vanish under a marker
        # it never saw (the round-5 review's INJ finding). Intent comes
        # from the CALLER — the file cannot infer it.
        try:
            with open(CONTEST_WATCH_JSON, "r", encoding="utf-8") as fh:
                _raw = json.load(fh)
            _raw = _raw if isinstance(_raw, dict) else {}
            raw_tombs = {k for k, v in _raw.items() if v is None}
            cur = {k: v for k, v in _raw.items() if v is not None}
        except Exception:
            raw_tombs = set()
            cur = _load_contest_watch()
        merged = {k: v for k, v in cur.items()
                  if k not in drops and k not in watch}
        merged.update({k: v for k, v in (watch or {}).items()
                       if v is not None
                       and (k not in raw_tombs or k in fresh)})
                                            # the DELETION wins for stale
                                            # carriers: a None re-added by
                                            # update would resurrect the
                                            # row it exists to remove
    except Exception:
        merged = {k: v for k, v in (watch or {}).items() if v is not None}
    # THE MARKER PERSISTS (the round-7 question, answered): a None in
    # the caller's dict is WRITTEN BACK to the file as {"pair": null}
    # — under the old shape the merged dict FILTERED the None out, so
    # the marker lived exactly one save: the next writer (console tick,
    # bot fill, op script) saw no marker, treated the closed pair as
    # re-addable, and one stale overlay resurrected the GET-OUT-closed
    # row. Markers also RIDE FORWARD: every marker on disk that this
    # save does not clear (a fresh re-add lands the live value in its
    # place) carries into the merged doc, so the file always holds the
    # machine's last word and raw_tombs guards real deletions, not a
    # one-save echo of them. The loaders' tombstone-free reads are
    # untouched — no reader ever iterates a marker; only the raw reads
    # do. app.py's merge-save already writes markers; contest_account's
    # wipe ships {k: None} and now lands as true persistence.
    for _k in raw_tombs:
        if _k not in merged and _k not in drops:
            merged[_k] = None
    for _k in drops:
        merged[_k] = None
    try:
        # THE PER-WRITER TMP + THE RETRYING REPLACE (Oct 2, the
        # de-flake): the storm runs proved this save loses the rename
        # race to a concurrent reader/writer — WinError 5 on the FIXED
        # shared .tmp — and a single raw os.replace turned each loss
        # into a silently-dropped row ("contest watch save failed" in
        # the storm's stderr). The tmp is pid-suffixed (two writers
        # never collide on one temp — the bot engine's own convention)
        # and the rename rides atomic_io.replace_atomic's bounded
        # backoff (WinError 5/32/Errno13 heal in-process; anything else
        # raises immediately). The report contract is unchanged: True
        # landed, False lost-for-real — the caller's truth-print stands.
        from atomic_io import replace_atomic
        tmp = CONTEST_WATCH_JSON + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
        replace_atomic(tmp, CONTEST_WATCH_JSON)
        return True    # THE REPORT (the re-add triage, Sep 29): the
                       # caller can tell 'landed' from 'lost the
                       # race' — a truth-print needs a truth to print
    except Exception as exc:
        logger.warning("contest watch save failed: %s", exc)
        return False


def _popup_already_watched(pair: str) -> bool:
    """THE ONE-WATCH RULE (user rule, Sep 21; SHARPENED Oct 9: "currently
    watching, not recently watched"): a BUY NOW popup never fires for an
    asset CURRENTLY being watched — the tombstone-free loader is the
    contract, so a SOLD or GET-OUT-cleared row is GONE for this gate the
    moment the tombstone lands (not "recently watched": that former
    state leaked — a manual sell with no tombstone sync left the pair
    gated for the rest of the rotation). The merged WATCHING rows live
    in HER WORDS; the wipe row is a distinct event from the watcher.
    Checked on all three contest BUY routes (champion, Sure Thing
    breakout, mover) — the bot's own DCA 'BUY MORE' is a different book
    and stays ungated."""
    try:
        if pair in _load_contest_watch():
            _log_suppression(pair, "already-watched",
                             "a BUY popup never fires on an asset already "
                             "being watched — SOLD or the GET OUT auto-close "
                             "clears it for the next setup")
            logger.info("  ONE-WATCH: %s already watched — no BUY popup, "
                        "no auto-track (it's tracked)",
                        pair.split("/")[0])
            return True
    except Exception as exc:
        # fail-open: a dead watch read must never silence the fire alarm
        logger.debug("already-watched check failed for %s (%s) — popup "
                     "fires anyway", pair, exc)
    return False


def auto_track_popup(pair: str, entry: float, size: float, goal_pct: float,
                     window_min: float, why: str, source: str,
                     hm_mode: str | None = None,
                     setup: dict | None = None,
                     plan_basis: str = "",
                     pace_pct_per_hour=None, exchange=None,
                     go_big: bool = False,
                     hard_stop_usd: float | None = None,
                     goal_usd: float | None = None,
                     bounce_exit_ts: float | None = None) -> bool:
    """THE POPUP IS THE CONTRACT (Sep 21): every popup that fires —
    champion, Sure Thing, mover, DCA — auto-tracks at fire time. One
    writer for every route: never displaces an entry the user already
    has, never resurrects a row the machine itself closed (the GET OUT
    auto-close removes it — a later fire re-adds fresh).

    THE DEATH FLOOR sits here too (Nov 12): this is the single writer for
    every popup route, so a Sure Thing or mover cannot add a row while the
    marked book is at/below $9,850 even if its own gates passed."""
    try:
        _at_watch = _load_contest_watch() or {}
        _at_marks = {}
        if exchange is not None:
            for _at_pair in list(_at_watch):
                try:
                    _at_marks[_at_pair] = float(
                        (exchange.fetch_ticker(_at_pair) or {}).get("last") or 0.0)
                except Exception:
                    pass
        _at_refusal = death_floor_refusal(
            pair.split("/")[0], watch=_at_watch, marks=_at_marks)
        if _at_refusal:
            # ADVISORY ONLY: never silently drop a row the operator can judge.
            logger.warning("%s (advisory — the row still tracks)", _at_refusal)
    except Exception as _at_exc:
        logger.warning("auto-track death-floor read failed for %s (%s) — "
                       "refusing the row (fail closed)", pair, _at_exc)
        return False
    import json
    try:
        # THE WINDOW STAMP (Sep 22): the row carries the contest window it
        # opened under, so this file's own GET OUT exit (apply_pnl judges
        # w.get("contest_id")) books into the RIGHT contest even if the
        # window rolls mid-trade. Stamp failure degrades to "" — the row
        # lands legacy and applies as before, never blocks the track.
        import contest_account as _ca_at
        _ca_stamp = _ca_at.current_contest_id()
    except Exception as _ca_at_exc:
        logger.debug("auto_track_popup: window stamp unavailable for %s "
                     "(%s) — row lands legacy", pair, _ca_at_exc)
        _ca_stamp = ""
    try:
        w = _load_contest_watch()
        if pair in w:
            return False
        # THE FRESHNESS STAMP (the TIA bounce audit, Oct 10): the row
        # carries the exit watch's read AT FIRE TIME, so the force gate
        # can tell standing background hum (the same ExitMod that armed
        # BEFORE the buy and would re-kill every re-entry instantly -
        # TIA ping-ponged 13 in/out cycles on one unchanged 0.3505) from
        # NEW deterioration after the entry. A broken read stamps None:
        # the grace then judges on held-time only.
        try:
            import tri_watch as _tw_at
            _entry_em = _tw_at.exit_state(pair, None).get("exit_mod")
        except Exception as _tw_at_exc:
            logger.debug("auto_track_popup: exit stamp unavailable for %s "
                         "(%s) - grace rides held-time only", pair,
                         _tw_at_exc)
            _entry_em = None
        # THE GOAL STAMP (Nov 6): a fire that names no goal (or names 0)
        # is stamped with the goal the exit rules actually manage it to, so
        # the row never lands unmeasurable. A named goal is untouched.
        _goal_e = _operative_goal_pct(goal_pct, pair)
        # ── THE DOLLAR GOAL STAMP (Nov 10, user rule: "the higher trade
        #    amounts ... the goal will trigger an exit check when the dollar
        #    goal is reached — the dollar goal is set by the entry logic").
        #    Stated in dollars so a big row banks the same money on a smaller
        #    move, but floored at its own 1R (a take cheaper than the risk
        #    does not pay however often it fires) and capped by the plan's
        #    own % goal (never demand a move the hold window cannot drain).
        _goal_usd_e = 0.0
        try:
            import contest_account as _ca_gs
            if goal_usd is not None:
                # the validated 5m edge states its own dollars (0 = %-goal
                # only) instead of inheriting the generic GO BIG take.
                _goal_usd_e = float(goal_usd or 0.0)
            elif go_big:
                # GO BIG books the scaled take: 5% of the contest start
                # (500 at the $10k tier) — never a fixed $20.
                _goal_usd_e = float(_ca_gs.go_big_marks(
                    (_ca_gs.load_state() or {}).get("start_balance") or 0.0)[1])
            else:
                _risk_e = _hard_stop_usd(float(entry or 0.0),
                                         float(size or 0.0), _ca_gs.buffer())
                _goal_usd_e = float(_ca_gs.row_goal_usd(
                    float(size or 0.0), goal_pct=_goal_e,
                    risk_usd=_risk_e).get("goal_usd") or 0.0)
        except Exception as _gs_exc:
            logger.debug("dollar goal stamp unavailable for %s (%s) — the "
                         "%% goal stands", pair, _gs_exc)
            _goal_usd_e = 0.0
        w[pair] = {"entry": float(entry or 0.0),
                   "high": float(entry or 0.0),
                   "added": _now_mst().isoformat(),
                   "opened_ts": _now_mst().isoformat(),
                   "contest_id": _ca_stamp,
                   "size": float(size or 0.0),
                   "goal_pct": _goal_e,
                   # the dollars this row takes (0 = unstamped, % goal only)
                   "goal_usd": _goal_usd_e,
                   # GO BIG rides the run: no fixed take; $30 net stop.
                   # The 5m edge names its own stop/deadline instead.
                   **({"go_big": True} if go_big else {}),
                   **(({"hard_stop_usd": float(hard_stop_usd)})
                      if hard_stop_usd is not None else
                      ({"hard_stop_usd": 30.0} if go_big else {})),
                   **(({"bounce_exit_ts": float(bounce_exit_ts)})
                      if bounce_exit_ts is not None else {}),
                   "window_min": float(window_min or 0.0),
                   # THE PLAN'S BASIS (Nov 7): the window alone cannot say
                   # whether 78 minutes came from the coin's own tape or from
                   # the 1%/h assumption. Recorded, so the fire carries the
                   # plan it was admitted on instead of leaving it in a log.
                   "plan_basis": str(plan_basis or ""),
                   "pace_pct_per_hour": (None if pace_pct_per_hour is None
                                         else float(pace_pct_per_hour)),
                   "why": [why],
                   "source": source,
                   "entry_exit_mod": _entry_em,
                   # THE SETUP STAMP (Nov 6): the fire's own family, carried
                   # to the close so expectancy is a group-by. A route that
                   # passes nothing lands "untagged", never unmeasurable.
                   "setup": dict(setup) if isinstance(setup, dict) else {},
                   "setup_v": (SETUP_TAGS_VERSION
                               if isinstance(setup, dict) and setup else 0)}
        if hm_mode:
            # THE OPPORTUNISTIC STAMP (user ask, Oct 1): the exit watch
            # reads this key to judge the 2% hard stop — without it the
            # row would ride the classic half-buffer stop.
            w[pair]["hm_mode"] = str(hm_mode)
        # FRESH-KEY INTENT (the round-5 INJ finding): the auto-track IS
        # the deliberate re-add — name the pair so a tombstone left by a
        # SOLD (or a new-contest wipe racing this fire) cannot silently
        # swallow the very entry the popup just promised the user.
        _save_contest_watch(w, fresh_keys=[pair])
        logger.info("AUTO-TRACK: %s watched at fire time (entry $%.4f, "
                    "goal %.1f%%, window %.0fm, %s, plan %s)",
                    pair.split("/")[0], float(entry or 0.0),
                    _goal_e, float(window_min or 0.0), source,
                    (str(plan_basis) if plan_basis else "unrecorded"))
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


def _save_scanner_state(state: dict, path: str | None = None) -> None:
    """MERGE-SAVE (the round-3 review): this file carries independent
    keys written at different times — the morning flatten date, the
    legacy armed-coil memory, the exit-extension canary watermark.
    The old whole-file replace let the 20:00 flatten's one-key write
    ERASE the canary watermark (the next boundary tick re-evaluated the
    rotation) and the armed-coil memory (a restart re-announced it).
    Overlaying `state` onto the file's CURRENT contents keeps every
    writer's keys; tmp + os.replace keeps it torn-write-proof."""
    import json
    p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "scanner_state.json")
    try:
        merged = dict(state or {})
        try:
            with open(p, "r", encoding="utf-8") as fh:
                cur = json.load(fh)
            if isinstance(cur, dict):
                cur.update(state or {})
                merged = cur
        except Exception as _mrg_exc:
            logger.debug("scanner-state merge read skipped (%s) — the "
                         "writer's own keys stand alone", _mrg_exc)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=1)
        os.replace(tmp, p)
    except Exception as exc:
        logger.warning("scanner state save failed: %s", exc)


def _voice_enabled() -> bool:
    """Her voice is ALWAYS on (Sep 21, user rule: the switches were
    critical to the system and are retired from the console — she can
    no longer be silenced by a toggle, and the snap-to-OFF bug class
    dies with them). Kept as a permanent True so the three call sites
    keep their meaning and any future accidental writer of
    console_state.voice_on=false stays harmless. Loudness is the
    console's VOLUME dial (voice_volume 0-100) — see _voice_volume."""
    return True


def _voice_volume() -> int:
    """Her loudness 0-100 (console_state.json voice_volume, default 100)
    — the console's VOLUME dial, applied when the WAV is rendered."""
    import json
    try:
        with open(CONSOLE_STATE_JSON, "r", encoding="utf-8") as fh:
            return max(0, min(100, int(json.load(fh).get("voice_volume", 100))))
    except Exception:
        return 100


PULSE_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "scanner_pulse.json")


# ── THE OUTLIVER HINDSIGHT FAMILY (rebuilt Sep 23 — the production
# block was lost in the stash-sweep era and test_outliverhindsight held
# the full spec): the rotation-outliver gate (get out at a deep dip
# instead of riding to the window's boundary) journals each fire's
# DECISION-TIME evidence once per boundary, and diag_outliverhindsight
# re-prices monthly whether the early exit beat holding.
_OUTLIVER_FIRES_FILE = Path(__file__).parent / "outliver_fires.jsonl"
_OUTLIVER_JOURNAL_BOUNDARY: set = set()   # one journal per boundary per pair


def _outliver_bucket(dip_pct: float) -> str:
    """The evidence book's dip bucket (the diag's own edges)."""
    d = float(dip_pct)
    for lo, hi in ((0.5, 1.0), (1.0, 2.0), (2.0, 4.5), (4.5, 100.0)):
        if lo <= d < hi:
            return f"{lo}-{hi}"
    return "0-0.5"


def coin_wiggle_profile(pair: str) -> dict | None:
    """The decision-time evidence book for this coin: how deep dips of
    each size have historically bounced (n, rec4, bmed) in the current
    rotation, from the deep 1h stores. None when the book cannot read
    the coin (thin stores) — the journal stamps nulls and the hindsight
    still prices the move (the grade needs the bar, not the book)."""
    try:
        import diag_outliverhindsight as _doh_self
        _sdir = _doh_self.STORES
        _base = str(pair).split("/")[0]
        _sp = _sdir / f"{_base}_USD-1h.feather"
        _highs, _closes, _ep = _doh_self._load_store(_sp)
    except Exception:
        return None
    try:
        _close = [_closes[i] for i in range(1, len(_closes))
                  if _closes[i - 1] > 0]
        _drops = [(_close[i - 1] - _close[i]) / _close[i - 1] * 100.0
                  for i in range(1, len(_close))
                  if (_close[i - 1] - _close[i]) / _close[i - 1] >= 0.005]
        _rec = [(max(_close[i + 1:i + 25], default=0.0) / _close[i] - 1.0)
                for i in range(len(_close) - 1) if i < len(_drops)]
        _n = len(_drops)
        if _n < 3:
            return None
        _rec4 = sum(1 for r in _rec if r >= 0.04) / max(1, len(_rec))
        _bmed = sorted(_rec)[len(_rec) // 2] * 100.0 if _rec else 0.0
        return {"env": 0.0, "n": _n,
                "buckets": {(4.5, 100.0): {"n": _n, "rec": 0.0,
                                           "rec4": round(_rec4, 4),
                                           "bmed": round(_bmed, 2)}}}
    except Exception:
        return None


def _journal_outliver_fire(pair: str, entry: float, live: float,
                           high: float, size: float, usd: float,
                           dip_pct: float, why: str, *,
                           remaining_min: float | None = None,
                           age_min: float | None = None):
    """Stamp one outliver fire's decision-time evidence to
    outliver_fires.jsonl. The 60s repeat calls (the exit voice's
    re-arm) dedupe per boundary — one decision, not five hearings. A
    journal failure never breaks the caller: the exit still fires."""
    try:
        import json as _json
        _key = (str(pair), _outliver_bucket(dip_pct))
        if _key in _OUTLIVER_JOURNAL_BOUNDARY:
            return
        _OUTLIVER_JOURNAL_BOUNDARY.add(_key)
        _prof = coin_wiggle_profile(pair)
        _bkey = _outliver_bucket(dip_pct)

        def _norm_bkey(_k) -> str:
            """Any bucket-key spelling resolves to 'lo-hi': tuples, and
            the string reprs callers hand over ('(4.5, 100.0)')."""
            if isinstance(_k, (tuple, list)) and len(_k) == 2:
                return f"{_k[0]}-{_k[1]}"
            _nums, _cur = [], ""
            for _ch in str(_k):
                if _ch.isdigit() or _ch == ".":
                    _cur += _ch
                elif _cur:
                    _nums.append(_cur)
                    _cur = ""
            if _cur:
                _nums.append(_cur)
            return f"{_nums[0]}-{_nums[1]}" if len(_nums) >= 2 else str(_k)

        _row = None
        if _prof and isinstance(_prof.get("buckets"), dict):
            for _k, _v in _prof["buckets"].items():
                if _norm_bkey(_k) == _bkey:
                    _row = _v
        _rec = {
            "ts": _now_mst().isoformat(timespec="seconds"),
            "pair": str(pair),
            "live": round(float(live), 8),
            "entry": round(float(entry), 8),
            "high": round(float(high), 8),
            "size": round(float(size), 2),
            "usd": round(float(usd), 2),
            "dip_pct": round(float(dip_pct), 2),
            "bucket": _bkey,
            "n": (_row or {}).get("n"),
            "rec4": (_row or {}).get("rec4"),
            "bmed_h": (_row or {}).get("bmed"),
            "remaining_min": (int(remaining_min) if remaining_min
                              else int(_rotation_window_min())),
            "age_min": (round(float(age_min), 1) if age_min is not None
                        else None),
            "why": str(why),
        }
        with open(_OUTLIVER_FIRES_FILE, "a", encoding="utf-8") as _f:
            _f.write(_json.dumps(_rec) + "\n")
        logger.info("OUTLIVER JOURNAL: %s dipped %.2f%% below entry — "
                    "the early Get Out fired (evidence: n=%s rec4=%s "
                    "bmed=%s, %d min left in the window)",
                    pair, float(dip_pct), _rec["n"], _rec["rec4"],
                    _rec["bmed_h"], _rec["remaining_min"])
    except Exception as exc:
        logger.debug("outliver journal skipped (%s) — the exit call "
                     "still fired", exc)


def _pulse_beat(phase: str = "done"):
    """THE SCANNER PULSE (Sep 22): one file write after every COMPLETED
    contest_light cycle — counter + completion timestamp — read by the
    console's auto-watch plate. Stamp-in-the-loop, not in contest_light:
    the loop's try/except swallows a raise, so only a real completion
    advances the counter — a crashed cycle (or a dead thread) freezes it
    and the console shows the frozen age. Empty watch beats too: a
    crashed cycle is visible even with no pairs watched.

    SCAN-AWARE (Sep 22, the boot-night incident): the full tournament
    runs INSIDE the same loop pass as the light cycle and can hold it
    ~9 minutes — the beat frozen the whole time and the console cried
    FROZEN for a machine that was working. phase="busy" refreshes
    last_cycle_ts and records phase=scan WITHOUT advancing the counter
    (cycles still count completed light cycles); the tournament's
    finally clears back to idle. The console    grants a 9-minute grace while phase=scan — a scan-aware alive, not a
    lie: a scanner dead mid-scan still goes FROZEN when the grace expires.

    THE SELF-SIZING SCAN GRACE (Sep 24): the clock lives HERE, riding the
    beats — the first busy beat of a hold stamps scan_started_ts, the
    finally's clear beat (which fires on EVERY path, success or crash)
    closes the tournament and records its real duration. The last 10
    durations ride the pulse file and the console's grace self-sizes to
    max(720, 1.25x the worst real scan) — no constant can go stale when
    the market's scan grows, and a crashed tournament's duration only
    lands if it is sane (30s..1800s), so a stranded clock can never
    inflate the grace."""
    try:
        import json as _json
        _p = {}
        try:
            with open(PULSE_JSON, "r", encoding="utf-8") as _fh:
                _p = _json.load(_fh)
        except Exception as exc:
            logger.debug("pulse read (first beat or unreadable — start at zero): %s", exc)
        _now = time.time()
        if phase == "busy":
            if _p.get("phase") != "scan":
                _p["scan_started_ts"] = _now   # THIS hold starts the clock
            _p["phase"] = "scan"
        elif phase == "light":
            # A COMPLETED LIGHT PASS (Nov 7, its own clock): count it and
            # time the gap, but NEVER touch the scan phase — the tournament's
            # busy beat and its own finally own that clock, and a light beat
            # from the watch thread (running exactly while a scan holds the
            # loop) must not clear a scan that is still going.
            if _p.get("pid") not in (None, os.getpid()):
                # a NEW process: last run's gap record is not this run's
                for _k in ("last_gap_s", "max_gap_s", "max_gap_ts",
                           "last_pass_ts"):
                    _p.pop(_k, None)
            _p["cycles"] = int(_p.get("cycles", 0)) + 1
            _prev_pass = _p.get("last_pass_ts")
            if isinstance(_prev_pass, (int, float)) \
                    and 0 < (_now - float(_prev_pass)) < 3600:
                _gap = round(_now - float(_prev_pass), 1)
                _p["last_gap_s"] = _gap
                if _gap > float(_p.get("max_gap_s") or 0.0):
                    _p["max_gap_s"] = _gap
                    _p["max_gap_ts"] = _now
            _p["last_pass_ts"] = _now
        else:
            if _p.get("phase") == "scan" and isinstance(
                    _p.get("scan_started_ts"), (int, float)):
                _dur = _now - float(_p["scan_started_ts"])
                if 30.0 <= _dur <= 1800.0:     # sane tournaments only
                    _durs = [float(d) for d in (_p.get("scan_durs") or [])
                             if isinstance(d, (int, float))][-9:]
                    _durs.append(_dur)
                    _p["scan_durs"] = _durs    # the last 10 scans' memory
                    _g = max(720.0, max(_durs) * 1.25)
                    _p["scan_grace_s"] = int(min(1800.0, _g) + 29) // 30 * 30
                    _p["scan_grace_set"] = _now
            _p.pop("scan_started_ts", None)   # the clock closes on every path
            _p["cycles"] = int(_p.get("cycles", 0)) + 1
            _p["phase"] = "idle"
        _p["last_cycle_ts"] = _now
        _p["pid"] = os.getpid()
        _tmp = PULSE_JSON + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _fh:
            _json.dump(_p, _fh)
        os.replace(_tmp, PULSE_JSON)
    except Exception as exc:
        logger.debug("pulse beat skipped: %s", exc)


def _pulse_scanning():
    """True when the pulse file says phase=scan (the tournament is
    holding the loop) — fail-soft False: an unreadable file claims
    nothing about a scan."""
    try:
        import json as _json
        with open(PULSE_JSON, "r", encoding="utf-8") as _fh:
            return (_json.load(_fh).get("phase") == "scan")
    except Exception:
        return False


_SLOW_CYCLE_S = 180.0   # a light cycle is a ~30s game; 3 minutes is a stall


def _cycle_slow_alarm(ep, completed_ts, scanning):
    """THE SLOW-CYCLE WATCH (Sep 23): the light cycle is a ~30s game —
    a pass still running after 3 minutes is either the full tournament
    holding the loop (a SLOW SCAN: alive, busy beats fresh — the console
    reads alive-scan) or a CRASHED CYCLE (its except swallowed the raise:
    no busy beats, a dead timestamp). Announce ONCE per episode, log-only
    — the console's FROZEN line is the standing display and warnings
    never go to the phone (user rule). The light block's finally resets
    the tracker on completion so the NEXT slow episode announces again."""
    try:
        dur = time.time() - completed_ts
        if dur < _SLOW_CYCLE_S:
            return
        if not ep.get("start"):
            ep["start"] = completed_ts
            ep["announced"] = False
        ep["scan"] = bool(scanning)
        if not ep.get("announced"):
            ep["announced"] = True
            _m = int(dur // 60)
            _s = int(dur % 60)
            if scanning:
                logger.warning("⏱ SLOW CYCLE (scan): the full tournament is holding the "
                               "light cycle (%dm%02ds so far) — busy beats fresh, the "
                               "machine is working; the console reads alive-scan",
                               _m, _s)
            else:
                logger.error("🚨 SLOW CYCLE (no scan): the light cycle has not completed "
                             "in %dm%02ds and NO busy beats are running — a crashed "
                             "cycle (its except swallowed the raise) or a hung fetch; "
                             "check the pulse file and the log tail below",
                             _m, _s)
    except Exception as exc:
        logger.debug("slow-cycle alarm check skipped: %s", exc)


# ── THE EXIT WATCH'S OWN CLOCK (Nov 7, the starvation finding) ────────────
# The light pass used to run inline in the loop, sequenced behind the mover
# scan and the tournament. The Nov 7 soak caught what that costs: 23 minutes
# (12:40 → 13:03) and 3 minutes (13:59 → 14:02) with no light pass at all —
# the hard stop, the force gate and the plan-spent gate were all blind for
# that long, because every one of them lives in that pass. A fire that
# crosses its plan window inside a scan is not a fire the market paused for.
#
# The repair is the pass's own cadence, not a shorter scan: single-flight +
# a start stamp allow ONE pass per tick whichever caller arrives first, and
# a watchdog thread takes the tick whenever the loop is late. Nothing about
# the gates changes — the watch runs the same contest_light on the same
# rows, in the same order, so force still outranks plan-spent and both
# still need the trend's own read.
LIGHT_TICK_S = 25.0          # one light pass per tick (the loop's old cadence)
LIGHT_STARVE_SLACK_S = 3.0   # a loop this far past its tick loses the tick to the watch
LIGHT_WATCH_POLL_S = 1.0     # how often the watch checks the clock
_LIGHT_LOCK = threading.Lock()
_LIGHT_CLOCK = {"at": 0.0, "passes": 0, "starved": 0, "last_done": 0.0,
                "starve_logged": False}


def _light_pass_due(now_ts=None) -> float:
    """Seconds since the last light pass STARTED (inf when none yet).

    An empty clock means the pass is due NOW — the runner treats inf as
    "first pass of this process" and the watch treats it as due, so a slow
    boot cannot sit through a whole tick with the gates unread.
    """
    at = float(_LIGHT_CLOCK.get("at") or 0.0)
    if at <= 0.0:
        return float("inf")
    return (now_ts if now_ts is not None else time.time()) - at


def _run_light_pass(exchange, cfg, source="loop", episode=None) -> bool:
    """ONE exit-watch pass on its own clock. True when this call ran it.

    SINGLE-FLIGHT: the lock is taken without blocking, so a second caller
    (the loop arriving mid-watch-pass, or the watch arriving while the loop
    is mid-pass) is refused rather than queued — two passes at once could
    close the same fire twice, and the gates are written for one pass at a
    time. The clock is stamped BEFORE the work: a slow pass delays the next
    tick, it can never start two ticks back to back.

    The gates inside contest_light are untouched: same rows, same order,
    same arithmetic. This function only decides WHEN the pass may run.
    """
    if not _LIGHT_LOCK.acquire(blocking=False):
        return False
    try:
        now_ts = time.time()
        late = _light_pass_due(now_ts)
        first = not (_LIGHT_CLOCK.get("at") or 0.0)   # boot: nothing has run yet
        if not first and late < LIGHT_TICK_S:
            return False                  # this tick already had its pass
        _LIGHT_CLOCK["at"] = now_ts       # stamp first: a slow pass cannot double-start
        _LIGHT_CLOCK["passes"] = int(_LIGHT_CLOCK.get("passes") or 0) + 1
        if source == "watch" and not first:
            _LIGHT_CLOCK["starved"] = int(_LIGHT_CLOCK.get("starved") or 0) + 1
            # speak the takeover ONCE per starvation episode: this is the
            # evidence line that a scan held the loop, not a per-tick hum
            if late > (2.0 * LIGHT_TICK_S) and not _LIGHT_CLOCK.get("starve_logged"):
                _LIGHT_CLOCK["starve_logged"] = True
                logger.warning(
                    "\u23f1 EXIT WATCH TOOK ITS OWN TICK: the loop was %.0fs past "
                    "the last light pass (mover scan or tournament holding it) — "
                    "the exit gates run NOW, not when the scan ends", late)
        elif source == "loop":
            _LIGHT_CLOCK["starve_logged"] = False
        try:
            contest_light(exchange, cfg)
            _pulse_beat("light")          # completed-cycle beat; never clears the scan phase
            _LIGHT_CLOCK["last_done"] = time.time()
        except Exception as exc:
            logger.debug("contest light error: %s", exc)
        finally:
            if episode is not None:
                episode.update(start=0.0, announced=False, scan=False)
        try:
            _aa_box_watermark_watch()
        except Exception as _wb_exc:
            logger.debug("aa box watermark watch skipped: %s", _wb_exc)
        return True
    finally:
        _LIGHT_LOCK.release()


def _light_watchdog(exchange, cfg, stop: threading.Event, episode=None):
    """The tick the loop owes, taken by the watch, never skipped.

    It only steps in when the loop is more than LIGHT_STARVE_SLACK_S past
    the tick, so an on-time loop keeps running the pass itself exactly as
    before; when the mover scan or the tournament holds the loop, this is
    what keeps the exit gates inside the tick.
    """
    while not stop.is_set():
        try:
            stop.wait(min(LIGHT_WATCH_POLL_S,
                          max(LIGHT_TICK_S / 5.0, 0.02)))
            if _light_pass_due() >= (LIGHT_TICK_S + LIGHT_STARVE_SLACK_S):
                _run_light_pass(exchange, cfg, source="watch", episode=episode)
                # The 5m entry path also survives a scan holding the loop:
                # exits ran first; this only scans a new closed candle.
                try:
                    import bounce_live as _blw
                    _blw.tick(exchange, auto_track_popup, logger,
                              push=_push_to_phone)
                except Exception as _blw_exc:
                    logger.debug("5m bounce edge watchdog tick skipped: %s",
                                 _blw_exc)
        except Exception as exc:
            logger.debug("light watchdog skipped a beat: %s", exc)


def _day_review_spoken(now) -> bool:
    """True once discipline_state.json carries today's DAY review row —
    the cooldown review runs ONCE per MST day and stays restart-proof:
    a scanner boot at 20:30 re-enters the rollover branch on its first
    cycle, and the state row (not the process memory) is the guard."""
    try:
        import discipline as _disc_g
        _hist = _disc_g.load_state().get("history") or []
        return any(h.get("rot") == "DAY"
                   and h.get("date") == now.strftime("%Y-%m-%d")
                   for h in _hist)
    except Exception:
        return False


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


# ── THE GIVE-BACK LEDGER (user, Sep 29: "Gave back $100 but doesn't
# count it? … nothing adjusted … 'All Good' without checking what we
# gave back first"): realized-only accounting never sees a round-trip
# erosion — the day can give back $95 of unrealized peak and every
# gauge still says "plus 4 dollars today", because the banked rows
# never held the peak and the drawdown machinery only reacts to losses
# vs the START balance. One watermark per day: the peak (realized +
# unrealized) the account ever held today, the give-back since, a
# voiced lead that checks it FIRST, and a once-per-day guard tighten
# through auto_adjust's own rails (the ledger counts it — the
# rotation_already_applied dedupe makes once-per-day structural).
# ── THE SEPTEMBER TUNING, TIER-SCALED (Sep 29): the tighten trigger
# and speak floor are contest-account math now — the calibration
# (tighten 0.20% of start, speak 0.10% of start; the study at
# scratch/giveback_tier_study.py: the fixed $50 caught only the blowoff
# and missed BOTH real erosion days, Sep 22 -26.0 and Sep 29 -23.9,
# both closed negative after being positive; the crossing-day median
# was 11.40 so the fixed $25 speak floor spoke almost nothing) and the
# tier-scaled resolver live in contest_account.giveback_thresholds() —
# ONE function for the tracker, the voice lead, and the console's
# gauge. GIVEBACK_TIGHTEN_FRAC stays the guard-delta knob (how hard,
# not when).
GIVEBACK_TIGHTEN_FRAC = 0.10     # tighten the guard factor 10% (unchanged)
GIVEBACK_MAX_TIERS = 2           # the ladder CEILS at the ask's one
# re-arm: tier 1 arms at the trigger, tier 2 at 2x, and past that the
# knob is spent — the spent-day trail floor (_armed_cap_base) is what
# keeps tightening for the rest of the day.


def _giveback_ceiling(tide_state) -> int:
    """THE REGIME-SCALED CEILING (user ask, Oct 6; the tier-ceiling
    study's answer): the fixed 2 covered every RISING/FLAT day in the
    book but bared the OUT days' later legs (Sep 26 $150 = 7.5 legs,
    Oct 5 $99 = 5.0 legs — both OUT; no RISING day ever crossed the
    second leg). OUT earns ONE more tier — the erosion keeps running
    while the fleet is underwater, and this code path already consults
    the tide. RISING and FLAT keep the fixed GIVEBACK_MAX_TIERS; an
    unreadable tide fails SOFT to the base — a broken read never
    widens a tightening knob."""
    try:
        if str(tide_state or "").upper() == "OUT":
            return GIVEBACK_MAX_TIERS + 1
    except Exception as _gb_exc:
        logger.debug("giveback ceiling read failed (%s) — the fixed "
                     "base stands", _gb_exc)
    return GIVEBACK_MAX_TIERS


def _today_realized_pnl() -> float:
    """Today's banked journal total — the sum _all_good_lead already
    speaks. Fail-soft to 0.0. The journal resolves LIVE from
    CONTEST_TRADES_FILE (the STATE_ENV pattern): a drive rebinds it to
    a tempdir sink instead of backing up the live bytes — a killed
    battery can no longer strand a fixture where the machine banks.
    The default is the module dir, exactly as before."""
    try:
        import json as _j_rp
        _day = _now_mst().strftime("%Y-%m-%d")
        _tot = 0.0
        for _l in (Path(CONTEST_TRADES_FILE)
                .read_text(encoding="utf-8").splitlines()):
            if not _l.strip():
                continue
            _r = _j_rp.loads(_l)
            if str(_r.get("sold_at", "")).startswith(_day) \
                    and _r.get("applied"):
                _tot += float(_r.get("pnl_net") or 0.0)
        return _tot
    except Exception:
        return 0.0


def _track_day_giveback(unrealized: float) -> dict:
    """One tick of the give-back watermark. `unrealized` is the open
    watch's live P&L in dollars (the caller sums (live−entry)×units
    across the rows it just priced). Updates the day's peak, computes
    the give-back, and — when the erosion crosses the tier-armed
    trigger (tier 1 at the trigger, the re-arm at 2x, ceiling
    GIVEBACK_MAX_TIERS) — tightens the guard factor through
    auto_adjust.apply_adjustment (rails, ledger, telemetry, dedupe).
    Every read/write fail-soft; the exit cycle must never stall here."""
    try:
        import profit_guard as _pg_gb
        import auto_adjust as _aa_gb
        import contest_account as _ca_gb
        _day = _now_mst().strftime("%Y-%m-%d")
        _total = _today_realized_pnl() + float(unrealized or 0.0)
        _now = _now_mst().isoformat()
        gb = (_pg_gb._state() or {}).get("giveback") or {}
        if gb.get("day") != _day:
            gb = {"day": _day, "peak_total": _total, "peak_ts": _now,
                  "tightened": False}
        else:
            # a day that opens AT zero is a valid baseline — only an
            # absent key counts as 'no peak yet' (the round-5 review,
            # now in the UPDATE too: a stored 0.0 is a peak, not a
            # missing one — the round-6 line-precise minor)
            if "peak_total" not in gb:
                gb["peak_total"] = _total
                gb["peak_ts"] = _now
        _peak = gb.get("peak_total")
        _peak = _total if _peak is None else float(_peak)
        if _total > _peak:
            gb["peak_total"] = _total
            gb["peak_ts"] = _now
            _peak = _total
        gb["now_total"] = round(_total, 2)
        gb["give_back"] = round(_peak - _total, 2)
        _gb_tighten_usd, _ = _ca_gb.giveback_thresholds()
        # THE CEILING READS THE TIDE (the regime-scaled ceiling, Oct 6):
        # one breadth read (cached, the popup floor's own source) picks
        # the day's ceiling; a broken read degrades to the fixed base.
        try:
            _gb_tide = _fleet_tide()[1]
        except Exception:
            _gb_tide = None
        _gb_ceiling = _giveback_ceiling(_gb_tide)
        # THE TIER LADDER (Oct 5, user ask: the 13:42 line showed the
        # day's second erosion leg with no knob once the first tighten
        # was spent): tier 1 arms at the trigger, the RE-ARM arms at
        # 2x, and the ladder CEILS at GIVEBACK_MAX_TIERS — one tier
        # per tick; a crashed attempt leaves the count unchanged and
        # retries the SAME tier next tick. A legacy watermark
        # (tightened=True, no count) reads as tier 1 spent, so the
        # re-arm arms at 2x the very day this ships.
        _t_gb = gb.get("tightens")
        _tightens = (int(_t_gb) if isinstance(_t_gb, (int, float))
                     else (1 if gb.get("tightened") else 0))
        _tier = _tightens + 1
        _ref = gb.get("refused") or {}

        _skip = (_ref.get("tier") == _tier

                 and _ref.get("reason") == "daily_cap_exceeded")

        if (gb["give_back"] >= _gb_tighten_usd * _tier
                and _tightens < _gb_ceiling and not _skip):
            _before = _aa_gb.read_guard_factor()
            _rec = _aa_gb.apply_adjustment(
                -(_before * GIVEBACK_TIGHTEN_FRAC),
                rotation_id=(f"giveback-{_day}" if _tier == 1
                             else f"giveback-{_day}-tier{_tier}"),
                evidence_checksum=("giveback-day-peak" if _tier == 1
                                   else f"giveback-day-peak-tier{_tier}"),
                auto=True)
            # ONE ATTEMPT PER TIER for outcomes that MOVE the guard:

            # applied and queued (a real review request) spend the

            # tier; a daily_cap_exceeded refusal does NOT - the cap

            # cannot free until midnight, so spending the tier let

            # every later tier burn on the same exhausted cap while

            # the guard never tightened despite real erosion (the

            # Oct 6 collision). The refused tier retries next day (the

            # watermark resets with the cap); the refusal marker keeps

            # the ledger unflooded in between. A crashed attempt leaves

            # the count unchanged and retries the same tier next tick.

            _cap_refused = _rec.get("reason") == "daily_cap_exceeded"

            gb["tighten"] = {"before": round(_before, 4),

                             "tier": _tier,

                             "status": _rec.get("status"),

                             "reason": _rec.get("reason")}

            if _cap_refused:

                # THE CAP DOES NOT BURN A TIER (the Oct 6 collision):

                # the attempt is recorded (the chip renders it) and the

                # marker dedupes the retries, but the tier stays for a

                # day the cap can actually serve.

                gb["refused"] = {"tier": _tier,

                                 "reason": _rec.get("reason"),

                                 "ts": _now}

            else:

                gb["tightens"] = _tier

                gb["tightened"] = True   # back-compat: any tier spent


            try:
                from telemetry_adjust import emit_adjustment as _ea_gb
                _ea_gb(f"giveback-{_day}" if _tier == 1
                       else f"giveback-{_day}-tier{_tier}",
                       _rec, 0, 0, [], cohort="giveback",
                       applied_by="giveback-watch")
            except Exception as _tel_exc:
                logger.debug("give-back telemetry skipped (%s) - the "
                             "ledger row is the record", _tel_exc)
        _pg_gb._save({"giveback": gb})
        return gb
    except Exception as exc:
        logger.debug("give-back tracking skipped: %s", exc)
        return {}


def _all_good_lead() -> str:
    """The pace-and-clock lead for the all-good line (Sep 21, user ask):
    \"All good — plus 13 dollars today, 8 minutes left in this rotation.\"
    Reads the SAME live data as the console's ON PACE / CLOCK gauges:
    today's applied realized PnL from contest_trades.jsonl and the
    minutes left in the current rotation. Returns just \"All good\" when
    either read comes up empty — a missing gauge must never stall the
    voice, and never lie."""
    lead = "All good"
    # THE GIVE-BACK CHECK COMES FIRST (user, Sep 29: "'All Good' without
    # checking what we gave back first"): the day's erosion from its own
    # peak — realized + unrealized — LEADS the line when it's worth
    # hearing, and "All good" never prefixes it. A $95 round-trip used
    # to be invisible: realized-only accounting said "plus 4 dollars
    # today" while the account sat $95 under its morning high.
    try:
        import profit_guard as _pg_ag
        import contest_account as _ca_ag
        _gb = (_pg_ag._state() or {}).get("giveback") or {}
        _give = float(_gb.get("give_back") or 0.0)
        _peak = float(_gb.get("peak_total") or 0.0)
        _nowt = float(_gb.get("now_total") or 0.0)
        if _give >= _ca_ag.giveback_thresholds()[1] and _peak > 0:
            lead = (f"Gave back {_give:.0f} dollars from the day's peak — "
                    f"we were up {_peak:.0f}, now "
                    f"{'plus' if _nowt >= 0 else 'minus'} {abs(_nowt):.0f}")
    except Exception as exc:
        logger.debug("give-back lead skipped: %s", exc)
    try:
        _today = _today_realized_pnl()
        if _today:
            lead += f" — {'plus' if _today >= 0 else 'minus'} " \
                    f"{abs(_today):.0f} dollars today"
    except Exception as exc:
        logger.debug("all-good pace clause skipped: %s", exc)
    try:
        from volatility_engine import ROTATIONS as _R_AG
        _now = _now_mst()
        _win = next((r for r in _R_AG if r[0] <= _now.hour < r[1]), None)
        if _win:
            _end = _now.replace(hour=_win[1] % 24, minute=0,
                                second=0, microsecond=0)
            if _win[1] == 24:
                _end += timedelta(days=1)
            _left = (_end - _now).total_seconds() / 60.0
            lead += f", {max(_left, 0):.0f} minutes left in this rotation"
    except Exception as exc:
        logger.debug("all-good clock clause skipped: %s", exc)
    # THE HEARABLE NET (user, Sep 22): when a watched pair's goal trail is
    # armed, the all-good line says the enforced stop — the same number the
    # exit watch enforces (ratchet % capped at half the goal, stop = peak ×
    # (1 − trail%)) — so the net is hearable, not just readable on the
    # WATCHING card. Multiple watched pairs: the tightest lock in dollars,
    # then the nearest in percent; pair names in the tail. Fail-soft: a
    # broken watch read or bad row leaves the lead untouched, and a lead
    # with no pace/clock clause still grows the tail ("All good — PUMP trail…").
    try:
        _watch_ag = _load_contest_watch() or {}
        _best = None   # (usd_lock, pct, name) — max by usd, tiebreak max pct
        for _p_ag, _w_ag in _watch_ag.items():
            if not _w_ag.get("trail_armed"):
                continue
            _e_ag, _h_ag, _s_ag = (float(_w_ag.get("entry") or 0.0),
                                   float(_w_ag.get("high") or 0.0),
                                   float(_w_ag.get("size") or 0.0))
            if _e_ag <= 0 or _h_ag <= 0:
                continue
            _g_ag = float(_w_ag.get("goal_pct") or 0.0)
            _t_ag = min(_ratchet_trail_pct(_e_ag, _h_ag, _s_ag),
                        _armed_cap_base(_g_ag))
            _px_ag = _h_ag * (1 - _t_ag / 100.0)
            _u_ag = (_px_ag - _e_ag) * _s_ag
            _k_ag = (_u_ag, _t_ag, str(_p_ag).split("/")[0])
            if _best is None or _k_ag[:2] > _best[:2]:
                _best = _k_ag
        if _best:
            _names = ", ".join(
                str(_p_ag).split("/")[0]
                for _p_ag, _w_ag in sorted(_watch_ag.items())
                if _w_ag.get("trail_armed"))
            _lock_txt = (f"{_best[0]:.0f} dollars" if _best[0] >= 1.0
                         else "less than a dollar")
            lead += (f" — {_names} trail armed, stop {_best[1]:.1f}% under "
                     f"the peak, locking {_lock_txt}")
    except Exception as exc:
        logger.debug("all-good net clause skipped: %s", exc)
    return lead


def _watch_lost_minutes(w: dict, now: float | None = None) -> float | None:
    """How long this watch row has gone UNSEEN, or None when it is watched.

    THE HOLE THIS CLOSES (Nov 6, the nine-day ride): the exit loop skips a
    row whose ticker read fails (`continue` before any gate) and nothing in
    the loop ever consulted age — so a dead feed, a delisted pair or an
    engine that was not running left a row riding with no clock on it. On
    Oct 22 the scanner died with nine rows open; nothing noticed for nine
    days, and the Oct 31 restart sold all nine in one minute with every
    receipt reading "PROFIT GUARD FIRED" — true about the drop, silent
    about the gap.

    `checked_ts` is stamped only when a price read SUCCEEDS, so the gap
    since that stamp IS the unseen time. Rows never priced once fall back
    to `added`. None (never close on an unknown age) when no stamp parses,
    and a future stamp (clock skew) is not "lost" either.
    """
    _now = time.time() if now is None else float(now)
    ts = w.get("checked_ts")
    if isinstance(ts, (int, float)) and float(ts) > 0:
        age = (_now - float(ts)) / 60.0
    else:
        try:
            age = (_now - datetime.fromisoformat(
                str(w.get("added"))).timestamp()) / 60.0
        except Exception:
            return None
    if age is None or age < 0 or age < LOST_ROW_MIN:
        return None
    return round(age, 1)


def _stale_unrealized_leg(w: dict) -> float | None:
    """THE STALE LEG (the Oct 6 triage - CodeRabbit 4342/4391's uncured
    twin): a failed ticker used to `continue` BEFORE the unrealized sum,
    so a dropped WINNER read as a drawdown the give-back guard then
    tightened on (the ride-prices cure closed the suppressed-book half;
    this closes the fetch-fail half). The leg prices from the row's LAST
    STORED price (written on every successful fetch below) and is handed
    to the give-back sum ONLY - the gates never run on it (the priced
    path below stays the only gate authority). None = no stored truth
    (a first-tick row never priced): the row stays unpriced, honestly."""
    try:
        entry = float(w.get("entry") or 0.0)
        size = float(w.get("size") or 0.0)
        px = float(w.get("last_px") or 0.0)
        if entry <= 0 or size <= 0 or px <= 0:
            return None
        return (px / entry - 1.0) * size
    except Exception:
        return None


def contest_light(exchange, cfg):
    """One light cycle on every contest position: 4% profit guard + 50% sell
    bar + scalp watch. Speaks the dollars every minute. A GET OUT call
    PERSISTS — the position stays tracked and the call repeats every ~60s
    until the user taps SOLD (Aug 30: auto-dropping after one alert was
    losing exits — 'I miss my exit')."""
    import pandas as pd
    from strategy import evaluate_strategy

    global _last_trade_done
    # ── THE AUTO-ADJUST CANARY HOOK (Sep 28; relocated Sep 29, the
    # isolated review's major): it sits ABOVE the empty-book early
    # return — a silent rotation with no open rows is exactly when the
    # propose path has the most to say, and the hook reads the journal
    # + guard state, never the live watch.
    try:
        _wk = (_rotation_window() or (None, None, None))[2]
        if _wk:
            # THE PREVIOUS, JUST-CLOSED ROTATION (the isolated review's
            # second major): a bare window key matches its 4h block on
            # EVERY day — the journal loader then merges cross-day rows
            # into the aggregate. The discipline block's own precedent
            # computes the closing block as (current_block_start - 4h)
            # % 24 and stamps it with TODAY's Denver date: one
            # evaluation per real rotation, never a cross-day merge.
            _now_m = _now_mst()
            # THE PREV ROTATION'S OWN START DATETIME (Sep 29 review
            # minor): subtract 4h from now — the date comes from the
            # rotation's actual start, so a boundary just after
            # midnight stamps YESTERDAY's date on the 20-24 key
            # instead of merging yesterday's rows into today's stamp.
            _prev_dt = _now_m - timedelta(hours=4)
            _aar_key = (f"{_prev_dt:%Y-%m-%d}-"
                        f"{rotation_key_for_hour(_prev_dt.hour)}")
            # THE BOX'S ROTATION HEARTBEAT (Oct 3, the wiring decision):
            # tick once per CLOSED rotation, canary-independent — the
            # box's lifecycle must not ride the cohort's arm state.
            # expire_if_due is drift-aware: it restores the anchor only
            # while the factor still sits where the chain left it; the
            # nightly recompute's factor retires the box without the
            # restore (auto_adjust owns the semantics, the drives own
            # the proof). Watermark lands last — the evaluation's own
            # retry-on-crash precedent.
            try:
                import auto_adjust as _aa_hb
                _hb_st = _load_scanner_state()
                if _hb_st.get("aa_box_last_rotation") != _aar_key:
                    _aa_hb.tick_rotation()
                    _aa_hb.expire_if_due()
                    _hb_st = _load_scanner_state()   # re-read: never clobber
                    _hb_st["aa_box_last_rotation"] = _aar_key
                    _save_scanner_state(_hb_st)
            except Exception as _hb_exc:
                logger.debug("auto-adjust box heartbeat skipped: %s",
                             _hb_exc)
            _aar = _exx.aa_apply_silent_rotation(_aar_key)
            if _aar.get("evaluated"):
                logger.info(
                    "AUTO-ADJUST CANARY: %s evaluated — %s%s",
                    _aar_key, _aar.get("status"),
                    f" ({_aar.get('reason')})" if _aar.get("reason") else "")
    except Exception as _aa_exc:
        logger.warning("auto-adjust canary hook failed (%s) — the "
                       "rotation boundary proceeds without it", _aa_exc)
    watch = _load_contest_watch()
    if not watch:
        return
    voice = _voice_enabled()
    # ── THE REAL-TIME DEATH-FLOOR EXIT MONITOR (Nov 12, user rule: "it should
    #    not block any signals from getting in but it should watch closely the
    #    exit in real-time and trigger the get out warning when staying in is
    #    too risky").  The floor no longer vetoes entries; here it prices the
    #    live book every cycle and the normal GET OUT path does the rest.
    #    Marks start from each row's last checked price and are refreshed below
    #    as this cycle fetches each ticker, so the monitor adds no extra reads.
    _floor_acct = {}
    _floor_watch = {}
    _floor_marks = {}
    try:
        import contest_account as _ca_floor
        _floor_acct = _ca_floor.load_state()
        for _fp, _fr in list(watch.items()):
            if not isinstance(_fr, dict):
                continue
            _floor_watch[_fp] = _fr
            try:
                _fm = float(_fr.get("last_px") or 0.0)
            except (TypeError, ValueError):
                _fm = 0.0
            if _fm > 0:
                _floor_marks[_fp] = _fm
    except Exception as _floor_boot_exc:
        logger.debug("death-floor exit monitor unavailable: %s", _floor_boot_exc)

    # ── THE DIAL-OFF SWEEP (6.1): when the operator has the extension
    # OFF, no row may carry a stale stamp — revert is mechanical. A
    # dirty set re-saves once; the next cycle sees the clean book.
    try:
        _dropped = _exx.expire_sweep(watch)
        if _dropped:
            _save_contest_watch(watch)
            logger.info("EXIT EXTENSION: dial off — dropped %d stamp(s), "
                        "the book reverts to the incumbent ladder", _dropped)
    except Exception as _es_exc:
        logger.warning("exit-extension sweep failed (%s) — proceeding on "
                       "the current book", _es_exc)

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
    # ── THE META LAYERS' TICKERS (user spec, Sep 22): the shadow trails
    # follow their exits and the end-game triggers watch the rotation
    # roll — both cadence-gated inside, fail-soft like everything here.
    try:
        import meta_layers as _mlt
        _mlt.shadow_trail_tick(list(watch.keys()), exchange=exchange)
        _mlt.endgame_trigger_check()
    except Exception as exc:
        logger.debug("shadow/endgame tick skipped: %s", exc)
    _unrealized_sum = 0.0   # THE GIVE-BACK LEDGER's live leg: summed
                            # across this tick's rows, fed to the
                            # day-peak watermark after the loop
    for pair, w in list(watch.items()):
        if w is None:
            # a tombstone rides in `watch` only when the remover saved
            # its raw dict (this loop bypasses the loader's filter):
            # the GET-OUT close tombstoned it this tick — nothing left
            # to watch, and w.get below sits OUTSIDE the try
            continue
        asset = pair.split("/")[0]
        # ── THE LOST-ROW CLOCK (Nov 6): read BEFORE the fetch, so no row
        # can be hidden by the very failure that loses it. None = watched.
        _lost_m = _watch_lost_minutes(w)
        try:
            live = float(exchange.fetch_ticker(pair)["last"])
            w["last_px"] = live
        except Exception:
            if _lost_m is None:
                _leg = _stale_unrealized_leg(w)
                if _leg is not None:
                    # THE STALE LEG (the Oct 6 triage): the failed fetch must
                    # not vanish the row's unrealized leg - a dropped WINNER
                    # is a drawdown the guard tightens on. Price it from the
                    # row's last truth, hand it to the give-back sum, and
                    # skip the gates (the priced path stays the only gate
                    # authority).
                    _unrealized_sum += _leg
                continue
            # lost AND unpriceable — the row cannot be watched any more, so
            # it is closed at its last known mark, loudly, never silently
            _mark = float(w.get("last_px") or w.get("entry") or 0.0)
            if not _mark:
                logger.warning(
                    "WATCH LOST: %s unseen %.0f min and no mark to close it "
                    "at — the row stays, and it stays noisy", pair, _lost_m)
                continue
            live = _mark
        # the live mark feeds the floor monitor's equity projection
        if _floor_watch and live > 0:
            _floor_marks[pair] = float(live)
        entry = float(w.get("entry") or 0)
        size = float(w.get("size") or 2500.0)
        if not entry:
            if _lost_m is not None:
                logger.warning("WATCH LOST: %s unseen %.0f min with no entry "
                               "stamp — nothing to price a close from",
                               pair, _lost_m)
            continue

        # ── LIVENESS STAMP (Sep 22, user: "a silent watcher is noticeable
        # instead of invisible"): the console reads this per-pair stamp
        # and shows the check's age — a watcher gone silent (dead ticker,
        # crashed cycle) shows as silent instead of looking healthy.
        # Written even when every gate says HOLD, so absence of an exit
        # can never be confused with absence of a check.
        w["checked_ts"] = time.time()
        dirty = True

        # track high in the watch file (survives restarts)
        old_high = float(w.get("high") or 0)
        high = max(old_high, live)
        if high > old_high:
            w["high"] = high
            dirty = True

        # ── 1. profit guard: trail off the peak, RATCHETED by run size, ──
        # then SCALED BY THE ADAPTIVE GUARD FACTOR (user spec, Sep 22:
        # "GuardFactor = EnvScore * DiscMod * (1 + MLT_Corr) * VolMod *
        # AssetMemory * FundingMod * DecayMod" — strong context widens
        # the trail, weak context tightens it; unreadable context keeps
        # the committed fixed rules)
        should_exit = False
        reason = ""
        _edge_row = str(w.get("source") or "") == "bounce_live"
        _floor = {}
        try:
            if _floor_watch and _floor_marks:
                _floor = death_floor_gate(_floor_acct, _floor_watch, _floor_marks)
        except Exception as _floor_exc:
            logger.debug("death-floor exit read failed for %s: %s", pair, _floor_exc)
        _pgres = None
        _pgf = None
        try:
            import profit_guard as _pg
            _pgres = _pg.guard_factor(pair, w)
            if _pgres.get("readable"):
                _pgf = float(_pgres["factor"])
        except Exception:
            _pgres = None
        # ── THE META LAYERS (user spec, Sep 22): RCL runs BEFORE the
        # factor is finalized, SAL's stored adjustment multiplies after,
        # End-Game Mode's trail/lock-in override rides the verdict.
        _meta = None
        _eg_m = None
        try:
            import meta_layers as _ml
            _pgf, _meta = _ml.finalize(pair, _pgf)
            if _meta.get("endgame"):
                _eg_m = _ml.endgame_scales()
        except Exception:
            _meta = None
        # ── THE GUARD BREAKDOWN STAMP (Sep 23, user: "show each watched
        # pair's live GuardFactor breakdown on the console — env,
        # discipline, MLT, vol, memory — so I can see why the net is
        # wide or tight"): the FINALIZED factor (after RCL/SAL/momentum)
        # plus every named part ride the watch row each check — the
        # page renders the why, it never computes it (the one-brain
        # rule). Written on HOLD too: the breakdown is a heartbeat,
        # not an event.
        # ── THE GUARD-FACTOR RING (Sep 24, user: "a 24-hour guard-factor
        # sparkline per pair on the WATCHING plate — see how the net has
        # moved across rotations"): the finalized factor is sampled onto
        # the same heartbeat, bounded to 24h. The sample cadence is
        # throttled to one per GUARD_RING_MIN_DT so the row stays small
        # no matter how fast the checks run; a sample older than the
        # window is dropped, never drawn. The ring lives on the row
        # (restart-proof like the rest of the watch file); the page
        # draws it and never rebuilds it (one-brain).
        GUARD_RING_S = 24 * 3600       # the window the user asked for
        GUARD_RING_MAX = 72            # 72 samples = the 24h at 20-min cadence
        GUARD_RING_MIN_DT = 20 * 60    # one sample per rotation-window
        if _pgres is not None:
            # the ring's history lives on the OLD stamp — capture it
            # before the dict below is replaced (the new stamp is built
            # fresh every check; only its ring inherits)
            _prev_brk = w.get("guard_brk")
            _prev_ring = (_prev_brk.get("ring") or []) \
                if isinstance(_prev_brk, dict) else []
            w["guard_brk"] = {
                "ts": time.time(),
                "gf": _pgf,
                "readable": bool(_pgres.get("readable")),
                "carried_from": _pgres.get("carried_from"),
                "parts": {k: _pgres.get(k) for k in
                          ("env", "disc_mod", "mlt_corr", "vol_mod",
                           "asset_mem", "funding_mod", "decay_mod")},
                "rcl": (_meta or {}).get("rcl"),
                "rcl_mult": (_meta or {}).get("mult"),
                "sal_mult": (_meta or {}).get("sal_mult"),
                "momentum": bool((_meta or {}).get("momentum_continuation")),
                "endgame": bool((_meta or {}).get("endgame")),
                "gf_min": (_meta or {}).get("gf_min"),
                "gf_max": (_meta or {}).get("gf_max"),
                "max_carve": (_meta or {}).get("max_carve"),
                "channel_verdict": _pg.channel_verdict(
                    res=dict(_pgres, factor=_pgf, gf_max=(_meta or {}).get("gf_max"),
                    gf_min=(_meta or {}).get("gf_min"))),
            }
            # the ring: carry the previous stamp's samples forward, drop
            # anything past the window, append THIS check's finalized
            # factor only when the throttle allows (the live value is
            # always the head line; the ring is the trend). An unreadable
            # guard records NO sample — unreadable is not 1.0.
            try:
                _now_s = time.time()
                _ring = [s for s in _prev_ring
                         if isinstance(s, (list, tuple)) and len(s) == 2
                         and isinstance(s[0], (int, float))
                         and isinstance(s[1], (int, float))
                         and 0 < _now_s - float(s[0]) <= GUARD_RING_S]
                if isinstance(_pgf, (int, float)) and _pgf > 0 and (
                        not _ring or _now_s - float(_ring[-1][0])
                        >= GUARD_RING_MIN_DT):
                    _ring.append((_now_s, round(float(_pgf), 3)))
                w["guard_brk"]["ring"] = _ring[-GUARD_RING_MAX:]
            except Exception as _ring_exc:
                logger.debug("guard ring failed for %s: %s", pair, _ring_exc)
            # ── THE TRI-WATCH EXIT STAMP (user spec, Sep 23): the
            # ExitWatch read rides the same breakdown heartbeat — the
            # console renders the watch's why, it never computes it
            # (one-brain). The force GATE reads this same stamp below,
            # after every calibrated gate has had its say.
            try:
                import tri_watch as _tw
                _tw_es = _tw.exit_state(pair, _pgf)
                _tw_es.pop("trend", None)          # the stamp stays flat
                w["exit_watch"] = _tw_es
                dirty = True
            except Exception as _tw_exc:
                logger.debug("tri-watch stamp failed for %s: %s",
                             pair, _tw_exc)
            dirty = True
        _trail = _ratchet_trail_pct(entry, high, size)
        if _pgf is not None:
            _trail = round(_trail * _pgf, 3)
        if _eg_m is not None:
            _trail = round(_trail * _eg_m[0], 3)
        # ── 1c. GOAL-ARMED TRAIL (user rule, Sep 21: "after the goal is
        # hit, trail a stop just under the recent high — keep riding the
        # climb, lock the profit when it turns; never wait for a full
        # trend break"). HALF-GOAL ARM (Sep 22: trigger the profit guard
        # half the way to the goal): the trail latches at
        # GOAL_TRAIL_ARM_FRAC (0.5) of the goal and, once armed, caps at
        # the same half-goal width (never looser than 1.0%) under the
        # tracked peak. The arm is LATCHED in the watch row, so a dip back
        # can't disarm it — the net, once set, stays set. Above the stop
        # the position rides every new high; the turn fires the same
        # GET OUT (popup → push → auto-close) as every other gate.
        _goal_tr = float(w.get("goal_pct") or 0) or _window_goal_pct(pair)
        _arm_at = _goal_tr * GOAL_TRAIL_ARM_FRAC
        if not w.get("trail_armed") and _goal_tr > 0 \
                and (live / entry - 1.0) * 100.0 >= _arm_at:
            w["trail_armed"] = True
            dirty = True
        if w.get("trail_armed") and _goal_tr > 0:
            # armed cap = goal * ARM_FRAC * (BASE_LOCK_IN / lockin_pct):
            # since lockin_pct = BASE_LOCK_IN / GF, the cap is the
            # half-goal width scaled by GF — strength waits for the full
            # goal, weakness locks the crumbs early (spec step 9).
            # (Sep 22, the CodeRabbit major): the WIDTH cap alone cannot
            # lock profit — at a 2% goal the arm gain is 1%, but the
            # ratchet band at that peak is 2%, so the stop lands BELOW
            # entry and net of the 0.08% round-trip cost an armed exit
            # could book a LOSS while the reason says profit is locked.
            # The stop is therefore PRICE-floored: it can never sit below
            # entry + 2x the round-trip cost (net-positive by one cost
            # unit). Near the arm point this tightens the net; a real
            # climb (peak well above entry) never feels it.
            _cap = _armed_cap_base(_goal_tr)
            if _pgf is not None:
                _cap = round(_cap * _pgf, 3)
            try:
                from contest_account import COST_ROUNDTRIP_PCT as _rt_pct
                _lock_w = max(0.0, (1.0 - entry * (1.0 + 2.0 * _rt_pct / 100.0)
                                    / high) * 100.0)
                if _lock_w > 0:
                    _cap = min(_cap, round(_lock_w, 3))
            except Exception as exc:
                logger.debug("trail cap (lock window) skipped: %s", exc)
            _trail = min(_trail, _cap)
        if high > entry and live <= high * (1 - _trail / 100):
            _peak_usd = (high / entry - 1.0) * size
            should_exit = True
            reason = (f"PROFIT GUARD FIRED — dropped {(high - live) / high * 100:.1f}% from the peak "
                      f"(trail ratcheted to {_trail:.1f}% — the run reached +${_peak_usd:.0f})")
            if w.get("trail_armed"):
                reason += (f" — HALF-GOAL TRAIL: the run reached +{_arm_at:.1f}% "
                           f"(half the +{_goal_tr:.1f}% goal), so the stop sits "
                           f"{_trail:.1f}% under the peak "
                           f"and the profit is locked (above entry + costs)")
            if _pgres is not None and _pgres.get("readable"):
                try:
                    reason += f" — ADAPTIVE GUARD: {_pg.explain(_pgres, asset)}"
                except Exception as exc:
                    logger.debug("guard explain clause skipped: %s", exc)

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
                _stop_usd = float(w.get("hard_stop_usd") or 0.0)
                if _stop_usd <= 0.0:
                    _stop_usd = _hard_stop_usd(entry, size, _ca_l.buffer(_acct_l))
                if w.get("hm_mode") == "opportunistic":
                    # THE OPPORTUNISTIC HARD STOP (user ask, Oct 1): the
                    # exit watch IS the authority and judges this position
                    # against a 2% fixed stop — tighter than the half-buffer
                    # rule (the strict risk control the opportunistic mode's
                    # lower bar pays for). Only ever TIGHTENS: the classic
                    # stop stands when the buffer is the smaller number.
                    _hm_stop = max(float(entry) * float(_ca_l.HAIL_MARY_OPP_HARD_STOP_PCT) / 100.0, 5.0)
                    if _hm_stop < _stop_usd:
                        _stop_usd = _hm_stop
            except Exception:
                _stop_usd = max((float(cfg.challenge_balance) * 0.03) * 0.5, 5.0)
            if _usd_now <= -_stop_usd:
                should_exit = True
                _hm_stop_why = ("the opportunistic hard stop"
                                if w.get("hm_mode") == "opportunistic"
                                else "half the platform death-line buffer")
                reason = (f"STOPPED OUT — down ${abs(_usd_now):.0f} (the stop is "
                          f"-${_stop_usd:.0f} — {_hm_stop_why}). "
                          f"The trade failed — get out, wait for the next one.")

        # ── THE VALIDATED 5m EDGE OWNS ITS EXIT (Nov 13): the exit marks
        #    come from the entry bar's and later bars' 5m high/low, not the
        #    once-per-30s ticker. The generic trail, 1h sell bar and
        #    exit-watch force are not this edge's rules; the death floor and
        #    lost-row verdict still run below and can never be cleared.
        if _edge_row:
            _edge_hit = None
            _blx = None
            try:
                import bounce_live as _blx
                _edge_hit = _blx.exit_signal(exchange, pair, w)
            except Exception as _blx_exc:
                logger.warning("5m edge exit read failed for %s (%s) — "
                               "ticker fallback only", pair, _blx_exc)
            if _edge_hit is None and _blx is not None:
                try:
                    _edge_hit = _blx.ticker_exit(w, live)
                except Exception as _blx_exc:
                    logger.warning("5m edge ticker fallback failed for %s (%s)",
                                   pair, _blx_exc)
            if _edge_hit:
                should_exit = True
                reason = _edge_hit
            else:
                # discard the generic trail/sell-bar reason from above
                should_exit = False
                reason = ""

        # ── 2. strategy sell bar (contest profile: 50%) ────────────────
        if not should_exit and not _edge_row:
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

        # ── 3. THE EXIT WATCH'S FORCE GATE (user spec, Sep 23) — the
        # LAST gate: the profit guard's trail, the hard stop and the
        # trend sell bar are the calibrated exits and get their say
        # first. ExitMod > EXITMOD_FORCE (a maxed reversal + exhaustion
        # on a dead trend read) arms the Get Out — same popup → push →
        # auto-close path as every other gate, never a silent close.
        if not should_exit and not _edge_row:
            try:
                _tw_gate = w.get("exit_watch")
                if isinstance(_tw_gate, dict) and _tw_gate.get("force_exit"):
                    # THE FRESHNESS GRACE (the TIA bounce cure, Oct 10,
                    # user-run rule change: 13 forced in/out cycles in one
                    # morning — every kill was the SAME unchanged ExitMod
                    # 0.3505 the entry inherited, +0.0005 over the gate,
                    # zero new information after the fire). Background
                    # hum the position was BORN into doesn't get to
                    # execute it: within FRESH_GRACE_EXIT_MIN of entry the
                    # force gate needs NEW deterioration — exit_mod risen
                    # EXIT_GRACE_RISE above the entry stamp — or it waits
                    # out the grace (the other calibrated gates + the
                    # trail + the hard stop still guard every cycle; the
                    # clock is the only thing that ever stays silent).
                    # After the grace, the raw gate stands as before. A
                    # missing entry stamp (old rows, a broken fire-time
                    # read) degrades to held-time-only — and ANY unreadable
                    # exit read never skips the other gates.
                    _held_min_g = -1.0
                    try:
                        _w_added = w.get("added")
                        if _w_added:
                            _held_min_g = (time.time()
                                           - datetime.fromisoformat(
                                               _w_added).timestamp()) / 60.0
                    except Exception:
                        _held_min_g = -1.0
                    _em_now = _tw_gate.get("exit_mod")
                    _em_entry = w.get("entry_exit_mod")
                    _grace_active = (0 <= _held_min_g < FRESH_GRACE_EXIT_MIN)
                    _rose = (isinstance(_em_now, (int, float))
                             and isinstance(_em_entry, (int, float))
                             and float(_em_now) >= float(_em_entry) + EXIT_GRACE_RISE)
                    if _grace_active and not _rose:
                        logger.info(
                            "  EXIT-WATCH GRACE: %s held %.1f min — force gate "
                            "(ExitMod %.2f) is the standing entry read (%.2f at "
                            "fire time), no NEW deterioration — the gate waits; "
                            "trail + hard stop still guard",
                            pair, max(_held_min_g, 0.0), float(_em_now or 0.0),
                            float(_em_entry or 0.0))
                    else:
                        should_exit = True
                        reason = str(_tw_gate.get("force_reason") or
                                     "EXIT WATCH — the trend read says this move is done")
            except Exception as _exc:
                logger.debug("exit-watch force gate read skipped for %s: %s",
                             pair, _exc)

        # ── 4. TAKE IT = GET OUT (Nov 12, user rule: "take it gets wired
        # to get out — they should act the same way; cleared and waiting
        # for a setup to re-enter"). The goal line is a BANK, not a perch:
        # reaching it enters the same popup → push → auto-close → clear
        # path as GET OUT, so the row cannot linger and block the next
        # clean setup. The old STAY CLOSE goal branch is retired below.
        if not should_exit and not _edge_row and entry > 0 and live > 0:
            try:
                _goal_take = (float(w.get("goal_pct") or 0)
                              or _window_goal_pct(pair))
                _pct_take = (live / entry - 1.0) * 100.0
                if _goal_take > 0 and _pct_take >= _goal_take:
                    should_exit = True
                    reason = (
                        f"TAKE IT — the +{_goal_take:.1f}% goal is paid "
                        f"({_pct_take:+.1f}%). Bank it, clear the watch, "
                        "and wait for the next clean setup to re-enter.")
                    _save_take_payout(
                        pair, round(_pct_take / 100.0 * size))
            except Exception as _take_exc:
                logger.debug("TAKE IT goal gate skipped for %s: %s",
                             pair, _take_exc)

        # ── 5. THE PLAN-SPENT TREND GATE (Nov 7, user ask): the drift report
        #    makes a spent plan VISIBLE; this lets a fire act on it — but only
        #    with the trend's agreement. Overdue alone closes nothing (the
        #    clock may never end a fire), and a damaged trend alone is already
        #    the force gate's call. Both together means the thesis is gone.
        if not should_exit and not _edge_row:
            try:
                _psx = plan_spent_exit(w)
                if _psx:
                    should_exit = True
                    reason = (
                        f"PLAN SPENT — {_psx['plan']['elapsed_min']:.0f} min of a "
                        f"{_psx['plan']['planned_min']:.0f}-minute plan "
                        f"({_psx['plan']['frac'] * 100:.0f}%), and the exit watch's "
                        f"own trend read is {_psx['exit_mod']:.2f} (bar "
                        f"{_psx['threshold']:.2f}): the plan that carried this fire "
                        f"is over and the move is done. The clock alone never closes "
                        f"a fire — this is the trend's call on a spent plan.")
                    logger.info("  PLAN SPENT + TREND LOST: %s — %.0f/%.0f min, "
                                "exit_mod %.2f >= bar %.2f — closing on the trend, "
                                "not the clock", pair.split("/")[0],
                                _psx["plan"]["elapsed_min"],
                                _psx["plan"]["planned_min"],
                                _psx["exit_mod"], _psx["threshold"])
            except Exception as _psx_exc:
                logger.debug("plan-spent gate skipped for %s: %s", pair, _psx_exc)

        # ── THE DOLLAR GOAL TAKE (Nov 10, user rule: "the goal when reached
        #    will trigger an exit check when the dollar goal is reached (the
        #    dollar goal is set by the entry logic, QC); this will replace
        #    the current way it is set up"). The row carries the dollars it
        #    must make — floored at its own 1R and capped by its plan — and
        #    reaching them IS the exit, not an announcement. A row with no
        #    stamp (an old row, a failed read) keeps the incumbent %-goal
        #    behaviour, so this can never make an unmeasured row exit blind.
        if (not should_exit and not _edge_row
                and _dollar_goal_armed() and not w.get("go_big")):
            try:
                _dg_goal = float(w.get("goal_usd") or 0.0)
                _dg_size = float(w.get("size") or 0.0)
                _dg_usd = ((float(live) / float(entry)) - 1.0) * _dg_size \
                    if float(entry or 0.0) > 0 else 0.0
                if _dg_goal > 0.0 and _dg_usd >= _dg_goal:
                    should_exit = True
                    reason = (
                        f"DOLLAR GOAL TAKEN — +${_dg_usd:,.2f} on a "
                        f"${_dg_size:,.0f} row reached the ${_dg_goal:,.2f} the "
                        f"entry logic set ({_dg_usd / _dg_size * 100.0:.2f}% of "
                        f"the row, plan goal +{_close_goal_pct(w, pair):.2f}%). "
                        f"The take is booked in dollars, not by a move target.")
                    logger.info("  DOLLAR GOAL: %s took $%.2f of $%.2f",
                                pair, _dg_usd, _dg_goal)
            except Exception as _dg_exc:
                logger.debug("dollar goal gate skipped for %s: %s",
                             pair, _dg_exc)

        # (Sep 21, user rule: the window NEVER exits by itself — "I don't
        # want to Exit simply because 'Time's up'. Stay close, and fire a
        # popup only when the trend watch or the exit watch fire the Get
        # out now.") The clock only reminds (gate 6 below); the exit call
        # belongs to the profit guard, the hard stop, the trend sell bar
        # and the climax — never to the clock.
        # ── THE LOST-ROW VERDICT (Nov 6): a row the watch did not see for
        # LOST_ROW_MIN is not a strategy call — the book was unmanaged for
        # that stretch, so the watch recovers the row whatever the gates
        # read, and says why. (The nine Oct 22 rows all wore "PROFIT GUARD
        # FIRED": the arithmetic ran on a nine-day-old peak, honest about
        # the drop and silent about the gap.)
        # ── 5b. THE DEATH-FLOOR EXIT MONITOR (Nov 12): entries were never
        #     vetoed, and the exit is where the floor earns its keep.  When
        #     the marked book is at/below the halt line, or holding through a
        #     normal adverse move would put equity at/below the death line,
        #     this raises the SAME GET OUT call as every other exit gate.  It
        #     is deliberately last: a more specific profit/stop/trend reason
        #     already on the row keeps its voice.
        if not should_exit and _floor.get("exit_warning"):
            should_exit = True
            reason = ("GET OUT — DEATH-LINE EXIT WATCH: "
                      f"{_floor.get('exit_reason') or _floor.get('why')}. "
                      "The floor does not veto entries; it eats them when "
                      "staying in is too risky.")
            logger.warning("  DEATH-LINE GET OUT: %s — %s", pair, reason)
        if _lost_m is not None:
            logger.warning("WATCH LOST: %s unseen %.0f min (cap %.0f) — "
                           "closing at %.8g; the book was unmanaged",
                           pair, _lost_m, LOST_ROW_MIN, float(live))
            should_exit = True
            reason = (f"WATCH LOST THIS ROW — unseen {_lost_m:.0f} min "
                      f"(cap {LOST_ROW_MIN:.0f}); the trail ran on a stale "
                      f"peak, so this is the watch recovering the row, "
                      f"not a signal")
        if should_exit:
            # ── THE HARD-STOP INDEPENDENT JUDGE (the recovered ordering
            # majors, Sep 29: CR 09:52 on :3842 and 15:18 on :3969): the
            # gates above run `if not should_exit`, so on a book past its
            # peak the PROFIT GUARD reason is already set while price
            # sinks through the death line — gate 1b never evaluates and
            # the reason never becomes STOPPED OUT. Judged here
            # INDEPENDENTLY: if the death line is breached, the reason
            # IS the hard stop, whatever fired first, and the seam below
            # is bypassed outright (the account-killer outranks every
            # window and every arm gate).
            _hs_breach, _hs_reason = _hard_stop_breached(w, live)
            if _hs_breach:
                reason = _hs_reason   # the reason IS the hard stop
            # ── THE PROTECTED-EXTENSION SEAM (Sep 27, exit_extension.py):
            # the machinery always ASKS before it closes — the module
            # answers per the operator's dial. "suppress" sets the call
            # aside THIS cycle: the book rides UNDER the incumbent trail
            # (the ratchet + the half-goal armed cap + the price floor,
            # never naked) until the window lapses or the dial flips off.
            # Any seam failure degrades to the incumbent exit — the
            # extension can never trap a position the machinery wants out.
            # BYPASS (the 09:52 major): a breached death line never asks.
            _had_stamp = isinstance(w.get("exit_extension"), dict)
            _floor_exit = bool(_floor.get("exit_warning"))
            if _hs_breach or _floor_exit:
                v_ext, why_ext = "exit", (
                    "death-floor bypass — the GET OUT warning outranks every "
                    "hold window" if _floor_exit else
                    "hard-stop bypass — the death line outranks every window")
                logger.info("EXIT EXTENSION bypassed for %s: %s", pair,
                            why_ext)
            else:
                try:
                    v_ext, why_ext = _exx.exit_verdict(pair, w, str(reason),
                                                       live=live)
                except Exception as _exv_exc:
                    v_ext, why_ext = "exit", f"seam failed ({_exv_exc}) — incumbent exit"
                    logger.warning("exit-extension seam failed for %s: %s",
                                   pair, _exv_exc)
            if v_ext == "suppress":
                if not _had_stamp:
                    # the arm just landed — persist the stamp once so
                    # the window and the daily cap are real across ticks
                    _save_contest_watch(watch)
                logger.info("EXIT EXTENSION: %s — %s (the ride continues "
                            "under the incumbent trail; the next exit "
                            "verdict fires when the window lapses)",
                            pair, why_ext)
                _last_exit_voice.pop(pair, None)   # the real exit shouts instantly later
                # THE RIDE PRICES (the round-6 major): the goal call —
                # where _unrealized_sum += usd lives — sits AFTER this
                # continue, so a suppressed book's unrealized leg never
                # reached the give-back watermark: the ledger would
                # tighten on a phantom drawdown and MISS the ride's
                # recovery. Accumulate the extended book's live P&L
                # right here, exactly as the goal call would.
                try:
                    _usd_ext = ((float(live) / float(entry) - 1.0)
                                * float(w.get("size") or 0.0))
                    _unrealized_sum += _usd_ext
                except Exception as _ug_exc:
                    logger.debug("give-back extended-book pricing skipped "
                                 "for %s (%s)", pair, _ug_exc)
                continue
            if _had_stamp and "exit_extension" not in w:
                # the window lapsed and the seam cleared the stamp —
                # persist the revert before the exit machinery proceeds
                _save_contest_watch(watch)
            exited_pairs.append(pair)
            # GET OUT PERSISTS + ALWAYS FIRES (user rule, Sep 21: "don't
            # ever clear an asset from the watch without firing a popup
            # to Get out Now"): the call repeats every ~60s while the exit
            # condition holds, and it fires whether or not HER VOICE is
            # on — the phone push and the her-words log ride along every
            # time, carrying the FULL reasons (the gate's why + the
            # position facts). The auto-close below removes the row this
            # same cycle; the popup is never skipped on its way out.
            _now_ts = time.time()
            if (_now_ts - _last_exit_voice.get(pair, 0)) >= 60:
                _last_exit_voice[pair] = _now_ts
                # THE OUTLIVER HINDSIGHT (the family spec, section 5): the
                # fire stamps its decision-time evidence at the branch —
                # the dip depth against the entry, the book's bucket row,
                # and the window minutes the boundary judgement rides.
                try:
                    _ol_dip = (float(entry) - float(live)) / float(entry) * 100.0 \
                        if float(entry) > 0 else 0.0
                    if _ol_dip > 0:
                        _journal_outliver_fire(pair, entry, live, high, size,
                            (float(live) / float(entry) - 1.0) * size,
                            _ol_dip, str(reason)[:120])
                except Exception as _ol_exc:
                    logger.debug("outliver fire-site journal skipped for %s "
                                 "(%s)", pair, _ol_exc)
                # voice-first (user rule, Aug 27: the voice IS the contest —
                # the user is on the phone, not at the screen): every call
                # carries its WHY, spoken
                _why = ""
                _r = reason.lower()
                if "take it" in _r:
                    _why = ("The goal is paid — taking it, clearing the "
                            "watch, and waiting for the next setup!")
                elif "profit guard" in _r:
                    _why = "It gave back too much from the top!"
                elif "stopped out" in _r:
                    _why = "Down a hundred dollars — the trade failed!"
                elif "death-line" in _r or "death line" in _r:
                    _why = ("The death line is too close — staying in can "
                            "close the contest! Get out before it!")
                elif "trend broke" in _r:
                    _why = "The trend turned against you!"
                elif "climax" in _r:
                    _why = "The move is done!"
                elif "exhaustion" in _r or "steam" in _r:
                    _why = "It's out of steam!"
                _push_txt = (f"GET OUT NOW — Sell {asset}!\n"
                             f"WHY: {reason}\n")
                try:
                    _gx_pct = (float(live) / float(entry) - 1.0) * 100.0
                    _gx_usd = _gx_pct / 100.0 * float(w.get("size") or 0.0)
                    _gx_held = ""
                    if w.get("added"):
                        _gx_held = (f" · held {(_now_mst() - datetime.fromisoformat(w['added'])).total_seconds() / 60.0:.0f} min")
                    _push_txt += (f"POSITION: entered {float(entry):.4f} · now {float(live):.4f} · "
                                  f"{_gx_pct:+.1f}% ({_gx_usd:+,.0f} dollars){_gx_held}\n"
                                  f"GOAL was +{_close_goal_pct(w, pair):.1f}% — "
                                  f"the watch auto-closed; the grade is journaled.")
                except Exception:
                    _push_txt += "The watch auto-closed; the grade is journaled."
                # THE POPUP LEDGER first (Sep 21): the exit call is journaled
                # even if the voice/push delivery dies — what fired is history.
                popup_journal("GET OUT", f"Get out now — Sell {asset}!", _push_txt)
                # one mouth (Oct 9 revert: user prefers Mira for
                # everything): GET OUT stays Mira's voice, the urgency
                # rides the rate and the exclamation marks — "Get out
                # now!" — nothing more.
                _speak_server(f"Get out now! Sell {asset}! {_why}"
                              f"I'll base-call the next one the moment it sets up — no clock-waiting!", rate=1,
                              push_text=_push_txt)
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
            _journaled = False   # (Sep 27) the pop permission: only a close
            # that actually wrote its ledger receipt may remove the row —
            # a vanished row with no receipt is the leak class the
            # battery's contest_watch prod-guard caught (OP/ONDO)
            try:
                import json as _jx
                import contest_account as _ca_x
                import ledger_artifacts as _la_x
                _entry_x = float(w.get("entry") or 0.0)
                _size_x = float(w.get("size") or 0.0)
                _live_x = float(live or 0.0)
                # the goal this close records: the stamp, else the goal the
                # exit logic is actually managing the trade to (Nov 6)
                _goal_x = _close_goal_pct(w, pair)
                # THE MARKET GUARD (Nov 6): the ledger takes facts, and only
                # real USD markets produce them. A synthetic self-test pair
                # closed through THIS path on Sep 30 (FAKE/USD, four closes,
                # $+2.40 booked and the balance moved); the pair is now
                # refused before any money moves, so a self-test can print
                # and speak but can never become a trade. The watch row is
                # then kept, because the _journaled rule below grants no
                # removal to a close with no receipt.
                if not _la_x.is_real_usd_pair(pair):
                    logger.warning("CLOSE NOT BOOKED — %s is not a real USD "
                                   "market; the ledger takes real pairs "
                                   "only", pair)
                    _closed_note = "not a real USD market — not booked"
                elif _entry_x > 0 and _size_x > 0 and _live_x > 0:
                    _gross, _cost, _net = _ca_x.pnl_net_usd(
                        _entry_x, _live_x, _size_x)
                    _applied, _new_bal, _note = _ca_x.apply_pnl(
                        _net, w.get("contest_id"),
                        dedupe_key=f"{pair}|{w.get('added')}|getout")
                    # THE SURVIVABILITY GUARD sees only REAL bookings: the
                    # guard trips on consecutive losses / drawdown measured
                    # from the booked balance, so a skipped or replayed
                    # close (applied=False) must not move its state.
                    if _applied:
                        try:
                            import survival_guard as _sgx
                            _sgx.record_close(_net, _new_bal)
                        except Exception as _sgx_e:
                            logger.debug("survival guard record failed: %s", _sgx_e)
                    _held_min = ""
                    try:
                        _added = w.get("added")
                        if _added:
                            _held_min = f"{(_now_mst() - datetime.fromisoformat(_added)).total_seconds() / 60.0:.0f}"
                    except (TypeError, ValueError):
                        pass  # cosmetic held-minutes only — the trade row is already booked
                    with open(CONTEST_TRADES_FILE, "a", encoding="utf-8") as _fx:
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
                            "goal_pct": _goal_x,
                            "held_min": _held_min,
                            "closed_by": "getout",
                            "exit_reason": reason[:200],
                            # THE FAMILY RIDES THE CLOSE (Nov 6): the row
                            # was stamped at fire time; the ledger keeps
                            # it, so the study never joins popup text.
                            "setup": w.get("setup") or {},
                            "setup_v": w.get("setup_v") or 0,
                        }) + "\n")
                    _journaled = True   # the receipt is on disk — the row may go
                    # THE PACE GOVERNOR RECOMPUTES ON THE CLOSE ANNOTATION:
                    # ETA, checkpoint and any 3-loss micro gate tune update
                    # from the row just written, before the next scan.
                    try:
                        import overwatch_governor as _og
                        _og.refresh()
                    except Exception as _og_exc:
                        logger.debug("overwatch governor refresh skipped: %s",
                                     _og_exc)
                    # ── THE SELF-AUDIT LAYER (user spec, Sep 22): after
                    # every close, count the rule violations the row can
                    # prove, feed tomorrow's guard (>=3 -> x0.85 + the
                    # confidence dock; 0 -> penalty decay + x1.05).
                    try:
                        import meta_layers as _mla
                        _aud = _mla.audit_close(
                            {"pair": pair, "entry": _entry_x,
                             "exit": _live_x, "goal_pct": _goal_x,
                             "exit_reason": reason, "closed_by": "getout"},
                            w)
                        if _aud.get("violations"):
                            logger.warning("SAL: %s closed with %d rule "
                                           "violations — tomorrow's guard "
                                           "adjusts", pair, _aud["violations"])
                    except Exception as _sal_exc:
                        logger.debug("self-audit failed: %s", _sal_exc)
                    # ── THE POST-EXIT SHADOW TRAIL (user spec, Sep 22):
                    # track what the market does after we leave — the
                    # next high, pullback, breakout, liquidity surge —
                    # so graduation writes the miss into the ledgers.
                    try:
                        import meta_layers as _mlsh
                        _mlsh.shadow_trail_start(pair, _live_x, w)
                    except Exception as _sh_exc:
                        logger.debug("shadow trail start failed: %s", _sh_exc)
                    # ── RE-ENTRY AWARENESS (user spec, Sep 22): the five
                    # conditions decide whether the trend that just paid
                    # deserves a marked candidate for its next setup.
                    try:
                        import meta_layers as _mlre
                        _re = _mlre.reentry_candidate_check(pair)
                        if _re.get("eligible"):
                            logger.info("RE-ENTRY CANDIDATE: %s — the "
                                        "trend still lives, the next "
                                        "clean setup may re-enter", asset)
                    except Exception as _re_exc:
                        logger.debug("re-entry check failed: %s", _re_exc)
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
                        except Exception as _ra_exc:
                            # display-only lift (Sep 21) — a failed lift
                            # must never un-book a closed trade, but it
                            # must be visible
                            logger.warning("re-arm display lift failed for %s: %s",
                                           pair, _ra_exc)
                else:
                    _closed_note = "skipped — missing entry/size/live"
                    logger.warning("GET OUT close skipped for %s: entry=%s size=%s live=%s",
                                   pair, _entry_x, _size_x, _live_x)
            except Exception as exc:
                _closed_note = "close failed — watch row kept for SOLD"
                logger.warning("GET OUT auto-close failed for %s (%s) — "
                               "the SOLD button remains the way out", pair, exc)
            if _journaled:
                # THE TOMBSTONE, NOT A POP (the round-2 review's
                # resurrection bug): under merge-save an ABSENT key
                # means "never seen" and the save would resurrect the
                # row the journal just closed — None is the deletion
                # marker the merge-save honors (dropped AND never
                # re-added by the update pass).
                watch[pair] = None
                _save_contest_watch(watch)
                # ── THE BOOK FOLLOWS THE LEDGER (Sep 29, the INJ ghost):
                # this auto-close sold REAL coins but only journaled the
                # sale — the signal bridge's filled row kept saying
                # "holding" (INJ sold 08:27 yet its bridge row rode every
                # rotation-boundary voice line until the 20:00 reset
                # finally closed it 12h later; the console's CLEAN GHOSTS
                # button exists for exactly this class). Close the pair's
                # filled row here, so the book and the ledger agree the
                # moment the receipt is on disk. Fail-soft: a bridge
                # hiccup can never unbook a closed trade.
                try:
                    from signal_bridge import mark_closed_by_pair as _mcbp
                    _mcbp(pair, _net)
                except Exception as _bg_exc:
                    logger.warning("bridge book-close failed for %s (the "
                                   "CLEAN GHOSTS button remains the net): %s",
                                   pair, _bg_exc)
            else:
                # the row STAYS — the SOLD button remains the way out
                # (the close promise, now enforced: skipped-close and
                # failed-close can no longer silently delete the row)
                logger.warning("GET OUT kept the %s watch row — no close "
                               "receipt (journal skipped or failed)", pair)
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
        _unrealized_sum += usd
        _goal = float(w.get("goal_pct") or 0) or _window_goal_pct(pair)
        if "goal_pct" not in w:
            w["goal_pct"] = _goal
            w["window_min"] = max(60.0, min(180.0, _goal * 60.0))
            dirty = True
        # ── THE PLAN vs THE CLOCK (Nov 7, user ask): report each open
        #    fire's planned window against the time it has spent, so a fire
        #    drifting past its plan is VISIBLE before the exit watch has to
        #    act. Report only — the exit stays the trend watch's call.
        try:
            _report_plan_drift(pair, w, pct)
        except Exception as _pd_exc:
            logger.debug("plan-drift report skipped for %s: %s", pair, _pd_exc)
        # A reached goal is a TAKE IT exit (gate 4 above): it shares the
        # GET OUT close path and never leaves a STAY CLOSE row behind.
        # The old goal-hit voice/save branch is retired with that rule.

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
                if _rem <= 0:
                    # STAY CLOSE (user rule, Sep 21): past the window the
                    # position stays WATCHED — she keeps talking, the exit
                    # comes only from the trend/exit gates, never the clock.
                    _tail = (" — time's up, stay close! I'll call the exit "
                             "when the trend breaks")
                else:
                    _tail = f", about {_rem:.0f} minutes left"
                    _tail += ", stay close" if _near else ", go walk around"
            else:
                _tail = ""
        except Exception:
            _tail = ""

        # the goal trail, when armed, is SAID so the rider knows the net
        # is already set under the peak — visible before it ever fires
        # (user rule: she must be able to hear the plan, not read code)
        if w.get("trail_armed") and _goal > 0:
            # the voice computes the SAME stop the exit enforces — the
            # adaptive factor scales both sides identically (parity)
            _vgf = None
            try:
                import profit_guard as _pgv
                _vres = _pgv.guard_factor(pair, w)
                if _vres.get("readable"):
                    _vgf = float(_vres["factor"])
            except Exception:
                _vgf = None
            _trail_now = _ratchet_trail_pct(entry, high, size)
            _cap_now = _armed_cap_base(_goal)
            try:
                from contest_account import COST_ROUNDTRIP_PCT as _rt_pct
                _lock_wv = max(0.0, (1.0 - entry * (1.0 + 2.0 * _rt_pct / 100.0)
                                     / high) * 100.0)
                if _lock_wv > 0:
                    _cap_now = min(_cap_now, round(_lock_wv, 3))
            except Exception as exc:
                logger.debug("trail cap (lock window, live) skipped: %s", exc)
            if _vgf is not None:
                _trail_now = round(_trail_now * _vgf, 3)
                _cap_now = round(_cap_now * _vgf, 3)
            if _eg_m is not None:
                _trail_now = round(_trail_now * _eg_m[0], 3)
            _trail_now = min(_trail_now, _cap_now)
            _trail_px = high * (1 - _trail_now / 100.0)
            _tail += (f" — trail set {_trail_now:.1f}% under the peak, "
                      f"locks {_trail_px:.5f}")
            if _eg_m is not None:
                _tail += (f" — END-GAME MODE: the net tightens "
                          f"(trail x{_eg_m[0]}, lock-in x{_eg_m[1]}) "
                          f"because reality drifted from the calibration")

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

    # THE GIVE-BACK LEDGER (user, Sep 29): every tick prices the open
    # book — the watermark tracks the day's peak total (realized +
    # unrealized) and, past the trigger, tightens the guard ONCE through
    # auto_adjust's own rails so the ledger counts the erosion. Runs
    # BEFORE the exit early-return: a full-exit tick is exactly when the
    # unrealized leg goes to zero and the give-back must update.
    _track_day_giveback(_unrealized_sum)
    if exited_pairs:
        # (Sep 21 comment fix — the code changed under this comment long
        # ago: the GET OUT path AUTO-CLOSES the trade and REMOVES the watch
        # row itself — the None tombstone above. The watch auto-stops when
        # GET OUT fires; SOLD is only for the user's own early take.)
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
        _speak_server(_all_good_lead() + ". " + ". ".join(voice_updates) + ".")


def main():
    import ccxt
    import pandas as pd
    from config import DEFAULT_CONFIG, TradingConfig   # DEFAULT_CONFIG: the fire path's unreadable-balance fallbacks (the rot census, Oct 3) — the module imports nothing at top level (the fleet convention) so the constant rides the main() locals
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
    # THE ENTRY GATE THIS PROCESS RUNS, named at boot (Nov 10): the dial is
    # invisible otherwise, and "which funnel am I in" must be readable from
    # the log of the process that is actually running -- not inferred from
    # the source on disk.
    try:
        _kb_armed = _knife_blocks_entry()
        logger.info("  ENTRY GATE: KNIFE_BLOCK %s — %s",
                    "ARMED" if _kb_armed else "OFF",
                    "a broken-trend fire is REFUSED at entry" if _kb_armed
                    else "a broken-trend fire lands at MINIMUM SIZE (Oct 9 rule)")
    except Exception as _kb_exc:
        logger.warning("  ENTRY GATE: KNIFE_BLOCK unreadable (%s) — fail-closed, armed", _kb_exc)
    # ── THE RISK ENVELOPE (Nov 6, the getout-cascade review): the boot
    #    line that names which funnel THIS process runs. The dial is
    #    invisible otherwise — a strict refusal and a dial-off skip look
    #    the same in the log until someone greps the source.
    # ── THE SIZED ENTRY (Nov 11): the ONE configured sizing number, printed
    #    at boot so the receipt is on the record before any fire happens. The
    #    3% guard and the book carry still cut it; this is the ceiling.
    try:
        try:
            import contest_account as _ca_boot
            _acct_boot = _ca_boot.load_state()
            _sbal_boot = float(_acct_boot.get("current_balance") or 0.0)
        except Exception:
            _sbal_boot = 0.0
        if _sbal_boot <= 0.0:
            _sbal_boot = float(DEFAULT_CONFIG.challenge_balance)
        _sfrac_boot = min(max(float(getattr(DEFAULT_CONFIG, "contest_size_frac", 1.0) or 1.0), 0.0), 1.0)
        logger.info("  SIZE: configured %.2f%% of $%.2f = $%.2f per entry "
                    "(ceiling — the 3%% guard and the book carry still cut it)",
                    100.0 * _sfrac_boot, _sbal_boot, _sbal_boot * _sfrac_boot)
    except (OSError, TypeError, ValueError) as exc:
        # 7597: the boot SIZE banner is informational; the scanner must boot
        # without it, and the miss is named
        logger.debug("  boot SIZE banner skipped: %s", exc)

    try:
        logger.info("  %s", contest_standing_text())
        contest_paycheck_check()
    except Exception as _cs_boot_exc:
        logger.debug("contest standing at boot failed: %s", _cs_boot_exc)

    logger.info("  RISK ENVELOPE: strict fires %s | book cap %.0f%% of balance "
                "| %d fires/day | stop <= %.1f%% of size, floor $%.2f",
                "ON" if _strict_fires() else "OFF (old funnel)",
                MAX_AGGREGATE_EXPOSURE_FRAC * 100.0, MAX_FIRES_PER_DAY,
                HARD_STOP_MAX_PCT, HARD_STOP_ABS_FLOOR_USD)
    _push_channel_boot()
    contest_watchdog_boot()
    death_floor_boot()
    # ── THE LOST-ROW CAP (Nov 6, the nine-day ride): the watch recovers a
    #    row it has not seen for this long instead of riding it (see
    #    _watch_lost_minutes). Named at boot like every other live dial.
    logger.info("  WATCH LOST CAP: %.0f min unheard (SCOUT_LOST_ROW_MIN) — "
                "an unseen row is closed with its gap on the receipt, "
                "never ridden blind", LOST_ROW_MIN)
    # the death-line lesson: the mandate the last breach armed (if any)
    try:
        _dm_boot = _audit_death_mandate()
        if _dm_boot is not None:
            import death_lessons as _dl_boot
            logger.info("  DEATH MANDATE: %s", _dl_boot.describe(_dm_boot))
    except Exception as _dm_boot_exc:
        logger.debug("death mandate readout skipped: %s", _dm_boot_exc)

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
        # ── THE OPP VERDICT DRIVER (Oct 2): the accumulation window's
        # keep/kill on a schedule — scratch/opp_hindsight.py run as a
        # subprocess (the untracked tool owns its method), the verdict
        # prepended to opp_hindsight_report.txt weekly. A gone grader
        # fails safe; a broken pass never takes the funnel down.
        threading.Thread(target=_tbw.opp_verdict_driver_loop,
                         args=(_tbw_stop,), daemon=True,
                         name="opp-verdict-driver").start()
        logger.info("  Opp verdict driver: ON (the champion-opportunistic "
                    "keep/kill on schedule — weekly; first read in "
                    "%d min)",
                    int(_tbw.OPP_VERDICT_FIRST_DELAY_S // 60))
    except Exception as _tbw_exc:
        logger.info("  Tide bar weekly driver: OFF (%s)", _tbw_exc)

    # ── THE BASE-GRADE DAEMON (Sep 20 spec; boot restored Sep 22 — the
    # block was lost in the stash-sweep era and the pin held the spec):
    # the study re-prices its fleet-wide verdict monthly as the deep
    # stores grow; first pass ~15 min after boot. Never raises; dies
    # with the scanner. Appends _basegrade_monthly.jsonl.
    try:
        import diag_basegrade as _dbg
        _dbg_stop = threading.Event()
        threading.Thread(target=_dbg.monthly_driver_loop,
                         args=(_dbg_stop,), daemon=True,
                         name="basegrade-monthly").start()
        logger.info("  Base grade driver: ON (the retired 'about an hour' "
                    "claim graded fleet-wide — appends _basegrade_monthly.jsonl, "
                    "monthly; first pass in %d min)",
                    int(_dbg.FIRST_DELAY_S // 60))
    except Exception as _dbg_exc:
        logger.info("  Base grade driver: OFF (%s)", _dbg_exc)

    # ── THE RECURRING-COST DRIVERS (the family, Sep 16): each study
    # re-prices monthly as the deep stores and the outcomes ledger grow;
    # first passes stagger after boot. Never raise; die with the scanner.
    try:
        import diag_tidememory_hindsight as _dth
        _dth_stop = threading.Event()
        threading.Thread(target=_dth.monthly_driver_loop,
                         args=(_dth_stop,), daemon=True,
                         name="tidehindsight-monthly").start()
        logger.info("  Tide-memory hindsight driver: ON (the per-coin memory's dollar effect re-priced monthly — appends _tidehindsight_monthly.jsonl; first pass in %d min)",
                    int(_dth.FIRST_DELAY_S // 60))
    except Exception as _dth_exc:
        logger.info("  Tide-memory hindsight driver: OFF (%s)", _dth_exc)
    try:
        import diag_laddergrade as _dlg
        _dlg_stop = threading.Event()
        threading.Thread(target=_dlg.monthly_driver_loop,
                         args=(_dlg_stop,), daemon=True,
                         name="laddergrade-monthly").start()
        logger.info("  No-chase ladder driver: ON (the whole ladder's counterfactual fleet pass, monthly — appends _laddergrade_monthly.jsonl; first pass in %d min)",
                    int(_dlg.FIRST_DELAY_S // 60))
    except Exception as _dlg_exc:
        logger.info("  No-chase ladder driver: OFF (%s)", _dlg_exc)
    try:
        import diag_outliverhindsight as _doh
        _doh_stop = threading.Event()
        threading.Thread(target=_doh.monthly_driver_loop,
                         args=(_doh_stop,), daemon=True,
                         name="outliverhindsight-monthly").start()
        # the family's conventions: the boot line names the ledger, the
        # file, and the first-pass delay — the drive pins all three.
        logger.info("  Outliver hindsight driver: ON (the early Get Out graded against holding to the boundary — appends _outliverhindsight_monthly.jsonl; first pass in %d min)",
                    int(_doh.FIRST_DELAY_S // 60))
    except Exception as _doh_exc:
        logger.info("  Outliver hindsight driver: OFF (%s)", _doh_exc)
    try:
        import diag_climbouthindsight as _dch
        _dch_stop = threading.Event()
        threading.Thread(target=_dch.monthly_driver_loop,
                         args=(_dch_stop,), daemon=True,
                         name="climbouthindsight-monthly").start()
        logger.info("  Climb-out hindsight driver: ON (the climb-out pops graded against holding — appends _climbouthindsight_monthly.jsonl; first pass in %d min)",
                    int(_dch.FIRST_DELAY_S // 60))
    except Exception as _dch_exc:
        logger.info("  Climb-out hindsight driver: OFF (%s)", _dch_exc)

    # ── Notification ──────────────────────────────────────────────────
    def notify_big(title: str, message: str, spoken: str | None = None):
        # THE DEATH FLOOR is advisory for popups: it never silences a BUY.
        # At/below the halt line it says its piece and the signal still lands.
        try:
            _nb_watch = _load_contest_watch() or {}
            _nb_marks = {}
            for _nb_pair in list(_nb_watch):
                try:
                    _nb_marks[_nb_pair] = float(
                        (exchange.fetch_ticker(_nb_pair) or {}).get("last") or 0.0)
                except Exception:
                    pass
            _nb_refusal = death_floor_refusal(
                "popup", watch=_nb_watch, marks=_nb_marks)
            if _nb_refusal:
                # ADVISORY ONLY: show the line, still show the signal.
                logger.warning("%s (advisory — the popup still fires)", _nb_refusal)
        except Exception as _nb_exc:
            logger.debug("popup death-floor read failed: %s", _nb_exc)
        """Desktop alert that STAYS until you read it — plus Zira says the
        WHOLE call out loud (user directive: she speaks why/when/how-long,
        not just the coin name — the bare 'BUY PENGU' morning taught us).
        The old balloon toast died in ~5 seconds; this popup holds the
        screen up to 3 minutes (or until you click OK).
        THE 17:48 HARDENING (Oct 9, the silent GET OUT lesson): the modal
        could sit UNDER the fullscreen console (never seen), and the voice
        spawn could die before rendering a single sample with no trace —
        both legs now fight back: the modal grabs TOPMOST (4096 = the
        always-on-top bit), and the voice spawn is WATCHED below (the
        _speak_server hardening logs 'died before speaking') instead of
        being mined 20 minutes later."""
        import subprocess
        safe_title = title.replace("'", "").replace('"', "")
        safe_msg = message.replace("'", "").replace('"', "").replace("\n", " | ")[:180]

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

        # 2) Persistent popup — stays up to 3 minutes or until clicked.
        #    TOPMOST force (4096 = MB_TOPMOST in the FSNAV bitfield): the
        #    modal now BEATS the fullscreen console instead of sitting
        #    behind it unseen.
        try:
            _t = safe_title.replace("\n", " ")
            _m = safe_msg.replace("\n", " ")
            ps_cmd = (
                f"$w = New-Object -ComObject WScript.Shell; "
                f"$r = $w.Popup('{_m}', 180, '{_t}', 4096 + 48)"
            )
            subprocess.Popen(['powershell', '-WindowStyle', 'Hidden', '-Command', ps_cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            logger.debug("Popup notification failed: %s", exc)

        # 3) Console log (always works)
        logger.info("🔔 ALERT: %s", title)
        for line in message.split("\n"):
            if line.strip():
                logger.info("   %s", line.strip())
        # 4) THE POPUP LEDGER (Sep 21): every popup the scanner fires is
        #    journaled append-only — the console's POPUP HISTORY dropdown
        #    reads this, so the console always shows what fired, even when
        #    the phone push fails, the watch clobbers, or the window is closed.
        popup_journal("BUY", title, message)

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
        # ── THE DEATH FLOOR (Nov 11): the deterministic refusal. At/below the
        #    halt line the book raises NO new risk at all — ahead of sizing,
        #    ahead of every gate, ahead of the bridge.
        try:
            _df_watch = _load_contest_watch() or {}
            _df_marks = {}
            for _df_pair in list(_df_watch):
                try:
                    _df_marks[_df_pair] = float(
                        (exchange.fetch_ticker(_df_pair) or {}).get("last") or 0.0)
                except Exception:
                    pass
            _df_msg = death_floor_refusal(
                asset, watch=_df_watch, marks=_df_marks)
        except Exception as _df_exc:
            logger.debug("death-floor mark snapshot unavailable: %s", _df_exc)
            _df_msg = death_floor_refusal(asset)
        if _df_msg:
            # ADVISORY ONLY (Nov 12, user rule: the operator makes the final
            # trade decision). The line is shown in the log and console; it is
            # not allowed to bury the signal by itself.
            logger.warning("%s (advisory — the signal still lands)", _df_msg)
        sizing = compute_position_sizing(signal, cfg)
        is_buy = signal.action == "buy"

        # ── LONG-ONLY mandate: the tournament hunts bull runs. Short signals
        # never alert and never reach the bridge — we never short an asset.
        if signal.action != "buy":
            logger.info("  %s %s signal suppressed — longs only (never short)", asset, signal.action.upper())
            return

        # ── THE EDGE REGISTRY (mission, Nov 12): the ONLY research-to-live
        #    route. With no promoted policy this returns inactive immediately
        #    and changes nothing. With one, the scanner takes that policy's
        #    signals and nothing else; a demoted/retired row stops matching on
        #    the very next scan because the registry is read here every time.
        _edge_plan = {"active": False, "allow": True, "size_frac": None}
        if is_buy:
            try:
                import edge_live as _edge_live
                try:
                    import contest_account as _ca_edge
                    _edge_acct = _ca_edge.load_state()
                except Exception:
                    _edge_acct = None
                _edge_plan = _edge_live.live_plan(
                    pair, signal, exchange=exchange, cfg=cfg,
                    account=_edge_acct)
                if _edge_plan.get("active"):
                    logger.info("  EDGE POLICY: %s — %s%s",
                                _edge_plan.get("policy"),
                                "ALLOW" if _edge_plan.get("allow")
                                else "REFUSE",
                                (" (" + _edge_plan.get("reason", "") + ")")
                                if _edge_plan.get("reason") else "")
                if _edge_plan.get("active") and not _edge_plan.get("allow"):
                    _log_suppression(
                        pair, "edge-policy",
                        _edge_plan.get("reason") or "promoted policy did not match",
                        quick=float(signal.confidence))
                    return
            except Exception as _edge_exc:
                # fail-open is the no-promotion contract: a broken research
                # sidecar must not silently disable the scanner or authorize
                # a non-registry policy trade.
                logger.warning("edge registry gate unavailable for %s (%s) — "
                               "the normal scanner path continues", pair,
                               _edge_exc)
                _edge_plan = {"active": False, "allow": True,
                              "size_frac": None}

        # ── ROTATION MEMORY (user rule, Aug 30) ─────────────────────────
        # 1. LOCKOUT — RETIRED (user rule, Sep 10: "delete the rule"): the
        #    one-green-per-rotation cap is OFF; a coin can fire again the
        #    moment it passes every remaining gate. The call stays for the
        #    re-entry display + the stale-move exemption; nothing returns
        #    on it.
        _allowed, _lock_reason, _is_reentry, _fine = rotation_gate(asset)
        # ── RE-ENTRY AWARENESS REFERENCES RCL (user spec, Sep 22): while
        # a fresh drift verdict stands, a re-entry candidate is NOT
        # offered — reality moved, the calibration is behind. Fail-open:
        # the layer being down never blocks a setup.
        if _is_reentry:
            try:
                import meta_layers as _mlr
                # The candidate's PAIR is bound for the bounded re-ask
                # (the Oct 1 quick-fix: a stale MILD re-measures reality
                # on the asset asking to re-enter, not on a default).
                _blocked = (_mlr.reentry_blocked_for(pair)
                            if hasattr(_mlr, "reentry_blocked_for")
                            else _mlr.reentry_blocked())
                if _blocked:
                    _mlr.note_reentry_block()
                    # THE UNIFIED RULE (user decree, Oct 9): the RCL
                    # drift tag is CALIBRATION HUMILITY, not a wall —
                    # a resumable trade (the PUMP shape: exit closed a
                    # min-size runner, trend resumes) re-fires at
                    # minimum size, marked. Nothing blocks QC.
                    logger.info("  RCL DRIFT: %s re-entry rides minimum size — "
                                "the calibration is behind but the unified "
                                "rule never blocks", asset)
                    _rcl_weaken = True
                if getattr(_mlr, "reentry_override_pair", lambda: None)() == pair:
                    # The override covers this pass (kept for its window:
                    # BOTH downstream paths — the popup gate AND the mover
                    # route's re-check — must stay allowed for the same
                    # signal, so clear-on-first-use would let the second
                    # check suppress what the first allowed; the override
                    # still dies with its rotation / 4h cap, and every
                    # allowed pass is logged).
                    logger.info("  RCL OVERRIDE: %s re-entry allowed by "
                                "operator override (recorded)", pair)
            except Exception as exc:
                logger.debug("re-entry gate check skipped: %s", exc)
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
                            # ── THE CALIBRATED RETUNE (Sep 30, the
                            # per-gate study): dial-gated, quick >= the
                            # study's floor, capped, fail-closed. The
                            # fill model's floor rides in the number
                            # itself: 66 quick prices a ~3% ride, and
                            # 3% x the 0.35 capture floor clears the
                            # 0.52% round trip. Opens the TIDE gate
                            # only — the deep read still owes its
                            # verdict downstream.
                            _rrel, _rrel_why = _tide_retune_releases(
                                signal.confidence, pair)
                            if _rrel:
                                _bot_out_priced_pass = True
                                _bot_out_pbar = _tide_retune_dial()["quick_min"]
                                logger.info("  OUT-TIDE RETUNE PASS: %s — quick %.1f — %s (the deep read still owes its verdict)",
                                            asset, float(signal.confidence), _rrel_why)
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
                # entry downstream. THE DIRECTION VETO IS GONE (user
                # rule, Oct 9: "delete the deep-veto gate — if the QC
                # will pass the deep scan then don't leave money on the
                # table"): a SELL/HOLD deep read no longer blocks the
                # bridge write. The bot's own reader gates (ladder,
                # head-to-head, carry cap) stay the real-money
                # protection, fully intact.
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
        # ── THE BRIDGE WRITE STANDS BEHIND THE FIRE DECISION (Nov 6, the
        #    cascade review's ordering gap): a BUY signal used to hit the
        #    bridge HERE — hundreds of lines before the contest book judged
        #    the fire — so a fire the book refused at the popup had already
        #    reached the bot. One decision, THEN both side effects: the BUY
        #    write now runs below the strict-fire gate. (The SELL write
        #    keeps its place and shape untouched — exits are a different
        #    contract and the bot already holds the position.)
        def _bridge_write_now(action) -> bool:
            """The one bridge write. Returns the bridge's own verdict
            (duplicate / full / limits may still decline it). Never
            raises: a dead bridge must not cost the contest its fire."""
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
                # GO BIG keeps the strategy's own take target (momentum
                # decides the exit) but prices the bridge stop at exactly
                # $30 net — the old percentage stop would risk hundreds.
                _b_stop, _b_take, _b_rr = (signal.stop_loss,
                                           signal.take_profit,
                                           signal.risk_reward)
                if _go_big:
                    try:
                        _b_entry = float(signal.entry_price or 0.0)
                        _b_size = float(_spend or 0.0)
                        if _b_entry > 0.0 and _b_size > 0.0:
                            _b_stop = _b_entry * (1.0 - 30.0 / _b_size)
                    except (TypeError, ValueError):
                        pass
                _written = write_buy_signal(
                    pair=pair, action=action,
                    # the BRAIN'S number (deep-read confidence when we have
                    # it): the bot's entry-conviction scoring and the QC
                    # should never disagree about what this signal was worth
                    confidence=(float(_deep.confidence) if _deep is not None
                                else float(signal.confidence)),
                    entry_price=signal.entry_price,
                    stop_loss=_b_stop,
                    take_profit=_b_take,
                    risk_reward=_b_rr,
                    position_size_usd=(float(_spend) if _go_big
                                       else sizing.position_size_usd if sizing
                                       else None),
                    reasons=(["GO BIG: 100% notional, momentum exit, $30 net stop",
                              *signal.reasons] if _go_big else signal.reasons),
                    source="scout_go_big" if _go_big else "scout_auto",
                    min_confidence=0,  # Already gated in send_alert — skip redundant check
                    tide_state=_tide_stamp,
                    tide_pct=_tpw,
                )
                if _written:
                    logger.info("📡 Signal written to bridge for XRP bot: %s %s (%.0f%%)", action.upper(), asset, signal.confidence)
                else:
                    logger.info("  Signal not written (duplicate or already pending)")
                return bool(_written)
            except Exception as exc:
                logger.warning("Could not write to signal bridge: %s", exc)
                return False

        signal_written = False
        if not is_buy and not gate_notes:
            signal_written = _bridge_write_now(signal.action)
        elif gate_notes:
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
        # ── RCL BORDERLINE SUPPRESSION (user spec, Sep 22): major drift
        # raises the popup floor 10 points — borderline signals die while
        # reality disagrees with the calibration (fail-open).
        try:
            import meta_layers as _mlf
            if _mlf.endgame_active():
                DESKTOP_ALERT_MIN = min(DESKTOP_ALERT_MIN + 10, 99)
                logger.info("  RCL END-GAME: the popup floor rises to %d "
                            "while the drift stands", DESKTOP_ALERT_MIN)
        except Exception as exc:
            logger.debug("endgame floor check skipped: %s", exc)
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
                    # THE DEEP DIRECTION VETO IS GONE (user rule, Oct 9:
                    # "delete the deep-veto gate"): a SELL/HOLD deep read
                    # no longer silences the popup — the brain's NUMBER
                    # still SIZES the entry (any direction), and the
                    # desk gates upstream (floor, tide, stale, book)
                    # carry the risk. A contradicting read is logged to
                    # the ledger (forensics) but the fire lands.
                    if _deep.action != "buy":
                        logger.info("  ADVISORY CONTRA (note): %s deep-reads "
                                    "%s %.0f%% — logged, the fire lands (the "
                                    "deep-veto gate is deleted)",
                                    asset, _deep.action, _bc)
                        _log_suppression(pair, "deep-contranote",
                                         f"deep-reads {_deep.action} {_bc:.0f}% "
                                         f"against the buy — logged only (the "
                                         f"deep-veto gate is deleted; the fire lands)",
                                         quick=float(signal.confidence), brain=_bc,
                                         floor=float(DESKTOP_ALERT_MIN),
                                         tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                         rot=_rot_now_key)
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
        # only solid trends"; Sep 21: judged on EVERY candidate — "it was
        # replaced with real data already", no re-entry class): a move
        # whose peak was hours ago is a sagging top, not a trend (APT:
        # peaked 08:15, the 12:11 chase fired at 100% and sank). Pure
        # chart data: 4h-high age + distance. One standard for all.
        if True:
            try:
                _sm_rel = False
                _st = _stale_move(exchange, pair)
                if _st:
                    # ── THE JOURNAL RELEASE (Oct 8): the STRANDED gate —
                    # the journal has carried stale-move relief since the
                    # release-fuel strip rendered it display-only (app.py)
                    # while both wiggle-trap sites hard-returned on the APT
                    # lesson. The fuel spends in the deep-veto shape:
                    # HOLD-only deep gate (a SELL always stands; no deep
                    # read in hand releases nothing — the release never
                    # buys a brain read), quick within SEAM_RELEASE_SLACK
                    # of the LIVE popup floor, fail-closed on a broken
                    # journal.
                    _sm_pts = 0.0
                    if _deep is not None and _deep.action == "hold":
                        try:
                            import discipline as _dc_sm
                            _sm_pts = _dc_sm.journal_release_pts("stale-move")
                            if (_sm_pts > 0.0
                                    and float(signal.confidence)
                                    + _dc_sm.SEAM_RELEASE_SLACK
                                    >= float(DESKTOP_ALERT_MIN)):
                                _sm_rel = True
                        except Exception:
                            _sm_rel = False
                    if _sm_rel:
                        _mark_release(pair, "stale-move")
                        # THE STALE-RELEASE TRIPWIRE (tripwire A's shape):
                        # the first spend of this wire pings the phone.
                        _note_release_alert(
                            pair, "stale-move", _sm_pts,
                            quick=float(signal.confidence),
                            floor=float(DESKTOP_ALERT_MIN))
                        logger.info(
                            "  JOURNAL RELEASE (desktop): %s — 4h high %.0fh ago, "
                            "%.1f%% below it — quick %.0f%% within %s pts of the floor "
                            "%.0f%%; the journal shows stale-move burying champion-grade "
                            "payers (relief %.1f pts) — released",
                            asset, _st[0], _st[1], float(signal.confidence),
                            _dc_sm.SEAM_RELEASE_SLACK, float(DESKTOP_ALERT_MIN),
                            _sm_pts)
                    else:
                        # THE STALE-MOVE VERDICT IS A RE-SCAN NOW (user rule,
                        # Oct 9: "if a stale-move happens then get a new
                        # scan to pass or fail"): the stale shape no longer
                        # SILENTLY buries the candidate — a one-shot FRESH
                        # deep read decides RIGHT NOW. A buy-direction
                        # fresh read releases the fire (the move may be
                        # sagging but the desk-grade scan says go); any
                        # other read stands as the honest fail — and the
                        # popup still does NOT fire (the fail is REAL,
                        # not silence). The fail lands in the ledger so
                        # the candidate is visible as judged, not hidden.
                        _sm_deeper = None
                        try:
                            if _deep is None:
                                _deep, _df = _do_deep_read()
                            elif float(getattr(_deep, "ts", 0) or 0) < time.time() - 60:
                                _deep, _df = _do_deep_read()   # a >60s-old read is not fresh
                            _sm_deeper = _deep
                        except Exception:
                            _sm_deeper = None
                        _sm_pass = (_sm_deeper is not None
                                    and _sm_deeper.action == "buy")
                        if _sm_pass:
                            _mark_release(pair, "stale-move")
                            logger.info(
                                "  STALE-MOVE RE-SCAN PASS: %s — 4h high %.0fh ago, "
                                "%.1f%% below it — the FRESH deep read says BUY %.0f%%; "
                                "the re-scan pass releases the fire",
                                asset, _st[0], _st[1], _sm_deeper.confidence)
                        else:
                            _sm_why = ("no fresh read" if _sm_deeper is None
                                       else f"deep reads {_sm_deeper.action} "
                                            f"{_sm_deeper.confidence:.0f}%")
                            logger.info("  STALE MOVE: %s — 4h high %.0fh ago, %.1f%% below it "
                                        "— the fresh re-scan FAILED (%s) — the popup STANDS DOWN "
                                        "(the fail is real, not silence)",
                                        asset, _st[0], _st[1], _sm_why)
                            _log_suppression(pair, "stale-move",
                                             f"stale: 4h high {_st[0]:.0f}h ago, {_st[1]:.1f}% below it — the "
                                             "fresh re-scan FAILED ("+
                                             ("no fresh read" if _sm_deeper is None else
                                              f"deep reads {_sm_deeper.action} {_sm_deeper.confidence:.0f}%")+
                                             ") — the fail is REAL, not silence",
                                             quick=float(signal.confidence),
                                             brain=(float(_deep.confidence) if _deep is not None else None),
                                             tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                             rot=_rot_now_key)
                            return
            except Exception as exc:
                _sm_rel = False
                # THE SAGGING-TOP RISK: a stale-move read that failed
                # lets a possibly-dead re-entry through — the guard's
                # absence must be visible against the APT lesson
                logger.warning("stale-move guard read failed for %s (%s) "
                               "— this re-entry popup ships UNGUARDED", pair, exc)

        # The signal already PASSED the contest criteria (champion + checklist,
        # checked upstream). Why the bot didn't take it gets spelled out in
        # plain English so the user can judge the call themselves — composed
        # WITH the write (Nov 6): the note describes the bridge's own verdict,
        # so it cannot be written before the bridge has given one.

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
        # ── THE HOLD WINDOW IS A FUNNEL INPUT (Nov 7): the armed lesson caps
        #    how long a fire may be held, and a plan is goal x 60 at the
        #    engine's 1%/hour. A goal the cap cannot reach plans a fire the
        #    mandate is ARITHMETICALLY certain to refuse — so the funnel
        #    resolves it HERE, before a dollar is sized and before a popup is
        #    built, instead of letting the gate discover it after all the
        #    work (five of five fires one morning: worth, size, stress and
        #    book all passed, and every one died on a 105-162 minute plan).
        #    The goal is capped to what the window can drain; when even the
        #    engine's own goal floor does not fit, the candidate is skipped at
        #    scan time and says so. The cap itself is NEVER relaxed — only the
        #    target is made reachable inside it.
        _pace_pct = None
        _plan_basis = ""
        _win_min = None
        if is_buy:
            try:
                # THE CHAMPION CLOCK (Nov 7): one call, so the live scan and
                # the test drive the same cap / pace / plan arithmetic.
                _dec = _goal_clock_decide(asset=asset, pair=pair,
                                          exchange=exchange,
                                          goal_pct=_goal_pct,
                                          hold_cap_min=_hold_window_cap())
                if _dec.get("skipped"):
                    return
                _goal_pct = float(_dec["goal_pct"])
                _win_min = float(_dec["window_min"])
                _pace_pct = _dec.get("pace_pct_per_hour")
                _plan_basis = _dec.get("basis") or ""
            except Exception as _fu_exc:
                logger.debug("hold-window goal cap skipped: %s", _fu_exc)
        if _win_min is None:
            # not a buy, or the clock could not read: the plan is STILL the
            # goal's horizon at the 1%/h assumption, never the block clock
            _win_min = _goal_plan_min(_goal_pct, pace_pct_per_hour=_pace_pct)
        if not _plan_basis:
            _plan_basis = _pace_basis_label(_pace_pct)
        _out_by = (_now_mst() + timedelta(minutes=round(_win_min / 5.0) * 5)).strftime("%I:%M").lstrip("0")
        _hold_lbl = (f"{_win_min:,.0f} min" if _win_min < 120.0
                     else f"{_win_min / 60.0:.0f}h")
        hold_txt = f"+{_goal_pct:.1f}% in ~{_hold_lbl}"
        # the SAME basis string the fire records below — a log line and a
        # watch row that disagree are how a plan goes unrecorded
        logger.info("  GOAL CLOCK: %s — goal +%.1f%% (engine expected move, capped by "
                    "the asset's week), plan %s (%s), %.0f min to the "
                    "rotation boundary, out by %s",
                    asset, _goal_pct, _hold_lbl,
                    _plan_basis,
                    _rotation_window_min(), _out_by)

        # ── THE ECONOMIC CONTRACT (Nov 12): no new risk without a positive,
        # fee-aware edge and a payoff that clears its own stop. This is not a
        # pause: the scanner keeps scanning and the exits keep working; it is
        # a refusal to pay tuition on a negative-expectancy fire. The governor
        # and the console read the same verdict.
        _econ_plan = None
        _econ_probe = False
        if is_buy:
            try:
                import strategy_economics as _econ_g
                import contest_account as _ca_g
                _g_acct = _ca_g.load_state()
                _g_bal = float(_g_acct.get("current_balance") or _bal or 0.0)
                _g_win = float(_ca_g.win_line(_g_acct))
                _g_remaining = max(0.0, _g_win - _g_bal)
                # No deadline: the target is progressive. Do not turn a
                # seven-day checkpoint into a false refusal of a positive
                # edge; only the edge and reward/risk gates apply.
                _g_days = 0.0
                try:
                    _g_cap = float(_ca_g.max_entry_usd(
                        _g_bal, _ca_g.window_drawdown_pct(pair),
                        float(_g_acct.get("start_balance") or _g_bal)))
                except Exception:
                    _g_cap = None
                _g_rows = _ca_g._rolling_trade_rows()
                _g_cadence = _econ_g.recent_cadence(_g_rows)
                _g_stop = 2.5
                try:
                    _g_entry = float(signal.entry_price or 0.0)
                    _g_stop_px = float(signal.stop_loss or 0.0)
                    if _g_entry > 0 and _g_stop_px > 0:
                        _g_stop = abs(_g_entry - _g_stop_px) / _g_entry * 100.0
                except (TypeError, ValueError):
                    pass
                _econ_plan = _econ_g.assess(
                    _g_rows, remaining_usd=_g_remaining, days_left=_g_days,
                    cadence=_g_cadence, goal_pct=_goal_pct,
                    stop_pct=_g_stop, max_size_usd=_g_cap,
                    max_cadence=float(MAX_FIRES_PER_DAY))
                if not _econ_plan.get("allow_new_risk"):
                    if _econ_plan.get("allow_probe"):
                        # LIVE PROBE: the edge is still negative, so this is
                        # not full size. The mistake-memory gate below still
                        # hard-blocks a context that repeated its loss.
                        _econ_probe = True
                        logger.warning(
                            "  ECONOMICS: %s — LIVE PROBE $%s, not full "
                            "risk; edge %s/close, win %.1f%%, payoff %s, "
                            "R:R %s",
                            _econ_plan.get("verdict"),
                            _econ_plan.get("probe_size_usd"),
                            (_econ_plan.get("edge") or {}).get("mean"),
                            100.0 * float((_econ_plan.get("edge") or {}).get("win_rate") or 0.0),
                            (_econ_plan.get("edge") or {}).get("payoff"),
                            _econ_plan.get("reward_risk"))
                    else:
                        # WATCH-ONLY (Nov 12, user rule: "it should not block
                        # any signals from getting in but it should watch
                        # closely the exit").  A negative-edge verdict no
                        # longer buries the signal: it lands at a small
                        # watch-only size, stamped PROBE, and the live exit
                        # monitor owns the downside.  The verdict is still
                        # spoken in full so the size is never mistaken for a
                        # full-risk call.
                        _econ_probe = True
                        _watch_cap = max(25.0, min(
                            float(_bal or 0.0) * 0.01, 100.0))
                        _econ_plan = dict(_econ_plan)
                        _econ_plan["probe_size_usd"] = _watch_cap
                        logger.warning(
                            "  ECONOMICS ADVISORY: %s %s — signal lands "
                            "(watch-only base $%.0f; %s; the exit monitor "
                            "owns the risk) — edge %s/close, win %.1f%%, "
                            "payoff %s, R:R %s",
                            asset, _econ_plan.get("verdict"), _watch_cap,
                            "; ".join(_econ_plan.get("reasons") or []),
                            (_econ_plan.get("edge") or {}).get("mean"),
                            100.0 * float((_econ_plan.get("edge") or {}).get("win_rate") or 0.0),
                            (_econ_plan.get("edge") or {}).get("payoff"),
                            _econ_plan.get("reward_risk"))
                        _log_suppression(
                            pair, "negative-expectancy-advisory",
                            f"economics watch-only: {_econ_plan.get('verdict')}; "
                            + "; ".join(_econ_plan.get("reasons") or []),
                            quick=float(signal.confidence),
                            rot=rotation_key_for_hour(_now_mst().hour))
                if _econ_plan.get("allow_new_risk"):
                    logger.info(
                        "  ECONOMICS: %s — full risk allowed; edge %s/close, "
                        "required size %s vs cap %s",
                        _econ_plan.get("verdict"),
                        (_econ_plan.get("edge") or {}).get("mean"),
                        _econ_plan.get("required_size_usd"),
                        _econ_plan.get("max_size_usd"))
            except Exception as _econ_exc:
                # A broken economics read must not silently authorize risk.
                logger.warning("  ECONOMICS unavailable (%s) — no fire",
                               _econ_exc)
                return

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
            _out_tide_weakened = False  # adversity landing (unified-rule min-size)
            _knife_weaken = False       # the EMA knife (unified-rule min-size)
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
                                _mark_release(pair, "tide-out")
                                logger.info("  OUT-TIDE JOURNAL RELEASE: %s — quick %.0f, brain %.0f — %s",
                                            asset, float(signal.confidence),
                                            float(getattr(_deep, "confidence", 0) or 0), _rel_why)
                            else:
                                # ── THE CALIBRATED RETUNE (the same
                                # valve the bot path rides): the study
                                # said the wall buried quick-66+ payers
                                # the champion seam's 92 never reached;
                                # the dial decides, the cap bounds it,
                                # the brain still rules downstream.
                                _rrel, _rrel_why = _tide_retune_releases(
                                    signal.confidence, pair)
                                if _rrel:
                                    _out_tide_learned = True  # rides the marked 'against the tide' path
                                    _mark_release(pair, "tide-out-retune")
                                    logger.info("  OUT-TIDE RETUNE RELEASE: %s — quick %.0f — %s (the brain still owes its verdict)",
                                                asset, float(signal.confidence), _rrel_why)
                                else:
                                    # THE UNIFIED RULE (user decree, Oct 9):
                                    # "If QC says BUY -> fire. If anything
                                    # is weak -> fire minimum size and
                                    # clearly display the amount + the
                                    # time to target. Nothing blocks QC."
                                    # The OUT tide is ADVERSITY, not a
                                    # veto: the fire lands at the
                                    # minimum-viable size, marked
                                    # 'against the tide' — the exit
                                    # watch already owns the risk on the
                                    # same terms as every fire.
                                    _out_tide_weakened = True   # the unified-rule choke min-sizes this
                                    _out_tide_learned = True    # rides the marked 'against the tide' path
                                    logger.info("  OUT TIDE ADVERSITY: %s — the fire LANDS at minimum size "
                                                "(the unified rule: QC never blocked)", asset)
                                    logger.info("  TIDE OUT (fleet %.0f%%) — the fire rides min-size "
                                                 "(journal: %s; retune: %s)",
                                                 _df_pct or 0.0, _rel_why, _rrel_why)
                # The brain rules EVERY alert — whether this stage read it
                # or the near-miss consult already did (one read, cached).
                if _deep is None:
                    _deep, _df = _do_deep_read()
                if _deep is not None:
                    # THE DEEP-VETO DIRECTION GATE IS GONE (user rule,
                    # Oct 9): a SELL/HOLD read is forensics, not a wall —
                    # the read is logged and the fire proceeds. The
                    # upstream gates (floor, tide, book, stale re-scan)
                    # carry the risk.
                    if _deep.action != "buy":
                        logger.info("  DEEP CONTRA (note): %s deep-reads %s %.0f%% "
                                    "— logged, the fire lands", asset,
                                    _deep.action, _deep.confidence)
                        _log_suppression(pair, "deep-contranote",
                                         f"deep-reads {_deep.action} {_deep.confidence:.0f}% "
                                         f"against the buy — logged only (the deep-veto "
                                         f"gate is deleted; the fire lands)",
                                         quick=float(signal.confidence),
                                         brain=float(_deep.confidence),
                                         floor=float(DESKTOP_ALERT_MIN),
                                         tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                         rot=_rot_now_key)
            except Exception as _exc:
                logger.debug("desktop pre-check unavailable for %s: %s (alerting anyway — QC still gates)", asset, _exc)

        # ── THE ENTRY TREND GATE (Nov 10) — OUTSIDE the pre-check's try ────
        # It used to sit INSIDE that try and inside `if _deep is not None:`,
        # so a deep read returning None (or any raise in the block, whose
        # handler says "alerting anyway") let a broken-trend fire land
        # untested. The gate now runs for every buy, reads its own 1h frame
        # when the deep read never produced one, and fails closed.
        if is_buy:
            _kv, _knote = entry_knife_verdict(
                asset=asset, pair=pair, cfg=cfg,
                df=locals().get("_df"),
                floor=locals().get("DESKTOP_ALERT_MIN"),
                signal=signal, deep=locals().get("_deep"),
                df_state=locals().get("_df_state"),
                df_pct=locals().get("_df_pct"),
                rot=locals().get("_rot_now_key"),
                fetch_1h=(lambda: exchange.fetch_ohlcv(pair, timeframe="1h",
                                                       limit=100)))
            if _kv == "block":
                return
            if _kv == "weaken":
                _knife_weaken = True

        # (the goal clock above set hold_txt + _out_by; the $20 patience
        # ladder and its "up twenty dollars" phrasing retired with the
        # scalp game — Sep 1)
        _how_long = (f"goal +{_goal_pct:.1f}% by {_out_by} — stay close "
                     f"after that; the exit is hers, never the clock.")
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
        try:
            _size_frac = min(max(float(getattr(cfg, "contest_size_frac", 1.0) or 1.0), 0.0), 1.0)
        except Exception:
            _size_frac = 1.0
        _spend = configured_base_spend(_bal, cfg)
        _go_big = False          # set when coin + rotation support all-in
        _go_big_why = ""
        _carry = None
        try:
            _depth = float(snap.get("book", {}).get("ask_tob") or 0.0)
            if _depth > 0:
                _carry = _depth * 0.20
                if _spend > _carry:
                    _fit = [c * _spend for c in (0.25, 0.50, 0.75, 1.00)
                            if c * _spend <= _carry]
                    # THE MINIMUM-VIABLE FLOOR (the JUP bump, Oct 9: a $60
                    # top-of-book zeroed the spend, and the worth floor
                    # then bumped a 72% QC pick for "paying $0" — the
                    # Oct 9 too-thin retirement missed this one line).
                    # A thin book sizes DOWN but never to zero: the
                    # tranche floor is the same 0.10x minimum the brain
                    # scaler uses. The buy is at-market; the book fills
                    # what it fills.
                    _spend = max(_fit) if _fit else _spend * 0.10
        except Exception:
            _carry = None
        # THE TOO-THIN GATE IS GONE (user rule, Oct 9: "delete the too-thin
        # gate; make the recommended spend amount more noticeable"). The
        # thin book no longer SILENCES the popup — the sized spend (even
        # a 0.10x minimum viable) IS the notice: the book carry still
        # SIZES the entry down, but the call always gets heard and the
        # SPEND line carries the money up front (the dopamine line).
        if _carry is not None and _spend < (float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)):
            contest_msg = (f"  SPEND: {_spend / (float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)):.0%} = "
                           f"${_spend:,.0f} — the book will carry it: ${_carry:,.0f} "
                           f"(20% of the visible book — small coin)\n")
        elif _carry is not None:
            contest_msg = (f"  SPEND: 100% = ${_bal:,.0f} — big book, will carry it: "
                           f"${_carry:,.0f} (20% of the visible book)\n")
        # ── THE 3% DEATH-LINE GUARD (Kraken Funded rules, encoded Sep 1 —
        # the drift audit found the -3% line was never modeled): the
        # contest account's real state (tier + current balance from the
        # console) sets the sizing base, and the entry is capped so the
        # coin's WORST NORMAL CASE (its profiled window drawdown) eats at
        # most ~1/3 of the remaining buffer. Buffer gone → NO buy popup
        # at all — the account is at the line and any trade can close it.
        _hail_mary = False   # set inside the guard below (safe default)
        _hail_mary_mode = ""   # "" until a window opens (mode-aware marks)
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
            _hail_mary_mode = (str(_acct.get("hail_mary_mode") or "classic")
                               if _hail_mary else "")
            if not _hail_mary and _risk_cap > 0:
                _hm_ok, _hm_why = _ca.hail_mary_available(_acct)
                _hm_bal = (float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance))
                if _hm_ok and not _challenger:
                    _hm_q, _hm_qwhy = _hail_mary_qualifies(
                        deep=_deep, df=_df, carry=_carry,
                        balance=_hm_bal,
                        floor=float(DESKTOP_ALERT_MIN),
                        ema_short=cfg.ema_short, ema_long=cfg.ema_long)
                    if _hm_q:
                        _acct = _ca.activate_hail_mary(_acct)
                        _hail_mary = True
                        _hail_mary_mode = "classic"
                        # the trade window: one hour, not the rotation
                        _win_min = min(_win_min, float(_ca.HAIL_MARY_WINDOW_MIN))
                        _out_by = (_now_mst() + timedelta(
                            minutes=round(_win_min / 5.0) * 5)).strftime("%I:%M").lstrip("0")
                        _how_long = (f"goal +{_goal_pct:.1f}% by {_out_by} — stay "
                                     f"close after that; the exit is hers, never the clock.")
                        logger.info("  💪 HAIL MARY ACTIVATED: %s — the emergency hour "
                                    "runs; the death line stands; cap $%.0f",
                                    _hm_qwhy, _risk_cap)
                    else:
                        # ── THE OPPORTUNISTIC DOOR (user ask, Oct 1): the
                        # champion-grade bar declined this setup, but the
                        # account still sits under break-even — the mode
                        # seeks ANY VIABLE UPSIDE under STRICTER risk: the
                        # deep-scan buy read IS the entry authority (this
                        # very read feeds the exit watch), a $300 hard
                        # spend cap rides under the guard's cap, and the
                        # watch row stamps hm_mode="opportunistic" so the
                        # exit watch judges a 2% hard stop. Classic first;
                        # the opp door opens ONLY when the champion bar
                        # says no. One window per rotation, shared.
                        _op_q, _op_why = _hail_mary_opp_qualifies(
                            deep=_deep, df=_df, carry=_carry,
                            balance=float(_acct["start_balance"]),
                            floor=_ca.HAIL_MARY_OPP_FLOOR)
                        if _op_q:
                            _acct = _ca.activate_hail_mary(_acct, mode="opportunistic")
                            _hail_mary = True
                            _hail_mary_mode = "opportunistic"
                            _spend = min(float(_spend), float(_risk_cap),
                                         float(_ca.OPP_HARD_SPEND_USD))
                            contest_msg = (f"  SPEND: ${_spend:,.0f} — OPPORTUNISTIC "
                                           f"HAIL MARY: ${_ca.OPP_HARD_SPEND_USD:,.0f} hard "
                                           f"cap under the guard's ${_risk_cap:,.0f}\n")
                            # the trade window: one hour, not the rotation
                            _win_min = min(_win_min, float(_ca.HAIL_MARY_WINDOW_MIN))
                            _out_by = (_now_mst() + timedelta(
                                minutes=round(_win_min / 5.0) * 5)).strftime("%I:%M").lstrip("0")
                            _how_long = (f"goal +{_goal_pct:.1f}% by {_out_by} — stay "
                                         f"close after that; the exit is hers, never the clock.")
                            logger.info("  💪 OPPORTUNISTIC HAIL MARY ACTIVATED: %s — "
                                        "cap $%.0f, hard stop %.1f%%, the exit watch judges",
                                        _op_why, _spend, _ca.HAIL_MARY_OPP_HARD_STOP_PCT)
                        else:
                            logger.info("  Hail Mary declined — %s; opportunistic "
                                        "declined — %s", _hm_qwhy, _op_why)
                elif _hm_ok and _challenger:
                    logger.info("  Hail Mary declined — a challenger never "
                                "gets the emergency window")
                else:
                    logger.debug("  Hail Mary unavailable — %s", _hm_why)
            if _risk_cap <= 0:
                # WATCH-ONLY (Nov 12): the floor no longer buries the signal.
                # The popup lands at the smallest viable size; the real-time
                # exit monitor is what owns the risk from here.  The account's
                # arithmetic is still spoken so the size is never mistaken for
                # a full-risk call.
                _floor_fl = _ca.death_line(_acct)
                _spend = max(10.0, min(float(_spend or 0.0) * 0.10,
                                       max(float(_acct_bal) * 0.02, 10.0)))
                contest_msg = (f"  DEATH-LINE WATCH: buffer gone (${_acct_bal - _floor_fl:,.0f} "
                               f"above the ${_floor_fl:,.2f} line) — signal still lands at "
                               f"MIN SIZE ${_spend:,.0f}; the exit monitor is live\n")
                logger.warning("  DEATH LINE WATCH: %s — buffer gone at $%.2f; "
                               "signal lands at min size $%.0f (entries are not vetoed)",
                               asset, _floor_fl, _spend)
                _log_suppression(pair, "death-line",
                                 f"watch-only: buffer gone; signal lands at min "
                                 f"size ${_spend:,.0f} — GET OUT monitor armed",
                                 quick=float(signal.confidence),
                                 brain=(float(_deep.confidence) if _deep is not None else None),
                                 floor=float(DESKTOP_ALERT_MIN),
                                 tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                 rot=_rot_now_key)
            elif _spend > _risk_cap:
                _spend = _risk_cap
                contest_msg = (f"  RISK CAP: ${_spend:,.0f} pre-sizing — THE 3% GUARD: this coin's worst-case "
                               f"window drawdown is {_wdd:.1f}%; ${_spend:,.0f} is the most the "
                               f"platform line can afford (the recommended spend is the title/ENTER line)\n")
        except Exception as _exc:
            logger.debug("3%% guard unavailable in send_alert: %s", _exc)  # fail-open
        try:
            logger.info("  SIZED SPEND: $%.2f = %.2f%% of $%.2f balance "
                        "(configured %.2f%%, 3%% guard cap $%.2f)",
                        _spend, (100.0 * _spend / float(_bal)) if _bal and _bal > 0 else 0.0,
                        float(_bal or 0.0), 100.0 * _size_frac,
                        float(locals().get("_risk_cap") or 0.0))
        except (TypeError, ValueError) as exc:
            # 9031: the SIZED SPEND line is a report of a decision already
            # made; a formatting failure is traced, never silent
            logger.debug("  SIZED SPEND line skipped: %s", exc)
        # ── THE SURVIVABILITY GUARD (Nov 10 — the enforced brake): the
        #    per-entry backtest showed the exits win 56.8% of starts and
        #    still touch the -3% line on 38.8% of them, because the death
        #    is a CLUSTER of losses in a bad regime, not one bad trade.
        #    survival_guard.entry_gate reads the pause a closed trade armed
        #    (persisted in survival_guard_state.json, so a restart cannot
        #    forget it) and either refuses the popup outright or hands back
        #    the post-reset PROBE multiplier. Fail-open like the 3% guard:
        #    a broken guard must not silence the scanner.
        try:
            import survival_guard as _sg
            _sg_gate = _sg.entry_gate(locals().get("_acct"))
            if _sg_gate.get("blocked"):
                logger.info("  SURVIVABILITY GUARD: %s — no buy popup",
                            _sg_gate.get("reason"))
                _log_suppression(pair, "survivability-guard",
                                 f"guard pause: {_sg_gate.get('reason')}",
                                 quick=float(signal.confidence),
                                 brain=(float(_deep.confidence) if _deep is not None else None),
                                 floor=float(DESKTOP_ALERT_MIN),
                                 tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                 rot=_rot_now_key)
                return
            _sg_mult = float(_sg_gate.get("size_multiplier") or 1.0)
            if _sg_mult < 1.0:
                _spend = _spend * _sg_mult
                logger.info("  SURVIVABILITY GUARD: probe size %.2fx → $%.0f "
                            "(first entries after a pause)", _sg_mult, _spend)
        except Exception as _exc:
            logger.debug("survivability guard unavailable in send_alert: %s", _exc)  # fail-open
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
                # RCL drift marker lives in _rcl_weaken; the min-floor
                # itself rides the unified choke at _base_spend below
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
        # ── THE MISTAKE-MEMORY PROBATION (Nov 12): the same context that
        #    keeps paying the same loss is sized down, not paused. This is
        #    what sharpens the blade: the loop still collects the next
        #    close, but the repeated mistake costs less while the table
        #    proves whether the context has turned. Fail-open: a missing
        #    table means full size, never a silent block.
        try:
            import overwatch_learner as _ol
            _learn_route = "champion-fast" if _fast_route else "champion"
            _learn_mult, _learn_why = _ol.size_multiplier(
                route=_learn_route, tide=_df_state,
                conviction=signal.confidence, goal_pct=_goal_pct)
            if _learn_mult <= 0.0:
                logger.info("  MISTAKE MEMORY: BLOCKED — %s", _learn_why)
                _log_suppression(pair, "mistake-memory", _learn_why,
                                 quick=float(signal.confidence),
                                 tide=(f"{_df_state} {_df_pct:.0f}%"
                                       if _df_pct is not None else _df_state))
                return
            if _learn_mult < 1.0:
                _spend = _spend * _learn_mult
                logger.info("  MISTAKE MEMORY: %.2fx -> $%.0f (%s)",
                            _learn_mult, _spend, _learn_why)
        except Exception as _learn_exc:
            logger.debug("mistake memory unavailable: %s", _learn_exc)
        # ── THE UNIFIED-RULE WEAKEN (user decree, Oct 9): "If QC says BUY
        #    -> fire. If anything is weak -> fire minimum size."
        #    Adversity is INFORMATION — the fire lands at min-viable and is
        #    marked plainly; it never returns. The min floor rides the
        #    same 0.10x the brain scaler uses, against the SIZED base
        #    (pre-brain: guard-cap, book tranche, tide are all in).
        _weak_fire_reason = ""
        if _econ_probe:
            _probe_cap = float(_econ_plan.get("probe_size_usd") or 0.0)
            if _probe_cap > 0 and _spend > _probe_cap:
                logger.info("  LIVE PROBE SIZE: $%.0f -> $%.0f (25%% of "
                            "the risk cap; full size refused)",
                            _spend, _probe_cap)
                _spend = _probe_cap
        _base_spend = _spend
        # ── THE TARGET TRANCHE: when the economic contract passes, size the
        #    entry to the pace the target actually needs — inside the 3%
        #    guard's ceiling, never above the live balance. The old worth
        #    floor only paid the $171/day bar; this pays the $199/day target
        #    or refuses the setup (the economics gate already did).
        try:
            if (_econ_plan and _econ_plan.get("allow_new_risk")
                    and _econ_plan.get("required_size_usd")):
                _target_spend = min(float(_econ_plan["required_size_usd"]),
                                    float(locals().get("_risk_cap") or 0.0),
                                    float(_bal or 0.0))
                if _target_spend > _base_spend:
                    logger.info("  TARGET TRANCHE: $%.0f → $%.0f — sizing "
                                "to the weekly pace (guard cap $%.0f)",
                                _base_spend, _target_spend,
                                float(locals().get("_risk_cap") or 0.0))
                    _base_spend = _spend = _target_spend
        except Exception as _tt_exc:
            logger.debug("target tranche skipped: %s", _tt_exc)
        # ── THE PROMOTED POLICY'S SIZE (mission, Nov 12): a 6% policy cannot
        #    ride the 9% configured ceiling; a 9% policy is exactly today's
        #    ceiling.  This cap is applied only when the registry is active,
        #    so the no-promotion path keeps its existing arithmetic.
        if (_edge_plan.get("active") and _edge_plan.get("allow")
                and _edge_plan.get("size_frac")):
            try:
                _edge_cap = max(10.0, float(_bal or 0.0)
                                * float(_edge_plan["size_frac"]))
                if _spend > _edge_cap:
                    logger.info("  EDGE SIZE: $%.0f → $%.0f (policy %.0f%%)",
                                _spend, _edge_cap,
                                100.0 * float(_edge_plan["size_frac"]))
                    _spend = _base_spend = _edge_cap
            except Exception as _edge_sz_exc:
                logger.debug("edge size cap skipped: %s", _edge_sz_exc)
        # ── THE TRANCHE PAYS THE BAR (Nov 7): the brain's confidence vote
        #    sizes the fire — it must not decide whether the fire is WORTH
        #    doing. A 0.10x read books a minimum-viable base ($330 under a
        #    $3,300 ceiling) and the worth floor then refuses it, because
        #    the $25 bar needs $1,710 at +1.5%: the funnel was sizing
        #    setups it had already decided not to trade. When a bar is set
        #    and the base cannot pay it, the tranche is raised to the
        #    paying minimum — never past the guard's own ceiling, the
        #    allowance or the book room, and never at all when the guard
        #    named no ceiling or the account cannot be read. Every later
        #    gate (gap stress, book cap, fires/day, the death mandate)
        #    still judges the raised fire in full.
        try:
            _pt = _paying_tranche(pair, _base_spend, _goal_pct,
                                  ceiling_usd=locals().get("_risk_cap"))
            if _pt is not None and _pt.get("ok") and _pt.get("raised"):
                logger.info("  TRANCHE RAISED: $%.0f → $%.0f — %s",
                            _base_spend, float(_pt["size"]), _pt["why"])
                _base_spend = _spend = float(_pt["size"])
            elif _pt is not None and not _pt.get("ok"):
                logger.info("  TRANCHE CANNOT PAY: %s", _pt.get("why"))
        except Exception as _pt_exc:
            logger.debug("tranche-to-pay skipped: %s", _pt_exc)
        _weak_min_usd = _base_spend * 0.10   # the MIN shown in the title
        _weak_paid = False                   # did the resize certify worth?
        try:
            _rcl_weaken_flag = _rcl_weaken
        except NameError:
            _rcl_weaken_flag = False

        def _min_size_weaken(gate: str, why: str):
            """Mark a fire as weakened and book the SMALLEST size that still
            pays the pace bar inside the death line's allowance (Nov 7: the
            10% clamp could never pay, so every weakened fire was buried by
            the worth floor two gates later). The 10% scale is applied first
            and only ever RAISED back up to the paid size — never above the
            fire's own sized base. When no size both pays and fits, the
            refusal is stated here, in full, and the fire keeps its floor."""
            nonlocal _spend, _weak_fire_reason, _weak_paid, _weak_min_usd
            _weak_fire_reason = f"{gate}: {why}"
            _weak_paid = False
            _spend = _weakened_spend(_spend, _base_spend)
            _ws = _weakened_fire_size(pair, _base_spend, _goal_pct)
            if _ws is None:
                _weak_min_usd = _base_spend * 0.10
                logger.info("  MIN-SIZE FIRE (%s): %s — spend clamped to $%.0f "
                            "(the account could not be read, so the floor "
                            "stands)", gate.upper(), why, _spend)
                return
            if not _ws.get("ok"):
                _weak_fire_reason = f"{gate}: {why}; {_ws.get('why')}"
                logger.info("  WEAKENED REFUSED (%s): %s — %s",
                            gate.upper(), why, _ws.get("why"))
                return
            _spend = min(_base_spend, max(_spend, float(_ws["size"])))
            _weak_min_usd = _spend
            _weak_paid = True
            logger.info("  WEAKENED SIZING (%s): %s — $%.0f booked: %s",
                        gate.upper(), why, _spend, _ws.get("why"))
        # OUT-tide adversity survivors (the block above no longer
        # returns): weakened, not suppressed — the earned passes
        # (lone wolf, learned bar, journal/retune release) fire FULL size
        if _out_tide_weakened:
            _min_size_weaken("out-tide", "fired against the OUT tide")
        if _knife_weaken:
            _min_size_weaken("knife", "downward trend (EMA9<EMA21, price<EMA21) — minimum size")
        if _rcl_weaken_flag:
            _min_size_weaken("rcl-drift", "re-entry while the calibration is behind — minimum size")
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
                                      _goal_pct,
                                      float(_spend or 0.0))
            if not _climb.get("ok"):
                _fg = _climb.get("fail_gate") or "?"
                logger.info("  CLIMB-OUT %s: %s gate — %s", asset, _fg,
                            _climb.get("fail_reason"))
                _log_suppression(pair, f"climb-{_fg}",
                                 f"{_climb.get('fail_reason')} — no clear "
                                 f"escape route (climb-out; the unified rule fires at min size)",
                                 quick=float(signal.confidence),
                                 brain=(float(_deep.confidence) if _deep is not None else None),
                                 floor=float(DESKTOP_ALERT_MIN),
                                 tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                 rot=_rot_now_key,
                                 climb=(_climb.get("features") or {}))
                # THE UNIFIED RULE (Oct 9): no escape route is ADVERSITY —
                # the fire lands at minimum size, marked
                _min_size_weaken(f"climb-{_fg}", _climb.get("fail_reason") or "no clear escape route")
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
        # (Oct 9) THE MINIMUM-VIABLE ENTRY WAIVES IT TOO: the sizing
        # doctrine says even a minimum entry fires; judging a
        # minimum-sized fire by a full-size pace bar re-buries every
        # thin-book pick the floor's own sizing created. The floor
        # judges what it was built to judge: full-size entries.
        if is_buy and not _fast_route and _spend > 0:
            try:
                import contest_account as _ca_w
                _acct_w = _ca_w.load_state()
                _pace_w = _ca_w.daily_pace_usd(_acct_w)
                _wf_w = _worth_floor_frac()  # the console dial, live
                _worth_w = float(_spend) * (float(_goal_pct) / 100.0)
                if not _ca_w.pays_the_bar(_worth_w, _pace_w * _wf_w):
                    # THE MERGED DIAL (user decree, Oct 9): worth-floor is no
                    # longer a veto — the popup fires at the weakened size with
                    # the recommended amount + time-to-target in the
                    # title/voice. (Nov 7) The size is the one that PAYS the
                    # bar, and the burial row states the size that was booked,
                    # never the small number the widen started from.
                    _min_size_weaken("worth-floor",
                                     f"a ${_spend:.0f} entry at +{_goal_pct:.1f}% pays "
                                     f"${_worth_w:.0f} (< ${_pace_w * _wf_w:.0f} pace bar) — sized to the bar")
                    _worth_w = float(_spend) * (float(_goal_pct) / 100.0)
                    _log_suppression(pair, "worth-floor",
                                     f"a ${_spend:.0f} entry at the +{_goal_pct:.1f}% goal pays "
                                     f"${_worth_w:.0f} < the ${_pace_w * _wf_w:.0f} floor "
                                     f"({_wf_w * 100.0:.0f}% of the daily pace; merged into the min-size rule Oct 9)",
                                     quick=float(signal.confidence),
                                     brain=(float(_deep.confidence) if _deep is not None else None),
                                     floor=float(DESKTOP_ALERT_MIN),
                                     tide=(f"{_df_state} {_df_pct:.0f}%" if _df_pct is not None else _df_state),
                                     rot=_rot_now_key)

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
                # RCL drift suppresses re-entry candidates here too (the
                # mover route): no popup while reality disagrees (fail-open)
                try:
                    import meta_layers as _mlr2
                    _blocked = (_mlr2.reentry_blocked_for(pair)
                                if hasattr(_mlr2, "reentry_blocked_for")
                                else _mlr2.reentry_blocked())
                    if _blocked:
                        _mlr2.note_reentry_block()
                        # unified rule (Oct 9): min-size inline — the
                        # earlier choke already ran, so weaken right here
                        _min_size_weaken("rcl-drift",
                                         "re-entry while the calibration is behind")
                        logger.info("  RCL DRIFT: %s re-entry rides "
                                    "minimum size — the unified rule "
                                    "never blocks", pair)
                    if getattr(_mlr2, "reentry_override_pair", lambda: None)() == pair:
                        logger.info("  RCL OVERRIDE: %s re-entry allowed by "
                                    "operator override (recorded, mover route)", pair)
                except Exception as exc:
                    logger.debug("re-entry suppression check skipped: %s", exc)
                # display only — the fine is retired (Sep 21): the same
                # real-data gates judged this fire as any other
                _reentry_line = "  RE-ENTRY: fired earlier today — the same real-data gates judged it\n"
                _reentry_say = "Re-entry! "
            # THE HAIL MARY MARK (user rule, Sep 4 — kept Sep 5): this
            # popup fired inside the one-hour emergency window — the
            # account-in-danger shot, the death line standing. Never
            # mistaken for an ordinary green.
            _hm_line = ""
            _hm_say = ""
            if _hail_mary:
                _hm_opp = (_hail_mary_mode == "opportunistic")
                try:
                    import contest_account as _ca_hm
                    _hm_left = _ca_hm.hail_mary_minutes_left(_acct)
                    _hm_line = (f"  💪 HAIL MARY: the emergency hour runs for "
                                f"{max(_hm_left, 0):.0f} more min — full allocation = the "
                                f"guard's cap vs the DEATH LINE (never the whole balance); "
                                f"the clock ends with the hour\n")
                    _hm_say = "Hail Mary! The emergency hour is open! "
                    if _hm_opp:
                        _hm_line = (f"  💪 OPPORTUNISTIC HAIL MARY: the window runs for "
                                    f"{max(_hm_left, 0):.0f} more min — capped at "
                                    f"${_ca_hm.OPP_HARD_SPEND_USD:,.0f}; the exit watch is "
                                    f"the authority and judges a "
                                    f"{_ca_hm.HAIL_MARY_OPP_HARD_STOP_PCT:.0f}% hard stop\n")
                        _hm_say = "Opportunistic hail mary! Small shot, tight stop! "
                except Exception:
                    _hm_line = ("  💪 HAIL MARY: the emergency hour runs — "
                                "the death line stands\n")
                    _hm_say = "Hail Mary! The emergency hour is open! "
            _small_coin = _carry is not None and _spend < (float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance))
            # THE MONEY IS THE HEADLINE (user rule, Oct 9): the recom-
            # mended spend leads the title — the dopamine dollar figure
            # the desk can act on at a glance, before ANY words follow.
            # THE MAX/MIN PAIR (user decree, Oct 10: "always display both
            # the maximum and minimum viable spend amounts"): MAX rides the
            # full sized base (pre-weaken), MIN is the 0.10x floor the brain
            # scaler already trades on - both live in the title + voice.
            _max_spend_say = (f"${_base_spend:,.0f}" if _base_spend > 0 else "")
            # (Nov 7) MIN is what a weakened fire ACTUALLY books — the paid
            # size the resize found, not a 10% floor that never pays.
            _min_spend_say = (f"${_weak_min_usd:,.0f}" if _base_spend > 0 else "")
            _title_spend = (f"MAX: ${_base_spend:,.0f} — MIN: ${_weak_min_usd:,.0f}"
                            if _base_spend > 0 else
                            "SIZE YOUR OWN — sizing unavailable")
            # THE UNIFIED-RULE MARK (user decree, Oct 9): a weakened fire
            # says WHY it rides small, right in the title.
            # (Nov 7) the mark no longer says "minimum size": the weakened
            # fire is booked at the size that PAYS the pace bar, and calling
            # that minimum would be the same label lie in the other direction.
            _weak_title = (f" — WEAKENED, SIZED TO PAY ({_weak_fire_reason.split(': ', 1)[-1].upper()})"
                           if _weak_fire_reason else "")
            title = (f"🟢 BUY {asset} {_title_spend}"
                     + f" — {_dc:.0f}% SURE"
                     + (f" — {_brain_mult:.2f}× SIZE" if _brain_mult < 1.0 else "")
                     + (" — FAST ROUTE" if _fast_route else "")
                     + (" — CHALLENGER" if _challenger else "")
                     + (" — AGAINST THE TIDE" if _out_tide_learned else "")
                     + (" — LONE WOLF (OUT TIDE)" if _out_tide_lw else "")
                     + _weak_title
                     + (" — OPPORTUNISTIC HAIL MARY" if _hail_mary and _hail_mary_mode == "opportunistic"
                        else " — HAIL MARY" if _hail_mary else ""))
            _challenger_say = (f"A challenger — just under the {DESKTOP_ALERT_MIN:.0f} percent floor! "
                               if _challenger else "")
            _challenger_line = (f"  CHALLENGER: the brain reads {_dc:.0f}% vs this rotation's "
                                f"{DESKTOP_ALERT_MIN:.0f}% floor — within {POPUP_NEAR_MISS_PTS:.0f}, "
                                f"allowed to challenge head-to-head. A near-miss, not a champion: "
                                f"size it like one.\n" if _challenger else "")
            _lw_say = (f"Lone wolf! Moving on her own against an out tide! " if _out_tide_lw
                       else "Lone wolf! Moving on her own while the fleet sits still! " if _lw_pts
                       else "")
            _spoken = (f"Buy {asset}! "
                       + (f"Recommended {(_spend):,.0f} — "
                          f"the range is max {_max_spend_say}"
                          f", min {_min_spend_say} dollars — " if _spend > 0 else
                          "Sizing unavailable — size your own! ")
                       + f"{_dc:.0f} percent sure! "
                       + _challenger_say
                       + _brain_say
                       + ("Flat tide — half size! " if _tide_size_mult < 1.0 else "")
                       + ("Weakened — sized to pay the pace bar! " if _weak_fire_reason else "")
                       + ("Fast route! " if _fast_route else "")
                       + _reentry_say
                       + _hm_say
                       + _lw_say
                       + f"Aiming plus {_goal_pct:.0f} percent — target around {_out_by or '?'}, "
                       + (f"Small size only — {_spend:,.0f} dollars! " if _small_coin else
                          (f"Big book — all {(float(_bal) if _bal > 0 else float(DEFAULT_CONFIG.challenge_balance)):,.0f} dollars in fine! "
                           if _carry is not None else
                           "Book unreadable — your call on size! "))
                       + _tide_spoken
                       + (f"Stay close after {_out_by} — I'll call the exit!" if _out_by else
                          "Hold for the goal!"))
            # ── THE DANGER-ZONE PANEL (user rule, Sep 6 — the thin-book
            #    optimized metrics): computed with the FINAL sized spend,
            #    BEFORE the message build; informs, never gates
            _dz = ""
            try:
                _dz = _danger_zone_panel(exchange, pair, float(_spend or 0.0),
                                         float(_operative_goal_pct(_goal_pct,
                                                                  pair)),
                                         float(_win_min or 60.0), _brain_mult)
            except Exception:
                _dz = ""
            # ── THE DECISION BEFORE THE SIDE EFFECT (Nov 6): the contest
            #    book judges the fire FIRST, and only a fire it accepts may
            #    reach the bridge, the popup or the ledger. This is the gate
            #    that used to sit below `message` and AFTER the write — the
            #    ordering gap that let the bot receive a signal the book had
            #    already refused (no contest row, no ledger receipt, but a
            #    live bot signal). A refusal now costs nothing downstream.
            #    The FAMILY is stamped ONCE here and reused by the watch row,
            #    so the widening judges exactly the family the ledger records.
            _fire_route = ("champion-fast" if _fast_route
                           else "champion-opportunistic"
                           if _hail_mary and _hail_mary_mode == "opportunistic"
                           else "champion")
            # THE LEARNED ENTRY RULE (Nov 12): a losing context does not
            # just shrink the next bet — it changes the entry itself. The
            # highest confidence that already lost in this route/tide is
            # now the floor; a deep context is blocked outright. This runs
            # before the fire is built, so the Scout behaves differently
            # instead of paying the same mistake at a smaller size.
            try:
                import overwatch_learner as _ol_entry
                _entry_ok, _entry_bar, _entry_why = _ol_entry.entry_gate(
                    route=_fire_route, tide=_df_state,
                    conviction=signal.confidence,
                    base_bar=DESKTOP_ALERT_MIN)
                if not _entry_ok:
                    logger.info("  ENTRY RULE: %s — %s", asset, _entry_why)
                    _log_suppression(pair, "learned-entry-rule", _entry_why,
                                     quick=float(signal.confidence),
                                     tide=(f"{_df_state} {_df_pct:.0f}%"
                                           if _df_pct is not None else _df_state))
                    return
                if _entry_bar and _entry_bar > float(DESKTOP_ALERT_MIN):
                    logger.info("  ENTRY RULE: %s — learned bar %.0f clears "
                                "at %.0f", asset, _entry_bar,
                                float(signal.confidence))
            except Exception as _entry_exc:
                logger.debug("learned entry rule unavailable: %s", _entry_exc)
            # ── GO BIG OR GO HOME (user rule, Nov 13): when the coin and
            #    the rotation support it, the old per-trade envelope gives
            #    way to the whole account, protected by the hard $30 net
            #    stop and exited by momentum death. A weak fire or a Hail Mary never
            #    takes this path; neither does a flat/out tide or a book
            #    that cannot carry the account.
            try:
                import contest_account as _ca_gb
                _gb_ok, _gb_why = _ca_gb.go_big_supported(
                    action=signal.action, tide_state=_df_state,
                    tide_pct=_df_pct,
                    carry_usd=_carry, balance=float(_bal or 0.0),
                    conviction=float(_dc or 0.0),
                    floor=float(DESKTOP_ALERT_MIN),
                    slot_open=bool(_load_contest_watch()))
                _gb_start = float(
                    _ca_gb.load_state().get("start_balance") or 0.0)
                _gb_stop, _gb_take = _ca_gb.go_big_marks(_gb_start)
                _gb = _ca_gb.go_big_plan(
                    float(_bal or 0.0), _gb_start,
                    supported=bool(_gb_ok and not _weak_fire_reason
                                   and not _hail_mary))
                if _gb.get("ok"):
                    _go_big = True
                    _go_big_why = _gb_why
                    _spend = _base_spend = float(_gb["spend_usd"])
                    _weak_min_usd = _spend
                    _weak_fire_reason = ""
                    _weak_paid = True
                    contest_msg = (
                        f"  SPEND: 100% = ${_spend:,.0f} — GO BIG OR GO HOME "
                        f"({_gb_why}); ride the run, GET OUT when momentum "
                        f"dies, hard stop -${_gb_stop:,.0f} net\n")
                    logger.info("  GO BIG: %s — 100%% = $%.0f (%s)",
                                asset, _spend, _gb_why)
                else:
                    logger.info("  GO BIG declined: %s", _gb.get("why"))
            except Exception as _gb_exc:
                logger.debug("GO BIG sizing skipped: %s", _gb_exc)
            _fire_setup = _setup_tags(
                route=_fire_route,
                tide_state=_df_state, tide_pct=_df_pct,
                conviction=signal.confidence,
                brain=_dc, mult=_brain_mult,
                climb=_climb, weak=_weak_fire_reason,
                size=_spend, base=_base_spend,
                fast=_fast_route,
                reentry=_is_reentry, hail=_hail_mary,
                probe=_econ_probe)
            if _strict_fire_refusal(pair, asset, _spend, _bal,
                                    _weak_fire_reason,
                                    win_min=float(_win_min or 0.0),
                                    family=_setup_family(_fire_setup),
                                    weak_paid=_weak_paid,
                                    go_big=_go_big):
                logger.info("  BRIDGE: no write for %s — the contest book "
                            "refused this fire (%s)", asset,
                            (_weak_fire_reason.split(":", 1)[0]
                             or "exposure/count cap"))
                return
            if not gate_notes:
                signal_written = _bridge_write_now(signal.action)
            # The bot's own answer, in plain English, right after the write
            # it describes (moved here with the write, never before it).
            bot_note = ""
            if gate_notes:
                bot_note = "BOT'S OUT: " + " / ".join(gate_notes) + "\n"
            elif not signal_written:
                bot_note = "BOT'S OUT: bot's full or already holds it.\n"
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

        # ── THE ONE-WATCH RULE (user rule, Sep 21): no BUY NOW popup on an
        # asset already being watched — the watch IS the entry record, so
        # a repeat popup can only be noise. Rotation re-entry after SOLD
        # or a GET OUT auto-close fires fresh as always.
        # (the strict-fire gate ran ABOVE, before the bridge write and the
        # message build — one decision, then both side effects)
        if _popup_already_watched(pair):
            return
        notify_big(title, message, spoken=_spoken if is_buy else None)
        if is_buy:
            # ROTATION LOCKOUT: the alert fired — this asset is now cold
            # until the next rotation boundary (no back-to-back re-entry).
            rotation_mark_fired(asset)
            # ── THE FIRED SNAPSHOT (user spec, Sep 5): every popup that
            # fires lands in the scorer's dataset with its full fire-time
            # features — the positive class the suppression records are
            # graded against.
            # the route is ONE local, computed once — the snapshot stamp
            # and the first-fire reminder must never disagree (the
            # CodeRabbit critical, Oct 2: the reminder first read a bare
            # `route`, a dict key, not a local — a NameError on every BUY
            # popup past this line)
            _opp_route = _fire_route      # computed once, above the gate
            try:
                _fsnap = dict(_SNAPSHOT_CTX.get("ctx") or {})
                _fsnap.update({"kind": "fired",
                               "route": _opp_route,
                               "release": _take_release_prov(pair),
                               "tide": (f"{_df_state} {_df_pct:.0f}%"
                                        if _df_state and _df_pct is not None else None),
                               "fast_why": _fast_why,
                               "brain": _dc, "mult": _brain_mult,
                               "climb": _climb_features_stamped(
                                   _climb.get("features") if _climb else None,
                                   _df_state, _df_pct),
                               "spend": float(_spend or 0.0),
                               # THE GOAL STAMP AT THE ENTRY RECORD (Nov 6):
                               # the watch row was already stamped, but this
                               # fire's hindsight snapshot still shipped a raw
                               # zero when the engine named no goal — and the
                               # snapshot is the row the graders read. Same
                               # operative fallback, one derivation.
                               "goal_pct": _operative_goal_pct(_goal_pct,
                                                               pair),
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
            # ── THE FIRST-FIRE REMINDER (Oct 2, the accumulation
            # window's watch begins): the opp class was born
            # KEEP-PROVISIONAL — zero graded fires, the hindsight grader
            # re-runs at 10 fires or 7 days. The FIRST
            # champion-opportunistic popup ever is the moment the
            # operator must know the clock started: one Zira line, one
            # popup, written into the reminders' one-shot marker (the
            # tide_reminder idiom — the event is the trigger, not a
            # date). The OTHER classes stay silent (that's the whole
            # point — the provisionality is the opp class's), and the
            # marker write happens FIRST so a crash between can only
            # lose the toast, never repeat it forever.
            if _opp_route == "champion-opportunistic" and not _opp_reminder_done():
                _opp_reminder_mark()
                try:
                    notify_big(
                        "Kraken Scout - OPPORTUNISTIC HAIL MARY: FIRST FIRE",
                        "The first opportunistic fire just landed.\n\n"
                        "The accumulation window is now being watched:\n"
                        ".venv\\Scripts\\python.exe scratch\\opp_hindsight.py\n"
                        "verdict: KEEP-PROVISIONAL until 10 graded fires.",
                        spoken="The first opportunistic hail mary just "
                               "fired. The accumulation window is now "
                               "being watched.")
                except Exception as _rem_exc:
                    logger.warning("opp first-fire reminder failed: %s",
                                   _rem_exc)
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
                             f"BUY {signal.confidence:.0f}% "
                             + ("(GO BIG)" if _go_big else
                                "(opportunistic hail mary)"
                                if _hail_mary and _hail_mary_mode == "opportunistic"
                                else "(champion)"),
                             "go_big" if _go_big else "champion",
                             go_big=_go_big,
                             hm_mode=(_hail_mary_mode
                                      if _hail_mary and _hail_mary_mode == "opportunistic"
                                      else None),
                             # the fire-time family — the SAME stamp the gate
                             # judged (one call, one truth)
                             setup=_fire_setup,
                             # the clock's OWN plan basis — the same string
                             # the GOAL CLOCK line printed, never re-derived
                             plan_basis=_plan_basis,
                             pace_pct_per_hour=_pace_pct, exchange=exchange)
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
    # THE SLOW-CYCLE WATCH (Sep 23): one announcement per slow episode —
    # the tracker lives in the loop, the truth in _cycle_slow_alarm().
    # The clock starts AT BOOT (not the epoch): the boot-night first-pass
    # tournament IS the first episode and announces as a slow scan.
    last_cycle_done = time.time()
    _slow_episode = {"start": 0.0, "announced": False, "scan": False}
    last_loser_fetch = ""  # date string — fetch losers once per day
    cached_loser_pairs = []
    alerted_recently = {}
    _mover_scanned = {}  # MOVER FUNNEL pacing: per-pair rescan timestamps (10 min), isolated from the contest path
    current_block_name = None  # track time block changes for position reset
    _scanner_state0 = _load_scanner_state()
    last_flatten_date = _scanner_state0.get("last_flatten_date", "")  # persisted — restarts don't re-flatten
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

    # ── THE EXIT WATCH THREAD (Nov 7): the light pass gets its own cadence,
    #    so a mover scan or a tournament holding this loop can no longer
    #    leave any exit gate unrun. One pass at a time (the single-flight
    #    lock), and an on-time loop still runs the pass itself.
    _light_stop = threading.Event()
    threading.Thread(target=_light_watchdog,
                     args=(exchange, cfg, _light_stop, _slow_episode),
                     name="exit-watch", daemon=True).start()

    while True:
        now = _now_mst()
        hour = now.hour
        dow = now.weekday()  # 0=Mon, 6=Sun
        block_name, full_interval, min_conf, auto_trade_threshold, block_desc, no_buy, rot_key = get_time_block(hour, dow)
        # THE OVERWATCH'S ENTRY-GATE MICRO-TUNE (Nov 12): a 3-loss streak
        # moves the confidence gate one point, then keeps trading at full
        # speed. No pause, no suspension, no risk cut.
        try:
            import overwatch_governor as _og
            min_conf = max(1, min(99, int(round(
                float(min_conf) + _og.confidence_bias_points()))))
        except Exception:
            pass

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
                # THE SILENT ROTATION ASKS WHY (user ask, Sep 28: "she
                # says 'all good' but doesn't check if it is"): a rotation
                # that closed with zero fired popups gets its WHY on the
                # record — buried candidates name the gates (and the
                # guard docked through the SAL channel), a drought or an
                # honest no says so plainly. The self-adjust lives in the
                # dock (meta_layers.dock_silent_rotation); the log is
                # the ask.
                if _dres.get("empty_fire"):
                    _sdock = _dres.get("silent_dock") or {}
                    _dock_txt = (f" — guard docked +{_sdock['docked']:.1f} "
                                 f"(penalty {_sdock['penalty']:.2f})"
                                 if _sdock.get("docked")
                                 else f" — {_sdock.get('reason', 'no dock')}")
                    _pay_txt = (f", {_dres.get('empty_payers')} reached goal"
                                if _dres.get("empty_payers") else "")
                    logger.warning("🔇 SILENT ROTATION %s: %s%s%s",
                                   _prev_key, _dres.get("empty_why")
                                   or "no popups fired", _pay_txt, _dock_txt)
            except Exception as _exc:
                logger.debug("discipline evaluation failed: %s", _exc)

        # ── THE COOLDOWN REVIEW (user spec, Sep 21: "What did I learn
        # today, and how do I increase the PnL when this setup appears
        # again?") — at the 20:00 cooldown boundary, ONCE per MST day:
        # the day-level factual readout (money left, discipline score,
        # liquidity/tide, realized vs the daily goal, the adjustments
        # actually made) + one learning step on the SAME weights the
        # rotation score uses — the brain log learns from the day the
        # way it learns from a rotation. The lesson rides the same
        # lessons log the console's brain-log readers consume.
        if no_buy and now.hour == 20 and not _day_review_spoken(now):
            try:
                import discipline as _disc
                _review = _disc.cooldown_review(exchange, speak=True)
                logger.info("🌙 COOLDOWN REVIEW %s: score %s | left $%.0f "
                            "| realized $%.2f vs pace $%.2f | tide %s | "
                            "deltas %s",
                            _review.get("date"),
                            _review.get("score"),
                            float(_review.get("total_left") or 0),
                            float(_review.get("realized_pnl") or 0),
                            float(_review.get("daily_pace") or 0),
                            _review.get("tide_at_close") or "unknown",
                            _review.get("weight_deltas") or {})
                logger.info("   lesson: %s", _review.get("lesson"))
                # THE NIGHTLY DIAGNOSTIC (Sep 21): the verdict-cost study
                # writes its ranked table to logs/verdict_cost_report.md —
                # the permanent record the nightly line cites (newest
                # first, one section per night, same shape as the tide-
                # bar weekly). Never raises; a failed write is a missing
                # report, never a broken review.
                try:
                    import _verdict_study as _vst
                    _vp = _vst.write_report()
                    if _vp:
                        logger.info("   verdict-cost report: %s", _vp.name)
                except Exception as _exc:
                    logger.debug("verdict-cost report failed: %s", _exc)
                # ── THE GIVE-BACK TIER HINDSIGHT (user ask, Oct 6):
                # every SPENT tier — applied, refused and queued (a
                # refusal burns the tier) — graded against what the day
                # did afterward: did the re-arm tighten at the right
                # moment, or cut a recovery short? The post-window facts
                # read the verdict study's candle-peak book; the newest
                # section prepends. A failed write is a missing report,
                # never a broken review.
                try:
                    import giveback_tier_hindsight as _gbh
                    _gbp = _gbh.write_report()
                    if _gbp:
                        logger.info("   give-back tier hindsight: %s",
                                    _gbp.name)
                except Exception as _exc:
                    logger.debug("give-back tier hindsight failed: %s",
                                 _exc)
                # THE ERA COMPARE (user, Sep 22): the re-base's week-one
                # read runs ONCE on/after Sep 28 — date-gated so a restart
                # during the day cannot miss it, state-marked so it never
                # re-runs — and its report lands beside the verdict-cost's.
                try:
                    import era_compare as _ec
                    if _ec.due(now):
                        _ecp = _ec.write_report()
                        if _ecp:
                            logger.info("   era-compare report: %s", _ecp.name)
                        _ec.mark_done(now)
                except Exception as _exc:
                    logger.debug("era-compare failed: %s", _exc)
                # THE PROFIT GUARD'S DAILY SELF-ADJUSTMENT (user spec,
                # Sep 22, step 10): recompute every modifier at cooldown
                # and store GuardFactor for tomorrow's learning —
                # date-marked, restart-proof, fail-soft like every clause.
                try:
                    import profit_guard as _pgc
                    _pgadj = _pgc.self_adjust(now)
                    if _pgadj:
                        logger.info("🛡 PROFIT GUARD re-tuned for tomorrow: "
                                    "factor %.2f — %s",
                                    _pgadj.get("factor", 1.0),
                                    _pgc.explain(_pgadj))
                except Exception as _exc:
                    logger.debug("profit-guard self-adjust failed: %s", _exc)
                # SAL's verdict feeds the penalty (user spec: audit the
                # day's closes, adjust for tomorrow) — before the review
                # so the night's readout already includes it.
                try:
                    import meta_layers as _mls
                    _extra = _mls.discipline_penalty_extra()
                    if _extra > 0:
                        logger.info("   SAL/RCL penalty extra: +%.1f on "
                                    "tomorrow's discipline modifier", _extra)
                except Exception as _exc:
                    logger.debug("SAL penalty read failed: %s", _exc)
                # ── THE DAILY NARRATIVE LAYER (user spec, Sep 22): runs
                # LAST — the day's numbers become the seven questions,
                # stored with the learning factor that scales tomorrow's
                # DailyGoal. Fail-soft like every clause.
                try:
                    import meta_layers as _mln
                    _dnl = _mln.dnl_report(now)
                    if _dnl:
                        logger.info("📖 DNL: %s | focus: %s",
                                    _dnl.get("What did I learn today?"),
                                    _dnl.get("What is tomorrow's focus?"))
                        logger.info("   tomorrow's goal multiplier: %.2f",
                                    _mln.tomorrow_goal_multiplier())
                except Exception as _exc:
                    logger.debug("DNL failed: %s", _exc)
                # THE CARVE-OUT'S NIGHT UPDATE (user spec, Sep 23):
                # freeze today's allowance + the five mods into the
                # history and carry unreadable sources for a day —
                # runs after DNL so the narrative cites tonight's
                try:
                    import carve_out as _co_n
                    _con = _co_n.night_update(now)
                    logger.info("   carve-out tonight: %.1f%% (goal x%.2f, lone-wolf %s)",
                                _con["carve"] * 100.0, _con["goal_factor"],
                                "ON" if _con["lone_wolf"] else "OFF")
                except Exception as _exc:
                    logger.debug("carve-out night update failed: %s", _exc)
                # ── THE BOUNDARY CROSS-EXAMINATION TICK (user spec,
                # Sep 23, section 5: the cooldown summary runs the exam
                # every 6 rotations, feeds WARN/FAIL to the correction
                # module, reschedules +3). One night = one rotation, so
                # the rotation count derives from the DNL ledger's
                # readable clamp_bounds records — restart-proof, no
                # counter to drift. Fail-soft like every clause.
                try:
                    import carve_out as _co_x
                    import meta_layers as _ml_x
                    _dnl = _ml_x._state().get("dnl") or {}
                    _rotations = sum(
                        1 for _d in _dnl
                        if ((_dnl.get(_d) or {}).get("_data") or {})
                        .get("clamp_bounds", {}).get("readable"))
                    if _rotations >= _co_x._EXAM_EVERY_N \
                            and _rotations >= _co_x._next_exam_due():
                        _exam = _co_x.cross_examine(now)
                        if _exam:
                            logger.info("🔍 BOUNDARY CROSS-EXAM (rotation %d): %s — %s",
                                        _rotations, _exam.get("FinalVerdict"),
                                        _exam.get("Summary"))
                            for _fam, _fd in (_exam.get("families") or {}).items():
                                logger.info("   %s: %s (%s)", _fam,
                                            "ok" if _fd.get("ok") else "NOT ok",
                                            _fd.get("evidence"))
                            _corr = _co_x.cross_examine_corrections(_exam, now)
                            if _corr.get("applied"):
                                logger.info("   boundary corrections applied: %s "
                                            "(expire %s)",
                                            ", ".join(_corr["applied"]),
                                            _corr.get("expires"))
                            elif _corr.get("cleared"):
                                logger.info("   boundary corrections cleared "
                                            "— the bands held their exam")
                    _co_x._schedule_exam(now)
                except Exception as _exc:
                    logger.debug("boundary cross-exam tick failed: %s", _exc)
                # THE TRI-WATCH COOLDOWN SNAPSHOT (user spec, Sep 23,
                # section 5): one read of every watched pair's exit
                # state at the cooldown boundary — the day's ExitMod
                # high-water mark and the near-missed force exits ride
                # the ledger for tomorrow's exam.
                try:
                    import tri_watch as _tws
                    _snap = _tws.snapshot(now)
                    if _snap:
                        logger.info("👁 TRI-WATCH snapshot: %d pair(s) read, "
                                    "day ExitMod high %.2f, forced exits %d",
                                    len(_snap.get("pairs") or {}),
                                    float(_snap.get("exitmod_high") or 0),
                                    int(_snap.get("force_exits") or 0))
                except Exception as _exc:
                    logger.debug("tri-watch snapshot failed: %s", _exc)
            except Exception as _exc:
                logger.debug("cooldown review failed: %s", _exc)

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
                        # THE DEEP-VETO JOURNAL WIRE IS GONE (user rule,
                        # Oct 9): the gate itself no longer exists, so its
                        # release fuel is dead — only the READ runs now.
                        try:
                            from strategy import evaluate_strategy as _ev, \
                                enable_deep_analysis, disable_deep_analysis
                            enable_deep_analysis()
                            _deep_sig = _ev(df, cfg)
                            disable_deep_analysis()
                            from indicators import ema as _ema_mv
                            _es_mv = float(_ema_mv(df["close"], cfg.ema_short).iloc[-1])
                            _el_mv = float(_ema_mv(df["close"], cfg.ema_long).iloc[-1])
                            # THE MOVER DEEP-VETO IS GONE (user rule,
                            # Oct 9): a contradicting read is forensics,
                            # not a wall — the fire proceeds. The
                            # downward-trend knife stays (Aug 25 rule).
                            if _deep_sig.action != "buy":
                                logger.info("  MOVER CONTRA (note): %s deep-reads %s %.0f%% — logged, the fire lands",
                                            asset, _deep_sig.action, _deep_sig.confidence)
                                _log_suppression(pair, "deep-contranote",
                                                 f"mover: deep-reads {_deep_sig.action} {_deep_sig.confidence:.0f}% "
                                                 f"against the buy — logged only (the deep-veto gate is deleted)",
                                                 quick=float(signal.confidence),
                                                 brain=float(_deep_sig.confidence), rot=rot_key)
                            if (_deep_sig.action == "buy"
                                    and _deep_sig.confidence < 60)                                     or (_es_mv < _el_mv and float(df["close"].iloc[-1]) < _el_mv):
                                logger.info("  MOVER VETO: %s deep reads %s sub-bar/trend — no pick (quality floor; the brain says no)",
                                            asset, getattr(_deep_sig, 'confidence', 0))
                                _log_suppression(pair, "quality-floor",
                                                 f"mover: deep {'<60' if _deep_sig.action == 'buy' else _deep_sig.action} "
                                                 f"or EMA knife — the quality floor stands",
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
                        # the ADAPTIVE hatch (Sep 23): the required own
                        # move is carve_out's allowance (Base x the five
                        # mods, 2-8%), and the hatch only OPENS when
                        # LoneWolfMode is granted (permission gate)
                        try:
                            import carve_out as _co_m
                            _m_cres = _co_m.gate(pair=pair)
                            _m_req = float(_co_m.required_own_move(_m_cres))
                            _m_mode = bool(_m_cres.get("lone_wolf"))
                        except Exception:
                            _m_req, _m_mode = LONE_WOLF_MOVE_MIN, False
                        _m_out_lw = bool(_tsw == "OUT"
                                         and _mv_pct is not None
                                         and float(_mv_pct) >= _m_req
                                         and _m_mode)
                        _m_tide_out_weak = bool(_tsw == "OUT" and not _m_out_lw)
                        if _m_tide_out_weak:
                            logger.info("  MOVER OUT-TIDE: %s — tide OUT (fleet %.0f%%) "
                                        "— the popup lands at minimum size",
                                        asset, _tpw or 0)
                        if _m_out_lw:
                            logger.info("  OUT-TIDE LONE WOLF: %s — mover %+.0f%% on 24h against "
                                        "an OUT tide (fleet %.0f%%) — no bridge write (the bot stays "
                                        "out); the contest popup fires marked",
                                        asset, _mv_pct, _tpw or 0)

                        # THE LEARNED ENTRY BAR, MOVER ROUTE (Nov 12): the
                        # mover contexts learn their own entry rule too. A
                        # losing mover route/tide no longer only affects its
                        # bet size later — the highest conviction that
                        # already lost there becomes the floor, and a deep
                        # mover context is refused before the bridge write
                        # and popup. Fail-open: a broken table never
                        # silences a mover.
                        try:
                            import overwatch_learner as _ol_m
                            _m_entry_ok, _m_entry_bar, _m_entry_why = _ol_m.entry_gate(
                                route="contest_mover", tide=_tsw,
                                conviction=signal.confidence,
                                base_bar=MOVER_MIN_CONF)
                            if not _m_entry_ok:
                                logger.info("  MOVER ENTRY RULE: %s — %s",
                                            asset, _m_entry_why)
                                _log_suppression(pair, "learned-entry-rule",
                                                 f"mover: {_m_entry_why}",
                                                 quick=float(signal.confidence),
                                                 tide=f"{_tsw} {_tpw or 0:.0f}%",
                                                 rot=rot_key)
                                continue
                            if _m_entry_bar and _m_entry_bar > float(MOVER_MIN_CONF):
                                logger.info("  MOVER ENTRY RULE: %s — learned bar "
                                            "%.0f clears at %.0f", asset,
                                            _m_entry_bar, float(signal.confidence))
                        except Exception as _m_entry_exc:
                            logger.debug("mover learned entry rule unavailable: %s",
                                         _m_entry_exc)

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
                                action=signal.action, route="contest_mover",
                                entry=float(signal.price or 0.0))
                        except Exception as exc:
                            logger.warning("mover fire snapshot failed for %s (%s) "
                                           "— this fire's hindsight row is lost", pair, exc)
                        # THE DESK GATE (the mover path): only AVAILABILITY
                        # gates the popup now — a contradicting read is
                        # logged (forensics) but never silences the fire
                        # (user rule, Oct 9: the deep-veto class is deleted;
                        # fail-closed stays for a MISSING read only — the
                        # JUP lesson, kept).
                        _m_desk_ok = _deep_sig is not None
                        if _deep_sig is not None and _deep_sig.action != "buy":
                            _log_suppression(pair, "deep-contranote",
                                             f"mover: the scan reads {_deep_sig.action} "
                                             f"{_deep_sig.confidence:.0f}% against the buy — logged only "
                                             f"(the deep-veto gate is deleted; the fire lands)",
                                             quick=float(signal.confidence),
                                             brain=(float(_deep_sig.confidence)
                                                    if _deep_sig is not None else None),
                                             floor=89.0,
                                             tide=f"{_tsw} {_tpw or 0:.0f}%", rot=rot_key)
                        if not _m_desk_ok:
                            logger.info("  Mover %s %.0f%% — bot-only (the scan "
                                        "could not be read — fail-closed stays)", asset, signal.confidence)
                            _log_suppression(pair, "fail-closed",
                                             "mover: the brain could not be read "
                                             "(fire-time rule: the scan must run; the number is advisory)",
                                             quick=float(signal.confidence),
                                             brain=None,
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
                            _m_sm_rel = False
                            if _m_stale:
                                # ── THE JOURNAL RELEASE (Oct 8): the mover's
                                # strand of the same wire — the journal's
                                # stale-move fuel spends in the deep-veto
                                # shape. THE GATE IS THE DESK-BLESSED
                                # DIRECTION (the commit-gate reviewers'
                                # reachability finding): the desk gate
                                # upstream already demanded a buy read (the
                                # JUP fail-closed lesson), so only blessed
                                # directions reach this guard — the release
                                # asks for a deep read in hand with a SELL
                                # always standing (a hold releases too, so
                                # the wire stays live if the desk gate ever
                                # admits it), quick within
                                # SEAM_RELEASE_SLACK of the 89.0 rail the
                                # suppression itself logs, fail-closed.
                                _m_sm_pts = 0.0
                                if _deep_sig is not None \
                                        and _deep_sig.action != "sell":
                                    try:
                                        import discipline as _dc_sm
                                        _m_sm_pts = _dc_sm.journal_release_pts("stale-move")
                                        if (_m_sm_pts > 0.0
                                                and float(signal.confidence)
                                                + _dc_sm.SEAM_RELEASE_SLACK
                                                >= 89.0):
                                            _m_sm_rel = True
                                    except Exception:
                                        _m_sm_rel = False
                            if _m_stale and not _m_sm_rel:
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
                                if _m_stale:
                                    _mark_release(pair, "stale-move")
                                    # THE STALE-RELEASE TRIPWIRE (the mover's strand)
                                    _note_release_alert(
                                        pair, "stale-move", _m_sm_pts,
                                        quick=float(signal.confidence),
                                        floor=89.0)
                                    logger.info(
                                        "  JOURNAL RELEASE (mover): %s — 4h high %.0fh ago, "
                                        "%.1f%% below it — quick %.0f%% within %s pts of the "
                                        "89.0 rail; the journal shows stale-move burying "
                                        "champion-grade payers (relief %.1f pts) — released",
                                        asset, _m_stale[0], _m_stale[1],
                                        float(signal.confidence),
                                        _dc_sm.SEAM_RELEASE_SLACK, _m_sm_pts)
                                # THE GOAL CLOCK — same math as the champion popup
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
                                _m_weak = ""            # a death-line fire rides the same min-size label
                                _m_weak_paid = False   # did the resize certify the mover?
                                _m_min_usd = 0.0       # the MIN the title shows
                                _m_mult = 1.0
                                _m_guard_capped = False
                                _m_climb = None  # fail-open init: a broken read never NameErrors the popup
                                # THE MAX/MIN PAIR (user decree, Oct 10): MAX is the
                                # full COMPUTED spend — before any weaken clamp, after
                                # every real size (tranche, guard, brain, tide) — so
                                # the trust anchor inits unusable; every assignment
                                # inside the try below ENGRAVES it (the last one that
                                # runs wins). A bomber read dies mid-flight: the stale
                                # anchor can never poison the next fire.
                                _m_base_spend = -1.0
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
                                                # minimum-viable floor (the
                                                # champion-side JUP fix, Oct 9)
                                                _m_spend = max(_m_fit) if _m_fit else _m_spend * 0.10
                                                _m_base_spend = _m_spend      # the tranche IS the size
                                    except Exception:
                                        _m_carry = None
                                    # THE TOO-THIN GATE IS GONE (user rule,
                                    # Oct 9, the mover path too): the thin
                                    # book SIZES the spend but never silences
                                    # the popup — the sized spend is the notice.
                                    if _m_carry is not None and _m_spend <= 0:
                                        _m_spend = 0.0   # the spend line reads SIZE YOUR OWN
                                    else:
                                        _m_wdd = _ca.window_drawdown_pct(pair)
                                        _m_risk = _ca.max_entry_usd(
                                            _m_bal, _m_wdd, float(_m_acct["start_balance"]))
                                        if _m_risk <= 0:
                                            # WATCH-ONLY (Nov 12): the mover signal is
                                            # never buried at the floor.  It lands at a
                                            # minimum size and the exit monitor owns it.
                                            _m_weak = ("DEATH-LINE WATCH: buffer gone "
                                                       f"(the platform line is "
                                                       f"${_ca.death_line(_m_acct):,.2f}) — "
                                                       "minimum size; GET OUT monitor armed")
                                            _m_spend = max(10.0, min(
                                                float(_m_spend or 0.0) * 0.10,
                                                max(float(_m_bal) * 0.02, 10.0)))
                                            _m_base_spend = _m_spend
                                            _m_min_usd = _m_spend
                                            _m_climb = {}   # no climb read on the min-size route
                                            logger.warning(
                                                "  MOVER DEATH LINE WATCH: %s — "
                                                "signal lands at min size $%.0f "
                                                "(entries are not vetoed)",
                                                asset, _m_spend)
                                        else:
                                            if _m_spend > _m_risk:
                                                _m_spend = _m_risk
                                                _m_guard_capped = True
                                                _m_base_spend = _m_spend      # the death-line cap IS the size
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
                                            # THE MAX/MIN EPILOGUE (user decree, Oct 10):
                                            # MAX = the full computed spend BEFORE any
                                            # weaken clamp — the balance that survived the
                                            # tranche + death-line cap + brain + tide,
                                            # genuine sizing.
                                            _m_base_spend = _m_spend
                                            # ── THE CLIMB-OUT GATES (Sep 6):
                                            #    same hard gate as the champion
                                            #    path — no escape route, no trade
                                            _m_climb = _climb_out_check(
                                                exchange, pair,
                                                float(signal.price or 0.0),
                                                _m_goal,
                                                float(_m_spend or 0.0))
                                            _m_weak = ("TIDE OUT: fleet "
                                                       f"{_tpw or 0:.0f}% up — "
                                                       "minimum size"
                                                       if _m_tide_out_weak else "")
                                            if not _m_climb.get("ok"):
                                                _mfg = _m_climb.get("fail_gate") or "?"
                                                # unified rule (Oct 9): adversity fires min-size
                                                _m_weak = f"CLIMB-OUT {_mfg}: {_m_climb.get('fail_reason') or '?'}"
                                            # THE MERGED DIAL (Oct 9): the worth floor no
                                            # longer bumps — it informs; the fire lands
                                            # at min size marked
                                            _m_pace = _ca.daily_pace_usd(_m_acct)
                                            _m_wf = _worth_floor_frac()  # the console dial, live
                                            _m_worth = _m_spend * (_m_goal / 100.0)
                                            if _m_worth < _m_pace * _m_wf and not _m_fast and not _m_weak:
                                                _m_weak = (f"WORTH FLOOR: ${_m_spend:,.0f} at the "
                                                           f"+{_m_goal:.1f}% goal pays ${_m_worth:,.0f} "
                                                           f"(< ${_m_pace * _m_wf:,.0f})")
                                except Exception as _exc:
                                    logger.debug("mover 3%% guard unavailable: %s", _exc)  # fail-open
                                if _m_weak:
                                    # the unified-rule weaken: the fire rides the
                                    # weakened size — and the label must be TRUE
                                    # (Nov 6, the cascade review: every MIN SIZE
                                    # fire booked its MAX). CLAMP first, then
                                    # (Nov 7) raise it to the smallest size whose
                                    # own goal pays the pace bar inside the death
                                    # line's allowance — a weakened fire that
                                    # cannot pay is refused here, in full.
                                    _m_spend = min(_m_spend, _m_bal * 0.02)
                                    _m_base = (_m_base_spend if _m_base_spend > 0
                                               else _m_spend)
                                    _m_min_usd = _m_spend
                                    _wsm = _weakened_fire_size(pair, _m_base, _m_goal)
                                    if _wsm is None:
                                        logger.info("  MOVER MIN-SIZE FIRE (%s): "
                                                    "spend $%.0f — the floor stands "
                                                    "(the account could not be read)",
                                                    _m_weak, _m_spend)
                                    elif not _wsm.get("ok"):
                                        _m_weak = f"{_m_weak}; {_wsm.get('why')}"
                                        logger.info("  MOVER WEAKENED REFUSED (%s): %s",
                                                    _m_weak, _wsm.get("why"))
                                    else:
                                        _m_spend = min(_m_base, max(_m_spend, float(_wsm["size"])))
                                        _m_min_usd = _m_spend
                                        _m_weak_paid = True
                                        logger.info("  MOVER WEAKENED SIZING (%s): $%.0f "
                                                    "booked: %s", _m_weak, _m_spend,
                                                    _wsm.get("why"))
                                    # MAX >= MIN, always — MAX is the body the MIN sits
                                    # under, never a number below it.
                                    _m_base_spend = max(_m_base_spend, _m_spend)
                                    _m_min_spend_say = f"${_m_min_usd:,.0f}"
                                    logger.info("  MOVER MIN-SIZE FIRE (%s): spend $%.0f — the fire LANDS",
                                                _m_weak, _m_spend)
                                    _log_suppression(pair, "worth-floor" if "WORTH" in _m_weak else "climb-out",
                                                     f"mover min-size: {_m_weak}",
                                                     quick=float(signal.confidence),
                                                     brain=(float(_deep_sig.confidence)
                                                            if _deep_sig is not None else None),
                                                     floor=89.0,
                                                     tide=f"{_tsw} {_tpw or 0:.0f}%", rot=rot_key)
                                    _m_wakemark = (f" 🧭 WEAKENED, SIZED TO PAY ({_m_weak.split(':')[0]})")
                                elif _m_skip:
                                    logger.info("  %s", _m_skip)
                                    _m_gate = "death-line" if "DEATH" in _m_skip else "worth-floor"
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
                                        _m_spend_line = (f"  RISK CAP: ${_m_spend:,.0f} pre-sizing — THE 3% GUARD: this coin's "
                                                         f"worst-case window drawdown is {_m_wdd:.1f}%; "
                                                         f"${_m_spend:,.0f} is the most the death line can afford "
                                                         f"(the recommended spend is the title/ENTER line)\n")
                                    elif _m_carry is not None and _m_spend < _m_bal:
                                        _m_spend_line = (f"  SPEND: {_m_spend / _m_bal:.0%} = ${_m_spend:,.0f} — "
                                                         f"the book will carry it: ${_m_carry:,.0f} "
                                                         f"(20% of the visible book — small coin)\n")
                                    elif _m_carry is not None:
                                        _m_spend_line = (f"  SPEND: 100% = ${_m_bal:,.0f} — big book, "
                                                         f"will carry it: ${_m_carry:,.0f} (20% of the visible book)\n")
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
                                    # THE MAX/MIN PAIR (user decree, Oct 10):
                                    # the title leads with both ends of the
                                    # sizing range.
                                    _m_min_say = (_m_min_usd if _m_min_usd > 0
                                                  else _m_bal * 0.02)
                                    _m_title = (f"🚀 MOVER BUY {asset}"
                                                + (f" — MAX: ${_m_base_spend:,.0f} — MIN: ${_m_min_say:,.0f}"
                                                   if _m_base_spend > 0 else
                                                   " — SIZE YOUR OWN — sizing unavailable")
                                                + f" — {_m_dc:.0f}% SURE"
                                                + (f" — {_m_mult:.2f}× SIZE" if _m_mult < 1.0 else "")
                                                + (" — FAST ROUTE" if _m_fast else "")
                                                + (" — LONE WOLF (OUT TIDE)" if _m_out_lw else "")
                                                + (" 🧭 WEAKENED" if _m_weak else ""))
                                    _m_spoken = (f"Buy {asset}! {_m_dc:.0f} percent sure! "
                                                 + (f"The range is max {_m_base_spend:,.0f}"
                                                    f", min {_m_min_say:,.0f} dollars — " if _m_base_spend > 0 else
                                                    "Sizing unavailable — size your own! ")
                                                 + (f"Weakened sizing — {_m_weak.split(':')[0] if _m_weak else ''}! " if _m_weak else "")
                                                 + (f"The brain says {_m_dc:.0f} percent — sizing at {_m_mult:.2f}! "
                                                    if _m_mult < 1.0 else "")
                                                 + ("Fast route! " if _m_fast else "")
                                                 + _m_re_say
                                                 + f"Runner outside the 54 — moving {_mv_pct:+.0f} percent on the day! "
                                                 + f"Aim for plus {_m_goal:.0f} percent! "
                                                 + _m_size_say
                                                 + _m_tide_say
                                                 + f"Stay close after {_m_out_by} — I'll call the exit!")
                                    _m_msg = (
                                        f"BUY {asset} — MOVER\n"
                                        f"WHY: runner outside the 54 ({_mv_pct:+.0f}% on 24h), and the brain "
                                        f"reads the setup at {_m_dc:.0f}%\n"
                                        f"HOW LONG: goal +{_m_goal:.1f}% by {_m_out_by} — "
                                        f"stay close after that; the exit is hers, never the clock.\n"
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
                                                             float(_operative_goal_pct(
                                                                 _m_goal, pair)),
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
                                    # THE ONE-WATCH RULE (Sep 21): already
                                    # watched → no popup, no tracking — the
                                    # mover loop just moves on.
                                    if _strict_fire_refusal(pair, asset, _m_spend,
                                                            _m_bal, _m_weak,
                                                            weak_paid=_m_weak_paid):
                                        continue
                                    if _popup_already_watched(pair):
                                        continue
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
                                                     "mover",
                                                     setup=_setup_tags(
                                                         route=("contest_mover_fast" if _m_fast
                                                                else "contest_mover"),
                                                         tide_state=_tsw,
                                                         tide_pct=_tpw,
                                                         conviction=signal.confidence,
                                                         brain=_m_dc, mult=_m_mult,
                                                         climb=_m_climb,
                                                         weak=_m_weak,
                                                         size=_m_spend,
                                                         base=_m_base_spend,
                                                         fast=_m_fast),
                                                     exchange=exchange)
                                    try:
                                        _mfs = dict(_SNAPSHOT_CTX.get("ctx") or {})
                                        _mfs.update({"kind": "fired",
                                                     "route": ("contest_mover-fast" if _m_fast else "contest_mover"),
                                                     "release": _take_release_prov(pair),
                                                     "tide": (f"{_tsw} {_tpw:.0f}%"
                                                              if _tsw and _tpw is not None else None),
                                                     "fast_why": _m_fast_why,
                                                     "brain": _m_dc, "mult": _m_mult,
                                                     "spend": float(_m_spend or 0.0),
                                                     # the mover fire's record,
                                                     # same fallback (Nov 6)
                                                     "goal_pct": _operative_goal_pct(_m_goal,
                                                                                     pair),
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

        # ── CONTEST LIGHT (THE EXIT WATCH'S OWN CLOCK, Nov 7): the pass used
        #    to sit inline behind the mover scan and the tournament, so a
        #    slow scan left the hard stop, the force gate and the plan-spent
        #    gate unrun for minutes at a time (the soak found 23). It now
        #    carries its own tick: this call runs the pass when the loop is
        #    on time, _light_watchdog takes the tick when it is not, and the
        #    single-flight lock keeps it to ONE pass per tick either way.
        #    The AA-box watermark watch rides inside the pass (AFTER
        #    contest_light: the stamp advances there, and a pre-pass call
        #    would page one pass early).
        #
        _run_light_pass(exchange, cfg, source="loop", episode=_slow_episode)

        # ── THE VALIDATED 5m EDGE (Nov 13): one closed-bar scan per 5m
        #    candle, one all-in slot, no bridge write. It runs AFTER the exit
        #    watch so a slow 5m read can never delay a stop. It hands its
        #    fire to the same contest watch writer, so the death line and
        #    one-slot book cap keep their authority, and the exit watch
        #    closes it on the edge's own stop/take/6-bar clock.
        try:
            import bounce_live as _bl
            _bl.tick(exchange, auto_track_popup, logger,
                     push=_push_to_phone)
        except Exception as _bl_exc:
            logger.warning("5m bounce edge tick failed (%s) — the normal "
                           "scanner continues", _bl_exc)
        if _LIGHT_CLOCK.get("last_done"):
            last_cycle_done = float(_LIGHT_CLOCK["last_done"])   # COMPLETED pass

        # ── THE SLOW-CYCLE WATCH (Sep 23): between light passes, name the
        #    stall once per episode — a slow scan (busy beats running) or
        #    a crashed cycle (none). Log-only: warnings never reach the
        #    phone (user rule); the console's pulse line is the display.
        _cycle_slow_alarm(_slow_episode, last_cycle_done, _pulse_scanning())

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
                _pulse_beat("busy")    # the tournament holds the loop ~9 min —
                                       # keep the pulse fresh so the console
                                       # reads a running scan. INSIDE the gate
                                       # (Sep 23 negative-space census): a busy
                                       # beat outside the try can never clear
                                       # its phase — an exception between here
                                       # and the finally would leave the pulse
                                       # stuck at scan until the next hold.
                # Parallel fetch all assets
                all_data = fetch_ohlcv_parallel(exchange, all_pairs, max_workers=10)
                _pulse_beat("busy")   # the fetch itself is minutes of the 9 —
                                      # the pulse stays fresh mid-fetch
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

                _pulse_beat("busy")   # the scoring bracket is the heart of
                                      # the full scan (~10 min of 54 assets) —
                                      # beat before the crown so the quiet
                                      # stretch can't outgrow the console's
                                      # scan grace (Sep 23: the plate cried
                                      # FROZEN through a working bracket)
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
                _pulse_beat("busy")   # the bounce/champion tail can add minutes
                                      # after the 'done' line — stay scan
            except Exception as exc:
                logger.error("Tournament failed: %s", exc)
            finally:
                _pulse_beat()         # clear to idle on EVERY path — the next
                                      # light cycle advances the counter

        # ── Sleep until next check (30 seconds) ──────────────────────
        _n = _now_mst()
        # THE DIAL IS READ ONCE PER CYCLE (Nov 10): a flip has to land on the
        # NEXT CYCLE, not on the next candidate that happens to reach the
        # knife -- a quiet tape would otherwise hide the revert for an hour
        # and the dial would look broken. Silent unless the value changed.
        try:
            _knife_blocks_entry()
        except Exception as _kb_cycle_exc:
            logger.warning("  KNIFE_BLOCK dial unreadable this cycle (%s) — fail-closed",
                           _kb_cycle_exc)
        # THE SURVIVABILITY GUARD IS READ ONCE PER CYCLE (Nov 11): the brake
        # that decides whether a fire may even be raised used to be consulted
        # ONLY inside send_alert, i.e. only when a candidate happened to reach
        # it — so on a quiet tape a paused guard was evaluated zero times, and
        # nobody could tell an armed guard from a missing one. Reading it here
        # makes the evaluation per-cycle and puts its state in the log, while
        # entry_gate (the refusal itself) stays where it was. The read is
        # side-effect-free except for arming PROBE size when a cooldown ends.
        _guard_cycle = {}
        try:
            import survival_guard as _sg_cycle
            _guard_cycle = _sg_cycle.entry_gate()
            if _guard_cycle.get("blocked"):
                logger.warning("  SURVIVABILITY GUARD: PAUSED — %s (no fire may be raised)",
                               _guard_cycle.get("reason"))
        except Exception as _sg_cycle_exc:
            _guard_cycle = {}
            logger.debug("cycle guard read failed: %s", _sg_cycle_exc)
        _guard_tag = ""
        if _guard_cycle:
            _guard_tag = (" | guard PAUSED"
                          if _guard_cycle.get("blocked") else
                          " | guard clear %d/%d streak, dd %.2f%%%s" % (
                              int(_guard_cycle.get("loss_streak") or 0),
                              int(_guard_cycle.get("pauses") or 0),
                              float(_guard_cycle.get("drawdown_pct") or 0.0),
                              " PROBE %.2fx" % float(_guard_cycle.get("size_multiplier") or 1.0)
                              if _guard_cycle.get("probe") else ""))
        # THE CONTEST STANDING, every cycle, against the FIXED baseline --
        # and the paycheck prompt the cycle the win line is first reached.
        try:
            logger.info("  %s", contest_standing_text())
            contest_paycheck_check()
            death_floor_cycle()
        except Exception as _cs_exc:
            logger.debug("contest standing read failed: %s", _cs_exc)
        next_block_name, next_interval, _, _, _, next_no_buy, _ = get_time_block(_n.hour, _n.weekday())
        _day_label = WEEKDAY_NAMES.get(_n.weekday(), "?")
        if next_no_buy:
            logger.info("  Next check in 30s | %s %s | COOLDOWN — no buys until 00:00%s",
                        next_block_name, _day_label, _guard_tag)
        else:
            logger.info("  Next check in 30s | %s %s | threshold %d%% | full scan every %dm%s",
                        next_block_name, _day_label, auto_trade_threshold, next_interval,
                        _guard_tag)
        _process_push_retries()   # GET OUT attempt 2/2, ~60s after attempt 1
        _process_daily_canary()   # one pipe-alive push per day at/after 09:00 MST
        _popup_famine_guard()     # flags silence the DEATH LINE is causing
        time.sleep(30)


if __name__ == "__main__":
    # THE ACCOUNT-STATE BOOT GUARD (Nov 5, the phantom-10k lesson): a
    # lost state file must never silently re-arm a phantom full
    # balance. Fail BEFORE the pid guard can even stamp ownership —
    # a boot death here lands in the launcher's stderr capture with a
    # named cause, and the engine never computes against a balance
    # nobody knows.
    import contest_account as _ca_boot
    _ca_boot.assert_state_on_boot("background_scanner")
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