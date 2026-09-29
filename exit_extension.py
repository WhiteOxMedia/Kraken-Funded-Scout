"""exit_extension.py — THE PROTECTED 24H EXTENSION (Sep 27).

The goal-reach study (_goalreach_24h.py, committed 2a100c6) found the one
result in the exit stack that points TOWARD a change: cut books held to
goal-or-24h net +140.16 (cut cohort +173.09) — but the NAKED hold the
study simulated has a fat tail (worst book -14.25% below the cut price).
So the implementable form is NOT the study's rule; it is this module:

  A cut-class exit (profit guard / hard stop / trend bar / climax) does
  not close the book when the operator's dial is ON. Instead the watch
  row is STAMPED with an extension window (default 24h) and the position
  keeps riding UNDER THE INCUMBENT TRAIL — the same ratchet, the same
  half-goal armed cap, the same price floor the scanner already enforces
  every tick. When the window lapses, the stamp is removed and the NEXT
  exit verdict fires through the normal machinery with a full receipt.

What this module deliberately does NOT do:
  - it never exits anything (the clock never exits — Sep 21 rule); it
    only delays and stamps. Expiry REVERTS to the incumbent machinery.
  - it never arms a book whose fill sits at/above 98% of the goal level
    (Sep 28, the acceptance gate's measured precondition: arming a PAID
    book re-holds a position that already reached its net — the leak the
    gate caught at ctrl -15.08). A cut with room left to reach the goal
    is the only thing this module ever holds.
  - it never widens protection. The trail functions here exist to prove
    parity with background_scanner's shapes and to let tests assert the
    guard survives; the scanner keeps using its own code path.
  - it never touches THESIS DEAD (a deliberate no-reentry signal is not
    a "cut" — holding past it would contradict the entry-control layer).

THE DIAL (SCHEDULER-style, the pool's pattern): settings live in the
environment, read LIVE on every decision (no import-time freeze — the
profit_guard._state_file lesson), default OFF. The operator flips
EXIT_EXTENSION_ENABLED=1 to arm; flipping it back to 0 expires every
active extension on the next scanner cycle (fail-safe to incumbent
behavior). No restart needed, no hidden state.

THE STATE (sandbox-safe): counters live in exit_extension_state.json
resolved beside the CURRENT state-file root at every access, never
frozen at import — a suite that rebinds EXIT_EXTENSION_STATE_FILE (or
sets it to a tempdir path) carries this module's writes with it. The
Sep 27 ledger-leak fix in contest_account.new_contest is the cautionary
tale this file was written under.

Run the drive:  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_exit_extension.py
"""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── the dials (LIVE reads, SCHEDULER-style) ──────────────────────────
ENABLED_KEY = "EXIT_EXTENSION_ENABLED"
HOURS_KEY = "EXIT_EXTENSION_HOURS"
DAILY_CAP_KEY = "EXIT_EXTENSION_MAX_PER_DAY"
AGE_GATE_KEY = "EXIT_EXTENSION_MIN_AGE_MIN"

DEFAULT_HOURS = 24.0
HOURS_MIN, HOURS_MAX = 1.0, 72.0        # the study's horizon, bracketed
DEFAULT_DAILY_CAP = 3                   # a runaway dial cannot blanket the book
DEFAULT_AGE_GATE_MIN = 30.0             # never extend a book cut minutes after add

# THE ARM PRECONDITION (Sep 28, hardcoded — a SAFETY gate like THESIS
# DEAD, deliberately NOT an operator dial): arm only when the live fill
# sits strictly below 98% of the goal level. Measured by the acceptance
# gate (_protected_extension_gate.py, commit 9f87266): the raw dial that
# re-holds paid books FAILS its bar (ctrl -15.08), the preconditioned
# form PASSES (ctrl +0.00, net +144.44 on 16/40 cuts). 2% of slack
# absorbs the TP-fill-vs-goal-level gap; at/above the line the book is
# already paid and the machinery's exit stands. Unverifiable fill or
# goal -> exit (fail-safe to the incumbent). Numeric edge, kept because
# it is the formula's honest shape: for goals under ~2.04% the 98% line
# sits BELOW entry itself, so a fill at entry fails — those books have
# less room than the slack band, and the incumbent exit stands.
ARM_FILL_SLACK = 0.98

STATE_ENV = "EXIT_EXTENSION_STATE_FILE"  # explicit sandbox hook

# THE OPERATOR SURFACE: the pool's SCHEDULER dials live in DB settings
# read live per tick — the operator flips them WITHOUT a restart. The
# scanner is a long-lived process with a fixed environment, so the same
# parity here needs a file: exit_extension.dial.json beside the state
# file, a flat {"EXIT_EXTENSION_ENABLED": "1", ...} map, read LIVE on
# every decision. Precedence: env var (tests/sandbox) > dial file
# (the operator) > defaults (OFF). No restart, no hidden state.
DIAL_FILE_NAME = "exit_extension.dial.json"


def dial_file() -> Path:
    return state_file().parent / DIAL_FILE_NAME


def _file_dials() -> dict:
    try:
        doc = json.loads(dial_file().read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def state_file() -> Path:
    """Resolved NOW, never at import (the profit_guard lesson): an env
    rebind moves the state file for every writer and reader that asks
    after the rebind — the sandbox carries the module's writes."""
    explicit = os.environ.get(STATE_ENV)
    if explicit:
        return Path(explicit)
    return ROOT / "exit_extension_state.json"


def _dial(key: str, default: str | None = None) -> str | None:
    """One dial lookup: env first (explicit, sandbox-safe), then the
    operator's dial file (live), then the default. Absence is meaning:
    the master ENABLED key absent everywhere = OFF."""
    if key in os.environ:
        return os.environ.get(key)
    fd = _file_dials()
    if key in fd:
        return str(fd.get(key))
    return default


def read_dials() -> dict:
    """The operator's knobs, read live and clamped. enabled is the
    fail-safe master: anything but exactly '1' means OFF."""
    enabled = str(_dial(ENABLED_KEY, "") or "").strip() == "1"
    try:
        hours = float(_dial(HOURS_KEY, "") or DEFAULT_HOURS)
    except (TypeError, ValueError):
        hours = DEFAULT_HOURS
    hours = max(HOURS_MIN, min(HOURS_MAX, hours))
    try:
        cap = int(float(_dial(DAILY_CAP_KEY, "") or DEFAULT_DAILY_CAP))
    except (TypeError, ValueError):
        cap = DEFAULT_DAILY_CAP
    cap = max(0, cap)
    try:
        age_gate = float(_dial(AGE_GATE_KEY, "") or DEFAULT_AGE_GATE_MIN)
    except (TypeError, ValueError):
        age_gate = DEFAULT_AGE_GATE_MIN
    age_gate = max(0.0, age_gate)
    return {"enabled": enabled, "hours": hours, "daily_cap": cap,
            "age_gate_min": age_gate}


# ── the cut-class gate ───────────────────────────────────────────────
_CUT_MARKS = ("PROFIT GUARD FIRED", "Trend broke",
              "Climax candle")
_NEVER_MARKS = ("THESIS DEAD",           # deliberate no-reentry: never a cut
                "STOPPED OUT")           # the hard stop (the platform
                                         # death-line) always wins — the
                                         # critical the isolated review
                                         # caught Sep 28: an active window
                                         # used to suppress the account-
                                         # killer stop itself (bf9c8da-era
                                         # classification, not the study's
                                         # measured population)


def is_cut_reason(reason: str) -> bool:
    """TRUE only for the cut-class exits the study measured. THESIS DEAD
    is a strategy verdict and STOPPED OUT is the hard stop (the platform
    death-line, URGENT push, 'get out and wait for the next one') — both
    always exit; neither is ever a dip to ride."""
    r = str(reason or "")
    if any(m in r for m in _NEVER_MARKS):
        return False
    return any(m in r for m in _CUT_MARKS)


# ── the state (day counter for the cap) ──────────────────────────────
def _load_state() -> dict:
    try:
        doc = json.loads(state_file().read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> bool:
    try:
        tmp = str(state_file()) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=2)
        os.replace(tmp, state_file())
        return True
    except OSError as exc:
        # counters are a safety dial, not a ledger — the CALLER decides
        # which side failure degrades to (the arm path fails CLOSED)
        print(f"[exit_extension] state save failed ({exc}) — "
              f"the caller fails closed", flush=True)
        return False


def _day_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _count_today(st: dict, now: datetime) -> int:
    return int((st.get("days") or {}).get(_day_key(now)) or 0)


# ── the one scanner seam ─────────────────────────────────────────────
def exit_verdict(pair: str, w: dict, reason: str,
                 now: datetime | None = None,
                 live: float | None = None) -> tuple[str, str]:
    """THE SEAM the scanner calls when should_exit is True.

    Returns ("exit", why) — run the incumbent exit machinery untouched —
    or ("suppress", why) — the exit call is set aside, the row rides
    under the incumbent trail inside the extension window.

    The precedence order IS the safety story:
      1. dial OFF            -> exit (fail-safe to the incumbent)
      2. THESIS DEAD / hard stop / non-cut -> exit (the gate never
         holds these — the account-killer stop outranks every window)
      3. active extension    -> suppress (the ride continues; it was
         armed under the same precondition, so no re-judgement — the
         ride must be allowed to approach its goal)
      4. fill at/above 98% of goal -> exit (arming a paid book is the
         measured leak; unverifiable fill/goal also exits)
      5. window lapse        -> exit (revert; the stamp is cleaned here)
      6. cut-class + gates   -> suppress + ARM (stamp the row)
    """
    now = now or datetime.now().astimezone()
    dials = read_dials()
    if not dials["enabled"]:
        return "exit", "dial off"
    if not is_cut_reason(reason):
        return "exit", "not a cut-class exit"

    ext = w.get("exit_extension") or {}
    if ext.get("until"):
        try:
            until = datetime.fromisoformat(str(ext["until"]))
            if now < until:
                return "suppress", "extension active until " + str(ext["until"])
            # the window lapsed: drop the stamp, revert to the machinery.
            # The row pops its own flag; the exit then proceeds and the
            # close journal becomes the receipt the study never had.
            _clear_stamp(w)
            return "exit", "extension window lapsed — incumbent machinery resumes"
        except (TypeError, ValueError):
            _clear_stamp(w)
            return "exit", "corrupt extension stamp — incumbent machinery resumes"

    # ── the arm path: gates before the stamp ────────────────────────
    # THE BELOW-GOAL PRECONDITION first (Sep 28, the gate's PASSING
    # form, hardcoded): judged on the ARM path only — a row already
    # riding its window was armed under this same gate, so it cannot be
    # a paid-at-goal book, and the ride needs no re-check (a ride must
    # be allowed to approach its goal).
    # THE GOAL GUARD (the entry guard's sibling, task 9B, Sep 28, red
    # first in the drive): the goal_pct side parsed with a RAW float() —
    # a non-numeric goal raised OUT of exit_verdict (an exception to the
    # caller, not a named verdict), and a NaN goal slipped every <= 0.0
    # comparison (NaN compares False) into goal_px = nan and an ARMED
    # window priced at nonsense. Positive-FINITE or refuse.
    try:
        goal_pct = float(w.get("goal_pct") or 0.0)
    except (TypeError, ValueError):
        goal_pct = 0.0
    # THE ENTRY GUARD (CodeRabbit minor on the isolated review, Sep 28,
    # fixed same day): the old check `not str(w.get("entry") or 0)` was
    # ALWAYS False — missing, None, and "" all became the string "0" —
    # so garbage reached float(w["entry"]) below and only the seam's
    # outer except made it fail safe (an exception verdict instead of a
    # named one). The entry is validated AS A NUMBER here: missing,
    # non-numeric, or non-positive rows cannot arm (goal_px would be
    # meaningless), and the ONE parsed value rides the goal-price calc
    # — no re-read, no second chance to disagree.
    try:
        entry = float(w.get("entry"))
    except (TypeError, ValueError):
        entry = 0.0
    # the chained positive-finite form: NaN fails `0.0 < x` (every
    # comparison with NaN is False), inf fails `x < inf` — both refuse
    # with the same named verdict as missing/garbage/negative
    if live is None or not (0.0 < goal_pct < float("inf")
                            and 0.0 < entry < float("inf")):
        return "exit", ("no goal level / no live fill to judge the arm "
                        "precondition — incumbent machinery resumes")
    goal_px = entry * (1.0 + goal_pct / 100.0)
    if live >= goal_px * ARM_FILL_SLACK:
        return "exit", (f"fill {live:.6g} is not below 98% of the goal "
                        f"level {goal_px * ARM_FILL_SLACK:.6g} — arming a "
                        "paid book is the leak the gate measured")
    added = str(w.get("added") or "")
    if added:
        try:
            age_min = (now - datetime.fromisoformat(added)).total_seconds() / 60.0
            if age_min < dials["age_gate_min"]:
                return "exit", f"book only {age_min:.0f}m old (age gate " \
                               f"{dials['age_gate_min']:.0f}m)"
        except (TypeError, ValueError):
            pass
    st = _load_state()
    if _count_today(st, now) >= dials["daily_cap"]:
        return "exit", f"daily extension cap {dials['daily_cap']} reached"
    if not str(w.get("pair") or pair):
        return "exit", "unnamed watch row"

    _arm(pair, w, reason, now, dials, st)
    return "suppress", (f"armed {dials['hours']:.0f}h protected ride "
                        f"until {w['exit_extension']['until']}")


def _arm(pair: str, w: dict, reason: str, now: datetime,
         dials: dict, st: dict) -> None:
    until = (now + timedelta(hours=dials["hours"])).isoformat()
    w["exit_extension"] = {
        "extended_at": now.isoformat(),
        "until": until,
        "hours": dials["hours"],
        "reason": str(reason)[:160],
        "pair": str(w.get("pair") or pair),
    }
    days = st.setdefault("days", {})
    key = _day_key(now)
    days[key] = int(days.get(key) or 0) + 1
    if not _save_state(st):
        # THE CAP FAILS CLOSED (the isolated review, Sep 28): a save
        # that silently lost the counter would read 0 forever and let
        # the dial blanket the book. Roll the count back and raise —
        # the seam's outer except turns it into the fail-safe EXIT
        # verdict: no arm, the book keeps the incumbent machinery.
        days[key] = int(days.get(key) or 1) - 1
        raise OSError("extension counter save failed — cap fails closed")


def _clear_stamp(w: dict) -> None:
    w.pop("exit_extension", None)


def expire_sweep(watch: dict, now: datetime | None = None) -> int:
    """Dial-OFF cleanup: drop every active stamp so the book reverts to
    the incumbent behavior on the next cycle. Rows are mutated in place
    (the scanner owns persistence); the return is the count dropped."""
    now = now or datetime.now().astimezone()
    dials = read_dials()
    if dials["enabled"]:
        return 0
    n = 0
    for w in (watch or {}).values():
        if isinstance(w, dict) and w.pop("exit_extension", None) is not None:
            n += 1
    return n


# ── trail parity (proof shims — the scanner keeps its own math) ──────
def extension_trail_pct(entry: float, high: float,
                        goal_pct: float, armed: bool) -> float:
    """The width the EXTENDED book is guarded with — asserted equal to
    the incumbent shapes: the ratchet while unarmed; once armed, the
    live half-goal cap max(goal/2, 1.0) — never wider, never naked."""
    import background_scanner as bs   # the incumbent, imported late + lazy
    ratchet = bs._ratchet_trail_pct(entry, high, 0.0)
    if not armed:
        return ratchet
    half_goal_cap = max((goal_pct or 0.0) * bs.GOAL_TRAIL_ARM_FRAC, 1.0)
    return min(ratchet, half_goal_cap)


def armed_stop_level(entry: float, high: float, goal_pct: float) -> float:
    """The armed stop PRICE the parity width implies (peak-percent under
    the high) — the number the naked-hold mutant test asserts is ABOVE
    the entry-protective floor. Percent off the peak, like the scanner."""
    width = extension_trail_pct(entry, high, goal_pct, armed=True)
    return high * (1.0 - width / 100.0)
