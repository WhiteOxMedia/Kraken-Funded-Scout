#!/usr/bin/env python3
"""tools/fleet_devstate.py — THE FRESH-WORKTREE STATE SEEDER (Oct 31).

The nine baseline drives assume local dev state a fresh clone does not
have (FLEET_DEV_STATE.md is the written inventory). This script is that
checklist made executable: it reports every item and, with --apply,
seeds each one that CAN be produced hermetically.

House discipline (sweep_db.py's shape): DRY-RUN DEFAULT, --apply to
write, every refusal NAMED, RESULT line + exit code. Nothing an
operator provisioned is ever overwritten: an existing field key is
never rotated from here, and seeding the DB never deletes a row.

WHAT IT MANAGES (each maps to the nine in FLEET_DEV_STATE.md):

  1. pool/jackpot.db        the operator DB. Seeded ONLY when the file is
                            absent or a zero-table stray (a fresh clone's
                            sqlite-connect side effect): the app schema
                            via database.init_db(), then the two standing
                            fleet fixtures test_fk_regression pins (4.3)
                            inserted ADDITIVELY (never a delete/update).
  2. pool/.keys/field_key   the at-rest field key. Generated ONLY when
                            absent - the keyed blind-index drives
                            (referral entries/programs) grade through it.
  3. node_modules           npm ci --no-audit --no-fund when missing -
                            the xmrj_offline_gates harness (1.2, 4.1)
                            resolves ts-node from here. Needs the network;
                            a failed install is reported, never swallowed.
  4. target/ artifacts      CANNOT be seeded: target/deploy/*.so and
                            target/idl/*.json come only from `anchor build`
                            (the Solana toolchain, not repo dev state).
                            Reported, with the fix named.

Run:
    python tools/fleet_devstate.py              # dry-run receipt (exit 0)
    python tools/fleet_devstate.py --apply      # seed what can be seeded
    python tools/fleet_devstate.py --root <worktree> --apply  # another tree
    python tools/fleet_devstate.py --check      # exit 1 when unsatisfied

Prints "RESULT: N passed, M failed" and exits 0/1 (2 on a bad root).
"""
import argparse
import hashlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # the repo root (.../monero_project)

# The two standing fleet fixtures (test_fk_regression 4.3 pins the two
# NAMES; security_probe's password here matches test_security's probe
# account so that drive's live login keeps working even on a machine
# seeded before its server ever ran).
FIXTURES = (
    ("security_probe", "correct horse battery"),
    ("prefs_live_probe_fleet", "correct horse battery"),
)

passed = failed = 0


def check(name, ok, why=""):
    global passed, failed
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({why})" if why else ""))
    if ok:
        passed += 1
    else:
        failed += 1


def _hash(password):
    """server.hash_password's exact discipline (hashed inline here so the
    whole pool/server app is not imported just to seed one row)."""
    return hashlib.sha256(("xmrjackpot" + password).encode()).hexdigest()


def _table_names(db_path: Path):
    raw = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return [r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_'")]
    finally:
        raw.close()


def _usernames(db_path: Path):
    try:
        raw = sqlite3.connect(str(db_path))
    except sqlite3.DatabaseError:
        return None
    try:
        return {r[0] for r in raw.execute("SELECT username FROM users")}
    except sqlite3.DatabaseError:
        return None
    finally:
        raw.close()


# -- 1. the operator DB (schema + the two fleet fixtures) ----------------
def item_jackpot(root: Path, apply: bool) -> None:
    label = "pool/jackpot.db: app schema + both fleet fixtures"
    db_path = root / "pool" / "jackpot.db"

    if db_path.exists():
        try:
            tables = _table_names(db_path)
        except sqlite3.DatabaseError:
            check(label, False, "file present but UNREADABLE as sqlite - "
                                "named, not touched")
            return
        if tables:
            print("  jackpot.db present with tables - nothing to seed")
        else:
            if not apply:
                print("  jackpot.db is a ZERO-TABLE stray (sqlite-connect "
                      "side effect) - would seed the schema (--apply)")
                check(label, False, "would init_db() the stray, then insert "
                                    "the fixtures (--apply)")
                return
            else:
                sys.path.insert(0, str(root / "pool"))
                import database as dbm
                dbm.DB_PATH = db_path
                dbm.init_db()
                print("  seeded: init_db ran against pool/jackpot.db")
    else:
        if not apply:
            print("  jackpot.db MISSING - would seed schema + fittings "
                  "(-apply)")
            missing_names = ", ".join(u for u, _ in FIXTURES)
            check(label, False, "would seed schema + fixtures ("
                                + missing_names + ") (--apply)")
            return
        sys.path.insert(0, str(root / "pool"))
        import database as dbm
        dbm.DB_PATH = db_path
        dbm.init_db()
        print("  seeded: init_db created pool/jackpot.db with the schema")

    # the two standing fixtures, additive-only, from here on
    users = _usernames(db_path) if db_path.exists() else None
    if users is None:
        check(label, False, "users table unreadable - cannot tell")
        return
    missing = [u for u, _ in FIXTURES if u not in users]
    if not missing:
        check(label, True, "")
        return
    if not apply:
        check(label, False, "would insert the fleet fixtures additively: "
                            + ", ".join(missing) + " (--apply)")
        return
    sys.path.insert(0, str(root / "pool"))
    import database as dbm
    dbm.DB_PATH = db_path
    for user, pw in FIXTURES:
        if user in missing:
            dbm.create_user(user, _hash(pw))
    print(f"  seeded fixtures additively: {', '.join(missing)}")
    check(label, True, "")


# -- 2. the at-rest field key ---------------------------------------------
def item_field_key(root: Path, apply: bool) -> None:
    label = "pool/.keys/field_key (the at-rest field key) is in place"
    key_file = root / "pool" / ".keys" / "field_key"
    if key_file.is_file():
        check(label, True, "already provisioned - left untouched")
        return
    if not apply:
        check(label, False, "would generate a throwaway key (--apply)")
        return
    sys.path.insert(0, str(root / "pool"))
    import field_crypto as fc
    fc.generate_key_file()
    check(label, True, "generated a fresh key (disposable; every seeded key "
                       "here is throwaway-by-design)")


# -- 3. node_modules (the ts-node harness) --------------------------------
def item_node_modules(root: Path, apply: bool) -> None:
    label = "node_modules resolves the xmrj_offline harness deps"
    has_pkg = (root / "package.json").is_file() and \
              (root / "package-lock.json").is_file()
    if (root / "node_modules").is_dir():
        check(label, True, "already present")
        return
    if not has_pkg:
        check(label, False, "no package.json + package-lock.json at the root "
                            "(the drive needs ts-node resolvable)")
        return
    if not apply:
        check(label, False, "would run: npm ci --no-audit --no-fund (--apply)")
        return
    npm = shutil.which("npm")
    if not npm:
        check(label, False, "npm not on PATH - install Node >=18 first")
        return
    print("  running: npm ci --no-audit --no-fund  (cwd=" + root.name + ")")
    proc = subprocess.run([npm, "ci", "--no-audit", "--no-fund"],
                          cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        check(label, False, "npm ci rc=" + str(proc.returncode)
              + " - last lines: " + " | ".join(tail))
        return
    check(label, True, "npm ci completed")


# -- 4. the anchor artifacts (NOT seeded - the fix is a toolchain) --------
def item_artifacts(root: Path, apply: bool) -> None:
    label = "target/deploy/*.so + target/idl/*.json exist (anchor build)"
    del apply                              # nothing here is seedable
    deploy = root / "target" / "deploy"
    idl = root / "target" / "idl"
    so = sorted(deploy.glob("*.so")) if deploy.is_dir() else []
    ij = sorted(idl.glob("*.json")) if idl.is_dir() else []
    if so and ij:
        check(label, True, f"{len(so)} .so / {len(ij)} idl")
        return
    check(label, False, "CANNOT be seeded - run `anchor build` on a machine "
                        "with the Solana toolchain (see "
                        "XMRJ_SOLANA_DEPLOYMENT_PLAN.md); until then the "
                        "artifact drives refuse as built")


# -- main ------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None,
                    help="worktree to inspect/seed (default: this repo)")
    ap.add_argument("--apply", action="store_true",
                    help="seed what can be seeded (default: dry-run receipt)")
    ap.add_argument("--check", action="store_true",
                    help="audit-only: exit 1 when any item is unsatisfied")
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else ROOT
    if not (root / "pool" / "database.py").is_file():
        print(f"no pool/database.py under {root} - not a worktree")
        return 2
    print(f"worktree: {root}")
    print("mode: " + ("APPLY" if args.apply
                      else ("CHECK (audit only)" if args.check
                            else "dry run")))

    print("\n== 1. pool/jackpot.db (schema + fleet fixtures) ==")
    item_jackpot(root, args.apply)
    print("\n== 2. pool/.keys/field_key (at-rest key) ==")
    item_field_key(root, args.apply)
    print("\n== 3. node_modules (ts-node harness) ==")
    item_node_modules(root, args.apply)
    print("\n== 4. target/ artifacts (anchor build) ==")
    item_artifacts(root, args.apply)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    if args.apply or args.check:
        return 1 if failed else 0
    return 0                                # dry-run receipt: informational


if __name__ == "__main__":
    sys.exit(main())
