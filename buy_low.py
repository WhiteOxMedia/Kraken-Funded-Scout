"""THE CHAMPION RANKING — measured, not assumed (Nov 15).

The ranking started as "prefer the coin being bought cheap". That preference was
graded on real closes and on a chronological paired replay, and it is
anti-predictive: every cheap band loses net money and the cheap band is the
worst. A sweep of every timing signal the scanner already computes (see
`EDGE_STUDY`, and `scratch/entry_edge_study.py` for the method) found what does
survive an out-of-sample holdout: **position in the prior hour — the live
`entry_quality` — preferring the coin that has already moved UP inside its hour**
(+0.111% per trade on the holdout, 95% CI +0.017..+0.204, n=63; win 38.1% vs
31.7% for the coin the bracket actually fires). Day-cheapness does not clear.

So the ranking now prefers the measured signal, and the record names which
signal, with its out-of-sample numbers, every time it fires. Everything else is
unchanged: it acts only among near-equal confidence candidates, only after the
timing and spend rankings, and only when `config.buy_low_rank` is on (off).

THE NUMBER. `cheapness` still folds the deep read into 0..1 (0 = at the lows,
1 = extended) and is still recorded, because it is the baseline the study is
measured against and the journal should carry both:

    pos_hour = entry_quality                     (position in the prior hour)
    pos_day  = pos_in_day_pct / 100              (position in today's range)
    cheap    = 1 - mean(pos_hour, pos_day)       (whatever is available)

A coin with no `pos_hour` is not ranked — `insufficient`, never defaulted.

THE SHADOW. `verdict()` returns one record per fire: who was compared, their
score on the measured signal, which candidate was preferred, by how much, and
whether the flag let it change the champion. The record travels to the watch row
and the journal through the usual path, so the decision can be graded against the
close it did or did not take.

No exit, stop or death-line rule reads this module. Nothing here sizes an order:
the dollars still come from the guard, and `spend_capacity` keeps the guard's own
cap as its `cap_usd` — this ranking never prices capacity.
"""
from __future__ import annotations

# Bump when the record's shape or meaning changes.
BUY_LOW_STAMP_VERSION = 3

# THE SIGNAL THE RANKING ACTUALLY PREFERS, and its direction. Chosen by the
# out-of-sample study below, not by taste: the coin that has moved up most inside
# its prior hour tends to keep going; the coin that is "cheap" in its day range
# does not (it is cheap because it is still falling).
PREFERRED_SIGNAL = "pos_hour"
PREFERRED_DIR = 1          # +1: prefer the HIGHER value

# "As good a signal": confidence may be this many points under the champion's.
BUY_LOW_CONF_WINDOW = 10.0

# How much better on the measured signal (0..1 of range space) the rival must be
# before the bracket is allowed to prefer it. 0.15 is the same "materially
# different entry" bar the timing tiebreak uses.
BUY_LOW_MIN_GAIN = 0.15

# THE STUDY this ranking is measured against. Every field was produced by
# scratch/entry_edge_study.py over 157 chronological decision points (45 days,
# 6h apart, no lookahead), each candidate marked to the same 60-minute forward
# window from the same 5m tape, net of the journal's median 0.080% round trip,
# paired against the coin the bracket fired (highest confidence). The earlier
# 60% is the fit window, the later 40% an untouched holdout.
EDGE_STUDY = {
    "measured_at": "2026-11-15",
    "source": "scratch/entry_edge_study.py",
    "horizon_min": 60,
    "cost_pct": 0.080,
    "fit_n": 94,
    "oos_n": 63,
    "signal": PREFERRED_SIGNAL,
    "dir": "+",
    "fit": {"diff_pct": 0.1327, "ci": [0.0400, 0.2250]},
    "oos": {"diff_pct": 0.1108, "ci": [0.0174, 0.2042],
            "win_fired_pct": 31.7, "win_preferred_pct": 38.1,
            "mean_fired_pct": -0.277, "mean_preferred_pct": -0.166,
            "changed": 31},
    "baseline": {"signal": "cheap", "dir": "-", "fit_diff_pct": 0.1331,
                 "oos_diff_pct": 0.0866, "oos_ci": [-0.0140, 0.1871],
                 "clears": False,
                 "note": "the preference the ranking shipped with; it does not "
                         "clear the holdout and its cheap bands lose money"},
    "rejected": {"pos_day": {"oos_diff_pct": -0.0904},
                 "off_high_24h": {"oos_diff_pct": 0.0239},
                 "off_low_24h": {"oos_diff_pct": 0.0110},
                 "vol_24h": {"oos_diff_pct": -0.0288}},
    "also_clears": {"mom_2h": {"oos_diff_pct": 0.0938,
                               "oos_ci": [0.0019, 0.1858],
                               "note": "not used: pos_hour is the same signal "
                                       "the scanner already computes, with the "
                                       "tighter out-of-sample bound"}},
    "verdict": "accepted",
}


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def cheapness(spend, entry_quality=None) -> dict | None:
    """How cheap this candidate is right now, 0 = at the lows, 1 = extended.

    Reads the deep read (`spend`) for the day's range and the fire's own
    `entry_quality` for the prior hour. Returns None when there is nothing to
    measure, with the missing pieces named in `src`. `pos_hour` is also the value
    the ranking scores on (`PREFERRED_SIGNAL`).
    """
    parts, src = [], []
    pos_hour = _num(entry_quality)
    if pos_hour is None and isinstance(spend, dict):
        pos_hour = _num(spend.get("entry_quality"))
    if pos_hour is not None:
        parts.append(max(0.0, min(1.0, pos_hour)))
        src.append("hour")
    pos_day = None
    if isinstance(spend, dict):
        pct = _num(spend.get("pos_in_day_pct"))
        if pct is not None:
            pos_day = max(0.0, min(1.0, pct / 100.0))
            parts.append(pos_day)
            src.append("day")
    if not parts:
        return None
    cheap = 1.0 - (sum(parts) / len(parts))
    out = {"v": BUY_LOW_STAMP_VERSION,
           "cheap": round(cheap, 4),
           "pos_hour": None if pos_hour is None else round(pos_hour, 4),
           "pos_day": None if pos_day is None else round(pos_day, 4),
           "src": "+".join(src)}
    if isinstance(spend, dict):
        # context the decision was made in, recorded so a study can band it
        for k in ("day_range_pct", "dd_pct", "dd_empirical_pct",
                  "off_high_pct", "volume_24h_usd", "spread_pct"):
            if spend.get(k) is not None:
                out[k] = spend[k]
    return out


def _score(cs):
    """The value the ranking compares on: the measured signal, never a guess."""
    if not isinstance(cs, dict):
        return None
    return _num(cs.get(PREFERRED_SIGNAL))


def verdict(champion, results, flag_on: bool, *,
            window: float = BUY_LOW_CONF_WINDOW,
            min_gain: float = BUY_LOW_MIN_GAIN) -> dict:
    """Which near-equal candidate does the MEASURED signal prefer, and would it win?

    Pure. `results` is the bracket's ScanResults; each candidate's score is the
    signal named in `EDGE_STUDY` (position in the prior hour), read from the same
    deep read the fire already carries. One record per fire, whether or not the
    flag is on — the decision is graded later against the close it did or did not
    take. The record always names the signal and its out-of-sample numbers.
    """
    out = {"v": BUY_LOW_STAMP_VERSION, "flag": "buy_low_rank",
           "on": bool(flag_on), "would": "insufficient", "applied": False,
           "pool": 0, "compared": [],
           "signal": PREFERRED_SIGNAL,
           "signal_dir": "+" if PREFERRED_DIR > 0 else "-",
           "study": EDGE_STUDY}
    try:
        pool, others = [], []      # eligible buys / read-but-ineligible
        items = results.values() if hasattr(results, "values") else results
        for r in items:
            sig = getattr(r, "signal", None)
            if sig is None:
                continue
            cs = cheapness(getattr(r, "spend", None),
                           getattr(r, "entry_quality", None))
            if cs is None:
                continue
            sc = _score(cs)
            if sc is None:
                continue
            row = (r, cs, sc)
            if getattr(sig, "action", "") == "buy":
                pool.append(row)
            else:
                others.append(row)
        out["pool"] = len(pool)

        ch_cs = cheapness(getattr(champion, "spend", None),
                          getattr(champion, "entry_quality", None))
        ch_sc = _score(ch_cs) if ch_cs else None
        if ch_cs is None or ch_sc is None:
            out["would"] = "no_read" if not pool else "insufficient"
            return out
        out["champion"] = {"pair": getattr(champion, "pair", ""),
                           "score": ch_sc, "cheap": ch_cs["cheap"],
                           "src": ch_cs["src"]}
        if not pool:
            out["would"] = "no_read"
            return out

        conf = _num(getattr(champion.signal, "confidence", None)) or 0.0
        cands = [(r, cs, sc) for (r, cs, sc) in pool
                 if (_num(getattr(r.signal, "confidence", None)) or 0.0)
                 >= conf - float(window)]
        if not cands:
            cands = [(champion, ch_cs, ch_sc)]

        # WHAT IT COMPARED: every priceable candidate the deep read produced,
        # marked with whether it could actually be crowned, so a night with a
        # single eligible buy still yields a real comparison.
        def _row(t, eligible):
            (r, cs, sc) = t
            return {"pair": getattr(r, "pair", ""), "score": sc,
                    "cheap": cs["cheap"],
                    "conf": _num(getattr(r.signal, "confidence", None)),
                    "src": cs["src"],
                    "action": getattr(r.signal, "action", None),
                    "eligible": eligible}

        out["compared"] = sorted([_row(t, True) for t in pool]
                                 + [_row(t, False) for t in others],
                                 key=lambda x: -x["score"])[:6]
        out["eligible"] = len(pool)

        best_r, best_cs, best_sc = max(
            cands, key=lambda t: (round(PREFERRED_DIR * t[2], 6),
                                  _num(getattr(t[0].signal, "confidence", None)) or 0.0))
        gain = round(PREFERRED_DIR * (best_sc - ch_sc), 4)
        # THE PREFERENCE IS ALWAYS STATED: which coin it would rather own on the
        # measured signal, by how much, against which bar, and whether that is
        # enough to change the bracket.
        out["preferred"] = {"pair": getattr(best_r, "pair", ""),
                            "score": best_sc, "src": best_cs["src"]}
        out["gain_score"] = gain
        out["min_gain"] = round(float(min_gain), 4)
        out["would_change"] = bool(best_r is not champion
                                   and gain >= float(min_gain))
        if not out["would_change"]:
            out["would"] = "hold"
            return out
        out.update({"would": "prefer",
                    "from": getattr(champion, "pair", ""),
                    "from_score": ch_sc,
                    "to": getattr(best_r, "pair", ""),
                    "to_score": best_sc,
                    "gain": gain,
                    "applied": bool(flag_on)})
        return out
    except Exception:
        out["would"] = "insufficient"
        return out


def apply(champion, results, config):
    """Run the verdict and honour it only when `buy_low_rank` is on."""
    flag_on = bool(getattr(config, "buy_low_rank", False))
    window = _num(getattr(config, "buy_low_window", None))
    gain = _num(getattr(config, "buy_low_min_gain", None))
    rec = verdict(champion, results, flag_on,
                  window=(BUY_LOW_CONF_WINDOW if window is None else window),
                  min_gain=(BUY_LOW_MIN_GAIN if gain is None else gain))
    if rec.get("applied") and rec.get("to"):
        try:
            chosen = results[rec["to"]]
        except Exception:
            rec["applied"] = False
            return champion, rec
        rec["champion"] = rec.get("from")
        rec["score_before"] = rec.get("from_score")
        return chosen, rec
    return champion, rec
