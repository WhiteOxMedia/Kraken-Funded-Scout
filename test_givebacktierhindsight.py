"""Pins for giveback_tier_hindsight: every SPENT give-back tier —
applied, refused or queued — is graded against what the day did
afterward; the grade window is day-scoped (the ladder is a day-scoped
knob); expired rows ride as receipts, not attempts; and the report
never raises."""
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import giveback_tier_hindsight as gh

MST = ZoneInfo("America/Denver")
_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=MST)   # after every fixture day
_GB_CLS = "stayed too long - gave back over half the peak"
_TAKE_CLS = "took the goal - the discipline shape"

_fails = []
def check(n, cond, why):
    if cond:
        print(f"  ok {n} - {why}")
    else:
        _fails.append(n)
        print(f"  FAIL {n} - {why}")


def _g(sold_iso, realized, verdict, left, applied=True):
    ts = datetime.fromisoformat(sold_iso)
    return {"pair": "X/USD", "date": sold_iso[:10], "applied": applied,
            "realized": realized, "verdict": verdict, "left": left,
            "ex_ms": int(ts.timestamp() * 1000)}


def _tier(status="applied", tier=1, day="2026-10-05",
          applied_at="2026-10-05T12:46:11-06:00", before=2.0, after=1.8,
          reason="ok"):
    rid = "giveback-" + day + ("" if tier == 1 else "-tier" + str(tier))
    return {"applied_at": applied_at, "day": day, "status": status,
            "before": before,
            "after": (after if status == "applied" else before),
            "delta": -(before * 0.10), "reason": reason,
            "evidence": "giveback-day-peak", "rotation_id": rid}


_LEDGER = [_tier(),
           _tier(status="refused", tier=2,
                 applied_at="2026-10-05T18:20:00-06:00",
                 reason="daily_cap_exceeded"),
           {"rotation_id": "giveback-2026-10-05", "status": "expired",
            "applied_at": "2026-10-06T00:03:16-06:00"},
           {"rotation_id": "rotation-2026-10-05", "status": "applied",
            "applied_at": "2026-10-05T09:00:00-06:00"}]

_tmp = None
_real_bars = gh._grade_bars
_real_report = gh.REPORT_PATH
try:
    _tmp = tempfile.mkdtemp()
    gh.REPORT_PATH = Path(_tmp) / "giveback_tier_hindsight.md"
    gh._grade_bars = lambda: (10.0, 20.0)

    # ── 1. the ledger read: attempts kept, receipts and strangers out ──
    tiers = gh.spent_tiers(_LEDGER)
    check(1, len(tiers) == 2
          and tiers[0]["status"] == "applied"
          and tiers[1]["status"] == "refused",
          "spent_tiers keeps applied/refused/queued in stamp order, "
          "drops expired rows and non-giveback rotations")
    lifts = gh._lifts(_LEDGER)
    check(2, lifts == {"giveback-2026-10-05": "2026-10-06 00:03"},
          "the expired box rides as a lift receipt keyed by rotation_id")

    # ── 2. the verdict ladder ──────────────────────────────────────────
    _line, _tag = gh._grade(_tier(), [_g("2026-10-05T15:21:00-06:00",
                                         -5.0, _GB_CLS, 60.0)],
                            lifts, now=_NOW)
    check(3, _tag == "right moment" and "$60.00" in _line
          and "APPLIED 2.00→1.80" in _line,
          "applied + a hand-back over the bar grades RIGHT MOMENT with "
          "the before→after receipt")
    _line, _tag = gh._grade(_tier(), [_g("2026-10-05T15:21:00-06:00",
                                         45.0, _TAKE_CLS, 0.0)],
                            lifts, now=_NOW)
    check(4, _tag == "cut short" and "$+45.00" in _line
          and "box lifted 2026-10-06 00:03" in _line,
          "applied + a heal over the bar grades CUT SHORT and cites the "
          "box's lift")
    _line, _tag = gh._grade(_tier(), [], lifts, now=_NOW)
    check(5, _tag == "silence",
          "applied + no banked trade after grades SILENCE (cure and "
          "cost both unproven)")
    _line, _tag = gh._grade(_tier(status="refused",
                                  reason="daily_cap_exceeded"),
                            [_g("2026-10-05T15:21:00-06:00",
                                -5.0, _GB_CLS, 26.35)],
                            lifts, now=_NOW)
    check(6, _tag == "paid for" and "daily_cap_exceeded" in _line
          and "2.00 held" in _line,
          "refused + a hand-back over the bar grades PAID FOR with the "
          "refusal reason and the held factor")
    _line, _tag = gh._grade(_tier(status="refused",
                                  reason="daily_cap_exceeded"),
                            [_g("2026-10-05T15:21:00-06:00",
                                45.0, _TAKE_CLS, 0.0)],
                            lifts, now=_NOW)
    check(7, _tag == "cheap",
          "refused + a heal over the bar grades CHEAP")
    _line, _tag = gh._grade(_tier(), [_g("2026-10-05T15:21:00-06:00",
                                         5.0, "scratch - small move, "
                                         "small result", 0.0)],
                            lifts, now=_NOW)
    check(8, _tag == "mixed",
          "neither bar crossed with trades after grades MIXED")
    _line, _tag = gh._grade(_tier(), [_g("2026-10-05T15:21:00-06:00",
                                         -5.0, _GB_CLS, 60.0)],
                            lifts, now=datetime(2026, 10, 5, 20, 0,
                                                tzinfo=MST))
    check(9, _tag == "unproven",
          "today's own tier grades UNPROVEN — the window is still live")

    # ── 3. the day-scoped window ───────────────────────────────────────
    _line, _tag = gh._grade(_tier(), [_g("2026-10-06T01:00:00-06:00",
                                         -5.0, _GB_CLS, 99.0)],
                            lifts, now=_NOW)
    check(10, _tag == "silence",
          "a trade sold AFTER the tier's midnight is the next day's "
          "grade — the window does not bleed forward")
    _line, _tag = gh._grade(_tier(), [_g("2026-10-05T23:50:00-06:00",
                                         -5.0, _GB_CLS, 30.0)],
                            lifts, now=_NOW)
    check(11, _tag == "right moment",
          "a trade sold 23:50 same day stays inside the window")
    _line, _tag = gh._grade(_tier(), [_g("2026-10-05T10:00:00-06:00",
                                         -5.0, _GB_CLS, 99.0)],
                            lifts, now=_NOW)
    check(12, _tag == "silence",
          "a trade sold BEFORE the tier's stamp is pre-context, never "
          "post-window fact")
    _line, _tag = gh._grade(_tier(), [_g("2026-10-05T15:21:00-06:00",
                                         100.0, _GB_CLS, 99.0,
                                         applied=False)],
                            lifts, now=_NOW)
    check(13, _tag == "silence",
          "unbanked journal rows are opportunity notes, not the "
          "account's post-window fact")

    # ── 4. the labels and the bars ─────────────────────────────────────
    _line, _tag = gh._grade(_tier(tier=2), [_g("2026-10-05T15:21:00-06:00",
                                               -5.0, _GB_CLS, 60.0)],
                            lifts, now=_NOW)
    check(14, "t2 APPLIED" in _line,
          "the re-arm's tier index reads out of the rotation_id")
    import contest_account as _ca
    _real_thr = _ca.giveback_thresholds
    gh._grade_bars = _real_bars          # the REAL bars for this block
    try:
        _ca.giveback_thresholds = lambda: (40.0, 80.0)
        check(15, gh._grade_bars() == (20.0, 40.0),
              "the bars scale with the LIVE tier-1 trigger (half, whole)")
        def _boom():
            raise RuntimeError("unreadable")
        _ca.giveback_thresholds = _boom
        check(16, gh._grade_bars() == (10.0, 20.0),
              "an unreadable account degrades the bars to the 10k "
              "defaults — never to zero")
    finally:
        _ca.giveback_thresholds = _real_thr
        gh._grade_bars = lambda: (10.0, 20.0)   # back to the sandbox bars

    # ── 5. the report ──────────────────────────────────────────────────
    _book = [_g("2026-10-05T15:21:00-06:00", -5.0, _GB_CLS, 60.0)]
    _p = gh.write_report(ledger=_LEDGER, graded=_book, now=_NOW)
    check(17, _p is not None and _p.exists()
          and "hand-back bar $10.00" in _p.read_text(encoding="utf-8")
          and "ladder read: 1 right moment, 1 silence" in
          _p.read_text(encoding="utf-8"),
          "write_report prepends a section with the bars and a tag "
          "count, and returns the path")
    _p2 = gh.write_report(ledger=_LEDGER, graded=_book,
                          now=datetime(2026, 10, 6, 13, 0, tzinfo=MST))
    _txt = _p2.read_text(encoding="utf-8")
    check(18, _txt.startswith("### 2026-10-06 13:00")
          and "### 2026-10-06 12:00" in _txt[60:],
          "the newest section leads; the older pass survives beneath")
    _p3 = gh.write_report(ledger=[], graded=[], now=_NOW)
    check(19, _p3 is not None
          and "no spent give-back tiers" in _p3.read_text(encoding="utf-8"),
          "an empty ledger writes its own honest section")
    _bad = _tier()
    _bad["applied_at"] = "garbage"
    _p4 = gh.write_report(ledger=[_bad], graded=[], now=_NOW)
    check(20, _p4 is not None and "ungraded" in _p4.read_text(encoding="utf-8"),
          "an unreadable tier row grades UNGRADED without killing the pass")

    # ── 6. the wiring and the drift guards ─────────────────────────────
    _src = Path("background_scanner.py").read_text(encoding="utf-8")
    check(21, "import giveback_tier_hindsight as _gbh" in _src
          and "_gbh.write_report()" in _src
          and "GIVE-BACK TIER HINDSIGHT" in _src,
          "the cooldown review calls the hindsight pass beside the "
          "verdict study, fail-soft")
    import discipline as _dc
    _probe = [("stayed too long",
               _dc._asset_verdict(100.0, 101.0, 103.0, 1.5, 10.0, 1000.0)[0]),
              ("exited too soon",
               _dc._asset_verdict(100.0, 101.0, 102.0, 1.5, 5.0, 1000.0)[0]),
              ("goal hit and never exited",
               _dc._asset_verdict(100.0, 0.0, 102.0, 1.5, 0.0, 1000.0)[0])]
    check(22, all(cls.startswith(pre) for pre, cls in _probe)
          and set(p for p, _ in _probe) == set(gh.HAND_BACK_PREFIXES),
          "every HAND_BACK_PREFIX names a real _asset_verdict class — "
          "the give-back leg cannot silently drift from the verdict "
          "study's book")
finally:
    gh._grade_bars = _real_bars
    gh.REPORT_PATH = _real_report
    if _tmp:
        shutil.rmtree(_tmp, ignore_errors=True)

print()


def main():
    if _fails:
        print(f"FAILED: {_fails}")
        sys.exit(1)
    print("all pins green")


if __name__ == "__main__":
    sys.exit(main())
