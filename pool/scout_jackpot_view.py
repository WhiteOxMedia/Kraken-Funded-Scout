"""scout_jackpot_view.py — THE SAFE PUBLIC VIEW (Sep 24).

What Kraken Scout may display about the jackpot, and NOTHING else. Two
layers enforce the protocol's visibility rules:

  1. THE WHITELIST: community_view() passes only keys listed in
     SAFE_STATS_KEYS. A new field appearing in get_pool_stats() is NOT
     auto-exposed — someone must consciously add it to the whitelist
     (the drive proves the filter by injecting a forbidden key).
  2. THE SHAPE: miner_view(wallet) returns the caller's own ticket and
     claim state only, keyed by the wallet they present — no enumeration
     of other miners, no wallet internals, no keys, no RPC credentials
     anywhere in either payload.

All reads go through the engine/db layer; nothing here writes.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import database as db
import jackpot_engine as engine

# the ONLY pool-stats fields Scout may render (community-facing numbers)
SAFE_STATS_KEYS = {
    "jackpot_xmr", "jackpot_usd", "total_miners", "active_miners",
    "total_hashrate", "total_shares", "pending_tickets",
    "house_tax_percent", "drawing_interval_hours",
    "free_tickets_per_shares", "mining_active",
}


def community_view() -> dict:
    """The community panel: current pool, participation, next drawing."""
    stats = engine.get_pool_stats()
    view = {k: stats[k] for k in SAFE_STATS_KEYS if k in stats}

    # next drawing: the last drawing's drawn_at + the interval, or the
    # interval horizon itself when no drawing has ever run
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT MAX(drawn_at) d FROM drawings").fetchone()
    interval = int(view.get("drawing_interval_hours") or 24) * 3600
    last = row["d"] if row and row["d"] else None
    now = time.time()
    if last:
        next_at = last + interval
        while next_at <= now:
            next_at += interval
    else:
        next_at = now + interval
    view["next_drawing_at"] = int(next_at)
    view["seconds_remaining"] = max(0, int(next_at - now))
    view["community_contribution"] = {
        "active_miners": view.get("active_miners", 0),
        "total_hashrate": view.get("total_hashrate", 0.0),
    }
    return view


def miner_view(wallet: str) -> dict:
    """One miner's own jackpot state, keyed by the wallet they present:
    active (unassigned) tickets, lifetime tickets, claim status of their
    latest drawing. Returns friendly public values — never another
    miner's data, never wallet internals."""
    wallet = (wallet or "").strip()
    out = {"registered": False, "pending_tickets": 0, "total_tickets": 0,
           "claim": None}
    if not wallet:
        return out
    with db.get_db() as conn:
        m = conn.execute("SELECT id FROM miners WHERE wallet_address = ?",
                         (wallet,)).fetchone()
        if not m:
            return out
        out["registered"] = True
        mid = m["id"]
        out["pending_tickets"] = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE miner_id = ? AND "
            "drawing_id IS NULL", (mid,)).fetchone()["c"]
        out["total_tickets"] = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE miner_id = ?",
            (mid,)).fetchone()["c"]
        d = conn.execute(
            """SELECT d.id, d.claim_status, d.claim_deadline
               FROM drawings d JOIN miners w ON d.winner_id = w.id
               WHERE w.wallet_address = ? AND d.status = 'drawn'
               ORDER BY d.id DESC LIMIT 1""", (wallet,)).fetchone()
    if d:
        st = d["claim_status"] or "pending"
        label = {"pending": "won — claim open at xmrjackpot.com",
                 "claimed": "claimed", "expired": "expired — returned to "
                 "the pool"}.get(st, st)
        out["claim"] = {"drawing_id": d["id"], "status": st,
                        "label": label,
                        "deadline": d["claim_deadline"]}
    return out
