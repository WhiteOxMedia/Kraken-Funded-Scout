"""test_zerowatch.py — THE WATCHER'S OWN RED PROOF (user ask, Oct 8):
the 12:00 zero-zero watcher (scratch/_watch_1200_zerozero.py) is
one-shot code — its evaluate() runs exactly once, live, tomorrow at
12:04, then the watcher exits. A watcher that rots has no second
chance: it would report GREEN on a regression, or crash at the one
moment it exists for. This drive runs the evaluator TODAY against
synthetic rows and proves every RED path fires and every GREEN path
stays green, so the watcher itself can't rot before it fires.

THE SEAM: evaluate() reads the world exclusively through jload() —
patch that one name to serve a SANDBOX state dict and the live
discipline_state.json is never opened (the hermeticity rule every
drive here lives by). Each case builds an 08-12 row for the watcher's
own TARGET_DATE, injects it, and judges the verdict the evaluator
returns — the same function main() will trust at 12:04 tomorrow.

THE ROWS (why each shape is what it is — discipline.py's caps):
  flagless       empty_fire/capped_zero_zero missing — THE OCT-7 CATCH:
                 the old _entry dropped both, every row read flagless.
                 The evaluator must scream RED about persistence, even
                 when the score looks perfect (a flagless row grades
                 SNIPER on the plate — bool(None) is False).
  missed-shape   fired, realized <= 0, left <= crumb, but capped_zero_zero
                 False — the guard MISSED the shape it owns. The
                 evaluator's honest verdict for this is AMBER (the flags
                 persisted; the CAP is what lied) — pinned as such.
  cap-unbound    capped_zero_zero=True but score over the cap — the flag
                 and the money disagree; RED.
  silent-unbound empty_fire=True but score over 60 — RED.
  green-zz       well-formed zero-zero row (capped=True, score 55) — GREEN.
  green-money    fired and realized money — GREEN, no cap owns it.

Hermetic: jload fully patched (the call counter in run() PROVES the
evaluator went through the seam on every case it drives), no live store
opened, no push (push_topic.txt never read), no report file written
(write_report is not called), no sleeps. Import-safe: the whole body
runs under the __main__ guard (pytest collection imports without
executing — the two majors on the first cut, cured in-amend).
"""
import importlib.util
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
WATCHER = BASE / "scratch" / "_watch_1200_zerozero.py"

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not ok else ""))


# ── import the watcher as a module (its main() is __main__-guarded) ──
spec = importlib.util.spec_from_file_location("zerowatch", WATCHER)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

TARGET_DATE = w.TARGET_DATE           # 2026-10-09 — the watcher's own
_REAL_JLOAD = w.jload
_JLOAD_CALLS = {"n": 0}


def make_state(rows, lessons=None):
    return {"history": rows, "lessons": lessons or []}


def install(state):
    """Serve the sandbox state through the evaluator's own seam."""
    def _patched(name):
        _JLOAD_CALLS["n"] += 1
        return json.loads(json.dumps(state))   # deep copy: no aliasing
    w.jload = _patched


def row(**over):
    base = {"date": TARGET_DATE, "rot": "08-12", "score": 50.0,
            "realized_pnl": 0.0, "total_left": 0.0, "popups": [],
            "empty_fire": False, "capped_zero_zero": False,
            "ts": "2026-10-09T12:00:40-06:00"}
    base.update(over)
    return base


def run(state):
    """Evaluate + THE SEAM PROOF (the Copilot low on the first cut,
    cured in-amend: the patch check must actually RUN, not just exist):
    exactly one jload call per evaluate and the hist_len the sandbox
    served — a fixture that stops reaching the evaluator can never fake
    a pass."""
    _JLOAD_CALLS["n"] = 0
    _, ctx = w.evaluate(dry=False)
    check("seam: the evaluator went through the patched jload (live "
          "file unreachable, sandbox actually served)",
          _JLOAD_CALLS["n"] == 1
          and ctx.get("hist_len") == len(state.get("history") or []),
          f"jload_calls={_JLOAD_CALLS['n']} hist_len={ctx.get('hist_len')}")
    return ctx


def main():
    try:
        # ── 1. THE FLAGLESS ROW (the ask) — the Oct-7 catch must scream
        state = make_state([row(empty_fire=None, capped_zero_zero=None,
                                popups=[{"pair": "X/USD"}],
                                realized_pnl=-3.0, total_left=0.4)])
        install(state)
        ctx = run(state)
        check("1.1 a flagless 08-12 row is judged RED (the persistence "
              "cure regressed — the watcher names it)",
              ctx["verdict"] == "RED" and ctx["flags_persisted"] is False
              and any("Oct-7" in s or "6287251" in s for s in ctx["why"]),
              f"verdict={ctx['verdict']} why={ctx['why'][:1]}")

        install(make_state([row(empty_fire=None, capped_zero_zero=None,
                                score=100.0)]))
        ctx = run(make_state([row(empty_fire=None, capped_zero_zero=None,
                                  score=100.0)]))
        check("1.2 a flagless row with a PERFECT score is still RED "
              "(a beautiful number can't buy a green)",
              ctx["verdict"] == "RED", f"verdict={ctx['verdict']}")

        # 1.3 the plate on a flagless row grades by score alone — the
        # exact silent rot the watcher's RED exists to catch
        # (bool(None) is False, so neither the SILENT nor the ZERO
        # grade can render)
        g = w.plate_grade({"score": 100.0, "empty_fire": None,
                           "capped_zero_zero": None})
        check("1.3 plate_grade on a flagless row renders SNIPER (the "
              "flagless blindness is plate-deep too — pinned so the RED "
              "stays honest)",
              g[0].startswith("SNIPER") and g[3] is False and g[4] is False,
              str(g[:3]))

        # ── 2. THE MISSED SHAPE — the guard saw zero-zero, stayed silent
        state = make_state([row(popups=[{"pair": "X/USD"}],
                                realized_pnl=0.0, total_left=0.0,
                                capped_zero_zero=False)])
        install(state)
        ctx = run(state)
        check("2.1 a zero-zero shape with capped_zero_zero=False is "
              "flagged as a guard MISS (AMBER — flags persisted, the "
              "cap lied)",
              ctx["verdict"] == "AMBER"
              and any("MISSED" in s for s in ctx["why"])
              and ctx["shape"].startswith("ZERO-ZERO"),
              f"verdict={ctx['verdict']} shape={ctx['shape']}")

        # ── 3. THE CAPS THAT DIDN'T BIND ──
        state = make_state([row(popups=[{"pair": "X/USD"}],
                                realized_pnl=0.0, total_left=0.0,
                                capped_zero_zero=True, score=80.0)])
        install(state)
        ctx = run(state)
        check("3.1 capped_zero_zero=True with score 80 > 55 is RED (the "
              "cap did not bind)",
              ctx["verdict"] == "RED"
              and any("did not bind" in s for s in ctx["why"]),
              f"verdict={ctx['verdict']}")

        state = make_state([row(empty_fire=True, score=90.0)])
        install(state)
        ctx = run(state)
        check("3.2 empty_fire=True with score 90 > 60 is RED (the silent "
              "cap did not bind)",
              ctx["verdict"] == "RED"
              and any("did not bind" in s for s in ctx["why"]),
              f"verdict={ctx['verdict']}")

        # ── 4. THE GREEN PATHS — the watcher stays quiet when healthy
        state = make_state(
            [row(popups=[{"pair": "X/USD"}], realized_pnl=0.0,
                 total_left=0.0, capped_zero_zero=True, score=55.0,
                 verdict="zz lesson rides the row")],
            lessons=[{"ts": row()["ts"],
                      "lesson": "zz lesson rides the row"}])
        install(state)
        ctx = run(state)
        check("4.1 a well-formed zero-zero row (capped=True, score 55, "
              "the verdict riding the row AND the log at the same ts) "
              "is GREEN with a matching lessons log",
              ctx["verdict"] == "GREEN" and ctx["lesson_log_matches"] is True
              and ctx["shape"].startswith("ZERO-ZERO"),
              f"verdict={ctx['verdict']} match={ctx['lesson_log_matches']}")

        state = make_state(
            [row(popups=[{"pair": "X/USD"}], realized_pnl=0.0,
                 total_left=0.0, capped_zero_zero=True, score=55.0,
                 verdict="the row's own words")],
            lessons=[{"ts": row()["ts"],
                      "lesson": "a DIFFERENT lesson the log carries"}])
        install(state)
        ctx = run(state)
        check("4.4 a verdict/log divergence is surfaced (matches=False) "
              "without flipping the verdict — the report names it",
              ctx["lesson_log_matches"] is False
              and ctx["verdict"] == "GREEN",
              f"match={ctx['lesson_log_matches']} verdict={ctx['verdict']}")

        state = make_state([row(popups=[{"pair": "X/USD"}],
                                realized_pnl=50.0, total_left=0.0)])
        install(state)
        ctx = run(state)
        check("4.2 a firing row with realized money is GREEN (no cap "
              "owns an honest win)",
              ctx["verdict"] == "GREEN"
              and ctx["shape"].startswith("neither silence"),
              f"verdict={ctx['verdict']} shape={ctx['shape']}")

        state = make_state([row(empty_fire=True, score=50.0)])
        install(state)
        ctx = run(state)
        check("4.3 a silent row under the 60 cap is GREEN (the "
              "empty-fire cap holds without binding)",
              ctx["verdict"] == "GREEN"
              and ctx["shape"] == "SILENT (no popups fired)",
              f"verdict={ctx['verdict']} shape={ctx['shape']}")

        # ── 5. ANTI-VACUITY — the drive proves it tested the real thing
        state = make_state([row(ts="2026-10-09T12:00:40-06:00")])
        install(state)
        ctx = run(state)
        row_v = w.evaluate(dry=False)[0]
        check("5.1 the injected row IS the row the evaluator judged "
              "(ts + row_found — no real-file leakage)",
              ctx["row_found"] is True and row_v is not None
              and ctx["row"]["ts"] == "2026-10-09T12:00:40-06:00"
              and ctx["row"]["date"] == TARGET_DATE,
              str(ctx["row"]))
    finally:
        w.jload = _REAL_JLOAD   # the module dies with the process, but
                                # the restore keeps the shape honest

    passed, failed = results.count(True), results.count(False)
    print(f"RESULT: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
