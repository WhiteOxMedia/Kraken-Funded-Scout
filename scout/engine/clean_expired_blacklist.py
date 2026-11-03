"""Remove expired blacklist entries from execution_state.json (bookkeeping)."""
import json, shutil
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
MST = ZoneInfo("America/Denver")
EXECUTION_STATE = "execution_state.json"
p = BASE / EXECUTION_STATE
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
    # ATOMIC (the state-writer tripwire's convention): the bookkeeping
    # one-shot never leaves a torn half-file for the bot's reader
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    import os
    os.replace(tmp, str(p))
print("removed:", removed or "nothing expired")
