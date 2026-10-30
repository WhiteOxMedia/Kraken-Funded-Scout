"""sweep_db.py - THE ORPHAN-SWEEP MAINTENANCE CLI (Sep 27).

The live jackpot.db once carried 287 FK orphans; the discipline that
healed them now lives in sweep_lib (backup -> BEGIN IMMEDIATE ->
in-transaction foreign_key_check gate -> manifest audit row). This CLI
is that discipline's operator face for pool/jackpot.db:

  DRY-RUN (the default, and the only mode a bare invocation can reach)
      rehearses the labeled statements on a THROWAWAY copy made through
      the sqlite3 backup API: the real statements really run and their
      real rowcounts print, but the original file is never opened for
      writing and never takes a write lock. The output is the exact
      receipt --apply would produce, plus the gate verdict the sweep
      would reach (would COMMIT / would ROLLBACK, with the violation
      count) - a statement set that misses an FK edge shows up HERE,
      not on the live file.

  --apply   performs the sweep through sweep_lib.sweep: the backup
            lands beside the DB (_backup_<stem>_pre_sweep_<ts>.db,
            never overwritten), the gate runs inside the transaction
            (non-empty -> SweepError -> full rollback), and the
            manifest audit row records every label's rowcount. A
            refusal exits 1 naming the reason; a clean sweep exits 0.

  --report  prints the orphan DETECTOR's receipt instead of the
            dry-run: every FK edge read from the schema itself,
            per-edge violation counts, sample rowids, and a
            disposition per edge (delete / null / UNPLANNED). The
            EXIT CODE is the alert: 0 all-clear, 1 violations
            found, 2 an edge is UNPLANNED - cron/alerting reacts
            to the receipt itself, and the receipt always prints
            first. Read-only by construction.

  --db PATH targets another file (the default resolves from THIS
            script's location, so the CLI behaves identically from any
            cwd). Bare `python sweep_db.py` on the live DB is always
            safe: it reads, copies to a temp dir, reports, and exits.

The statement set covers every FK edge in the schema: children of dead
miners and dead drawings are deleted, dead-user sessions are deleted,
and dead-user audit rows are reference-nulled (history is kept, the
dangling user_id is dropped). A future edge needs a new labeled pair
here - the labels ARE the manifest, so name them like a receipt.

Stdlib-only; runs with plain python from anywhere: the DB default
resolves from the checkout root this tools/ directory sits in (the
Oct 29 root-organization layout), so receipts still land against
pool/jackpot.db.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "tools" else HERE
sys.path.insert(0, str(HERE))
import sweep_lib  # noqa: E402

DEFAULT_DB = ROOT / "pool" / "jackpot.db"
AUDIT_ACTION = "sweep.cli.orphans"

# (label, sql) - labels land in the manifest; every FK edge gets a pair.
STATEMENTS = (
    ("tickets_of_dead_miners",
     "DELETE FROM tickets WHERE miner_id IS NOT NULL AND miner_id "
     "NOT IN (SELECT id FROM miners)"),
    ("tickets_of_dead_drawings",
     "DELETE FROM tickets WHERE drawing_id IS NOT NULL AND drawing_id "
     "NOT IN (SELECT id FROM drawings)"),
    ("paid_spins_of_dead_miners",
     "DELETE FROM paid_spins WHERE miner_id IS NOT NULL AND miner_id "
     "NOT IN (SELECT id FROM miners)"),
    ("sessions_of_dead_users",
     "DELETE FROM sessions WHERE user_id IS NOT NULL AND user_id "
     "NOT IN (SELECT id FROM users)"),
    ("null_dead_audit_user",
     "UPDATE audit_log SET user_id = NULL WHERE user_id IS NOT NULL "
     "AND user_id NOT IN (SELECT id FROM users)"),
)

# The sweep PLAN as dispositions: what --apply would DO to each
# FK edge (the labels above are the receipt, this is the intent).
# An edge missing here is UNPLANNED - --report says so loudly and
# the dry-run gate would refuse before anything touched the DB.
EDGE_PLAN = {
    ("tickets", "miner_id", "miners"): "delete",
    ("tickets", "drawing_id", "drawings"): "delete",
    ("paid_spins", "miner_id", "miners"): "delete",
    ("sessions", "user_id", "users"): "delete",
    ("audit_log", "user_id", "users"): "null (history kept)",
}


def _fk_violations(conn):
    return len(conn.execute("PRAGMA foreign_key_check").fetchall())


def dry_run(db_path):
    """Rehearse on a throwaway backup-API copy; the original file is
    never opened for writing. Prints the receipt and returns the rc."""
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"FAIL  no such database: {db_path}")
        return 1
    workdir = Path(tempfile.mkdtemp(prefix="sweep_dryrun_"))
    copy_path = workdir / db_path.name
    try:
        src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            bak = sqlite3.connect(str(copy_path))
            try:
                src.backup(bak)
            finally:
                bak.close()
        finally:
            src.close()
        raw = sqlite3.connect(str(copy_path))
        try:
            raw.execute("BEGIN IMMEDIATE")
            print(f"DRY RUN over {db_path} (rehearsed on a throwaway "
                  f"copy - the original file was not written)")
            print(f"  labeled statements ({len(STATEMENTS)}):")
            counts = {}
            for label, sql in STATEMENTS:
                counts[label] = raw.execute(sql).rowcount
                print(f"    - {label}: {counts[label]} row(s)")
            fk = _fk_violations(raw)
            raw.execute("ROLLBACK")
        finally:
            raw.close()
        if fk:
            print(f"  GATE: would ROLLBACK - {fk} foreign_key_check "
                  f"violation(s) would remain; extend the statement set "
                  f"before --apply")
        else:
            print(f"  GATE: would COMMIT - foreign_key_check would be "
                  f"empty after the sweep")
        print("  to perform the sweep (backup, gate, manifest): "
              "python tools/sweep_db.py --apply"
              + (f" --db {db_path}" if db_path != DEFAULT_DB else ""))
        return 0
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


def apply_sweep(db_path):
    """The real sweep through sweep_lib: backup, gate, manifest."""
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"FAIL  no such database: {db_path}")
        return 1
    statements = [(label, sql, ()) for label, sql in STATEMENTS]
    try:
        res = sweep_lib.sweep(
            db_path, statements, audit_action=AUDIT_ACTION,
            audit_details={"runner": "sweep_db.py --apply"})
    except sweep_lib.SweepError as e:
        print(f"FAIL  the discipline refused: {e}")
        return 1
    print(f"SWEEP COMMITTED over {db_path}")
    for label, n in res["rowcounts"].items():
        print(f"    - {label}: {n} row(s)")
    print(f"  backup: {res['backup']}")
    print(f"  manifest: audit_log action '{AUDIT_ACTION}' "
          f"(rowcounts recorded)")
    print(f"  post-commit foreign_key_check: "
          f"{res['fk_after_commit']} violation(s)")
    return 0


def report(db_path):
    """The detector receipt: per-edge counts, samples, plan.

    The exit code is the alert for cron/alerting (the receipt
    ALWAYS prints first): 0 = all-clear, 1 = violations found,
    2 = an edge is UNPLANNED (the plan itself needs a human -
    that outranks sweeping, so it takes precedence over rc 1).
    """
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"FAIL  no such database: {db_path}")
        return 1
    edges = sweep_lib.orphans(db_path)
    total = sum(e["violations"] for e in edges)
    print(f"ORPHAN REPORT for {db_path} (read-only): "
          f"{total} violating row(s) across {len(edges)} edge(s)")
    if not edges:
        return 0
    unplanned = 0
    for e in edges:
        key = (e["child_table"], e["child_col"], e["parent_table"])
        plan = EDGE_PLAN.get(key)
        if plan is None:
            unplanned += 1
            plan = "UNPLANNED - add a labeled statement pair"
        print(f"  {e['child_table']}.{e['child_col']} -> "
              f"{e['parent_table']}: {e['violations']} row(s), "
              f"plan: {plan}; sample rowids: "
              f"{e['sample_rowids']}")
    if unplanned:
        print(f"  {unplanned} edge(s) UNPLANNED - extend the "
              f"statement set and EDGE_PLAN before --apply")
        return 2        # the plan needs a human first
    print("  every violated edge is planned - a dry run will "
          f"confirm, then --apply sweeps")
    return 1            # violations, all planned: sweepable


def main(argv):
    apply = False
    report_mode = False
    db_path = DEFAULT_DB
    for a in argv:
        if a == "--apply":
            apply = True
        elif a == "--report":
            report_mode = True
        elif a == "--db":
            db_path = None      # the NEXT arg is the path
        elif db_path is None:
            db_path = Path(a)
        else:
            print(f"FAIL  unknown argument: {a} "
                  f"(usage: sweep_db.py [--report] [--apply] [--db PATH])")
            return 2
    if db_path is None:
        print("FAIL  --db needs a path")
        return 2
    if report_mode:
        return report(db_path)
    return apply_sweep(db_path) if apply else dry_run(db_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
