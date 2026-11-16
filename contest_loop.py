"""UNATTENDED PAPER CONTEST LOOP — one day in, one graded day out.

This is the day-level seam that was missing between the scanner/edge ledger and
the governor.  It does NOT place orders and does NOT touch the live contest
account or trade ledger.  A day is:

    ingest a day's realized paper closes (live ledger replay or a scripted tape)
      -> active-policy label
      -> hard death-line sizing
      -> paper fill + running balance
      -> GREEN/RED grade: close above the prior close
      -> persisted streak and day report

The important properties:

* `run_day(date, fills)` is IDEMPOTENT.  A date already in the state is returned
  unchanged, so restarting the process or replaying the same command cannot
  double-apply a close.  State is replaced atomically after the whole day.
* risk sizing goes through `contest_account.hard_death_entry_usd`, which caps
  the entry by the remaining buffer. At or below the death line it returns
  exactly 0. A realized close that would
  cross the line raises `DeathLineBreach` rather than being booked.
* THE SAFETY FLOOR: once the remaining death-line buffer is at/below
  `SAFETY_BUFFER_FRAC` (2.5% of the contest start, $250 on $10,000), sizing
  returns exactly 0 in `hard_death_entry_usd` itself, the day is persisted as
  HALTED, and the halt is sticky across restarts.  The loop's $0.50 minimum
  fill is the operational stop point, so a buffer within $0.50 of the floor is
  treated as halted too.  The floor is deliberately above the worst single-day
  loss observed in the final loop proof (-$209.55): one further bad day at
  the observed magnitude cannot spend the last cushion.  A day's risk budget
  is capped at the buffer ABOVE the floor, so a normal day cannot push the
  buffer through it either.
* the grade is produced by `overwatch_governor._daily_goal`, the same function
  the console uses.  GREEN means the balance is above the prior close AND the
  dollar distance to the fixed +12% line is smaller.
* every day, grade, streak, fill and balance is persisted in
  `contest_loop_state.json`; `contest_loop_report.json` is the printable ledger.

Documented command (real ledger replay over the 2026-10-12..18 stress week):

    python -X utf8 contest_loop.py --replay-ledger \
        --from-date 2026-10-12 --to-date 2026-10-18 --reset

Use a different state/report path for experiments; never point this at real
money or the live account.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import contest_account as ca
import death_floor as df
import overwatch_governor as og

BASE = Path(__file__).resolve().parent
STATE = BASE / "contest_loop_state.json"
REPORT = BASE / "contest_loop_report.json"
LEDGER = BASE / "contest_trades.jsonl"
MST = ZoneInfo("America/Denver")
SCHEMA = 1
TARGET_PCT = 12.0
DEATH_PCT = 3.0
HALT_BAND_PCT = 1.5               # no new risk inside the death-floor band
SAFETY_BUFFER_FRAC = 2.5          # percent of CONTEST START; $250 on $10k
SAFETY_EPS_USD = 0.50             # smallest fill; below floor+this = halted
DEFAULT_STOP_PCT = 2.5
MIN_FILL_USD = 0.50
# GO BIG's marks are scaled off the contest start (ca.go_big_marks): 1% stop,
# 5% take — 100/500 at the $10k tier, and the same shape at every other tier.


class DeathLineBreach(RuntimeError):
    """A fill would put the paper balance at/below the death line."""


def _now() -> str:
    return datetime.now(MST).isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    # On Windows, OneDrive/AV/indexers can hold a destination open for a few
    # milliseconds; a restart must not crash on that transient share.  Keep
    # the replace atomic and retry the same operation, never copy-in-place.
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _day(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(MST).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _dates_between(start: str, end: str) -> list[str]:
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    if b < a:
        raise ValueError("end date is before start date")
    return [(a + timedelta(days=i)).isoformat()
            for i in range((b - a).days + 1)]


def _new_state(start_balance: float, start_date: str) -> dict:
    return {
        "schema": SCHEMA,
        "created_at": _now(),
        "updated_at": _now(),
        "contest_start": start_date,
        "start_balance": round(float(start_balance), 2),
        "current_balance": round(float(start_balance), 2),
        "target_pct": TARGET_PCT,
        "death_pct": DEATH_PCT,
        "safety_buffer_frac": SAFETY_BUFFER_FRAC,
        "safety_buffer_usd": round(float(start_balance) * SAFETY_BUFFER_FRAC / 100.0, 2),
        "halted": False,
        "halted_at": None,
        "halted_reason": None,
        "days": [],
        "closes": [],
        "next_seq": 0,
    }


def _active_policy() -> str | None:
    try:
        import edge_live
        return edge_live.status().get("active")
    except Exception:
        return None


def _fill(date_s: str, seq: int, pair: str, ret_net_pct: float,
          stop_pct: float, policy: str | None, source: str) -> dict:
    return {
        "close_id": f"{date_s}:{seq}:{pair}",
        "date": date_s,
        "seq": seq,
        "pair": pair,
        "ret_net_pct": round(float(ret_net_pct), 6),
        "stop_pct": round(max(float(stop_pct or 0.0), 0.5), 6),
        "policy": policy or "unattributed",
        "source": source,
    }


def tape_from_ledger(path: Path | None = None, start: str | None = None,
                     end: str | None = None) -> list[dict]:
    """Turn applied realized closes into a replayable day tape.

    The return percentage is `pnl_net / size`, so the loop can re-size the same
    signal at the CURRENT paper balance instead of copying the old dollar P&L.
    A negative close's own adverse move is used as its worst-case excursion;
    this is a replay input, not an order instruction.
    """
    p = Path(path or LEDGER)
    by_day: dict[str, list[tuple[str, float, float, str]]] = {}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not r.get("applied"):
            continue
        d = _day(r.get("sold_at"))
        if start and d < start:
            continue
        if end and d > end:
            continue
        try:
            size = float(r.get("size") or 0.0)
            pnl = float(r.get("pnl_net") or 0.0)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        ret = pnl / size * 100.0
        stop = max(DEFAULT_STOP_PCT, -ret if ret < 0 else 0.0)
        by_day.setdefault(d, []).append(
            (str(r.get("sold_at") or d), float(r.get("entry") or 0.0),
             ret, str(r.get("pair") or "?"), stop))
    out = []
    for d in sorted(by_day):
        fills = []
        for i, (_ts, _entry, ret, pair, stop) in enumerate(
                sorted(by_day[d], key=lambda x: x[0])):
            fills.append(_fill(d, i, pair, ret, stop, _active_policy(),
                               "contest_trades.jsonl"))
        out.append({"date": d, "fills": fills})
    return out


def scripted_tape(start: str = "2026-11-01", days: int = 14,
                  seed: int = 7) -> list[dict]:
    """Deterministic adversarial fallback used by tests and offline smoke runs.

    A mixture of wins, losses and a near-halt sequence.  It is deliberately not
    tuned to make the edge look good; it exists to exercise the risk path.
    """
    import random
    rng = random.Random(seed)
    begin = date.fromisoformat(start)
    out = []
    for d in range(days):
        day = (begin + timedelta(days=d)).isoformat()
        fills = []
        # Two-thirds of days get four closes; every fourth day is quiet.
        n = 0 if d % 7 == 6 else (4 if d % 3 else 2)
        for i in range(n):
            win = rng.random() < 0.55
            ret = rng.uniform(0.8, 2.8) if win else -rng.uniform(0.8, 2.6)
            stop = max(DEFAULT_STOP_PCT, -ret if ret < 0 else 0.0)
            fills.append(_fill(day, i, f"SIM{i % 4}/USD", ret, stop,
                               "scripted", "scripted_tape"))
        out.append({"date": day, "fills": fills})
    return out


def _prior_rows(days: list[dict]) -> list[dict]:
    return [{"date": d["date"], "n": int(d.get("fills") or 0),
             "net": float(d.get("net") or 0.0),
             "green": bool(d.get("green")),
             "close_balance": d.get("balance")} for d in days]


def _account(state: dict) -> dict:
    start = float(state["start_balance"])
    return {
        "tier": int(round(start)),
        "start_balance": start,
        "current_balance": float(state["current_balance"]),
        "contest_id": f"{state['contest_start']}T00:00:00-07:00",
    }


def _grade_day(state: dict, day: dict, prior_days: list[dict]) -> dict:
    """Call the governor's own daily rule — never a second implementation."""
    start = float(state["start_balance"])
    bal = float(day["balance"])
    target = start * (1.0 + TARGET_PCT / 100.0)
    remaining = max(0.0, target - bal)
    day_index = len(prior_days)
    days_left = max(0.0, 7.0 - day_index)
    rec = {
        "prior_days": prior_days,
        "prior_net": round(sum(float(d["net"]) for d in prior_days), 2),
    }
    goal = og._daily_goal(
        _account({**state, "current_balance": bal}),
        remaining, days_left, day["net"],
        today_n=len(day.get("closes") or []), reconciliation=rec)
    return goal


def _go_big_exit_kind(fill: dict, pnl: float, stop_usd: float,
                      take_usd: float = 0.0) -> str:
    """Classify the GO BIG close from the replay's own exit cause.

    A real-tape stop often lands at -$29.999... before the dollar field is
    rounded to -$50.00.  Comparing only the rounded number let a stop wear the
    momentum label, which made the report claim a momentum exit that never
    happened.  A replay that knows its cause carries `replay_exit_kind`; the
    arithmetic fallback keeps the synthetic tape honest and uses half a cent
    of tolerance for a stop that filled at its exact net line.
    """
    cause = str(fill.get("replay_exit_kind") or "").strip().lower()
    if cause in ("stop", "stop_gap"):
        return "stop"
    if cause == "momentum":
        return "momentum"
    if cause == "take":
        return "take"
    if cause == "eod":
        return "eod"
    if take_usd > 0.0 and pnl >= float(take_usd) - 0.005:
        return "take"
    return "stop" if pnl <= -float(stop_usd) + 0.005 else "momentum"


class ContestLoop:
    """One unattended paper day at a time, persisted after every close-out."""

    def __init__(self, state_path: Path | None = None,
                 report_path: Path | None = None):
        self.state_path = Path(state_path or STATE)
        self.report_path = Path(report_path or REPORT)
        self.state = _read_json(self.state_path, {})

    def reset(self, start_balance: float = 10_000.0,
              start_date: str | None = None) -> dict:
        self.state = _new_state(start_balance, start_date or _now()[:10])
        _write_json(self.state_path, self.state)
        return self.state

    def _load(self) -> dict:
        self.state = _read_json(self.state_path, {})
        return self.state

    def _persist(self) -> None:
        self.state["updated_at"] = _now()
        _write_json(self.state_path, self.state)

    def run_day(self, day: dict) -> dict:
        """Run one date exactly once; a repeat returns the saved day."""
        date_s = _day(day.get("date"))
        self._load()
        if not self.state:
            self.reset(start_date=date_s)
        for saved in self.state.get("days") or []:
            if saved.get("date") == date_s:
                return saved
        fills = day.get("fills") or []
        state = self.state
        start = float(state["start_balance"])
        death = start * (1.0 - DEATH_PCT / 100.0)
        halt = df.halt_line(start, HALT_BAND_PCT)
        safety = start * SAFETY_BUFFER_FRAC / 100.0
        day_start = float(state["current_balance"])
        buffer = max(0.0, day_start - death)
        if buffer <= safety + SAFETY_EPS_USD and not state.get("halted"):
            state["halted"] = True
            state["halted_at"] = date_s
            state["halted_reason"] = (
                f"buffer ${buffer:,.2f} is at/below the ${safety:,.2f} "
                f"safety floor ({SAFETY_BUFFER_FRAC:g}% of start) plus the "
                f"${SAFETY_EPS_USD:,.2f} minimum fill")
            self._persist()
        day_halted = bool(state.get("halted"))
        closed = []
        skipped = []

        for f in fills:
            bal = float(state["current_balance"])
            if bal <= death:
                raise DeathLineBreach(
                    f"{date_s}: balance ${bal:,.2f} is at the death line")
            stop = max(float(f.get("stop_pct") or DEFAULT_STOP_PCT),
                       DEFAULT_STOP_PCT)
            go_big = bool(f.get("go_big"))
            # THE COST GATE (Nov 16): a measured round trip above the edge is
            # a NAMED refusal, decided before any sizing runs. Left to the
            # sizing formula it would have surfaced as "death cap" or a
            # sub-$1 spend — true statements that hide the actual reason, so
            # the pair looked blocked by the buffer instead of by its own cost.
            import spread_table as _st_cg
            _cost_gate = _st_cg.cost_gate(f.get("pair"))
            if _cost_gate["verdict"] == "refuse":
                skipped.append({"pair": f.get("pair"),
                                "why": f"cost gate: {_cost_gate['why']}"})
                continue
            _gb_stop, _gb_take = ca.go_big_marks(start)
            gb = {"spend_usd": 0.0, "stop_usd": _gb_stop,
                  "take_usd": _gb_take}
            if go_big:
                # GO BIG OR GO HOME: the tape row is a freshly called entry.
                # A row may mark `go_big_supported=False` to prove the refusal
                # path; otherwise coin/rotation support is the caller's call
                # (the live scanner and QC make it from tide + book carry).
                gb = ca.go_big_plan(
                    bal, start,
                    supported=bool(f.get("go_big_supported", True)),
                    stopped=bool(state.get("halted")),
                    stop_usd=float(f.get("stop_usd") or _gb_stop),
                    take_usd=float(f.get("take_usd") or _gb_take),
                    min_buffer_usd=safety)
                spend = float(gb["spend_usd"])
            else:
                spend = ca.hard_death_entry_usd(
                    bal, start, stop, size_frac=0.09,
                    pair=f.get("pair"),
                    window_dd_pct=max(stop, ca.DEFAULT_WINDOW_DD_PCT),
                    min_buffer_usd=safety,
                    halted=bool(state.get("halted")))
            if spend < MIN_FILL_USD:
                _why = "death cap"
                if _cost_gate["verdict"] == "thin":
                    _why = f"death cap (cost gate thin: {_cost_gate['why']})"
                skipped.append({"pair": f.get("pair"), "why": _why})
                continue
            if go_big:
                worst = float(gb["stop_usd"])
            else:
                import spread_table as _st_sp
                loss_pct = min(100.0, max(stop, 0.5) + _st_sp.roundtrip_pct(f.get("pair")))
                worst = spend * loss_pct / 100.0
            ret = float(f.get("ret_net_pct") or 0.0)
            pnl = spend * ret / 100.0
            if go_big:
                # The fixed +$20 take truncates a winner; a hard -$50 stop
                # truncates a loser. A replay's own exit kind still wins.
                take = float(gb["take_usd"])
                if take > 0.0:
                    pnl = min(pnl, take)
                pnl = max(pnl, -float(gb["stop_usd"]))
            after = bal + pnl
            if after <= death + 1e-9:
                raise DeathLineBreach(
                    f"{date_s}: {f.get('pair')} fill would close at "
                    f"${after:,.2f}, at/below ${death:,.2f}")
            seq = int(state.get("next_seq") or 0)
            close = {
                **f, "spend": round(spend, 2), "worst_loss": round(worst, 2),
                "pnl_net": round(pnl, 2), "balance_after": round(after, 2),
                "close_id": f"{date_s}:{seq}:{f.get('pair')}",
                "go_big": go_big,
                **({"take_usd": float(gb["take_usd"]),
                    "stop_usd": float(gb["stop_usd"]),
                    "exit_kind": _go_big_exit_kind(
                        f, pnl, float(gb["stop_usd"]),
                        float(gb["take_usd"])),
                    "go_big_why": str(gb.get("why") or "")} if go_big else {}),
            }
            state["next_seq"] = seq + 1
            state["current_balance"] = round(after, 6)
            state.setdefault("closes", []).append(close)
            closed.append(close)

        state["current_balance"] = round(float(state["current_balance"]), 2)
        net = round(float(state["current_balance"]) - day_start, 2)
        saved = {
            "date": date_s,
            "start_balance": round(day_start, 2),
            "balance": round(float(state["current_balance"]), 2),
            "net": net,
            "fills_tape": len(fills),
            "fills": len(closed),
            "closed": len(closed),
            "closes": closed,
            "skipped": len(skipped),
            "risk_skips": skipped,
            "halted": day_halted,
            "halt_reason": state.get("halted_reason"),
            "safety_buffer_usd": round(safety, 2),
        }
        found = [d for d in state.get("days") or [] if d.get("date") < date_s]
        goal = _grade_day(state, saved, _prior_rows(found))
        # The user's pace rule: today is on track when the close is above
        # yesterday's close.  Target-pace arithmetic remains diagnostic only.
        goal = dict(goal)
        goal["green"] = bool(saved["balance"] > day_start)
        goal["pace_on_track"] = goal["green"]
        goal["status"] = "GREEN" if goal["green"] else "RED"
        goal["note"] = ("close above prior close" if goal["green"]
                        else "close at/below prior close")
        room_after = saved["balance"] - death
        if room_after <= safety + SAFETY_EPS_USD and not state.get("halted"):
            state["halted"] = True
            state["halted_at"] = date_s
            state["halted_reason"] = (
                f"buffer ${room_after:,.2f} reached the ${safety:,.2f} "
                f"safety floor ({SAFETY_BUFFER_FRAC:g}% of start) plus the "
                f"${SAFETY_EPS_USD:,.2f} minimum fill")
        saved.update({
            "prior_close_balance": goal.get("prior_close_balance"),
            "balance_delta_usd": goal.get("balance_delta_usd"),
            "distance_usd": goal.get("remaining_usd"),
            "distance_delta_usd": goal.get("distance_delta_usd"),
            "green": goal.get("green"),
            "status": "HALTED" if day_halted else goal.get("status"),
            "governor_status": goal.get("status"),
            "streak_days": int(goal.get("streak_days") or 0),
            "target_balance": goal.get("target_balance"),
            "death_line": round(death, 2),
            "death_margin_usd": round(saved["balance"] - death, 2),
            "halt_line": round(halt, 2),
            "status_note": goal.get("note"),
        })
        state.setdefault("days", []).append(saved)
        self._persist()
        return saved

    def run(self, tape: list[dict]) -> list[dict]:
        """Run a chronological tape; already-saved dates are never re-applied."""
        out = []
        for day in sorted(tape, key=lambda d: _day(d.get("date"))):
            out.append(self.run_day(day))
        return out

    def report(self) -> dict:
        state = self._load()
        days = sorted(state.get("days") or [], key=lambda d: d["date"])
        start = float(state.get("start_balance") or 0.0)
        target = start * (1.0 + TARGET_PCT / 100.0)
        death = start * (1.0 - DEATH_PCT / 100.0)
        mismatches = []
        streak = 0
        max_streak = 0
        for d in days:
            prior = d.get("prior_close_balance")
            if prior is not None and int(d.get("fills") or 0) > 0:
                expected = (float(d["balance"]) > float(prior)
                            and float(d["distance_usd"])
                            < float(d["target_balance"]) - float(prior))
                if bool(d.get("green")) != bool(expected):
                    mismatches.append({"date": d["date"],
                                       "expected": bool(expected),
                                       "got": d.get("green")})
            if d.get("green"):
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        breaches = [d["date"] for d in days
                    if float(d.get("balance") or 0.0) <= death + 1e-9]
        worst = min((float(d.get("death_margin_usd") or 0.0)
                     for d in days), default=None)
        halted_days = [d for d in days if d.get("halted")]
        halted_fills = sum(int(d.get("fills") or 0) for d in halted_days)
        gb_closes = [c for d in days for c in (d.get("closes") or [])
                     if c.get("go_big")]
        gb_takes = [c for c in gb_closes
                    if float(c.get("pnl_net") or 0.0) > 0.0]
        gb_stops = [c for c in gb_closes
                    if float(c.get("pnl_net") or 0.0)
                    <= -ca.go_big_marks(start)[0] + 1e-9]
        gb_momentum = [c for c in gb_closes
                       if c.get("exit_kind") == "momentum"]
        safety = start * SAFETY_BUFFER_FRAC / 100.0
        final = float(state.get("current_balance") or start)
        out = {
            "schema": SCHEMA,
            "generated_at": _now(),
            "contest_start": state.get("contest_start"),
            "start_balance": round(start, 2),
            "current_balance": round(final, 2),
            "target_balance": round(target, 2),
            "death_line": round(death, 2),
            "safety_buffer_frac": SAFETY_BUFFER_FRAC,
            "safety_buffer_usd": round(safety, 2),
            "final_distance_usd": round(max(0.0, target - final), 2),
            "green_days": sum(1 for d in days if d.get("green")),
            "red_days": sum(1 for d in days if d.get("status") == "RED"),
            "no_close_days": sum(1 for d in days
                                 if d.get("status") == "NO CLOSES TODAY"),
            "max_streak": max_streak,
            "current_streak": int(days[-1].get("streak_days") or 0)
            if days else 0,
            "worst_death_margin_usd": round(worst, 2) if worst is not None else None,
            "halted": bool(state.get("halted")),
            "halted_at": state.get("halted_at"),
            "halted_reason": state.get("halted_reason"),
            "halted_days": len(halted_days),
            "halted_fills": halted_fills,
            "breaches": breaches,
            "grade_mismatches": mismatches,
            "fills": sum(int(d.get("fills") or 0) for d in days),
            "risk_skips": sum(int(d.get("skipped") or 0) for d in days),
            "go_big_fills": len(gb_closes),
            "go_big_takes": len(gb_takes),
            "go_big_stops": len(gb_stops),
            "go_big_momentum_exits": len(gb_momentum),
            "go_big_best_win_usd": round(max((float(c.get("pnl_net") or 0.0)
                                               for c in gb_takes), default=0.0), 2),
            "go_big_pnl_net": round(sum(float(c.get("pnl_net") or 0.0)
                                        for c in gb_closes), 2),
            "days": days,
        }
        out["ok"] = (not breaches and not mismatches
                     and halted_fills == 0
                     and out["green_days"] is not None)
        _write_json(self.report_path, out)
        return out


def _print_ledger(rep: dict) -> None:
    print("DATE        BALANCE      DIST_TO_12%  BAL_DELTA   DIST_DELTA  "
          "GRADE              STREAK  DEATH_MARGIN  FILLS")
    for d in rep["days"]:
        print(f"{d['date']}  ${d['balance']:>10,.2f}  "
              f"${d['distance_usd']:>10,.2f}  "
              f"${(d.get('balance_delta_usd') or 0):>+9,.2f}  "
              f"${(d.get('distance_delta_usd') or 0):>+9,.2f}  "
              f"{str(d.get('status')):<18} {int(d.get('streak_days') or 0):>6}  "
              f"${d['death_margin_usd']:>10,.2f}  {int(d.get('fills') or 0):>5}")
    print("-" * 112)
    print(f"SUMMARY green_days={rep['green_days']} red_days={rep['red_days']} "
          f"max_streak={rep['max_streak']} current_streak={rep['current_streak']} "
          f"breaches={len(rep['breaches'])} "
          f"worst_death_margin=${rep['worst_death_margin_usd']:,.2f} "
          f"halted_days={rep['halted_days']} halted_fills={rep['halted_fills']} "
          f"safety_floor=${rep['safety_buffer_usd']:,.2f} "
          f"final_balance=${rep['current_balance']:,.2f} "
          f"final_distance=${rep['final_distance_usd']:,.2f} "
          f"go_big_fills={rep['go_big_fills']} "
          f"wins={rep['go_big_takes']} stops={rep['go_big_stops']} "
          f"momentum_exits={rep['go_big_momentum_exits']} "
          f"best_win=${rep['go_big_best_win_usd']:,.2f} "
          f"go_big_pnl=${rep['go_big_pnl_net']:,.2f} "
          f"grade_mismatches={len(rep['grade_mismatches'])}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=("ledger", "scripted"),
                    default="ledger")
    ap.add_argument("--replay-ledger", action="store_true",
                    help="read applied closes from contest_trades.jsonl")
    ap.add_argument("--from-date")
    ap.add_argument("--to-date")
    ap.add_argument("--days", type=int, default=7,
                    help="number of trailing ledger dates when no range is given")
    ap.add_argument("--start-balance", type=float, default=10_000.0)
    ap.add_argument("--state", default=str(STATE))
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--go-big", action="store_true",
                    help="run every tape fill as a fresh GO BIG call "
                         "(100%% notional, +$20 take, $50 net stop)")
    args = ap.parse_args(argv)
    source = "ledger" if args.replay_ledger else args.source

    if source == "ledger":
        tape = tape_from_ledger(start=args.from_date, end=args.to_date)
        if not args.from_date and not args.to_date:
            dates = sorted({d["date"] for d in tape})
            keep = set(dates[-max(1, args.days):])
            tape = [d for d in tape if d["date"] in keep]
    else:
        tape = scripted_tape(start=args.from_date or "2026-11-01",
                             days=args.days)
    if not tape:
        print("NO TAPE: no applied closes in that range")
        return 2
    if args.go_big:
        for _day_row in tape:
            for _fill_row in _day_row.get("fills") or []:
                _fill_row["go_big"] = True
                _fill_row["go_big_supported"] = True
    start_date = args.from_date or tape[0]["date"]
    loop = ContestLoop(args.state, args.report)
    if args.reset:
        loop.reset(args.start_balance, start_date)
    loop.run(tape)
    rep = loop.report()

    # PROOF OF RESTART: a fresh process object re-reads the state and runs the
    # same tape.  Nothing may be re-applied.
    before = (len(loop.state.get("closes") or []), rep["current_balance"],
              len(rep["days"]))
    restarted = ContestLoop(args.state, args.report)
    restarted.run(tape)
    after = (len(restarted.state.get("closes") or []), rep["current_balance"],
             len(restarted.state.get("days") or []))
    rep["restart_ok"] = before == after
    # A halted state must also survive a restart with NEW risk offered.  Use a
    # throwaway copy so the proof cannot append a canary day to the real ledger.
    canary_ok = True
    canary_fills = None
    if rep.get("halted"):
        probe = Path(str(args.state) + ".restart_probe.json")
        shutil.copy2(args.state, probe)
        try:
            probe_loop = ContestLoop(probe, args.report)
            canary_date = (date.fromisoformat(rep["days"][-1]["date"])
                           + timedelta(days=1)).isoformat()
            canary = probe_loop.run_day({"date": canary_date, "fills": [
                _fill(canary_date, 0, "RESTART/USD", 2.0, 2.5,
                      "restart-canary", "restart-canary")]})
            canary_fills = int(canary.get("fills") or 0)
            canary_ok = bool(canary.get("halted") and canary_fills == 0)
        finally:
            try:
                probe.unlink()
            except OSError:
                pass
    rep["restart_halt_ok"] = canary_ok
    rep["restart_canary_fills"] = canary_fills
    rep["ok"] = bool(rep["ok"] and canary_ok)
    _write_json(loop.report_path, rep)
    _print_ledger(rep)
    print(f"RESTART idempotent={rep['restart_ok']} "
          f"halted_canary={rep['restart_halt_ok']} "
          f"canary_fills={rep['restart_canary_fills']} closes={before[0]}")
    print(f"REPORT {loop.report_path}")
    return 0 if rep["ok"] and rep["restart_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
