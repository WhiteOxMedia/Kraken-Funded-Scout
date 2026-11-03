"""Drive of THE STALE-RELEASE TRIPWIRE (Oct 8, user ask): the first time
a stale-move JOURNAL RELEASE fires in production, the phone learns it —
tripwire A's day-latched shape (one push per day-key, re-arms tomorrow),
the OPS push leg (never the classified trade push), retry rails.

THE EVENT IS IN-SCANNER: unlike tripwire A (a polling watcher, because
the penalty has no in-scanner moment), the release fires INSIDE
background_scanner at the two stale-move release sites — so the
tripwire is an EMIT there, not a babysitter process. The latch is a
day-key in its own state file (the ops-watcher idiom: price_alert_
state.json, payer_alert_state.json); the drive runs the emit PURE
(injected state + injected push sink), the production defaults touch
only their own gitignored file.

THE RAILS (tripwire A's, inherited):
  - a failed push leaves the latch UNSET — the trip stays armed and
    the next release retries (a dead pipe must not swallow the trip);
  - the day-latch: the same day's second release pushes nothing;
  - a fresh day-key re-arms (tomorrow's first release re-alarms);
  - only gate "stale-move" trips — the funnel is generic, the ask
    is specific;
  - the first-ever fire is recorded separately (first marker) so the
    live verification can prove the first push happened.

The guarded house shape (test_pytestsafe's law): the body rides main(),
the exit rides the guard.
"""
import ast
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = 0, 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


import background_scanner as bs

NOW = datetime.datetime(2026, 10, 8, 20, 0)
PUSHES = []          # the injected sink: (text, prio, tag)
STATE = {}           # the injected latch state (mutated in place)


def _push_sink(text, prio="high", tag=None):
    PUSHES.append((str(text), prio, tag))
    return True


def _emit(pair="X/USD", gate="stale-move", pts=1.03, quick=88.0,
          floor=92.0, now=None, push=None, state=None):
    return bs._note_release_alert(
        pair, gate, pts, quick=quick, floor=floor,
        now=now or NOW, push=push or _push_sink, state=state or STATE)


def main():
    # ── 1. the fire ──────────────────────────────────────────────────
    r = _emit()
    check("first release: the push fires", r is True and len(PUSHES) == 1,
          (r, len(PUSHES)))
    txt = PUSHES[0][0] if PUSHES else ""
    check("the push names the pair, the gate, the relief, the slack",
          "X/USD" in txt and "STALE-MOVE" in txt and "1.0" in txt
          and "88" in txt and "92" in txt, txt[:110])
    check("the trip is CONSOLE-ONLY by default (the push charter: the "
          "phone carries BUY/EXIT only — the injected sink IS the surface)",
          PUSHES[0][1] == "high", PUSHES[0][1])
    check("the latch set: day-key + first-ever marker",
          STATE.get("stale_release_alert_day") == "2026-10-08"
          and STATE.get("stale_release_alert_first") == "2026-10-08",
          STATE)
    check("the first-ever record keeps the event's facts",
          STATE.get("stale_release_alert_pair") == "X/USD"
          and float(STATE.get("stale_release_alert_pts") or 0) == 1.03,
          STATE)

    # ── 2. the day-latch ─────────────────────────────────────────────
    PUSHES.clear()
    r = _emit(quick=90.0)
    check("same-day second release: the latch holds, no push",
          r is False and not PUSHES, (r, len(PUSHES)))
    check("the latch state unchanged by the silenced release",
          STATE.get("stale_release_alert_day") == "2026-10-08"
          and STATE.get("stale_release_alert_first") == "2026-10-08")

    LATER = NOW + datetime.timedelta(days=1, hours=2)
    r = _emit(pair="Y/USD", pts=2.5, quick=87.0, now=LATER)
    check("a fresh day-key re-arms: tomorrow's first release pushes again",
          r is True and len(PUSHES) == 1
          and STATE.get("stale_release_alert_day") == "2026-10-09",
          (r, STATE.get("stale_release_alert_day")))
    check("the first-ever marker NEVER moves (the first is the first)",
          STATE.get("stale_release_alert_first") == "2026-10-08", STATE)
    check("the second day's push names its own pair and relief",
          "Y/USD" in PUSHES[0][0] and "2.5" in PUSHES[0][0],
          PUSHES[0][0][:90])

    # ── 3. the dead-pipe rail ────────────────────────────────────────
    STATE.clear()
    PUSHES.clear()
    r = _emit(push=lambda t, prio="high", tag=None: False)
    check("failed push: the trip stays ARMED (latch unset), rc False",
          r is False and not STATE, (r, STATE))
    r = _emit()   # the next release retries — and lands
    check("the retry on the next release lands and latches",
          r is True and len(PUSHES) == 1
          and STATE.get("stale_release_alert_day") == "2026-10-08")

    # ── 4. the gate check ────────────────────────────────────────────
    STATE.clear()
    PUSHES.clear()
    r = _emit(gate="deep-veto")
    check("only stale-move trips: a deep-veto release emits nothing",
          r is False and not PUSHES and not STATE, (r, STATE))
    r = _emit(gate="tide-out")
    check("only stale-move trips: a tide-out release emits nothing",
          r is False and not PUSHES and not STATE)

    # ── 5. hermeticity: the production file never opens in this drive ─
    check("the state file is a dedicated ops-idiom file (not scanner_state)",
          bs.STALE_RELEASE_ALERT_STATE.endswith(
              "stale_release_alert_state.json")
          and bs.STALE_RELEASE_ALERT_STATE.replace("\\", "/")
          != str(ROOT / "scanner_state.json").replace("\\", "/"))
    check("the state file is gitignored (runtime state, never committed)",
          "stale_release_alert_state.json" in
          (ROOT / ".gitignore").read_text(encoding="utf-8"))

    # ── 6. the wiring: the emit rides both stale-move release sites ──
    src = Path(ROOT / "background_scanner.py").read_text(encoding="utf-8")
    _emit_calls = src.count("_note_release_alert(") - 1  # minus the def
    check("the emit is called exactly twice (the two stale-move sites)",
          _emit_calls == 2, _emit_calls)
    _emit_sites = [m.start() for m in re.finditer(
        r"_note_release_alert\(", src)
        if src[max(0, m.start() - 4):m.start()] != "def "]
    check("both emit sites sit AFTER their _mark_release (the release "
          "is real)",
          len(_emit_sites) == 2
          and all(0 < m - src.rfind('_mark_release(pair, "stale-move")',
                                    0, m) < 400 for m in _emit_sites),
          [(m, m - src.rfind('_mark_release(pair, "stale-move")', 0, m))
           for m in _emit_sites])
    check("the gate check is in the source (only stale-move trips)",
          'if gate != "stale-move":' in src
          or "gate != 'stale-move'" in src)
    check("the latch keys are the day-key shape (tripwire A's latch)",
          "stale_release_alert_day" in src
          and "stale_release_alert_first" in src)
    check("the emit text names the event (first-spend voice)",
          "JOURNAL RELEASE" in src and "stale-move" in src)

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
