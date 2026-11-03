"""evidence_ladder.py — THE EVIDENCE LADDER (Sep 19).

One place where every maturity bar in the fleet lives, with the
evidence definition that fills it. A dial may not speak until its
rung on this ladder is reached by the evidence the ladder names.

WHY THIS EXISTS: the maturity bars grew up in three files — the
growth gate (diag_chaseback: 10 graded turns, early speak at 8),
the cap tune (diag_chasecap: 20 graded turns), and the regime
stability gate (tidebar_weekly: 3 winning weeks to flip, 2 to flip
back, 4-week rolling windows). Three numbers with no shared home
means no reader can see the whole ladder at once, and a tune in one
file never reconciles against its neighbors. This module is the
single home: the consumers import their constants FROM HERE, so the
numbers cannot drift apart, and the ladder renders itself as text
for reports and the MASTER plate.

WHAT COUNTS AS EVIDENCE (the shared predicate):
  A GRADED TURN is a spoken chase turn in chase_turns.jsonl whose
  forecast horizon has closed and whose pair's deep store could
  price it. Each study prices its own world on that row —
  diag_chaseback: 4-bar ran/end on the pair's store; diag_chasecap:
  the same plus the zero-shift deferral window. The LADDER fixes
  only the BAR; the studies remain the only counters. There is
  deliberately no second counter here: two counters that can
  disagree are worse than one.

  A GRADED WEEK is an ISO week in the weekly tide ledger whose
  outcome bar and quantile baseline both priced (the fit's pooled
  edge exists). Stability rungs count consecutive such weeks.

Stdlib only — every module in the fleet may import this safely.
"""

from __future__ import annotations

from pathlib import Path

# ── the one ledger the turn rungs count from ──
CHASE_TURNS_FILE = Path("chase_turns.jsonl")
# ── the one ledger the week rungs count from (the weekly pass's
# own history file — the SAME file tidebar_weekly.HISTORY_FILE
# reads; the drift-guard pin in test_apptest holds the two names
# together so the ladder can never count a ledger the gate doesn't) ──
WEEKLY_LEDGER_FILE = Path("_tidebarweekly_history.jsonl")

# ── the turn rungs (graded chase turns) ──
TURN_BARS = {
    # The chase-watch widening's growth verdict: the watchlist earns a
    # GROW call at 10 graded turns. The early gate lets a verdict speak
    # at 8 when the watched cohort's ran-rate separation stays extreme
    # (the Sep 19 user rule: evidence, not eagerness — the separation,
    # not the count, is what justifies speaking early).
    "GROWTH_GATE": 10,
    "GROWTH_GATE_EARLY": 8,
    # The speak-frequency cap tune: 20 graded turns before the cap's
    # verdict may recommend a change. Frequency alone is never a gate —
    # this is the ONLY maturity bar the cap answers to.
    "CAP_MATURITY": 20,
}

# ── the week rungs (graded ISO weeks on the tide ledger) ──
WEEK_BARS = {
    # Regime stability, flip direction: a benched state flips back to
    # its fit after 3 consecutive winning weeks on the pooled edge.
    "FLIP_WEEKS": 3,
    # Regime stability, flip-back direction: a fit re-arms after 2
    # consecutive winning weeks out of its bench.
    "FLIP_BACK_WEEKS": 2,
    # The rolling evidence windows (reckoning clusters, verdict grades)
    # that price the dials once the rungs above are met.
    "ROLLING_WEEKS": 4,
}

# ── the row rungs (graded rows on the direction-grade ledger) ──
ROW_BARS = {
    # The sell-veto review: at 30+ graded sell rows the pooled verdict
    # has earned a recommendation on the plate — the net speaks without
    # a human noticing it first (Sep 19 user rule). Below the bar the
    # plate shows the wait honestly; the recommendation never fires on
    # a thin sample.
    "SELLVETO_REVIEW": 30,
}

# ── the event rungs (priced regime events on the flip ledger) ──
# A "priced event" is a real flip or flip-back whose aftermath the
# aftermath walk has priced in dollars — the same counter the sweeps'
# min_events already used, now living here so the ladder documents it
# and every consumer imports one number.
EVENT_BARS = {
    # The pool-dial and streak-dial raise proposals: 3 priced re-arms
    # minimum (tidebar_weekly.POOLMIN_DEF_EVENTS' whole life, promoted
    # to the ladder Sep 20 so the bar is one import away and the
    # scoreboard can render progress against it).
    "DIAL_EVENTS": 3,
}

# ── the sample rungs (graded rows in a hindsight ledger) ──
# These gate the MONTHLY STUDIES' aggregate verdicts: a study may
# describe any sample, but its VERDICT line — the part that says the
# machinery paid or bled — waits for enough graded rows to mean
# something. One graded row per fire/join; the studies count their
# own rows, the ladder owns the bar.
SAMPLE_BARS = {
    # The coin-floor delta study: the relief's fleet-wide net needs a
    # dozen graded reliefs before "THE DELTA IS MONEY" or "THE RELIEF
    # BLEEDS" may speak (below it, single reliefs dominate the sum).
    "COINFLOOR_DELTA": 12,
    # The rotation-outliver hindsight: the early-Get-Out gate's net vs
    # holding to the boundary — the whole point of the study — needs
    # 8 graded fires (below it one deep dip is the whole sample).
    "OUTLIVER_HINDSIGHT": 8,
    # The climb-out bridge's hindsight: paid-vs-cost on the bounce
    # bracket needs 8 graded closes for the same reason.
    "CLIMBOUT_BRIDGE": 8,
}

# ── evidence definitions, one line per rung (plate + report text) ──
_LADDER_TEXT = (
    "EVIDENCE LADDER — what each dial needs before it may speak",
    "  GROWTH GATE     >= 10 graded turns  (early speak at 8 when the "
    "watched cohort's ran-rate separation stays extreme)",
    "  CAP TUNE        >= 20 graded turns  (frequency is never a gate "
    "on its own; graded turns are the only maturity bar)",
    "  FLIP (benched)  >= 3 winning weeks  (consecutive, pooled edge "
    "vs the quantile baseline on graded weeks)",
    "  FLIP BACK (fit) >= 2 winning weeks (consecutive, after the bench)",
    "  SELLVETO REVIEW >= 30 graded sell rows (a direction-vetoed hour "
    "the replay graded as a brain SELL whose deep store priced it)",
    "  DIAL RAISES     >= 3 priced regime events (real flip-backs whose "
    "aftermath priced — the pool and streak dials' proposal bar)",
    "  STUDY VERDICTS  >= 12 graded reliefs (coin-floor delta), >= 8 "
    "graded fires (outliver, climb-out) before a monthly verdict "
    "speaks — the numbers always render, the verdict waits",
    "  EVIDENCE = a graded turn: a spoken chase turn whose horizon "
    "closed and whose deep store priced it (chase_turns.jsonl)",
    "  EVIDENCE = a graded week: an ISO week where both the outcome "
    "bar and the quantile baseline priced (weekly tide ledger)",
)


def ladder_lines() -> list:
    """The ladder as renderable lines — pure, no IO, pinned."""
    return list(_LADDER_TEXT)


def progress_bar(n: int, bar: int, width: int = 10) -> str:
    """THE SCOREBOARD BAR (Sep 20, user: "each dial's current graded
    turns/weeks against its bar, so the ladder reads as a scoreboard").
    A tiny ASCII progress bar — '#' per slot the evidence has filled,
    '-' for the rest, clamped at the bar (an over-mature rung renders
    full, never overflows). Pure, ASCII-only, pinned."""
    n, bar, width = int(n), int(bar), int(width)
    if width <= 0:
        return ""
    if bar <= 0:
        return "#" * width
    filled = min(width, max(0, round(width * min(n, bar) / bar)))
    return "#" * filled + "-" * (width - filled)


def mature(dial: str, evidence_n: int) -> bool:
    """Has a turn-rung dial reached its bar? Pure; for callers that
    want the bar as a predicate instead of a bare constant."""
    if dial not in TURN_BARS:
        raise KeyError(f"unknown turn dial: {dial}")
    return int(evidence_n) >= TURN_BARS[dial]


def weeks_mature(rung: str, evidence_n: int) -> bool:
    """Has a week-rung dial reached its bar? Pure."""
    if rung not in WEEK_BARS:
        raise KeyError(f"unknown week rung: {rung}")
    return int(evidence_n) >= WEEK_BARS[rung]


def rows_mature(rung: str, evidence_n: int) -> bool:
    """Has a row-rung dial reached its bar? Pure."""
    if rung not in ROW_BARS:
        raise KeyError(f"unknown row rung: {rung}")
    return int(evidence_n) >= ROW_BARS[rung]


def events_mature(rung: str, evidence_n: int) -> bool:
    """Has an event-rung dial reached its bar? Pure."""
    if rung not in EVENT_BARS:
        raise KeyError(f"unknown event rung: {rung}")
    return int(evidence_n) >= EVENT_BARS[rung]


def samples_mature(rung: str, evidence_n: int) -> bool:
    """Has a sample-rung study reached its bar? Pure."""
    if rung not in SAMPLE_BARS:
        raise KeyError(f"unknown sample rung: {rung}")
    return int(evidence_n) >= SAMPLE_BARS[rung]
