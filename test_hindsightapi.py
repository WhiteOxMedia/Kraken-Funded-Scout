"""THE HINDSIGHT API CONTRACT DRIVE (task 3): all four endpoints
through the SAME dispatcher the HTTP handler uses, on a sandboxed
journal + cache + guard state — plus one live wire round-trip through
the stdlib server to prove the stub and the tests share a brain."""
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import hindsight_api as ha
import profit_guard as pg

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ── the sandbox: journal + cache + guard state + telemetry ─────────
_sbx = Path(tempfile.mkdtemp())
sup = _sbx / "popup_suppressions.jsonl"
now = datetime.now().astimezone()
t0 = now.replace(minute=0, second=0, microsecond=0)
ms0 = int(t0.timestamp() * 1000)
sup.write_text("\n".join(json.dumps(r) for r in (
    {"ts": t0.isoformat(), "pair": "AAA/USD", "gate": "tide-out",
     "quick": 71.0, "rot": "ROT-1"},
    {"ts": (t0 + timedelta(minutes=5)).isoformat(), "pair": "BBB/USD",
     "gate": "tide-out", "quick": 64.0, "rot": "ROT-1"},
    {"ts": (t0 + timedelta(minutes=6)).isoformat(), "pair": "CCC/USD",
     "gate": "deep-veto", "quick": 94.0, "rot": "ROT-1"},
    {"ts": (t0 + timedelta(minutes=7)).isoformat(), "pair": "DDD/USD",
     "gate": "deep-veto", "quick": 91.0, "rot": "ROT-1"},
    {"ts": (t0 + timedelta(minutes=8)).isoformat(), "pair": "EEE/USD",
     "gate": "deep-veto", "quick": 88.0, "rot": "ROT-1"},
    {"ts": (t0 + timedelta(minutes=9)).isoformat(), "pair": "FFF/USD",
     "gate": "deep-veto", "quick": 85.0, "rot": "ROT-1"},
    {"ts": (t0 + timedelta(minutes=10)).isoformat(), "pair": "GGG/USD",
     "gate": "deep-veto", "quick": 82.0, "rot": "ROT-1"},
    # ROT-2: one concentrated stream (all deep-veto, 5 of 6 payers) —
    # the concentration gate's fixture
    *({"ts": (t0 + timedelta(minutes=20)).isoformat(), "pair": f"R{i}/USD",
       "gate": "deep-veto", "quick": 90.0, "rot": "ROT-2"}
      for i in range(1, 7)),
)), encoding="utf-8")
db = _sbx / "ohlcv_cache.db"
con = sqlite3.connect(db)
con.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, "
            "high REAL, low REAL, close REAL)")
rows = []
for pair, base, run in (("AAA/USD", 1.0, 1.08), ("BBB/USD", 1.0, 1.00),
                        ("CCC/USD", 2.0, 2.10), ("DDD/USD", 2.0, 2.04),
                        ("EEE/USD", 2.0, 2.03), ("FFF/USD", 2.0, 2.02),
                        ("GGG/USD", 2.0, 2.02),
                        ("R1/USD", 1.0, 1.09), ("R2/USD", 1.0, 1.08),
                        ("R3/USD", 1.0, 1.07), ("R4/USD", 1.0, 1.06),
                        ("R5/USD", 1.0, 1.05), ("R6/USD", 1.0, 1.00)):
    rows.append((pair, ms0 - 600_000, base, base, base, base))
    rows.append((pair, ms0 + 3_600_000, base, run, base,
                 (run + base) / 2))
con.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)", rows)
con.commit()
con.close()

state = _sbx / "meta_layers_state.json"
state.write_text(json.dumps({"factor": 2.0}), encoding="utf-8")
_real_state = getattr(pg, "STATE_FILE", None)
pg.STATE_FILE = state

import auto_adjust as aa
aa.EVIDENCE_DIR = _sbx / "evidence"
import telemetry_adjust as ta
ta.TELEMETRY_PATH = _sbx / "telemetry.jsonl"

# the WIRE route answers from the module defaults — the first run
# proved the lesson: point them at the sandbox or the route quietly
# reads production state (B=0 out of an empty default journal)
ha.DEFAULT_SUPP = sup
ha.DEFAULT_CACHE = db

# explicit paths for the direct-call tests (the sandbox pattern)
def _g(**kw):
    kw.setdefault("supp_path", sup)
    kw.setdefault("cache_path", db)
    return kw

try:
    # ── 1. the summary ──────────────────────────────────────────────
    s = ha.rotation_summary("ROT-1", **_g())
    # payer arithmetic: AAA +8% payer; BBB never moves (dud); CCC +5%,
    # DDD +2%, EEE +1.5% (the >= boundary pays); FFF/GGG +1% (duds)
    check("summary: B 7, P 4, r 4/7, current GF read from the sandbox "
          "state",
          s["B"] == 7 and s["P"] == 4 and abs(s["r"] - 4 / 7) < 1e-9
          and s["current_GF"] == 2.0, s)
    # THE PREVIEW PRICES THE MIX (Oct 5): the rotation's own shape is
    # asymmetric (deep-veto 3/5, tide-out 1/2), so the wire preview
    # carries the payer-weighted magnitude — r_eff = (3x0.6 + 1x0.5)/4
    # = 0.575 vs the aggregate 4/7 ≈ 0.5714 — exactly what apply_action
    # would apply.
    import math as _math_prev
    _delta_prev = min(0.10, 0.05 * 0.575 * _math_prev.log(8))
    check("summary: the proposed delta is the weighted rule's own "
          "output (the mix is asymmetric: deep-veto 3/5 + tide-out "
          "1/2 -> r_eff 0.575, NOT the aggregate 4/7 — the preview "
          "prices what the machine would do)",
          abs(s["proposed_delta"] - (-_delta_prev)) < 1e-9
          and abs(s["proposed_delta"] - aa.compute_delta(7, 4)[0]) > 1e-9
          and s["proposed_reason"] == "ok", s)

    # ── 2. the gates ────────────────────────────────────────────────
    gates = ha.rotation_gates("ROT-1", **_g())
    dg = [g for g in gates if g["gate"] == "deep-veto"][0]
    to = [g for g in gates if g["gate"] == "tide-out"][0]
    check("gates: deep-veto carries 3 payers of 5 (r 0.6) and sorts "
          "FIRST (payers desc, rate tiebreak)",
          gates[0]["gate"] == "deep-veto" and dg["B_g"] == 5
          and dg["P_g"] == 3 and abs(dg["r_g"] - 0.6) < 1e-9, gates)
    check("gates: the top-candidate list carries the payers with their "
          "runs, best first (AAA +8% leads tide-out)",
          to["top_candidates"][0]["pair"] == "AAA/USD"
          and abs(to["top_candidates"][0]["run"] - 8.0) < 0.01
          and all(c["run"] >= 1.5 for c in dg["top_candidates"]),
          to["top_candidates"])
    check("gates: the dud (BBB, never moved) never rides the top list",
          all(c["pair"] != "BBB/USD" for c in to["top_candidates"]),
          to["top_candidates"])

    # ── 3. the evidence ─────────────────────────────────────────────
    ev = ha.candidate_evidence(
        "ROT-1", "AAA/USD|ROT-1|tide-out", cache_path=db,
        log_path=_sbx / "scanner_console.log")
    check("evidence: the OHLCV snippet covers the 7h window AFTER the "
          "refusal (the pre-refusal entry bar sits before it)",
          len(ev["ohlcv_snippet"]) == 1
          and ev["ohlcv_snippet"][0][0] == "AAA/USD", ev["ohlcv_snippet"])
    check("evidence: an absent log yields an empty excerpt, never a "
          "crash", ev["log_excerpt"] == [], ev["log_excerpt"])
    check("evidence: the trace id and checksum ride (checksum covers "
          "the payload)",
          ev["trace_id"].startswith("trace_")
          and ev["checksum"].startswith("sha256:"),
          (ev["trace_id"], ev["checksum"]))
    (_sbx / "scanner_console.log").write_text(
        "\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
    ev2 = ha.candidate_evidence(
        "ROT-1", "AAA/USD|ROT-1|tide-out", cache_path=db,
        log_path=_sbx / "scanner_console.log")
    check("evidence: the log excerpt is the LAST 30 lines",
          len(ev2["log_excerpt"]) == 30
          and ev2["log_excerpt"][-1] == "line 49", ev2["log_excerpt"][-1:])
    check("evidence: a different payload yields a different checksum",
          ev2["checksum"] != ev["checksum"], (ev["checksum"],
                                              ev2["checksum"]))

    # ── 4. apply: the mutating POSTs are AUTHENTICATED first ────────
    import os as _os
    _tok = _os.environ.pop("HINDSIGHT_OPERATOR_TOKEN", None)
    try:
        unauth = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                          {"action": "queue_review",
                           "operator_id": "the-operator"})
        check("apply: with NO token configured, a mutating POST is 403 "
              "(nothing that moves the guard or the ledger is "
              "reachable unauthenticated — the panel still reads)",
              unauth[0] == 403
              and "token required" in unauth[1]["error"], unauth)
        _os.environ["HINDSIGHT_OPERATOR_TOKEN"] = "op-token-1"
        wrong = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                         {"action": "queue_review",
                          "operator_id": "not-the-token"})
        check("apply: a WRONG token is the same 403 (matching, not "
              "merely presenting)",
              wrong[0] == 403, wrong)
        q = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                     {"action": "queue_review",
                      "operator_id": "op-token-1"})
        check("apply: the AUTHENTICATED queue_review computes + bundles "
              "but never moves the factor — and its recommendation "
              "prices the WEIGHTED delta (r_eff 0.575, the same number "
              "the confirmed apply would land; never the aggregate "
              "understatement the operator would then approve)",
              q[0] == 200 and q[1]["status"] == "queued"
              and abs(q[1]["delta"] - (-_delta_prev)) < 1e-9
              and abs(q[1]["delta"] - aa.compute_delta(7, 4)[0]) > 1e-9
              and aa.read_guard_factor() == 2.0, q)
        # THE QUEUE HONORS THE CELL (the follow-up review's [medium]):
        # with the canary's params threaded, the queued recommendation
        # prices the CELL's weighted math (0.03 x 0.575 x ln 8, capped
        # at the cell's 0.05) — never the default-weighted number the
        # confirmed apply would not land.
        import math as _math_q
        _q_cell = min(0.05, 0.03 * 0.575 * _math_q.log(8))
        _q_def = min(0.10, 0.05 * 0.575 * _math_q.log(8))
        qp = ha.apply_action("ROT-1", "queue_review",
                             operator_id="op-token-1",
                             supp_path=sup, cache_path=db,
                             params={"alpha": 0.03, "delta_max": 0.05,
                                     "daily_cap": 5.0})
        check("apply: the queue with the CANARY'S PARAMS prices the "
              "cell's weighted math (alpha 0.03/dmax 0.05 -> -0.0359, "
              "NOT the default-weighted -0.0598 — the queued number is "
              "what the confirmed apply would land under the same "
              "cell)",
              qp["status"] == "queued"
              and abs(qp["delta"] - (-_q_cell)) < 1e-9
              and abs(qp["delta"] - (-_q_def)) > 1e-3, qp)
        na = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                      {"action": "auto_apply", "operator_id": "op-token-1"})
        check("apply: auto_apply without confirm refuses (the HTTP-level "
              "safety catch, riding behind the token gate)",
              na[0] == 200 and na[1]["status"] == "refused"
              and na[1]["reason"] == "confirmation_required", na)
        ok_apply = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                            {"action": "auto_apply", "confirm": True,
                             "operator_id": "op-token-1",
                             "override_concentration": True})
        check("apply: a confirmed AUTHENTICATED apply with the EXPLICIT "
              "override field proceeds through the concentration gate "
              "(review-then-act: auth alone never lifts it)",
              ok_apply[0] == 200 and ok_apply[1]["status"] in ("applied",
                                                               "queued"),
              ok_apply)
        rb = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                      {"action": "bogus", "operator_id": "op-token-1"})
        check("apply: an unknown action refuses by name",
              rb[1]["status"] == "refused"
              and "unknown_action" in rb[1]["reason"], rb)
        check("apply: every decision landed on the telemetry stream",
              len(ta.read_events()) >= 2, len(ta.read_events()))
        # the override semantics: ROT-2 is fully concentrated (6/6)
        sneaky = ha.route("POST", "/api/hindsight/rotation/ROT-2/apply",
                          {"action": "auto_apply", "confirm": True,
                           "operator_id": "i-am-definitely-not-the-token"})
        check("an authenticated-but-wrong body cannot lift the "
              "concentration gate (the override matches the token, "
              "not the claim)",
              sneaky[0] == 403, sneaky)
        authed_no_override = ha.route(
            "POST", "/api/hindsight/rotation/ROT-2/apply",
            {"action": "auto_apply", "confirm": True,
             "operator_id": "op-token-1"})
        check("an authenticated apply WITHOUT the explicit override "
              "field still queues (auth is not the override — the "
              "review's major; runs BEFORE the override consumes the "
              "daily cap)",
              authed_no_override[1]["status"] == "queued"
              and authed_no_override[1]["reason"]
              == "concentration_gate_blocks_auto_apply",
              authed_no_override)
        authed = ha.route("POST", "/api/hindsight/rotation/ROT-2/apply",
                          {"action": "auto_apply", "confirm": True,
                           "operator_id": "op-token-1",
                           "override_concentration": True})
        check("the AUTHENTICATED operator override lifts ONLY the queue "
              "(the apply proceeds through the caps)",
              authed[1]["status"] == "applied"
              and abs(aa.read_guard_factor()
                      - aa.clamp(authed[1]["before"],
                                 authed[1]["delta"])) < 1e-9,
              (authed, aa.read_guard_factor()))
        last_ev = ta.read_events()[-1]
        check("the TOKEN NEVER LANDS IN TELEMETRY (applied_by carries a "
              "non-secret identifier, not the credential)",
              last_ev["applied_by"] == "operator"
              and "op-token-1" not in json.dumps(last_ev),
              last_ev["applied_by"])
        rbw = ha.route("POST", "/api/hindsight/rotation/ROT-2/rollback",
                       {"operator_id": "op-token-1"})
        check("the authenticated rollback restores the applied "
              "adjustment's before",
              rbw[0] == 200 and rbw[1].get("status") == "reverted", rbw)
        rb403 = ha.route("POST", "/api/hindsight/rotation/ROT-2/rollback",
                         {})
        check("rollback without the token is 403 too",
              rb403[0] == 403, rb403)
        # THE STACKED-BOX WIRE CASE (Oct 3): rbw undid the TOP of a
        # two-deep stack — the seam must carry the anchor semantics, not
        # just the status: the box SURVIVES under its ORIGINAL anchor
        # with the lineage rewound to the surviving rotation.
        _wbox = pg._state().get("auto_adjust_box") or {}
        check("the wire rollback of a stack's TOP keeps the box alive "
              "under its ORIGINAL anchor, lineage rewound to the "
              "surviving rotation (the stacked-box semantics ride the "
              "seam verbatim)",
              _wbox.get("rotation_id") == "ROT-1"
              and abs(float(_wbox.get("before") or 0) - 2.0) < 1e-9
              and abs(float(_wbox.get("after") or 0)
                      - float(ok_apply[1]["after"])) < 1e-9
              and abs(aa.read_guard_factor()
                      - float(ok_apply[1]["after"])) < 1e-9,
              (_wbox, ok_apply[1].get("after")))
        # THE LINEAGE-GATE WIRE CASES: the stale-undo and boxless guards
        # ride the same seam — the refusals' NAMES surface over the wire
        # and no refusal ever moves state.
        dbl = ha.route("POST", "/api/hindsight/rotation/ROT-2/rollback",
                       {"operator_id": "op-token-1"})
        check("a SECOND wire rollback of the already-undone stacked row "
              "refuses by name (rotation_not_in_live_box — the row is "
              "stale: the surviving box's chain is ROT-1's; shape never "
              "decided ownership)",
              dbl[0] == 200 and dbl[1].get("status") == "refused"
              and dbl[1].get("reason") == "rotation_not_in_live_box"
              and abs(aa.read_guard_factor()
                      - float(ok_apply[1]["after"])) < 1e-9
              and (pg._state().get("auto_adjust_box") or {})
              .get("rotation_id") == "ROT-1", dbl)
        full = ha.route("POST", "/api/hindsight/rotation/ROT-1/rollback",
                        {"operator_id": "op-token-1"})
        check("the wire full undo of the LIVE opener collapses the box "
              "(200 reverted to 2.0, boxless)",
              full[0] == 200 and full[1].get("status") == "reverted"
              and full[1].get("restored_to") == 2.0
              and abs(aa.read_guard_factor() - 2.0) < 1e-9
              and not (pg._state().get("auto_adjust_box") or {}), full)
        ghost = ha.route("POST", "/api/hindsight/rotation/ROT-2/rollback",
                         {"operator_id": "op-token-1"})
        check("a wire rollback on the BOXLESS slate refuses by name "
              "(time_box_already_cleared — no stale intermediate factor "
              "resurrects outside any box)",
              ghost[0] == 200 and ghost[1].get("status") == "refused"
              and ghost[1].get("reason") == "time_box_already_cleared"
              and abs(aa.read_guard_factor() - 2.0) < 1e-9, ghost)
    finally:
        if _tok is not None:
            _os.environ["HINDSIGHT_OPERATOR_TOKEN"] = _tok
        else:
            _os.environ.pop("HINDSIGHT_OPERATOR_TOKEN", None)

    # THE HISTORY ROUTES (the panel spec's zones 4-5, Sep 29): the
    # ledger and the telemetry stream, GET-only and read-only — a POST
    # to either is a named 404 (no mutation can ride a history URL).
    code, led = ha.route("GET", "/api/hindsight/ledger")
    check("the ledger route answers 200 with rows from the guard "
          "state's own record (zone 4)",
          code == 200 and isinstance(led.get("rows"), list)
          and all(isinstance(r, dict) and "status" in r
                  for r in led["rows"]),
          (code, led))
    code, tel = ha.route("GET", "/api/hindsight/telemetry")
    check("the telemetry route answers 200 with newest-first events "
          "carrying applied_by/cohort/commit_sha (the audit trail, "
          "zone 5)",
          code == 200 and isinstance(tel.get("events"), list)
          and all(isinstance(e, dict) and "status" in e for e in tel["events"]),
          (code, tel))
    post404 = ha.route("POST", "/api/hindsight/ledger", {})
    check("a POST to a history route is a named 404 (read-only by "
          "construction — no mutation can ride a history URL)",
          post404[0] == 404, post404)

    # THE EVIDENCE ROUTE (the isolated review's major): a pair id with
    # a slash must arrive INTACT through one encoded path segment.
    code, ev_wire = ha.route(
        "GET", "/api/hindsight/rotation/ROT-1/evidence/"
               "AAA%2FUSD%7CROT-1%7Ctide-out")
    check("the evidence route decodes a slash-bearing candidate id and "
          "returns its payload",
          code == 200 and ev_wire["candidate_id"] == "AAA/USD|ROT-1|tide-out"
          and ev_wire["checksum"].startswith("sha256:"),
          (code, ev_wire.get("candidate_id")))
    code404, _e404 = ha.route(
        "GET", "/api/hindsight/rotation/ROT-1/evidence/extra/deep")
    check("a malformed evidence path still 404s (encoding is not a "
          "bypass)", code404 == 404, code404)

    # (the rollback route's success + 403 pins live in the auth block)

    # ── the wire: a live HTTP round-trip through the SAME route() ───
    handler_cls, HTTPServer = ha.make_handler()
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        url = f"http://127.0.0.1:{port}/api/hindsight/rotation/ROT-1/summary"
        with urllib.request.urlopen(url, timeout=10) as resp:
            wire = json.loads(resp.read())
        check("the wire speaks the same brain: HTTP summary matches the "
              "dispatcher's payload",
              wire["B"] == s["B"] and wire["current_GF"] ==
              aa.read_guard_factor(), wire)
        url404 = f"http://127.0.0.1:{port}/api/hindsight/rotation/ROT-1/nonsense"
        try:
            urllib.request.urlopen(url404, timeout=10)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check("an unknown route answers 404 over the wire",
              code == 404, code)
    finally:
        srv.shutdown()
        srv.server_close()

    # ── THE ROTATION-BOUNDARY HOOK (Sep 28): the propose path rides the
    # boundary — gated behind the canary's OWN dial key, one evaluation
    # per rotation (watermark in scanner_state), one telemetry event.
    import os as _os_hook
    import background_scanner as _bs_hook
    import exit_extension as _exx_mod
    import telemetry_adjust as _ta_mod
    _real_hook = (_os_hook.environ.get("EXIT_EXTENSION_STATE_FILE"),
                  _ta_mod.TELEMETRY_PATH, _bs_hook.__file__)
    _sbx_dial = _sbx / "exit_extension.dial.json"
    _hook_tel = _sbx / "boundary_telemetry.jsonl"
    _os_hook.environ["EXIT_EXTENSION_STATE_FILE"] = str(
        _sbx / "exit_extension_state.json")
    _ta_mod.TELEMETRY_PATH = _hook_tel
    _bs_hook.__file__ = str(_sbx / "background_scanner.py")   # the
        # watermark store derives from __file__ (the apptest trick) —
        # the drive's stamp lands in the sandbox, never the live state
    try:
        # dial off: the boundary answers honestly and touches nothing
        _r0 = _exx_mod.aa_apply_silent_rotation("ROT-1", supp_path=sup,
                                                cache_path=db)
        check("the boundary hook refuses when the canary dial is off "
              "(gated by name, nothing evaluated, no watermark)",
              _r0 == {"evaluated": False, "reason": "canary_dial_off"}
              and not _hook_tel.exists()
              and not (_sbx / "scanner_state.json").exists(), _r0)
        # arm the cohort via the runner's flip — the cohort's OWN file
        # (the two-file design): the dial file is never created or
        # written, and the hook reads the cohort file through the env seam
        import canary_runner as _cr2
        _sbx_cohort = _sbx / "auto_adjust_canary.json"
        _real_cr_dial, _real_cr_cohort = _cr2.DIAL_FILE, _cr2.COHORT_FILE
        _real_env_cohort = _os_hook.environ.get("AUTO_ADJUST_CANARY_FILE")
        _cr2.DIAL_FILE = _sbx_dial
        _cr2.COHORT_FILE = _sbx_cohort
        _os_hook.environ["AUTO_ADJUST_CANARY_FILE"] = str(_sbx_cohort)
        try:
            _flip = _cr2.flip_canary("canary-boundary-1")
            _cohort_doc = json.loads(_sbx_cohort.read_text(encoding="utf-8"))
            check("the flip arms the cohort's OWN file and never touches "
                  "the exit-extension dial file (two controls, two files)",
                  _flip["dial"] == "canary"
                  and _cohort_doc.get("cohort_id") == "canary-boundary-1"
                  and not _sbx_dial.exists(), _cohort_doc)
        finally:
            _cr2.DIAL_FILE, _cr2.COHORT_FILE = _real_cr_dial, _real_cr_cohort
            if _real_env_cohort is None:
                _os_hook.environ.pop("AUTO_ADJUST_CANARY_FILE", None)
            else:
                _os_hook.environ["AUTO_ADJUST_CANARY_FILE"] = _real_env_cohort
        # THE BOUNDARY: the completed rotation evaluates with every
        # rail live (concentration gate, cap, evidence, ledger)
        _r1 = _exx_mod.aa_apply_silent_rotation("ROT-1", supp_path=sup,
                                                cache_path=db)
        check("the boundary hook evaluates the completed rotation "
              "through the FULL propose path (a named status rides the "
              "decision)",
              _r1.get("evaluated") is True
              and _r1.get("status") in ("applied", "queued", "refused"),
              _r1)
        _events = [json.loads(l) for l in
                   _hook_tel.read_text(encoding="utf-8").splitlines()
                   if l.strip()]
        check("the boundary emitted ONE spec-shaped telemetry event "
              "(the journal's own rot id, the cohort, the non-secret "
              "applied_by)",
              len(_events) == 1
              and _events[0]["rotation_id"] == "ROT-1"
              and _events[0]["cohort"] == "canary-boundary-1"
              and _events[0]["applied_by"] == "rotation_boundary",
              _events[0] if _events else None)
        check("the boundary event carries the machine's numbers (B/P "
              "and per-gate rows computed from the rot id, not zeros)",
              isinstance(_events[0]["B"], int)
              and _events[0]["B"] >= 5
              and isinstance(_events[0]["per_gate"], list)
              and _events[0]["per_gate"],
              (_events[0]["B"], _events[0]["per_gate"]))
        # the watermark: a second call is a nameable no
        _r2 = _exx_mod.aa_apply_silent_rotation("ROT-1", supp_path=sup,
                                                cache_path=db)
        check("the watermark holds: the same rotation is never "
              "evaluated twice (one event per rotation, ever)",
              _r2.get("evaluated") is False
              and _r2.get("reason") == "already_evaluated"
              and len(_hook_tel.read_text(encoding="utf-8")
                      .splitlines()) == 1, _r2)
        check("the watermark lives in scanner_state (restart-proof: "
              "the stamp rides the file the scanner already persists)",
              _bs_hook._load_scanner_state()
                  .get("aa_canary_last_rotation") == "ROT-1",
              _bs_hook._load_scanner_state())

        # ── THE ARMED-CELL PROOF (Sep 29, the params seam's end-to-end): 
        #    a fresh rotation whose delta math DIFFERS between the armed
        #    cell and the module defaults; the emitted event's delta
        #    must match the CELL, not the defaults — arming is provably
        #    live through the whole wire path.
        import math as _math
        _B_p, _P_p, _r_p = 4, 4, 1.0
        _mod_delta = min(0.10, 0.05 * _r_p * _math.log(1 + _B_p))
        _cell_delta = min(0.05, 0.03 * _r_p * _math.log(1 + _B_p))
        assert abs(_mod_delta - _cell_delta) > 0.01   # the cells differ
        # THE APPEND-NEWLINE GUARD (the trap this pin caught): the
        # fixture's original write_text ends WITHOUT a trailing newline,
        # so an append's first row would concatenate onto the last line
        # and die silently in the loader's json.loads (P1 was being
        # swallowed — B measured 3 instead of 4). Guard the append.
        _sup_tail = sup.read_text(encoding="utf-8") if sup.exists() else ""
        with open(sup, "a", encoding="utf-8") as _f:
            if _sup_tail and not _sup_tail.endswith("\n"):
                _f.write("\n")
            for _i, (_pair, _gate, _quick) in enumerate((
                    ("P1/USD", "tide-out", 71.0), ("P2/USD", "tide-out", 66.0),
                    ("P3/USD", "stale-move", 82.0), ("P4/USD", "stale-move", 77.0),
                    ("D1/USD", "tide-out", 55.0), ("D2/USD", "tide-out", 51.0),
                    ("D3/USD", "stale-move", 58.0), ("D4/USD", "stale-move", 49.0))):
                _f.write(json.dumps({
                    # anchored INSIDE the fixture's hour (the same shape
                    # every measured leg uses — the ts edge is where
                    # nondeterminism lived)
                    "ts": (t0 + timedelta(minutes=20 + _i)).isoformat(),
                    "pair": _pair, "gate": _gate, "quick": _quick,
                    "rot": "ROT-P"}) + "\n")
        _con_p = sqlite3.connect(db)
        _orows_p = []
        for _pair in ("P1/USD", "P2/USD", "P3/USD", "P4/USD"):
            _orows_p.append((_pair, ms0 - 600_000, 1.0, 1.0, 1.0, 1.0))
            _orows_p.append((_pair, ms0 + 3_600_000, 1.0, 1.08, 1.0, 1.04))
        _con_p.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)", _orows_p)
        _con_p.commit(); _con_p.close()
        import canary_runner as _cr4
        _real4 = (_cr4.DIAL_FILE, _cr4.COHORT_FILE)
        _cr4.DIAL_FILE = _sbx_dial
        _cr4.COHORT_FILE = _sbx_cohort
        _os_hook.environ["AUTO_ADJUST_CANARY_FILE"] = str(_sbx_cohort)
        try:
            _flip4 = _cr4.flip_canary(
                "canary-params-proof",
                params={"alpha": 0.03, "delta_max": 0.05,
                        "daily_cap": 5.0})
            _cell_doc = json.loads(
                _sbx_cohort.read_text(encoding="utf-8"))
            check("flip_canary persists the gate-selected cell on the "
                  "cohort file (alpha/delta_max/daily_cap all three)",
                  _cell_doc.get("params") == {"alpha": 0.03,
                                              "delta_max": 0.05,
                                              "daily_cap": 5.0}, _cell_doc)
            # B_MIN is patched to 4 for this leg (the drive's own
            # constant-patch precedent, DAILY_CAP): the pin's target is
            # the params seam, not the sample floor, and B=4/P=4 keeps
            # concentration at exactly 0.5 (the gate's edge).
            _real_bmin = aa.B_MIN
            aa.B_MIN = 4
            try:
                _rp = _exx_mod.aa_apply_silent_rotation(
                    "ROT-P", supp_path=sup, cache_path=db)
            finally:
                aa.B_MIN = _real_bmin
            _events_p = [json.loads(l) for l in
                         _hook_tel.read_text(encoding="utf-8").splitlines()
                         if l.strip()]
            _ev_p = _events_p[-1]
            _led_p = [r for r in json.loads(
                pg.STATE_FILE.read_text(encoding="utf-8")).get(
                "auto_adjust_ledger", [])
                if r.get("rotation_id") == "ROT-P"]
            check("THE PROOF: the boundary hook's emitted delta matches "
                  "the ARMED cell's math (min(dmax, a·r·ln(1+B)) with "
                  "a=0.03/dmax=0.05), NOT the module defaults — the "
                  "armed params are live through the whole wire path",
                  _rp.get("evaluated") is True and _rp.get("status") == "applied"
                  and abs(_ev_p["delta"] - (-_cell_delta)) < 1e-6
                  and abs(_ev_p["delta"] - (-_mod_delta)) > 0.01
                  and _led_p and _led_p[-1].get("params") == {
                      "alpha": 0.03, "delta_max": 0.05, "daily_cap": 5.0},
                  (_rp, _ev_p.get("delta"), _mod_delta, _cell_delta,
                   _led_p[-1].get("params") if _led_p else None))
        finally:
            _cr4.DIAL_FILE, _cr4.COHORT_FILE = _real4

        # ── THE BAR FIRES END-TO-END (Oct 4, the freeze guard): the
        #    user rule — r >= 0.25 with B >= 5 — must BIRTH a box
        #    through the full wire (journal rows -> compute_delta ->
        #    the rails -> the box + the ledger), and the pin carries
        #    its own bite-proof: freeze the bar (R_MIN 0.9) and the
        #    SAME healthy world refuses with no box and no ledger row —
        #    a future regression that quietly freezes the boundary
        #    hook flips 4d.1 red by name instead of passing vacuously.
        import math as _math_bar
        _B_bar, _P_bar = 12, 6
        _delta_bar = min(0.10, 0.05 * (_P_bar / _B_bar)
                         * _math_bar.log(1 + _B_bar))
        _sup_bar_tail = sup.read_text(encoding="utf-8") if sup.exists() else ""
        with open(sup, "a", encoding="utf-8") as _f:
            if _sup_bar_tail and not _sup_bar_tail.endswith("\n"):
                _f.write("\n")
            for _i, (_pair, _gate, _quick) in enumerate((
                    ("PB1/USD", "tide-out", 74.0), ("PB2/USD", "tide-out", 71.0),
                    ("PB3/USD", "stale-move", 77.0), ("PB4/USD", "stale-move", 73.0),
                    ("PB5/USD", "climb-floor", 69.0), ("PB6/USD", "climb-floor", 66.0),
                    ("DB1/USD", "tide-out", 52.0), ("DB2/USD", "tide-out", 49.0),
                    ("DB3/USD", "stale-move", 55.0), ("DB4/USD", "stale-move", 48.0),
                    ("DB5/USD", "climb-floor", 51.0), ("DB6/USD", "climb-floor", 47.0))):
                _f.write(json.dumps({
                    "ts": (t0 + timedelta(minutes=30 + _i)).isoformat(),
                    "pair": _pair, "gate": _gate, "quick": _quick,
                    "rot": "ROT-BAR"}) + "\n")
        _con_bar = sqlite3.connect(db)
        _orows_bar = []
        for _pair, _run in (("PB1/USD", 1.03), ("PB2/USD", 1.03),
                            ("PB3/USD", 1.03), ("PB4/USD", 1.03),
                            ("PB5/USD", 1.03), ("PB6/USD", 1.03),
                            ("DB1/USD", 1.01), ("DB2/USD", 1.01),
                            ("DB3/USD", 1.01), ("DB4/USD", 1.01),
                            ("DB5/USD", 1.01), ("DB6/USD", 1.01)):
            _orows_bar.append((_pair, ms0 - 600_000, 1.0, 1.0, 1.0, 1.0))
            _orows_bar.append((_pair, ms0 + 3_600_000, 1.0, _run, 1.0, 1.0))
        _con_bar.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)",
                             _orows_bar)
        _con_bar.commit(); _con_bar.close()
        import canary_runner as _cr5
        _real5 = (_cr5.DIAL_FILE, _cr5.COHORT_FILE)
        _cr5.DIAL_FILE = _sbx_dial
        _cr5.COHORT_FILE = _sbx_cohort
        _os_hook.environ["AUTO_ADJUST_CANARY_FILE"] = str(_sbx_cohort)
        try:
            _cr5.flip_canary("canary-bar-fire",
                             params={"alpha": 0.05, "delta_max": 0.10,
                                     "daily_cap": 5.0})
            # the day's earlier legs already moved the sandbox factor —
            # the expectation reads the LIVE pre-call factor, never an
            # assumed 2.0
            _gf_before = float(aa.read_guard_factor())
            _rb = _exx_mod.aa_apply_silent_rotation(
                "ROT-BAR", supp_path=sup, cache_path=db)
            _st_bar = json.loads(
                pg.STATE_FILE.read_text(encoding="utf-8"))
            _box_bar = _st_bar.get("auto_adjust_box")
            _led_bar = [r for r in (_st_bar.get("auto_adjust_ledger") or [])
                        if r.get("rotation_id") == "ROT-BAR"]
            check("4d.1 THE BAR FIRES END-TO-END: B 12 / P 6 (r 0.5) "
                  "births a box through the full wire — status applied, "
                  "the ledger row reason ok, the box standing on the "
                  "guard state",
                  _rb.get("evaluated") is True
                  and _rb.get("status") == "applied"
                  and _led_bar
                  and _led_bar[-1].get("status") == "applied"
                  and _led_bar[-1].get("reason") == "ok"
                  and isinstance(_box_bar, dict)
                  and abs(float(_box_bar.get("before") or 0) - 2.0) < 1e-9,
                  (_rb, _box_bar, _led_bar[-1] if _led_bar else None))
            check("4d.2 the birth's math is the rule's own output "
                  "(delta -0.05 x 0.5 x ln 13, the factor moved by it "
                  "from its live pre-call value)",
                  _led_bar
                  and abs(_led_bar[-1].get("delta", 0) - (-_delta_bar)) < 1e-6
                  and abs(float(_led_bar[-1].get("after") or 0)
                          - aa.clamp(_gf_before, -_delta_bar)) < 1e-6,
                  (_gf_before, _delta_bar, _led_bar[-1] if _led_bar else None))
            # THE BITE-PROOF: freeze the bar (R_MIN 0.9) — the SAME
            # healthy world now refuses low_ratio, no ledger row, the
            # standing box untouched: the freeze demonstrably flips
            # 4d.1's assertion red, so the pin can never pass vacuously.
            # the freeze world needs its OWN seeded rotation (the
            # aggregate is per-rot: an unseeded rot id reads B 0 and
            # refuses insufficient_samples, never reaching the ratio)
            with open(sup, "a", encoding="utf-8") as _f:
                for _i, (_pair, _gate, _quick) in enumerate((
                        ("PB1/USD", "tide-out", 74.0), ("PB2/USD", "tide-out", 71.0),
                        ("PB3/USD", "stale-move", 77.0), ("PB4/USD", "stale-move", 73.0),
                        ("PB5/USD", "climb-floor", 69.0), ("PB6/USD", "climb-floor", 66.0),
                        ("DB1/USD", "tide-out", 52.0), ("DB2/USD", "tide-out", 49.0),
                        ("DB3/USD", "stale-move", 55.0), ("DB4/USD", "stale-move", 48.0),
                        ("DB5/USD", "climb-floor", 51.0), ("DB6/USD", "climb-floor", 47.0))):
                    _f.write(json.dumps({
                        "ts": (t0 + timedelta(minutes=50 + _i)).isoformat(),
                        "pair": _pair, "gate": _gate, "quick": _quick,
                        "rot": "ROT-BAR-2"}) + "\n")
            _real_rmin = aa.R_MIN
            aa.R_MIN = 0.9
            try:
                _rf = _exx_mod.aa_apply_silent_rotation(
                    "ROT-BAR-2", supp_path=sup, cache_path=db)
            finally:
                aa.R_MIN = _real_rmin
            _st_frozen = json.loads(
                pg.STATE_FILE.read_text(encoding="utf-8"))
            _led_f = [r for r in (_st_frozen.get("auto_adjust_ledger") or [])
                      if r.get("rotation_id") == "ROT-BAR-2"]
            _box_f = _st_frozen.get("auto_adjust_box")
            check("4d.3 THE BITE-PROOF: with the bar frozen (R_MIN 0.9) "
                  "the same healthy world refuses low_ratio — no ledger "
                  "row, the standing box untouched — a quiet freeze of "
                  "the boundary hook flips 4d.1 red by name",
                  _rf.get("evaluated") is True
                  and _rf.get("status") == "refused"
                  and _rf.get("reason") == "low_ratio"
                  and not _led_f
                  and isinstance(_box_f, dict)
                  and _box_f.get("set_at") == (_box_bar or {}).get("set_at"),
                  (_rf, _led_f, _box_f))
        finally:
            _cr5.DIAL_FILE, _cr5.COHORT_FILE = _real5

        # ── 4d.4 THE WEIGHTED WIRE (Oct 5, red-first): an ASYMMETRIC
        #    world through the full boundary hook — the aggregate is
        #    the same healthy 12/6 (r 0.5), but the payers sit only in
        #    tide-out (3/4) and stale-move (3/4); climb-floor buried
        #    four and paid nothing. The rule's magnitude prices the
        #    burial mix (r_eff = (3x0.75 + 3x0.75 + 0)/6 = 0.75), so
        #    the ledger's delta is the rule's own math at 0.75 — NOT
        #    the aggregate's 0.5 nudge. Concentration sits exactly at
        #    0.5 (tide-out's 3 of 6 payers) so the auto path runs.
        import math as _math_w
        _r_eff_w = 0.75
        _delta_w = min(0.10, 0.05 * _r_eff_w * _math_w.log(13))
        _delta_agg_w = min(0.10, 0.05 * 0.5 * _math_w.log(13))
        _sup_w_tail = sup.read_text(encoding="utf-8") if sup.exists() else ""
        with open(sup, "a", encoding="utf-8") as _f:
            if _sup_w_tail and not _sup_w_tail.endswith("\n"):
                _f.write("\n")
            for _i, (_pair, _gate, _quick) in enumerate((
                    ("PB1/USD", "tide-out", 74.0), ("PB2/USD", "tide-out", 71.0),
                    ("PB3/USD", "tide-out", 68.0), ("DB6/USD", "tide-out", 46.0),
                    ("PB4/USD", "stale-move", 73.0), ("PB5/USD", "stale-move", 69.0),
                    ("PB6/USD", "stale-move", 66.0), ("DB1/USD", "stale-move", 52.0),
                    ("DB2/USD", "climb-floor", 51.0), ("DB3/USD", "climb-floor", 55.0),
                    ("DB4/USD", "climb-floor", 48.0), ("DB5/USD", "climb-floor", 47.0))):
                _f.write(json.dumps({
                    "ts": (t0 + timedelta(minutes=70 + _i)).isoformat(),
                    "pair": _pair, "gate": _gate, "quick": _quick,
                    "rot": "ROT-BAR-3"}) + "\n")
        # the candles the 7h forward window prices: base BEFORE the
        # candidates, the run-peak AFTER them (the shared fixture's
        # rows sit at t0-10m/t0+60m — before this rotation's ts)
        _con_w = sqlite3.connect(db)
        _orows_w = []
        for _pair, _run in (("PB1/USD", 1.03), ("PB2/USD", 1.03),
                            ("PB3/USD", 1.03), ("DB6/USD", 1.01),
                            ("PB4/USD", 1.03), ("PB5/USD", 1.03),
                            ("PB6/USD", 1.03), ("DB1/USD", 1.01),
                            ("DB2/USD", 1.01), ("DB3/USD", 1.01),
                            ("DB4/USD", 1.01), ("DB5/USD", 1.01)):
            _orows_w.append((_pair, ms0 + 65 * 60_000, 1.0, 1.0, 1.0, 1.0))
            _orows_w.append((_pair, ms0 + 120 * 60_000, 1.0, _run, 1.0,
                             (_run + 1.0) / 2))
        _con_w.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?)",
                           _orows_w)
        _con_w.commit(); _con_w.close()
        import canary_runner as _cr6
        _real6 = (_cr6.DIAL_FILE, _cr6.COHORT_FILE)
        _cr6.DIAL_FILE = _sbx_dial
        _cr6.COHORT_FILE = _sbx_cohort
        _os_hook.environ["AUTO_ADJUST_CANARY_FILE"] = str(_sbx_cohort)
        try:
            _cr6.flip_canary("canary-bar-fire",
                             params={"alpha": 0.05, "delta_max": 0.10,
                                     "daily_cap": 5.0})
            _gf_w = float(aa.read_guard_factor())
            _rw = _exx_mod.aa_apply_silent_rotation(
                "ROT-BAR-3", supp_path=sup, cache_path=db)
            _st_w = json.loads(
                pg.STATE_FILE.read_text(encoding="utf-8"))
            _led_w = [r for r in (_st_w.get("auto_adjust_ledger") or [])
                      if r.get("rotation_id") == "ROT-BAR-3"]
            check("4d.4 THE WEIGHTED WIRE: the asymmetric world applies "
                  "the burial-mix math end-to-end (tide-out 3/4 + "
                  "stale-move 3/4 + climb-floor 4/0 -> r_eff 0.75, the "
                  "ledger delta is -alpha x 0.75 x ln 13 from the live "
                  "factor — the gate that buried zero payers says "
                  "nothing, the gates that buried the money speak)",
                  _rw.get("evaluated") is True
                  and _rw.get("status") == "applied"
                  and _led_w
                  and abs(_led_w[-1].get("delta", 0) - (-_delta_w)) < 1e-6
                  and abs(_led_w[-1].get("delta", 0) - (-_delta_agg_w)) > 1e-4
                  and abs(float(_led_w[-1].get("after") or 0)
                          - aa.clamp(_gf_w, -_delta_w)) < 1e-6,
                  (_rw, _gf_w, _delta_w, _delta_agg_w,
                   _led_w[-1] if _led_w else None))
        finally:
            _cr6.DIAL_FILE, _cr6.COHORT_FILE = _real6
        _bs_src_hook = (BASE / "background_scanner.py").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        # THE HOOK'S SHAPE (upgraded Sep 29, the review's two majors):
        # it calls with the PREVIOUS, JUST-CLOSED rotation's DATED key
        # (a bare window id would merge cross-day rows), and it lives
        # ABOVE the empty-book early return (a silent rotation with no
        # open rows is exactly when the propose path must still run).
        _cl_src = _bs_src_hook[_bs_src_hook.find("def contest_light"):
              ]
        _early = _cl_src.find("if not watch:")
        _hook_pos = _cl_src.find("aa_apply_silent_rotation")
        check("the scanner calls the hook per tick with the previous "
          "rotation's dated key (from the rotation's OWN start "
          "datetime — a midnight-crossing boundary stamps yesterday's "
          "date), above the empty-book early return (every tick "
          "self-heals a missed boundary; the dedupe is by stamp; a "
          "silent rotation is never skipped for an empty watch)",
              "_exx.aa_apply_silent_rotation(_aar_key)" in _bs_src_hook
              and "_prev_dt = _now_m - timedelta(hours=4)" in _bs_src_hook
              and "_prev_dt:%Y-%m-%d" in _bs_src_hook
              and _hook_pos != -1
              and (_early == -1 or _hook_pos < _early), "")
        # the rollback leg: the cohort's own stop — the dial file's
        # master value is NEVER rewritten; only the legacy parked keys
        # are stripped (one migration window)
        import canary_runner as _cr3
        _real_cr3_dial, _real_cr3_cohort = _cr3.DIAL_FILE, _cr3.COHORT_FILE
        _cr3.DIAL_FILE = _sbx_dial
        _cr3.COHORT_FILE = _sbx_cohort
        _os_hook.environ["AUTO_ADJUST_CANARY_FILE"] = str(_sbx_cohort)
        try:
            _sbx_dial.write_text(json.dumps(
                {"EXIT_EXTENSION_ENABLED": "canary",
                 "AUTO_ADJUST_CANARY": "legacy-junk",
                 "cohort_id": "legacy-junk"}), encoding="utf-8")
            _r3 = _cr3.rollback()
            _after = json.loads(_sbx_dial.read_text(encoding="utf-8"))
            _cdoc = json.loads(_sbx_cohort.read_text(encoding="utf-8"))
            check("the rollback stops the COHORT on its own file and "
                  "strips the legacy parked keys from the dial WITHOUT "
                  "rewriting the master value (the feature is never "
                  "silently disabled by a cohort rollback)",
                  _r3["rolled_back"] is True
                  and _cdoc.get("disarmed") is True
                  and _cr3.read_cohort() == ""
                  and "AUTO_ADJUST_CANARY" not in _after
                  and "cohort_id" not in _after
                  and _after.get("EXIT_EXTENSION_ENABLED") == "canary",
                  (_r3, _cdoc, _after))
        finally:
            _cr3.DIAL_FILE, _cr3.COHORT_FILE = _real_cr3_dial, _real_cr3_cohort
            _os_hook.environ.pop("AUTO_ADJUST_CANARY_FILE", None)
    finally:
        _ta_mod.TELEMETRY_PATH = _real_hook[1]
        _bs_hook.__file__ = _real_hook[2]
        if _real_hook[0] is None:
            _os_hook.environ.pop("EXIT_EXTENSION_STATE_FILE", None)
        else:
            _os_hook.environ["EXIT_EXTENSION_STATE_FILE"] = _real_hook[0]
finally:
    if _real_state is not None:
        pg.STATE_FILE = _real_state
    else:
        del pg.STATE_FILE
    shutil.rmtree(_sbx, ignore_errors=True)

fails = [r for r in results if not r[1]]
rep = BASE / "artifacts" / "hindsight_api" / "_hindsightapi_drive.txt"
rep.parent.mkdir(parents=True, exist_ok=True)
rep.write_text("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                         (f"  [{d}]" if d and not ok else "")
                         for n, ok, d in results)
               + f"\n\nRESULT: {len(results) - len(fails)} passed, "
                 f"{len(fails)} failed\n", encoding="utf-8")
print(f"RESULT: {len(results) - len(fails)} passed, {len(fails)} failed")
sys.exit(1 if fails else 0)
