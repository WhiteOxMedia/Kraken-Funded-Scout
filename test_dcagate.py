"""Test THE DCA TRANCHE RETIREMENT (user option 2, Sep 2): the exit-check
loop's buy-the-dip path — scout_dca writes on held real-money positions
that dipped >=2.5% with a quick strategy >=50 — is retired behind
SCOUT_DCA_ENABLED=False. No brain bar, no 3% death-line guard, no goal
clock, and the bot's reader folds those writes into the position (averages
down real money): the wiggle-buy the new game retired. Code KEPT per the
don't-delete rule; the flag stands guard."""
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


import background_scanner as bs

src = open("background_scanner.py", encoding="utf-8").read()

# ── 1. the flag: retired by default ───────────────────────────────────
check("SCOUT_DCA_ENABLED exists and is False (retired)",
      hasattr(bs, "SCOUT_DCA_ENABLED") and bs.SCOUT_DCA_ENABLED is False,
      getattr(bs, "SCOUT_DCA_ENABLED", "MISSING"))
check("the flag is defined with the retirement rationale",
      "SCOUT_DCA_ENABLED = False" in src and "wiggle-buy" in src)

# ── 2. the gate: the write + popup can never fire while retired ───────
check("the DCA condition is gated by the flag (first term — short-circuits)",
      "SCOUT_DCA_ENABLED and not no_buy and dip_pct >= 2.5" in src)
check("the old ungated condition is gone",
      "if (not no_buy and dip_pct >= 2.5" not in src)

# ── 3. don't-delete: the code is kept, just disarmed ──────────────────
check("the scout_dca write code is kept (not deleted)",
      'source="scout_dca"' in src and "write_buy_signal(" in src)
check("the DCA popup code is kept (not deleted)",
      "DCA TRANCHE: BUY MORE" in src)
check("count_dca_signals still imported by the kept block",
      "from signal_bridge import count_dca_signals, write_buy_signal" in src)
check("the retirement comment explains WHY (user option 2, Sep 2)",
      "user option 2, Sep 2" in src and "RETIRED behind SCOUT_DCA_ENABLED=False" in src)

# ── 4. the bot side is untouched (the reader stays as-is — dormant path) ─
BOT_READER = r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py"
rsrc = open(BOT_READER, encoding="utf-8").read()
check("reader unchanged: scout_dca still accepted (dormant — no writer)",
      '"scout_dca"' in rsrc and 'is_dca = source == "scout_dca"' in rsrc)

# ── 5. semantics: with the flag False, the gate is inert ──────────────
_enabled, _dip, _buy, _nodca = bs.SCOUT_DCA_ENABLED, 3.0, "buy", 0
check("the gated condition evaluates False while retired "
      "(dip 3%, strategy buy, no prior DCA — would have fired)",
      not (_enabled and not False and _dip >= 2.5 and _buy == "buy"
           and _nodca == 0))

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_dcagate_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
