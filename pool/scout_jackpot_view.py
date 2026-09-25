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

# THE KRAKEN CHALLENGE (charter, Sep 25): the win -> challenge ->
# referral-bonus -> bigger-jackpot loop, surfaced ONLY when there is a
# live claim to point at. Both mandated phrases ride this module.
try:
    import kraken_challenge as _challenge
except Exception:                          # headless fallback: phrases only
    _challenge = None

GETTING_STARTED = ("Not sure where to get started? "
                   "Start here: xmrjackpot.com")
FREE_PHRASE = "Start for free at: https://xmrjackpot.com"

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


# THE FUNDING MODEL, DISPLAY-SAFE (charter, Sep 25): the plate may
# show HOW TODAY'S POT IS PLANNED — the two formula components, the
# planned daily amount, the rate it converted at, and whether the
# $20 charter cap bound — and nothing else about the funding ledger.
# An explicit allowlist (not SAFE_STATS_KEYS, which governs
# get_pool_stats()): a new funding field appearing in
# compute_daily_jackpot() is NOT auto-exposed; someone must
# consciously add it here (the drive pins this the same way it pins
# the whitelist).
FUNDING_VIEW_KEYS = {
    "referral_velocity_usd", "miner_yield_usd", "raw_usd",
    "daily_jackpot_usd", "daily_jackpot_xmr", "capped", "xmr_usd",
}


def funding_view() -> dict:
    """Today's self-adjusting plan (the charter formula, live):

        DailyJackpot = min(20 USD, ReferralVelocity + MinerYield)

    Read-only and fail-soft: a funding-model problem degrades to an
    empty dict, never a broken plate. The cap_usd field is dropped
    from the display payload (internal constant; the boolean `capped`
    carries the user-facing fact)."""
    try:
        import jackpot_funding as _funding
        plan = _funding.compute_daily_jackpot()
        if not isinstance(plan, dict):
            return {}
        return {k: plan[k] for k in FUNDING_VIEW_KEYS
                if k in plan}
    except Exception:
        return {}


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
        if st == "pending":
            out["kraken_challenge"] = challenge_view()
    return out


def challenge_view() -> dict:
    """THE KRAKEN CHALLENGE GUIDE — display data for Scout, shaped for
    the visibility layer: public steps, the operator-configured
    referral link, both mandated phrases, and the jackpot's own
    funding facts (no wallet internals, no user identity, no secret)."""
    out = {"steps": [], "referral_link": "", "funding": None,
           "phrases": {"getting_started": GETTING_STARTED,
                       "free": FREE_PHRASE}}
    if _challenge is not None:
        try:
            out["steps"] = _challenge.guide_lines()
            out["referral_link"] = _challenge.get_referral_link()
        except Exception:
            pass
    try:
        import jackpot_funding as _f
        out["funding"] = _f.compute_daily_jackpot()
    except Exception:
        pass
    return out
