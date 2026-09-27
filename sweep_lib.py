"""sweep_lib.py - THE SHARED SWEEP DISCIPLINE (Sep 27).

The live jackpot.db once carried 287 FK orphans, and healing them by
hand is exactly the kind of operation that recreates them: a raw
ad-hoc connection with PRAGMA foreign_keys left at SQLite's default 0
is the orphan factory itself (test_fk_regression section 2 proves it).
The sweep that healed the live DB therefore ran as a DISCIPLINE, and
every future cleanup must run the same one. This module owns that
discipline so the FK drive and future cleanups share ONE implementation
instead of re-typing the ritual:

  1. THE BACKUP      the sqlite3 backup API snapshots the pre-state to
                     _backup_<stem>_pre_sweep_<ts>.db BESIDE the DB
                     (the live convention) before anything is touched -
                     the oracle every sweep is judged against. A sweep
                     that cannot first prove its pre-state is preserved
                     has no undo.
  2. THE TRANSACTION one BEGIN IMMEDIATE on a raw connection: ordered,
                     dependency-aware statements (children first or the
                     references nulled first - the CALLER owns the
                     order and labels each statement), and ROLLBACK on
                     any failure so a half-sweep can never persist.
  3. THE FK GATE     PRAGMA foreign_key_check runs INSIDE the same
                     transaction, before COMMIT - non-empty raises
                     SweepError and everything rolls back. A gate that
                     runs after the commit is a post-mortem, not a gate.
                     The check is re-run after the commit, on the same
                     connection, and shipped in the result.
  4. THE MANIFEST    one audit_log row (user_id NULL, the house-action
                     convention) records every statement's label and
                     rowcount - a sweep that leaves no trace of what it
                     did is indistinguishable from one that ran wild.

The CALLER supplies only the statements (what to sweep) and the audit
action name (how to remember it); everything above is fixed here.
Stdlib-only, no server imports: a cleanup script runs this on a live
file without booting the app.

Returns {"committed", "backup", "rowcounts", "fk_after_commit"}.
"""
import json
import sqlite3
import time
from pathlib import Path


class SweepError(RuntimeError):
    """The discipline refused (backup target exists, or the FK gate
    fired mid-transaction). The DB is untouched when this raises."""


def _fk_violations(conn):
    return len(conn.execute("PRAGMA foreign_key_check").fetchall())


def sweep(db_path, statements, *, audit_action="sweep.orphans",
          audit_details=None, backup_dir=None, backup_name=None):
    """Run the four-piece sweep discipline against db_path.

    statements: ordered [(label, sql, params)] - the label lands in the
    manifest row, so read it like a receipt, not a comment.
    audit_details: optional dict merged into the manifest payload.
    backup_dir / backup_name: override the default beside-the-DB,
    timestamped backup location (tests point these at their sandbox).
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir) if backup_dir else db_path.parent
    name = backup_name or (
        f"_backup_{db_path.stem}_pre_sweep_{int(time.time())}.db")
    backup_path = backup_dir / name
    if backup_path.exists():
        raise SweepError(
            f"backup target already exists, refusing to overwrite: "
            f"{backup_path}")

    # 1. THE BACKUP: the sqlite3 backup API (not a file copy - it takes
    # a consistent snapshot through the SQLite layer).
    src = sqlite3.connect(str(db_path))
    try:
        bak = sqlite3.connect(str(backup_path))
        try:
            src.backup(bak)
        finally:
            bak.close()
    finally:
        src.close()

    # 2-4. THE TRANSACTION, THE GATE, THE MANIFEST.
    rowcounts = {}
    raw = sqlite3.connect(str(db_path))
    committed = False
    fk_after = None
    try:
        raw.execute("BEGIN IMMEDIATE")
        for label, sql, params in statements:
            rowcounts[label] = raw.execute(sql, params).rowcount
        fk = _fk_violations(raw)
        if fk != 0:
            raise SweepError(
                f"fk_check non-empty mid-transaction ({fk} violation(s)) "
                f"- rolled back, nothing persisted")
        raw.execute(
            "INSERT INTO audit_log (user_id, action, details) "
            "VALUES (?, ?, ?)",
            (None, audit_action, json.dumps(
                {"statements": rowcounts, **(audit_details or {})})))
        raw.execute("COMMIT")
        committed = True
        fk_after = _fk_violations(raw)
    except BaseException:
        try:
            raw.execute("ROLLBACK")
        except sqlite3.Error:
            pass  # no transaction was open - re-raise the ORIGINAL error
        raise
    finally:
        raw.close()
    return {"committed": committed, "backup": backup_path,
            "rowcounts": rowcounts, "fk_after_commit": fk_after}
