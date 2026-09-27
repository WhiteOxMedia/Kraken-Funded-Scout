"""test_fk_regression.py — THE FK REGRESSION TRIPWIRE (Sep 26).

The live jackpot.db once carried 287 FK orphans (dead tickets, paid_spins,
drawings, sessions pointing at deleted miners/users 4-8). Forensics:
production code has NO user/miner deletion paths at all — no DELETE
FROM users/miners in any module, no delete route. The orphans came from
RAW ad-hoc connections (the retest.py pattern: sqlite3.connect +
DELETE with PRAGMA foreign_keys left at SQLite's default 0), which no
route drive can ever exercise. So this drive pins the four-layer
contract that actually keeps the class dead:

  1. THE HOUSE CONNECTION REFUSES   db.get_db() runs PRAGMA
     foreign_keys=ON; deleting a user or a miner that own child rows
     raises IntegrityError and the rows survive — the linchpin holds
     through the real seam every module shares.
  2. THE RAW-CONNECTION HAZARD IS REAL   a bare sqlite3.connect on the
     same file defaults to foreign_keys=0 and deletes parents
     silently, orphaning their children — the factory is proven, not
     assumed (a regression that reverts get_db's PRAGMA fails 1.x;
     this section shows what such a revert would re-enable).
  3. THE SWEEP DISCIPLINE HEALS   through sweep_lib, the shared
     module: backup via the sqlite3 backup API,
     one BEGIN IMMEDIATE transaction, dependency-ordered deletes +
     reference nulling, an in-transaction PRAGMA foreign_key_check
     gate (non-empty -> ROLLBACK), the manifest audit row — the exact
     moves the live sweep ran, driven end-to-end to an empty check.
  4. THE LIVE CANARIES (read-only)   pool/jackpot.db must answer
     integrity ok, an EMPTY foreign_key_check, and hold the two
     standing fleet fixtures — a dirty live DB fails the fleet at
     commit time instead of surfacing in the next manual sweep.

Hermetic: sections 1-3 run on a temp-dir DB; section 4 opens the live
DB read-only (mode=ro, URI) and writes nothing. Prints
"RESULT: N passed, M failed".
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pool"))
sys.path.insert(0, str(HERE))

import field_crypto as fc                          # noqa: E402
fc.KEY_DIR = Path(tempfile.mkdtemp(prefix="fkreg_key_"))
fc.KEY_FILE = fc.KEY_DIR / "field_key"
fc._KEY_CACHE = None
fc.generate_key_file()

_tmp = Path(tempfile.mkdtemp(prefix="fkreg_drive_"))
import database as db                              # noqa: E402
db.DB_PATH = _tmp / "jackpot.db"
db.init_db()
import sweep_lib as sw                              # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


WALLET = ("4AoPP7szGLmG9qG1HQg6fXH84nyprSP4tbUTiCXmCoagWqAKZYGerv63kradK"
          "AqpufNZ5H3PGMNeDfrAUa9iZtiyCUMcCms")


def _fk_violations(conn):
    return len(conn.execute("PRAGMA foreign_key_check").fetchall())


print("== 1. THE HOUSE CONNECTION REFUSES PARENT DELETES ==")
uid = db.create_user("fkreg_user", "x")     # create_user returns the id
miner = db.get_or_create_miner(WALLET, "rig-fkreg")
db.add_tickets(miner["id"], "free", 3)
db.create_session("fkreg-live-token", uid, 9999999999)
db.add_audit_log(uid, "fkreg_note", '{"k": 1}')

with db.get_db() as conn:
    check("1.1", conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
          "db.get_db() enforces foreign keys (the linchpin PRAGMA)")

_refused = False
try:
    with db.get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
except sqlite3.IntegrityError:
    _refused = True
check("1.2", _refused,
      "deleting a user that owns sessions raises IntegrityError")

_refused = False
try:
    with db.get_db() as conn:
        conn.execute("DELETE FROM miners WHERE id = ?", (miner["id"],))
except sqlite3.IntegrityError:
    _refused = True
check("1.3", _refused,
      "deleting a miner that owns tickets raises IntegrityError")

with db.get_db() as conn:
    _still = (conn.execute("SELECT COUNT(*) c FROM users WHERE id=?",
                           (uid,)).fetchone()["c"]
              + conn.execute("SELECT COUNT(*) c FROM miners WHERE id=?",
                             (miner["id"],)).fetchone()["c"])
    _clean = _fk_violations(conn) == 0
check("1.4", _still == 2,
      "both parents survived the refusals (no partial delete)")
check("1.5", _clean,
      "the sandbox reads a clean foreign_key_check through the house")

print("== 2. THE RAW-CONNECTION HAZARD IS REAL (the orphan factory) ==")
raw = sqlite3.connect(str(db.DB_PATH))
check("2.1", raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0,
      "a bare connection defaults to foreign_keys=OFF — the factory's floor")
_nu = raw.execute("DELETE FROM users WHERE id = ?", (uid,)).rowcount
_nm = raw.execute("DELETE FROM miners WHERE id = ?", (miner["id"],)).rowcount
raw.commit()
check("2.2", _nu == 1 and _nm == 1,
      "the same deletes the house refused succeed silently on raw")
with db.get_db() as conn:
    _orphans = _fk_violations(conn)
check("2.3", _orphans >= 3,
      f"the delete orphaned {_orphans} child rows (tickets, session, audit)"
      " — exactly how the live DB got its 287")
raw.close()

print("== 3. THE SWEEP DISCIPLINE HEALS (sweep_lib owns the ritual) ==")
_statements = [
    ("sessions_of_dead_users",
     "DELETE FROM sessions WHERE user_id IS NOT NULL AND user_id NOT IN "
     "(SELECT id FROM users)", ()),
    ("tickets_of_dead_miners",
     "DELETE FROM tickets WHERE miner_id IS NOT NULL AND miner_id NOT IN "
     "(SELECT id FROM miners)", ()),
    ("null_dead_audit_user",
     "UPDATE audit_log SET user_id = NULL WHERE user_id IS NOT NULL "
     "AND user_id NOT IN (SELECT id FROM users)", ()),
]
_res = sw.sweep(db.DB_PATH, _statements, audit_action="fkreg.orphans_swept",
                backup_dir=_tmp, backup_name="fkreg_pre_sweep.db")
_bak = sqlite3.connect(str(_res["backup"]))
check("3.1", _fk_violations(_bak) >= 3,
      "the lib's backup API snapshot preserved the pre-state (the oracle)")
_bak.close()
check("3.2", _res["committed"]
      and _res["rowcounts"]["sessions_of_dead_users"] == 1
      and _res["rowcounts"]["tickets_of_dead_miners"] == 3
      and _res["rowcounts"]["null_dead_audit_user"] == 1,
      f"the ordered sweep removed exactly the debris "
      f"(rowcounts={_res['rowcounts']})")
check("3.3", _res["fk_after_commit"] == 0,
      "foreign_key_check is EMPTY on the same connection after the commit")
with db.get_db() as conn:
    check("3.4", _fk_violations(conn) == 0,
          "and the healed state is visible through the house seam")
    _manifest = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE action='fkreg.orphans_swept'"
    ).fetchone()["c"]
check("3.5", _manifest == 1,
      "the sweep left its manifest audit row (the live convention)")

print("== 3b. THE DISCIPLINE REFUSES (the lib's negative paths) ==")
# the gate: a statement that WOULD orphan rolls back wholesale
_uid2 = db.create_user("fkreg_gate_user", "x")
db.create_session("fkreg-gate-token", _uid2, 9999999999)
_refused = False
try:
    sw.sweep(db.DB_PATH,
             [("hazard_probe", "DELETE FROM users WHERE id = ?", (_uid2,))],
             audit_action="fkreg.gate_probe",
             backup_dir=_tmp, backup_name="fkreg_gate_probe.db")
except sw.SweepError:
    _refused = True
with db.get_db() as conn:
    _alive = conn.execute(
        "SELECT COUNT(*) c FROM users WHERE id=?", (_uid2,)).fetchone()["c"]
check("3.6", _refused and _alive == 1,
      f"an orphan-making statement raises SweepError and ROLLS BACK "
      f"(the user survives: {_alive})")
# the backup is never overwritten
_twice = False
try:
    sw.sweep(db.DB_PATH, [], audit_action="fkreg.twice",
             backup_dir=_tmp, backup_name="fkreg_pre_sweep.db")
except sw.SweepError:
    _twice = True
check("3.7", _twice,
      "a sweep refuses to overwrite its own backup target")

print("== 3c. THE ORPHAN DETECTOR (plan before you sweep) ==")
_rep = sw.orphans(db.DB_PATH)
check("3.8", _rep == [],
      f"a healed DB reports NO violations at all: {_rep}")
# rebuild the factory: a fresh miner+ticket and a fresh user, then
# delete the parents raw so each edge owns a known violation count
raw3 = sqlite3.connect(str(db.DB_PATH))
_m2 = db.get_or_create_miner(WALLET, "rig-fkreg-report")
db.add_tickets(_m2["id"], "free", 1)
_uid3 = db.create_user("fkreg_report_user", "x")
db.create_session("fkreg-report-token", _uid3, 9999999999)
db.add_audit_log(_uid3, "fkreg_report_note", "{}")
raw3.execute("DELETE FROM miners WHERE id = ?", (_m2["id"],))
raw3.execute("DELETE FROM users WHERE id = ?", (_uid3,))
raw3.commit()
_rep = sw.orphans(db.DB_PATH)
_edges = {(e["child_table"], e["child_col"], e["parent_table"]): e["violations"]
          for e in _rep}
check("3.9", _edges.get(("tickets", "miner_id", "miners")) == 1,
      f"the detector names the EDGE, not just a table total: {_edges}")
check("3.10", _edges.get(("sessions", "user_id", "users")) == 1
      and _edges.get(("audit_log", "user_id", "users")) == 1,
      "every violated edge is reported with its own count")
_samples = {e["child_table"]: e["sample_rowids"] for e in _rep}
check("3.11", len(_samples.get("tickets", [])) == 1,
      f"sample rowids ride along for the receipt: {_samples}")
# the detector is a READER: it must work on a filesystem read-only file
_ro = Path(str(db.DB_PATH) + ".ro_copy")
_src = sqlite3.connect(str(db.DB_PATH))
_dst = sqlite3.connect(str(_ro))
_src.backup(_dst)
_src.close()
_dst.close()
_ro.chmod(0o444)
try:
    _rep_ro = sw.orphans(_ro)
    check("3.12", _rep_ro == _rep,
          "the detector runs on a READ-ONLY file (mode=ro) and agrees")
finally:
    _ro.chmod(0o644)
    _ro.unlink(missing_ok=True)
check("3.13", '?mode=ro", uri=True' in
      Path(sw.__file__).read_text(encoding="utf-8"),
      "the detector OPENS with the mode=ro URI idiom in source (not just "
      "in a docstring) - SQLite itself then refuses every write, so a "
      "hot journal can never be replayed through a read-only report")
raw3.close()

print("== 4. THE LIVE CANARIES (read-only) ==")
live = sqlite3.connect(f"file:{(HERE / 'pool' / 'jackpot.db').as_posix()}"
                       "?mode=ro", uri=True)
check("4.1", live.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
      "the live DB answers integrity ok")
check("4.2", _fk_violations(live) == 0,
      "the live DB reads an EMPTY foreign_key_check — the 287 stay dead")
_fixtures = live.execute(
    "SELECT COUNT(*) c FROM users WHERE username IN "
    "('security_probe','prefs_live_probe_fleet')").fetchone()[0]
check("4.3", _fixtures == 2,
      f"both standing fleet fixtures present ({_fixtures}/2 — they must "
      "survive every sweep)")
live.close()

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
