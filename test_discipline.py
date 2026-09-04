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

MST = ZoneInfo("America/Denver")
src = Path("discipline.py").read_text(encoding="utf-8")
scan = Path("background_scanner.py").read_text(encoding="utf-8")
ap = Path("app.py").read_text(encoding="utf-8")

# ── 1. the constants + the rotation windows ───────────────────────────
check("the learned weights default 1.0/0.9/1.25/0.5",
      dc.DEFAULT_WEIGHTS == {"missed": 1.0, "giveback": 0.9,
                             "missed_exit": 1.25, "early_exit": 0.5})
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
# agreement -> weights hold
state3 = {"weights": dict(dc.DEFAULT_WEIGHTS), "history": [], "lessons": []}
res_ok = {"score": 70.0, "realized_pnl": 40.0, "pace_share": 48.0,
          "usd_left": {"missed": 10.0, "giveback": 0, "missed_exit": 0, "early_exit": 0},
          "ts": datetime.now(MST).isoformat()}
lesson3 = dc._learn(state3, res_ok)
check("score and money agree -> the weights hold",
      state3["weights"] == dc.DEFAULT_WEIGHTS and "agree" in lesson3)
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

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_discipline_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
