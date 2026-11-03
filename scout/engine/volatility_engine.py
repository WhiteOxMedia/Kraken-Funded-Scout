"""Kraken Scout — Volatility Engine (data-driven rotation thresholds).

Pulls rolling 1h OHLCV for the full universe (54 challenge assets +
captured gainers), computes per-rotation volatility statistics, and writes
rotation_thresholds.json — the file the background scanner gates trades on.

NOTE ON DATA DEPTH: Kraken's OHLC endpoint only serves the most recent
~30 days of 1h candles per request. The sqlite cache ACCUMULATES the
rolling window continuously (hourly refresh, nothing inside the window is
ever pruned), so stats start at ~4 weeks of depth and deepen toward the
12-week target as the engine keeps running.

Rotations (America/Denver, DST-aware):
    R1 00-04  🌏 Asia            R4 12-16  🌤 US Afternoon
    R2 04-08  🌅 Pre-US          R5 16-20  🌆 US Late
    R3 08-12  ☀️ EU/US Overlap   20-24     🌙 Cooldown — no trading

For each asset × rotation × {weekday, weekend}:
    avg move (mean |open→close|), σ, max spike/drop vs rotation open,
    median USD volume (Kraken-only basis), false-breakout rate
    (excursion beyond the standard threshold that round-trips).
    thresholds: aggressive = avg | standard = avg + 0.5σ | conservative = avg + σ
    confidence bar = 60 + 35 × false-breakout rate (clamped 50-99)

The scanner applies BOTH gates:
    confidence bar  = quality filter ("how sure am I?")
    expected move   = size filter   ("is this move big enough to matter?")

Usage:
    python volatility_engine.py          # continuous loop (own window)
    python volatility_engine.py --once   # single pass (testing)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")  # auto DST — never hardcode an offset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%I:%M %p")
logger = logging.getLogger("volatility_engine")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DB = os.path.join(BASE_DIR, "ohlcv_cache.db")
THRESHOLDS_JSON = os.path.join(BASE_DIR, "rotation_thresholds.json")
UNIVERSE_JSON = os.path.join(BASE_DIR, "volatility_universe.json")

# ── THE SINGLETON GUARD (Oct 8, red-first): one engine pair, ever ──
# The launcher's only duplicate defense was a racy CIM cmdline probe
# (Test-Component misses during the boot window — imports + the first
# full 12-week pull take seconds — and misses processes on a single
# query, the Aug 28 duplicate-scanner cause). The engine now guards
# ITSELF: claimed at the top of main(), BEFORE any boot work. THE
# ARBITER IS THE KERNEL, NOT THE FILE: the claim is an msvcrt
# byte-range lock (LK_NBLCK) on .volatility.lock — atomic (two racers
# cannot both hold byte 0), self-releasing (a killed engine's lock
# vanishes with the process, so a stale world self-heals on the next
# boot — no retake race is even possible), held for the process
# lifetime via a global fd. THE PID FILE IS A SEPARATE, ALWAYS-
# READABLE ARTIFACT (.volatility.pid — the house shape): only the lock
# holder writes it, so a refused racer reads the true holder's pid,
# and no diagnostic read ever touches a locked byte (mixing the
# arbiter and the pid in one file made the diagnostics race the lock —
# the Oct 8 drive caught it). The tasklist python-check only enriches
# the refusal, and an unverifiable holder (tasklist failure) still
# refuses — fail-closed, never "dead because the check broke".
# release_lock is owner-only: only the process holding the kernel lock
# may unlock and unlink. start_detached deliberately does NOT pre-clear
# either file (a helper-side Remove-Item races a fresh claim —
# TOCTOU): the engine is the single retaker. Two racing launch paths
# collapse to one pair: the second instance dies loudly at the guard
# instead of pulling 12 weeks of OHLCV twice.
VOL_PID_FILE = os.path.join(BASE_DIR, ".volatility.pid")
VOL_LOCK_FILE = os.path.join(BASE_DIR, ".volatility.lock")

try:
    import msvcrt as _msvcrt          # the fleet is Windows; the lock
except ImportError:                    # is the arbiter, so a platform
    _msvcrt = None                     # without it refuses (never runs
                                       # unguarded)
_LOCK_HOLDER_FD = None   # this process's kernel lock, for owner-only release


def _pid_alive_python(pid: int):
    """True only when the pid names a LIVE PYTHON process — the
    recycled-pid trap (tasklist sees ANY process on that pid) closed
    the cheap way (the house shape: ops/price_alert's own guard).
    Returns None when the check itself fails (tasklist error/timeout):
    an UNKNOWN holder is never treated as dead — refusal only, never
    an overwrite of a possibly-live engine's lock."""
    if not pid or pid <= 0:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout or ""
        return "python" in out.lower()
    except Exception:
        return None


def _read_lock_pid() -> int:
    """The pid file's recorded pid, 0 when unreadable/corrupt — the
    pid file is PLAIN TEXT and never locked (the arbiter is the
    separate .volatility.lock), so diagnostics can always read it."""
    try:
        return int(open(VOL_PID_FILE, encoding="utf-8").read().strip() or 0)
    except Exception:
        return 0


def claim_lock() -> bool:
    """Refuse a live engine double. False means the pair is taken (or
    the holder is unverifiable) — the caller must exit loud.

    ATOMICITY (the review majors): the arbiter is the kernel byte-
    range lock, not a check-then-write on the file — two racing
    processes cannot both hold byte 0, a killed holder's lock is
    released by the OS (the stale-file self-heal needs no retake
    race), and ONLY the lock holder writes its pid into the file (a
    refused racer never touches the content)."""
    global _LOCK_HOLDER_FD
    if _msvcrt is None:
        logger.error("no msvcrt on this platform — the singleton lock "
                     "cannot be enforced — refusing to run unguarded")
        return False
    try:
        fd = os.open(VOL_LOCK_FILE, os.O_CREAT | os.O_RDWR)
    except OSError as exc:
        logger.error("could not open the lock arbiter (%s) — refusing "
                     "to run unguarded", exc)
        return False
    try:
        _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
    except OSError:
        os.close(fd)
        old = _read_lock_pid()
        alive = _pid_alive_python(old)
        if alive is True:
            logger.warning("REFUSED: a Volatility Engine already runs "
                           "(pid %s) — one engine per pair; killing me, "
                           "not racing it", old)
        elif alive is None:
            logger.error("the pair is taken and the holder is "
                         "unverifiable (tasklist failed) — refusing "
                         "fail-closed; clear %s by hand if the engine "
                         "is truly dead", VOL_PID_FILE)
        else:
            # the kernel lock is held but the recorded pid is not a
            # live python: a live NON-engine process holds byte 0 —
            # still a held lock, still a refusal (never stomp it)
            logger.warning("REFUSED: the pid lock is held by a live "
                           "process that is not a recorded engine (pid "
                           "%s) — not stomping it", old)
        return False
    # OWNER: the kernel lock is ours for the process lifetime — now
    # (and only now) the pid file carries this process (plain text,
    # never locked — every refused racer reads the true holder).
    try:
        with open(VOL_PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError as exc:
        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
        os.close(fd)
        logger.error("could not stamp the pid lock (%s) — refusing", exc)
        return False
    _LOCK_HOLDER_FD = fd
    return True


def release_lock() -> None:
    """Clean-shutdown release — OWNER-ONLY: only the process holding
    the kernel lock (the claimer's fd) unlocks and unlinks; a non-
    owner's release is a no-op, so a stale file from a kill is left
    for the next boot's self-heal."""
    global _LOCK_HOLDER_FD
    fd = _LOCK_HOLDER_FD
    if fd is None:
        return
    _LOCK_HOLDER_FD = None
    try:
        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass
    for _f in (VOL_PID_FILE, VOL_LOCK_FILE):
        try:
            if os.path.exists(_f):
                os.unlink(_f)
        except Exception:
            pass

WINDOW_WEEKS = 12          # target — Kraken OHLC caps at ~30 days of 1h per pull,
                           # so the cache ACCUMULATES toward this over time
MIN_SAMPLES = 10           # weekday cells below this fall back to the universe median
WEEKEND_MIN_SAMPLES = 8    # weekends only get 2 days/week — lower bar
REFRESH_SECONDS = 3600     # incremental refresh cadence
FULL_PULL_EVERY_HOURS = 24
FETCH_WORKERS = 4

# (start_hour, end_hour, key, label, scan_interval_min)
ROTATIONS = [
    (0, 4,   "00-04", "R1 🌏 Asia",          15),
    (4, 8,   "04-08", "R2 🌅 Pre-US",        15),
    (8, 12,  "08-12", "R3 ☀️ EU/US Overlap",  7),
    (12, 16, "12-16", "R4 🌤 US Afternoon",  10),
    (16, 20, "16-20", "R5 🌆 US Late",       10),
    (20, 24, "20-24", "🌙 Cooldown (no trading)", 30),
]
ROTATION_KEYS = [r[2] for r in ROTATIONS]
TRADING_ROTATIONS = [r[2] for r in ROTATIONS[:5]]  # 20-24 has no buys
COOLDOWN_KEY = "20-24"

SCHEDULES = ("weekday", "weekend")

# Fallback bars (used by the scanner only when rotation_thresholds.json is
# missing/stale). Anchored to the previous hand-tuned schedule.
FALLBACK_BARS = {"00-04": 65, "04-08": 80, "08-12": 90, "12-16": 82, "16-20": 76, COOLDOWN_KEY: 95}


def _now_mst() -> datetime:
    return datetime.now(MST)


# ── Universe (54 challenge assets + captured gainers) ────────────────────

def load_universe() -> list[str]:
    """Challenge pairs from config + gainer pairs captured by the scanner."""
    try:
        from config import TradingConfig
        pairs = list(TradingConfig().trading_pairs)
    except Exception as exc:
        logger.error("Could not load TradingConfig: %s", exc)
        pairs = []
    try:
        with open(UNIVERSE_JSON, encoding="utf-8") as f:
            gainers = json.load(f)
        # Prune gainers not seen in 14 days — they left the momentum list
        cutoff = (_now_mst() - timedelta(days=14)).isoformat()
        fresh = {p: g for p, g in gainers.items()
                 if str(g.get("last_seen", "")) >= cutoff}
        pairs.extend(p for p in fresh if p not in pairs)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("Universe file unreadable: %s", exc)
    return pairs


def remember_gainer(pair: str, pct: float = 0.0) -> None:
    """Persist a captured gainer so thresholds exist for it tomorrow.
    Called by the background scanner when a new gainer signal fires."""
    try:
        data = {}
        if os.path.exists(UNIVERSE_JSON):
            with open(UNIVERSE_JSON, encoding="utf-8") as f:
                data = json.load(f)
        now_iso = _now_mst().isoformat()
        entry = data.get(pair, {})
        data[pair] = {
            "first_seen": entry.get("first_seen", now_iso),
            "last_seen": now_iso,
            "last_pct": pct or entry.get("last_pct", 0),
        }
        tmp = UNIVERSE_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, UNIVERSE_JSON)
    except Exception as exc:
        logger.warning("Could not persist gainer %s: %s", pair, exc)


# ── OHLCV cache (own sqlite — signals.db is never touched) ──────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB, timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ohlcv ("
        " pair TEXT NOT NULL, ts INTEGER NOT NULL,"
        " open REAL, high REAL, low REAL, close REAL, volume REAL,"
        " PRIMARY KEY (pair, ts))"
    )
    return conn


_local = threading.local()


def _exchange():
    """Thread-local ccxt.kraken instance (sync ccxt is not thread-safe)."""
    if getattr(_local, "exchange", None) is None:
        import ccxt
        _local.exchange = ccxt.kraken({"enableRateLimit": True})
    return _local.exchange


def _window_start_ms() -> int:
    start = _now_mst() - timedelta(weeks=WINDOW_WEEKS)
    return int(start.timestamp() * 1000)


def _fetch_history(pair: str, since_ms: int) -> list[list]:
    """Fetch 1h candles from since_ms to now.

    Kraken's OHLC endpoint returns [since, now] capped at the most recent
    ~720 candles — history older than ~30 days is simply not available,
    so one request is all there is. The cache accumulates the rolling
    window over time (nothing within the window is ever pruned).
    """
    ex = _exchange()
    try:
        return ex.fetch_ohlcv(pair, timeframe="1h", since=since_ms, limit=720) or []
    except Exception as exc:
        logger.warning("  fetch failed %s: %s", pair, exc)
        return []


def refresh_cache(pairs: list[str], full: bool = False) -> tuple[int, int]:
    """Incrementally pull candles into the cache. Returns (pairs_attempted, new_rows).

    Workers only touch the network; all sqlite ops happen on the main thread
    (sqlite connections refuse cross-thread use by default).
    """
    window_start = _window_start_ms()
    now_ms = int(time.time() * 1000)
    conn = _connect()

    # Decide the fetch cursor per pair (main thread — needs the DB)
    tasks: list[tuple[str, int]] = []
    for pair in pairs:
        if full:
            since = window_start
        else:
            row = conn.execute(
                "SELECT MAX(ts) FROM ohlcv WHERE pair=?", (pair,)).fetchone()
            max_ts = row[0] if row and row[0] else None
            if max_ts is None:
                since = window_start
            else:
                since = max_ts + 3_600_000
                if since >= now_ms:
                    continue  # already current
        tasks.append((pair, since))

    new_rows = 0

    def _work(item: tuple[str, int]) -> tuple[str, list[list]]:
        pair, since = item
        return pair, _fetch_history(pair, since)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        for pair, rows in executor.map(_work, tasks):
            if rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO ohlcv VALUES (?,?,?,?,?,?,?)",
                    [(pair, r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows])
                conn.commit()
                new_rows += len(rows)

    # prune candles older than the window
    conn.execute("DELETE FROM ohlcv WHERE ts < ?", (window_start,))
    conn.commit()
    conn.close()
    return len(tasks), new_rows


# ── Statistics ───────────────────────────────────────────────────────────

def _sessions(df):
    """Group 1h candles into complete 4h rotation sessions (Denver local).

    4h blocks aligned to midnight never cross a day boundary, so each
    session maps to exactly one local date. Yields dicts with the stats
    needed downstream. Incomplete sessions (gaps) are skipped.
    """
    import pandas as pd
    idx = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert(MST)
    d = df.copy()
    d.index = idx
    # .to_numpy() — plain Series assignment would align on the OLD index (all NaN)
    d["local_date"] = idx.dt.date.to_numpy()
    d["rot"] = (idx.dt.hour // 4).to_numpy()
    d["dow"] = idx.dt.dayofweek.to_numpy()  # 0=Mon .. 6=Sun
    d["vol_usd"] = d["volume"] * d["close"]

    for (ldate, rot), g in d.groupby(["local_date", "rot"]):
        if len(g) != 4:
            continue  # incomplete rotation (gap or partial at window edge)
        open_ = float(g["open"].iloc[0])
        close_ = float(g["close"].iloc[-1])
        if open_ <= 0:
            continue
        yield {
            "rot_key": ROTATION_KEYS[int(rot)],
            "dow": int(g["dow"].iloc[0]),
            "ret_pct": (close_ / open_ - 1) * 100,
            "spike_pct": (float(g["high"].max()) / open_ - 1) * 100,
            "drop_pct": (float(g["low"].min()) / open_ - 1) * 100,
            "vol_usd": float(g["vol_usd"].sum()),
        }


def _cell_stats(sessions: list[dict], min_samples: int = MIN_SAMPLES) -> dict | None:
    """Stats for one (asset, schedule, rotation) bucket."""
    n = len(sessions)
    if n == 0:
        return None
    abs_rets = [abs(s["ret_pct"]) for s in sessions]
    avg = sum(abs_rets) / n
    if n > 1:
        mean_sq = sum((x - avg) ** 2 for x in abs_rets) / (n - 1)
        std = mean_sq ** 0.5
    else:
        std = 0.0

    aggressive = avg
    standard = avg + 0.5 * std
    conservative = avg + std

    # False breakout: excursion beyond the standard threshold that
    # round-trips — closes flat (< 25% of the avg move).
    if n >= min_samples and avg > 0:
        false_bo = sum(
            1 for s in sessions
            if max(abs(s["spike_pct"]), abs(s["drop_pct"])) >= standard
            and abs(s["ret_pct"]) < 0.25 * avg
        ) / n
    else:
        false_bo = 0.0

    vols = sorted(s["vol_usd"] for s in sessions)
    median_vol = vols[n // 2] if n % 2 else (vols[n // 2 - 1] + vols[n // 2]) / 2

    bar = int(round(60 + 35 * false_bo))
    bar = max(50, min(99, bar))

    return {
        "samples": n,
        "reliable": n >= min_samples,
        "avg_move": round(avg, 3),
        "std": round(std, 3),
        "max_spike": round(max(s["spike_pct"] for s in sessions), 2),
        "max_drop": round(min(s["drop_pct"] for s in sessions), 2),
        "median_volume_usd": round(median_vol, 0),
        "false_breakout_rate": round(false_bo, 3),
        "confidence_bar": bar,
        "thresholds": {
            "aggressive": round(aggressive, 3),
            "standard": round(standard, 3),
            "conservative": round(conservative, 3),
        },
    }


def _liquidity_note(vol_usd: float) -> str:
    if vol_usd < 250_000:
        return "thin"
    if vol_usd < 1_000_000:
        return "ok"
    return "deep"


def _risk_note(false_bo: float) -> str:
    if false_bo < 0.20:
        return "low"
    if false_bo < 0.35:
        return "medium"
    return "high"


def compute_thresholds() -> dict:
    """Read the cache and produce the full thresholds document."""
    import pandas as pd
    conn = _connect()
    window_start = _window_start_ms()
    universe: dict[str, dict] = {}
    per_cell_values: dict[tuple, list[dict]] = {}  # (schedule, rot_key) -> [cell]
    earliest_ts = None

    pairs = [r[0] for r in conn.execute(
        "SELECT DISTINCT pair FROM ohlcv").fetchall()]

    for pair in pairs:
        df = pd.read_sql_query(
            "SELECT ts, open, high, low, close, volume FROM ohlcv "
            "WHERE pair=? AND ts>=? ORDER BY ts",
            conn, params=(pair, window_start))
        if df.empty or len(df) < 24:
            continue
        pair_min = int(df["ts"].min())
        earliest_ts = pair_min if earliest_ts is None else min(earliest_ts, pair_min)
        by_schedule: dict[str, dict[str, list[dict]]] = {"weekday": {}, "weekend": {}}
        for s in _sessions(df):
            schedule = "weekend" if s["dow"] >= 5 else "weekday"
            by_schedule[schedule].setdefault(s["rot_key"], []).append(s)

        pair_doc = {}
        for schedule in SCHEDULES:
            schedule_doc = {}
            min_samples = MIN_SAMPLES if schedule == "weekday" else WEEKEND_MIN_SAMPLES
            for rot_key in ROTATION_KEYS:
                cell = _cell_stats(by_schedule[schedule].get(rot_key, []),
                                   min_samples=min_samples)
                if cell:
                    cell["notes"] = (
                        f"liquidity {_liquidity_note(cell['median_volume_usd'])}, "
                        f"false-breakout risk {_risk_note(cell['false_breakout_rate'])}"
                    )
                    schedule_doc[rot_key] = cell
                    if cell["reliable"]:
                        per_cell_values.setdefault((schedule, rot_key), []).append(cell)
            if schedule_doc:
                pair_doc[schedule] = schedule_doc
        if pair_doc:
            universe[pair] = pair_doc
    conn.close()

    # Universe median per (schedule, rotation) — the fallback for assets
    # without reliable history (freshly captured gainers).
    median: dict[str, dict] = {}
    for schedule in SCHEDULES:
        median[schedule] = {}
        for rot_key in ROTATION_KEYS:
            cells = per_cell_values.get((schedule, rot_key), [])
            if not cells:
                continue
            def _m(key: str) -> float:
                vals = sorted(c[key] for c in cells)
                n = len(vals)
                return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
            bar_vals = sorted(c["confidence_bar"] for c in cells)
            n = len(bar_vals)
            bar = bar_vals[n // 2] if n % 2 else round((bar_vals[n // 2 - 1] + bar_vals[n // 2]) / 2)
            median[schedule][rot_key] = {
                "assets": len(cells),
                "avg_move": round(_m("avg_move"), 3),
                "std": round(_m("std"), 3),
                "max_spike": round(_m("max_spike"), 2),
                "max_drop": round(_m("max_drop"), 2),
                "median_volume_usd": round(_m("median_volume_usd"), 0),
                "false_breakout_rate": round(_m("false_breakout_rate"), 3),
                "confidence_bar": int(bar),
                "thresholds": {
                    "aggressive": round(_m("avg_move"), 3),
                    "standard": round(_m("avg_move") + 0.5 * _m("std"), 3),
                    "conservative": round(_m("avg_move") + _m("std"), 3),
                },
            }

    data_weeks = 0.0
    if earliest_ts:
        data_weeks = round((time.time() * 1000 - earliest_ts) / (7 * 86400 * 1000), 1)

    return {
        "generated_at": _now_mst().isoformat(),
        "window_weeks": WINDOW_WEEKS,
        "data_weeks": data_weeks,  # actual depth in cache (grows toward window_weeks)
        "min_samples": MIN_SAMPLES,
        "universe": universe,
        "median": median,
    }


def write_thresholds(doc: dict) -> None:
    tmp = THRESHOLDS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    os.replace(tmp, THRESHOLDS_JSON)


# ── Loader for the scanner / dashboard ──────────────────────────────────

_THRESH_CACHE: dict = {"mtime": 0.0, "data": None, "checked": 0.0}


def load_rotation_thresholds(max_age_s: float = 60.0) -> dict | None:
    """mtime-cached read of rotation_thresholds.json. None if unavailable."""
    now = time.time()
    if _THRESH_CACHE["data"] is not None and now - _THRESH_CACHE["checked"] < max_age_s:
        return _THRESH_CACHE["data"]
    _THRESH_CACHE["checked"] = now
    try:
        mtime = os.path.getmtime(THRESHOLDS_JSON)
        if mtime != _THRESH_CACHE["mtime"] or _THRESH_CACHE["data"] is None:
            with open(THRESHOLDS_JSON, encoding="utf-8") as f:
                _THRESH_CACHE["data"] = json.load(f)
            _THRESH_CACHE["mtime"] = mtime
        return _THRESH_CACHE["data"]
    except Exception:
        return None


def get_move_threshold(pair: str, rot_key: str, schedule: str,
                       level: str = "standard") -> float | None:
    """Expected-move gate for a pair in the current rotation. None = no gate."""
    doc = load_rotation_thresholds()
    if not doc:
        return None
    cell = (doc.get("universe", {}).get(pair, {})
            .get(schedule, {}).get(rot_key))
    if not cell or not cell.get("reliable"):
        cell = doc.get("median", {}).get(schedule, {}).get(rot_key)
    if not cell:
        return None
    return cell.get("thresholds", {}).get(level)


def get_confidence_bar(pair: str, rot_key: str, schedule: str) -> int | None:
    """Data-calibrated confidence bar for a pair/rotation. None = no data."""
    doc = load_rotation_thresholds()
    if not doc:
        return None
    cell = (doc.get("universe", {}).get(pair, {})
            .get(schedule, {}).get(rot_key))
    if not cell or not cell.get("reliable"):
        cell = doc.get("median", {}).get(schedule, {}).get(rot_key)
    if not cell:
        return None
    return cell.get("confidence_bar")


# ── Console tables ───────────────────────────────────────────────────────

def _fmt_vol(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1e6:.1f}M"
    if v >= 1_000:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def _schedule_table(doc: dict, schedule: str) -> str:
    lines = []
    title = "WEEKDAY SCHEDULE (Mon-Fri)" if schedule == "weekday" else "WEEKEND SCHEDULE (Sat-Sun)"
    lines.append("")
    lines.append(f"  ┌─ {title} " + "─" * max(0, 66 - len(title)) + "┐")
    lines.append("  │ Rotation  AvgMove    Std   AGG   STD   CONS  MaxSpike MaxDrop  Volume   FalseBO  Bar │")
    lines.append("  │ " + "─" * 94 + " │")
    med = doc.get("median", {}).get(schedule, {})
    for rot_key in ROTATION_KEYS:
        label = f"{rot_key}"
        cell = med.get(rot_key)
        if not cell:
            lines.append(f"  │ {label:<8}  — insufficient data —{'':>60} │")
            continue
        t = cell["thresholds"]
        lines.append(
            f"  │ {label:<8} {cell['avg_move']:>6.2f}% {cell['std']:>6.2f} "
            f"{t['aggressive']:>5.2f} {t['standard']:>5.2f} {t['conservative']:>5.2f} "
            f"{cell['max_spike']:>7.1f}% {cell['max_drop']:>6.1f}% "
            f"{_fmt_vol(cell['median_volume_usd']):>8} {cell['false_breakout_rate']*100:>6.1f}% {cell['confidence_bar']:>4} │"
        )
    lines.append("  └" + "─" * 96 + "┘")
    return "\n".join(lines)


def _differences_summary(doc: dict) -> str:
    wd = doc.get("median", {}).get("weekday", {})
    we = doc.get("median", {}).get("weekend", {})
    lines = ["", "  SUMMARY — weekend vs weekday"]
    for rot_key in TRADING_ROTATIONS:
        w, e = wd.get(rot_key), we.get(rot_key)
        if not w or not e:
            continue
        move_ratio = e["avg_move"] / w["avg_move"] if w["avg_move"] else 0
        bar_delta = e["confidence_bar"] - w["confidence_bar"]
        lines.append(
            f"    {rot_key}: weekend moves {move_ratio:.2f}x weekday, "
            f"false-BO {w['false_breakout_rate']*100:.0f}%→{e['false_breakout_rate']*100:.0f}%, "
            f"bar {w['confidence_bar']}→{e['confidence_bar']} ({bar_delta:+d})"
        )
    return "\n".join(lines)


def _dow_notes(doc: dict) -> str:
    """Saturday morning rush + Sunday evening reversal observations."""
    lines = ["", "  DAY-OF-WEEK OBSERVATIONS"]
    universe = doc.get("universe", {})
    found_sat = found_sun = 0
    sat_moves, sun_moves = [], []
    for pair_doc in universe.values():
        we = pair_doc.get("weekend", {})
        r3 = we.get("08-12")
        if r3 and r3.get("reliable"):
            sat_moves.append(r3["avg_move"])
        r5 = we.get("16-20")
        if r5 and r5.get("reliable"):
            sun_moves.append(r5["avg_move"])
    if sat_moves:
        found_sat = len(sat_moves)
        avg_sat = sum(sat_moves) / len(sat_moves)
        lines.append(f"    Saturday morning rush (Sat 08-12): avg move {avg_sat:.2f}% across {found_sat} assets")
    if sun_moves:
        found_sun = len(sun_moves)
        avg_sun = sum(sun_moves) / len(sun_moves)
        lines.append(f"    Sunday evening (Sun 16-20): avg move {avg_sun:.2f}% across {found_sun} assets — watch for Monday reversal")
    if not sat_moves and not sun_moves:
        lines.append("    (not enough weekend history yet)")
    return "\n".join(lines)


def _backtest_notes(doc: dict) -> str:
    n_assets = len(doc.get("universe", {}))
    data_weeks = doc.get("data_weeks", 0)
    return (
        "\n  BACKTESTING NOTES\n"
        "  ─────────────────\n"
        f"  • Universe: {n_assets} assets × rolling 1h candles, Kraken-only volume.\n"
        f"  • Data depth now: {data_weeks} weeks (target {WINDOW_WEEKS}). Kraken's OHLC endpoint\n"
        "    only serves the most recent ~30 days of 1h candles — the cache ACCUMULATES\n"
        "    continuously and the stats deepen toward the 12-week target over time.\n"
        f"  • Reliability: weekday cells need ≥ {MIN_SAMPLES} sessions, weekend ≥ {WEEKEND_MIN_SAMPLES}.\n"
        "    Unreliable cells fall back to the universe median for that rotation.\n"
        "  • Walk-forward check: recompute on a trailing window, then simulate entries whose projected\n"
        "    move clears the standard threshold — measure TP-before-stop rate vs the old static bars.\n"
        "  • Fee drag warning (Aug 21 audit): avg loss ≈ one round of taker fees. Keep the $100 minimum\n"
        "    trade and MAX_CONCURRENT_POSITIONS=3 — the expected-move gate should cut low-edge churn.\n"
        "  • The two gates are independent vetoes: confidence bar (quality) AND expected move (size).\n"
        "    A signal must clear BOTH to reach the bridge."
    )


def print_report(doc: dict) -> None:
    print("\n" + "=" * 100)
    print(f"  KRAKEN SCOUT — VOLATILITY ENGINE  |  generated {doc['generated_at'][:19]}  |  "
          f"{doc.get('data_weeks', 0)}w of {WINDOW_WEEKS}w target")
    print("=" * 100)
    print(_schedule_table(doc, "weekday"))
    print(_schedule_table(doc, "weekend"))
    print(_differences_summary(doc))
    print(_dow_notes(doc))
    print(_backtest_notes(doc))
    print("\n  AGG = aggressive (avg move)   STD = standard noise floor (avg + 0.5σ)   CONS = conservative (avg + σ)")
    print("  Bar = data-calibrated confidence gate. Scanner applies BOTH gates before writing to the bridge.")
    print("=" * 100 + "\n")


# ── Main ─────────────────────────────────────────────────────────────────

def run_once(full_pull: bool = False) -> dict | None:
    pairs = load_universe()
    if not pairs:
        logger.error("Empty universe — aborting pass")
        return None
    logger.info("Universe: %d assets (%d-week window)", len(pairs), WINDOW_WEEKS)
    pairs_ok, new_rows = refresh_cache(pairs, full=full_pull)
    logger.info("Cache refreshed: %d pairs ok, %d new candles", pairs_ok, new_rows)
    doc = compute_thresholds()
    if not doc.get("universe"):
        logger.error("No cached data produced thresholds — keeping previous JSON")
        return None
    write_thresholds(doc)
    logger.info("Wrote %s (%d assets)", os.path.basename(THRESHOLDS_JSON), len(doc["universe"]))
    print_report(doc)
    return doc


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kraken Scout Volatility Engine")
    parser.add_argument("--once", action="store_true", help="single pass then exit")
    args = parser.parse_args()

    # THE GUARD RUNS BEFORE ANY BOOT WORK (the race is the boot window:
    # imports + the first full pull are exactly when a second launcher
    # sees nothing and spawns again). A refused claim exits loudly —
    # the launcher's early-death capture names the cause.
    if not claim_lock():
        raise SystemExit(3)

    logger.info("=" * 60)
    logger.info("  Kraken Scout — Volatility Engine")
    logger.info("  %d-week rolling window | rotations 00-04/04-08/08-12/12-16/16-20", WINDOW_WEEKS)
    logger.info("  20:00-24:00 = cooldown (no trading)")
    logger.info("  Press Ctrl+C to stop")
    logger.info("=" * 60)

    try:
        _run_loop(args)
    finally:
        release_lock()
    logger.info("Volatility Engine stopped.")


def _run_loop(args) -> None:
    """The pull/refresh loop, extracted so main's guard + release
    bracket it exactly once (the lock must outlive every exit path —
    the finally is the shutdown release; a hard kill leaves the stale
    file for the next boot's retake, which the recycled-pid check makes
    safe)."""
    full_pull = True  # first pass always pulls the whole window
    last_full = time.time()
    while True:
        try:
            run_once(full_pull=full_pull)
            full_pull = False
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.error("Pass failed: %s", exc)

        if args.once:
            break

        # hourly incremental refresh; full re-pull daily to heal gaps
        sleep_until_full = FULL_PULL_EVERY_HOURS * 3600 - (time.time() - last_full)
        if sleep_until_full <= 0:
            full_pull = True
            last_full = time.time()
        logger.info("Next refresh in %d min%s",
                    REFRESH_SECONDS // 60,
                    " (full re-pull)" if full_pull else "")
        try:
            time.sleep(REFRESH_SECONDS)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nVolatility Engine stopped.")
