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
from datetime import datetime, timedelta
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
# THE DAILY PROFIT LOCK — REMOVED (user rule, Sep 5: "remove the daily
# lock and everything to do with that idea, it is causing missed trades.
# leaving money on the table"). The lock's ratcheting user-side floor
# starved the account exactly when it needed room (Sep 5: balance
# $9,855 under a $10,012 lock floor = negative buffer = zero sizing,
# both Hail Mary windows already burned). The ONLY loss rule now is the
# platform's death line below — Kraken's own -3% from start.
TRADING_DAYS_PER_WEEK = 5     # the daily pace: weekly goal / 5
COST_ROUNDTRIP_PCT = 0.08     # the platform spread: 0.04% each side

# ── THE HAIL MARY EXCEPTION (user rule, Sep 4 — KEPT when the lock was
# removed, Sep 5: "Keep the 'Hail Mary'"): the account-in-danger
# exception. When the contest sits UNDER break-even (no profits to
# protect, the account needs the shot) with platform room left, a
# CHAMPION-grade setup — verified uptrend on a major book — fires a
# ONE-HOUR window: the popup is marked, the trade clock bounds by the
# hour, one window per rotation. With the lock gone, sizing inside the
# window is the guard's full platform cap — the same cap every trade
# gets; the Hail Mary adds the champion-only bar, the hour clock, and
# the loud mark. THE DEATH LINE NEVER YIELDS: at the platform line
# there is no exception, ever.
HAIL_MARY_WINDOW_MIN = 60      # the one-hour full-allocation window

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
        "contest_id": datetime.now(MST).isoformat(),
        "updated_at": datetime.now(MST).isoformat(),
    }


def save_state(tier: int, current_balance: float) -> dict:
    """Write the account state (the console owns this — the user sets the
    tier and updates the balance as the contest account moves). The daily
    lock's peak fields are gone (Sep 5) — a rebuild here also scrubs any
    legacy lock keys out of the JSON on the next write; the Hail Mary's
    window fields ride along (a balance write mid-window must not close
    it)."""
    tier = int(tier) if int(tier) in TIERS else DEFAULT_TIER
    s = {
        "tier": tier,
        "start_balance": TIERS[tier]["start"],
        "current_balance": max(float(current_balance or 0), 0.0),
        "updated_at": datetime.now(MST).isoformat(),
    }
    prev = load_state()
    for _k in ("hail_mary_until", "hail_mary_date", "hail_mary_rot",
               "hail_mary_count", "contest_id"):
        if prev.get(_k) is not None:
            s[_k] = prev[_k]
    _write_state(s)
    return s


def new_contest(tier: int) -> dict:
    """Start a NEW contest window (user rule, Sep 9: the account knows
    which contest it is). The ONLY path that resets the balance to a
    tier's starting amount -- the sidebar's tier select calls this on
    USER INTERACTION (on_change), never on a rerun, so a stale second
    console can no longer reset the account by re-rendering (the Sep 9
    bug: a pinned $10,000 widget reset the 1k account every cycle, which
    also re-widened every sizing gate -- TAO was buried too-thin under
    the phantom 10k). A fresh window stamps a fresh contest_id and
    resets the Hail Mary windows (a new account gets new ones)."""
    tier = int(tier) if int(tier) in TIERS else DEFAULT_TIER
    s = {
        "tier": tier,
        "start_balance": TIERS[tier]["start"],
        "current_balance": TIERS[tier]["start"],
        "contest_id": datetime.now(MST).isoformat(),
        "updated_at": datetime.now(MST).isoformat(),
    }
    _write_state(s)
    return s


def current_contest_id(state: dict | None = None) -> str:
    """The window stamp of the contest we are in NOW."""
    s = state or load_state()
    return str(s.get("contest_id") or "")


def apply_pnl(net: float, contest_id: str | None = None) -> tuple[bool, float, str]:
    """Apply a trade's net P&L to the CURRENT contest's balance -- but
    only if the trade BELONGS to this contest window (its stamped
    contest_id matches; None = a legacy record, applies as before).
    (user rule, Sep 9: closing TIA from an OLD contest's console moved
    the CURRENT balance -- a trade must land on its own window's
    balance, and the balance self-adjusts from its own contest's trades
    only.) Returns (applied, balance_now, note)."""
    s = load_state()
    here = str(s.get("contest_id") or "")
    if contest_id and here and str(contest_id) != here:
        return False, float(s["current_balance"]), \
            f"trade from another contest window (record {contest_id} vs here {here})"
    nb = max(float(s["current_balance"]) + float(net or 0), 0.0)
    save_state(int(s["tier"]), nb)
    return True, nb, ""


def _write_state(s: dict) -> None:
    tmp = str(STATE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(s, indent=1))
    os.replace(tmp, STATE_FILE)


def _today_str() -> str:
    # (Sep 5) once the daily lock's date helper — the lock is removed;
    # the Hail Mary still stamps its window date.
    return datetime.now(MST).strftime("%Y-%m-%d")


def hail_mary_active(state: dict | None = None) -> bool:
    """True inside an active Hail Mary window (the emergency hour is
    open; the platform death line stands throughout)."""
    s = state or load_state()
    try:
        until = datetime.fromisoformat(str(s.get("hail_mary_until") or ""))
        if until.tzinfo is None:
            until = until.replace(tzinfo=MST)
        return datetime.now(MST) < until
    except Exception:
        return False


def hail_mary_available(state: dict | None = None) -> tuple[bool, str]:
    """(available, why_not) — the ACCOUNT preconditions for the Hail Mary
    Exception (the candidate side — champion-grade brain, verified
    uptrend, major book — is checked by the callers):
      1. under break-even: balance ≤ start (the contest is in danger —
         no profits to protect, the account needs the shot)
      2. the platform line has room (buffer > 0 — AT THE DEATH LINE
         nothing yields, ever)
      3. no window consumed this rotation (one Hail Mary per rotation;
         the next rotation re-arms the challenge)
    (Sep 5: the daily lock precondition died with the lock — the
    exception now fires purely on ACCOUNT DANGER.) Pure read; fail-closed
    on any doubt."""
    s = state or load_state()
    bal = float(s.get("current_balance") or 0.0)
    start = float(s.get("start_balance") or 0.0)
    if bal <= 0 or start <= 0:
        return False, "no account"
    if bal > start:
        return False, (f"over break-even (${bal - start:+,.0f}) — "
                       f"no emergency, no exception")
    if buffer(s) <= 0:
        return False, "at the death line — nothing yields, ever"
    now = datetime.now(MST)
    rot_key = f"{now.hour // 4 * 4:02d}-{now.hour // 4 * 4 + 4:02d}"
    if (s.get("hail_mary_rot") == rot_key
            and s.get("hail_mary_date") == _today_str()
            and not hail_mary_active(s)):
        return False, ("Hail Mary already used this rotation "
                       "(re-arms at the rotation boundary)")
    return True, "under break-even, platform room, no window this rotation"


def activate_hail_mary(state: dict | None = None) -> dict:
    """Open the one-hour window: the emergency hour runs, the platform
    death line + every risk control stand. Stamps the rotation (one
    window per rotation) + the count. The QUALIFYING SETUP is the
    callers' check — this only opens the door."""
    s = state or load_state()
    now = datetime.now(MST)
    s["hail_mary_until"] = (now + timedelta(minutes=HAIL_MARY_WINDOW_MIN)).isoformat()
    s["hail_mary_date"] = _today_str()
    s["hail_mary_rot"] = f"{now.hour // 4 * 4:02d}-{now.hour // 4 * 4 + 4:02d}"
    s["hail_mary_count"] = int(s.get("hail_mary_count") or 0) + 1
    _write_state(s)
    return s


def hail_mary_minutes_left(state: dict | None = None) -> float:
    """Minutes remaining in the active window (0 when none) — the plates
    and the light's clock read this."""
    s = state or load_state()
    try:
        until = datetime.fromisoformat(str(s.get("hail_mary_until") or ""))
        if until.tzinfo is None:
            until = until.replace(tzinfo=MST)
        return max(0.0, (until - datetime.now(MST)).total_seconds() / 60.0)
    except Exception:
        return 0.0


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


def realized_pnl_today(state: dict | None = None, now=None) -> float:
    """The ACTUAL day: net realized P&L banked today (MST), summed from
    the bridge ledger's closed buys (pnl is NET — the platform's spread
    is already in it; pnl_net_usd's contract). The worth-floor auto-dial's
    actual factor reads this (Sep 7: the dial tunes to the day as it IS,
    not as the pace hopes it to be). Fail-open to 0.0 — a broken ledger
    must not bend the dial."""
    try:
        import sqlite3
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        _mst = _ZI("America/Denver")
        now = now or _dt.now(_mst)
        day = now.astimezone(_mst).strftime("%Y-%m-%d")
        db_path = Path(__file__).resolve().parent / "signals.db"
        if not db_path.exists():
            return 0.0
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0.0) FROM signals "
                "WHERE action='buy' AND status='closed' AND pnl IS NOT NULL "
                "AND substr(closed_at, 1, 10) = ?", (day,)).fetchone()
            return float(row[0] or 0.0)
        finally:
            conn.close()
    except Exception:
        return 0.0


def pnl_net_usd(entry_price: float, exit_price: float, size_usd: float) -> tuple[float, float, float]:
    """(pnl_gross, cost_rt, pnl_net) for one closed contest round trip.
    The platform's only cost is the 0.04% spread each side — 0.08% round
    trip — and it counts toward BOTH the target and the death line, so
    the net is what the balance actually moved. Used by the SOLD button's
    auto-balance (user rule, Sep 2: "auto calculate the PnL... auto fill
    when the button is pushed")."""
    gross = float(size_usd) * (float(exit_price) / float(entry_price) - 1.0)
    cost = float(size_usd) * COST_ROUNDTRIP_PCT / 100.0
    return gross, cost, gross - cost


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
    """THE 3% GUARD (PLATFORM-ONLY, Sep 5 — the daily lock removed): the
    largest entry whose WORST NORMAL CASE (the coin's profiled window
    drawdown) consumes at most RISK_PER_TRADE_FRAC of the remaining
    death-line buffer. Returns 0 when the buffer is gone — no new risk.
    The only loss rule is Kraken's own -3% from start; it never moves
    and nothing is layered above it."""
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
