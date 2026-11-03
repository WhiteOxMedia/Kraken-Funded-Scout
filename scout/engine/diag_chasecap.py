"""diag_chasecap.py — THE CHASE-CAP STUDY (Oct 3).

WHY THIS EXISTS: CHASE_WATCH_MAX_PER_PASS (2) is a frequency governor,
not a quality gate — its whole cost is delay. It never blocks a pair
from speaking (a deferred pair re-enters the next pass's queue and
cannot be deferred twice in a row), so "the cap deferred a turn that
then ran" reduces to a pure delay question: did the 1-pass deferral
push the she-decides point past a window that then paid?

WHAT IT PRICES — only [watchlist]-provenance turns. The funnel's own
budgeted 2/pass never defers the live mover's line (a [watchlist]
deferral frees the slot), and funnel-turn quality is diag_chaseback's
job. The deferral's cost decomposes into three non-overlapping parts
(the chaseback convention, full chase position, ran = +2% MFE):

  A. RAN MISSED      — the zero-delay world's 4-bar window touched
                       +2% MFE from its entry but the delayed world's
                       did not: position x 2%.
  B. RAN ENTRY SHAVE — both windows ran, but the delayed entry paid a
                       worse close: position x 2% x (1 - close1/close0).
  C. HOLD DELTA      — neither ran: the shifted 4-bar hold window's
                       end-move difference.

The zero-delay world enters at the store close 2 hourly bars before the
spoken turn (1 watch pass = the 10-min rescan pacing = 2 hourly bars);
the actual world enters at the spoken turn's close. Both windows are
the 4 bars strictly after entry.

THE VERDICT is report-only and gated on GRADED TURNS ALONE (the Sep 18
revisit): fewer than MATURITY_TURNS graded watchlist turns speaks
"keep - immature data", no matter how long the window or how chatty
the speak frequency — frequency alone can never justify a change,
because a turn that never graded (horizon closed, priced on the store)
carries zero evidence about whether the cap's delay costs anything.
Window age and turns/day are descriptive context, never a gate.
Beyond the gate: deferral events with net-positive cost argue a raise,
deferral events that cost nothing argue keep, and a cap that never
bound argues keep. The budget-spent lines are the binding evidence;
the turn ledger's GRADED rows are the maturity evidence.

NOTE ON DEFERRAL RECORDS: budget-spent lines live only in the console
log (truncated per boot) - they are NOT a durable ledger. Until a
durable deferral journal exists, the study reads whatever log lines
survive and reports unmatched events as unpriced. This file is the
machinery; the journal is the next seam if deferrals prove costly.
"""
from __future__ import annotations

import glob
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent

logger = logging.getLogger("chasecap")

POSITION_USD = 1055.0
POSITION_USD_FALLBACK = 1055.0
RAN_PCT = 2.0          # the +2% MFE popup contract
HORIZON_BARS = 4       # the 4-bar forward window
DELTA_BARS = 2         # 1 watch pass = the 10-min rescan pacing
CAPTURE_DAYS = 14.0    # the ~2-week speak-frequency window (descriptive)
# THE EVIDENCE LADDER (Sep 19): the bar lives in evidence_ladder.py.
from evidence_ladder import TURN_BARS as _EL_TURN_BARS
MATURITY_TURNS = _EL_TURN_BARS["CAP_MATURITY"]    # min GRADED watchlist turns before a verdict —
                       # the ONLY maturity gate (frequency is never one)
MAX_PER_PASS = 2       # CHASE_WATCH_MAX_PER_PASS, mirrored for reports
REF_TOL = 0.005        # deferral-line -> turn match: ref close within 0.5%
PASSES_PER_DAY = 144.0  # the ~10-min main-loop pass cadence

TURNS_FILE = ROOT / "chase_turns.jsonl"
DEFERRALS_JOURNAL = ROOT / "chase_deferrals.jsonl"
# the fleet convention (diag_chaseback): 1h feather stores per base
STORES_DIR = ROOT / "surething_data" / "kraken"
STUDY_OUT = ROOT / "_chasecap_check.txt"
STUDY_LEDGER = ROOT / "_chasecap_monthly.jsonl"
LOG_GLOBS = (str(ROOT / "logs" / "*.log"), str(ROOT / "logs" / "*.txt"))


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _position_usd() -> float:
    """Resolve the live contest size, fail-closed to the study fallback."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import contest_account as ca
        size = getattr(ca, "CONTEST_SIZE", None)
        size = size() if callable(size) else size
        if size and float(size) > 0:
            return float(size)
    except Exception:
        pass
    return POSITION_USD_FALLBACK


def _epoch_s_of(ts_iso: str) -> float:
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(str(ts_iso))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _load_store(path: Path):
    """One feather store -> (closes np array, UTC DatetimeIndex).

    The diag_chaseback loader convention: the 'date' column is UTC,
    sorted, de-duplicated by sort order; closes are plain floats."""
    import numpy as _np
    import pandas as _pd
    df = _pd.read_feather(path)
    df["date"] = _pd.to_datetime(df["date"], utc=True)
    df = df.sort_values("date").reset_index(drop=True)
    return (_np.asarray(df["close"], dtype=float),
            _pd.DatetimeIndex(df["date"]))


def _store_epochs(idx):
    """A UTC DatetimeIndex -> epoch SECONDS via the datetime64[s] cast
    (the house chain — the naive astype('int64') is unit-ambiguous
    across pandas versions; this environment returned us-scale ints)."""
    import numpy as np
    ep = np.asarray(idx.tz_convert(None).to_numpy()
                    .astype("datetime64[s]").astype("int64"))
    return ep, np


def _bar_at_or_before(ep, np, t0: float) -> int:
    """Index of the last store bar with epoch <= t0 (-1 if none)."""
    return int(np.searchsorted(ep, t0, side="right")) - 1


def _entry_and_window(closes, ep, np, t0: float, shift: int = 0):
    """(entry_close, fwd_closes, entry_epoch) for the bar t0 shifted
    `shift` bars early; None when the store cannot price it (too short
    or the 4-bar horizon is not yet closed)."""
    w = _bar_at_or_before(ep, np, t0) - shift
    if w < 0:
        return None
    if w + HORIZON_BARS >= len(closes):
        return None  # horizon not yet closed: the turn is pending
    fwd = closes[w + 1:w + 1 + HORIZON_BARS]
    return (float(closes[w]), fwd, float(ep[w]))


def _price_side(close: float, fwd) -> dict:
    """The chaseback convention on one entry: ran + end-move."""
    ran = bool((fwd.max() / close - 1.0) >= RAN_PCT / 100.0)
    end = (float(fwd[-1]) / close - 1.0) * 100.0
    return {"close": close, "ran": ran, "end_pct": end}


def _deferral_cost(zero: dict, actual: dict, position: float) -> dict:
    """The three non-overlapping components, A/B/C partitioned."""
    if zero["ran"] and not actual["ran"]:
        kind = "ran_missed"
        usd = position * RAN_PCT / 100.0
    elif zero["ran"] and actual["ran"]:
        kind = "entry_shave"
        usd = (position * RAN_PCT / 100.0
               * max(0.0, 1.0 - actual["close"] / zero["close"]))
    else:
        kind = "hold_delta"
        usd = position * (actual["end_pct"] - zero["end_pct"]) / 100.0
    return {"kind": kind, "usd": round(usd, 2),
            "zero": {"close": zero["close"], "ran": zero["ran"],
                     "end_pct": round(zero["end_pct"], 2)},
            "actual": {"close": actual["close"], "ran": actual["ran"],
                       "end_pct": round(actual["end_pct"], 2)}}


def read_turns(path: Path = TURNS_FILE, window_days: float = CAPTURE_DAYS
               ) -> list:
    """Watchlist-provenance turns inside the capture window."""
    import time as _t
    cutoff = _t.time() - window_days * 86400.0
    rows = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if "[watchlist]" not in str(d.get("why", "")):
            continue
        try:
            ts = _epoch_s_of(d["ts"])
        except Exception:
            continue
        if ts < cutoff:
            continue
        d["_ts"] = ts
        rows.append(d)
    rows.sort(key=lambda r: r["_ts"])
    return rows


def read_deferrals(journal: Path = DEFERRALS_JOURNAL,
                   log_globs=LOG_GLOBS) -> dict:
    """Deferral events: the DURABLE journal first (chase_deferrals.jsonl,
    written by the scanner's defer site since Sep 18), then any surviving
    budget-spent console-log lines (the log truncates per boot - the
    journal exists because that made pre-journal deferrals unanswerable).

    Returns {"events": [{pair, ref_close, raw}], "journal_rows": n,
    "log_lines": n, "unparsed": n} with events de-duplicated across
    both sources."""
    events, unparsed = [], 0
    seen = set()

    def _add(pair, ref_close, raw):
        pair = str(pair)
        if not pair:
            return False
        key = (pair, None if ref_close is None else round(float(ref_close), 8))
        if key in seen:
            return False
        seen.add(key)
        events.append({"pair": pair,
                       "ref_close": (float(ref_close)
                                     if ref_close is not None else None),
                       "raw": str(raw)[:220]})
        return True

    journal_rows = 0
    try:
        for line in journal.read_text(
                encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                unparsed += 1
                continue
            if _add(d.get("pair"), d.get("ref_close"), line):
                journal_rows += 1
    except OSError:
        pass

    log_lines = 0
    for pattern in log_globs:
        for logp in sorted(glob.glob(pattern)):
            try:
                text = Path(logp).read_text(encoding="utf-8",
                                            errors="replace")
            except OSError:
                continue
            for ln in text.splitlines():
                if "budget spent" not in ln.lower():
                    continue
                log_lines += 1
                mp = re.search(r"\b([A-Z0-9]{2,10}/USD)\b", ln)
                mr = re.search(r"\bref\s+([0-9]*\.?[0-9]+)", ln)
                if not mp:
                    unparsed += 1
                    continue
                _add(mp.group(1),
                     float(mr.group(1)) if mr else None, ln.strip())
    return {"events": events, "journal_rows": journal_rows,
            "log_lines": log_lines, "unparsed": unparsed}


def match_deferral(ev: dict, turns: list):
    """Pair + ref-close match to a spoken turn (within REF_TOL)."""
    if ev.get("ref_close") is None:
        return None
    for t in turns:
        if t.get("pair") != ev.get("pair"):
            continue
        try:
            ref = float(t.get("ref_close"))
        except (TypeError, ValueError):
            continue
        if ref > 0 and abs(ev["ref_close"] - ref) / ref <= REF_TOL:
            return t
    return None


def _store_path(stores_dir: Path, pair: str) -> Path:
    base = str(pair).split("/")[0]
    return Path(stores_dir) / f"{base}_USD-1h.feather"


def price_turn(store, turn: dict, position: float) -> dict:
    """Price one spoken turn in both worlds (zero-delay vs actual)."""
    closes, idx = store
    ep, np = _store_epochs(idx)
    t0 = turn["_ts"]
    actual = _entry_and_window(closes, ep, np, t0, shift=0)
    zero = _entry_and_window(closes, ep, np, t0, shift=DELTA_BARS)
    if actual is None:
        return {"state": "pending"}
    if zero is None:
        return {"state": "skipped"}  # store too short before the turn
    a = _price_side(*actual[:2])
    z = _price_side(*zero[:2])
    return {"state": "graded",
            "actual": a, "zero": z,
            "deferral": _deferral_cost(z, a, position)}


def run_study(max_pairs=None, stores_dir: Path = STORES_DIR,
              turns_path: Path = TURNS_FILE,
              position_usd=None, now=None,
              trigger: str = "scheduled") -> dict:
    """The full pass: turns -> graded worlds -> deferral matching ->
    frequency stats -> the report-only cap verdict."""
    import time as _t
    pos = float(position_usd) if position_usd is not None else _position_usd()
    turns = read_turns(turns_path)
    now = now if now is not None else _t.time()
    window_days = min(CAPTURE_DAYS,
                      max(0.25, (now - turns[0]["_ts"]) / 86400.0)
                      if turns else CAPTURE_DAYS)

    graded, pending, skipped = [], 0, 0
    by_pair = {}
    for t in turns:
        sp = _store_path(stores_dir, t.get("pair", ""))
        if not sp.exists():
            skipped += 1
            continue
        try:
            store = _load_store(sp)
        except Exception:
            skipped += 1
            continue
        pr = price_turn(store, t, pos)
        if pr["state"] == "graded":
            pr.update({"pair": t.get("pair"), "ts": t.get("ts"),
                       "ref_close": t.get("ref_close")})
            graded.append(pr)
            bp = by_pair.setdefault(t.get("pair"),
                                    {"graded": 0, "ran_actual": 0,
                                     "usd_actual": 0.0, "usd_zero": 0.0})
            bp["graded"] += 1
            bp["ran_actual"] += int(pr["actual"]["ran"])
            bp["usd_actual"] += round(pos * pr["actual"]["end_pct"] / 100.0, 2)
            bp["usd_zero"] += round(pos * pr["zero"]["end_pct"] / 100.0, 2)
        elif pr["state"] == "pending":
            pending += 1
        else:
            skipped += 1

    defl = read_deferrals()
    matched, unmatched = [], 0
    for ev in defl["events"]:
        t = match_deferral(ev, turns)
        if t is None:
            unmatched += 1
            continue
        sp = _store_path(stores_dir, t.get("pair", ""))
        try:
            store = _load_store(sp)
            pr = price_turn(store, t, pos)
        except Exception:
            pr = {"state": "skipped"}
        matched.append({"pair": ev["pair"], "turn_ts": t.get("ts"),
                        "turn_state": pr.get("state"),
                        "deferral": pr.get("deferral"),
                        "then_ran": bool(pr.get("actual", {}).get("ran"))
                        if pr.get("state") == "graded" else None})

    cost_by_kind = {"ran_missed": 0.0, "entry_shave": 0.0, "hold_delta": 0.0}
    for m in matched:
        d = m.get("deferral")
        if d:
            cost_by_kind[d["kind"]] = round(
                cost_by_kind[d["kind"]] + d["usd"], 2)
    net_cost = round(sum(cost_by_kind.values()), 2)
    then_ran = [m for m in matched if m["then_ran"]]

    tdays = window_days
    per_day = len(turns) / tdays if tdays > 0 else 0.0
    rec = cap_verdict(per_day=per_day, tdays=tdays, n_graded=len(graded),
                      n_events=defl["journal_rows"] + defl["log_lines"],
                      net_cost=net_cost)
    return {
        "ts": _utcnow_iso(), "position_usd": pos, "trigger": trigger,
        "window_days": round(tdays, 2),
        "turns": {"watchlist": len(turns), "graded": len(graded),
                  "pending": pending, "skipped": skipped},
        "frequency": {"turns_per_day": round(per_day, 2),
                      "demand_per_pass": round(per_day / PASSES_PER_DAY, 4),
                      "max_per_pass": MAX_PER_PASS},
        "graded": graded, "by_pair": by_pair,
        "deferrals": {"journal_rows": defl["journal_rows"],
                      "log_lines": defl["log_lines"],
                      "events": len(defl["events"]),
                      "unparsed": defl["unparsed"], "matched": len(matched),
                      "unmatched": unmatched,
                      "cost_by_kind": cost_by_kind, "net_cost": net_cost,
                      "then_ran": then_ran},
        "recommendation": rec}


def cap_verdict(per_day: float, tdays: float, n_graded: int,
                n_events: int, net_cost: float) -> dict:
    """THE CAP VERDICT - report-only, never auto-applied.

    The cap is a burst governor: with ~144 passes/day it binds only when
    several pairs turn in the same pass, so budget-spent lines are the
    binding evidence and GRADED TURNS are the only maturity gate.
    Frequency (window age, turns/day) is descriptive context and can
    never by itself justify a change — a chatty fortnight of
    never-graded turns tunes nothing.
    """
    if n_graded < MATURITY_TURNS:
        return {"verdict": "keep", "recommended": MAX_PER_PASS,
                "reason": (f"immature data ({n_graded} graded turns - "
                           f"frequency alone can never justify a change; "
                           f"the tune needs {MATURITY_TURNS}+ GRADED "
                           f"turns, horizons closed and priced on the "
                           f"store)")}
    if n_events == 0:
        return {"verdict": "keep", "recommended": MAX_PER_PASS,
                "reason": ("the cap never bound - no budget-spent lines in "
                           "surviving logs; frequency alone (%.1f turns/day "
                           "= %.3f/pass demand) cannot bind 2/pass" %
                           (per_day, per_day / PASSES_PER_DAY))}
    if net_cost > 0:
        return {"verdict": "raise", "recommended": MAX_PER_PASS + 1,
                "reason": (f"deferrals cost ${net_cost:,.2f} net across "
                           f"{n_events} surviving events - propose raising "
                           f"to {MAX_PER_PASS + 1}/pass (report-only)")}
    return {"verdict": "keep", "recommended": MAX_PER_PASS,
            "reason": (f"{n_events} deferral events priced net "
                       f"${net_cost:,.2f} - the cap's delay was free")}


def write_report(summary: dict, out: Path = STUDY_OUT) -> Path:
    L = []
    L.append("CHASE-CAP STUDY - " + summary["ts"])
    L.append("=" * 62)
    L.append(f"position ${summary['position_usd']:,.0f} | window "
             f"{summary['window_days']}d | max/pass {MAX_PER_PASS}")
    t = summary["turns"]
    L.append(f"[watchlist] turns: {t['watchlist']} "
             f"(graded {t['graded']} / pending {t['pending']} / "
             f"skipped {t['skipped']})")
    f = summary["frequency"]
    L.append(f"speak frequency: {f['turns_per_day']}/day "
             f"= {f['demand_per_pass']}/pass demand vs cap {f['max_per_pass']}")
    d = summary["deferrals"]
    L.append("")
    L.append("THE DEFERRAL QUESTION - did the cap ever defer a turn "
             "that then ran?")
    if d["events"] == 0:
        L.append("  no deferral records - the journal is empty and no "
                 "budget-spent lines survive. The journal writes from "
                 "today, so every future defer is priced; pre-journal "
                 "deferrals are unanswerable, not absent.")
    else:
        L.append(f"  {d['events']} event(s) (journal {d['journal_rows']}, "
                 f"log lines {d['log_lines']}): matched {d['matched']}, "
                 f"unmatched {d['unmatched']}, unparsed {d['unparsed']}")
        for m in d["then_ran"]:
            L.append(f"    THEN RAN: {m['pair']} @ {m['turn_ts']} - "
                     f"deferral {json.dumps(m['deferral'])}")
        L.append(f"  cost by kind: {json.dumps(d['cost_by_kind'])} "
                 f"-> net ${d['net_cost']:,.2f}")
    L.append("")
    if summary["graded"]:
        L.append("PER-PAIR (graded watchlist turns, actual world):")
        for pair, bp in sorted(summary["by_pair"].items(),
                               key=lambda kv: -kv[1]["usd_actual"]):
            L.append(f"  {pair:12s} n={bp['graded']} "
                     f"ran={bp['ran_actual']} "
                     f"${bp['usd_actual']:+,.2f} actual "
                     f"(zero-delay world ${bp['usd_zero']:+,.2f})")
    L.append("")
    r = summary["recommendation"]
    L.append(f"CAP VERDICT (report-only): {r['verdict'].upper()} at "
             f"{r['recommended']}/pass - {r['reason']}")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out


def append_ledger(summary: dict, path: Path = STUDY_LEDGER) -> Path:
    slim = {k: v for k, v in summary.items() if k not in ("graded",)}
    slim["graded_n"] = len(summary.get("graded", []))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(slim, sort_keys=True) + "\n")
    return path


def monthly_driver_loop(stop: threading.Event,
                        first_delay_s: float = 1800.0,
                        interval_days: float = 1.0) -> None:
    """The driver: first pass ~30 min after boot, then daily."""
    time.sleep(first_delay_s)
    while not stop.is_set():
        try:
            s = run_study()
            write_report(s)
            append_ledger(s)
        except Exception as exc:
            logger.warning("chasecap driver pass failed (%s) — this "
                           "cycle skips, the ledger stands", exc)
        stop.wait(interval_days * 86400.0)


# ── THE CAP GATE WATCH (Sep 20, user: "wire the cap-study trigger that
# runs the first full verdict when the 20th watchlist turn grades — 9
# of 20 are in") ────────────────────────────────────────────────────
# The monthly driver re-prices daily, but every pass below the ladder's
# CAP_MATURITY rung renders only the immature-data keep. This watch
# fires the study at the FIRST moment turn #20 grades — the first full
# verdict (deferrals priced, recommendation rendered) lands within one
# poll of the gate, not on the next scheduled pass. One-shot by
# design: once a mature verdict is on the ledger, the daily driver
# owns re-pricing and the watch exits.


def graded_cap_turns(now: float | None = None) -> int:
    """The cap gate's exact count: every [watchlist] row in
    chase_turns.jsonl whose 4-bar horizon has closed — the same filter
    and horizon math the study grades by (HORIZON_BARS on 1h bars), so
    the watch fires when the STUDY would count the gate, not before."""
    from datetime import datetime as _dt, timezone as _tz
    now = time.time() if now is None else float(now)
    n = 0
    try:
        lines = TURNS_FILE.read_text(encoding="utf-8",
                                     errors="replace").splitlines()
    except OSError:
        return 0
    for ln in lines:
        ln = ln.strip()
        if not ln or "[watchlist]" not in ln:
            continue
        try:
            d = json.loads(ln)
            ts = _dt.fromisoformat(str(d["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
            if now - ts.timestamp() >= HORIZON_BARS * 3600.0:
                n += 1
        except Exception:
            continue
    return n


def ledger_last_graded() -> int:
    """The newest ledger record's graded count (-1 when the ledger is
    missing/unreadable — never read as already-mature)."""
    try:
        _last = None
        for _l in STUDY_LEDGER.read_text(encoding="utf-8",
                                         errors="replace").splitlines():
            _l = _l.strip()
            if not _l:
                continue
            try:
                _r = json.loads(_l)
            except ValueError:
                continue  # a junk line never kills the newest record
            if isinstance(_r, dict):
                _last = _r
        return int(((_last or {}).get("turns") or {}).get("graded") or 0) \
            if _last else -1
    except OSError:
        return -1


def cap_watch_loop(stop: threading.Event, poll_s: float = 300.0) -> None:
    """Poll chase_turns.jsonl and fire the study at the FIRST moment
    the cap's maturity bar (20 graded watchlist turns) is reached —
    the first FULL verdict, one poll after the 20th turn's horizon
    closes instead of the next scheduled pass. A fire whose verdict
    still lands immature (a thin store skipped a grading) does not end
    the watch — it continues; a mature verdict ends it. Exits too when
    the ledger already holds a mature verdict (a restart never
    re-fires history). Polls quietly; only the fire (or an error)
    speaks. Never raises; dies with the scanner."""
    while not stop.is_set():
        try:
            if ledger_last_graded() >= MATURITY_TURNS:
                logger.info(" CAP GATE WATCH: the ledger already holds a "
                            "mature cap verdict — the daily driver owns "
                            "re-pricing; the watch stands down")
                return
            _n = graded_cap_turns()
            if _n >= MATURITY_TURNS:
                logger.info(" CAP GATE WATCH: %d graded watchlist turns "
                            "reached the cap maturity bar (%d) — running "
                            "the study for the FIRST FULL VERDICT",
                            _n, MATURITY_TURNS)
                s = run_study(trigger="cap_gate_watch")
                write_report(s)
                append_ledger(s)
                rec = s.get("recommendation") or {}
                _graded = ((s.get("turns") or {}).get("graded") or 0)
                if _graded < MATURITY_TURNS:
                    logger.info(" CAP GATE WATCH: the fire graded only %d "
                                "turns (thin stores skipped %d) — the "
                                "verdict stays immature; the watch "
                                "continues to the next closure",
                                _graded, s.get("turns", {}).get("skipped", 0))
                else:
                    logger.info(" CAP GATE WATCH: the first full cap "
                                "verdict is %s (recommended %s/pass, "
                                "fired at %d graded turns) — %s",
                                rec.get("verdict"), rec.get("recommended"),
                                _graded, str(rec.get("reason"))[:140])
                    return
        except Exception as exc:
            logger.warning(" CAP GATE WATCH: poll failed (%s) — retrying "
                           "next cycle", exc)
        if stop.wait(poll_s):
            return


def main() -> int:
    s = run_study()
    write_report(s)
    append_ledger(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
