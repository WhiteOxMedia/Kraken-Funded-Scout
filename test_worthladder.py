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

# - 12. THE QC OPERATOR SURFACE (user ask, Oct 6): the plate's
# confirm/reject buttons, wired to qc_decide with the armed-cell
# checksum shown BEFORE any confirm.
check("QC SURFACE the plate mounts confirm/reject controls",
      "def _worth_ladder_qc_controls" in _app
      and _app.count(chr(10) + "    _worth_ladder_qc_controls()") == 1
      and "st.markdown(_discipline_plate(), unsafe_allow_html=True)"
      in _app)
_qc_start = _app.find("def _worth_ladder_qc_controls")
check("QC SURFACE the armed-cell checksum renders before any confirm",
      _qc_start > -1
      and _app.find("_wlq.armed_cell_checksum()", _qc_start) > -1
      and _app.find("_wlq.armed_cell_checksum()", _qc_start)
      < _app.find("CONFIRM", _qc_start)
      and "armed cell is" in _app)
# the flag-off pin above disarmed the env: re-arm for the QC section
os.environ["WORTH_LADDER_ENABLED"] = "1"
_mint = wl.propose_from_rotation(rotation_id="2026-10-06-plate-qc",
                                 score=88.0, realized_usd=30.0,
                                 total_left_usd=0.0, current_floor=0.25)
check("QC SURFACE a minted candidate carries the payload the buttons need",
      _mint["verdict"] == "PROVEN"
      and _mint.get("evidence_checksum")
      and _mint.get("candidate_floor") == 0.275)
# the EXACT payload dict the plate's buttons build, then the EXACT call
# the reject button makes:
_pay = {"rotation_id": str(_mint.get("rotation_id")),
        "verdict": str(_mint.get("verdict") or "PROVEN"),
        "candidate_floor": _mint.get("candidate_floor"),
        "current_floor": _mint.get("current_floor"),
        "score": _mint.get("score"),
        "evidence_checksum": str(_mint.get("evidence_checksum") or "")}
_res_rej = wl.qc_decide(_pay, operator="plate", decision="rejected",
                        reason="operator rejected on the plate")
check("QC SURFACE the reject button's call rejects ONCE with provenance",
      _res_rej["applied"] is False
      and _res_rej["decision"] == "rejected"
      and _res_rej["operator"] == "plate"
      and wl._ledger_rows(kind="qc_decision")[-1]["operator"] == "plate")
try:
    wl.qc_decide(_pay, operator="plate", decision="rejected")
    _double = False
except ValueError:
    _double = True
check("QC SURFACE a decided rotation can never re-decide through the plate",
      _double)

# - 13. THE GATE-FLOOR MINT (user ask, Oct 6): the weekly scorer's
# gate-floor recommendations mint as QC candidates - the same evidence
# bundle, the same ledger, the same immutability - so a recommendation
# can only move its floor through qc_decide's exit-watch path, never a
# direct file write. Red-first: these failed against the missing seam.
os.environ["WORTH_LADDER_ENABLED"] = "1"
_gate_rec = {"recommendation": 0.25, "n_priced": 7, "stable": True,
             "mode": "walked",
             "proposal": {"pool_min": 0.25, "n_priced": 7,
                          "net": 412.5, "mode": "walked"}}
_gp = wl.propose_from_recommendation(_gate_rec, source="pool_min_weekly",
                                     channel="pool_min",
                                     week_key="2026-W40")
check("GATE a stable recommendation mints a PROVEN candidate carrying "
      "the gate's OWN number",
      _gp["verdict"] == "PROVEN" and _gp["channel"] == "pool_min"
      and abs(_gp["candidate_floor"] - 0.25) < 1e-9
      and _gp["rotation_id"] == "pool_min_weekly_2026-W40_0.25"
      and _gp["evidence_checksum"].startswith("sha256:"), _gp)
check("GATE the candidate ledger row carries the channel + source the "
      "plate dispatches on",
      wl._ledger_rows(kind="candidate")[-1].get("channel") == "pool_min"
      and wl._ledger_rows(kind="candidate")[-1].get("source")
      == "pool_min_weekly")
_gp2 = wl.propose_from_recommendation(_gate_rec, source="pool_min_weekly",
                                      channel="pool_min", week_key="2026-W40")
check("GATE the mint is idempotent (a re-grade mints no second row)",
      _gp2["rotation_id"] == _gp["rotation_id"]
      and _gp2.get("already_minted") is True
      and len([r for r in wl._ledger_rows(kind="candidate")
               if r.get("rotation_id") == _gp["rotation_id"]]) == 1)
_bad = wl.propose_from_recommendation({"recommendation": 0.25,
                                       "n_priced": 7, "stable": False},
                                      source="pool_min_weekly",
                                      channel="pool_min", week_key="2026-W41")
check("GATE an unstable recommendation mints NOTHING (the scorer's "
      "stability gate is the PROVEN rule)",
      _bad["verdict"] == "UNPROVEN" and bool(_bad.get("reason"))
      and not [r for r in wl._ledger_rows(kind="candidate")
               if str(r.get("rotation_id", "")).startswith(
                   "pool_min_weekly_2026-W41")])
check("GATE an out-of-range bar mints nothing either",
      wl.propose_from_recommendation({"recommendation": 1.5,
                                      "stable": True},
                                     source="pool_min_weekly",
                                     channel="pool_min",
                                     week_key="2026-W41"
                                     )["verdict"] == "UNPROVEN")

_wl_before = wl.current_floor_frac()
_ch_calls = []
def _chan_apply():
    _ch_calls.append(1)
    return {"applied": True, "pool_min": 0.25}
_watch_ok = [{"pair": "A/USD", "entry": 1.0, "size": 5000.0,
              "goal_pct": 2.0},
             {"pair": "B/USD", "entry": 1.0, "size": 5000.0,
              "goal_pct": 2.0}]
_gr = wl.qc_decide(_gp, operator="plate", decision="confirmed",
                   watch_rows=_watch_ok, daily_pace=240.0,
                   channel_apply=_chan_apply)
check("GATE a confirmed gate candidate rides the SAME exit-watch path "
      "and applies ONLY through the channel",
      _gr["applied"] is True and _gr.get("channel") == "pool_min"
      and len(_ch_calls) == 1, _gr)
check("GATE the gate apply NEVER touches the console worth floor",
      wl.current_floor_frac() == _wl_before)
_gr2 = wl.propose_from_recommendation({"recommendation": 0.5,
                                       "n_priced": 6, "stable": True,
                                       "mode": "walked",
                                       "proposal": {"pool_min": 0.5}},
                                      source="pool_min_weekly",
                                      channel="pool_min", week_key="2026-W42")
check("GATE the evidence summary carries each pending candidate's "
      "channel to the plate",
      any(str(p.get("rotation_id")) == _gr2["rotation_id"]
          and p.get("channel") == "pool_min"
          for p in wl.evidence_summary()["pending"]))
_calls_before = len(_ch_calls)
_gr2res = wl.qc_decide(_gr2, operator="plate", decision="confirmed",
                       watch_rows=[], daily_pace=240.0,
                       channel_apply=_chan_apply)
check("GATE the SAME exit-watch gate refuses an unservable book - the "
      "channel never applies",
      _gr2res["applied"] is False and _gr2res.get("exit_watch") == "fail"
      and len(_ch_calls) == _calls_before, _gr2res)
_gr3 = wl.propose_from_recommendation({"recommendation": 0.75,
                                       "n_priced": 6, "stable": True,
                                       "mode": "walked",
                                       "proposal": {"pool_min": 0.75}},
                                      source="pool_min_weekly",
                                      channel="pool_min", week_key="2026-W43")
_gr3res = wl.qc_decide(_gr3, operator="ops", decision="confirmed",
                       watch_rows=_watch_ok, daily_pace=240.0,
                       channel_apply=lambda: {"applied": False,
                                              "reason": "stale - the live "
                                                        "sweep no longer "
                                                        "proposes this bar"})
check("GATE a refusing channel apply surfaces its reason and applies "
      "nothing",
      _gr3res["applied"] is False
      and "stale" in str(_gr3res.get("reason")), _gr3res)
_gr4 = wl.propose_from_recommendation({"recommendation": 1.0,
                                       "n_priced": 6, "stable": True,
                                       "mode": "walked",
                                       "proposal": {"pool_min": 1.0}},
                                      source="pool_min_weekly",
                                      channel="pool_min", week_key="2026-W44")
_gr4res = wl.qc_decide(_gr4, operator="ops", decision="confirmed",
                       watch_rows=_watch_ok, daily_pace=240.0)
check("GATE a pool_min confirm with no registered channel refuses "
      "cleanly",
      _gr4res["applied"] is False
      and "channel" in str(_gr4res.get("reason", "")).lower(), _gr4res)
_gf = wl.propose_from_recommendation({"recommendation": 4, "n_priced": 5,
                                      "stable": True, "mode": "walked",
                                      "proposal": {"weeks": 4,
                                                   "net": 90.0}},
                                     source="fbw_weekly",
                                     channel="fbw_weeks",
                                     week_key="2026-W40")
check("GATE the fbw advice recommendation mints its own channel "
      "candidate",
      _gf["verdict"] == "PROVEN" and _gf["channel"] == "fbw_weeks", _gf)
_gfr = wl.qc_decide(_gf, operator="plate", decision="confirmed",
                    watch_rows=_watch_ok, daily_pace=240.0)
check("GATE a confirmed fbw candidate LEDGERS the hand-apply (the "
      "machinery never edits itself)",
      _gfr.get("applied") is True and _gfr.get("hand_apply") is True
      and wl.current_floor_frac() == _wl_before, _gfr)
try:
    wl.qc_decide(_gp, operator="plate", decision="rejected")
    _gdouble = False
except ValueError:
    _gdouble = True
check("GATE a decided gate candidate can never re-decide (the same "
      "immutability)",
      _gdouble)
os.environ["WORTH_LADDER_ENABLED"] = "0"
check("GATE flag off -> the gate mint is inert too",
      wl.propose_from_recommendation(_gate_rec, source="pool_min_weekly",
                                     channel="pool_min",
                                     week_key="2026-W45") is None)
os.environ["WORTH_LADDER_ENABLED"] = "1"
_app_g = (BASE / "app.py").read_text(encoding="utf-8")
check("GATE the plate's direct Apply is retired - the button queues for "
      "QC",
      "Queue POOL_MIN for worth-ladder QC" in _app_g
      and "mint_gatefloor_candidates" in _app_g
      and _app_g.count("apply_staged_pool_min()") == 0)
_gqc_s = _app_g.find("def _worth_ladder_qc_controls")
_gqc_e = _app_g.find(chr(10) + "def ", _gqc_s + 1)
_gqc_b = _app_g[_gqc_s:(_gqc_e if _gqc_e > -1 else None)]
check("GATE the QC surface dispatches the gate channel's applier on "
      "confirm",
      "channel_apply" in _gqc_b
      and "_tbwq.apply_staged_pool_min" in _gqc_b
      and "pool_min" in _gqc_b)
_tbw_g = (BASE / "tidebar_weekly.py").read_text(encoding="utf-8")
check("GATE the weekly grade mints both sweeps' recommendations into "
      "the ladder",
      "def mint_gatefloor_candidates" in _tbw_g
      and _tbw_g.count("mint_gatefloor_candidates(") >= 2
      and "worth-ladder mint" in _tbw_g)

# - 14. THE CROSS-CHANNEL LINE (user ask, Oct 6): the discipline plate's
# gate-hindsight x ladder-verdict line for the SAME day, the two
# channels' disagreement visible. Red-first: these failed against the
# missing line first.
import giveback_tier_hindsight as _gbh14
from datetime import datetime as _dt14
from zoneinfo import ZoneInfo as _zi14
_MST14 = _zi14("America/Denver")
def _ms14(s):
    return int(_dt14.fromisoformat(s).timestamp() * 1000)
def _tier14(rid, status, at, day, reason="x"):
    return {"rotation_id": rid, "status": status, "applied_at": at,
            "day": day, "before": 1.8, "after": 1.8, "reason": reason}
def _hb14(at, left):
    return {"ex_ms": _ms14(at), "applied": True, "realized": -5.0,
            "left": left, "verdict": "stayed too long - the peak held"}
def _ok14(at):
    return {"ex_ms": _ms14(at), "applied": True, "realized": 3.0,
            "left": 0.0, "verdict": "clean exit"}
_h_proven = [{"date": "2026-10-02", "rot": "00-04",
              "realized_pnl": 55.0, "total_left": 0.0},
             {"date": "2026-10-02", "rot": "04-08",
              "realized_pnl": -12.0, "total_left": 88.0}]
_h_unproven = [{"date": "2026-10-03", "rot": "00-04",
                "realized_pnl": 0.0, "total_left": 210.0},
               {"date": "2026-10-03", "rot": "04-08",
                "realized_pnl": -12.0, "total_left": 88.0}]
_h_one = [{"date": "2026-10-04", "rot": "00-04",
           "realized_pnl": 40.0, "total_left": 0.0}]
_l_dis = _gbh14.day_line(
    [_tier14("giveback-2026-10-02-tier1", "refused",
             "2026-10-02T14:00:00-06:00", "2026-10-02",
             "daily cap spent")],
    [_hb14("2026-10-02T15:00:00-06:00", 400.0),
     _ok14("2026-10-02T16:00:00-06:00")], {}, _h_proven)
check("XCH a refused tier that PAID FOR it on a day the ladder PROVED -> DISAGREE",
      "DISAGREE" in _l_dis and "paid for" in _l_dis
      and "1/2 proven" in _l_dis and "handed back" in _l_dis, _l_dis)
_l_rm = _gbh14.day_line(
    [_tier14("giveback-2026-10-03-tier1", "applied",
             "2026-10-03T14:00:00-06:00", "2026-10-03")],
    [_hb14("2026-10-03T15:00:00-06:00", 400.0)], {}, _h_unproven)
check("XCH a right-moment re-arm on a day the ladder could NOT prove -> DISAGREE",
      "DISAGREE" in _l_rm and "right moment" in _l_rm
      and "0/2 proven" in _l_rm, _l_rm)
_l_ag = _gbh14.day_line(
    [_tier14("giveback-2026-10-04-tier1", "applied",
             "2026-10-04T14:00:00-06:00", "2026-10-04")],
    [_hb14("2026-10-04T15:00:00-06:00", 400.0)], {}, _h_one)
check("XCH a right-moment re-arm on a PROVEN day -> agree, no flag",
      "DISAGREE" not in _l_ag and "agree" in _l_ag, _l_ag)
_l_nu = _gbh14.day_line(
    [_tier14("giveback-2026-10-05-tier1", "refused",
             "2026-10-05T14:00:00-06:00", "2026-10-05")],
    [_ok14("2026-10-05T15:00:00-06:00")], {}, _h_one)
check("XCH a mixed grade shows both reads with NO flag either way",
      "mixed" in _l_nu and "DISAGREE" not in _l_nu
      and "agree" not in _l_nu, _l_nu)
_l_nr = _gbh14.day_line(
    [_tier14("giveback-2026-10-01-tier1", "refused",
             "2026-10-01T14:00:00-06:00", "2026-10-01",
             "daily cap spent")],
    [_hb14("2026-10-01T15:00:00-06:00", 400.0)], {}, [])
check("XCH a day with no ladder rotations reads 'no rotations on record' - no fake flag",
      "no rotations on record" in _l_nr and "DISAGREE" not in _l_nr
      and "paid for" in _l_nr, _l_nr)

_l_open = _gbh14.day_line(
    [_tier14("giveback-2026-10-02-tier1", "applied",
             "2026-10-02T14:00:00-06:00", "2026-10-02")],
    [_hb14("2026-10-02T15:00:00-06:00", 400.0)], {}, _h_proven,
    now=_dt14(2026, 10, 2, 20, 0, tzinfo=_MST14))
check("XCH a tier whose window is still live reads 'unproven' - never a flag",
      "unproven" in _l_open and "DISAGREE" not in _l_open
      and "agree" not in _l_open, _l_open)
_l_sil = _gbh14.day_line(
    [_tier14("giveback-2026-09-29-tier1", "applied",
             "2026-09-29T14:00:00-06:00", "2026-09-29")], [], {},
    _h_proven)
check("XCH silence (no banked trade followed) reads without a flag",
      "silence" in _l_sil and "DISAGREE" not in _l_sil
      and "agree" not in _l_sil, _l_sil)
check("XCH no spent tiers -> the line disappears (the plate stays byte-stable)",
      _gbh14.day_line([], [], {}, _h_proven) == ""
      and _gbh14.day_line(None, [], {}, _h_proven) == "")
_gbh14_src = (BASE / "giveback_tier_hindsight.py").read_text(encoding="utf-8")
check("XCH the cross-channel core lives in the hindsight module (import-safe, pure)",
      "def day_line" in _gbh14_src
      and "compute_verdict" in _gbh14_src)
check("XCH the plate mounts the line beneath the ladder's evidence lines",
      "def _gate_ladder_line" in _app_g
      and "_gl_html = _gate_ladder_line()" in _app_g
      and _app_g.count("{_gl_html}") == 1
      and _app_g.find("{_gl_html}") > _app_g.find("{_wl_html}")
      and "def day_line" in _gbh14_src)

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
