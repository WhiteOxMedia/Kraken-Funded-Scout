"""Set BNB position size to $5,000 (user entered 100% chunk)."""
import json
from pathlib import Path

WATCH = Path(__file__).resolve().parent / "contest_watch.json"
w = json.loads(WATCH.read_text())
if "BNB/USD" in w:
    w["BNB/USD"]["size"] = 5000.0
    WATCH.write_text(json.dumps(w, indent=2))
    print(f"BNB size set to $5,000 (entry {w['BNB/USD']['entry']})")
else:
    print("BNB not on watch!")
