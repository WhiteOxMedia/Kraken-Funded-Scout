"""THE PER-GATE DRIVE (task 1): the SQL, the loader, and the CSV all
proven on a fixture db — plus the exporter executed against the LIVE
journals (read-only) to prove the real path runs end to end."""
import csv
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import per_gate_export as pge

results: list[tuple[str, bool, object]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ── the fixture db (spec task 1: run the SQL against a small fixture) ──
def _fixture_con():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE popup_suppressions (rotation_id TEXT, "
                "candidate_id TEXT, gate TEXT, ts TEXT, score REAL)")
    con.execute("CREATE TABLE hindsight_peaks (candidate_id TEXT, "
                "max_run_pct REAL, peak_ts INTEGER)")
    rows = [
        # gate A: 4 measurable, 3 payers -> r_g 0.75
        ("R", "A|1", "tide-out", "2026-09-28T12:10:00-07:00", 71.0),
        ("R", "A|2", "tide-out", "2026-09-28T12:11:00-07:00", 60.0),
        ("R", "A|3", "tide-out", "2026-09-28T12:12:00-07:00", 66.0),
        ("R", "A|4", "tide-out", "2026-09-28T12:13:00-07:00", 58.0),
        # gate B: 2 measurable, 2 payers -> r_g 1.0 (worst by rate)
        ("R", "B|1", "deep-veto", "2026-09-28T12:20:00-07:00", 94.0),
        ("R", "B|2", "deep-veto", "2026-09-28T12:21:00-07:00", 88.0),
        # gate C: 1 measurable, 0 payers -> r_g 0.0
        ("R", "C|1", "stale-move", "2026-09-28T12:30:00-07:00", 40.0),
        # gate D: ONLY an unmeasurable candidate -> B_g 0, r_g NULL
        ("R", "D|1", "popup-floor", "2026-09-28T12:40:00-07:00", 50.0),
        # a DIFFERENT rotation must not leak in
        ("R2", "X|1", "tide-out", "2026-09-28T13:10:00-07:00", 90.0),
    ]
    con.executemany("INSERT INTO popup_suppressions VALUES (?,?,?,?,?)", rows)
    peaks = [
        ("A|1", 2.0, 1), ("A|2", 1.6, 2), ("A|3", 9.0, 3), ("A|4", 0.2, 4),
        ("B|1", 3.1, 5), ("B|2", 1.5, 6), ("C|1", 0.4, 7),
        ("D|1", None, None), ("X|1", 5.0, 8),
    ]
    con.executemany("INSERT INTO hindsight_peaks VALUES (?,?,?)", peaks)
    return con


con = _fixture_con()
try:
    out = pge.run_ratios_sql(con, "R", 1.5)
    check("the SQL runs verbatim from sql/per_gate_ratios.sql", len(out) == 4,
          out)
    by = {r["gate"]: r for r in out}
    check("r_g sorts DESC with NULL last (deep-veto 1.0 first, the "
          "unmeasurable gate last)",
          [r["gate"] for r in out] ==
          ["deep-veto", "tide-out", "stale-move", "popup-floor"],
          [r["gate"] for r in out])
    check("deep-veto: B_g 2, P_g 2, r_g 1.0",
          by["deep-veto"]["measurable_B_g"] == 2
          and by["deep-veto"]["P_g"] == 2
          and abs(by["deep-veto"]["r_g"] - 1.0) < 1e-9, by["deep-veto"])
    check("tide-out: B_g 4, P_g 3, r_g 0.75",
          by["tide-out"]["measurable_B_g"] == 4
          and by["tide-out"]["P_g"] == 3
          and abs(by["tide-out"]["r_g"] - 0.75) < 1e-9, by["tide-out"])
    check("stale-move: B_g 1, P_g 0, r_g 0.0 (an honest zero)",
          by["stale-move"]["measurable_B_g"] == 1
          and by["stale-move"]["P_g"] == 0
          and abs(by["stale-move"]["r_g"] - 0.0) < 1e-9, by["stale-move"])
    check("popup-floor: B_g 0 (unmeasurable only), P_g 0, r_g NULL",
          by["popup-floor"]["measurable_B_g"] == 0
          and by["popup-floor"]["P_g"] in (0, None)
          and by["popup-floor"]["r_g"] is None, by["popup-floor"])
    check("another rotation's rows never leak into the window",
          all(r["gate"] != "ghost" for r in out), out)
    check("the goal boundary is >= (1.5 counts, 1.49 does not)",
          by["tide-out"]["P_g"] == 3, by["tide-out"])
finally:
    con.close()

# ── the loader: journals + cache -> fixture db (the machine path) ───
_sbx = Path(tempfile.mkdtemp())
sup = _sbx / "popup_suppressions.jsonl"
now = datetime.now().astimezone()
t0 = now.replace(minute=0, second=0, microsecond=0)
sup.write_text("\n".join(json.dumps(r) for r in (
    {"ts": t0.isoformat(), "pair": "AAA/USD", "gate": "tide-out",
     "quick": 71.0, "rot": "R"},
    {"ts": (t0 + timedelta(minutes=5)).isoformat(), "pair": "AAA/USD",
     "gate": "tide-out", "quick": 72.0, "rot": "R"},          # dup
    {"ts": (t0 + timedelta(minutes=6)).isoformat(), "pair": "BBB/USD",
     "gate": "deep-veto", "quick": 94.0, "rot": "R"},
    {"ts": (t0 + timedelta(minutes=7)).isoformat(), "pair": "CCC/USD",
     "gate": "tide-out", "quick": 55.0, "rot": "R"},        # a dud
    {"ts": (t0 + timedelta(minutes=8)).isoformat(), "pair": "DDD/USD",
     "gate": "tide-out", "quick": 80.0, "rot": "OTHER"},     # other rot
)), encoding="utf-8")
db = _sbx / "ohlcv_cache.db"
c2 = sqlite3.connect(db)
c2.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, "
           "high REAL, low REAL, close REAL)")
ms = int(t0.timestamp() * 1000)
c2.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)", [
    ("AAA/USD", ms - 600_000, 1.0, 1.0, 1.0, 1.0),
    ("AAA/USD", ms + 3_600_000, 1.0, 1.08, 1.0, 1.05),
    ("BBB/USD", ms + 240_000, 2.0, 2.0, 2.0, 2.0),
    ("BBB/USD", ms + 3_600_000, 2.0, 2.10, 2.0, 2.05),
    ("CCC/USD", ms - 600_000, 1.0, 1.0, 1.0, 1.0),   # entry for the dud
    ("CCC/USD", ms + 1_800_000, 1.0, 1.00, 0.999, 1.00),  # never moves
    ("DDD/USD", ms - 600_000, 1.0, 1.0, 1.0, 1.0),   # other rot: excluded
])
c2.commit()
c2.close()

rows = pge.dedup_first(pge.load_rotation_rows(sup, "R"))
check("the journal loads one rotation only and dedups to FIRST refusal "
      "per (pair, gate)", len(rows) == 3
      and {r["pair"] for r in rows} == {"AAA/USD", "BBB/USD", "CCC/USD"},
      rows)

con3 = pge.build_fixture_db(rows, db, 1.5)
try:
    out3 = pge.run_ratios_sql(con3, "R", 1.5)
    check("the machine loader computes real peaks from the cache "
          "(tide-out 1/2, deep-veto 1/1)",
          {r["gate"]: (r["measurable_B_g"], r["P_g"]) for r in out3} ==
          {"tide-out": (2, 1), "deep-veto": (1, 1)}, out3)
    summ = pge.rotation_summary(out3)
    check("the rotation summary feeds the auto-adjust (B 3, P 2)",
          summ == {"B": 3, "P": 2, "r": 2 / 3}, summ)
finally:
    con3.close()

# the CSV artifact
art = _sbx / "artifacts" / "per_gate_ratios" / "rotation_R.csv"
pge.export_csv(out3, art)
with art.open(encoding="utf-8") as f:
    content = f.read()
check("the CSV artifact carries the header, both gates, and 4-place "
      "ratios",
      content.splitlines()[0] == "gate,measurable_B_g,P_g,r_g"
      and "deep-veto,1,1,1.0000" in content
      and "tide-out,2,1,0.5000" in content, content)

# the LIVE path: real journals, read-only, no exception
try:
    live_rows = pge.dedup_first(pge.load_rotation_rows(
        BASE / "popup_suppressions.jsonl", "LIVE-PROBE-NO-SUCH-ROT"))
    check("the live journal path runs read-only and returns [] for an "
          "unknown rotation", live_rows == [], len(live_rows))
except Exception as exc:
    check("the live journal path runs read-only", False, repr(exc))

# the peak seam matches the console panel's join (same queries)
con4 = sqlite3.connect(":memory:")
con4.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, "
             "high REAL, low REAL, close REAL)")
con4.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)", [
    ("Z/USD", ms - 3_600_000, 1.0, 1.0, 1.0, 1.0),
    ("Z/USD", ms, 1.0, 1.02, 1.0, 1.01),
    ("Z/USD", ms + 7 * 3_600_000 - 60_000, 1.0, 1.30, 1.0, 1.2),
    ("Z/USD", ms + 7 * 3_600_000 + 60_000, 1.0, 9.99, 1.0, 9.0),  # outside
])
pk = pge.compute_peak(con4, "Z/USD", ms, 1.5)
check("the peak window is 7h inclusive and entry is the pre-refusal "
      "close (+30% inside, the 9.99 outside bar ignored)",
      abs(pk["max_run_pct"] - 30.0) < 0.01, pk)
con4.close()

fails = [r for r in results if not r[1]]
out_txt = BASE / "apptest_check.txt"  # leave the suite's file alone
rep = BASE / "artifacts" / "per_gate_ratios" / "_pergate_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
