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
# THE DAILY PROFIT LOCK (user rule, Sep 4): the fraction of the day's
# gain the lock protects (the floor sits this far into the day's gain;
# the giveback allowance is the rest). 0.65 = protect 65%, give back at
# most 35% of the day's progress. Resets with the Denver day.
DAILY_PROTECT_FRAC = 0.65
TRADING_DAYS_PER_WEEK = 5     # the daily pace: weekly goal / 5
COST_ROUNDTRIP_PCT = 0.08     # the platform spread: 0.04% each side

# ── THE HAIL MARY EXCEPTION (user rule, Sep 4: "the daily lock is
# protecting my profits but I am under, no profits yet. Contest in
# danger") ──────────────────────────────────────────────────────────────
# THE SITUATION: the account sits UNDER break-even with no realized
# gains, but the day ticked up — so the daily lock (protecting a small
# intraday gain) becomes the binding floor and the entry guard sizes
# everything to ~$0. The lock was built to protect profits; with no
# profits to protect it guards a rounding error while the contest
# starves. THE EXCEPTION: for a CHAMPION-grade setup on a major book,
# the lock yields for ONE HOUR — sizing runs against the PLATFORM
# death-line buffer; the guard (0.34/dd), the knife, the exits, every
# other risk control stands UNTOUCHED ("no uncontrolled exposure" —
# "full allocation" = the guard's full cap against the platform buffer,
# never the whole balance). After the hour the standard lock resumes.
# ONE window per rotation ("until challenged again by another Hail
# Mary" — the next rotation re-arms the challenge). THE DEATH LINE
# NEVER YIELDS: at the platform line there is no exception, ever.
HAIL_MARY_WINDOW_MIN = 60      # the one-hour full-allocation window
HAIL_MARY_LOCK_BITE_FRAC = 0.25  # the lock "bites" when it crushes the
                                  # possible size below this fraction of
                                  # the platform-only cap

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
    tier and updates the balance as the contest account moves). Every
    balance write rolls the day + ratchets the daily peak (the lock's
    state rides along — save_state is the one chokepoint the balance
    moves through: SOLD auto-fill, the sidebar, tier switches)."""
    tier = int(tier) if int(tier) in TIERS else DEFAULT_TIER
    s = {
        "tier": tier,
        "start_balance": TIERS[tier]["start"],
        "current_balance": max(float(current_balance or 0), 0.0),
        "updated_at": datetime.now(MST).isoformat(),
    }
    prev = load_state()
    today = _today_str()
    if prev.get("daily_peak_date") == today:
        s["daily_peak_date"] = today
        s["daily_start_balance"] = float(prev.get("daily_start_balance")
                                         or s["current_balance"])
        s["daily_peak_balance"] = max(float(prev.get("daily_peak_balance") or 0.0),
                                      s["current_balance"])
    else:
        # new Denver day (or legacy state without daily fields) — the lock
        # resets: the day STARTS at the previous balance (yesterday's close
        # / the last known balance), fresh room today
        _prev_bal = float(prev.get("current_balance") or 0.0)
        s["daily_peak_date"] = today
        s["daily_start_balance"] = _prev_bal if _prev_bal > 0 else s["current_balance"]
        s["daily_peak_balance"] = max(_prev_bal, s["current_balance"])
    # THE HAIL MARY fields ride along (save_state rebuilds the dict —
    # without this, a balance write mid-window would silently close it)
    for _k in ("hail_mary_until", "hail_mary_date", "hail_mary_rot",
               "hail_mary_count"):
        if prev.get(_k) is not None:
            s[_k] = prev[_k]
    _write_state(s)
    return s


def _write_state(s: dict) -> None:
    tmp = str(STATE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(s, indent=1))
    os.replace(tmp, STATE_FILE)


def _today_str() -> str:
    return datetime.now(MST).strftime("%Y-%m-%d")


def touch_daily_peak(state: dict | None = None) -> dict:
    """Roll the day + ratchet the peak WITHOUT a balance write (the light
    calls this every pass so the day rolls at midnight even with no
    trades; the peak only rises when the balance actually does — writes
    go through save_state). Idempotent; writes only when something
    changed."""
    s = state or load_state()
    today = _today_str()
    cur = float(s.get("current_balance") or 0.0)
    changed = False
    if s.get("daily_peak_date") != today:
        s["daily_peak_date"] = today
        s["daily_start_balance"] = cur
        s["daily_peak_balance"] = cur
        changed = True
    elif cur > float(s.get("daily_peak_balance") or 0.0):
        s["daily_peak_balance"] = cur
        changed = True
    if changed:
        _write_state(s)
    return s


def daily_lock_floor(state: dict | None = None) -> float:
    """THE DAILY PROFIT LOCK (user rule, Sep 4: "a profit guard that moves
    the 3% death-line rule with the contest balance to protect the daily
    progress" — user choice: daily reset, "each day locks its own
    progress, fresh room tomorrow"): the ratcheting USER-SIDE effective
    floor. The PLATFORM line never moves (Kraken: max loss is always
    against the STARTING balance, never the peak — re-audited Sep 4);
    this floor sits ABOVE it once the day's peak is up, protecting
    DAILY_PROTECT_FRAC of the day's gain. Pure read; missing/legacy
    state fields fail open to the platform line alone."""
    s = state or load_state()
    line = death_line(s)
    if s.get("daily_peak_date") != _today_str():
        return line                      # no lock today (yet)
    start_of_day = float(s.get("daily_start_balance") or 0.0)
    peak = float(s.get("daily_peak_balance") or 0.0)
    gain = peak - start_of_day
    if gain <= 0:
        return line
    return max(line, start_of_day + gain * DAILY_PROTECT_FRAC)


def effective_buffer(state: dict | None = None) -> float:
    """The buffer the guards key off: the SMALLER of the platform
    death-line buffer and the daily-lock buffer. Negative = a binding
    floor is breached — no new risk. During an active HAIL MARY window
    the lock yields: the platform buffer alone (the death line never
    yields)."""
    s = state or load_state()
    bal = float(s.get("current_balance") or 0.0)
    if hail_mary_active(s):
        return buffer(s)
    return min(buffer(s), bal - daily_lock_floor(s))


def binding_floor(state: dict | None = None) -> tuple[str, float]:
    """('daily-lock' | 'platform', floor_usd) — which floor actually binds
    right now, for honest messages (the popup that skips says WHICH
    wall it hit)."""
    s = state or load_state()
    lf = daily_lock_floor(s)
    dl = death_line(s)
    return ("daily-lock", lf) if lf > dl else ("platform", dl)


def hail_mary_active(state: dict | None = None) -> bool:
    """True inside an active Hail Mary window (the daily lock yields;
    the platform death line stands)."""
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
      1. under break-even: balance ≤ start (no realized gains to
         protect — the lock is guarding a rounding error)
      2. the platform line has room (buffer > 0 — AT THE DEATH LINE
         nothing yields, ever)
      3. the DAILY LOCK is the binding floor (the thing crushing the
         entries; if the platform line binds, there is no exception to
         grant)
      4. no window consumed this rotation (one Hail Mary per rotation;
         the next rotation re-arms the challenge)
    Pure read; fail-closed on any doubt."""
    s = state or load_state()
    bal = float(s.get("current_balance") or 0.0)
    start = float(s.get("start_balance") or 0.0)
    if bal <= 0 or start <= 0:
        return False, "no account"
    if bal > start:
        return False, (f"over break-even (${bal - start:+,.0f}) — "
                       f"the lock protects real profit")
    if buffer(s) <= 0:
        return False, "at the death line — nothing yields, ever"
    which, _floor = binding_floor(s)
    if which != "daily-lock":
        return False, "the daily lock isn't the binding floor"
    now = datetime.now(MST)
    rot_key = f"{now.hour // 4 * 4:02d}-{now.hour // 4 * 4 + 4:02d}"
    if (s.get("hail_mary_rot") == rot_key
            and s.get("hail_mary_date") == _today_str()
            and not hail_mary_active(s)):
        return False, ("Hail Mary already used this rotation "
                       "(re-arms at the rotation boundary)")
    return True, "under break-even, the lock binds, no window this rotation"


def activate_hail_mary(state: dict | None = None) -> dict:
    """Open the one-hour window: the daily lock yields, the platform
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
                  start_balance: float | None = None,
                  state: dict | None = None) -> float:
    """THE 3% GUARD (daily-lock aware, Sep 4): the largest entry whose
    WORST NORMAL CASE (the coin's profiled window drawdown) consumes at
    most RISK_PER_TRADE_FRAC of the remaining buffer — where the buffer
    is the SMALLER of the platform death-line room and the daily-lock
    room (once the day is up, sizing shrinks to protect the progress).
    Returns 0 when either floor is gone — no new risk.

    state=None (the default, and what the tests rely on for deterministic
    numbers) = PLATFORM-ONLY math: no lock considered. The live callers
    (the popup, the mover popup, the QC) all pass the account state so
    the lock bites; passing none is the explicit opt-out."""
    start = start_balance if start_balance is not None else TIERS[DEFAULT_TIER]["start"]
    buf = float(current_balance) - start * (1.0 - MAX_LOSS_PCT / 100.0)
    if state is not None:
        if hail_mary_active(state):
            # THE HAIL MARY WINDOW: the daily lock yields — the platform
            # death-line buffer alone (the guard cap itself stands)
            pass
        else:
            # the daily lock: the SMALLER of the two buffers
            buf = min(buf, float(current_balance) - daily_lock_floor(state))
    if buf <= 0:
        return 0.0
    dd = max(float(window_dd_pct), 0.5) / 100.0
    return min(float(current_balance), (buf * RISK_PER_TRADE_FRAC) / dd)


def entry_size_usd(current_balance: float, book_carry_usd: float | None,
                   window_dd_pct: float,
                   start_balance: float | None = None,
                   state: dict | None = None) -> float:
    """The entry size: min(the account, the book's carry, the 3% guard)."""
    cap = max_entry_usd(current_balance, window_dd_pct, start_balance, state)
    if book_carry_usd is not None:
        cap = min(cap, book_carry_usd)
    return max(min(cap, float(current_balance)), 0.0)
