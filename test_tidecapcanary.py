"""test_tidecapcanary.py - THE CAP CANARY DRIVE (Oct 5, the operator's
ask: "run the retune cap canary on 3 low-risk pairs with rollback
triggers and monitoring").

The retune valve (TIDE_RETUNE_ENABLED=1, TIDE_RETUNE_DAILY_CAP=1) is
the operator's risk knob on the OUT tide's sub-bar admissions. The cap
canary is a SCOPED widening, not a dial flip: three named low-risk
pairs (BTC/USD, ETH/USD, UNI/USD — the deep-book majors; UNI already
carries the valve's one release on record) may spend up to
TIDE_RETUNE_CANARY_CAP slots/day while every other pair and the whole
funnel stay at the base cap. Rollback triggers, all dial-native:
  ROLLBACK 1  flip/remove the TIDE_RETUNE_CANARY_* keys — read LIVE
              per tick, the widening expires on the next tick
  ROLLBACK 2  TIDE_RETUNE_CANARY_UNTIL time box — the canary dies by
              itself on the date (an unparseable box = expired)
  fail-closed always: junk pair list, cap < 1, or a base cap of 0
  (operator OFF) leaves the valve exactly at the base posture.

Monitoring: the weekly reckoning's dial line names the canary scope,
cap and time box (the report is the dashboard), and the day ledger
records every release for the reconciliation.

Hermetic like every drive: the day ledger is rebound into a sandbox
(the valve's own call-time lockfile derivation keeps the lock beside
it), the dial is driven through the env-first seam, and the env and
module state are restored in the finally (the leak gate's contract).
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

results, passed, failed = [], 0, 0


def check(n, cond, why=""):
    global passed, failed
    results.append(bool(cond))
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}")


import background_scanner as bs                    # noqa: E402

SANDBOX = Path(tempfile.mkdtemp(prefix="capcanary_"))
_real_state = bs.TIDE_RETUNE_STATE_FILE
_ENV_KEYS = ("TIDE_RETUNE_ENABLED", "TIDE_RETUNE_DAILY_CAP",
             "TIDE_RETUNE_QUICK_MIN", "TIDE_RETUNE_CANARY_PAIRS",
             "TIDE_RETUNE_CANARY_CAP", "TIDE_RETUNE_CANARY_UNTIL",
             "EXIT_EXTENSION_STATE_FILE")
_real_env = {k: os.environ.get(k) for k in _ENV_KEYS}


def _setenv(**kw):
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


_until = (datetime.now().astimezone().date() + timedelta(days=7)).isoformat()
_CANARY_ENV = {
    "TIDE_RETUNE_ENABLED": "1",
    "TIDE_RETUNE_DAILY_CAP": "1",
    "TIDE_RETUNE_CANARY_PAIRS": "btc/usd, ETH/USD , uni/usd",
    "TIDE_RETUNE_CANARY_CAP": "2",
    "TIDE_RETUNE_CANARY_UNTIL": _until,
}

try:
    bs.TIDE_RETUNE_STATE_FILE = str(SANDBOX / "tide_retune_state.json")
    ledger = Path(bs.TIDE_RETUNE_STATE_FILE)
    # THE DIAL FILE IS SANDBOXED TOO (the live dial.json has had the
    # canary armed since the ops session): env-first keys beat the file
    # for the legs that SET them, but the ABSENT-keys legs must not read
    # the operator's live file — the state-file env rebind moves
    # dial_file() into the sandbox, where no dial.json exists.
    _setenv(EXIT_EXTENSION_STATE_FILE=str(SANDBOX / "exx_state.json"))

    def _fresh():
        ledger.unlink(missing_ok=True)
        # the sandbox journal resets with the ledger (it accumulates —
        # the scenario rows of earlier checks must never count in later
        # ones; the LIVE journal is never touched, that is the 4.2 gate)
        (SANDBOX / "tide_retune_history.jsonl").unlink(missing_ok=True)

    def _pairs():
        try:
            return json.loads(
                ledger.read_text(encoding="utf-8")).get("pairs", [])
        except Exception:
            return []

    print("== 1. THE DIAL SHAPE (fail-closed parse) ==")
    _setenv(**_CANARY_ENV)
    d = bs._tide_retune_dial()
    check(1.1, d.get("armed") is True and d.get("cap") == 1,
          "the base dial reads exactly as the operator set it "
          "(armed, cap 1/day)")
    check(1.2, sorted(d.get("canary_pairs") or [])
          == ["BTC/USD", "ETH/USD", "UNI/USD"],
          "the canary pair list parses, strips and uppercases (a messy "
          "operator string normalizes; junk entries drop; the dial "
          "returns a sorted LIST — JSON-safe for the weekly sidecar)")
    check(1.3, d.get("canary_cap") == 2 and d.get("canary_until") == _until,
          "the canary cap and the time box ride the dial (the time box "
          "is the automatic rollback trigger)")

    _setenv(TIDE_RETUNE_CANARY_PAIRS=None, TIDE_RETUNE_CANARY_CAP=None,
            TIDE_RETUNE_CANARY_UNTIL=None)
    d0 = bs._tide_retune_dial()
    check(1.4, d0.get("canary_pairs") == []
          and d0.get("canary_cap") is None
          and d0.get("canary_until") is None,
          "absent canary keys = no canary (the base cap stands; an "
          "expansion knob fails CLOSED, never open)")
    _setenv(**_CANARY_ENV)
    _setenv(TIDE_RETUNE_CANARY_CAP="inf")
    d_inf = bs._tide_retune_dial()
    check(1.5, d_inf.get("armed") is True and d_inf.get("cap") == 1
          and d_inf.get("canary_cap") is None,
          "a canary cap of 'inf' (OverflowError on int(float)) is INERT "
          "— the canary dies, the BASE valve stays armed at its dial "
          "(the commit review's medium: the typo must never reach the "
          "outer fallback and disarm the whole valve)")
    _setenv(TIDE_RETUNE_CANARY_CAP=None, TIDE_RETUNE_DAILY_CAP="inf")
    d_inf2 = bs._tide_retune_dial()
    check(1.6, d_inf2.get("armed") is True
          and d_inf2.get("cap") == bs.TIDE_RETUNE_CAP,
          "a base cap of 'inf' falls back to the CODE default cap (the "
          "documented chain: junk dial value -> default, never OFF)")
    _setenv(**_CANARY_ENV)   # restores DAILY_CAP=1 within the set
    _setenv(TIDE_RETUNE_DAILY_CAP="0")
    d_off = bs._tide_retune_dial()
    check(3.6, d_off.get("canary_pairs") == []
          and d_off.get("canary_cap") is None,
          "with the base cap OFF (0), the dial itself reports the canary "
          "INERT (the commit review's medium: the report and the release "
          "logic share one posture — the report can never claim a "
          "widening the release logic will not apply)")
    _setenv(TIDE_RETUNE_DAILY_CAP="1")
    _setenv(TIDE_RETUNE_CANARY_CAP="1")
    d_flat = bs._tide_retune_dial()
    check(3.7, d_flat.get("canary_pairs") == [],
          "a canary cap AT the base cap is inert too (no widening to "
          "apply = no canary, by construction in the dial)")
    _setenv(TIDE_RETUNE_CANARY_CAP="2", TIDE_RETUNE_CANARY_UNTIL=None)
    d_nobox = bs._tide_retune_dial()
    check(3.8, d_nobox.get("canary_pairs") == [],
          "an OMITTED time box is an expired one (the commit review's "
          "medium: a canary without a mandate end-date could widen "
          "indefinitely — a canary IS its time box, so the box is "
          "mandatory and fail-closed)")
    _setenv(**_CANARY_ENV)

    print("== 2. THE SCOPED CAP (3 pairs at 2/day; the funnel at 1) ==")
    _fresh()
    ok1, _w = bs._tide_retune_releases(80.0, "BTC/USD")
    check(2.1, ok1 and _pairs() == ["BTC/USD"],
          "a canary pair takes the day's first slot (quick 80 >= floor 66)")
    ok2, why2 = bs._tide_retune_releases(75.0, "XRP/USD")
    check(2.2, not ok2 and "cap is spent" in why2,
          "a NON-canary pair is refused at the base cap once one slot "
          "is spent — the canary never widens the funnel for anyone else")
    ok3, _w = bs._tide_retune_releases(72.0, "LTC/USD")
    check(2.3, ok3 is False,
          "LTC/USD is NOT in the canary set as dialed — the scoped cap "
          "is exact, not a fuzzy prefix match")
    ok4, _w = bs._tide_retune_releases(72.0, "ETH/USD")
    check(2.4, ok4 and _pairs() == ["BTC/USD", "ETH/USD"],
          "a second CANARY pair takes the canary's extra slot "
          "(1 spent < canary cap 2) — the day's +1 release, scoped")
    ok5, _w = bs._tide_retune_releases(70.0, "DOT/USD")
    check(2.5, not ok5,
          "the day hard-stops at the canary cap (2 spent >= 2)")
    ok6, why6 = bs._tide_retune_releases(80.0, "BTC/USD")
    check(2.6, not ok6 and "already released" in why6,
          "the per-pair once-a-day rule outranks the canary (no pair "
          "double-spends, canary or not)")
    _fresh()
    bs._tide_retune_releases(80.0, "BTC/USD")   # the stored casing
    ok7, why7 = bs._tide_retune_releases(80.0, "btc/usd")
    check(2.7, not ok7 and "already released" in why7,
          "a CASING VARIANT of a released pair is refused (the commit "
          "review's medium: the ledger dedup normalizes at the boundary "
          "— a casing variant can never bypass once-per-pair and spend "
          "another canary slot)")
    _fresh()
    ledger.write_text(json.dumps(
        {"day": datetime.now().astimezone().strftime("%Y-%m-%d"),
         "pairs": ["btc/usd"]}), encoding="utf-8")   # a PRE-change entry
    ok8, why8 = bs._tide_retune_releases(80.0, "BTC/USD")
    check(2.8, not ok8 and "already released" in why8,
          "a LOWERCASE entry persisted before the normalization still "
          "dedupes (the loader normalizes at read — the commit review's "
          "second medium: the casing boundary is the loader's, so old "
          "ledgers can never double-spend)")

    print("== 3. THE ROLLBACK TRIGGERS ==")
    _fresh()
    _setenv(TIDE_RETUNE_CANARY_PAIRS=None, TIDE_RETUNE_CANARY_CAP=None,
            TIDE_RETUNE_CANARY_UNTIL=None)
    bs._tide_retune_releases(80.0, "XRP/USD")   # the base slot is spent
    ok7, _w = bs._tide_retune_releases(80.0, "BTC/USD")
    check(3.1, not ok7 and "cap is spent" in _w,
          "ROLLBACK 1 — flipping the dial keys off (live, next tick) "
          "restores the base cap for everyone, canary pairs included")
    _fresh()
    _setenv(TIDE_RETUNE_CANARY_UNTIL=(datetime.now().astimezone().date()
                                      - timedelta(days=1)).isoformat())
    bs._tide_retune_releases(80.0, "XRP/USD")
    ok8, _w = bs._tide_retune_releases(80.0, "BTC/USD")
    check(3.2, not ok8,
          "ROLLBACK 2 — the time box expires the canary by itself (a "
          "canary that outlives its mandate is not a canary)")
    _fresh()
    _setenv(TIDE_RETUNE_CANARY_UNTIL="not-a-date")
    bs._tide_retune_releases(80.0, "XRP/USD")   # the base slot is spent
    ok9, _w = bs._tide_retune_releases(80.0, "BTC/USD")
    check(3.3, not ok9,
          "an unparseable time box expires the canary too (fail-CLOSED "
          "on an expansion knob, always)")
    _fresh()
    _setenv(TIDE_RETUNE_CANARY_UNTIL=_until, TIDE_RETUNE_CANARY_CAP="0")
    bs._tide_retune_releases(80.0, "XRP/USD")   # the base slot is spent
    ok10, _w = bs._tide_retune_releases(80.0, "BTC/USD")
    check(3.4, not ok10,
          "a canary cap of 0 (or junk) is inert — a misconfigured "
          "canary can never narrow or break the base posture")
    _fresh()
    _setenv(TIDE_RETUNE_CANARY_CAP="2", TIDE_RETUNE_DAILY_CAP="0")
    ok11, _w = bs._tide_retune_releases(80.0, "BTC/USD")
    check(3.5, not ok11,
          "the canary cannot override an operator OFF (base cap 0 stays "
          "0 for every pair — the canary only ever widens a LIVE cap)")
    _setenv(**_CANARY_ENV)

    print("== 4. THE MIRROR SPENDS NOTHING ==")
    _fresh()
    okc, _w = bs._tide_retune_releases(80.0, "BTC/USD", consume=False)
    check(4.1, okc and not _pairs(),
          "the QC mirror consults the same doors with consume=False — a "
          "passing consult never spends a slot (the alert's own release "
          "will; the mirror must agree, never double-spend)")

    print("== 4b. THE JOURNAL LEAK GATE ==")
    import tidebar_weekly as _tbw_leak
    _leak_before = None
    _live_journal = _tbw_leak.TIDE_RETUNE_HISTORY_FILE
    if _live_journal.exists():
        _leak_before = _live_journal.read_text(encoding="utf-8")
    _fresh()
    bs._tide_retune_releases(80.0, "BTC/USD")     # a consuming release
    check(4.2, not _live_journal.exists()
          or _live_journal.read_text(encoding="utf-8")
          == (_leak_before or ""),
          "a consuming release under a SANDBOXED day ledger writes ZERO "
          "rows into the live journal (the journal derives from the "
          "ledger's own path — the leak gate; the first drive run "
          "polluted the repo-root journal and the memo draft caught it)")
    _can_journal = SANDBOX / "tide_retune_history.jsonl"
    check(4.3, _can_journal.exists()
          and len(_can_journal.read_text(encoding="utf-8").strip()
                  .splitlines()) == 1,
          "the consuming release journalled ONCE beside the sandboxed "
          "ledger (the reconciliation's data source, self-sandboxing)")
    _fresh()
    bs._tide_retune_releases(80.0, "BTC/USD", consume=False)
    check(4.4, not _can_journal.exists()
          or _can_journal.read_text(encoding="utf-8").strip() == "",
          "the mirror's consult journals NOTHING (consume=False never "
          "writes — the journal counts releases, not consultations)")

    print("== 5. THE MONITORING SURFACE (the reckoning names it) ==")
    import tidebar_weekly as tbw                 # noqa: E402
    tbw.RETUNE_DIAL_OVERRIDE = {
        "armed": True, "quick_min": 66.0, "cap": 1, "spent_today": 0,
        "canary_pairs": ["BTC/USD", "LTC/USD", "UNI/USD"],
        "canary_cap": 2, "canary_until": _until}
    rec = tbw._retune_reckoning(
        [(80.0, True, "2026-10-05T10:00:00-06:00"),
         (75.0, False, "2026-10-05T11:00:00-06:00")], 86.0, "PROBE")
    tbw.RETUNE_DIAL_OVERRIDE = None
    dial_line = (rec or {}).get("lines", ["", ""])[1]
    check(5.1, "canary 2/day for BTC/USD, LTC/USD, UNI/USD" in dial_line
          and ("until " + _until) in dial_line,
          "the reckoning's dial line names the canary scope, cap and "
          "time box — the weekly report is the canary's dashboard")
    check(5.2, (rec or {}).get("summary", {}).get(
        "dial", {}).get("canary_cap") == 2,
        "the reckoning summary carries the canary fields for the ops record")
    _all_lines = "\n".join((rec or {}).get("lines", []))
    check(5.3, "canary note" in _all_lines
          and "BASE cap" in _all_lines and "pair identity" in _all_lines,
          "with the canary armed, the reckoning NAMES the bound's "
          "limitation (it prices the BASE cap; the canary's allowance is "
          "additional and unpriced — the week join carries no pair "
          "identity, the day ledger reconciles the canary). A silently "
          "undercounted bound would cry SLOTS BIND the canary had "
          "already relieved — the commit review's medium")
    tbw.RETUNE_DIAL_OVERRIDE = {
        "armed": True, "quick_min": 66.0, "cap": 1, "spent_today": 0}
    rec0 = tbw._retune_reckoning(
        [(80.0, True, "2026-10-05T10:00:00-06:00")], 86.0, "PROBE")
    tbw.RETUNE_DIAL_OVERRIDE = None
    check(5.4, "canary note" not in "\n".join(
              (rec0 or {}).get("lines", [])),
          "without the canary the note is absent — the default report "
          "is byte-for-byte the shipped shape (every existing pin holds)")
finally:
    bs.TIDE_RETUNE_STATE_FILE = _real_state
    for k, v in _real_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(SANDBOX, ignore_errors=True)

print(f"RESULT: {passed} passed, {failed} failed")


def main():
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
