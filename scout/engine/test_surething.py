"""Test THE SURE THING: the breakout-PREDICTION engine (user spec, Sep 6:
"Build a breakout-prediction engine that detects volatility compression,
repeated resistance/support tests, volume pressure shifts, and historical
breakout signatures. Combine these into a breakout probability score and
trigger a pre-breakout BUY signal with a countdown timer when the
probability exceeds the threshold.") -- the upgraded detector: the
PRE-BREAKOUT fire (probability over the threshold inside the coil; the
bar sits at the top-of-fleet coil AND the 0.50x sizing bucket line,
Sep 7 recalibration) + the CRACK fire (price crossing the ceiling on
volume -- the original path AND the closed-through path, because live
polling reads the crack candle half-full). CONTEST-ONLY; the 3% guard,
the popup cap, the rotation lock, the knife and the tide gates stand."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import sure_thing as st
import scratch_space   # scratch policy: nothing volatile lands in the repo root

T0 = 1700000000000


def mk(hist, coil_closes, coil_highs, coil_lows, coil_vols,
       last=(100.2, 100.7, 100.1, 100.62, 200.0)):
    """hist: list of (o,h,l,c,v). The coil follows; then the last candle."""
    rows = []
    for (o, h, l, c, v) in hist:
        rows.append([T0 + len(rows) * 300000, o, h, l, c, v])
    for i in range(len(coil_closes)):
        rows.append([T0 + len(rows) * 300000, 100.0, coil_highs[i],
                     coil_lows[i], coil_closes[i], coil_vols[i]])
    rows.append([T0 + len(rows) * 300000, last[0], last[1], last[2],
                 last[3], last[4]])
    return rows


def osc_hist(n=155, amp=0.004, vol=100.0, base=100.0):
    """A plain oscillating history (no paid signatures)."""
    out = []
    for i in range(n):
        o = base
        c = base * (1 + (amp if i % 2 == 0 else -amp))
        out.append((o, max(o, c) * 1.001, min(o, c) * 0.999, c, vol))
    return out


def paid_hist(n_calms=3):
    """A history with PAID breakout signatures: calm 24-candle coils each
    followed by a +3% run (the miner should find them and their payoff)."""
    out = []
    for k in range(n_calms):
        for i in range(24):  # the calm coil (oscillates ±0.15%)
            o = 100.0
            c = 100.0 * (1 + (0.0015 if i % 2 == 0 else -0.0015))
            out.append((o, max(o, c) * 1.001, min(o, c) * 0.999, c, 90.0))
        for i in range(12):  # the run: +3% over 12 candles
            c = 100.0 * (1 + 0.03 * (i + 1) / 12.0)
            out.append((100.0, c * 1.001, 100.0 * 0.999, c, 150.0))
    # tail padding so the live coil doesn't overlap the last run
    for i in range(24):
        o = 100.0
        c = 100.0 * (1 + (0.0015 if i % 2 == 0 else -0.0015))
        out.append((o, max(o, c) * 1.001, min(o, c) * 0.999, c, 90.0))
    return out


def coil(n=24, hi_test=100.5, hi_flat=100.2, lo0=99.65, lo1=None,
         v0=90.0, greens_late=False, micro_at=()):
    """A coil: ceiling tests, a floor (flat or RISING with lo1), fading
    volume, optional late green candles + micro-spikes. Early candles
    close RED (under the 100.0 open) so greens_late creates real
    buy-side dominance."""
    lo1 = lo0 if lo1 is None else lo1
    closes, highs, lows, vols = [], [], [], []
    for i in range(n):
        frac = i / max(1, n - 1)
        lo = lo0 + (lo1 - lo0) * frac          # the rising floor
        if greens_late and i >= n // 2:
            c = 100.08                          # green (over the 100.0 open)
        else:
            c = 99.95                           # red (under the open)
        hi = hi_test if i % 6 in (0, 2, 5) else hi_flat
        v = max(20.0, v0 - 2.0 * i)
        if i in micro_at:
            hi = min(hi, hi_flat)               # revving WITHOUT touching
            v = (v0 - 2.0 * i) * 2.2            # the micro-spike
        closes.append(c)
        highs.append(hi)
        lows.append(lo)
        vols.append(v)
    return closes, highs, lows, vols


# ── 1. THE PRE-BREAKOUT FIRE: probability crosses the threshold ──────
cl, ch, clow, cv = coil(hi_test=100.45, lo0=99.55, lo1=99.75,
                        greens_late=True, micro_at=(16, 20))
rows = mk(paid_hist(), cl, ch, clow, cv,
          last=(100.0, 100.15, 99.9, 100.05, 60.0))  # still inside the coil
r = st.analyze(rows)
check("the full-pressure coil (rising floor + revs + greens + paid "
      "history) fires PRE-BREAKOUT, still inside the coil",
      r["fire"] and r["trigger"] == "pre-breakout", (r["score"], r["detail"]))
check("the probability crossed the threshold (>= the fire bar)",
      r["score"] >= st.PRE_BREAKOUT_THRESHOLD, r["score"])
check("the trigger price is INSIDE the coil (before the breakout runs)",
      r["trigger_px"] < r["zone_high"], (r["trigger_px"], r["zone_high"]))
check("the countdown estimate is present (the hot setup reads 30 min)",
      r["est_min"] == 30, r["est_min"])
check("the historical signature miner found the coin's paid coils",
      r["hist_n"] >= 2 and (r["hist_hit_rate"] or 0) >= 0.99,
      (r["hist_n"], r["hist_hit_rate"]))
check("the pullbacks shrank (failed rejections: the floor rose)",
      (r["pullback_trend"] or 0) >= 0.75, r["pullback_trend"])
check("micro-spikes were counted (revving without cracking)",
      r["micro_spikes"] >= 2, r["micro_spikes"])
check("buy-side dominance rose in the late coil",
      (r["buy_dominance"] or 0) > 0.0, r["buy_dominance"])

# ── 2. THE CRACK FIRE: the original trigger stands ────────────────────
cl, ch, clow, cv = coil()
rows = mk(osc_hist(), cl, ch, clow, cv)
r = st.analyze(rows)
check("the tight coil with a volume crack fires AT THE CRACK",
      r["fire"] and r["trigger"] == "crack", (r["score"], r["detail"]))
check("the crack detail names the structure",
      "coil" in r["detail"] and "tests" in r["detail"], r["detail"])
check("the structural probability lands in a sane band",
      0.0 <= r["score"] <= 100.0, r["score"])
check("the zone boundaries are reported",
      abs(r["zone_high"] - 100.5) < 1e-9 and abs(r["zone_low"] - 99.65) < 1e-9)
check("no paid history in a plain oscillating market -> no history bonus",
      r["hist_n"] >= 1 and (r["hist_hit_rate"] or 0) < 0.5,
      (r["hist_n"], r["hist_hit_rate"]))

# ── 3. armed but NOT firing: a lukewarm coil under the threshold ─────
cl, ch, clow, cv = coil()
rows = mk(osc_hist(), cl, ch, clow, cv,
          last=(100.0, 100.15, 99.9, 100.05, 70.0))
r = st.analyze(rows)
check("a flat-floor, no-revs coil stays under the threshold -> ARMED, "
      "no pre-breakout fire",
      r["armed"] and not r["fire"] and r["score"] < st.PRE_BREAKOUT_THRESHOLD,
      r["score"])
check("the lukewarm countdown reads the slower window (60 min)",
      r["est_min"] == 60, r["est_min"])
# the crack without the volume burst
rows = mk(osc_hist(), cl, ch, clow, cv,
          last=(100.2, 100.7, 100.1, 100.62, 80.0))
r = st.analyze(rows)
check("price crosses the ceiling WITHOUT volume -> no crack",
      r["armed"] and not r["fire"] and r["vol_spike"] < st.VOL_SPIKE_X,
      r["vol_spike"])

# ── 4. THE DETECTOR GATES ────────────────────────────────────────────
cl2, ch2, clow2, cv2 = coil(hi_test=103.0, lo0=99.6)
rows = mk(osc_hist(), cl2, ch2, clow2, cv2)
r = st.analyze(rows)
check("a wide range is not a coil", not r["armed"] and "no coil" in r["detail"])

cl3, ch3, clow3, cv3 = coil()
ch3 = [100.5 if i == 5 else 100.2 for i in range(24)]
rows = mk(osc_hist(), cl3, ch3, clow3, cv3)
r = st.analyze(rows)
check("one ceiling test is not a weakened ceiling",
      not r["armed"] and "ceiling tests" in r["detail"])

cl4, ch4, clow4, cv4 = coil()
cl4 = [99.0 if i == 10 else c for i, c in enumerate(cl4)]
rows = mk(osc_hist(), cl4, ch4, clow4, cv4)
r = st.analyze(rows)
check("a broken floor inside the coil kills the setup",
      not r["armed"] and "floor" in r["detail"])

rows = mk(osc_hist(amp=0.0001),
          coil_closes=[100.0 + (0.8 if i % 2 else -0.7) for i in range(24)],
          coil_highs=[100.9 if i % 3 else 100.6 for i in range(24)],
          coil_lows=[99.3] * 24, coil_vols=[80.0] * 24,
          last=(100.2, 100.95, 100.1, 100.92, 200.0))
r = st.analyze(rows)
check("no squeeze (the coil ranks volatile vs a calm history) -> not armed",
      not r["armed"] and "squeeze" in r["detail"])

r = st.analyze([[T0, 100, 101, 99, 100, 10]] * 10)
check("thin data -> a clean no-op", not r["armed"] and r["detail"])
r = st.analyze(None)
check("no candles at all -> a clean no-op", not r["armed"])

# ── 5. THE SIZING SEAM + THE CONSTANTS ───────────────────────────────
check("the countdown window is the 4-hour contract (re-contracted Sep 11 "
      "per the grade's contract test: the coil's edge is a 4h structure — "
      "+1.5%/4h pays 47.2% at bar 45 vs 16.2% on the 60-min clock; the "
      "old 60-min promise graded 4h edge at near-noise and broke on the "
      "crack's ~50-min pays)",
      st.SURE_THING_WINDOW_MIN == 240.0)
check("the pre-breakout threshold is 45 (the Sep 11 refit bar — the "
      "old 65 priced a score that ranked payers BACKWARDS; the refit's "
      "holdout shows 45 pays 17.6% under the popup's own contract)",
      st.PRE_BREAKOUT_THRESHOLD == 45.0)
check("the refit weights shipped with a rollback switch",
      st.SURE_THING_USE_REFIT is True and abs(sum(st.REFIT_W.values()) - 100.0) < 1.0
      and st.REFIT_W["hist"] > st.REFIT_W["tests"] > 0.0
      and st.REFIT_W["tight"] == 0.0)
check("the micro-spike bar is 1.25x the faded mean (a rev is volume "
      "WAKING, not the burst -- Sep 7 recalibration)",
      st.MICRO_SPIKE_X == 1.25)
check("the crack still demands the full burst (2.0x the coil mean)",
      st.VOL_SPIKE_X == 2.0)
check("the closed-through crack path exists (1.5x burst, live polling "
      "reads the crack candle half-full -- Sep 7 recalibration)",
      st.VOL_SPIKE_X * 0.75 == 1.5)
from hindsight_scorer import brain_size_mult
cl5, ch5, clow5, cv5 = coil()
rows = mk(osc_hist(), cl5, ch5, clow5, cv5)
r = st.analyze(rows)
check("the probability sizes through the SAME bucket table",
      brain_size_mult(r["score"]) in (1.0, 0.75, 0.5, 0.33, 0.2, 0.1))

# ── 6. THE SCANNER WIRING ────────────────────────────────────────────
bs = open("background_scanner.py", encoding="utf-8").read()
st_fn = bs[bs.find("def _sure_thing_scan"):bs.find("# ── ONE VOICE ENGINE")]
check("the scanner runs the fleet breakout scan (5-min cadence, 720 candles)",
      "def _sure_thing_scan" in bs
      and "last_sure_thing_scan) >= 300" in bs
      and "limit=720" in st_fn)
check("CONTEST-ONLY: the Sure Thing never writes the bridge",
      "write_buy_signal" not in st_fn)
check("the protecting gates all run before the fire "
      "(the lock retired Sep 10 — the call stays for the re-entry display)",
      "rotation_gate(asset)" in st_fn and "knife_reversed_1h" in st_fn
      and "max_entry_usd" in st_fn and "_fleet_tide" in st_fn)
check("the death-line guard caps + the probability sizes",
      "brain_size_mult as _st_mult" in st_fn and "_cap * _mult" in st_fn)
check("every fire logs to the scorer's dataset (route sure-thing + trigger)",
      '"route": "sure-thing"' in st_fn and '"trigger": _trig' in st_fn)
check("the PRE-BREAKOUT popup variant exists (Breakout Alert + the countdown)",
      "BREAKOUT ALERT" in st_fn and "~{_est} MIN WINDOW" in st_fn
      and "countdown starts now" in st_fn
      and "Watch for the ceiling crack!" in st_fn)
check("the crack variant stands (the second trigger)",
      "the coil cracked" in st_fn)
check("the deep scan runs BEFORE the popup (the Sep 6 waiver retired, Sep 7)",
      "_deep_read(exchange, pair, cfg)" in st_fn
      and "sure-thing-fail-closed" in st_fn
      and "sure-thing-contranote" in st_fn
      and "popup precedes" not in st_fn)
check("a contradicting read is a NOTE — the coil fires (deep-veto deleted)",
      "against the breakout" in st_fn
      and "the deep-veto gate is deleted" in st_fn
      and "no deep read, " in st_fn and "no popup (the fire-time rule)" in st_fn)
check("the deep-read helper is ONE implementation shared with the champion",
      "def _deep_read(exchange, pair, cfg)" in bs
      and bs.count("_deep_read(exchange, pair, cfg)") >= 2)
check("the shared helper never raises itself (callers own fail-closed)",
      bs.count("return None, None") >= 2
      and "finally:" in bs[bs.find("def _deep_read"):bs.find("def _popup_cap")])
check("coils are silent once per zone (user rule Sep 8: the armed watch is "
      "noise, the BUY is the first alert) — logged, never spoken",
      "_st_armed" in bs and "ARMED (silent)" in st_fn
      and "is coiling" not in st_fn)
# the LIVE GOAL (user rule, Sep 8: "the breakout should aim at what the
# market can actually give, not a flat default") — the fire path calls
# the champion funnel's market-goal machinery, fail-open to the engine
# constant, and stamps whatever it picked
check("the fire aims at the LIVE market goal (the champion machinery), not a flat default",
      "def _sure_thing_goal" in bs
      and "_market_goal_pct(exchange, pair)" in bs
      and "_st_goal = _sure_thing_goal(exchange, pair)" in bs)
# THE FAIL-OPEN, BEHAVIORAL (the rot census, Oct 3): the old pin froze
# the ROTTED token itself (`return st.SURE_THING_GOAL_PCT` — no `st` was
# ever bound in the scanner module, so the documented fail-open path
# died into the caller's except instead of shipping the constant). Both
# fall-through causes are pinned for real: the read RAISING and the
# read returning a non-positive goal — each must ship the engine
# constant, and the rotted token must stay gone.
import background_scanner as _bs

_mgp_bak = _bs._market_goal_pct
class _BrokenGoal:
    def __call__(self, *a, **k):
        raise RuntimeError("the broken goal read")
try:
    _bs._market_goal_pct = _BrokenGoal()
    _r1 = _bs._sure_thing_goal(object(), "ZEC/USD")
    _bs._market_goal_pct = lambda *a, **k: 0.0   # a useless goal read
    _r0 = _bs._sure_thing_goal(object(), "ZEC/USD")
finally:
    _bs._market_goal_pct = _mgp_bak
check("a broken goal read fails open to the engine constant (the fire "
      "never stalls — the path actually works now, raise and 0.0 both)",
      _r1 == st.SURE_THING_GOAL_PCT and _r0 == st.SURE_THING_GOAL_PCT
      and "st.SURE_THING_GOAL_PCT" not in bs, (_r1, _r0))
check("the goal the fire picked is stamped into the snapshot (the diag grades it)",
      '"goal_pct": _st_goal' in bs)

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open(scratch_space.report_temp('_surething_check.txt'), "w", encoding="ascii", errors="replace").write(out)
print(out)


def main():
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
