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
)), encoding="utf-8")
db = _sbx / "ohlcv_cache.db"
con = sqlite3.connect(db)
con.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, "
            "high REAL, low REAL, close REAL)")
rows = []
for pair, base, run in (("AAA/USD", 1.0, 1.08), ("BBB/USD", 1.0, 1.00),
                        ("CCC/USD", 2.0, 2.10), ("DDD/USD", 2.0, 2.04),
                        ("EEE/USD", 2.0, 2.03), ("FFF/USD", 2.0, 2.02),
                        ("GGG/USD", 2.0, 2.02)):
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

    # ── 4. apply: queue_review first, then auto_apply with confirm ──
    q = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                 {"action": "queue_review", "operator_id": "the-operator"})
    check("apply: queue_review computes + bundles but never moves the "
          "factor",
          q[0] == 200 and q[1]["status"] == "queued"
          and abs(q[1]["delta"] - aa.compute_delta(7, 4)[0]) < 1e-12
          and aa.read_guard_factor() == 2.0, q)
    na = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                  {"action": "auto_apply"})
    check("apply: auto_apply without confirm refuses (the HTTP-level "
          "safety catch)",
          na[1]["status"] == "refused"
          and na[1]["reason"] == "confirmation_required", na)
    ok_apply = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                        {"action": "auto_apply", "confirm": True,
                         "operator_id": "the-operator"})
    check("apply: a CONFIRMED OPERATOR apply proceeds through the "
          "concentration gate (deep-veto carries 3 of 4 payers — the "
          "review happened, that is what confirm means) and moves the "
          "factor",
          ok_apply[1]["status"] == "applied"
          and abs(aa.read_guard_factor()
                  - aa.clamp(2.0, ok_apply[1]["delta"])) < 1e-9,
          (ok_apply, aa.read_guard_factor()))
    rb = ha.route("POST", "/api/hindsight/rotation/ROT-1/apply",
                  {"action": "bogus"})
    check("apply: an unknown action refuses by name",
          rb[1]["status"] == "refused"
          and "unknown_action" in rb[1]["reason"], rb)
    check("apply: every decision landed on the telemetry stream",
          len(ta.read_events()) >= 2, len(ta.read_events()))

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
