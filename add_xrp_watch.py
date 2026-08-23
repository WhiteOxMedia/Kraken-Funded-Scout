"""Add XRP to the contest watch at live price (user entered manually after a green light)."""
import json
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
MST = ZoneInfo("America/Denver")

WATCH = BASE / "contest_watch.json"
try:
    w = json.loads(WATCH.read_text())
except Exception:
    w = {}

import ccxt
ex = ccxt.kraken({"enableRateLimit": True})
px = float(ex.fetch_ticker("XRP/USD")["last"])

if "XRP/USD" in w:
    print(f"XRP already watched @ {w['XRP/USD']['entry']} — no change")
else:
    w["XRP/USD"] = {"entry": px, "high": px, "added": datetime.now(MST).isoformat()}
    WATCH.write_text(json.dumps(w, indent=2))
    print(f"XRP added to watch @ ${px:.4f} (live price)")
    print("watch list now:", ", ".join(w.keys()))
