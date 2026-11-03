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
# THE AUTO-ADJUST CANARY READS ITS OWN FILE (the two-file design, Sep
# 28): the cohort marker lives in auto_adjust_canary.json — arming or
# rolling back the cohort never touches the exit-extension dial file,
# so a cohort operation can never silently disable (or re-enable) the
# feature. COHORT_ENV is the sandbox seam (the STATE_ENV pattern). Must
# match canary_runner.COHORT_FILE (pinned in the drive).
AA_COHORT_ENV = "AUTO_ADJUST_CANARY_FILE"
AA_COHORT_FILE = "auto_adjust_canary.json"


def _aa_cohort() -> str:
    """The armed cohort id, '' = disarmed: env first (sandbox-safe),
    then the cohort file. Exit-extension dials are NEVER consulted."""
    explicit = os.environ.get(AA_COHORT_ENV)
    path = Path(explicit) if explicit else dial_file().parent / AA_COHORT_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and str(doc.get("cohort_id", "") or ""):
            return str(doc["cohort_id"])
    except (OSError, ValueError):
        pass
    return ""


def _aa_params() -> dict:
    """The armed cohort's SELECTED CELL (Sep 29, the review's major):
    alpha/delta_max/daily_cap the gate picked and flip_canary
    persisted — ALL THREE ride (the cap is part of the cell: an armed
    0.02 daily cap must bite, not fall back to the module default).
    Empty dict = the module defaults ride (a legacy cohort without
    params — the pre-seam armed cohort keeps evaluating as it always
    has). Env-first like the cohort id (the drive's sandbox seam)."""
    explicit = os.environ.get(AA_COHORT_ENV)
    path = Path(explicit) if explicit else dial_file().parent / AA_COHORT_FILE
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        p = doc.get("params") if isinstance(doc, dict) else None
        if isinstance(p, dict):
            return {k: float(v) for k, v in p.items()
                    if k in ("alpha", "delta_max", "daily_cap")
                    and isinstance(v, (int, float))}
    except (OSError, ValueError, TypeError):
        pass
    return {}
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


# ── THE AUTO-ADJUST CANARY HOOK (Sep 28): the propose path rides the
# rotation boundary — each SILENT rotation evaluates itself through
# hindsight_api's full rails (concentration gate, daily cap, evidence
# bundle, ledger) and lands ONE spec-shaped telemetry event. Gated
# behind the canary dial (the runner's own AA_CANARY_KEY — never the
# feature master), watermarked so a restart cannot double-evaluate.
def aa_apply_silent_rotation(
        rot_key: str, goal_pct: float | None = None,
        supp_path=None, cache_path=None) -> dict:
    from per_gate_export import DEFAULT_GOAL_PCT as _DP
    if goal_pct is None:
        goal_pct = float(_DP)
    # THE GATE (the cohort's OWN file — the machine stays OFF for the
    # book while the cohort is armed; the exit-extension dials are
    # never consulted, so this path is structurally independent of the
    # feature master)
    cohort = _aa_cohort()
    if not cohort:
        return {"evaluated": False, "reason": "canary_dial_off"}
    # THE WATERMARK: scanner_state.json carries the last EVALUATED
    # rotation stamp (bare window, matching the journal's own shape) —
    # one evaluation per rotation across restarts and cadence ticks.
    import background_scanner as _bs
    _st = _bs._load_scanner_state()
    if _st.get("aa_canary_last_rotation") == rot_key:
        return {"evaluated": False, "reason": "already_evaluated"}
    # THE EVALUATION (task 3's propose path, the journal's own rot id):
    # every rail live — the concentration gate, the daily cap, the
    # evidence bundle, the append-only ledger — and ONE telemetry event
    # (apply_action emits it; the hook NEVER double-emits). The hook
    # calls the propose path DIRECTLY: machine-internal, gated by the
    # canary dial — the operator token gates the WIRE, not the
    # machine's own boundary. applied_by rides the non-secret
    # 'rotation_boundary' label so telemetry can tell a boundary
    # decision from an operator or wire one.
    import hindsight_api as _ha
    try:
        supp, cache = _ha._paths(supp_path, cache_path)
        agg = _ha._rotation_aggregate(rot_key, goal_pct, supp, cache)
        rec = _ha.apply_action(
            rot_key, "auto_apply", confirm=True, goal_pct=goal_pct,
            cohort=cohort, operator_id="rotation_boundary",
            supp_path=supp_path, cache_path=cache_path,
            params=_aa_params())
    except Exception as exc:
        return {"evaluated": False, "reason": f"propose_error: {exc!r}"}
    # THE WATERMARK LANDS LAST — a failed evaluation leaves no stamp
    # and the next tick retries the whole path.
    try:
        _st2 = _bs._load_scanner_state()   # re-read: never clobber keys
        _st2["aa_canary_last_rotation"] = rot_key
        _bs._save_scanner_state(_st2)
    except Exception:
        pass
    # THE OPERATOR'S PROJECTION (Sep 30): where the machine's
    # calibration is heading. The hook evaluates the live rotation's
    # own B/P anyway — the same aggregate feeds the walk projection the
    # bot's learning plate renders beside the GF line (one walk, two
    # consumers: the projection is evaluate()'s own mechanics via the
    # shared _walk_factor). Merge-saved through profit_guard._save:
    # two writers share the state file and the overlay must never
    # erase the factor, the history, or the ledger. Fail-soft: a broken
    # projection never un-books an evaluated rotation.
    try:
        from canary_runner import gf_walk_projection as _gfwp
        import profit_guard as _pg_proj
        _pg_proj._save({"gf_projection": _gfwp(
            [{"B": agg["B"], "P": agg["P"], "cycle": 0,
              "rot": rot_key}],
            params=_aa_params())})
    except Exception:
        pass
    return {"evaluated": True, "status": rec.get("status"),
            "reason": rec.get("reason"), "cohort": cohort,
            "B": agg["B"], "P": agg["P"]}


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
# THE LOCK-WINDOW FLOOR ON THE ACTIVE WINDOW (Sep 29, the textless
# major from the review stream, VERIFIED + CLOSED): the ACTIVE-WINDOW
# branch used to suppress unconditionally — precedence said "the ride
# must be allowed to approach its goal". But the trail the ride hides
# under is price-floored at entry + 2x RT only on the ARM path's goal
# guard; on the ACTIVE path a book whose PEAK ran past the armed cap's
# lock window had a trailing stop ABOVE its window floor — the breach
# (a PROFIT GUARD FIRED reason) arrived with real-profit protection
# in hand and was suppressed anyway, bouncing the exit until the
# window lapsed. THE FIX: a breach that carries net-positive
# protection in hand (peak above the entry + 2x RT lock level, entry
# and peak readable) EXITS NOW — the window keeps riding approaches
# that never locked profit (the peak still under the lock level:
# nothing to protect yet, exactly the ride the study measured) and
# loses only the power to hold a book PAST its own locked floor.
# Below the line it is the same triangle as the study's floor guard:
# a verified or corrupt stamp both revert to the machinery.
def _lock_level(entry: float) -> float:
    """entry + 2x the round-trip cost — the net-positive floor the
    goal-armed trail prices its stops against (the scanner's own
    shape; the constant is the platform's)."""
    from contest_account import COST_ROUNDTRIP_PCT as _rt
    return entry * (1.0 + 2.0 * _rt / 100.0)


def _hard_stop_live(w: dict, live: float | None) -> tuple[bool, float]:
    """THE DEATH-LINE JUDGE (the recovered 15:18 ordering major): the
    window can never hold a book past its hard stop — computed HERE
    from entry/size/live and the platform buffer's own half (the gate
    1b shape), independent of any reason string. Fail-soft toward the
    ride: an unreadable buffer keeps the window (the scanner's own
    independent judge is the second net; a seam failure degrades to
    the incumbent exit anyway)."""
    try:
        entry = float(w.get("entry") or 0.0)
        size = float(w.get("size") or 0.0)
        lv = float(live or 0.0)
        if entry <= 0 or size <= 0 or lv <= 0:
            return False, 0.0
        import contest_account as _ca_hs
        _stop = max(_ca_hs.buffer(_ca_hs.load_state()) * 0.5, 5.0)
        _usd = (lv / entry - 1.0) * size
        return ((True, _usd) if _usd <= -_stop else (False, _usd))
    except Exception:
        return False, 0.0


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
                # THE DEATH-LINE JUDGE (the 15:18 ordering major): gate
                # 1b runs only `if not should_exit`, so on a book past
                # its peak the PROFIT GUARD reason is already set while
                # price sinks through the stop — the reason never
                # becomes STOPPED OUT and _NEVER_MARKS never applies.
                # The judge rides no reason: the window cannot hold a
                # book past its hard stop, whatever the caller says.
                _hs_b, _hs_usd = _hard_stop_live(w, live)
                if _hs_b:
                    _clear_stamp(w)
                    return "exit", (
                        f"STOPPED OUT — down ${abs(_hs_usd):.0f} past the "
                        "death line — the hard stop outranks every window "
                        "(bypassed — the death line outranks every window)")
                # THE LOCK-WINDOW FLOOR ON THE ACTIVE WINDOW (the textless
                # major, closed Sep 29): an exit arriving while the window
                # rides is suppressed UNLESS the reason is a trail breach
                # with net-positive protection already locked in. The
                # breach facts ride the reason string (peak % and live %
                # off entry — the scanner's own PROFIT GUARD shape); the
                # guard fires only when the PEAK is above the entry + 2x
                # RT lock level, i.e. the armed cap's price floor is
                # above the window floor and the suppression would hold
                # a book PAST its own locked profit. An unreadable
                # reason (any parse failure) suppresses: the active
                # window stays the ride it was armed to be, and the
                # lapse path reverts to the machinery with the full
                # receipt — fail toward the FEATURE, never silently
                # toward the exit.
                _pk, _lv = _breach_facts(str(reason), w)
                if _pk is not None and _lv is not None and _lv < _pk:
                    try:
                        entry = float(w.get("entry"))
                        if 0.0 < entry < float("inf") \
                                and _pk / 100.0 >= _lock_level(entry) / entry - 1.0:
                            _clear_stamp(w)
                            return "exit", (
                                "trail breach below the locked floor — the "
                                "window cannot hold a book past its own "
                                "net-positive stop (incumbent machinery "
                                "resumes)")
                    except (TypeError, ValueError):
                        pass   # unreadable entry: suppress, as armed
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
            stamp = datetime.fromisoformat(added)
            # THE NAIVE STAMP IS HOUSE TIME (the recurring review minor,
            # closed Sep 28): the writer stamps naive-local, so judging
            # it against aware `now` raises TypeError (aware - naive)
            # and the row SKIPPED the age gate on the exception path —
            # a fresh book could arm. A naive stamp is assumed to be
            # the machine's own zone; only then is the age judged.
            if stamp.tzinfo is None:
                # house time is America/Denver (the journal and every
                # dial writer stamp MST); ZoneInfo names it explicitly
                # instead of inheriting now.tzinfo (the review's minor:
                # an imaginary `now` would poison the age math)
                from zoneinfo import ZoneInfo
                stamp = stamp.replace(tzinfo=ZoneInfo("America/Denver"))
            age_min = (now - stamp).total_seconds() / 60.0
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
        # THE CAP FAILS CLOSED AND THE STAMP GOES WITH IT (the isolated
        # review's follow-up, Sep 28): a save that lost the counter
        # would read 0 forever and let the dial blanket the book — and
        # a stamp left on the row would let the NEXT verdict ride a
        # window the state no longer remembers. Roll the count back,
        # strip the half-written stamp, and raise — the seam's outer
        # except turns it into the fail-safe EXIT verdict: no arm, no
        # stamp, the book keeps the incumbent machinery.
        days[key] = int(days.get(key) or 1) - 1
        w.pop("exit_extension", None)
        raise OSError("extension counter save failed — cap fails closed")


def _clear_stamp(w: dict) -> None:
    w.pop("exit_extension", None)


import re as _re_mod2
_BREACH_RE = _re_mod2.compile(
    r"dropped\s+([0-9]+(?:\.[0-9]+)?)%\s+from the peak")


def _breach_facts(reason: str, w: dict):
    """(peak_gain_pct, live_gain_pct) from a PROFIT GUARD breach reason —
    peak % from the reason's own stamp, live % derived from the peak
    and the trail width the reason itself reports (the scanner writes
    both numbers into the sentence). Returns (None, None) when the
    reason is not a priced trail breach — every non-breach exit and
    every malformed sentence reads as (None, None) and the active
    window suppresses as it always has."""
    r = str(reason or "")
    m = _BREACH_RE.search(r)
    if not m:
        return None, None
    try:
        dropped = float(m.group(1))          # peak minus live, in % of peak
        trail_m = _re_mod2.search(r"trail ratcheted to ([0-9]+(?:\.[0-9]+)?)%", r)
        trail = float(trail_m.group(1)) if trail_m else dropped
        peak_pct = dropped + trail           # the peak's % gain over entry
        live_pct = peak_pct - dropped        # == trail, by the same sentence
        return peak_pct, live_pct
    except (TypeError, ValueError):
        return None, None


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
def extension_trail_pct(entry: float, high: float, goal_pct: float,
                        armed: bool, gf: float | None = None,
                        endgame: float | None = None) -> float:
    """The width the EXTENDED book is guarded with — asserted equal to
    the incumbent ENFORCED shape (the scanner's trail block, not the
    bare shape): the ratchet SCALED BY THE FINALIZED GUARD FACTOR (and
    the end-game multiplier on the ratchet) while unarmed; once armed,
    the half-goal cap scaled by the SAME factor and the ratchet-and-cap
    min() then narrowed by the UNSCALED lock-window width whose implied
    stop sits at entry + 2x RT — the floor binds under EVERY factor,
    and the factor decides WHERE the width binds (at GF=2 the enforced
    width is 2x the neutral one, stop 100.88 vs 102.44 at entry 100 /
    peak 104 — the neutral shim proved a stop the machine never
    enforced; the factor ran 0.9168-2.0 across Sep 29's rotations).

    gf/endgame ride the machine's OWN finalized values; None keeps the
    factor-neutral 1.0 posture for callers that cannot read the
    machine's context (the studies)."""
    import background_scanner as bs   # the incumbent, imported late + lazy
    _gf = 1.0 if gf is None else float(gf)
    _eg = 1.0 if endgame is None else float(endgame)
    ratchet = bs._ratchet_trail_pct(entry, high, 0.0)
    if _gf != 1.0:
        ratchet = round(ratchet * _gf, 3)
    if _eg != 1.0:
        ratchet = round(ratchet * _eg, 3)
    if not armed:
        return ratchet
    half_goal_cap = max((goal_pct or 0.0) * bs.GOAL_TRAIL_ARM_FRAC, 1.0)
    if _gf != 1.0:
        half_goal_cap = round(half_goal_cap * _gf, 3)
    # THE LOCK-WINDOW CAP (the round-5 review: my round-3 notch was
    # ineffective — min(ratchet, wider-cap) keeps the violating ratchet
    # width). The scanner's own shape: the cap narrows to the width
    # whose implied stop sits at entry + 2x RT — the net-positive floor
    # — exactly like background_scanner's goal-armed trail does.
    if high > entry:
        try:
            from contest_account import COST_ROUNDTRIP_PCT as _rt
            _lock_w = max(0.0, (1.0 - entry * (1.0 + 2.0 * _rt / 100.0)
                                / high) * 100.0)
            if _lock_w > 0:
                half_goal_cap = min(half_goal_cap, round(_lock_w, 3))
        except Exception:
            pass   # the cost constant is unavailable — cap stays as-is
    return min(ratchet, half_goal_cap)


def armed_stop_level(entry: float, high: float, goal_pct: float,
                     gf: float | None = None,
                     endgame: float | None = None) -> float:
    """The armed stop PRICE the parity width implies (peak-percent under
    the high) — the number the naked-hold mutant test asserts is ABOVE
    the entry-protective floor. Percent off the peak, like the scanner.
    gf/endgame pass through for the ENFORCED shape (see
    extension_trail_pct); the assertion is honest only under the factor
    the machine actually rides."""
    width = extension_trail_pct(entry, high, goal_pct, armed=True,
                                gf=gf, endgame=endgame)
    return high * (1.0 - width / 100.0)
