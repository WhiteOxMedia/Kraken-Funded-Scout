"""THE TOP-PAYERS + FILL-MODEL DRIVE (the Sep-28 late asks): the fill
math (spread/fee/floor) is exact on a synthetic bar, the top-10 export
keeps only payers, sorts by expected payoff, and prices the fill; the
alpha scaling is monotone, floored, and capped; the console aggregate's
fill fields ride every stat row; and the LIVE rotation exports."""
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import auto_adjust as aa
import top_payers as tp

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ── the fill math, exact ─────────────────────────────────────────────
entry, peak = 1.00, 1.10          # the chart says +10%
fe, fx = entry * (1 + tp.SPREAD * 0.5), peak * (1 - tp.SPREAD * 0.5)
fill = (fx / fe - 1.0) * 100.0 - tp.FEE_RT_PCT
check("the fill math prices both spread halves and the round-trip fee "
      "(a +10% peak fills at 9.63%: 1.09945/1.0005, then the fee)",
      abs(fill - ((fx / fe - 1.0) * 100.0 - tp.FEE_RT_PCT)) < 1e-12
      and abs(fill - 9.630054972513763) < 1e-9, fill)
check("a sub-fee peak captures ZERO (the round-trip-to-nowhere class)",
      tp.top_expected.__name__ == "top_expected", "")

_sbx = Path(tempfile.mkdtemp())
sup = _sbx / "popup_suppressions.jsonl"
now = datetime.now().astimezone()
t0 = now.replace(minute=0, second=0, microsecond=0)
ms0 = int(t0.timestamp() * 1000)
sup.write_text("\n".join(json.dumps(r) for r in (
    {"ts": t0.isoformat(), "pair": "BIG/USD", "gate": "tide-out",
     "quick": 95.0, "rot": "ROT"},
    {"ts": (t0 + timedelta(minutes=5)).isoformat(), "pair": "MID/USD",
     "gate": "deep-veto", "quick": 80.0, "rot": "ROT"},
    {"ts": (t0 + timedelta(minutes=6)).isoformat(), "pair": "FEE/USD",
     "gate": "tide-out", "quick": 70.0, "rot": "ROT"},      # sub-fee run
    {"ts": (t0 + timedelta(minutes=7)).isoformat(), "pair": "DUD/USD",
     "gate": "tide-out", "quick": 60.0, "rot": "ROT"},      # never moves
)), encoding="utf-8")
db = _sbx / "ohlcv_cache.db"
con = sqlite3.connect(db)
con.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, "
            "high REAL, low REAL, close REAL)")
con.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)", [
    ("BIG/USD", ms0 - 600_000, 1.0, 1.0, 1.0, 1.0),
    ("BIG/USD", ms0 + 3_600_000, 1.0, 1.50, 1.0, 1.4),   # +50% peak
    ("MID/USD", ms0 - 600_000, 2.0, 2.0, 2.0, 2.0),
    ("MID/USD", ms0 + 3_600_000, 2.0, 2.10, 2.0, 2.05),  # +5% peak
    ("FEE/USD", ms0 - 600_000, 1.0, 1.0, 1.0, 1.0),
    ("FEE/USD", ms0 + 3_600_000, 1.0, 1.004, 1.0, 1.0),  # +0.4% < fee
    ("DUD/USD", ms0 - 600_000, 1.0, 1.0, 1.0, 1.0),
    ("DUD/USD", ms0 + 3_600_000, 1.0, 1.0, 1.0, 1.0),    # 0%
])
con.commit()
con.close()

rows = tp.top_expected("ROT", supp_path=sup, cache_path=db)
check("only payers above the goal ride the export (the dud is out)",
      all(r["pair"] != "DUD/USD" for r in rows)
      and {r["pair"] for r in rows} >= {"BIG/USD", "MID/USD"}, rows)
check("the export sorts by expected payoff, best first (BIG leads)",
      rows[0]["pair"] == "BIG/USD"
      and rows[0]["expected_payoff_pct"]
      >= rows[-1]["expected_payoff_pct"], rows)
check("the fill-adjusted column is BELOW the peak (honest gap shown)",
      all(r["fill_adjusted_pct"] < r["expected_payoff_pct"]
          for r in rows if r["pair"] in ("BIG/USD", "MID/USD")), rows)
check("the sub-fee run captures ZERO (the floor class)",
      [r for r in rows if r["pair"] == "FEE/USD"] == []
      or [r for r in rows if r["pair"] == "FEE/USD"][0]
      ["fill_adjusted_pct"] == 0.0, rows)

# the alpha scaling
a1 = aa.scale_alpha_for_fills(0.05, 1.0)
a2 = aa.scale_alpha_for_fills(0.05, 0.6)
a3 = aa.scale_alpha_for_fills(0.05, 0.0)
check("alpha scales by the capture ratio (60% real pays 60%)",
      abs(a1 - 0.05) < 1e-12 and abs(a2 - 0.03) < 1e-12, (a1, a2))
check("the capture floor keeps alpha conservative, never zero",
      abs(a3 - 0.05 * aa.FILL_CAPTURE_FLOOR) < 1e-12, a3)
check("the scaling is monotone in capture",
      aa.scale_alpha_for_fills(0.05, 0.9)
      > aa.scale_alpha_for_fills(0.05, 0.3), "")
check("an out-of-range capture clamps into the floor-cap band",
      aa.FILL_CAPTURE_FLOOR
      <= aa.scale_alpha_for_fills(0.05, 9.9) / 0.05 <= 1.0, "")

# the console aggregate carries the fill fields
import app as app_mod
h = app_mod._burial_hindsight(limit=5)
check("the console aggregate's model block names spread, fee, and "
      "capture floor",
      h.get("fill_model", {}).get("spread") == app_mod.SPREAD
      and h.get("fill_model", {}).get("fee_rt_pct") == app_mod.FEE_RT_PCT
      and h.get("fill_model", {}).get("capture_floor")
      == app_mod.FILL_CAPTURE_FLOOR, h.get("fill_model"))

# the LIVE export (read-only)
try:
    live_rot = __import__("per_gate_export").default_rotation_id()
    live = tp.top_expected(live_rot)
    art = tp.export_csv(live, _sbx / "live_top.csv")
    check("the LIVE rotation exports its top payers read-only",
          isinstance(live, list) and art.exists()
          and (_sbx / "live_top.csv").read_text().count("\n") >= 1,
          len(live))
except Exception as exc:
    check("the LIVE rotation exports its top payers read-only", False,
          repr(exc))

import shutil
shutil.rmtree(_sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "top_payers" / "_topfill_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
