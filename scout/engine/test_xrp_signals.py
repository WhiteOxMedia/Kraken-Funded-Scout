"""What XRP signals fired today? Tournament winners + bridge signals."""
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
lines = []

# Tournament winners DB (Sep 24, the RULE D sweep lesson): the winners
# tables live in the REMOTE Postgres (db.py / config DB_URL) — the old
# sqlite3.connect(BASE / "scout.db") here AUTO-CREATED a 0-byte scout.db
# fossil at the repo root every run (sqlite creates on connect; the d8ca85f
# rebirth class). The read now goes through db.py's own engine, and the
# scout.db NAME is pinned known-dead so any writer flags in the hygiene gate.
try:
    import db as _db
    from sqlalchemy import text as _text
    with _db.get_engine().connect() as _c:
        tables = [r[0] for r in _c.execute(_text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public'")).fetchall()]
        lines.append(f"tournament db tables: {len(tables)}")
        if "tournament_winners" in tables:
            rows = _c.execute(_text(
                "SELECT * FROM tournament_winners WHERE pair LIKE 'XRP%' "
                "ORDER BY rowid DESC LIMIT 5")).fetchall()
            lines.append("XRP tournament winners:")
            for r in rows:
                lines.append(f"  {dict(r._mapping)}")
            rows = _c.execute(_text(
                "SELECT timestamp, pair, action, confidence FROM tournament_winners "
                "ORDER BY rowid DESC LIMIT 8")).fetchall()
            lines.append("recent winners (any pair):")
            for r in rows:
                d = dict(r._mapping)
                lines.append(f"  {d.get('timestamp')}  {d.get('pair')}  "
                             f"{d.get('action')}  {d.get('confidence')}%")
except Exception as exc:
    lines.append(f"tournament db: {exc}")

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
