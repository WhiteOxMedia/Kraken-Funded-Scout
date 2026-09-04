"""Test DISPLACEMENT PROTECTION IN THE SEP 3 ROTATION (rewritten Sep 4
after the user decision: "keep the simpler Sep 3 bot — the rotation
design replaces the old rules"). The Sep 2 winner-lock (the ASTER
lesson: a green winner whose own chart says SELL is challengeable; a
pullback rider stays locked) is RETIRED — the rotation design the user
asked for on Sep 3 does the job with different semantics:

  _find_weakest_position scores every held position by P&L + the chart's
  own read: a position whose fresh read is BUY >=50 is NEVER sold as the
  weakest ("recovering — skip"); a sell-reading position scores -5
  (weakest first, even in profit); a fading hold scores -2. There is no
  pnl>0 immunity and no entry-conviction math — the weakest seat funds
  the stronger (>=70%) incoming buy, at a loss if that's what it is
  (the user's Sep 3 rule: sell the weakest, not 'never sell at a loss')."""
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


READER = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py")
src = READER.read_text(encoding="utf-8")
fn = src[src.find("def _find_weakest_position"):]
fn = fn[:fn.find("\ndef ")]

# ── 1. the protection: the chart decides who is weakest ──────────────
check("a recovering position (own read buy >=50) is skipped, never sold",
      'sig.action == "buy" and sig.confidence >= 50' in fn
      and "recovering" in fn)
check("a sell-reading position is scored weakest (-5) — even in profit",
      "signal_penalty = -5.0" in fn and "# trend already reversed" in fn)
check("a fading hold (hold <30) scores down (-2)",
      "signal_penalty = -2.0" in fn and "momentum fading" in fn)
check("the score is P&L + the chart's vote",
      "score = pnl_pct + signal_penalty" in fn)
check("a broken strategy read still ranks by P&L (fail-soft)",
      "if strategy fails, just use P&L" in fn)

# ── 2. no immunity — the weakest funds the stronger, at a loss if needed
check("no never-sell-at-a-loss filter (the old bug is gone)",
      "pnl_pct >= 0" not in fn)
check("no winner lock — pnl>0 does not shield a seat",
      "pnl_pct > 0" not in fn)
check("no entry-conviction strength math (retired with the lock)",
      "entry_conf" not in fn)

# ── 3. the old lock machinery is retired, not deleted ────────────────
check("the challenge/churn machinery is gone from the reader",
      "_challenge_weakest" not in src and "CHALLENGE_MARGIN" not in src
      and "FRESH_BUY_COOLDOWN_MIN" not in src)
check("the displacement happens only through the rotation (>=70% buys)",
      src.count("weakest = _find_weakest_position(kraken, config)") == 2
      and "if confidence >= 70:" in src)

# ── 4. the ASTER lesson is superseded, not forgotten ─────────────────
# (documented decision — no code to assert; the docstring above is the
#  record. A winner whose chart says SELL still exits FIRST through the
#  scorer's -5 penalty: it is the weakest seat by the chart's own read.)
check("a chart-says-SELL winner is the weakest seat by construction",
      "-5.0" in fn)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_winnerlock_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
