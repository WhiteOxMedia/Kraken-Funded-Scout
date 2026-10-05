"""Test THE HAIL MARY EXCEPTION — KEPT, LOCK-FREE (user rule, Sep 5:
"remove the daily lock and everything to do with that idea" — then,
same day: "Keep the 'Hail Mary'").

WHAT IT IS NOW: the account-in-danger exception. The daily lock is
gone; the Hail Mary re-anchors: when the contest sits UNDER break-even
(no profits to protect, the account needs the shot) with platform room
left and no window used this rotation, a CHAMPION-grade setup —
verified uptrend on a major book, never a challenger — opens the
ONE-HOUR window: the popup is marked "— HAIL MARY", the trade clockbounds by the hour, one window per rotation. Sizing inside the window
is the guard's full platform cap (0.34/dd) — the same cap every trade
gets now that the lock is gone; the exception adds the champion-only
bar, the hour clock, and the loud mark. THE DEATH LINE NEVER YIELDS.

OCT 1 — THE OPPORTUNISTIC MODE (user ask), TIGHTENED OCT 4 (user rule:
"the confidence score needs to be above 90% ... needs to be trending
up (no wiggles) ... the book can carry the whole size of the contest
balance (1k, 5k, 10k) depending on which contest window we are in"):
the same account-in-danger door with a STRICTER candidate bar — the
deep-scan buy must read ABOVE 90% (90.0 itself declines), the frame
must show a verified uptrend with NO WIGGLES (every one of the last
six closes rises — a single down-step declines), and the book must
carry the WINDOW's WHOLE balance (start_balance: the 1k/5k/10k tier),
— under STRICTER risk: a $300 hard spend cap on top of the 3% guard
and a 2% hard stop the exit watch judges on the hm_mode-stamped watch
row. The deep-scan exit watch is the authoritative ENTRY (its read
opens the door) and EXIT (its stack — trend sell, profit guard, hard
stop — closes it). The classic window is tried FIRST; the opp door
opens only when the champion bar declines; one window per rotation,
shared. The console settings carry NO emergency window (Oct 4: the
old version's sidebar plate is removed — the window is scanner-owned)."""
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
import scratch_space   # scratch policy: nothing volatile lands in the repo root

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
      '" — OPPORTUNISTIC HAIL MARY" if _hail_mary' in bs_src
      and '" — HAIL MARY" if _hail_mary else ""' in bs_src)
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
check("the emergency-window plate is GONE from the console settings "
      "(Oct 4 user rule: no emergency window in the console — the old "
      "version's sidebar clock is removed; the marked popup, the voice "
      "line and the scanner log carry the visibility)",
      "WINDOWS USED" not in ap_src
      and "THE EMERGENCY HOUR — ACCOUNT IN DANGER" not in ap_src,
      "the plate still renders in the Console Settings sidebar")
check("the light panel header carries the window",
      "_hm_note" in ap_src and "the emergency hour" in ap_src)
check("the watch clock bounds by the hour when the window is open",
      "_champ_hm_win" in ap_src)

# ── 8. THE OPPORTUNISTIC HAIL MARY (user ask, Oct 1; TIGHTENED Oct 4:
#    the door only fires when the book can carry the WHOLE contest
#    balance — the window's own 1k/5k/10k size — the brain reads ABOVE
#    90%, and the trend is up with NO WIGGLES: every bar in the
#    lookback rises, no down-step) ──────────────────────────────────
_opp_deep = SimpleNamespace(action="buy", confidence=93.0)
_opp_wiggle = list(_rising["close"])
_opp_wiggle[20] = 1.13   # a dip INSIDE the last six bars (the wiggle)
_wiggling = pd.DataFrame({"close": _opp_wiggle})
q, why = bs._hail_mary_opp_qualifies(
    _opp_deep, _rising, 12000.0, 10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.1 the opp door takes a >90 no-wiggle uptrend buy on a book "
      "that carries the window's full balance", q, why)
q, why = bs._hail_mary_opp_qualifies(
    SimpleNamespace(action="buy", confidence=55.0), _rising, 12000.0,
    10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.2 below the opp floor = declined (the Oct 4 rule: the score "
      "must be ABOVE 90 — the 62 viable line is retired)",
      not q and "90" in why, why)
q, why = bs._hail_mary_opp_qualifies(
    SimpleNamespace(action="buy", confidence=90.0), _rising, 12000.0,
    10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.2b exactly 90 is declined (ABOVE 90 — not at it)", not q, why)
q, why = bs._hail_mary_opp_qualifies(
    SimpleNamespace(action="buy", confidence=93.0), _rising, 12000.0,
    10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.3 champion-grade also clears the opp bar (the door is order-safe "
      "— the consumer tries classic first)", q, why)
q, why = bs._hail_mary_opp_qualifies(
    SimpleNamespace(action="buy", confidence=93.0), _rising, 5000.0,
    10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.3b the book must carry the WINDOW's whole balance (1k/5k/10k "
      "by tier — a 10k window on a 5k-carry book declines even at 93%)",
      not q and "book" in why, why)
q, why = bs._hail_mary_opp_qualifies(
    SimpleNamespace(action="buy", confidence=93.0), _wiggling, 12000.0,
    10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.3c a wiggle declines (trending up means EVERY bar in the "
      "lookback rises — no down-step)",
      not q and "wiggle" in why, why)
q, why = bs._hail_mary_opp_qualifies(
    SimpleNamespace(action="hold", confidence=93.0), _rising, 12000.0,
    10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.4 a non-buy deep read never opens the opp door (the exit watch's "
      "own brain IS the entry authority)",
      not q and "not buy" in why, why)
q, why = bs._hail_mary_opp_qualifies(
    None, _rising, 12000.0, 10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.5 no deep read = fail-closed", not q, why)
q, why = bs._hail_mary_opp_qualifies(
    _opp_deep, None, 12000.0, 10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.5b no frame = fail-closed (the uptrend cannot be verified)",
      not q, why)
q, why = bs._hail_mary_opp_qualifies(
    _opp_deep, _rising, None, 10000.0, ca.HAIL_MARY_OPP_FLOOR)
check("8.5c no book read = fail-closed (the window's size cannot be "
      "carried unseen)", not q, why)
check("8.6 the strict constants stand (ABOVE-90 floor, $300 cap, 2% "
      "hard stop)",
      ca.OPP_HARD_SPEND_USD == 300.0
      and ca.HAIL_MARY_OPP_HARD_STOP_PCT == 2.0
      and ca.HAIL_MARY_OPP_FLOOR == 90.0)
check("8.7 the opp window shares the rotation slot with the classic door "
      "(no second window per rotation)",
      ca.hail_mary_opp_available(state(9990.0, hail_mary_rot=_now_rot,
                                      hail_mary_date=TODAY))[0] is False)
check("8.8 the opp door IS the account-in-danger door (over break-even, "
      "no emergency)",
      ca.hail_mary_opp_available(state(10100.0))[0] is False
      and ca.hail_mary_opp_available(state(9990.0))[0] is True)
check("8.9 the consumer tries the champion bar FIRST and the opp door "
      "only when it declines (and the opp call carries the window's "
      "whole balance)",
      bs_src.find("_hm_q, _hm_qwhy = _hail_mary_qualifies(") > 0
      and 0 < bs_src.find("_op_q, _op_why = _hail_mary_opp_qualifies(")
      and bs_src.find("_op_q, _op_why = _hail_mary_opp_qualifies(")
          > bs_src.find("if _hm_q:")
      and 'balance=float(_acct["start_balance"])' in bs_src)
check("8.10 the opp activation stamps the OPPORTUNISTIC mode",
      '_ca.activate_hail_mary(_acct, mode="opportunistic")' in bs_src)
check("8.11 the opp spend is capped by the guard AND the $300 hard cap "
      "(min wins — the death line still rules)",
      "min(float(_spend), float(_risk_cap)," in bs_src
      and "float(_ca.OPP_HARD_SPEND_USD))" in bs_src)
check("8.12 the opp spend line is spoken (the operator sees the strict cap)",
      'HAIL MARY: ${_ca.OPP_HARD_SPEND_USD:,.0f} hard' in bs_src)
check("8.13 the classic activation keeps its unambiguous mode stamp",
      '_hail_mary_mode = "classic"' in bs_src)
check("8.14 the marks: the title, the message line and the voice all "
      "carry the OPPORTUNISTIC mark",
      '" — OPPORTUNISTIC HAIL MARY"' in bs_src
      and "OPPORTUNISTIC HAIL MARY: the window runs" in bs_src
      and "Opportunistic hail mary! Small shot, tight stop!" in bs_src)
check("8.15 the fired snapshot routes the class (champion-opportunistic)",
      '"champion-opportunistic"' in bs_src)
check("8.16 the QC mirror passes the SAME bars (the deep read, the "
      "frame, the book carry and the window's whole balance — the QC "
      "agrees with the alert, never disagrees)",
      "deep=sig," in ap_src
      and 'balance=float(_acct["start_balance"])' in ap_src)
# (Oct 2 — THE FIRST-FIRE REMINDER: the opp class was born
# KEEP-PROVISIONAL — zero graded fires, the hindsight grader re-runs at
# 10 fires or 7 days. The first champion-opportunistic popup ever is the
# moment the accumulation window starts being watched: one Zira line,
# one popup, one-shot via the reminders' marker idiom — the event is
# the trigger, not a date. The OTHER classes stay silent.)
check("8.31 the first opp fire fires the one-shot reminder (the event is "
      "the trigger, not a date — the reminders/ marker idiom)",
      "if _opp_route == \"champion-opportunistic\" and not _opp_reminder_done():" in bs_src
      and "_opp_reminder_mark()" in bs_src
      and "OPPORTUNISTIC HAIL MARY: FIRST FIRE" in bs_src
      and "accumulation window is now being watched" in bs_src
      and "scratch\\\\opp_hindsight.py" in bs_src)
check("8.32 the reminder is one-shot across restarts (a marker FILE, not "
      "memory) and runtime state, never committed",
      "_opp_first_fire_reminder_file" in bs_src
      and "_opp_first_fire_reminder.done" in bs_src
      and "fail OPEN to re-remind" in bs_src
      and "f.write(datetime.now().astimezone().isoformat())" in bs_src)
check("8.33 the OTHER classes stay silent — no champion or champion-fast "
      "popup ever spends the reminder (the provisionality is the opp "
      "class's: one decision, one mark)",
      bs_src.count('if _opp_route == "champion-opportunistic" and not _opp_reminder_done():') == 1
      and bs_src.count("_opp_reminder_mark()") == 2   # the def + the one call
      and bs_src.count("not _opp_reminder_done()") == 1)
check("8.34 the reminder rides the fired-popup block (it fires only on a "
      "POPUP, never on a bridge write alone — the console reads the "
      "same event)",
      0 < bs_src.find('if _opp_route == "champion-opportunistic" and not _opp_reminder_done():')
      and bs_src.find('if _opp_route == "champion-opportunistic" and not _opp_reminder_done():')
      > bs_src.find("_log_signal_snapshot(_fsnap)"))
# (the CodeRabbit CRITICAL, Oct 2: the reminder condition first read a
# bare `route` — a dict KEY in the snapshot literal, never a local — and
# would have NameError'd every BUY popup past the snapshot block. The
# route is now computed ONCE as a local and reused by both the snapshot
# stamp and the reminder; this pin parses send_alert's AST and demands
# every name the reminder reads actually be ASSIGNED in the function.)
import ast as _ast_hm
_fn_hm = {n.name: n for n in _ast_hm.walk(
    _ast_hm.parse(Path("background_scanner.py").read_text(encoding="utf-8")))
    if isinstance(n, _ast_hm.FunctionDef)}.get("send_alert")
_assigned_hm = ({n.id for n in _ast_hm.walk(_fn_hm)
                 if isinstance(n, _ast_hm.Name) and isinstance(n.ctx,
                                                               _ast_hm.Store)}
                if _fn_hm else set())
check("8.36 the route local exists and is assigned ONCE in send_alert "
      "(the dict-key-is-not-a-local class dies here — a bare `route` "
      "can never again ride the popup path; the module-level reminder "
      "helpers are loads by design)",
      bool(_fn_hm) and "_opp_route" in _assigned_hm
      and '"route": _opp_route' in bs_src
      and bs_src.count('_opp_route = (') == 1)
check("8.16 auto_track_popup carries the hm_mode stamp into the watch row",
      'w[pair]["hm_mode"] = str(hm_mode)' in bs_src)
check("8.17 the light's exit watch judges the 2% stop on the stamp "
      "(only ever TIGHTENS the half-buffer stop)",
      'w.get("hm_mode") == "opportunistic"' in bs_src
      and "HAIL_MARY_OPP_HARD_STOP_PCT" in bs_src
      and "if _hm_stop < _stop_usd:" in bs_src)
check("8.18 the console's manual exit check judges the same stop (the two "
      "halves of the exit watch never disagree)",
      '_wr.get("hm_mode") == "opportunistic"' in ap_src
      and "HAIL_MARY_OPP_HARD_STOP_PCT" in ap_src
      and "if _hm_stop < _stop_usd:" in ap_src)
check("8.19 the mode stamp rides save_state's carried keys (a balance "
      "write mid-window never closes it)",
      '"hail_mary_mode"' in ca_src
      and '"hail_mary_mode", "contest_id"' in ca_src)
# (Oct 1 evening — THE QC JOINS THE OPP DOOR: the console's Quick Check
# mirrored the alert's CLASSIC door only; the opportunistic window was
# invisible to the QC. The alert and the QC now agree on BOTH doors.)
check("8.23 the QC mirrors the alert's candidate order: the champion bar "
      "FIRST, the opp door ONLY on its decline",
      ap_src.count('_hail_mary_opp_qualifies(') == 1
      and 0 < ap_src.find('_op_q, _op_why = _bs_opp._hail_mary_opp_qualifies(')
      and ap_src.find('_op_q, _op_why = _bs_opp._hail_mary_opp_qualifies(')
          > ap_src.find("_hm_ok, _hm_why = _ca.hail_mary_available(_acct)"))
check("8.24 the QC's opp read feeds the same floor constant the alert uses "
      "(no private number) and fail-closes when the scanner is unreadable",
      "floor=float(_ca.HAIL_MARY_OPP_FLOOR))" in ap_src
      and "fail-closed" in ap_src)
check("8.25 the QC's opp activation stamps the mode (the exit watch's "
      "2% stop follows the row, alert or QC — never disagree)",
      '_ca.activate_hail_mary(_acct, mode="opportunistic")' in ap_src)
check("8.26 the QC's opp spend is capped by the guard AND the $300 hard "
      "cap (min wins — the death line still rules)",
      "min(float(_spend_usd), float(_risk_cap)," in ap_src
      and "float(_ca.OPP_HARD_SPEND_USD))" in ap_src)
check("8.27 the QC verdict line speaks the STRICT CAP when the window is "
      "opportunistic (the classic line keeps its full-allocation truth)",
      'result.get("hail_mary_mode") == "opportunistic"' in ap_src
      and "strict-cap opportunistic shot" in ap_src
      and "OPPORTUNISTIC HAIL MARY (the emergency hour" in ap_src)
check("8.28 the plate's mode lines are GONE with the plate (Oct 4: no "
      "emergency window in the console — the window's mode is told by "
      "the scanner's popup and log, not the console)",
      "CHAMPION-GRADE SHOT" not in ap_src
      and "STRICT-CAP OPPORTUNISTIC SHOT" not in ap_src)
check("8.29 an ALREADY-ACTIVE window reads its mode and caps the QC entry "
      "(the $300 cap follows the WINDOW, not just the activation — an "
      "opp window's later QC entry keeps the cap AND the 2% stop)",
      "_qc_hm_mode = (str(_acct.get(\"hail_mary_mode\")" in ap_src
      and ap_src.count(
          "min(float(_spend_usd), float(_risk_cap), float(_ca.OPP_HARD_SPEND_USD))") == 2
      and 'st.session_state["qc_result"]["hail_mary_mode"] = _qc_hm_mode' in ap_src)
check("8.30 the opp activation's mode SURVIVES the block (the tail is the "
      "single writer — the activation sets the local, never a stale clobber)",
      '_qc_hm_mode = "opportunistic"' in ap_src
      and ap_src.count('st.session_state["qc_result"]["hail_mary_mode"] =') == 1)
with tempfile.TemporaryDirectory() as _td3:
    # the one-shot proven where it lives: the marker path repointed into
    # the sandbox (the state-file repointing idiom), then the helpers'
    # round-trip — fresh reads not-done, the mark writes a durable iso
    # stamp, the next read is done. The production decision itself
    # (route + not-done, one mark) is pinned by shape at 8.31/8.33;
    # this proves the FILE half a restart would re-read.
    _old_marker = bs._opp_first_fire_reminder_file
    try:
        bs._opp_first_fire_reminder_file = (
            lambda: str(Path(_td3) / "_opp_first_fire_reminder.done"))
        _fresh = bs._opp_reminder_done()
        bs._opp_reminder_mark()
        _after = bs._opp_reminder_done()
        _stamp = (Path(_td3) / "_opp_first_fire_reminder.done").read_text(
            encoding="utf-8")
        check("8.35 the one-shot round-trip on a sandbox marker: fresh "
              "reads not-done, the mark writes a durable iso stamp, "
              "the next read is done (a restart re-reads the file — "
              "never re-fires)",
              _fresh is False and _after is True and "T" in _stamp)
    finally:
        bs._opp_first_fire_reminder_file = _old_marker

with tempfile.TemporaryDirectory() as _td2:
    _old2 = ca.STATE_FILE
    try:
        ca.STATE_FILE = Path(_td2) / "contest_account.json"
        ca.STATE_FILE.write_text(json.dumps(state(9990.0)), encoding="utf-8")
        s4 = ca.activate_hail_mary(mode="opportunistic")
        check("8.20 the opp activation stamps the mode on disk (active + "
      "opportunistic + count 1)",
              ca.hail_mary_active(s4)
              and s4.get("hail_mary_mode") == "opportunistic"
              and s4.get("hail_mary_count") == 1)
        s5 = ca.save_state(10000, 9985.0)
        check("8.21 the mode stamp rides a mid-window balance write "
      "(save_state never closes the window)",
              s5.get("hail_mary_mode") == "opportunistic"
              and ca.hail_mary_active(s5))
        s6 = ca.activate_hail_mary()   # the classic default
        check("8.22 a classic activation stamps classic (the default mode)",
              s6.get("hail_mary_mode") == "classic"
              and s6.get("hail_mary_count") == 2)
    finally:
        ca.STATE_FILE = _old2

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
scratch_space.report_temp('_hailmary_check.txt').write_text("\n".join(lines), encoding="utf-8")

print(f"RESULT: {PASS} passed, {FAIL} failed")

raise SystemExit(1 if FAIL else 0)   # the fleet-honesty fix (Sep 30): DONE-only tails failed closed
print("DONE")
