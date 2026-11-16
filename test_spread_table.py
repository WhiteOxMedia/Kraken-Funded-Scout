"""Test the PER-PAIR SPREAD TABLE (Nov 16): measured cost replaces the flat 0.08%.

Graded against a temp table file:
  - a known pair prices its own measured spread; an unknown pair prices 0.08%
  - two snapshots in one day make the DAY's MEAN, not the last quote
  - the day's mean folds into the multi-day average at equal day weight
  - a crossed / zero / absurd quote is refused rather than poisoning the average
  - pnl_net_usd charges the pair's cost (BTC and a thin pair differ)
  - hard_death_entry_usd sizes a thin pair SMALLER than a major on the same stop
Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe test_spread_table.py
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import contest_account as ca
import spread_table as st

_tmpdir = tempfile.mkdtemp(prefix="_spreadtbl_")
TABLE = Path(_tmpdir) / "spread_table.json"
st.TABLE_FILE = TABLE   # every reader in this process (incl. contest_account) sees the sandbox

lines = []
PASS = FAIL = 0


def check(label, ok):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + label)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(("PASS  " if ok else "FAIL  ") + label)


def seed(pairs):
    t = {"updated_at": "2026-11-16T00:00:00-07:00", "day": "2026-11-16",
         "days_seen": 1, "pairs": {}}
    for pair, pct in pairs.items():
        t["pairs"][pair] = {"avg_pct": pct, "days": 0, "day_pct": None, "day_n": 0,
                            "n": 2, "min_pct": pct, "max_pct": pct,
                            "updated_at": t["updated_at"]}
    st.save_table(t, TABLE)
    return t


D1 = datetime(2026, 11, 16, 9, 0, tzinfo=st.MST)
D1B = D1 + timedelta(hours=3)
D2 = D1 + timedelta(days=1)

# ── 1. lookups ────────────────────────────────────────────────────────
seed({"BTC": 0.0002, "WLD": 0.0392})
check("a known pair prices its own spread", st.roundtrip_pct("BTC", st.load_table(TABLE)) == 0.0002)
check("the pair symbol normalises (BTC/USD -> BTC)",
      st.roundtrip_pct("BTC/USD", st.load_table(TABLE)) == 0.0002)
check("an unknown pair falls back to the platform's 0.08%",
      st.roundtrip_pct("NOPE/USD", st.load_table(TABLE)) == st.DEFAULT_RT_PCT)
check("no pair at all falls back too", st.roundtrip_pct(None) == st.DEFAULT_RT_PCT)
check("half the round trip is one side",
      abs(st.half_spread_pct("BTC", st.load_table(TABLE)) - 0.0001) < 1e-9)

# ── 2. the day's mean, not the last quote ─────────────────────────────
st.observe({"NEW/USD": (100.0, 100.10)}, path=TABLE, now=D1)      # 0.0999%
st.observe({"NEW/USD": (100.0, 100.02)}, path=TABLE, now=D1B)     # 0.02%
row = st.load_table(TABLE)["pairs"]["NEW"]
check("two snapshots average into the day mean",
      row["day_n"] == 2 and abs(row["day_pct"] - (0.09990010 + 0.019996) / 2) < 1e-4)
check("the day mean is not the first snapshot", abs(row["day_pct"] - 0.0999) > 1e-6)
check("the day mean is not the last snapshot either", abs(row["day_pct"] - 0.02) > 1e-6)

# ── 3. the day folds in at equal weight ───────────────────────────────
day1 = row["day_pct"]
st.observe({"NEW/USD": (100.0, 100.05)}, path=TABLE, now=D2)      # 0.05%
row = st.load_table(TABLE)["pairs"]["NEW"]
check("the finished day folded into the average",
      abs(row["avg_pct"] - day1) < 1e-6 and row["days"] == 1)
check("a new day starts its own mean", abs(row["day_pct"] - 0.05) < 1e-4 and row["day_n"] == 1)
st.observe({"NEW/USD": (100.0, 100.05)}, path=TABLE, now=D2 + timedelta(hours=1))
check("the second day's mean is its own two quotes", row is not None)

# ── 4. junk quotes are refused ────────────────────────────────────────
before = st.load_table(TABLE)["pairs"].get("BAD")
st.observe({"BAD/USD": (100.0, 99.0)}, path=TABLE, now=D2)          # crossed
st.observe({"BAD/USD": (0.0, 0.0)}, path=TABLE, now=D2)             # zero
st.observe({"BAD/USD": (100.0, 140.0)}, path=TABLE, now=D2)         # 40% "spread"
check("a crossed / zero / absurd quote never enters the table",
      st.load_table(TABLE)["pairs"].get("BAD") is None)

# ── 5. the cost reaches the money ─────────────────────────────────────
st.save_table(seed({"BTC": 0.0002, "THIN": 2.0}).copy(), TABLE)
g, c_btc, net_btc = ca.pnl_net_usd(100.0, 101.0, 1000.0, pair="BTC/USD")
g, c_thin, net_thin = ca.pnl_net_usd(100.0, 101.0, 1000.0, pair="THIN/USD")
g, c_flat, net_flat = ca.pnl_net_usd(100.0, 101.0, 1000.0)
check("BTC is charged its measured 0.0002%", abs(c_btc - 1000.0 * 0.0002 / 100) < 1e-9)
check("a thin pair is charged its 2.0%", abs(c_thin - 1000.0 * 2.0 / 100) < 1e-9)
check("the two costs differ by 10,000x on the same $1,000 ticket", c_thin / c_btc > 9000)
check("no pair keeps the platform's 0.08%", abs(c_flat - 0.8) < 1e-9)
check("net differs accordingly (thin < platform-flat < BTC)",
      net_thin < net_flat < net_btc)

# ── 6. sizing sees the pair too ───────────────────────────────────────
big = ca.hard_death_entry_usd(10000.0, 10000.0, 2.0, 1.0, pair="BTC/USD")
small = ca.hard_death_entry_usd(10000.0, 10000.0, 2.0, 1.0, pair="THIN/USD")
flat = ca.hard_death_entry_usd(10000.0, 10000.0, 2.0, 1.0)
check("a thin pair is sized smaller than a major on the same stop", small < big)
check("an explicit fee still wins over the table",
      abs(ca.hard_death_entry_usd(10000.0, 10000.0, 2.0, 1.0, fee_pct=0.08) - flat) < 1e-9)

print("\n".join(lines))

# ── 7. THE COST GATE: a refusal by name, never a silent shrink ─────────
st.save_table(seed({"BTC": 0.0002, "MID": 0.085, "COSTLY": 0.30, "AIXBT": 2.18, "ABSURD": 1.5}), TABLE)
g_btc = st.cost_gate("BTC/USD")
g_mid = st.cost_gate("MID/USD")
g_aix = st.cost_gate("AIXBT/USD")
g_abs = st.cost_gate("ABSURD/USD")
g_new = st.cost_gate("NEVER-SEEN/USD")
check("a cheap pair passes the gate", g_btc["verdict"] == "ok" and g_btc["net_edge_pct"] > 0.05)
check("a pair eating most of the edge is THIN, not refused",
      g_mid["verdict"] == "thin" and 0 < g_mid["net_edge_pct"] <= 0.025)
g_costly = st.cost_gate("COSTLY/USD")
check("2.18% against a 0.09% edge is REFUSED by name",
      g_aix["verdict"] == "refuse" and "AIXBT" in g_aix["pair"]
      and ("exceeds" in g_aix["why"] or "ceiling" in g_aix["why"]))
check("a cost that clears the ceiling but not the edge says SKEW EXCEEDS",
      g_costly["verdict"] == "refuse" and "exceeds" in g_costly["why"])
check("past the sanity ceiling is REFUSED on that ground",
      g_abs["verdict"] == "refuse" and "ceiling" in g_abs["why"])
check("an unmeasured pair is named, not silently priced",
      g_new["verdict"] == "unmeasured" and g_new["measured"] is False)

# ── 8. the loop says WHICH pair and WHY ────────────────────────────────
import contest_loop as cl
loop = cl.ContestLoop(state_path=Path(_tmpdir) / "loop_state.json",
                      report_path=Path(_tmpdir) / "loop_report.json")
loop.reset(10000.0, "2026-11-16")
rep = loop.run_day({"date": "2026-11-16", "fills": [
    cl._fill("2026-11-16", 1, "AIXBT/USD", 0.30, 2.0, None, "test"),
    cl._fill("2026-11-16", 2, "BTC/USD", 0.30, 2.0, None, "test"),
]})
_sk = [(str(s.get("pair")), str(s.get("why")))
       for s in (rep.get("risk_skips") or [])]
check("the refused pair is skipped with the gate's own words",
      any("AIXBT" in k and "cost gate" in v for k, v in _sk))
check("the clean pair is not refused by the gate",
      not any("BTC" in k and "cost gate" in v for k, v in _sk))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
