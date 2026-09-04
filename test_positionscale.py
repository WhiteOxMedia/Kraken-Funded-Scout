"""Test POSITION GATING IN THE SEP 3 READER (rewritten Sep 4 after the
user decision: "keep the simpler Sep 3 bot — the rotation design replaces
the old rules"). The Sep 2 position ladder (1k=2, 5k=5, 10k=10 slots)
and the winner-lock/challenge displacement are RETIRED — code kept per
the don't-delete rule, but the reader never calls them. What governs
positions now:
  - ONE static slot: MAX_CONCURRENT_POSITIONS (=1, from the scout bridge
    — the copy the reader resolves via its scout-path-first import)
  - already-holding gates (bridge + the bot's own DB)
  - THE ROTATION (the Sep 3 replacement for displacement): a >=70% scout
    buy with <$100 free sells the weakest position to fund it BEFORE the
    minimum reject, and a second rotation fires when the engine itself
    reports Insufficient
  - the engine deploys trade_pct (99%) of available — all-in, no
    per-slot division"""
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


import signal_bridge as sb

BOT = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot")
rsrc = (BOT / "scout_signal_reader.py").read_text(encoding="utf-8")
esrc = (BOT / "bot_engine.py").read_text(encoding="utf-8")

# ── 1. the one static slot (the scout bridge is the live copy) ───────
check("MAX_CONCURRENT_POSITIONS is 1 (concentrated by design)",
      sb.MAX_CONCURRENT_POSITIONS == 1, sb.MAX_CONCURRENT_POSITIONS)
check("the reader resolves the SCOUT's bridge (path insert first)",
      'sys.path.insert(0, str(SCOUT_PATH))' in rsrc)

# ── 2. the ladder: retired but kept (don't-delete rule) ───────────────
check("the ladder math is kept in signal_bridge (retired, not deleted)",
      hasattr(sb, "max_positions_for") and sb.max_positions_for(10_000) == 10)
check("the ladder is the user's original rule verbatim",
      sb.POSITION_LADDER == ((1_000.0, 2), (5_000.0, 5), (10_000.0, 10))
      and sb.POSITION_CEILING == 10)
check("the reader does NOT call the ladder (retired by decision, Sep 4)",
      "max_positions_for(" not in rsrc)
check("the reader enforces the static gate",
      "open_count >= MAX_CONCURRENT_POSITIONS" in rsrc)

# ── 3. the already-holding gates ─────────────────────────────────────
check("bridge holding gate (is_already_holding)",
      "is_already_holding(pair)" in rsrc)
check("bot-DB holding gate (get_position_for_asset)",
      "get_position_for_asset(base)" in rsrc)

# ── 4. THE ROTATION — the Sep 3 replacement for displacement ─────────
check("rotation runs BEFORE the minimum reject (rotation precedes reject)",
      0 < rsrc.find("attempting rotation") < rsrc.find("Insufficient funds:"))
check("the minimum is $100 (fewer, bigger positions)",
      "available_usd < 100" in rsrc and "min $100" in rsrc)
check("rotation only fires on a >=70% incoming buy",
      "if confidence >= 70:" in rsrc)
check("the second rotation: engine Insufficient -> sell weakest -> retry",
      '"Insufficient" in buy_result.get("skipped", "")' in rsrc
      and "Rebalance: sold" in rsrc)
check("rotation closes the sold pair's bridge record",
      "mark_closed_by_pair(weak_pair" in rsrc)

# ── 5. the weakest-position scorer (what displacement became) ────────
fn = rsrc[rsrc.find("def _find_weakest_position"):]
fn = fn[:fn.find("\ndef ")]
check("a recovering position (own read buy >=50) is never the weakest",
      'sig.action == "buy" and sig.confidence >= 50' in fn)
check("a sell-reading position scores weakest (-5)",
      "signal_penalty = -5.0" in fn)
check("a fading hold scores down (-2)",
      "signal_penalty = -2.0" in fn)
check("score = PnL + signal penalty (the chart has a vote)",
      "score = pnl_pct + signal_penalty" in fn)
check("a broken strategy read still scores by P&L (fail-soft)",
      "if strategy fails, just use P&L" in fn)

# ── 6. the engine sizes all-in (no per-slot division) ────────────────
check("the engine deploys trade_pct of available (all-in)",
      "available_quote * (self.config.trade_pct / 100.0)" in esrc)
check("the per-slot division is retired (no _acct_v / _slots)",
      "_acct_v / _slots" not in esrc)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_positionscale_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
