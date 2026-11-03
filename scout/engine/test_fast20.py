"""Verify the FAST $20 ranking logic — does PUMP land on the board?"""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

from background_scanner import (rotation_key_for_hour, _schedule_for_dow,
                                LIQ_ROT_FLOOR_WEEKDAY, LIQ_ROT_FLOOR_WEEKEND,
                                SCALP_TARGET_PCT)
from volatility_engine import load_rotation_thresholds
import scratch_space   # scratch policy: nothing volatile lands in the repo root

ASSETS = ["BTC","ETH","LINK","BCH","DOGE","ARB","AAVE","AVAX","BNB","CRV",
          "DOT","FIL","GRASS","ADA","HBAR","INJ","KAITO","LDO","LTC","MOODENG",
          "NEAR","PNUT","SOL","S","TRUMP","UNI","WLD","XRP","ALGO","APT",
          "ATOM","ETC","JTO","JUP","ONDO","OP","POL","POPCAT","RENDER","STX",
          "SUI","TAO","TIA","TRX","WIF","AIXBT","ASTER","FARTCOIN","HYPE",
          "PENGU","PUMP","VIRTUAL","XPL","ZEC"]

rot = rotation_key_for_hour(now.hour)
sched = _schedule_for_dow(now.weekday())
floor = LIQ_ROT_FLOOR_WEEKDAY if sched == "weekday" else LIQ_ROT_FLOOR_WEEKEND
doc = load_rotation_thresholds(max_age_s=900)

rows = []
for a in ASSETS:
    cell = (doc.get("universe", {}).get(f"{a}/USD", {}).get(sched, {}).get(rot) or {})
    avg = float(cell.get("avg_move") or 0)
    vol = float(cell.get("median_volume_usd") or 0)
    if avg <= 0 or vol < floor:
        continue
    rows.append((a, (SCALP_TARGET_PCT / avg) * 4.0, avg, vol, bool(cell.get("reliable"))))
rows.sort(key=lambda x: x[1])

lines = [f"rotation {rot} {sched} (floor ${floor:,.0f}): top 10 of {len(rows)} liquid coins",
         f"{'ASSET':<10} {'$20 ETA':>8} {'avg/rot':>8} {'rot vol':>9}  reliable"]
for a, eta, avg, vol, rel in rows[:10]:
    lines.append(f"{a:<10} {eta:>6.1f}h {avg:>7.2f}% {vol:>9,.0f}  {'yes' if rel else 'THIN'}")
pump = [r for r in rows if r[0] == "PUMP"]
lines.append("")
lines.append(f"PUMP rank: {rows.index(pump[0]) + 1 if pump else 'NOT ON BOARD (filtered)'}")

out = BASE / "fast20_check.txt"
scratch_space.report_temp('fast20_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
