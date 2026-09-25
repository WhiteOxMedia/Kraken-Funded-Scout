"""run_battery.py - THE ONE-COMMAND BATTERY (rebuilt Sep 15 after the
deletion event took the original with it).

Every verification the project owns, one command:
  - every test_*.py in the scout root (auto-discovered, so a new suite
    is an entry the moment it exists - the flood suite became #104 that
    way on Sep 14);
  - the DIAG_RUN subset of diag_* worlds (the structural gates + hermetic
    replays - fast, deterministic, sandbox-safe);
  - every other diag_* is listed as SKIP so the master list stays
    visible and nothing silently disappears from discovery.

Output: a streaming per-entry line (so a hang is visible live), then a
summary table with pass-counts parsed from each suite's own final
"N/M" line. Exit 0 only when every RUN entry passed.

Run:  .venv\\Scripts\\python.exe -X utf8 run_battery.py

THE LANES (Sep 22): the battery grew past what one shell window holds -
a full run exceeds the 600s terminal sync timeout, so "test everything"
kept dying mid-sweep. Lanes split the fleet by MEASURED runtime, kept
in a manifest the battery itself maintains:

  battery_lanes.json    {stem: {"secs": <last measured>, "runs": <n>}}
                        (bare name, matching the other permanent runtime
                        files: scanner_pulse.json, console_prefs.json)

  --fast --budget=N     only suites PROVEN fast (last measured secs <=
                        fastmax, default 60), and only while the lane's
                        cumulative elapsed stays under budget (default
                        420s). Anything slow or NEVER MEASURED defers —
                        the fast lane is terminal-safe by construction,
                        not by optimism. A per-entry watchdog that would
                        outlive the lane budget is tightened to fit.
  --slow                the complement: proven-slow suites PLUS every
                        never-measured one. This lane is allowed to be
                        long - run it when there is time, or in chunks
                        via the existing positional filter.
  --rerun-deferred      (with --slow) also run the entries a previous
                        --fast deferred — they are measured-FAST, so the
                        plain lane filter excludes them and the summary's
                        follow-up names this flag.
  --list                print the lane assignment and exit (no runs).
  (no flags)            the full battery, exactly as before; every run
                        refreshes the manifest with fresh measurements.A deferred entry is not a failure: the summary counts it separately
  and says how to finish the sweep. The manifest is runtime state (it
  stays uncommitted like the check files).

THE AUTO-CHUNK (Sep 24): even the full sweep outgrew the lanes - a
complete run is ~20 minutes, past what one shell window reliably
holds, and a window death used to lose the whole sweep AND its
history record. Now a full or --slow run chunks ITSELF: after
CHUNK_DEFAULT seconds of wall time (--chunk=N overrides, --chunk=0
disables) the battery pauses cleanly at the next entry boundary and
saves battery_resume.json (runtime state, uncommitted). Re-running
the same command RESUMES: finished entries are skipped, the remainder
runs under a fresh budget, and the completed sweep lands ONE summary
and ONE history record assembled across chunks - true total wall
time, "chunks": N in the record. State is saved after every entry,
so a hard window death costs at most the one entry that was mid-run.
--fresh discards a paused sweep; a pause of a different mode or
filter is left alone; --fast never pauses (its lane is terminal-safe
by construction) and leaves any pause waiting.
"""

import json
import math
import os
import re
import subprocess
import sys
import time
try:
    import msvcrt            # the lanes manifest's cross-process lock (Windows)
except ImportError:          # non-Windows: unlocked — the atomic replace still
    msvcrt = None            # prevents torn reads, and this fleet is Windows
                             # (the Sep-24 review asked for a cross-platform
                             # lock; a third-party dep buys nothing here)

import scratch_space
from datetime import datetime as _dt
from pathlib import Path

SCOUT = Path(__file__).resolve().parent
PY = SCOUT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)

LANES_MANIFEST = SCOUT / "battery_lanes.json"
FAST_DEFAULT_BUDGET = 420    # the fast lane's total wall-clock ceiling
FAST_DEFAULT_MAX = 60        # a suite measured slower than this is SLOW

CHUNK_DEFAULT = 600          # wall seconds per invocation before the run
                             # auto-chunks (the terminal sync window)
RESUME_FILE = SCOUT / "battery_resume.json"

# The diag worlds the battery runs (kept fast + hermetic by design).
# Heavy/interactive replays (fleet studies, live-API calibrations, the
# sim-arc seeders) stay manual: listed as SKIP below.
DIAG_RUN = {
    "diag_hygiene",        # root-litter gate (both repos)
    "diag_cooldowncheck",  # THE MORNING CHECKLIST as one command: runs
                           # the stalewatch/hygiene cycle in order and
                           # grades all six agenda steps (routing, report
                           # integrity, ledger health, RULE D alignment)
    "diag_coderabbit_backlog",  # review-coverage watchdog: a commit with
                           # no review batch after N hours (both repos,
                           # CODERABBIT_BACKLOG_HOURS overrides) FAILS
                           # - the silent review gap gets loud
    "diag_cachesweep",     # cached-function staleness hazards
    "diag_stshadow",       # st-shadowing (swallowed widgets)
    "diag_widgetsweep",    # stateless widget sweep
    "diag_dialaudit",      # shared-file dial contracts (volume/watch/exit)
    "diag_silentpass",     # except-pass gates, fleet-wide
    "diag_prefsmirror",    # prefs JSON mirror integrity
    "diag_sizingaudit",    # read-before-assign in sizing gates
    "diag_smoketier",      # THE $1K-TIER SMOKE: guard/carry/stop by arithmetic
    "diag_testleaks",      # module-global leaks in the suites
    "kraken_stalewatch",   # THE STALE-FILE WATCHER: purges scratch/ + OS
                           # temp workspace of 90-min-old artifacts every
                           # rotation, summary-only into reports/
}

PER_ENTRY_TIMEOUT = 420   # seconds; a hung suite fails instead of hanging

# Per-suite budget overrides. test_apptest is the heavy plate suite (every
# MASTER render, 400+ pins) and its runtime grows with every plate added -
# it has tripped the flat 420s watchdog before, and its growth has only one
# direction. The override hands it 900s while every other entry keeps the
# tight 420s hang-detection budget: a genuinely hung suite is still declared
# dead fast, and the heavy suite's extra headroom is stated here instead of
# hiding in a global bump. The WARN_FRACTION line in the summary makes any
# suite trending toward its budget visible BEFORE the trip.
TIMEOUT_OVERRIDES = {
    "test_apptest": 900,
}
WARN_FRACTION = 0.5       # PASS using >50% of budget gets a WARN in its detail

# ── MEASURED SLOW-SUITE BUDGETS (Sep 23, the costdrivers WARN): a flat
# watchdog makes the WARN_FRACTION band a tripwire on every honest run of
# a genuinely slow suite — costdrivers' lane mean is ~280s against the
# 420s default, so the >210s WARN band fired on a healthy 318s PASS. A
# suite with a measured lane time now earns a watchdog of
# max(override, MEASURED_BUDGET_MULT x mean), capped at
# MAX_MEASURED_BUDGET — in EVERY mode, the apptest-override precedent:
# a budget that only spoke in one lane would leave the full run lying.
# An honest run lands well under 50% again; a suite drifting toward 3x
# its mean STILL warns (the signal keeps its meaning). Never-measured
# suites and fresh clones (no manifest) keep the flat default — hang
# detection stays tight everywhere the measurement can't speak.
MEASURED_BUDGET_MULT = 3.0    # watchdog = 3x the lane mean (headroom, not a target)
MAX_MEASURED_BUDGET = 1800    # 30 min hard ceiling: a suite that slows 10x
                              # re-baselines its own budget (latest-wins
                              # manifest) — this bounds that ratchet


def budget_for(stem: str) -> int:
    """The watchdog budget for one entry: the override map, else the flat
    default — raised to max(override, MULT x measured mean) when the lanes
    manifest holds a measurement for it (capped at MAX_MEASURED_BUDGET)."""
    b = TIMEOUT_OVERRIDES.get(stem, PER_ENTRY_TIMEOUT)
    e = _lanes_load().get(stem)
    if e and e.get("secs"):
        b = max(b, min(int(float(e["secs"]) * MEASURED_BUDGET_MULT),
                       MAX_MEASURED_BUDGET))
    return b


def _lanes_entry(e) -> dict | None:
    """One manifest entry, VALIDATED: secs must parse to a finite,
    non-negative number and runs to a non-negative int. A corrupt or
    hand-edited entry is DISCARDED, never trusted — a `secs: "unknown"`
    that reached lane_of would raise ValueError mid-`--list`/`--fast`
    and stop the battery (the Sep-22 CodeRabbit finding, merged Sep 24).
    None means unusable."""
    if not isinstance(e, dict):
        return None
    try:
        secs = float(e.get("secs"))
        runs = int(e.get("runs") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(secs) or secs < 0.0:
        return None
    return {"secs": secs, "runs": max(0, runs)}


def _lanes_load() -> dict:
    """The lanes manifest, entry-validated. A corrupt shape or a
    malformed entry fails soft to that entry's absence — one bad
    measurement can never stop a sweep, and garbage secs can never
    reach lane_of."""
    try:
        d = json.loads(LANES_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for st, e in d.items():
        v = _lanes_entry(e)
        if v is not None:
            out[st] = v
    return out


def _lanes_save(measured: dict) -> None:
    """Merge this run's fresh measurements into the manifest: latest
    seconds win, the run count accumulates. The read-modify-write is
    SERIALIZED across concurrent battery processes (a lock file in the
    scratch policy dirs — two lanes racing used to let the later write
    discard the other's measurements) and published ATOMICALLY via
    temp+replace, so a concurrent _lanes_load can never read a
    half-written manifest. Runtime state - never a product file,
    never committed."""
    try:
        lock_path = scratch_space.report_temp("battery_lanes.lock")
        with open(lock_path, "a+") as lk:
            if msvcrt is not None:
                try:
                    msvcrt.locking(lk.fileno(), msvcrt.LK_LOCK, 1)
                except OSError:
                    pass                   # a lost lock degrades, never blocks
            try:
                lanes = _lanes_load()
                for stem, secs in measured.items():
                    e = lanes.get(stem) or {}
                    lanes[stem] = {"secs": round(float(secs), 1),
                                   "runs": int(e.get("runs") or 0) + 1}
                # the temp publishes from the scratch tree (same volume,
                # so the replace stays atomic): a root-adjacent .tmp could
                # be caught by the hygiene gate's root audit mid-write
                tmp = scratch_space.report_temp("battery_lanes.json.tmp")
                tmp.write_text(json.dumps(lanes, indent=1), encoding="utf-8")
                os.replace(tmp, LANES_MANIFEST)
            except OSError:
                pass  # a lost manifest only costs the lanes their memory
            finally:
                if msvcrt is not None:
                    try:
                        msvcrt.locking(lk.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
    except OSError:
        pass   # a lost lock or an unwritable scratch only costs the merge


def _resume_load() -> dict:
    """The paused sweep, if one waits. Runtime state - never committed."""
    try:
        d = json.loads(RESUME_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("mode") else {}
    except (OSError, ValueError):
        return {}


def _resume_save(mode: str, flt: str, chunk_no: int, rows: list,
                 done: list, wall_acc: float) -> None:
    """Persist the sweep-in-progress (called after every finished entry,
    so a hard window death costs at most the one mid-run entry)."""
    try:
        RESUME_FILE.write_text(json.dumps({
            "mode": mode, "filter": flt, "chunk": chunk_no,
            "ts": _dt.now().astimezone().isoformat(timespec="seconds"),
            "rows": rows, "done": done,
            "wall_acc": round(float(wall_acc), 1),
        }, indent=1), encoding="utf-8")
    except OSError:
        pass  # a lost pause only costs the sweep its resume


def _resume_clear() -> None:
    try:
        RESUME_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── THE HEALTH REPORT (Sep 23): every battery run lands one timestamped
# JSON record in battery_history/ — the consolidated table, machine-
# readable, so battery history is comparable week over week. The console
# table stays the human view; the record is the durable one. Each record
# carries the mode it ran in (full/fast/slow/chunk) and the skip/defer
# counts, so a partial lane run is self-describing and never reads as a
# regression. Runtime state: the folder stays uncommitted, like the
# check files. A lost or unwritable history never fails a green run.
HISTORY_DIR = SCOUT / "battery_history"


def _history_record(rows, mode: str, wall_s: float) -> dict:
    """The run's durable record: totals, failures, warns, per-suite
    status + measured seconds (the same numbers the lanes manifest
    keeps, plus everything the manifest drops)."""
    suites = {}
    failures, warns = [], []
    n_pass = n_fail = n_skip = n_defer = 0
    for kind, name, st, dt, det in rows:
        suites[name] = {"status": st, "secs": round(float(dt), 1),
                        "detail": det}
        if st == "PASS":
            n_pass += 1
        elif st in ("SKIP", "DEFER"):
            n_skip += 1
            if st == "DEFER":
                n_defer += 1
        else:
            n_fail += 1
            failures.append({"name": name, "status": st,
                             "detail": str(det)[:200]})
        if "WARN:" in str(det):
            warns.append({"name": name, "detail": str(det)[:160]})
    return {
        "ts": _dt.now().astimezone().isoformat(timespec="seconds"),
        "ts_epoch": time.time(),
        "mode": mode,
        "totals": {"pass": n_pass, "fail": n_fail, "skip": n_skip,
                   "defer": n_defer, "ran": n_pass + n_fail},
        "wall_s": round(wall_s, 1),
        "failures": failures,
        "warn": warns,
        "suites": suites,
    }


def _history_save(rec: dict) -> Path | None:
    """Write run_<stamp>.json + the latest.json mirror. Best-effort:
    history is a convenience, never a gate."""
    try:
        HISTORY_DIR.mkdir(exist_ok=True)
        stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
        p = HISTORY_DIR / f"run_{stamp}.json"
        p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        (HISTORY_DIR / "latest.json").write_text(
            json.dumps(rec, indent=1), encoding="utf-8")
        return p
    except OSError:
        return None


def _history_compare(rec: dict) -> str | None:
    """The week-over-week line: the record nearest 7 days back (±2 days,
    same mode preferred), else the most recent previous one. Totals,
    wall clock, failure-set diff, and the notable per-suite time moves
    (>25% AND >0.5s, biggest first)."""
    try:
        cands = []
        for p in HISTORY_DIR.glob("run_*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("ts_epoch") and d.get("ts_epoch") < rec["ts_epoch"]:
                cands.append((float(d["ts_epoch"]), p.name, d))
        if not cands:
            return None
        cands.sort()
        week_lo, week_hi = rec["ts_epoch"] - 9 * 86400, rec["ts_epoch"] - 5 * 86400
        same_mode = [c for c in cands
                     if week_lo <= c[0] <= week_hi and c[2].get("mode") == rec["mode"]]
        week_any = [c for c in cands if week_lo <= c[0] <= week_hi]
        # same mode preferred; inside the window the record NEAREST the
        # 7-day mark wins; with no week-window record at all, the most
        # recent previous run — named honestly in the line
        week_pool = same_mode or week_any
        if week_pool:
            base = min(week_pool,
                       key=lambda c: abs((rec["ts_epoch"] - c[0]) - 7 * 86400))
        else:
            base = cands[-1]
        bep, bname, b = base
        age_d = (rec["ts_epoch"] - bep) / 86400.0
        bt, rt = b.get("totals") or {}, rec["totals"]
        out = [f"WEEK OVER WEEK (vs {bname}, {age_d:.1f}d ago, "
               f"mode={b.get('mode')})"]
        out.append(f"  pass {bt.get('pass')} -> {rt['pass']}   "
                   f"fail {bt.get('fail')} -> {rt['fail']}   "
                   f"wall {b.get('wall_s')}s -> {rec['wall_s']}s")
        bf = {f["name"] for f in b.get("failures") or []}
        rf = {f["name"] for f in rec["failures"]}
        if rf - bf:
            out.append(f"  NEW FAILURES: {', '.join(sorted(rf - bf))}")
        if bf - rf:
            out.append(f"  healed: {', '.join(sorted(bf - rf))}")
        common = set(b.get("suites") or {}) & set(rec["suites"])
        moves = []
        for nm in common:
            bs_ = float((b["suites"][nm] or {}).get("secs") or 0.0)
            rs_ = float((rec["suites"][nm] or {}).get("secs") or 0.0)
            if bs_ > 0 and abs(rs_ - bs_) > 0.5 and abs(rs_ - bs_) / bs_ > 0.25:
                moves.append((abs(rs_ - bs_) / bs_, nm, bs_, rs_))
        moves.sort(reverse=True)
        for frac, nm, bs_, rs_ in moves[:3]:
            arrow = "slower" if rs_ > bs_ else "faster"
            out.append(f"  {arrow}: {nm} {bs_:.1f}s -> {rs_:.1f}s "
                       f"({(rs_ / bs_ - 1.0) * 100:+.0f}%)")
        return "\n".join(out)
    except Exception:
        return None   # a broken comparison line must never fail the run


def lane_of(stem: str, fastmax: float) -> str:
    """'fast' (proven fast), 'slow' (proven slow), or 'unmeasured'."""
    e = _lanes_load().get(stem)
    if not e or e.get("secs") is None:
        return "unmeasured"
    return "fast" if float(e["secs"]) <= fastmax else "slow"


PASS_RE = re.compile(r"(\d+)/(\d+)\s*$")

# THE PRODUCTION-LEDGER GUARD (Sep 17): the battery's contest-window
# suite wiped the LIVE watch mid-window (INJ/ATOM erased at 13:54 while
# their windows ran) - the suite passed its own checks while erasing
# production. The guard snapshots the two ledgers no suite may touch,
# and a destructive change fails the entry even if its checks passed.
# A live fire may legitimately ENRICH the watch mid-battery; only the
# erase signature (entries -> {}) or a contest_id move is a leak.
PROD_GUARD = ("contest_watch.json", "contest_account.json")


def _guard_snap() -> dict:
    snap = {}
    for name in PROD_GUARD:
        p = SCOUT / name
        val = None
        for _try in range(3):   # the live scanner writes these files
            try:                # throughout a battery; a read landing
                val = (p.read_text(encoding="utf-8")  # in a write window
                       if p.exists() else None)      # retries before it
                break                                # may call <unreadable>
            except OSError:
                val = "<unreadable>"
                time.sleep(0.25)
        snap[name] = val
    return snap


def _guard_close_receipts(t0: float, t1: float) -> set[str]:
    """Pairs with a REAL close record inside the run window — the exit
    that removes a watch row lands its trade here before (or moments
    after) the row is dropped. ±60s slop covers the save gap. An
    unreadable ledger yields NO receipts: the guard fails toward
    flagging, never toward silence."""
    out: set[str] = set()
    try:
        f = SCOUT / "contest_trades.jsonl"
        if not f.exists():
            return out
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
                sold = _dt.fromisoformat(str(rec.get("sold_at"))).timestamp()
                if t0 - 60 <= sold <= t1 + 60:
                    out.add(str(rec.get("pair") or ""))
            except (ValueError, TypeError):
                continue
    except OSError:
        pass
    return out


def _guard_entry_expiry(e: dict) -> float | None:
    """The watch entry's window expiry as epoch (added + window_min);
    None when unparseable — an unknown window is never assumed expired."""
    try:
        added = _dt.fromisoformat(str((e or {}).get("added")))
        return added.timestamp() + float((e or {}).get("window_min") or 0.0) * 60.0
    except Exception:
        return None


def _guard_leak(before: dict, after: dict,
                t0: float | None = None, t1: float | None = None,
                receipts: set[str] | None = None) -> str | None:
    """THE SCAN-AWARE GUARD (Sep 24): a battery entry can run 40 minutes,
    and the scanner keeps working the whole time — an exit auto-closes a
    position and drops its row, an expired window retires quietly, a
    user opens a new contest (the ONLY contest_id writer, whose fresh
    stamp is its own receipt and whose documented wipe rides along).
    The old before/after text compare called all of that the 13:54 leak.
    Now every vanished entry needs a RECEIPT: a close record for its
    pair inside the run window, or a window that expired by the
    after-snapshot, or a fresh contest window. No receipt and the window
    still open -> the leak — even when OTHER entries survive (the
    partial-wipe gap the old ->{}-only check never saw). Additions stay
    free (a live fire enriching the watch); file create/delete and an
    unexplained contest_id move still flag."""
    t0 = time.time() - 1 if t0 is None else t0
    t1 = time.time() if t1 is None else t1

    # the fresh-window receipt, computed once: contest_id moves ONLY in
    # new_contest (user action) — a stamp born inside the run window is
    # that user action's signature, and the wipe it performs is documented
    _fresh_window = False
    try:
        _bid = str((json.loads(before.get("contest_account.json")
                               or "{}") or {}).get("contest_id") or "")
        _aid = str((json.loads(after.get("contest_account.json")
                               or "{}") or {}).get("contest_id") or "")
        if _aid and _aid != _bid:
            _st = _dt.fromisoformat(_aid).timestamp()
            _fresh_window = t0 - 60 <= _st <= t1 + 60
    except (ValueError, TypeError):
        pass

    for name in PROD_GUARD:
        b, a = before.get(name), after.get(name)
        if b == a:
            continue
        if name == "contest_watch.json":
            try:
                bj = json.loads(b) if b else None
                aj = json.loads(a) if a else None
            except (ValueError, TypeError):
                return f"{name} became unreadable"
            if b is None and a is not None:
                return f"{name} was CREATED during the battery"
            if b is not None and a is None:
                return f"{name} was DELETED during the battery"
            if not isinstance(bj, dict) or not isinstance(aj, dict):
                continue
            vanished = {k: v for k, v in bj.items() if k not in aj}
            if not vanished:
                continue          # additions/edits only: enrichment is fine
            if receipts is None:
                receipts = _guard_close_receipts(t0, t1)   # lazy: only a
                           # vanished entry opens the ledger
            for k, e in vanished.items():
                exp = _guard_entry_expiry(e)
                if (k not in receipts
                        and not (exp is not None and exp <= t1)
                        and not _fresh_window):
                    return (f"{name} lost live entry {k} with no close "
                            f"receipt, expiry, or new-window wipe")
            continue
        # contest_account.json: balance writes from live fills are normal
        # mid-battery; the window stamp moving WITHOUT a fresh-window
        # signature is the phantom-reset class and never legitimate here
        try:
            bid = str((json.loads(b) or {}).get("contest_id") or "")
            aid = str((json.loads(a) or {}).get("contest_id") or "")
        except (ValueError, TypeError):
            return f"{name} became unreadable"
        if bid != aid and not _fresh_window:
            return (f"{name} contest_id moved "
                    f"{bid[:19]} -> {aid[:19]}")
    return None


def run_entry(path: Path, cap: int | None = None):
    """Returns (status, seconds, detail). status in PASS/FAIL/TIMEOUT.
    cap (lanes): tighten the watchdog to the lane's remaining budget so
    a hung 'fast' suite cannot blow the lane's terminal-safety promise."""
    b = budget_for(path.stem)
    if cap is not None:
        b = min(b, max(cap, 5))
    t0 = time.monotonic()
    try:
        p = subprocess.run(
            [str(PY), "-X", "utf8", str(path)],
            cwd=str(SCOUT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=b)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", time.monotonic() - t0, f">{b}s"
    dt = time.monotonic() - t0
    if p.returncode != 0:
        tail = (p.stdout or "").strip().splitlines()[-3:]
        err = (p.stderr or "").strip().splitlines()[-2:]
        detail = " | ".join(tail + err)[:160]
        return "FAIL", dt, detail
    m = None
    for line in (p.stdout or "").splitlines():
        m2 = PASS_RE.search(line.strip())
        if m2:
            m = m2
    det = f"{m.group(1)}/{m.group(2)}" if m else ""
    if dt > b * WARN_FRACTION:
        det = (det + f"  WARN: {dt:.0f}s of {b}s budget").strip()
    return "PASS", dt, det


def _parse_flags(argv: list[str]):
    """Split --flags from the legacy positional args (the chunk filter
    keeps its positional index; flags never shift it)."""
    flags: dict = {}
    pos: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--fast":
            flags["lane"] = "fast"
        elif a == "--slow":
            flags["lane"] = "slow"
        elif a == "--list":
            flags["list"] = True
        elif a.startswith("--budget="):
            try:
                flags["budget"] = max(30, int(a.split("=", 1)[1]))
            except ValueError:
                pass
        elif a.startswith("--fastmax="):
            try:
                flags["fastmax"] = max(5, float(a.split("=", 1)[1]))
            except ValueError:
                pass
        elif a.startswith("--chunk="):
            try:
                flags["chunk"] = max(0, int(a.split("=", 1)[1]))
            except ValueError:
                pass
        elif a == "--fresh":
            flags["fresh"] = True
        elif a == "--rerun-deferred":
            flags["rerun_deferred"] = True
        else:
            pos.append(a)
        i += 1
    return flags, pos


def main(argv: list[str] | None = None) -> int:
    # THE CHUNK FILTER (Sep 20): an optional substring arg runs only the
    # matching entries (`run_battery.py _batt_chunk.log '' trend`). The
    # battery's growth (100+ entries, the plate suite alone 900s) means
    # a full run no longer fits a single shell window - chunks let a
    # session sweep the family in slices and still read one summary.
    # THE LANES (Sep 22) slice by MEASURED TIME instead of by name:
    # --fast keeps a terminal window safe, --slow owns the long tail,
    # no flags runs everything and refreshes the lanes manifest.
    _argv = list(argv if argv is not None else sys.argv[1:])
    flags, _argv_pos = _parse_flags(_argv)
    _flt = _argv_pos[2] if len(_argv_pos) > 2 else ""
    lane = flags.get("lane")
    fastmax = flags.get("fastmax", FAST_DEFAULT_MAX)
    fast_budget = flags.get("budget", FAST_DEFAULT_BUDGET)
    tests = sorted(SCOUT.glob("test_*.py"))
    diags = sorted(SCOUT.glob("diag_*.py"))
    if _flt:
        tests = [t for t in tests if _flt in t.stem]
        diags = [d for d in diags if _flt in d.stem]
    # DIAG_RUN may name a rotation entry outside the diag_ glob: the
    # Kraken stale-file watcher is policy machinery (scratch hygiene),
    # not a diagnostic - it joins the rotation through the same
    # per-entry machinery all the same.
    for _n in sorted(DIAG_RUN):
        if _n.startswith("diag_"):
            continue
        _p = SCOUT / f"{_n}.py"
        if _p.exists() and _p not in diags and (_flt in _n if _flt else True):
            diags.append(_p)
            diags.sort()

    if flags.get("list"):
        for nm, coll in (("test", tests), ("diag", diags)):
            for p in coll:
                print(f"  {lane_of(p.stem, fastmax):<11} {p.stem}")
        return 0

    if lane == "fast":
        tests = [t for t in tests if lane_of(t.stem, fastmax) == "fast"]
        diags = [d for d in diags if lane_of(d.stem, fastmax) == "fast"]
    elif lane == "slow":
        # the slow lane owns the long tail: proven-slow PLUS every suite
        # never yet measured (it is the coverage lane - allowed to run
        # long). --rerun-deferred folds back in the entries a previous
        # --fast deferred: they are measured-FAST, so the plain lane
        # filter excludes them and the printed follow-up could never
        # finish the sweep it names (the Sep-22 CodeRabbit finding,
        # merged Sep 24).
        tests = [t for t in tests if flags.get("rerun_deferred")
                 or lane_of(t.stem, fastmax) != "fast"]
        diags = [d for d in diags if flags.get("rerun_deferred")
                 or lane_of(d.stem, fastmax) != "fast"]

    # THE AUTO-CHUNK (Sep 24): the full sweep outgrew one shell window,
    # and a window death used to lose the sweep AND its history record.
    # chunk_limit = wall seconds this invocation may spend before it
    # pauses at the next entry boundary; --chunk=N overrides, 0 disables;
    # --fast never pauses (its lane is terminal-safe by construction) and
    # leaves a waiting pause alone for the command that owns it. A paused
    # sweep saves after EVERY entry, so a hard window death costs at most
    # the one entry that was mid-run. Rerunning the same command resumes:
    # the tail runs under a fresh budget, no further pausing (chunk_limit
    # = 0), and the mode label keeps the sweep's TRUE lane so a chunked
    # full run still lands a 'full' history record (with "chunks": N)
    # and stays baseline-comparable.
    chunk_limit = flags.get("chunk", CHUNK_DEFAULT)
    mode_key = (lane or "full") + (f":{_flt}" if _flt else "")
    if flags.get("fresh") and _resume_load():
        _resume_clear()
        print("  --fresh: the paused sweep is discarded.")
    paused = {} if lane == "fast" else _resume_load()
    _resumed = False
    _foreign_block = False   # a foreign pause waits: this run must not save
    _cno = 1
    if paused:
        if paused.get("mode") != mode_key:
            print(f"  a paused '{paused.get('mode')}' sweep is waiting — "
                  f"rerun that command to resume, or --fresh to discard.")
            paused = {}
            _foreign_block = True
        else:
            _resumed = True
            done_names = {r[1] for r in paused.get("rows") or []
                          if r[2] != "PAUSED"}
            tests = [t for t in tests if t.stem not in done_names]
            diags = [d for d in diags if d.stem not in done_names]
            chunk_limit = 0     # the tail finishes, however long it takes
            _cno = int(paused.get("chunk") or 1) + 1
            print(f"  RESUME: chunk #{_cno} of the paused sweep — "
                  f"{len(done_names)} entries done, "
                  f"{len(tests)} suites + "
                  f"{sum(1 for d in diags if d.stem in DIAG_RUN)} diags to go.")
    rows = [r for r in (paused.get("rows") or []) if r[2] != "PAUSED"]
    _i_base = sum(1 for r in rows if r[0] == "test")
    measured: dict = {}
    wall_acc = float(paused.get("wall_acc") or 0.0)
    lane_t0 = time.monotonic()
    if lane == "fast":
        chunk_limit = 0          # --fast never pauses: the lane is
                                 # terminal-safe by construction, and a
                                 # fast run must never write resume state
    _saving = lane != "fast" and not _foreign_block

    def _chunk_pause(kind: str, stem: str) -> bool:
        """True when this invocation must stop at the next entry boundary:
        the pause is persisted (surviving even a hard window death, since
        it is refreshed after every entry) and the caller exits rc=2
        without a summary — the completed sweep assembles it."""
        if not chunk_limit or time.monotonic() - lane_t0 < chunk_limit:
            return False
        rows.append((kind, stem, "PAUSED", 0.0,
                     f"auto-chunk wall {chunk_limit}s — rerun to resume"))
        if _saving:
            _resume_save(mode_key, _flt, _cno, rows, [],
                         wall_acc + (time.monotonic() - lane_t0))
        else:
            print("  (a foreign sweep's pause is waiting — this run's "
                  "progress is NOT saved; --fresh to discard it first)")
        print(f"\n  AUTO-CHUNK: {len(rows)} entries done, wall "
              f"{wall_acc + time.monotonic() - lane_t0:.0f}s cumulative — "
              f"pausing cleanly (this chunk hit the {chunk_limit}s window).")
        print("  rerun the same command to resume; "
              "--fresh discards, --chunk=0 runs to the end.")
        return True

    n_total = len(tests) + sum(1 for d in diags if d.stem in DIAG_RUN)
    n_defer = 0             # every runnable entry this lane did NOT run
    deferred: list[str] = []   # their names, for the honest follow-up line

    def _fits(stem: str, left: float) -> bool:
        """False when the remaining fast-lane budget cannot hold this
        entry even at its measured pace: starting a 50s suite with 20s
        left only produces a TIMEOUT the lane then calls a failure.
        Unmeasured entries keep the 15s gate (they cannot promise
        anything). The Sep-22 CodeRabbit finding, merged Sep 24."""
        e = _lanes_load().get(stem)
        if not e or not e.get("secs"):
            return left >= 15
        return left >= max(15.0, float(e["secs"]))
    print(f"KRAKEN BATTERY - {len(tests)} suites, "
          f"{len(diags)} diags ({len(DIAG_RUN & {d.stem for d in diags})} run)"
          + (f"  [chunk: '{_flt}']" if _flt else "")
          + (f"  [lane: {lane}, budget {fast_budget}s]" if lane == "fast"
             else (f"  [lane: {lane}]" if lane else "")))
    if lane == "fast" and n_total == 0:
        print("  the fast lane is empty — every suite is unmeasured yet.")
        print("  seed the lanes once: run the full battery (no flags) or --slow,")
        print("  then --fast knows exactly what fits a terminal window.")
    for i, t in enumerate(tests, 1 + _i_base):
        if _chunk_pause("test", t.stem):
            return 2
        _gs, _g_t0 = _guard_snap(), time.time()
        if lane == "fast":
            left = fast_budget - (time.monotonic() - lane_t0)
            if not _fits(t.stem, left):
                n_defer += 1
                deferred.append(t.stem)
                rows.append(("test", t.stem, "DEFER", 0.0,
                             "deferred: fast-lane budget — "
                             "run --slow --rerun-deferred"))
                print(f"  [{i:3d}] - {t.stem:<34}      deferred", flush=True)
                continue
            st, dt, det = run_entry(t, cap=int(left))
        else:
            st, dt, det = run_entry(t)
        _leak = _guard_leak(_gs, _guard_snap(), _g_t0, time.time())
        if _leak:
            st, det = "FAIL", f"LEDGER LEAK: {_leak}"
        if st == "PASS":
            measured[t.stem] = dt   # only a GREEN FINAL verdict proves the
                                    # runtime — the leak check can still flip
                                    # a PASS to FAIL, so measurement waits
                                    # for it (Sep-22 CodeRabbit, and the
                                    # Sep-24 review of this merge itself)
        rows.append(("test", t.stem, st, dt, det))
        if _saving:
            _resume_save(mode_key, _flt, _cno, rows, [],
                         wall_acc + (time.monotonic() - lane_t0))
        mark = {"PASS": ".", "FAIL": "F", "TIMEOUT": "T"}.get(st, "-")
        print(f"  [{i:3d}] {mark} {t.stem:<34} {dt:6.1f}s  {det}",
              flush=True)
    for d in diags:
        if d.stem in DIAG_RUN:
            if _chunk_pause("diag", d.stem):
                return 2
            _gs, _g_t0 = _guard_snap(), time.time()
            if lane == "fast":
                left = fast_budget - (time.monotonic() - lane_t0)
                if not _fits(d.stem, left):
                    n_defer += 1
                    deferred.append(d.stem)
                    rows.append(("diag", d.stem, "DEFER", 0.0,
                                 "deferred: fast-lane budget — "
                                 "run --slow --rerun-deferred"))
                    print(f"  [diag] - {d.stem:<34}      deferred",
                          flush=True)
                    continue
                st, dt, det = run_entry(d, cap=int(left))
            else:
                st, dt, det = run_entry(d)
            _leak = _guard_leak(_gs, _guard_snap(), _g_t0, time.time())
            if _leak:
                st, det = "FAIL", f"LEDGER LEAK: {_leak}"
            if st == "PASS":
                measured[d.stem] = dt   # PASS-only, after the FINAL verdict,
                                        # like the test loop
            rows.append(("diag", d.stem, st, dt, det))
            if _saving:
                _resume_save(mode_key, _flt, _cno, rows, [],
                             wall_acc + (time.monotonic() - lane_t0))
            print(f"  [diag] {'.' if st == 'PASS' else 'X'} {d.stem:<34} "
                  f"{dt:6.1f}s  {det}", flush=True)
        else:
            rows.append(("diag", d.stem, "SKIP", 0.0, "manual world"))

    if measured:
        _lanes_save(measured)

    # the sweep completed — only the OWNER retires the pause: a --fast
    # run (or any run whose pause waited on a different command) must
    # leave the waiting sweep's state alone. The owner is whoever was
    # allowed to WRITE pause state, so _saving is the exact dial: a
    # plain single-invocation sweep also retires its own pause here —
    # the old _resumed-only clear left a COMPLETED sweep's resume file
    # behind, and the next same-mode run hijacked it as a resume and
    # skipped every entry as already done.
    if _saving:
        _resume_clear()
    _n_chunks = _cno if wall_acc > 0 else 1

    print("\n" + "=" * 72)
    print(f"{'ENTRY':<38}{'RESULT':<10}{'SECS':>7}  DETAIL")
    print("-" * 72)
    n_pass = n_fail = n_skip = 0
    for kind, name, st, dt, det in rows:
        print(f"{name:<38}{st:<10}{dt:>7.1f}  {det}")
        if st == "PASS":
            n_pass += 1
        elif st in ("SKIP", "DEFER"):
            n_skip += 1
        else:
            n_fail += 1
    print("-" * 72)
    print(f"BATTERY: {n_pass} passed, {n_fail} FAILED, {n_skip} skipped "
          f"(manual diag worlds"
          + (f", {n_defer} deferred" if n_defer else "") + ")")
    if n_defer:
        # the follow-up must be able to FINISH the sweep it names: --fast
        # defers measured-FAST entries, which the plain --slow lane filter
        # excludes — hence --rerun-deferred (the Sep-22 CodeRabbit finding).
        print(f"  {n_defer} entry(ies) deferred by the fast lane — "
              f"finish the sweep with: run_battery.py --slow "
              f"--rerun-deferred ({', '.join(deferred[:4])}"
              + (f", +{len(deferred) - 4} more" if len(deferred) > 4 else "")
              + ")")
    if _n_chunks > 1:
        print(f"  sweep assembled across {_n_chunks} auto-chunks — "
              f"one summary, one history record.")

    # THE HEALTH REPORT (Sep 23): land the durable record, then speak the
    # week-over-week delta when history exists to compare against.
    _mode = (lane or "full") + (f"+chunk:{_flt}" if _flt else "")
    _rec = _history_record(rows, _mode, wall_acc + (time.monotonic() - lane_t0))
    if _n_chunks > 1:
        _rec["chunks"] = _n_chunks
    _hp = _history_save(_rec)
    if _hp:
        print(f"  health report: battery_history/{_hp.name} "
              f"(+ latest.json mirror)")
    _cmp = _history_compare(_rec)
    if _cmp:
        print(_cmp)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
