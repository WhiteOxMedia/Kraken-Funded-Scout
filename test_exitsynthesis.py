"""Test THE EXIT SYNTHESIS, BOT RULES ONLY (user rules, Sep 4: "every
decision should now be based on the real time data + brain knowledge +
scout history" → "Yes, add that" — then the LEDGER CORRECTION: "the bot
rules are different than the contest rules on purpose — the bot is
optimized to make the most money as possible"). The bot's bridge exit
check (the one whose scout_exit_check sells the bot executes) asks the
BOT'S questions:

  1. REAL-TIME — the ticker seeds the in-progress candle; live price is
     the truth for the trailing stop AND the synthesis
  2. POSITION — entry conviction from the bridge record itself
  3. THESIS — a buy thesis is alive only while the read is buy (or an
     ambiguous hold <60); a sell read or a confident hold kills it

THE GOAL CLOCK IS CONTEST-ONLY: no GOAL REACHED, no WINDOW EXPIRED on
the bot — it doesn't bank at 1-3% or exit on a window; it rides winners
to its own exits (SL −5% → TP +10% → knife → ATR trail → strategy sell
≥75/60 → exhaustion → momentum-rider → THESIS DEAD). The trailing stop
still runs FIRST. POPUPS STAY CONTEST-ONLY — the exit write never
notifies."""
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
import scratch_space   # scratch policy: nothing volatile lands in the repo root

src = Path("background_scanner.py").read_text(encoding="utf-8")
# the 5-min exit check block only
_i0 = src.find("Exit check (every 5 minutes")
blk = src[_i0:src.find("Full tournament", _i0)]

# ── 1. the ledger split: the goal clock is GONE from the bot ─────────
check("GOAL REACHED exit is GONE from the bot's code (contest-only rule)",
      'f"GOAL REACHED' not in blk and "🎯 GOAL REACHED" not in blk)
check("WINDOW EXPIRED exit is GONE from the bot's code (contest-only rule)",
      'f"WINDOW EXPIRED' not in blk and "⏰ WINDOW EXPIRED" not in blk)
check("the bot doesn't read the market goal in its exits (no _market_goal_pct here)",
      "_market_goal_pct" not in blk)
check("the ledger correction is documented at the synthesis",
      "most money as possible" in blk and "is CONTEST-ONLY" in blk)

# ── 2. what the bot's synthesis DOES ask ──────────────────────────────
check("THESIS DEAD exit exists (the reason we entered isn't alive — the ASTER rule)",
      "THESIS DEAD" in blk and "the reason we entered isn't alive" in blk)
check("position conviction from the bridge record",
      's.get("confidence")' in blk)
check("real-time: the ticker still seeds the in-progress candle",
      "_rt_live" in blk and 'df.loc[_rt_idx, "close"] = _rt_live' in blk)
check("the synthesis is fail-soft (a broken record keeps the mechanical gates)",
      "synthesis check failed" in blk)

# ── 3. the bot's own exits all stand ──────────────────────────────────
check("the trailing stop still runs FIRST (protects winners)",
      0 < blk.find("Trailing stop check (runs FIRST") < blk.find("THESIS DEAD"))
check("the mechanical gates still run after the synthesis",
      blk.find("THESIS DEAD") < blk.find("sell_threshold = 60 if is_momentum_pos else 75"))
check("strategy sell bar: 75 / 60 momentum (the bot's own bar, not the contest's 50)",
      "sell_threshold = 60 if is_momentum_pos else 75" in blk)

# ── 4. the thesis semantics ───────────────────────────────────────────
check("thesis alive = buy read OR weak hold <60",
      'exit_sig.action == "buy"\n                                        or (exit_sig.action == "hold" and exit_sig.confidence < 60)' in blk)

# ── 5. popups stay contest-only ───────────────────────────────────────
_wbranch = blk[blk.find("if should_exit:"):]
check("the exit write NEVER pops a desktop alert",
      "NO desktop popup" in _wbranch and "notify_big" not in _wbranch)

# ── 6. behavior simulation (the bot's branch logic, replicated) ──────
def synth(action, conf, trail_hit=False):
    """Replicates the bot's synthesis exactly: trail first, then thesis.
    No goal, no window — the bot rides winners."""
    if trail_hit:
        return True, "trail"
    if not (action == "buy" or (action == "hold" and conf < 60)):
        return True, "thesis"
    return False, ""


ok, why = synth("hold", 70)
check("a confident hold (>=60) against a buy thesis -> THESIS DEAD",
      ok and why == "thesis")
ok, why = synth("sell", 65)
check("a sell read BELOW the 75/60 bar -> THESIS DEAD (the gap this closes)",
      ok and why == "thesis")
ok, why = synth("hold", 45)
check("a weak hold (<60) keeps the thesis (the carve-out)",
      not ok)
ok, why = synth("buy", 55)
check("a buy read keeps the thesis (recovering — the winner RIDES: no goal banks it)",
      not ok)
ok, why = synth("buy", 80, trail_hit=True)
check("the trail decides first — an +8% winner with a live thesis can still be trailed out (the bot's own exit)",
      ok and why == "trail")
ok, why = synth("hold", 80)
check("a strong winner reading confident hold gets out on the DEAD THESIS (weak positions don't ride)",
      ok and why == "thesis")

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_exitsynthesis_check.txt').write_text("\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
