"""ENTRY TIMING AS A DECISION (Nov 15).

`entry_quality` is recorded on every fire. This module turns that recording into
a decision the bracket can make — and, while the flag is off, into a SHADOW
verdict that says what the decision would have been, so a study can grade it
against realized closes without a single order moving.

THE FLAG. `config.TradingConfig.entry_timing_rank` (default False). Off, the
bracket behaves exactly as it did: the champion is whoever won the tournament,
and the verdict is only written down. On, the champion may be swapped for a
candidate that entered materially lower in its prior-hour range, provided that
candidate's signal confidence is within ENTRY_TIMING_CONF_WINDOW points — the
same "as good a signal" tolerance the existing per-match tiebreak uses. It never
promotes a materially weaker signal: the earlier diagnosis (1,106 entries) found
timing only redraws WHO wins among equals, and a gate on timing alone returned
-$1.54 held-out expectancy.

THE VETO IS SHADOW-ONLY. ENTRY_TIMING_VETO marks a champion bought in the top of
its range with no better alternative on offer. It is recorded, never enforced,
for the same reason: the measured gate (eq <= 0.2) cost money out of sample.

No order, stop, exit price or death-line rule reads this module. Only the
tournament's choice of champion does, and only while the flag is on.
"""
from __future__ import annotations

# Bump when the verdict's keys or meaning change: an older verdict is then
# identifiable instead of silently reinterpreted. 1 = first shadow verdict.
ENTRY_TIMING_STAMP_VERSION = 1

# "As good a signal": confidence may be this many points below the champion's.
ENTRY_TIMING_CONF_WINDOW = 10.0

# How much lower the rival must have entered before the bracket is allowed to
# prefer it. Below this the swing is noise, not a different entry.
ENTRY_TIMING_MIN_GAIN = 0.15

# A champion at or above this fraction of its prior-hour range was bought near
# the top. Recorded as `veto`, never acted on.
ENTRY_TIMING_VETO = 0.85


def _num(v) -> float | None:
    """A real number or None — bools and strings are not measurements."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def resolve(row) -> tuple[float | None, str]:
    """The best timing number a stored row carries, and where it came from.

    `entry_quality` is the tournament's own number and wins when the row was
    stamped with it (`entry_quality_v` >= 1). Rows closed before that field
    existed carry `timing_eq` instead — the same idea rebuilt from candles at
    fire time — so a study can still place them. Nothing is guessed: a row with
    neither returns (None, "none") and must be excluded, not defaulted.
    """
    if not isinstance(row, dict):
        return None, "none"
    v = _num(row.get("entry_quality"))
    if v is not None and _num(row.get("entry_quality_v")):
        return v, "entry_quality"
    te = _num(row.get("timing_eq"))
    if te is not None:
        return te, "timing_eq"
    return None, "none"


def verdict(champion, results, flag_on: bool, *,
            window: float = ENTRY_TIMING_CONF_WINDOW,
            min_gain: float = ENTRY_TIMING_MIN_GAIN,
            veto: float = ENTRY_TIMING_VETO) -> dict:
    """What the entry-timing ranking decides about this champion.

    Pure: it reads the bracket's results and returns a record. `apply()` is what
    honours it, and only when the flag is on.

    `would` is the whole point of the shadow record:
      "hold"         — the champion is already the low entry among its equals
      "prefer"       — a rival within the confidence window entered >= min_gain
                       lower; with the flag on, that rival would fire instead
      "insufficient" — this champion has no timing number (too little history)
      "no_bracket"   — nothing to rank (a sell-only or empty bracket)
    """
    out = {"v": ENTRY_TIMING_STAMP_VERSION,
           "flag": "entry_timing_rank",
           "on": bool(flag_on),
           "would": "insufficient",
           "applied": False,
           "src": "none",
           "eq": None,
           "veto": False,
           "pool": 0}
    try:
        pool = []
        items = results.values() if hasattr(results, "values") else results
        for r in items:
            sig = getattr(r, "signal", None)
            if sig is None or getattr(sig, "action", "") != "buy":
                continue
            eq = _num(getattr(r, "entry_quality", None))
            if eq is None:
                continue
            pool.append((r, eq))
        out["pool"] = len(pool)

        eq_ch = _num(getattr(champion, "entry_quality", None))
        if eq_ch is not None:
            out["src"] = "entry_quality"
            out["eq"] = round(eq_ch, 4)
        if not pool:
            out["would"] = "no_bracket" if out["eq"] is None else "insufficient"
            return out
        if out["eq"] is None:
            return out

        conf = _num(getattr(champion.signal, "confidence", None)) or 0.0
        cands = [(r, eq) for (r, eq) in pool
                 if (_num(getattr(r.signal, "confidence", None)) or 0.0)
                 >= conf - float(window)]
        if not cands:
            cands = [(champion, eq_ch)]
        best_r, best_eq = min(
            cands, key=lambda t: (round(t[1], 6),
                                  -(_num(getattr(t[0].signal, "confidence", None)) or 0.0)))
        if best_r is champion or round(eq_ch - best_eq, 4) < float(min_gain):
            out["would"] = "hold"
            out["veto"] = bool(eq_ch >= float(veto))
            return out

        out.update({"would": "prefer",
                    "from": getattr(champion, "pair", ""),
                    "from_eq": round(eq_ch, 4),
                    "to": getattr(best_r, "pair", ""),
                    "to_eq": round(best_eq, 4),
                    "gain": round(eq_ch - best_eq, 4),
                    "applied": bool(flag_on)})
        return out
    except Exception:                                    # never break a scan
        out["would"] = "insufficient"
        return out


def apply(champion, results, config) -> tuple[object, dict]:
    """Run the verdict and honour it ONLY when the flag is on.

    Returns (champion_to_fire, verdict_record). With the flag off the champion
    is returned untouched, verdict recorded: live behaviour is byte-identical to
    before this module existed.
    """
    flag_on = bool(getattr(config, "entry_timing_rank", False))
    window = _num(getattr(config, "entry_timing_window", None))
    gain = _num(getattr(config, "entry_timing_min_gain", None))
    rec = verdict(champion, results, flag_on,
                  window=(ENTRY_TIMING_CONF_WINDOW if window is None else window),
                  min_gain=(ENTRY_TIMING_MIN_GAIN if gain is None else gain))
    if rec.get("applied") and rec.get("to"):
        try:
            chosen = results[rec["to"]]
        except Exception:
            rec["applied"] = False
            return champion, rec
        rec["eq"], rec["src"], rec["eq_before"] = rec.get("to_eq"), "entry_quality", rec.get("eq")
        return chosen, rec
    return champion, rec
