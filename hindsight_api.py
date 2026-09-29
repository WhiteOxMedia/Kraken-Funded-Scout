"""THE HINDSIGHT API (task 3, Sep 28 spec): the four endpoints the
spec names, as pure functions over the machine's real seams — the
per-gate SQL (task 1), the proportional rule (task 2), and the
telemetry stream (task 4) — plus a route() dispatcher and a stdlib
http.server stub exposing the same functions over HTTP.

    GET  /api/hindsight/rotation/:id/summary
         -> {rotation_id, B, P, r, proposed_delta, current_GF}
    GET  /api/hindsight/rotation/:id/gates
         -> [{gate, B_g, P_g, r_g, top_candidates[]}]
    GET  /api/hindsight/rotation/:id/evidence/:candidate_id
         -> {ohlcv_snippet, log_excerpt, trace_id, checksum}
    POST /api/hindsight/rotation/:id/apply
         body {action: auto_apply|queue_review, operator_id?, confirm?}

Every path defaults to the machine's real files but accepts explicit
paths (the sandbox pattern) — the contract tests run against fixtures,
never production state. The dispatcher is the same object the HTTP
handler calls, so a contract test IS a wire test.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# The module-default paths: the API's functions take explicit paths
# (the sandbox pattern), and when omitted they fall back to THESE —
# which the tests re-point so the WIRE route is sandboxable too (the
# first run proved the hard lesson: a route that ignores the sandbox
# quietly answers from production state — B=0 out of an empty default).
DEFAULT_SUPP: Path | None = None
DEFAULT_CACHE: Path | None = None


def _paths(supp: Path | None, cache: Path | None) -> tuple[Path, Path]:
    return (supp or DEFAULT_SUPP or ROOT / "popup_suppressions.jsonl",
            cache or DEFAULT_CACHE or ROOT / "ohlcv_cache.db")


def _sha256_obj(obj) -> str:
    blob = json.dumps(obj, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _authenticated(operator_id) -> bool:
    """The mutating-POST gate: the request's operator_id must match the
    deployment's HINDSIGHT_OPERATOR_TOKEN (the machine's env-dial
    pattern). No token configured = nothing that changes state is
    reachable over the wire — the panel still reads everything, and
    the concentration gate always queues."""
    import os
    token = os.environ.get("HINDSIGHT_OPERATOR_TOKEN", "")
    return bool(token) and str(operator_id or "") == token


# ── the aggregate underneath all routes ─────────────────────────────
def _rotation_aggregate(rotation_id: str, goal_pct: float,
                        supp_path: Path, cache_path: Path) -> dict:
    import per_gate_export as pge
    rows = pge.dedup_first(pge.load_rotation_rows(supp_path, rotation_id))
    con = pge.build_fixture_db(rows, cache_path, goal_pct,
                               rotation_id=rotation_id)
    try:
        gate_rows = pge.run_ratios_sql(con, rotation_id, goal_pct)
    finally:
        con.close()
    summ = pge.rotation_summary(gate_rows)
    return {"rows": rows, "gate_rows": gate_rows,
            "B": summ["B"], "P": summ["P"], "r": summ["r"]}


# ── 1. the summary ──────────────────────────────────────────────────
def rotation_summary(rotation_id: str, goal_pct: float = 1.5,
                     supp_path: Path | None = None,
                     cache_path: Path | None = None) -> dict:
    import auto_adjust as aa
    supp, cache = _paths(supp_path, cache_path)
    agg = _rotation_aggregate(rotation_id, goal_pct, supp, cache)
    delta, reason = aa.compute_delta(agg["B"], agg["P"])
    return {"rotation_id": rotation_id, "B": agg["B"], "P": agg["P"],
            "r": agg["r"], "proposed_delta": delta,
            "proposed_reason": reason,
            "current_GF": aa.read_guard_factor()}


# ── 2. the per-gate table ───────────────────────────────────────────
def rotation_gates(rotation_id: str, goal_pct: float = 1.5,
                   supp_path: Path | None = None,
                   cache_path: Path | None = None) -> list[dict]:
    import per_gate_export as pge
    supp, cache = _paths(supp_path, cache_path)
    agg = _rotation_aggregate(rotation_id, goal_pct, supp, cache)
    # per-candidate runs for the top list (payers only, the panel's rule)
    per_cand = []
    con = pge._cache_ro(cache)
    try:
        for r in agg["rows"]:
            try:
                t = datetime.fromisoformat(str(r.get("ts") or ""))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=pge._now_mst().tzinfo)
                peak = pge.compute_peak(con, str(r.get("pair")),
                                        int(t.timestamp() * 1000),
                                        goal_pct)
            except Exception:
                peak = {"max_run_pct": None}
            per_cand.append({"pair": r.get("pair"),
                             "gate": r.get("gate") or "?",
                             "ts": r.get("ts"),
                             "run": peak.get("max_run_pct")})
    finally:
        if con is not None:
            con.close()
    by_gate: dict[str, dict] = {}
    for g in agg["gate_rows"]:
        by_gate[g["gate"]] = {"gate": g["gate"],
                              "B_g": g["measurable_B_g"],
                              "P_g": g["P_g"], "r_g": g["r_g"],
                              "top_candidates": []}
    for c in sorted(per_cand, key=lambda c: -(c["run"] or -1)):
        g = by_gate.get(c["gate"])
        if g is not None and c["run"] is not None and c["run"] >= goal_pct:
            g["top_candidates"].append(c)
    # the wireframe's order: payers first, payer-rate the tiebreak
    return sorted(by_gate.values(),
                  key=lambda g: (-(g["P_g"] or 0),
                                 -(g["r_g"] if g["r_g"] is not None
                                   else -1)))


# ── 3. the evidence endpoint ────────────────────────────────────────
def candidate_evidence(rotation_id: str, candidate_id: str,
                       cache_path: Path | None = None,
                       log_path: Path | None = None,
                       supp_path: Path | None = None,
                       hours: float = 7.0) -> dict:
    """OHLCV snippet around the refusal, a log excerpt, a trace id, a
    checksum — the bundle the operator reads before trusting a delta."""
    import per_gate_export as pge
    cache, supp = _paths(supp_path, cache_path)[1], _paths(supp_path, None)[0]
    log_path = log_path or ROOT / "logs" / "scanner_console.log"
    parts = candidate_id.split("|")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        # VALIDATED BEFORE USE (the isolated review's follow-up): a
        # malformed id is a named 404-shape refusal, not a ValueError
        # from the unpack, and never a silent wrong-candidate read.
        return {"rotation_id": rotation_id, "candidate_id": candidate_id,
                "error": "malformed candidate_id (want pair|rot|gate)",
                "ohlcv_snippet": [], "log_excerpt": [], "trace_id": "",
                "checksum": ""}
    pair, row_rot, gate = parts
    # THE EVIDENCE ROW IS SCOPED BY THE FULL ROTATION ID (the isolated
    # review's major): the candidate id's rot half may be the journal's
    # bare window — the lookup asks for THE rotation's rows, never
    # every day's same window.
    ts = ""
    for r in pge.load_rotation_rows(supp, rotation_id):
        if r.get("pair") == pair and (r.get("gate") or "?") == gate:
            ts = str(r.get("ts") or "")
            break
    snippet: list = []
    if ts and cache.exists():
        t = datetime.fromisoformat(ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=pge._now_mst().tzinfo)
        ms = int(t.timestamp() * 1000)
        con = sqlite3.connect(cache.as_uri() + "?mode=ro", uri=True,
                              timeout=2.0)
        try:
            snippet = con.execute(
                "SELECT pair, ts, open, high, low, close FROM ohlcv "
                "WHERE pair=? AND ts>=? AND ts<=? ORDER BY ts",
                (pair, ms, ms + int(hours * 3_600_000))).fetchall()
        finally:
            con.close()
    excerpt = (log_path.read_text(encoding="utf-8",
                                  errors="replace").splitlines()[-30:]
               if log_path.exists() else [])
    out = {"rotation_id": rotation_id, "candidate_id": candidate_id,
           "ts": ts, "ohlcv_snippet": snippet,
           "log_excerpt": excerpt,
           "trace_id": f"trace_{hashlib.sha256(candidate_id.encode()).hexdigest()[:12]}"}
    out["checksum"] = _sha256_obj(out)
    return out


# ── 4. the apply endpoint ───────────────────────────────────────────
def apply_action(rotation_id: str, action: str,
                 operator_id: str | None = None, confirm: bool = False,
                 goal_pct: float = 1.5, cohort: str = "canary-1",
                 operator_override: bool = False,
                 supp_path: Path | None = None,
                 cache_path: Path | None = None,
                 params: dict | None = None) -> dict:
    """auto_apply runs the full proportional path (every rail live);
    queue_review computes and bundles but does NOT touch the factor.
    auto_apply demands confirm=True — the HTTP-level safety catch.
    operator_override=True is the AUTHENTICATED human-in-the-loop (the
    route checks the operator token; an unauthenticated body can never
    lift the concentration queue): only the queue lifts — every other
    rail still bites. params (Sep 29): the canary's selected cell
    (alpha, delta_max) overriding the module defaults for THIS call —
    the boundary hook threads the cohort's armed params through."""
    import auto_adjust as aa
    import telemetry_adjust as ta
    supp, cache = _paths(supp_path, cache_path)
    agg = _rotation_aggregate(rotation_id, goal_pct, supp, cache)
    B, P, gate_rows = agg["B"], agg["P"], agg["gate_rows"]
    delta, reason = aa.compute_delta(B, P)
    cks, d = aa.write_evidence_bundle(
        rotation_id, gate_rows, B, P, agg["r"])
    if action == "queue_review":
        rec = {"status": "queued", "reason": "operator_queue_review",
               "before": aa.read_guard_factor(),
               "after": aa.read_guard_factor(), "delta": delta,
               "evidence": cks, "bundle": str(d)}
        ta.emit_adjustment(rotation_id, rec, B, P, gate_rows,
                           cohort=cohort,
                           applied_by=operator_id or "operator")
        return rec
    if action != "auto_apply":
        return {"status": "refused",
                "reason": f"unknown_action {action!r}"}
    if not confirm:
        return {"status": "refused", "reason": "confirmation_required",
                "evidence": cks, "bundle": str(d)}
    rec = aa.propose_from_aggregate(rotation_id, B, P, gate_rows,
                                    operator_override=operator_override,
                                    params=params)
    ta.emit_adjustment(rotation_id, rec, B, P, gate_rows, cohort=cohort,
                       applied_by=operator_id or "system")
    return rec


def rollback(rotation_id: str) -> dict:
    import auto_adjust as aa
    return aa.rollback(rotation_id)


# ── 4. the ledger (the adjustments' authoritative history) ──────────
def adjustment_ledger(limit: int = 50) -> dict:
    """The auto_adjust_ledger, NEWEST FIRST, capped — the guard state's
    own record of every apply/queue/refuse (day, rotation_id, status,
    before → after, reason, evidence checksum). Read-only over the
    wire; the panel's zone-4 companion renders it verbatim."""
    import profit_guard as pg
    st = pg._state()
    rows = list(st.get("auto_adjust_ledger") or [])
    rows.reverse()                          # newest first
    return {"rows": rows[:max(0, int(limit))]}


# ── 5. the audit trail (telemetry stream, newest first) ─────────────
def telemetry_events(limit: int = 20) -> dict:
    """The last N auto_adjust telemetry events — the spec's zone-5
    stream (applied_by, cohort, commit_sha, evidence checksum ride the
    event; nothing is ever deleted, the file is append-only)."""
    import telemetry_adjust as ta
    evs = list(ta.read_events())
    evs.reverse()                           # newest first
    return {"events": evs[:max(0, int(limit))]}


# ── the wire: one dispatcher, HTTP or tests ─────────────────────────
def route(method: str, path: str, body: dict | None = None,
          goal_pct: float = 1.5) -> tuple[int, dict]:
    """The routing table both the http.server stub and the contract
    tests call. Returns (status_code, payload)."""
    parts = [p for p in path.split("/") if p]
    # the global history endpoints (NOT rotation-scoped — the ledger is
    # the guard state's whole record, the telemetry stream is the whole
    # audit trail; both GET-only, both read-only)
    if len(parts) == 3 and parts[:2] == ["api", "hindsight"]:
        if method == "GET" and parts[2] == "ledger":
            return 200, adjustment_ledger()
        if method == "GET" and parts[2] == "telemetry":
            return 200, telemetry_events()
    # api / hindsight / rotation / :id / ...
    if len(parts) >= 4 and parts[:3] == ["api", "hindsight", "rotation"]:
        from urllib.parse import unquote
        rid = unquote(parts[3])
        tail = parts[4:]
        if method == "GET" and tail == ["summary"]:
            return 200, rotation_summary(rid, goal_pct)
        if method == "GET" and tail == ["gates"]:
            return 200, {"rotation_id": rid,
                         "gates": rotation_gates(rid, goal_pct)}
        if method == "GET" and len(tail) == 2 and tail[0] == "evidence":
            # the candidate id is a single encoded segment: pairs carry
            # slashes (AAA/USD|ROT|gate) and a raw slash would split
            # into extra path parts and 404
            return 200, candidate_evidence(rid, unquote(tail[1]))
        if method == "POST" and tail == ["rollback"]:
            b = body or {}
            if not _authenticated(b.get("operator_id")):
                return 403, {"error": "operator token required for "
                                       "state-changing POSTs"}
            return 200, rollback(rid)
        if method == "POST" and tail == ["apply"]:
            b = body or {}
            action = str(b.get("action") or "")
            operator_id = b.get("operator_id")
            # THE MUTATING POSTS ARE AUTHENTICATED (the isolated
            # review's major): every POST that can move the guard or
            # the ledger requires the operator token — queue_review
            # too, not only the concentration override. auto_apply
            # carries its own confirm catch on top.
            if not _authenticated(operator_id):
                return 403, {"error": "operator token required for "
                                       "state-changing POSTs"}
            # THE CONCENTRATION OVERRIDE rides an EXPLICIT request
            # field, never a side effect of authentication (the
            # follow-up review's major): an authenticated operator who
            # does not ask for the override still gets the queue
            # (review-then-act, not act-on-auth). The token itself is
            # never echoed back - the ledger carries the non-secret
            # identifier the body sent.
            override = bool(b.get("override_concentration"))
            # THE TOKEN IS NEVER ECHOED (round-2 major, the
            # no-override path): the body's operator_id IS the credential
            # — passing it raw leaked it into telemetry's applied_by on
            # every authenticated POST, override or not. Telemetry
            # always carries the non-secret identifier; the secret
            # stays in the env where _authenticated read it.
            operator_id = "operator"
            return 200, apply_action(
                rid, action, operator_id=operator_id,
                confirm=bool(b.get("confirm")), goal_pct=goal_pct,
                operator_override=override)
    return 404, {"error": f"no route {method} {path}"}


def make_handler(goal_pct: float = 1.5):
    """The stdlib http.server handler wrapping route() — the stub the
    spec asks for; the console panel's wireframe talks to this."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _H(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def do_GET(self):          # noqa: N802 (stdlib's name)
            code, payload = route("GET", self.path, goal_pct=goal_pct)
            self._send(code, payload)

        def do_POST(self):         # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            code, payload = route("POST", self.path, body,
                                  goal_pct=goal_pct)
            self._send(code, payload)

        def log_message(self, *a):  # quiet
            pass

    return _H, HTTPServer
