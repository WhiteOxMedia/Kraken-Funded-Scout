"""Calibrate the rotation-volume floors from real engine data."""
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
doc = json.loads((BASE / "rotation_thresholds.json").read_text())
n = datetime.now(MST)

lines = []
# sample a few assets' median_volume_usd across rotations
for pair in ["BTC/USD", "XRP/USD", "SOL/USD", "ADA/USD", "ALGO/USD", "JUP/USD", "WIF/USD"]:
    u = doc.get("universe", {}).get(pair, {})
    for sched in ("weekday", "weekend"):
        row = []
        for rot in ["00-04", "04-08", "08-12", "12-16", "16-20"]:
            cell = u.get(sched, {}).get(rot) or {}
            mv = cell.get("median_volume_usd") or 0
            row.append(f"{mv/1000:>7.0f}k")
        lines.append(f"{pair:<10} {sched:<8} " + " ".join(row))
lines.append(f"{'':<10} {'':<8} " + " ".join(["00-04", "04-08", "08-12", "12-16", "16-20"]))

# where did the dropped 26 sit? check their current cells
dropped = ["ARB", "KAITO", "DOT", "GRASS", "FIL", "LDO", "APT", "ALGO", "S", "PNUT",
           "OP", "ATOM", "JTO", "RENDER", "ETC", "JUP", "TIA", "WIF", "VIRTUAL",
           "STX", "AIXBT", "MOODENG", "TURBO"]
lines.append("")
lines.append("dropped-list rotation medians (current-ish rotation, weekday):")
for a in dropped:
    pair = f"{a}/USD"
    u = doc.get("universe", {}).get(pair, {})
    cells = {r: (u.get("weekday", {}).get(r) or {}).get("median_volume_usd") or 0 for r in ["00-04", "04-08", "08-12"]}
    lines.append(f"  {a:<9} R1 {cells['00-04']/1000:>6.0f}k · R2 {cells['04-08']/1000:>6.0f}k · R3 {cells['08-12']/1000:>6.0f}k")

out = BASE / "liq_cal.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
