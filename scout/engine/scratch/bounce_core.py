"""Single source of truth for the validated 5m capitulation-bounce rule.

Fees and slippage come from ``contest_replay``.  A bounce signal is a 5m
close at or below -drop%; the entry is the next bar's open.  The exit is the
first stop, take, or time exit.  One all-in slot only.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import contest_replay as cr  # noqa: E402

FEE_PCT = cr.FEE_PCT
SLIP = cr.SLIP
CARRY_FRAC = cr.CARRY_FRAC


def exit_fill(f, entry_i: int, hold: int, stop: float,
              take: float) -> tuple[int, float, str]:
    """First fill at/after the entry bar, using tape gaps honestly."""
    last = min(entry_i + hold - 1, len(f["close"]) - 1)
    for j in range(entry_i, last + 1):
        if f["open"][j] <= stop:
            return j, float(f["open"][j]), "stop_gap"
        if f["low"][j] <= stop:
            return j, stop, "stop"
        if take and f["open"][j] >= take:
            return j, float(f["open"][j]), "take_gap"
        if take and f["high"][j] >= take:
            return j, take, "take"
    return last, float(f["close"][last]), "time"


def net_return(entry: float, raw_exit: float) -> float:
    """Round-trip return after the shared fee and exit slippage."""
    return (float(raw_exit) * (1.0 - SLIP) / entry - 1.0) - FEE_PCT


def make_events(frames: dict[str, dict], *, drop: float, hold: int,
                stop_pct: float, take_pct: float,
                min_carry: float = 0.0) -> list[dict]:
    """Signals for every asset, sorted chronologically by entry bar."""
    events = []
    n = min(len(f["close"]) for f in frames.values())
    for asset, f in frames.items():
        close = f["close"]
        for k in range(40, n - 1):
            prev = close[k - 1]
            if not prev or (close[k] / prev - 1.0) * 100.0 > -drop:
                continue
            carry = float(f["qv"][k]) * CARRY_FRAC
            if carry < min_carry:
                continue
            entry_i = k + 1
            entry = float(f["open"][entry_i]) * (1.0 + SLIP)
            stop = entry * (1.0 - stop_pct / 100.0)
            take = entry * (1.0 + take_pct / 100.0) if take_pct else 0.0
            exit_i, px, kind = exit_fill(f, entry_i, hold, stop, take)
            events.append({
                "asset": asset, "i": entry_i, "exit_i": exit_i,
                "entry_ts": f["dt"][entry_i], "carry": carry,
                "ret": net_return(entry, px), "kind": kind,
            })
    events.sort(key=lambda e: e["i"])
    return events


def simulate_one_slot(events: list[dict], *, start_balance: float,
                      death_line: float) -> tuple[list[dict], float, float]:
    """One all-in slot; skip signals while committed or under-reserved."""
    equity = float(start_balance)
    low = equity
    busy_until = -1
    taken = []
    for e in events:
        if e["i"] <= busy_until:
            continue
        # PEPE events carry None: contest_replay already enforced its gate.
        if e["carry"] is not None and e["carry"] < equity:
            continue
        pnl = equity * float(e["ret"])
        equity += pnl
        low = min(low, equity)
        busy_until = e["exit_i"]
        taken.append({**e, "pnl": pnl, "equity": equity})
        if equity <= death_line:
            break
    return taken, equity, low
