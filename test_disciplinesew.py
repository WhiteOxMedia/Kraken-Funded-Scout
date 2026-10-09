"""Test THE SEAM (user rule, Sep 4: "sewing the suppression log to the
discipline score so it learns from it also?" — approved).

The discipline score now separates WHO left the money:
  USER misses   a popup FIRED (voice-confirmed / not suppressed), the
                coin paid, never taken → 'missed' (the user's leave)
  SYSTEM leaves a paying candidate the FUNNEL buried (the suppression
                log's gate) → 'system_burial' (the machine's leave,
                theoretical, attributed to the gate by name)
The voice record rescues suppressed-then-fired pairs (buried at 09:00,
the tide turned, fired at 10:30 → FIRED). Buried mover-path pairs count
too (entry approximated from the candle at the burial). The
system_burial weight LEARNS whether the gates are calibrated; the
insight names the gate when it dominates; the plate shows the third
column ($ by the gates, theoretical)."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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

MST = ZoneInfo("America/Denver")
src = Path("discipline.py").read_text(encoding="utf-8")
scan = Path("background_scanner.py").read_text(encoding="utf-8")
ap = Path("app.py").read_text(encoding="utf-8")

# ── 1. the seam's plumbing ────────────────────────────────────────────
check("the system_burial weight exists (bounded like the others)",
      dc.DEFAULT_WEIGHTS.get("system_burial") == 1.0)
check("the suppression reader exists",
      "def _suppressions_in_rotation" in src)
check("the fired-reader exists (the voice ground truth)",
      "def _fired_pairs" in src)
check("the suppression log cap was raised for the seam",
      "SUPPRESSION_LOG_MAX = 1000" in scan)
check("the voice log cap was raised (the fired record must span a rotation)",
      "VOICE_LOG_MAX = 400" in scan)
check("the result carries system_left + top_gate + system_pairs",
      '"system_left"' in src and '"top_gate"' in src and '"system_pairs"' in src)
check("the history entry carries system_left + top_gate (the plate reads them)",
      '"system_left",\n               "top_gate"' in src.replace(" ", "").replace("system_left", " system_left")
      or '"system_left"' in src.split("_entry = {k: result[k] for k in")[1][:200])
check("the plate shows the third column (by the gates, theoretical)",
      "by the gates (theoretical)" in ap and "user left" in ap)

# ── 2. the readers, functional ────────────────────────────────────────
import tempfile
import scratch_space   # scratch policy: nothing volatile lands in the repo root

start = datetime(2026, 9, 4, 8, tzinfo=MST)
end = start + timedelta(hours=4)

with tempfile.TemporaryDirectory() as td:
    supp_file = Path(td) / "supp.jsonl"
    supp_file.write_text("\n".join([
        json.dumps({"ts": "2026-09-04T08:29:00-06:00", "pair": "POPCAT/USD",
                    "gate": "tide-out", "detail": "fleet 20% up", "quick": 67.0}),
        json.dumps({"ts": "2026-09-04T08:46:00-06:00", "pair": "POPCAT/USD",
                    "gate": "tide-out", "detail": "fleet 20% up", "quick": 67.0}),
        json.dumps({"ts": "2026-09-04T08:57:00-06:00", "pair": "POPCAT/USD",
                    "gate": "tide-out", "detail": "fleet 20% up", "quick": 87.0}),
        json.dumps({"ts": "2026-09-04T09:30:00-06:00", "pair": "XMR/USD",
                    "gate": "brain-floor", "detail": "brain 71 < 79", "quick": 82.0}),
        json.dumps({"ts": "2026-09-04T15:00:00-06:00", "pair": "LATE/USD",
                    "gate": "tide-out", "detail": "different rotation"}),
    ]) + "\n", encoding="utf-8")
    supp = dc._suppressions_in_rotation(start, end, supp_file)
    check("12-like repeats dedupe to ONE opportunity per pair",
          supp.get("POPCAT/USD", {}).get("n") == 3 and len(supp) == 2,
          {k: v.get("n") for k, v in supp.items()})
    check("the last entry's gate is the verdict that held",
          supp.get("POPCAT/USD", {}).get("gate") == "tide-out"
          and supp.get("POPCAT/USD", {}).get("quick") == 87.0)
    check("entries outside the window are excluded",
          "LATE/USD" not in supp)

    voice_file = Path(td) / "voice.jsonl"
    voice_file.write_text("\n".join([
        json.dumps({"ts": "2026-09-04T08:15:00-06:00",
                    "text": "Hello there! My systems are online!"}),
        json.dumps({"ts": "2026-09-04T09:05:00-06:00",
                    "text": "Buy ZEC! 91 percent sure! Aim for plus 2 percent!"}),
        json.dumps({"ts": "2026-09-04T09:10:00-06:00",
                    "text": "ZEC is up 1 percent. Plus 26 dollars."}),
        json.dumps({"ts": "2026-09-04T10:30:00-06:00",
                    "text": "Buy POPCAT! 84 percent sure! A lone wolf against an out tide!"}),
        json.dumps({"ts": "2026-09-04T15:00:00-06:00",
                    "text": "Buy LATE! 90 percent sure!"}),
    ]) + "\n", encoding="utf-8")
    fired = dc._fired_pairs(start, end, voice_file)
    check("fired pairs parse from the spoken Buy lines",
          fired == {"ZEC/USD", "POPCAT/USD"}, fired)
    check("non-Buy lines and other windows don't count as fires",
          "LATE/USD" not in fired)

# ── 3. the attribution, end-to-end on a fake rotation ─────────────────
class FakeEx:
    """Candles: POPCAT pays from its burial base; XMR never pays;
    ZEC pays (it fired)."""
    def __init__(self):
        self.t = {}
        base = start + timedelta(hours=1)
        self.t["POPCAT/USD"] = [[int((base + timedelta(hours=i)).timestamp() * 1000),
                                  1, 0.05020 * 1.03, 1, 0.05020, 100] for i in range(3)]
        self.t["XMR/USD"] = [[int((base + timedelta(hours=i)).timestamp() * 1000),
                              1, 100.0, 1, 100.0, 100] for i in range(3)]
        self.t["ZEC/USD"] = [[int((base + timedelta(hours=i)).timestamp() * 1000),
                              1, 1.0, 1, 1.0, 100] for i in range(3)]
        # ZEC pays: +3% over its entry
        for i, c in enumerate(self.t["ZEC/USD"]):
            c[1] = c[4] = 1.0
            c[2] = 1.0 * 1.03
    def fetch_ohlcv(self, pair, timeframe="1h", since=None, limit=10):
        return list(self.t.get(pair, []))
    def fetch_ticker(self, pair):
        return {"last": 1.0}

popups = [
    {"pair": "POPCAT/USD", "confidence": 87.0, "entry": 0.05020,
     "ts": start + timedelta(minutes=57)},
    {"pair": "XMR/USD", "confidence": 82.0, "entry": 100.0,
     "ts": start + timedelta(minutes=90)},
    {"pair": "ZEC/USD", "confidence": 91.0, "entry": 1.0,
     "ts": start + timedelta(minutes=65)},
]

_orig = {"popups": dc._popups_in_rotation, "sold": dc._sold_in_rotation,
         "supp": dc._suppressions_in_rotation, "fired": dc._fired_pairs,
         "state": dc.STATE_FILE}
try:
    with tempfile.TemporaryDirectory() as td:
        dc.STATE_FILE = Path(td) / "state.json"
        supp_file = Path(td) / "supp.jsonl"
        supp_file.write_text("\n".join([
            json.dumps({"ts": "2026-09-04T08:57:00-06:00", "pair": "POPCAT/USD",
                        "gate": "tide-out", "quick": 87.0, "brain": 71.0}),
            json.dumps({"ts": "2026-09-04T09:30:00-06:00", "pair": "XMR/USD",
                        "gate": "brain-floor", "quick": 82.0, "brain": 71.0}),
        ]) + "\n", encoding="utf-8")
        voice_file = Path(td) / "voice.jsonl"
        voice_file.write_text(json.dumps({
            "ts": "2026-09-04T09:05:00-06:00",
            "text": "Buy ZEC! 91 percent sure!"}) + "\n", encoding="utf-8")

        dc._popups_in_rotation = lambda s, e: [dict(p) for p in popups]
        dc._sold_in_rotation = lambda s, e: []
        dc._suppressions_in_rotation = lambda s, e, log_file=None: \
            dc._suppressions_in_rotation.__wrapped__(s, e, supp_file) \
            if hasattr(dc._suppressions_in_rotation, "__wrapped__") else \
            json.loads("{}") if False else _read_supp(supp_file)
        dc._fired_pairs = lambda s, e, voice_file=None: {"ZEC/USD"}

        def _read_supp(f):
            out = {}
            for line in f.read_text(encoding="utf-8").splitlines():
                d = json.loads(line)
                out[d["pair"]] = {"gate": d["gate"],
                                  "ts": datetime.fromisoformat(d["ts"]).astimezone(MST),
                                  "quick": d.get("quick"), "brain": d.get("brain"), "n": 1}
            return out

        res = dc.evaluate_rotation(FakeEx(), "08-12",
                                   when=start + timedelta(hours=4), speak=False)
        usd = res.get("usd_left", {})
        # LIVE-DATA LESSON (Sep 4): the engine recalibrates rotation_thresholds.json
        # periodically — the goal cells DRIFTED between two runs tonight (ZEC
        # 3.0 -> 1.5, POPCAT 2.28 -> 1.14) and a hardcoded $200 assertion went
        # stale. Compute the expectations from the SAME live cells the
        # evaluation read, with a 5% tolerance for mid-test cell flips.
        goal_zec = dc._rotation_goal_pct("ZEC/USD", "08-12", "weekday")
        goal_pop = dc._rotation_goal_pct("POPCAT/USD", "08-12", "weekday")
        try:
            import contest_account as _ca_t
            _bal_t = float(_ca_t.load_state().get("current_balance") or 10000.0)
        except Exception:
            _bal_t = 10000.0
        _exp_sys = _bal_t * goal_pop / 100.0
        _exp_miss = _bal_t * goal_zec / 100.0
        check("a buried payer counts as SYSTEM, not missed (the attribution fix)",
              (usd.get("system_burial", 0) > 0.005 * _bal_t   # material: >=0.5% of the LIVE balance (the old literal 50 assumed 10k)
               and abs(usd["system_burial"] - _exp_sys) <= 0.05 * max(_exp_sys, 1.0)
               and abs(usd.get("missed", 0) - _exp_miss) <= 0.05 * max(_exp_miss, 1.0)),
              {"got": usd, "expected": {"system_burial": round(_exp_sys, 2),
                                        "missed": round(_exp_miss, 2)}})
        check("the top gate is named",
              res.get("top_gate") in ("tide-out", "brain-floor"), res.get("top_gate"))
        check("the fired payer (ZEC, voice-confirmed) is the user's miss",
              usd.get("missed", 0) > 0, usd)
        check("a buried NON-payer (XMR) counts nothing",
              all(abs(v) < 0.01 for k, v in usd.items()
                  if k in ("giveback", "missed_exit", "early_exit")), usd)
        check("system_left rides on the result + history",
              res.get("system_left", 0) > 0
              and any(h.get("system_left", 0) > 0 for h in
                      json.loads(dc.STATE_FILE.read_text())["history"]))
        sp = res.get("system_pairs") or []
        check("system_pairs names the pair + gate",
              any(p.get("pair") == "POPCAT/USD" and p.get("gate") == "tide-out"
                  for p in sp), sp)
        # the insight names the gate when system burials DOMINATE; a bigger
        # user miss keeps the user's insight (this rotation's ZEC case)
        ins = dc._insight(res, [], [])
        res_dom = dict(res)
        res_dom["usd_left"] = {"missed": 10.0, "giveback": 0, "missed_exit": 0,
                               "early_exit": 0, "system_burial": 250.0}
        res_dom["top_gate"] = "tide-out"
        res_dom["system_pairs"] = [{"pair": "POPCAT/USD", "gate": "tide-out",
                                    "usd": 250.0}]
        ins_dom = dc._insight(res_dom, [], [])
        check("the insight names the gate when system burials dominate",
              "gate buried" in ins_dom and "tide-out" in ins_dom, ins_dom)
        check("a bigger user miss keeps the user's insight",
              "green light" in ins, ins)
finally:
    dc._popups_in_rotation = _orig["popups"]
    dc._sold_in_rotation = _orig["sold"]
    dc._suppressions_in_rotation = _orig["supp"]
    dc._fired_pairs = _orig["fired"]
    dc.STATE_FILE = _orig["state"]

# ── 4. the learning (REDISTRICTED Oct 7, the full battery surfacing it:
#    the burial-cap study retired the scalar - the dial prices the
#    gates from the journal, so these pins assert the NAME, not the
#    nudge) ───────
try:
    with tempfile.TemporaryDirectory() as td:
        dc.STATE_FILE = Path(td) / "state.json"
        st = dc.load_state()
        # a losing rotation dominated by system burials -> the weight rises
        r = {"score": 82.0, "realized_pnl": -10.0, "pace_share": 48.0,
             "ts": "2026-09-04T12:00:00-06:00",
             "usd_left": {"missed": 0, "giveback": 0, "missed_exit": 0,
                          "early_exit": 0, "system_burial": 250.0},
             "top_gate": "tide-out"}
        lesson = dc._learn(st, r)
        check("losing rotation + gate burials -> the gate is named, the scalar stays retired",
              st["weights"]["system_burial"] == 1.0 and "tide-out" in lesson
      and "retire" in lesson, lesson)
        # a winning rotation dominated by system burials -> the weight eases
        st2 = dc.load_state()
        r2 = dict(r, score=30.0, realized_pnl=120.0)
        lesson2 = dc._learn(st2, r2)
        check("winning rotation + buried non-payers -> the gates are credited, the scalar stays retired",
              st2["weights"]["system_burial"] == 1.0
      and "gates were RIGHT" in lesson2 and "retire" in lesson2, lesson2)
finally:
    dc.STATE_FILE = _orig["state"]

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_disciplinesew_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("DONE")


def main():
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
