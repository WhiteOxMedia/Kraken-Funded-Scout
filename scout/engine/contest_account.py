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
to make $1200 a week" — 12%/week = $1,200 in 7 days ≈ $171.43/day on the
10k tier. That is the normal weekly-bar pace, not the live checkpoint:
the live checkpoint is remaining ÷ days left. The 12%/day figure is a
separate one-day sprint, never the ordinary daily pace.

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
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MST = ZoneInfo("America/Denver")
STATE_FILE = Path(__file__).parent / "contest_account.json"
TRADES_FILE = Path(__file__).parent / "contest_trades.jsonl"
ROLLING_ETA_WINDOWS = (3, 5, 10, 20, 50, 100)

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
WEEKLY_DAYS = 7.0             # the contest bar is $1,200 cumulative by day 7
TRADING_DAYS_PER_WEEK = 7     # crypto trades 7 days; normal pace = weekly/7
ONE_DAY_SPRINT_PCT = PROFIT_TARGET_PCT  # 12%/day: separate one-day sprint
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

# ── THE HAIL MARY OPPORTUNISTIC MODE (user ask, Oct 1: "add an
# opportunistic mode to the 'Hail Mary' that seeks any viable upside;
# use the deep-scan exit watch as the authoritative entry/exit signal
# and enforce strict risk controls"): the SAME account-in-danger door
# (under break-even, platform room, one window per rotation) with a
# LOWER candidate bar — the deep read must read buy and clear the
# OPPORTUNISTIC floor — while the RISK gets STRICTER, never looser:
# a $300 hard spend cap ( OPP_HARD_SPEND_USD ) on top of the guard's
# cap, and the position's watch row carries hm_mode="opportunistic"
# so the exit watch judges it against a 2% hard stop ( the classic
# window keeps its half-buffer stop ). The exit watch — the deep
# scan's own trend-sell / profit-guard / hard-stop stack — is THE
# entry AND exit authority: it says when the upside is still viable
# and it says when out. The classic Hail Mary is tried FIRST; the
# opportunistic door only opens when the champion-grade bar declines.
HAIL_MARY_OPP_FLOOR = 90.0     # the Oct 4 user rule: the confidence score
                               # must be ABOVE 90 (the qualify refuses a read
                               # AT the floor itself — 90.0 is not above 90).
                               # Was 62 (Oct 1 "viable" line — retired: the
                               # opportunistic shot is no longer a
                               # challenger-grade bet).
HAIL_MARY_OPP_HARD_STOP_PCT = 2.0   # the exit watch's hard stop for an
                                    # opportunistic position (vs the classic
                                    # window's half-buffer stop) — the strict
                                    # risk control the lower bar pays for
OPP_HARD_SPEND_USD = 300.0     # the opportunistic window's hard spend cap —
                               # a fixed ceiling on top of the 3% guard's cap
                               # (min wins; the death line still rules both)

# ── The guard's dials ──────────────────────────────────────────────────
RISK_PER_TRADE_FRAC = 0.34    # a trade's worst case may eat at most ~1/3
                              # of the remaining buffer (3 worst-cases to die)
DEFAULT_WINDOW_DD_PCT = 3.0   # unknown coin, young profile: assume a 3%
                              # window drawdown (the conservative default)

# ── THE GAP-STRESS DIALS (Nov 7, the Nov 5 flush) ───────────────────────
# The Nov 5 death was not a slow bleed: thirteen correlated longs were
# stopped out inside sixty seconds, stop notes saying "-$5" while the
# realized loss ran -$30..-$37 (the stops gapped through 5-7x). Sizing
# per trade cannot see that shape and a notional cap in BALANCE units
# never shrinks as the account bleeds, so the BOOK gets its own cap,
# stated in BUFFER units: what ONE adverse gap on the whole book may cost
# before half the death-line buffer is gone.
GAP_STRESS_PCT = 3.0          # the one-move adverse gap the whole book must
                              # survive (the flush's realized move)
MAX_BUFFER_STRESS_FRAC = 0.50 # of the remaining buffer a whole-book gap may eat
STRESS_SLIP_MULT = 5.0        # how far past its stop a FLUSH fills: Nov 5
                              # measured 5-7x the stop note ("-$5" on a -$30..
                              # $37 catch), so a stop is credited at NO BETTER
                              # than 1/5 of its face value in a correlated move
STRESS_BETA_MIN = 0.5         # no coin rides a flush as free insurance
STRESS_BETA_MAX = 3.0         # ...and none is priced worse than 3x the fleet


_BOOT_ARMED = False   # the boot guard's tell: True once a LONG-RUNNING
                      # component verified the state file at its start


class AccountStateMissing(RuntimeError):
    """The boot guard's loud failure: the account state file is gone or
    unusable, and without it the fail-soft machinery would silently
    fabricate a fresh full tier — the phantom the Nov 5 incident proved
    a real-money engine must never size against."""


def load_state() -> dict:
    """{tier, start_balance, current_balance, updated_at} — defaults to the
    10k tier at full balance when no state exists yet.

    THE ARMED CONTRACT (Nov 5, the phantom-10k lesson): a process that
    passed assert_state_on_boot() (scanner boot, console entry) gets a
    LOUD AccountStateMissing instead of the phantom default once the
    file is gone/unusable — a calibrated engine must never silently
    re-arm sizing on a balance nobody knows. Sandboxed tests never
    call the boot guard, so their fail-soft contract is untouched."""
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if s.get("tier") in TIERS and float(s.get("current_balance") or 0) > 0:
            return s
    except Exception:
        pass
    if _BOOT_ARMED:
        raise AccountStateMissing(
            f"load_state: {STATE_FILE} is gone or unusable after the boot "
            "guard had verified it — refusing to fabricate the phantom full "
            "balance. Restore the real account state (funded dashboard "
            "figure, the console's balance field) and restart.")
    return {
        "tier": DEFAULT_TIER,
        "start_balance": TIERS[DEFAULT_TIER]["start"],
        "current_balance": TIERS[DEFAULT_TIER]["start"],
        "contest_id": datetime.now(MST).isoformat(),
        "updated_at": datetime.now(MST).isoformat(),
    }


def assert_state_on_boot(context: str = "boot") -> dict:
    """THE BOOT GUARD (Nov 5, the phantom-10k lesson): a component that
    starts WITHOUT the account state file would read load_state() and
    receive a fabricated fresh full tier — sizing gates, pace targets
    and the death-line buffer all computing against a balance nobody
    knows. The long-running entry points call this ONCE before any
    engine work; a missing/unusable file raises AccountStateMissing
    (the launcher's early-death capture turns that into a named exit,
    not a silent boot). A pass ARMS the process: from then on even a
    mid-session loss of the file raises on read instead of silently
    phantom-ing."""
    global _BOOT_ARMED
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        usable = (isinstance(s, dict) and s.get("tier") in TIERS
                  and float(s.get("current_balance") or 0) > 0)
    except Exception as exc:
        usable = False
        s = None
        _err = f"unreadable ({exc.__class__.__name__})"
    else:
        _err = "no usable tier/balance"
    if not usable:
        raise AccountStateMissing(
            f"{context}: {STATE_FILE} is missing or {_err} — REFUSING TO "
            "START: the phantom full balance the fail-soft default would "
            "fabricate is a calibration lie. Restore the real account "
            "state first (funded dashboard figure, the console's balance "
            "field), then relaunch.")
    _BOOT_ARMED = True
    return (s if isinstance(s, dict)
            else {"tier": DEFAULT_TIER,
                  "start_balance": TIERS[DEFAULT_TIER]["start"],
                  "current_balance": TIERS[DEFAULT_TIER]["start"],
                  "contest_id": datetime.now(MST).isoformat(),
                  "updated_at": datetime.now(MST).isoformat()})


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
               "hail_mary_count", "hail_mary_mode", "contest_id"):
        if prev.get(_k) is not None:
            s[_k] = prev[_k]
    # a USER action never drops silently (Sep 9, the phantom-1k lesson):
    # a busy lock must RAISE so the caller can say "try again" -- the
    # silent skip returned a state that was never written, and the UI
    # showed a contest the file had never heard of
    with _StateLock() as _lk:
        if not _lk.held:
            raise RuntimeError("account state busy -- retry in a moment")
        _write_state(s)
    return s


def win_line(state: dict | None = None) -> float:
    """The win line: the contest start +12% (11,200 on the 10k tier)."""
    s = state or load_state()
    return round(float(s["start_balance"]) * (1.0 + PROFIT_TARGET_PCT / 100.0), 2)


def contest_won(state: dict | None = None) -> bool:
    """True once the balance has reached the +12% win line."""
    s = state or load_state()
    return float(s["current_balance"]) >= win_line(s)


def standing(state: dict | None = None) -> dict:
    """THE CONTEST STANDING against the FIXED baseline (user rule, Nov 11).

    Pass = the balance reaches +12% of the contest's STARTING balance, at any
    time, with no deadline. The death line is -3% of that SAME start and never
    moves -- profits grow the buffer instead of ratcheting the line. One dict,
    so every surface (scanner log, console) grades the contest the same way.
    """
    s = state or load_state()
    start = float(s["start_balance"])
    now = float(s["current_balance"])
    win = round(start * (1.0 + PROFIT_TARGET_PCT / 100.0), 2)
    dead = round(start * (1.0 - MAX_LOSS_PCT / 100.0), 2)
    prog = ((now / start - 1.0) * 100.0) if start > 0 else 0.0
    return {
        "start_balance": round(start, 2),
        "current_balance": round(now, 2),
        "win_line": win,
        "death_line": dead,
        "target_pct": PROFIT_TARGET_PCT,
        "progress_pct": round(prog, 2),
        "percent_to_win": (round(100.0 * prog / PROFIT_TARGET_PCT, 1)
                           if PROFIT_TARGET_PCT else 0.0),
        "to_go_usd": round(max(win - now, 0.0), 2),
        "buffer_usd": round(now - dead, 2),
        "won": now >= win,
        "contest_id": s.get("contest_id"),
    }


def _t_critical_95(df: int) -> float:
    """Two-sided 95% t critical for small samples; normal for large ones."""
    table = {
        1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
        6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
        11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
        16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
        21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
        26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
        40: 2.021, 60: 2.000, 120: 1.980,
    }
    if df in table:
        return table[df]
    if df < 40:
        return 2.042
    if df < 60:
        return 2.021
    if df < 120:
        return 2.000
    return 1.960


def _rolling_trade_rows(trades_path: Path | None = None) -> list[dict]:
    """Applied closed rows, sorted by sold_at. Read-only; malformed rows skipped."""
    path = Path(trades_path) if trades_path else TRADES_FILE
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(row, dict) or row.get("applied") is not True:
                    continue
                if row.get("pnl_net") is None or not row.get("sold_at"):
                    continue
                try:
                    ts = datetime.fromisoformat(
                        str(row["sold_at"]).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=MST)
                    row["_ts"] = ts
                    row["_net"] = float(row["pnl_net"])
                except (TypeError, ValueError, OverflowError):
                    continue
                rows.append(row)
    except OSError:
        return []
    rows.sort(key=lambda r: r["_ts"])
    return rows


def _rolling_window(rows: list[dict], label: str, remaining: float) -> dict:
    """One rolling sample: actual net closes, mean, 95% CI and realized rate."""
    n = len(rows)
    if not n:
        return {"label": label, "n": 0, "net": None, "mean_net": None,
                "wins": 0, "win_rate": None, "sd": None, "se": None,
                "ci_low": None, "ci_high": None, "span_days": None,
                "closes_per_day": None, "rate_per_day": None,
                "eta_days": None, "eta_weeks": None,
                "edge": "NO DATA", "significant": False}
    net = sum(r["_net"] for r in rows)
    mean = net / n
    wins = sum(1 for r in rows if r["_net"] > 0)
    sd = statistics.stdev([r["_net"] for r in rows]) if n > 1 else 0.0
    se = sd / (n ** 0.5) if n > 1 else None
    half = (_t_critical_95(n - 1) * se) if se is not None else None
    ci_low = (mean - half) if half is not None else None
    ci_high = (mean + half) if half is not None else None
    span_days = ((rows[-1]["_ts"] - rows[0]["_ts"]).total_seconds() / 86400.0
                 if n > 1 else None)
    rate = (net / span_days) if span_days and span_days > 0 else None
    eta_days = (remaining / rate) if rate and rate > 0 else None
    edge = "POSITIVE" if mean > 0 else "NEGATIVE" if mean < 0 else "FLAT"
    significant = bool(
        (ci_low is not None and ci_low > 0)
        or (ci_high is not None and ci_high < 0))
    return {
        "label": label,
        "n": n,
        "net": round(net, 2),
        "mean_net": round(mean, 4),
        "wins": wins,
        "win_rate": round(wins / n, 4),
        "sd": round(sd, 4),
        "se": round(se, 4) if se is not None else None,
        "ci_low": round(ci_low, 4) if ci_low is not None else None,
        "ci_high": round(ci_high, 4) if ci_high is not None else None,
        "span_days": round(span_days, 4) if span_days is not None else None,
        "closes_per_day": round(n / span_days, 4) if span_days else None,
        "rate_per_day": round(rate, 4) if rate is not None else None,
        "eta_days": round(eta_days, 2) if eta_days is not None else None,
        "eta_weeks": round(eta_days / 7.0, 2) if eta_days is not None else None,
        "edge": edge,
        "significant": significant,
        "window_start": rows[0]["_ts"].isoformat(),
        "window_end": rows[-1]["_ts"].isoformat(),
    }


def _setup_route(row: dict) -> str:
    tags = row.get("setup") if isinstance(row.get("setup"), dict) else {}
    route = str(tags.get("route") or "").strip().lower().replace("_", "-")
    return route or "unknown"


def _setup_tide(row: dict) -> str:
    tags = row.get("setup") if isinstance(row.get("setup"), dict) else {}
    tide = str(tags.get("tide") or "").strip().upper()
    return (tide.split()[0] if tide.split() else "UNKNOWN")


def _setup_family(row: dict) -> str:
    """Champion vs mover, independent of the fast/opportunistic suffix."""
    route = _setup_route(row)
    if route.startswith("champion"):
        return "CHAMPION"
    if route.startswith("contest-mover") or route.startswith("mover"):
        return "SCOUT MOVER"
    if route.startswith("sure"):
        return "SURE-THING"
    return "OTHER"


def _setup_breakdown(rows: list[dict], getter, label_fn,
                     remaining: float, empty: tuple = ()) -> list[dict]:
    groups: dict[str, list[dict]] = {key: [] for key in empty}
    for row in rows:
        groups.setdefault(getter(row), []).append(row)
    return [
        _rolling_window(groups[key], label_fn(key), remaining)
        for key in sorted(groups, key=lambda k: (-len(groups[k]), k))
    ]


def rolling_eta_report(state: dict | None = None,
                       trades_path: Path | None = None,
                       windows: tuple[int, ...] | None = None) -> dict:
    """Read-only rolling ETA from the live ledger's applied closed trades.

    Every window ends at the newest applied close. The ETA uses only the
    window's realized net dollars/day; it never assumes the configured
    12%/week pace. A non-positive window has no finite ETA and is labeled
    NEGATIVE; a CI that includes zero is reported separately from the sign.
    """
    s = state or load_state()
    start = float(s.get("start_balance") or 0.0)
    current = float(s.get("current_balance") or 0.0)
    win = round(start * (1.0 + PROFIT_TARGET_PCT / 100.0), 2)
    remaining = max(win - current, 0.0)
    rows = _rolling_trade_rows(trades_path)

    current_rows = []
    contest_start = None
    try:
        contest_start = datetime.fromisoformat(
            str(s.get("contest_id") or "").replace("Z", "+00:00"))
        if contest_start.tzinfo is None:
            contest_start = contest_start.replace(tzinfo=MST)
    except (TypeError, ValueError):
        contest_start = None
    if contest_start is not None:
        current_rows = [r for r in rows if r["_ts"] >= contest_start]

    report_rows = []
    if current_rows:
        report_rows.append(_rolling_window(current_rows, "CURRENT CONTEST",
                                           remaining))
    for size in (windows or ROLLING_ETA_WINDOWS):
        if len(rows) >= size:
            report_rows.append(_rolling_window(rows[-size:], f"LAST {size}",
                                               remaining))
    if rows and (not report_rows or report_rows[-1]["n"] != len(rows)):
        report_rows.append(_rolling_window(rows, "ALL APPLIED", remaining))

    tagged_rows = [
        r for r in rows
        if isinstance(r.get("setup"), dict) and r["setup"]
    ]
    setup = {
        "tagged": len(tagged_rows),
        "applied": len(rows),
        "first_tagged": (tagged_rows[0]["_ts"].isoformat()
                         if tagged_rows else None),
        "last_tagged": (tagged_rows[-1]["_ts"].isoformat()
                        if tagged_rows else None),
        "route": _setup_breakdown(
            tagged_rows, _setup_route,
            lambda key: key.upper(), remaining),
        "tide": _setup_breakdown(
            tagged_rows, _setup_tide,
            lambda key: key.upper(), remaining, empty=("RISING",)),
        "family": _setup_breakdown(
            tagged_rows, _setup_family,
            lambda key: key.upper(), remaining, empty=("SCOUT MOVER",)),
    }

    return {
        "generated_at": datetime.now(MST).isoformat(),
        "current_balance": round(current, 2),
        "start_balance": round(start, 2),
        "win_line": win,
        "remaining": round(remaining, 2),
        "applied_closes": len(rows),
        "windows": report_rows,
        "setup": setup,
    }


CONTEST_HISTORY_FILE = STATE_FILE.parent / "contest_history.jsonl"


def score_and_restart(tier: int | None = None) -> dict:
    """Score a WON contest and start the next one (Console-triggered).

    Refuses unless the balance has reached the win line. Appends the finished
    contest's result to contest_history.jsonl, then starts a new contest whose
    START is the current balance, so the death line and the +12% target are
    recalculated from it. The board is cleared exactly as new_contest does.
    """
    s = load_state()
    if not contest_won(s):
        raise ValueError(
            f"contest not won yet: balance {float(s['current_balance']):,.2f} "
            f"is below the win line {win_line(s):,.2f}")
    record = {
        "contest_id": s.get("contest_id"),
        "tier": s.get("tier"),
        "start_balance": float(s["start_balance"]),
        "final_balance": float(s["current_balance"]),
        "win_line": round(win_line(s), 2),
        "result": "won",
        "scored_at": _now_iso(),
    }
    with open(CONTEST_HISTORY_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return new_contest(int(tier or s.get("tier") or DEFAULT_TIER),
                       start_balance=float(s["current_balance"]))


def reanchor_after_win(state: dict | None = None) -> dict:
    """MARK THE CONTEST WON and RE-ANCHOR to the new balance (user rule, Nov 11).

    Writes the won record to contest_history.jsonl, then makes the current
    balance the new contest's START -- so the profit becomes the buffer and the
    next win line is +12% of the new baseline, its death line -3% of it.

    Unlike the console's score_and_restart this KEEPS the position/hail-mary
    state: a human is present when the console scores a contest, but the scout
    may be holding a row when the line is crossed and must not drop its
    tracking. Refuses below the win line, so it can never re-anchor early.
    """
    s = dict(state) if state else load_state()
    if float(s["current_balance"]) < win_line(s):
        raise ValueError(
            f"contest not won yet: balance {float(s['current_balance']):,.2f} "
            f"is below the win line {win_line(s):,.2f}")
    record = {
        "contest_id": s.get("contest_id"),
        "tier": s.get("tier"),
        "start_balance": float(s["start_balance"]),
        "final_balance": float(s["current_balance"]),
        "win_line": round(win_line(s), 2),
        "result": "won",
        "scored_at": _now_iso(),
    }
    with open(CONTEST_HISTORY_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    out = dict(s)
    out["start_balance"] = float(s["current_balance"])
    out["contest_id"] = datetime.now(MST).isoformat()
    out["updated_at"] = datetime.now(MST).isoformat()
    with _StateLock() as _lk:
        if not _lk.held:
            raise RuntimeError("account state busy -- retry in a moment")
        _write_state(out)
    return out


def new_contest(tier: int, start_balance: float | None = None) -> dict:
    """Start a NEW contest window (user rule, Sep 9: the account knows
    which contest it is). The ONLY path that resets the balance to a
    tier's starting amount -- the sidebar's tier select calls this on
    USER INTERACTION (on_change), never on a rerun, so a stale second
    console can no longer reset the account by re-rendering (the Sep 9
    bug: a pinned $10,000 widget reset the 1k account every cycle, which
    also re-widened every sizing gate -- TAO was buried too-thin under
    the phantom 10k). A fresh window stamps a fresh contest_id and
    resets the Hail Mary windows (a new account gets new ones).

    THE WIPE (user rule, Sep 9: "the record is wiped and starts fresh"):
    a new contest also drops the OLD contest's tracked positions -- the
    TIA ghost lesson (a Sep 6 position outlived its contest and shouted
    GET OUT for 3 days into the next one). Position tracking is the old
    contest's business; this window starts clean."""
    tier = int(tier) if int(tier) in TIERS else DEFAULT_TIER
    # a restart after a win starts from the balance it was won at; a plain
    # new contest starts at the tier amount
    start = (float(start_balance) if start_balance is not None
             else TIERS[tier]["start"])
    s = {
        "tier": tier,
        "start_balance": start,
        "current_balance": start,
        "contest_id": datetime.now(MST).isoformat(),
        "updated_at": datetime.now(MST).isoformat(),
    }
    # same rule as save_state: a busy lock raises -- a new contest that
    # silently skipped would leave the file in the OLD window while the
    # user believes a new one started
    with _StateLock() as _lk:
        if not _lk.held:
            raise RuntimeError("account state busy -- retry in a moment")
        _write_state(s)
    # THE WIPE (user rule, Sep 9): the old contest's positions are not
    # this window's business. The TIA ghost (Sep 6 position shouting GET
    # OUT into the Sep 9 contest) is what this kills.
    try:
        # (Sep 27 ledger-leak fix) resolve beside the CURRENT state file:
        # a sandbox that rebinds STATE_FILE carries the wipe with it. The
        # hardcoded repo root let a suite running new_contest() inside a
        # tempdir wipe the LIVE watch ledger — the OP/ONDO rows the
        # battery's prod-guard caught vanishing with no receipts.
        # ATOMIC (the round-2 cross-writer sweep): a torn half-file
        # would fail-soft to {} in every reader anyway — tmp +
        # os.replace keeps the wipe all-or-nothing like every other
        # writer of this file.
        _cw_path = STATE_FILE.parent / "contest_watch.json"
        # THE TOMBSTONE WIPE (the round-4 review): a bare "{}" replace
        # lets a writer already mid-RMW (it read the OLD book) overlay
        # its stale rows right back over the wipe — the ghost row
        # resurrects. None-markers are the fleet's deletion convention;
        # the next merge-save consumes them and the wipe sticks. The
        # read is the SAME file the wipe writes (beside STATE_FILE —
        # the scanner's loader would resolve the scanner's own root and
        # leak foreign rows into a sandbox's wipe), raw json, fail-soft.
        try:
            _cw_cur = json.loads(
                _cw_path.read_text(encoding="utf-8"))
            _cw_wipe = ({k: None for k in _cw_cur}
                        if isinstance(_cw_cur, dict) else {})
        except Exception:
            _cw_wipe = {}
        _cw_tmp = str(_cw_path) + ".tmp"
        with open(_cw_tmp, "w", encoding="utf-8") as _cw_fh:
            json.dump(_cw_wipe, _cw_fh)
        # THE RETRYING REPLACE (the stateclobber storm's second
        # verdict): a rename colliding with a concurrent READER (the
        # console tick, the scanner's every-cycle load — Python's
        # Windows open() does not share delete) fails with the named
        # contention family (WinError 5/32, [Errno 13]) — transient,
        # retryable. The old single attempt, swallowed by this
        # function's fail-soft pass, could leave the wipe SILENTLY
        # undone: the ghosts it exists to kill survive. Bounded
        # backoff; a wipe that loses every attempt stays silent (the
        # next tick / the operator's re-wipe), never crashes.
        for _att in range(6):
            try:
                os.replace(_cw_tmp, str(_cw_path))
                break
            except OSError:
                if _att == 5:
                    break
                time.sleep(0.05 * (_att + 1))
        # THE SETTLE LOOP (the stateclobber storm's verdict, Sep 29): a
        # stale carrier whose read predated the wipe's replace re-reads
        # a marker-free file and merges the old book right back — the
        # raw_tombs defense never sees the markers (they lived exactly
        # one save). So the wipe VERIFIES: briefly re-read and, if an
        # old book's row reappeared as a live dict, re-tombstone ONLY
        # those keys. New rows written after the wipe (the popup's
        # fresh_keys auto-track, a bot fill) are NEVER touched — their
        # pairs were not in the old book, or a marker for them was
        # already cleared by their own fresh re-add. Bounded, small
        # sleeps; the next tick's merge-save consumes whatever
        # survives. Inspired by the storm's heal-to-completion shape —
        # the operator's real next action is "wipe again anyway".
        for _ in range(5):
            time.sleep(0.08)
            try:
                _re = json.loads(_cw_path.read_text(encoding="utf-8"))
                _re = _re if isinstance(_re, dict) else {}
                _regress = {k: None for k, v in _re.items()
                            if v is not None and k in _cw_wipe}
                if not _regress:
                    break
                _re_t = str(_cw_path) + ".tmp"
                with open(_re_t, "w", encoding="utf-8") as _cw_fh2:
                    json.dump({**_re, **_regress}, _cw_fh2)
                for _att2 in range(6):
                    try:
                        os.replace(_re_t, str(_cw_path))
                        break
                    except OSError:
                        if _att2 == 5:
                            break
                        time.sleep(0.05 * (_att2 + 1))
            except Exception:
                break   # a contention loss here is the next tick's job
    except (OSError, ValueError):
        # fail-soft for the ENVIRONMENT only (missing/unreadable file,
        # a corrupt half-document): the next tick or the operator's
        # re-wipe heals it. Programmer errors (wrong STATE_FILE type,
        # bad key types) are NOT swallowed — the stateclobber wave
        # caught a whole wipe silently skipped behind this bare pass,
        # and the skip was undiagnosable. Loud beats silent here.
        pass  # the watch panel would still show the ghost card -- tappable SOLD
    return s


def current_contest_id(state: dict | None = None) -> str:
    """The window stamp of the contest we are in NOW."""
    s = state or load_state()
    return str(s.get("contest_id") or "")


def apply_pnl(net: float, contest_id: str | None = None,
              dedupe_key: str | None = None) -> tuple[bool, float, str]:
    """Apply a trade's net P&L to the CURRENT contest's balance -- but
    only if the trade BELONGS to this contest window (its stamped
    contest_id matches; None = a legacy record, applies as before).
    Returns (applied, balance_now, note).

    This is the un-journaled form: the money moves and the caller writes
    its receipt afterwards. A caller whose receipt must not be lost (the
    SOLD button's ledger) uses apply_pnl_journaled instead.

    UNDER THE STATE LOCK (Copilot round 3, Sep 9): load, judge, write --
    all inside one critical section, so two consoles cannot interleave
    (a lost P&L) and a new_contest cannot slip between load and write
    (a stale window over a fresh balance). Busy (lock contested past
    the retries): the trade is logged by the caller but the P&L is NOT
    applied -- honest skip, never a silent loss. A missing dedupe_key
    falls back to a UNIQUE per-call key (a constant fallback would
    reject every later trade of that pair as a replay)."""
    return _apply_pnl(net, contest_id, dedupe_key, None)


def apply_pnl_journaled(net: float, contest_id: str | None = None,
                        dedupe_key: str | None = None,
                        journal=None) -> tuple[bool, float, str]:
    """Apply the P&L only AFTER the caller's receipt is safely on disk.

    THE ORDER IS THE POINT (Nov 6): the money move used to happen first and
    the receipt second, so a failed ledger append left the balance moved and
    nothing on the books to prove it. Here `journal(applied, balance_now,
    note)` is called INSIDE the state lock, with the decision made and the
    new balance known, but BEFORE `_write_state`. If the journal raises, the
    state file is never touched and the exception propagates: a failed write
    cannot move money. A DECLINED apply (foreign window, replay, busy lock)
    still journals — the trade really did close, only the money did not
    move, and the row says so through `applied`/`note`.

    A journal that succeeds and a state write that then fails leaves a
    receipt for a move that did not happen: the honest direction (nothing
    moved silently), logged as a critical by the caller's exception path.
    """
    return _apply_pnl(net, contest_id, dedupe_key, journal)


def _apply_pnl(net, contest_id, dedupe_key, journal):
    """The one decision path: journal (if any) before the balance write."""
    def _settle(res):
        if journal is not None:
            journal(*res)
        return res

    with _StateLock() as _lk:
        if not _lk.held:
            return _settle((False, load_state()["current_balance"],
                            "account state busy -- trade logged, P&L not applied"))
        s = load_state()
        here = str(s.get("contest_id") or "")
        if contest_id and here and str(contest_id) != here:
            return _settle((False, float(s["current_balance"]),
                            f"trade from another contest window "
                            f"(record {contest_id} vs here {here})"))
        key = dedupe_key or f"{here or 'legacy'}|{_now_iso()}"
        seen = s.get("applied_trades") or {}
        if key in seen:
            return _settle((False, float(s["current_balance"]),
                            "trade already applied (replayed SOLD)"))
        nb = max(float(s["current_balance"]) + float(net or 0), 0.0)
        # THE RECEIPT FIRST (Nov 6). It runs inside the lock so no other
        # writer can move the balance between the receipt and its commit;
        # a raise here aborts before any state is written.
        if journal is not None:
            journal(True, nb, "")
        seen[key] = round(float(net or 0), 2)
        s["applied_trades"] = dict(list(seen.items())[-50:])
        s["current_balance"] = nb
        s["updated_at"] = _now_iso()
        _write_state(s)
    return True, nb, ""


def _write_state(s: dict) -> None:
    tmp = str(STATE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(s, indent=1))
    os.replace(tmp, STATE_FILE)


# -- THE STATE LOCK (Copilot round 3, Sep 9): the account file is written
# by every console AND the scanner -- bare load/modify/write loses updates
# when two writers interleave (a lost P&L, or a stale window overwriting a
# fresh one). One O_EXCL lockfile guards every WRITE (reads stay lock-free:
# os.replace is atomic, a reader sees the whole old or whole new record).
# Bounded retry, stale takeover (a crashed run's lock ages out), and
# pid-ownership release -- the atomic tilt-writer pattern.
STATE_LOCK_FILE = STATE_FILE.with_suffix(".json.lock")
PNL_LOCK_RETRIES = 6
PNL_LOCK_RETRY_S = 0.05
PNL_LOCK_STALE_S = 10.0


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        h = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if h:
            k32.CloseHandle(h)
            return True
        return False
    except Exception:
        return True    # probe broken: assume alive, let staleness decide


class _StateLock:
    """A create-if-absent lockfile (atomic os.link), bounded retries,
    stale takeover; release only if the file still carries OUR token
    (round 4: pid + a fresh uuid per acquisition -- token ownership
    kills the same-pid cross-thread deletion a bare pid allowed)."""

    def __init__(self):
        self.held = False

    def __enter__(self):
        import time as _t
        import uuid as _uuid
        # pid|iso|uuid (round 4, review-critical fix): the iso timestamp
        # STAYS in the payload (the staleness parse reads field 2 -- a
        # bare pid|uuid token parsed as stale-forever and every lock
        # would be stolen instantly), the uuid makes ownership unique
        # per acquisition (same-pid threads can't delete each other's)
        self._token = f"{os.getpid()}|{_now_iso()}|{_uuid.uuid4().hex}"
        tmp = Path(str(STATE_LOCK_FILE)
                   + f".tmp{os.getpid()}.{_uuid.uuid4().hex[:8]}")
        for attempt in range(PNL_LOCK_RETRIES + 1):
            # write the payload FIRST at a private temp path, then move
            # it into place with os.link -- create-IF-ABSENT and atomic,
            # so no observer ever sees a young half-written lock it
            # could mistake for stale and steal, and the acquire FAILS
            # instead of clobbering when a holder exists (os.replace
            # would silently overwrite the holder and break mutual
            # exclusion)
            try:
                fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
                os.write(fd, self._token.encode())
                os.close(fd)
                os.link(str(tmp), str(STATE_LOCK_FILE))
                self.held = True
                return self
            except FileExistsError:
                try:
                    _parts = str(STATE_LOCK_FILE.read_text(encoding="utf-8")).split("|")
                    pid = int(_parts[0])
                    ts = _iso_ts(_parts[1]) if len(_parts) > 1 else None
                except Exception:
                    pid, ts = None, None
                age = (_now_ts() - ts) if ts else 999.0
                if (age > PNL_LOCK_STALE_S
                        or (pid is not None and pid != os.getpid()
                            and not _pid_alive(pid))):
                    try:
                        STATE_LOCK_FILE.unlink()   # stale takeover
                    except Exception:
                        pass
                _t.sleep(PNL_LOCK_RETRY_S)
            except OSError:
                _t.sleep(PNL_LOCK_RETRY_S)
            finally:
                tmp.unlink(missing_ok=True)
        return self            # NOT held -- caller checks .held

    def __exit__(self, *exc):
        if self.held:
            try:
                txt = STATE_LOCK_FILE.read_text(encoding="utf-8")
                if str(txt).strip() == self._token:
                    STATE_LOCK_FILE.unlink()
            except Exception:
                pass
            self.held = False
        return False


def _now_iso() -> str:
    return datetime.now(MST).isoformat()


def _now_ts() -> float:
    return datetime.now(MST).timestamp()


def _iso_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


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


def hail_mary_opp_available(state: dict | None = None) -> tuple[bool, str]:
    """(available, why_not) — the OPPORTUNISTIC mode's account side: the
    SAME account-in-danger door as the classic window (under break-even
    with platform room, one window per rotation — the modes SHARE the
    rotation slot, never stack a second window). Strict caps live with
    the callers' candidate check; this is the door. Pure read;
    fail-closed on any doubt."""
    return hail_mary_available(state)


def activate_hail_mary(state: dict | None = None,
                       mode: str = "classic") -> dict:
    """Open the one-hour window: the emergency hour runs, the platform
    death line + every risk control stand. Stamps the rotation (one
    window per rotation) + the count. The QUALIFYING SETUP is the
    callers' check — this only opens the door. mode stamps the
    window's own mode on the state (the console plate and the QC read
    it) — the candidate bars and the risk caps differ by mode; the
    window and the rotation slot do not."""
    s = state or load_state()
    now = datetime.now(MST)
    s["hail_mary_until"] = (now + timedelta(minutes=HAIL_MARY_WINDOW_MIN)).isoformat()
    s["hail_mary_date"] = _today_str()
    s["hail_mary_rot"] = f"{now.hour // 4 * 4:02d}-{now.hour // 4 * 4 + 4:02d}"
    s["hail_mary_count"] = int(s.get("hail_mary_count") or 0) + 1
    s["hail_mary_mode"] = "opportunistic" if mode == "opportunistic" else "classic"
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
    """The NORMAL weekly-bar pace: weekly goal / 7 calendar days.

    On the 10k tier this is $1,200 / 7 = $171.43/day. It is not the live
    daily checkpoint: if the account is behind, the checkpoint is
    remaining ÷ days left, which is higher. The 12%/day one-day sprint is
    a separate stretch target (see `one_day_sprint_usd`).
    """
    s = state or load_state()
    return (float(s["start_balance"]) * WEEKLY_GOAL_PCT / 100.0
            / WEEKLY_DAYS)


def contest_start(state: dict | None = None):
    """The contest anchor timestamp from `contest_id`, or None."""
    s = state or load_state()
    raw = str(s.get("contest_id") or "")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MST)
    return dt


def days_left(state: dict | None = None, now=None) -> float | None:
    """Calendar days left in the contest's seven-day weekly bar."""
    start = contest_start(state)
    if start is None:
        return None
    now = now or datetime.now(MST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MST)
    deadline = start + timedelta(days=WEEKLY_DAYS)
    return max(0.0, (deadline - now).total_seconds() / 86400.0)


def weekly_target_usd(state: dict | None = None) -> float:
    """The cumulative +12% dollar target from the contest anchor."""
    s = state or load_state()
    return round(win_line(s) - float(s["start_balance"]), 2)


def daily_checkpoint_usd(state: dict | None = None, now=None) -> float | None:
    """Live daily checkpoint: remaining dollars ÷ days left.

    Returns 0.0 once the target is reached and None when the contest has
    no usable start stamp (the caller must not invent a pace).
    """
    s = state or load_state()
    remaining = max(0.0, win_line(s) - float(s["current_balance"]))
    if remaining <= 0.0:
        return 0.0
    left = days_left(s, now=now)
    if left is None or left <= 0.0:
        return None
    return round(remaining / left, 2)


def one_day_sprint_usd(state: dict | None = None) -> float:
    """The separate 12%/day sprint: the whole +12% target in one day."""
    s = state or load_state()
    return round(float(s["start_balance"]) * ONE_DAY_SPRINT_PCT / 100.0, 2)


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


# ── THE SEPTEMBER GIVE-BACK TUNING, TIER-SCALED (Sep 29) ────────────
# The journal study (scratch/giveback_tier_study.py, 54 applied
# September closes) showed the fixed $50 tighten caught only the
# blowoff (Sep 26, -118) and missed BOTH real erosion days that closed
# negative after being positive (Sep 22 -26.0, Sep 29 -23.9); the
# crossing-day median was $11.40, so the fixed $25 speak floor spoke
# almost nothing. The thresholds scale with the tier as % of start —
# the Sep 1 "everything auto-adjusts with the tier" rule:
#   tighten 0.20% of start  ($20 @ 10k, $10 @ 5k, $2 @ 1k)
#   speak   0.10% of start  ($10 @ 10k, $5 @ 5k, $1 @ 1k)
# 0.20% is the calibration the study justifies: at the $20 line the
# crossing table keeps its PERFECT separation (every crossing day
# closed >= $10 under its own peak — 2.0x the base rate) AND both
# missed days now arm. The helper lives HERE because the thresholds
# are contest-account math (tier-scaled) — the scanner's tracker, the
# voice lead, and the console's gauge all read this one function.
GIVEBACK_TIGHTEN_PCT_OF_START = 0.0020
GIVEBACK_SPEAK_PCT_OF_START = 0.0010


def giveback_thresholds() -> tuple[float, float]:
    """(tighten_usd, speak_usd) for the LIVE contest tier. Fail-soft to
    the 10k default: a broken account read degrades to the calibrated
    10k numbers, NEVER to zero — a zero trigger would tighten on any
    red tick and speak every scratch trade."""
    try:
        _meta = TIERS.get(int(load_state().get("tier") or 0))
        _start = float((_meta or {}).get("start") or 0.0)
        if _start > 0:
            return (_start * GIVEBACK_TIGHTEN_PCT_OF_START,
                    _start * GIVEBACK_SPEAK_PCT_OF_START)
    except Exception:
        pass
    return (10_000.0 * GIVEBACK_TIGHTEN_PCT_OF_START,
            10_000.0 * GIVEBACK_SPEAK_PCT_OF_START)


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


def sized_entry_usd(current_balance: float, window_dd_pct: float,
                    start_balance: float | None = None,
                    size_frac: float = 1.0) -> float:
    """WHAT LIVE SPENDS ON ONE ENTRY: `size_frac` of the balance, capped by the
    3% guard. THE single sizing rule (Nov 11).

    Why it exists: the scanner's base spend used to be 100% of the balance, so
    `max_entry_usd` alone decided the size and a configured fraction could not
    bind. Measured on the live account at a $203.91 buffer, that cap ranged
    $462 (wide coins) to $2,938 (BTC) -- 4.7% to 29.7% of the balance. A
    ceiling makes the risk per start a number the operator picks.

    `size_frac` is a CEILING, not a target: the guard cap still cuts it while
    the buffer is thin (that is the death-line rule doing its job), and
    returns 0.0 exactly when the buffer is gone. Clamped to (0, 1].
    """
    bal = float(current_balance or 0.0)
    if bal <= 0.0:
        return 0.0
    frac = float(1.0 if size_frac is None else size_frac)
    frac = min(max(frac, 0.0), 1.0)
    want = bal * frac
    if want <= 0.0:
        return 0.0
    cap = max_entry_usd(bal, window_dd_pct, start_balance)
    return float(min(want, cap, bal))


# ── GO BIG OR GO HOME (user rule, Nov 13) ────────────────────────────────
# The old per-trade envelope is superseded for a qualified entry: when the
# coin and the rotation support it, the whole account goes in. The trade rides
# until momentum dies (the exit watch's GET OUT), not a fixed dollar take, and
# the next entry waits for a fresh call. The protection is the hard dollar
# stop, not a smaller notional.
# ONE SOURCE OF TRUTH (Nov 13): the validated 100 / 500 rule is 1% and 5% of
# the CONTEST START, never an absolute dollar amount. At the $10,000 tier that
# is exactly 100/500; at any other tier it keeps the same shape — the stop is
# a third of the 3% death line, the take ~42% of the 12% target. Callers may
# still pin their own marks (take 0 rides until momentum dies).
GO_BIG_STOP_FRAC = 0.01
GO_BIG_TAKE_FRAC = 0.05
GO_BIG_MIN_CONVICTION = 85.0
GO_BIG_FLAT_TIDE_MIN = 50.0   # FLAT is eligible at real-tape breadth; the
                              # RISING-only bar left GO BIG almost never firing
                              # (Nov 13 real tape: only 6 of 426 signal hours
                              # were at/above the 75% RISING line).


def go_big_supported(*, action: str, tide_state: str | None,
                     carry_usd: float | None, balance: float,
                     conviction: float,
                     floor: float = GO_BIG_MIN_CONVICTION,
                     tide_pct: float | None = None,
                     slot_open: bool = False) -> tuple[bool, str]:
    """Can the coin and the market rotation carry an all-in entry?

    Facts, all observed rather than assumed: the signal is a BUY; the fleet
    tide either RISING, or FLAT with real breadth at/above
    `GO_BIG_FLAT_TIDE_MIN`; the visible book carries the whole account at the
    20%-of-book carry rule; conviction clears the champion floor; and no
    all-in position is already open. `slot_open` is the single-slot rule: the
    account cannot be all-in twice, so a new all-in waits until the existing
    position is sold. Returns (ok, why) and never raises.
    """
    try:
        bal = float(balance or 0.0)
        conv = float(conviction or 0.0)
        carry = None if carry_usd is None else float(carry_usd)
        tide = str(tide_state or "").upper()
        pct = None if tide_pct is None else float(tide_pct)
        if str(action or "").lower() != "buy":
            return False, "not a buy signal"
        if slot_open:
            return False, "an all-in position is already open — wait for the sell"
        if tide != "RISING" and not (
                tide == "FLAT" and pct is not None
                and pct + 1e-9 >= GO_BIG_FLAT_TIDE_MIN):
            return False, (f"rotation tide is {tide_state or 'unreadable'}, "
                           f"not RISING or FLAT >= {GO_BIG_FLAT_TIDE_MIN:.0f}%")
        if carry is None or carry <= 0.0:
            return False, "book carry is unreadable"
        if carry + 1e-9 < bal:
            return False, (f"book carries ${carry:,.0f} of the "
                           f"${bal:,.0f} account")
        if conv + 1e-9 < float(floor or 0.0):
            return False, (f"conviction {conv:.0f}% is under the "
                           f"{float(floor):.0f}% GO BIG floor")
        return True, (f"BUY {conv:.0f}% in a {tide} tide"
                      + (f" ({pct:.0f}% breadth)" if pct is not None else "")
                      + f" with ${carry:,.0f} of book carry for ${bal:,.0f}")
    except (TypeError, ValueError) as exc:
        return False, f"GO BIG support unreadable ({exc})"


def go_big_marks(start_balance: float) -> tuple[float, float]:
    """(stop_usd, take_usd) for a GO BIG entry at this contest start."""
    try:
        start = max(0.0, float(start_balance or 0.0))
    except (TypeError, ValueError):
        start = 0.0
    return (round(start * GO_BIG_STOP_FRAC, 2),
            round(start * GO_BIG_TAKE_FRAC, 2))


def go_big_plan(current_balance: float, start_balance: float, *,
                supported: bool, stopped: bool = False,
                stop_usd: float | None = None,
                take_usd: float | None = None,
                min_buffer_usd: float = 0.0,
                fee_pct: float = COST_ROUNDTRIP_PCT) -> dict:
    """THE GO BIG ENTRY: 100% of the account, 1% net stop, 5% take of start.

    `supported` is the caller's coin + rotation read (`go_big_supported`).
    When it is false, or the account is halted, this returns spend 0 — no
    all-in entry without support. When it is true the answer is the WHOLE
    current balance. The trade books a fixed +$20 take; a caller can pass 0 to
    ride until momentum dies. The stop is NET of the round-trip fee: the
    required death-line room is `stop_usd + balance * fee_pct/100`, so a $50
    net loss can never be
    the trade that crosses the line. At/below the safety floor the answer is
    0. Never raises.
    """
    out = {"spend_usd": 0.0, "stop_usd": 0.0, "take_usd": 0.0,
           "room_usd": 0.0, "go_big": False, "ok": False, "why": ""}

    def _fin(x, default=0.0):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return float(default)
        if v != v or v in (float("inf"), float("-inf")):
            return float(default)
        return v

    bal = max(0.0, _fin(current_balance))
    start = max(0.0, _fin(start_balance))
    _d_stop, _d_take = go_big_marks(start)
    stop = _d_stop if stop_usd is None else max(0.0, _fin(stop_usd))
    take = _d_take if take_usd is None else max(0.0, _fin(take_usd))
    out.update({"stop_usd": round(stop, 2), "take_usd": round(take, 2)})
    if not supported:
        out["why"] = "coin/rotation do not support an all-in entry"
        return out
    if stopped:
        out["why"] = "the account is halted"
        return out
    if bal <= 0.0 or start <= 0.0:
        out["why"] = "the account is unreadable"
        return out
    room = bal - start * (1.0 - MAX_LOSS_PCT / 100.0)
    out["room_usd"] = round(room, 2)
    if room <= max(0.0, _fin(min_buffer_usd)):
        out["why"] = f"no room above the safety floor (${room:,.2f})"
        return out
    fees = bal * max(0.0, _fin(fee_pct)) / 100.0
    worst_net = stop + fees
    if worst_net > room + 1e-9:
        out["why"] = (f"a ${stop:,.2f} net stop plus ${fees:,.2f} fees "
                      f"does not fit the ${room:,.2f} buffer")
        return out
    _exit = (f"fixed take ${take:,.2f}" if take > 0.0
             else "momentum exit (GET OUT when the run dies)")
    out.update({"spend_usd": round(bal, 2), "go_big": True, "ok": True,
                "why": (f"all-in ${bal:,.2f}; {_exit}; "
                        f"hard net stop ${stop:,.2f}")})
    return out


def hard_death_entry_usd(current_balance: float, start_balance: float,
                         stop_pct: float, size_frac: float = 1.0, *,
                         fee_pct: float = COST_ROUNDTRIP_PCT,
                         risk_frac: float = RISK_PER_TRADE_FRAC,
                         window_dd_pct: float | None = None,
                         min_buffer_usd: float = 0.0,
                         halted: bool = False) -> float:
    """Size one entry so its WORST CASE cannot cross the death line.

    This is the hard gate the contest loop calls in the same expression that
    decides spend.  It is deliberately stricter than a separate "is the
    account near the line?" check:

        room        = balance - (start * 0.97)
        worst_loss  = max(stop_pct, 0.5) + fee_pct
        risk_cap    = room * risk_frac / (worst_loss / 100)
        spend       = min(size_frac * balance, risk_cap, balance)

    The resulting loss at the stop plus fees consumes at most `risk_frac` of
    the remaining buffer. At or below the line room is non-positive and the
    answer is exactly 0.0. A
    caller cannot pass a larger size_frac to defeat it.

    The safety floor is part of this function, not a caller-side veto.  If
    room is at/below `min_buffer_usd`, or `halted` is true, the answer is
    exactly 0.0.  That makes the floor impossible to bypass by calling the
    sizing rule directly with a different size.

    `window_dd_pct` optionally adds the scanner's existing profiled-drawdown
    ceiling, so the loop never sizes above what the live path allows.
    """
    bal = float(current_balance or 0.0)
    start = float(start_balance or 0.0)
    if halted or bal <= 0.0 or start <= 0.0:
        return 0.0
    room = bal - start * (1.0 - MAX_LOSS_PCT / 100.0)
    floor = max(0.0, float(min_buffer_usd or 0.0))
    if room <= floor:
        return 0.0
    frac = min(max(float(size_frac if size_frac is not None else 1.0), 0.0), 1.0)
    worst_loss = min(100.0, max(float(stop_pct or 0.0), 0.5)
                     + max(float(fee_pct or 0.0), 0.0))
    risk_cap = room * max(0.0, float(risk_frac)) / (worst_loss / 100.0)
    caps = [bal * frac, risk_cap, bal]
    if window_dd_pct is not None:
        caps.append(max_entry_usd(bal, float(window_dd_pct), start))
    spend = max(0.0, min(caps))
    return float(spend)


def stress_book_cap_usd(current_balance: float,
                        start_balance: float | None = None,
                        gap_pct: float | None = None,
                        frac: float | None = None) -> float:
    """THE GAP-STRESS CAP: the largest WHOLE BOOK — every open row's cost
    basis plus the candidate being sized — whose worst case at ONE adverse
    gap still leaves the death-line buffer standing.

    A per-trade 3% guard and a notional cap both answer "how big may one
    row be"; neither answers "what happens if they all move together",
    which is exactly how the last contest died. Stated in BUFFER units so
    it tightens as the account bleeds: at the fresh $10k window, $300 x
    0.50 / 3% = $5,000; with half the buffer gone, $2,500.

    Returns 0.0 at or below the death line — a dead account gets no book
    (the sizing guard returns 0 there too; this one says it in book terms).
    """
    start = (start_balance if start_balance is not None
             else TIERS[DEFAULT_TIER]["start"])
    buf = float(current_balance) - start * (1.0 - MAX_LOSS_PCT / 100.0)
    if buf <= 0:
        return 0.0
    gap = max(float(GAP_STRESS_PCT if gap_pct is None else gap_pct), 0.1) / 100.0
    frac = float(MAX_BUFFER_STRESS_FRAC if frac is None else frac)
    return max(0.0, buf * max(0.0, frac) / gap)


def stress_allowance_usd(current_balance: float,
                         start_balance: float | None = None,
                         frac: float | None = None) -> float:
    """The DOLLARS of whole-book stress loss this window may carry — half
    the remaining buffer ($150 at the fresh $10k line).
    `stress_book_cap_usd` states the same allowance as a book size; this one
    states it in the currency the per-row model adds up, and at frac=1.0 it
    is the buffer itself (the hard stop's half-buffer input)."""
    start = (start_balance if start_balance is not None
             else TIERS[DEFAULT_TIER]["start"])
    buf = float(current_balance) - start * (1.0 - MAX_LOSS_PCT / 100.0)
    frac = float(MAX_BUFFER_STRESS_FRAC if frac is None else frac)
    return max(0.0, buf) * max(0.0, frac)


def stress_beta(pair: str) -> float:
    """How hard ONE market-wide flush hits this coin, relative to the fleet:
    the coin's PROFILED window drawdown over the 3% default, clamped. BTC
    rides a flush at ~0.5x; a POL/TIA mover at 2.5-3x — the difference the
    flat 3%-per-book model could not see. Unknown profile -> 1.0 (the fleet
    mean), never 0: no coin is free insurance against a market move."""
    try:
        dd = float(window_drawdown_pct(pair))
    except Exception:
        return 1.0
    if not (dd > 0.0):
        return 1.0
    return max(STRESS_BETA_MIN,
               min(STRESS_BETA_MAX, dd / DEFAULT_WINDOW_DD_PCT))


def stress_row_loss_usd(size_usd: float, stop_pct: float | None = None,
                        beta: float = 1.0,
                        gap_pct: float | None = None,
                        slip_mult: float | None = None) -> float:
    """One row's worst case in a flush, in dollars:

        lost = size x min( beta x gap , stop% x slip )

    The first term is the coin's share of the market move — that is the
    price you actually pay. The second term is what its real stop would
    cost if the fill lands on a 5x slip: it can only LOWER the number when
    the stop is tighter than the move (stop% x 5 < beta x gap), which is
    exactly the case a genuinely tight exit deserves credit for. Today's
    2.5%-of-size stop prices at 12.5% here, so it binds nothing — the
    model says out loud that the stop is not protection in a flush
    instead of pretending it caps the loss."""
    gap = float(GAP_STRESS_PCT if gap_pct is None else gap_pct)
    slip = float(STRESS_SLIP_MULT if slip_mult is None else slip_mult)
    move = max(float(beta or 0.0), 0.0) * gap / 100.0
    try:
        if stop_pct is not None and float(stop_pct) > 0.0:
            move = min(move, float(stop_pct) / 100.0 * slip)
    except (TypeError, ValueError):
        pass
    return max(0.0, float(size_usd or 0.0)) * move


def stress_loss_usd(legs, gap_pct: float | None = None,
                    slip_mult: float | None = None) -> float:
    """The whole book's worst case: sum `stress_row_loss_usd` over legs of
    (size_usd, stop_pct | None, beta). A malformed leg is skipped, never
    counted as free."""
    total = 0.0
    for leg in legs or ():
        try:
            size, stop, beta = leg
        except Exception:
            continue
        total += stress_row_loss_usd(size, stop, beta, gap_pct, slip_mult)
    return total


# ── THE WEAKENED SIZING (Nov 7, the second deadlock) ─────────────────
#    The Nov 6 min-size clamp booked 10% of the sized base and the worth
#    floor then refused every one of those fires: $187 at a +2.6% goal
#    pays $5 against a $24 pace bar. "Weakened" had quietly become
#    "buried" — 73 such refusals in one morning and one trade for the
#    day. A weakened fire is still a REDUCED fire (it may never exceed
#    half the base the funnel sized), but it is now sized to the SMALLEST
#    entry that pays the pace bar at its OWN goal, then clamped by the
#    dollars the death line can still lose in a flush and by the room
#    left under the book cap. No size that both pays and fits -> an
#    explicit NO with the arithmetic, never a minimum that the envelope
#    buries two gates later.
#    The cap is the base ITSELF, not half of it. A half-base cap looks like
#    prudence and is the same deadlock at a different number: a $2,149 base
#    at a +1.2% goal needs $1,936 to pay, so half of it ($1,075) could never
#    pay either, and the resize would have buried the very fires it exists
#    to save. The reduction is whatever the worth rule implies — usually a
#    large one ($3,400 -> $925) — and when the funnel already sized near the
#    paying minimum there is nothing left to reduce, only a choice: fire it
#    at worth, or refuse it.
WEAKENED_MAX_FRAC = 1.00      # of the sized base — never MORE than the funnel would risk
WEAKENED_MIN_FRAC = 0.10      # the Nov 6 floor, kept as the unmeasured case


def _money(x):
    """A cents figure, or None. NaN / infinities / junk are never money."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return round(v, 2)


def pays_the_bar(pay_usd, bar_usd) -> bool:
    """Does this fire's own goal pay the pace bar — judged at CENT precision.

    (Nov 7) The worth floor compared raw floats, so a fire sitting exactly on
    the bar ($1,081 at +2.3% = $24.863 against a $24.863 bar) could miss by a
    fraction of a cent and be labelled

        weak (worth-floor: a $1081 entry at +2.3% pays $25 (< $25 pace bar))

    — a sentence that contradicts itself, and contradicts the resize that then
    certified the very same size two lines later. Both numbers are printed in
    whole dollars and the ledger books cents, so the comparison is made where
    the money is decided: rounded to the cent. Junk is not payment.
    """
    pay, bar = _money(pay_usd), _money(bar_usd)
    if pay is None or bar is None:
        return False
    return pay >= bar


def measured_pace_pct_per_hour(closes, min_samples: int = 4,
                               cap_pct: float = 6.0):
    """The coin's OWN hourly pace, in %/hour, from its hourly closes.

    The MEDIAN of the hourly returns (robust to a single spike), over at
    least `min_samples` hours, capped at `cap_pct`. Returns None when it
    cannot be measured (too few closes, a zero price, junk) — the caller
    then keeps the engine's 1%/hour planning assumption, which is what every
    fire used before this rule existed.

    A negative median is returned as measured: the funnel treats it as "no
    faster than the assumption", never as a reason to lengthen a plan.
    """
    try:
        px = [float(c) for c in (closes or []) if c is not None]
    except (TypeError, ValueError):
        return None
    px = [c for c in px if c > 0.0]
    if len(px) < int(min_samples) + 1:
        return None
    rets = []
    for i in range(1, len(px)):
        if px[i - 1] > 0.0:
            rets.append((px[i] / px[i - 1] - 1.0) * 100.0)
    if len(rets) < int(min_samples):
        return None
    rets.sort()
    _n = len(rets)
    med = rets[_n // 2] if _n % 2 else (rets[_n // 2 - 1] + rets[_n // 2]) / 2.0
    if med != med:                      # NaN can never be a pace
        return None
    return min(med, float(cap_pct))


# ── THE DOLLAR GOAL (Nov 10, user ask): the exit target stated in the
#    currency the entry logic already speaks (the pace bar). A % goal is the
#    same for a $50 row and a $2,000 row, so it pays $0.75 on one and $30 on
#    the other for the same amount of correct market call. Sizing the take
#    in DOLLARS lets a big row bank quick cash on a small move — that is the
#    point — but the take must never be cheaper than the trade costs to
#    make. These three numbers are the bound on that idea.
GOAL_COST_RT_PCT = 0.08      # the contest's cost: 0.04% spread each side
GOAL_MIN_EDGE_MULT = 3.0     # a take must clear THREE round trips
GOAL_MIN_EDGE_PCT = 0.35     # ...and never be a sub-noise scalp


def row_goal_usd(size_usd: float, *, goal_pct: float,
                 pace_bar_usd: float = 0.0, rows_open: int = 1,
                 risk_usd: float | None = None,
                 cost_rt_pct: float | None = None,
                 min_edge_mult: float | None = None,
                 min_edge_pct: float | None = None,
                 max_goal_pct: float | None = None) -> dict:
    """The DOLLAR goal a row exits at — pure and total.

    target = max(pace share, size x the take floor), capped by the plan:

      * the PACE SHARE is what the entry logic says this row must earn to
        keep the contest on pace — `pace_bar_usd` split across the rows the
        book can carry. It does NOT scale with the row, so a bigger row
        reaches it in a smaller move. That is the user's rule: big rows
        scalp, small rows ride.
      * the FLOOR is `max(this row's own 1R, cost x 3, 0.35%)`. The 1R term
        is the one the tape taught (Nov 10): a take smaller than the row's
        own risk does not pay, however often it fires — at 0.1R the replay
        wins 85% of its trades and still loses money. A fixed dollar
        take on a very large row would otherwise sit INSIDE the round trip
        (at $2,000 and $5 the move is 0.25% against a 0.26% taker cost):
        churn is not free at size, it is 0.08% of the notional in this
        contest and 0.26% at the console's taker rate, so the scalping
        idea is bounded by the trade's own cost, never by hope.
      * the PLAN's own goal (`max_goal_pct`, default `goal_pct`) can only
        RAISE the take, never lower it under the risk: the shipped 2.5%-of
        -size stop makes 1R wider than a ~1.5% plan goal on every row, so a
        plan-capped take would refuse the whole book. The consequence is
        worth saying out loud — with this stop the dollar goal is pinned at
        2.5% of the row, so a dollar take does NOT let a bigger row scalp a
        smaller move. Shrinking the stop in step does not buy it either
        (measured Nov 10, `_tightstop.py`): net expectancy per trade falls
        monotonically as the take shrinks — 0.43% takes lose $0.034 a trade,
        0.65% break even, the shipped 2.01% take earns $0.94 — because the
        whipsaw and the churn scale with trade count while the dollars per
        win shrink. The floor is the answer, not a smaller move.

    Returns {"goal_usd", "implied_pct", "floor_usd", "cap_usd", "source",
    "ok", "why"}. `source` is "pace" when the bar set the target, "edge"
    when the floor did. Never raises.
    """
    out = {"goal_usd": 0.0, "implied_pct": 0.0, "floor_usd": 0.0,
           "cap_usd": 0.0, "source": "", "ok": False, "why": ""}

    def _fin(x) -> float:
        """A dollar figure or 0.0 — NaN and the infinities are never a goal."""
        try:
            v = float(x or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if v != v or v in (float("inf"), float("-inf")):
            return 0.0
        return max(v, 0.0)

    size, goal = _fin(size_usd), _fin(goal_pct)
    cost = GOAL_COST_RT_PCT if cost_rt_pct is None else _fin(cost_rt_pct)
    mult = GOAL_MIN_EDGE_MULT if min_edge_mult is None else _fin(min_edge_mult)
    edge = GOAL_MIN_EDGE_PCT if min_edge_pct is None else _fin(min_edge_pct)
    if size <= 0.0:
        out["why"] = "no sized row — nothing to take"
        return out
    if goal <= 0.0:
        out["why"] = "the fire names no goal %, so it has no plan to cap the take"
        return out
    floor_pct = max(cost * mult, edge)
    floor_usd = max(size * floor_pct / 100.0, _fin(risk_usd))
    cap_pct = goal if max_goal_pct is None else min(goal, _fin(max_goal_pct) or goal)
    # The plan's goal is a FLOOR ON THE AMBITION, not a ceiling on the risk
    # (Nov 10, from the live numbers): with the shipped 2.5%-of-size stop, 1R
    # is always wider than a 1.5% plan goal, so capping the take at the plan
    # would refuse every real row. The take may therefore never be pushed
    # under the row's own risk — the plan can only raise it.
    cap_usd = max(size * cap_pct / 100.0, floor_usd)
    rows = max(1, int(rows_open or 1))
    pace_share = _fin(pace_bar_usd) / rows if _fin(pace_bar_usd) > 0.0 else 0.0
    target = min(max(pace_share, floor_usd), cap_usd)
    out["floor_usd"], out["cap_usd"] = floor_usd, cap_usd
    out.update({"goal_usd": target, "implied_pct": target / size * 100.0,
                "source": "pace" if pace_share > floor_usd else "edge",
                "ok": target > 0.0})
    if not out["ok"]:
        out["why"] = "the take resolves to zero"
    return out


def hold_reachable_goal_pct(hold_cap_min, margin_min: float = 5.0,
                           pace_pct_per_hour=None) -> float:
    """The largest goal whose PLAN still drains inside the mandate's hold cap.

    A plan is goal x 60 minutes at the engine's 1%/hour assumption, and the
    cap is exclusive ("the mandate caps it UNDER 90 min"), so the window
    leaves `margin_min` minutes of headroom. The armed lesson's cap of 90
    therefore reaches +1.42% (+85 min), not +2.6% (+162 min).

    Returns 0.0 when the cap is unreadable or too small to hold any plan at
    all — the caller then SKIPS the candidate at scan time instead of building
    a fire the mandate is arithmetically certain to refuse. This never
    proposes a plan at or above the cap: the cap is a constraint to satisfy,
    never a threshold to move.
    """
    cap, margin = _money(hold_cap_min), _money(margin_min)
    if cap is None or not (cap > 0.0):
        return 0.0
    if margin is None:
        margin = 0.0
    left = cap - margin
    if not (left > 0.0):
        return 0.0
    # The reach is pace x time. With no measurement the engine's 1%/hour
    # assumption stands, which is the reach every fire used before the pace
    # was read. A pace is only ever allowed to make the reach BIGGER: a quiet
    # six hours does not prove a breakout cannot happen, and shrinking the
    # reach against a cap priced at 1%/h would be moving the threshold.
    pace = _money(pace_pct_per_hour)
    if pace is None or pace < 1.0:
        pace = 1.0
    return pace * left / 60.0


def paying_tranche_usd(base_spend_usd: float, *, goal_pct: float,
                       pace_bar_usd: float = 0.0,
                       ceiling_usd: float | None = None,
                       allowance_usd: float | None = None,
                       beta: float | None = None,
                       book_room_usd: float | None = None,
                       stop_pct: float | None = None) -> dict:
    """The funnel's tranche, sized to PAY the pace bar — not to a fraction
    of itself. Pure and total.

    (Nov 7) The funnel sizes a fire balance -> the 3% guard's ceiling ->
    the brain's confidence multiplier -> the tide factor -> the book carry.
    A 0.10x confidence read therefore books a MINIMUM-VIABLE base ($330
    under a $3,300 ceiling) and the worth floor then refuses it, because
    the $25 bar needs $1,710 at +1.5%. The brain is a vote on how much of
    the tranche to use; it must never be a vote on whether the fire is
    worth doing at all — that is the pace bar's question, and the bar is
    answered in dollars.

    So when a bar is set and the base cannot pay it, the tranche is RAISED
    to the smallest entry that pays, capped by:

      * the guard's own ceiling — the raised tranche never exceeds what the
        -3% line already priced as affordable,
      * the allowance left, priced at this row's beta and real stop,
      * the book room left under the aggregate cap.

    Returns {"size", "ok", "raised", "why", "pace_min", "cap"}.
      ok False  -> the raise cannot reach the paying size; the caller says
                   so with the arithmetic (never a silent minimum).
      raised    -> True only when the size actually moved.
      A missing ceiling, allowance or book room is NOT permission to raise
      blind: no ceiling at all means the base is left exactly as it was.
    """
    out = {"size": 0.0, "ok": False, "raised": False, "why": "",
           "pace_min": 0.0, "cap": 0.0}

    def _fin(x) -> float:
        """A dollar figure or 0.0 — NaN and the infinities are NEVER a size."""
        try:
            v = float(x or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if v != v or v in (float("inf"), float("-inf")):
            return 0.0
        return v

    try:
        base = _fin(base_spend_usd)
        goal = _fin(goal_pct)
        bar = max(_fin(pace_bar_usd), 0.0)
        out["size"], out["cap"] = base, base
        if not (base > 0.0):
            out["why"] = "no sized base — the funnel never sized this fire"
            return out
        if not (bar > 0.0):
            out["ok"], out["why"] = True, ("no pace bar is set — the tranche "
                                           "keeps its ordinary size")
            return out
        if not (goal > 0.0):
            out["why"] = ("the fire has no goal %, so no entry can be shown "
                          "to pay the pace bar")
            return out
        pace_min = bar / (goal / 100.0)
        out["pace_min"] = pace_min
        if pays_the_bar(base * (goal / 100.0), bar):
            out["ok"], out["why"] = True, (f"${base:,.0f} already pays the "
                                           f"${bar:,.0f} bar at +{goal:.1f}%")
            return out
        ceiling = None if ceiling_usd is None else float(ceiling_usd or 0.0)
        if ceiling is None or not (ceiling > 0.0):
            out["why"] = ("the guard named no ceiling — a tranche is never "
                          "raised blind")
            return out
        size = min(pace_min, ceiling)
        _b = 1.0 if beta is None else float(beta)
        allow_cap = None
        if allowance_usd is not None:
            _price = stress_row_loss_usd(1.0, stop_pct, _b)
            allow_cap = (max(float(allowance_usd or 0.0), 0.0) / _price
                         if _price > 0.0 else 0.0)
            size = min(size, allow_cap)
        if book_room_usd is not None:
            size = min(size, max(float(book_room_usd or 0.0), 0.0))
        size = max(0.0, size)
        out["size"], out["cap"] = size, min(pace_min, ceiling)
        if not pays_the_bar(size * (goal / 100.0), bar):
            if ceiling + 1e-9 < pace_min and (
                    allow_cap is None or ceiling <= allow_cap + 1e-9):
                out["why"] = (f"the ${bar:,.0f} pace bar needs ${pace_min:,.0f} "
                              f"at +{goal:.1f}%, but the -3% line's own ceiling "
                              f"is ${ceiling:,.0f} — the tranche may not be "
                              f"raised past it")
            elif allow_cap is not None and not pays_the_bar(
                    allow_cap * (goal / 100.0), bar) and (
                    book_room_usd is None
                    or allow_cap <= max(float(book_room_usd or 0.0), 0.0) + 1e-9):
                out["why"] = (f"the ${float(allowance_usd or 0.0):,.0f} the line "
                              f"can still lose prices this {_b:.2f}-beta fire "
                              f"at ${allow_cap:,.0f} — below the "
                              f"${pace_min:,.0f} the pace bar needs")
            else:
                out["why"] = (f"only ${max(float(book_room_usd or 0.0), 0.0):,.0f} "
                              f"of book room is left — below the "
                              f"${pace_min:,.0f} the pace bar needs")
            return out
        out["ok"], out["raised"] = True, True
        out["why"] = (f"${size:,.0f} is the smallest entry that pays the "
                      f"${bar:,.0f} bar at +{goal:.1f}%"
                      + (f", inside the ${float(allowance_usd or 0.0):,.0f} the "
                         f"line can lose" if allowance_usd is not None else "")
                      + f", never past the line's own ${ceiling:,.0f} ceiling")
        return out
    except Exception as exc:                 # never raise into a fire
        out["size"], out["ok"], out["raised"] = 0.0, False, False
        out["why"] = f"the tranche sizing could not be read ({exc})"
        return out


def weakened_size_usd(base_spend_usd: float, *, goal_pct: float,
                      pace_bar_usd: float = 0.0,
                      allowance_usd: float | None = None,
                      beta: float | None = None,
                      book_room_usd: float | None = None,
                      stop_pct: float | None = None,
                      min_frac: float | None = None,
                      max_frac: float | None = None) -> dict:
    """The size a WEAKENED fire is booked at: the smallest entry whose
    own goal pays the pace bar, capped by half the sized base, by the
    stress allowance (priced at this row's beta and real stop) and by
    the book room left. Pure and total.

    Returns {"size", "ok", "why", "pace_min", "cap"}. `ok` False means
    the weakened path has NOTHING it may honestly trade — the caller
    refuses with `why` rather than booking a minimum that cannot pay.
    """
    out = {"size": 0.0, "ok": False, "why": "", "pace_min": 0.0,
           "cap": 0.0}
    try:
        base = float(base_spend_usd or 0.0)
        goal = float(goal_pct or 0.0)
        bar = max(float(pace_bar_usd or 0.0), 0.0)
        if not (base > 0.0):
            out["why"] = "no sized base — the funnel never sized this fire"
            return out
        if not (goal > 0.0):
            out["why"] = ("the fire has no goal %, so no entry can be "
                          "shown to pay the pace bar")
            return out
        _maxf = float(WEAKENED_MAX_FRAC if max_frac is None else max_frac)
        _minf = float(WEAKENED_MIN_FRAC if min_frac is None else min_frac)
        cap = max(0.0, min(base * max(_maxf, 0.0), base))
        floor = max(0.0, base * max(_minf, 0.0))
        pace_min = (bar / (goal / 100.0)) if bar > 0.0 else 0.0
        out["cap"], out["pace_min"] = cap, pace_min
        # The bar is the FLOOR when it is set: the smallest entry that is
        # still worth doing is the one that pays, not 10% of a base that may
        # be ten times larger. With the floor dial OFF there is no worth
        # rule to size against, so the Nov 6 10% scale stands as before.
        size = min(cap, pace_min if pace_min > 0.0 else floor)
        _b = 1.0 if beta is None else float(beta)
        allow_cap = None
        if allowance_usd is not None:
            _price = stress_row_loss_usd(1.0, stop_pct, _b)
            allow_cap = (max(float(allowance_usd or 0.0), 0.0) / _price
                         if _price > 0.0 else 0.0)
            size = min(size, allow_cap)
        if book_room_usd is not None:
            size = min(size, max(float(book_room_usd or 0.0), 0.0))
        out["size"] = max(0.0, size)
        if pace_min > 0.0 and not pays_the_bar(out["size"] * (goal / 100.0), bar):
            if not pays_the_bar(cap * (goal / 100.0), bar):
                out["why"] = (f"the ${bar:,.0f} pace bar needs ${pace_min:,.0f} "
                              f"at +{goal:.1f}%, but a weakened fire may not "
                              f"exceed this fire's own ${base:,.0f} base "
                              f"(${cap:,.0f})")
            elif allow_cap is not None and not pays_the_bar(
                    allow_cap * (goal / 100.0), bar) and (
                    book_room_usd is None
                    or allow_cap <= max(float(book_room_usd or 0.0), 0.0) + 1e-9):
                out["why"] = (f"the ${float(allowance_usd or 0.0):,.0f} the line "
                              f"can still lose prices this {_b:.2f}-beta fire "
                              f"at ${allow_cap:,.0f} — below the "
                              f"${pace_min:,.0f} the pace bar needs")
            else:
                out["why"] = (f"only ${max(float(book_room_usd or 0.0), 0.0):,.0f} "
                              f"of book room is left — below the "
                              f"${pace_min:,.0f} the pace bar needs")
            return out
        if not (out["size"] > 0.0):
            out["why"] = ("no size pays the bar and fits the line (the "
                          "allowance or the book room is spent)")
            return out
        out["ok"] = True
        if pace_min <= 0.0:
            out["why"] = (f"$0 pace bar is set — the ${out['size']:,.0f} "
                          f"min-size floor stands")
        else:
            out["why"] = (f"${out['size']:,.0f} is the smallest entry that "
                          f"pays the ${bar:,.0f} pace bar at +{goal:.1f}%"
                          + (f", inside the ${float(allowance_usd or 0.0):,.0f} "
                             f"the line can lose" if allowance_usd is not None
                             else "")
                          + f", never above its own ${base:,.0f} base")
        return out
    except Exception as exc:                 # never raise into a fire
        out["size"], out["ok"] = 0.0, False
        out["why"] = f"the weakened sizing could not be read ({exc})"
        return out


def entry_size_usd(current_balance: float, book_carry_usd: float | None,
                   window_dd_pct: float,
                   start_balance: float | None = None) -> float:
    """The entry size: min(the account, the book's carry, the 3% guard)."""
    cap = max_entry_usd(current_balance, window_dd_pct, start_balance)
    if book_carry_usd is not None:
        cap = min(cap, book_carry_usd)
    return max(min(cap, float(current_balance)), 0.0)
