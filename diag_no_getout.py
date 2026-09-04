"""Why no GET OUT? What's watched, what's it doing, any exits fired?"""
import sys, json, sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")
now = datetime.now(MST)

lines = [f"now: {now.strftime('%a %I:%M %p')} Denver", ""]

# 1. the watch — is anything being watched?
try:
    w = json.loads((BASE / "contest_watch.json").read_text())
    lines.append(f"contest watch: {list(w.keys()) or 'EMPTY'}")
    import ccxt
    ex = ccxt.kraken()
    for p, rec in w.items():
        live = float(ex.fetch_ticker(p)["last"])
        entry = float(rec.get("entry") or 0)
        size = float(rec.get("size") or 5000)
        high = float(rec.get("high") or entry)
        usd = (live / entry - 1) * size if entry else 0
        pct_off_peak = (high - live) / high * 100 if high > entry else 0
        lines.append(f"  {p}: entry={entry:.5f} live={live:.5f} high={high:.5f}")
        lines.append(f"    P&L: ${usd:+,.0f} | off peak: {pct_off_peak:.1f}% (trail fires at 3%)")
        lines.append(f"    added: {str(rec.get('added',''))[:19]}")
except Exception as e:
    lines.append(f"watch read failed: {e}")

# 2. recent signals — any sells/exits in the bridge?
try:
    conn = sqlite3.connect(BASE / "signals.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, pair, action, status, source, confidence, execution_reason "
        "FROM signals WHERE timestamp >= '2026-08-27T10:45' "
        "ORDER BY timestamp DESC LIMIT 20").fetchall()
    lines.append("")
    lines.append(f"bridge records since 10:45 AM: {len(rows)}")
    for r in rows:
        lines.append(f"  {r['timestamp'][11:19]} {r['pair']:<12} {r['action']:<5} {r['status']:<9} "
                     f"conf={r['confidence']:.0f} exec={(r['execution_reason'] or '')[:50]}")
    conn.close()
except Exception as e:
    lines.append(f"bridge query failed: {e}")

# 3. scanner state — is the light even running?
try:
    ss = json.loads((BASE / "scanner_state.json").read_text())
    lines.append("")
    lines.append(f"scanner state: {ss}")
except Exception:
    pass

out = BASE / "no_getout.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("done")
