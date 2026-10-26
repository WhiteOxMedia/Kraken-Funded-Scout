"""scout_lifecycle.py — where every Scout is in its life (Phase F).

Scout is the product; the miner is a runtime dependency. One row per Scout
records whether it was installed, activated, ever authenticated and ever
produced work, so the fleet is counted instead of inferred.

THE FOUR QUESTIONS (``report()["answers"]``)
    scouts_installed          an install beacon or an activation
    scouts_activated          activation data reached the miner
    scouts_ever_connected     a stratum authentication was observed
    scouts_submitted_shares   a share of any kind was observed

ONE ROW PER SCOUT
    The installer mints an ``install_id`` per machine and sends it with the
    install beacon and with the registration. Rows the pool learns from its
    own side (proxy telemetry for a wallet the installer never reported) are
    keyed ``w:<wallet>``. A write looks the row up by install id first, then
    by wallet, so the same machine cannot become two rows once both
    identities are known.

FIRST-OCCURRENCE STAMPS (never rewritten)
    first_seen            the row was created
    installed_at          the install beacon landed (files staged)
    first_connect         the first stratum authentication
    first_share           the first share of any kind
    first_valid_share     the first ACCEPTED share
    activation_complete   activation data was applied (installer registered)
    last_seen             the most recent telemetry (only moves forward)

STATES (derived on read — one definition, no stored drift)
    NEW         the row exists but no Scout fact has been recorded yet
                (the ladder's base; every normal write carries a fact)
    INSTALLED   the installer beaconed, registration never landed
    ACTIVATED   the installer registered (wallet bound into the config)
    CONNECTED   authenticated at the stratum, no accepted share yet
    MINING      an accepted share inside the dormancy window
    DORMANT     had telemetry, then went silent past the window

    DORMANT is "used to work, stopped". A Scout that never authenticated is
    NEVER dormant — it is reported under ``never_connected``, because
    "never worked" and "stopped working" are different failures.

DORMANCY
    ``XMRJ_DORMANT_DAYS`` (default 7) changes only the classification, never
    a stored stamp, so history survives a window change.

Run for the operator report:
    python pool/scout_lifecycle.py [--days N] [--json]
"""
from __future__ import annotations

import json
import os
import sys
import time

try:                                    # package import (pool.scout_lifecycle)
    from . import address_check as _addr
    from . import database as db
except ImportError:                     # direct ``python pool/x.py`` layout
    import address_check as _addr       # type: ignore[no-redef]
    import database as db               # type: ignore[no-redef]

NEW = "NEW"
INSTALLED = "INSTALLED"
ACTIVATED = "ACTIVATED"
CONNECTED = "CONNECTED"
MINING = "MINING"
DORMANT = "DORMANT"
STATES = (NEW, INSTALLED, ACTIVATED, CONNECTED, MINING, DORMANT)

DEFAULT_DORMANT_DAYS = 7
DAY = 86400.0

SCHEMA = """
    CREATE TABLE IF NOT EXISTS scout_lifecycle (
        install_id TEXT PRIMARY KEY,
        wallet_address TEXT UNIQUE,
        first_seen REAL NOT NULL,
        installed_at REAL,
        first_connect REAL,
        first_share REAL,
        first_valid_share REAL,
        activation_complete INTEGER NOT NULL DEFAULT 0,
        last_seen REAL
    );
    CREATE INDEX IF NOT EXISTS idx_scout_lifecycle_seen
        ON scout_lifecycle (first_seen);
"""


# ─── schema ──────────────────────────────────────────────────────────

def ensure_schema() -> None:
    """Create the lifecycle table. Idempotent; safe on every boot."""
    with db.get_db() as conn:
        conn.executescript(SCHEMA)


# ─── the one writer ──────────────────────────────────────────────────

def _row(conn, install_id: str) -> dict:
    row = conn.execute("SELECT * FROM scout_lifecycle WHERE install_id = ?",
                       (install_id,)).fetchone()
    return dict(row) if row is not None else {}


def _find(conn, wallet: str, install_id: str, at: float) -> str:
    """The row this fact belongs to: the installer's machine record, then
    the wallet the pool already knows, then a new row."""
    if install_id:
        row = conn.execute(
            "SELECT install_id FROM scout_lifecycle WHERE install_id = ?",
            (install_id,)).fetchone()
        if row:
            return row["install_id"]
    if wallet:
        row = conn.execute(
            "SELECT install_id FROM scout_lifecycle WHERE wallet_address = ?",
            (wallet,)).fetchone()
        if row:
            return row["install_id"]
    key = install_id or ("w:" + wallet)
    conn.execute(
        "INSERT OR IGNORE INTO scout_lifecycle "
        "(install_id, wallet_address, first_seen) VALUES (?, ?, ?)",
        (key, wallet or None, at))
    # re-read: a concurrent first event for the same wallet may have won
    row = conn.execute(
        "SELECT install_id FROM scout_lifecycle WHERE install_id = ? "
        "OR wallet_address = ? LIMIT 1", (key, wallet or None)).fetchone()
    return row["install_id"]


def record(*, wallet: str = "", install_id: str = "", at: float | None = None,
           installed: bool = False, activated: bool = False,
           connected: bool = False, valid: int = 0,
           invalid: int = 0) -> dict:
    """Apply one observed fact to a Scout's single row.

    This is the only writer. Every stamp is first-occurrence only, so
    replayed or out-of-order events cannot rewrite history. Fail-soft by
    contract: telemetry may never block registration, share ingest, ticket
    minting or a running poller, so a database error is logged and returns
    ``{}`` instead of raising.
    """
    wallet = (wallet or "").strip()
    install_id = (install_id or "").strip()
    if not wallet and not install_id:
        return {}
    when = float(at if at is not None else time.time())
    valid, invalid = int(valid or 0), int(invalid or 0)
    observed_share = valid + invalid > 0
    try:
        with db.get_db() as conn:
            key = _find(conn, wallet, install_id, when)
            conn.execute(
                """UPDATE scout_lifecycle SET
                     wallet_address = COALESCE(wallet_address, NULLIF(?, '')),
                     installed_at = COALESCE(installed_at, ?),
                     activation_complete = MAX(activation_complete, ?),
                     first_connect = COALESCE(first_connect, ?),
                     first_share = COALESCE(first_share, ?),
                     first_valid_share = COALESCE(first_valid_share, ?),
                     last_seen = MAX(COALESCE(last_seen, 0), ?)
                   WHERE install_id = ?""",
                (wallet,
                 when if installed else None,
                 1 if activated else 0,
                 # a share cannot exist without a completed handshake
                 when if (connected or observed_share) else None,
                 when if observed_share else None,
                 when if valid > 0 else None,
                 when, key))
            return _row(conn, key)
    except Exception as e:              # noqa: BLE001 — telemetry is fail-soft
        print(f"[Lifecycle] not recorded: {e}")
        return {}


# ─── classification ──────────────────────────────────────────────────

def dormant_days() -> float:
    try:
        return max(0.0, float(os.environ.get("XMRJ_DORMANT_DAYS",
                                             DEFAULT_DORMANT_DAYS)))
    except (TypeError, ValueError):
        return float(DEFAULT_DORMANT_DAYS)


def classify(row: dict, *, now: float | None = None,
             days: float | None = None) -> str:
    """The one state definition (precedence is the lifecycle order).

    DORMANT outranks MINING: a rig that produced a share a month ago is not
    mining now, and calling it MINING would hide the churn this exists to
    surface. DORMANT requires telemetry, so a silent install stays INSTALLED
    and a never-connected Scout is never counted as inactive.
    """
    when = float(now if now is not None else time.time())
    window = float(days if days is not None else dormant_days())
    telemetry = (row.get("first_connect") is not None
                 or row.get("first_share") is not None)
    last = float(row.get("last_seen") or row.get("first_seen") or 0)
    if telemetry and last and (when - last) > window * DAY:
        return DORMANT
    if row.get("first_valid_share") is not None:
        return MINING
    if row.get("first_connect") is not None:
        return CONNECTED
    if row.get("activation_complete"):
        return ACTIVATED
    if row.get("installed_at"):
        return INSTALLED
    return NEW


# ─── backfill ────────────────────────────────────────────────────────

def _wallet_valid(addr: str) -> bool:
    """The payout gate's offline check, fail-CLOSED: ``is_valid`` can raise
    inside base58 decoding on pathological input, and a backfill must not
    lose every row because one legacy wallet is corrupt."""
    try:
        return bool(_addr.is_valid(addr))
    except Exception:                 # noqa: BLE001 — a corrupt row, not a crash
        return False


def backfill() -> int:
    """Give every miner the pool already knew a lifecycle row (idempotent).

    Registration was the installer's activation act before this table
    existed, so ``activation_complete`` is set. ``installed_at`` stays NULL
    because no beacon ever ran on those machines — the report counts
    beacons apart from activations. ``last_seen`` is written only by
    telemetry, so it is the connection/share evidence. Rows the payout gate
    would refuse (test/audit fixtures such as ``PENDING_*`` or a corrupt
    checksum) never become fleet members.
    """
    seeded = 0
    with db.get_db() as conn:
        miners = conn.execute(
            "SELECT wallet_address, created_at, last_seen, shares_total, "
            "shares_valid FROM miners").fetchall()
        for m in miners:
            wallet = (m["wallet_address"] or "").strip()
            if not _wallet_valid(wallet):     # the registration gate's rule
                continue
            if conn.execute("SELECT 1 FROM scout_lifecycle WHERE "
                            "wallet_address = ?", (wallet,)).fetchone():
                continue
            first = float(m["created_at"] or 0) or time.time()
            seen = float(m["last_seen"] or 0)
            stamp = seen or first
            conn.execute(
                "INSERT INTO scout_lifecycle (install_id, wallet_address, "
                "first_seen, activation_complete, first_connect, first_share, "
                "first_valid_share, last_seen) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
                ("w:" + wallet, wallet, first, seen or None,
                 stamp if int(m["shares_total"] or 0) > 0 else None,
                 stamp if int(m["shares_valid"] or 0) > 0 else None,
                 stamp))
            seeded += 1
    return seeded


# ─── report ──────────────────────────────────────────────────────────

def _rows() -> list[dict]:
    ensure_schema()
    with db.get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM scout_lifecycle ORDER BY first_seen ASC")]


def _item(row: dict, state: str) -> dict:
    return {
        "install_id": row["install_id"],
        "wallet_address": row.get("wallet_address"),
        "state": state,
        "first_seen": row.get("first_seen"),
        "installed_at": row.get("installed_at"),
        "first_connect": row.get("first_connect"),
        "first_share": row.get("first_share"),
        "first_valid_share": row.get("first_valid_share"),
        "activation_complete": bool(row.get("activation_complete")),
        "last_seen": row.get("last_seen"),
    }


def report(*, now: float | None = None, days: float | None = None) -> dict:
    """The four answers, the state census, and the never-connected split.

    ``never_connected`` is its own bucket, never folded into ``dormant``:
    those are the Scouts that never appeared in telemetry at all, split by
    whether activation data ever reached them. The third bucket is
    ``connected_without_valid_share`` (dialed in, nothing accepted).
    """
    when = float(now if now is not None else time.time())
    window = float(days if days is not None else dormant_days())
    by_state = {s: 0 for s in STATES}
    installed = activated = connected = shared = 0
    never: list[dict] = []
    no_valid: list[dict] = []
    dormant: list[dict] = []

    for row in _rows():
        state = classify(row, now=when, days=window)
        by_state[state] += 1
        installed += bool(row.get("installed_at")
                          or row.get("activation_complete"))
        activated += bool(row.get("activation_complete"))
        connected += row.get("first_connect") is not None
        shared += row.get("first_share") is not None

        if row.get("first_connect") is None and row.get("first_share") is None:
            never.append(_item(row, state))
        elif row.get("first_valid_share") is None:
            no_valid.append(_item(row, state))
        if state == DORMANT:
            dormant.append(_item(row, state))

    without_activation = sum(1 for i in never if not i["activation_complete"])
    return {
        "generated_at": when,
        "dormant_days": window,
        "answers": {
            "scouts_installed": installed,
            "scouts_activated": activated,
            "scouts_ever_connected": connected,
            "scouts_submitted_shares": shared,
        },
        "by_state": by_state,
        "never_connected": {
            "total": len(never),
            "without_activation": without_activation,
            "after_activation": len(never) - without_activation,
            "scouts": never,
        },
        "connected_without_valid_share": {
            "total": len(no_valid),
            "scouts": no_valid,
        },
        "dormant": {"total": len(dormant), "scouts": dormant},
    }


# ─── operator report ─────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    days = None
    if "--days" in args:
        try:
            days = float(args[args.index("--days") + 1])
        except (IndexError, ValueError):
            print("--days needs a number", file=sys.stderr)
            return 2
    ensure_schema()
    backfill()
    data = report(days=days)
    if "--json" in args:
        print(json.dumps(data, indent=2))
        return 0

    a = data["answers"]
    never = data["never_connected"]
    print("SCOUT FLEET — lifecycle")
    print(f"  installed            {a['scouts_installed']}")
    print(f"  activated            {a['scouts_activated']}")
    print(f"  ever connected       {a['scouts_ever_connected']}")
    print(f"  submitted shares     {a['scouts_submitted_shares']}")
    print("  states               " + "  ".join(
        f"{s} {n}" for s, n in data["by_state"].items() if n))
    print(f"  never connected      {never['total']}  "
          f"({never['without_activation']} without activation data, "
          f"{never['after_activation']} activated)")
    print(f"  connected, no valid  "
          f"{data['connected_without_valid_share']['total']}")
    print(f"  dormant (> {data['dormant_days']:g}d)       "
          f"{data['dormant']['total']}")
    if never["scouts"]:
        print("\nNEVER CONNECTED (not the same as dormant)")
        for i in never["scouts"]:
            why = ("activated" if i["activation_complete"]
                   else "no activation data")
            print(f"  {i['state']:<9} "
                  f"{i['wallet_address'] or i['install_id']}  {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
