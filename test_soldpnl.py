"""Test THE SOLD AUTO-PnL (user rule, Sep 2: "stamp the real sold time and
auto calculate the PnL to subtract it from the balance; and auto fill when
the button is pushed, I can manually self adjust it later"). The SOLD tap:
stamps the sold time, computes the round trip (0.08% RT cost included — it
counts toward the target AND the death line), adjusts the contest balance,
logs the trade to contest_trades.jsonl, and speaks the new balance. No size
or no live price → the manual reminder path stands."""
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

# ── 1. the round-trip math ────────────────────────────────────────────
g, c, n = ca.pnl_net_usd(100.0, 102.0, 1000.0)
check("win: entry 100 -> exit 102 on $1,000: gross +$20.00",
      abs(g - 20.0) < 0.005, round(g, 2))
check("win: the 0.08% RT cost is $0.80 (the platform's only cost)",
      abs(c - 0.80) < 0.005, round(c, 2))
check("win: net +$19.20 — what the balance actually moved",
      abs(n - 19.20) < 0.005, round(n, 2))

g2, c2, n2 = ca.pnl_net_usd(100.0, 99.0, 1000.0)
check("loss: entry 100 -> exit 99: gross -$10, net -$10.80 (cost still paid)",
      abs(g2 + 10.0) < 0.005 and abs(n2 + 10.80) < 0.005, round(n2, 2))

g3, c3, n3 = ca.pnl_net_usd(100.0, 100.0, 1000.0)
check("flat scratch still pays the spread: net -$0.80",
      abs(n3 + 0.80) < 0.005, round(n3, 2))

g4, c4, n4 = ca.pnl_net_usd(0.72655, 0.73735, 233.0)
check("ASTER-sized case: 0.72655 -> 0.73735 on $233 (+1.487%): net ≈ +$3.28",
      abs(n4 - 3.28) < 0.05, round(n4, 2))

# ── 2. the SOLD handler wiring (source checks) ───────────────────────
ap = open("app.py", encoding="utf-8").read()
sold_blk = ap[ap.find("SOLD buttons"):ap.find("contest watch list")]
check("SOLD computes the PnL via contest_account.pnl_net_usd",
      "_ca_s.pnl_net_usd" in sold_blk)
check("SOLD auto-fills the balance (save_state with current + net)",
      "_ca_s.save_state" in sold_blk and "+ _net" in sold_blk)
check("the trade is logged with a SOLD-TIME stamp",
      "contest_trades.jsonl" in sold_blk and '"sold_at"' in sold_blk)
check("the log carries entry/exit/size/pnl/cost/balance/goal/held",
      '"pnl_net"' in sold_blk and '"cost_rt"' in sold_blk
      and '"balance_after"' in sold_blk and '"held_min"' in sold_blk)
check("her voice speaks the NET and the new balance",
      "dollars net!" in sold_blk and "The balance is now" in sold_blk)
check("she still says the user can hand-correct (self-adjust later)",
      "Adjust me in the sidebar if that's off!" in sold_blk)
check("no size / no live price → the manual reminder path stands",
      "Update the contest balance in the sidebar" in sold_blk)
check("losses SUBTRACT from the balance (net can be negative)",
      "float(_acct_s[\"current_balance\"]) + _net" in sold_blk)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_soldpnl_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
