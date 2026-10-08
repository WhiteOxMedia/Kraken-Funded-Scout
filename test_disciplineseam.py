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
import re
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
    check("tide-out relief: 2 x 1.5 - 0.5 = 2.5 pts (the dial: 2P/1D prices 1.5)",
          abs(r - 2.5) < 0.01, r)
    check("brain-floor relief: 1 x 2.0 = 2.0 pts (the dial: all-payer prices 2.0)",
          abs(dc.gate_relief("brain-floor", now=_noon) - 2.0) < 0.01)
    check("a gate with no record gets nothing",
          dc.gate_relief("deep-veto", now=_noon) == 0.0)
    # -- 2b. THE PER-GATE SENSITIVITY DIAL (the burial-cap study, Oct 6:
    # sens = 0.5 + 1.5*P/(P+D) over the 30-rotation ledger, band
    # 0.5..2.0) - relief flows through the journal's OWN payer/dud
    # ratio, not the learned scalar: the weight sat saturated at 2.5
    # (nudged there by an empty-fire branch, two lying "rises 2.50->2.50"
    # lessons) while the consumer's 4.0 cap left 34% of live relief
    # states in the dead zone. A gate that buries mostly payers eases
    # more per burial; a dud-heavy gate damps itself; the scalar retires.
    check("the dial helper: tide-out 2P/1D prices sens 1.5",
          abs(dc._gate_sens("tide-out") - 1.5) < 0.001, dc._gate_sens("tide-out"))
    check("an all-payer gate prices at the 2.0 top of the band",
          abs(dc._gate_sens("brain-floor") - 2.0) < 0.001, dc._gate_sens("brain-floor"))
    check("an unseen gate prices at the 0.5 floor",
          abs(dc._gate_sens("deep-veto") - 0.5) < 0.001, dc._gate_sens("deep-veto"))
    st["weights"]["system_burial"] = 0.25   # absurd on purpose: the dial must not care
    dc.save_state(st)
    check("the relief flows through the dial, never the scalar (1 payer x sens 2.0)",
          abs(dc.gate_relief("brain-floor", now=_noon) - 2.0) < 0.01,
          dc.gate_relief("brain-floor", now=_noon))
    check("tide-out relief: 2 x 1.5 - 0.5 = 2.5 (the dial's number, weight ignored)",
          abs(dc.gate_relief("tide-out", now=_noon) - 2.5) < 0.01,
          dc.gate_relief("tide-out", now=_noon))
    # THE ONE-SNAPSHOT CONTRACT (the Copilot medium, Oct 6): the dial
    # rides the ledger gate_relief already loaded - one disk read per
    # relief check, one ratio for the whole window, no snapshot mixing
    # if the journal updates mid-check.
    _loads = {"n": 0}
    _real_load = dc.load_state
    def _counting_load():
        _loads["n"] += 1
        return _real_load()
    dc.load_state = _counting_load
    try:
        _r_dial = dc.gate_relief("brain-floor", now=_noon)
    finally:
        dc.load_state = _real_load
    check("the relief prices from ONE snapshot (one load_state per check)",
          _loads["n"] == 1 and abs(_r_dial - 2.0) < 0.01, (_loads["n"], _r_dial))
    # THE STUDY'S OWN SHAPE (deep-veto 6P/28D): the dud-heavy gate damps
    # itself - sens 0.765, the window's own duds claw the rest to zero.
    dc.STATE_FILE = Path(_td) / "dial_damp_state.json"
    dc.save_state({"weights": {"system_burial": 2.5},
                   "burial_ledger": [
        {"date": "2026-09-10", "rot": "08-12", "ts": "2026-09-10T12:00:00-06:00",
         "gates": {"deep-veto": {"n": 5, "usd": 60.0}}, "duds": {"deep-veto": 24}},
        {"date": "2026-09-10", "rot": "12-16", "ts": "2026-09-10T12:00:00-06:00",
         "gates": {"deep-veto": {"n": 1, "usd": 12.0}}, "duds": {"deep-veto": 4}}]})
    _sens_dv = dc._gate_sens("deep-veto")
    check("a dud-heavy gate damps itself (6P/28D prices 0.765, relief clawed to zero)",
          abs(_sens_dv - 0.765) < 0.001 and dc.gate_relief("deep-veto", now=_noon) == 0.0,
          (_sens_dv, dc.gate_relief("deep-veto", now=_noon)))
    dc.STATE_FILE = Path(_td) / "discipline_state.json"

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
    check("3 buried payers at $0 realized -> the scalar RETIRES (the weight holds)",
          st2["weights"]["system_burial"] == dc.DEFAULT_WEIGHTS["system_burial"],
          st2["weights"]["system_burial"])
    check("the lesson speaks the journal, never a rise (the lying lesson dies)",
          "weight rises" not in lesson and "dial" in lesson.lower(), lesson[:120])
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
      "is MEASURED — payers x payer-rate, per-rotation 2.0 cap — and "
      "the dock is keyed by the EVALUATED rotation (the boundary "
      "fires at 16:00 ABOUT 12-16; the review's follow-up)",
      "dock_silent_rotation(" in src
      and "n_payers=empty_payers" in src
      and "rot_key=rot_key" in src
      and "rot_key: str | None = None" in src_ml
      and "rot_key or _rot_key_of(when)" in src_ml
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

# ── 7.15 THE FIRING-ROTATION COVERAGE (the isolated review's critical
# on 1ea87fd, Sep 28: worst_gate was assigned only inside the empty
# branch but read unconditionally in the result dict — a FIRING
# rotation would have NameError'd the whole evaluation into the
# scanner's debug-swallow. The drive's earlier calls all exercised the
# silent branch, so the crash class survived the first landing.)
# Hermetic shape: module file-constants re-pinned to the sandbox, a
# stubbed winners db + contest account, a NULL exchange (the mover
# path fail-closes to duds), and a rotation window holding a FIRED
# voice line so empty_fire is False.
with tempfile.TemporaryDirectory() as _td7f:
    _keep = (dc.STATE_FILE, dc.SUPPRESSION_FILE, dc.VOICE_FILE)
    _sv, _sp, _sf = (Path(_td7f) / "voice.jsonl",
                     Path(_td7f) / "supp.jsonl",
                     Path(_td7f) / "trades.jsonl")
    try:
        dc.STATE_FILE = Path(_td7f) / "disc.json"
        dc.save_state({"weights": dict(dc.DEFAULT_WEIGHTS),
                       "history": [], "lessons": []})
        dc.SUPPRESSION_FILE = _sp
        _sp.write_text("", encoding="utf-8")
        dc.VOICE_FILE = _sv
        _now = dc._now()
        _win_start = _now.replace(hour=12, minute=0, second=0, microsecond=0)
        _sv.write_text(json.dumps({
            "ts": (_win_start + __import__("datetime").timedelta(minutes=30)
                   ).isoformat(), "text": "Buy STX! 91 percent sure!"})
            + "\n", encoding="utf-8")
        _sold_fake = _win_start + __import__("datetime").timedelta(hours=1)
        _sf.write_text(json.dumps({
            "pair": "STX/USD", "closed_at": _sold_fake.isoformat(),
            "entry": 1.0, "exit": 1.02, "size": 100.0, "pnl_net": 2.0,
            "goal_pct": 1.5, "high": 1.025}) + "\n", encoding="utf-8")
        _sold_real = dc._sold_in_rotation
        dc._sold_in_rotation = lambda s, e: json.loads(
            _sf.read_text(encoding="utf-8").splitlines()[0]) and \
            [json.loads(_sf.read_text(encoding="utf-8").splitlines()[0])]
        _ca = __import__("contest_account")
        _ca_real = _ca.load_state
        _ca.load_state = lambda: {"current_balance": 10000.0,
                                  "start_balance": 10000.0}
        _db = __import__("db")
        _db_real = _db.get_tournament_winners
        _db.get_tournament_winners = lambda limit=200: []
        try:
            _res = dc.evaluate_rotation(None, "12-16", when=_now,
                                        speak=False,
                                        watch_file=str(_td7f + "/watch.json"))
            check("7.15 a FIRING rotation evaluates without crashing and "
                  "carries worst_gate=None (the unbound-name critical "
                  "cannot return)",
                  _res.get("score") is not None
                  and _res.get("empty_fire") is False
                  and _res.get("worst_gate", "MISSING") is None
                  and "worst_gate" in _res, _res.get("error"))
        finally:
            dc._sold_in_rotation = _sold_real
            _ca.load_state = _ca_real
            _db.get_tournament_winners = _db_real
    finally:
        dc.STATE_FILE, dc.SUPPRESSION_FILE, dc.VOICE_FILE = _keep
check("7.16 the init is OUTSIDE the empty branch (the source freeze)",
      re.search(r"worst_gate = None\s*#", src) is not None
      and src.index("worst_gate = None") < src.index("if empty_fire:"))

# ── 7.17-7.19 THE RIGHT-CENSORING RULE (Oct 1, the operator's confirm:
# a burial evaluated MINUTES before the boundary — the coin test's 1h
# candles gave it almost no horizon — was counted as a DUD ("the gate
# was right"), docking NOTHING on the day the machine buried its own
# money: the dock's payer-rate math and the honest-drought reason both
# read censoring as acquittal. The rule: a burial with LESS than the
# payer horizon (3h, the payers' own median time-to-goal band) in the
# window is UNJUDGED — it rides system_pending (visible in the why, kept
# in the record), never a dud, and the dock prices only the MEASURED
# burials. Conservative direction: the machine never acquits itself on
# right-censored evidence; the info surfaces instead of docking.
def _silent_eval(supp_rows):
    """evaluate_rotation over a silent window with the given burial rows
    (ts-aware, in-window); returns (result, dock_calls)."""
    _keep2 = (dc.STATE_FILE, dc.SUPPRESSION_FILE, dc.VOICE_FILE)
    with tempfile.TemporaryDirectory() as _td7g:
        _sv2, _sp2 = (Path(_td7g) / "voice.jsonl",
                      Path(_td7g) / "supp.jsonl")
        try:
            dc.STATE_FILE = Path(_td7g) / "disc.json"
            dc.save_state({"weights": dict(dc.DEFAULT_WEIGHTS),
                           "history": [], "lessons": []})
            dc.SUPPRESSION_FILE = _sp2
            _sp2.write_text(
                "\n".join(json.dumps(r) for r in supp_rows) + "\n",
                encoding="utf-8")
            dc.VOICE_FILE = _sv2
            _sv2.write_text("", encoding="utf-8")
            _now2 = dc._now()
            _ws = _now2.replace(hour=12, minute=0, second=0, microsecond=0)
            _we = _ws + __import__("datetime").timedelta(hours=4)
            _ca2 = __import__("contest_account")
            _ca2_real = _ca2.load_state
            _ca2.load_state = lambda: {"current_balance": 10000.0,
                                       "start_balance": 10000.0}
            _db2 = __import__("db")
            _db2_real = _db2.get_tournament_winners
            _db2.get_tournament_winners = lambda limit=200: []
            import meta_layers as _ml7
            _dock_real = _ml7.dock_silent_rotation
            _dock_calls = []
            _ml7.dock_silent_rotation = (
                lambda n, when=None, n_payers=0, rot_key=None:
                (_dock_calls.append((n, n_payers, rot_key))
                 or {"docked": 0.0, "reason": "spy"}))
            try:
                _r = dc.evaluate_rotation(None, "12-16", when=_now2,
                                          speak=False,
                                          watch_file=str(_td7g + "/w.json"))
                _st_persisted = json.loads(
                    dc.STATE_FILE.read_text(encoding="utf-8"))
            finally:
                _ml7.dock_silent_rotation = _dock_real
                _ca2.load_state = _ca2_real
                _db2.get_tournament_winners = _db2_real
            return _r, _dock_calls, _st_persisted
    # (finally blocks above restore every rebind)
        finally:
            dc.STATE_FILE, dc.SUPPRESSION_FILE, dc.VOICE_FILE = _keep2

_ts_a = None   # the CENSORED burial: 1h of horizon at the boundary
_ts_b = None   # the JUDGED burial: 3.5h of horizon
with tempfile.TemporaryDirectory():
    pass
_now7 = dc._now()
_ws7 = _now7.replace(hour=12, minute=0, second=0, microsecond=0)
_we7 = _ws7 + __import__("datetime").timedelta(hours=4)
from datetime import timedelta as _td7
_ts_a = (_we7 - _td7(hours=1)).isoformat()
_ts_b = (_we7 - _td7(hours=3, minutes=30)).isoformat()
_res7, _calls7, _st7 = _silent_eval([
    {"ts": _ts_a, "pair": "LDO/USD", "gate": "tide-out",
     "quick": 69.0},
    {"ts": _ts_b, "pair": "ARB/USD", "gate": "deep-veto",
     "quick": 55.0},
])
check("7.17 the censored burial is PENDING, not a dud (1h of horizon "
      "cannot acquit the gate); the 3.5h burial stays judged",
      _res7.get("empty_fire") is True
      and {d["pair"] for d in _res7.get("system_duds") or []} == {"ARB/USD"}
      and {d["pair"] for d in _res7.get("system_pending") or []} == {"LDO/USD"}
      and _res7.get("empty_pending") == 1, _res7.get("empty_why"))
check("7.18 the dock prices only the MEASURED burials (the spy saw "
      "n_buried=1, not 2 — the censored row never feeds the payer-rate "
      "math)",
      len(_calls7) == 1 and _calls7[0][0] == 1, _calls7)
check("7.19 the WHY names the unjudged row (the operator sees the "
      "censoring, it never vanishes into 'the gate was right')",
      "unjudged" in str(_res7.get("empty_why"))
      and "3h" in str(_res7.get("empty_why")), _res7.get("empty_why"))
_res8, _calls8, _st8 = _silent_eval([   # 7.20's call (the all-pending case)
    {"ts": (_we7 - _td7(minutes=30)).isoformat(), "pair": "S/USD",
     "gate": "tide-out", "quick": 66.0},
])
check("7.20 an ALL-PENDING silent rotation docks NOTHING (the honest "
      "drought reason is for MEASURED burials only — 0 measured = 0 "
      "dock) and says so plainly",
      _res8.get("empty_pending") == 1
      and len(_calls8) == 1 and _calls8[0][0] == 0
      and "unjudged" in str(_res8.get("empty_why")),
      (_calls8, _res8.get("empty_why")))
check("7.21 the horizon constant is code and the record keeps the "
      "pendings (the history entry cannot drop them)",
      "BURIAL_HORIZON_HOURS = 3.0" in src
      and '"system_pending": system_pending' in src
      and '"empty_pending": empty_pending' in src
      and 'empty_buried - empty_pending' in src)
_h7 = (_st7.get("history") or [{}])[-1]
check("7.22 the PERSISTED history keeps the unjudged rows (the review's "
      "medium: a result-dict field the _entry never copied dies at the "
      "boundary — the censoring evidence must outlive the call)",
      _h7.get("empty_pending") == 1
      and [p.get("pair") for p in _h7.get("system_pending") or []]
      == ["LDO/USD"], _h7.get("system_pending"))
check("7.23 the all-pending case persists its single unjudged row "
      "(S/USD rides the saved history too)",
      _st8 and ([p.get("pair") for p in
                 ((_st8.get("history") or [{}])[-1]
                  .get("system_pending") or [])] == ["S/USD"]))
# 7.24 (the review's medium, the DETECT-FIRST refinement): censoring
# makes NO-evidence unjudgable — it never discounts DETECTED evidence.
# A near-boundary burial whose candles DID reach goal must dock (it is
# a payer the machine buried), never hide behind pending; the test
# runs BEFORE the horizon classifies.
_res9, _calls9, _st9 = None, None, None
class _PayExchange:
    """The 1h candles say the goal high happened within the burial's
    own hour (a DETECTED payer despite the tiny horizon)."""
    def fetch_ohlcv(self, pair, timeframe="1h", since=None, limit=10):
        _t = _we7 - _td7(hours=0, minutes=30)
        return [[int(_t.timestamp() * 1000), 1.0, 1.02, 0.99, 1.01, 1.0]]
    def fetch_ohlcv_ms(self, *a, **k):
        return []
import types as _types
_entry_at_real = dc._entry_at
dc._entry_at = lambda ex, pair, ts: 1.0
_coin_real = dc._coin_paid
dc._coin_paid = lambda ex, pair, entry, goal, alert, wend: True
try:
    _res9, _calls9, _st9 = _silent_eval([
        {"ts": _ts_a, "pair": "NEAR/USD", "gate": "tide-out",
         "quick": 69.0},
    ])
finally:
    dc._entry_at = _entry_at_real
    dc._coin_paid = _coin_real
check("7.24 a DETECTED near-boundary payer DOCKS (test first, classify "
      "second — censoring never discounts real evidence)",
      len(_calls9) == 1 and _calls9[0] == (1, 1, "12-16")
      and {p["pair"] for p in _res9.get("system_pairs") or []}
      == {"NEAR/USD"} and not (_res9.get("system_pending") or []),
      (_calls9, _res9.get("system_pending")))

# ── 8. THE FORGIVENESS TERM (user ask, Oct 8 — "Tripwire B, red-first:
# the score should forgive once the gates have widened and paid for the
# lesson"): the dock stings the WHOLE book via discipline_penalty_extra
# (disc_mod = 1 - penalty/10 at ~5 GF discipline points per penalty
# point), but the family's OTHER half — the journal — prices relief
# per gate from the SAME burials (gate_relief, sens dial, dud clawback,
# 8h decay, cap 4.0). The two halves never met: a penalty that nears
# the 3.0 rail while the gates are actively widening and RELEASING
# payers is a lesson that healed but never stopped billing. The term:
#   forgive_on_paid_relief(min_pts, total, ml, now, state) — what the
#   RELIEF already proved: capped, clamped to (0, penalty), keys
#   stamped (silent_forgave = rot@date — audit-visible, re-forgivable
#   next day), SELF ( følger the journal's dud clawback: relief <
#   min_pts forgives NOTHING — the lesson healed only when paid).
# RED-FIRST: 8.1-8.5 fail until the machinery exists in meta_layers.
import meta_layers as _ml8   # (already imported as ml in section 7)

with tempfile.TemporaryDirectory() as _td8:
    _real_state8 = ml.STATE_FILE
    ml.STATE_FILE = Path(_td8) / "meta_state8.json"
    try:
        st8 = ml._state()
        st8["discipline_penalty_extra"] = 1.0
        ml._save(st8)
        # 8.1 the forgiveness is THE ASK (min_pts = what the lesson
        # costs in relief terms — the gates crossed it, that much of
        # the bill comes back; the RELIEF is the gates' side, the ASK
        # is the lesson's price — one number, one meaning)
        f1 = ml.forgive_on_paid_relief(min_pts=0.5, total=1.0, ml=ml,
                                       now=dc._now(), state=st8)
        check("8.1 the relief-paid lesson FORGIVES THE ASK (min_pts "
              "0.5 crossed on a 1.0 penalty = 0.5 back — the gates "
              "widened and paid for the lesson's price)",
              f1.get("forgiven") == 0.5
              and ml.discipline_penalty_extra() == 0.5, f1)
        # 8.2 fresh key, fresh day: the clamp still holds (a 3.0
        # relief still forgives only the ASK — 0.5, not the relief)
        st8 = ml._state()
        st8["discipline_penalty_extra"] = 0.5
        ml._save(st8)
        f2 = ml.forgive_on_paid_relief(min_pts=0.5, total=3.0, ml=ml,
                                       now=dc._now(), state=st8,
                                       rot_key="16-20", date_key="2026-10-08")
        check("8.2 the forgiveness CLAMPS to the ASK (3.0 relief "
              "forgives 0.5 — the lesson's price, not the gates' "
              "whole relief)",
              f2.get("forgiven") == 0.5
              and ml.discipline_penalty_extra() == 0.0, f2)
        # 8.3 dud-heavy gates get NOTHING (the lesson healed only
        # when PAID): relief under the min_pts forgives zero
        st8 = ml._state()
        st8["discipline_penalty_extra"] = 1.0
        ml._save(st8)
        f3 = ml.forgive_on_paid_relief(min_pts=2.0, total=1.0, ml=ml,
                                       now=dc._now(), state=st8)
        check("8.3 relief BELOW min_pts forgives NOTHING (the gate "
              "has not yet paid for the lesson — dud clawback holds)",
              f3.get("forgiven") == 0.0
              and ml.discipline_penalty_extra() == 1.0, f3)
        # 8.4 the KEY is stamped and re-inkable: same key does not
        # re-forgive (the dedup side of the family), a DAY-later key does
        st8 = ml._state()
        st8["discipline_penalty_extra"] = 1.0
        ml._save(st8)
        f4 = ml.forgive_on_paid_relief(min_pts=0.5, total=1.0, ml=ml,
                                       now=dc._now(), state=st8,
                                       rot_key="12-16", date_key="2026-10-08")
        f4b = ml.forgive_on_paid_relief(min_pts=0.5, total=1.0, ml=ml,
                                        now=dc._now(), state=st8,
                                        rot_key="12-16", date_key="2026-10-08")
        check("8.4 the forgiveness is keyed per (date, rot) on the "
              "record (same key cannot re-forgive; silent_forgaved "
              "carries both for the audit)",
              f4.get("forgiven") == 0.5 and f4b.get("forgiven") == 0.0
              and (ml._state().get("silent_forgaved") or {}).get("rot")
              == "12-16"
              and (ml._state().get("silent_forgaved") or {}).get("date")
              == "2026-10-08", (f4, f4b))
        # 8.5 the MANDATE (the ask crossed, the penalty gone): the
        # forgiveness is EXACTLY the ask, not a grain more (the bill
        # comes back to the lesson's price, the rest of the relief
        # stays where it is — the relief belongs to the gates)
        st8 = ml._state()
        st8["discipline_penalty_extra"] = 2.0
        ml._save(st8)
        f5 = ml.forgive_on_paid_relief(min_pts=0.7, total=2.5, ml=ml,
                                       now=dc._now(), state=st8,
                                       rot_key="20-24", date_key="2026-10-08")
        check("8.5 the forgiveness is EXACTLY the ask (0.7 of 2.5 "
              "relief = 0.7 off the 2.0 penalty — the lesson's own "
              "price, the rest of the relief belongs to the gates)",
              f5.get("forgiven") == 0.7
              and ml.discipline_penalty_extra() == 1.3, f5)
    finally:
        ml.STATE_FILE = _real_state8

# 8.6-8.8 the WIRE (source contract): the evaluation reports HOW MUCH
# relief the burying gates have earned (so the forgiveness inputs are
# the journal's OWN hindsight), the boundary consults, and the tool
# rides the existing meta state — no new plane, no second st.
src_ml8 = Path("meta_layers.py").read_text(encoding="utf-8")
check("8.6 the term lives in meta_layers on the penalty key (same "
      "channel family — dock, RCL, SAL, forgiveness — one key, per-"
      "call caps, never a new plane)",
      "def forgive_on_paid_relief" in src_ml8
      and src_ml8.index("def forgive_on_paid_relief")
          > src_ml8.index("def dock_silent_rotation"))
check("8.7 the forgiveness respects the relief's own dud clawback "
      "(the min_pts ask rides gate_relief's math — the dial, the cap, "
      "the decay — not a new pricing)",
      "gate_relief" in src_ml8 or "journal_release_pts" in src_ml8)
check("8.8 the forgiveness is silent-stamped on the record (the "
      "audit trail: silent_forgaved rides the meta state, the review "
      "can see when the gates paid)",
      "silent_forgaved" in src_ml8)

# ── 8.9-8.11 THE PRODUCTION WIRE (the Copilot high + medium, cured
# in-commit): the term MUST have a live production caller — the
# cooldown review forgives on the day-learn wrap, keyed to the DAY row
# (date-only, so one forgiveness per day regardless of rotation
# count); the relief quote is the fleet's own gate_relief (the relief
# ANY burying gate currently holds), asource-pinned so the wire
# survives refactors; and the corollaries ride it too (NaN fail-
# neutral, the day-lesson text, the DAY row carrying forgave).
src_dc8 = Path("discipline.py").read_text(encoding="utf-8")
check("8.9 the cooldown review CALLS the forgiveness (the production "
      "wire: no caller = no forgiveness, the Copilot high)",
      "forgive_on_paid_relief(" in src_dc8
      and 'rot_key="DAY"' in src_dc8
      and 'min_pts=0.5' in src_dc8)
check("8.10 the relief quote is the journal's OWN gate_relief (the "
      "fleet's relief, never a new pricing; the gates-seen universe "
      "names the burying gates)",
      src_dc8.count("gate_relief(") >= 2
      and "_gates_seen" in src_dc8)
check("8.11 the DAY row speaks the forgiveness (the persisted history "
      "carries forgave — the review's plate can see when the gates "
      "paid) and the NaN quote FAILS NEUTRAL (the meta side)",
      '"forgave": _day_result.get("forgave")' in src_dc8
      and "isfinite" in src_ml8
      and "not finite" in src_ml8)
check("8.12 THE DRY-RUN GATE (the Copilot high, cured): the "
      "historical reviews (dnl_report, profit_guard) run THROUGH the "
      "cooldown review with dry_run=True — a dry run must never write "
      "meta state (a historical read could have reduced the live "
      "penalty with relief it never asked to be paid). The real "
      "review at the day boundary is the forgiver; everything else "
      "reads the lesson with the penalty world untouched",
      "if not dry_run:" in src_dc8[
          src_dc8.index("THE PAYING-GATE FORGIVENESS"):
          src_dc8.index("THE PAYING-GATE FORGIVENESS") + 4000]
      and "_mlf.forgive_on_paid_relief(" in src_dc8)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
out = Path("_disciplineseam_test.txt")
out.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print("DONE")
raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): the verdict printed, the exit never rode it
