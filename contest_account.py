"""THE CONTEST ACCOUNT — the Kraken Funded rules, encoded (rebuild, Sep 1).

The platform's ACTUAL rules (pulled from Kraken's Funded documentation,
last updated Aug 28 2026 — the drift audit found none of this was modeled):

- Tiers: $1,000 ($20 fee) / $5,000 ($50 fee) / $10,000 ($90 fee).
- THE DEATH LINE: -3% from the STARTING balance. It never moves — not from
  the peak, not after withdrawals. Touch it, even briefly, and the account
  closes. There is NO daily limit; this is the only loss rule, in both the
  challenge and the funded state.
- Challenge target: +12% from start.
- Costs: no commissions, no withdrawal fees — a 0.04% spread each side
  (0.08% round trip), built into the price, counting toward BOTH the
  target and the line.
- No stop orders exist on the platform — buys and sells in dollar amounts
  only. Our alert system is the ONLY exit mechanism the user has.

THE PACE (user, Sep 1): "set the default around the 10k contest, it needs
to make $1200 a week" — 12%/week ≈ $240/day on the 10k tier. A few good
1-3% trades a day clears it.

THE GUARD (user: "never takes a big risk... it has eliminated most of the
risk of the trade for that time window"): entries are sized so a trade's
WORST NORMAL CASE — the coin's profiled window drawdown — consumes at
most RISK_PER_TRADE_FRAC of the remaining death-line buffer. Three
consecutive worst-cases still leave the account alive. On a fresh 10k
($300 buffer) with a 2%-drawdown coin: max entry ≈ $5,100. A 3%-drawdown
coin: ≈ $3,400. The 1k tier's $30 buffer means small fractional entries
by arithmetic, not by choice.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")
STATE_FILE = Path(__file__).parent / "contest_account.json"

# ── The platform's rules (one source of truth) ─────────────────────────
TIERS = {
    1000: {"start": 1_000.0, "fee": 20.0, "label": "$1,000"},
    5000: {"start": 5_000.0, "fee": 50.0, "label": "$5,000"},
    10000: {"start": 10_000.0, "fee": 90.0, "label": "$10,000"},
}
DEFAULT_TIER = 10000          # user, Sep 1: "set the default around the 10k contest"

MAX_LOSS_PCT = 3.0            # the death line: -3% from start, never moves
PROFIT_TARGET_PCT = 12.0      # the challenge target: +12% from start
WEEKLY_GOAL_PCT = 12.0        # the user's pace: 12%/week ($1,200 on 10k)
TRADING_DAYS_PER_WEEK = 5     # the daily pace: weekly goal / 5
COST_ROUNDTRIP_PCT = 0.08     # the platform spread: 0.04% each side

# ── The guard's dials ──────────────────────────────────────────────────
RISK_PER_TRADE_FRAC = 0.34    # a trade's worst case may eat at most ~1/3
                              # of the remaining buffer (3 worst-cases to die)
DEFAULT_WINDOW_DD_PCT = 3.0   # unknown coin, young profile: assume a 3%
                              # window drawdown (the conservative default)


def load_state() -> dict:
    """{tier, start_balance, current_balance, updated_at} — defaults to the
    10k tier at full balance when no state exists yet."""
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if s.get("tier") in TIERS and float(s.get("current_balance") or 0) > 0:
            return s
    except Exception:
        pass
    return {
        "tier": DEFAULT_TIER,
        "start_balance": TIERS[DEFAULT_TIER]["start"],
        "current_balance": TIERS[DEFAULT_TIER]["start"],
        "updated_at": datetime.now(MST).isoformat(),
    }


def save_state(tier: int, current_balance: float) -> dict:
    """Write the account state (the console owns this — the user sets the
    tier and updates the balance as the contest account moves)."""
    tier = int(tier) if int(tier) in TIERS else DEFAULT_TIER
    s = {
        "tier": tier,
        "start_balance": TIERS[tier]["start"],
        "current_balance": max(float(current_balance or 0), 0.0),
        "updated_at": datetime.now(MST).isoformat(),
    }
    tmp = str(STATE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(s, indent=1))
    os.replace(tmp, STATE_FILE)
    return s


def death_line(state: dict | None = None) -> float:
    """The line: start - 3%. It NEVER moves."""
    s = state or load_state()
    return float(s["start_balance"]) * (1.0 - MAX_LOSS_PCT / 100.0)


def buffer(state: dict | None = None) -> float:
    """How much room is left before the account dies."""
    s = state or load_state()
    return float(s["current_balance"]) - death_line(s)


def daily_pace_usd(state: dict | None = None) -> float:
    """The pace: weekly goal / 5 trading days (≈$240/day on the 10k tier)."""
    s = state or load_state()
    return float(s["start_balance"]) * WEEKLY_GOAL_PCT / 100.0 / TRADING_DAYS_PER_WEEK


def window_drawdown_pct(pair: str, rot_key: str | None = None,
                        schedule: str | None = None) -> float:
    """The coin's profiled worst-window drawdown from the Volatility
    Engine's cell (max_drop for this coin/rotation/schedule). Young or
    missing profiles fail conservative to 3%."""
    try:
        from background_scanner import _rotation_window, _now_mst, _schedule_for_dow
        from volatility_engine import load_rotation_thresholds
        now = _now_mst()
        win = _rotation_window(now)
        rot = rot_key or (win[2] if win else "08-12")
        sched = schedule or _schedule_for_dow(now.weekday())
        doc = load_rotation_thresholds()
        cell = (doc.get("universe", {}).get(pair, {}).get(sched, {}).get(rot))
        if cell and cell.get("max_drop") is not None:
            dd = abs(float(cell["max_drop"]))
            if dd > 0.2:
                return min(dd, 15.0)
    except Exception:
        pass
    return DEFAULT_WINDOW_DD_PCT


def max_entry_usd(current_balance: float, window_dd_pct: float,
                  start_balance: float | None = None) -> float:
    """THE 3% GUARD: the largest entry whose WORST NORMAL CASE (the coin's
    profiled window drawdown) consumes at most RISK_PER_TRADE_FRAC of the
    remaining death-line buffer. Returns 0 when the buffer is gone — the
    account is at/near the line and takes no new risk."""
    start = start_balance if start_balance is not None else TIERS[DEFAULT_TIER]["start"]
    buf = float(current_balance) - start * (1.0 - MAX_LOSS_PCT / 100.0)
    if buf <= 0:
        return 0.0
    dd = max(float(window_dd_pct), 0.5) / 100.0
    return min(float(current_balance), (buf * RISK_PER_TRADE_FRAC) / dd)


def entry_size_usd(current_balance: float, book_carry_usd: float | None,
                   window_dd_pct: float,
                   start_balance: float | None = None) -> float:
    """The entry size: min(the account, the book's carry, the 3% guard)."""
    cap = max_entry_usd(current_balance, window_dd_pct, start_balance)
    if book_carry_usd is not None:
        cap = min(cap, book_carry_usd)
    return max(min(cap, float(current_balance)), 0.0)
