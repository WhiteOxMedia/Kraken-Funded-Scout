"""install_user_delete_guard.py - THE RAW-DELETE GUARD INSTALLER.

The FK tripwire (test_fk_regression) proved the orphan factory: a raw
sqlite3.connect deletes a parent with foreign_keys=0 and silently
orphans the children - and on Oct 30 that factory DID fire out of
band: the prefs_live_probe_fleet fixture (user 42) was deleted raw
between 12:55:08 and 12:56:10 and re-registered as a new id,
stranding 4,493 audit rows + 90 sessions. Production code has no
user-delete path (the tripwire drive greps it), but an out-of-band
connection can never be reached by code review - the guard has to
live IN THE DATABASE.

A TRIGGER is that database-level guard: SQLite fires it for EVERY
connection, including raw ones holding PRAGMA foreign_keys=0 where
the schema-level FK enforcement is switched off. The policy it
enforces:

  a user with child rows is never deleted. Null or delete the
  children first (the sweep discipline), then the delete goes
  through. A truly childless account stays deletable - the guard
  wedges nothing the discipline already allows.

The guarded edges are read from the SCHEMA ITSELF at install time
(PRAGMA foreign_key_list over every table), so a future table's edge
into users is covered by the next idempotent run. The trigger's SQL
is the one canonical definition here, and the tripwire drive pins
the live trigger against it VERBATIM: the installed guard is the
reviewed guard.

Usage:
  python install_user_delete_guard.py            apply (idempotent)
  python install_user_delete_guard.py --check    verify-only, exit
                                                 0 wired / 1 absent /
                                                 2 drifted
  python install_user_delete_guard.py --db PATH  another file

Stdlib-only. Nothing is written without a bare invocation (apply);
apply refuses unknown args with usage.
"""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "tools" else HERE
DEFAULT_DB = ROOT / "pool" / "jackpot.db"
TRIGGER_NAME = "users_no_orphan_delete"
PARENT = "users"
RAISE_MESSAGE = (
    "REFUSED by users_no_orphan_delete: this user still owns child "
    "rows; null or delete the children via the sweep discipline "
    "before deleting (the out-of-band raw-DELETE factory stays closed)"
)


def _child_edges(conn):
    """(child_table, child_col) for every FK edge into users."""
    edges = []
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchall()]
    for table in tables:
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
            if fk[2] == PARENT:
                edges.append((table, fk[3] or fk[4]))
    return sorted(set(edges))


def guard_sql(edges):
    """The canonical CREATE TRIGGER for a concrete edge list."""
    lines = [
        f"CREATE TRIGGER {TRIGGER_NAME}",
        f"BEFORE DELETE ON {PARENT}",
        "FOR EACH ROW",
        "WHEN EXISTS (",
    ]
    for i, (t, c) in enumerate(edges):
        prefix = "      UNION ALL" if i else "      "
        lines.append(f"{prefix} SELECT 1 FROM {t} WHERE {c} = OLD.id")
    lines.extend([
        ")",
        "BEGIN",
        f"  SELECT RAISE(ABORT, '{RAISE_MESSAGE}');",
        "END",
    ])
    return "\n".join(lines)


def _norm(sql):
    return " ".join((sql or "").split()).lower()


def current(conn):
    """The live trigger's SQL, or None when absent."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
        (TRIGGER_NAME,)).fetchone()
    return row[0] if row else None


def install(conn):
    """Idempotent: create when absent; silent no-op when the live SQL
    matches the canonical form; `drift` (NO mutation) when it does not
    - a drifted guard must be corrected by a human, not overwritten."""
    prior = current(conn)
    if prior is not None:
        if _norm(prior) != _norm(guard_sql(_child_edges(conn))):
            return "drift"
        return "already-wired"
    conn.execute(guard_sql(_child_edges(conn)))
    conn.commit()
    return "wired"


def verdict(conn):
    """The --check verdict: 0 wired, 1 absent, 2 drifted. Prints its
    own receipt; checks never mutate."""
    prior = current(conn)
    if prior is None:
        print(f"GUARD ABSENT  no trigger {TRIGGER_NAME} on {PARENT}")
        return 1
    if _norm(prior) != _norm(guard_sql(_child_edges(conn))):
        print("GUARD DRIFTED  the live trigger's SQL does not match the "
              f"canonical definition in {Path(__file__).name} - write "
              "the corrected definition by hand, then --check")
        return 2
    print(f"GUARD WIRED  {TRIGGER_NAME} active - {PARENT} refuses "
          "orphaning deletes on every connection, foreign_keys pragma "
          "included; childless deletes stay open")
    return 0


def main(argv):
    db_path = DEFAULT_DB
    check_only = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--db" and i + 1 < len(argv):
            db_path = Path(argv[i + 1])
            i += 2
            continue
        if a == "--check":
            check_only = True
        else:
            print("FAIL  unknown argument: " + a +
                  " (usage: install_user_delete_guard.py [--check] "
                  "[--db PATH])")
            return 2
        i += 1
    if not db_path.exists():
        print(f"FAIL  no such database: {db_path}")
        return 1
    conn = sqlite3.connect(str(db_path))
    try:
        if check_only:
            return verdict(conn)
        what = install(conn)
        if what == "drift":
            print("REFUSED  the live trigger drifted from the canonical "
                  "definition; an overwrite would hide a bad guard - "
                  "fix it by hand and run --check")
            return 2
        print(("WIRED  " + TRIGGER_NAME + " installed over " + str(db_path))
              if what == "wired" else
              ("ALREADY-WIRED  " + TRIGGER_NAME + " present and matching"))
        print("  edges guarded: " + ", ".join(
            f"{t}.{c}" for t, c in _child_edges(conn)))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
