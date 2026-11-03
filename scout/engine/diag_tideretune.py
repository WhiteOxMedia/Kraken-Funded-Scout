"""THE TIDE RETUNE (Sep 11, on the recalibrated spot-native store) —
sweeps the tide class thresholds (TIDE_RISING_MIN, TIDE_OUT_MAX) and
prices every candidate split by what the gates could have monetized:
the POPUP FLEET's forward payer rates (cfg.trading_pairs, the 54 the
gates trade) over the popup horizon (4h, >= +1.5%).

Method — mirrors the live gate exactly:
  breadth = % of ALL deep pairs above their own EMA21, per hour
  (the all-pairs cut — _fleet_tide reads the whole cached universe),
  then each candidate (rising_min, out_max) classifies the SAME
  breadth series into RISING/FLAT/OUT.

A good split makes the payer-rate ordering RISING > FLAT > OUT with
the widest RISING-OUT spread, subject to state-hour floors (a state
priced on < 150h is a coin flip, not a price).

The learned OUT bar (tide_learn.py) re-prices itself against whatever
states the shipped thresholds produce — no separate retune needed.

Run: .venv\\Scripts\\python.exe -X utf8 diag_tideretune.py -> _tideretune.txt
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "surething_data" / "kraken"
GOAL_PCT = 1.5
HORIZON_H = 4
FLEET_MIN = 40          # deep ships needed to call an hour's tide
STATE_MIN_H = 150       # a state needs this many hours to be priceable

lines = []


def out(s=""):
    print(s)
    lines.append(s)


# ── 1. the deep store, hourly closes (1h store wins per pair) ─────────
closes = {}
for f in sorted(DATA.glob("*.feather")):
    name = f.name
    if name.endswith("-1h.feather"):
        pair = name[: -len("-1h.feather")].replace("_", "/")
    elif name.endswith("-5m.coinbase.feather"):
        pair = name[: -len("-5m.coinbase.feather")].replace("_", "/")
        if pair in closes:
            continue
    elif name.endswith("-5m.feather"):
        pair = name[: -len("-5m.feather")].replace("_", "/")
        if pair in closes:
            continue
    else:
        continue
    try:
        df = pd.read_feather(f)
    except Exception:
        continue
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("date") or cols.get("ts")
    if tcol is None or "close" not in cols:
        continue
    s = df[[tcol, cols["close"]]].copy()
    s.columns = ["ts", "close"]
    s["ts"] = pd.to_datetime(s["ts"], utc=True)
    s = s.set_index("ts")["close"].resample("1h").last().dropna()
    if len(s) >= 200:
        closes[pair] = s

out("=== THE TIDE RETUNE — spot-native store ===")
out(f"deep pairs loaded: {len(closes)}")

px = pd.DataFrame(closes).sort_index()
px = px.loc[px.index >= "2020-01-01"]

import config as _cfg
popup = [p for p in (_cfg.DEFAULT_CONFIG.trading_pairs
                     if hasattr(_cfg, "DEFAULT_CONFIG") else [])
         if p in px.columns]
out(f"popup fleet priced: {len(popup)} pairs")
out("")

fwd_all = px.shift(-HORIZON_H) / px - 1.0
fwd_popup = px[popup].shift(-HORIZON_H) / px[popup] - 1.0

# ── 2. the live gate's mirror: ALL-pairs breadth, hour by hour ────────
ema21 = px.ewm(span=21, adjust=False).mean()
above = px > ema21
n_ships = above.notna().sum(axis=1)
breadth = above.mean(axis=1) * 100.0
breadth[n_ships < FLEET_MIN] = float("nan")
breadth = breadth.dropna()
out(f"breadth hours callable: {len(breadth)} "
    f"({breadth.index[0]} .. {breadth.index[-1]})")
out("")


def classify(br: pd.Series, rmin: float, omax: float) -> pd.Series:
    def cls(p):
        if p >= rmin:
            return "RISING"
        if p <= omax:
            return "OUT"
        return "FLAT"
    return br.apply(cls)


def price(st: pd.Series) -> dict:
    """Per-state popup-fleet payer rate / hours on this classification."""
    rows = {}
    for stt in ("RISING", "FLAT", "OUT"):
        hrs = st.index[st == stt]
        hrs = fwd_popup.loc[fwd_popup.index.isin(hrs)].dropna(how="all").index
        if not len(hrs):
            rows[stt] = (0.0, 0, 0.0)
            continue
        f = fwd_popup.loc[hrs]
        pay = (f >= GOAL_PCT / 100.0).mean().mean() * 100.0
        med = f.median().median() * 100.0
        rows[stt] = (pay, len(hrs), med)
    return rows


# ── 3. the sweep ──────────────────────────────────────────────────────
RISING_GRID = [60.0, 65.0, 70.0, 75.0, 80.0]
OUT_GRID = [20.0, 25.0, 30.0, 35.0, 40.0]

out(f"{'RIS>=':>6} {'OUT<=':>6} | {'RISING pay':>10} {'(hrs)':>6} | "
    f"{'FLAT pay':>8} {'(hrs)':>6} | {'OUT pay':>8} {'(hrs)':>6} | "
    f"{'R-O spread':>10} {'order':>16}")
out("-" * 105)

results = []
for rmin in RISING_GRID:
    for omax in OUT_GRID:
        if omax >= rmin:
            continue
        st = classify(breadth, rmin, omax)
        r = price(st)
        (rp, rh, rm) = r["RISING"]
        (fp, fh, fm) = r["FLAT"]
        (op, oh, om) = r["OUT"]
        spread = rp - op
        order_ok = (rp > fp > op)
        order_s = "RISING>FLAT>OUT" if order_ok else "not monotonic"
        results.append((rmin, omax, r))
        out(f"{rmin:>6.0f} {omax:>6.0f} | {rp:>9.1f}% {rh:>6d} | "
            f"{fp:>7.1f}% {fh:>6d} | {op:>7.1f}% {oh:>6d} | "
            f"{spread:>+9.1f}pp {order_s:>16}")

out("")
out("── candidates failing the hour floors "
    f"(every state needs >= {STATE_MIN_H}h) are shown but not eligible ──")

# ── 4. the eligible winners ───────────────────────────────────────────
elig = [(rmin, omax, r) for (rmin, omax, r) in results
        if all(r[s][1] >= STATE_MIN_H for s in ("RISING", "FLAT", "OUT"))]
out(f"eligible candidates: {len(elig)} of {len(results)}")
out("")

if elig:
    best = max(elig, key=lambda t: t[2]["RISING"][0] - t[2]["OUT"][0])
    (br, bo, r) = best
    out("── THE PICK — widest RISING−OUT payer spread among eligible ──")
    out(f"  TIDE_RISING_MIN = {br:.0f}   TIDE_OUT_MAX = {bo:.0f}")
    for s in ("RISING", "FLAT", "OUT"):
        pay, hrs, med = r[s]
        out(f"  {s:>7}: payers {pay:5.1f}% | median 4h {med:+.2f}% | {hrs}h")
    spread = r["RISING"][0] - r["OUT"][0]
    out(f"  RISING−OUT payer spread: {spread:+.1f}pp")

# current dials for comparison
cur = price(classify(breadth, 70.0, 35.0))
out("")
out("── the dials in force (70 / 35) on the same store ──")
for s in ("RISING", "FLAT", "OUT"):
    pay, hrs, med = cur[s]
    out(f"  {s:>7}: payers {pay:5.1f}% | median 4h {med:+.2f}% | {hrs}h")
out("")
out("The learned OUT bar (tide_learn.py) re-prices itself against")
out("whatever states the shipped thresholds produce on its 30-min TTL.")
Path("_tideretune.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n-> _tideretune.txt")
