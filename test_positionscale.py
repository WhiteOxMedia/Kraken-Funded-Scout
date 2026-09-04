"""Test THE POSITION LADDER v2 (user rule, respecified Sep 4: "'max_
positions_for' is for when the real money account grows we shift gears
and focus on more assets then one at a time. 1k-3k=3 assets; 3k-5k=5
assets; 5k-10k=10 assets and so on").

RANGES with fixed counts — a step, not a slope: anywhere in 1k-3k is
exactly 3. Below $1k: 1 (concentrate while small). $10k+: holds at 10
(capped — new checkpoints when the account gets there). SIZING stays
ALL-IN (user choice, Sep 4): each buy deploys 99% of whatever's free —
the first position takes everything, later seats fill via ROTATION
(sell the weakest to fund a >=70% signal). The ladder is the
CONCURRENCY CEILING the reader enforces at take-time on the TOTAL
account value (free + positions at market)."""
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

# ── 1. the ladder math — the user's ranges, stepped ───────────────────
check("$0 -> 1 (concentrate while tiny)", sb.max_positions_for(0) == 1)
check("$999 -> 1", sb.max_positions_for(999) == 1)
check("$1,000 -> 3 (first range opens)", sb.max_positions_for(1_000) == 3)
check("$2,000 -> 3 (mid-range, NOT interpolated toward 5)",
      sb.max_positions_for(2_000) == 3, sb.max_positions_for(2_000))
check("$2,999 -> 3 (the range's last dollar)", sb.max_positions_for(2_999) == 3)
check("$3,000 -> 5 (second range opens)", sb.max_positions_for(3_000) == 5)
check("$4,500 -> 5", sb.max_positions_for(4_500) == 5)
check("$5,000 -> 10 (third range opens)", sb.max_positions_for(5_000) == 10)
check("$10,000 -> 10 (the cap holds)", sb.max_positions_for(10_000) == 10)
check("$50,000 -> 10 (capped — 'and so on' awaits new checkpoints)",
      sb.max_positions_for(50_000) == 10)
check("a broken/zero read concentrates to 1 (fail-closed)",
      sb.max_positions_for(None) == 1 and sb.max_positions_for(0) == 1)
check("the ladder is the user's rule verbatim: (1k,3),(3k,5),(5k,10)",
      sb.POSITION_LADDER == ((1_000.0, 3), (3_000.0, 5), (5_000.0, 10))
      and sb.POSITION_CEILING == 10)

# ── 2. the reader enforces the ladder at take-time ────────────────────
check("the reader's gate uses max_positions_for(_get_total_account_value(kraken))",
      "max_positions_for(_get_total_account_value(kraken))" in rsrc)
check("the static MAX_CONCURRENT_POSITIONS gate is gone from the reader",
      "open_count >= MAX_CONCURRENT_POSITIONS" not in rsrc)
check("the reader resolves the SCOUT's bridge (path insert first)",
      'sys.path.insert(0, str(SCOUT_PATH))' in rsrc)
check("the rejection names the ladder",
      "ladder max %d" in rsrc)

# ── 3. sizing stays ALL-IN (user choice, Sep 4) ───────────────────────
check("the engine deploys trade_pct of available (all-in)",
      "available_quote * (self.config.trade_pct / 100.0)" in esrc)
check("no per-slot division (all-in was the choice)",
      "_acct_v / _slots" not in esrc)
check("later seats fill via ROTATION (>=70% buy + <$100 free)",
      "if confidence >= 70:" in rsrc
      and "attempting rotation" in rsrc)

# ── 4. the already-holding gates ─────────────────────────────────────
check("bridge holding gate (is_already_holding)",
      "is_already_holding(pair)" in rsrc)
check("bot-DB holding gate (get_position_for_asset)",
      "get_position_for_asset(base)" in rsrc)

# ── 5. the rotation — how later seats fill ────────────────────────────
check("rotation runs BEFORE the minimum reject (rotation precedes reject)",
      0 < rsrc.find("attempting rotation") < rsrc.find("Insufficient funds:"))
check("the second rotation: engine Insufficient -> sell weakest -> retry",
      '"Insufficient" in buy_result.get("skipped", "")' in rsrc
      and "Rebalance: sold" in rsrc)
check("rotation closes the sold pair's bridge record",
      "mark_closed_by_pair(weak_pair" in rsrc)
check("a recovering position (own read buy >=50) is never the weakest",
      'sig.action == "buy" and sig.confidence >= 50' in rsrc)
check("a sell-reading position scores weakest (-5)",
      "signal_penalty = -5.0" in rsrc)

# ── 6. the write side: signals flow to the reader (no scanner-side cap) ─
check("the scout bridge writes through at the ceiling (the reader decides)",
      "POSITION_CEILING" in Path("signal_bridge.py").read_text(encoding="utf-8"))

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_positionscale_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
