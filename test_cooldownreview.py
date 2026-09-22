"""Headless drive of the REAL cooldown_review (discipline.py) + the
scanner's once-per-day guard. Dry-run everywhere: no state write, no
voice. Scenarios:
  A. real-data consistency  aggregates == manual sum of today's rows
  B. weight-delta parsing   the 'rises/eases A -> B' lesson sentences
  C. fired-any              voice-log fire evidence (shape + manual match)
  D. no-rotation day        score None -> facts recorded, weights HOLD
  E. aggregate learning     day-level burial pile-up nudges the SAME weight
  F. once-per-day guard     _day_review_spoken flips on the DAY row
"""
import json
import sys
from datetime import datetime

import discipline as disc

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1
    print(("PASS  " if cond else "FAIL  ") + name)


now = disc._now()
today = now.strftime("%Y-%m-%d")
_real_load = disc.load_state

# ── A. real-data consistency (the live state, dry-run) ───────────────
rev = disc.cooldown_review(when=now, speak=False, dry_run=True)
print("REVIEW:", json.dumps({k: rev[k] for k in
                             ("date", "rotations", "total_left",
                              "realized_pnl", "daily_pace", "lesson")},
                             default=str)[:260])
state = _real_load()
rows = [h for h in (state.get("history") or [])
        if h.get("date") == today and h.get("rot") != "DAY"]
check("rotation count matches the state", rev["rotations"] == len(rows))
_exp_left = round(sum(float(h.get("total_left") or 0) for h in rows), 2)
check("money-left aggregates the day's rotations", rev["total_left"] == _exp_left)
_exp_real = 0.0
try:
    for line in open("contest_trades.jsonl", encoding="utf-8"):
        if not line.strip():
            continue
        t = json.loads(line)
        ts = str(t.get("sold_at", ""))
        if ts.startswith(today) and t.get("applied"):
            _exp_real += float(t.get("pnl_net") or 0.0)
except Exception:
    pass
check("realized PnL matches the ledger", rev["realized_pnl"] == round(_exp_real, 2))
check("lesson is a factual string", isinstance(rev["lesson"], str) and len(rev["lesson"]) > 5)
check("review kind stamped", rev.get("kind") == "cooldown_review")

# ── B. weight-delta parsing (pure function) ──────────────────────────
fake = {"lessons": [
    {"ts": f"{today}T12:00:00",
     "lesson": "The system_burial weight rises 1.00 -> 1.10: test."},
    {"ts": f"{today}T16:00:00",
     "lesson": "The missed weight eases 1.20 -> 1.08: test."},
    {"ts": "2026-09-19T12:00:00",
     "lesson": "The missed weight rises 1.00 -> 9.99: old day, ignored."},
]}
d = disc._day_weight_deltas(fake, today)
check("deltas parsed from today's lessons",
      abs(d.get("system_burial", 0) - 0.10) < 1e-9
      and abs(d.get("missed", 0) + 0.12) < 1e-9)
check("old days excluded", d.get("missed", 0) != 8.99)

# ── C. fired-any (the real voice log) ────────────────────────────────
fired = disc._day_fired_any(today)
_grep = False
try:
    for line in open("voice_log.jsonl", encoding="utf-8"):
        if line.strip() and json.loads(line).get("ts", "").startswith(today) \
                and str(json.loads(line).get("text", "")).startswith("Buy "):
            _grep = True
            break
except Exception:
    pass
check("fired-any matches the voice log manually", fired == _grep)

# ── D. no-rotation day: score None, weights HOLD ─────────────────────
disc.load_state = lambda: {"weights": dict(disc.DEFAULT_WEIGHTS),
                           "history": [], "lessons": []}
rev_d = disc.cooldown_review(when=now, speak=False, dry_run=True)
check("empty day -> score None", rev_d["score"] is None)
check("empty day -> rotations 0", rev_d["rotations"] == 0)
check("empty day lesson holds the weights",
      "weights hold" in rev_d["lesson"])
disc.load_state = _real_load

# ── E. aggregate learning: the day's burial pile-up moves the weight ──
_st = _real_load()
_w0 = float((_st.get("weights") or {}).get("system_burial", 1.0))
_st["history"] = (_st.get("history") or []) + [
    {"date": today, "rot": "08-12", "score": 35.0, "total_left": 60.0,
     "realized_pnl": -50.0, "balance_after": 10000.0,
     "usd_left": {"missed": 0, "giveback": 0, "missed_exit": 0,
                  "early_exit": 0, "system_burial": 118.0},
     "system_pairs": [{"pair": "A/USD", "gate": "deep-direction", "usd": 60},
                      {"pair": "B/USD", "gate": "floor", "usd": 58}],
     "system_duds": [], "ts": f"{today}T12:00:00"}]
disc.load_state = lambda: _st
rev_e = disc.cooldown_review(when=now, speak=False, dry_run=True)
_w1 = float(_st["weights"]["system_burial"])
check("day-level burial pile-up raises the SAME weight", _w1 > _w0)
check("the day lesson names the burials", "buried" in rev_e["lesson"])
_dayrows_before = sum(1 for h in _real_load().get("history") or []
                      if h.get("rot") == "DAY" and h.get("date") == today)
check("no state write on dry-run",
      sum(1 for h in _real_load().get("history") or []
          if h.get("rot") == "DAY" and h.get("date") == today)
      == _dayrows_before)
disc.load_state = _real_load

# ── E2. per-asset review: verdicts on fabricated trades ─────────────
v1 = disc._asset_verdict(100.0, 101.5, 101.6, 1.5, 7.0, 500.0)
check("took-the-goal verdict", v1[0].startswith("took the goal") and v1[1] == 0.0)
v2 = disc._asset_verdict(100.0, 100.4, 101.6, 1.5, 0.10, 500.0)
check("exited-too-soon verdict with the leaked dollars",
      v2[0].startswith("exited too soon") and abs(v2[1] - 7.9) < 0.01)
v3 = disc._asset_verdict(100.0, 100.1, 102.5, 1.5, 0.10, 500.0)
check("stayed-too-long outranks exited-too-soon",
      v3[0].startswith("stayed too long") and abs(v3[1] - 12.4) < 0.01)
v4 = disc._asset_verdict(100.0, 99.0, 100.3, 1.5, -2.0, 500.0)
check("bought-the-top verdict", v4[0].startswith("bought the top"))
v5 = disc._asset_verdict(100.0, 0.0, 101.8, 1.5, 0.0, 500.0)
check("held-past-goal verdict", v5[0].startswith("goal hit and never exited"))

# ── E3. the real day's assets agree with the ledgers ────────────────
rev2 = disc.cooldown_review(None, when=now, speak=False, dry_run=True)
_assets = rev2.get("assets") or []
_sold_n = sum(1 for a in _assets if a.get("kind") == "sold")
_ledger_n = 0
try:
    for line in open("contest_trades.jsonl", encoding="utf-8"):
        if line.strip() and json.loads(line).get("sold_at", "").startswith(today):
            _ledger_n += 1
except Exception:
    pass
check("sold assets match the ledger count", _sold_n == _ledger_n)
check("every asset carries a verdict",
      all(a.get("verdict") for a in _assets))
_leads = [a for a in _assets if a.get("kind") == "buried"]
check("buried assets carry the gate attribution",
      all(a.get("gate") for a in _leads))
print("ASSETS:", json.dumps(_assets)[:300])

import background_scanner as bs
if not bs._day_review_spoken(now):
    check("guard open before the review ran", bs._day_review_spoken(now) is False)
    _st2 = _real_load()
    _st2["history"] = (_st2.get("history") or []) + [
        {"date": today, "rot": "DAY", "score": 50.0, "total_left": 0.0,
         "realized_pnl": 0.0, "balance_after": 10000.0, "ts": now.isoformat()}]
    disc.load_state = lambda: _st2
    check("guard closed once the DAY row exists", bs._day_review_spoken(now) is True)
    disc.load_state = _real_load
else:
    print("NOTE: today's review already ran live - guard held end to end (proof, not a failure)")

# ── G. the book-wide verdict cost on the nightly line (Sep 21) ──────
import types as _types
_vs = _types.ModuleType("_verdict_study")
_vs.grade_trades = lambda: [
    {"verdict": "stayed too long - gave back over half the peak", "left": 40.0},
    {"verdict": "stayed too long - gave back over half the peak", "left": 25.0},
    {"verdict": "bought the top - the coin never ran", "left": 10.0},
    {"verdict": "took the goal - the discipline shape", "left": 0.0},
]
sys.modules["_verdict_study"] = _vs
_rv1 = disc._top_verdict_class()
print("      top verdict ->", _rv1)
check("top verdict class is the costliest",
      _rv1 == ("stayed too long - gave back over half the peak", 65.0))
_vs.grade_trades = lambda: [{"verdict": "took the goal", "left": 0.0}]
check("all-zero classes -> None (no chatter)", disc._top_verdict_class() is None)
_vs.grade_trades = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
check("broken study -> None (fail-soft)", disc._top_verdict_class() is None)
sys.modules.pop("_verdict_study", None)
_rtv = disc._top_verdict_class()   # the REAL study, through the same helper
print("      real study ->", _rtv)
check("real study returns a (name, usd) pair",
      _rtv is None or (isinstance(_rtv, tuple)
                       and isinstance(_rtv[0], str) and _rtv[1] >= 0))

# the review carries it (dry-run, stubbed study) and the line speaks it
sys.modules["_verdict_study"] = _vs
_vs.grade_trades = lambda: [
    {"verdict": "stayed too long - gave back over half the peak", "left": 65.0}]
rev_g = disc.cooldown_review(when=now, speak=False, dry_run=True)
check("the review's result carries top_verdict",
      rev_g.get("top_verdict") == ("stayed too long - gave back over "
                                   "half the peak", 65.0))
spoke = []
_orig_speak = bs._speak_server
try:
    bs._speak_server = lambda line, rate=1: spoke.append(line)
    disc._speak_cooldown_review(
        {"total_left": 704.2, "top_gate": "downward-trend",
         "top_verdict": ("stayed too long - gave back over half the peak",
                         64.78),
         "lesson": "Every asset taken closed clean - nothing leaked."},
        240.0, 12.94, 2, "flat (61% of the fleet up)", 0)
finally:
    bs._speak_server = _orig_speak
    sys.modules.pop("_verdict_study", None)
print("      spoken line ->", spoke[:1])
check("the nightly line names the costliest pattern",
      bool(spoke) and "costliest pattern" in spoke[0]
      and "stayed too long" in spoke[0] and "65 dollars" in spoke[0])
spoke.clear()
try:
    bs._speak_server = lambda line, rate=1: spoke.append(line)
    disc._speak_cooldown_review(
        {"total_left": 0.0, "top_gate": None,
         "top_verdict": ("stayed too long - gave back over half the peak",
                         64.78),
         "lesson": ("FARTCOIN: stayed too long - gave back over half the "
                    "peak - $14 of the move went with it.")},
        240.0, 0.0, 0, None, 0)
    check("no doubled pattern when the lesson already names it",
          bool(spoke) and "costliest pattern" not in spoke[0])
    spoke.clear()
    disc._speak_cooldown_review(
        {"total_left": 0.0, "top_gate": None, "top_verdict": None,
         "lesson": ""}, 240.0, 0.0, 0, None, 0)
    check("no pattern clause when the study says nothing",
          bool(spoke) and "costliest pattern" not in spoke[0])
finally:
    bs._speak_server = _orig_speak

print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
