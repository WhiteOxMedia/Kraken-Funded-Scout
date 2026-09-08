# sure_thing.py -- THE SURE THING: the breakout-PREDICTION engine
# (user spec, Sep 6: "Build a breakout-prediction engine that detects
# volatility compression, repeated resistance/support tests, volume
# pressure shifts, and historical breakout signatures. Combine these
# into a breakout probability score and trigger a pre-breakout BUY
# signal with a countdown timer when the probability exceeds the
# threshold." -- modifying the fire-at-the-crack detector built earlier
# the same day, per the user's "unless you already built it like this
# before... then modify it").
#
# THE FIVE MODULES (the user's spec, verbatim intent):
#   1. VOLATILITY COMPRESSION ("spring loading"): ATR dropping below a
#      threshold, Bollinger Band width narrowing, price clustering in a
#      tight range -- at least two of the three must read compressed.
#   2. REPEATED RESISTANCE/SUPPORT TESTS (pressure building): touch
#      count on the ceiling, the pullback depth between touches
#      SHRINKING (failed rejections -- price doesn't fall as far each
#      time = support rising). Deepening pullbacks = support failing =
#      breakdown pressure (lowers the probability -- the honest
#      two-sided read; long-only trades the up-resolution).
#   3. VOLUME PRESSURE SHIFT ("engine revving"): declining volume
#      during compression, micro-spikes before the breakout (volume
#      waking without a crack yet), buy-side dominance rising (green
#      candle volume share in the recent coil vs the early coil).
#   4. HISTORICAL BREAKOUT SIGNATURES: mine the coin's own fetched
#      history for past compression setups and what they paid -- the
#      per-coin hit rate feeds the probability. Probability, not
#      guesswork; it grows as the dataset grows.
#   5. THE PROBABILITY SCORE + COUNTDOWN: everything combines into one
#      0-100 probability; a PRE-BREAKOUT BUY fires when it crosses the
#      threshold (still inside the coil -- before the breakout runs),
#      with the estimated breakout window (rate of compression + volume
#      acceleration). The CRACK fire (price crossing the ceiling on the
#      volume burst) remains the second trigger.
#
# The same rules as the first build stand: CONTEST-ONLY (no bridge
# write), the deep scan runs BEFORE the popup (Sep 7: the Sep 6
# post-entry waiver retired at the user's call — the fire-time rule:
# the read must run, fail-closed, and its direction must agree), the 3%
# death-line guard sizes every entry, the popup cap / rotation lock /
# knife / tide gates all apply. The structural probability sizes through
# the SAME bucket table as the brain. There is no sure thing -- this is
# a probability detector, and the scorer grades the class.

# ── THE TUNING (every constant probe-tested) ──────────────────────────
COIL_LOOKBACK = 24          # 5m candles in the zone (2 hours of coiling)
COIL_MAX_RANGE_PCT = 2.0    # the zone's total range must be under this (%)
CEILING_TOUCH_PCT = 0.15    # a "test" = high within this % of the ceiling
MIN_CEILING_TESTS = 3       # the weakening-the-ceiling count
VOL_SPIKE_X = 2.0           # the crack candle's volume vs the coil mean
MICRO_SPIKE_X = 1.25        # a revving candle: volume without a crack
                            #   (Sep 7 recalibration: at 1.5x a FADING coil
                            #   mean a rev almost never prints -- two days of
                            #   fleet scans read 0-4 revs per armed coil. A
                            #   rev is volume WAKING, not the burst; the
                            #   crack still demands the full 2.0x.)
SQUEEZE_LOOKBACK = 288      # 24h of 5m candles for the vol baseline
SQUEELE_PCT = 0.20          # the coil's vol must sit in the calmest fifth
TRIGGER_EDGE_PCT = 0.05     # price at/above (ceiling - this) = the crack
ATR_PERIOD = 14
ATR_COMPRESS_RATIO = 0.75   # ATR now vs the window mean = compression
BB_PERIOD = 20
BB_STD = 2.0
BB_NARROW_PCT = 0.30        # BB width in the narrowest 30% of the window
HIST_PAY_MOVE_PCT = 1.5     # a past setup "paid" if +this% within 12 candles
HIST_PAY_WINDOW = 12
PRE_BREAKOUT_THRESHOLD = 65.0   # the probability that fires pre-breakout
                            #   (Sep 7 recalibration: two days of live fleet
                            #   scans topped out at 60.3 -- the 75 bar was
                            #   unreachable as calibrated; 65 is the top-of-
                            #   fleet coil AND the 0.50x sizing bucket line.
                            #   The protective gates -- rotation lock, tide,
                            #   knife, popup cap, climb-out -- still stand
                            #   between the fire and the popup.)


def analyze(rows):
    """One structural read of 5m candles -> dict. rows: [ts,o,h,l,c,v]
    (ccxt order; 720 candles = 2.5 days -- enough for the signatures).
    Pure math, never raises. Returns (among others):
      armed / fire / trigger ('pre-breakout' | 'crack' | None)
      score (the 0-100 breakout probability), est_min (the window guess)
    """
    out = {"armed": False, "fire": False, "trigger": None, "score": 0.0,
           "detail": "", "zone_high": None, "zone_low": None, "tests": 0,
           "coil_range_pct": None, "vol_spike": None, "squeeze_pct": None,
           "trigger_px": None, "atr_ratio": None, "bb_width_pctile": None,
           "pullback_trend": None, "micro_spikes": 0,
           "buy_dominance": None, "hist_hit_rate": None, "hist_n": 0,
           "est_min": None}
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
        o = [float(r[1]) for r in rows]
        n = len(rows)
        px = c[-1]

        # 1. THE CONSOLIDATION ZONE (the coil)
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

        # 2. REPEATED RESISTANCE TESTS + the pullback trend (pressure)
        thr = zh * (1.0 - CEILING_TOUCH_PCT / 100.0)
        test_idx = [i for i in range(zone.start, zone.stop) if h[i] >= thr]
        tests = len(test_idx)
        out["tests"] = tests
        if tests < MIN_CEILING_TESTS:
            out["detail"] = f"only {tests} ceiling tests (< {MIN_CEILING_TESTS})"
            return out
        if not all(c[i] >= zl for i in range(zone.start, zone.stop)):
            out["detail"] = "the floor broke inside the coil"
            return out
        # the pullbacks after each test: the low in the window following
        # each test candle until the next test. Shrinking pullbacks =
        # failed rejections (support rising); deepening = support failing
        pullbacks = []
        for k, ti in enumerate(test_idx):
            nxt = test_idx[k + 1] if k + 1 < len(test_idx) else zone.stop
            seg_low = min(l[i] for i in range(ti, nxt)) if nxt > ti else l[ti]
            pullbacks.append((zh - seg_low) / zh * 100.0)
        if len(pullbacks) >= 2:
            _shrink = sum(1 for a, b in zip(pullbacks, pullbacks[1:]) if b < a)
            out["pullback_trend"] = round(
                _shrink / (len(pullbacks) - 1), 3)
        else:
            out["pullback_trend"] = 0.5

        # 3. THE SQUEEZE (realized-vol percentile vs the trailing day)
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
                             f"(needs ≥ {1.0 - SQUEELE_PCT:.2f})")
            return out

        # 4. VOLATILITY COMPRESSION, the other two gauges: ATR ratio +
        #    Bollinger Band width percentile
        trs = []
        for i in range(1, n):
            trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]),
                           abs(l[i] - c[i - 1])))
        atr_now = (sum(trs[-ATR_PERIOD:]) / ATR_PERIOD) if len(trs) >= ATR_PERIOD else None
        atr_hist = (sum(trs[:-COIL_LOOKBACK]) / max(1, len(trs) - COIL_LOOKBACK)) \
            if len(trs) > COIL_LOOKBACK else None
        atr_ratio = (atr_now / atr_hist) if (atr_now and atr_hist and atr_hist > 0) else None
        out["atr_ratio"] = round(atr_ratio, 3) if atr_ratio else None
        bb_w = []
        for i in range(max(BB_PERIOD, zone.start), n):
            seg = c[i - BB_PERIOD:i]
            m = sum(seg) / len(seg)
            sd = _st.pstdev(seg)
            bb_w.append((4.0 * sd / m * 100.0) if m > 0 else 0.0)
        bb_now = bb_w[-1] if bb_w else None
        # pctile: the share of window widths WIDER than now (1.0 = the
        # narrowest). "Narrowing" = the narrowest BB_NARROW_PCT band:
        # pctile >= 1 - BB_NARROW_PCT
        bb_pctile = (sum(1 for x in bb_w if bb_now <= x) / len(bb_w)) \
            if (bb_w and bb_now is not None) else None
        out["bb_width_pctile"] = round(bb_pctile, 3) if bb_pctile is not None else None
        _compressed = sum([
            rng_pct <= COIL_MAX_RANGE_PCT,
            atr_ratio is not None and atr_ratio <= ATR_COMPRESS_RATIO,
            bb_pctile is not None and bb_pctile >= (1.0 - BB_NARROW_PCT)])
        if _compressed < 2:
            out["detail"] = (f"compression incomplete: coil ✓ but "
                             f"ATR {atr_ratio} / BB {bb_pctile} not both tight")
            return out

        # 5. VOLUME PRESSURE: fading + micro-spikes + buy-side dominance
        coil_v = v[zone.start:zone.stop]
        coil_mean = sum(coil_v) / len(coil_v)
        pre_v = v[max(0, zone.start - COIL_LOOKBACK):zone.start]
        pre_mean = (sum(pre_v) / len(pre_v)) if pre_v else coil_mean
        vol_fading = coil_mean <= pre_mean * 1.1
        burst = v[-1] / coil_mean if coil_mean > 0 else 0.0
        out["vol_spike"] = round(burst, 2)
        # micro-spikes: revving candles (volume ≥ MICRO_SPIKE_X, price
        # still under the ceiling) in the last half of the coil
        micro = sum(1 for i in range(zone.start + COIL_LOOKBACK // 2, zone.stop)
                    if v[i] >= coil_mean * MICRO_SPIKE_X and h[i] < thr)
        out["micro_spikes"] = micro
        # buy-side dominance: green-candle volume share, late vs early coil
        def _green_share(seg):
            gv = sum(v[i] for i in seg if c[i] >= o[i])
            tv = sum(v[i] for i in seg)
            return gv / tv if tv > 0 else 0.5
        early = range(zone.start, zone.start + COIL_LOOKBACK // 2)
        late = range(zone.start + COIL_LOOKBACK // 2, zone.stop)
        gs_early, gs_late = _green_share(early), _green_share(late)
        out["buy_dominance"] = round(gs_late - gs_early, 3)

        # 6. HISTORICAL BREAKOUT SIGNATURES: this coin's own past
        #    compression setups and what they paid (the fetched window)
        hist_n = hist_paid = 0
        for i in range(0, n - COIL_LOOKBACK - HIST_PAY_WINDOW, COIL_LOOKBACK):
            seg = slice(i, i + COIL_LOOKBACK)
            if seg.stop >= zone.start:
                break  # don't count the live coil or overlap it
            sh, sl = max(h[seg]), min(l[seg])
            sm = (sh + sl) / 2.0
            if sm <= 0 or (sh - sl) / sm * 100.0 > COIL_MAX_RANGE_PCT:
                continue
            fwd = slice(i + COIL_LOOKBACK, i + COIL_LOOKBACK + HIST_PAY_WINDOW)
            if fwd.stop > n:
                continue
            hist_n += 1
            if max(h[fwd]) >= sh * (1.0 + HIST_PAY_MOVE_PCT / 100.0):
                hist_paid += 1
        out["hist_n"] = hist_n
        out["hist_hit_rate"] = round(hist_paid / hist_n, 3) if hist_n else None

        # ── ARMED: the spring is loaded
        out["armed"] = True
        out["score"] = _score(rng_pct, tests, rank, burst, vol_fading,
                              atr_ratio, bb_pctile, out["pullback_trend"],
                              micro, out["buy_dominance"], out["hist_hit_rate"])
        out["detail"] = (f"coil {rng_pct:.2f}% · {tests} tests · squeeze "
                         f"{rank:.2f} · ATR {atr_ratio or '—'} · BB "
                         f"{bb_pctile if bb_pctile is not None else '—'} · "
                         f"revs {micro} · hist "
                         f"{(out['hist_hit_rate'] or 0) * 100:.0f}%/{hist_n}")

        # the estimated breakout window: compression speed + revving
        # hot = the window tightens: 3+ revs (the 1.25x bar counts more
        # candles than the old 1.5x did) or a rising floor
        _hot = micro >= 3 or (out["pullback_trend"] or 0) >= 0.75
        out["est_min"] = 30 if _hot else 60

        # 7. THE TRIGGERS
        # (a) PRE-BREAKOUT: the probability crosses the threshold while
        #     still inside the coil -- fire BEFORE the breakout runs
        if out["score"] >= PRE_BREAKOUT_THRESHOLD:
            out["fire"] = True
            out["trigger"] = "pre-breakout"
            out["trigger_px"] = px
        # (b) THE CRACK: price crossing the ceiling on the volume burst.
        #     Two paths (Sep 7): the original -- px at the edge, burst >=
        #     2.0x -- and the closed-through path, because the scanner
        #     polls LIVE 5m candles: mid-candle the crack candle's volume
        #     is partial, so the 2.0x bar kept reading a half-full candle
        #     and the crack died in a polling lottery (172 armed zones in
        #     two days, zero cracks). If price stands strictly THROUGH the
        #     ceiling (px > zh), 1.5x of the faded mean is a burst. The
        #     next poll the crack candle enters the zone, its range blows
        #     the coil, and armed drops -- one-shot by construction.
        edge = zh * (1.0 - TRIGGER_EDGE_PCT / 100.0)
        if px >= edge and burst >= VOL_SPIKE_X:
            out["fire"] = True
            out["trigger"] = "crack"
            out["trigger_px"] = px
        elif px > zh and burst >= VOL_SPIKE_X * 0.75:
            out["fire"] = True
            out["trigger"] = "crack"
            out["trigger_px"] = px
        return out
    except Exception as exc:
        out["detail"] = f"analyze error: {exc}"
        return out


def _score(rng_pct, tests, vol_rank, burst, vol_fading, atr_ratio,
           bb_pctile, pullback_trend, micro, buy_dominance, hist_rate):
    """The breakout probability (0-100) -- the weighted blend:
    compression (30) + pressure (25) + volume (20) + squeeze (15) +
    history (10). Monotonic in every pressure input."""
    s = 0.0
    # compression: tightness + ATR + BB
    s += max(0.0, 15.0 * (1.0 - rng_pct / COIL_MAX_RANGE_PCT))
    if atr_ratio is not None:
        s += max(0.0, min(7.5, 7.5 * (1.0 - atr_ratio / ATR_COMPRESS_RATIO)))
    if bb_pctile is not None:
        s += max(0.0, min(7.5, 7.5 * (bb_pctile - (1.0 - BB_NARROW_PCT)) / BB_NARROW_PCT))
    # pressure: tests + shrinking pullbacks
    s += min(10.0, (tests - MIN_CEILING_TESTS) * 3.0 + 5.0)
    s += max(0.0, min(7.5, 7.5 * (2.0 * (pullback_trend or 0.0))))
    s += max(0.0, min(7.5, 7.5 * (2.0 * (buy_dominance or 0.0))))
    # volume: fading + revving + the crack burst
    if vol_fading:
        s += 5.0
    s += min(10.0, 3.5 * micro)
    s += min(5.0, 5.0 * (burst - 1.0)) if burst > 1.0 else 0.0
    # the squeeze
    s += max(0.0, min(15.0, 15.0 * (vol_rank - (1.0 - SQUEELE_PCT)) / SQUEELE_PCT))
    # history: the coin's own hit rate. UNKNOWN history sits at neutral
    # (5.0), not zero -- most majors have no minable +1.5%-in-an-hour coil
    # in their fetched window, and zeroing them turned the bonus into a
    # tax the bar could never clear (Sep 7: every armed coin in the fleet
    # read hist 0.0-0.11). The score grows as the dataset grows -- from
    # the middle, not the floor.
    if hist_rate is None:
        s += 5.0
    else:
        s += 10.0 * hist_rate
    return round(min(100.0, s), 1)


# the countdown window: the trade bounds (out-by clock); breakouts
# resolve fast -- one hour max, the est_min field estimates the crack
SURE_THING_WINDOW_MIN = 60.0
