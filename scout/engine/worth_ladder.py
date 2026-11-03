"""THE WORTH LADDER - the learning ladder closes the loop (user spec,
Oct 5, build-only-what-is-missing): the verdict (the agreement bar) and
the reconciler (the resolver) already live in discipline.py; this module
owns the verdict-gated WORTH-FLOOR channel they were missing. A
calibration may raise the worth floor only when money confirmed the
score; QC and the ladder must agree on the same number before anything
applies; the exit-watch preflight protects the apply; and every decision
leaves atomic, checksummed evidence in an immutable append-only ledger."""
import hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE_DIR = ROOT / "scratch" / "hailmary"
LEDGER_FILE = EVIDENCE_DIR / "worth_ladder_ledger.jsonl"
CONSOLE_STATE = ROOT / "console_state.json"
DIAL_FILE = ROOT / "exit_extension.dial.json"

WORTH_PROVEN_MIN_REALIZED = 0.0   # strictly greater
WORTH_PROVEN_MAX_LEFT = 1.0       # the $1 crumb the voice itself ignores
WORTH_LADDER_NUDGE = 0.10         # the ladder's floor delta per Proven
WORTH_LADDER_MIN = 0.10           # worth_floor_min (the dial's own clamp)
WORTH_LADDER_MAX = 0.40           # worth_floor_max (the dial's own clamp)
WORTH_PREFLIGHT_FILL_MIN = 0.95   # the sim's floor for the book's capacity
WORTH_PREFLIGHT_SLIP_TOL = 0.50   # the RT-cost share of the marginal payout
COST_ROUNDTRIP_PCT = 0.08         # the contest account's own cost model


def compute_verdict(realized_usd, total_left_usd) -> str:
    """THE AGREEMENT RULE (hard rule 1): a rotation is Proven only when
    money actually confirms the score - realized > 0 AND the table
    drained to the crumb. Everything else is Unproven and renders as
    such; the ladder may not raise the floor on it."""
    try:
        _r = float(realized_usd or 0.0)
        _l = float(total_left_usd or 0.0)
    except Exception:
        return "UNPROVEN"
    return "PROVEN" if (_r > WORTH_PROVEN_MIN_REALIZED
                        and _l <= WORTH_PROVEN_MAX_LEFT) else "UNPROVEN"


def candidate_floor(current, proven, delta=WORTH_LADDER_NUDGE,
                    lo=WORTH_LADDER_MIN, hi=WORTH_LADDER_MAX) -> float:
    """THE WORTH-FLOOR UPDATE RULE (hard rule 2): candidates come only
    from Proven rotations, nudged UP, bounded by the dial's own band
    (worth_floor_min..worth_floor_max); never lowered from a single
    rotation; an Unproven rotation changes nothing."""
    try:
        _c = float(current or 0.0)
    except Exception:
        _c = 0.0
    if not proven:
        return round(_c, 6)
    _cand = min(float(hi), max(float(lo), _c * (1.0 + float(delta))))
    return round(max(_c, _cand), 6)


def _dial_key(key, default=""):
    """The dial's env-first seam (the TIDE_RETUNE_CANARY idiom): an env
    override, then exit_extension.dial.json, then the default."""
    _v = os.environ.get(key)
    if _v is not None:
        return _v
    try:
        _doc = json.loads(DIAL_FILE.read_text(encoding="utf-8"))
        return str(_doc.get(key, default))
    except Exception:
        return default


def worth_ladder_enabled() -> bool:
    """The feature flag (the spec's rollout lever: canary first, then
    global). Anything but exactly '1' is OFF - the ladder is inert."""
    return _dial_key("WORTH_LADDER_ENABLED", "0") == "1"


def armed_cell_checksum() -> tuple:
    """The armed cohort cell - the single source of truth for the cohort
    params - read and checksummed (an env path override first, then the
    dial file itself)."""
    _p = os.environ.get("WORTH_LADDER_ARMED_CELL")
    _path = Path(_p) if _p else DIAL_FILE
    try:
        _b = _path.read_bytes()
        return ("sha256:" + hashlib.sha256(_b).hexdigest(), _path.name)
    except Exception:
        return ("sha256:unreadable", "missing")


def _ledger_rows(kind=None) -> list:
    """The immutable ledger, read back. Append-only by construction -
    the writers only ever open 'a' - and filtered by kind for the pins."""
    try:
        _rows = [json.loads(_l) for _l
                 in LEDGER_FILE.read_text(encoding="utf-8").splitlines()
                 if _l.strip()]
    except Exception:
        return []
    return [_r for _r in _rows if kind is None or _r.get("kind") == kind]


def _decided(rotation_id: str) -> bool:
    """A decided rotation can never re-decide - the QC ledger's own
    immutability rule (hard rule 3)."""
    return any(_r.get("rotation_id") == str(rotation_id)
               for _r in _ledger_rows(kind="qc_decision"))


def _evidence_write(kind: str, payload: dict, ledger_kind=None,
                    extra=None) -> dict:
    """ONE atomic, checksummed evidence artifact: written via tmp +
    os.replace (no torn evidence), sha256 over the EXACT bytes, and an
    append-only ledger row. The ledger never rewrites - immutability is
    the append."""
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    _name = f"{kind}_{_ts}.json"
    _path = EVIDENCE_DIR / _name
    _body = dict(payload or {})
    _body["kind"] = kind
    _body["written_at"] = _ts
    _raw = json.dumps(_body, sort_keys=True, default=str).encode("utf-8")
    _tmp = _path.with_suffix(".tmp")
    _tmp.write_bytes(_raw)
    os.replace(_tmp, _path)
    _sum = "sha256:" + hashlib.sha256(_raw).hexdigest()
    _row = {"ts": datetime.now(timezone.utc).isoformat(),
            "kind": ledger_kind or kind, "file": _name,
            "evidence_checksum": _sum}
    _row.update(extra or {})
    try:
        with LEDGER_FILE.open("a", encoding="utf-8") as _f:
            _f.write(json.dumps(_row, sort_keys=True, default=str) + chr(10))
    except Exception:
        pass
    return {"file": _name, "evidence_checksum": _sum}


def propose_from_rotation(rotation_id, score, realized_usd, total_left_usd,
                          current_floor):
    """THE INGEST SEAM (hard rules 1+2+5, the rollover/cooldown entry):
    one rotation in, one QC payload out. Unproven mints NOTHING - no
    candidate row, no evidence, no floor movement (rule 2's only-from-
    Proven); a Proven rotation writes its evidence bundle and the
    candidate ledger row exactly once (a replay after a crash returns
    the same payload, never a second row). Flag off -> None: inert."""
    if not worth_ladder_enabled():
        return None
    _rid = str(rotation_id)
    _verdict = compute_verdict(realized_usd, total_left_usd)
    try:
        _cur = round(float(current_floor or 0.0), 6)
    except Exception:
        _cur = 0.0
    try:
        _score = float(score or 0.0)
    except Exception:
        _score = 0.0
    _cell_sum, _cell_name = armed_cell_checksum()
    if _verdict != "PROVEN":
        return {"rotation_id": _rid, "score": _score,
                "realized_usd": realized_usd, "total_left_usd": total_left_usd,
                "verdict": "UNPROVEN", "current_floor": _cur,
                "candidate_floor": _cur,
                "armed_cell_checksum": _cell_sum}
    _mine = [r for r in _ledger_rows(kind="candidate")
             if r.get("rotation_id") == _rid]
    if _mine:
        _r0 = _mine[-1]
        return {"rotation_id": _rid, "score": _score,
                "realized_usd": realized_usd, "total_left_usd": total_left_usd,
                "verdict": "PROVEN", "current_floor": _cur,
                "candidate_floor": _r0.get("candidate_floor", _cur),
                "armed_cell_checksum": _r0.get("armed_cell_checksum", _cell_sum),
                "evidence_checksum": _r0.get("evidence_checksum", ""),
                "evidence_file": _r0.get("file", "")}
    _cand = candidate_floor(_cur, True)
    _res = _evidence_write("evidence", {
        "rotation_id": _rid, "score": _score,
        "realized_usd": realized_usd, "total_left_usd": total_left_usd,
        "verdict": "PROVEN", "current_floor": _cur, "candidate_floor": _cand,
        "learning_inputs": {"nudge": WORTH_LADDER_NUDGE,
                            "band": [WORTH_LADDER_MIN, WORTH_LADDER_MAX]},
        "armed_cell": _cell_name, "armed_cell_checksum": _cell_sum},
        ledger_kind="candidate",
        extra={"rotation_id": _rid, "score": _score, "verdict": "PROVEN",
               "current_floor": _cur, "candidate_floor": _cand,
               "armed_cell_checksum": _cell_sum})
    return {"rotation_id": _rid, "score": _score, "realized_usd": realized_usd,
            "total_left_usd": total_left_usd, "verdict": "PROVEN",
            "current_floor": _cur, "candidate_floor": _cand,
            "armed_cell_checksum": _cell_sum,
            "evidence_checksum": _res["evidence_checksum"],
            "evidence_file": _res["file"]}


def propose_from_recommendation(rec, source="pool_min_weekly",
                                channel="pool_min", week_key="") -> dict:
    """THE GATE-FLOOR INGEST SEAM (user ask, Oct 6): the weekly scorer's
    gate-floor recommendations mint a QC candidate through the SAME
    evidence path a Proven rotation rides - one evidence bundle, one
    candidate ledger row, the armed cell, the checksum - so the
    recommendation can only move its floor through qc_decide's
    exit-watch path, never a direct file write. The scorer's own
    stability gate IS the money proof here: a payload whose
    recommendation is absent, out of range, or not stability-gated
    mints NOTHING (the UNPROVEN receipt says why). The candidate floor
    is the gate's own number - the worth band is the worth channel's,
    not the gate's. A re-mint of the same source+week+value is
    idempotent: one candidate, ever. Flag off -> None: inert."""
    if not worth_ladder_enabled():
        return None
    _src = str(source or "gate_weekly")
    _ch = str(channel or "pool_min")
    _wk = str(week_key or "unknown").replace(" ", "_").replace(":", "")
    _v = (rec or {}).get("recommendation") if isinstance(rec, dict) else None
    if isinstance(_v, bool) or not isinstance(_v, (int, float)):
        return {"source": _src, "week_key": _wk, "channel": _ch,
                "verdict": "UNPROVEN",
                "reason": "the sweep carries no recommendation - "
                          "nothing minted"}
    if _ch == "pool_min" and not 0.0 < float(_v) <= 1.0:
        return {"source": _src, "week_key": _wk, "channel": _ch,
                "verdict": "UNPROVEN",
                "reason": "the recommendation is out of the gate's own "
                          "range - nothing minted"}
    if _ch == "fbw_weeks" and not 1 <= int(_v) <= 12:
        return {"source": _src, "week_key": _wk, "channel": _ch,
                "verdict": "UNPROVEN",
                "reason": "the weeks recommendation is out of range - "
                          "nothing minted"}
    if (rec or {}).get("stable") is not True:
        return {"source": _src, "week_key": _wk, "channel": _ch,
                "verdict": "UNPROVEN",
                "reason": "the scorer's stability gate did not clear - "
                          "nothing minted"}
    _rid = "{}_{}_{}".format(_src, _wk, "{:.6g}".format(float(_v)))
    _cell_sum, _cell_name = armed_cell_checksum()
    _mine = [r for r in _ledger_rows(kind="candidate")
             if r.get("rotation_id") == _rid]
    if _mine:
        _r0 = _mine[-1]
        return {"rotation_id": _rid, "source": _src, "week_key": _wk,
                "channel": _ch, "verdict": "PROVEN",
                "candidate_floor": _r0.get("candidate_floor", float(_v)),
                "armed_cell_checksum": _r0.get("armed_cell_checksum",
                                               _cell_sum),
                "evidence_checksum": _r0.get("evidence_checksum", ""),
                "evidence_file": _r0.get("file", ""),
                "already_minted": True}
    _cand = round(float(_v), 6)
    _res = _evidence_write("evidence", {
        "rotation_id": _rid, "source": _src, "week_key": _wk,
        "channel": _ch, "recommendation": _v,
        "n_priced": (rec or {}).get("n_priced"), "stable": True,
        "mode": (rec or {}).get("mode"),
        "proposal": (rec or {}).get("proposal"),
        "verdict": "PROVEN", "current_floor": current_floor_frac(),
        "candidate_floor": _cand,
        "learning_inputs": {"scorer": "tidebar_weekly", "gate": _ch},
        "armed_cell": _cell_name, "armed_cell_checksum": _cell_sum},
        ledger_kind="candidate",
        extra={"rotation_id": _rid, "source": _src, "channel": _ch,
               "verdict": "PROVEN", "candidate_floor": _cand,
               "armed_cell_checksum": _cell_sum})
    return {"rotation_id": _rid, "source": _src, "week_key": _wk,
            "channel": _ch, "verdict": "PROVEN", "candidate_floor": _cand,
            "armed_cell_checksum": _cell_sum,
            "evidence_checksum": _res["evidence_checksum"],
            "evidence_file": _res["file"]}



def _exit_watch_sim(watch_rows, candidate_floor, daily_pace) -> dict:
    """THE EXIT-WATCH PREFLIGHT SIM (hard rule 4): the book's capacity
    against the candidate floor. A watch row is servable when its
    marginal goal payout still clears the floor's share of the daily
    pace - a floor the book cannot serve would silence the very exits
    that earn the worth - and the roundtrip cost model must sit inside
    the slippage tolerance. fill_probability >= 0.95 or the apply dies."""
    _rows = [r for r in (watch_rows or []) if isinstance(r, dict)]
    try:
        _pace = float(daily_pace or 0.0)
    except Exception:
        _pace = 0.0
    try:
        _cand = float(candidate_floor or 0.0)
    except Exception:
        _cand = 0.0
    _bar = _cand * _pace
    _filled, _reasons = [], []
    for _r in _rows:
        try:
            _payout = (float(_r.get("size", 0.0))
                       * float(_r.get("goal_pct", 0.0)) / 100.0)
        except Exception:
            _payout = 0.0
        if _bar <= 0.0 or _payout >= _bar:
            _filled.append(_r.get("pair"))
        else:
            _reasons.append(str(_r.get("pair")) + " payout "
                            + str(round(_payout, 2)) + " < floor share "
                            + str(round(_bar, 2)))
    _slip = COST_ROUNDTRIP_PCT
    if _slip > WORTH_PREFLIGHT_SLIP_TOL:
        _reasons.append("roundtrip cost above tolerance")
    _prob = (float(len(_filled)) / float(len(_rows))) if _rows else 0.0
    _ok = bool(_rows) and _prob >= WORTH_PREFLIGHT_FILL_MIN
    _reason = "" if _ok else ("; ".join(_reasons) or "empty watch book")
    return {"fill_probability": round(_prob, 6),
            "expected_slippage": _slip, "rows": len(_rows),
            "servable": _filled, "bar_per_row": round(_bar, 6),
            "ok": _ok, "reason": _reason}


def _apply_console_floor(applied_floor) -> None:
    """THE APPLY PATH's dial move: console_state.json's worth_floor_frac
    becomes the applied number - read-modify-write through tmp +
    os.replace (the console's own atomic idiom), created if missing."""
    try:
        _doc = json.loads(CONSOLE_STATE.read_text(encoding="utf-8"))
    except Exception:
        _doc = {}
    if not isinstance(_doc, dict):
        _doc = {}
    _doc["worth_floor_frac"] = round(float(applied_floor), 6)
    try:
        CONSOLE_STATE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _tmp = CONSOLE_STATE.with_suffix(".tmp")
    _tmp.write_text(json.dumps(_doc, sort_keys=True), encoding="utf-8")
    os.replace(_tmp, CONSOLE_STATE)


def qc_decide(pay, operator, decision, reason="", watch_rows=None,
              daily_pace=None, channel_apply=None) -> dict:
    """THE QC SEAM (hard rule 3): the operator decides ONCE, on the same
    candidate number the ladder minted. A decided rotation can never
    re-decide (the ledger's immutability); a rejection writes its row
    and applies nothing; a confirmation runs the exit-watch preflight
    and only a servable book applies - and an UNPROVEN payload is
    THE GATE CHANNELS (user ask, Oct 6): a payload carrying a channel
    ("pool_min" or "fbw_weeks") rides the SAME confirm + exit-watch
    path; its apply dispatches by channel - pool_min through the
    registered channel applier (the guarded confirm-to-apply),
    fbw_weeks as the LEDGERED hand-apply instruction - and a gate
    apply NEVER touches the console worth floor.
    refused outright, because nothing unproven ever moves the floor."""
    if not isinstance(pay, dict) or pay.get("verdict") != "PROVEN":
        raise ValueError("qc_decide requires a PROVEN payload")
    _rid = str(pay.get("rotation_id", ""))
    if _decided(_rid):
        raise ValueError("rotation already decided: " + _rid)
    _op = str(operator or "unknown")
    _sum = str(pay.get("evidence_checksum", ""))
    _ch = str(pay.get("channel") or "worth_floor")
    if str(decision) != "confirmed":
        _res = _evidence_write("qc_decision", {
            "rotation_id": _rid, "operator": _op, "decision": "rejected",
            "reason": str(reason or ""), "candidate_floor": pay.get("candidate_floor"),
            "evidence_checksum": _sum},
            ledger_kind="qc_decision",
            extra={"rotation_id": _rid, "operator": _op,
                   "decision": "rejected", "reason": str(reason or "")})
        return {"applied": False, "decision": "rejected",
                "rotation_id": _rid, "operator": _op,
                "qc_file": _res["file"],
                "evidence_checksum": _res["evidence_checksum"]}
    try:
        _candf = round(float(pay.get("candidate_floor")), 6)
    except Exception:
        raise ValueError("payload carries no candidate floor")
    # THE CONFIRMED DECISION RIDES THE LEDGER TOO (hard rule 3): the
    # operator, the SAME candidate and the payload checksum, written
    # BEFORE the preflight - a decided rotation can never re-decide,
    # whatever the exit watch then answers.
    _evidence_write("qc_decision", {
        "rotation_id": _rid, "operator": _op, "decision": "confirmed",
        "candidate_floor": _candf, "reason": str(reason or ""),
        "evidence_checksum": _sum},
        ledger_kind="qc_decision",
        extra={"rotation_id": _rid, "operator": _op,
               "decision": "confirmed"})
    _sim_floor = _candf
    if _ch != "worth_floor":
        # A gate apply leaves the worth floor in force - the preflight
        # asks the SAME capacity question under the floor that governs
        # the book after the apply (the live one).
        _sim_floor = current_floor_frac()
    _sim = _exit_watch_sim(watch_rows, _sim_floor, daily_pace)
    _pf = _evidence_write("preflight", {
        "rotation_id": _rid, "channel": _ch, "candidate_floor": _candf,
        "sim_floor": _sim_floor,
        "daily_pace": daily_pace, "sim": _sim,
        "watch_rows": list(watch_rows or [])},
        ledger_kind="exit_watch",
        extra={"rotation_id": _rid,
               "exit_watch": "pass" if _sim["ok"] else "fail"})
    if not _sim["ok"]:
        return {"applied": False, "decision": "confirmed",
                "exit_watch": "fail", "reason": _sim["reason"],
                "rotation_id": _rid, "operator": _op,
                "preflight_file": _pf["file"],
                "evidence_checksum": _pf["evidence_checksum"]}
    if _ch == "worth_floor":
        _ap = _evidence_write("apply", {
            "rotation_id": _rid, "operator": _op, "applied_floor": _candf,
            "previous_floor": pay.get("current_floor"), "sim": _sim,
            "daily_pace": daily_pace, "fills": _sim["servable"],
            "fill_fraction": _sim["fill_probability"],
            "realized_slippage": _sim["expected_slippage"]},
            ledger_kind="apply",
            extra={"rotation_id": _rid, "operator": _op, "applied_floor": _candf})
        _apply_console_floor(_candf)
        return {"applied": True, "decision": "confirmed", "exit_watch": "pass",
                "applied_floor": _candf, "rotation_id": _rid, "operator": _op,
                "preflight_file": _pf["file"], "apply_file": _ap["file"],
                "evidence_checksum": _ap["evidence_checksum"]}
    # THE GATE CHANNELS (user ask, Oct 6): the same evidence + exit-watch
    # path, dispatched by the payload's channel - and a gate apply NEVER
    # touches the console worth floor.
    if _ch == "pool_min":
        if not callable(channel_apply):
            return {"applied": False, "decision": "confirmed",
                    "exit_watch": "pass", "channel": _ch,
                    "reason": "no apply channel registered for pool_min",
                    "rotation_id": _rid, "operator": _op,
                    "preflight_file": _pf["file"],
                    "evidence_checksum": _pf["evidence_checksum"]}
        try:
            _ca = channel_apply()
        except Exception as _exc:
            _ca = {"applied": False,
                   "reason": "the channel apply raised: " + str(_exc)}
        _ap = _evidence_write("apply", {
            "rotation_id": _rid, "operator": _op, "channel": _ch,
            "applied_floor": _candf, "previous_floor": pay.get("current_floor"),
            "sim": _sim, "daily_pace": daily_pace,
            "channel_result": _ca},
            ledger_kind="apply",
            extra={"rotation_id": _rid, "operator": _op, "channel": _ch,
                   "applied": bool((_ca or {}).get("applied"))})
        if not (_ca or {}).get("applied"):
            return {"applied": False, "decision": "confirmed",
                    "exit_watch": "pass", "channel": _ch,
                    "reason": str((_ca or {}).get("reason")
                                  or "the channel apply refused"),
                    "rotation_id": _rid, "operator": _op,
                    "apply_file": _ap["file"],
                    "evidence_checksum": _ap["evidence_checksum"]}
        return {"applied": True, "decision": "confirmed",
                "exit_watch": "pass", "channel": _ch,
                "applied_floor": _candf, "rotation_id": _rid,
                "operator": _op, "apply_file": _ap["file"],
                "evidence_checksum": _ap["evidence_checksum"]}
    if _ch == "fbw_weeks":
        # THE ADVICE CHANNEL: FLIP_BACK_WEEKS is a shipped constant - the
        # machinery never edits itself. The confirmed decision's apply is
        # the LEDGERED hand-apply instruction: the evidence row records
        # what her hand must change, nothing else moves.
        _ap = _evidence_write("apply", {
            "rotation_id": _rid, "operator": _op, "channel": _ch,
            "applied_floor": _candf, "sim": _sim, "daily_pace": daily_pace,
            "hand_apply": True,
            "instruction": "set FLIP_BACK_WEEKS -> {} in code (her hand; "
                           "the machinery never edits itself)".format(_candf)},
            ledger_kind="apply",
            extra={"rotation_id": _rid, "operator": _op, "channel": _ch,
                   "applied_floor": _candf, "hand_apply": True})
        return {"applied": True, "hand_apply": True, "channel": _ch,
                "decision": "confirmed", "exit_watch": "pass",
                "applied_floor": _candf, "rotation_id": _rid,
                "operator": _op, "apply_file": _ap["file"],
                "evidence_checksum": _ap["evidence_checksum"]}
    return {"applied": False, "decision": "confirmed", "exit_watch": "pass",
            "channel": _ch, "reason": "unknown apply channel: " + _ch,
            "rotation_id": _rid, "operator": _op,
            "preflight_file": _pf["file"],
            "evidence_checksum": _pf["evidence_checksum"]}


def reconciler_flip(rotation_id, realized_usd, total_left_usd,
                    current_floor) -> dict:
    """THE RECONCILER (hard rule 5, idempotent by the ledger): a
    rotation that closed Unproven but realized later flips to PROVEN -
    once, with its audit row (verdict_flip: proven_by_realization) and
    the re-evaluated candidate for the next day's floor. A re-run
    returns the original evidence and mints nothing new; a rotation
    still Unproven on its later numbers stays Unproven."""
    _verdict = compute_verdict(realized_usd, total_left_usd)
    if _verdict != "PROVEN":
        return {"flip": "none", "verdict": _verdict,
                "rotation_id": str(rotation_id)}
    _rid = str(rotation_id)
    _minted = [r for r in _ledger_rows(kind="candidate")
               if r.get("rotation_id") == _rid]
    if _minted:
        return {"flip": "none", "verdict": "PROVEN",
                "rotation_id": _rid, "already_minted": True}
    _prior = [r for r in _ledger_rows(kind="verdict_flip")
              if r.get("rotation_id") == _rid]
    if _prior:
        _r0 = _prior[-1]
        return {"flip": "proven_by_realization", "verdict": "PROVEN",
                "rotation_id": _rid,
                "candidate_floor": _r0.get("candidate_floor"),
                "reconciler_file": _r0.get("file", ""),
                "evidence_checksum": _r0.get("evidence_checksum", ""),
                "idempotent": True}
    _cand = candidate_floor(current_floor, True)
    _res = _evidence_write("reconciler", {
        "rotation_id": _rid, "realized_usd": realized_usd,
        "total_left_usd": total_left_usd, "verdict": "PROVEN",
        "flip": "proven_by_realization", "original_verdict": "UNPROVEN",
        "current_floor": current_floor, "candidate_floor": _cand,
        "next_day_floor_recheck": True},
        ledger_kind="verdict_flip",
        extra={"rotation_id": _rid, "flip": "proven_by_realization",
               "candidate_floor": _cand})
    return {"flip": "proven_by_realization", "verdict": "PROVEN",
            "rotation_id": _rid, "candidate_floor": _cand,
            "reconciler_file": _res["file"],
            "evidence_checksum": _res["evidence_checksum"]}


def current_floor_frac(default=0.25) -> float:
    """The console's LIVE worth floor - what the last apply wrote into
    console_state.json, else the documented base. The reconciler's and
    the QC display's 'current' number; nothing else moves it."""
    try:
        _doc = json.loads(CONSOLE_STATE.read_text(encoding="utf-8"))
        return round(float(_doc.get("worth_floor_frac", default)), 6)
    except Exception:
        return round(float(default), 6)


def evidence_summary() -> dict:
    """ONE read of the ledger for the console's evidence block (the
    discipline plate renders this): the undecided candidates, the QC
    decisions, the flips, the exit-watch failures - the loop's whole
    paper trail, only ever what the ledger proves."""
    _qc = _ledger_rows(kind="qc_decision")
    _gone = {str(_r.get("rotation_id")) for _r in _qc}
    _pend = [{"rotation_id": _r.get("rotation_id"),
              "candidate_floor": _r.get("candidate_floor"),
              "channel": _r.get("channel") or "worth_floor",
              "verdict": _r.get("verdict"),
              "evidence_checksum": _r.get("evidence_checksum")}
             for _r in _ledger_rows(kind="candidate")
             if str(_r.get("rotation_id")) not in _gone]
    return {"enabled": worth_ladder_enabled(),
            "current_floor": current_floor_frac(),
            "pending": _pend[-3:], "qc": _qc[-3:],
            "candidates": len(_ledger_rows(kind="candidate")),
            "flips": len(_ledger_rows(kind="verdict_flip")),
            "watch_fails": len(_ledger_rows(kind="exit_watch_fail")),
            "decided": len(_qc)}
