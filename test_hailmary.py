"""Test THE HAIL MARY EXCEPTION (user rule, Sep 4: "the daily lock is
protecting my profits but I am under, no profits yet. Contest in danger.

daily lock gets triggered and Activate Hail Mary Mode... When a verified
upward trend is detected on a high-liquidity book with strong momentum
and confirmed stability, authorize a one-hour full-allocation trade
window... Risk controls remain active; no uncontrolled exposure. After
one hour, revert to standard daily lock until challenged again by
another 'Hail Mary'").

THE INTERPRETATION ON RECORD: "full allocation" = the death-line guard's
full cap against the PLATFORM buffer (0.34/dd), NEVER the whole balance
(the user's own rule 4). The exception relaxes the LOCK only; the death
line, the guard, the knife, the exits all stand. One window per
rotation; the next rotation re-arms the challenge."""
import json
import sys
import tempfile
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

MST = ZoneInfo("America/Denver")
ca_src = Path("contest_account.py").read_text(encoding="utf-8")
bs_src = Path("background_scanner.py").read_text(encoding="utf-8")
ap_src = Path("app.py").read_text(encoding="utf-8")

# ── 1. the constants + the account layer ──────────────────────────────
check("the one-hour window + the lock-bite threshold are defined",
      ca.HAIL_MARY_WINDOW_MIN == 60 and ca.HAIL_MARY_LOCK_BITE_FRAC == 0.25)
check("the three hail mary functions exist",
      all(f in ca_src for f in ("def hail_mary_active", "def hail_mary_available",
                                "def activate_hail_mary", "def hail_mary_minutes_left")))
check("effective_buffer yields the lock during the window",
      "if hail_mary_active(s):" in ca_src.split("def effective_buffer")[1].split("def ")[0])
check("max_entry_usd sizes vs the platform line during the window",
      "hail_mary_active(state)" in ca_src.split("def max_entry_usd")[1].split("def ")[0])
check("save_state carries the hail mary fields (a balance write mid-window "
      "must not silently close it)",
      '"hail_mary_until", "hail_mary_date", "hail_mary_rot",' in ca_src)

# ── 2. functional: the account preconditions (sandboxed state) ───────
def _state(**over):
    s = {"tier": 10000, "start_balance": 10000.0, "current_balance": 9990.0,
         "daily_peak_date": datetime.now(MST).strftime("%Y-%m-%d"),
         "daily_start_balance": 9980.0, "daily_peak_balance": 10010.0}
    s.update(over)
    return s

try:
    with tempfile.TemporaryDirectory() as td:
        orig_state_file = ca.STATE_FILE
        ca.STATE_FILE = Path(td) / "acct.json"

        # under break-even (9990 < 10000), lock binding (floor 9999.5 > 9700),
        # platform room (buffer 290) -> AVAILABLE
        s = _state()
        ok, why = ca.hail_mary_available(s)
        check("under break-even + the lock binds -> AVAILABLE", ok, why)
        check("the lock floor actually binds in the fixture",
              ca.binding_floor(s) == ("daily-lock", 9999.5),
              ca.binding_floor(s))

        # over break-even: the lock protects REAL profit -> no exception
        ok, why = ca.hail_mary_available(_state(current_balance=10050.0,
                                                daily_peak_balance=10070.0))
        check("over break-even -> NOT available (real profit is protected)",
              not ok and "break-even" in why, why)

        # at the death line: NOTHING yields, ever
        ok, why = ca.hail_mary_available(_state(current_balance=9690.0,
                                                daily_start_balance=9695.0,
                                                daily_peak_balance=9710.0))
        check("at the death line -> NOT available (the line never yields)",
              not ok and "death line" in why, why)

        # the platform line binds (no daily gain) -> nothing to except
        ok, why = ca.hail_mary_available(_state(daily_start_balance=9990.0,
                                                daily_peak_balance=9990.0))
        check("platform binding (no lock) -> NOT available", not ok, why)

        # activation: the window opens, the lock yields, sizing = platform
        ca._write_state(_state())
        s2 = ca.activate_hail_mary()
        check("activation opens the window", ca.hail_mary_active(s2))
        check("minutes left ~ 60", 55 <= ca.hail_mary_minutes_left(s2) <= 60,
              ca.hail_mary_minutes_left(s2))
        check("during the window: effective_buffer = the PLATFORM buffer",
              abs(ca.effective_buffer(s2) - (9990.0 - 9700.0)) < 0.01,
              ca.effective_buffer(s2))
        _platform_cap = ca.max_entry_usd(9990.0, 3.0, 10000.0, state=None)
        check("during the window: the guard cap = the platform-only cap",
              abs(ca.max_entry_usd(9990.0, 3.0, 10000.0, state=s2) - _platform_cap) < 0.01,
              (ca.max_entry_usd(9990.0, 3.0, 10000.0, state=s2), _platform_cap))
        check("the cap is the GUARD's (0.34/dd), never the whole balance",
              _platform_cap < 9990.0 and abs(_platform_cap - 290.0 * 0.34 / 0.03) < 1.0,
              _platform_cap)
        check("the rotation is stamped (one per rotation)",
              s2.get("hail_mary_rot") and s2.get("hail_mary_count") == 1)

        # expiry: the lock rebinds
        s3 = dict(s2)
        s3["hail_mary_until"] = (datetime.now(MST) - timedelta(minutes=1)).isoformat()
        check("after the hour: the lock rebinds (effective = the lock buffer)",
              ca.effective_buffer(s3) < 0, ca.effective_buffer(s3))
        ok, why = ca.hail_mary_available(s3)
        check("after the hour, same rotation: NOT available (one per rotation)",
              not ok and "rotation" in why, why)

        # the next rotation re-arms the challenge
        _hour = datetime.now(MST).hour
        _other_rot = f"{((_hour + 4) // 4 * 4) % 24:02d}-{((_hour + 4) // 4 * 4 + 4) % 24:02d}"
        s4 = dict(s3)
        s4["hail_mary_rot"] = _other_rot
        ok, why = ca.hail_mary_available(s4)
        check("the next rotation re-arms the challenge", ok, why)

        # save_state carries the window (a balance write mid-window)
        s5 = ca.save_state(10000, 9992.0)
        check("a balance write mid-window keeps the window open",
              s5.get("hail_mary_until") and ca.hail_mary_active(s5))
        ca.STATE_FILE = orig_state_file
finally:
    pass

# ── 3. the candidate side: _hail_mary_qualifies ──────────────────────
try:
    import pandas as pd
    import background_scanner as bs
    _rising = pd.DataFrame({"close": [1.0 + 0.01 * i for i in range(40)]})
    _falling = pd.DataFrame({"close": [2.0 - 0.01 * i for i in range(40)]})
    _deep_ok = SimpleNamespace(action="buy", confidence=91.0)
    q, why = bs._hail_mary_qualifies(_deep_ok, _rising, 12000.0, 10000.0, 89.0)
    check("champion-grade + verified uptrend + major book -> QUALIFIES",
          q and "champion-grade" in why, why)
    q, why = bs._hail_mary_qualifies(SimpleNamespace(action="buy", confidence=85.0),
                                     _rising, 12000.0, 10000.0, 89.0)
    check("a challenger-grade read does NOT qualify (a Hail Mary is "
          "never challenger-grade)", not q and "champion floor" in why, why)
    q, why = bs._hail_mary_qualifies(_deep_ok, _falling, 12000.0, 10000.0, 89.0)
    check("no verified uptrend -> does not qualify", not q and "uptrend" in why, why)
    q, why = bs._hail_mary_qualifies(_deep_ok, _rising, 5000.0, 10000.0, 89.0)
    check("a thin book does not qualify (majors only)", not q and "major book" in why, why)
    q, why = bs._hail_mary_qualifies(None, _rising, 12000.0, 10000.0, 89.0)
    check("no deep read -> fail-closed", not q)
    q, why = bs._hail_mary_qualifies(SimpleNamespace(action="sell", confidence=95.0),
                                     _rising, 12000.0, 10000.0, 89.0)
    check("a sell read never qualifies", not q)
except Exception as exc:
    check("qualify checks ran", False, exc)

# ── 4. the gate + the marks (source) ──────────────────────────────────
check("the gate asks for the exception when the lock BITES",
      "HAIL_MARY_LOCK_BITE_FRAC" in bs_src and "hail_mary_available(_acct)" in bs_src)
check("activation recomputes the cap vs the platform line",
      "activate_hail_mary(_acct)" in bs_src)
check("the trade window bounds by the hour (not the rotation)",
      "min(_win_min, float(_ca.HAIL_MARY_WINDOW_MIN))" in bs_src)
check("the popup title carries the mark", "— HAIL MARY" in bs_src)
check("the voice announces it", "Hail Mary! The daily lock yields for one hour!" in bs_src)
check("the message names the death line + the guard's cap",
      "guard's cap vs the DEATH LINE" in bs_src
      and "never the whole balance" in bs_src)
check("the unqualified lock block still logs (with the Hail Mary note)",
      "the Hail Mary window is closed or unqualified" in bs_src)
check("the guard message names the window",
      "HAIL MARY — the lock yields" in bs_src)

# ── 5. the QC agrees (never disagree) ─────────────────────────────────
check("the QC mirrors the activation (champion floor + UP trend + big book)",
      'float(sig.confidence) >= _hm_floor' in ap_src
      and '_trend == "UP" and _hm_big_book' in ap_src
      and "activate_hail_mary(_acct)" in ap_src)
check("the QC result carries hail_mary + the decline reason",
      '"hail_mary"] = _qc_hm' in ap_src.replace(" ", "")
      or '["hail_mary"] = _qc_hm' in ap_src)
check("the green light names the HAIL MARY",
      "SCALP GREEN LIGHT — 💪 HAIL MARY" in ap_src)
check("the watch's clock bounds by the hour during the window",
      "_champ_hm_win" in ap_src)
check("the sidebar plate shows the countdown",
      "💪 HAIL MARY" in ap_src and "MIN LEFT" in ap_src)
check("the light panel header carries the window",
      "HAIL MARY {max(_hm_left, 0):.0f}m (the lock yields)" in ap_src)
check("the AT THE LOCK toast explains the Hail Mary path when unqualified",
      "_hm_why_txt" in ap_src)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
Path("_hailmary_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("DONE")
if FAIL:
    sys.exit(1)
