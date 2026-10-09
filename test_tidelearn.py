"""THE EV-LADDER DRIVE (Sep 29): pins the ladder's admission policy —
the longest-recurring CodeRabbit finding (the Sep 27 exhaustion hole
0f48047 reopened as a 0-vs-0 admission in round 3 because the policy
lived mid-function with no drive over it). The policy now lives in
tide_learn._ladder_admits (pure) and this drive pins every branch PLUS
an integration leg through the real _outcome_fit_bar on a constructed
zero-EV >=80 band. Red-first: with the pre-round-3 admission shape
(`ev_low >= ev_80 * MARGIN` alone) the integration leg ships 68 — the
pin reads 68 and fails."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name
                 + (f"  [{detail}]" if detail and not ok else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import tide_learn as tl
import scratch_space   # scratch policy: nothing volatile lands in the repo root

M = tl.EV_LADDER_MARGIN

# ── 1. the pure policy: every branch ────────────────────────────────
check("the zero-EV tie DENIES (the round-3 regression: a 0-vs-0 "
      "admission shipped a below-80 bar whose fires pay nothing)",
      tl._ladder_admits(0.0, 0.0) is False, (0.0, 0.0))
check("admission demands POSITIVE low-band EV (negative evidence denies)",
      tl._ladder_admits(-1.0, 0.0) is False
      and tl._ladder_admits(-0.5, -1.0) is False, "")
check("a MISPRICED funnel admits: positive EV beating the >=80 band "
      "by the margin",
      tl._ladder_admits(5.0 * M, 5.0) is True, (5.0 * M, 5.0))
check("the margin is exact: meeting EV_LADDER_MARGIN admits, falling "
      "short denies",
      tl._ladder_admits(5.0, 5.0 / M) is True
      and tl._ladder_admits(5.0, 5.0 / M + 0.01) is False, M)
check("an EQUAL band never widens the funnel (parity is not mispricing)",
      tl._ladder_admits(5.0, 5.0) is False, "")
check("a missing >=80 comparison is refused (the caller's own "
      "fail-closed branch handles it; the policy never sees None as "
      "beatable)",
      tl._ladder_admits(5.0, None) is False, "")

# ── 2. the wiring: the fit path must call the policy ────────────────
# THE SOURCE COMES FROM THE IMPORTED MODULE (inspect, not a path):
# under the gate the drive runs in scratch with PYTHONPATH at the real
# repo — a BASE-relative open can hit a path another drive's cleanup
# already removed; getsource always reads the code that actually ran.
import inspect
src = inspect.getsource(tl)
check("the ladder's admission branch calls _ladder_admits (the policy "
      "is the single seam, not an inline comparison)",
      "_ladder_admits(best_low[1], best_80[1])" in src
      and "elif _ladder_admits(" in src, "")

# ── 3. INTEGRATION through the real _outcome_fit_bar ────────────────
# 30 fires @ quick 90 + 30 fires @ quick 70, NONE of them paying: the
# >=80 band has 30 fires with EV exactly 0.0 and the low band pays
# nothing either. A healthy ladder DENIES (bar 80); the pre-round-3
# shape ADMITS the 0-vs-0 tie (bar 68) — the red-first tripwire.
import numpy as np
import pandas as pd

rows = []
t0 = pd.Timestamp("2026-09-01", tz="America/Denver")
for i in range(30):    # the >=80 band: fires, never pays
    rows.append({"ts": t0 + pd.Timedelta(hours=i),
                 "pair": f"P{i}/USD", "quick": 90.0, "state": "OUT"})
for i in range(30):    # the low band: fires, never pays
    rows.append({"ts": t0 + pd.Timedelta(hours=100 + i),
                 "pair": f"Q{i}/USD", "quick": 70.0, "state": "OUT"})
_df = pd.DataFrame(rows)

# candle stubs: every pair stays flat (mfe 0.0 < the 1.5% goal) — the
# real MFE walk runs over arrays sized so the guard (i+1+12 <= len)
# passes: 14 bars, the synthetic ts landing at searchsorted index 1.
arrs = {}
for i in range(30):
    arrs[f"P{i}/USD"] = (np.array([0.0]), np.ones(14), np.ones(14))
    arrs[f"Q{i}/USD"] = (np.array([0.0]), np.ones(14), np.ones(14))

_real_records = tl._outcome_records
tl._outcome_records = lambda: _df
try:
    res = tl._outcome_fit_bar("OUT", [50.0] * 80, arr_cache=arrs)
finally:
    tl._outcome_records = _real_records

check("the fit ran on the constructed evidence (the harness itself is "
      "sound — 60 joined rows, a 42-row train split)",
      res is not None and res["n"] == 60
      and abs(res["payer_rate"]) < 1e-12, res)
check("INTEGRATION: the zero-EV >=80 band DENIES the below-80 bar "
      "(bar ships at the 80 floor, not 68 — the pre-round-3 shape "
      "fails this leg)",
      res is not None and res["bar"] == 80.0
      and res["ev_ladder"] is False, res)
check("the deny kept the honest zero EV in the record (no fiction)",
      res is not None and res["ev_o"] == 0.0, res)

# the ADMIT side through the same real path: the low band EARNs it —
# 40 low-band fires that PAY (positive EV) vs a >=80 band of 20
# non-payers (enough fires, EV exactly 0.0). The V rows come FIRST so
# the 70% chronological split keeps both bands inside the train half:
# 42 train rows = 20 @90 + 22 @70-payers; OUTCOME_MIN_JOIN (40) passes.
rows2 = []
for i in range(20):    # the 80 band: fires, never pays (EV 0.0)
    rows2.append({"ts": t0 + pd.Timedelta(hours=i),
                  "pair": f"V{i}/USD", "quick": 90.0, "state": "OUT"})
for i in range(40):    # low band, payers
    rows2.append({"ts": t0 + pd.Timedelta(hours=100 + i),
                  "pair": f"W{i}/USD", "quick": 70.0, "state": "OUT"})
_df2 = pd.DataFrame(rows2)
arrs2 = {}
for i in range(40):
    arrs2[f"W{i}/USD"] = (np.array([0.0]),
                          np.concatenate([np.ones(1), np.full(13, 2.0)]),
                          np.ones(14))
for i in range(20):
    arrs2[f"V{i}/USD"] = (np.array([0.0]), np.ones(14), np.ones(14))

tl._outcome_records = lambda: _df2
try:
    res2 = tl._outcome_fit_bar("OUT", [50.0] * 80, arr_cache=arrs2)
finally:
    tl._outcome_records = _real_records

check("INTEGRATION: a genuinely mispriced low band ADMITS (positive EV "
      "payers below 80 with a paid-out >=80 band ships the wider bar)",
      res2 is not None and res2["bar"] < 80.0
      and res2["ev_ladder"] is True
      and res2["ev_o"] > 0.0, res2)

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open(scratch_space.report_temp('_tidelearn_check.txt'), "w",
     encoding="ascii", errors="replace").write(out)
print(out)


def main():
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
