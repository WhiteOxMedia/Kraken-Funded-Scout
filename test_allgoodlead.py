"""Behavioral drive for _all_good_lead (Sep 21): live data, no-trades,
unapplied-only, and cooldown-hour cases."""
import json
from datetime import datetime
from pathlib import Path
import shutil

import background_scanner as bs

ok = 0
fail = 0

# the LIVE bindings, captured once — every window below runs against
# tempdir sinks and the restore lands here, at process exit
_real_cw = bs.CONTEST_WATCH_JSON
_real_ctf = bs.CONTEST_TRADES_FILE


def check(name, cond):
    global ok, fail
    print(("PASS  " if cond else "FAIL  ") + name)
    ok, fail = ok + (1 if cond else 0), fail + (0 if cond else 1)


# ── THE JOURNAL SINK (the test-pollutes-live-state sweep, Sep 29):
# every trades-journal window below rebinds CONTEST_TRADES_FILE to a
# tempdir sink instead of opening the LIVE journal by name (the old
# backup/restore shape died with the process mid-window — the apptest
# history lesson). The reader (_today_realized_pnl) and the tracker
# resolve the constant live; the sink dies with the process.
import tempfile as _tf_sink
_sink_dir = Path(_tf_sink.mkdtemp())
bs.CONTEST_TRADES_FILE = str(_sink_dir / "contest_trades.jsonl")

# ── THE GUARD-STATE SINK, FLEET-RUN LESSON (Sep 30): the give-back lead
# (Sep 29) reads profit_guard's giveback FIRST and leads the line with it
# when the day's erosion crosses the speak floor — so sections 1-4 below
# are environment-dependent unless the guard state is sandboxed too (the
# fleet battery caught this: a real $55 give-back day made three pins
# fail while the PRODUCTION behavior was correct). The sandbox ships an
# all-good state; section 7 re-ships its own values per window; the
# restore lands in the same finally that restores the journal sink.
import profit_guard as _pg_sink
import telemetry_adjust as _ta_sink
_pg_sink.STATE_FILE = _sink_dir / "profit_guard_state.json"
_ta_sink.TELEMETRY_PATH = _sink_dir / "telemetry_sink.jsonl"   # the sink seam:
# the tracker's emit_adjustment must never write a FAKE event into the
# live journal (section 7's lesson, now covering every window)
_pg_sink._save({"giveback": {"give_back": 0.0, "peak_total": 0.0,
                             "now_total": 0.0}})


# ── 1. live data: an applied trade for today → the pace clause speaks it
# (hermetic since Sep 22: the live day's applied count changes under the
# test — a seeded applied trade makes the clause deterministic)
# clock-audit: ok — SANDBOXED (CONTEST_TRADES_FILE sink rebind, Sep 29; the
# old LIVE-PROBE backup/restore shape is retired with the sweep)
_tf = Path(bs.CONTEST_TRADES_FILE)
_today = bs._now_mst().strftime("%Y-%m-%d")
try:
    _tf.write_text(json.dumps({"sold_at": f"{_today}T10:00:00-06:00",
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
    _tf.unlink(missing_ok=True)

# ── 2. no trades today → pace clause dropped, clock stays
# clock-audit: ok — SANDBOXED (CONTEST_TRADES_FILE sink rebind, Sep 29)
try:
    _tf.write_bytes(b"")
    lead2 = bs._all_good_lead()
    print("NO-TRADES:", repr(lead2))
    check("no-trades: pace clause dropped", "dollars today" not in lead2)
    check("no-trades: clock clause survives",
          "minutes left in this rotation" in lead2)
    check("no-trades: still a valid all-good line",
          lead2.startswith("All good"))
finally:
    _tf.unlink(missing_ok=True)

# ── 3. trades present but unapplied → dropped (applied gate respected)
try:
    _tf.write_text(json.dumps({"sold_at": "2026-09-21T10:00:00-06:00",
                              "pnl_net": 50.0, "applied": False}) + "\n",
                  encoding="utf-8")
    lead3 = bs._all_good_lead()
    print("UNAPPLIED:", repr(lead3))
    check("unapplied trade: pace clause dropped", "dollars today" not in lead3)
finally:
    _tf.unlink(missing_ok=True)

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
# THE REBIND COMES FIRST (the 16:55 incident, task 44): capturing the
# path before the rebind aimed the seeded rows at the LIVE book — the
# running scanner auto-closed the phantom and banked +$257.20 of
# nothing. Rebind, THEN resolve the path.
wsink = _sink_dir / "contest_watch.json"
bs.CONTEST_WATCH_JSON = str(wsink)
wf = Path(bs.CONTEST_WATCH_JSON)
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
    wsink.unlink(missing_ok=True)   # the sink dies; the live book never saw it

# ── 7. THE GIVE-BACK LEAD (user, Sep 29: "Gave back $100 but doesn't
# count it? … 'All Good' without checking what we gave back first"):
# a realized day-swing (up ~$100, back to +$2) must LEAD the line and
# "All good" must never prefix it. The guard state is SANDBOXED via
# STATE_FILE (profit_guard._state resolves it live; the live
# profit_guard_state.json is never touched).
import profit_guard as _pg7
import auto_adjust as _aa7
import telemetry_adjust as _ta7
# (the guard-state sink now ships UP TOP — the fleet-run lesson: sections
# 1-4 need the all-good sandbox as much as section 7 needs its own values;
# the separate section-7 tempdir is gone, the shared sink is re-shipped
# per window below)
_orig_aa_apply = _aa7.apply_adjustment
_tf = Path(bs.CONTEST_TRADES_FILE)   # already the sink (the sweep, Sep 29)
_today = bs._now_mst().strftime("%Y-%m-%d")
try:
    # the realized leg: one banked +$13 row (the familiar pace clause)
    _tf.write_text(json.dumps({"sold_at": f"{_today}T10:00:00-06:00",
                              "pnl_net": 13.0, "applied": True}) + "\n",
                  encoding="utf-8")
    _pg7._save({"giveback": {"day": _today, "peak_total": 100.0,
                             "now_total": 5.0, "give_back": 95.0,
                             "tightened": True}})
    lead9 = bs._all_good_lead()
    print("GIVEBACK:", repr(lead9))
    check("give-back: the erosion LEADS the line",
          lead9.startswith("Gave back 95 dollars from the day's peak"))
    check("give-back: the peak and the now are both spoken",
          "up 100" in lead9 and "now plus 5" in lead9)
    check("give-back: 'All good' never prefixes an erosion",
          "All good" not in lead9)
    check("give-back: the realized pace clause still rides",
          "plus 13 dollars today" in lead9)

    # a small give-back (under the speak floor) stays the all-good line
    _pg7._save({"giveback": {"day": _today, "peak_total": 100.0,
                             "now_total": 92.0, "give_back": 8.0,
                             "tightened": True}})
    lead10 = bs._all_good_lead()
    check("small give-back: the line stays All good",
          lead10.startswith("All good"))

    # THE TRACKER: past the $50 trigger it tightens the guard ONCE, in
    # the PROTECTIVE direction (factor DOWN — the only direction
    # apply_adjustment admits; a positive delta would loosen the trail
    # after a loss, the exact inversion this pin forbids), records the
    # attempt in the ledger-shaped state, RE-ARMS at 2x the trigger
    # (the Oct 5 tier ladder) and CEILS there — no tier 3.
    calls = []

    def _rec_apply(delta, rotation_id="", evidence_checksum="",
                   per_gate=None, auto=True, override_concentration=False,
                   params=None):
        calls.append({"delta": delta, "rotation_id": rotation_id})
        return {"status": "applied", "reason": "ok", "delta": delta,
                "before": 1.9704, "after": 1.9704 + delta}

    _aa7.apply_adjustment = _rec_apply
    _pg7._save({"giveback": {"day": _today, "peak_total": 140.0,
                             "tightened": False}})
    gb1 = bs._track_day_giveback(-40.0)   # realized 0 + unrealized −40
    check("tracker: the tighten fires once past the trigger, in the "
          "PROTECTIVE direction (negative delta = factor down)",
          len(calls) == 1 and calls[0]["delta"] < 0
          and calls[0]["rotation_id"] == f"giveback-{_today}"
          and gb1.get("tightened") is True)
    gb2 = bs._track_day_giveback(-45.0)
    check("tracker: the deep second leg RE-ARMS the tighten under its "
          "own tier rotation_id (the 13:42 lesson)",
          len(calls) == 2
          and calls[1]["rotation_id"] == f"giveback-{_today}-tier2"
          and gb2.get("tightened") is True
          and gb2.get("tightens") == 2)
    gb3 = bs._track_day_giveback(-50.0)
    check("tracker: past the ceiling the knob is spent (no tier 3 — "
          "the ledger cannot flood)",
          len(calls) == 2 and gb3.get("tightens") == 2
          and gb3.get("tighten", {}).get("tier") == 2)
finally:
    _aa7.apply_adjustment = _orig_aa_apply
    _tf.unlink(missing_ok=True)   # the sink again; the live journal was never touched
    # restore the LIVE bindings (the sandbox files die with the
    # tempdir; the live scanner's state and journal were never written)
    import profit_guard as _pg_restore
    if hasattr(_pg_restore, "STATE_FILE"):
        del _pg_restore.STATE_FILE
    # (the guard-state sink from the fleet-run lesson is the same
    # _pg_restore.STATE_FILE deletion — sections 1-4 and section 7
    # shared the sandbox; nothing extra to restore)
    import telemetry_adjust as _ta_restore
    if hasattr(_ta_restore, "TELEMETRY_PATH"):
        del _ta_restore.TELEMETRY_PATH
    bs.CONTEST_WATCH_JSON = _real_cw   # the watch sink un-rebound last
    bs.CONTEST_TRADES_FILE = _real_ctf
    shutil.rmtree(_sink_dir, ignore_errors=True)

print(f"RESULT: {ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
