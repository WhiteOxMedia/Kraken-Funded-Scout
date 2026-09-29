"""One-shot operator: add XRP/USD to the live contest watch at the
current market price. Merge-save + tombstone-aware (the fleet's
cross-writer convention): the book is RE-READ at the last moment and
only the XRP row is handed to background_scanner's production
_save_contest_watch — tombstones stay deleted, foreign rows survive,
the write is atomic (tmp + os.replace) inside the production writer.
"""
import sys
from datetime import datetime

import ccxt

from background_scanner import _load_contest_watch, _save_contest_watch

MST = __import__("zoneinfo").ZoneInfo("America/Denver")

ex = ccxt.kraken({"enableRateLimit": True})
px = float(ex.fetch_ticker("XRP/USD")["last"])

# RE-READ AT THE LAST MOMENT + IDEMPOTENT (the reviews' minors):
# between the top-of-file read and the write, the scanner and the bot
# keep RMW-ing the book — a stale snapshot here would resurrect
# tombstones. An EXISTING valid XRP row is left exactly as it is (its
# entry/size/goal/trail state stay); only a missing or malformed row
# is created fresh, at the live price.
w = _load_contest_watch()
_row = w.get("XRP/USD")
if not isinstance(_row, dict) or _row.get("entry") is None:
    w["XRP/USD"] = {"entry": px, "high": px,
                    "added": datetime.now(MST).isoformat()}
_save_contest_watch(w)
print(f"XRP added to watch @ ${px:.4f} (live price)")
w2 = _load_contest_watch()
print("watch list now:", ", ".join(k for k, v in w2.items() if v is not None))
