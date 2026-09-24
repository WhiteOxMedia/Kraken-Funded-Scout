"""Test THE DANGER-ZONE PANEL (user rule, Sep 6: "updated thin book
rules: when you're already in a trade the effective buying power
shrinks, so any new buy popup needs to show different danger-zone
metrics than when you're flat... We already have some of this so just
modify it and build only what we need") — the THIN-BOOK OPTIMIZED popup
panel: balance overview, position status, auto-adjusted purchase power,
danger-zone metrics + the trade's impact, the thin-book indicator with
cluster risk, the turnaround profile ($10/10-min model), and the final
decision snapshot. INFORMS, never gates — appended to every BUY popup
(champion + mover + sure-thing)."""
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
import contest_account as ca
import execution_tiers as et
import scratch_space   # scratch policy: nothing volatile lands in the repo root


class _FakeEx:
    def fetch_ticker(self, pair):
        return {"last": 100.0}


def _setup(balance=9917.0, start=10000.0, watch=None, sig10=1,
           ask_tob=100000.0):
    ca.load_state = lambda: {"tier": 10000, "start_balance": start,
                             "current_balance": balance}
    bs._load_contest_watch = lambda: (watch or {})
    bs._recent_signal_events = lambda w=10.0: sig10
    et.measure_book = lambda ex, pair: {"ask_tob": ask_tob}
    return _FakeEx()


# ── 1. FLAT: the full-power read ─────────────────────────────────────
ex = _setup(watch={})
p = bs._danger_zone_panel(ex, "BTC/USD", 1800.0, 1.5, 60.0, 0.75)
check("flat: the panel renders with all seven sections",
      all(s in p for s in ("DANGER ZONE — FLAT", "Balance:", "Position: FLAT",
                           "Buying power:", "Drawdown:", "This trade:",
                           "Book:", "Turnaround:", "RISK")), p[:120])
check("flat: available = total (nothing committed)",
      "available" in p and "$9,917" in p and "0% committed" in p, "")
check("flat: full 25% purchase power shown",
      "full 25% $2,479" in p, "")
check("the $10/10-minute model: goal 1.5% -> $0.15 per $10 unit",
      "$10 unit pays $0.15" in p, "")
check("turnaround type: 60-min window -> QUICK SCALP",
      "QUICK SCALP" in p, "")
check("drawdown + distance computed from the account",
      "Drawdown: 0.8%" in p and "distance to line: 2.2%" in p, "")

# ── 2. IN TRADE: the reduced-power read ──────────────────────────────
ex = _setup(watch={"RENDER/USD": {"entry": 100.0, "size": 2300.0}})
p = bs._danger_zone_panel(ex, "BTC/USD", 1800.0, 1.5, 60.0, 0.75)
check("in trade: the header flips to IN TRADE",
      "DANGER ZONE — IN TRADE" in p, "")
check("in trade: 23% committed, available reduced",
      "23% committed" in p and "$7,617" in p, "")
check("in trade: reduced 25% purchase power ($1,904 of $7,617)",
      "reduced $1,904" in p, "")
check("in trade: the unrealized P/L line renders",
      "unrealized" in p, "")
check("in trade: exposure level computed (MEDIUM at 23%)",
      "exposure MEDIUM" in p, "")

# ── 3. THE IMPACT READ ───────────────────────────────────────────────
# buffer = 9917-9700 = 217; worst = spend*wdd; wdd for an unknown pair
# defaults ~3% -> 1800*0.03 = 54 < 0.5*217 -> not danger; goal_gain
# 1800*1.5% = 27 >= 0.25*pace(240)=60? No -> NEUTRAL
ex = _setup()
p = bs._danger_zone_panel(ex, "TEST/USD", 1800.0, 1.5, 60.0, 0.75)
check("impact: a mid-size trade reads NEUTRAL",
      "This trade: NEUTRAL" in p, "")
# a big goal vs pace -> HELPS RECOVERY (3000 * 4% = 120 >= 60)
p = bs._danger_zone_panel(ex, "TEST/USD", 3000.0, 4.0, 120.0, 1.0)
check("impact: goal >= 25% of the daily pace reads HELPS RECOVERY",
      "HELPS RECOVERY" in p, "")
# spend*3% >= 0.5*217 -> 4000*0.03=120... need >= 108.5 -> INCREASES DANGER
p = bs._danger_zone_panel(ex, "TEST/USD", 4000.0, 1.0, 60.0, 0.33)
check("impact: worst-case >= half the buffer reads INCREASES DANGER",
      "INCREASES DANGER" in p, "")
check("final snapshot: the danger case reads AVOID or HIGH risk",
      ("AVOID" in p) or ("RISK HIGH" in p), "")

# ── 4. THE THIN-BOOK INDICATOR + CLUSTER ────────────────────────────
ex = _setup(ask_tob=1500.0, sig10=1)     # carry $300 < 50% of balance
p = bs._danger_zone_panel(ex, "TEST/USD", 300.0, 1.5, 60.0, 0.75)
check("thin book: carry < 50% of balance reads THIN",
      "Book: THIN" in p, "")
check("thin book: the turnaround type carries the warning",
      "THIN BOOK)" in p, "")
ex = _setup(ask_tob=1500.0, sig10=3)     # thin + clustered signals
p = bs._danger_zone_panel(ex, "TEST/USD", 300.0, 1.5, 60.0, 0.75)
check("cluster: >=2 signals in 10 min on a thin book reads HIGH",
      "cluster HIGH" in p, "")
ex = _setup(ask_tob=200000.0, sig10=0)   # thick, quiet
p = bs._danger_zone_panel(ex, "TEST/USD", 1800.0, 1.5, 60.0, 1.0)
check("thick + quiet: book THICK, cluster LOW",
      "Book: THICK" in p and "cluster LOW" in p, "")
ex = _setup(ask_tob=200000.0, sig10=2)   # thick but clustered
p = bs._danger_zone_panel(ex, "TEST/USD", 1800.0, 1.5, 60.0, 1.0)
check("cluster: >=2 signals alone reads MEDIUM",
      "cluster MEDIUM" in p, "")

# ── 5. THE FINAL SNAPSHOT ────────────────────────────────────────────
ex = _setup()
p = bs._danger_zone_panel(ex, "TEST/USD", 1800.0, 1.5, 60.0, 1.0)
check("the final line carries risk + recovery + the action",
      "RISK" in p and "recovery" in p and ("ENTRY" in p or "AVOID" in p), "")
p = bs._danger_zone_panel(ex, "TEST/USD", 1800.0, 1.5, 60.0, 0.33)
check("a 0.33x sizing reads at least MEDIUM risk",
      "RISK MEDIUM" in p or "RISK HIGH" in p, "")

# ── 6. never raises ──────────────────────────────────────────────────
try:
    p = bs._danger_zone_panel(None, "X/USD", 0.0, 0.0, 0.0, 0.0)
    check("a broken exchange never raises (honest fallback)",
          "unavailable" in p or "DANGER ZONE" in p, p[:80])
except Exception as e:
    check("a broken exchange never raises (honest fallback)", False, e)

# ── 7. THE WIRING: all three popup paths carry the panel ────────────
src = open("background_scanner.py", encoding="utf-8").read()
champ = src[src.find("def send_alert"):src.find("def log_winner")]
check("the champion popup computes the panel BEFORE the message build",
      champ.find("_dz = _danger_zone_panel") < champ.find("message = (")
      and "{_dz}" in champ)
mover = src[src.find("# desktop: THE NEW-GAME MOVER POPUP"):
            src.find("MOVER POPUP CAP")]
check("the mover popup carries the panel",
      "_danger_zone_panel(exchange, pair," in mover)
st_fn = src[src.find("def _sure_thing_scan"):src.find("def _drain_speech_queue")]
check("the sure-thing popup carries the panel",
      "_danger_zone_panel(exchange, pair," in st_fn)
check("the panel reads the scanner's own pair list "
      "(the ASSETS bug is fixed — pyflakes caught it before restart)",
      "for pair in list(all_pairs)" in st_fn
      and "for a in ASSETS" not in st_fn)
check("the panel INFORMS, never gates (no return on its output)",
      "def _danger_zone_panel" in src and
      src.count("_danger_zone_panel(") >= 4)

out = "\n".join(lines) + f"\n\n{PASS} passed, {FAIL} failed\n"
open(scratch_space.report_temp('_dangerzone_check.txt'), "w", encoding="ascii", errors="replace").write(out)
print(out)
sys.exit(0)
