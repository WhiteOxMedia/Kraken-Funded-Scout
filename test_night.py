"""Why no trades overnight? Full signal trail since 20:00 yesterday."""
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import scratch_space   # scratch policy: nothing volatile lands in the repo root

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"now: {now.strftime('%a %I:%M %p')} Denver"]

# bridge trail since Sun 20:00
try:
    # clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
    conn = sqlite3.connect(BASE / "signals.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence FROM signals "
        "WHERE timestamp >= '2026-08-23T20:00' ORDER BY timestamp ASC").fetchall()
    lines.append(f"\nbridge signals since Sun 8 PM ({len(rows)}):")
    for r in rows:
        lines.append(f"  {r['timestamp'][:16]}  {r['pair']:<10} {r['action']:<4} {r['status']:<9} "
                     f"{r['source']:<14} {r['confidence']}%")
    conn.close()
except Exception as exc:
    lines.append(f"bridge: {exc}")

# scanner state: did the cooldown flatten run? winners kept?
try:
    # clock-audit: ok — LIVE-PROBE (manual bot-day report — reports on the live system by design) (clockbombs census 4)
    st = json.loads((BASE / "scanner_state.json").read_text())
    lines.append(f"\nscanner_state: {st}")
except Exception as exc:
    lines.append(f"scanner_state: {exc}")

# bot positions now
try:
    from sqlalchemy import create_engine, text
    url = os.environ.get("DB195374BC_DATABASE_URL") or os.environ.get("DB1005AA9E_DATABASE_URL")
    if not url:
        from config import DB_URL
        url = DB_URL
    eng = create_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, pair, entry_price, entry_timestamp, is_open FROM positions "
            "ORDER BY id DESC LIMIT 5")).fetchall()
        lines.append("\nbot positions (latest 5):")
        for r in rows:
            lines.append(f"  #{r[0]} {r[1]} @ {r[2]} opened {str(r[3])[:16]} open={r[4]}")
        rows = conn.execute(text(
            "SELECT side, pair, price, timestamp FROM trades "
            "WHERE timestamp >= '2026-08-23 20:00' ORDER BY timestamp DESC LIMIT 10")).fetchall()
        lines.append(f"bot trades since 8 PM ({len(rows)}):")
        for r in rows:
            lines.append(f"  {str(r[3])[:16]} {r[0]:<4} {r[1]}")
except Exception as exc:
    lines.append(f"bot DB: {exc}")

# execution state
try:
    import json
    ex = json.loads((BASE / "execution_state.json").read_text())
    lines.append(f"\nexecution: blacklist={list(ex.get('blacklist', {}).keys())} "
                 f"strikes={list(ex.get('strikes', {}).keys())} "
                 f"pending_scales={list(ex.get('pending_scales', {}).keys())}")
except Exception as exc:
    lines.append(f"execution: {exc}")

# kill switch
lines.append(f"\nkill switch: {'ACTIVE' if (BASE / 'kill_switch.lock').exists() else 'clear'}")

# rotation now
try:
    from background_scanner import get_time_block
    name, interval, conf, bar, desc, no_buy, key = get_time_block(now.hour, now.weekday())
    lines.append(f"rotation now: {name} | bar {bar}% | no_buy={no_buy}")
except Exception as exc:
    lines.append(f"rotation: {exc}")

out = BASE / "night_check.txt"
scratch_space.report_temp('night_check.txt').write_text("\n".join(lines), encoding="utf-8")
print("done")
