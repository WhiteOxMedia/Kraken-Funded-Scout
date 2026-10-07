"""Test THE DISCIPLINE SCORE (user spec, Sep 4: "Did I leave money on
the table?" — the rotation goal vs the scanner's real data vs what the
user did, scored 0-100, learning its own weights).

The components of money left on the table:
  MISSED       a paying popup never taken (the framed spend x goal)
  GIVEBACK     reached the goal, exited below the peak
  MISSED EXIT  reached the goal, no exit at all (the harshest weight)
  EARLY EXIT   sold before the goal while the coin paid after
Deductions scale against the rotation's share of the daily pace; the
weights LEARN (nudged when the score and the money disagree); good
behaviors earn bonuses. The scanner evaluates at every rotation
rollover and speaks the score; the console shows it side-by-side with
the contest results on MASTER."""
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
import scratch_space   # scratch policy: nothing volatile lands in the repo root

MST = ZoneInfo("America/Denver")
src = Path("discipline.py").read_text(encoding="utf-8")
scan = Path("background_scanner.py").read_text(encoding="utf-8")
ap = Path("app.py").read_text(encoding="utf-8")

# ── 1. the constants + the rotation windows ───────────────────────────
check("the learned weights default 1.0/0.9/1.25/0.5/1.0 (system_burial since the seam)",
      dc.DEFAULT_WEIGHTS == {"missed": 1.0, "giveback": 0.9,
                             "missed_exit": 1.25, "early_exit": 0.5,
                             "system_burial": 1.0})
check("weights are bounded [0.25, 2.5] with ±10% nudges",
      dc.W_MIN == 0.25 and dc.W_MAX == 2.5 and dc.NUDGE == 0.10)
s, e = dc._rot_window("08-12", datetime(2026, 9, 4, 10, tzinfo=MST))
check("rotation windows resolve (08-12 -> 08:00-12:00 MST)",
      s.hour == 8 and e.hour == 12 and s.day == 4)
s2, e2 = dc._rot_window("20-24", datetime(2026, 9, 4, 22, tzinfo=MST))
check("the 20-24 window resolves",
      s2.hour == 20 and e2.day == 4 and e2.day != s2.day or e2.hour == 0)

# ── 2. the rotation goal (the engine's per-rotation number) ───────────
g = dc._rotation_goal_pct("BTC/USD", "08-12", "weekday")
check("the rotation goal resolves to the [1, 3] band", 1.0 <= g <= 3.0, g)
g2 = dc._rotation_goal_pct("NOSUCHCOIN/USD", "08-12", "weekday")
check("an unknown coin fails to the universe median (still in the band)",
      1.0 <= g2 <= 3.0, g2)

# ── 3. the coin-paid check (the scanner's real data) ──────────────────
class FakeExchange:
    def __init__(self, highs):
        self.highs = highs   # list of (ts, high)
    def fetch_ohlcv(self, pair, timeframe="1h", since=None, limit=10):
        return [[int(ts.timestamp() * 1000), 1, hi, 1, 1, 100] for ts, hi in self.highs]

alert = datetime(2026, 9, 4, 10, 30, tzinfo=MST)
end = datetime(2026, 9, 4, 12, 0, tzinfo=MST)
paid_fx = FakeExchange([(datetime(2026, 9, 4, 11, tzinfo=MST), 1.03)])
flat_fx = FakeExchange([(datetime(2026, 9, 4, 11, tzinfo=MST), 1.005)])
check("a coin that hit entry x (1+goal) inside the window PAID",
      dc._coin_paid(paid_fx, "T/USD", 1.0, 2.0, alert, end) is True)
check("a coin that never reached the goal did NOT pay",
      dc._coin_paid(flat_fx, "T/USD", 1.0, 2.0, alert, end) is False)
after_fx = FakeExchange([(datetime(2026, 9, 4, 13, tzinfo=MST), 1.05)])
check("a coin that paid AFTER the window closed does not count",
      dc._coin_paid(after_fx, "T/USD", 1.0, 2.0, alert, end) is False)
class BrokenExchange:
    def fetch_ohlcv(self, *a, **k):
        raise RuntimeError("api down")
check("an unreadable path fails closed (no phantom missed money)",
      dc._coin_paid(BrokenExchange(), "T/USD", 1.0, 2.0, alert, end) is False)

# ── 4. the learning step (the self-learning core) ────────────────────
state = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
# high score + losing money -> the dominant weight rises
res_hi = {"score": 85.0, "realized_pnl": -50.0, "pace_share": 48.0,
          "usd_left": {"missed": 30.0, "giveback": 0, "missed_exit": 0, "early_exit": 0},
          "ts": datetime.now(MST).isoformat()}
lesson = dc._learn(state, res_hi)
check("high score + lost money -> the dominant weight RISES",
      state["weights"]["missed"] > dc.DEFAULT_WEIGHTS["missed"], state["weights"]["missed"])
check("the lesson is logged in plain words", "rises" in lesson and "missed" in lesson)
# low score + winning money -> the dominant weight eases
state2 = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
res_lo = {"score": 35.0, "realized_pnl": 120.0, "pace_share": 48.0,
          "usd_left": {"missed": 60.0, "giveback": 0, "missed_exit": 0, "early_exit": 0},
          "ts": datetime.now(MST).isoformat()}
lesson2 = dc._learn(state2, res_lo)
check("low score + made money -> the dominant weight EASES",
      state2["weights"]["missed"] < dc.DEFAULT_WEIGHTS["missed"], state2["weights"]["missed"])
check("that lesson names the easing", "eases" in lesson2)
# agreement -> weights hold (THE AGREEMENT BAR, Oct 5: realized money
# AND the table drained — ≤ the $1 crumb the voice itself ignores)
state3 = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
res_ok = {"score": 70.0, "realized_pnl": 40.0, "pace_share": 48.0,
          "usd_left": {"missed": 0.5, "giveback": 0, "missed_exit": 0, "early_exit": 0},
          "ts": datetime.now(MST).isoformat()}
lesson3 = dc._learn(state3, res_ok)
check("score and money agree -> the weights hold",
      state3["weights"] == dc.DEFAULT_WEIGHTS and "agree" in lesson3)
# THE AGREEMENT BAR (user rule, Oct 5): score 50 + realized $+0 + $505
# on the table is NOT agreement — the money never confirmed the score;
# the verdict is unproven until the leave realizes and the table
# drains to $0
state3b = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
res_505 = {"score": 50.0, "realized_pnl": 0.0, "pace_share": 48.0,
           "usd_left": {"missed": 305.0, "giveback": 200.0, "missed_exit": 0,
                        "early_exit": 0},
           "ts": datetime.now(MST).isoformat()}
lesson3b = dc._learn(state3b, res_505)
check("score 50 + realized $0 + $505 on the table -> NOT agreement, unproven",
      state3b["weights"] == dc.DEFAULT_WEIGHTS and "not agreement" in lesson3b
      and "unproven" in lesson3b, lesson3b[:100])
# money came but real leave is still on the table -> still unproven
state3c = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
res_left = dict(res_ok, usd_left={"missed": 60.0, "giveback": 0,
                                  "missed_exit": 0, "early_exit": 0})
lesson3c = dc._learn(state3c, res_left)
check("realized $40 but $60 still on the table -> not agreement yet",
      state3c["weights"] == dc.DEFAULT_WEIGHTS
      and "not agreement" in lesson3c, lesson3c[:100])
# ── 4b. THE SELF-RESOLVING VERDICT (user rule, Oct 5): an unproven
# lesson is not the end — the money has not spoken YET. A later
# evaluation re-joins the window's PAIR-NAMED leave to the applied SOLD
# journal and upgrades the lesson the money proved. ──
_sold_file = Path(__file__).parent / "contest_trades.jsonl"
_sold_backup = _sold_file.read_text(encoding="utf-8") if _sold_file.exists() else None
try:
    _day = (datetime.now(MST) - timedelta(days=1)).date()
    _iso = lambda h: _day.strftime("%Y-%m-%d") + f"T{h:02d}:00:00-06:00"
    def _row(pair, pnl, sold_h=11, applied=True, entry=1.0):
        return json.dumps({"sold_at": _iso(sold_h), "pair": pair,
                           "pnl_net": pnl, "entry": entry, "exit": 1.02,
                           "size": 1000.0, "applied": applied}) + chr(10)
    _mk = lambda: {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
    _verdict = ("Score 50, realized $+0 — $505 left on the table: the "
                "money hasn't confirmed the score, so this is not "
                "agreement. Weights hold unproven — the calibration "
                "stands only when the leave realizes and the money "
                "left on the table becomes $0.")
    _hist = {"date": _day.isoformat(), "rot": "08-12", "score": 50.0,
             "verdict": _verdict,
             "usd_left": {"missed": 505.0, "giveback": 0.0, "missed_exit": 0.0,
                          "early_exit": 0.0, "system_burial": 0.0},
             "left_pairs": {"missed": ["T/USD"], "giveback": [],
                            "missed_exit": [], "early_exit": [],
                            "system_burial": []},
             "left_entries": {"missed": {"T/USD": 1.0}, "giveback": {},
                              "missed_exit": {}, "early_exit": {},
                              "system_burial": {}},
             "system_pairs": []}
    _sold_file.write_text(_row("T/USD", 520.0), encoding="utf-8")
    st_r = _mk(); st_r["history"].append(dict(_hist))
    n1 = dc._resolve_prior_verdict(st_r)
    check("the window's own pair, sold inside the window, upgrades the lesson",
          n1 == 1 and "proven after the fact" in st_r["history"][0]["verdict"],
          st_r["history"][0]["verdict"][:80])
    check("the resolver speaks the upgrade in the lessons log",
          any("PROVEN after the fact" in l["lesson"] for l in st_r["lessons"]))
    n1b = dc._resolve_prior_verdict(st_r)
    check("re-reviewing a graded window appends NO duplicate lesson",
          n1b == 0 and len(st_r["lessons"]) == 1)
    _sold_file.write_text(_row("UNRELATED/USD", 520.0), encoding="utf-8")
    st_r2 = _mk(); st_r2["history"].append(dict(_hist))
    n2 = dc._resolve_prior_verdict(st_r2)
    check("an unrelated pair's profit proves NOTHING (pair-matched)",
          n2 == 0 and st_r2["history"][0]["verdict"] == _hist["verdict"])
    st_r2b = _mk(); st_r2b["history"].append(dict(_hist))
    _sold_file.write_text(_row("T/USD", 520.0, entry=1.5), encoding="utf-8")
    n2b = dc._resolve_prior_verdict(st_r2b)
    check("an unrelated ENTRY (not THE opportunity) proves nothing too",
          n2b == 0 and st_r2b["history"][0]["verdict"] == _hist["verdict"])
    _sold_file.write_text(_row("T/USD", 300.0), encoding="utf-8")
    st_r3 = _mk(); st_r3["history"].append(dict(_hist))
    n3 = dc._resolve_prior_verdict(st_r3)
    check("a partial landing renders partially resolved, still unproven",
          n3 == 1 and "partially resolved" in st_r3["history"][0]["verdict"]
          and "unproven" in st_r3["history"][0]["verdict"],
          st_r3["history"][0]["verdict"][:80])
    n3b = dc._resolve_prior_verdict(st_r3)
    check("a re-graded partial moves no words and appends no lesson",
          n3b == 0 and len(st_r3["lessons"]) == 1)
    _sold_file.write_text(_row("T/USD", 3.0), encoding="utf-8")
    st_r4 = _mk(); st_r4["history"].append(dict(_hist))
    n4 = dc._resolve_prior_verdict(st_r4)
    check("a too-thin realized sale never upgrades the verdict",
          n4 == 0 and st_r4["history"][0]["verdict"] == _hist["verdict"])
    _sold_file.write_text(_row("T/USD", -40.0), encoding="utf-8")
    st_r5 = _mk(); st_r5["history"].append(dict(_hist))
    n5 = dc._resolve_prior_verdict(st_r5)
    check("a LOSS is not a landing: only realized profit drains the leave",
          n5 == 0 and st_r5["history"][0]["verdict"] == _hist["verdict"])
    st_r6 = _mk(); st_r6["history"].append(dict(_hist))
    _sold_file.write_text(_row("T/USD", 520.0, applied=False), encoding="utf-8")
    n6 = dc._resolve_prior_verdict(st_r6)
    check("an UNAPPLIED sale never proves anything (counted money only)",
          n6 == 0 and st_r6["history"][0]["verdict"] == _hist["verdict"])
    _sold_file.unlink()
    st_r7 = _mk(); st_r7["history"].append(dict(_hist))
    n7 = dc._resolve_prior_verdict(st_r7)
    check("no journal evidence leaves the lesson exactly as it was",
          n7 == 0 and st_r7["history"][0]["verdict"] == _hist["verdict"])
    _buried = dict(_hist)
    _buried["usd_left"] = {"missed": 0.0, "giveback": 0.0, "missed_exit": 0.0,
                           "early_exit": 0.0, "system_burial": 505.0}
    _buried["left_pairs"] = {"missed": [], "giveback": [], "missed_exit": [],
                             "early_exit": [], "system_burial": ["X/USD"]}
    _buried["left_entries"] = {"missed": {}, "giveback": {},
                               "missed_exit": {}, "early_exit": {},
                               "system_burial": {"X/USD": 1.0}}
    _buried["system_pairs"] = [{"pair": "X/USD", "gate": "tide-out"}]
    _sold_file.write_text(_row("X/USD", 600.0, sold_h=13), encoding="utf-8")
    st_r8 = _mk(); st_r8["history"].append(dict(_buried))
    n8 = dc._resolve_prior_verdict(st_r8)
    check("a MARKED burial's own later sale lands the machine's leave",
          n8 == 1 and "proven after the fact" in st_r8["history"][0]["verdict"],
          st_r8["history"][0]["verdict"][:80])
    _sold_file.write_text(_row("X/USD", 300.0, sold_h=13)
                          + _row("X/USD", 300.0, sold_h=15), encoding="utf-8")
    st_r10 = _mk(); st_r10["history"].append(dict(_buried))
    n10 = dc._resolve_prior_verdict(st_r10)
    check("stacked re-entry sales never outrun the leave (one sale per pair)",
          n10 == 1 and "partially resolved" in st_r10["history"][0]["verdict"],
          st_r10["history"][0]["verdict"][:80])
    # ONE SALE PROVES ONE VERDICT (the fifth-round medium): the same
    # pair buried on TWO days does not turn one later sale into two
    # proofs — the winning sale is consumed on use
    _b1 = dict(_buried); _b1["date"] = _day.isoformat()
    _b2 = dict(_buried)
    _b2["date"] = (_day - timedelta(days=1)).isoformat()
    _sold_file.write_text(_row("X/USD", 600.0, sold_h=13), encoding="utf-8")
    st_r11 = _mk()
    st_r11["history"] = [dict(_b2), dict(_b1)]   # older first
    n11 = dc._resolve_prior_verdict(st_r11)
    check("one sale proves the most recent burial (consumed on use)",
          n11 == 1 and "proven after the fact"
          in st_r11["history"][1]["verdict"])
    n11b = dc._resolve_prior_verdict(st_r11)
    check("the consumed sale never proves the OLDER burial of the pair",
          n11b == 0
          and st_r11["history"][0]["verdict"] == _buried["verdict"]
          and len(st_r11["lessons"]) == 1)
    _sold_file.write_text(_row("X/USD", 600.0, sold_h=11), encoding="utf-8")
    st_r9 = _mk(); st_r9["history"].append(dict(_buried))
    n9 = dc._resolve_prior_verdict(st_r9)
    check("a burial-pair sale INSIDE the window is not the burial's landing",
          n9 == 0 and st_r9["history"][0]["verdict"] == _buried["verdict"])
    # SMALLER SALES COMBINE (sixth-round medium): the running credit
    # accumulates across passes — two $300 landings resolve a $505
    _two = dict(_hist)
    _two["left_pairs"] = {"missed": ["T/USD", "ETH/USD"], "giveback": [],
                          "missed_exit": [], "early_exit": [],
                          "system_burial": []}
    _two["left_entries"] = {"missed": {"T/USD": 1.0, "ETH/USD": 1.0},
                            "giveback": {}, "missed_exit": {},
                            "early_exit": {}, "system_burial": {}}
    _sold_file.write_text(_row("T/USD", 300.0), encoding="utf-8")
    st_r12 = _mk(); st_r12["history"].append(dict(_two))
    n12 = dc._resolve_prior_verdict(st_r12)
    _sold_file.write_text(_row("T/USD", 300.0)
                          + _row("ETH/USD", 300.0), encoding="utf-8")
    n12b = dc._resolve_prior_verdict(st_r12)
    check("smaller sales COMBINE across passes to resolve the leave",
          n12 == 1 and n12b == 1
          and "proven after the fact" in st_r12["history"][0]["verdict"]
          and st_r12["history"][0]["verdict_credit"]["missed"] == 600.0,
          st_r12["history"][0]["verdict"][:80])
    # PER-COMPONENT DRAINAGE (sixth-round medium): one oversized sale
    # proves only ITS OWN component — another pair leave waits
    _split = dict(_hist)
    _split["usd_left"] = {"missed": 305.0, "giveback": 200.0,
                          "missed_exit": 0.0, "early_exit": 0.0,
                          "system_burial": 0.0}
    _split["left_pairs"] = {"missed": ["T/USD"], "giveback": ["OTHER/USD"],
                            "missed_exit": [], "early_exit": [],
                            "system_burial": []}
    _split["left_entries"] = {"missed": {"T/USD": 1.0},
                              "giveback": {"OTHER/USD": 1.0},
                              "missed_exit": {}, "early_exit": {},
                              "system_burial": {}}
    _sold_file.write_text(_row("T/USD", 520.0), encoding="utf-8")
    st_r13 = _mk(); st_r13["history"].append(dict(_split))
    n13 = dc._resolve_prior_verdict(st_r13)
    check("one oversized sale proves only its own component",
          n13 == 1 and "proven after the fact"
          not in st_r13["history"][0]["verdict"]
          and "giveback" in st_r13["history"][0]["verdict"],
          st_r13["history"][0]["verdict"][:90])
    # THE LEGACY SHIM (user ask, Oct 5): rows written before the verdict
    # rode the row carry verdict=None and no left_pairs - the resolver
    # derives the burial map from the row's OWN system_pairs record and
    # lets the agreement bar decide eligibility from the stored numbers.
    _legacy = {"date": _day.isoformat(), "rot": "04-08", "score": 55.0,
               "verdict": None, "left_pairs": None, "left_entries": None,
               "usd_left": {"missed": 0.0, "giveback": 0.0, "missed_exit": 0.0,
                            "early_exit": 0.0, "system_burial": 376.30},
               "system_pairs": [{"pair": "F/USD", "gate": "tide-out"},
                                {"pair": "Z/USD", "gate": "deep-veto"}],
               "realized_pnl": 0, "total_left": 376.30}
    _sold_file.write_text(_row("F/USD", 52.94, sold_h=13), encoding="utf-8")
    st_l1 = _mk(); st_l1["history"].append(dict(_legacy))
    nl1 = dc._resolve_prior_verdict(st_l1)
    check("a LEGACY row (no verdict, no map) resolves from its own "
          "system_pairs record",
          nl1 == 1 and "partially resolved" in st_l1["history"][0]["verdict"]
          and "unproven" in st_l1["history"][0]["verdict"]
          and st_l1["history"][0]["verdict_credit"]["system_burial"] == 52.94,
          str(st_l1["history"][0].get("verdict"))[:90])
    _legacy_ok = dict(_legacy)
    _legacy_ok["realized_pnl"] = 3.40
    _legacy_ok["total_left"] = 0.0
    _legacy_ok["usd_left"] = {"missed": 0.0, "giveback": 0.0,
                              "missed_exit": 0.0, "early_exit": 0.0,
                              "system_burial": 0.0}
    _sold_file.write_text(_row("F/USD", 52.94, sold_h=13), encoding="utf-8")
    st_l2 = _mk(); st_l2["history"].append(dict(_legacy_ok))
    nl2 = dc._resolve_prior_verdict(st_l2)
    check("a LEGACY row the agreement bar itself proves never enters "
          "the resolver inbox",
          nl2 == 0 and st_l2["history"][0].get("verdict") is None)
    _legacy_nopair = dict(_legacy)
    _legacy_nopair["system_pairs"] = []
    _sold_file.write_text(_row("F/USD", 52.94, sold_h=13), encoding="utf-8")
    st_l3 = _mk(); st_l3["history"].append(dict(_legacy_nopair))
    nl3 = dc._resolve_prior_verdict(st_l3)
    check("a LEGACY row with no pair record of its own still never "
          "resolves (the seal holds - nothing is invented)",
          nl3 == 0 and st_l3["history"][0].get("verdict") is None)
    _legacy_two = dict(_legacy)
    _legacy_two["usd_left"] = {"missed": 200.0, "giveback": 0.0,
                               "missed_exit": 0.0, "early_exit": 0.0,
                               "system_burial": 100.0}
    _sold_file.write_text(_row("F/USD", 150.0, sold_h=13), encoding="utf-8")
    st_l4 = _mk(); st_l4["history"].append(dict(_legacy_two))
    nl4 = dc._resolve_prior_verdict(st_l4)
    check("a LEGACY row's unpaired leave never rides a paired sale "
          "to proven (the remainder counts EVERY nonzero component)",
          nl4 == 1 and "partially resolved" in st_l4["history"][0]["verdict"]
          and "proven after the fact" not in st_l4["history"][0]["verdict"]
          and "missed" in st_l4["history"][0]["verdict"],
          str(st_l4["history"][0].get("verdict"))[:90])
finally:
    if _sold_backup is None:
        if _sold_file.exists():
            _sold_file.unlink()
    else:
        _sold_file.write_text(_sold_backup, encoding="utf-8")

# the bounds hold across many nudges
state4 = {"weights": {"missed": 2.4}, "history": [], "lessons": []}
for _ in range(5):
    dc._learn(state4, res_hi)
check("the weight never exceeds the 2.5 bound", state4["weights"]["missed"] <= 2.5,
      state4["weights"]["missed"])

# ── 5. the insight (one actionable suggestion) ────────────────────────
ins = dc._insight({"usd_left": {"missed": 0, "giveback": 0, "missed_exit": 50, "early_exit": 0}},
                  [], [])
check("a no-exit rotation points at the SOLD tap", "SOLD" in ins or "exit" in ins.lower())
ins2 = dc._insight({"usd_left": {"missed": 50, "giveback": 0, "missed_exit": 0, "early_exit": 0}},
                   [], [])
check("a missed-popup rotation points at the green light", "green" in ins2.lower() or "popup" in ins2.lower())

# ── 5b. THE OPPORTUNITY COST (user rule, Sep 7: "the Discipline score
# gave itself a 100 because there were 0 popups — 0 popups does not help
# the goal; the goal is the BEST popups at the RIGHT time") ────────────
check("the empty-fire cap constant exists (60 = the CAREFUL band)",
      dc.EMPTY_FIRE_CAP == 60.0)
# a synthetic perfect-looking rotation with zero fires: no deductions, no
# bonuses — the raw score would be 100; the cap must hold it at 60
res_empty = {"score": 100.0, "realized_pnl": 0.0, "pace_share": 48.0,
             "usd_left": {"missed": 0, "giveback": 0, "missed_exit": 0,
                          "early_exit": 0, "system_burial": 0},
             "empty_fire": True,
             "ts": datetime.now(MST).isoformat()}
state_e = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
lesson_e = dc._learn(state_e, res_empty)
check("a zero-fire rotation's lesson says the silence, not perfection",
      "No popups fired" in lesson_e and "capped" in lesson_e, lesson_e[:80])
# zero fires + LOSING money -> the gates' leave: system_burial rises
res_empty_loss = dict(res_empty, realized_pnl=-30.0)
state_el = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
lesson_el = dc._learn(state_el, res_empty_loss)
check("zero fires + a losing rotation -> the scalar holds, the lesson names the silence",
      state_el["weights"]["system_burial"] == dc.DEFAULT_WEIGHTS["system_burial"]
      and "weight rises" not in lesson_el,
      (state_el["weights"]["system_burial"], lesson_el[:90]))
check("that lesson names the silent funnel", "silent funnel" in lesson_el
      or "surfaced nothing" in lesson_el)
# a FIRING rotation with the same clean sheet still earns 100
res_full = dict(res_empty, empty_fire=False)
state_f = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
lesson_f = dc._learn(state_f, res_full)
check("a firing rotation with a clean sheet rides the ZERO-ZERO verdict (weights hold, never agreement)",
      state_f["weights"] == dc.DEFAULT_WEIGHTS
      and "Zero realized" in lesson_f and "directive cap" in lesson_f
      and "not agreement" not in lesson_f)
check("the cap is wired into evaluate_rotation (score, not just the lesson)",
      "EMPTY_FIRE_CAP" in src and "empty_fire = not fired" in src
      and "score = EMPTY_FIRE_CAP" in src)
check("the result carries the empty_fire flag (the plate reads it)",
      '"empty_fire": empty_fire' in src)
check("the voice says the silence instead of a grade",
      "can't grade perfect" in src or "can't grade perfect" in src.replace("'", "'"))
check("the plate grades a silent rotation (amber, never SNIPER)",
      "SILENT ROTATION" in ap and "empty_fire" in ap)
check("a capped score never feeds the streak",
      "and not row.get(" in ap
      and "empty_fire" in ap[ap.find("# the streak: consecutive scores >= 70"):ap.find("# the streak: consecutive scores >= 70") + 500]
      and "capped_zero_zero" in ap[ap.find("# the streak: consecutive scores >= 70"):ap.find("# the streak: consecutive scores >= 70") + 500])

# ── 6. the wiring ─────────────────────────────────────────────────────
check("the scanner evaluates at every rotation rollover",
      "evaluate_rotation(exchange, _prev_key, speak=True)" in scan
      and "DISCIPLINE: rotation" in scan)
check("the closing rotation derives across midnight (00:xx -> 20-24)",
      "((now.hour // 4) * 4 - 4) % 24" in scan)
check("the dopamine voice fires at the rollover",
      "_speak_result" in src and "Discipline score" in src)
check("the console shows the plate on MASTER with the side-by-side",
      "_discipline_plate()" in ap and "render_master(cfg" in ap[:ap.find("def _discipline_plate")]
      and "left on the table" in ap)
check("the plate reads the scanner-written state (one source of truth)",
      "load_state()" in ap)
check("the popups come from the winners table (the fired log)",
      "get_tournament_winners" in src)
check("the realized side reads the SOLD ledger",
      "contest_trades.jsonl" in src)

# ── THE GIVEBACK'S OWN CHANNEL (user ask, Oct 5): a giveback-dominant
#    day raises the giveback weight even when the score's other
#    components dilute the sting — a mid score or a green day cannot
#    launder money the account REACHED and handed back. The machine's
#    own tighten trigger is the floor: dust dominates nothing worth
#    moving.
_gbs = lambda: {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [],
                "lessons": []}
_res_gb = {"score": 50.0, "realized_pnl": 0.0,
           "usd_left": {"missed": 5.0, "giveback": 505.0,
                        "missed_exit": 0.0, "early_exit": 0.0,
                        "system_burial": 0.0},
           "total_left": 510.0, "pace_share": 5.0,
           "ts": "2026-10-05T08:00:00"}
_st_gb = _gbs()
_gb_w0 = float(_st_gb["weights"]["giveback"])
dc._learn(_st_gb, _res_gb)
_gb_w1 = float(_st_gb["weights"]["giveback"])
check("giveback-dominant raises the giveback weight THROUGH the "
      "dilution (score 50, realized $0 — the old gates held it)",
      _gb_w1 > _gb_w0
      and "giveback weight rises" in _st_gb["lessons"][-1]["lesson"],
      _st_gb["lessons"][-1]["lesson"][:130])
check("the rise is exactly the nudge, bounded, and DIRECTIONAL (up)",
      abs(_gb_w1 - round(min(2.5, _gb_w0 * 1.1), 3)) < 1e-9,
      (_gb_w0, _gb_w1))
_res_gbd = {"score": 50.0, "realized_pnl": 0.0,
            "usd_left": {"missed": 0.0, "giveback": 3.0,
                         "missed_exit": 0.0, "early_exit": 0.0,
                         "system_burial": 0.0},
            "total_left": 3.0, "pace_share": 5.0,
            "ts": "2026-10-05T08:00:00"}
_st_gbd = _gbs()
_gb_w2 = float(_st_gbd["weights"]["giveback"])
dc._learn(_st_gbd, _res_gbd)
check("dust giveback (dominant, under the tighten trigger) moves "
      "nothing (the floor)",
      float(_st_gbd["weights"]["giveback"]) == _gb_w2,
      _st_gbd["lessons"][-1]["lesson"][:110])
# THE PRECEDENCE PIN (the second opinion's medium, cured): giveback
# dominance OUTRANKS the burial pile-up — a day that both buried two
# payers AND handed back the biggest money raises the GIVEBACK weight
# (the burial still learned at its own rotation's rollover).
_res_gbb = {"score": 50.0, "realized_pnl": 0.0,
            "usd_left": {"missed": 0.0, "giveback": 300.0,
                         "missed_exit": 0.0, "early_exit": 0.0,
                         "system_burial": 118.0},
            "total_left": 418.0, "pace_share": 5.0,
            "system_pairs": [{"pair": "A/USD", "gate": "g1", "usd": 60},
                             {"pair": "B/USD", "gate": "g2", "usd": 58}],
            "system_duds": [],
            "ts": "2026-10-05T08:00:00"}
_st_gbb = _gbs()
_gb_w3 = float(_st_gbb["weights"]["giveback"])
_b_w3 = float(_st_gbb["weights"]["system_burial"])
dc._learn(_st_gbb, _res_gbb)
check("giveback dominance OUTRANKS the burial pile-up (the biggest "
      "leave names its own weight)",
      float(_st_gbb["weights"]["giveback"]) > _gb_w3
      and float(_st_gbb["weights"]["system_burial"]) == _b_w3
      and "giveback weight rises" in _st_gbb["lessons"][-1]["lesson"],
      _st_gbb["lessons"][-1]["lesson"][:130])

# ── 5z. THE ROTATION-MODE DIRECTIVE (user directive, Oct 7 - "recorded on
#    the discipline score card"): ZERO-ZERO IS A FAILURE SHAPE. A FIRING
#    rotation that realized $0 and left nothing on the table graded 100
#    ("score 100, realized $+0 - weights hold", live twice) - the silent
#    case was capped (Sep 7), the firing case was the hole. Red-first:
#    every pin here failed against the uncapped scorer.
check("the directive cap exists and stacks UNDER the silence ceiling",
      dc.ZERO_REALIZED_CAP == 55.0 and dc.EMPTY_FIRE_CAP == 60.0
      and dc.ZERO_REALIZED_CAP < dc.EMPTY_FIRE_CAP)
check("the crumb is the same $1 the voice ignores", dc.ZZ_CRUMB == 1.0)
check("5z.a a FIRING zero-zero rotation caps at 55 (was 100 - the live defect)",
      dc._zero_zero_cap(100.0, True, 0.0, 0.0) == 55.0)
check("5z.b the SILENT case belongs to the empty-fire cap (not doubled up)",
      dc._zero_zero_cap(100.0, False, 0.0, 0.0) == 100.0)
check("5z.c a PAID firing rotation is untouched (realized money speaks)",
      dc._zero_zero_cap(100.0, True, 40.0, 0.0) == 100.0)
check("5z.d real leave on the table is untouched (it already docks)",
      dc._zero_zero_cap(100.0, True, 0.0, 376.30) == 100.0)
_res_zz = {"score": 100.0, "realized_pnl": 0.0, "pace_share": 48.0,
           "usd_left": {"missed": 0, "giveback": 0, "missed_exit": 0,
                        "early_exit": 0, "system_burial": 0},
           "empty_fire": False,
           "ts": datetime.now(MST).isoformat()}
_st_zz = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
_lesson_zz = dc._learn(_st_zz, _res_zz)
check("5z.e the zero-zero lesson names the directive cap, never perfection",
      "Zero realized" in _lesson_zz and "directive cap" in _lesson_zz,
      _lesson_zz[:100])
check("5z.f zero-zero nudges nothing (the weights stay unproven-held)",
      _st_zz["weights"] == dc.DEFAULT_WEIGHTS)
check("5z.g evaluate_rotation wires the cap (firing, realized<=0, crumb left)",
      "ZERO_REALIZED_CAP" in src and "_zero_zero_cap(" in src
      and "realized_pnl <= 0.0" in src and "ZZ_CRUMB" in src)
check("5z.h the result carries the flag (the plate reads it)",
      '"capped_zero_zero": _capped_zz' in src)
check("5z.i the voice names the cap on the capped shape",
      "Zero realized" in src and "capped_zero_zero" in src)
check("5z.j the plate grades ZERO REALIZED (never SNIPER while capped)",
      "ZERO REALIZED" in ap and "capped_zero_zero" in ap)
check("5z.k the streak excludes a zero-realized cap (like the silence)",
      'not row.get("capped_zero_zero")' in ap)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_discipline_check.txt').write_text("\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
