"""Test the CONTEST HIGH-WATER MARK (Nov 16): a touch-and-fall-back pass.

The win condition is a TOUCH of +12%, not a resting balance. Before the mark,
contest_won() compared only current_balance to the win line, so a balance that
crossed 11,200 and gave it back before the next read was never recorded, and
reanchor_after_win() refused to score it.

Graded here against a TEMP state file:
  - a fresh window's mark starts at its own start
  - a close that crosses the line records the mark, even if later closes fall back
  - contest_won() stays true after the fall-back (the touch is the win)
  - reanchor_after_win() scores and re-anchors from the fallen-back balance
  - the new window's mark RESETS to the new start (no cross-window carry)
  - a legacy state with no mark still grades on its spot balance
  - a flat cycle writes nothing
Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_contest_peak.py
"""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import contest_account as ca

_tmpdir = tempfile.mkdtemp(prefix="_contestpeak_")
STATE = Path(_tmpdir) / "contest_account.json"
ca.STATE_FILE = STATE
ca.STATE_LOCK_FILE = Path(_tmpdir) / "contest_account.json.lock"
ca.CONTEST_HISTORY_FILE = Path(_tmpdir) / "contest_history.jsonl"

lines = []
PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + label)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(("PASS  " if ok else "FAIL  ") + label)


def write_state(**kw):
    s = {"tier": 10000, "start_balance": 10000.0, "current_balance": 10000.0,
         "contest_id": "2026-11-16T00:00:00-07:00",
         "updated_at": "2026-11-16T00:00:00-07:00"}
    s.update(kw)
    STATE.write_text(json.dumps(s), encoding="utf-8")
    return s


def close(net, key):
    return ca.apply_pnl(net, None, f"peak-test|{key}")


# ── 1. a fresh window's mark is its own start ─────────────────────────
write_state()
s = ca.new_contest(10000)
check("new contest stamps the mark at its start",
      ca.peak_balance(s) == 10000.0 and s["peak_balance"] == 10000.0)
check("fresh contest not won", not ca.contest_won())

# ── 2. a close above the line records the touch ───────────────────────
close(+1250.0, "cross")
check("11,250 crosses: won on the touch", ca.contest_won())
check("state carries the mark", ca.load_state()["peak_balance"] >= 11250.0)

# ── 3. the fall-back does not erode it ────────────────────────────────
close(-180.0, "fallback")
now = ca.load_state()
check("balance is back below the win line",
      float(now["current_balance"]) < ca.win_line(now))
check("the touch still grades as WON", ca.contest_won(now))
st = ca.standing(now)
check("standing says won", st["won"] is True)
check("standing flags won_on_touch", st.get("won_on_touch") is True)
check("standing reports the peak", st.get("peak_balance", 0) >= 11250.0)

# ── 4. re-anchor reads the mark, not the spot balance ─────────────────
pre = ca.load_state()
new = ca.reanchor_after_win(pre)
check("re-anchor accepted a fallen-back balance",
      float(new["start_balance"]) == round(float(pre["current_balance"]), 10))
check("new window's mark resets to the new start",
      float(new["peak_balance"]) == float(new["start_balance"]))
check("new window is not won", not ca.contest_won())
hist = [json.loads(x) for x in
        ca.CONTEST_HISTORY_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
check("history records the peak and the touch",
      bool(hist) and hist[-1].get("peak_balance", 0) >= 11250.0
      and hist[-1].get("won_on_touch") is True)

# ── 5. a legacy state with no mark grades on its spot balance ─────────
write_state(start_balance=10000.0, current_balance=11250.0)
check("legacy state above the line still wins", ca.contest_won())
check("legacy state has no mark key",
      "peak_balance" not in json.loads(STATE.read_text(encoding="utf-8")))
check("a markless state reads its balance as the mark",
      ca.peak_balance() == 11250.0)
ca.capture_peak()
check("an unchanged balance writes nothing (no phantom stamp)",
      "peak_balance" not in json.loads(STATE.read_text(encoding="utf-8")))

# ── 6. a flat cycle writes nothing ────────────────────────────────────
before = STATE.read_text(encoding="utf-8")
ca.capture_peak()
check("flat cycle leaves the file untouched",
      STATE.read_text(encoding="utf-8") == before)

# ── 7. the console's hand edit cannot rewrite the peak down ───────────
ca.save_state(10000, 10900.0)
after = json.loads(STATE.read_text(encoding="utf-8"))
check("a lower hand-entered balance keeps the peak", after["peak_balance"] == 11250.0)
check("the pass survives the balance edit", ca.contest_won(after))

print("\n".join(lines))
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
