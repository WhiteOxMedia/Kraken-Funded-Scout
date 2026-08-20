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

MST = timezone(timedelta(hours=-7))

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
                pnl REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON signals(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pair ON signals(pair)")
        conn.commit()


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
    """Check if a pair is still in cooldown (can't receive new signals)."""
    init_db()
    now = _now_iso()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM signals WHERE pair=? AND cooldown_until > ? AND status IN ('pending','acknowledged','filled') LIMIT 1",
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


def get_open_position_count() -> int:
    """Count how many positions are currently open (filled, not closed)."""
    init_db()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM signals WHERE action='buy' AND status='filled'"
        ).fetchone()
        return row["cnt"] if row else 0


MAX_CONCURRENT_POSITIONS = 5  # Allow more positions — some may be stuck/unbonded


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
) -> str | None:
    """Write a buy/sell signal to the database.

    Returns signal_id if written, None if rejected (cooldown, duplicate, etc.).
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
    if not risk_reward or risk_reward < 2.0:
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
    if action == "buy":
        # Don't open too many positions at once — concentrate capital
        if get_open_position_count() >= MAX_CONCURRENT_POSITIONS:
            logger.info("Already %d open positions (max %d) — not writing new buy", get_open_position_count(), MAX_CONCURRENT_POSITIONS)
            return None
        if is_in_cooldown(pair):
            logger.info("Cooldown active for %s — not writing signal", pair)
            return None
        if is_already_holding(pair):
            logger.info("Already holding %s — not writing new buy signal", pair)
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
                risk_reward, position_size_usd, source, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            signal_id, pair, action, confidence, reason_summary,
            now.isoformat(), valid_until, cooldown_until,
            entry_price, entry_price, stop_loss, take_profit,
            risk_reward, position_size_usd, source,
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


def mark_closed(signal_id: str, pnl: float):
    """Mark a position as closed (exit executed)."""
    init_db()
    with _get_db() as conn:
        conn.execute(
            "UPDATE signals SET status='closed', closed_at=?, pnl=? WHERE signal_id=?",
            (_now_iso(), pnl, signal_id),
        )
        conn.commit()
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
    """Activate the kill switch — stops all new trades."""
    KILL_SWITCH_FILE.write_text(f"{_now_iso()}\n{reason}\n")
    logger.error("🛑 KILL SWITCH ACTIVATED: %s", reason)


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
