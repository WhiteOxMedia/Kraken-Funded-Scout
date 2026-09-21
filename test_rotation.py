"""Test the rotation memory (Aug 30): yesterday context, re-entry display,
the profit re-arm ledger. THE LOCKOUT RETIRED (user rule, Sep 10: "delete
the rule") — the one-green-per-rotation cap and the 10-minute anti-spam
are OFF; the gate always allows and the ledger stays for the re-entry
display and the re-arm machinery."""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

import background_scanner as bs

PASS, FAIL = 0, 0
lines = []


def check(name, ok, detail=""):
    global PASS, FAIL
    lines.append(("PASS  " if ok else "FAIL  ") + name + "  " + str(detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


# isolate: use a scratch lockout file
bs.ROTATION_LOCKOUT_FILE = str(Path(__file__).parent / "_test_rotation_lockout.json")
try:
    os.unlink(bs.ROTATION_LOCKOUT_FILE)
except FileNotFoundError:
    pass

MST = ZoneInfo("America/Denver")

# ── 1. THE LOCK IS RETIRED (user rule, Sep 10) ────────────────────────
allowed, reason, reentry, fine = bs.rotation_gate("PUMP")
check("fresh asset allowed", allowed and not reentry)

bs.rotation_mark_fired("PUMP")
allowed, reason, reentry, fine = bs.rotation_gate("PUMP")
check("same-rotation re-fire -> ALLOWED (the cap is off)", allowed, reason)
check("...flagged as a re-entry (the popup still says so)", reentry)
check("the strict 'already ran' verdict can no longer return",
      "already ran" not in reason)

# a different asset is unaffected
allowed2, _, _, _ = bs.rotation_gate("BTC")
check("other asset unaffected", allowed2)

# ── 2. RE-ARM on rotation change (simulate by writing a stale rotation key) ──
now = datetime.now(MST)
win = bs._rotation_window(now)
cur_key = win[2]
other_key = next(k for k in bs.ROTATION_KEYS if k != cur_key and k != "20-24")
state = {"PUMP": {"date": now.strftime("%Y-%m-%d"), "rotation": other_key,
                  "fired_at": now.isoformat()}}
Path(bs.ROTATION_LOCKOUT_FILE).write_text(json.dumps(state))
allowed, reason, reentry, fine = bs.rotation_gate("PUMP")
check("new rotation -> re-armed", allowed, reason)
check("flagged as re-entry", reentry)
check("fine defaults to 0", fine == 0.0)

# the fine is RETIRED (Sep 21: "I don't want a re-entry gate — it was
# replaced with real data already"): no configuration re-arms a class
# tax — the same real-data gates judge every fire identically
bs.CONTEST_REENTRY_FINE_USD = 10.0
allowed, reason, reentry, fine = bs.rotation_gate("PUMP")
check("fine is retired: re-entry returns 0.0 even if misconfigured",
      reentry and fine == 0.0)
bs.CONTEST_REENTRY_FINE_USD = 0.0

# a NEW DAY re-arms everything (stale records pruned)
state = {"PUMP": {"date": "2026-08-01", "rotation": cur_key, "fired_at": "old"}}
Path(bs.ROTATION_LOCKOUT_FILE).write_text(json.dumps(state))
allowed, _, reentry, _ = bs.rotation_gate("PUMP")
check("old day record ignored + pruned", allowed and not reentry)

# ── 2b. THE PROFIT RE-ARM (user rule, Sep 6; the ledger machinery KEPT
#        Sep 10 after the lock's retirement — a profitable SOLD still
#        clears the record so the re-entry display and the re-arm stay
#        honest. A loss/scratch exit never calls this.)
bs.rotation_mark_fired("PUMP")
bs.rotation_mark_fired("BTC")   # a second ledger record
rearmed = bs.rotation_rearm_on_profit("PUMP")
check("profit re-arm lifts the ledger record", rearmed)
check("the lift is one-shot (record gone)", not bs.rotation_rearm_on_profit("PUMP"))
allowed, reason, reentry, _ = bs.rotation_gate("PUMP")
check("post: PUMP clean (no record at all)", allowed and not reentry, reason)
check("other assets' records only lift on their own sale",
      bs.rotation_rearm_on_profit("BTC"))

# an earlier-rotation record = the gate is already open -> nothing to
# disarm, and the re-entry context survives
state = {"PUMP": {"date": now.strftime("%Y-%m-%d"), "rotation": other_key,
                  "fired_at": now.isoformat()}}
Path(bs.ROTATION_LOCKOUT_FILE).write_text(json.dumps(state))
check("earlier-rotation record: nothing to disarm",
      not bs.rotation_rearm_on_profit("PUMP"))
allowed, _, reentry, _ = bs.rotation_gate("PUMP")
check("...and the re-entry context survives", allowed and reentry)

# no record at all -> clean no-op
Path(bs.ROTATION_LOCKOUT_FILE).write_text("{}")
check("no record: re-arm is a clean no-op",
      not bs.rotation_rearm_on_profit("PUMP"))


# ── 3. YESTERDAY CONTEXT on a synthetic cache ─────────────────────────
tmp_db = str(Path(__file__).parent / "_test_ohlcv_cache.db")
try:
    os.unlink(tmp_db)
except FileNotFoundError:
    pass
conn = sqlite3.connect(tmp_db)
conn.execute("CREATE TABLE ohlcv (pair TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL)")
real_cache = bs._OHLCV_CACHE_PATH
bs._OHLCV_CACHE_PATH = tmp_db


def put_window(days_back, rot_start, o, hi, cl, pair="TEST/USD"):
    day = datetime.now(MST) - timedelta(days=days_back)
    s = day.replace(hour=rot_start, minute=0, second=0, microsecond=0)
    for i in range(4):  # 4 hourly candles
        ts = int((s + timedelta(hours=i)).timestamp() * 1000)
        # candle 0 opens at o, mid candle hits hi, last closes at cl
        op = o if i == 0 else cl
        close = cl if i == 3 else (hi if i in (1, 2) else o)
        candle_hi = max(op, close, hi if i in (1, 2) else op)
        candle_lo = min(op, close)
        conn.execute("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,10)",
                     (pair, ts, op, candle_hi, candle_lo, close))
    conn.commit()


# blowoff: ran +5%, gave back 2/3 of it — on 2 of the last 3 days (majority)
rot_start = win[0]
put_window(1, rot_start, 100.0, 105.0, 101.5)   # ran 5%, closed +1.5% -> blowoff
put_window(2, rot_start, 100.0, 106.0, 101.0)   # ran 6%, closed +1% -> blowoff
put_window(3, rot_start, 100.0, 100.8, 100.4)   # quiet
ctx = bs.rotation_yesterday_context("TEST/USD")
check("blowoff majority detected", ctx and ctx["shape"] == "blowoff", ctx)
check("blowoff detail counts days", "2/3" in ctx.get("detail", ""), ctx.get("detail"))

# trend: ran and held, majority
conn.execute("DELETE FROM ohlcv")
put_window(1, rot_start, 100.0, 105.0, 104.5)   # ran 5%, closed +4.5% -> trend
put_window(2, rot_start, 100.0, 106.0, 105.5)   # trend
put_window(3, rot_start, 100.0, 100.8, 100.4)   # quiet
ctx = bs.rotation_yesterday_context("TEST/USD")
check("trend-and-held detected", ctx and ctx["shape"] == "trend", ctx)

# quiet: no 2% runs
conn.execute("DELETE FROM ohlcv")
put_window(1, rot_start, 100.0, 100.9, 100.5)
put_window(2, rot_start, 100.0, 100.7, 100.3)
put_window(3, rot_start, 100.0, 100.8, 100.4)
ctx = bs.rotation_yesterday_context("TEST/USD")
check("quiet detected", ctx and ctx["shape"] == "quiet", ctx)

# no data -> fail-open None
ctx = bs.rotation_yesterday_context("NOPE/USD")
check("no data -> None (fail-open)", ctx is None)

conn.close()
bs._OHLCV_CACHE_PATH = real_cache  # restored to the pre-suite value

# ── 4. LIVE cache sanity: BTC has data for the current rotation window ──
ctx = bs.rotation_yesterday_context("BTC/USD")
check("live BTC context computed", ctx is not None, ctx)

# ── 5. Source contracts: the retirement is wired honestly ────────────
bs_src = open("background_scanner.py", encoding="utf-8").read()
app_src = open("app.py", encoding="utf-8").read()
check("THE LOCK IS RETIRED — the gate says so",
      "RETIRED GATE" in bs_src and "always allows" in bs_src)
check("send_alert's lock branch is retired-marked (kept, dead)",
      "if not _allowed:  # dead since the Sep 10 retirement" in bs_src)
check("the header retires the cap AND the anti-spam",
      "10-minute per-pair anti-spam" in bs_src)
check("send_alert has blowoff demotion", "YESTERDAY BLOWOFF:" in bs_src)
check("send_alert marks on alert", "rotation_mark_fired(asset)" in bs_src)
check("QC gate checks rotation", "_rot_ok" in app_src)
check("QC gate checks blowoff", 'shape") == "blowoff"' in app_src)
check("QC marks on green", "rotation_mark_fired(asset)" in app_src)
check("QC rejection display kept (dead since the retirement, honest machinery)",
      "ALREADY RAN THIS ROTATION" in app_src)
check("card filter kept (Aug 31: with the confirmation window — a just-fired pick stays visible for CHECK)",
      'rotation_gate(h["asset"], confirm_window_min=_cwin)' in app_src)
check("the fine wording is retired from voice and card (Sep 21)",
      "fine risk" not in app_src and "fine risk" not in bs_src)

# ── 6. THE PROFIT RE-ARM wiring (Sep 6) ───────────────────────────────
check("the SOLD tap re-arms through the scanner's ONE function",
      "rotation_rearm_on_profit(_asset(_sp))" in app_src)
check("the re-arm fires ONLY on net profit (a loss keeps the lock)",
      app_src.find("if _net > 0:") < app_src.find("rotation_rearm_on_profit"))
check("the re-arm speaks it",
      "is re-armed" in app_src)
check("the re-arm is dup-safe (reload-if-missing pattern)",
      'hasattr(_bsr, "rotation_rearm_on_profit")' in app_src)
check("the scanner-side function lives with the lock it disarms",
      "def rotation_rearm_on_profit" in bs_src
      and bs_src.find("def rotation_rearm_on_profit")
      > bs_src.find("def rotation_mark_fired"))

# cleanup
for f in (bs.ROTATION_LOCKOUT_FILE, tmp_db):
    try:
        os.unlink(f)
    except FileNotFoundError:
        pass

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_rotation_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
