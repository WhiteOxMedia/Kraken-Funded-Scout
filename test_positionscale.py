"""Test THE POSITION LADDER + THE DEEP WINNER-LOCK SCAN (user rule, Sep 2:
"'never interrupt a winner' should now trigger a current deep scan... A
growing scale will change the focus from one asset to multiple assets as
the account balance grows. 1k = 2 assets, 5k = 5 and so on up to 10k = 10").
The ladder runs on TOTAL account value (free + positions at market — the
user chose this): below $1k concentrate on ONE pick; slots unlock as the
account compounds; each buy targets total/N so the spread is real."""
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

# ── 1. the ladder math (the user's checkpoints, piecewise between) ────
check("$0 → 1 (concentrate while tiny)", sb.max_positions_for(0) == 1)
check("$500 → 1", sb.max_positions_for(500) == 1)
check("$999 → 1", sb.max_positions_for(999) == 1)
check("$1,000 → 2 (the user's first checkpoint)", sb.max_positions_for(1_000) == 2)
check("$2,500 → 3 (interpolated)", sb.max_positions_for(2_500) == 3, sb.max_positions_for(2_500))
check("$5,000 → 5 (the user's second checkpoint)", sb.max_positions_for(5_000) == 5)
check("$7,500 → 8 (interpolated)", sb.max_positions_for(7_500) == 8, sb.max_positions_for(7_500))
check("$10,000 → 10 (the user's third checkpoint)", sb.max_positions_for(10_000) == 10)
check("$50,000 → 10 (capped)", sb.max_positions_for(50_000) == 10)
check("the ladder is the user's rule verbatim: (1k,2),(5k,5),(10k,10)",
      sb.POSITION_LADDER == ((1_000.0, 2), (5_000.0, 5), (10_000.0, 10))
      and sb.POSITION_CEILING == 10)

# ── 2. the reader enforces the ladder at take-time ────────────────────
rsrc = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py").read_text(encoding="utf-8")
check("the reader's gate uses max_positions_for(_get_total_account_value(kraken))",
      "max_positions_for(_get_total_account_value(kraken))" in rsrc)
check("the old static MAX_CONCURRENT_POSITIONS gate is gone from the reader",
      "open_count >= MAX_CONCURRENT_POSITIONS" not in rsrc)

# ── 3. THE DEEP winner-lock scan (the brain's number decides the lock) ─
fn = rsrc[rsrc.find("def _challenge_weakest"):rsrc.find("def _sell_held_position")]
check("the winner-lock scan is the DEEP read (enable/disable around evaluate)",
      "enable_deep_analysis()" in fn and "disable_deep_analysis()" in fn
      and "evaluate_strategy(df, config)" in fn)
check("the deep flag can never leak (try/finally)",
      fn.find("finally:") < fn.find("disable_deep_analysis()")
      and "finally:" in fn)
check("the lock still breaks only on the chart's sell read",
      'if pnl_pct > 0 and scan_action != "sell":' in fn)

# ── 4. the engine sizes per slot (the spread is real) ─────────────────
esrc = Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\bot_engine.py").read_text(encoding="utf-8")
check("the engine caps each buy at total/slots (POSITION LADDER)",
      "POSITION LADDER" in esrc and "_acct_v / _slots" in esrc)
check("one slot keeps the all-in behavior (guard: _slots > 1)",
      "_slots > 1" in esrc)
check("ladder sizing fails OPEN to all-in on a broken read",
      "fail-open all-in" in esrc)

# ── 5. the write side: signals flow to the reader (no scanner-side cap) ─
check("the scout bridge writes through at the ceiling (the reader decides)",
      "POSITION_CEILING" in Path("signal_bridge.py").read_text(encoding="utf-8"))
check("the bot's twin bridge is synced",
      "POSITION_CEILING" in Path(r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\signal_bridge.py").read_text(encoding="utf-8"))

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_positionscale_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
