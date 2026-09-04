"""Why did DOGE fire in a downtrend without an ETA? Facts first."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"now: {now.strftime('%a %I:%M %p')} Denver", ""]

conn = sqlite3.connect(BASE / "signals.db")
conn.row_factory = sqlite3.Row
for a in ("OP", "DOGE"):
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence, entry_price, execution_reason "
        "FROM signals WHERE pair LIKE ? AND timestamp >= '2026-08-25T15:00' ORDER BY timestamp",
        (f"%{a}%",)).fetchall()
    lines.append(f"{a} records since 3:00 PM: {len(rows)}")
    for r in rows:
        lines.append(f"  {r['timestamp'][:19]} {r['action']} {r['status']:<9} src={r['source']:<12} "
                     f"conf={r['confidence']:.0f} entry={r['entry_price']} "
                     f"exec={(r['execution_reason'] or '')[:70]}")
conn.close()

# DOGE trend at fire time (1h EMA knife test)
import ccxt
ex = ccxt.kraken()
for p in ("DOGE/USD", "OP/USD"):
    oh = ex.fetch_ohlcv(p, "1h", limit=30)
    closes = [c[4] for c in oh]
    import pandas as pd
    from indicators import ema
    s = pd.Series(closes)
    es, el = float(ema(s, 9).iloc[-1]), float(ema(s, 21).iloc[-1])
    px = closes[-1]
    state = "FALLING" if (es < el and px < el) else ("UP" if es > el else "BASING")
    lines.append(f"{p}: live={px:.5f} EMA9={es:.5f} EMA21={el:.5f} -> {state}")

out = BASE / "doge_op_facts.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
