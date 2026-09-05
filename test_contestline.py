"""Test THE CONTEST ACCOUNT + THE 3% DEATH-LINE GUARD (the rebuild, Sep 1:
'using the rules for the contest you just learned about, we need to start
fresh'). The Kraken Funded rules encoded: -3% death line that never moves,
+12% target, 0.08% round-trip cost, tiers 1k/5k/10k, $1,200/week pace on
the 10k default. The guard: an entry's worst normal case (profiled window
drawdown) eats at most ~1/3 of the buffer — three worst-cases to die,
never one."""
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


import contest_account as ca

# ── 1. the platform rules, encoded ────────────────────────────────────
check("tiers: 1k/$20, 5k/$50, 10k/$90",
      ca.TIERS[1000]["fee"] == 20 and ca.TIERS[5000]["fee"] == 50
      and ca.TIERS[10000]["fee"] == 90)
check("default tier is 10k (user: 'set the default around the 10k contest')",
      ca.DEFAULT_TIER == 10000)
check("the live account state carries a VALID tier (user-selected in the sidebar; "
      "1k selected Sep 2 — the validation-tier plan)",
      ca.load_state()["tier"] in ca.TIERS, ca.load_state()["tier"])
check("the death line: -3% from start, never moves",
      ca.death_line({"start_balance": 10000.0}) == 9700.0
      and ca.death_line({"start_balance": 1000.0}) == 970.0)
check("the challenge target: +12%",
      abs(ca.TIERS[10000]["start"] * 1.12 - 11200.0) < 0.01)
check("the pace: $1,200/week on 10k = $240/day",
      abs(ca.daily_pace_usd({"start_balance": 10000.0}) - 240.0) < 0.01)
check("the cost basis: 0.08% round trip (0.04% each side)",
      ca.COST_ROUNDTRIP_PCT == 0.08)

# ── 2. the guard's arithmetic ─────────────────────────────────────────
# fresh 10k: buffer $300, 1/3 = $100 max risk per trade
# a 2%-drawdown coin -> max entry $5,100
check("fresh 10k + 2% window drawdown -> max entry $5,100",
      abs(ca.max_entry_usd(10000.0, 2.0, 10000.0) - 5100.0) < 1.0,
      ca.max_entry_usd(10000.0, 2.0, 10000.0))
# a 3%-drawdown coin -> $3,400
check("fresh 10k + 3% window drawdown -> max entry $3,400",
      abs(ca.max_entry_usd(10000.0, 3.0, 10000.0) - 3400.0) < 1.0,
      ca.max_entry_usd(10000.0, 3.0, 10000.0))
# the 1k tier: $30 buffer, 2% coin -> $510 — fractional by ARITHMETIC
check("fresh 1k + 2% drawdown -> $510 (fractional entries on small tiers)",
      abs(ca.max_entry_usd(1000.0, 2.0, 1000.0) - 510.0) < 1.0)
# at the line: no new risk at all
check("buffer gone (balance at the line) -> max entry $0",
      ca.max_entry_usd(9700.0, 2.0, 10000.0) == 0.0)
check("buffer gone (balance BELOW the line) -> $0",
      ca.max_entry_usd(9500.0, 2.0, 10000.0) == 0.0)
# three consecutive worst-cases survive: $300 buffer, 3 trades each eating
# $100 of risk (34% frac of a shrinking buffer) never breaches the line
bal, start = 10000.0, 10000.0
for _ in range(3):
    entry = ca.max_entry_usd(bal, 2.0, start)
    bal -= entry * 0.02  # the worst case hits
check("three consecutive worst-cases still leave the account alive",
      bal > ca.death_line({"start_balance": start}), f"balance after 3 worst cases: {bal:.2f}")
# profit grows the buffer (the line never moves from START)
check("profit grows the buffer: 10k -> 10.5k leaves $800 of room",
      abs(ca.buffer({"current_balance": 10500.0, "start_balance": 10000.0}) - 800.0) < 0.01)

# ── 3. entry_size = min(account, book carry, guard) ───────────────────
check("entry size: book caps below the guard",
      ca.entry_size_usd(10000.0, 2000.0, 2.0, 10000.0) == 2000.0)
check("entry size: guard caps below the book",
      abs(ca.entry_size_usd(10000.0, 9000.0, 3.0, 10000.0) - 3400.0) < 1.0)
check("entry size: never above the account",
      ca.entry_size_usd(1000.0, 99999.0, 0.5, 1000.0) <= 1000.0)

# ── 4. the window drawdown read (engine cells, conservative default) ──
dd = ca.window_drawdown_pct("BTC/USD")
check("window drawdown reads the engine (or the 3% conservative default)",
      0.2 < dd <= 15.0, dd)
check("unknown coin fails conservative to 3%",
      ca.window_drawdown_pct("NOSUCHCOIN/USD") == ca.DEFAULT_WINDOW_DD_PCT)

# ── 5. state persistence ──────────────────────────────────────────────
import os
_bak = None
if ca.STATE_FILE.exists():
    _bak = ca.STATE_FILE.read_text(encoding="utf-8")
try:
    s = ca.save_state(10000, 9800.0)
    check("save/load round trip", ca.load_state()["current_balance"] == 9800.0)
    check("start balance follows the tier, not the balance",
          ca.load_state()["start_balance"] == 10000.0)
    check("buffer math on the loaded state",
          abs(ca.buffer() - 100.0) < 0.01)
    s2 = ca.save_state(999, 500.0)  # invalid tier -> default
    check("invalid tier falls back to the default", s2["tier"] == ca.DEFAULT_TIER)
finally:
    if _bak is not None:
        ca.STATE_FILE.write_text(_bak, encoding="utf-8")
    elif ca.STATE_FILE.exists():
        os.remove(ca.STATE_FILE)

# ── 6. the wiring (console + QC + popup) ──────────────────────────────
ap = open("app.py", encoding="utf-8").read()
sc = open("background_scanner.py", encoding="utf-8").read()
check("console: tier selector drives the contest account state",
      "_acct = _ca.save_state(_t, _ca.TIERS[_t][\"start\"])" in ap)
check("console: current balance is the sizing base",
      "cfg.challenge_balance = float(_acct[\"current_balance\"])" in ap)
check("console: the death line is always visible (header LED + account plate)",
      "☠ LINE ${_line:,.0f}" in ap and "☠ CONTEST ACCOUNT" in ap)
check("QC: the 3% guard caps _spend_usd",
      "_spend_usd = min(_spend_usd, _risk_cap)" in ap)
check("QC: at-the-line rejection (the platform death line = no new risk)",
      "AT THE DEATH LINE" in ap and "No! At the death line!" in ap
      and "AT THE DAILY LOCK" not in ap)
check("QC: NO GREEN line names the death line (the only floor, Sep 5)",
      "NO GREEN — AT THE DEATH LINE" in ap)
check("popup: the guard caps the alert's spend and can suppress",
      "_spend = _risk_cap" in sc and "no buy popup" in sc)
check("popup: the sizing base is the LIVE contest account",
      "_bal = _acct_bal" in sc)
check("popup: the guard's message explains the cap",
      "THE 3% GUARD" in sc)
check("guard fails OPEN (broken read -> the old sizing stands)",
      "fail-open" in sc and "fail-open: book sizing stands" in ap)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_contestline_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
