"""Test THE HAIL MARY EXCEPTION — KEPT, LOCK-FREE (user rule, Sep 5:
"remove the daily lock and everything to do with that idea" — then,
same day: "Keep the 'Hail Mary'").

WHAT IT IS NOW: the account-in-danger exception. The daily lock is
gone; the Hail Mary re-anchors: when the contest sits UNDER break-even
(no profits to protect, the account needs the shot) with platform room
left and no window used this rotation, a CHAMPION-grade setup —
verified uptrend on a major book, never a challenger — opens the
ONE-HOUR window: the popup is marked "— HAIL MARY", the trade clock
bounds by the hour, one window per rotation. Sizing inside the window
is the guard's full platform cap (0.34/dd) — the same cap every trade
gets now that the lock is gone; the exception adds the champion-only
bar, the hour clock, and the loud mark. THE DEATH LINE NEVER YIELDS."""
import sys
import tempfile
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


import contest_account as ca
import background_scanner as bs

MST = ZoneInfo("America/Denver")
TODAY = datetime.now(MST).strftime("%Y-%m-%d")
ca_src = Path("contest_account.py").read_text(encoding="utf-8")
bs_src = Path("background_scanner.py").read_text(encoding="utf-8")
ap_src = Path("app.py").read_text(encoding="utf-8")


def state(cur=10000.0, start=10000.0, **kw):
    s = {"tier": 10000, "start_balance": start, "current_balance": cur,
         "updated_at": datetime.now(MST).isoformat()}
    s.update(kw)
    return s


# ── 1. the account side: the window machinery ─────────────────────────
check("HAIL_MARY_WINDOW_MIN is 60 (the one-hour window)",
      ca.HAIL_MARY_WINDOW_MIN == 60)
check("the lock's bite-fraction constant is GONE (lock-specific)",
      not hasattr(ca, "HAIL_MARY_LOCK_BITE_FRAC"))
check("no window: hail_mary_active False, minutes 0",
      not ca.hail_mary_active(state())
      and ca.hail_mary_minutes_left(state()) == 0.0)
check("an active window reads True with minutes ≤ 60",
      ca.hail_mary_active(state(hail_mary_until=(
          datetime.now(MST) + timedelta(minutes=30)).isoformat()))
      and 29.0 < ca.hail_mary_minutes_left(state(hail_mary_until=(
          datetime.now(MST) + timedelta(minutes=30)).isoformat())) <= 30.0)
check("an expired window reads inactive",
      not ca.hail_mary_active(state(hail_mary_until=(
          datetime.now(MST) - timedelta(minutes=1)).isoformat())))
check("broken state fails closed (no window)",
      not ca.hail_mary_active(state(hail_mary_until="not-a-date")))

# ── 2. availability — account danger, LOCK-FREE ───────────────────────
check("under break-even with platform room: AVAILABLE (the contest is in danger)",
      ca.hail_mary_available(state(9990.0))[0] is True)
check("over break-even: not available (no emergency)",
      ca.hail_mary_available(state(10100.0))[0] is False)
check("at the death line: not available — nothing yields, ever",
      ca.hail_mary_available(state(9700.0))[0] is False)
check("exactly at break-even: available (no profits to protect)",
      ca.hail_mary_available(state(10000.0))[0] is True)
_now_rot = f"{datetime.now(MST).hour // 4 * 4:02d}-{datetime.now(MST).hour // 4 * 4 + 4:02d}"
_s_used = state(9990.0, hail_mary_rot=_now_rot, hail_mary_date=TODAY)
check("a window already used this rotation: not available "
      "(re-arms at the boundary)",
      ca.hail_mary_available(_s_used)[0] is False)
_other_rot = "00-04" if _now_rot != "00-04" else "04-08"
check("yesterday's window in another rotation doesn't block today",
      ca.hail_mary_available(state(9990.0, hail_mary_rot=_other_rot,
                                   hail_mary_date="2026-09-01"))[0] is True)
check("the lock's binding-floor precondition is GONE from the source",
      "binding_floor" not in ca_src and "daily_lock_floor" not in ca_src)

# ── 3. activation + state carry ───────────────────────────────────────
with tempfile.TemporaryDirectory() as _td:
    _old = ca.STATE_FILE
    try:
        ca.STATE_FILE = Path(_td) / "contest_account.json"
        ca.STATE_FILE.write_text(json.dumps(state(9990.0)), encoding="utf-8")
        s2 = ca.activate_hail_mary()
        check("activate opens the hour (active, ≤60 min left, count 1)",
              ca.hail_mary_active(s2)
              and 59.0 < ca.hail_mary_minutes_left(s2) <= 60.0
              and s2.get("hail_mary_count") == 1)
        s3 = ca.save_state(10000, 9980.0)
        check("a balance write mid-window carries the fields "
              "(save_state doesn't close it)",
              s3.get("hail_mary_count") == 1
              and ca.hail_mary_active(s3))
        on_disk = json.loads(ca.STATE_FILE.read_text(encoding="utf-8"))
        check("the window fields persist to disk (survives restarts)",
              "hail_mary_until" in on_disk and "hail_mary_rot" in on_disk)
    finally:
        ca.STATE_FILE = _old

# ── 4. sizing: the window is a MARK, not a sizing change (lock-free) ──
_platform_cap = ca.max_entry_usd(9990.0, 3.0, 10000.0)
_s_win = state(9990.0, hail_mary_until=(
    datetime.now(MST) + timedelta(minutes=30)).isoformat())
check("during the window sizing = the platform guard cap (the lock that "
      "used to crush it is gone — there is nothing to yield)",
      abs(ca.max_entry_usd(9990.0, 3.0, 10000.0) - _platform_cap) < 0.01)
check("max_entry_usd takes no state param anymore (platform-only math)",
      "def max_entry_usd(current_balance: float, window_dd_pct: float,\n                  start_balance: float | None = None) -> float:"
      in ca_src)
check("at the platform line = $0 even mid-window "
      "(the death line never yields)",
      ca.max_entry_usd(9700.0, 3.0, 10000.0) == 0.0)

# ── 5. the candidate side: _hail_mary_qualifies ──────────────────────
_deep_ok = SimpleNamespace(action="buy", confidence=92.0)


def _df(closes):
    return {"close": closes}


import pandas as pd
_rising = pd.DataFrame({"close": [1.0, 1.01, 1.02, 1.03, 1.04, 1.05,
                                  1.06, 1.07, 1.08, 1.09, 1.10, 1.11,
                                  1.12, 1.13, 1.14, 1.15, 1.16, 1.17,
                                  1.18, 1.19, 1.20, 1.21, 1.22, 1.23]})
_falling = pd.DataFrame({"close": list(_rising["close"])[::-1]})
q, why = bs._hail_mary_qualifies(_deep_ok, _rising, 12000.0, 10000.0, 89.0)
check("champion brain + verified uptrend + major book = qualifies", q, why)
q, why = bs._hail_mary_qualifies(
    SimpleNamespace(action="buy", confidence=85.0), _rising, 12000.0, 10000.0, 89.0)
check("below the champion floor = declined (never challenger-grade)",
      not q and "never challenger-grade" in why)
q, why = bs._hail_mary_qualifies(_deep_ok, _falling, 12000.0, 10000.0, 89.0)
check("no verified uptrend = declined", not q, why)
q, why = bs._hail_mary_qualifies(_deep_ok, _rising, 5000.0, 10000.0, 89.0)
check("not a major book = declined", not q, why)
q, why = bs._hail_mary_qualifies(None, _rising, 12000.0, 10000.0, 89.0)
check("no deep read = fail-closed", not q, why)
q, why = bs._hail_mary_qualifies(
    SimpleNamespace(action="sell", confidence=95.0), _rising, 12000.0, 10000.0, 89.0)
check("a sell read never qualifies", not q, why)

# ── 6. the alert path: trigger + marks (lock-free) ────────────────────
check("send_alert activates on account danger + a qualifying champion "
      "(no lock-bite test — the lock is gone)",
      "hail_mary_available(_acct)" in bs_src
      and "activate_hail_mary(_acct)" in bs_src
      and "HAIL_MARY_LOCK_BITE_FRAC" not in bs_src)
check("a challenger never gets the window (the bar is champion-grade)",
      "not _challenger" in bs_src.replace(" ", "")
      or "_hm_ok and not _challenger" in bs_src)
check("the window bounds the trade clock (one hour, not the rotation)",
      "min(_win_min, float(_ca.HAIL_MARY_WINDOW_MIN))" in bs_src)
check("the popup title carries the mark",
      '(" — HAIL MARY" if _hail_mary else "")' in bs_src)
check("the voice announces the emergency hour",
      "Hail Mary! The emergency hour is open! " in bs_src)
check("the message line explains full allocation = the guard's cap vs "
      "the death line",
      "full allocation = the " in bs_src
      and "guard's cap vs the DEATH LINE" in bs_src)
check("no 'the lock yields' language survives (the lock is gone)",
      "the lock yields" not in bs_src)
check("_hail_mary defaults False before the guard try "
      "(a broken read can't NameError the marks)",
      "_hail_mary = False" in bs_src)

# ── 7. the console: the QC agrees (never disagree) ────────────────────
check("the QC mirror is back: champion floor + UP trend + big book",
      "_hm_floor" in ap_src and "_trend == \"UP\" and _hm_big_book" in ap_src)
check("the QC result carries hail_mary + the decline reason",
      '"hail_mary"] = _qc_hm' in ap_src.replace(" ", "")
      or '["hail_mary"] = _qc_hm' in ap_src)
check("the green light reads the HAIL MARY mark",
      "SCALP GREEN LIGHT — 💪 HAIL MARY" in ap_src)
check("the sidebar plate shows the countdown (kept)",
      "💪 HAIL MARY" in ap_src and "WINDOWS USED" in ap_src)
check("the light panel header carries the window",
      "_hm_note" in ap_src and "the emergency hour" in ap_src)
check("the watch clock bounds by the hour when the window is open",
      "_champ_hm_win" in ap_src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_hailmary_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
