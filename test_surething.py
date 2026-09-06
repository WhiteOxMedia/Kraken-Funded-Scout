"""Test THE SURE THING (user rule, Sep 6: "I'm losing this contest, and
that cannot happen. I need the Sure Thing running immediately.") -- the
breakout-pattern detector: consolidation zones, repeated resistance
tests, volume pressure shifts, squeeze conditions, and the trigger that
fires AT THE CRACK with the countdown clock. CONTEST-ONLY (no bridge
write); the deep scan runs AFTER entry (the user's explicit waiver);
the 3% guard, popup cap, rotation lock, knife and tide gates stand;
every fire logs to the scorer's dataset (route "sure-thing")."""
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

T0 = 1700000000000


def mk(hist_n=155, coil_closes=None, coil_highs=None, coil_lows=None,
       coil_vols=None, last=(100.2, 100.7, 100.1, 100.62, 200.0),
       hist_amp=0.004, hist_vol=100.0):
    """Synthetic 5m candles: a volatile history, then a coil, then the
    last (trigger) candle. last=(o,h,l,c,v)."""
    rows = []
    for i in range(hist_n):
        o = 100.0
        c = 100.0 * (1 + (hist_amp if i % 2 == 0 else -hist_amp))
        rows.append([T0 + len(rows) * 300000, o, max(o, c) * 1.001,
                     min(o, c) * 0.999, c, hist_vol])
    n_coil = len(coil_closes)
    for i in range(n_coil):
        c = coil_closes[i]
        rows.append([T0 + len(rows) * 300000, 100.0, coil_highs[i],
                     coil_lows[i], c, coil_vols[i]])
    rows.append([T0 + len(rows) * 300000, last[0], last[1], last[2],
                 last[3], last[4]])
    return rows


def coil(n=24, close_hi=100.02, hi_test=100.5, hi_flat=100.2, lo=99.65,
         v0=90.0):
    closes = [100.0 + (close_hi - 100.0) * (i % 3 == 2) for i in range(n)]
    highs = [hi_test if i % 6 in (0, 2, 5) else hi_flat for i in range(n)]
    lows = [lo] * n
    vols = [max(20.0, v0 - 2.0 * i) for i in range(n)]
    return closes, highs, lows, vols


# ── 1. THE FIRE: a coiled spring cracks on volume ────────────────────
cl, ch, clow, cv = coil()
rows = mk(coil_closes=cl, coil_highs=ch, coil_lows=clow, coil_vols=cv)
r = st.analyze(rows)
check("a tight coiled spring with 12 ceiling tests + a volume crack FIRES",
      r["fire"] and r["armed"], r["detail"])
check("the fire carries the trigger price (the crack, not a close-confirm)",
      r["trigger_px"] == 100.62, r["trigger_px"])
check("the detail names the structure (coil · tests · vol rank · burst)",
      "coil" in r["detail"] and "tests" in r["detail"]
      and "burst" in r["detail"], r["detail"])
check("the structural score lands in the strong band (50-100)",
      50.0 <= r["score"] <= 100.0, r["score"])
check("the zone boundaries are reported (the ceiling is the trade line)",
      abs(r["zone_high"] - 100.5) < 1e-9 and abs(r["zone_low"] - 99.65) < 1e-9,
      (r["zone_high"], r["zone_low"]))

# ── 2. armed but NOT firing: no crack ────────────────────────────────
rows = mk(coil_closes=cl, coil_highs=ch, coil_lows=clow, coil_vols=cv,
          last=(100.0, 100.15, 99.9, 100.05, 70.0))
r = st.analyze(rows)
check("the spring loads but no crack -> ARMED, no fire",
      r["armed"] and not r["fire"], r["detail"])
# the crack without the volume burst
rows = mk(coil_closes=cl, coil_highs=ch, coil_lows=clow, coil_vols=cv,
          last=(100.2, 100.7, 100.1, 100.62, 80.0))
r = st.analyze(rows)
check("price crosses the ceiling WITHOUT volume -> armed, no fire "
      "(a crack needs the burst)",
      r["armed"] and not r["fire"] and r["vol_spike"] < st.VOL_SPIKE_X,
      r["vol_spike"])

# ── 3. THE GATES INSIDE THE DETECTOR ────────────────────────────────
cl2, ch2, clow2, cv2 = coil(n=24, hi_test=103.0, hi_flat=100.2, lo=99.6)
rows = mk(coil_closes=cl2, coil_highs=ch2, coil_lows=clow2, coil_vols=cv2)
r = st.analyze(rows)
check("a wide range is not a coil (range > the threshold)",
      not r["armed"] and "no coil" in r["detail"], r["detail"])

cl3, ch3, clow3, cv3 = coil()
ch3 = [100.5 if i == 5 else 100.2 for i in range(24)]  # one ceiling test
rows = mk(coil_closes=cl3, coil_highs=ch3, coil_lows=clow3, coil_vols=cv3)
r = st.analyze(rows)
check("one ceiling test is not a weakened ceiling (< MIN_CEILING_TESTS)",
      not r["armed"] and "ceiling tests" in r["detail"], r["detail"])

cl4, ch4, clow4, cv4 = coil()
cl4 = [99.0 if i == 10 else c for i, c in enumerate(cl4)]  # floor breaks
rows = mk(coil_closes=cl4, coil_highs=ch4, coil_lows=clow4, coil_vols=cv4)
r = st.analyze(rows)
check("a broken floor inside the coil kills the setup",
      not r["armed"] and "floor" in r["detail"], r["detail"])

# no squeeze: a calm history makes the coil's vol look HIGH
rows = mk(hist_amp=0.0001, hist_vol=100.0,
          coil_closes=[100.0 + (0.8 if i % 2 else -0.7) for i in range(24)],
          coil_highs=[100.9 if i % 3 else 100.6 for i in range(24)],
          coil_lows=[99.3] * 24, coil_vols=[80.0] * 24,
          last=(100.2, 100.95, 100.1, 100.92, 200.0))
r = st.analyze(rows)
check("no squeeze (the coil's vol ranks HIGH vs a calm history) -> not armed",
      not r["armed"] and "squeeze" in r["detail"], r["detail"])

r = st.analyze([[T0, 100, 101, 99, 100, 10]] * 10)
check("thin data -> a clean no-op, never raises",
      not r["armed"] and r["detail"], r["detail"])
r = st.analyze(None)
check("no candles at all -> a clean no-op", not r["armed"])

# ── 4. THE COUNTDOWN + THE SIZING SEAM ──────────────────────────────
check("the countdown window is the one-hour breakout clock",
      st.SURE_THING_WINDOW_MIN == 60.0, st.SURE_THING_WINDOW_MIN)
from hindsight_scorer import brain_size_mult
cl5, ch5, clow5, cv5 = coil()
rows = mk(coil_closes=cl5, coil_highs=ch5, coil_lows=clow5, coil_vols=cv5)
r = st.analyze(rows)
check("the structural score sizes through the SAME bucket table "
      "(one sizing philosophy)",
      brain_size_mult(r["score"]) in (1.0, 0.75, 0.5, 0.33, 0.2, 0.1))

# ── 5. THE SCANNER WIRING ────────────────────────────────────────────
bs = open("background_scanner.py", encoding="utf-8").read()
st_fn = bs[bs.find("def _sure_thing_scan"):bs.find("# ── ONE VOICE ENGINE")]
check("the scanner runs the fleet breakout scan (5-min cadence)",
      "def _sure_thing_scan" in bs
      and "last_sure_thing_scan) >= 300" in bs)
check("CONTEST-ONLY: the Sure Thing never writes the bridge",
      "write_buy_signal" not in st_fn)
check("the protecting gates all run before the fire",
      "rotation_gate(asset)" in st_fn and "knife_reversed_1h" in st_fn
      and "_popup_cap()" in st_fn and "max_entry_usd" in st_fn
      and "_fleet_tide" in st_fn)
check("the death-line guard caps + the structural score sizes",
      "brain_size_mult as _st_mult" in st_fn
      and "_cap * _mult" in st_fn)
check("every fire logs to the scorer's dataset (route sure-thing)",
      '"route": "sure-thing"' in st_fn)
check("the deep-scan waiver is the user's documented call (post-entry scan)",
      "popup precedes" in st_fn or "The Deep Scan is too slow" in bs)
check("coils are announced once per zone (the armed-watch dedup)",
      "_st_armed" in bs and "is coiling" in st_fn)

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open("_surething_check.txt", "w", encoding="ascii", errors="replace").write(out)
print(out)
sys.exit(0)
