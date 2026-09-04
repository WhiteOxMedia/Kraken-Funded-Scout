"""PUMP vs BTC fact check: what the gates did, what the bars are,
and what price actually did since."""
import sys, json, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"now: {now.strftime('%a %I:%M %p')} Denver", ""]

# ── 1. bridge records for PUMP/BTC since noon ─────────────────────
conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
for asset in ("PUMP", "BTC"):
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence, entry_price, execution_reason "
        "FROM signals WHERE pair LIKE ? AND timestamp >= '2026-08-24T12:00' ORDER BY timestamp",
        (f"%{asset}%",)).fetchall()
    lines.append(f"bridge records {asset} since 12:00: {len(rows)}")
    for r in rows:
        lines.append(f"  {r['timestamp'][:19]} {r['action']} {r['status']:<9} src={r['source']:<13} "
                     f"conf={r['confidence']:.0f} entry={r['entry_price']} "
                     f"reason={(r['execution_reason'] or '')[:60]}")
    lines.append("")
conn.close()

# ── 2. engine bars for both (current rotation cell) ────────────────
try:
    from volatility_engine import load_rotation_thresholds, ROTATIONS
    doc = load_rotation_thresholds(max_age_s=600)
    rot = next((k for s, e, k, *_ in ROTATIONS if s <= now.hour < e), None)
    sched = "weekend" if now.weekday() >= 5 else "weekday"
    lines.append(f"engine cells (rotation={rot}, {sched}):")
    for pair in ("PUMP/USD", "BTC/USD"):
        cell = (doc.get("universe", {}).get(pair, {}).get(sched, {}).get(rot) or {})
        avg = float(cell.get("avg_move") or 0)
        fbr = float(cell.get("false_breakout_rate") or 0)
        bar = 60 + 35 * fbr
        lines.append(f"  {pair:<10} avg_move={avg:.2f}%  false_breakout={fbr:.0%}  "
                     f"bar=60+35x{fbr:.2f}={bar:.0f}  $20-ETA={(0.4/avg*4 if avg>0 else 0):.1f}h")
    lines.append("")
except Exception as e:
    lines.append(f"engine lookup failed: {e}")

# ── 3. price action since ~12:00 (public 15m candles) ─────────────
import ccxt
ex = ccxt.kraken()
for pair in ("PUMP/USD", "BTC/USD"):
    try:
        ohlcv = ex.fetch_ohlcv(pair, "15m", limit=40)
        candles = [(datetime.fromtimestamp(c[0] / 1000, MST), c[1], c[2], c[3], c[4]) for c in ohlcv]
        noon = [c for c in candles if c[0].hour >= 12]
        if not noon:
            noon = candles[-16:]
        base_price = noon[0][1]  # open of first post-noon candle
        lines.append(f"{pair} since {noon[0][0].strftime('%H:%M')} (open {base_price}):")
        # $20 scalp = +0.4%: count distinct rally legs of +0.4% off local lows
        legs = 0
        low_ref = None
        for dt, o, h, l, c in noon:
            if low_ref is None or l < low_ref:
                low_ref = l
            if low_ref and (h - low_ref) / low_ref >= 0.004:
                legs += 1
                lines.append(f"    ${low_ref:.5f} -> ${h:.5f}  +{(h-low_ref)/low_ref:.2%}  "
                             f"($20 window, ended by {dt.strftime('%H:%M')})")
                low_ref = None
        last = noon[-1][4]
        lines.append(f"    now {last:.5f} ({(last-base_price)/base_price:+.2%} from noon open), "
                     f"$20-legs counted: {legs}")
        lines.append("")
    except Exception as e:
        lines.append(f"{pair} fetch failed: {e}")

out = BASE / "pump_btc_facts.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
