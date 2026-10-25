"""installer.py — THE OPT-IN INSTALLER CORE (Sep 24).

The bundled install presents ONE choice: the Miner Bot checkbox.

  ON  (the default) -> configure the miner, auto-register the user with
                       the jackpot system, grant the first raffle ticket,
                       bind the wallet. The user is IN the jackpot.
  OFF               -> nothing is configured, nobody is registered, no
                       ticket exists. The user may register manually at
                       xmrjackpot.com later; the phrase on the finish
                       screen says exactly that.

THE OPT-OUT GUARANTEE (audited contract): the opt-in state lives in
installer/install_config.json and NOWHERE ELSE. This module writes no
registry keys, no scheduled tasks, no Startup-folder entries, no
services — "uninstall" is deleting the folder, and that is the whole
story. The drive (test_installer.py) scans this file's source to keep
that promise enforceable.

Registration mapping (per the bundled protocol):
  - the MINER registry (/api/miner/register -> the miners table) is the
    jackpot entry: drawings draw from miner tickets. Opt-in auto-
    registers HERE, with the wallet bound to the account.
  - the web account (the users table behind require_auth) is the CLAIM
    identity and stays a manual step at xmrjackpot.com — the installer
    never creates it (registration before payout remains a human act).

Run:  .venv/Scripts/python.exe installer/installer.py --status
      .venv/Scripts/python.exe installer/installer.py --opt-in --wallet <addr>
      .venv/Scripts/python.exe installer/installer.py --opt-out
      (add --dry-run to any command to print the plan without writing)
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

INSTALLER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = INSTALLER_DIR.parent

XMRIG_CONFIG = PROJECT_DIR / "xmrig" / "xmrig-6.26.0" / "config.json"
RECORD_PATH = INSTALLER_DIR / "install_config.json"
DEFAULT_API = "https://xmrjackpot.com"
GETTING_STARTED = ("Not sure where to get started? "
                   "Start here: xmrjackpot.com")

# CUSTOMER-NEUTRAL MINER VALUES (Oct 25). The shipped/downloaded xmrig
# config used to carry the operator's rig id and a developer-local pool
# address, because it was the operator's own working config. A customer
# config is built from THESE values instead: the public stratum, no rig id
# at all, and a placeholder user until the customer activates.
PUBLIC_STRATUM = (os.environ.get("XMRJ_STRATUM_URL")
                  or "mine.xmrjackpot.com:3333")
PLACEHOLDER_WALLET = "YOUR_MONERO_ADDRESS"
PLACEHOLDER_PASS = "x"


def _neutral_config() -> dict:
    """A minimal xmrig config with no developer or operator values."""
    return {
        "api": {"id": None, "worker-id": None},
        "http": {"enabled": True, "host": "127.0.0.1", "port": 18000,
                 "access-token": None, "restricted": True},
        "autosave": True,
        "background": False,
        "colors": False,
        "title": True,
        "randomx": {"init": -1, "mode": "auto", "1gb-pages": False,
                    "rdmsr": False, "wrmsr": False},
        "cpu": {"enabled": True, "huge-pages": True, "hw-aes": None,
                "priority": None, "memory-pool": False, "yield": True,
                "asm": True},
        "pools": [{
            "algo": None,
            "coin": "monero",
            "url": PUBLIC_STRATUM,
            "user": PLACEHOLDER_WALLET,
            "pass": PLACEHOLDER_PASS,
            "rig-id": None,
            "nicehash": False,
            "keepalive": True,
            "enabled": True,
            "tls": False,
            "tls-fingerprint": None,
        }],
        "print-time": 60,
        "health-print-time": 60,
        "retries": 5,
        "retry-pause": 5,
        "watch": False,
    }

FRESH_RECORD = {
    "schema": 1,
    "miner_bot_opt_in": False,     # THE DEFAULT IS OPT-OUT: nothing exists
    "installed_at": None,          # until the checkbox is chosen
    "wallet_address": None,
    "registration": None,          # {miner_id, worker, at} when opted in
}


def load_record(path: Path | None = None) -> dict:
    """The durable opt-in record (fail-soft: unreadable = fresh)."""
    p = path or RECORD_PATH
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and "miner_bot_opt_in" in d \
            else dict(FRESH_RECORD)
    except (OSError, ValueError):
        return dict(FRESH_RECORD)


def save_record(rec: dict, path: Path | None = None) -> None:
    p = path or RECORD_PATH
    p.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def valid_wallet(addr: str) -> bool:
    """Same shape rule the server enforces on claims: starts 4 or 8,
    90-120 chars (no network call, no wallet internals touched)."""
    a = (addr or "").strip()
    return (a.startswith("4") or a.startswith("8")) and 90 <= len(a) <= 120


def _post_register(api_base: str, wallet: str, worker: str) -> dict:
    """The registration HTTP call, isolated for testing."""
    req = urllib.request.Request(
        api_base.rstrip("/") + "/api/miner/register",
        data=json.dumps({"wallet_address": wallet,
                         "worker_name": worker}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _patch_xmrig_user(wallet: str, config_path: Path | None = None) -> bool:
    """Bind the customer's wallet into a NEUTRAL xmrig config.

    Writes the config when it is absent (a customer downloads the official
    xmrig zip, which has none) and normalises it when it is present: the
    pool is the public stratum, the user is the customer's own address,
    the pass is the pool's ordinary placeholder, and no rig id is sent —
    an identifier is that customer's to choose, never an inherited one.
    Returns False only when the file cannot be written."""
    p = config_path or XMRIG_CONFIG
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        cfg = _neutral_config()
    pools = cfg.get("pools") or []
    if not pools:
        cfg["pools"] = _neutral_config()["pools"]
        pools = cfg["pools"]
    pool = pools[0]
    pool["url"] = PUBLIC_STRATUM
    pool["user"] = wallet
    pool["pass"] = PLACEHOLDER_PASS
    pool["rig-id"] = None
    pool["keepalive"] = True
    if "coin" not in pool:
        pool["coin"] = "monero"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def _grant_welcome_ticket(miner_id: int) -> int:
    """The first raffle ticket, granted at opt-in (direct DB layer — this
    box IS the pool server; a hosted deployment would expose the same
    grant as an admin API). Type 'free' — the schema's CHECK constrains
    ticket_type to free/paid — unassigned, and the drawing's
    assign_tickets_to_drawing picks up EVERY unassigned ticket, so this
    ticket genuinely enters the jackpot. Idempotent per wallet,
    SERVER-side: only a ticket-less miner receives it (a miner with any
    ticket has registered or mined before — the drive proves the
    one-ticket rule)."""
    if str(PROJECT_DIR / "pool") not in sys.path:
        sys.path.insert(0, str(PROJECT_DIR / "pool"))
    import database as db
    with db.get_db() as conn:
        have = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE miner_id = ?",
            (miner_id,)).fetchone()["c"]
        if have:
            return 0
    db.add_tickets(miner_id, "free", 1)
    return 1


def opt_in(wallet: str, api_base: str = DEFAULT_API, worker: str = "",
           dry_run: bool = False, record_path: Path | None = None,
           config_path: Path | None = None, live_db: bool = False) -> dict:
    """Checkbox ON: configure the miner + register the wallet on the pool.

    live_db is OFF for customer installs. The old default granted a
    "welcome ticket" by writing to a LOCAL pool database on the customer's
    machine — a database that is not the pool's, so the entry was never in
    any drawing. Registration (POST /api/miner/register) is the real act;
    entries come from accepted shares (and the paid Scout Pro unlock). The
    operator's own box can still pass live_db=True deliberately.
    """
    wallet = (wallet or "").strip()
    if not valid_wallet(wallet):
        return {"ok": False, "error": "invalid Monero address (4/8 prefix, "
                                      "90-120 chars)"}
    if dry_run:
        # dry-run touches NOTHING: the plan is described, not executed
        return {"ok": True, "dry_run": True, "plan": {
            "write_xmrig_config": True,
            "register": {"api": api_base, "worker": worker},
            "welcome_ticket": bool(live_db),
        }}

    # the real sequence, each step reported: config, registration, and (only
    # on the operator's own box) the local welcome ticket
    patched = _patch_xmrig_user(wallet, config_path)
    reg = _post_register(api_base, wallet, worker)
    if reg.get("status") != "ok":
        return {"ok": False, "error": f"registration failed: {reg}"}
    granted = _grant_welcome_ticket(reg["miner_id"]) if live_db else 0

    rec = load_record(record_path)
    rec.update({
        "miner_bot_opt_in": True,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wallet_address": wallet,
        "registration": {"miner_id": reg["miner_id"], "worker": worker,
                         "api": api_base},
    })
    save_record(rec, record_path)
    return {"ok": True, "miner_id": reg["miner_id"], "wallet": wallet,
            "xmrig_patched": patched, "welcome_ticket_granted": granted,
            "stratum": PUBLIC_STRATUM,
            "entry_note": ("Entries are minted from accepted shares; the "
                           "first one appears in your dashboard after the "
                           "pool accepts work from this wallet."),
            "phrase": GETTING_STARTED}


def opt_out(record_path: Path | None = None) -> dict:
    """Checkbox OFF (or a later change of heart): the record says opted-
    out and NOTHING else is touched — no miner process is killed from an
    installer, no config is scrubbed (a stopped miner mines nothing, and
    the user may still be running it deliberately)."""
    rec = load_record(record_path)
    rec.update({"miner_bot_opt_in": False,
                "opted_out_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                              time.gmtime()),
                "registration": rec.get("registration"),
                "wallet_address": rec.get("wallet_address")})
    save_record(rec, record_path)
    return {"ok": True, "opted_out": True}


def status(record_path: Path | None = None) -> dict:
    return load_record(record_path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Kraken Scout bundled installer (Miner Bot opt-in)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--opt-in", action="store_true")
    ap.add_argument("--opt-out", action="store_true")
    ap.add_argument("--wallet", default="")
    ap.add_argument("--worker", default="")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.status:
        print(json.dumps(status(), indent=1))
        return 0
    if a.opt_out:
        r = opt_out()
        print("Miner Bot disabled. " + GETTING_STARTED)
        return 0 if r["ok"] else 1
    if a.opt_in:
        r = opt_in(a.wallet, a.api, a.worker, a.dry_run)
        if r.get("ok") and r.get("dry_run"):
            # a plan is not an install: say so, show it, touch nothing
            print("DRY RUN — nothing was written. The opt-in would:")
            print(json.dumps(r["plan"], indent=1))
            print("  " + GETTING_STARTED)
            return 0
        if r.get("ok"):
            print("Miner Bot installed — you are entered in the jackpot.")
            print(f"  miner_id: {r.get('miner_id')}  "
                  f"welcome ticket: {r.get('welcome_ticket_granted')}")
            print("  " + r["phrase"])
            return 0
        print("ERROR: " + r.get("error", "unknown"))
        return 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
