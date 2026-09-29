"""Add XRP to the contest watch at live price (user entered manually
after a green light). (Sep 28: conforms to the state-writer conventions
the tripwire enforces — merge-read, tombstone-aware, atomic write.)"""
import json
import os
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
    w = w if isinstance(w, dict) else {}
except Exception:
    w = {}
# tombstone-free read (None-means-delete under the merge convention)
w = {k: v for k, v in w.items() if v is not None}

import ccxt
ex = ccxt.kraken({"enableRateLimit": True})
px = float(ex.fetch_ticker("XRP/USD")["last"])

if "XRP/USD" in w:
    print(f"XRP already watched @ {w['XRP/USD'].get('entry')} — no change")
else:
    w["XRP/USD"] = {"entry": px, "high": px,
                    "added": datetime.now(MST).isoformat()}
    tmp = str(WATCH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(w, fh, indent=2)
    os.replace(tmp, str(WATCH))
    print(f"XRP added to watch @ ${px:.4f} (live price)")
    print("watch list now:", ", ".join(w.keys()))
