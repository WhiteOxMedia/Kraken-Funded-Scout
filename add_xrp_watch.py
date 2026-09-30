"""One-shot operator: add XRP/USD to the live contest watch at the
current market price. Merge-save + tombstone-aware (the fleet's
cross-writer convention): the book is RE-READ at the last moment and
only the XRP row is handed to background_scanner's production
_save_contest_watch — tombstones stay deleted, foreign rows survive,
the write is atomic (tmp + os.replace) inside the production writer.
The print tells the truth: added only when the save landed, already-
on-watch when the idempotent no-op ran.
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
_added = False
if not isinstance(_row, dict) or _row.get("entry") is None:
    w["XRP/USD"] = {"entry": px, "high": px,
                    "added": datetime.now(MST).isoformat()}
    # FRESH-KEY INTENT (the re-add triage, Sep 29 — the recovered CR
    # major's named site): the operator's hand IS the deliberate
    # re-add; without fresh_keys a tombstone left by the SOLD tap or
    # the GET-OUT auto-close silently swallowed this add — the exact
    # scenario a re-add happens in. The writer's True/False report
    # makes the print the truth (it used to return None, so the
    # print could never say 'added').
    _added = _save_contest_watch(w, fresh_keys=["XRP/USD"])
if _added:
    print(f"XRP added to watch @ ${px:.4f} (live price)")
elif "XRP/USD" in w:
    print("XRP already on the watch (a valid row stays untouched — "
          "idempotent re-run)")
else:
    print("XRP NOT added — the save lost a write race (named "
          "contention in the log); re-run in a moment")
w2 = _load_contest_watch()
print("watch list now:", ", ".join(k for k, v in w2.items() if v is not None))
