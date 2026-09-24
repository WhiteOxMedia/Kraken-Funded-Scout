"""test_rebootsurv.py — the REBOOT-SURVIVAL drive (Sep 22, user rule:
"prove pulse, stamps, and guard state all survive a scanner reboot").

The restart is the machine's most violent routine event: the process
dies, the disk remains. Whatever must outlive it has to live on disk and
be re-read on boot. This drive proves each surface's survival contract
HERMETICALLY (tmpdir state, guarded swaps, try/finally restores — never
the production files, never a real restart):

  1. THE PULSE — cycles CARRY across a beat (read-modify-write, the
     counter never resets on a reboot); the pid refreshes to the live
     process; a corrupt or missing pulse file degrades to a zero-start
     beat, never a crash.
  2. THE STAMPS — watch rows survive a save/load round-trip with every
     stamp intact (checked_ts, contest_id, goal_pct, window_min,
     opened_ts); the boot's loader inherits them; a corrupt watch
     degrades to {} (a dead file must not make a dead scanner); the
     save is atomic (.tmp + os.replace — a reboot mid-write leaves old
     or new, never a half-file).
  3. THE GUARD — self_adjust's once-per-day gate lives in the FILE
     (adjusted_on), not the process: proven by importlib.reload (the
     fresh-process stand-in), a same-day call after the reload returns
     None (no double adjust); a carried/unreadable recompute never
     stamps (no false freshness).
  4. THE REBOOT EQUIVALENCE ITSELF — the reload is only a faithful
     reboot if state is read from disk per call: a source census pins
     the read-per-call shape in all three modules. A future module-level
     cache would silently make this drive a liar — the census makes it
     a loud one instead.

Run:  python test_rebootsurv.py   (exit 0 = all pass)
"""
import importlib
import json
import os
import sys
import time
from pathlib import Path
import tempfile

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import background_scanner as bs
import profit_guard as pg
import scratch_space   # scratch policy: nothing volatile lands in the repo root

PASS = FAIL = 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


TMP = Path(tempfile.mkdtemp(prefix="rebootsurv_"))
PULSE = TMP / "scanner_pulse.json"
WATCH = TMP / "contest_watch.json"
GSTATE = TMP / "profit_guard_state.json"

_real_pulse, _real_watch = bs.PULSE_JSON, bs.CONTEST_WATCH_JSON
# the guard's state path is ROOT-derived per call now (Sep 24) — capture
# the MODULE DEFAULT via getattr (an explicit pin may not exist)
_real_gstate = getattr(pg, "STATE_FILE", None)
_real_pg_root = pg.ROOT

try:
    # GUARDED SWAPS (testleaks, Sep 23): installed inside the try whose
    # finally hands the production paths back — the reboot-survival
    # scenarios can never strand a sandbox pointer on a crash.
    bs.PULSE_JSON = str(PULSE)       # leak-audit: ok — restored in the finally below
    bs.CONTEST_WATCH_JSON = str(WATCH)   # leak-audit: ok — same finally
    pg.ROOT = TMP                    # leak-audit: ok — the state path follows ROOT per call, restored in the finally
    pg.STATE_FILE = GSTATE           # leak-audit: ok — explicit pin still wins (belt + braces); restored in the finally
    # ── 1. THE PULSE SURVIVES ────────────────────────────────────────
    PULSE.write_text(json.dumps({"cycles": 499, "last_cycle_ts": 0.0,
                                 "pid": 26208}), encoding="utf-8")
    bs._pulse_beat()
    p1 = json.loads(PULSE.read_text(encoding="utf-8"))
    check("pulse: cycles CARRY across a beat (499 -> 500, never reset)",
          p1["cycles"] == 500, p1)
    check("pulse: the pid refreshes to the live (new) process",
          p1["pid"] == os.getpid(), f"{p1['pid']} vs {os.getpid()}")
    check("pulse: the beat stamps a fresh completion time",
          time.time() - p1["last_cycle_ts"] < 10, p1["last_cycle_ts"])

    PULSE.write_text("not json at all", encoding="utf-8")
    bs._pulse_beat()
    p2 = json.loads(PULSE.read_text(encoding="utf-8"))
    check("pulse: a corrupt file degrades to a zero-start beat (no crash)",
          p2["cycles"] == 1, p2)

    if PULSE.exists():
        PULSE.unlink()
    bs._pulse_beat()
    p3 = json.loads(PULSE.read_text(encoding="utf-8"))
    check("pulse: a missing file starts the count at 1 (first boot)",
          p3["cycles"] == 1, p3)

    _pbs = (BASE / "background_scanner.py").read_text(encoding="utf-8")
    check("pulse: the beat READS before it WRITES (the carry contract)",
          "_p.get(\"cycles\", 0)) + 1" in _pbs.replace("\r\n", "\n")
          or "_p.get('cycles', 0)) + 1" in _pbs.replace("\r\n", "\n"))
    check("pulse: the write is atomic (.tmp + os.replace)",
          "os.replace(_tmp, PULSE_JSON)" in _pbs.replace("\r\n", "\n"))

    # ── 2. THE STAMPS SURVIVE ────────────────────────────────────────
    rows = {"ADA/USD": {"entry": 0.42, "high": 0.45, "size": 2500.0,
                        "added": "2026-09-22T10:00:00-06:00",
                        "opened_ts": "2026-09-22T10:00:00-06:00",
                        "checked_ts": "2026-09-22T21:59:00-06:00",
                        "contest_id": "c-2026-09-08", "goal_pct": 1.5,
                        "window_min": 120},
            "KAITO/USD": {"entry": 1.10, "high": 1.14, "size": 1800.0,
                          "added": "2026-09-22T12:00:00-06:00",
                          "checked_ts": "2026-09-22T21:59:30-06:00",
                          "contest_id": "c-2026-09-08"}}
    bs._save_contest_watch(rows)
    got = bs._load_contest_watch()
    _ada = got.get("ADA/USD") or {}
    check("stamps: the boot's loader inherits the watch rows",
          len(got) == 2 and abs(_ada.get("entry", 0) - 0.42) < 1e-9,
          list(got.keys()))
    check("stamps: every time/window stamp survives the round-trip",
          _ada.get("checked_ts") == "2026-09-22T21:59:00-06:00"
          and _ada.get("added") == "2026-09-22T10:00:00-06:00"
          and _ada.get("opened_ts") == "2026-09-22T10:00:00-06:00"
          and _ada.get("window_min") == 120,
          {k: _ada.get(k) for k in ("checked_ts", "added", "opened_ts")})
    check("stamps: the contest window stamp survives (the GET OUT judge "
          "reads it after any reboot)",
          _ada.get("contest_id") == "c-2026-09-08", _ada.get("contest_id"))
    check("stamps: the goal the light's clock runs on survives",
          _ada.get("goal_pct") == 1.5, _ada.get("goal_pct"))

    WATCH.write_text("{corrupt", encoding="utf-8")
    check("stamps: a corrupt watch degrades to {} (a dead file never "
          "makes a dead scanner)",
          bs._load_contest_watch() == {})
    if WATCH.exists():
        WATCH.unlink()
    check("stamps: a missing watch boots empty",
          bs._load_contest_watch() == {})

    _sv = _pbs.replace("\r\n", "\n")
    check("stamps: the watch save is atomic (.tmp + os.replace — a "
          "reboot mid-write leaves old or new, never a half-file)",
          "os.replace(tmp, CONTEST_WATCH_JSON)" in _sv)

    # ── 3. THE GUARD SURVIVES ────────────────────────────────────────
    import datetime as _dt
    MST = getattr(bs, "MST", None) or __import__("zoneinfo",
                                                 fromlist=["ZoneInfo"]) \
        .ZoneInfo("America/Denver")
    _today = _dt.datetime(2026, 9, 22, 20, 0, tzinfo=MST)
    _yesterday_state = {"adjusted_on": "2026-09-21", "factor": 1.9,
                        "ts": "2026-09-21T20:00:00-06:00"}
    GSTATE.write_text(json.dumps(_yesterday_state), encoding="utf-8")

    _real_gf = pg.guard_factor

    def _gf_fresh(pair, live, when=None):
        return {"readable": True, "factor": 2.0, "env": 3.0,
                "disc_mod": 0.8, "mlt_corr": 1.0, "vol_mod": None,
                "asset_mem": 1.0, "funding_mod": 1.0, "decay_mod": 1.0}

    try:
        pg.guard_factor = _gf_fresh
        res = pg.self_adjust(now=_today)
        st1 = json.loads(GSTATE.read_text(encoding="utf-8"))
        check("guard: self_adjust re-stamps for the new day (yesterday's "
              "mark never blocks today)",
              res is not None and st1["adjusted_on"] == "2026-09-22", st1)
        check("guard: the freshly computed factor and parts land in the "
              "file (tomorrow's learning)",
              st1["factor"] == 2.0 and st1["parts"].get("env") == 3.0, st1)
        check("guard: a same-day second call is a no-op (once per day)",
              pg.self_adjust(now=_today) is None)

        # ── THE REBOOT: a fresh module, no process memory ──
        importlib.reload(pg)
        pg.ROOT = TMP           # the reload restores the ROOT default
        pg.STATE_FILE = GSTATE  # re-pin (belt + braces for the guard state)
        check("guard: after a REBOOT (fresh module) the same-day gate "
              "still holds — the date lives in the FILE, not the process",
              pg.self_adjust(now=_today) is None,
              json.loads(GSTATE.read_text(encoding="utf-8")))

        def _gf_carried(pair, live, when=None):
            return {"readable": True, "factor": 2.0, "carried_from": "old",
                    "env": None, "disc_mod": None, "mlt_corr": None}

        pg.guard_factor = _gf_carried
        _st_before = GSTATE.read_text(encoding="utf-8")
        _tomorrow = _dt.datetime(2026, 9, 23, 20, 0, tzinfo=MST)
        check("guard: a CARRIED recompute never stamps (no false "
              "freshness — the honest-retry contract)",
              pg.self_adjust(now=_tomorrow) is None
              and GSTATE.read_text(encoding="utf-8") == _st_before)
        pg.guard_factor = _gf_fresh
        check("guard: the honest retry lands the next day (yesterday's "
              "unreadable night never costs tomorrow its factor)",
              pg.self_adjust(now=_tomorrow) is not None
              and json.loads(GSTATE.read_text(encoding="utf-8"))
              ["adjusted_on"] == "2026-09-23")
    finally:
        pg.guard_factor = _real_gf

    # ── 4. THE REBOOT EQUIVALENCE ITSELF ─────────────────────────────
    # the reload above is only a faithful reboot if every state read is
    # a disk read per call — a module-level cache would make this drive
    # a liar. The census pins the read-per-call shape in all three.
    check("census: the pulse reads disk per beat",
          "open(PULSE_JSON" in _sv)
    check("census: the watch loader reads disk per call",
          "open(CONTEST_WATCH_JSON" in _sv)
    check("census: the guard's state is read per call (no cache)",
          "json.loads(_state_file().read_text" in
          (BASE / "profit_guard.py").read_text(encoding="utf-8")
          .replace("\r\n", "\n"))
    check("census: the DAY-review guard reads discipline state per call "
          "(the review's restart-proofing)",
          "def _day_review_spoken" in _sv
          and 'h.get("rot") == "DAY"' in _sv
          and "load_state().get(" in _sv)
finally:
    # leak-audit: ok — the GUARDED RESTORE: production pointers handed
    # back (STATE_FILE None when no default existed, returning the
    # module to its ROOT-derived per-call path)
    bs.PULSE_JSON, bs.CONTEST_WATCH_JSON = _real_pulse, _real_watch
    pg.ROOT = _real_pg_root
    if _real_gstate is not None:
        pg.STATE_FILE = _real_gstate
    else:   # leak-audit: ok — the guarded restore in this finally
        pg.STATE_FILE = None
    for _p in PULSE, WATCH, GSTATE:
        try:
            _p.unlink()
        except OSError:
            pass
    try:
        TMP.rmdir()
    except OSError:
        pass

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
(scratch_space.report_temp('_rebootsurv_check.txt')).write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
print("\n".join(lines))
sys.exit(1 if FAIL else 0)
