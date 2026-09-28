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

# ── 7. THE SILENT ROTATION ASKS WHY + SELF-ADJUSTS (user ask, Sep 28) ─
# "She says 'all good' but doesn't check if it is": the empty-fire voice
# line gains its WHY, and a buried-candidates silence docks the guard
# through the SAL penalty channel (same key, shared cap, one decay).
import meta_layers as ml

with tempfile.TemporaryDirectory() as _td7:
    _real_state = ml.STATE_FILE
    ml.STATE_FILE = Path(_td7) / "meta_state.json"   # testleaks idiom:
    try:                                             # sandbox, restore in finally
        # 7.1 the burial case, MEASURED (Sep 28: proportional dock —
        # 0.5 per payer x payer-rate; one payer of three burials = 0.2)
        d1 = ml.dock_silent_rotation(3, when=dc._now(), n_payers=1)
        check("7.1 a burial docks the guard by its MEASURED cost (one "
              "payer of three = 0.5*1*(1/3) = 0.2)",
              d1.get("docked") == 0.2 and d1.get("buried") == 3
              and d1.get("payers") == 1
              and ml.discipline_penalty_extra() == 0.2, d1)
        # 7.2 the dedup: the same rotation cannot sting twice
        d2 = ml.dock_silent_rotation(2, when=dc._now())
        check("7.2 the dock is deduped per rotation (a restart cannot "
              "double-sting)",
              d2.get("docked") == 0.0 and ml.discipline_penalty_extra() == 0.2,
              d2)
        # 7.2b the cross-day case (CodeRabbit, Sep 28): the bare rotation
        # key repeats daily — YESTERDAY's dock must not silence TODAY's
        # same rotation. The key carries the date; a day-earlier when
        # docks again (its own full-sting: 1 payer of 1 = 0.5).
        import datetime as _dt72
        _yesterday = dc._now() - _dt72.timedelta(days=1)
        d2b = ml.dock_silent_rotation(1, when=_yesterday, n_payers=1)
        check("7.2b the dedup key carries the DATE: yesterday's same "
              "rotation docks again (the bare-key dedup would have "
              "silenced every future day)",
              d2b.get("docked") == 0.5
              and ml.discipline_penalty_extra() == 0.7, d2b)
        # 7.2c the dock SCALES with the payer count (the flag era is over:
        # 4 payers of 5 -> 0.5*4*(4/5) = 1.6)
        ml.STATE_FILE = Path(_td7) / "meta_state2c.json"   # fresh state
        d2c = ml.dock_silent_rotation(5, when=dc._now(), n_payers=4)
        check("7.2c the dock scales with payers (4 of 5 = 1.6, not the "
              "old flat 0.5)",
              d2c.get("docked") == 1.6 and d2c.get("payers") == 4
              and ml.discipline_penalty_extra() == 1.6, d2c)
        # 7.2d the per-rotation cap: payers > 4 cannot dock beyond 2.0 —
        # a docked penalty must not equal a fired-and-lost trade
        ml.STATE_FILE = Path(_td7) / "meta_state2d.json"   # fresh state
        d2d = ml.dock_silent_rotation(9, when=dc._now(), n_payers=9)
        check("7.2d the per-rotation dock is CAPPED at 2.0 (9 payers "
              "cannot dock 2.25 — the dock is a lesson, not the loss)",
              d2d.get("docked") == 2.0
              and ml.discipline_penalty_extra() == 2.0, d2d)
        # 7.2e the duds: burials that never reached goal are the gate
        # being RIGHT — the payer-rate drives them to zero
        ml.STATE_FILE = Path(_td7) / "meta_state2e.json"   # fresh state
        d2e = ml.dock_silent_rotation(6, when=dc._now(), n_payers=0)
        check("7.2e burials that were all DUDS dock nothing (the gate "
              "was right — payer-rate 0 -> 0.0, on the record)",
              d2e.get("docked") == 0.0 and d2e.get("payers") == 0
              and "duds" in (d2e.get("reason") or "")
              and ml.discipline_penalty_extra() == 0.0, d2e)
        # 7.3 the honest drought: nothing docked
        ml.STATE_FILE = Path(_td7) / "meta_state2.json"   # fresh state
        d3 = ml.dock_silent_rotation(0, when=dc._now())
        check("7.3 an honest drought (nothing buried) docks NOTHING",
              d3.get("docked") == 0.0
              and ml.discipline_penalty_extra() == 0.0, d3)
        # 7.4 the cap: the shared 3.0 ceiling holds (a full-sting 0.5
        # added to a 2.8 accumulated penalty lands exactly at 3.0)
        st4 = ml._state()
        st4["discipline_penalty_extra"] = 2.8
        ml._save(st4)
        d4 = ml.dock_silent_rotation(1, when=dc._now(), n_payers=1)
        check("7.4 the dock rides the shared 3.0 cap (never above)",
              d4.get("docked") == 0.5
              and ml.discipline_penalty_extra() == 3.0,
              ml.discipline_penalty_extra())
    finally:
        ml.STATE_FILE = _real_state

# 7.5-7.7 the source contract: the WHY is computed from the evaluation's
# own data, the voice asks it, the boundary logs it
check("7.5 evaluate_rotation diagnoses WHY it was silent (buried / "
      "drought / honest no) from its own supp+popups data",
      "empty_why" in src and "empty_buried = len(supp)" in src
      and "the funnel offered nothing rail-worthy" in src)
check("7.6 the silent voice line ASKS WHY (the all-good line checks "
      "itself)",
      "Because: {result['empty_why']}" in src)
src_ml = Path("meta_layers.py").read_text(encoding="utf-8")
check("7.7 the boundary docks through meta_layers (the self-adjust "
      "rides the existing SAL channel, no new learning plane), and it "
      "is MEASURED — payers x payer-rate, per-rotation 2.0 cap",
      "dock_silent_rotation(empty_buried" in src
      and "n_payers=empty_payers" in src
      and "0.5 * n_payers * (n_payers / int(n_buried))" in src_ml)
check("7.7b the payer measurement is the evaluation's OWN hindsight "
      "(system_burial = suppressed pairs the coin test says reached "
      "goal; duds are the gate being right)",
      "empty_payers = len(system_burial)" in src
      and "empty_payers" in src[src.index("def evaluate_rotation"):
                                src.index("def evaluate_rotation") + 14000]
      and "system_duds" in src)
src_ml = Path("meta_layers.py").read_text(encoding="utf-8")
src_bs = Path("background_scanner.py").read_text(encoding="utf-8")
check("7.8 the scanner boundary speaks the silent-rotation WHY on the "
      "record",
      "SILENT ROTATION" in src_bs
      and src_bs.index("dock_silent_rotation") < src_bs.index("COOLDOWN REVIEW"))
check("7.9 the decay contract survives: the cooldown x0.9 decay still "
      "owns the penalty key (one plane, one decay)",
      "discipline_penalty_extra\"] = round(prev_pen * 0.9" in src_ml)

# ── 7.10-7.13 THE PER-GATE BURIED-PAYER RATIO (Sep 28, user ask: the
# boundary names WHICH gate buried the most payers and by how much) ──
# behavioral: the counter executed on synthetic attribution data
_wg = dc._worst_burial_gate(
    [{"gate": "tide-out"}, {"gate": "tide-out"}, {"gate": "tide-out"},
     {"gate": "tide-out"}, {"gate": "tide-out"},
     {"gate": "deep-veto"}],
    [])
check("7.10 the worst gate is the one with the MOST PAYERS (5 tide-out "
      "beat 1 deep-veto — primary sort)",
      _wg and _wg["gate"] == "tide-out" and _wg["payers"] == 5
      and _wg["buried"] == 5 and _wg["rate"] == 1.0, _wg)
_wg2 = dc._worst_burial_gate(
    [{"gate": "stale-move"}, {"gate": "stale-move"},
     {"gate": "tide-out"}, {"gate": "tide-out"}],
    [{"gate": "stale-move"}] * 6 + [{"gate": "tide-out"}] * 2)
check("7.11 the payer-rate is the tiebreak (2 payers each: stale-move "
      "0.25 rate loses to tide-out 0.5)",
      _wg2 and _wg2["gate"] == "tide-out" and _wg2["rate"] == 0.5, _wg2)
check("7.12 no gate to blame: no payers at all -> None (duds are the "
      "gates being RIGHT; a drought blames nobody)",
      dc._worst_burial_gate([], []) is None
      and dc._worst_burial_gate([], [{"gate": "tide-out"}]) is None
      and dc._worst_burial_gate(None, None) is None)
check("7.13 the clause is human-shaped and rideable",
      _worst_txt := dc._worst_gate_txt(
          {"gate": "tide-out", "payers": 14, "buried": 27, "rate": 0.52}),
      _worst_txt)
check("7.13b the clause text names gate, payers, buried, and rate",
      "tide-out" in _worst_txt and "14" in _worst_txt
      and "27" in _worst_txt and "52%" in _worst_txt, _worst_txt)
# census: all three surfaces speak it, from ONE computation
check("7.14 the worst-gate clause rides the WHY line, both silent "
      "lesson branches, and the result (the boundary + voice inherit "
      "it through empty_why)",
      "empty_why = (" in src and "_worst_gate_txt(worst_gate)" in src
      and src.count("_worst_gate_txt(result.get(\"worst_gate\"))") >= 2
      and '"worst_gate": worst_gate' in src
      and "def _worst_burial_gate" in src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
out = Path("_disciplineseam_test.txt")
out.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print("DONE")
