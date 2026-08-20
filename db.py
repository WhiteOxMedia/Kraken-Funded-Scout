"""Database layer for Kraken Scout.

Uses SQLAlchemy with the Neon Postgres connection.
Tables: signals, challenge_trades, challenge_state
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np


def _to_native(value: Any) -> Any:
    """Convert numpy types to native Python types for psycopg2 compatibility."""
    if isinstance(value, np.float64):
        return float(value)
    if isinstance(value, np.float32):
        return float(value)
    if isinstance(value, np.int64):
        return int(value)
    if isinstance(value, np.int32):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import DB_URL


class Base(DeclarativeBase):
    pass


class Signal(Base):
    """Logs every strategy signal evaluation from a scan."""

    __tablename__ = "scout_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    pair = Column(String(20), nullable=False)
    action = Column(String(10), nullable=False)  # buy / sell / hold
    confidence = Column(Float, nullable=False)
    price = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    risk_reward = Column(Float, nullable=True)
    indicators = Column(Text, nullable=True)  # JSON snapshot of indicator values
    patterns = Column(Text, nullable=True)  # JSON snapshot of detected patterns
    reasons = Column(Text, nullable=True)  # JSON list of reason strings


class ChallengeTrade(Base):
    """A trade manually executed in the Kraken Funded app, logged for tracking."""

    __tablename__ = "scout_challenge_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    pair = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)  # buy / sell
    amount_usd = Column(Float, nullable=False)  # dollar amount traded
    price = Column(Float, nullable=False)  # execution price
    notes = Column(Text, nullable=True)


class ChallengeState(Base):
    """Singleton row tracking the Kraken Funded challenge progress."""

    __tablename__ = "scout_challenge_state"

    id = Column(Integer, primary_key=True, default=1)  # always row 1
    starting_balance = Column(Float, default=1000.0)
    current_balance = Column(Float, default=1000.0)
    max_loss_pct = Column(Float, default=5.0)
    profit_target_pct = Column(Float, default=12.0)
    status = Column(String(20), default="active")  # active / passed / failed
    loss_streak = Column(Integer, default=0)       # consecutive losing trades
    win_streak = Column(Integer, default=0)        # consecutive winning trades
    total_wins = Column(Integer, default=0)
    total_losses = Column(Integer, default=0)


class TournamentWinner(Base):
    """Records every tournament champion for history and winners-only brackets."""

    __tablename__ = "scout_tournament_winners"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    pair = Column(String(20), nullable=False)
    action = Column(String(10), nullable=False)
    confidence = Column(Float, nullable=False)
    price = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    risk_reward = Column(Float, nullable=True)
    regime = Column(String(30), nullable=True)
    phase = Column(String(30), nullable=True)
    reasons = Column(Text, nullable=True)  # JSON list of reasons
    scan_type = Column(String(20), default="full")  # "full" or "winners"


_engine: create_engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    """Return the cached SQLAlchemy engine (created lazily)."""
    global _engine
    if _engine is None:
        _engine = create_engine(DB_URL, pool_pre_ping=True, pool_size=5)
    return _engine


def get_session():
    """Return a new SQLAlchemy session."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


def init_db():
    """Create all tables if they don't exist.  Call this at app startup."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    # Add new columns to existing databases (ALTER TABLE ... ADD COLUMN IF NOT EXISTS)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE scout_challenge_state ADD COLUMN IF NOT EXISTS loss_streak INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE scout_challenge_state ADD COLUMN IF NOT EXISTS win_streak INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE scout_challenge_state ADD COLUMN IF NOT EXISTS total_wins INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE scout_challenge_state ADD COLUMN IF NOT EXISTS total_losses INTEGER DEFAULT 0"))
            conn.commit()
    except Exception:
        pass
    # Ensure the singleton challenge_state row exists
    with get_session() as session:
        state = session.get(ChallengeState, 1)
        if state is None:
            state = ChallengeState(id=1)
            session.add(state)
            session.commit()


def update_streak(is_win: bool):
    """Update win/loss streak counters after a trade closes.

    Parameters
    ----------
    is_win : bool
        True if the trade was profitable, False if it was a loss.
    """
    with get_session() as session:
        state = session.get(ChallengeState, 1)
        if state is None:
            state = ChallengeState(id=1)
            session.add(state)
        if is_win:
            state.win_streak = (state.win_streak or 0) + 1
            state.loss_streak = 0
            state.total_wins = (state.total_wins or 0) + 1
        else:
            state.loss_streak = (state.loss_streak or 0) + 1
            state.win_streak = 0
            state.total_losses = (state.total_losses or 0) + 1
        session.commit()


# ── Signal logging ───────────────────────────────────────────────────────

def log_signal(
    pair: str,
    action: str,
    confidence: float,
    price: float | None,
    indicators: dict,
    patterns: dict,
    reasons: list[str] | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    risk_reward: float | None = None,
):
    """Persist a scout signal evaluation."""
    pair = (pair or "")[:20]
    action = (action or "")[:10]
    with get_session() as session:
        sig = Signal(
            pair=pair,
            action=action,
            confidence=_to_native(confidence),
            price=_to_native(price),
            entry_price=_to_native(entry_price),
            stop_loss=_to_native(stop_loss),
            take_profit=_to_native(take_profit),
            risk_reward=_to_native(risk_reward),
            indicators=json.dumps(indicators, default=str),
            patterns=json.dumps(patterns, default=str),
            reasons=json.dumps(reasons or [], default=str),
        )
        session.add(sig)
        session.commit()
        return sig.id


def get_recent_signals(limit: int = 50) -> list[Signal]:
    """Return recent signals, newest first."""
    with get_session() as session:
        from sqlalchemy import select
        stmt = select(Signal).order_by(Signal.id.desc()).limit(limit)
        return list(session.scalars(stmt))


# ── Tournament winner logging ───────────────────────────────────────────

def log_tournament_winner(
    pair: str,
    action: str,
    confidence: float,
    price: float | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    risk_reward: float | None = None,
    regime: str = "",
    phase: str = "",
    reasons: list[str] | None = None,
    scan_type: str = "full",
) -> int:
    """Log a tournament champion."""
    # Truncate string fields to match DB column sizes
    pair = (pair or "")[:20]
    action = (action or "")[:10]
    regime = (regime or "")[:30]
    phase = (phase or "")[:30]
    scan_type = (scan_type or "full")[:20]
    with get_session() as session:
        w = TournamentWinner(
            pair=pair, action=action, confidence=_to_native(confidence),
            price=_to_native(price), entry_price=_to_native(entry_price),
            stop_loss=_to_native(stop_loss), take_profit=_to_native(take_profit),
            risk_reward=_to_native(risk_reward), regime=regime, phase=phase,
            reasons=json.dumps(reasons or [], default=str),
            scan_type=scan_type,
        )
        session.add(w)
        session.commit()
        return w.id


def get_tournament_winners(limit: int = 50) -> list[TournamentWinner]:
    """Return recent tournament winners, newest first."""
    try:
        with get_session() as session:
            from sqlalchemy import select
            stmt = select(TournamentWinner).order_by(TournamentWinner.id.desc()).limit(limit)
            return list(session.scalars(stmt))
    except Exception:
        return []


def get_winner_pairs(limit: int = 20) -> list[str]:
    """Return the list of pairs that have won tournaments recently."""
    try:
        with get_session() as session:
            from sqlalchemy import select, func
            # Get pairs ordered by most recent winner, deduplicated in Python
            stmt = (
                select(TournamentWinner.pair)
                .order_by(TournamentWinner.id.desc())
                .limit(100)
            )
            rows = list(session.scalars(stmt))
            # Deduplicate preserving order (most recent first)
            seen = set()
            result = []
            for p in rows:
                if p not in seen:
                    seen.add(p)
                    result.append(p)
                    if len(result) >= limit:
                        break
            return result
    except Exception:
        return []


# ── Challenge trade logging ──────────────────────────────────────────────

def log_challenge_trade(
    pair: str,
    side: str,
    amount_usd: float,
    price: float,
    notes: str = "",
) -> int:
    """Log a trade manually executed in the Kraken Funded app."""
    with get_session() as session:
        t = ChallengeTrade(
            pair=pair,
            side=side,
            amount_usd=_to_native(amount_usd),
            price=_to_native(price),
            notes=notes,
        )
        session.add(t)
        session.commit()
        return t.id


def get_challenge_trades(limit: int = 100) -> list[ChallengeTrade]:
    """Return recent challenge trades, newest first."""
    with get_session() as session:
        from sqlalchemy import select
        stmt = select(ChallengeTrade).order_by(ChallengeTrade.id.desc()).limit(limit)
        return list(session.scalars(stmt))


def delete_challenge_trade(trade_id: int):
    """Delete a challenge trade entry (for correcting mistakes)."""
    with get_session() as session:
        t = session.get(ChallengeTrade, trade_id)
        if t:
            session.delete(t)
            session.commit()


# ── Challenge state ──────────────────────────────────────────────────────

def get_challenge_state() -> ChallengeState:
    """Return the singleton challenge state row."""
    with get_session() as session:
        state = session.get(ChallengeState, 1)
        if state is None:
            state = ChallengeState(id=1)
            session.add(state)
            session.commit()
        return state


def update_challenge_state(**kwargs: Any):
    """Update one or more fields on the challenge state singleton."""
    with get_session() as session:
        state = session.get(ChallengeState, 1)
        if state is None:
            state = ChallengeState(id=1)
            session.add(state)
        for key, value in kwargs.items():
            setattr(state, key, _to_native(value))
        session.commit()


def recompute_challenge_balance():
    """Recompute current_balance from the starting balance + all trade P&L.

    For each buy followed by a sell of the same pair, the P&L is:
        (sell_price - buy_price) / buy_price * buy_amount_usd
    Simplified: each sell records the realized P&L directly.
    """
    state = get_challenge_state()
    starting = state.starting_balance
    trades = get_challenge_trades(limit=1000)
    # Reverse to chronological order
    trades = list(reversed(trades))

    # Track open positions per pair
    open_buys: dict[str, list[tuple[float, float]]] = {}  # pair -> [(amount_usd, price)]

    balance = starting
    for t in trades:
        if t.side == "buy":
            open_buys.setdefault(t.pair, []).append((t.amount_usd, t.price))
        elif t.side == "sell":
            buys = open_buys.get(t.pair, [])
            if buys:
                buy_amount, buy_price = buys.pop(0)
                pnl = (t.price - buy_price) / buy_price * buy_amount
                balance += pnl

    update_challenge_state(current_balance=balance)
    return balance
