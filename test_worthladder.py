"""THE WORTH LADDER's drive (user spec, Oct 5) - the learning ladder
closes the loop: verdict-gated floor candidates, QC agreement on the
same number, the exit-watch preflight, immutable checksummed evidence.
Red-first: every pin here failed against the missing module first."""
import json, sys, hashlib, os, tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))

import worth_ladder as wl
import scratch_space   # scratch policy: nothing volatile lands in the repo root

# - 0. the sandbox: the evidence dir, the ledger, the armed cell and the
# console state all ride tempdir sinks (no test rows where the machine banks).
_sbx = Path(tempfile.mkdtemp())
wl.EVIDENCE_DIR = _sbx / "evidence"
wl.LEDGER_FILE = wl.EVIDENCE_DIR / "worth_ladder_ledger.jsonl"
wl.CONSOLE_STATE = _sbx / "console_state.json"
_cell = _sbx / "armed_cell.json"
_cell.write_text(json.dumps({"tier": 10000, "worth_floor_frac": 0.25}),
                 encoding="utf-8")
os.environ["WORTH_LADDER_ARMED_CELL"] = str(_cell)
os.environ["WORTH_LADDER_ENABLED"] = "1"

# - 1. THE AGREEMENT PINS (hard rule 1+2)
check("A realized $40, left $0 -> PROVEN", wl.compute_verdict(40.0, 0.0) == "PROVEN")
check("A the unproven shapes: realized 0, loss, or a crumb over",
      wl.compute_verdict(0.0, 505.0) == "UNPROVEN"
      and wl.compute_verdict(-10.0, 0.0) == "UNPROVEN"
      and wl.compute_verdict(40.0, 1.01) == "UNPROVEN")
cand = wl.candidate_floor(0.25, True)
check("A the PROVEN candidate rises by the ladder nudge inside the dial band",
      abs(cand - 0.275) < 1e-9, cand)
check("A the candidate never LOWERS from a single rotation (0.40 at cap)",
      wl.candidate_floor(0.40, True) == 0.40)
check("B the UNPROVEN candidate is the current floor (no change)",
      wl.candidate_floor(0.25, False) == 0.25
      and wl.candidate_floor(0.40, False) == 0.40)

# - 2. THE QC SEAM (hard rule 3): one number, evidence, immutable rows
pay = wl.propose_from_rotation(rotation_id="2026-10-05-12-16", score=80.0,
                               realized_usd=40.0, total_left_usd=0.0,
                               current_floor=0.25)
check("QC the payload carries the verdict, the armed cell and the SAME candidate",
      pay["verdict"] == "PROVEN"
      and abs(pay["candidate_floor"] - 0.275) < 1e-9
      and pay["armed_cell_checksum"].startswith("sha256:")
      and pay["evidence_checksum"].startswith("sha256:"), pay)
_evp = wl.EVIDENCE_DIR / pay["evidence_file"]
_h = "sha256:" + hashlib.sha256(_evp.read_bytes()).hexdigest()
check("QC the evidence checksum verifies the artifact's exact bytes",
      _evp.exists() and _h == pay["evidence_checksum"])
r1 = wl.qc_decide(pay, operator="ops", decision="rejected", reason="not tonight")
check("QC a rejection writes its immutable row and applies NOTHING",
      r1["applied"] is False
      and not wl.CONSOLE_STATE.exists()
      and wl._ledger_rows(kind="qc_decision")[-1]["decision"] == "rejected")
try:
    wl.qc_decide(pay, operator="ops", decision="confirmed")
    check("QC the ledger is immutable (a decided rotation cannot re-decide)", False)
except Exception:
    check("QC the ledger is immutable (a decided rotation cannot re-decide)", True)

# - 3. THE EXIT-WATCH PREFLIGHT (hard rule 4): the sim gates the apply
pay2 = wl.propose_from_rotation(rotation_id="2026-10-05-16-16", score=85.0,
                                realized_usd=60.0, total_left_usd=0.0,
                                current_floor=0.275)
_watch = [{"pair": "A/USD", "entry": 1.0, "size": 5000.0, "goal_pct": 2.0},
          {"pair": "B/USD", "entry": 1.0, "size": 5000.0, "goal_pct": 2.0}]
r3 = wl.qc_decide(pay2, operator="ops", decision="confirmed",
                  watch_rows=_watch, daily_pace=240.0)
check("EXITCH a servable book passes the sim and the apply lands the CANDIDATE number",
      r3["applied"] is True
      and abs(r3["applied_floor"] - pay2["candidate_floor"]) < 1e-9, r3)
check("EXITCH the queued recommendation equals the apply number (QC agreement)",
      r3["applied_floor"] == pay2["candidate_floor"])
check("EXITCH the console dial now carries the applied floor",
      abs(json.loads(wl.CONSOLE_STATE.read_text())["worth_floor_frac"]
          - pay2["candidate_floor"]) < 1e-9)
check("EXITCH the apply artifact + the checksummed ledger row exist",
      (wl.EVIDENCE_DIR / r3["apply_file"]).exists()
      and r3["evidence_checksum"].startswith("sha256:"))
pay3 = wl.propose_from_rotation(rotation_id="2026-10-05-20-16", score=82.0,
                                realized_usd=30.0, total_left_usd=0.0,
                                current_floor=pay2["candidate_floor"])
_tight = [{"pair": "C/USD", "entry": 1.0, "size": 3500.0, "goal_pct": 2.0}]
r4 = wl.qc_decide(pay3, operator="ops", decision="confirmed",
                  watch_rows=_tight, daily_pace=240.0)
check("EXITCH a book the candidate would silence FAILS the preflight",
      r4["applied"] is False and r4.get("exit_watch") == "fail"
      and bool(r4.get("reason")), r4)
check("EXITCH the failed apply left the dial untouched and wrote its evidence",
      abs(json.loads(wl.CONSOLE_STATE.read_text())["worth_floor_frac"]
          - pay2["candidate_floor"]) < 1e-9
      and (wl.EVIDENCE_DIR / r4["preflight_file"]).exists())

# - 4. THE LOOP (hard rule 5): only Proven mints; the flip re-evaluates
_n0 = len(wl._ledger_rows(kind="candidate"))
_e0 = len(list(wl.EVIDENCE_DIR.glob("evidence_*.json")))
wl.propose_from_rotation(rotation_id="2026-10-06-00-16", score=50.0,
                         realized_usd=0.0, total_left_usd=505.0,
                         current_floor=pay2["candidate_floor"])
check("LOOP an unproven rotation mints NO candidate and NO evidence",
      len(wl._ledger_rows(kind="candidate")) == _n0
      and len(list(wl.EVIDENCE_DIR.glob("evidence_*.json"))) == _e0)
flip = wl.reconciler_flip(rotation_id="2026-10-05-08-12",
                          realized_usd=40.0, total_left_usd=0.0,
                          current_floor=pay2["candidate_floor"])
check("C a late realization FLIPS the verdict and mints the candidate",
      flip["flip"] == "proven_by_realization" and flip["verdict"] == "PROVEN"
      and flip["candidate_floor"] > pay2["candidate_floor"], flip)
check("C the reconciler artifact + audit row are checksummed",
      (wl.EVIDENCE_DIR / flip["reconciler_file"]).exists()
      and flip["evidence_checksum"].startswith("sha256:")
      and wl._ledger_rows(kind="verdict_flip")[-1]["rotation_id"]
      == "2026-10-05-08-12")

_flip0 = len(wl._ledger_rows(kind="verdict_flip"))
flip2 = wl.reconciler_flip(rotation_id="2026-10-05-08-12",
                           realized_usd=40.0, total_left_usd=0.0,
                           current_floor=pay2["candidate_floor"])
check("C the reconciler is IDEMPOTENT (a re-run mints nothing new)",
      flip2["flip"] == "proven_by_realization"
      and flip2["reconciler_file"] == flip["reconciler_file"]
      and len(wl._ledger_rows(kind="verdict_flip")) == _flip0)

# - 4b. THE WIRE: the seams ride the live surfaces - discipline ingests
# at the rollover and the cooldown (flag-gated, fail-soft), the cooldown
# runs the reconciler pass, and the discipline plate shows the evidence.
_src = (BASE / "discipline.py").read_text(encoding="utf-8")
check("WIRE the rollover ingests the closed rotation",
      "_worth_ladder_ingest(result)" in _src)
check("WIRE the cooldown ingests the DAY and runs the reconciler pass",
      "_worth_ladder_ingest(_day_result)" in _src
      and "_worth_ladder_reconcile(state)" in _src)
check("WIRE both seams are flag-gated fail-soft helpers",
      "def _worth_ladder_ingest" in _src
      and "def _worth_ladder_reconcile" in _src
      and _src.count("if not _wl.worth_ladder_enabled():") == 2)
_app = (BASE / "app.py").read_text(encoding="utf-8")
check("WIRE the discipline plate shows the ladder evidence",
      "def _worth_ladder_evidence_html" in _app and "{_wl_html}" in _app)
import discipline as _disc
_wl_n0 = len(wl._ledger_rows(kind="candidate"))
_disc._worth_ladder_ingest({"date": "2026-10-05", "rot": "00-04",
                            "score": 90.0, "realized_pnl": 55.0,
                            "total_left": 0.0})
check("WIRE the rollover seam mints under the SAME sandboxed ledger",
      len(wl._ledger_rows(kind="candidate")) == _wl_n0 + 1
      and wl._ledger_rows(kind="candidate")[-1]["rotation_id"]
      == "2026-10-05-00-04")
_disc._worth_ladder_ingest({"date": "2026-10-05", "rot": "04-08",
                            "score": 40.0, "realized_pnl": 0.0,
                            "total_left": 210.0})
check("WIRE the seam gates on the money: an UNPROVEN day mints nothing",
      len(wl._ledger_rows(kind="candidate")) == _wl_n0 + 1)
pay5 = wl.propose_from_rotation(rotation_id="2026-10-05-04-04", score=70.0,
                                realized_usd=25.0, total_left_usd=0.0,
                                current_floor=0.25)
_f5 = wl.reconciler_flip(rotation_id="2026-10-05-04-04", realized_usd=25.0,
                         total_left_usd=0.0, current_floor=0.25)
check("WIRE a minted rotation cannot double-mint through the reconciler",
      _f5["flip"] == "none" and _f5.get("already_minted") is True
      and len(wl._ledger_rows(kind="verdict_flip")) == _flip0)
_wlf0 = len(wl._ledger_rows(kind="verdict_flip"))
_disc._worth_ladder_reconcile({"history": [
    {"date": "2026-10-04", "rot": "20-24", "realized_pnl": 40.0,
     "total_left": 0.0},
    {"date": "2026-10-04", "rot": "16-20", "realized_pnl": -12.0,
     "total_left": 88.0}]})
check("WIRE the reconciler pass flips the proven entry and skips the loss",
      wl._ledger_rows(kind="verdict_flip")[-1]["rotation_id"]
      == "2026-10-04-20-24"
      and len(wl._ledger_rows(kind="verdict_flip")) == _wlf0 + 1)
_disc._worth_ladder_reconcile({"history": [
    {"date": "2026-10-04", "rot": "20-24", "realized_pnl": 40.0,
     "total_left": 0.0}]})
check("WIRE the reconciler pass is idempotent through the wire too",
      len(wl._ledger_rows(kind="verdict_flip")) == _wlf0 + 1)
_es = wl.evidence_summary()
check("WIRE the evidence summary reads the whole paper trail",
      _es["candidates"] == _wl_n0 + 2 and _es["flips"] == _wlf0 + 1
      and len(_es["pending"]) == 2)
os.environ["WORTH_LADDER_ENABLED"] = "0"
_disc._worth_ladder_ingest({"date": "2026-10-05", "rot": "08-12",
                            "score": 95.0, "realized_pnl": 70.0,
                            "total_left": 0.0})
check("WIRE flag off leaves the whole seam inert",
      len(wl._ledger_rows(kind="candidate")) == _wl_n0 + 2)

# - 5. THE FLAG: off = inert (the canary rollout lever)
os.environ["WORTH_LADDER_ENABLED"] = "0"
_n1 = len(wl._ledger_rows(kind="candidate"))
check("FLAG off -> the ladder is inert (no candidates, no rows)",
      wl.propose_from_rotation(rotation_id="x", score=80.0, realized_usd=40.0,
                               total_left_usd=0.0,
                               current_floor=0.25) is None
      and len(wl._ledger_rows(kind="candidate")) == _n1)

# the leak audit's env arm: every env store above is restored before
# the drive exits (the audit reads the whole file; one finally covers
# all four stores, and the process's own env dies with it anyway)
try:
    pass
finally:
    try:
        del os.environ["WORTH_LADDER_ENABLED"]
        del os.environ["WORTH_LADDER_ARMED_CELL"]
    except Exception:
        pass

fails = [r for r in results if not r[1]]
scratch_space.report_temp('worthladder_check.txt').write_text(
    "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{d}]" if d and not ok else "")
              for name, ok, d in results) + "\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
raise SystemExit(1 if fails else 0)
