"""test_sweep_cli.py - THE SWEEP-CLI DRIVE (Sep 27).

Pins sweep_db.py, the operator face of sweep_lib, hermetically and
against the live file (read-only):

  1. DRY-RUN IS THE DEFAULT AND TOUCHES NOTHING
     a bare invocation rehearses the labeled statements on a throwaway
     backup-API copy: the real rowcounts print, the gate verdict
     (would COMMIT / would ROLLBACK) names what --apply would do, and
     the target DB's logical state is identical after the run.
  2. THE RECEIPT IS HONEST
     a sandbox seeded with orphans prints the exact per-label counts
     and still leaves the original orphaned; an UNCOVERED FK edge
     (a table the statement set does not know) flips the verdict to
     "would ROLLBACK" - the gate catches unknown edges BEFORE --apply.
  3. --APPLY GOES THROUGH THE DISCIPLINE
     backup beside the DB, manifest audit row in audit_log, clean
     foreign_key_check after; and the discipline REFUSES (exit 1,
     data untouched) when the gate would fire.
  4. ARGUMENT HYGIENE
     unknown args and a pathless --db exit 2 with usage; a missing
     database exits 1.
  5. THE LIVE CANARY (read-only)
     the bare CLI against the real pool/jackpot.db exits 0 with the
     dry-run receipt and the live DB's logical state is unchanged -
     a bare `python sweep_db.py` on the live file is always safe.

Minimal-schema sandboxes (only the FK edges + audit_log the CLI and
sweep_lib touch) keep this drive fast and decoupled from the app
schema; the lib itself is pinned against the REAL schema by
test_fk_regression. Prints "RESULT: N passed, M failed".
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE / "tools" / "sweep_db.py"
PY = sys.executable

results = []


def check(n, cond, why=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {n}"
          + (f"  ({why})" if why and not cond else ""))


def _run(args, cwd=HERE):
    return subprocess.run([PY, str(CLI), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=120, cwd=cwd)


SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
CREATE TABLE miners (id INTEGER PRIMARY KEY, wallet TEXT);
CREATE TABLE drawings (id INTEGER PRIMARY KEY, seed TEXT);
CREATE TABLE tickets (id INTEGER PRIMARY KEY, miner_id INTEGER
    REFERENCES miners(id), drawing_id INTEGER REFERENCES drawings(id));
CREATE TABLE paid_spins (id INTEGER PRIMARY KEY, miner_id INTEGER
    REFERENCES miners(id));
CREATE TABLE sessions (id INTEGER PRIMARY KEY, user_id INTEGER
    REFERENCES users(id));
CREATE TABLE audit_log (id INTEGER PRIMARY KEY, user_id INTEGER
    REFERENCES users(id), action TEXT, details TEXT);
"""

LABELS = ("tickets_of_dead_miners", "tickets_of_dead_drawings",
          "paid_spins_of_dead_miners", "sessions_of_dead_users",
          "null_dead_audit_user")

SANDBOX = Path(tempfile.mkdtemp(prefix="sweep_cli_drive_"))


def _live_ready():
    """The default-target checks rehearse against THE LIVE
    pool/jackpot.db (sweep_db.py's DEFAULT_DB), operator state the
    checkout does not carry: gitignored, absent in a fresh clone (and
    an empty file any previous drive's unguarded connect created once
    crashed this very section on `no such table: sessions`). Sections
    2-4 grade the CLI entirely on sandboxes; when the live file holds
    no schema, the default-target checks name the gap and keep going,
    and WHERE the live DB is present they run unchanged.
    """
    if not (HERE / "pool" / "jackpot.db").exists():
        return False
    try:
        raw = sqlite3.connect(
            f"file:{(HERE / 'pool' / 'jackpot.db').as_posix()}?mode=ro",
            uri=True)
        try:
            _t = {r[0] for r in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            raw.close()
    except sqlite3.Error:
        return False
    return {"users", "sessions", "audit_log"}.issubset(_t)


_LIVE_OK = _live_ready()


def _mk_db(name, *, with_orphans=False, with_extra_edge=False):
    path = SANDBOX / name
    raw = sqlite3.connect(str(path))
    raw.executescript(SCHEMA)
    raw.execute("INSERT INTO users (id, username) VALUES (1, 'alive')")
    raw.execute("INSERT INTO miners (id, wallet) VALUES (10, 'w-alive')")
    raw.execute("INSERT INTO drawings (id, seed) VALUES (20, 's')")
    raw.execute("INSERT INTO tickets (id, miner_id, drawing_id) "
                "VALUES (30, 10, 20)")                       # healthy
    raw.execute("INSERT INTO paid_spins (id, miner_id) VALUES (40, 10)")
    raw.execute("INSERT INTO sessions (id, user_id) VALUES (50, 1)")
    raw.execute("INSERT INTO audit_log (id, user_id, action, details) "
                "VALUES (60, 1, 'note', '{}')")
    if with_orphans:
        raw.execute("INSERT INTO tickets (id, miner_id, drawing_id) "
                    "VALUES (31, 11, 20)")                   # dead miner
        raw.execute("INSERT INTO sessions (id, user_id) VALUES (51, 2)")
        raw.execute("INSERT INTO audit_log (id, user_id, action, details) "
                    "VALUES (61, 2, 'note', '{}')")
    if with_extra_edge:
        # an FK edge the CLI's statement set does NOT know about
        raw.execute("CREATE TABLE mystery (id INTEGER PRIMARY KEY, "
                    "user_id INTEGER REFERENCES users(id))")
        raw.execute("INSERT INTO mystery (id, user_id) VALUES (70, 2)")
    raw.commit()
    raw.close()
    return path


def _logical(path):
    """A fingerprint immune to journal/checkpoint noise: FK violations
    plus the total row counts of every table."""
    raw = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        fk = len(raw.execute("PRAGMA foreign_key_check").fetchall())
        tables = [r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        counts = {t: raw.execute(
            f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(tables)}
        return fk, counts
    finally:
        raw.close()


print("== 1. DRY-RUN DEFAULT: the receipt without the write ==")
clean = _mk_db("clean.db")
before = _logical(clean)
if _LIVE_OK:
    r = _run([])
    check(1.1, r.returncode == 0 and all(l in r.stdout for l in LABELS)
          and "would COMMIT" in r.stdout and "was not written" in r.stdout,
          f"the bare CLI prints every labeled statement and the commit "
          f"verdict (rc {r.returncode})")
else:
    print("SKIP: 1.1 the live pool/jackpot.db is absent in this checkout "
          "— the bare CLI's default target is the operator DB "
          "(tools/sweep_db.py DEFAULT_DB), so there is nothing to "
          "rehearse against; sections 2-4 grade the same drive on "
          "sandboxes and 4.3 pins the missing-file refusal")
check(1.2, _logical(clean) == before,
      "the target DB's logical state is identical after the dry-run")
ro = _mk_db("readonly.db")
ro.chmod(0o444)          # the FILESYSTEM forbids writes - Windows attr too
try:
    r = _run(["--db", str(ro)])
    check(1.3, r.returncode == 0 and "would COMMIT" in r.stdout,
          "the dry-run SUCCEEDS on a read-only database file - the "
          "rehearsal needs zero write access to the original")
finally:
    ro.chmod(0o644)      # so the sandbox rmtree can remove it

print("== 2. THE RECEIPT IS HONEST (orphans counted, unknown edges caught) ==")
dirty = _mk_db("dirty.db", with_orphans=True)
r = _run([f"--db", str(dirty)])
check(2.1, r.returncode == 0
      and "- tickets_of_dead_miners: 1 row(s)" in r.stdout
      and "- sessions_of_dead_users: 1 row(s)" in r.stdout
      and "- null_dead_audit_user: 1 row(s)" in r.stdout
      and "- tickets_of_dead_drawings: 0 row(s)" in r.stdout
      and "- paid_spins_of_dead_miners: 0 row(s)" in r.stdout
      and "would COMMIT" in r.stdout,
      "the orphaned sandbox rehearses to the exact per-label counts")
fk_dirty, _ = _logical(dirty)
check(2.2, fk_dirty >= 3,
      "and the original file is STILL orphaned (the dry-run wrote nothing)")
edge = _mk_db("edge.db", with_orphans=True, with_extra_edge=True)
r = _run([f"--db", str(edge)])
check(2.3, r.returncode == 0 and "would ROLLBACK" in r.stdout
      and "extend the statement set" in r.stdout,
      "an UNCOVERED FK edge flips the verdict to would-ROLLBACK with "
      "the extend hint - caught here, not on the live file")

print("== 3. --APPLY GOES THROUGH THE DISCIPLINE ==")
r = _run(["--apply", "--db", str(dirty)])
after = sqlite3.connect(str(dirty))
_manifest = after.execute("SELECT COUNT(*) FROM audit_log "
                          "WHERE action='sweep.cli.orphans'").fetchone()[0]
_fk_after = len(after.execute("PRAGMA foreign_key_check").fetchall())
after.close()
check(3.1, r.returncode == 0 and "SWEEP COMMITTED" in r.stdout
      and _manifest == 1 and _fk_after == 0,
      "the sweep committed: manifest row recorded, fk_check empty "
      f"(rc {r.returncode})")
_backups = list(SANDBOX.glob("_backup_dirty_pre_sweep_*.db"))
check(3.2, len(_backups) == 1 and _backups[0].exists(),
      "the backup landed beside the DB (never overwritten later)")
r = _run(["--apply", "--db", str(edge)])
conn = sqlite3.connect(str(edge))
_alive = conn.execute("SELECT COUNT(*) FROM mystery").fetchone()[0]
conn.close()
check(3.3, r.returncode == 1 and "the discipline refused" in r.stdout
      and _alive == 1,
      "an uncovered edge makes --apply REFUSE (exit 1) and the data "
      "is untouched")

print("== 4. ARGUMENT HYGIENE ==")
r = _run(["--bogus"])
check(4.1, r.returncode == 2 and "usage" in r.stdout,
      "an unknown argument exits 2 with the usage line")
r = _run(["--db"])
check(4.2, r.returncode == 2 and "--db needs a path" in r.stdout,
      "a pathless --db exits 2")
r = _run(["--db", str(SANDBOX / "missing.db")])
check(4.3, r.returncode == 1 and "no such database" in r.stdout,
      "a missing database exits 1")

print("== 5. THE LIVE CANARY (read-only) ==")
if not _LIVE_OK:
    print("SKIP: 5.x the live pool/jackpot.db is absent in this checkout "
          "— the read-only canary has no operator database to open, so "
          "the default-target rehearsal (5.1) and the state-snapshot "
          "comparison (5.2) cannot grade anything here")
if _LIVE_OK:
    live = sqlite3.connect(
        f"file:{(HERE / 'pool' / 'jackpot.db').as_posix()}?mode=ro", uri=True)
    _live_before = (len(live.execute("PRAGMA foreign_key_check").fetchall()),
                    live.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                    live.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
    live.close()
    r = _run([])     # the DEFAULT: pool/jackpot.db, dry-run
    check(5.1, r.returncode == 0 and "jackpot.db" in r.stdout
          and "would COMMIT" in r.stdout and "was not written" in r.stdout,
          "the bare CLI against the LIVE db exits 0 with the dry-run receipt")
    live = sqlite3.connect(
        f"file:{(HERE / 'pool' / 'jackpot.db').as_posix()}?mode=ro", uri=True)
    _live_after = (len(live.execute("PRAGMA foreign_key_check").fetchall()),
                   live.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                   live.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
    live.close()
    check(5.2, _live_before == _live_after,
          "the live DB's logical state is identical after the dry-run")

print("== 6. THE ORPHAN REPORT (--report: plan before you sweep) ==")
clean2 = _mk_db("report_clean.db")
r = _run(["--report", "--db", str(clean2)])
check(6.1, r.returncode == 0 and "0 violating row(s)" in r.stdout,
      f"a clean sandbox reports the all-clear (rc {r.returncode})")
report_dirty = _mk_db("report_dirty.db", with_orphans=True)
r = _run(["--report", "--db", str(report_dirty)])
check(6.2, r.returncode == 1
      and "tickets.miner_id -> miners: 1 row(s), plan: delete" in r.stdout
      and "sessions.user_id -> users: 1 row(s), plan: delete" in r.stdout
      and "audit_log.user_id -> users: 1 row(s), "
          "plan: null (history kept)" in r.stdout
      and "sample rowids: [" in r.stdout
      and "every violated edge is planned" in r.stdout,
      "the orphaned sandbox names every EDGE with its count and the "
      "disposition, then exits 1 - cron/alerting reacts to the rc "
      "(violations found, all planned)")
r = _run(["--report", "--db", str(edge)])
check(6.3, r.returncode == 2 and "UNPLANNED" in r.stdout
      and "extend the statement set" in r.stdout,
      "an edge outside EDGE_PLAN exits 2 - the plan needs a human, "
      "which outranks the violations themselves")
def _default_live_report():
    """The live-default --report receipt (6.4): only meaningful where the
    live DB exists; see _live_ready's note above."""
    return _run(["--report"])


if _LIVE_OK:
    r = _default_live_report()
    check(6.4, r.returncode == 0 and "jackpot.db" in r.stdout
          and "ORPHAN REPORT" in r.stdout and "0 violating row(s)" in r.stdout,
          "the live default --report exits 0 with the all-clear receipt")
else:
    print("SKIP: 6.4 the live pool/jackpot.db is absent in this checkout "
          "— the default --report has no live file to report on; 6.1-6.3 "
          "pinned the same receipt, edges and UNPLANNED gate on sandboxes")

import shutil
shutil.rmtree(SANDBOX, ignore_errors=True)
passed, failed = results.count(True), results.count(False)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
