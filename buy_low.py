"""BUY LOW, SELL HIGH — as a decision the bracket can make (Nov 15).

`entry_quality` says where the last close sat inside the prior hour's range.
The deep read says more: where price sits inside the day's range, how far it is
from its own recent high and low, and what its drawdown context looks like. This
module folds those into one cheapness number per candidate and lets the bracket
prefer the coin that is being bought LOW over one that is already extended —
among candidates whose signal is near-equal, after the timing and spend
rankings, and only when `config.buy_low_rank` is on.

THE NUMBER. `cheapness` is 0.0 at the lows and 1.0 at the highs:

    pos_hour = entry_quality                     (position in the prior hour)
    pos_day  = pos_in_day_pct / 100              (position in today's range)
    cheapness = 1 - mean(pos_hour, pos_day)      (whatever is available)

Both components are recorded, so a study can see which one did the work. A coin
with neither is not ranked — it is reported as `insufficient`, never defaulted to
0 (which would read as "the low" and quietly win every comparison).

THE SHADOW. `verdict()` returns one record per fire: who was compared, their
scores, which candidate was preferred, by how much, and whether the flag let it
change the champion. The record travels to the watch row and the journal through
the usual path, so the decision can be graded against the close it did or did
not take.

No exit, stop or death-line rule reads this module. Nothing here sizes an order:
the dollars still come from the guard, and `spend_capacity` keeps the guard's own
cap as its `cap_usd` — this ranking never prices capacity.
"""
from __future__ import annotations

# Bump when the record's shape or meaning changes.
BUY_LOW_STAMP_VERSION = 2

# "As good a signal": confidence may be this many points under the champion's.
BUY_LOW_CONF_WINDOW = 10.0

# How much cheaper (0..1 of range space) the rival must be before the bracket is
# allowed to prefer it. 0.15 is the same "materially different entry" bar the
# timing tiebreak uses, so the two never disagree about what counts as a move.
BUY_LOW_MIN_GAIN = 0.15


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def cheapness(spend, entry_quality=None) -> dict | None:
    """How cheap this candidate is right now, 0 = at the lows, 1 = extended.

    Reads the deep read (`spend`) for the day's range and the fire's own
    `entry_quality` for the prior hour. Returns None when there is nothing to
    measure, with the missing pieces named in `src`.
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


def verdict(champion, results, flag_on: bool, *,
            window: float = BUY_LOW_CONF_WINDOW,
            min_gain: float = BUY_LOW_MIN_GAIN) -> dict:
    """Which near-equal candidate is being bought cheapest, and would it win?

    Pure. `results` is the bracket's ScanResults; each candidate's cheapness is
    computed from its own deep read and entry quality.
    """
    out = {"v": BUY_LOW_STAMP_VERSION, "flag": "buy_low_rank",
           "on": bool(flag_on), "would": "insufficient", "applied": False,
           "pool": 0, "compared": []}
    try:
        pool = []          # eligible: a buy with a measurement
        _others = []       # read, priceable, but not eligible to be crowned
        items = results.values() if hasattr(results, "values") else results
        for r in items:
            sig = getattr(r, "signal", None)
            if sig is None:
                continue
            cs = cheapness(getattr(r, "spend", None),
                           getattr(r, "entry_quality", None))
            if cs is None:
                continue
            if getattr(sig, "action", "") == "buy":
                pool.append((r, cs))
            else:
                _others.append((r, cs))
        out["pool"] = len(pool)

        ch_cs = cheapness(getattr(champion, "spend", None),
                          getattr(champion, "entry_quality", None))
        if ch_cs is None:
            out["would"] = "no_read" if not pool else "insufficient"
            return out
        out["champion"] = {"pair": getattr(champion, "pair", ""),
                           "cheap": ch_cs["cheap"], "src": ch_cs["src"]}
        if not pool:
            out["would"] = "no_read"
            return out

        conf = _num(getattr(champion.signal, "confidence", None)) or 0.0
        cands = [(r, cs) for (r, cs) in pool
                 if (_num(getattr(r.signal, "confidence", None)) or 0.0)
                 >= conf - float(window)]
        if not cands:
            cands = [(champion, ch_cs)]
        # WHAT IT COMPARED (Nov 15): the priceable set the deep read produced —
        # every candidate with a cheapness, whatever its action — so a night
        # with a single eligible buy still yields a real comparison instead of
        # a one-item list. `eligible` marks the ones that could actually be
        # crowned, and the DECISION below still only ever prefers those.
        _seen = []
        for _r, _cs in pool:
            _seen.append({"pair": getattr(_r, "pair", ""),
                          "cheap": _cs["cheap"],
                          "conf": _num(getattr(_r.signal, "confidence", None)),
                          "src": _cs["src"],
                          "action": getattr(_r.signal, "action", None),
                          "eligible": True})
        for _r, _cs in _others:
            _seen.append({"pair": getattr(_r, "pair", ""),
                          "cheap": _cs["cheap"],
                          "conf": _num(getattr(_r.signal, "confidence", None)),
                          "src": _cs["src"],
                          "action": getattr(_r.signal, "action", None),
                          "eligible": False})
        out["compared"] = sorted(_seen, key=lambda x: -x["cheap"])[:6]
        out["eligible"] = len(pool)
        best_r, best_cs = max(
            cands, key=lambda t: (round(t[1]["cheap"], 6),
                                  _num(getattr(t[0].signal, "confidence", None)) or 0.0))
        gain = round(best_cs["cheap"] - ch_cs["cheap"], 4)
        # THE PREFERENCE IS ALWAYS STATED (Nov 15): a "hold" that does not say
        # which coin it would rather own, by how much, and against which
        # threshold is an opinion, not a record. These fields are additive;
        # `would`/`applied` still decide everything the flag ever acts on.
        out["cheapest"] = {"pair": getattr(best_r, "pair", ""),
                           "cheap": best_cs["cheap"], "src": best_cs["src"]}
        out["gain_cheap"] = gain
        out["min_gain"] = round(float(min_gain), 4)
        out["would_change"] = bool(best_r is not champion
                                   and gain >= float(min_gain))
        if not out["would_change"]:
            out["would"] = "hold"
            return out
        out.update({"would": "prefer",
                    "from": getattr(champion, "pair", ""),
                    "from_cheap": ch_cs["cheap"],
                    "to": getattr(best_r, "pair", ""),
                    "to_cheap": best_cs["cheap"],
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
        rec["champion"], rec["cheap_before"] = rec.pop("champion", None), rec.get("from_cheap")
        return chosen, rec
    return champion, rec
