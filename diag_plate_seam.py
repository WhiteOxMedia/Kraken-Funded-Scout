"""Plate render check for the seam (safe: runs the plate against a temp
state copy, never touching the live discipline_state.json)."""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import discipline as dc

orig_state = dc.STATE_FILE
try:
    with tempfile.TemporaryDirectory() as td:
        dc.STATE_FILE = Path(td) / "state.json"
        dc.STATE_FILE.write_text(json.dumps({
            "weights": dict(dc.DEFAULT_WEIGHTS),
            "history": [{"date": "2026-09-04", "rot": "08-12", "score": 62.0,
                         "total_left": 280.0, "system_left": 227.72,
                         "top_gate": "tide-out", "realized_pnl": 0.0,
                         "balance_after": 10000.69,
                         "ts": "2026-09-04T12:00:00-06:00"}],
            "lessons": []}), encoding="utf-8")
        import app
        html = app._discipline_plate()
        ok1 = "by the gates (theoretical)" in html
        ok2 = "tide-out" in html
        ok3 = "user left" in html and "$52" in html  # 280 - 227.72 = 52.28 -> $52
        print("third column:", "OK" if ok1 else "MISSING")
        print("gate named:", "OK" if ok2 else "MISSING")
        print("user-vs-system split:", "OK" if ok3 else "MISSING")
        print("PLATE_" + ("PASS" if (ok1 and ok2 and ok3) else "FAIL"))
finally:
    dc.STATE_FILE = orig_state
