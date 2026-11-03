"""test_user_delete_guard.py - THE RAW-DELETE TRIPWIRE (Oct 30).

The Forensics: the prefs_live_probe_fleet fixture (user 42) was
DELETED RAW out of the live jackpot.db between 12:55:08 and
12:56:10 (Oct 30) and re-registered as a new id - stranding 4,493
audit rows + 90 sessions, the "287" orphan class reborn through the
one path no fleet drive can exercise: an out-of-band connection the
repo never contained. No pool route, no library function, no
installer step deletes users (this drive greps the source), so the
guard has to live IN THE DATABASE, not in code review.

The guard: `tools/install_user_delete_guard.py` installs
`users_no_orphan_delete`, a BEFORE DELETE trigger that fires for
EVERY connection - raw ones with PRAGMA foreign_keys=0 included -
and aborts the delete while the user still owns child rows. A
childless user stays deletable; a parent delete (via the sweep
discipline) is untouched. This drive pins:

  1. THE LIVE DB CARRIES THE GUARD   the trigger exists on
     pool/jackpot.db (mode=ro: this drive never mutates the live
     file), and the CLI's own --check answers 0 WIRED through it.
  2. THE LIVE GUARD IS THE REVIEWED GUARD   the live trigger's SQL
     matches the installer's canonical guard_sql() VERBATIM
     (whitespace-normalized) - the installed copy is the code the
     tripwire reads.
  3. THE GUARD BEHAVES   a sandbox with the guard applied REFUSES a
     raw foreign_keys=0 delete of a user with child rows (the
     refusal names the trigger), allows the same user once the
     discipline nulled the children, allows a childless delete, and
     never touches unrelated parents.
  4. RED-FIRST   the same auditor re-run on a guardless sandbox
     COPIES catches absence: the refusal check flips when the
     trigger is dropped.
  5. THE SOURCE TRIPWIRE   no source outside the declared authorities
     contains a users DELETE: the grep pattern
     (DELETE<TAB>FROM+users) is pinned by its own allowlist being
     exactly the tripwire drives that NEED the raw probe (this
     file, test_fk_regression, test_scheduler_vapid).

Prints "RESULT: N passed, M failed".
"""
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tools"))
INSTALLER = HERE / "tools" / "install_user_delete_guard.py"
LIVE = HERE / "pool" / "jackpot.db"
PY = sys.executable

import install_user_delete_guard as guard          # noqa: E402

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
CREATE TABLE sessions (id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id));
CREATE TABLE audit_log (id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id));
CREATE TABLE drawings (id INTEGER PRIMARY KEY);
CREATE TABLE tickets (id INTEGER PRIMARY KEY,
    drawing_id INTEGER REFERENCES drawings(id));
"""


def seed_and_guard(db=None):
    """A sandbox with the canon schema, seeded, guard applied. Pass a
    path to re-run the whole ritual on the SAME file (the idempotence
    probe)."""
    db = db or (Path(tempfile.mkdtemp(prefix="user_guard_")) / "jackpot.db")
    raw = sqlite3.connect(str(db))
    raw.executescript(SCHEMA)
    raw.execute("INSERT INTO users VALUES (1, 'parent_user')")
    raw.execute("INSERT INTO sessions VALUES (5, 1)")
    raw.execute("INSERT INTO audit_log VALUES (6, 1)")
    raw.execute("INSERT INTO drawings VALUES (9)")
    raw.execute("INSERT INTO tickets VALUES (10, 9)")
    raw.commit()
    status = guard.install(raw)
    raw.close()
    return db, status


def _behavior(db):
    """The battery of section 3, as one boolean: the guard refuses an
    orphaning raw delete (naming the trigger)."""
    raw = sqlite3.connect(str(db))
    raw.execute("PRAGMA foreign_keys=OFF")
    refused = False
    marker = False
    try:
        raw.execute("DELETE FROM users WHERE id = 1")
        raw.rollback()
    except sqlite3.Error as e:
        refused = True
        marker = guard.TRIGGER_NAME in str(e)
    finally:
        raw.close()
    return refused, marker


print("== 1. THE LIVE DB CARRIES THE GUARD ==")
c = sqlite3.connect(f"file:{LIVE.as_posix()}?mode=ro", uri=True)
check("1.1", guard.current(c) is not None,
      "pool/jackpot.db has no trigger users_no_orphan_delete - run "
      "python tools/install_user_delete_guard.py")
check("1.2", c.execute(
    "SELECT tbl_name FROM sqlite_master WHERE type='trigger' "
    "AND name=?", (guard.TRIGGER_NAME,)).fetchone()[0] == "users",
    "the trigger is on the users table")
check("1.3", len(c.execute("PRAGMA foreign_key_check").fetchall()) == 0,
      "the live DB answers a clean tripwire while guarded")
c.close()
check("1.4", subprocess.run([PY, str(INSTALLER), "--check"]).returncode == 0,
      "the CLI's own verdict is WIRED (rc 0)")

print("== 2. THE LIVE GUARD IS THE REVIEWED GUARD ==")
c = sqlite3.connect(f"file:{LIVE.as_posix()}?mode=ro", uri=True)
live_sql = guard.current(c)
check("2.1", guard._norm(live_sql)
      == guard._norm(guard.guard_sql(guard._child_edges(c))),
      f"the live trigger matches guard_sql globally: "
      f"{guard._norm(live_sql)[:80]}...")
check("2.2", guard.TRIGGER_NAME in live_sql,
      "the RAISE message carries the trigger name (the drive pins "
      "behavior by that marker)")
c.close()

print("== 3. THE GUARD BEHAVES (sandbox, raw FK-off) ==")
db, status = seed_and_guard()
check("3.0", status == "wired",
      f"the installer applied to a fresh sandbox: {status}")
_status2 = None
with sqlite3.connect(str(db)) as _re:
    _status2 = guard.install(_re)
check("3.0b", _status2 == "already-wired",
      "a re-apply on the SAME file is the idempotent no-op")
refused, marker = _behavior(db)
check("3.1", refused,
      "a raw foreign_keys=0 DELETE of a parent user is REFUSED")
check("3.2", marker,
      "the refusal names users_no_orphan_delete")
raw = sqlite3.connect(str(db))
_child = raw.execute("UPDATE audit_log SET user_id=NULL WHERE user_id=1").rowcount
raw.execute("DELETE FROM sessions WHERE user_id=1")
raw.commit()
_child_edits = _child
_n = raw.execute("DELETE FROM users WHERE id=1")
raw.commit()
check("3.3", _n.rowcount == 1 and _child_edits == 1,
      "once the discipline nulled/deleted the children, the delete "
      "goes through (the guard wedges nothing legit)")
raw.execute("INSERT INTO users VALUES (2, 'childless')")
raw.commit()
raw.execute("DELETE FROM users WHERE id=2")
raw.commit()
check("3.4", raw.execute(
    "SELECT COUNT(*) FROM users WHERE id=2").fetchone()[0] == 0,
      "a childless user deletes without ceremony")
check("3.5", raw.execute(
    "SELECT COUNT(*) FROM drawings").fetchone()[0] == 1,
      "unrelated parents (drawings) stay outside the guard's reach")
raw.close()
import shutil
shutil.rmtree(db.parent, ignore_errors=True)

print("== 4. RED-FIRST: absent guard -> the auditor goes red ==")
import tempfile as _temp
db3 = Path(_temp.mkdtemp(prefix="user_guard_mut")) / "jackpot.db"
raw = sqlite3.connect(str(db3))
raw.executescript(SCHEMA)
raw.execute("INSERT INTO users VALUES (1, 'parent_user')")
raw.execute("INSERT INTO sessions VALUES (5, 1)")
raw.execute("INSERT INTO audit_log VALUES (7, 1)")
raw.commit()
raw.close()
refused_red, _ = _behavior(db3)
check("4.1", refused_red is False,
      "a GUARDLESS sandbox refuses nothing - the raw factory "
      "deletes silently (the pin in 3.1 can fail)")
shutil.rmtree(str(db3.parent), ignore_errors=True)

print("== 5. THE SOURCE TRIPWIRE (code review's missing eye) ==")
PATTERN = re.compile(r"delete\s+from\s+[" + "'\"]?users\b", re.IGNORECASE)
AUTHORITY = {
    "test_fk_regression.py",        # the factory section: proves the hazard
    "test_scheduler_vapid.py",      # the orphan-alert leg: manufactures one
    "test_user_delete_guard.py",    # this drive
}
offenders = []
for dirpath, dirnames, filenames in os.walk(HERE):
    dirnames[:] = [d for d in dirnames if d not in (
        ".git", "node_modules", "dist", "__pycache__",
        ".pr-cleanup-worktree", "reports")]
    for fn in filenames:
        if not fn.endswith(".py") or fn in AUTHORITY:
            continue
        path = Path(dirpath) / fn
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines()):
            if PATTERN.search(line) and not line.strip().startswith("#"):
                offenders.append(f"{path.relative_to(HERE)}:{i + 1}")
check("5.1", not offenders,
      f"no DELETE of users outside the authorities: {offenders}")

passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
