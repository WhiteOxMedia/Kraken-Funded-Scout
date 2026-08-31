"""Signal bridge between Kraken Scout and the XRP Trading Bot.

Uses SQLite for atomic, queryable, crash-safe signal storage.
Both projects read/write the same database file.

Database: C:\\Users\\birds\\Workspace\\kraken_scout\\signals.db

Signal lifecycle:
  pending → acknowledged → filled → closed
                         → rejected
  pending → expired (validity window passed)
  pending → cancelled (opposite signal arrived)

Each signal includes:
  - valid_until: buy=5min, sell=10min (stale signals auto-expire)
  - cooldown_until: 30min after signal (prevents re-signaling same asset)
  - price_at_signal: for slippage tracking
  - acknowledged_at / executed_at / execution_price: execution feedback
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from zoneinfo import ZoneInfo
MST = ZoneInfo("America/Denver")  # Auto DST: MDT (UTC-6) summer, MST (UTC-7) winter

# Shared database — both projects use this file
DB_PATH = Path(r"C:\Users\birds\Workspace\kraken_scout\signals.db")

# Kill switch lock file — when present, no new trades
KILL_SWITCH_FILE = Path(r"C:\Users\birds\Workspace\kraken_scout\kill_switch.lock")


def _get_db() -> sqlite3.Connection:
    """Get a SQLite connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
    return conn


def init_db():
    """Create the signals table if it doesn't exist."""
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                signal_id TEXT PRIMARY KEY,
                pair TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                reason TEXT,
                timestamp TEXT NOT NULL,
                valid_until TEXT NOT NULL,
                cooldown_until TEXT,
                price_at_signal REAL,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                risk_reward REAL,
                position_size_usd REAL,
                source TEXT DEFAULT 'scout_auto',
                status TEXT DEFAULT 'pending',
                acknowledged_at TEXT,
                executed_at TEXT,
                execution_price REAL,
                order_id TEXT,
                execution_reason TEXT,
                closed_at TEXT,
                pnl REAL,
                highest_price REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON signals(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pair ON signals(pair)")
        # Daily balance snapshots for the circuit breaker
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_balance (
                date TEXT PRIMARY KEY,
                starting_balance REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        # Migration: add highest_price column to existing DBs
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(signals)").fetchall()]
        if "highest_price" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN highest_price REAL")
        # Migration (Aug 31): tide stamp — the fleet state at signal time.
        # Data collection for the tide sizing factor (user rule: collect the
        # data before picking a FLAT number). NULL/'' = pre-stamp signal.
        if "tide_state" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN tide_state TEXT")
        if "tide_pct" not in cols:
            conn.execute("ALTER TABLE signals ADD COLUMN tide_pct REAL")
        conn.commit()


# ── Balance-based circuit breaker ────────────────────────────────────────

MAX_DAILY_LOSS_PCT = 10.0  # Kill switch triggers if balance drops 10% from day start


def record_daily_start_balance(balance: float) -> str:
    """Record today's starting account balance. Called once at first check of the day.

    If today's entry already exists, it's not overwritten.
    Returns today's date string (YYYY-MM-DD MST).
    """
    init_db()
    today = _now_mst().strftime("%Y-%m-%d")
    now = _now_iso()
    with _get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO daily_balance (date, starting_balance, timestamp) VALUES (?, ?, ?)",
            (today, balance, now),
        )
        conn.commit()
    return today


def get_daily_start_balance() -> float | None:
    """Return today's starting account balance, or None if not recorded yet."""
    init_db()
    today = _now_mst().strftime("%Y-%m-%d")
    with _get_db() as conn:
        row = conn.execute(
            "SELECT starting_balance FROM daily_balance WHERE date=?", (today,)
        ).fetchone()
        return float(row["starting_balance"]) if row else None


# ── Trailing stop ────────────────────────────────────────────────────────

TRAILING_STOP_PCT = 8.0  # Sell if price drops 8% from its highest point


def update_trailing_high(pair: str, current_price: float) -> float:
    """Track the highest price since entry for an open position.

    Returns the tracked high (updated if current_price exceeds it).
    """
    init_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT signal_id, highest_price, execution_price FROM signals "
            "WHERE pair=? AND action='buy' AND status='filled' "
            "ORDER BY executed_at DESC LIMIT 1",
            (pair,),
        ).fetchone()
        if not row:
            return 0.0

        # Seed the high with the fill price on first check
        current_high = row["highest_price"] or row["execution_price"] or current_price
        if current_price > current_high:
            conn.execute(
                "UPDATE signals SET highest_price=? WHERE signal_id=?",
                (current_price, row["signal_id"]),
            )
            conn.commit()
            current_high = current_price
        return current_high


def get_trailing_high(pair: str) -> float | None:
    """READ-ONLY: the auto system's tracked high for an open position.

    Manual/advisory tools (console Quick Check, Exit Check, Auto-Watch) use
    this to stay in sync with the scanner's tracking WITHOUT writing to it.
    """
    init_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT highest_price, execution_price FROM signals "
            "WHERE pair=? AND action='buy' AND status='filled' "
            "ORDER BY executed_at DESC LIMIT 1",
            (pair,),
        ).fetchone()
    if not row:
        return None
    return row["highest_price"] or row["execution_price"] or None


def check_trailing_stop(pair: str, current_price: float) -> tuple[bool, float]:
    """Check if a position has hit its trailing stop.

    Returns (triggered, high). Triggers when current_price has dropped
    TRAILING_STOP_PCT% below the tracked high AND the position has some
    profit cushion above entry (high must be above entry so we only
    trail-stop winners, never below the entry stop-loss).
    """
    init_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT highest_price, execution_price FROM signals "
            "WHERE pair=? AND action='buy' AND status='filled' "
            "ORDER BY executed_at DESC LIMIT 1",
            (pair,),
        ).fetchone()
    if not row:
        return False, 0.0

    entry = row["execution_price"] or 0.0
    high = row["highest_price"] or entry or current_price

    # Only trail-stop when the position is in profit (high above entry).
    # Losers are handled by the regular stop-loss / exit checks.
    if not entry or high <= entry:
        return False, high

    stop_level = high * (1 - TRAILING_STOP_PCT / 100)
    triggered = current_price <= stop_level
    return triggered, high


def _now_mst() -> datetime:
    return datetime.now(MST)


def _now_iso() -> str:
    return _now_mst().isoformat()


def _is_expired(valid_until: str) -> bool:
    """Check if a signal's validity window has passed."""
    try:
        vu = datetime.fromisoformat(valid_until)
        return _now_mst() > vu
    except Exception:
        return True


def expire_old_signals():
    """Mark all pending signals past their valid_until as expired."""
    init_db()
    now = _now_iso()
    with _get_db() as conn:
        conn.execute(
            "UPDATE signals SET status='expired' WHERE status='pending' AND valid_until < ?",
            (now,),
        )
        conn.commit()


def is_in_cooldown(pair: str) -> bool:
    """Check if a pair is still in cooldown (can't receive new signals).

    Includes 'closed' so the 4-hour loss cooldown on losing trades blocks
    re-buying the same loser over and over.
    """
    init_db()
    now = _now_iso()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM signals WHERE pair=? AND cooldown_until > ? AND status IN ('pending','acknowledged','filled','closed') LIMIT 1",
            (pair, now),
        ).fetchone()
        return row is not None


def is_already_holding(pair: str) -> bool:
    """Check if we already have an open (filled, not closed) buy for this pair."""
    init_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM signals WHERE pair=? AND action='buy' AND status='filled' LIMIT 1",
            (pair,),
        ).fetchone()
        return row is not None


def count_dca_signals(pair: str) -> int:
    """Count DCA tranches already written for a pair (pending or filled).

    Scout allows max 1 DCA tranche per position (initial buy + 1 add).
    """
    init_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM signals "
            "WHERE pair=? AND source='scout_dca' AND status IN ('pending','acknowledged','filled')",
            (pair,),
        ).fetchone()
        return row["cnt"] if row else 0


def get_open_position_count() -> int:
    """Count how many positions are currently open (filled, not closed)."""
    init_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM signals WHERE action='buy' AND status='filled'"
        ).fetchone()
        return row["cnt"] if row else 0


MAX_CONCURRENT_POSITIONS = 1  # ONE position at a time — all capital on the single
                              # best pick (user directive, Aug 25). A swap needs a
                              # challenger CLEARLY stronger than the incumbent's
                              # entry conviction (+10, 2h hold, winners protected) —
                              # no more multi-position fee churn.


def write_buy_signal(
    pair: str,
    action: str,
    confidence: float,
    entry_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    risk_reward: float | None,
    position_size_usd: float | None,
    reasons: list[str],
    source: str = "scout_auto",
    reason_summary: str = "",
    min_confidence: float = 75,  # Adaptive — passed by scanner based on time of day
    is_dca: bool = False,  # DCA tranche: adds to an existing position
    tide_state: str = "",  # fleet state at signal time (RISING/FLAT/OUT/UNKNOWN)
    tide_pct: float | None = None,  # fleet breadth % at signal time
) -> str | None:
    """Write a buy/sell signal to the database.

    Returns signal_id if written, None if rejected (cooldown, duplicate, etc.).

    DCA mode (is_dca=True, source='scout_dca'): skips the already-holding and
    max-positions checks (the bot folds it into the existing position) and
    relaxes the R:R gate (averaging down improves the blended entry).
    """
    init_db()

    # Pre-entry checklist — confidence threshold is adaptive by time of day
    if action not in ("buy", "sell"):
        return None
    if confidence < min_confidence:
        logger.info("Confidence %.1f%% below threshold %.1f%% for %s — not writing", confidence, min_confidence, pair)
        return None
    if not entry_price or not stop_loss or not take_profit:
        return None
    if not is_dca and (not risk_reward or risk_reward < 2.0):
        return None
    if any("exhaustion" in r.lower() for r in reasons):
        return None
    if any("breakout failure" in r.lower() for r in reasons):
        return None

    # Check kill switch
    if is_kill_switch_active():
        logger.info("Kill switch active — no new signals")
        return None

    # For buys: check cooldown, duplicate, and position limit
    if action == "buy" and not is_dca:
        # At max positions we still WRITE the signal as pending — the bot's
        # challenge system (head-to-head: new signal vs weakest incumbent)
        # decides there. Rejecting here would preempt the challenge and let
        # incumbents keep their seats without ever being tested.
        if get_open_position_count() >= MAX_CONCURRENT_POSITIONS:
            logger.info("At max positions — writing anyway; the bot's challenge system will test this challenger")
        if is_already_holding(pair):
            logger.info("Already holding %s — not writing new buy signal", pair)
            return None
    # DCA still respects cooldown (a fresh buy's 30-min window must have passed)
    if action == "buy" and is_in_cooldown(pair):
        logger.info("Cooldown active for %s — not writing signal", pair)
        return None

    now = _now_mst()
    signal_id = str(uuid.uuid4())[:8]

    # Validity window: buys=5min, sells=10min
    valid_minutes = 5 if action == "buy" else 10
    valid_until = (now + timedelta(minutes=valid_minutes)).isoformat()

    # Cooldown: 30 min after signal
    cooldown_until = (now + timedelta(minutes=30)).isoformat()

    # Reason summary
    if not reason_summary:
        reason_summary = "; ".join(reasons[:3])[:200]

    with _get_db() as conn:
        conn.execute("""
            INSERT INTO signals (
                signal_id, pair, action, confidence, reason,
                timestamp, valid_until, cooldown_until,
                price_at_signal, entry_price, stop_loss, take_profit,
                risk_reward, position_size_usd, source, status,
                tide_state, tide_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """, (
            signal_id, pair, action, confidence, reason_summary,
            now.isoformat(), valid_until, cooldown_until,
            entry_price, entry_price, stop_loss, take_profit,
            risk_reward, position_size_usd, source,
            tide_state, tide_pct,
        ))
        conn.commit()

    logger.info("Signal written: %s %s (%.0f%%) [%s] valid_until=%s",
                action.upper(), pair, confidence, signal_id, valid_until)
    return signal_id


def write_exit_signal(pair: str, reason: str, source: str = "scout_exit_check") -> str | None:
    """Write an exit signal for the XRP bot to close a position."""
    init_db()

    # Don't write duplicate exit signals
    with _get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM signals WHERE pair=? AND action='sell' AND status IN ('pending','acknowledged') LIMIT 1",
            (pair,),
        ).fetchone()
        if existing:
            logger.info("Exit signal already pending for %s", pair)
            return None

    now = _now_mst()
    signal_id = str(uuid.uuid4())[:8]
    valid_until = (now + timedelta(minutes=10)).isoformat()

    with _get_db() as conn:
        conn.execute("""
            INSERT INTO signals (
                signal_id, pair, action, confidence, reason,
                timestamp, valid_until, source, status
            ) VALUES (?, ?, 'sell', 0, ?, ?, ?, ?, 'pending')
        """, (
            signal_id, pair, reason,
            now.isoformat(), valid_until, source,
        ))
        conn.commit()

    logger.info("Exit signal written: SELL %s [%s]", pair, signal_id)
    return signal_id


def get_pending_signals() -> list[dict]:
    """Return all valid pending signals (not expired)."""
    init_db()
    expire_old_signals()
    now = _now_iso()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM signals WHERE status='pending' AND valid_until > ? ORDER BY timestamp",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_open_positions() -> list[dict]:
    """Return all filled-but-not-closed signals (open positions)."""
    init_db()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM signals WHERE status='filled' ORDER BY executed_at",
        ).fetchall()
        return [dict(r) for r in rows]


def reset_all_positions(keep_pairs: list[str] | None = None) -> dict:
    """Reset positions when the time block rotates.

    - Marks filled positions as closed (they'll be sold via reset_positions.py)
      EXCEPT pairs in keep_pairs (winners riding the trend — trailing stop handles them)
    - Cancels all pending signals (stale from the old block)
    - Returns dict with counts: {closed, cancelled, kept}
    """
    init_db()
    now = _now_iso()
    keep = set(keep_pairs or [])
    with _get_db() as conn:
        # Count before
        filled_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM signals WHERE status='filled'"
        ).fetchone()["cnt"]
        pending_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM signals WHERE status='pending'"
        ).fetchone()["cnt"]
        acknowledged_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM signals WHERE status='acknowledged'"
        ).fetchone()["cnt"]

        # Close filled positions except winners being kept
        if keep:
            placeholders = ",".join("?" for _ in keep)
            conn.execute(
                f"UPDATE signals SET status='closed', closed_at=?, pnl=0 "
                f"WHERE status='filled' AND pair NOT IN ({placeholders})",
                (now, *keep),
            )
        else:
            conn.execute(
                "UPDATE signals SET status='closed', closed_at=?, pnl=0 WHERE status='filled'",
                (now,),
            )
        # Cancel all pending and acknowledged signals (even for kept pairs —
        # stale entries from the old block; new ones can be written fresh)
        conn.execute(
            "UPDATE signals SET status='cancelled' WHERE status IN ('pending','acknowledged')",
        )
        conn.commit()

    kept_count = sum(1 for p in keep if p)
    logger.info("Reset: closed %d filled (kept %d winners), cancelled %d pending/%d acknowledged",
                filled_count - kept_count, kept_count, pending_count, acknowledged_count)
    return {"closed": filled_count - kept_count, "cancelled": pending_count + acknowledged_count, "kept": kept_count}


def acknowledge(signal_id: str):
    """Bot acknowledges it has received the signal and is about to execute."""
    init_db()
    with _get_db() as conn:
        conn.execute(
            "UPDATE signals SET status='acknowledged', acknowledged_at=? WHERE signal_id=? AND status='pending'",
            (_now_iso(), signal_id),
        )
        conn.commit()


def mark_executed(signal_id: str, order_id: str, fill_price: float):
    """Mark a signal as filled (order executed on Kraken)."""
    init_db()
    with _get_db() as conn:
        conn.execute(
            "UPDATE signals SET status='filled', executed_at=?, execution_price=?, order_id=? WHERE signal_id=?",
            (_now_iso(), fill_price, order_id, signal_id),
        )
        conn.commit()
    logger.info("Signal %s filled @ %s (order %s)", signal_id, fill_price, order_id)


def mark_rejected(signal_id: str, reason: str):
    """Mark a signal as rejected (pre-check failed)."""
    init_db()
    with _get_db() as conn:
        conn.execute(
            "UPDATE signals SET status='rejected', execution_reason=? WHERE signal_id=?",
            (reason, signal_id),
        )
        conn.commit()
    logger.info("Signal %s rejected: %s", signal_id, reason)


LOSS_COOLDOWN_HOURS = 4  # Don't re-buy an asset for 4h after a losing trade


def mark_closed(signal_id: str, pnl: float):
    """Mark a position as closed (exit executed).

    Losing trades put the pair on a 4-hour cooldown so we stop
    churning the same losers over and over (PENGU/RENDER/WLD pattern).
    """
    init_db()
    now = _now_mst()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT pair FROM signals WHERE signal_id=?", (signal_id,)
        ).fetchone()
        conn.execute(
            "UPDATE signals SET status='closed', closed_at=?, pnl=? WHERE signal_id=?",
            (now.isoformat(), pnl, signal_id),
        )
        # 4h cooldown on losers
        if row and pnl is not None and pnl < 0:
            cooldown_until = (now + timedelta(hours=LOSS_COOLDOWN_HOURS)).isoformat()
            conn.execute(
                "UPDATE signals SET cooldown_until=? WHERE signal_id=?",
                (cooldown_until, signal_id),
            )
        conn.commit()
    if row and pnl is not None and pnl < 0:
        logger.info("Signal %s closed (PnL: %.4f) — %s on %dh loss cooldown",
                    signal_id, pnl, row["pair"], LOSS_COOLDOWN_HOURS)
    else:
        logger.info("Signal %s closed (PnL: %.4f)", signal_id, pnl)


def mark_closed_by_pair(pair: str, pnl: float):
    """Close the most recent filled signal for a pair."""
    init_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT signal_id FROM signals WHERE pair=? AND status='filled' ORDER BY executed_at DESC LIMIT 1",
            (pair,),
        ).fetchone()
        if row:
            mark_closed(row["signal_id"], pnl)


def get_recent_signals(limit: int = 20) -> list[dict]:
    """Return recent signals for display/auditing."""
    init_db()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_daily_pnl() -> float:
    """Sum P&L from all signals closed today (MST)."""
    init_db()
    today_start = _now_mst().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) as total FROM signals WHERE status='closed' AND closed_at >= ?",
            (today_start,),
        ).fetchone()
        return float(row["total"]) if row else 0.0


def get_consecutive_losses() -> int:
    """Count consecutive losing closed signals."""
    init_db()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT pnl FROM signals WHERE status='closed' ORDER BY closed_at DESC LIMIT 10"
        ).fetchall()
        count = 0
        for r in rows:
            if r["pnl"] is not None and r["pnl"] < 0:
                count += 1
            else:
                break
        return count


# ── Kill switch ──────────────────────────────────────────────────────────

def trigger_kill_switch(reason: str):
    """Activate the kill switch — stops all new trades and flattens positions.

    Writes the lock file, then runs reset_positions.py to sell everything on Kraken.
    """
    KILL_SWITCH_FILE.write_text(f"{_now_iso()}\n{reason}\n")
    logger.error("KILL SWITCH ACTIVATED: %s", reason)
    logger.error("Flattening all positions on Kraken...")

    # Run reset_positions.py to sell everything
    try:
        import subprocess
        import os
        bot_dir = r"C:\Users\birds\Workspace\kraken_xrp_trading_bot"
        bot_python = os.path.join(bot_dir, ".venv", "Scripts", "python.exe")
        if not os.path.exists(bot_python):
            bot_python = "python"
        result = subprocess.run(
            [bot_python, "reset_positions.py"],
            cwd=bot_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    logger.error("  flatten: %s", line.strip())
        else:
            logger.error("  reset_positions.py failed: %s", result.stderr.strip()[:200])
    except Exception as exc:
        logger.error("  Could not run reset_positions.py: %s", exc)

    # Also clear the signal bridge DB
    try:
        reset_all_positions()
        logger.error("  Bridge DB cleared")
    except Exception as exc:
        logger.error("  Bridge reset failed: %s", exc)


def is_kill_switch_active() -> bool:
    """Check if the kill switch is active."""
    return KILL_SWITCH_FILE.exists()


def reset_kill_switch():
    """Manually reset the kill switch."""
    if KILL_SWITCH_FILE.exists():
        KILL_SWITCH_FILE.unlink()
        logger.info("Kill switch reset")


def get_kill_switch_reason() -> str | None:
    """Return the kill switch reason if active."""
    if KILL_SWITCH_FILE.exists():
        lines = KILL_SWITCH_FILE.read_text().strip().split("\n")
        return lines[1] if len(lines) > 1 else "Unknown"
    return None


# ── Migration from old JSON ──────────────────────────────────────────────

def migrate_from_json():
    """One-time migration: import old signals from trade_signals.json."""
    json_path = Path(r"C:\Users\birds\Workspace\kraken_scout\trade_signals.json")
    if not json_path.exists():
        return

    import json
    try:
        with open(json_path, "r") as f:
            old_signals = json.load(f)
    except Exception:
        return

    init_db()
    now = _now_iso()
    imported = 0
    with _get_db() as conn:
        for s in old_signals:
            try:
                signal_id = str(uuid.uuid4())[:8]
                ts = s.get("timestamp", now)
                status = s.get("status", "closed")
                # Map old statuses to new
                if status == "executed":
                    status = "filled"
                conn.execute("""
                    INSERT OR IGNORE INTO signals (
                        signal_id, pair, action, confidence, reason,
                        timestamp, valid_until, cooldown_until,
                        price_at_signal, entry_price, stop_loss, take_profit,
                        risk_reward, position_size_usd, source, status,
                        executed_at, execution_price, order_id, closed_at, pnl
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    signal_id, s.get("pair",""), s.get("action","hold"),
                    s.get("confidence",0), "; ".join(s.get("reasons",[]))[:200],
                    ts, ts, ts,
                    s.get("entry_price"), s.get("entry_price"),
                    s.get("stop_loss"), s.get("take_profit"),
                    s.get("risk_reward"), s.get("position_size_usd"),
                    s.get("source","migrated"), status,
                    s.get("executed_at"), s.get("fill_price"),
                    s.get("order_id",""), s.get("closed_at"), s.get("pnl"),
                ))
                imported += 1
            except Exception:
                continue
        conn.commit()

    logger.info("Migrated %d signals from JSON to SQLite", imported)
    # Rename old file so we don't migrate again
    json_path.rename(json_path.with_suffix(".json.bak"))
