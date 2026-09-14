"""THE TIDE BAR REFIT (Sep 11) — the tide learner's OUT bar, given the
Sure Thing treatment. The live learner prices OUT with a FIXED QUANTILE:
bar = the (1 - payer_rate) quantile of the funnel's quick-score history.
That assumes score rank == payer rank — the exact assumption the Sure
Thing refit proved can be backwards (legacy AUC 0.410).

THE FIT: join the LIVE FUNNEL OUTCOMES to the deep store — every
suppressed candidate (popup_suppressions.jsonl + signal_snapshots.jsonl,
each stamped with its live tide string) gets its forward 60-min outcome
from the recalibrated 5m store (the popup's own contract: entry at the
candidate candle close, payer = MFE >= +1.5% within 12 bars). Then learn
the EMPIRICAL score->payer mapping for OUT candidates and pick the bar
that maximizes realized EV ($/fire, the refit's convention: 1055 base x
brain_size_mult(score) x 1.5% goal-take on payers), chosen on a
chronological train slice and PROVEN on the held-out tail against the
quantile bar computed the learner's own way.

SHIP RULE: the outcome bar ships into tide_learn.learn() only if it
beats the quantile bar out-of-sample; learn() itself re-checks evidence
volume on every 30-min build and falls back to the quantile when the
outcome sample is thin (fail-closed, quantile fallback).

Run: .venv\\Scripts\\python.exe -X utf8 diag_tidebarrefit.py -> _tidebarrefit.txt
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pandas as pd

import tide_learn as tl
import hindsight_scorer as hs

ROOT = Path(__file__).parent
STORE = ROOT / "surething_data" / "kraken"
SUPP = ROOT / "popup_suppressions.jsonl"
SNAP = ROOT / "signal_snapshots.jsonl"
OUT_FILE = ROOT / "_tidebarrefit.txt"

WINDOW_BARS = 12          # the popup's 60-min contract on 5m candles
GOAL_PCT = 1.5
BASE_ENTRY_USD = 1055.0   # the refit's convention — same dollars, same law
DEDUPE_BUCKET_MIN = 15    # scan-repeats: one candidate per pair per 15 min
TRAIN_FRAC = 0.70         # chronological split by candidate timestamp
MIN_TRAIN_FIRES = 10      # a bar must have this many train fires to be picked

lines = []


def out(s=""):
    print(s)
    lines.append(s)


def _records():
    """Candidates from both ledgers: (ts_utc, pair, quick, tide_state)."""
    rows = []
    for path in (SUPP, SNAP):
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                q = r.get("quick")
                # the live stamp reads like "OUT 30%" / "RISING 55%" —
                # the state is the first token, the breadth rides along
                tide = ((r.get("tide") or "").split() or [""])[0].upper()
                if q is None or tide not in ("OUT", "FLAT", "RISING"):
                    continue
                try:
                    ts = pd.to_datetime(r["ts"], utc=True)
                    pair = str(r["pair"])
                    q = float(q)
                except Exception:
                    continue
                if not (0 < q <= 100):
                    continue
                rows.append({"ts": ts, "pair": pair, "quick": q,
                             "tide": "RISING" if tide == "RISIN" else tide,
                             "src": path.name})
    return pd.DataFrame(rows)


def _store_arrs(pair):
    """(ts_s int64, high, close) from the pair's native 5m feather."""
    fn = STORE / (pair.replace("/", "_") + "-5m.feather")
    if not fn.exists():
        return None
    try:
        df = pd.read_feather(fn)
    except Exception:
        return None
    cols = {c.lower(): c for c in df.columns}
    if "close" not in cols or "high" not in cols:
        return None
    tcol = cols.get("date") or cols.get("ts")
    if tcol is None:
        return None
    ts = (pd.to_datetime(df[tcol], utc=True)
          .astype("datetime64[ns, UTC]").astype("int64") // 10**9
          ).to_numpy(np.int64)
    order = np.argsort(ts)
    return ts[order], df["high"].to_numpy(float)[order], \
        df["close"].to_numpy(float)[order]


def _outcome(arrs, i):
    """(payer, mfe_pct) for a candidate at bar i — entry at close[i],
    MFE over the next 12 bars. None when censored."""
    ts, h, c = arrs
    n = len(c)
    if i < 0 or i + 1 + WINDOW_BARS > n:
        return None
    entry = float(c[i])
    if entry <= 0:
        return None
    mfe = float(h[i + 1: i + 1 + WINDOW_BARS].max()) / entry - 1.0
    return (mfe >= GOAL_PCT / 100.0, mfe * 100.0)


def _fire_ev(s, y, bar):
    """$/fire at a bar — the refit's convention."""
    m = s >= bar
    n = int(m.sum())
    if not n:
        return 0.0, 0, 0.0
    usd = sum(BASE_ENTRY_USD * hs.brain_size_mult(float(si)) *
              (GOAL_PCT / 100.0 if yy else 0.0)
              for si, yy in zip(s[m], y[m]))
    return usd / n, n, y[m].mean() * 100.0


t0 = time.time()
rec = _records()
out("=== THE TIDE BAR REFIT — the OUT bar learns from live outcomes ===")
out(f"candidates with a tide stamp: {len(rec)} "
    f"(tides: " + ", ".join(f"{k} {v}" for k, v in
                            rec["tide"].value_counts().items()) + ")")
out("")

# ── 1. dedupe scan-repeats: one candidate per (pair, 15-min bucket) ───
rec["bucket"] = rec["ts"].dt.floor(f"{DEDUPE_BUCKET_MIN}min")
n0 = len(rec)
rec = (rec.sort_values("quick", ascending=False)
          .drop_duplicates(subset=["pair", "bucket"]).reset_index(drop=True))
out(f"after dedupe (1 per pair per {DEDUPE_BUCKET_MIN} min, best score): "
    f"{len(rec)} of {n0}")

# ── 2. join to the deep store: the forward 60-min outcome ────────────
arrs_cache = {}
joined = []
censored = 0
for r in rec.itertuples():
    a = arrs_cache.get(r.pair)
    if a is None:
        a = arrs_cache[r.pair] = _store_arrs(r.pair)
    if a is None:
        continue
    i = int(np.searchsorted(a[0], int(r.ts.timestamp())))
    oc = _outcome(a, i)
    if oc is None:
        censored += 1
        continue
    pay, mfe = oc
    joined.append({"ts": r.ts, "pair": r.pair, "quick": r.quick,
                   "tide": r.tide, "payer": pay, "mfe": mfe})
J = pd.DataFrame(joined)
out(f"joined to the store: {len(J)} candidates "
    f"({censored} censored inside the last {WINDOW_BARS} bars)")
if J.empty:
    out("NOTHING JOINED — cannot fit; keeping the quantile.")
    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0)
out(f"  outcome window: {J['ts'].min()} .. {J['ts'].max()}")
out("")

# ── 3. the OUT slice + the gradient check ────────────────────────────
O = J[J["tide"] == "OUT"].sort_values("ts").reset_index(drop=True)
out(f"OUT candidates joined: {len(O)}  "
    f"(payers {O['payer'].mean() * 100:.1f}% — the store's own verdict "
    f"on what the gates suppressed in OUT tides)")
out("")
out("payer rate by quick-score band (OUT) — the gradient the quantile")
out("assumes is monotone-up:")
band = 10.0
for lo in (40, 50, 60, 70, 80):
    m = (O["quick"] >= lo) & (O["quick"] < lo + band)
    if m.any():
        out(f"  [{lo:>2.0f}-{lo + band:>3.0f})  n={int(m.sum()):>4}  "
            f"{O.loc[m, 'payer'].mean() * 100:>5.1f}%")
out("")

if len(O) < 60:
    out("too few OUT candidates to fit honestly — keeping the quantile.")
    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0)

# ── 4. chronological split + the two bars ────────────────────────────
split_ts = O["ts"].quantile(TRAIN_FRAC)
tr = O[O["ts"] <= split_ts]
ho = O[O["ts"] > split_ts]
s_tr, y_tr = tr["quick"].to_numpy(float), tr["payer"].to_numpy(float)
s_ho, y_ho = ho["quick"].to_numpy(float), ho["payer"].to_numpy(float)
out(f"chronological split at {split_ts}: train {len(tr)}, holdout {len(ho)}")

rate_tr = float(y_tr.mean())
out(f"train OUT payer rate: {rate_tr * 100:.1f}%")

# the QUANTILE bar, the learner's own recipe: the (1-rate) quantile of
# the funnel's score history (learn() feeds it the whole candidate
# population — all tides — so the same here)
q = max(0.0, min(1.0, 1.0 - rate_tr))
bar_q = tl._quantile(sorted(J["quick"].tolist()), q)
bar_q = round(max(tl.BAR_MIN, min(tl.BAR_MAX, bar_q)), 1)

# the OUTCOME bar: EV-max over the train grid
grid = list(range(80, 98))
best = None
for B in grid:
    ev, n_fire, _ = _fire_ev(s_tr, y_tr, float(B))
    if n_fire < MIN_TRAIN_FIRES:
        continue
    if best is None or ev > best[1]:
        best = (B, ev, n_fire)
if best is None:
    out("no bar clears the train-fires floor — keeping the quantile.")
    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0)
bar_o = float(best[0])

out("")
out(f"{'bar':>12} {'value':>6} | {'train $/fire':>12} {'fires':>6} | "
    f"{'holdout $/fire':>14} {'fires':>6} {'payers':>7}")
# the full holdout curve around the clamp floor — the production floor
# must be evidence, not an inherited guard
for B in (78, 80, 82, 84, 86, 90, bar_o if bar_o not in (78, 80, 82, 84, 86, 90) else None):
    if B is None:
        continue
    ev_t, n_t, _ = _fire_ev(s_tr, y_tr, float(B))
    ev_h, n_h, p_h = _fire_ev(s_ho, y_ho, float(B))
    out(f"{'  curve':>12} {B:>6.1f} | {ev_t:>+11.2f}$ {n_t:>6d} | "
        f"{ev_h:>+13.2f}$ {n_h:>6d} {p_h:>6.1f}%")
ev_q_tr, n_q_tr, _ = _fire_ev(s_tr, y_tr, bar_q)
ev_q_ho, n_q_ho, pay_q_ho = _fire_ev(s_ho, y_ho, bar_q)
ev_o_tr, n_o_tr, _ = _fire_ev(s_tr, y_tr, bar_o)
ev_o_ho, n_o_ho, pay_o_ho = _fire_ev(s_ho, y_ho, bar_o)
out(f"{'quantile':>12} {bar_q:>6.1f} | {ev_q_tr:>+11.2f}$ {n_q_tr:>6d} | "
    f"{ev_q_ho:>+13.2f}$ {n_q_ho:>6d} {pay_q_ho:>6.1f}%")
out(f"{'outcomes':>12} {bar_o:>6.1f} | {ev_o_tr:>+11.2f}$ {n_o_tr:>6d} | "
    f"{ev_o_ho:>+13.2f}$ {n_o_ho:>6d} {pay_o_ho:>6.1f}%")
out("")

ship = ev_o_ho > ev_q_ho
out(f"VERDICT: {'SHIP the outcome bar' if ship else 'KEEP the quantile bar'} "
    f"(holdout $/fire {ev_o_ho:+.2f} vs {ev_q_ho:+.2f})")
out("")
out("learn() re-checks outcome evidence on every 30-min build: the")
out("outcome mapping ships only when enough live candidates have been")
out("joined and it prices at-or-above the quantile in-sample; otherwise")
out("the quantile stays (fail-closed, quantile fallback).")
out(f"join+fit took {time.time() - t0:.0f}s")

OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n-> _tidebarrefit.txt")
