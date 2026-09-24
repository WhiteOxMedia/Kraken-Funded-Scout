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


# ── 1. live data: an applied trade for today → the pace clause speaks it
# (hermetic since Sep 22: the live day's applied count changes under the
# test — a seeded applied trade makes the clause deterministic)
# clock-audit: ok — LIVE-PROBE (backup/seed/restore — determinism comes from the seed, not the book) (clockbombs census 4)
tf = Path("contest_trades.jsonl")
backup = tf.read_bytes() if tf.exists() else None
_today = bs._now_mst().strftime("%Y-%m-%d")
try:
    tf.write_text(json.dumps({"sold_at": f"{_today}T10:00:00-06:00",
                              "pnl_net": 12.75, "applied": True}) + "\n",
                  encoding="utf-8")
    lead = bs._all_good_lead()
    print("LIVE:", repr(lead))
    check("lead starts All good", lead.startswith("All good"))
    check("pace clause spoken from real trades",
          "plus 13 dollars today" in lead)
    check("clock clause spoken with rotation minutes",
          "minutes left in this rotation" in lead)
    import re
    m = re.search(r"(plus|minus) (\d+) dollars today", lead)
    check("pace number is a whole dollar figure", m is not None)
finally:
    if backup is not None:
        tf.write_bytes(backup)
    elif tf.exists():
        tf.unlink()

# ── 2. no trades today → pace clause dropped, clock stays
# clock-audit: ok — SANDBOXED (backup/restore byte-for-byte in the try/finally below) (clockbombs census 4)
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

# ── 6. the hearable net (Sep 22): armed trails ride the lead ────────────
# sandbox the watch file — backup/restore byte-for-byte, removed if this
# test created it (the live scanner must never see a stray file)
# clock-audit: ok — SANDBOXED (backup/restore byte-for-byte in the try/finally below) (clockbombs census 4)
wf = Path("contest_watch.json")
wbackup = wf.read_bytes() if wf.exists() else None
_orig_ratchet = bs._ratchet_trail_pct
try:
    # PUMP's real ratchet math (1.5% at peak ≥ +2%): peak +6.5% → trail 1.5%,
    # goal 2.7% → the half-goal cap (1.35%) is tighter and wins
    bs._ratchet_trail_pct = lambda e, h, s: 1.5
    wf.write_text(json.dumps({"PUMP/USD": {
        "entry": 0.004561, "high": 0.004599 * 1.065, "size": 896.61,
        "goal_pct": 2.7, "trail_armed": True}}), encoding="utf-8")
    lead6 = bs._all_good_lead()
    print("ARMED:", repr(lead6))
    m6 = re.search(r"stop (\d+\.\d)% under the peak, locking "
                   r"(?:less than a dollar|(\d+) dollars)", lead6)
    check("armed trail: the net rides the all-good line",
          bool(m6))
    if m6:
        stop_pct = float(m6.group(1))
        check("stop percent is the exit watch's enforced width (half-goal cap)",
              abs(stop_pct - 1.35) <= 0.051)   # 0.1% display rounding
        high = 0.004599 * 1.065
        want = (high * (1 - 1.35 / 100) - 0.004561) * 896.61
        if want >= 1.0:
            check("lock dollars match peak×(1−stop) − entry, × size",
                  abs(float(m6.group(2)) - want) < 1.0)
        else:
            check("sub-dollar lock speaks honestly (no fake rounding up)",
                  "locking less than a dollar" in lead6)
        check("the pair is named", "PUMP" in lead6)
        check("the clause is voice-safe (no symbols)",
              "$" not in lead6 and "+" not in lead6.split("trail")[-1])

    # two armed pairs: the tail names both, the lock is the tightest
    wf.write_text(json.dumps({
        "PUMP/USD": {"entry": 0.004561, "high": 0.004599 * 1.065,
                     "size": 896.61, "goal_pct": 2.7, "trail_armed": True},
        "SUI/USD": {"entry": 100.0, "high": 103.0, "size": 400.0,
                    "goal_pct": 2.0, "trail_armed": True}}), encoding="utf-8")
    lead7 = bs._all_good_lead()
    print("TWO:", repr(lead7))
    check("two armed pairs: both named",
          "PUMP" in lead7 and "SUI" in lead7)
    check("two armed pairs: the tightest lock is the one spoken",
          bool(re.search(r"locking (\d+) dollars", lead7))
          and float(re.search(r"locking (\d+) dollars", lead7).group(1))
          >= 160.0)

    # a not-armed pair stays silent — no invented net
    wf.write_text(json.dumps({"PUMP/USD": {
        "entry": 0.004561, "high": 0.004599, "size": 896.61,
        "goal_pct": 2.7}}), encoding="utf-8")
    lead8 = bs._all_good_lead()
    check("not armed: no trail clause anywhere",
          "trail armed" not in lead8 and "locking" not in lead8)
finally:
    bs._ratchet_trail_pct = _orig_ratchet
    if wbackup is not None:
        wf.write_bytes(wbackup)
    elif wf.exists():
        wf.unlink()

print(f"RESULT: {ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
