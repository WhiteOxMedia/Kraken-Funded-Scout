"""
Jackpot Engine — handles ticket allocation, provably fair drawings, and house tax.

Provably fair protocol:
1. Before drawing: commit_seed = SHA256(random) is published
2. Drawing uses: reveal_seed + next_monero_block_hash as randomness source
3. Winner = tickets[SHA256(reveal_seed + block_hash) % total_tickets]
4. After drawing: reveal_seed is published so anyone can verify
"""
import hashlib
import os
import time
import random
import json
import urllib.request
from datetime import datetime, timedelta

import database as db


def _miner_won_recently(miner_id, days=30, exclude_drawing_id=None):
    """THE 30-DAY WIN LIMIT (charter, Sep 25): one win per rolling 30
    days — mining and tickets continue, only the CLAIM waits. The
    gate reads the miner's OWN drawing (status='drawn' whose id is the
    greatest among their drawn wins — i.e. their most recent win) and
    compares its drawn_at against the window. exclude_drawing_id lets
    the claim-side gate exclude the drawing being claimed — the
    claimant's OTHER wins are what the cooldown counts (claiming your
    own pending prize is the normal first claim)."""
    try:
        with db.get_db() as conn:
            row = conn.execute(
                """SELECT MAX(d.drawn_at) t FROM drawings d
                   WHERE d.winner_id = ? AND d.status = 'drawn'
                   AND (? IS NULL OR d.id != ?)""",
                (miner_id, exclude_drawing_id, exclude_drawing_id)).fetchone()
        t = row["t"] if row else None
        return bool(t) and (time.time() - float(t)) < days * 86400
    except Exception:
        return False                        # fail-open: never block a claim on a read error


def generate_commit_reveal():
    """Generate a commit-reveal pair for provably fair drawing."""
    reveal_seed = os.urandom(32).hex()
    commit_seed = hashlib.sha256(reveal_seed.encode()).hexdigest()
    return commit_seed, reveal_seed


def get_next_block_hash():
    """Fetch the latest Monero block hash for use as randomness source."""
    try:
        # Use moneroblocks.info API
        req = urllib.request.Request(
            "https://moneroblocks.info/api/get_stats",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            height = data.get("height", 0)
            # Use block height as the randomness anchor
            return str(height)
    except Exception:
        # Fallback to timestamp-based seed (less ideal but works)
        return str(int(time.time()))


def select_winner(total_tickets, commit_seed, reveal_seed, block_hash):
    """
    Provably fair winner selection.
    Returns the winning ticket index (0-based).
    """
    # Combine reveal seed + block hash
    combined = f"{reveal_seed}:{block_hash}"
    # Generate enough random bytes for the selection
    h = hashlib.sha256(combined.encode()).hexdigest()
    # Use the hash as a deterministic random number
    winning_index = int(h, 16) % total_tickets
    return winning_index


def verify_drawing(drawing_id):
    """
    Public verification: re-run the drawing math to confirm fairness.
    Returns the full calculation trace so anyone can verify.
    """
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM drawings WHERE id = ? AND status = 'drawn'",
            (drawing_id,),
        ).fetchone()
        if not row:
            return {"error": "Drawing not found or not yet drawn"}

    drawing = dict(row)
    total_tickets = drawing["total_tickets"]
    if total_tickets <= 0:
        return {"error": "Drawing has no tickets — cannot verify", "drawing_id": drawing_id}
    commit_seed = drawing["commit_seed"]
    reveal_seed = drawing["reveal_seed"]
    block_hash = drawing["block_hash"]
    total_tickets = drawing["total_tickets"]
    winning_ticket_id = drawing["winning_ticket_id"]

    # Step 1: Verify commit matches reveal
    computed_commit = hashlib.sha256(reveal_seed.encode()).hexdigest()
    commit_verified = computed_commit == commit_seed

    # Step 2: Recompute the winning index
    combined = f"{reveal_seed}:{block_hash}"
    entropy = hashlib.sha256(combined.encode()).hexdigest()
    computed_index = int(entropy, 16) % total_tickets

    # Step 3: Verify the computed index matches the winning ticket's position
    with db.get_db() as conn:
        ticket_rows = conn.execute(
            "SELECT id, miner_id FROM tickets WHERE drawing_id = ? ORDER BY id",
            (drawing_id,),
        ).fetchall()

    expected_winner = None
    expected_ticket_id = None
    if total_tickets > 0:
        target_row = ticket_rows[computed_index] if computed_index < len(ticket_rows) else None
        if target_row:
            expected_ticket_id = target_row["id"]
            expected_winner = target_row["miner_id"]

    # THE 30-DAY SKIP IS PART OF THE DRAW (review triage, Sep 25
    # evening V): a skipped round is still fair — the published winner
    # is the drawn index ADVANCED past gated holders. When a
    # winner_limit_skip audit exists for THIS drawing, re-walk the ring
    # exactly as run_drawing did and grade against that walk; the raw
    # index stays published for transparency either way.
    with db.get_db() as conn:
        _skip_rows = conn.execute(
            "SELECT details FROM audit_log WHERE action = 'winner_limit_skip'"
        ).fetchall()
    _skip_miners = set()
    for _sr in _skip_rows:
        try:
            _det = json.loads(_sr["details"] or "{}")
        except (ValueError, TypeError):
            continue
        try:
            if int(_det.get("drawing_id", -1)) == int(drawing_id):
                _skip_miners.add(int(_det.get("miner_id", -1)))
        except (TypeError, ValueError):
            continue
    if _skip_miners and ticket_rows:
        _start = computed_index % len(ticket_rows)
        for _off in range(len(ticket_rows)):
            _cand = ticket_rows[(_start + _off) % len(ticket_rows)]
            if int(_cand["miner_id"]) not in _skip_miners:
                expected_ticket_id = _cand["id"]
                expected_winner = _cand["miner_id"]
                break
    index_verified = expected_ticket_id == winning_ticket_id

    return {
        "drawing_id": drawing_id,
        "jackpot_amount": drawing["jackpot_amount"],
        "house_tax": drawing["house_tax"],
        "total_tickets": total_tickets,
        "commit_seed": commit_seed,
        "reveal_seed": reveal_seed,
        "block_hash": block_hash,
        "verification": {
            "step_1_commit_match": {
                "sha256(reveal_seed)": computed_commit,
                "published_commit_seed": commit_seed,
                "verified": commit_verified,
            },
            "step_2_entropy": {
                "combined_string": combined,
                "sha256_result": entropy,
                "index_modulo": f"{entropy} % {total_tickets} = {computed_index}",
                "winning_index": computed_index,
            },
            "step_3_ticket_match": {
                "computed_winning_ticket_id": expected_ticket_id,
                "published_winning_ticket_id": winning_ticket_id,
                "verified": index_verified,
            },
        },
        "fair": commit_verified and index_verified,
    }


def get_jackpot_balance():
    """Get the current jackpot balance."""
    # Jackpot = base setting - previous payouts
    base = float(db.get_setting("jackpot_balance", "0"))

    # Subtract already paid out jackpots
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(jackpot_amount - house_tax), 0) as total
               FROM drawings WHERE status = 'drawn'
               AND COALESCE(claim_status, 'pending') = 'pending'"""
        ).fetchone()
        paid_out = row["total"]

    return max(0, base - paid_out)


# ═══════════════════════════════════════════════════════════════
#  WHO FUNDED THE POT (Oct 26 — the admin card's second line)
#
#  The base is one number written by several flows, and the operator's
#  question is which of them the money in play actually came from: the
#  daily funding accrual (the charter's formula, paid out of the pool's
#  own income) or XMR actually received (a Scout Pro donation's jackpot
#  share, a wallet seed, a house-tax top-up).
#
#  Two counters carry that attribution — DERIVED state, never a second
#  balance. The base stays the only number the pool draws on, so a wrong
#  or missing counter can never inflate or block a payout; the worst it
#  can do is mislabel the split, and _attribute() re-derives it from the
#  base on every write and every read so that cannot last.
#
#  A claim is paid out of BOTH sources in proportion to what each held
#  when it settled. "Accrual first" would be tidier to implement and a
#  fiction to read: once the XMR is in the one wallet nothing earmarks it
#  to a source, so the honest answer to "how much of the pot is accrual"
#  moves down with every payout rather than jumping.

SRC_ACCRUAL = "accrual"
SRC_RECEIVED = "received"
_SRC_SETTINGS = {SRC_ACCRUAL: "jackpot_src_accrual",
                 SRC_RECEIVED: "jackpot_src_received"}


def _src_read():
    """The two counters as stored. Missing or unparsable reads as 0 —
    _attribute() is what turns a 0 into a real answer."""
    out = {}
    for src, key in _SRC_SETTINGS.items():
        try:
            out[src] = max(0.0, float(db.get_setting(key, "0") or 0))
        except (TypeError, ValueError):
            out[src] = 0.0
    return out


def _src_write(accrual, received):
    db.set_setting(_SRC_SETTINGS[SRC_ACCRUAL],
                   str(round(max(0.0, accrual), 12)))
    db.set_setting(_SRC_SETTINGS[SRC_RECEIVED],
                   str(round(max(0.0, received), 12)))


def _lifetime_accrued_xmr():
    """Bootstrap only: what the daily compute has ever added. The
    funding ledger already stamps every accrual, so a box that predates
    this feature does not have to guess."""
    try:
        import jackpot_funding as _funding
        return float(_funding.lifetime_accrued_xmr())
    except Exception:
        return 0.0


def _attribute(base, accrual=None, received=None):
    """Describe `base` by source. Returns the two counters — it NEVER
    moves the base.

    Four cases, in the order they matter:
      * counters already sum to the base -> keep them as recorded;
      * no counters on an already-funded box (the feature is newer than
        the money) -> split the base by the funding ledger's own accrual
        history, capped at the base, and say so (`inferred`);
      * the base grew without a counter moving (a writer that passes no
        source: the house-tax seed, a demo seed, an operator's raw
        set_setting) -> the gap is RECEIVED, because the accrual only
        ever arrives through add_funding_base, so anything else is money
        that actually turned up (`reconciled`);
      * the base shrank without a counter moving (a payout, a manual
        correction) -> both counters shrink by the same factor, which is
        a claim being paid from both sources proportionally.
    """
    base = max(0.0, float(base or 0.0))
    if accrual is None or received is None:
        _held = _src_read()
        accrual = _held[SRC_ACCRUAL] if accrual is None else accrual
        received = _held[SRC_RECEIVED] if received is None else received
    accrual = max(0.0, float(accrual or 0.0))
    received = max(0.0, float(received or 0.0))
    total = accrual + received
    inferred = reconciled = False

    if total <= 0.0:
        if base > 0.0:
            _accrued = min(max(0.0, _lifetime_accrued_xmr()), base)
            accrual, received = _accrued, base - _accrued
            inferred = True
    elif abs(total - base) > 1e-9:
        gap = base - total
        if gap > 0.0:                    # money in, no counter moved
            received += gap
        else:                            # money out: paid from both
            _factor = base / total
            accrual, received = accrual * _factor, base - accrual * _factor
        reconciled = True

    return {SRC_ACCRUAL: round(accrual, 12),
            SRC_RECEIVED: round(received, 12),
            "inferred": inferred, "reconciled": reconciled}


def get_jackpot_split():
    """The pot split by where it came from — the admin card's second
    line. Every figure is XMR; a caller converts if it wants dollars.

    `accrual_xmr + received_xmr == jackpot_xmr` by construction. The
    counters describe the BASE, and a drawn-but-unclaimed prize is held
    back from the pot, so their share through the pot is scaled down by
    exactly the fraction of the base still in play. Printing the base
    counters beside the pot would have the card claim more of the money
    than the pool will pay, which is the one thing this card exists to
    prevent.
    """
    base = max(0.0, float(db.get_setting("jackpot_balance", "0") or 0))
    pot = round(max(0.0, get_jackpot_balance()), 8)
    att = _attribute(base)
    if att["inferred"] or att["reconciled"]:
        try:
            _src_write(att[SRC_ACCRUAL], att[SRC_RECEIVED])
        except Exception as _e:      # a read must never die on a receipt
            print(f"[Sources] attribution write skipped: {_e}")
    held = att[SRC_ACCRUAL] + att[SRC_RECEIVED]
    share = (pot / held) if held > 0 else 0.0
    accrual_xmr = min(pot, round(att[SRC_ACCRUAL] * share, 8))
    received_xmr = round(max(0.0, pot - accrual_xmr), 8)
    return {
        "jackpot_xmr": pot,
        "accrual_xmr": accrual_xmr,
        "received_xmr": received_xmr,
        "accrual_pct": round(accrual_xmr * 100.0 / pot, 1) if pot > 0 else 0.0,
        "base_xmr": round(base, 8),
        "unclaimed_xmr": round(max(0.0, base - pot), 8),
        "attributed": pot > 0 and held > 0,
        "inferred": att["inferred"],
        "reconciled": att["reconciled"],
    }


# ═══════════════════════════════════════════════════════════════
#  THE ENTRY RULE (settled Oct 24 — one rule, three surfaces)
#
#   1 unit   = 1 valid share accepted by the pool.
#   1 entry  = ENTRY_UNIT units (the operator setting, default 100).
#   A token  IS an entry — there is no second balance anywhere: the
#   `tickets` table is the one ledger, and "token" is only ever the
#   word for an entry in the Pro unlock copy.
#
# The unlock is DAY-based and nothing else: 100 contributing days
# (db.pro_progress), plus the one +1 entry the unlock awards. Tokens
# do not gate or accelerate it, so no copy may imply they do.
#
# Everything reads the unit through entry_unit() below — the engine's
# minting, /api/stats (which the dashboard's counter draws from) and
# the homepage copy (server.py fills {{ENTRY_UNIT}}). One number, one
# source, so a change to the operator setting cannot leave the page
# describing yesterday's odds.
# ═══════════════════════════════════════════════════════════════
ENTRY_UNIT_SETTING = "free_tickets_per_shares"
DEFAULT_ENTRY_UNIT = 100


def entry_unit() -> int:
    """How many valid shares convert into ONE entry. Always >= 1.

    A 0, a negative or junk is a MISCONFIGURATION, not a switch: the
    pool falls back to DEFAULT_ENTRY_UNIT, exactly as the public stats
    guard always did. That keeps one meaning for one number — no
    surface can advertise "0 shares" and no admin typo can silently
    stop every entry in the pool.
    """
    try:
        val = int(float(db.get_setting(
            ENTRY_UNIT_SETTING, str(DEFAULT_ENTRY_UNIT))))
    except (TypeError, ValueError):
        return DEFAULT_ENTRY_UNIT
    return val if val > 0 else DEFAULT_ENTRY_UNIT


def allocate_free_tickets(miner_id, shares_submitted):
    """
    Allocate free tickets based on shares submitted.

    THE SETTLED ENTRY RULE: a share is a unit; entry_unit() units buy
    one entry. Partial units are kept in miner_xp.ticket_units, so an
    entry is minted whole or not at all. Also awards referral tickets
    when a referred miner becomes active.
    """
    tickets_per = entry_unit()          # never 0 — see entry_unit()
    if tickets_per <= 0:                # belt and braces only
        return 0

    miner = db.get_miner(miner_id)
    if not miner:
        return 0

    # THE STREAK BONUS (Oct 24): the day's conversion rate is decided by
    # the streak BEFORE the shares are converted. On a milestone day the
    # day's shares count 3x toward tickets; hashrate is never touched.
    try:
        streak = db.update_mining_streak(miner_id)
    except Exception as e:
        print(f"[Gamification] streak update failed for {miner_id}: {e}")
        streak = 0
    bonus = db.get_streak_bonus(streak)

    xp_rec = db.get_or_create_xp(miner_id)
    units = int(xp_rec.get("ticket_units") or 0)
    if units <= 0:
        # One-time baseline for miners who banked tickets before the
        # multiplier existed: their already-accepted shares start the
        # weighted counter, so nothing is minted retroactively.
        units = max(0, int(miner["shares_valid"] or 0)
                    - int(shares_submitted or 0))
    units += int(shares_submitted or 0) * int(bonus["multiplier"])
    expected_tickets = units // tickets_per

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE miner_id = ? "
            "AND ticket_type = 'free' "
            "AND COALESCE(source, '') NOT IN ('unlock', 'referral')",
            (miner_id,),
        ).fetchone()
        current_tickets = row["cnt"]
        conn.execute(
            "UPDATE miner_xp SET ticket_units = ? WHERE miner_id = ?",
            (units, miner_id))
        if bonus["active"]:
            conn.execute(
                "UPDATE miner_xp SET streak_milestones = "
                "streak_milestones + 1 WHERE miner_id = ?", (miner_id,))

    new_tickets = expected_tickets - current_tickets
    if new_tickets > 0:
        db.add_tickets(miner_id, "free", new_tickets)
    # A referral no longer pays here (Oct 25): the programme pays at
    # signup, to BOTH sides, one entry each, capped per cycle — see
    # db.award_referral_entries. Paying at first share as well would pay
    # the same referral twice.

    # ─── Gamification: XP, streak milestones, badges, Pro progression ───
    try:
        # Update lifetime shares + XP (1 XP per share)
        db.add_shares_lifetime(miner_id, shares_submitted)

        # Super ticket milestones: every 1000 shares = 1 super ticket
        xp_rec = db.get_or_create_xp(miner_id)
        milestone_count = xp_rec["total_shares_lifetime"] // 1000
        existing_super = xp_rec["super_tickets"]
        if milestone_count > existing_super:
            db.add_super_tickets(miner_id, milestone_count - existing_super)

        # Check & award badges
        ticket_count = db.get_miner_ticket_count(miner_id)
        ref_count = 0
        user = db.get_user_by_wallet(miner["wallet_address"])
        if user:
            ref_stats = db.get_referral_stats(user["id"])
            ref_count = ref_stats["active_referrals"] if ref_stats else 0

        newly_awarded = db.check_and_award_badges(
            miner_id,
            shares_valid=miner["shares_valid"],
            ticket_count=ticket_count,
            referral_count=ref_count,
            streak=streak,
        )
        if newly_awarded:
            print(f"[Gamification] Miner {miner_id} earned badges: {newly_awarded}")

        # THE 100-DAY PATH (Oct 24): a day counts only when the mining
        # bot actually contributed to the jackpot pool. The unlock awards
        # its +1 entry exactly once (db.unlock_scout_pro is idempotent).
        days = db.record_jackpot_day(miner_id)
        user = db.find_user_by_wallet(miner["wallet_address"])
        if user:
            prog = db.pro_progress(miner_id, user["id"])
            if not prog["pro_unlocked"] and days >= prog["days_required"]:
                grant = db.unlock_scout_pro(user["id"], "progression")
                print(f"[Progression] Miner {miner_id} unlocked Kraken Scout "
                      f"Pro after {days} jackpot days ({grant})")
    except Exception as e:
        print(f"[Gamification] Error updating stats for miner {miner_id}: {e}")

    return max(0, new_tickets)


def run_drawing():
    """
    Execute a jackpot drawing.
    This is the core lottery function.
    """
    # THE SELF-ADJUSTING DAILY SEED (charter, Sep 25): funding lands as
    # REAL base XMR once per day — the formula's amount, from the
    # funding ledger's 14-day rolling averages, capped at $20. Claims
    # and expiry keep their exact settlement semantics underneath.
    try:
        import jackpot_funding as _funding
        import jackpot_engine as _self_engine
        _funding.accrue_daily_funding(engine=_self_engine)
    except Exception as _e:
        print(f"[Funding] daily accrual skipped: {_e}")

    jackpot = get_jackpot_balance()
    min_jackpot = float(db.get_setting("min_jackpot_xmr", "0.01"))

    if jackpot < min_jackpot:
        return {"status": "skipped", "reason": f"Jackpot ({jackpot:.6f} XMR) below minimum ({min_jackpot} XMR)"}

    # Generate commit-reveal pair
    commit_seed, reveal_seed = generate_commit_reveal()

    # Create drawing record
    scheduled_for = time.time()
    drawing_id = db.create_drawing(jackpot, scheduled_for)

    # Assign all unassigned tickets to this drawing
    total_tickets = db.assign_tickets_to_drawing(drawing_id)

    if total_tickets == 0:
        # Cancel — no tickets. (Sep 24 audit: this executed a bare `conn`
        # with no enclosing context — NameError instead of a clean cancel.)
        # (second triage, Sep 25 evening batch: STAMP drawn_at here too —
        # same class as the all-gated cancel; an unstamped cancel re-fires
        # every cycle on any fresh install with a funded pot but no miners)
        with db.get_db() as conn:
            conn.execute("UPDATE drawings SET status = 'cancelled', "
                         "drawn_at = ? WHERE id = ?",
                         (time.time(), drawing_id))
        return {"status": "cancelled", "reason": "No tickets in pool", "drawing_id": drawing_id}

    # Get block hash for randomness
    block_hash = get_next_block_hash()

    # Select winner
    winning_index = select_winner(total_tickets, commit_seed, reveal_seed, block_hash)

    # Find the winning ticket
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT id, miner_id FROM tickets WHERE drawing_id = ? ORDER BY id LIMIT 1 OFFSET ?",
            (drawing_id, winning_index),
        ).fetchone()

        if not row:
            # review triage (Sep 25 evening V): the drawing and its
            # tickets EXIST by this point — a bare error return left the
            # drawing unfinished and its tickets assigned forever. Cancel
            # + release + stamp, exactly like the other exits.
            with db.get_db() as conn:
                conn.execute("UPDATE drawings SET status = 'cancelled', "
                             "drawn_at = ? WHERE id = ?",
                             (time.time(), drawing_id))
                conn.execute("UPDATE tickets SET drawing_id = NULL "
                             "WHERE drawing_id = ?", (drawing_id,))
            db.add_audit_log(
                None, "drawing_cancelled_select_failed",
                json.dumps({"drawing_id": drawing_id,
                            "reason": "winning ticket not found"}),
            )
            return {"status": "error", "reason": "Could not find winning ticket"}

        winning_ticket_id = row["id"]
        winner_id = row["miner_id"]

    # Resolve the winner ONCE, before any use (Sep 24 audit: `if winner:`
    # below ran BEFORE the lookup at the function tail — UnboundLocalError
    # on every draw, after finalize + deadline but before the house-tax
    # bookkeeping; the server loop's broad except swallowed it)
    winner = db.get_miner(winner_id)

    # THE 30-DAY WIN LIMIT (charter): a miner who won within the last
    # 30 days cannot take another jackpot. Their tickets stay in the
    # draw (they keep earning), but the win must land on an ELIGIBLE
    # holder: walk the ring from the drawn index — the same
    # commit-reveal index, advanced — past EVERY ticket of gated
    # miners (a 99-ticket winner is skipped whole, not shifted once),
    # to the first eligible holder. Fully audited; the payout model
    # does not move (no claim can occur for a skipped win).
    if winner and _miner_won_recently(winner_id):
        db.add_audit_log(
            None, "winner_limit_skip",
            json.dumps({"drawing_id": drawing_id, "miner_id": winner_id,
                        "worker": (winner.get("worker_name") or "")[:40],
                        "reason": "30_day_win_limit"}),
        )
        with db.get_db() as conn:
            _ring = conn.execute(
                "SELECT id, miner_id FROM tickets WHERE drawing_id = ? "
                "ORDER BY id", (drawing_id,)).fetchall()
        _n = len(_ring)
        if not _n:
            return {"status": "error", "reason": "No tickets to redraw "
                    "under the 30-day limit"}
        _start = select_winner(_n, commit_seed, reveal_seed, block_hash)
        _picked = None
        for _off in range(_n):
            _cand = _ring[(_start + _off) % _n]
            if not _miner_won_recently(_cand["miner_id"]):
                _picked = _cand
                break
        if _picked is None:
            # THE ALL-GATED ROUND (review triage, Sep 25): the round is
            # CANCELLED, not left pending — an unfinished drawing kept
            # its tickets assigned forever, and because a cancelled
            # round never wrote drawn_at, the scheduler re-fired every
            # cycle and created a fresh drawing each pass. Release the
            # ring, audit the cancellation, and let the scheduler's
            # clock (which now reads cancelled rounds too) wait a full
            # interval before the next attempt.
            # (second triage, Sep 25 evening batch: the clock change was
            # lost in a patch bounce — the UPDATE now STAMPS drawn_at,
            # and check_drawing_schedule reads MAX(drawn_at) over
            # drawn+cancelled. Drive-pinned both ways.)
            with db.get_db() as conn:
                conn.execute("UPDATE drawings SET status = 'cancelled', "
                             "drawn_at = ? WHERE id = ?",
                             (time.time(), drawing_id))
                conn.execute("UPDATE tickets SET drawing_id = NULL "
                             "WHERE drawing_id = ?", (drawing_id,))
            db.add_audit_log(
                None, "drawing_cancelled_all_gated",
                json.dumps({"drawing_id": drawing_id,
                            "tickets_released": _n,
                            "reason": "30_day_win_limit"}),
            )
            return {"status": "skipped", "reason": "Every ticket holder "
                    "is inside the 30-day win window — the round was "
                    "cancelled and its tickets released"}
        winning_ticket_id = _picked["id"]
        winner_id = _picked["miner_id"]
        winner = db.get_miner(winner_id)

    # Calculate house tax
    house_tax_percent = float(db.get_setting("house_tax_percent", "10"))
    house_tax = jackpot * (house_tax_percent / 100)
    payout_amount = jackpot - house_tax

    # Finalize drawing
    db.finalize_drawing(drawing_id, winner_id, winning_ticket_id, house_tax, block_hash, commit_seed, reveal_seed, total_tickets)

    # Set 7-day claim deadline — winner must add a wallet & claim within this window
    claim_days = float(db.get_setting("claim_window_days", "7"))
    db.set_drawing_claim_deadline(drawing_id, time.time() + (claim_days * 86400))

    # Store winner wallet on the drawing for quick lookup
    if winner:
        db.set_drawing_winner_wallet(drawing_id, winner["wallet_address"])

    # Create payout record for house tax (sent immediately as before)
    # to the HOUSE account's REAL id (review batch, Sep 27 - see
    # house_miner_id; miners.id = 1 was an assumption the live DB
    # already disproves)
    db.add_payout(house_miner_id(), house_tax, "house_tax", drawing_id)

    # Accumulate house tax for Kraken payout
    accumulate_house_tax(house_tax)

    # NOTE: Winner payout is DEFERRED until they claim via /api/winner/claim.
    # The jackpot balance is NOT zeroed here — it stays until claimed or expired.
    # Only the winner's portion (payout_amount) is earmarked.

    return {
        "status": "drawn",
        "drawing_id": drawing_id,
        "winner": winner["wallet_address"] if winner else "unknown",
        "jackpot_amount": jackpot,
        "house_tax": house_tax,
        "payout": payout_amount,
        "total_tickets": total_tickets,
        "commit_seed": commit_seed,
        "reveal_seed": reveal_seed,
        "block_hash": block_hash,
    }


def check_drawing_schedule():
    """Check if it's time for a drawing and run it if so."""
    interval_hours = float(db.get_setting("drawing_interval_hours", "24"))

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT MAX(drawn_at) as last_drawn FROM drawings "
            "WHERE status IN ('drawn', 'cancelled')"
        ).fetchone()

    last_drawn = row["last_drawn"] if row and row["last_drawn"] else 0
    next_drawing = last_drawn + (interval_hours * 3600)

    if time.time() >= next_drawing:
        return run_drawing()
    else:
        return {
            "status": "waiting",
            "next_drawing_in": next_drawing - time.time(),
        }


def seed_jackpot_from_wallet():
    """
    Seed the jackpot from the local wallet balance.
    Called when wallet has funds and jackpot is empty.
    """
    try:
        import monero_wallet as xmr
        if not xmr.rpc_available():
            return None

        balance = xmr.get_balance()
        if not balance or balance["unlocked_balance"] <= 0:
            return None

        seed_amount = float(db.get_setting("jackpot_seed_xmr", "0.05"))
        available = balance["unlocked_balance"]

        if available < seed_amount:
            seed_amount = available * 0.5  # use half of what's available

        if seed_amount <= 0:
            return None

        # Add to jackpot balance — through the one funding writer and
        # tagged RECEIVED (Oct 26): this is real wallet money moving into
        # the pot, not the formula's accrual, and the admin card says so.
        new_jackpot = add_funding_base(seed_amount, source=SRC_RECEIVED)

        return {
            "seeded": seed_amount,
            "new_jackpot": new_jackpot,
            "wallet_balance": available,
        }
    except Exception as e:
        print(f"[Seed] Error: {e}")
        return None


def _get_real_proxy_miners():
    """Get the count of real miners connected to xmrig-proxy."""
    try:
        import json as _json
        import urllib.request as _urlreq
        # THE DEPLOYMENT'S TOKEN, never a literal (Oct 25): the proxy
        # token is rotated in monero/proxy_config.json; a hard-coded copy
        # here kept presenting the retired one after every rotation.
        token = (os.environ.get("XMRJ_PROXY_API_TOKEN") or "").strip()
        if not token:
            try:
                _cfg = Path(__file__).resolve().parent.parent / "monero" / "proxy_config.json"
                _tok = ((json.loads(_cfg.read_text(encoding="utf-8"))
                         .get("http") or {}).get("access-token"))
                token = str(_tok or "").strip()
            except Exception:
                token = ""
        req = _urlreq.Request(
            "http://127.0.0.1:9090/2/summary",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Authorization": f"Bearer {token}",
            },
        )
        with _urlreq.urlopen(req, timeout=3) as resp:
            summary = _json.loads(resp.read())
        return summary.get("workers", 0)
    except Exception:
        return 0


def _get_p2pool_hashrate():
    """Get total hashrate from P2Pool API."""
    try:
        import json as _json
        import urllib.request as _urlreq
        req = _urlreq.Request("http://127.0.0.1:3334/api/local_stats", headers={"User-Agent": "Mozilla/5.0"})
        with _urlreq.urlopen(req, timeout=3) as resp:
            data = _json.loads(resp.read())
        return round(data.get("hashrate", 0), 1)
    except Exception:
        return 0


def add_funding_base(amount_xmr, source=SRC_ACCRUAL):
    """The funding model's ONE entry into the balance: raise the base
    by a REAL, backed amount (the self-adjusting daily seed). Nothing
    else may write the base for funding — one writer per flow.

    `source` says WHICH flow funded it, because the admin pot card now
    reports the mix: SRC_ACCRUAL for the daily compute's own income,
    SRC_RECEIVED for XMR that actually arrived (a Scout Pro donation's
    jackpot share). Anything that reaches the base outside this function
    is reconciled as received on the next read, so a flow that forgets
    the argument can only under-claim the formula, never over-claim it.
    """
    amount_xmr = max(0.0, float(amount_xmr or 0.0))
    _b = float(db.get_setting("jackpot_balance", "0"))
    _att = _attribute(_b)            # absorb any drift before crediting,
    _a, _r = _att[SRC_ACCRUAL], _att[SRC_RECEIVED]   # so this credit lands
    if source == SRC_RECEIVED:                       # on the right side
        _r += amount_xmr
    else:
        _a += amount_xmr
    db.set_setting("jackpot_balance", str(round(_b + amount_xmr, 8)))
    _src_write(_a, _r)
    return round(_b + amount_xmr, 8)


def _xmr_usd_rate():
    """The pool's ONE XMR/USD reader (jackpot_funding's), so the hero
    pot and the funding formula can never quote different dollars. The
    constant here is only reachable if that import itself breaks — it
    is never the price a working box quotes."""
    try:
        import jackpot_funding as _funding
        return float(_funding.xmr_usd_rate())
    except Exception:
        return 323.0


def _xmr_usd_rate_info():
    """The rate WITH its provenance (Oct 26), for the surfaces that
    quote dollars: a visitor must be able to see what the conversion
    was, how old it is, and whether it is the fallback."""
    try:
        import jackpot_funding as _funding
        return _funding.rate_info()
    except Exception:
        return {"rate": 323.0, "source": "default", "as_of": None,
                "stale": True,
                "label": ("converted at the FALLBACK $323.00/XMR "
                          "\u2014 no live rate on this box")}


def get_pool_stats():
    """Get aggregate pool statistics."""
    jackpot = get_jackpot_balance()
    rate_info = _xmr_usd_rate_info()
    rate = float(rate_info.get("rate") or _xmr_usd_rate())

    with db.get_db() as conn:
        # Total miners
        row = conn.execute("SELECT COUNT(*) as cnt FROM miners").fetchone()
        total_miners = row["cnt"]

        # Active miners (seen in last 15 min — wider window to avoid timing gaps with scanner)
        cutoff = time.time() - 900
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM miners WHERE last_seen > ?", (cutoff,)
        ).fetchone()
        active_miners = row["cnt"]

        # Total hashrate (clamp to filter corrupted values)
        row = conn.execute(
            "SELECT COALESCE(SUM(hashrate), 0) as total FROM miners WHERE last_seen > ? AND hashrate > 0 AND hashrate < 100000",
            (cutoff,),
        ).fetchone()
        total_hashrate = row["total"]

        # Total shares
        row = conn.execute(
            "SELECT COALESCE(SUM(shares_valid), 0) as total FROM miners"
        ).fetchone()
        total_shares = row["total"]

        # Pending tickets
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id IS NULL"
        ).fetchone()
        pending_tickets = row["cnt"]

    return {
        "jackpot_xmr": round(jackpot, 6),
        "jackpot_usd": round(jackpot * rate, 2),
        # the rate the dollar figure was converted at, so a caller can
        # label it instead of implying the pot IS dollars — and the
        # descriptor beside it (source, age, stale) so the label can say
        # WHOSE rate it is and whether it is old or a fallback
        "xmr_usd_rate": round(rate, 2),
        "xmr_usd_rate_info": rate_info,
        "total_miners": total_miners,
        "active_miners": active_miners,
        "total_hashrate": round(total_hashrate, 1),
        "total_shares": total_shares,
        "pending_tickets": pending_tickets,
        "house_tax_percent": int(db.get_setting("house_tax_percent", "10")),
        "drawing_interval_hours": int(db.get_setting("drawing_interval_hours", "24")),
        "free_tickets_per_shares": entry_unit(),
        "real_miners": _get_real_proxy_miners(),
        "p2pool_hashrate": _get_p2pool_hashrate(),
        "mining_active": _get_real_proxy_miners() > 0,
    }


# ─── Kraken-Compliant Auto-Payout ────────────────────────────

def house_miner_id():
    """THE HOUSE ACCOUNT (review batch, Sep 27 major): the house tax
    records used the hardcoded assumption miners.id = 1, which is
    FALSE on any real database - the live box's miner ids start at 23
    (autoincrement was consumed by deleted probe rows). The house
    account is instead the miner row with the reserved HOUSE address,
    bootstrapped idempotently on first use (get_or_create_miner) and
    then looked up by that address - the ACTUAL id, whatever it is,
    satisfying the payouts FK without touching real miners' ids."""
    house = db.get_or_create_miner("HOUSE", worker_name="house_tax")
    return house["id"]


def accumulate_house_tax(amount_xmr):
    """Add house tax to the accumulated pool for Kraken payout."""
    current = float(db.get_setting("house_tax_accumulated", "0"))
    db.set_setting("house_tax_accumulated", str(round(current + amount_xmr, 8)))


def check_and_send_house_tax():
    """
    Check if accumulated house tax meets Kraken payout threshold.
    Sends to Kraken deposit address with fee buffer if ready.
    Returns dict with action details, or None if below threshold.
    """
    accumulated = float(db.get_setting("house_tax_accumulated", "0"))
    threshold = float(db.get_setting("payout_threshold_xmr", "0.01"))
    fee_buffer = float(db.get_setting("transfer_fee_buffer_xmr", "0.0001"))
    kraken_addr = db.get_setting("kraken_deposit_address",
        os.environ.get("XMRJ_KRAKEN_DEPOSIT_ADDRESS", ""))

    if not kraken_addr:
        return {"action": "skipped",
                "reason": "no Kraken deposit address configured "
                          "(XMRJ_KRAKEN_DEPOSIT_ADDRESS)"}

    if accumulated < threshold:
        return None

    # Calculate payout: accumulated minus fee buffer, floor at Kraken minimum 0.0001
    payout_amount = max(0.0001, accumulated - fee_buffer)

    # Attempt transfer via monero-wallet-rpc
    tx_hash = None
    try:
        import monero_wallet as xmr
        if xmr.rpc_available():
            result = xmr._rpc("transfer", {
                "destinations": [{"address": kraken_addr, "amount": int(payout_amount * 1e12)}],
                "priority": 1,
                "ring_size": 16,
            })
            if "error" not in result:
                tx_hash = result.get("tx_hash", "")
                db.set_setting("house_tax_accumulated", "0")
            else:
                return {"action": "failed", "error": result["error"], "amount": payout_amount}
        else:
            tx_hash = "pending_manual"
    except Exception as e:
        return {"action": "failed", "error": str(e), "amount": payout_amount}

    # Record payout. SCHEMA-DRIFT FIX (Sep 26) + HOUSE-ID FIX (review
    # batch, Sep 27): the row goes to the HOUSE account's REAL id
    # (reserved 'HOUSE' address, bootstrapped idempotently) - the old
    # hardcoded id=1 was false on any real database and the manual
    # "UPDATE miners SET id = 1" rewrite here could have clobbered a
    # real miner row. With the reserved address there is nothing to
    # rewrite and no real miner can ever be touched.
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO payouts (miner_id, amount_xmr, payout_type, tx_hash, status) VALUES (?, ?, ?, ?, ?)",
            (house_miner_id(), payout_amount, "house_tax", tx_hash,
             "sent" if tx_hash and tx_hash != "pending_manual" else "pending"),
        )

    return {
        "action": "sent" if tx_hash else "pending",
        "amount": payout_amount,
        "fee_buffer": fee_buffer,
        "tx_hash": tx_hash,
    }


# ─── Winner Claim Flow ───────────────────────────────────────

def process_winner_claim(drawing_id, wallet_address):
    """
    Process a winner's claim: verify they're the winner, send the payout,
    mark as claimed. Returns dict with result.
    """
    drawing = None
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM drawings WHERE id = ?", (drawing_id,)
        ).fetchone()
        if row:
            drawing = dict(row)

    if not drawing:
        return {"status": "error", "reason": "Drawing not found"}

    if drawing["status"] != "drawn":
        return {"status": "error", "reason": "Drawing not finalized"}

    if drawing.get("claim_status") == "claimed":
        return {"status": "error", "reason": "Already claimed"}

    if drawing.get("claim_status") == "expired":
        return {"status": "error", "reason": "Claim window expired"}

    # Verify the winner's wallet matches
    winner = db.get_miner(drawing["winner_id"])
    if not winner or winner["wallet_address"] != wallet_address:
        return {"status": "error", "reason": "Wallet does not match the winner"}

    # THE 30-DAY WIN LIMIT, CLAIM SIDE (charter: "Reject claim if
    # within 30-day cooldown") — implemented (review triage, Sep 25:
    # the charter ordered a claim-time gate and the code carried only a
    # comment; the drive masked it because every fixture claimed a
    # miner whose only win was THIS drawing). The gate counts the
    # claimant's OTHER wins only — excluding the drawing being claimed
    # — because claiming your own pending prize is the normal first
    # claim, and a payout after the draw-time skip already landed them
    # an eligible win. A refused claim writes the audit row and is
    # retriable after the window passes (the claim stays pending —
    # only the cooldown holds it).
    if _miner_won_recently(drawing["winner_id"], days=30,
                           exclude_drawing_id=drawing_id):
        db.add_audit_log(
            None, "claim_refused_30_day_limit",
            json.dumps({"drawing_id": drawing_id,
                        "miner_id": drawing["winner_id"],
                        "reason": "30_day_win_limit"}),
        )
        return {"status": "error",
                "reason": "30-day win limit: you already won within the "
                          "last 30 days — the claim unlocks when the "
                          "cooldown passes"}

    # Check deadline
    deadline = drawing.get("claim_deadline")
    if deadline and time.time() > deadline:
        # Expire it — the earmark releases back to the pool via the
        # balance model; log the rollover event like the sweep's path
        # does (this branch cannot double-fire: if the sweep expired
        # first, the claim_status == 'expired' guard above returned)
        db.expire_drawing(drawing_id)
        try:
            _ret = drawing["jackpot_amount"] - drawing.get("house_tax", 0)
            db.add_audit_log(
                None, "jackpot_rollover",
                json.dumps({"drawing_id": drawing_id,
                            "returned_xmr": round(_ret, 8),
                            "via": "claim_raced_deadline"}),
            )
        except Exception as _e:
            print(f"[Rollover] audit log write failed: {_e}")
        return {"status": "error", "reason": "Claim window expired"}

    # Calculate payout amount
    jackpot = drawing["jackpot_amount"]
    house_tax = drawing.get("house_tax", 0)
    payout_amount = jackpot - house_tax

    # Send payout via wallet RPC
    tx_hash = None
    try:
        import monero_wallet as xmr
        if xmr.rpc_available():
            result = xmr._rpc("transfer", {
                "destinations": [{"address": wallet_address, "amount": int(payout_amount * 1e12)}],
                "priority": 1,
                "ring_size": 16,
            })
            if "error" not in result:
                tx_hash = result.get("tx_hash", "")
            else:
                return {"status": "error", "reason": "Wallet RPC error: " + str(result.get("error"))}
        else:
            tx_hash = "pending_manual"
    except Exception as e:
        return {"status": "error", "reason": f"Transfer failed: {e}"}

    # Record the payout
    db.add_payout(drawing["winner_id"], payout_amount, "jackpot", drawing_id)
    # Update payout status if we got a tx_hash
    if tx_hash and tx_hash != "pending_manual":
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT id FROM payouts WHERE drawing_id = ? AND payout_type = 'jackpot' ORDER BY id DESC LIMIT 1",
                (drawing_id,),
            ).fetchone()
            if row:
                db.update_payout_status(row["id"], "sent", tx_hash)

    # Mark drawing as claimed
    db.claim_drawing(drawing_id, wallet_address)

    # THE SETTLEMENT (Sep 24 protocol audit): the payout has LEFT the
    # wallet, so the base loses exactly the paid amount — never more
    # (the old set_setting("jackpot_balance", "0") destroyed the
    # un-earmarked remainder on every claim) and never less (no mutation
    # at all would leave the pool drawing on money already sent). The
    # earmark released via claim_drawing; subtracting the payout here
    # keeps base == the pool's real remaining funds.
    _base = float(db.get_setting("jackpot_balance", "0"))
    if _base >= payout_amount:               # real funds pay real claims;
        db.set_setting("jackpot_balance",    # a negative base is not a
                       str(round(_base - payout_amount, 8)))  # payout
        _new_base = round(_base - payout_amount, 8)
    else:
        db.set_setting("jackpot_balance", "0")
        _new_base = 0.0
        db.add_audit_log(
            None, "claim_overdraw",
            json.dumps({"drawing_id": drawing_id,
                        "requested": round(payout_amount, 8),
                        "base_was": round(_base, 8)}),
        )

    # WHO PAID FOR IT (Oct 26): shrink the source counters with the base,
    # proportionally, so the admin card keeps describing the money still
    # in play instead of the money that was here yesterday.
    try:
        _att = _attribute(_new_base)
        _src_write(_att[SRC_ACCRUAL], _att[SRC_RECEIVED])
    except Exception as _e:
        print(f"[Settlement] source attribution skipped: {_e}")

    # Update winner's total_earned
    with db.get_db() as conn:
        conn.execute(
            "UPDATE users SET total_earned = total_earned + ?, total_jackpots = total_jackpots + 1 WHERE wallet_address = ?",
            (payout_amount, wallet_address),
        )

    return {
        "status": "ok",
        "drawing_id": drawing_id,
        "payout_amount": payout_amount,
        "tx_hash": tx_hash,
    }


def process_winner_claim_by_user(user_id, wallet_address):
    """
    Process a winner's claim by user ID — looks up the pending drawing
    for this user's wallet, then processes the claim.
    """
    # Find the pending drawing for this wallet
    pending = db.get_pending_claim_for_wallet(wallet_address)
    if not pending:
        return {"status": "error", "reason": "No pending claim found for this wallet"}

    return process_winner_claim(pending["id"], wallet_address)


def check_expired_claims():
    """
    Check for drawings past their claim deadline that haven't been claimed.
    Return the jackpot amount to the pool and mark as expired.
    """
    unclaimed = db.get_unclaimed_drawings()
    expired = []
    now = time.time()

    for drawing in unclaimed:
        deadline = drawing.get("claim_deadline")
        if deadline and now > deadline:
            # THE ROLLOVER (Sep 24 protocol audit): the winner's portion
            # returns to the pool by RELEASE, not by refund — the earmark
            # model (get_jackpot_balance = base minus PENDING drawn
            # winner-portions) means expiring the drawing IS the money
            # moving back; the old `base += returned` refund CONJURED
            # phantom funds once the earmark released. No base mutation.
            jackpot = drawing["jackpot_amount"]
            house_tax = drawing.get("house_tax", 0)
            returned = jackpot - house_tax

            # THE RACE GUARD (Sep 25 de-gate): expire ONLY if the
            # claim is still pending AT THE WRITE. The old
            # unconditional UPDATE would erase a claim that landed
            # between this sweep's read and its write — and a sweep
            # that runs from more than one loop (server + poller) runs
            # against claimers, not just against silence. Compare-and-
            # set, mirroring db.expire_drawing with the pending
            # precondition; rowcount 0 means claimed (or expired)
            # under us — hands off the winner's money.
            with db.get_db() as conn:
                cur = conn.execute(
                    "UPDATE drawings SET claim_status = 'expired' "
                    "WHERE id = ? AND status = 'drawn' "
                    "AND COALESCE(claim_status, 'pending') = 'pending'",
                    (drawing["id"],),
                )
                if cur.rowcount == 0:
                    continue
            # THE ROLLOVER EVENT LOG (protocol: "Log the rollover event"):
            # an unclaimed jackpot returning to the pool is a money event —
            # it lands in audit_log like every other money action, with the
            # drawing id and the returned amount (names/numbers, never
            # personal data). user_id NULL: the system is the actor.
            try:
                db.add_audit_log(
                    None, "jackpot_rollover",
                    json.dumps({"drawing_id": drawing["id"],
                                "returned_xmr": round(returned, 8),
                                "via": "claim_sweep"}),
                )
            except Exception as _e:
                print(f"[Rollover] audit log write failed: {_e}")
            expired.append({
                "drawing_id": drawing["id"],
                "returned": round(returned, 8),
                "new_jackpot": round(get_jackpot_balance(), 8),
            })

    return expired if expired else None
