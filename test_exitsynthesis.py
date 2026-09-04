"""Test THE EXIT SYNTHESIS ON REAL MONEY (user rule, Sep 4: "every
decision should now be based on the real time data + brain knowledge +
scout history + pop ups + console?" → "Yes, add that. The popups stay
for the contest only."). The scanner's 5-min bridge exit check (the one
whose scout_exit_check sells the bot executes) now asks the SAME
questions the console's EXIT CHECK asks:

  1. REAL-TIME — the ticker seeds the in-progress candle; live price is
     the truth for the trailing stop AND the synthesis
  2. POSITION — entry price, entry clock, entry conviction from the
     bridge record itself (the bot's ledger)
  3. GOAL — the engine's expected move for this coin at this rotation
     (the SAME _window_goal_pct the popup goal clock uses); window =
     goal × 60 min clamped 1-3h
  4. THESIS — a buy thesis is alive only while the read is buy (or an
     ambiguous hold <60); a sell read or a confident hold kills it —
     the console's exact semantics

New exit reasons: GOAL REACHED / WINDOW EXPIRED / THESIS DEAD. The
mechanical gates (trailing stop first, then strategy sell >=75/60,
exhaustion, momentum-rider) all still run. POPUPS STAY CONTEST-ONLY —
the exit write never notifies."""
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

src = Path("background_scanner.py").read_text(encoding="utf-8")
# the 5-min exit check block only
_i0 = src.find("Exit check (every 5 minutes")
blk = src[_i0:src.find("Full tournament", _i0)]

# ── 1. the three new exit reasons exist ──────────────────────────────
check("GOAL REACHED exit exists (take the profit at the market analysis)",
      "GOAL REACHED" in blk and "take the profit" in blk)
check("WINDOW EXPIRED exit exists (it hasn't paid, get out)",
      "WINDOW EXPIRED" in blk and "it hasn't paid, get out" in blk)
check("THESIS DEAD exit exists (the reason we entered isn't alive)",
      "THESIS DEAD" in blk and "the reason we entered isn't alive" in blk)

# ── 2. the inputs ─────────────────────────────────────────────────────
check("position data from the bridge record (entry price + clock + conviction)",
      "s.get(\"execution_price\") or s.get(\"entry_price\")" in blk
      and "s.get(\"executed_at\")" in blk and "s.get(\"confidence\")" in blk)
check("the goal is the SAME engine number the popup clock uses",
      "_window_goal_pct(pair)" in blk)
check("window math matches the popup (goal x 60, clamped 60-180)",
      "max(60.0, min(180.0, _syn_goal * 60.0))" in blk)
check("real-time: the ticker still seeds the in-progress candle",
      "_rt_live" in blk and 'df.loc[_rt_idx, "close"] = _rt_live' in blk)
check("the synthesis is fail-soft (a broken record keeps the mechanical gates)",
      "synthesis check failed" in blk)

# ── 3. the ordering ───────────────────────────────────────────────────
check("the trailing stop still runs FIRST (protects winners)",
      0 < blk.find("Trailing stop check (runs FIRST") < blk.find("GOAL REACHED"))
check("the mechanical gates still run after the synthesis",
      blk.find("THESIS DEAD") < blk.find("sell_threshold = 60 if is_momentum_pos else 75"))
check("goal reached outranks window expiry (bank the profit when it's there)",
      blk.find("GOAL REACHED") < blk.find("WINDOW EXPIRED") < blk.find("THESIS DEAD"))

# ── 4. the thesis semantics = the console's exact logic ──────────────
check("thesis alive = buy read OR weak hold <60 (the console's semantics)",
      'exit_sig.action == "buy"\n                                          or (exit_sig.action == "hold" and exit_sig.confidence < 60)' in blk)
ap = Path("app.py").read_text(encoding="utf-8")
check("console agreement: the same hold<60 carve-out in the EXIT CHECK",
      'sig.action == "hold" and sig.confidence < 60' in ap)

# ── 5. popups stay contest-only ───────────────────────────────────────
_wbranch = blk[blk.find("if should_exit:"):]
check("the exit write NEVER pops a desktop alert",
      "NO desktop popup" in _wbranch and "notify_big" not in _wbranch)

# ── 6. behavior simulation (the branch logic, replicated) ────────────
def synth(pnl_pct, goal_pct, elapsed_min, action, conf, trail_hit=False):
    """Replicates the scanner's synthesis branch order exactly."""
    should_exit, reason = False, ""
    if trail_hit:
        return True, "trail"
    win_min = max(60.0, min(180.0, goal_pct * 60.0))
    if pnl_pct >= goal_pct:
        return True, "goal"
    if elapsed_min is not None and elapsed_min >= win_min and pnl_pct < goal_pct:
        return True, "window"
    if not (action == "buy" or (action == "hold" and conf < 60)):
        return True, "thesis"
    return False, ""


ok, why = synth(2.0, 1.5, 30, "hold", 70)
check("+2.0% vs 1.5% goal -> GOAL (even a confident hold banks it)",
      ok and why == "goal")
ok, why = synth(0.4, 1.5, 200, "buy", 80)
check("3.3h held, +0.4% vs 1.5% goal -> WINDOW EXPIRED",
      ok and why == "window")
ok, why = synth(0.4, 1.5, 200, "buy", 80, trail_hit=True)
check("the trail outranks everything",
      ok and why == "trail")
ok, why = synth(0.4, 1.5, 45, "sell", 65)
check("a sell read BELOW the 75/60 bar -> THESIS DEAD (the gap this closes)",
      ok and why == "thesis")
ok, why = synth(0.4, 1.5, 45, "hold", 72)
check("a confident hold (>=60) against a buy thesis -> THESIS DEAD",
      ok and why == "thesis")
ok, why = synth(0.4, 1.5, 45, "hold", 45)
check("a weak hold (<60) keeps the thesis (the console's carve-out)",
      not ok)
ok, why = synth(0.4, 1.5, 45, "buy", 55)
check("a buy read keeps the thesis (recovering — the rotation's skip rule agrees)",
      not ok)
ok, why = synth(1.4, 1.5, 45, "hold", 80)
check("1.4% vs 1.5% goal, still inside the 90-min window -> thesis decides",
      ok and why == "thesis")
ok, why = synth(-2.0, 1.5, 30, "buy", 70)
check("a fresh loser with a live buy thesis holds (SL/trail still guard it)",
      not ok)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_exitsynthesis_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
