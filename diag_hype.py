"""HYPE post-mortem: why didn't the bot sell the ~12:30 peak? Pull the
bot's actual entry, the candles since, and replay the exit ladder math
(SL/TP/ATR trail) minute by minute to show exactly what the rules did
and didn't do. READ-ONLY."""
import sys
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
MST = ZoneInfo("America/Denver")

# ── 1. the bot's HYPE position (bridge = the contract) ───────────────
conn = sqlite3.connect("signals.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT * FROM signals WHERE pair LIKE 'HYPE%' ORDER BY timestamp DESC LIMIT 3"
).fetchall()
print("1. HYPE BRIDGE RECORDS:")
for r in rows:
    d = dict(r)
    print(f"  {d['timestamp'][:19]}  {d['action']}  conf {d['confidence']}  "
          f"status {d['status']}  entry {d.get('entry_price')}")
    if d.get("executed_at"):
        print(f"      executed {str(d['executed_at'])[:19]} @ {d.get('execution_price')}")
    if d.get("closed_at"):
        print(f"      closed {str(d['closed_at'])[:19]}  pnl {d.get('pnl')}")
conn.close()
print()

# ── 2. candles since 12:00 (5m for precision) ────────────────────────
import ccxt
ex = ccxt.kraken()
o = ex.fetch_ohlcv("HYPE/USD", timeframe="5m", limit=96)  # 8h
print("2. HYPE/USD 5m candles since ~12:00 (O/H/L/C):")
for c in o:
    t = datetime.fromtimestamp(c[0] / 1000, MST)
    if t.hour >= 12:
        print(f"  {t.strftime('%H:%M')}  O {c[1]:.4f}  H {c[2]:.4f}  "
              f"L {c[3]:.4f}  C {c[4]:.4f}")
tk = ex.fetch_ticker("HYPE/USD")
print(f"  NOW: last {tk.get('last')}  24h {tk.get('percentage'):.1f}%")
print()

# ── 3. replay the ladder math from the bot's entry ──────────────────
entry = float(rows[0]["entry_price"]) if rows else 0.0
if entry > 0 and o:
    # bot config values
    SL_PCT, TP_PCT = 5.0, 10.0
    ARM_PCT, ATR_MULT, FEES = 0.5, 4.0, 0.52
    try:
        import pandas as pd
        df = pd.DataFrame(o, columns=["ts", "open", "high", "low", "close", "vol"])
        # ATR(14) on 5m candles — approximation of the bot's 15m ATR (it
        # scales with the candle; we note this caveat)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
    except Exception as e:
        atr = None

    peak = entry
    print(f"3. LADDER REPLAY (entry {entry:.4f}, SL -{SL_PCT}%, TP +{TP_PCT}%, "
          f"trail arms +{ARM_PCT}%, trails {ATR_MULT}x ATR below peak):")
    for i, c in enumerate(o):
        t = datetime.fromtimestamp(c[0] / 1000, MST)
        if t.hour < 12:
            continue
        peak = max(peak, c[2])  # high
        gain = (c[4] - entry) / entry * 100  # close
        peak_gain = (peak - entry) / entry * 100
        a = float(atr.iloc[i]) if atr is not None and i < len(atr) else 0.0
        trail_pct = (a * ATR_MULT / peak) * 100 if a > 0 and peak > 0 else None
        drop = (c[4] - peak) / peak * 100
        fired = []
        if gain <= -SL_PCT:
            fired.append("SL")
        if gain >= TP_PCT:
            fired.append("TP")
        if (trail_pct is not None and peak_gain >= ARM_PCT
                and drop <= -trail_pct and gain > FEES):
            fired.append("TRAIL")
        mark = " <-- " + ",".join(fired) if fired else ""
        # print the interesting window (12:00-13:30) plus any fire
        if t.strftime("%H:%M") <= "13:30" or fired:
            tp_txt = f"{trail_pct:.2f}%" if trail_pct else "n/a"
            print(f"  {t.strftime('%H:%M')}  close {c[4]:.4f}  peak {peak:.4f} "
                  f"(+{peak_gain:.2f}%)  drop {drop:+.2f}%  trail@{tp_txt}{mark}")
