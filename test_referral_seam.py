"""test_referral_seam.py — THE REFERRALVELOCITY SEAM DRIVE (Sep 25).

Grades record_referral.py — the operator seam that gives the charter
formula's first component its real source:

  1. winner resolution: id | wallet | worker name, ambiguity refused
     with candidates, unknown refused
  2. the 30-day gate: a bonus follows a win — no win, no write
     (unless --force says the operator means it)
  3. the dry plan: without --yes NOTHING is written
  4. the real record: ledger receipt, credited key, ReferralVelocity
     moves, the daily plan reflects it
  5. idempotence: one bonus per winner per calendar month — a re-run
     cannot double-count
  6. the REAL CLI, in a real subprocess: show reads, record writes,
     record again refuses — with the DB rebound in a preamble so the
     live jackpot.db is never even opened
  7. source discipline: no quoted phrases, no server imports
  8. hermetic: the live jackpot.db is byte-identical throughout

House conventions: check(n, cond, why), RESULT line, sys.exit.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
passed = failed = 0


def check(n, cond, why):
    global passed, failed
    tag = "PASS" if cond else "FAIL"
    print(f"{tag}  {n}  {why}")
    if cond:
        passed += 1
    else:
        failed += 1


SANDBOX = tempfile.mkdtemp(prefix="referral_seam_drive_")
sys.path.insert(0, os.path.join(HERE, "pool"))

import database as db                                  # noqa: E402
db.DB_PATH = __import__("pathlib").Path(
    os.path.join(SANDBOX, "jackpot.db"))
db.init_db()

import jackpot_funding as funding                      # noqa: E402
import kraken_challenge as kc                          # noqa: E402
import record_referral as rr                           # noqa: E402

LIVE_DB = os.path.join(HERE, "pool", "jackpot.db")
LEDGER_KEY = funding.LEDGER_KEY


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_live_before = (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) if os.path.exists(LIVE_DB) else None

W_A, W_B, W_C = "4" + "a" * 94, "4" + "b" * 94, "4" + "c" * 94
m_a = db.get_or_create_miner(W_A, "alpha")
m_b = db.get_or_create_miner(W_B, "dup")
m_c = db.get_or_create_miner(W_C, "dup")
id_a, id_b, id_c = int(m_a["id"]), int(m_b["id"]), int(m_c["id"])


def _seed_win(miner_id):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO drawings (jackpot_amount, house_tax, winner_id, "
            "status, drawn_at) VALUES (0.01, 0.001, ?, 'drawn', "
            "strftime('%s','now'))", (miner_id,))


def _reset_ledger():
    db.set_setting(LEDGER_KEY, "[]")
    db.set_setting(kc.CREDITED_KEY, "[]")


def _ledger():
    return json.loads(db.get_setting(LEDGER_KEY, "[]"))


print("== 1. WINNER RESOLUTION ==")
miner, cand = rr.resolve_winner(str(id_a))
check(1.1, miner is not None and int(miner["id"]) == id_a,
      "a miner id resolves to its row")
miner, cand = rr.resolve_winner(W_A)
check(1.2, miner is not None and int(miner["id"]) == id_a,
      "a wallet address resolves to its row")
miner, cand = rr.resolve_winner("alpha")
check(1.3, miner is not None and int(miner["id"]) == id_a,
      "a unique worker name resolves to its row")
miner, cand = rr.resolve_winner("dup")
check(1.4, miner is None and cand is not None and len(cand) == 2,
      "an ambiguous name is refused WITH its candidates — the operator "
      "picks by id, never a coin-flip write")
miner, cand = rr.resolve_winner("nobody-here")
check(1.5, miner is None and cand == [],
      "an unknown token matches nothing")
miner, cand = rr.resolve_winner("")
check(1.6, miner is None and cand == [],
      "an empty token matches nothing")

print("== 2. THE 30-DAY GATE: A BONUS FOLLOWS A WIN ==")
_reset_ledger()
rc = rr.main(["record", "25.00", str(id_c)])
check(2.1, rc == 1 and _ledger() == [],
      f"recording for a never-won miner is refused (rc {rc}) and the "
      f"ledger is untouched")
rc = rr.main(["record", "25.00", str(id_c), "--force", "--yes"])
check(2.2, rc == 0 and len([r for r in _ledger()
                            if r.get("category") == "referral_bonus"]) == 1,
      "--force records it anyway — the operator's call, on the record")
_reset_ledger()

print("== 3. THE DRY PLAN: NO --yes, NO WRITE ==")
_seed_win(id_a)
rc = rr.main(["record", "25.00", str(id_a)])
check(3.1, rc == 0 and _ledger() == [],
      "without --yes the plan prints and NOTHING is written")

print("== 4. THE REAL RECORD ==")
rc = rr.main(["record", "25.00", "alpha", "--yes"])
rows = _ledger()
refs = [r for r in rows if r.get("category") == "referral_bonus"]
check(4.1, rc == 0 and len(refs) == 1 and refs[0]["amount_usd"] == 25.0,
      "the record lands: one referral_bonus receipt, $25.00")
check(4.2, refs[0].get("sponsor_id") == id_a
      and refs[0].get("source") == "kraken_challenge"
      and W_A not in json.dumps(rows),
      "sponsor = the miner id, source = kraken_challenge, and no "
      "wallet string anywhere in the ledger")
plan = funding.compute_daily_jackpot()
check(4.3, abs(plan["referral_velocity_usd"] - round(25.0 / 14, 4)) < 1e-9
      and abs(plan["daily_jackpot_usd"]
              - round(min(20.0, 25.0 / 14), 2)) < 1e-9,
      f"ReferralVelocity = 25/14 → DailyJackpot "
      f"${plan['daily_jackpot_usd']:.2f} (under the cap; the plan's "
      f"fields are display-rounded, so the pin is too)")

print("== 5. IDEMPOTENCE: ONE BONUS PER WINNER PER MONTH ==")
rc = rr.main(["record", "25.00", str(id_a), "--yes"])
check(5.1, rc == 1 and len([r for r in _ledger()
                            if r.get("category") == "referral_bonus"]) == 1,
      "a re-run the same month is refused — the ledger cannot "
      "double-count")
_seed_win(id_b)
rc = rr.main(["record", "25.00", str(id_b), "--yes"])
check(5.2, rc == 0 and len([r for r in _ledger()
                            if r.get("category") == "referral_bonus"]) == 2,
      "a DIFFERENT winner records independently — the loop pays once "
      "per winner, not once ever")
rc = rr.main(["record", "0", str(id_c), "--force", "--yes"])
check(5.3, rc == 1 and len(_ledger()) == 2,
      "a non-positive amount is refused before anything is written")

print("== 6. THE REAL CLI, IN A REAL SUBPROCESS ==")
# a fresh sandbox for the subprocess (the parent's state stays out of it)
SUB_DB = os.path.join(SANDBOX, "sub", "jackpot.db")
os.makedirs(os.path.dirname(SUB_DB), exist_ok=True)
_preamble = (
    "import sys, pathlib\n"
    f"sys.path.insert(0, {os.path.join(HERE, 'pool')!r})\n"
    f"import database as db\n"
    f"db.DB_PATH = pathlib.Path({SUB_DB!r})\n"
    f"db.init_db()\n"
)



def _run_cli(argv):
    code = (_preamble
            + "import json, time\n"
            + f"import database as db\n"
            + f"wallet = '4' + 'z'*94\n"          # built in PYTHON
            + f"with db.get_db() as c:\n"
            + f"    c.execute(\"INSERT OR IGNORE INTO miners "
              f"(wallet_address, worker_name) VALUES (?, 'subwinner')\", "
              f"(wallet,))\n"
            + f"    mid = c.execute(\"SELECT id FROM miners WHERE "
              f"worker_name='subwinner'\").fetchone()[0]\n"
            + f"    c.execute(\"INSERT OR IGNORE INTO drawings "
              f"(jackpot_amount, house_tax, winner_id, status, drawn_at) "
              f"VALUES (0.01, 0.001, ?, 'drawn', "
              f"strftime('%s','now'))\", (mid,))\n"
            + f"sys.argv = ['record_referral.py'] + {argv!r}\n"
            + f"exec(open({os.path.join(HERE, 'record_referral.py')!r}, "
              f"encoding='utf-8').read(), "
              f"{{'__name__': '__main__', '__file__': "
              f"{os.path.join(HERE, 'record_referral.py')!r}}})\n")
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        capture_output=True, text=True, timeout=120,
        cwd=SANDBOX)


r = _run_cli(["show"])
check(6.1, r.returncode == 0 and "ReferralVelocity" in r.stdout
      and "DailyJackpot" in r.stdout
      and "no receipts in the window" in r.stdout,
      "`show` reads the funding picture from a fresh DB (and never "
      "the live one — the preamble rebinds before exec)"
      + (f" [rc={r.returncode} err={r.stderr[-200:]}]"
         if (r.returncode != 0 or "ReferralVelocity" not in r.stdout)
         else ""))
r = _run_cli(["record", "25.00", "4" + "z" * 94, "--yes"])
check(6.2, r.returncode == 0 and "recorded: ledger row #" in r.stdout,
      f"`record --yes` through the real CLI: {r.stdout.strip().splitlines()[-1][:70] if r.stdout.strip() else r.stderr[-120:]}")
r = _run_cli(["record", "25.00", "4" + "z" * 94, "--yes"])
check(6.3, r.returncode == 1
      and "already credited this month" in r.stdout,
      "the second record through a FRESH PROCESS still refuses — "
      "idempotence lives in the DB, not the process")
def db_get_sub_ledger(path):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (LEDGER_KEY,)).fetchone()
        return row[0] if row else "[]"
    finally:
        conn.close()


sub_rows = json.loads(db_get_sub_ledger(SUB_DB))
sub_refs = [x for x in sub_rows if x.get("category") == "referral_bonus"]
check(6.4, len(sub_refs) == 1 and sub_refs[0]["amount_usd"] == 25.0,
      "the subprocess's ledger holds exactly one $25 receipt — the "
      "write happened once, at the DB level")

print("== 7. SOURCE DISCIPLINE ==")
with open(os.path.join(HERE, "record_referral.py"),
          encoding="utf-8") as f:
    src = f.read()
check(7.1, "Not sure where to get started" not in src
      and "Start for free at" not in src,
      "the CLI references, never quotes the mandated phrases — the "
      "constants stay in kraken_challenge/installer alone")
check(7.2, "import server" not in src and "from server" not in src
      and "fastapi" not in src.lower(),
      "no server imports, no routes touched — the dirty server files "
      "stay the other thread's")
check(7.3, "--yes" in src and "--force" in src
      and "store_true" in src and "add_subparsers" in src,
      "writes require explicit flags on an argparse CLI — the seam is "
      "opt-in at the command line, exactly like the installer's checkbox")

print("== 8. HERMETIC: THE LIVE DB NEVER MOVED ==")
if _live_before:
    check(8.1, (_sha(LIVE_DB), os.path.getmtime(LIVE_DB)) == _live_before,
          "the live jackpot.db is sha256+mtime identical throughout")
else:
    check(8.1, not os.path.exists(LIVE_DB),
          "no live jackpot.db exists — nothing to protect, nothing touched")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
