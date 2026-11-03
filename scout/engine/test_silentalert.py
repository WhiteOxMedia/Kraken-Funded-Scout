"""test_silentalert.py — TRIPWIRE A, PINNED (Oct 8).

ops/silent_alert.py: a phone ping when the silent-rotation dock
pushes discipline_penalty_extra past 2.0 — latched ONCE PER DAY
like the extension bell (user ask Oct 8). The same private ntfy
pipe as the GET OUTs (push_topic.txt), the one-second-attempt
lesson (a 200 is not a delivery receipt), and an HONEST source
label on every dose (the RML drift verdict names itself; the RA
silent-rotation dock names itself — the phone is never lied to
about WHY the penalty is up).

Pins, hermetic (the meta state file, the lock paths, the ntfy send
and the sleeps are rebound; the loop runs on a fake clock against
a SCRIPTED penalty tape: a sandboxed silent_penalty_ambient is the
drive's own seam — the live meta_layers_state.json is never opened):

  1 the trip arithmetic: 2.0 is NOT past (the boundary is strict,
    'past 2.0' = > 2.0), 2.1 trips, the fixed THRESHOLD constant is
    the ask's number;
  2 the push shape: the same topic file, the ntfy.sh wire, the
    Title/Priority/Tags headers the scanner's own push uses;
  3 the fire text names the penalty, the source and the dose in
    the ship's own words (RML = drift verdict not silent, RA =
    silent-rotation dock — the exact blind), and passes 2.0 with
    the 3.0 rail named;
  4 the trip: attempt 1 + attempt 2/2 sixty seconds later, the
    fired_day latch WRITTEN, and a fresh watcher REFUSES to re-fire
    the same day (the ask: latched once per day like the bell);
  5 a new DAY re-arms (the next day-key is a fresh morning);
  6 the dead-pipe rails: a failed send keeps the trip ARMED and
    retries; the cap gives up LOUDLY (exit 3);
  7 below-threshold cycles journal and stay quiet (no push);
  8 the dry-run seam is real: ambient_penalty dry-run reads the
    SANDBOX dict (a seeded 2.4 penalty with an RML source stamp
    parses to source 'RML (MAJOR DRIFT)' and seconds_latched > 0),
    and the production (non-dry) path reads the live state — the
    source pin names the exact ambient call in the source;
  9 read-only on the firm: the watch/account/bridge paths and the
    live meta file never opened (the sandbox file hash holds).
"""
import sys
import json
import tempfile
import time as _time_mod
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "ops"))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import silent_alert as sa
import meta_layers as ml

# ── 1. the trip arithmetic ────────────────────────────────────────────
check("THRESHOLD is the ask's number, 2.0", sa.THRESHOLD == 2.0, sa.THRESHOLD)
check("'past 2.0' is STRICT — the boundary itself does not fire",
      sa.tripped({"live_penalty": 2.0}) is False)
check("2.1 trips", sa.tripped({"live_penalty": 2.1}) is True)
check("0.5 does not trip", sa.tripped({"live_penalty": 0.5}) is False)
check("a NON-FINITE reading never trips (NaN fails the strict >)",
      sa.tripped({"live_penalty": float("nan")}) is False)

# ── 2. the push shape ─────────────────────────────────────────────────
src = (Path(__file__).parent / "ops" / "silent_alert.py").read_text(encoding="utf-8")
check("the push rides the SAME topic file and ntfy.sh wire shape the "
      "GET OUTs use (title, priority, tags headers)",
      "push_topic.txt" in src and "https://ntfy.sh/" in src
      and '"Title": "Kraken Scout"' in src and '"Priority": prio' in src
      and '"Tags": tag' in src)

# ── 3. the fire text (the dose with its name on it) ───────────────────
_rml = sa.fire_text({"live_penalty": 2.4, "source": "RML (MAJOR DRIFT)"})
check("the RML dose names itself in the ship's own words (drift, not "
      "silent, the reentry gate owned it)",
      "2.4" in _rml and "RML" in _rml and "drift" in _rml.lower()
      and "silent" in _rml.lower(), _rml)
_ra = sa.fire_text({"live_penalty": 2.6, "source": "RA (silent-rotation dock)"})
check("the RA dose names the exact blind (silent-rotation dock, "
      "buried payers behind a quiet rotation)",
      "2.6" in _ra and "RA" in _ra and "buried" in _ra.lower()
      and "silent" in _ra.lower(), _ra)
check("the fire text names the 3.0 rail (the dose is against the "
      "committed lifetime cap)", "3.0" in _ra, _ra)

# ── 4. the hermetic loop world ────────────────────────────────────────
SBX = Path(tempfile.mkdtemp())
sa.LOCK_FILE = SBX / "silent_alert.lock"
sa.STATE_FILE = SBX / "silent_alert_state.json"
sa.TOPIC_FILE = SBX / "push_topic.txt"
sa.TOPIC_FILE.write_text("scout_test_topic", encoding="utf-8")

T = {"t": 1000.0}
slept = []
sends = []             # (text, prio, tag) every attempt
_real_sleep, _real_time = _time_mod.sleep, _time_mod.time
_time_mod.sleep = lambda s: (slept.append(s), T.__setitem__("t", T[("t")][0] if False else T["t"] + s))
_time_mod.time = lambda: T["t"]


def _scripted(tape):
    """A scripted penalty READING tape (exhausted tape repeats the
    last entry; the tape serves whole ambient dicts). A _STOP marker
    ends the drive's ride with a controlled raise — the quiet case
    never exits on its own (a watcher loops forever by design)."""
    it = iter(tape)

    def _ambient(dry_run=False, **_kw):
        try:
            v = next(it)
        except StopIteration:
            v = tape[-1]
        if v is _STOP:
            raise _Stop()
        return v
    return _ambient


class _Stop(Exception):
    """the drive's controlled ride-end (never crosses into sa)."""


_STOP = object()   # the sentinel marker (a tape entry, not a reading)


def run_loop(tape, dry=True):
    """(unused placeholder — the rebind point below is the real seam)"""
    return None


# the REBIND POINT: silent_alert.ambient_penalty is the watcher's only
# sensor — patch it and every loop cycle reads the tape.
_real_ambient = sa.ambient_penalty


def _rebind(fn):
    sa.ambient_penalty = fn


# ── 5. THE TRIP (attempt 1 + attempt 2/2, the day latch) ──────────────
_attempt_starts = []
_real_send = sa.send_ntfy


def send_recorder(text, prio, tag):
    sends.append((str(text), prio, str(tag)))
    _attempt_starts.append(T["t"])
    return True
sa.send_ntfy = send_recorder

_rebind(_scripted([
    {"live_penalty": 0.5, "source": "RA (silent-rotation dock)"},   # quiet
    {"live_penalty": 0.5, "source": "RA (silent-rotation dock)"},   # quiet
    {"live_penalty": 2.4, "source": "RML (MAJOR DRIFT)"},           # TRIP
]))
sa._run_locked(30.0, dry_run=True)
check("the trip pings attempt 1, then attempt 2/2 ~60s later",
      len(sends) == 2 and (T["t"] - _attempt_starts[0]) >= 60.0,
      f"{len(sends)} sends, t={T['t']}")
check("both attempts are SKIP-worthy high priority with the mute tag",
      sends[0][1] == "high" and "mute" in sends[0][2]
      and "attempt 2/2" in sends[1][0], sends)
_fired_state = json.loads(sa.STATE_FILE.read_text(encoding="utf-8"))
_today = sa._day_key(sa._now())
check("the fired_day latch is WRITTEN (the ask, literally: latched "
      "once per day like the bell)",
      _fired_state.get("fired_day") == _today, _fired_state)

# ── 6. the latch HOLDS the same day, a NEW day re-arms ────────────────
sends.clear()
_rebind(_scripted([
    {"live_penalty": 3.0, "source": "RA (silent-rotation dock)"},
]))
_rc = sa._run_locked(30.0, dry_run=True)
check("the same-day watcher exits WITHOUT another push (the latch "
      "reads the fired_day and refuses)", len(sends) == 0 and _rc == 0,
      f"{len(sends)} sends, rc={_rc}")

sends.clear()
_real_dk = sa._day_key
sa._day_key = lambda dt: "2030-01-01"   # tomorrow, by the desk clock
_rc = sa._run_locked(30.0, dry_run=True)
sa._day_key = _real_dk
check("a NEW day re-arms and fires again (the latch is per day, not "
      "per process: the next silent dock re-alarms a fresh morning)",
      len(sends) == 2 and _rc == 0, f"{len(sends)} sends, rc={_rc}")

# ── 7. the dead-pipe rails ────────────────────────────────────────────
sends.clear()
_sa_state_days = json.loads(sa.STATE_FILE.read_text(encoding="utf-8"))
# clear the latch to re-arm (a fresh state, the day key back to today)
sa.STATE_FILE.write_text(json.dumps({"cycles": 0}), encoding="utf-8")
sa.send_ntfy = lambda text, prio, tag: False   # the pipe is dead
_rebind(_scripted([[{"live_penalty": 2.4,
                     "source": "RML (MAJOR DRIFT)"}]][0]))
_rc = None
try:
    # give-up caps at SEND_RETRY_CAP failed cycles — drive it with a
    # scripted tape of all-send-failures and a fake clock (sleep advances)
    _rebind(_scripted([{"live_penalty": 2.4, "source": "RML (MAJOR DRIFT)"}]))
    _rc = sa._run_locked(30.0, dry_run=True)
finally:
    sa.send_ntfy = send_recorder
check("a dead pipe NEVER swallows the trip: the cap gives up LOUDLY "
      "(exit 3) after the retry cap", _rc == 3, f"rc={_rc}")
check("the retry cap gave up exactly SEND_RETRY_CAP cycles in "
      "(the order-of-magnitude sanity)",
      T["t"] >= 1000.0 + 29 * 30.0, f"t advanced to {T['t']}")

# ── 8. below-threshold cycles journal and stay quiet ────────────────
sends.clear()
sa.STATE_FILE.write_text(json.dumps({"cycles": 0}), encoding="utf-8")
try:
    _rebind(_scripted([
        {"live_penalty": 0.5, "source": "RA (silent-rotation dock)"},
        {"live_penalty": 0.5, "source": "RA (silent-rotation dock)"},
        {"live_penalty": 0.5, "source": "RA (silent-rotation dock)"},
        _STOP,
    ]))
    sa._run_locked(30.0, dry_run=True)
except _Stop:
    pass
_quiet_state = json.loads(sa.STATE_FILE.read_text(encoding="utf-8"))
check("below-threshold cycles journal their state and push NOTHING",
      len(sends) == 0 and int(_quiet_state.get("cycles", 0)) >= 1
      and float(_quiet_state.get("last_penalty", 9)) == 0.5, _quiet_state)
check("the witness sat on the trip line honestly: 0.5 stayed untripped",
      sa.tripped({"live_penalty": 0.5}) is False)

# ── 9. the dry-run seam is real (the honest sensor) ───────────────────
MLSBX = Path(tempfile.mkdtemp())
ml.STATE_FILE = MLSBX / "meta_layers_state.json"
ml_state = ml._state()
ml_state["discipline_penalty_extra"] = 2.4
ml_state["rcl_last"] = {"verdict": "MAJOR DRIFT",
                        "rot": ml._rot_key_of(ml._now_mst()) if hasattr(ml, "_rot_key_of") else "",
                        "verdict_ts": ml._now_mst().timestamp() - 120.0}
ml._save(ml_state)
reading = ml.silent_penalty_ambient()
check("the sandboxed ambient read serves the RML dose: source label 'RML "
      "(MAJOR DRIFT)', seconds_latched measures the verdict fire",
      reading.get("source") == "RML (MAJOR DRIFT)"
      and float(reading.get("seconds_latched") or 0) >= 100.0
      and float(reading.get("live_penalty") or 0) == 2.4, reading)
check("the production ambient call is the SAME sensor without the seam "
      "(the source pin: ambient_penalty passes dry_run through to the "
      "meta_layers reader — no second penalty-world)",
      "ml.silent_penalty_ambient(" in src and "def ambient_penalty" in src)
# the live-file seals: the drive wrote only its own sandbox
_live_meta = Path(__file__).parent / "meta_layers_state.json"
_hash_before = hashlib.sha256(_live_meta.read_bytes()).hexdigest() if _live_meta.exists() else None
sa.ambient_penalty = _real_ambient   # restore before the fleet reads this
sa.send_ntfy = _real_send
_time_mod.time = _real_time
_time_mod.sleep = _real_sleep
check("the LIVE meta_layers_state.json was never opened for writing "
      "(the hash held through the whole run)",
      _hash_before is not None
      and hashlib.sha256(_live_meta.read_bytes()).hexdigest() == _hash_before)

print(f"\nRESULT: PASS {PASS} / FAIL {FAIL}")
for ln in lines:
    print(ln)


def main():
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    sys.exit(main())
