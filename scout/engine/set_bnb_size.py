"""One-shot operator: set the BNB/USD contest size. Delegates the write
to background_scanner's production merge-save (_save_contest_watch) —
tombstones stay deleted, foreign rows survive, the write is atomic.
"""
from background_scanner import _load_contest_watch, _save_contest_watch

w = _load_contest_watch()
row = w.get("BNB/USD")
if not isinstance(row, dict):
    # TYPE-GUARDED (the review's minor): a tombstone (None) or a
    # malformed row must not be "brought back" by writing a size onto
    # it — the operator sees the book's real state instead.
    print("BNB not on watch (or tombstoned) — nothing written")
else:
    row["size"] = 5000.0
    _save_contest_watch(w)
    print(f"BNB size set to $5,000 (entry {row.get('entry')})")
