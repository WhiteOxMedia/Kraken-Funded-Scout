"""diag_bump.py — was a bumped pair CLOSE to a pass? Reproduces every popup
gate with live data and prints each gate's margin (the scanner's visible
window keeps no logs, so we re-run the evaluation the alert path ran).
Usage: python diag_bump.py HBAR/USD"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ccxt

PAIR = sys.argv[1] if len(sys.argv) > 1 else "HBAR/USD"
ASSET = PAIR.split("/")[0]

ex = ccxt.kraken({"enableRateLimit": True})
import background_scanner as bs
import contest_account as ca
from strategy import evaluate_strategy, enable_deep_analysis, disable_deep_analysis
import pandas as pd

print(f"=== {ASSET} — gate-by-gate popup evaluation (live) ===\n")

# ── 1. the reads ──────────────────────────────────────────────────────
ohl = ex.fetch_ohlcv(PAIR, timeframe="1h", limit=200)
df = pd.DataFrame(ohl, columns=["timestamp", "open", "high", "low", "close", "volume"])
df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
df.set_index("timestamp", inplace=True)

from config import TradingConfig
cfg = TradingConfig()
quick = evaluate_strategy(df, cfg)
enable_deep_analysis()
try:
    deep = evaluate_strategy(df, cfg)
finally:
    disable_deep_analysis()
print(f"QUICK read:  {quick.action} {quick.confidence:.0f}%")
print(f"DEEP read:   {deep.action} {deep.confidence:.0f}%   <- the number that matters")

# ── 2. the brain-judged bar ───────────────────────────────────────────
_tp, _ts = bs._fleet_tide()
floor = 89 if _ts == "FLAT" else (78 if _ts == "RISING" else 89)
lw_pts, lw_note = bs.lone_wolf_discount(ex, PAIR)
floor = max(55, floor - lw_pts) if lw_pts else floor
print(f"\nTide: {_ts} ({_tp:.0f}% fleet up)" + (f"  |  LONE WOLF −{lw_pts}: {lw_note}" if lw_pts else ""))
print(f"Brain bar: deep {deep.confidence:.0f}% vs floor {floor}  ->  "
      f"{'PASS' if deep.action == 'buy' and deep.confidence >= floor else 'FAIL by ' + f'{floor - deep.confidence:.0f} pts'}")

# ── 3. the stale-move guard (re-entries) ──────────────────────────────
st = bs._stale_move(ex, PAIR)
print(f"Stale guard: {'STALE — 4h high %.0fh ago, %.1f%% below (FAIL)' % st if st else 'fresh / not stale (PASS)'}")

# ── 4. the goal clock + the goal gate ─────────────────────────────────
goal = bs._window_goal_pct(PAIR)
win_min = max(60.0, min(180.0, goal * 60.0))
try:
    from volatility_engine import get_move_threshold
    from background_scanner import _rotation_window, _now_mst, _schedule_for_dow
    now = bs._now_mst()
    w = bs._rotation_window(now)
    raw = get_move_threshold(PAIR, w[2] if w else "08-12", bs._schedule_for_dow(now.weekday()), level="standard")
except Exception:
    raw = None
print(f"\nGoal: +{goal:.1f}% (engine raw move {raw if raw is not None else '?'}%) on a {win_min/60:.1f}h window"
      f"  ->  goal gate {'PASS' if raw is None or float(raw) >= 1.0 else f'FAIL: raw {raw}% < 1%'}")

# ── 5. the 3% guard sizing ────────────────────────────────────────────
acct = ca.load_state()
bal = float(acct["current_balance"])
buf = ca.buffer(acct)
dd = ca.window_drawdown_pct(PAIR)
risk_cap = ca.max_entry_usd(bal, dd, float(acct["start_balance"]))
print(f"\nAccount: ${bal:,.0f} (buffer ${buf:,.0f}) | {ASSET} window drawdown {dd:.2f}%")
print(f"3% guard cap: ${risk_cap:,.0f}  ->  {'PASS' if risk_cap > 0 else 'FAIL: buffer gone'}")

# ── 6. the book carry ─────────────────────────────────────────────────
try:
    from execution_tiers import measure_book
    book = measure_book(ex, PAIR)
    tob = float((book or {}).get("ask_tob") or 0)
    carry = tob * 0.20 if tob > 0 else None
    if carry is not None:
        fit = [c * bal for c in (0.25, 0.50, 0.75, 1.00) if c * bal <= carry]
        spend_carry = max(fit) if fit else 0.0
    else:
        spend_carry = None
    print(f"Book: ask_tob ${tob:,.0f} -> carry ${carry:,.0f} -> carry-capped spend "
          f"{'$%.0f' % spend_carry if spend_carry is not None else 'unreadable'}"
          f"  ->  {'PASS' if spend_carry is None or spend_carry > 0 else 'FAIL: too thin'}")
except Exception as e:
    spend_carry = None
    print(f"Book: unreadable ({e}) — fail-open")

# ── 7. THE WORTH FLOOR (the dial) ─────────────────────────────────────
spend = min(risk_cap, spend_carry) if spend_carry else risk_cap
pace = ca.daily_pace_usd(acct)
frac = bs._worth_floor_frac()
worth = spend * (goal / 100.0)
floor_usd = pace * frac
print(f"\nWorth floor (dial {frac:.0%}): ${spend:,.0f} x +{goal:.1f}% = ${worth:,.2f} vs ${floor_usd:,.0f} minimum"
      f"  ->  {'PASS' if worth >= floor_usd else 'FAIL by $%.0f' % (floor_usd - worth)}")

print("\n(the bot-side challenge is separate: the bridge write always happens first)")
