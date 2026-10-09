"""Headless drive of the REAL _boot_facts_voice (the factual boot greeting).

Extracts the function body from app.py source (the repo's established
pattern — importing app.py boots Streamlit) and execs it in a sandbox
with leaf dependencies stubbed. Scenarios:
  A. full facts      (healthy systems, tide, vol, account, ledger)
  B. broken scanner  (heartbeat down -> 'fix the systems')
  C. no tide/vol     (engine reads fail -> clauses drop, line still speaks)
  D. cooldown        (no_buy -> the rotation announces no new buys)
  E. unapplied-only ledger  (no fake 'booked' from unapplied trades)
  F. zero balance    (no money clause)
"""
import re
import sys
from pathlib import Path

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1
    print(("PASS  " if cond else "FAIL  ") + name)


src = open("app.py", encoding="utf-8").read()
m = re.search(r"def _boot_facts_voice\(block, no_buy, vol\) -> str:.*?(?=\ndef |\n# ── )",
              src, re.S)
assert m, "function not found in app.py source"
body = m.group(0)

NS = {
    "logger": type("L", (), {"debug": staticmethod(lambda *a, **k: None)})(),
    "_now_mst": lambda: type("D", (), {"strftime": staticmethod(lambda f: "2026-09-21")})(),
    "scanner_status": lambda: True,
    "bot_status": lambda: ("RUNNING", "10m ago"),
}


class _BS:
    @staticmethod
    def _fleet_tide():
        return (81.0, "RISING")


class _CA:
    MAX_LOSS_PCT = 3.0
    WEEKLY_GOAL_PCT = 12.0
    TRADING_DAYS_PER_WEEK = 5

    @staticmethod
    def load_state():
        return {"current_balance": 10012.94, "start_balance": 10000.0}


import json as _json
import builtins

REAL_OPEN = builtins.open


def _stub_open(path, *a, **k):
    if "background_scanner" in str(path):
        import io
        return io.StringIO("x = 1\n")
    return REAL_OPEN(path, *a, **k)


builtins.open = _stub_open
NS["__builtins__"] = builtins
exec(compile(body, "app.py#_boot_facts_voice", "exec"), NS)
builtins.open = REAL_OPEN

_F = NS["_boot_facts_voice"]

import os

# ── A. full facts ────────────────────────────────────────────────────
# (Sep 23: the cwd dance is gone — the exec'd function resolves its file
# reads from NS["__file__"], so an absolute __file__ gives the same
# coverage without ever moving the process's working directory.)
NS["__file__"] = str(Path("app.py").resolve())
line = _F("EU/US Overlap", False, {"data_weeks": 12.4,
                                   "thresholds": {"standard": 1.4}})
print("FULL:", line)
check("rotation announced", "EU/US Overlap rotation" in line)
# the tide clause comes from the REAL engine import (live values) —
# assert the shape, not a frozen number
check("tide clause live", "tide " in line and "percent of the fleet up" in line)
check("fleet knowledge clause", "12 weeks of data" in line and "noise floor 1.4 percent" in line)
# the balance is LIVE — the frozen "10,013"/"313" rotted the day the
# account moved (same lesson as the tide clause above): pin the clause
# shape, never the dollar figure
check("liquidity size clause", "dollars on the book" in line)
check("buffer clause", "above the death line" in line)
check("realistic pace clause", "pace 240 a day" in line)
check("systems-green tail", line.rstrip(".").endswith("systems green, hunting"))

# ── B. broken scanner ────────────────────────────────────────────────
NS["scanner_status"] = lambda: False
line_b = _F("EU/US Overlap", False, None)
print("DEAD-SCANNER:", line_b)
check("broken scanner -> fix verdict", "fix the systems" in line_b)
check("no money fantasy anywhere", "ton of money" not in line_b and "have some fun" not in line_b)
NS["scanner_status"] = lambda: True

# ── B2. bot down — named with the heartbeat's detail ─────────────────
NS["bot_status"] = lambda: ("NOT RUNNING", "no heartbeat file")
line_b2 = _F("EU/US Overlap", False, None)
print("BOT-DOWN:", line_b2)
check("bot-down named with heartbeat detail",
      "the bot is not running (no heartbeat file)" in line_b2)
check("bot-down -> fix verdict", "fix the systems" in line_b2)
NS["bot_status"] = lambda: ("RUNNING", "10m ago")

# ── B3. bot TRADING is HEALTHY, never an alarm ───────────────────────
NS["bot_status"] = lambda: ("TRADING", "0m ago")
line_b3 = _F("EU/US Overlap", False, None)
check("trading bot gets no false alarm", "the bot is" not in line_b3
      and "systems green, hunting" in line_b3)
NS["bot_status"] = lambda: ("RUNNING", "10m ago")

# ── C. no tide, no vol, no account (everything drops) ───────────────
import contest_account as _real_ca
_orig_load = _real_ca.load_state
_real_ca.load_state = staticmethod(lambda: (_ for _ in ()).throw(OSError("boom")))
line_c = _F("R3 EU/US Overlap", False, None)
print("BARE:", line_c)
check("bare line still speaks", line_c.startswith("R3 EU/US Overlap rotation."))
check("bare tail still lands", line_c.rstrip(".").endswith("systems green, hunting"))
_real_ca.load_state = _orig_load

# ── D. cooldown ──────────────────────────────────────────────────────
line_d = _F("R5 US Late", True, None)
print("COOLDOWN:", line_d)
check("cooldown announces itself", "no new buys" in line_d)

# ── E. unapplied trades are NOT booked ───────────────────────────────
_orig_json_loads = _json.loads
_json.loads = lambda s: {"applied": False, "pnl_net": 999.0,
                         "sold_at": "2026-09-21T10:00:00"}
line_e = _F("EU/US Overlap", False, None)
check("unapplied-only ledger not booked", "booked today" not in line_e
      and "down today" not in line_e)
_json.loads = _orig_json_loads

# ── E2. a sub-$1 day says nothing (no $0 chatter) ────────────────────
_ledger = os.path.join(os.getcwd(), "contest_trades.jsonl")
_backup = open(_ledger, "rb").read() if os.path.exists(_ledger) else None
try:
    with open(_ledger, "w", encoding="utf-8") as _fh:
        _fh.write(_json.dumps({"sold_at": "2026-09-21T10:00:00",
                               "pnl_net": 0.19, "applied": True}) + "\n")
    line_e2 = _F("EU/US Overlap", False, None)
    print("SUB-DOLLAR:", line_e2)
    check("sub-dollar day speaks no amount", "booked today" not in line_e2
          and "down today" not in line_e2)
finally:
    if _backup is not None:
        open(_ledger, "wb").write(_backup)
    elif os.path.exists(_ledger):
        os.remove(_ledger)

# ── F. zero balance ──────────────────────────────────────────────────
_real_ca.load_state = staticmethod(lambda: {"current_balance": 0.0,
                                            "start_balance": 10000.0})
line_f = _F("EU/US Overlap", False, None)
check("zero balance drops money clauses", "on the book" not in line_f)
_real_ca.load_state = _orig_load

print(f"RESULT: {PASS} passed, {FAIL} failed")


def main():
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    sys.exit(main())
