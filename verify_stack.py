"""Post-restart verification: bridge signals + execution state + engine JSON freshness."""
import json
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
lines = []

# 1. Bridge: recent signals
try:
    conn = sqlite3.connect(BASE / "signals.db")
    rows = conn.execute(
        "select timestamp, pair, action, status, source from signals order by timestamp desc limit 5"
    ).fetchall()
    lines.append("BRIDGE — last 5 signals:")
    for r in rows:
        lines.append(f"  {r[0]}  {r[1]:<12} {r[2]:<5} {r[3]:<10} {r[4]}")
    conn.close()
except Exception as exc:
    lines.append(f"BRIDGE ERROR: {exc}")

# 2. Execution state
try:
    st = json.loads((BASE / "execution_state.json").read_text(encoding="utf-8"))
    lines.append(f"EXECUTION STATE — blacklist: {st.get('blacklist', {})}")
    lines.append(f"  strikes: {st.get('strikes', {})}")
    lines.append(f"  pending_scales: {st.get('pending_scales', {})}")
except FileNotFoundError:
    lines.append("EXECUTION STATE — not created yet (no gates have fired; clean start)")
except Exception as exc:
    lines.append(f"EXECUTION STATE ERROR: {exc}")

# 3. Engine JSON freshness
try:
    doc = json.loads((BASE / "rotation_thresholds.json").read_text(encoding="utf-8"))
    lines.append(f"ENGINE JSON — generated {doc.get('generated_at', '?')[:19]} · "
                 f"data_weeks {doc.get('data_weeks')} · assets {len(doc.get('universe', {}))}")
except Exception as exc:
    lines.append(f"ENGINE JSON ERROR: {exc}")

out = BASE / "verify_check.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print("ok")
