# hindsight_scorer.py -- THE HINDSIGHT SCORER + GATE GRADER (user spec,
# Sep 5: "Hindsight Scorer + Gate Grader + Entry-Path Optimizer").
#
# THE ARCHITECTURE IT SERVES (user rules, Sep 5):
#   FIRE-TIME RULE: the deep scan MUST run at fire-time. A failed/null/
#     invalid read = NO popup, NO entry, NO ledger write (fail-closed --
#     the JUP guarantee, kept).
#   ENTRY-TIME RULE: once the scan ran, the BRAIN'S NUMBER IS ADVISORY --
#     it sizes the entry, it never blocks it. Entry is governed by the
#     goal gate, the stale-move guard, trend/tide/book/rotation gates,
#     and (real money) the ladder, head-to-head, and carry cap.
#   SIZE SCALER (the user's bucket table): >=85 -> 1.00x, 75-84 -> 0.75x,
#     65-74 -> 0.50x, 55-64 -> 0.33x, 45-54 -> 0.20x, <45 -> 0.10x
#     (minimum viable size -- no trade is ever sized at zero; the scorer
#     gets full data across the spectrum).
#     Continuous alternative (documented, not default):
#       size = maxSize * (0.1 + 0.9 * brain / 100)
#
# WHAT THIS MODULE OWNS:
#   1. brain_size_mult()      -- the ONE bucket table every consumer reads
#                                (scanner popups, mover popups, the QC).
#   2. fire_features()        -- the fire-time feature vector from 5-min
#                                candles (momentum, volume burst, candle
#                                velocity, wick structure, trend context).
#   3. outcome()              -- the hindsight verdict: speed bucket
#                                (fast/medium/slow/never), payoff bucket
#                                (winner/grinder/dud), MFE, drawdown.
#   4. backfill               -- rebuild features+outcomes for every
#                                suppression-log / snapshot event from
#                                Kraken candles (the Sep 4-5 history
#                                becomes the first dataset TODAY).
#   5. extract_rules          -- correlate fire-time features vs outcomes;
#                                emit hindsight_rules.json (the future
#                                dual-funnel router: fast vs slow).
#   6. grade_gates            -- the weekly gate report: winners buried,
#                                duds prevented, net PnL impact, per
#                                speed bucket, with floor recommendations.
#
# CLI:  python hindsight_scorer.py backfill | rules | grade | all
# Output files: snapshot_outcomes.jsonl, hindsight_rules.json,
#               gate_grades_<date>.txt -- all ASCII, PS 5.1 safe.

import json
import os
import time
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOTS_FILE = os.path.join(BASE, "signal_snapshots.jsonl")
OUTCOMES_FILE = os.path.join(BASE, "snapshot_outcomes.jsonl")
RULES_FILE = os.path.join(BASE, "hindsight_rules.json")
SUPPRESSIONS_FILE = os.path.join(BASE, "popup_suppressions.jsonl")

# ── THE SIZE SCALER (user rule, Sep 5) ─────────────────────────────────
# Buckets first (the user's table); the continuous formula is documented
# for reference. An unreadable number keeps the MINIMUM VIABLE size --
# the scan ran (fail-closed above us guarantees that), so the trade
# stands, sized tiny.
BRAIN_SIZE_TABLE = (
    (85.0, 1.00),
    (75.0, 0.75),
    (65.0, 0.50),
    (55.0, 0.33),
    (45.0, 0.20),
    (0.0, 0.10),
)


# ── THE FAST FUNNEL ROUTER (user rule, Sep 6: "we have enough data to
# act now. Get it done.") ─────────────────────────────────────────────
# The dual-funnel architecture: FAST = immediate entry, minimal gates
# (the worth floor waives; the 3% guard, the popup cap, the death line,
# too-thin, tide-OUT and the rotation lock all STAND); SLOW = the full
# stack, exactly as it runs today. The rule source: hindsight_rules.json's
# fast_route keys (re-extracted weekly by the scorer, read LIVE — no
# restart); until a tercile clears the support-15/lift-1.3 bar, the
# PROVISIONAL data-backed rule stands: momentum_1h >= +0.4% — the top
# tercile of the first 109-event dataset (73% winners, ZERO duds, lift
# 1.28 vs the 1.30 bar). AND-semantics when multiple rules land.
FAST_ROUTE_PROVISIONAL = {"momentum_1h": 0.4}


def fast_route(features) -> tuple[bool, str]:
    """(is_fast, why) — route one fire-time feature vector. Features
    missing/unreadable -> (False, reason): the router fails CLOSED to
    the slow funnel (a broken read never buys speed)."""
    try:
        if not isinstance(features, dict) or not features:
            return False, "no fire-time features"
        rules = {}
        try:
            rules = json.loads(open(RULES_FILE, encoding="utf-8").read()
                               ).get("fast_route") or {}
        except Exception:
            rules = {}
        if not rules:
            rules = FAST_ROUTE_PROVISIONAL
        why = []
        for k, spec in rules.items():
            mn = float(spec.get("min")) if isinstance(spec, dict) else float(spec)
            v = features.get(k)
            if v is None:
                return False, f"{k} missing"
            try:
                v = float(v)
            except (TypeError, ValueError):
                return False, f"{k} unreadable"
            if v < mn:
                return False, f"{k} {v:.2f} < {mn}"
            why.append(f"{k} {v:.2f}≥{mn}")
        src = "rules.json" if rules is not FAST_ROUTE_PROVISIONAL else "provisional"
        return True, f"{src}: " + " & ".join(why)
    except Exception as exc:
        return False, f"router error: {exc}"


def brain_size_mult(conf) -> float:
    """The advisory-brain size multiplier. Monotonic, never zero."""
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return 0.10
    if c != c:  # NaN
        return 0.10
    for floor_pct, mult in BRAIN_SIZE_TABLE:
        if c >= floor_pct:
            return mult
    return 0.10


def brain_size_mult_continuous(conf) -> float:
    """The continuous version: max * (0.1 + 0.9 * brain/100)."""
    try:
        c = max(0.0, min(100.0, float(conf)))
    except (TypeError, ValueError):
        return 0.10
    return round(0.1 + 0.9 * (c / 100.0), 4)


# ── FIRE-TIME FEATURES (5-min candles) ─────────────────────────────────
# rows: [ts_ms, open, high, low, close, volume] -- ccxt order. Pure math,
# no network: the scanner fetches, this computes. All features are
# None-safe (a short/odd series yields None, never an exception).


def fire_features(rows):
    """The fire-time snapshot vector. ~100 5-min candles: 8h of context."""
    out = {}
    try:
        if not rows or len(rows) < 30:
            return {"insufficient": True}
        o = [float(r[1]) for r in rows]
        h = [float(r[2]) for r in rows]
        l = [float(r[3]) for r in rows]
        c = [float(r[4]) for r in rows]
        v = [float(r[5]) for r in rows if len(r) > 5]
        n = len(c)
        px = c[-1]

        # momentum: 15-min and 1-h net move
        out["momentum_15m"] = (px / c[-4] - 1.0) * 100.0 if n >= 4 else None
        out["momentum_1h"] = (px / c[-13] - 1.0) * 100.0 if n >= 13 else None
        # volume burst: last 3 candles vs the ~100-min baseline before them
        base_v = v[-23:-3] if len(v) >= 23 else (v[:-3] if len(v) > 5 else [])
        out["vol_burst"] = (sum(v[-3:]) / 3.0) / (sum(base_v) / len(base_v)) \
            if base_v and sum(base_v) > 0 and len(v) >= 3 else None
        # candle velocity: last candle's body as %/min (5-min candles)
        out["velocity_pct_min"] = ((c[-1] - o[-1]) / o[-1] * 100.0) / 5.0 if o[-1] else None
        # wick structure: body share of the last 3 candles' ranges
        bodies, ranges = [], []
        for i in range(max(0, n - 3), n):
            rng = h[i] - l[i]
            if rng > 0:
                bodies.append(abs(c[i] - o[i]))
                ranges.append(rng)
        out["body_ratio"] = (sum(bodies) / len(bodies)) / (sum(ranges) / len(ranges)) \
            if bodies and ranges else None
        # position: distance from the 4h high / the window low
        hi4 = max(h[-48:]) if n >= 4 else max(h)
        lo_all = min(l)
        out["pos_from_4h_high"] = (px / hi4 - 1.0) * 100.0 if hi4 else None
        out["off_window_low"] = (px / lo_all - 1.0) * 100.0 if lo_all else None
        # trend context: 5m EMA9 vs EMA21 gap
        out["ema_gap"] = _ema_gap(c, 9, 21)
        # realized range over the fetched window
        out["range_pct"] = (max(h) - min(l)) / px * 100.0 if px else None
        out["last_px"] = px
        out["n_candles"] = n
    except Exception:
        return {"error": True}
    return out


def _ema_gap(closes, fast=9, slow=21):
    if len(closes) < slow + 1:
        return None
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    if not ef or not es or closes[-1] == 0:
        return None
    return (ef[-1] - es[-1]) / closes[-1] * 100.0


def _ema(values, period):
    if not values or period <= 0:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


# ── OUTCOMES (hindsight, from forward candles) ─────────────────────────


def outcome(fwd_rows, entry, goal_pct, window_min=180.0):
    """The hindsight verdict for one entry.
    fwd_rows: candles STRICTLY AFTER the entry candle (5-min rows,
    timestamps in MILLISECONDS — ccxt order).
    Returns speed/payoff buckets + continuous stats. entry<=0 -> None."""
    try:
        if entry is None or float(entry) <= 0 or not fwd_rows:
            return None
        entry = float(entry)
        goal = max(0.1, float(goal_pct or 1.5))
        win_ms = max(15.0, min(240.0, float(window_min or 180.0))) * 60.0 * 1000.0
        t0 = float(fwd_rows[0][0])  # the entry moment (~the first fwd open)
        hi, lo, closes = [], [], []
        min_to_goal = None
        for r in fwd_rows:
            if float(r[0]) > t0 + win_ms:
                break
            hh, ll, cc = float(r[2]), float(r[3]), float(r[4])
            hi.append(hh)
            lo.append(ll)
            closes.append(cc)
            if min_to_goal is None and hh >= entry * (1.0 + goal / 100.0):
                min_to_goal = (float(r[0]) - t0) / 60000.0
        if not hi:
            return None
        mfe = (max(hi) / entry - 1.0) * 100.0
        max_dd = (min(lo) / entry - 1.0) * 100.0
        end_move = (closes[-1] / entry - 1.0) * 100.0
        # speed bucket (user spec): fast 0-15, medium 15-90, slow >90/never
        if min_to_goal is None:
            speed = "never"
        elif min_to_goal <= 15.0:
            speed = "fast"
        elif min_to_goal <= 90.0:
            speed = "medium"
        else:
            speed = "slow"
        # payoff bucket: winner = the goal paid in-window; grinder =
        # positive excursion but short of the goal; dud = no real move
        if mfe >= goal:
            payoff = "winner"
        elif mfe >= 0.3:
            payoff = "grinder"
        else:
            payoff = "dud"
        return {"speed": speed, "payoff": payoff,
                "min_to_goal_min": round(min_to_goal, 1) if min_to_goal is not None else None,
                "mfe_pct": round(mfe, 3), "max_dd_pct": round(max_dd, 3),
                "end_move_pct": round(end_move, 3)}
    except Exception:
        return None


# ── BACKFILL: every logged event -> features + outcome ─────────────────


def _fetch_5m(session, pair, since_ms):
    import requests
    kpair = pair.replace("/", "")
    r = session.get("https://api.kraken.com/0/public/OHLC",
                    params={"pair": kpair, "interval": 5, "since": int(since_ms // 1000)},
                    timeout=20)
    data = r.json().get("result", {})
    key = [k for k in data if k != "last"]
    return data[key[0]] if key else []


def _parse_ts(ts):
    return datetime.fromisoformat(ts).timestamp()


def _read_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def backfill(max_events=400, sleep_s=0.35):
    """Rebuild the dataset: every suppression + every fired snapshot gets
    fire-time features (from candles BEFORE ts) and a hindsight outcome
    (candles AFTER ts). One line per event -> snapshot_outcomes.jsonl."""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "scout-hindsight"})
    events = []
    for r in _read_jsonl(SUPPRESSIONS_FILE):
        events.append({"ts": r.get("ts"), "pair": r.get("pair"),
                       "kind": "suppressed", "gate": r.get("gate"),
                       "brain": r.get("brain"), "quick": r.get("quick"),
                       "floor": r.get("floor"), "tide": r.get("tide"),
                       "rot": r.get("rot"), "detail": r.get("detail")})
    for r in _read_jsonl(SNAPSHOTS_FILE):
        if r.get("kind") == "fired":
            events.append({"ts": r.get("ts"), "pair": r.get("pair"),
                           "kind": "fired", "gate": None,
                           "brain": r.get("brain"), "quick": r.get("quick"),
                           "floor": r.get("floor"), "tide": r.get("tide"),
                           "rot": r.get("rot"),
                           "route": r.get("route"),
                           "mult": r.get("mult"), "spend": r.get("spend")})
    # dedupe: ONE event per pair per ROTATION — the honest unit. The
    # rotation lockout means a pair could only ever be entered once per
    # rotation, so a pair buried at 00:41 and again at 00:52 was ONE
    # missed trade, not two (per-minute dedupe inflated winner counts
    # badly on clustered re-suppressions of the same runner).
    seen, uniq = set(), []
    for e in events:
        if not e.get("ts") or not e.get("pair"):
            continue
        try:
            from datetime import datetime as _dt
            _rot = e.get("rot") or _dt.fromisoformat(e["ts"]).strftime("%H")[:2]
        except Exception:
            _rot = e["ts"][:13]
        key = (e["pair"], str(_rot))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    uniq = uniq[-max_events:]
    out, done = [], 0
    for e in uniq:
        try:
            t0 = _parse_ts(e["ts"])
            rows = _fetch_5m(s, e["pair"], t0 - 9 * 3600 * 1000)
            if not rows:
                continue
            pre = [r for r in rows if float(r[0]) <= t0]
            fwd = [r for r in rows if float(r[0]) > t0]
            if not pre or not fwd:
                continue
            feats = fire_features(pre[-100:])
            entry = float(pre[-1][4])
            oc = outcome(fwd, entry, 1.5, 180.0)
            rec = dict(e)
            rec["features"] = feats
            rec["entry"] = entry
            rec["outcome"] = oc
            out.append(rec)
            done += 1
        except Exception:
            continue
        time.sleep(sleep_s)
    with open(OUTCOMES_FILE, "w", encoding="utf-8") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return {"events": len(uniq), "scored": done}


# ── RULE EXTRACTION (the future dual-funnel router) ────────────────────
FEATURE_KEYS = ["momentum_15m", "momentum_1h", "vol_burst", "velocity_pct_min",
                "body_ratio", "pos_from_4h_high", "off_window_low", "ema_gap"]


def extract_rules(min_support=15, lift=1.3):
    """Correlate each fire-time feature with the outcomes. Emits
    hindsight_rules.json: per-feature tercile stats + the best fast-route
    thresholds (the dual-funnel router's inputs). Small data = honest
    'insufficient' verdicts; nothing is forced."""
    recs = [r for r in _read_jsonl(OUTCOMES_FILE) if r.get("outcome")]
    n = len(recs)
    rules = {"generated": datetime.now().isoformat(), "n_events": n,
             "min_support": min_support,
             "overall": _rates(recs),
             "features": {}, "fast_route": {}, "brain_advisory": {}}
    if n >= 3 * min_support:
        for k in FEATURE_KEYS:
            vals = [(float(r["features"][k]), r) for r in recs
                    if r.get("features") and r["features"].get(k) is not None]
            if len(vals) < 3 * min_support:
                rules["features"][k] = {"insufficient": True, "n": len(vals)}
                continue
            vals.sort(key=lambda x: x[0])
            third = len(vals) // 3
            terciles = []
            for name, chunk in (("lo", vals[:third]), ("mid", vals[third:2*third]),
                                ("hi", vals[2*third:])):
                rs = [r for _, r in chunk]
                terciles.append({"band": name,
                                 "range": [round(chunk[0][0], 4), round(chunk[-1][0], 4)],
                                 "n": len(chunk), **_rates(rs)})
            rules["features"][k] = {"terciles": terciles}
            # the fast-route candidate: the hi tercile must lift winner rate
            hi_t = terciles[2]
            base = rules["overall"]["winner_rate"] or 0.0
            if (hi_t["n"] >= min_support and base > 0
                    and (hi_t["winner_rate"] / base) >= lift):
                rules["fast_route"][k] = {
                    "min": hi_t["range"][0], "support": hi_t["n"],
                    "winner_rate": hi_t["winner_rate"],
                    "fast_rate": hi_t["fast_rate"], "lift": round(
                        hi_t["winner_rate"] / base, 2)}
        # the brain's number, graded honestly (the Sep 5 finding: it was
        # anti-correlated on day one -- this tracks it over time)
        hi = [r for r in recs if (r.get("brain") or 0) >= 85]
        lo = [r for r in recs if (r.get("brain") is not None and r["brain"] < 45)]
        if len(hi) >= min_support:
            rules["brain_advisory"]["hi_brain_85+"] = {**_rates(hi), "n": len(hi)}
        if len(lo) >= min_support:
            rules["brain_advisory"]["lo_brain_45-"] = {**_rates(lo), "n": len(lo)}
    else:
        rules["note"] = ("insufficient events for terciles -- the scorer "
                         "accumulates; rules land when n >= %d" % (3 * min_support))
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=1, default=str)
    return rules


def _rates(recs):
    n = len(recs)
    if not n:
        return {"n": 0}
    win = sum(1 for r in recs if r["outcome"].get("payoff") == "winner")
    fast = sum(1 for r in recs if r["outcome"].get("speed") == "fast")
    dud = sum(1 for r in recs if r["outcome"].get("payoff") == "dud")
    return {"n": n, "winner_rate": round(win / n, 3),
            "fast_rate": round(fast / n, 3), "dud_rate": round(dud / n, 3)}


# ── GATE GRADING (the weekly report) ───────────────────────────────────
# Coherent model, honestly labeled: a suppressed WINNER is the profit the
# gate cost (entry * goal); a suppressed loss-shaped event (end < 0) is
# the loss the gate prevented (-entry * |end|); sizes are representative
# ($2,000 -- the guard's current cap) because most suppressions never
# got a sized spend.


def grade_gates(size_usd=2000.0, goal_pct=1.5):
    recs = [r for r in _read_jsonl(OUTCOMES_FILE) if r.get("outcome")]
    gates = {}
    for r in recs:
        g = r.get("gate") or ("fired" if r.get("kind") == "fired" else "?")
        gates.setdefault(g, []).append(r)
    lines = ["GATE GRADES -- %s -- %d scored events, $%.0f representative size"
             % (datetime.now().strftime("%Y-%m-%d %H:%M"), len(recs), size_usd)]
    lines.append("(winner buried = gate cost +%0.1f%% of size; end<0 prevented"
                 " = gate saved; NET>0 = the gate pays for itself)" % goal_pct)
    lines.append("")
    lines.append("%-16s %5s %7s %7s %7s %7s %7s %9s" % (
        "gate", "n", "winners", "fastW", "duds", "prev$ ", "cost$ ", "NET$"))
    for g in sorted(gates, key=lambda k: -len(gates[k])):
        rs = gates[g]
        winners = [r for r in rs if r["outcome"].get("payoff") == "winner"]
        fastw = [r for r in winners if r["outcome"].get("speed") == "fast"]
        duds = [r for r in rs if r["outcome"].get("payoff") == "dud"]
        prevented = sum(-size_usd * r["outcome"]["end_move_pct"] / 100.0
                        for r in rs if r["outcome"].get("end_move_pct", 0) < 0)
        cost = len(winners) * size_usd * goal_pct / 100.0
        lines.append("%-16s %5d %7d %7d %7d %7.0f %7.0f %+9.0f" % (
            g, len(rs), len(winners), len(fastw), len(duds),
            prevented, cost, prevented - cost))
    lines.append("")
    for g in sorted(gates, key=lambda k: -len(gates[k])):
        rs = gates[g]
        winners = [r for r in rs if r["outcome"].get("payoff") == "winner"]
        w = len(winners)
        verdict = _gate_verdict(g, rs, winners)
        pairs = ", ".join(sorted({r["pair"] for r in winners})[:6]) or "-"
        lines.append("%-16s %s (buried winners: %s)" % (g, verdict, pairs))
    out_path = os.path.join(
        BASE, "gate_grades_%s.txt" % datetime.now().strftime("%Y%m%d"))
    with open(out_path, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(lines) + "\n")
    return {"path": out_path, "gates": len(gates), "events": len(recs)}


def _gate_verdict(gate, rs, winners):
    n = len(rs)
    wr = len(winners) / n if n else 0.0
    ends = [r["outcome"].get("end_move_pct", 0.0) for r in rs]
    neg = sum(1 for e in ends if e < 0) / n if n else 0.0
    if gate == "fired":
        return "BASELINE -- what actually fired; compare every gate against this"
    if wr >= 0.45:
        return "LOOSEN -- buried winners at %.0f%%: this gate is costing more than it saves" % (wr * 100)
    if wr >= 0.30:
        return ("CONDITIONAL -- a coin-flip gate (%.0f%% winners buried): make it "
                "size-scaling, not blocking" % (wr * 100))
    if wr < 0.15 and neg >= 0.5:
        return "KEEP -- mostly prevents losses (duds/losses %.0f%%)" % (neg * 100)
    return "WATCH -- not clearly good or bad yet (winners %.0f%%, losers %.0f%%)" % (wr * 100, neg * 100)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("backfill", "all"):
        print("backfill:", backfill())
    if cmd in ("rules", "all"):
        r = extract_rules()
        print("rules: n=%s fast_route_keys=%s" % (r.get("n_events"), list(r.get("fast_route", {}).keys())))
    if cmd in ("grade", "all"):
        print("grades:", grade_gates())
