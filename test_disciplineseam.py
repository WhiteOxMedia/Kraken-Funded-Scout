"""Test THE DISCIPLINE SEAM SEWN BOTH SIDES (Sep 10): the discipline
journal (per-gate burial ledger) is written every rotation and the
gates OPEN it — gate_relief() relaxes the popup floor for gates that
bury PAYING candidates, the tide-out gate releases ONE champion-grade
candidate per rotation, and _learn() stings on burial pile-ups even
when the rotation realized $0. Hindsight flows: bury payers → the
journal thickens → the gate eases; bury duds → the relief claws back;
a silent gate → no relief. Fail-closed everywhere.

Run: .venv\\Scripts\\python.exe test_disciplineseam.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import discipline as dc
import background_scanner as bs
import tempfile, json, os

# ── 1. the ledger: payers and duds recorded PER GATE ──────────────────
with tempfile.TemporaryDirectory() as _td:
    # sandbox swap (testleaks triage, Sep 23): nothing production is ever
    # written through this pointer — the block's only save goes to the
    # sandbox, so the suite-per-process exit already bounds the window.
    dc.STATE_FILE = Path(_td) / "discipline_state.json"
    st = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
    # the ts is NOW (the floor's relief read uses the real clock — a
    # fixed 12:00 stamp decays to zero after 20:00 local and flakes)
    import datetime as _dtn
    _now_ts = _dtn.datetime.now(dc.MST).isoformat()
    res = {"date": _now_ts[:10], "rot": "12-16", "ts": _now_ts,
           "system_pairs": [{"pair": "STX/USD", "gate": "tide-out", "usd": 73.0},
                            {"pair": "ADA/USD", "gate": "tide-out", "usd": 23.0},
                            {"pair": "PUMP/USD", "gate": "brain-floor", "usd": 22.0}],
           "system_duds": [{"pair": "MOODENG/USD", "gate": "tide-out"}]}
    dc.record_burial_ledger(st, res)
    led = st.get("burial_ledger") or []
    check("the ledger is written in the state (no second save — no clobber)",
          len(led) == 1 and st is st)
    g = led[0]["gates"]
    check("tide-out: 2 payers, $96 theoretical",
          g.get("tide-out", {}).get("n") == 2
          and abs(g.get("tide-out", {}).get("usd", 0) - 96.0) < 0.01, g)
    check("brain-floor: 1 payer, $22", g.get("brain-floor") == {"n": 1, "usd": 22.0})
    check("the dud is recorded against its gate",
          led[0].get("duds", {}).get("tide-out") == 1)
    # re-evaluation of the same rotation REPLACES (no double count)
    dc.record_burial_ledger(st, res)
    check("a re-evaluation replaces the rotation's entry (no double count)",
          len(st["burial_ledger"]) == 1)
    # gate_relief reads the JOURNAL FROM DISK — persist before relief checks
    dc.STATE_FILE = Path(_td) / "discipline_state.json"
    dc.save_state(st)

    # ── 2. gate_relief: payers open, duds claw back, decay fades ──────
    # (now pinned to the entry ts — no real-clock decay in the assertions)
    import datetime as _dt
    _noon = _dt.datetime(2026, 9, 10, 12, 0, tzinfo=dc.MST)
    r = dc.gate_relief("tide-out", now=_noon)
    check("tide-out relief: 2 payers − 1 dud×0.5 = 1.5 pts (weight 1.0)",
          abs(r - 1.5) < 0.01, r)
    check("brain-floor relief: 1 pt",
          abs(dc.gate_relief("brain-floor", now=_noon) - 1.0) < 0.01)
    check("a gate with no record gets nothing",
          dc.gate_relief("deep-veto", now=_noon) == 0.0)
    # a big pile-up is CAPPED (6 fresh payers → uncapped would be 6)
    for i in range(6):
        res2 = dict(res, rot="08-12",
                    date=f"2026-09-{10 - i:02d}",
                    ts="2026-09-10T12:00:00-06:00",
                    system_pairs=[{"pair": f"P{i}/USD", "gate": "too-thin", "usd": 10.0}],
                    system_duds=[])
        dc.record_burial_ledger(st, res2)
    dc.save_state(st)
    check("the relief never exceeds the 4-pt cap",
          dc.gate_relief("too-thin", now=_noon) == dc.SEAM_RELIEF_MAX_PTS,
          dc.gate_relief("too-thin", now=_noon))
    # decay: judged 11h AFTER the last entry → past the 8h fade → zero
    future = _dt.datetime(2026, 9, 10, 23, 0, tzinfo=dc.MST)
    check("a stale journal fades to zero (8h decay)",
          dc.gate_relief("too-thin", now=future) == 0.0,
          dc.gate_relief("too-thin", now=future))

    # ── 3. _learn: the pile-up stings at $0 realized ──────────────────
    st2 = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
    res_pile = {"score": 50.0, "realized_pnl": 0.0, "empty_fire": True,
                "pace_share": 48.0, "ts": "2026-09-10T12:00:00-06:00",
                "usd_left": {"missed": 0, "giveback": 0, "missed_exit": 0,
                             "early_exit": 0, "system_burial": 118.35},
                "system_pairs": [{"pair": "STX/USD", "gate": "tide-out", "usd": 73.0},
                                 {"pair": "ADA/USD", "gate": "tide-out", "usd": 23.0},
                                 {"pair": "PUMP/USD", "gate": "brain-floor", "usd": 22.0}],
                "system_duds": [], "top_gate": "tide-out"}
    lesson = dc._learn(st2, res_pile)
    check("3 buried payers at $0 realized → system_burial rises",
          st2["weights"]["system_burial"] > dc.DEFAULT_WEIGHTS["system_burial"],
          st2["weights"]["system_burial"])
    check("the lesson names the gates", "tide-out" in lesson, lesson[:80])
    # duds shrink the pile-up: net < 2 → no sting
    st3 = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
    res_net = dict(res_pile, system_duds=[{"pair": "X/USD", "gate": "tide-out"},
                                          {"pair": "Y/USD", "gate": "tide-out"}])
    dc._learn(st3, res_net)
    check("buried duds offset the pile-up (net 1 < 2 → weights hold)",
          st3["weights"]["system_burial"] == dc.DEFAULT_WEIGHTS["system_burial"])

    # ── 4. the scanner side: the floor opens by the journal ───────────
    # (a clean journal: the original 3-gate entry only — the cap-test
    # entries above would crowd the 5-entry recency window)
    st["burial_ledger"] = [st["burial_ledger"][0]]
    dc.save_state(st)
    base = bs.popup_floor_for_rotation("08-12")   # 84
    fl = bs.popup_floor("FLAT", 0, "08-12")
    check("the popup floor takes the journal relief (84 − ~1 brain-floor pt)",
          base - dc.SEAM_RELIEF_MAX_PTS <= fl < base, (base, fl))
    check("the floor never breaches the deep-read bound (≥ 60)",
          fl >= 60.0)
    try:
        bs.popup_floor("FLAT", 0, "08-12")
        check("the QC and the alert read the same relieved stack (one helper)", True)
    except Exception as _e:
        check("the QC and the alert read the same relieved stack (one helper)", False, _e)

    # ── 5. the tide-out release: champion-grade, UNCOUNTED (Sep 10: the
    # user removed the once-per-rotation cap — every champion passes) ───
    class _Deep:
        action, confidence = "buy", 86.0
    class _DeepWeak:
        action, confidence = "buy", 70.0
    class _DeepSell:
        action, confidence = "sell", 95.0
    journal_on = dc.gate_relief("tide-out") > 0
    rel, why = bs._seam_releases_tide_out(94.0, None, "ETH/USD",
                                          deep_fetcher=lambda: _Deep())
    check(f"journal {'open' if journal_on else 'closed'}: a 94-quick champion "
          f"{'RELEASES' if rel else 'stays buried'} — {why}",
          rel == journal_on)
    if journal_on:
        check("a sub-84 brain does not champion the release",
              not bs._seam_releases_tide_out(94.0, _DeepWeak(), "ETH/USD")[0])
        check("a SELL deep read never releases",
              not bs._seam_releases_tide_out(94.0, _DeepSell(), "ETH/USD")[0])
        check("a sub-92 quick is not champion-grade",
              not bs._seam_releases_tide_out(88.0, None, "ETH/USD",
                                             deep_fetcher=lambda: _Deep())[0])
        # the cap is GONE (Sep 10): the second champion of the same
        # rotation releases too — champions are never rationed
        rel2, why2 = bs._seam_releases_tide_out(94.0, None, "HBAR/USD",
                                                deep_fetcher=lambda: _Deep())
        check("the second champion of the same rotation also releases (uncapped)",
              rel2 and "journal opened" in why2, why2)
    # fail-closed: a broken journal opens nothing
    _bad = Path(_td) / "broken.json"
    _bad.write_text("{not json", encoding="utf-8")
    _real = dc.STATE_FILE
    try:
        dc.STATE_FILE = _bad
        check("a broken journal → gate_relief 0 (fail-closed)",
              dc.gate_relief("tide-out") == 0.0)
        check("a broken journal → the release refuses",
              not bs._seam_releases_tide_out(100.0, _Deep(), "X/USD")[0])
    finally:
        dc.STATE_FILE = _real

# ── 6. the result contract: the ledger rides the persisted state ──────
src = Path("discipline.py").read_text(encoding="utf-8")
check("evaluate_rotation records the ledger BEFORE the save",
      src.index("record_burial_ledger(state") < src.index("save_state(state)"))
check("the ledger rides the SAME save as the history (no clobber window)",
      "record_burial_ledger(state, result)" in src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
out = Path("_disciplineseam_test.txt")
out.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print("DONE")
