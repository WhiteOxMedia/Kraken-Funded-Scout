"""Set BNB position size to $5,000 (user entered 100% chunk).
(Sep 28: conforms to the state-writer conventions the tripwire enforces
— merge-read, tombstone-aware, atomic write.)"""
import json
import os
from pathlib import Path

WATCH = Path(__file__).resolve().parent / "contest_watch.json"
try:
    w = json.loads(WATCH.read_text())
    w = w if isinstance(w, dict) else {}
except Exception:
    w = {}
# tombstone-free read (None-means-delete under the merge convention)
w = {k: v for k, v in w.items() if v is not None}

if "BNB/USD" in w:
    w["BNB/USD"]["size"] = 5000.0
    tmp = str(WATCH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(w, fh, indent=2)
    os.replace(tmp, str(WATCH))
    print(f"BNB size set to $5,000 (entry {w['BNB/USD'].get('entry')})")
else:
    print("BNB not on watch!")
