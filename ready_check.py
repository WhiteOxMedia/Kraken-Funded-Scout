"""Readiness check: is everything armed for the next signal?"""
import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)
lines = [f"readiness check @ {now.strftime('%a %I:%M %p')} Denver"]

# 1. Scanner log freshness — background_scanner writes stdout; use the
# bridge + engine JSON mtimes as liveness proxies instead
for f, label in [("rotation_thresholds.json", "engine JSON"),
                 ("contest_watch.json", "watch list")]:
    p = BASE / f
    if p.exists():
        age = time.time() - p.stat().st_mtime
        lines.append(f"{label}: updated {age/60:.0f} min ago")
    else:
        lines.append(f"{label}: missing")

# 2. Kill switch
try:
    lock = BASE / "kill_switch.lock"
    lines.append(f"kill switch: {'ACTIVE — BUYS BLOCKED!' if lock.exists() else 'clear'}")
except Exception:
    pass

# 3. Bridge: pending signals + recent winners today
try:
    conn = sqlite3.connect(BASE / "signals.db")
    conn.row_factory = sqlite3.Row
    pend = conn.execute("SELECT COUNT(*) c FROM signals WHERE status='pending'").fetchone()["c"]
    lines.append(f"bridge: {pend} pending signal(s)")
    today = now.strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence FROM signals "
        "WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 6", (today,)).fetchall()
    for r in rows:
        lines.append(f"  today: {r['timestamp'][11:16]} {r['pair']:<10} {r['action']:<4} "
                     f"{r['status']:<9} {r['source']:<14} {r['confidence']}%")
    conn.close()
except Exception as exc:
    lines.append(f"bridge error: {exc}")

# 4. Execution state (blacklist / strikes / scale queue)
try:
    st = json.loads((BASE / "execution_state.json").read_text())
    bl = st.get("blacklist", {})
    ps = st.get("pending_scales", {})
    lines.append(f"execution: blacklist {len(bl)}, pending scale-ins {len(ps)}")
    if bl:
        lines.append(f"  blacklisted: {', '.join(bl.keys())}")
except FileNotFoundError:
    lines.append("execution: clean (no gates fired)")
except Exception as exc:
    lines.append(f"execution: {exc}")

# 5. Watch list (contest positions)
try:
    w = json.loads((BASE / "contest_watch.json").read_text())
    lines.append(f"contest watch: {', '.join(w.keys()) if w else 'empty'}")
except Exception:
    lines.append("contest watch: empty")

# 6. Payouts tracked
try:
    pt = json.loads((BASE / "payout_track.json").read_text())
    total = sum(p["amount"] for p in pt.get("payouts", {}).values())
    lines.append(f"payouts tracked: ${total:,.2f}")
except Exception:
    lines.append("payouts: none tracked yet")

# 7. Current rotation + bar (what the signal must clear)
try:
    doc = json.loads((BASE / "rotation_thresholds.json").read_text())
    from background_scanner import get_time_block
    name, interval, conf, bar, desc, no_buy, key = get_time_block(now.hour, now.weekday())
    lines.append(f"rotation now: {name} | scan every {interval} min | confidence bar {bar}% | "
                 f"no-buy window: {'YES' if no_buy else 'no'}")
except Exception as exc:
    lines.append(f"rotation: {exc}")

out = BASE / "ready_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
