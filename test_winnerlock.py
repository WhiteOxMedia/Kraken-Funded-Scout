"""Test THE WINNER-LOCK EXCEPTION (user rule, Sep 2 — the ASTER fade: it
ran +2.5% to 0.74501, faded, lost momentum, and the Aug 30 winner lock
froze it while green — "we don't want to hold weak positions"). The lock
now breaks on the CHART's verdict, not the price trail: a winner whose
own fresh read says SELL is challengeable even in profit; a winner riding
a pullback with a buy/hold read stays locked. The user chose 'bearish
read only' — no giveback/trail test."""
import sys
from pathlib import Path
import scratch_space   # scratch policy: nothing volatile lands in the repo root

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


READER = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py")
src = READER.read_text(encoding="utf-8")
fn = src[src.find("def _challenge_weakest"):src.find("def _sell_held_position")]

# ── 1. the lock breaks on the chart's verdict ─────────────────────────
check("the lock is pnl>0 AND the fresh read is NOT sell — the chart decides",
      'if pnl_pct > 0 and scan_action != "sell":' in fn)
check("the old unconditional lock is gone",
      "if pnl_pct > 0:\n                continue" not in fn)
check("the fresh scan runs BEFORE the lock (the exception reads it)",
      fn.find("fresh scan: what does the held asset say RIGHT NOW?")
      < fn.find("WINNER LOCK"))
check("the exception is documented with the ASTER lesson",
      "ASTER fade" in fn and "the chart decides" in fn.lower()
      or "CHART DECIDES" in fn)

# ── 2. what still protects a real winner ─────────────────────────────
check("fresh-buy protection still stands (never churn a new entry)",
      "FRESH_BUY_COOLDOWN_MIN" in fn)
check("the strength math still guts a bearish incumbent "
      "(entry_conf − scan_conf — a green-but-dead seat loses fast)",
      "entry_conf - scan_conf" in fn)
check("a broken scan keeps the lock (fail-locked: never unlock on missing data)",
      "never unlock protection on missing data" in fn)
check("buy/hold reads still lock a profitable position",
      'scan_action == "buy"' in fn and 'scan_action == "sell"' in fn)

# ── 3. the churn guard is intact (the Aug 29 fee-machine lesson) ──────
check("the winner lock's original rationale is kept (Aug 29: ENA/NEAR churn)",
      "fee machine" in fn and "ENA" in fn)
check("the challenger still must clear CHALLENGE_MARGIN",
      "CHALLENGE_MARGIN" in fn)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open(scratch_space.report_temp('_winnerlock_check.txt'), "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
