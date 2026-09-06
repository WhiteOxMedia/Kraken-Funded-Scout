# sure_thing.py -- THE SURE THING (user rule, Sep 6: "I'm losing this
# contest, and that cannot happen. I need the Sure Thing running
# immediately.") -- the breakout-pattern detector.
#
# WHAT IT DETECTS (the user's spec, verbatim intent):
#   1. CONSOLIDATION ZONES -- price coils into a narrow range (stored
#      momentum): the last N candles' total range under a tight threshold.
#   2. REPEATED RESISTANCE TESTS -- the coin pushes against the same
#      ceiling multiple times, weakening it: >=3 touches of the zone high
#      with the floor holding flat-or-rising.
#   3. VOLUME PRESSURE SHIFTS -- declining volume during the coil, then a
#      sudden spike: mean volume fading vs the earlier baseline, and the
#      latest candle(s) bursting above the coil's mean.
#   4. SQUEEZE CONDITIONS -- realized volatility at unusually low levels
#      vs the coin's own recent history (the bottom quintile of the
#      trailing window) -- the spring-loaded setup.
#   5. HISTORICAL BREAKOUT SIGNATURES -- per-coin recurring behaviors:
#      every fire + every armed coil logs to the scorer's dataset
#      (route "sure-thing"); the signatures LEARN from outcomes. The
#      per-coin profile (the engine's expected move) sets the goal.
#
# THE TRIGGER: price crossing the ceiling on the volume burst -- the
# popup fires AT THE CRACK (entering before the breakout runs, not after
# a candle-close confirmation), with the countdown clock bounding the
# trade window. This is the ONE path where the popup precedes the deep
# scan (the user's explicit call, Sep 6 -- "The Deep Scan is too slow
# for this situation... I'll enter that manually afterward"): the deep
# scan runs AFTER entry, for tracking. CONTEST-ONLY -- no bridge write,
# ever: the bot's real money never sees an unverified signal.
#
# The structural score (0-100) sizes the entry through the SAME bucket
# table as the brain (hindsight_scorer.brain_size_mult) -- one sizing
# philosophy. The 3% death-line guard caps it. There is no sure thing --
# this is a probability detector; the scorer grades the class.

# ── THE TUNING (conservative defaults; every constant probe-tested) ──
COIL_LOOKBACK = 24          # 5m candles in the zone (2 hours of coiling)
COIL_MAX_RANGE_PCT = 2.0    # the zone's total range must be under this (%)
CEILING_TOUCH_PCT = 0.15    # a "test" = high within this % of the ceiling
MIN_CEILING_TESTS = 3       # the weakening-the-ceiling count
VOL_SPIKE_X = 2.0           # the trigger candle's volume vs the coil mean
SQUEEZE_LOOKBACK = 288      # 24h of 5m candles for the vol baseline
SQUEELE_PCT = 0.20          # current vol must sit in the bottom fifth
TRIGGER_EDGE_PCT = 0.05     # price at/above (ceiling - this) counts as the crack


def analyze(rows):
    """One structural read of 5m candles -> dict. rows: [ts,o,h,l,c,v]
    (ccxt order; ~150+ candles = 12.5h). Pure math, never raises."""
    out = {"armed": False, "fire": False, "score": 0.0, "detail": "",
           "zone_high": None, "zone_low": None, "tests": 0,
           "coil_range_pct": None, "vol_spike": None, "squeeze_pct": None,
           "trigger_px": None}
    try:
        if not rows or len(rows) < COIL_LOOKBACK + 3:
            out["detail"] = "not enough candles"
            return out
        h = [float(r[2]) for r in rows]
        l = [float(r[3]) for r in rows]
        c = [float(r[4]) for r in rows]
        v = [float(r[5]) for r in rows if len(r) > 5]
        if len(v) != len(rows):
            v = [1.0] * len(rows)
        n = len(rows)
        px = c[-1]

        # 1. THE CONSOLIDATION ZONE (the coil): the last COIL_LOOKBACK
        #    candles minus the trigger candle(s)
        zone = slice(n - COIL_LOOKBACK - 1, n - 1)
        zh = max(h[zone])
        zl = min(l[zone])
        mid = (zh + zl) / 2.0
        if mid <= 0:
            out["detail"] = "degenerate zone"
            return out
        rng_pct = (zh - zl) / mid * 100.0
        out["zone_high"], out["zone_low"] = zh, zl
        out["coil_range_pct"] = round(rng_pct, 3)
        if rng_pct > COIL_MAX_RANGE_PCT:
            out["detail"] = f"no coil: range {rng_pct:.2f}% > {COIL_MAX_RANGE_PCT}%"
            return out

        # 2. REPEATED RESISTANCE TESTS: count candles whose high is
        #    within CEILING_TOUCH_PCT of the ceiling, with the floor
        #    holding (no candle closing under the zone low)
        thr = zh * (1.0 - CEILING_TOUCH_PCT / 100.0)
        tests = sum(1 for i in range(zone.start, zone.stop) if h[i] >= thr)
        out["tests"] = tests
        floor_holds = all(c[i] >= zl for i in range(zone.start, zone.stop))
        if tests < MIN_CEILING_TESTS:
            out["detail"] = f"only {tests} ceiling tests (< {MIN_CEILING_TESTS})"
            return out
        if not floor_holds:
            out["detail"] = "the floor broke inside the coil"
            return out

        # 3. THE SQUEEZE: the realized vol of the coil vs the trailing
        #    day. rank = the coil's vol PERCENTILE among the baseline
        #    windows (0 = the most volatile, 1 = the calmest). A squeeze
        #    = the coil sits in the CALMEST fifth: rank >= 1 - SQUEELE_PCT.
        if n < SQUEEZE_LOOKBACK + COIL_LOOKBACK:
            _sq_look = max(COIL_LOOKBACK * 2, n - COIL_LOOKBACK - 1)
        else:
            _sq_look = SQUEEZE_LOOKBACK
        import statistics as _st
        def _rv(vals):
            rets = [(vals[i] / vals[i - 1] - 1.0) for i in range(1, len(vals))
                    if vals[i - 1] > 0]
            return _st.pstdev(rets) if len(rets) >= 2 else None
        coil_rv = _rv(c[zone.start:zone.stop])
        base_rvs = [_rv(c[i:i + COIL_LOOKBACK])
                    for i in range(max(0, n - 1 - _sq_look - COIL_LOOKBACK),
                                   n - 1 - COIL_LOOKBACK, COIL_LOOKBACK // 4)
                    if i >= 0]
        base_rvs = [x for x in base_rvs if x is not None]
        if coil_rv is None or not base_rvs:
            out["detail"] = "vol baseline unavailable"
            return out
        rank = sum(1 for x in base_rvs if coil_rv <= x) / len(base_rvs)
        out["squeeze_pct"] = round(rank, 3)
        if rank < (1.0 - SQUEELE_PCT):
            out["detail"] = (f"no squeeze: vol percentile {rank:.2f} "
                             f"(needs ≥ {1.0 - SQUEELE_PCT:.2f} — the "
                             f"calmest fifth)")
            return out

        # 4. VOLUME PRESSURE: the coil's mean volume vs the earlier
        #    baseline (fading), and the trigger candle's burst
        coil_v = v[zone.start:zone.stop]
        coil_mean = sum(coil_v) / len(coil_v)
        pre_v = v[max(0, zone.start - COIL_LOOKBACK):zone.start]
        pre_mean = (sum(pre_v) / len(pre_v)) if pre_v else coil_mean
        vol_fading = coil_mean <= pre_mean * 1.1
        burst = v[-1] / coil_mean if coil_mean > 0 else 0.0
        out["vol_spike"] = round(burst, 2)

        # ── ARMED: the coil + tests + squeeze exist (the spring is
        #    loaded); the trigger needs the volume crack
        out["armed"] = True
        out["score"] = _score(rng_pct, tests, rank, burst, vol_fading)
        out["detail"] = (f"coil {rng_pct:.2f}% · {tests} ceiling tests · "
                         f"vol rank {rank:.2f} · burst {burst:.1f}x"
                         + ("" if vol_fading else " · volume not fading"))

        # 5. THE TRIGGER: price at/above the ceiling's edge on a volume
        #    burst -- the crack. Enter BEFORE the breakout runs.
        edge = zh * (1.0 - TRIGGER_EDGE_PCT / 100.0)
        if px >= edge and burst >= VOL_SPIKE_X:
            out["fire"] = True
            out["trigger_px"] = px
        return out
    except Exception as exc:
        out["detail"] = f"analyze error: {exc}"
        return out


def _score(rng_pct, tests, vol_rank, burst, vol_fading):
    """The structural score (0-100): tighter coil, more tests, deeper
    squeeze, bigger burst = higher. Sized through the brain's buckets."""
    s = 0.0
    s += max(0.0, 40.0 * (1.0 - rng_pct / COIL_MAX_RANGE_PCT))     # tightness
    s += min(25.0, (tests - MIN_CEILING_TESTS) * 8.0 + 10.0)       # tests
    # the squeeze term: only the calmest fifth scores, deeper = more
    s += max(0.0, min(25.0, 25.0 * (vol_rank - (1.0 - SQUEELE_PCT)) / SQUEELE_PCT))
    s += min(15.0, 5.0 * (burst - 1.0)) if burst > 1.0 else 0.0    # burst
    if vol_fading:
        s += 5.0
    return round(min(100.0, s), 1)


# the countdown window: breakouts resolve fast -- one hour, stated in
# the popup as the out-by clock
SURE_THING_WINDOW_MIN = 60.0
