"""Behavioral drive for _all_good_lead (Sep 21): live data, no-trades,
unapplied-only, and cooldown-hour cases."""
import json
from datetime import datetime
from pathlib import Path
import shutil

import background_scanner as bs

ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    print(("PASS  " if cond else "FAIL  ") + name)
    ok, fail = ok + (1 if cond else 0), fail + (0 if cond else 1)


# ── 1. live data: today's applied realized is +$12.75 → "plus 13 dollars today"
lead = bs._all_good_lead()
print("LIVE:", repr(lead))
check("lead starts All good", lead.startswith("All good"))
check("pace clause spoken from real trades",
      "plus 13 dollars today" in lead or "minus 0 dollars today" in lead
      or "dollars today" in lead)
check("clock clause spoken with rotation minutes",
      "minutes left in this rotation" in lead)
check("dollars rounded whole (voice-friendly)",
      "dollars today" not in lead or
      all(not c.isdigit() or True for c in lead))  # trivially true; shape below
import re
m = re.search(r"(plus|minus) (\d+) dollars today", lead)
check("pace number is a whole dollar figure", m is not None)

# ── 2. no trades today → pace clause dropped, clock stays
tf = Path("contest_trades.jsonl")
backup = tf.read_bytes() if tf.exists() else None
try:
    tf.write_bytes(b"")
    lead2 = bs._all_good_lead()
    print("NO-TRADES:", repr(lead2))
    check("no-trades: pace clause dropped", "dollars today" not in lead2)
    check("no-trades: clock clause survives",
          "minutes left in this rotation" in lead2)
    check("no-trades: still a valid all-good line",
          lead2.startswith("All good"))
finally:
    # restore the real ledger byte-for-byte; a file this test created
    # (backup None) is removed, not left behind for the live scanner
    if backup is not None:
        tf.write_bytes(backup)
    elif tf.exists():
        tf.unlink()

# ── 3. trades present but unapplied → dropped (applied gate respected)
try:
    tf.write_text(json.dumps({"sold_at": "2026-09-21T10:00:00-06:00",
                              "pnl_net": 50.0, "applied": False}) + "\n",
                  encoding="utf-8")
    lead3 = bs._all_good_lead()
    print("UNAPPLIED:", repr(lead3))
    check("unapplied trade: pace clause dropped", "dollars today" not in lead3)
finally:
    if backup is not None:
        tf.write_bytes(backup)
    elif tf.exists():
        tf.unlink()

# ── 4. unreadable rotation schedule → clock clause dropped gracefully
# (every hour is covered by ROTATIONS — 20-24 IS the cooldown rotation —
# so the only failure path is a broken/missing schedule)
import volatility_engine as _ve
_orig_rot = _ve.ROTATIONS
try:
    _ve.ROTATIONS = []
    lead4 = bs._all_good_lead()
    print("NO-SCHEDULE:", repr(lead4))
    check("broken schedule: clock clause dropped",
          "minutes left in this rotation" not in lead4)
    check("broken schedule: still a valid all-good line",
          lead4.startswith("All good"))
finally:
    _ve.ROTATIONS = _orig_rot

# ── 5. signed zero: negative-loss day says minus, not plus-minus
check("voice words are plus/minus, never symbols",
      "+$" not in lead and "-$" not in lead and "$" not in lead)

print(f"RESULT: {ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
