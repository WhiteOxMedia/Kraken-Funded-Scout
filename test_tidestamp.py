"""Test the TIDE STAMP (user rule, Aug 31: hold 99% in every tradeable tide,
collect the data first). The scanner stamps the fleet state on every bridge
signal; the bot logs it on execution and hard-refuses an OUT stamp."""
import os
import sqlite3
import sys
from pathlib import Path

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


import signal_bridge as sb

# ── scratch DB (never touch the live signals.db) ──────────────────────
SCRATCH = str(Path(__file__).parent / "_test_tidestamp.db")
try:
    os.unlink(SCRATCH)
except FileNotFoundError:
    pass
sb.DB_PATH = Path(SCRATCH)
sb.init_db()

# ── 1. write_buy_signal stores the tide stamp ─────────────────────────
sid = sb.write_buy_signal(
    pair="XMR/USD", action="buy", confidence=90.0,
    entry_price=1.0, stop_loss=0.95, take_profit=1.10,
    risk_reward=2.5, position_size_usd=250.0,
    reasons=["lone wolf"], source="scout_auto",
    min_confidence=0, tide_state="FLAT", tide_pct=37.0,
)
check("stamped signal written", sid is not None, sid)

conn = sqlite3.connect(SCRATCH)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT tide_state, tide_pct FROM signals WHERE signal_id=?", (sid,)).fetchone()
check("tide_state stored", row["tide_state"] == "FLAT", row["tide_state"])
check("tide_pct stored", abs(float(row["tide_pct"]) - 37.0) < 0.01, row["tide_pct"])

# ── 2. defaults: no tide passed -> empty stamp (pre-stamp behavior) ───
sid2 = sb.write_buy_signal(
    pair="ZEC/USD", action="buy", confidence=90.0,
    entry_price=1.0, stop_loss=0.95, take_profit=1.10,
    risk_reward=2.5, position_size_usd=250.0,
    reasons=["strong"], source="scout_auto", min_confidence=0,
)
row2 = conn.execute("SELECT tide_state, tide_pct FROM signals WHERE signal_id=?", (sid2,)).fetchone()
check("unstamped write leaves blank tide", row2["tide_state"] in (None, ""), row2["tide_state"])
conn.close()

# ── 3. migration: an OLD DB (no tide columns) gets them added ─────────
OLD = str(Path(__file__).parent / "_test_tidestamp_old.db")
try:
    os.unlink(OLD)
except FileNotFoundError:
    pass
oc = sqlite3.connect(OLD)
oc.execute("""CREATE TABLE signals (
    signal_id TEXT PRIMARY KEY, pair TEXT, action TEXT, confidence REAL,
    reason TEXT, timestamp TEXT, valid_until TEXT, cooldown_until TEXT,
    price_at_signal REAL, entry_price REAL, stop_loss REAL, take_profit REAL,
    risk_reward REAL, position_size_usd REAL, source TEXT, status TEXT,
    acknowledged_at TEXT, executed_at TEXT, execution_price REAL, order_id TEXT,
    execution_reason TEXT, closed_at TEXT, pnl REAL, highest_price REAL)""")
oc.commit()
oc.close()
sb.DB_PATH = Path(OLD)
sb.init_db()  # migration runs here
cols = [r[1] for r in sqlite3.connect(OLD).execute("PRAGMA table_info(signals)").fetchall()]
check("migration adds tide_state", "tide_state" in cols)
check("migration adds tide_pct", "tide_pct" in cols)
check("migration keeps existing columns", "highest_price" in cols and "pnl" in cols)
# a write against the migrated old DB works
sid3 = sb.write_buy_signal(
    pair="BTC/USD", action="buy", confidence=90.0,
    entry_price=1.0, stop_loss=0.95, take_profit=1.10,
    risk_reward=2.5, position_size_usd=250.0,
    reasons=["x"], source="scout_auto", min_confidence=0,
    tide_state="RISING", tide_pct=78.0,
)
check("write works on migrated old DB", sid3 is not None, sid3)

# ── 4. scanner passes the tide at the bridge write ────────────────────
src = open("background_scanner.py", encoding="utf-8").read()
check("scanner stamps the bridge write", 'tide_state=(_tsw or "UNKNOWN")' in src)
check("scanner stamps the fleet pct", "tide_pct=_tpw" in src)

# ── 5. the tide guards — BOTH layers (restored Sep 4: the reader's
# belt-and-braces came back with the Sep 3-era gates; the scanner's
# upstream refusal stands too — belt AND braces)
BOT_READER = r"C:\Users\birds\Workspace\kraken_xrp_trading_bot\scout_signal_reader.py"
rsrc = open(BOT_READER, encoding="utf-8").read()
check("reader belt-and-braces: TIDE OUT stamp hard-refused (restored Sep 4)",
      'if _sig_tide == "OUT":' in rsrc)
check("reader's OUT rejection marks the signal",
      "Tide OUT at signal time" in rsrc)
check("reader's UNKNOWN stamp is tradeable (fail-open, exact match)",
      'str(signal.get("tide_state") or "")' in rsrc)
check("scanner refuses the bridge write on TIDE OUT (the upstream guard)",
      'if _ts0 == "OUT":' in src)
check("scanner's OUT refusal names the tide's PRICE (the Sep 10 priced wall — the blanket 'all ships sink together' refusal is retired)",
      "the OUT tide's price on the bot's ledger" in src
      and "no bridge write; all ships sink together" not in src)

# ── 6. the collector runs clean on the scratch (pending-only) DB ─────
sb.DB_PATH = Path(SCRATCH)
import subprocess
res = subprocess.run([sys.executable, "diag_tidedata.py", SCRATCH],
                     capture_output=True, text=True, timeout=60)
check("diag runs without error", res.returncode == 0, (res.stderr or "")[-200:])
_check = Path("_tidedata_check.txt").read_text(encoding="utf-8")
check("diag output file written", "TIDE DATA" in _check)
check("diag reports no stamped trades yet", "no stamped trades" in _check)

# ── 7. the collector aggregates outcomes: closed trades per tide ─────
sb.DB_PATH = Path(SCRATCH)
sb.init_db()
_conn = sqlite3.connect(SCRATCH)
_conn.execute("UPDATE signals SET status='closed', executed_at='2026-08-31T10:00:00', pnl=6.0 WHERE signal_id=?", (sid,))
_conn.execute("UPDATE signals SET status='closed', executed_at='2026-08-31T11:00:00', pnl=-2.0 WHERE signal_id=?", (sid2,))
_conn.commit()
_conn.close()
res2 = subprocess.run([sys.executable, "diag_tidedata.py", SCRATCH],
                      capture_output=True, text=True, timeout=60)
check("diag aggregates without error", res2.returncode == 0, (res2.stderr or "")[-200:])
_check2 = Path("_tidedata_check.txt").read_text(encoding="utf-8")
check("FLAT bucket shows 1 trade", "FLAT" in _check2 and " 1 " in _check2)
check("closed count appears", "closed" in _check2)
check("readiness line present", "READINESS" in _check2)

# cleanup (best-effort: Windows/Defender can hold a brief lock on fresh .db files)
import time as _time
for f in (SCRATCH, OLD):
    for _attempt in range(3):
        try:
            os.unlink(f)
            break
        except (FileNotFoundError, PermissionError):
            _time.sleep(0.5)
sb.DB_PATH = Path(r"C:\Users\birds\Workspace\kraken_scout\signals.db")  # leak-audit: ok (hand back to production at suite end; the suite ran hermetic before this)

lines.append(f"\nRESULT: {PASS} passed, {FAIL} failed")
open("_tidestamp_check.txt", "w", encoding="utf-8").write("\n".join(lines))
print("DONE")
