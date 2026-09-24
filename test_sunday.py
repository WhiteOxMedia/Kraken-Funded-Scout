"""Weekend vs weekday market character, from the Volatility Engine's real data."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"today: {now.strftime('%A')} — {'weekend' if now.weekday() >= 5 else 'weekday'} schedule, "
         f"rotation hour-block {now.hour // 4 * 4:02d}-{(now.hour // 4 + 1) * 4:02d}"]

# clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
doc = json.loads((BASE / "rotation_thresholds.json").read_text())
med = doc.get("median", {})
lines.append("")
lines.append("avg 4h move per rotation (universe median, %):")
lines.append(f"{'rotation':<12} {'weekday':>8} {'weekend':>8} {'diff':>7}")
for rot in ["00-04", "04-08", "08-12", "12-16", "16-20"]:
    wd = med.get("weekday", {}).get(rot, {}).get("avg_move")
    we = med.get("weekend", {}).get(rot, {}).get("avg_move")
    if wd and we:
        lines.append(f"{rot:<12} {wd:>7.2f}% {we:>7.2f}% {we - wd:>+6.2f}")

lines.append("")
lines.append("false-breakout rate (universe median, % — higher = more fakeouts):")
for rot in ["00-04", "04-08", "08-12", "12-16", "16-20"]:
    wd = med.get("weekday", {}).get(rot, {}).get("false_breakout_rate")
    we = med.get("weekend", {}).get(rot, {}).get("false_breakout_rate")
    if wd is not None and we is not None:
        lines.append(f"{rot:<12} {wd * 100:>7.1f}% {we * 100:>7.1f}% {we - wd:>+6.1f}")

# today's champions from the scout DB
try:
    import os
    from sqlalchemy import create_engine, text
    url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
    if not url:
        from config import DB_URL
        url = DB_URL
    eng = create_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT timestamp, pair, action, confidence FROM scout_signals "
            "WHERE timestamp >= '2026-08-23' ORDER BY timestamp DESC LIMIT 12")).fetchall()
        lines.append("")
        lines.append("today's tournament picks (all):")
        for r in rows:
            lines.append(f"  {r[0]}  {r[1]:<10} {r[2]:<4} {r[3]}%")
        if not rows:
            lines.append("  (none logged yet today)")
except Exception as exc:
    lines.append(f"pick history: {exc}")

out = BASE / "sunday_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
