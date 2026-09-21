"""diag_chaseback.py — THE CHASE-COVERAGE STUDY (Sep 17).

WHY THIS EXISTS: the chase-setup tracker only watches pairs the mover
funnel REJECTED — and the funnel only deep-scans the top-15 runners
(24h gain >= MOVER_MIN_PCT = 5%, MOVER_TOP_N = 15). A pullback-and-turn
on a pair that was never a big enough runner is INVISIBLE to the alert:
never tracked, never spoken, never graded. This study prices that
blind spot on the deep candle stores: the tracker's exact state machine
(arm on rejection, 8h life, refresh ref, rising-EMA cross turn, 4%
rollover guard) replayed hour by hour across every contest store, each
setup graded forward through the popup contract (RAN = +2% MFE within
4 bars, end = the window close), priced in the chase convention (the
FULL CHASE_POS_USD position — the alert is not brain-sized).

The two worlds:
  CAUGHT  — the turn fired while the pair was tracked (the funnel had
            rejected it within its 8h life): the alert spoke.
  MISSED  — the same candle pattern (rising EMA9, cross from below,
            drop <= 4% from the recent high close) on an UNTRACKED
            pair: silence. The reference for an untracked pair is the
            rolling 8-bar max close (the pullback's origin) — the
            honest analog of the tracker's reject-hour ref_close.

Hermetic pins bound the replay (max_pairs, sandbox stores, injected
position); the study is pure candle math — no exchange, no strategy
calls — so it runs in minutes and can re-price itself monthly.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("chaseback")

BASE = Path(__file__).parent
OUT = BASE / "_chaseback_check.txt"
LEDGER = BASE / "_chaseback_monthly.jsonl"
CHASE_TURNS_JSONL = BASE / "chase_turns.jsonl"   # the widening's spoken turns (the lived feed)
STORES_GLOB = "surething_data/kraken/*_USD-1h.feather"

# the house chase convention (tidebar_weekly.CHASE_POS_USD): the alert
# speaks a full position — it is not brain-sized
POSITION_USD = 1055.0
POSITION_USD_FALLBACK = 1055.0
HORIZON = 4                 # the popup contract's own window (bars)
WARMUP = 60                 # EMA9 converges to <1% weight after ~60 bars

# the tracker's dials, mirrored from background_scanner (kept in sync
# by the source pins in test_option23.py)
CHASE_MAX_AGE_H = 8.0       # a tracked reject older than this is dead
CHASE_TURN_MAX_DROP_PCT = 4.0
MOVER_MIN_PCT = 5.0         # 24h gain that makes a coin a runner
MOVER_TOP_N = 15            # runners deep-scanned per pass
PULLBACK_LOOKBACK = 8       # rolling max close = the untracked pullback origin
RAN_PCT = 2.0               # tidebar_weekly.VERDICT_RAN_PCT

MONTHLY_PERIOD_S = 30 * 86400.0
FIRST_DELAY_S = 3600.0
# THE EVIDENCE LADDER (Sep 19): the bars live in evidence_ladder.py —
# every maturity gate in the fleet reads its rung from there.
from evidence_ladder import TURN_BARS as _EL_TURN_BARS
GROWTH_GATE_N = _EL_TURN_BARS["GROWTH_GATE"]  # graded [watchlist] turns before the growth verdict may speak
GROWTH_MILESTONE = BASE / "growth_milestone.json"   # the GROW call's date + dollars (Sep 19)
GROWTH_GATE_EARLY_N = _EL_TURN_BARS["GROWTH_GATE_EARLY"]  # THE EARLY GATE (Sep 19): the verdict may speak sooner
GROWTH_GATE_RATE_EDGE = 0.10  # early gate: run-rate edge over the pool = "extreme"
GROWTH_GATE_USD_MULT = 2.0    # early gate: end-move/turn must DOUBLE the pool's


def merge_milestone(prev: dict | None, *, verdict: str | None,
                    graded: int, ran: int, usd: float, gate_src: str | None,
                    first_ts: float | None, now: float,
                    position_usd: float,
                    gate: int = GROWTH_GATE_N) -> dict | None:
    """THE GROWTH MILESTONE (Sep 19, user: "when the growth gate hits 10
    graded turns, have the study record the verdict date and dollars so
    the plate can show how long the widening took to earn its grow
    call"). Pure state machine, idempotent:
      - no doc + the verdict speaks GROW (early or full) -> CREATE with
        earned_date, the gate that spoke, the widening's span (first
        turn -> earn), and the live dollars.
      - doc exists + graded >= gate + full_gate_date missing -> STAMP the
        full-evidence date (objective count, verdict-independent).
      - doc exists -> refresh the live stats only; earned_date and the
        first-turn anchor are immutable (history, not current mood).
    Returns the doc (or the refreshed prev), or None when the gate has
    not spoken GROW yet. Pinned hermetically."""
    # THE GUARD (probe-caught): below the EARLY threshold the verdict
    # cannot have spoken at all — reject. At 8-9 graded turns a GROW
    # verdict is legitimate (the early gate), so the creation path must
    # accept it; the old `graded < gate` guard killed exactly the
    # early-earn case this feature exists to record.
    if graded < GROWTH_GATE_EARLY_N and not prev:
        return None
    rate = (ran / graded) if graded else None
    usd_turn = (usd / graded) if graded else None
    days = (max(now - first_ts, 0.0) / 86400.0
            if (first_ts and now) else None)
    if not prev:
        if verdict != "GROW":
            return None
        return {"earned_date": time.strftime("%Y-%m-%d", time.gmtime(now)),
                "gate_src": gate_src or "full",
                "graded_at_earn": graded,
                "first_turn_ts": (time.strftime("%Y-%m-%d", time.gmtime(first_ts))
                                  if first_ts else None),
                "days_to_earn": (round(days, 1) if days is not None else None),
                "full_gate_date": (time.strftime("%Y-%m-%d", time.gmtime(now))
                                   if graded >= gate else None),
                "graded": graded, "ran": ran, "rate": (round(rate, 3)
                                                       if rate is not None else None),
                "usd_turn": (round(usd_turn, 2) if usd_turn is not None else None),
                "total_usd": round(usd, 2),
                "position_usd": round(position_usd, 2)}
    doc = dict(prev)
    if graded >= gate and not doc.get("full_gate_date"):
        doc["full_gate_date"] = time.strftime("%Y-%m-%d", time.gmtime(now))
    doc["graded"], doc["ran"] = graded, ran
    doc["rate"] = round(rate, 3) if rate is not None else None
    doc["usd_turn"] = round(usd_turn, 2) if usd_turn is not None else None
    doc["total_usd"] = round(usd, 2)
    doc["position_usd"] = round(position_usd, 2)
    return doc


def growth_gate_open(n_graded, pool_rate, pool_usd_turn, rate, usd_turn) -> bool:
    """THE EARLY GATE'S EVIDENCE RULE (Sep 19, user: "lower the growth
    gate to 8 graded turns if the rate separation stays extreme, and pin
    the evidence rule that would justify it"). The full evidence gate
    stays GROWTH_GATE_N — the early gate opens only on EXTREME
    separation, never merely good: the widening's graded ran-rate
    out-runs the pool's by GROWTH_GATE_RATE_EDGE and its end-move per
    turn at least DOUBLES the pool's (either edge alone is a hot
    streak; both together, sustained across every graded turn, is a
    regime). A good-but-not-extreme 8-turn sample still waits the full
    gate — 8 hot turns are exactly the hot streak the gate exists to
    outlast. Reads symmetric: a bleeder at double the pool's LOSSES is
    extreme in the bad direction and may speak SHRINK early — retiring
    seats early protects dollars the same way growing them earns them
    (a bleeder's ran-rate sits below the pool's by construction, so the
    rate edge cannot be its evidence). An empty pool is infinite
    separation: the rate threshold alone carries the gate."""
    if n_graded < GROWTH_GATE_EARLY_N:
        return False
    if rate is None:
        return False
    _pr = 0.0 if pool_rate is None else float(pool_rate)
    _pu = None if pool_usd_turn is None else float(pool_usd_turn)
    if (_pu is not None and _pu < 0 and usd_turn is not None
            and usd_turn <= _pu * GROWTH_GATE_USD_MULT):
        return True             # extreme in the bad direction — early SHRINK
    if rate < _pr + GROWTH_GATE_RATE_EDGE:
        return False
    if _pu is None or usd_turn is None:
        return True             # the pool prices nothing; the rate carries it
    return usd_turn >= _pu * GROWTH_GATE_USD_MULT


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _position_usd() -> float:
    """Today's ledger (the live contest size) with the study's own
    fallback — the family convention."""
    try:
        import background_scanner as _bs
        _s = float(_bs._contest_size())
        if _s > 0:
            return _s
    except Exception as exc:
        logger.debug("live contest size unreadable (%s) — the fallback "
                     "$%.0f stands", exc, POSITION_USD_FALLBACK)
    return POSITION_USD_FALLBACK


def _load_store(path):
    """One feather store -> (closes np array, highs, datetimes UTC)."""
    import numpy as _np
    import pandas as _pd
    df = _pd.read_feather(path)
    df["date"] = _pd.to_datetime(df["date"], utc=True)
    df = df.sort_values("date").reset_index(drop=True)
    return (_np.asarray(df["close"], dtype=float),
            _np.asarray(df["high"], dtype=float),
            _pd.DatetimeIndex(df["date"]))


# the sweep's quality rungs: a 24h-pct floor the watch could demand
# of an untracked turn before speaking
FLOOR_STEPS = (0.0, 1.0, 2.0, 3.0, 5.0, 7.5, 10.0)


def _epoch_s(idx) -> 'np.ndarray':
    """A UTC DatetimeIndex -> epoch SECONDS via the datetime64[s] cast.
    The naive astype('int64') is unit-ambiguous across pandas versions
    (this environment returned µs-scale ints, which made the 8h expiry
    unreachable and inflated every mover-list epoch — the smoke caught
    it before the study could lie)."""
    import numpy as _np
    return _np.asarray(idx.tz_convert(None).to_numpy()
                       .astype("datetime64[s]").astype("int64"))


def _fleet_movers_on_list(closes_by_pair, warmup: int) -> dict:
    """Hour-major fleet walk: {pair: {hour_i: bool}} — was the pair in
    the top MOVER_TOP_N by 24h pct (>= MOVER_MIN_PCT) at that hour?
    The 24h pct uses the honest smaller window near a store's start
    (base = the first close), the same convention as the scanner's
    _chase_pair_24h. Rank is computed across every pair with data at
    that hour-epoch (stores are hour-aligned UTC, so hours coincide)."""
    import numpy as _np
    # hour-epoch -> list of (pct, pair)
    per_hour = {}
    pct_by_pair = {}
    for pair, (closes, _h, idx) in closes_by_pair.items():
        n = len(closes)
        ep = _epoch_s(idx)
        pct = _np.full(n, _np.nan)
        for i in range(n):
            base = closes[i - 24] if i >= 24 else closes[0]
            if base > 0:
                pct[i] = (closes[i] - base) / base * 100.0
        pct_by_pair[pair] = (ep, pct)
        for i in range(warmup, n):
            if pct[i] == pct[i]:                       # not NaN
                per_hour.setdefault(int(ep[i] // 3600), []).append(
                    (float(pct[i]), pair))
    on_list = {pair: {} for pair in closes_by_pair}
    for ep_h, cands in per_hour.items():
        cands.sort(key=lambda t: -t[0])
        top = {p for _pct, p in cands[:MOVER_TOP_N]
               if _pct >= MOVER_MIN_PCT}
        if not top:
            continue
        for _pct, pair in cands:
            if pair in top:
                # first hour index whose epoch matches — stores are
                # unique per (pair, epoch) so a dict is safe
                on_list[pair][ep_h] = True
    return on_list, pct_by_pair


def _detect_setups(closes, highs, idx, on_hours, pct_arr,
                   warmup: int = WARMUP) -> list:
    """One pair's tracker state machine over its own candle grid ->
    [{ts_epoch_s, caught: bool, px, ran, end_pct}]. Mirrors the real
    machinery: arm/refresh on an on-list (rejection) hour, 8h life,
    rising-EMA9 cross turn, drop guard (tracked ref vs rolling-8 max
    for the untracked world). Every setup is graded forward from its
    own px on the SAME grid (the popup contract)."""
    import numpy as _np
    import pandas as _pd
    from indicators import ema as _ema
    n = len(closes)
    if n <= warmup + HORIZON + 2:
        return []
    ema_s = _ema(_pd.Series(closes, dtype=float), 9).to_numpy()
    ep = _epoch_s(idx)
    ep_h = ep // 3600                     # hour-epochs: the mover list's grid
    setups = []
    tracked = None            # {"first": s, "ref": px, "said": s}
    for i in range(warmup, n - HORIZON):
        ts_s = int(ep[i])
        if tracked and (ts_s - tracked["first"]) > CHASE_MAX_AGE_H * 3600.0:
            tracked = None                                  # stale — dead
        on = int(ep_h[i]) in on_hours
        if on:
            if tracked is None:
                tracked = {"first": ts_s, "ref": float(closes[i]),
                           "said": -1.0e18}
            else:
                tracked["ref"] = float(closes[i])           # refresh
        # the pattern: rising EMA + cross from below
        em, em_p, em_5 = ema_s[i], ema_s[i - 1], ema_s[i - 5]
        if not (em == em and em_p == em_p and em_5 == em_5):
            continue
        rising = em > em_5
        cross = closes[i] > em and closes[i - 1] <= em_p
        if not (rising and cross):
            continue
        px = float(closes[i])
        if tracked is not None:
            drop = (tracked["ref"] - px) / tracked["ref"] * 100.0
            caught = True
            tracked["said"] = ts_s
        else:
            ref8 = float(_np.max(closes[max(0, i - PULLBACK_LOOKBACK):i])) \
                if i >= 1 else px
            drop = (ref8 - px) / ref8 * 100.0 if ref8 > 0 else 0.0
            caught = False
        if drop > CHASE_TURN_MAX_DROP_PCT:
            continue                                        # rollover guard
        # grade forward on the same grid: the popup contract
        j_end = min(i + HORIZON, n - 1)
        fwd_high = float(_np.max(highs[i + 1:j_end + 1])) if j_end > i else px
        ran = bool((fwd_high / px - 1.0) * 100.0 >= RAN_PCT)
        end_pct = (float(closes[j_end]) / px - 1.0) * 100.0
        setups.append({"ts": ts_s, "caught": caught, "px": round(px, 8),
                       "ran": ran, "end": round(end_pct, 3),
                       "pct24": round(float(pct_arr[i]), 3)
                       if pct_arr is not None and pct_arr[i] == pct_arr[i]
                       else None,
                       "ema_slope": round((em - em_5) / px * 100.0, 4),
                       "pullback": round(drop, 3)})
    return setups


def _load_lived_turns(path=None, now=None, stores_dir=None) -> dict:
    """THE LIVED FEED (Sep 18): the widening's real catches — every
    [watchlist]-provenance turn in chase_turns.jsonl whose 4-bar
    horizon has closed, graded on its own deep store with the sim's
    exact convention (ran = +2% MFE on the highs, end-move at the
    close, full chase position). These rows NEVER fold into the
    caught/missed counterfactual (the sim already models those
    patterns across history — folding lived rows in would tax the
    same evidence twice); they price in their own world so the study
    re-prices the six watched coins with the widening in place.
    Fail-open: an unreadable row is skipped, a pending horizon is
    counted, nothing raises. Returns {"rows", "pending", "total"}."""
    import numpy as _np
    from datetime import datetime as _dt, timezone as _tz
    path = Path(path) if path is not None else CHASE_TURNS_JSONL
    now = time.time() if now is None else float(now)
    sdir = Path(stores_dir) if stores_dir is not None         else (BASE / "surething_data" / "kraken")
    rows, pending, total = [], 0, 0
    try:
        lines = path.read_text(encoding="utf-8",
                               errors="replace").splitlines()
    except OSError:
        return {"rows": rows, "pending": pending, "total": total}
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if "[watchlist]" not in str(d.get("why", "")):
            continue
        total += 1
        try:
            ts = _dt.fromisoformat(str(d["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
            ts = ts.timestamp()
            px = float(d["px"])
            pair = str(d.get("pair") or "")
        except Exception:
            continue
        if not pair or px <= 0:
            continue
        if now - ts < HORIZON * 3600.0:
            pending += 1                       # horizon still open
            continue
        base = pair.split("/")[0]
        sp = sdir / (base + "_USD-1h.feather")
        try:
            closes, highs, idx = _load_store(sp)
        except Exception:
            continue                           # no store: unpriceable
        ep = _epoch_s(idx)
        i = int(_np.searchsorted(ep, ts, side="right")) - 1
        if i < 0 or abs(int(ep[i]) - ts) > 3600:
            continue                           # no bar at the turn hour
        if i + HORIZON >= len(closes):
            pending += 1
            continue
        fwd_high = float(_np.max(highs[i + 1:i + HORIZON + 1]))
        ran = bool((fwd_high / px - 1.0) * 100.0 >= RAN_PCT)
        end = (float(closes[i + HORIZON]) / px - 1.0) * 100.0
        # the decision-time rung stamp (Sep 18): rows written before the
        # pct24 capture carry None and price in the ?pct bucket — never
        # invented from the store
        rows.append({"ts": int(ts), "caught": True, "px": round(px, 8),
                     "ran": ran, "end": round(end, 3), "pair": pair,
                     "pct24": d.get("pct24"), "lived": True})
    return {"rows": rows, "pending": pending, "total": total}


def _lived_weekly(rows, position_usd=POSITION_USD) -> dict:
    """THE LIVED-TREND FEED (Sep 19): the widening's graded turns
    bucketed by ISO week — the plate's sparkline series. Weeks are
    computed in UTC (the ledgers already stamp UTC) from each row's
    epoch ts; a week's usd prices the full chase position at the
    row's end-move, exactly the by_pair convention, so the weekly
    bars and the per-pair dollars can never disagree. Returns
    {"YYYY-Www": {"n", "ran", "usd"}} ascending by week; empty dict
    on empty input — fail-open like every other helper."""
    from datetime import datetime as _dtw, timezone as _utcz
    out = {}
    for _r in rows or []:
        try:
            _iso = _dtw.fromtimestamp(float(_r["ts"]),
                                      tz=_utcz.utc).isocalendar()
            _wk = f"{_iso[0]}-W{_iso[1]:02d}"
            _d = out.setdefault(_wk, {"n": 0, "ran": 0, "usd": 0.0})
        except (KeyError, TypeError, ValueError, OSError):
            continue
        _d["n"] += 1
        _d["ran"] += int(bool(_r.get("ran")))
        _d["usd"] = round(_d["usd"] + position_usd
                          * float(_r.get("end") or 0.0) / 100.0, 2)
    return dict(sorted(out.items()))


def _marginal_floor_bands(fs: dict) -> dict:
    """THE MARGINAL BANDS (Sep 19): a floor sweep prices cumulative rung
    worlds — rung dollars must never be added, and a raise's price is
    the band it DENIES. This helper derives the one true pricing object
    from a sweep. For a candidate floor C (None = no floor) and a rung
    F > C: band(C->F) = kept(C) - kept(F), where kept(None) is every
    pct-known setup (bucket(0) + the <0 bucket) and kept(F) = bucket(F).
    So the first band is the <0 bucket (what a floor at 0 denies vs no
    floor), the next is [0, F1), and so on — each band is exactly the
    setups the raise would exclude. THE CHOICE (the walk): start at no
    floor; walk the rungs ascending; move the candidate to a rung while
    its band BLEEDS (band usd < 0 — denying it strictly raises the kept
    world's average end-move); STOP at the first band that pays (a
    floor above it would deny real money). The chosen rung is the
    minimal speak-floor the data supports — the watch stays fat, the
    bleed at the bottom is priced out. Consumers: the unified widening
    policy (which speak-floor earns the seats' turns) and any dial that
    must price a raise's denial, not its keeping."""
    keys = [k for k in fs.get("buckets", {})
            if k not in ("<0",)]
    try:
        keys = sorted(keys, key=float)
    except (TypeError, ValueError):
        return {"bands": {}, "chosen": None, "why": "unsortable rungs"}
    bks = fs.get("buckets") or {}
    _zero = {"n": 0, "ran": 0, "usd": 0.0, "ceiling": 0.0}
    neg = bks.get("<0") or _zero
    k0 = bks.get("0") or _zero
    bands = {}
    cand = None
    # kept(None) = every pct-known setup (the <0 bucket + the 0 rung)
    kept_all = {"n": k0["n"] + neg["n"], "ran": k0["ran"] + neg["ran"],
                "usd": k0["usd"] + neg["usd"],
                "ceiling": k0["ceiling"] + neg["ceiling"]}
    kept = dict(kept_all)
    for k in keys:
        d = bks.get(k) or _zero
        p = {"n": kept["n"] - d["n"], "ran": kept["ran"] - d["ran"],
             "usd": round(kept["usd"] - d["usd"], 2),
             "ceiling": round(kept["ceiling"] - d["ceiling"], 2)}
        p["rate"] = (p["ran"] / p["n"]) if p["n"] else None
        p["from"] = cand
        p["denied"] = bool(p["n"]) and p["usd"] < 0
        bands[k] = p
        if cand is None:
            if p["denied"]:
                cand, kept = k, dict(d)   # the first denial pays — take it
            else:
                break                     # the first band already pays
        elif p["denied"]:
            cand, kept = k, dict(d)       # this denial also pays — climb
        else:
            break                         # this band is worth keeping
    chosen = cand
    if chosen is not None:
        cb = bands[chosen]
        _rr = f"{cb['rate'] * 100.0:.0f}% ran" if cb["rate"] is not None \
            else "?"
        why = (f">=+{chosen}% denies {cb['n']} setups ({_rr}) worth "
               f"${cb['usd']:+,.2f} — the last denial that pays; every "
               f"raise past it would exclude real money")
        d_end = bks.get(chosen) or _zero
        denied_total = {
            "n": kept_all["n"] - d_end["n"],
            "ran": kept_all["ran"] - d_end["ran"],
            "usd": round(kept_all["usd"] - d_end["usd"], 2),
            "ceiling": round(kept_all["ceiling"] - d_end["ceiling"], 2)}
        denied_total["rate"] = ((denied_total["ran"] / denied_total["n"])
                                if denied_total["n"] else None)
    else:
        why = ("no rung's denial strictly improves the kept world — "
               "every pct band earns its keep")
        denied_total = None
    return {"bands": bands, "chosen": chosen, "why": why,
            "denied_total": denied_total}


# THE DIMENSION EDGES (Sep 19): the marginal walk is not married to
# 24h-pct. Each turn-hour feature gets its own ascending edges — a
# floor at edge E keeps every setup whose feature >= E. pct24 = the
# original rungs; ema_slope = the turn's EMA9 rise over 5 bars (price
# units normalized to % — steeper turn, stronger trend); pullback =
# the turn's dip depth below the tracked ref / rolling-8 high (a
# deeper discount may recover less).
DIM_EDGES = {
    "pct24":     (0.0, 1.0, 2.0, 3.0, 5.0, 7.5, 10.0),
    "ema_slope": (0.0, 0.25, 0.5, 1.0, 1.5, 2.5, 4.0),
    "pullback":  (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5),
}


def _dim_sweep(rows, dim: str, edges, position_usd: float | None = None) -> dict:
    """THE DIMENSION-GENERAL SWEEP (Sep 19): the same cumulative
    set-bucket math _floor_sweep prices pct24 with, run over ANY
    turn-hour feature. A floor at edge E keeps every setup whose dim
    >= E, so each bucket IS the kept world that floor would hold (rung
    dollars must never be added). The sub bucket holds dim < edges[0]
    (what the smallest edge denies vs no floor); a setup whose feature
    is unknown prices nowhere, honestly."""
    _pusd = float(position_usd) if position_usd else POSITION_USD
    keys = tuple(f"{e:g}" for e in edges)
    b = {k: {"n": 0, "ran": 0, "usd": 0.0, "ceiling": 0.0} for k in keys}
    sub = {"n": 0, "ran": 0, "usd": 0.0, "ceiling": 0.0}
    unknown = {"n": 0, "ran": 0, "usd": 0.0, "ceiling": 0.0}
    for s in rows:
        usd = _pusd * float(s["end"]) / 100.0
        ceil = _pusd * RAN_PCT / 100.0 if s["ran"] else 0.0
        d = {"n": 1, "ran": int(s["ran"]), "usd": usd, "ceiling": ceil}
        v = s.get(dim)
        if v is None:
            for tgt in (unknown,):
                tgt["n"] += d["n"]; tgt["ran"] += d["ran"]
                tgt["usd"] += d["usd"]; tgt["ceiling"] += d["ceiling"]
            continue
        if v < edges[0]:
            for tgt in (sub,):
                tgt["n"] += d["n"]; tgt["ran"] += d["ran"]
                tgt["usd"] += d["usd"]; tgt["ceiling"] += d["ceiling"]
            continue
        for tgt in tuple(b[k] for e, k in zip(edges, keys) if v >= e):
            tgt["n"] += d["n"]; tgt["ran"] += d["ran"]
            tgt["usd"] += d["usd"]; tgt["ceiling"] += d["ceiling"]
    for d in (unknown, sub, *b.values()):
        d["usd"] = round(d["usd"], 2)
        d["ceiling"] = round(d["ceiling"], 2)
    return {"buckets": b, "sub": sub, "unknown": unknown, "keys": keys}


def _dim_band_walk(sweep: dict, edges) -> dict:
    """THE WALK, DIMENSION-GENERAL (Sep 19): start at no floor (every
    dim-known setup as one kept world), climb the edges ascending, and
    price each raise as the band it DENIES — the difference between
    consecutive KEPT worlds (kept_all -> bucket(e0) -> bucket(e1) ...).
    (The sub bucket is a denied SET, never a kept world — the first
    band, moving to edges[0], is exactly kept_all - bucket(e0).) Move
    the candidate while the denied band BLEEDS; stop at the first
    band worth keeping. An EMPTY band is skipped (no evidence — it
    can neither pay nor bleed; the pct24 walk never needed this
    because its <0 band is always populated). The chosen edge is the
    minimal floor the data supports on this dimension; denied_total is
    exactly what the floor would exclude, priced."""
    keys = sweep["keys"]
    bks, sub = sweep["buckets"], sweep["sub"]
    # THE DISJOINT DECOMPOSITION: the buckets are CUMULATIVE — b(e0) is
    # the whole >=e0 world and every higher bucket is its subset — so
    # the no-floor world is sub + b(e0) ONLY (summing every bucket
    # double-counts each setup once per edge it clears). Unknown
    # prices nowhere and never enters any world.
    kept_all = {"n": sub["n"] + bks[keys[0]]["n"],
                "ran": sub["ran"] + bks[keys[0]]["ran"],
                "usd": round(sub["usd"] + bks[keys[0]]["usd"], 2),
                "ceiling": sub["ceiling"] + bks[keys[0]]["ceiling"]}
    # the kept-world ladder: no floor, then each edge's cumulative world
    worlds = [dict(kept_all)] + [dict(bks[k]) for k in keys]
    names = ["sub"] + list(keys)
    cand, kept, bands = None, dict(kept_all), {}
    for i in range(1, len(worlds)):
        name, w = names[i], worlds[i]
        band = {"n": kept["n"] - w["n"], "ran": kept["ran"] - w["ran"],
                "usd": round(kept["usd"] - w["usd"], 2)}
        band["from"] = cand
        band["denied"] = bool(band["n"]) and band["usd"] < 0
        bands[name] = band
        if not band["n"]:
            continue                       # empty: no evidence, climb on
        if band["denied"]:
            cand, kept = name, dict(w)     # this denial pays — take it
        else:
            break                          # this band is worth keeping
    chosen = cand
    if chosen is not None:
        denied_total = {"n": kept_all["n"] - kept["n"],
                        "usd": round(kept_all["usd"] - kept["usd"], 2)}
        denied_total["bleed_per_seat"] = (
            round(-denied_total["usd"] / denied_total["n"], 2)
            if denied_total["n"] else None)
        _lbl = ("<" + f"{edges[0]:g}" if chosen == "sub"
                else ">=" + chosen)
        why = (f"floor {_lbl} denies {denied_total['n']} setups worth "
               f"${denied_total['usd']:+,.2f} "
               f"(${denied_total['bleed_per_seat']:,.2f}/seat denied)")
        kept_world = {"n": kept["n"], "ran": kept["ran"],
                      "usd": kept["usd"]}
    else:
        denied_total = None
        kept_world = None
        why = ("no edge's denial strictly improves the kept world — "
               "every band earns its keep on this dimension")
    return {"chosen": chosen, "bands": bands, "why": why,
            "denied_total": denied_total, "kept_world": kept_world}


def _unified_policy(mw: dict, fs: dict, lfs: dict, lw: dict,
                    seats: str | None) -> dict:
    """THE UNIFIED WIDENING POLICY (Sep 19, user: "combine the pct24
    quality floor and the growth verdict into one widening policy:
    which coins earn seats AND which turns are worth speaking, priced
    together"): the two report calls were independent lines that never
    saw each other. ONE policy prices BOTH sides of the widening:
      · the SPEAK FLOOR — the marginal rung a floor at F denies (from
        _marginal_floor_bands on the missed pool's sweep) must pay:
        if the denial's end-move is negative, the floor STRICTLY
        improves the kept world — speak only above it. The LIVED
        sweep's own marginal verdict rides as the confirmation check:
        the floor ships speaking only when the lived bands agree.
      · the SEATS — the growth verdict (GROW/HOLD/SHRINK at the
        evidence gate, passed in as `seats`) governs seat COUNT.
      · the TIE — GROW earns the extra seats only WITH the speak
        floor (new coins' turns clear the same bar the pool's data
        demands); HOLD with a paying floor keeps the six and tightens
        what speaks; SHRINK retires seats and demands the floor
        regardless. Below the evidence gate the floor can still speak
        from the POOL's data (the pool's distribution, not the
        widening's) — named honestly as pool-evidence. Never
        auto-applied: CHASE_WATCHLIST stays hand-set; the floor ships
        speaking only on lived confirmation."""
    _mf = _marginal_floor_bands(fs)
    _lmf = _marginal_floor_bands(lfs)
    _policy_why, _floor_ship = [], False
    if _mf["chosen"] is not None:
        _fb = _mf["bands"][_mf["chosen"]]
        _dt = _mf.get("denied_total") or {}
        _policy_why.append(
            f"pool bands: a >=+{_mf['chosen']}% floor denies "
            f"{_fb['n']} setups that ran {_fb['rate'] * 100.0:.0f}% "
            f"and cost ${_fb['usd']:+,.2f} at the margin"
            + (f" (all denials below it together: {_dt['n']} setups, "
               f"${_dt['usd']:+,.2f} — speak only above "
               f"+{_mf['chosen']}%)"
               if _dt else "") + " — the denial pays")
        if _lmf["chosen"] is not None:
            _floor_ship = True
            _policy_why.append(
                "lived bands agree — the floor is CONFIRMED for "
                "speaking (ship on the watch)")
        else:
            _policy_why.append(
                "lived bands have not confirmed — the floor stays a "
                "report line until the widening's own turns agree")
    else:
        _policy_why.append(
            "pool bands price no paying denial — no 24h-pct floor "
            "earns the watch; every seat's turn speaks")
    if seats == "GROW":
        _policy_why.append(
            "seats: GROW — the widening earns more seats, and the "
            "new coins' turns clear the same speak floor")
    elif seats == "SHRINK":
        _policy_why.append(
            "seats: SHRINK — retire the bleeding seats and demand "
            "the speak floor on whatever remains")
    elif seats == "HOLD":
        _policy_why.append(
            "seats: HOLD — the six stand; the speak floor is what "
            "moves")
    else:
        _policy_why.append(
        f"seats: the grow/hold verdict still waits "
        f"({lw.get('n') or 0}/{GROWTH_GATE_N} graded turns — the early "
        f"gate opens at {GROWTH_GATE_EARLY_N} only on extreme "
        f"separation: the rate edge plus double the pool's dollars) — "
        f"the speak floor rides pool evidence alone until the "
        f"widening earns its own verdict")
    return {"speak_floor": _mf["chosen"],
            "floor_confirmed": _floor_ship,
            "seats": seats,
            "marginal": _mf, "lived_marginal": _lmf,
            "why": " | ".join(_policy_why)}


def _price_world(setups: list, position_usd: float, now: float) -> dict:
    """One world's ledger: n, ran, usd (end-move at the FULL chase
    position), ran ceiling, and the recent-window splits."""
    agg = {"n": 0, "ran": 0, "usd": 0.0, "ceiling": 0.0,
           "w14": {"n": 0, "ran": 0, "usd": 0.0},
           "w30": {"n": 0, "ran": 0, "usd": 0.0}}
    for s in setups:
        usd = position_usd * float(s["end"]) / 100.0
        ceil = position_usd * RAN_PCT / 100.0 if s["ran"] else 0.0
        agg["n"] += 1
        agg["ran"] += int(s["ran"])
        agg["usd"] += usd
        agg["ceiling"] += ceil
        age_d = max(0.0, (now - float(s["ts"])) / 86400.0)
        for win, days in (("w14", 14.0), ("w30", 30.0)):
            if age_d <= days:
                agg[win]["n"] += 1
                agg[win]["ran"] += int(s["ran"])
                agg[win]["usd"] += usd
    for k in ("usd", "ceiling"):
        agg[k] = round(agg[k], 2)
    for win in ("w14", "w30"):
        agg[win]["usd"] = round(agg[win]["usd"], 2)
    return agg


def _floor_sweep(missed: list, floors=FLOOR_STEPS,
                 position_usd: float | None = None) -> dict:
    """THE QUALITY CUTOFF (Sep 18): the coverage study's blind spot ran
    at 16% while the tracked world ran at 35% — but the missed pool is
    not one world. Bucket the MISSED setups by their turn-hour 24h pct
    into rungs — < 0, >= 0, >= +1, >= +2, >= +3, >= +5, >= +7.5, >= +10
    — and price each rung in the chase convention. THE PITFALL: set
    logic, not filtering. The rungs are cumulative — a floor at F
    watches every setup with p >= F, so each bucket IS the kept world
    that floor would hold (rung dollars must never be added; the floor
    CHOICE prices the marginal exclusions, each rung vs the rung just
    below = the band [F, F_next)). The 0.0 rung is the whole p >= 0
    pool, so 0-rung + <0 + unknown reproduces the missed world exactly
    (total must price to it) — the < 0 bucket is the drawdown side,
    whose dollars never count toward any floor's net. A setup whose
    turn-hour 24h pct is unknown prices NOWHERE — unknowable is not
    free money; it surfaces as the ?pct line and never enters a
    floor's world. position_usd (Sep 18): the dollars the caller prices
    at — the lived rungs ride run_study's position_usd, not the module
    global."""
    _pusd = float(position_usd) if position_usd else POSITION_USD
    keys = tuple(f"{g:g}" for g in floors)
    b = {k: {"n": 0, "ran": 0, "usd": 0.0, "ceiling": 0.0} for k in keys}
    b["<0"] = {"n": 0, "ran": 0, "usd": 0.0, "ceiling": 0.0}
    unknown = {"n": 0, "ran": 0, "usd": 0.0, "ceiling": 0.0}
    for s in missed:
        usd = _pusd * float(s["end"]) / 100.0
        ceil = _pusd * RAN_PCT / 100.0 if s["ran"] else 0.0
        d = {"n": 1, "ran": int(s["ran"]), "usd": usd,
             "ceiling": ceil}
        p = s.get("pct24")
        if p is None:
            unknown["n"] += d["n"]
            unknown["ran"] += d["ran"]
            unknown["usd"] += d["usd"]
            unknown["ceiling"] += d["ceiling"]
            continue
        if p < 0:
            tgts = (b["<0"],)
        else:
            # CUMULATIVE: a floor at F would watch every setup with
            # p >= F, so a setup clears every rung at or below its pct —
            # b[k] is the kept world a floor at k would actually watch.
            # (floors[0] must be 0.0: the 0-rung anchors the whole p >= 0
            # pool, so total = 0-rung + <0 + unknown reproduces the pool.)
            tgts = tuple(b[k] for fl, k in zip(floors, keys) if p >= fl)
        for tgt in tgts:
            tgt["n"] += d["n"]
            tgt["ran"] += d["ran"]
            tgt["usd"] += d["usd"]
            tgt["ceiling"] += d["ceiling"]
    for d in (unknown, b["<0"], *b.values()):
        d["usd"] = round(d["usd"], 2)
        d["ceiling"] = round(d["ceiling"], 2)
    # the marginal exclusions: moving the floor from F to F_next excludes
    # exactly the band [F, F_next) — its dollars are the price of the raise
    marg = {}
    for i, k in enumerate(keys):
        if i + 1 < len(keys):
            nxt = keys[i + 1]
            marg[k] = {"band_n": b[k]["n"] - b[nxt]["n"],
                       "band_ran": b[k]["ran"] - b[nxt]["ran"],
                       "band_usd": round(b[k]["usd"] - b[nxt]["usd"], 2),
                       "band_ceiling": round(b[k]["ceiling"] - b[nxt]["ceiling"], 2)}
    marg[keys[-1]] = {"band_n": b[keys[-1]]["n"],
                      "band_ran": b[keys[-1]]["ran"],
                      "band_usd": b[keys[-1]]["usd"],
                      "band_ceiling": b[keys[-1]]["ceiling"]}
    _k0 = keys[0]
    return {"buckets": b, "marginal": marg, "unknown": unknown,
            "total": {"n": b[_k0]["n"] + b["<0"]["n"] + unknown["n"],
                      "ran": b[_k0]["ran"] + b["<0"]["ran"]
                      + unknown["ran"],
                      "usd": round(b[_k0]["usd"] + b["<0"]["usd"]
                                   + unknown["usd"], 2),
                      "ceiling": round(b[_k0]["ceiling"]
                                       + b["<0"]["ceiling"]
                                       + unknown["ceiling"], 2)}}


def run_study(max_pairs: int | None = None, stores_dir: Path | None = None,
              position_usd: float | None = None, now: float | None = None,
              write: bool = True, lived_path=None) -> dict:
    """The fleet study across the contest stores. Bounds make it
    hermetic: max_pairs slices, stores_dir/position_usd injectable."""
    global POSITION_USD
    POSITION_USD = position_usd if position_usd is not None else _position_usd()
    from config import DEFAULT_CONFIG as cfg
    t_now = time.time() if now is None else float(now)
    glob_pattern = (str(stores_dir / "*_USD-1h.feather") if stores_dir
                    else str(BASE / STORES_GLOB))
    all_paths = sorted(glob.glob(glob_pattern))
    wanted = {str(p).replace("/", "_") for p in (cfg.trading_pairs or [])}
    paths = [p for p in all_paths
             if Path(p).name.replace("-1h.feather", "") in wanted] \
        if not stores_dir else all_paths
    bounded = max_pairs is not None and len(paths) > max_pairs
    if bounded:
        _step = max(1, len(paths) // max_pairs)
        paths = paths[::_step][:max_pairs]

    closes_by_pair = {}
    for path in paths:
        pair = Path(path).name.replace("-1h.feather", "")
        try:
            closes_by_pair[pair] = _load_store(path)
        except Exception as exc:
            logger.debug("chaseback: %s unreadable (%s)", pair, exc)

    # the funnel's view: top-15 runners per hour across the fleet
    on_list, pct_by_pair = _fleet_movers_on_list(closes_by_pair, WARMUP)

    caught_all, missed_all = [], []
    per_coin = {}
    for pair, (closes, highs, idx) in closes_by_pair.items():
        try:
            setups = _detect_setups(closes, highs, idx,
                                    on_list.get(pair, {}),
                                    pct_by_pair.get(pair, ([], None))[1],
                                    WARMUP)
        except Exception as exc:
            logger.debug("chaseback: %s walk failed (%s)", pair, exc)
            continue
        if not setups:
            continue
        pc = {"caught": [s for s in setups if s["caught"]],
              "missed": [s for s in setups if not s["caught"]]}
        per_coin[pair] = {"caught_n": len(pc["caught"]),
                          "missed_n": len(pc["missed"]),
                          "missed_ran": sum(1 for s in pc["missed"] if s["ran"]),
                          "missed_usd": round(sum(
                              POSITION_USD * s["end"] / 100.0
                              for s in pc["missed"]), 2),
                          "caught_usd": round(sum(
                              POSITION_USD * s["end"] / 100.0
                              for s in pc["caught"]), 2)}
        caught_all.extend(pc["caught"])
        missed_all.extend(pc["missed"])

    cw = _price_world(caught_all, POSITION_USD, t_now)
    mw = _price_world(missed_all, POSITION_USD, t_now)
    fs = _floor_sweep(missed_all)
    # the widening's lived record: real [watchlist] catches, closed
    # horizons only, priced with the same convention — never folded
    # into the counterfactual worlds above (no double-counting)
    _lived = _load_lived_turns(path=lived_path, now=t_now,
                               stores_dir=stores_dir)
    lw = _price_world(_lived["rows"], POSITION_USD, t_now)
    lw["pending"] = _lived["pending"]
    lw["total"] = _lived["total"]
    lw["by_pair"] = {}
    for _lr in _lived["rows"]:
        _lp = lw["by_pair"].setdefault(
            _lr["pair"], {"n": 0, "ran": 0, "usd": 0.0})
        _lp["n"] += 1
        _lp["ran"] += int(_lr["ran"])
        _lp["usd"] = round(_lp["usd"]
                           + POSITION_USD * _lr["end"] / 100.0, 2)
    # THE LIVED RUNGS (Sep 18): the same floor sweep, over the widening's
    # real turns — the decision-time pct24 stamps make lived rows grade
    # by rung exactly like the missed pool. Legacy rows (no stamp) price
    # in ?pct, honestly, so the sweep never invents a rung.
    lfs = _floor_sweep(_lived["rows"], position_usd=position_usd)
    lw["floor_sweep"] = lfs
    # THE LIVED-TREND FEED (Sep 19): the graded turns bucketed by ISO
    # week — the plate's sparkline rides this instead of re-deriving
    # timezone math in the renderer. One dict, ascending weeks.
    lw["by_week"] = _lived_weekly(_lived["rows"],
                                  position_usd=position_usd)

    # THE GROWTH VERDICT (Sep 18): should the widening grow past its six
    # worst offenders? The evidence gate is absolute — fewer than
    # GROWTH_GATE_N GRADED watchlist turns and the verdict is a wait,
    # named as such (a hot two-turn streak is not a regime; the
    # stability-gate lesson). At evidence the widening's graded ran-rate
    # and end-move-per-turn are priced against the missed pool's own
    # rates — the pool it was carved from: GROW when the watchlist
    # out-runs the pool by ten points AND makes at least the pool's
    # dollars per turn, SHRINK when its turns bleed end-move (never grow
    # a bleeder), HOLD otherwise. A report verdict, never auto-applied
    # — the POOL_MIN house style; CHASE_WATCHLIST stays hand-set.
    _gq = mw["ran"] / max(1, mw["n"])
    _gu = mw["usd"] / max(1, mw["n"])
    _gverdict, _gwhy = None, ""
    _gr = (lw["ran"] / lw["n"]) if lw["n"] else None
    _guv = (lw["usd"] / lw["n"]) if lw["n"] else None
    # THE EARLY GATE (Sep 19): the verdict may speak at 8 graded turns
    # when the separation is extreme (the authority is
    # growth_gate_open — the rule and its thresholds live there, not
    # here); otherwise the full gate stands. gate_src names which gate
    # the verdict rode so the hindsight can price the early calls
    # separately from the full-gate ones.
    if lw["n"] >= GROWTH_GATE_N:
        _gate_src = "full"
    elif growth_gate_open(lw["n"], _gq, _gu, _gr, _guv):
        _gate_src = "early"
    else:
        _gate_src = None
    if _gate_src:
        _gbase = (f"{lw['ran']}/{lw['n']} ran ({_gr * 100.0:.0f}%) vs the "
                  f"pool's {_gq * 100.0:.0f}%, ${_guv:+,.2f}/turn vs "
                  f"${_gu:+,.2f}")
        if _guv < 0:
            _gverdict = "SHRINK"
            _gworst = (min(lw["by_pair"].items(),
                           key=lambda kv: kv[1]["usd"])[0]
                       if lw["by_pair"] else "?")
            _gwhy = (_gbase + " — the widening bleeds end-move ("
                     + _gworst + " bleeds worst); retire seats, do not add")
        elif _gr >= _gq + 0.10 and _guv >= _gu:
            _gverdict = "GROW"
            _gwhy = (_gbase + " — the watchlist out-runs the pool it was "
                     "carved from; it earns more seats")
        else:
            _gverdict = "HOLD"
            _gwhy = (_gbase + " — inside the pool's band; the six stand "
                     "until the rate separates")
        if _gate_src == "early":
            _gwhy += (" — the evidence gate fired early: the separation "
                      "is extreme (the rate edge plus doubled dollars), "
                      "not merely good")
    lw["growth"] = {"verdict": _gverdict, "gate": GROWTH_GATE_N,
                    "gate_src": _gate_src,
                    "graded": lw["n"], "pending": lw["pending"],
                    "rate": (round(lw["ran"] / lw["n"], 3)
                             if lw["n"] else None),
                    "pool_rate": round(_gq, 3),
                    "usd_turn": (round(lw["usd"] / lw["n"], 2)
                                 if lw["n"] else None),
                    "pool_usd_turn": round(_gu, 2), "why": _gwhy}
    # THE MILESTONE (Sep 19): the GROW call's date and dollars, stamped
    # once and refreshed live — the plate reads how long the widening
    # took to earn its grow call. Failed writes never take the study down.
    try:
        _ms_prev = None
        if GROWTH_MILESTONE.exists():
            _ms_prev = json.loads(
                GROWTH_MILESTONE.read_text(encoding="utf-8"))
        _ms_first = (min((_lr["ts"] for _lr in _lived["rows"]),
                        default=None) if _lived["rows"] else None)
        _ms = merge_milestone(_ms_prev, verdict=_gverdict, graded=lw["n"],
                              ran=lw["ran"], usd=lw["usd"],
                              gate_src=_gate_src, first_ts=_ms_first,
                              now=t_now, position_usd=POSITION_USD)
        if _ms:
            lw["growth"]["milestone"] = _ms
            _mstmp = str(GROWTH_MILESTONE) + ".tmp"
            with open(_mstmp, "w", encoding="utf-8") as _msf:
                _msf.write(json.dumps(_ms))
            os.replace(_mstmp, GROWTH_MILESTONE)
    except Exception as _ms_exc:
        logger.debug("growth milestone write failed: %s", _ms_exc)

    # THE UNIFIED WIDENING POLICY (Sep 19, user: "combine the pct24
    # quality floor and the growth verdict into one widening policy:
    # which coins earn seats AND which turns are worth speaking, priced
    # together"): the two calls were independent report lines that never
    # saw each other. ONE policy now prices BOTH sides of the widening —
    # composed in _unified_policy (pure, hermetic-pin-able) and stored
    # on the lived block for the plate and the txt.
    lw["policy"] = _unified_policy(mw, fs, lfs, lw, seats=_gverdict)

    # missed money, per coin, worst first (the chase-coverage watchlist)
    miss_coins = sorted(((v["missed_usd"], k, v) for k, v in per_coin.items()
                         if v["missed_n"] > 0), reverse=True)[:10]

    summary = {"ts": _utcnow_iso(), "pairs": len(closes_by_pair),
               "position_usd": POSITION_USD, "bounded": bounded,
               "caught": cw, "missed": mw, "per_coin": per_coin,
               "floor_sweep": fs, "lived": lw}
    # THE DIMENSION BAKE-OFF (Sep 19): run the marginal walk over every
    # turn-hour feature on the same missed pool. The winner prices the
    # most bleed per SEAT denied — each silenced setup costs a watch
    # seat, so $/seat is the currency, not raw dollars.
    _bake = {}
    for _dim, _edges in DIM_EDGES.items():
        _sw = _dim_sweep(missed_all, _dim, _edges)
        _bw = _dim_band_walk(_sw, _edges)
        _bake[_dim] = {"chosen": _bw["chosen"], "why": _bw["why"],
                       "denied_total": _bw["denied_total"],
                       "kept_world": _bw["kept_world"]}
    summary["dim_bakeoff"] = _bake
    out = ["=== THE CHASE-COVERAGE STUDY: pullback-and-turn setups — "
           "caught vs missed (Sep 17) ===",
           f"stores: {len(closes_by_pair)} contest pairs | position "
           f"${POSITION_USD:,.0f} (the full chase position) | horizon "
           f"{HORIZON}h | ran = +{RAN_PCT:.0f}% MFE | the funnel's view: "
           f"top {MOVER_TOP_N} runners >= {MOVER_MIN_PCT:.0f}% on 24h",
           "",
           f"CAUGHT (tracked — the alert spoke): {cw['n']} setups, "
           f"{cw['ran']}/{cw['n']} ran, ${cw['usd']:+,.2f} "
           f"(ceiling ${cw['ceiling']:,.2f})",
           f"  w14: {cw['w14']['n']} setups, {cw['w14']['ran']} ran, "
           f"${cw['w14']['usd']:+,.2f} | w30: {cw['w30']['n']} setups, "
           f"{cw['w30']['ran']} ran, ${cw['w30']['usd']:+,.2f}",
           f"MISSED (the same pattern, never tracked): {mw['n']} setups, "
           f"{mw['ran']}/{mw['n']} ran, ${mw['usd']:+,.2f} "
           f"(ceiling ${mw['ceiling']:,.2f})",
           f"  w14: {mw['w14']['n']} setups, {mw['w14']['ran']} ran, "
           f"${mw['w14']['usd']:+,.2f} | w30: {mw['w30']['n']} setups, "
           f"{mw['w30']['ran']} ran, ${mw['w30']['usd']:+,.2f}",
           "",
           f"LIVED (the widening's real [watchlist] catches, closed "
           f"horizons): {lw['n']} turns, {lw['ran']}/{lw['n']} ran, "
           f"${lw['usd']:+,.2f} (ceiling ${lw['ceiling']:,.2f})"
           + (f" | {lw['pending']} pending" if lw["pending"] else "")
           + (" | " + ", ".join(
               f"{_lp} {_lv['ran']}/{_lv['n']} ran ${_lv['usd']:+,.2f}"
               for _lp, _lv in sorted(lw["by_pair"].items()))
              if lw["by_pair"] else ""),
           ""]
    # the growth verdict line: waits honestly below the evidence gate,
    # speaks GROW/HOLD/SHRINK with its priced why at evidence (only
    # when the widening has spoken at all)
    _gw = lw.get("growth") or {}
    if lw.get("total"):
        if _gw.get("verdict") is None:
            out.append(f"  WATCHLIST GROWTH: {lw['n']}/{_gw.get('gate') or 10} "
                       f"graded turns — the grow/hold verdict waits "
                       f"({(_gw.get('gate') or 10) - lw['n']} more)")
        else:
            out.append(f"  WATCHLIST GROWTH VERDICT: {_gw['verdict']} "
                       f"— {_gw['why']}")
        _ms = _gw.get("milestone")
        if _ms:
            _ms_line = (f"  GROWTH MILESTONE: earned {_ms['earned_date']} "
                        f"via the {_ms.get('gate_src')} gate at "
                        f"{_ms.get('graded_at_earn')} graded turns")
            if _ms.get("first_turn_ts") and _ms.get("days_to_earn") is not None:
                _ms_line += (f" — {_ms['days_to_earn']:.1f} days from the "
                             f"first lived turn ({_ms['first_turn_ts']})")
            _ms_line += (f" | then: {_ms.get('ran')}/{_ms.get('graded')} ran, "
                         f"${_ms.get('total_usd'):,.2f} lived")
            if _ms.get("full_gate_date") and _ms.get("gate_src") == "early":
                _ms_line += f" | full-evidence date: {_ms['full_gate_date']}"
            out.append(_ms_line)
    # the unified policy line: one place where the seats verdict and
    # the speak floor are read together (the growth line above stays
    # for continuity; the policy is the call)
    _pol = lw.get("policy") or {}
    if lw.get("total"):
        out.append("")
        out.append("  WIDENING POLICY (seats x speak floor, priced "
                   "together): "
                   + (f"speak floor >= +{_pol['speak_floor']}% "
                      f"({'CONFIRMED — ships speaking' if _pol.get('floor_confirmed') else 'pool evidence — awaits lived confirmation'})"
                      if _pol.get("speak_floor") is not None else
                      "no speak floor earns the watch — every seat's "
                      "turn speaks"))
        out.append("    " + str(_pol.get("why") or ""))
    if lw["n"] and lw["floor_sweep"]["total"]["n"]:
        out.append("  LIVED BY RUNG (decision-time 24h pct; each row is "
                   "the kept world a floor there would hold):")
        for _lk in tuple(f"{g:g}" for g in FLOOR_STEPS) + ("<0",):
            _ld = lw["floor_sweep"]["buckets"][_lk]
            if _ld["n"]:
                _lrate = f"{_ld['ran'] / _ld['n'] * 100.0:5.1f}%"
                out.append(f"    >=+{_lk}% | {_ld['n']:>3} turns, "
                           f"{_ld['ran']}/{_ld['n']} ran {_lrate}, "
                           f"${_ld['usd']:>+9,.2f} end, "
                           f"${_ld['ceiling']:>7,.2f} ceiling"
                           if _lk != "<0" else
                           f"    <0%   | {_ld['n']:>3} turns, "
                           f"{_ld['ran']}/{_ld['n']} ran {_lrate}, "
                           f"${_ld['usd']:>+9,.2f} end")
        _lu = lw["floor_sweep"]["unknown"]
        if _lu["n"]:
            out.append(f"    ?pct  | {_lu['n']:>3} turns (pre-stamp legacy "
                       f"rows — no decision-time 24h pct, priced nowhere)")
        out.append("")
    if mw["n"]:
        rate = mw["ran"] / mw["n"] * 100.0
        out.append(f"THE BLIND SPOT: {mw['n']} patterns the tracker never "
                   f"saw ({mw['n'] / max(1, cw['n'] + mw['n']) * 100.0:.0f}% "
                   f"of all setups), {rate:.0f}% ran — "
                   f"${mw['usd']:+,.2f} end-move, ${mw['ceiling']:,.2f} "
                   f"at the ran ceiling")
        out.append("")
        out.append("missed money by coin (worst first):")
        for usd, pair, v in miss_coins:
            out.append(f"  {pair}: {v['missed_n']} missed "
                       f"({v['missed_ran']} ran), ${usd:+,.2f} "
                       f"(caught {v['caught_n']}, ${v['caught_usd']:+,.2f})")
    if fs["total"]["n"]:
        out += ["",
                "THE QUALITY CUTOFF — missed setups by turn-hour 24h pct "
                "(the funnel's own runner dial); each row is the kept "
                "world a floor there would watch:",
                "   floor | setups |   ran | ran-rate |  end-move $ | "
                "ceiling $ | excluded by raising to the next rung"]
        for k in tuple(f"{g:g}" for g in FLOOR_STEPS) + ("<0",):
            d = fs["buckets"][k]
            m = fs["marginal"].get(k)
            rate = f"{d['ran'] / d['n'] * 100.0:5.1f}%" if d["n"] else "-"
            if m and m["band_n"]:
                excl = (f"-{m['band_n']:>3} stp, ${-m['band_usd']:>+8,.2f}, "
                        f"-${m['band_ceiling']:>7,.2f} ceiling")
            else:
                excl = "-"
            lbl = "<0" if k == "<0" else ">=" + k
            out.append(f"  {lbl:>6} | {d['n']:>6} | {d['ran']:>5} | "
                       f"{rate:>8} | {d['usd']:>+11,.2f} | "
                       f"{d['ceiling']:>9,.2f} | {excl}")
        u = fs["unknown"]
        if u["n"]:
            out.append(f"   ?pct | {u['n']:>6} | {u['ran']:>5} | "
                       f"no 24h pct at the turn hour — priced nowhere "
                       f"(${u['usd']:+,.2f}, ${u['ceiling']:,.2f} ceiling)")
        # the decision rule (a REPORT recommendation, never auto-applied
        # — the POOL_MIN house style): the highest rung whose kept world
        # at least doubles the missed pool's own ran-rate while holding
        # at least half its ceiling. That is the floor that separates
        # payers from noise without throwing away the payer store.
        _tmw = mw["ran"] / max(1, mw["n"])
        rec = None
        for k in tuple(f"{g:g}" for g in FLOOR_STEPS):
            d = fs["buckets"][k]
            if d["n"] and d["ran"] / d["n"] >= 2.0 * _tmw \
                    and d["ceiling"] >= 0.5 * max(1e-9, fs["total"]["ceiling"]):
                rec = k
        out.append("")
        if rec is not None:
            kept = fs["buckets"][rec]
            out.append(f"  VERDICT: floor >= +{rec}% is the separator — "
                       f"it keeps {kept['n']} setups ({kept['ran']}/{kept['n']} "
                       f"ran = {kept['ran'] / max(1, kept['n']) * 100.0:.0f}% "
                       f"vs the pool's {mw['ran'] / max(1, mw['n']) * 100.0:.0f}%), "
                       f"holding ${kept['ceiling']:,.2f} of the "
                       f"${fs['total']['ceiling']:,.2f} ceiling "
                       f"({kept['ceiling'] / max(1e-9, fs['total']['ceiling']) * 100.0:.0f}%)")
        else:
            out.append("  VERDICT: no rung doubles the pool's ran-rate "
                       "while holding half its ceiling — a 24h-pct cutoff "
                       "does not separate payers from noise on this data.")
        # THE DIMENSION BAKE-OFF (Sep 19): the marginal walk is not
        # married to 24h-pct — every turn-hour feature gets the same
        # pricing, and the winner denies the most bleed per SEAT (each
        # silenced setup costs a watch seat). A report, never applied.
        out.append("")
        out.append("  DIMENSION BAKE-OFF (the marginal walk per turn-hour "
                   "feature; winner = most bleed denied per seat):")
        for _dim, _bb in _bake.items():
            out.append(f"    {_dim:>10}: {_bb['why']}")
    out.append("")

    if write:
        OUT.write_text("\n".join(out), encoding="utf-8")
        try:
            _tmp = str(LEDGER) + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as jf:
                json.dump(summary, jf, indent=1)
            import os
            os.replace(_tmp, LEDGER)
        except Exception as exc:
            logger.debug("chaseback ledger write failed: %s", exc)
        logger.info("CHASEBACK: caught %d / missed %d ($%.2f missed)",
                    cw["n"], mw["n"], mw["usd"])
    return summary


def monthly_driver_loop(stop, first_delay_s: float = FIRST_DELAY_S,
                        period_s: float = MONTHLY_PERIOD_S):
    """The daemon loop: one study shortly after boot, then monthly —
    the blind spot re-prices itself as the stores grow. Never raises;
    dies with the scanner."""
    import threading
    if stop.wait(first_delay_s):
        return
    while not stop.is_set():
        try:
            run_study(write=True)
        except Exception as exc:
            logger.debug("chaseback pass failed: %s", exc)
        if stop.wait(period_s):
            return


def graded_watchlist_turns(now: float | None = None) -> int:
    """The growth gate's exact count: every [watchlist] row in
    chase_turns.jsonl whose HORIZON-hour horizon has closed. Mirrors
    _load_lived_turns' grading (same filter, same horizon math) so the
    watch fires when the STUDY would count the gate — not before."""
    from datetime import datetime as _dt, timezone as _tz
    now = time.time() if now is None else float(now)
    n = 0
    try:
        lines = CHASE_TURNS_JSONL.read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    for ln in lines:
        ln = ln.strip()
        if not ln or "[watchlist]" not in ln:
            continue
        try:
            d = json.loads(ln)
            ts = _dt.fromisoformat(str(d["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
            if now - ts.timestamp() >= HORIZON * 3600.0:
                n += 1
        except Exception:
            continue
    return n


def growth_watch_loop(stop, poll_s: float = 300.0):
    """THE GROWTH WATCH (Sep 19, user: "watch chase_turns.jsonl until 10
    watchlist turns have graded horizons, re-run the coverage study, and
    read the first real GROW/HOLD/SHRINK verdict"; early gate Sep 19):
    polls the ledger and fires the study at the FIRST moment a verdict
    can speak — GROWTH_GATE_EARLY_N graded turns (the early gate; the
    study itself decides whether the separation is extreme enough to
    speak) or GROWTH_GATE_N (the full gate, always decisive). An early
    fire whose verdict waits does not end the watch — it continues to
    the full gate. Exits once a verdict has rendered. Polls quietly;
    only the fires (or an error) speak. Never raises; dies with the
    scanner."""
    import threading
    _early_fired = False
    while not stop.is_set():
        try:
            _n = graded_watchlist_turns()
            if not _early_fired and _n >= GROWTH_GATE_EARLY_N:
                _early_fired = True
                logger.info(" GROWTH WATCH: %d graded [watchlist] turns "
                            "reached the early gate (%d) — running the "
                            "study; the verdict speaks only if the "
                            "separation is extreme",
                            _n, GROWTH_GATE_EARLY_N)
                run_study(write=True)
                rec = ledger_last_record() or {}
                v = ((rec.get("lived") or {}).get("growth") or {}).get("verdict")
                if v:
                    logger.info(" GROWTH WATCH: the early-gate verdict "
                                "is %s (fired at %d graded turns)", v, _n)
                    return
                logger.info(" GROWTH WATCH: the early gate held — the "
                            "separation is not extreme; the watch "
                            "continues to the full gate (%d)",
                            GROWTH_GATE_N)
            elif _early_fired and _n >= GROWTH_GATE_N:
                logger.info(" GROWTH WATCH: %d graded turns reached the "
                            "full gate (%d) — running the study for the "
                            "first full-gate verdict", _n, GROWTH_GATE_N)
                run_study(write=True)
                rec = ledger_last_record() or {}
                v = ((rec.get("lived") or {}).get("growth") or {}).get("verdict")
                logger.info(" GROWTH WATCH: the first evidence-gate "
                            "verdict is %s",
                            v if v else "rendered by an earlier pass")
                return
        except Exception as exc:
            logger.warning(" GROWTH WATCH: poll failed (%s) — retrying "
                           "next cycle", exc)
        if stop.wait(poll_s):
            return


def ledger_last_record() -> dict | None:
    """The study snapshot's newest record (for the watch's verdict read).
    The chaseback ledger holds ONE pretty-printed JSON object, os.replace'd
    each pass; an older JSONL shape (one record per line) loads by its
    last line. None when the ledger is missing or unreadable."""
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8",
                                           errors="replace"))
    except (OSError, ValueError):
        pass
    try:
        lines = [l for l in LEDGER.read_text(
            encoding="utf-8", errors="replace").splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(message)s")
    s = run_study(write=True)
    print(OUT.read_text(encoding="utf-8"))
