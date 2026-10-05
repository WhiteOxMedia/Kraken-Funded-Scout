# climb_out.py -- THE CLIMB-OUT SYSTEM (user spec, Sep 6: "Find swing
# trades that avoid danger zones: no trades near major resistance, no
# trades in chop, no trades when liquidity is thin. Only show setups
# with clear escape routes.") -- the merged spec: the four-layer
# architecture, no fixed thresholds anywhere (every check is a
# percentile against the coin's own history, scaled by context, graded
# by outcomes weekly).
#
# LAYER 1 -- THE LEVEL MAP: per-coin persistent structure memory
#   (climb_levels.json). Pivot highs/lows (k-neighbor rule), each level
#   scored by touches x recency x outcome (broke levels decay, held
#   levels strengthen, stale levels fade). "Major" is a strength score
#   in the coin's own terms, not a fixed percentage.
# LAYER 2 -- THE THREE GATES:
#   CLEARANCE: room to the nearest major ceiling >= goal x multiplier,
#     where the multiplier moves with the tide (RISING x1.5 / FLAT x2.0
#     / OUT x3.0), the account floor (+0.5x near the death line), the
#     coin's break history (levels that break often -> easier), the
#     book (thin -> x1.25: a narrower door needs more runway), and the
#     strictness dial. COILS: runway beyond the ceiling >= 1x goal.
#   ER (not chop): the efficiency ratio as a PERCENTILE of this coin's
#     trailing two weeks. Squeezed vol + low ER = a coil (allowed --
#     Sure Thing territory); normal vol + low ER = chop (reject). The
#     tide bends the bar: RISING eases, OUT tightens.
#   FLOOR: structural support below entry from the map, with direction
#     (rising allows a smaller cushion, flat/sinking demand more); the
#     stop goes STRUCTURAL (below the floor minus an ATR wick buffer);
#     the exit door is the BID side of the book (it must carry the
#     exit at size).
# LAYER 3 -- SELF-AWARENESS: every gate metric logs to the snapshot
#   dataset (fired AND buried); the weekly gate report grades the
#   climb gates like every other gate (LOOSEN / KEEP / CONDITIONAL /
#   TIGHTEN); the thresholds live in climb_rules.json, read LIVE by
#   the scanner -- adjustments apply without restarts.
# LAYER 4 -- THE DIAL: the strictness multiplier (0.5x-2.0x, the
#   sidebar dial in console_state.json) scales every climb multiplier.
#
# HONEST NOTE: adaptive gates can adapt into cowardice -- burying
# everything while the account bleeds by inaction. The safeguards: the
# scorer's LOOSEN verdict, the dial, and every burial logged + priced
# (the discipline score's SYSTEM BURIAL component feels each one).

import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.path.join(BASE, "climb_levels.json")
RULES_FILE = os.path.join(BASE, "climb_rules.json")

# ── THE TUNING (defaults; climb_rules.json overrides LIVE) ───────────
DEFAULT_RULES = {
    "tide_mult": {"RISING": 1.5, "FLAT": 2.0, "OUT": 3.0},
    "death_add": 0.5,          # + this when the account sits near the line
    "coil_mult": 1.0,          # coils: runway beyond the ceiling >= 1x goal
    "er_chop_pctile": 30.0,    # below this (and not squeezed) = chop
    "er_tide_ease": 10.0,      # RISING eases the bar by this many points
    "er_tide_tighten": 10.0,   # OUT tightens it
    "squeeze_pctile": 30.0,    # calmest this fraction of windows = squeezed
    "floor_cushion": {"rising": 0.5, "flat": 1.0, "sinking": 1.5},
    "floor_max_goal_x": 3.0,   # no identifiable floor within this = mid-air
    "thin_penalty": 1.25,      # thin book -> clearance requirement x this
    "major_strength": 2.0,     # a level is MAJOR at this strength
    "door_frac": 0.5,          # the bid side must carry >= this x the spend
}
PIVOT_K = 3                # neighbors each side for a pivot
LEVEL_TOL_PCT = 0.3        # pivots within this merge into one level
RECENCY_HALF_DAYS = 7.0    # strength decay half-life
ER_WINDOW = 24             # candles per ER window (1h candles -> 24h)
ER_STEP = 6
FETCH_1H = 336             # 14 days of 1h candles -- the coin history
MAX_LEVELS_PER_SIDE = 12
MAP_MAX_PAIRS = 80


def load_rules() -> dict:
    try:
        r = json.loads(open(RULES_FILE, encoding="utf-8").read())
        merged = dict(DEFAULT_RULES)
        merged.update(r)
        return merged
    except Exception:
        return dict(DEFAULT_RULES)


def _load_map() -> dict:
    try:
        return json.loads(open(MAP_FILE, encoding="utf-8").read())
    except Exception:
        return {}


def _save_map(m: dict):
    try:
        tmp = MAP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(m, indent=1))
        os.replace(tmp, MAP_FILE)
    except Exception:
        pass


# ── LAYER 1: THE LEVEL MAP ────────────────────────────────────────────
def detect_pivots(rows):
    """(high_pivots, low_pivots) — (index, price) pairs. rows:
    [ts,o,h,l,c,v] 1h candles."""
    h = [float(r[2]) for r in rows]
    l = [float(r[3]) for r in rows]
    hi_p, lo_p = [], []
    n = len(rows)
    for i in range(PIVOT_K, n - PIVOT_K):
        if all(h[i] > h[i + j] and h[i] > h[i - j] for j in range(1, PIVOT_K + 1)):
            hi_p.append((i, h[i]))
        if all(l[i] < l[i + j] and l[i] < l[i - j] for j in range(1, PIVOT_K + 1)):
            lo_p.append((i, l[i]))
    return hi_p, lo_p


def _level_strength(rec, now_ms):
    """touches x recency x outcome. Broken levels decay hard; held
    levels strengthen; stale levels fade."""
    try:
        age_days = max(0.0, (now_ms - float(rec.get("last_ms") or 0)) / 86400000.0)
        recency = 0.5 ** (age_days / RECENCY_HALF_DAYS)
        touches = float(rec.get("touches") or 1)
        held = float(rec.get("held") or 0)
        broke = min(int(rec.get("broke") or 0), 2)
        return round((touches + 0.5 * held) * recency * (0.3 ** broke), 3)
    except Exception:
        return 0.0


def update_map(pair: str, rows) -> dict:
    """Fold a fresh 14-day fetch into the coin's persistent level map.
    Returns the pair's map entry. Never raises."""
    try:
        now_ms = float(rows[-1][0]) if rows else time.time() * 1000.0
        hi_p, lo_p = detect_pivots(rows or [])
        m = _load_map()
        entry = m.get(pair) or {"updated": now_ms, "levels": []}
        levels = entry.get("levels") or []
        # merge the fresh pivots into existing levels (or create them)
        for side, pivots in (("high", hi_p), ("low", lo_p)):
            for idx, px in pivots:
                ts = float(rows[idx][0])
                match = None
                for lv in levels:
                    if lv.get("side") == side and \
                            abs(float(lv["price"]) - px) / px * 100.0 <= LEVEL_TOL_PCT:
                        match = lv
                        break
                if match:
                    match["touches"] = int(match.get("touches") or 1) + 1
                    match["last_ms"] = ts
                    match["last_price"] = px
                else:
                    levels.append({"side": side, "price": px, "touches": 1,
                                   "held": 0, "broke": 0, "last_ms": ts,
                                   "last_price": px})
        # outcomes since each level's LAST TOUCH (not the map's last
        # update — a level formed in the tail must not be graded against
        # candles from before it existed: history below a fresh floor is
        # not a break of it)
        for lv in levels:
            _last_ms = float(lv.get("last_ms") or 0)
            seg = [r for r in rows if float(r[0]) > _last_ms] if _last_ms else []
            if not seg:
                continue
            px = float(lv["price"])
            if lv["side"] == "high":
                if any(float(r[4]) > px * 1.005 for r in seg):
                    lv["broke"] = int(lv.get("broke") or 0) + 1
                elif any(float(r[2]) >= px * 0.9985 and float(r[4]) < px for r in seg):
                    lv["held"] = int(lv.get("held") or 0) + 1
            else:
                if any(float(r[4]) < px * 0.995 for r in seg):
                    lv["broke"] = int(lv.get("broke") or 0) + 1
                elif any(float(r[3]) <= px * 1.0015 and float(r[4]) > px for r in seg):
                    lv["held"] = int(lv.get("held") or 0) + 1
        # strength + prune
        for lv in levels:
            lv["strength"] = _level_strength(lv, now_ms)
        levels = [lv for lv in levels if lv["strength"] >= 0.4]
        levels.sort(key=lambda x: -x["strength"])
        levels = levels[:2 * MAX_LEVELS_PER_SIDE]
        entry["levels"] = levels
        entry["updated"] = now_ms
        m[pair] = entry
        # keep the file bounded
        if len(m) > MAP_MAX_PAIRS:
            for k in sorted(m, key=lambda k: float(m[k].get("updated") or 0)):
                m.pop(k)
                if len(m) <= MAP_MAX_PAIRS:
                    break
        _save_map(m)
        return entry
    except Exception:
        return {"levels": [], "updated": 0}


# ── THE METRICS ───────────────────────────────────────────────────────
def efficiency_ratio(closes, window=ER_WINDOW):
    """|net move| / total path — Kaufman ER. 1.0 = a straight line,
    0.0 = traveled far and went nowhere (chop)."""
    try:
        seg = [float(c) for c in closes[-(window + 1):]]
        if len(seg) < 3:
            return None
        net = abs(seg[-1] - seg[0])
        path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
        return (net / path) if path > 0 else 0.0
    except Exception:
        return None


def _er_percentile(closes):
    """The current ER vs this coin's own trailing distribution."""
    try:
        vals = [float(c) for c in closes]
        if len(vals) < ER_WINDOW * 4:
            return None, None
        ers = []
        for i in range(0, len(vals) - ER_WINDOW, ER_STEP):
            seg = vals[i:i + ER_WINDOW + 1]
            if len(seg) < 3:
                continue
            net = abs(seg[-1] - seg[0])
            path = sum(abs(seg[j] - seg[j - 1]) for j in range(1, len(seg)))
            if path > 0:
                ers.append(net / path)
        if not ers:
            return None, None
        now = efficiency_ratio(vals)
        if now is None:
            return None, None
        pctile = sum(1 for x in ers if now >= x) / len(ers)
        return now, round(pctile, 3)
    except Exception:
        return None, None


def _vol_percentile(closes):
    """The realized vol of the last window vs the trailing windows —
    the squeeze read (the coil-vs-chop disambiguator)."""
    try:
        import statistics as st
        vals = [float(c) for c in closes]
        if len(vals) < ER_WINDOW * 4:
            return None
        def rv(seg):
            rets = [(seg[i] / seg[i - 1] - 1.0) for i in range(1, len(seg))
                    if seg[i - 1] > 0]
            return st.pstdev(rets) if len(rets) >= 2 else None
        now = rv(vals[-(ER_WINDOW + 1):])
        base = [rv(vals[i:i + ER_WINDOW + 1])
                for i in range(0, len(vals) - ER_WINDOW, ER_STEP)]
        base = [x for x in base if x is not None]
        if now is None or not base:
            return None
        return round(sum(1 for x in base if now <= x) / len(base), 3)
    except Exception:
        return None


def _atr(rows, period=14):
    try:
        trs = []
        for i in range(1, len(rows)):
            h, l, c_prev = float(rows[i][2]), float(rows[i][3]), float(rows[i - 1][4])
            trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period
    except Exception:
        return None


# ── LAYER 2: THE GATES (one entry point) ─────────────────────────────
def climb_out_gate(exchange, pair: str, entry: float, goal_pct: float,
                   spend: float, is_coil: bool = False,
                   coil_ceiling: float | None = None,
                   tide_state: str = "FLAT", near_death: bool = False,
                   balance: float | None = None,
                   strictness: float = 1.0) -> dict:
    """The full climb-out read. Returns:
      ok (bool), fail_gate, fail_reason, escape_line, output_block,
      features (for the snapshot dataset), adjustments (what applied).
    Never raises -- a broken read FAILS OPEN (ok=True, degraded=True):
    the climb-out gates protect quality, not existence; the death-line
    guard is the gate that never yields."""
    out = {"ok": True, "fail_gate": None, "fail_reason": "",
           "escape_line": "", "output_block": "", "features": {},
           "adjustments": [], "degraded": False}
    try:
        rules = load_rules()
        rows = exchange.fetch_ohlcv(pair, timeframe="1h", limit=FETCH_1H) or []
        if len(rows) < ER_WINDOW * 4:
            out["degraded"] = True
            out["output_block"] = "CLIMB-OUT: insufficient history — fail-open\n"
            return out
        px = float(rows[-1][4])
        entry = float(entry or px)
        goal = max(0.1, float(goal_pct or 1.5))

        # LAYER 1: refresh the map
        map_entry = update_map(pair, rows)
        levels = map_entry.get("levels") or []
        major = float(rules["major_strength"])
        ceilings = sorted([lv for lv in levels
                           if lv["side"] == "high"
                           and float(lv["price"]) > entry
                           and float(lv.get("strength") or 0) >= major],
                          key=lambda lv: float(lv["price"]))
        floors = sorted([lv for lv in levels
                         if lv["side"] == "low"
                         and float(lv["price"]) < entry
                         and float(lv.get("strength") or 0) >= major],
                        key=lambda lv: -float(lv["price"]))

        # ── GATE 1: CLEARANCE (room to climb) ──
        adj = []
        if is_coil and coil_ceiling:
            ceil_px = float(coil_ceiling)
            room = (ceil_px - entry) / entry * 100.0 if entry > 0 else 0.0
            mult = float(rules["coil_mult"])
            adj.append(f"coil runway x{mult:.1f}")
        elif ceilings:
            ceil_px = float(ceilings[0]["price"])
            room = (ceil_px - entry) / entry * 100.0 if entry > 0 else 0.0
            tm = float(rules["tide_mult"].get(tide_state or "FLAT", 2.0))
            mult = tm
            adj.append(f"tide {tide_state} x{tm:.1f}")
            if near_death:
                mult += float(rules["death_add"])
                adj.append(f"death line +{rules['death_add']:.1f}")
            # the coin's break history: levels that break often -> easier
            broke = sum(int(lv.get("broke") or 0) for lv in levels)
            held = sum(int(lv.get("held") or 0) for lv in levels)
            if broke + held >= 4:
                frac = broke / (broke + held)
                if frac >= 0.5:
                    mult *= 0.75
                    adj.append(f"break history {frac:.0%} -> x0.75")
                elif frac <= 0.2:
                    mult *= 1.25
                    adj.append(f"break history {frac:.0%} -> x1.25")
            if balance and balance > 0:
                try:
                    from execution_tiers import measure_book
                    carry = float((measure_book(exchange, pair) or {})
                                  .get("ask_tob") or 0.0) * 0.20
                    if 0 < carry < 0.5 * balance:
                        mult *= float(rules["thin_penalty"])
                        adj.append(f"thin book x{rules['thin_penalty']:.2f}")
                except Exception:
                    pass
        else:
            ceil_px = None
            room = 15.0  # clear skies (capped for the read)
            mult = 1.0
            adj.append("clear skies (no major ceiling in range)")
        mult *= max(0.1, float(strictness or 1.0))
        if strictness != 1.0:
            adj.append(f"dial x{strictness:.2f}")
        required = goal * mult
        clearance_ok = room >= required

        # ── GATE 2: ER (not chop) ──
        closes = [float(r[4]) for r in rows]
        er_now, er_pct = _er_percentile(closes)
        vol_pct = _vol_percentile(closes)
        bar = float(rules["er_chop_pctile"])
        if (tide_state or "FLAT") == "RISING":
            bar -= float(rules["er_tide_ease"])
            adj.append(f"ER bar eased to {bar:.0f} (rising tide)")
        elif (tide_state or "FLAT") == "OUT":
            bar += float(rules["er_tide_tighten"])
            adj.append(f"ER bar tightened to {bar:.0f} (out tide)")
        # THE DIAL'S SECOND HAND (Oct 4 audit): the strictness dial
        # scales the chop bar about its calibrated anchor — 0.5x eases
        # it, 2.0x tightens it — so the dial owns every gate the help
        # text names. The tide ease/tighten land first; the dial then
        # scales the whole bar.
        if strictness != 1.0:
            bar *= max(0.1, float(strictness))
            adj.append(f"ER bar x{strictness:.1f}")
        squeezed = (vol_pct is not None
                    and vol_pct >= (1.0 - float(rules["squeeze_pctile"]) / 100.0))
        if er_pct is None or vol_pct is None:
            er_ok, er_class = True, "unreadable (fail-open)"
        elif squeezed:
            er_ok, er_class = True, f"COIL (ER {er_pct:.0%} but squeezed vol {vol_pct:.0%})"
        elif er_pct * 100.0 >= bar:
            er_ok, er_class = True, f"TRENDING (ER {er_pct:.0%} ≥ bar {bar:.0f}th pct)"
        else:
            er_ok, er_class = False, f"CHOP (ER {er_pct:.0%} < bar {bar:.0f}th pct, vol normal)"

        # ── GATE 3: THE FLOOR (escape route) ──
        atr = _atr(rows)
        if floors:
            fl = floors[0]
            fl_px = float(fl["price"])
            fl_dist = (entry - fl_px) / entry * 100.0 if entry > 0 else 0.0
            # direction: the last two floor pivots ascending?
            fl_lows = sorted([lv for lv in levels if lv["side"] == "low"],
                             key=lambda lv: float(lv.get("last_ms") or 0))
            direction = "flat"
            if len(fl_lows) >= 2:
                if float(fl_lows[-1]["price"]) > float(fl_lows[-2]["price"]) * 1.001:
                    direction = "rising"
                elif float(fl_lows[-1]["price"]) < float(fl_lows[-2]["price"]) * 0.999:
                    direction = "sinking"
            cushion_req = (goal * float(rules["floor_cushion"][direction])
                           * max(0.1, float(strictness or 1.0)))
            adj.append(f"floor {direction} -> cushion x{rules['floor_cushion'][direction]:.1f}")
            # THE DIAL'S THIRD HAND (Oct 4 audit): the min cushion scales
            # with the dial; the trapdoor MAX stays unscaled — a floor
            # too far below is a cliff at every strictness.
            if strictness != 1.0:
                adj.append(f"cushion dial x{strictness:.1f}")
            floor_ok = (fl_dist >= cushion_req
                        and fl_dist <= goal * float(rules["floor_max_goal_x"]))
            stop = fl_px - (atr or 0.0)
            fl_reason = (f"floor ${fl_px:.4f} ({direction}, strength "
                         f"{float(fl.get('strength') or 0):.1f}) {fl_dist:.1f}% below — "
                         f"cushion needs {cushion_req:.1f}%")
        else:
            floor_ok = False
            direction, fl_dist, stop = "none", None, None
            fl_reason = (f"no major floor within view — standing in mid-air "
                         f"(needs one within {rules['floor_max_goal_x']:.0f}x the goal)")

        # ── THE EXIT DOOR (bid side) ──
        door = None
        door_ok = True
        try:
            from execution_tiers import measure_book
            door = float((measure_book(exchange, pair) or {}).get("bid_tob") or 0.0)
            if spend and door > 0:
                door_ok = door >= float(rules["door_frac"]) * float(spend)
        except Exception:
            door = None

        # ── THE VERDICT ──
        gates = {"clearance": (clearance_ok,
                               f"room {room:.1f}% vs required {required:.1f}% "
                               f"(goal {goal:.1f}% x {mult:.2f})"),
                 "er": (er_ok, er_class),
                 "floor": (floor_ok, fl_reason),
                 "door": (door_ok, (f"BID ${door:,.0f} vs exit ${spend or 0:,.0f}"
                                    if door is not None else "book unreadable"))}
        for gname, (gok, greason) in gates.items():
            if not gok:
                out["ok"] = False
                out["fail_gate"] = gname
                out["fail_reason"] = greason
                break

        ceil_txt = f"${ceil_px:.4f}" if ceil_px else "clear skies"
        fl_txt = f"${fl_px:.4f}" if floors else "none"
        out["escape_line"] = (f"ESCAPE: floor {fl_txt} ({direction}) · "
                              f"room +{room:.1f}% to {ceil_txt} · "
                              f"ER {(er_pct * 100 if er_pct is not None else 0):.0f}th pct · "
                              f"door BID " + (f"${door:,.0f}" if door else "n/a"))
        verdict = "PASS" if out["ok"] else f"FAIL ({out['fail_gate']})"
        out["output_block"] = (
            f"{'─' * 30}\n"
            f"CLIMB-OUT — {verdict}\n"
            f"  Levels: {len(ceilings)} major ceiling(s) above · "
            f"{len(floors)} major floor(s) below "
            f"(of {len(levels)} mapped)\n"
            f"  Clearance: room {room:.1f}% to {ceil_txt} vs required "
            f"{required:.1f}% — {'PASS' if clearance_ok else 'FAIL'}\n"
            f"  ER: {er_class} — {'PASS' if er_ok else 'FAIL'}\n"
            f"  Floor: {fl_reason}\n"
            f"  Structural stop: " + (f"${stop:.4f} (floor − "
                                      f"{atr:.4f} ATR buffer)" if stop else "n/a") + "\n"
            f"  Exit door: {'PASS' if door_ok else 'FAIL'} — "
            + (f"BID ${door:,.0f} carries ≥ "
               f"{rules['door_frac']:.0%} of ${spend or 0:,.0f}" if door is not None
               else "book unreadable") + "\n"
            f"  Adjustments: " + ("; ".join(adj) if adj else "none") + "\n"
            f"  {out['escape_line']}\n")
        out["features"] = {"climb_room_pct": round(room, 2),
                           "climb_required_pct": round(required, 2),
                           "climb_er_pctile": er_pct,
                           "climb_vol_pctile": vol_pct,
                           "climb_floor_dist_pct": round(fl_dist, 2) if fl_dist else None,
                           "climb_floor_dir": direction,
                           "climb_bid_door": door}
        out["adjustments"] = adj
        return out
    except Exception as exc:
        out["degraded"] = True
        out["output_block"] = f"CLIMB-OUT: unavailable ({exc}) — fail-open\n"
        return out
