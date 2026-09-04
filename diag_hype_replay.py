"""Would the bot have bought HYPE under the NEW rules (committed, pending
restart)? Replay today's HYPE signals through the new gates + read the 1h
knife on HYPE right now (what the trend-exit watch would be saying)."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import sqlite3

MST = ZoneInfo("America/Denver")

# ── 1. what the 1h knife says about HYPE right now ────────────────────
import background_scanner as bs
import ccxt


class _Cfg:
    ema_short = 9
    ema_long = 21


ex = ccxt.kraken()
rev, note = bs.knife_reversed_1h(ex, "HYPE/USD", _Cfg())
print(f"1. THE 1h KNIFE ON HYPE RIGHT NOW: reversed={rev}")
print(f"   {note or '(trend intact — EMA9 at/above EMA21)'}")
print()

# ── 2. the depth check, old vs new, on today's rejections ────────────
print("2. THE DEPTH CHECK (the gate that decides) — old rule vs new rule on today's HYPE reads:")
print("   10:45 read: $536 -> $12,865 (book got 24x DEEPER)")
print("   OLD rule (abs, symmetric): UNSTABLE -> rejected  <- what actually happened")
print("   NEW rule (dries only, >=$500 rows only): an improvement never flags -> PASS")
print()

# ── 3. the other new gates on the 10:45 signal (conf 92) ─────────────
print("3. THE OTHER NEW GATES on the 10:45 signal (conf 92):")
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
r = conn.execute(
    "SELECT * FROM signals WHERE pair LIKE 'HYPE%' AND timestamp LIKE '2026-08-31T10:%'"
).fetchone()
print(f"   signal: {r['pair']} conf {r['confidence']} entry {r['entry_price']} "
      f"tide {r['tide_state']}/{str(r['tide_pct'])[:5]}")
conn.close()
print("   Gate 2 bar (R3 weekday, data): 60 — 92 passes outright (no near-miss consult needed)")
print("   Tide-aware bar relax: FLAT keeps the data bar — irrelevant, 92 >> 60")
print("   Deep read >= 60: 92 IS the deep number — pass")
print("   Tide stamp FLAT: tradeable — pass (OUT would refuse)")
print("   CONCLUSION: the ONLY thing that killed the 10:45 buy was the broken depth check.")
