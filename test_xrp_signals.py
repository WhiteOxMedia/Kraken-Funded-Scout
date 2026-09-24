"""What XRP signals fired today? Tournament winners + bridge signals."""
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
lines = []

# Tournament winners DB (scout's own db.py tables)
try:
    conn = sqlite3.connect(BASE / "scout.db")
    conn.row_factory = sqlite3.Row
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    lines.append(f"scout.db tables: {tables}")
    if "tournament_winners" in tables:
        rows = conn.execute(
            "SELECT * FROM tournament_winners WHERE pair LIKE 'XRP%' ORDER BY rowid DESC LIMIT 5").fetchall()
        lines.append("XRP tournament winners:")
        for r in rows:
            d = dict(r)
            lines.append(f"  {d}")
    # any winners today
    if "tournament_winners" in tables:
        rows = conn.execute(
            "SELECT timestamp, pair, action, confidence FROM tournament_winners "
            "ORDER BY rowid DESC LIMIT 8").fetchall()
        lines.append("recent winners (any pair):")
        for r in rows:
            lines.append(f"  {r['timestamp']}  {r['pair']}  {r['action']}  {r['confidence']}%")
    conn.close()
except Exception as exc:
    lines.append(f"scout.db: {exc}")

# Bridge signals today
try:
    # clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
    conn = sqlite3.connect(BASE / "signals.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence FROM signals "
        "WHERE timestamp >= '2026-08-22' ORDER BY timestamp DESC LIMIT 12").fetchall()
    lines.append("bridge signals today:")
    for r in rows:
        lines.append(f"  {r['timestamp'][:19]}  {r['pair']:<10} {r['action']:<4} {r['status']:<9} "
                     f"{r['source']:<14} {r['confidence']}%")
    conn.close()
except Exception as exc:
    lines.append(f"signals.db: {exc}")

out = BASE / "xrp_signal_check.txt"
scratch_space.report_temp('xrp_signal_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
