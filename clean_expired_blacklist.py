"""Remove expired blacklist entries from execution_state.json (bookkeeping)."""
import json, shutil
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
p = BASE / "execution_state.json"
doc = json.loads(p.read_text())
now = datetime.now(MST)
removed = []
bl = doc.get("blacklist", {})
for pair, rec in list(bl.items()):
    try:
        until = datetime.fromisoformat(rec["until"])
        if until < now:
            removed.append(f"{pair} (was until {rec['until'][:16]})")
            bl.pop(pair)
    except Exception:
        pass
if removed:
    shutil.copy2(p, BASE / "execution_state.json.bak-expired")
    doc["blacklist"] = bl
    p.write_text(json.dumps(doc, indent=1))
print("removed:", removed or "nothing expired")
