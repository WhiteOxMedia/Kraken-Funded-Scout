"""Full edge policies: entry + long-side + exit + regime/vol/time gates + sizing.

The original `edge_engine` searched long-only entry selectors with one fixed
exit.  A policy is the unit the live scanner can actually reconstruct:

    entry selector  x  long side  x  exit management  x  regime gate
    x  ATR-percentile gate  x  time-of-day gate  x  size fraction

Everything is serialized as plain strings/floats in `edge_registry.json`.
No lambdas cross the research/live boundary.

Exits are all ATR-based and keep the shipped geometry as their base:
stop = 1.5 x ATR, first target = 2.5R.  The variants are:
  fixed:       the shipped 2.5R target
  breakeven:   same target, stop moves to entry after +1R
  chandelier:  no fixed target; after +1R trail at 1.5 x ATR under the peak
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Callable

import pandas as pd

import edge_engine as ee

SIDES = ("long",)   # the spot contest is long-only; there are no shorts
EXIT_MODES = ("fixed", "breakeven", "chandelier")
REGIME_KEYS = ("all", "risk_on", "risk_off", "trend")
VOL_KEYS = ("any", "low", "high")
# THE SESSION GATE IS ROTATION-ALIGNED (Nov 16). The old "us" gate was
# `13 <= hour < 24` on UTC candles, i.e. 07:00-18:00 Denver in summer and
# 06:00-17:00 in winter: it CLIPPED the 04-08 and 16-20 rotations and could
# never admit 00-04 or 20-24 at all - a timezone artefact sitting inside the
# evidence. The gate now names the Denver-local 4h rotations it means: the four
# the old UTC window did reach. A rotation is admitted whole or not at all, and
# DST cannot move the boundary.
US_ROTS = (1, 2, 3, 4)          # Denver 04-08, 08-12, 12-16, 16-20
HOUR_KEYS = ("any", "us", "us_local")
# The screen and the search only ever discover these.  "us_local" is the
# control arm (see hour_rule) and must not be a candidate: it would only
# re-promote the clipped evidence under a new name.
SEARCH_HOUR_KEYS = ("any", "us")
SIZE_FRACS = (0.09, 0.06)
MAX_HOLD = 24 * 14
# One chained position is much sparser than the screen's signal count.  A
# window only counts as evidence when the chain actually traded this many
# times; otherwise a 3-trade lucky path could masquerade as an edge.
MIN_CHAIN_TRADES = 10
# A promotion must fit inside the contest's own 3% loss budget from the peak
# and keep at least 1% of the window start as a death-line cushion.  These
# are risk limits, not performance targets; a merely profitable path that
# saws through either is not safe enough for live size.
PROMOTION_MAX_DD_PCT = 3.0
MIN_DEATH_HEADROOM_FRAC = 0.01
# A window must close at least this far above its start, not merely above zero.
# Measured noise band (Nov 16): moving the entry from the signal bar's close to
# the next bar's open - 0.007-0.019% per trade - flipped the sign of one window
# for all six promotions.  A window that clears by less than this float is the
# assumption talking, not the edge.
PROMOTION_MIN_WINDOW_PCT = 0.5


@dataclass(frozen=True)
class Policy:
    id: str
    label: str
    side: str
    entry: str
    regime: str
    exit_mode: str
    size_frac: float
    vol_gate: str
    hour_gate: str
    rule: Callable[[dict], bool]

    @property
    def spec(self) -> dict:
        return {
            "side": self.side, "entry": self.entry, "regime": self.regime,
            "exit": self.exit_mode, "size_frac": self.size_frac,
            "vol_gate": self.vol_gate, "hour_gate": self.hour_gate,
        }


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


# ── entry rules ───────────────────────────────────────────────────────────

LONG_ENTRIES: dict[str, Callable[[dict], bool]] = {
    "trend": lambda f: f["ema_up"] and f["close"] > f["ema21"],
    "trend200": lambda f: f["ema_up"] and f["above200"],
    "pullback": lambda f: (
        f["ema_up"] and f["close"] > f["ema50"] and f["ret1"] < 0
        and f["close"] <= f["ema9"] and _finite(f["rsi"]) and f["rsi"] <= 55),
    "break20": lambda f: f["break20"] and _finite(f["vol_surge"])
        and f["vol_surge"] >= 1.2,
    "ignition": lambda f: _finite(f["ret24"]) and f["ret24"] >= 3.0
        and _finite(f["vol_surge"]) and f["vol_surge"] >= 1.5
        and f["close"] > f["ema9"],
    "dip": lambda f: _finite(f["ret24"]) and f["ret24"] <= -4.0
        and f["close"] > f["open"] and _finite(f["rsi"]) and f["rsi"] <= 45,
    "leader": lambda f: _finite(f["xs72"]) and f["xs72"] >= 0.80
        and f["ema_up"] and f["ret1"] < 0,
    "lag": lambda f: _finite(f["xs168"]) and f["xs168"] <= 0.20
        and f["break4"] and _finite(f["rsi"]) and f["rsi"] >= 45,
    "squeeze": lambda f: _finite(f["atr_rank"]) and f["atr_rank"] <= 0.35
        and f["break20"] and _finite(f["vol_surge"]) and f["vol_surge"] >= 1.2,
    "volbreak": lambda f: _finite(f["vol_surge"]) and f["vol_surge"] >= 2.0
        and f["break20"],
    "range_reclaim": lambda f: _finite(f["range_pos"]) and f["range_pos"] <= 0.20
        and f["close"] > f["open"] and _finite(f["rsi"]) and f["rsi"] <= 40,
    "rsi_reclaim": lambda f: _finite(f["rsi_prev"]) and f["rsi_prev"] <= 35
        and _finite(f["rsi"]) and f["rsi"] >= 40 and f["close"] > f["open"],
}

# NO SHORT ENTRIES (user rule, Nov 12): this is a spot long-only contest.
# The replay still understands a short exit for old serialized rows, but no
# search policy, registry row, or forward candidate can be born short.
ENTRIES: dict[str, tuple[str, Callable[[dict], bool]]] = {
    **{k: ("long", v) for k, v in LONG_ENTRIES.items()},
}


def regime_rule(key: str) -> Callable[[dict], bool]:
    return {
        "all": lambda f: True,
        "risk_on": lambda f: _finite(f["btc24"]) and f["btc24"] > 0
            and _finite(f["breadth"]) and f["breadth"] > 0.50,
        "risk_off": lambda f: _finite(f["btc24"]) and f["btc24"] < 0
            and _finite(f["breadth"]) and f["breadth"] < 0.50,
        "trend": lambda f: ((f["above200"] and f["ema_up"])
                            or ((not f["above200"]) and (not f["ema_up"]))),
    }[key]


def vol_rule(key: str) -> Callable[[dict], bool]:
    return {
        "any": lambda f: True,
        "low": lambda f: _finite(f["atr_rank"]) and f["atr_rank"] <= 0.35,
        "high": lambda f: _finite(f["atr_rank"]) and f["atr_rank"] >= 0.65,
    }[key]


def hour_rule(key: str) -> Callable[[dict], bool]:
    return {
        "any": lambda f: True,
        # f["rot"] is the Denver-local rotation (edge_engine._frame_arrays); a
        # feature dict without one admits nothing rather than guessing
        "us": lambda f: f.get("rot") in US_ROTS,
        # CONTROL ARM, the retired gate verbatim: UTC 13-24, which is
        # 07:00-18:00 Denver under MDT and 06:00-17:00 under MST.  That
        # seasonal drift is the artefact being controlled for, so do not
        # "correct" this rule - chain it beside "us" to keep the comparison
        # honest.  A row without an hour raises, and _safe drops it.
        "us_local": lambda f: 13 <= int(f["hour"]) < 24,
    }[key]


def _safe(rule: Callable[[dict], bool], f: dict) -> bool:
    try:
        return bool(rule(f))
    except Exception:
        return False


def make_policy(side: str, entry: str, regime: str, exit_mode: str,
                size_frac: float, vol_gate: str, hour_gate: str) -> Policy:
    _, efn = ENTRIES[entry]
    rfn, vfn, hfn = regime_rule(regime), vol_rule(vol_gate), hour_rule(hour_gate)
    pid = (f"{side[0].upper()}.{entry}.{regime}.{exit_mode}."
           f"{int(round(size_frac * 100))}.{vol_gate}.{hour_gate}")

    def rule(f: dict) -> bool:
        return (_safe(efn, f) and _safe(rfn, f)
                and _safe(vfn, f) and _safe(hfn, f))

    label = (f"{side} {entry} / {regime} / {exit_mode} / "
             f"{int(size_frac * 100)}% / ATR {vol_gate} / {hour_gate}")
    return Policy(pid, label, side, entry, regime, exit_mode,
                  float(size_frac), vol_gate, hour_gate, rule)


def all_policies() -> list[Policy]:
    out = []
    for entry, (side, _fn) in ENTRIES.items():
        for regime in REGIME_KEYS:
            for exit_mode in EXIT_MODES:
                for size in SIZE_FRACS:
                    for vol in VOL_KEYS:
                        for hour in SEARCH_HOUR_KEYS:
                            out.append(make_policy(side, entry, regime,
                                                   exit_mode, size, vol, hour))
    return out


def policy_from_spec(spec: dict) -> Policy | None:
    """Rebuild the exact policy from a registry row without trusting its id."""
    try:
        side = str(spec.get("side") or "")
        entry = str(spec.get("entry") or "")
        regime = str(spec.get("regime") or "")
        exit_mode = str(spec.get("exit") or "")
        vol = str(spec.get("vol_gate") or "")
        hour = str(spec.get("hour_gate") or "")
        size = float(spec.get("size_frac"))
        if (side not in SIDES or entry not in ENTRIES
                or ENTRIES[entry][0] != side or regime not in REGIME_KEYS
                or exit_mode not in EXIT_MODES or vol not in VOL_KEYS
                or hour not in HOUR_KEYS or size not in SIZE_FRACS):
            return None
        return make_policy(side, entry, regime, exit_mode, size, vol, hour)
    except (TypeError, ValueError):
        return None


# ── event table and exit resolution ───────────────────────────────────────


def build_base_rows(pairs: dict[str, pd.DataFrame]) -> list[dict]:
    """Every closed bar where at least one entry selector fires.

    Features are computed at that bar's close.  Rows are deliberately light:
    only what a policy filter and its exit resolver need.
    """
    ranks = ee._panel_ranks(pairs)
    rows: list[dict] = []
    for p, df in pairs.items():
        if len(df) < 210:
            continue
        fa = ee.frame_features(df, ee.atr14(df))
        for i in range(200, len(df) - 1):
            t = df.index[i]
            f = ee.features_at(fa, ranks, t, i)
            keys = [k for k, (_side, fn) in ENTRIES.items()
                    if _safe(fn, f)]
            if keys:
                rows.append({"ts": t, "pair": p, "i": i, "f": f,
                             "entries": keys})
    rows.sort(key=lambda r: (r["ts"], r["pair"]))
    return rows


class Resolver:
    """Resolve one policy's trade from the bar after its signal.

    `fill` decides what price the entry can actually get.  "close" (the
    default) assumes the signal bar's own close; "next_open" only allows the
    open of the bar after it, which is what a decision made at that close can
    reach in a live book.  The stop and target are set from whichever price is
    used, so the gap between the two modes is the cost of fill timing.
    """

    FILLS = ("close", "next_open")

    def __init__(self, pairs: dict[str, pd.DataFrame], cfg,
                 fill: str = "close"):
        if fill not in self.FILLS:
            raise ValueError(f"unknown fill mode {fill!r}")
        self.pairs = pairs
        self.cfg = cfg
        self.fill = fill
        self._a = {p: (df["open"].to_numpy(float),
                       df["low"].to_numpy(float),
                       df["high"].to_numpy(float),
                       df["close"].to_numpy(float),
                       df.index) for p, df in pairs.items()}
        self._atrs = {p: ee.atr14(df).to_numpy(float)
                      for p, df in pairs.items()}
        self.cache: dict[tuple, dict | None] = {}
        self.use_cache = False

    def resolve(self, p: str, i: int, side: str, mode: str) -> dict | None:
        key = (p, i, side, mode, self.fill)
        if self.use_cache and key in self.cache:
            return self.cache[key]
        a = self._a.get(p)
        if a is None:
            self.cache[key] = None
            return None
        op, lo, hi, cl, idx = a
        if i < 0 or i + 1 >= len(cl):
            self.cache[key] = None
            return None
        # the ATR is measured at the signal bar either way: the fill bar comes
        # later, so reading its ATR would be lookahead
        entry = (float(op[i + 1]) if self.fill == "next_open"
                 else float(cl[i]))
        atr = float(self._atrs[p][i])
        if not (entry > 0 and math.isfinite(atr) and atr > 0):
            self.cache[key] = None
            return None
        risk = atr * float(getattr(self.cfg, "atr_multiplier", 1.5))
        rr = float(getattr(self.cfg, "risk_reward_ratio", 2.5))
        if side == "long":
            stop, tp = entry - risk, entry + risk * rr
        else:
            stop, tp = entry + risk, entry - risk * rr
        if risk <= 0:
            self.cache[key] = None
            return None
        be = mode == "breakeven"
        chand = mode == "chandelier"
        if chand:
            tp = math.inf if side == "long" else -math.inf
        cur_stop = stop
        armed = False
        peak = entry
        lim = min(len(cl), i + 1 + MAX_HOLD)
        out = None
        for j in range(i + 1, lim):
            o, l, h = float(op[j]), float(lo[j]), float(hi[j])
            if side == "long":
                if o <= cur_stop:
                    out = (j, o, "stop"); break
                if o >= tp:
                    out = (j, o, "target"); break
                if l <= cur_stop:
                    out = (j, cur_stop, "stop"); break
                if h >= tp:
                    out = (j, tp, "target"); break
                if be and not armed and h >= entry + risk:
                    armed, cur_stop = True, max(cur_stop, entry)
                if chand:
                    peak = max(peak, h)
                    if h >= entry + risk:
                        cur_stop = max(cur_stop, peak - atr * 1.5)
            else:
                if o >= cur_stop:
                    out = (j, o, "stop"); break
                if o <= tp:
                    out = (j, o, "target"); break
                if h >= cur_stop:
                    out = (j, cur_stop, "stop"); break
                if l <= tp:
                    out = (j, tp, "target"); break
                if be and not armed and l <= entry - risk:
                    armed, cur_stop = True, min(cur_stop, entry)
                if chand:
                    peak = min(peak, l)
                    if l <= entry - risk:
                        cur_stop = min(cur_stop, peak + atr * 1.5)
        if out is None:
            j = lim - 1
            out = (j, float(cl[j]), "time")
        j, px, why = out
        net = ((px / entry - 1.0) * 100.0 if side == "long"
               else (entry / px - 1.0) * 100.0) - ee.FEE_RT_PCT
        res = {"exit_i": j, "exit_ts": idx[j], "px": px, "why": why,
               "net": net, "hold": j - i, "entry": entry,
               "stop": stop, "tp": tp}
        if self.use_cache:
            self.cache[key] = res
        return res


# ── statistics / search ──────────────────────────────────────────────────


def _selected(rows: list[dict], entry: str, regime: str,
              vol: str, hour: str) -> list[dict]:
    rfn, vfn, hfn = regime_rule(regime), vol_rule(vol), hour_rule(hour)
    return [r for r in rows if entry in r["entries"]
            and _safe(rfn, r["f"]) and _safe(vfn, r["f"])
            and _safe(hfn, r["f"])]


def evaluate_policies(rows: list[dict], resolver: Resolver,
                      policies: list[Policy] | None = None) -> list[dict]:
    """Group first, then resolve each exit once per group.

    A group is entry x regime x ATR gate x hour gate.  Exit modes share the
    selected rows, and the two sizing variants share the same trade returns;
    only the chain replay needs the size.  That keeps 2,880 policies from
    re-scanning 210k bars 5,400 times.
    """
    policies = policies or all_policies()
    wanted = {(p.entry, p.regime, p.vol_gate, p.hour_gate, p.exit_mode,
               p.size_frac) for p in policies}
    windows = ee.split_rows(rows)
    window_ids = [{id(r) for r in w} for w in windows]
    out: list[dict] = []
    for entry, (side, _efn) in ENTRIES.items():
        print(f"  entry {entry} ({side})", flush=True)
        for regime in REGIME_KEYS:
            base = _selected(rows, entry, regime, "any", "any")
            if len(base) < 2:
                continue
            rfn = regime_rule(regime)
            base = [r for r in base if _safe(rfn, r["f"])]
            for vol in VOL_KEYS:
                vfn = vol_rule(vol)
                vsel = [r for r in base if _safe(vfn, r["f"])]
                if len(vsel) < 2:
                    continue
                for hour in SEARCH_HOUR_KEYS:
                    hfn = hour_rule(hour)
                    chosen = [r for r in vsel if _safe(hfn, r["f"])]
                    if len(chosen) < 2:
                        continue
                    for mode in EXIT_MODES:
                        vals = []
                        ok = True
                        key = (side, mode)
                        for r in chosen:
                            ex = (r.get("exits") or {}).get(key)
                            if ex is None:
                                ex = resolver.resolve(r["pair"], r["i"],
                                                       side, mode)
                                r.setdefault("exits", {})[key] = ex
                            if ex is None:
                                ok = False
                                break
                            vals.append((r, ex))
                        if not ok or not vals:
                            continue
                        stats = [ee._stats([res["net"] for r, res in vals
                                            if id(r) in idx])
                                 for idx in window_ids]
                        if (not stats[0].n or not stats[1].n
                                or not stats[2].n):
                            continue
                        if (stats[0].n < ee.MIN_TRADES
                                or stats[1].n < ee.MIN_TRADES):
                            continue
                        score = min(stats[0].mean or -99,
                                    stats[1].mean or -99)
                        for size in SIZE_FRACS:
                            if (entry, regime, vol, hour, mode,
                                    size) not in wanted:
                                continue
                            pol = make_policy(side, entry, regime, mode,
                                               size, vol, hour)
                            out.append({
                                "id": pol.id, "label": pol.label,
                                "spec": pol.spec, "side": side,
                                "entry": entry, "regime": regime,
                                "exit": mode, "size_frac": size,
                                "vol_gate": vol, "hour_gate": hour,
                                "n": len(vals), "w1": stats[0].__dict__,
                                "w2": stats[1].__dict__, "w3": stats[2].__dict__,
                                "score": round(score, 4),
                                "screen_positive": bool(
                                    stats[0].mean and stats[0].mean > 0
                                    and stats[1].mean and stats[1].mean > 0),
                                "robust": bool(stats[0].positive
                                               and stats[1].positive
                                               and stats[2].positive),
                                "p": ee._p_value(stats[2].t),
                            })
    q = ee._bh_q([r["p"] for r in out])
    for row, qv in zip(out, q):
        row["q"] = round(qv, 4)
    seen = {r["id"] for r in out}
    for pol in policies:
        if pol.id in seen:
            continue
        out.append({
            "id": pol.id, "label": pol.label, "spec": pol.spec,
            "side": pol.side, "entry": pol.entry, "regime": pol.regime,
            "exit": pol.exit_mode, "size_frac": pol.size_frac,
            "vol_gate": pol.vol_gate, "hour_gate": pol.hour_gate,
            "n": 0, "w1": {}, "w2": {}, "w3": {}, "score": -99.0,
            "screen_positive": False, "robust": False, "p": 1.0, "q": 1.0,
            "insufficient": True,
        })
    out.sort(key=lambda r: (not r["robust"], not r["screen_positive"],
                            -r["score"], -(r["w3"].get("mean") or -99)))
    return out


def stress_passes(policy: Policy, rows: list[dict],
                  resolver: "Resolver") -> dict:
    """Chain the same policy under a perturbed fill, for the promotion rule.

    The resolver carries the arm (e.g. `fill="next_open"`); the policy's own
    exit mode is untouched.  A promotion has to survive this, not just the
    signal bar's close.
    """
    return chain_result(policy, selected_for(policy, rows, resolver))


def selected_for(policy: Policy, rows: list[dict],
                 resolver: Resolver) -> list[tuple[dict, dict]]:
    """Rebuild the exact selected rows for the chain shortlist."""
    chosen = _selected(rows, policy.entry, policy.regime,
                       policy.vol_gate, policy.hour_gate)
    # the cache lives on the row, so the fill mode must be part of the key or
    # a close-filled exit would be reused for a next-open run
    key = (policy.side, policy.exit_mode, resolver.fill)
    out = []
    for r in chosen:
        res = (r.get("exits") or {}).get(key)
        if res is None:
            res = resolver.resolve(r["pair"], r["i"], policy.side,
                                    policy.exit_mode)
            r.setdefault("exits", {})[key] = res
        if res is not None:
            out.append((r, res))
    return out


def chain_result(policy: Policy, selected: list[tuple[dict, dict]]) -> dict:
    """One carried contest through all three windows, one position at a time.

    A window is *qualified* when it closes above its start, took at least
    `MIN_CHAIN_TRADES` chained trades, and never touched the 3% death line.
    The original +12% contest target is still measured (`contest_passed`) but
    it is not the promotion definition: at live 9%/6% position sizing the
    one-slot chain cannot reach +12% in these windows even with a real edge.
    Promotion means repeatable positive expectancy under the live risk path.
    """
    bounds = ([pd.Timestamp(a, tz="UTC") for a, _ in ee.WINDOWS]
              + [pd.Timestamp(ee.WINDOWS[-1][1], tz="UTC")])
    bal = ee.CHAIN_START
    windows = [{"start": bal, "min": bal, "max": bal, "peak": bal,
                "passed": None, "died": False, "death_ts": None,
                "trades": 0, "wins": 0, "losses": 0, "net": 0.0,
                "gross_win": 0.0, "gross_loss": 0.0,
                "max_dd": 0.0, "max_dd_pct": 0.0, "day_pnl": {},
                "pnl_pcts": []}]
    wi = 0
    heap: list[tuple] = []
    busy = False

    def book_exit(ts):
        nonlocal bal, busy
        _, _, pnl, row, spend = heapq.heappop(heap)
        busy = False
        bal += pnl
        w = windows[wi]
        w["min"] = min(w["min"], bal)
        w["max"] = max(w["max"], bal)
        w["trades"] += 1
        w["net"] += pnl
        if pnl > 0:
            w["wins"] += 1
            w["gross_win"] += pnl
        elif pnl < 0:
            w["losses"] += 1
            w["gross_loss"] += pnl
        if spend:
            w["pnl_pcts"].append(pnl / spend * 100.0)
        day = pd.Timestamp(ts).date().isoformat()
        w["day_pnl"][day] = w["day_pnl"].get(day, 0.0) + pnl
        w["peak"] = max(w["peak"], bal)
        dd = max(0.0, w["peak"] - bal)
        w["max_dd"] = max(w["max_dd"], dd)
        w["max_dd_pct"] = max(w["max_dd_pct"],
                               dd / w["peak"] * 100.0 if w["peak"] else 0.0)
        if w["passed"] is None and bal >= w["start"] * 1.12:
            w["passed"] = True
        if w["passed"] is None and bal <= w["start"] * 0.97:
            w["passed"] = False
            w["died"] = True
            w["death_ts"] = str(ts)

    def advance_to(t):
        nonlocal wi
        while True:
            nb = bounds[wi + 1] if wi + 1 < len(bounds) else None
            ne = heap[0][0] if heap else None
            cands = [x for x in (nb, ne) if x is not None]
            if not cands:
                return
            nxt = min(cands)
            if nxt > t:
                return
            if ne is not None and ne == nxt:
                book_exit(nxt)
                continue
            if wi + 2 < len(bounds) and nb <= t:
                windows.append({"start": bal, "min": bal, "max": bal,
                                "peak": bal, "passed": None, "died": False,
                                "death_ts": None, "trades": 0, "wins": 0,
                                "losses": 0, "net": 0.0, "gross_win": 0.0,
                                "gross_loss": 0.0, "max_dd": 0.0,
                                "max_dd_pct": 0.0, "day_pnl": {},
                                "pnl_pcts": []})
                wi += 1
                continue
            return

    selected = sorted(selected, key=lambda pair: pd.Timestamp(pair[0]["ts"]))
    for r, res in selected:
        t = pd.Timestamp(r["ts"])
        if t < bounds[0] or t >= bounds[-1]:
            continue
        advance_to(t)
        if windows[wi]["passed"] is not None or busy:
            continue
        exit_ts = pd.Timestamp(res["exit_ts"])
        if exit_ts >= bounds[-1]:
            continue
        spend = bal * policy.size_frac
        pnl = spend * (res["net"] / 100.0)
        busy = True
        heapq.heappush(heap, (exit_ts, len(heap), pnl, r, spend))
    while heap:
        _t = heap[0][0]
        advance_to(_t)
        if heap:
            book_exit(_t)
    advance_to(bounds[-1])
    out = []
    all_day: dict[str, float] = {}
    for k, w in enumerate(windows):
        close = w["net"] + w["start"]
        trades = int(w["trades"])
        wins, losses = int(w["wins"]), int(w["losses"])
        worst_day = min(w["day_pnl"].values()) if w["day_pnl"] else 0.0
        profitable = close > w["start"]
        contest_passed = bool(w["passed"])
        headroom = w["min"] - w["start"] * 0.97
        clears_margin = bool(
            w["start"] and (close - w["start"]) >=
            w["start"] * PROMOTION_MIN_WINDOW_PCT / 100.0)
        qualified = bool(
            profitable and clears_margin and not w["died"]
            and trades >= MIN_CHAIN_TRADES
            and w["max_dd_pct"] < PROMOTION_MAX_DD_PCT
            and headroom >= w["start"] * MIN_DEATH_HEADROOM_FRAC)
        for day, pnl in w["day_pnl"].items():
            all_day[day] = all_day.get(day, 0.0) + pnl
        out.append({
            "window": f"{ee.WINDOWS[k][0]} -> {ee.WINDOWS[k][1]}",
            "start_balance": round(w["start"], 2),
            "close_balance": round(close, 2),
            "trades": trades, "wins": wins, "losses": losses,
            "win_rate": round(wins / trades, 4) if trades else None,
            "net": round(w["net"], 2),
            "return_pct": round((close / w["start"] - 1.0) * 100.0, 3)
            if w["start"] else None,
            "expectancy_usd": round(w["net"] / trades, 2) if trades else None,
            "expectancy_pct": round(sum(w["pnl_pcts"]) / len(w["pnl_pcts"]), 4)
            if w["pnl_pcts"] else None,
            "profit_factor": round(w["gross_win"] / abs(w["gross_loss"]), 3)
            if w["gross_loss"] else None,
            "max_drawdown_usd": round(w["max_dd"], 2),
            "max_drawdown_pct": round(w["max_dd_pct"], 3),
            "worst_day_usd": round(worst_day, 2),
            "worst_day_pct_of_start": round(worst_day / w["start"] * 100.0, 3)
            if w["start"] else None,
            "min_balance": round(w["min"], 2),
            "max_balance": round(w["max"], 2),
            "headroom_to_death": round(headroom, 2),
            "headroom_pct_of_start": round(
                headroom / w["start"] * 100.0, 3) if w["start"] else None,
            "profitable": profitable,
            "clears_margin": clears_margin,
            "min_window_pct": PROMOTION_MIN_WINDOW_PCT,
            "qualified": qualified,
            "passed": contest_passed,
            "contest_passed": contest_passed,
            "insufficient_trades": bool(trades < MIN_CHAIN_TRADES),
            "died": bool(w["died"]), "death_ts": w["death_ts"],
        })
    passes = sum(1 for w in out if w["qualified"])
    contest_passes = sum(1 for w in out if w["contest_passed"])
    died = any(w["died"] for w in out)
    total_trades = sum(w["trades"] for w in out)
    total_wins = sum(w["wins"] for w in out)
    total_losses = sum(w["losses"] for w in out)
    pnl_pcts = [x for w in windows for x in w["pnl_pcts"]]
    total_net = bal - ee.CHAIN_START
    worst_day = min(all_day.values()) if all_day else 0.0
    max_dd_pct = max((w["max_drawdown_pct"] or 0.0) for w in out) if out else 0.0
    max_dd_usd = max((w["max_drawdown_usd"] or 0.0) for w in out) if out else 0.0
    if died:
        status, reason = "rejected", "a chained window touched the death line"
    elif passes == 3 and policy.side == "long":
        status, reason = (
            "promoted",
            f"all three chained windows profitable with >={MIN_CHAIN_TRADES} "
            f"trades, max DD <{PROMOTION_MAX_DD_PCT:g}% and no death "
            f"(contest target {contest_passes}/3)")
    elif passes == 3:
        status, reason = ("research", "3/3 but short is not executable on the spot contest")
    elif passes == 2:
        status, reason = "watch", "two of three windows profitable; needs forward evidence"
    elif passes == 1:
        status, reason = "watch", "one window profitable; not enough for live risk"
    elif contest_passes:
        status, reason = (
            "rejected",
            f"contest target hit {contest_passes}/3 but the chained sample "
            f"was below {MIN_CHAIN_TRADES} trades in a window")
    else:
        status, reason = "rejected", "no qualifying profitable window"
    worst = min(out, key=lambda w: w["headroom_to_death"]) if out else {}
    return {"id": policy.id, "label": policy.label, "spec": policy.spec,
            "windows": out, "passes": passes,
            "contest_passes": contest_passes, "died": bool(died),
            "final": round(bal, 2), "final_return_pct": round(
                total_net / ee.CHAIN_START * 100.0, 3),
            "status": status, "status_reason": reason,
            "worst_window": worst.get("window"),
            "worst_headroom": worst.get("headroom_to_death"),
            "death_window": next((w["window"] for w in out if w["died"]), None),
            "trades": total_trades, "wins": total_wins,
            "losses": total_losses,
            "win_rate": round(total_wins / total_trades, 4)
            if total_trades else None,
            "net": round(total_net, 2),
            "expectancy_usd": round(total_net / total_trades, 2)
            if total_trades else None,
            "expectancy_pct": round(sum(pnl_pcts) / len(pnl_pcts), 4)
            if pnl_pcts else None,
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_pct": round(max_dd_pct, 3),
            "min_headroom_pct": round(min(
                (w["headroom_to_death"] / w["start_balance"] * 100.0)
                for w in out), 3) if out else None,
            "worst_day_usd": round(worst_day, 2),
            "worst_day_pct_of_start": round(
                worst_day / ee.CHAIN_START * 100.0, 3),
            "min_chain_trades": MIN_CHAIN_TRADES}
