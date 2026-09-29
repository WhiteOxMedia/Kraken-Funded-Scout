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
    check("summary: the proposed delta is the rule's own output "
          "(0.05 x 4/7 x ln 8, under the cap)",
          abs(s["proposed_delta"] - aa.compute_delta(7, 4)[0]) < 1e-12
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
              "but never moves the factor",
              q[0] == 200 and q[1]["status"] == "queued"
              and abs(q[1]["delta"] - aa.compute_delta(7, 4)[0]) < 1e-12
              and aa.read_guard_factor() == 2.0, q)
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
    finally:
        if _tok is not None:
            _os.environ["HINDSIGHT_OPERATOR_TOKEN"] = _tok
        else:
            _os.environ.pop("HINDSIGHT_OPERATOR_TOKEN", None)

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
        # arm the cohort via the runner's flip (the seam-moved dial)
        import canary_runner as _cr2
        _real_cr_dial = _cr2.DIAL_FILE
        _cr2.DIAL_FILE = _sbx_dial
        try:
            _flip = _cr2.flip_canary("canary-boundary-1")
            _dial_doc = json.loads(_sbx_dial.read_text(encoding="utf-8"))
            check("the flip sets the DEDICATED canary key (the feature "
                  "master EXIT_EXTENSION_ENABLED is never clobbered — "
                  "the reuse major stays closed)",
                  _flip["dial"] == "canary"
                  and _dial_doc.get("AUTO_ADJUST_CANARY")
                  == "canary-boundary-1"
                  and "EXIT_EXTENSION_ENABLED" not in _dial_doc, _dial_doc)
        finally:
            _cr2.DIAL_FILE = _real_cr_dial
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
        _bs_src_hook = (BASE / "background_scanner.py").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        check("the scanner calls the hook per tick (every tick self-heals "
          "a missed boundary — the dedupe is by stamp, not by cadence)",
              "_exx.aa_apply_silent_rotation(_wk)" in _bs_src_hook, "")
        # the rollback leg: legacy junk on the feature master is
        # de-contaminated to the documented OFF (the reuse major's tail)
        import canary_runner as _cr3
        _real_cr3_dial = _cr3.DIAL_FILE
        _cr3.DIAL_FILE = _sbx_dial
        try:
            _sbx_dial.write_text(json.dumps(
                {"EXIT_EXTENSION_ENABLED": "canary"}), encoding="utf-8")
            _r3 = _cr3.rollback()
            _after = json.loads(_sbx_dial.read_text(encoding="utf-8"))
            check("the rollback clears the cohort key and stops the "
                  "machine (a legacy 'canary' junk on the master is "
                  "de-contaminated to the documented OFF)",
                  _r3["rolled_back"] is True
                  and _after.get("AUTO_ADJUST_CANARY") is None
                  and _after.get("EXIT_EXTENSION_ENABLED") == "0",
                  _after)
        finally:
            _cr3.DIAL_FILE = _real_cr3_dial
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
