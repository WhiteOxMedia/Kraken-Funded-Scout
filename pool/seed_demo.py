"""
Seed the database with demo activity data so the site doesn't look empty.
Run once: python pool/seed_demo.py
Safe to re-run (checks for existing data first).
"""
import time
import random
import hashlib
import database as db

# ─── Demo miner wallet addresses (fake but valid-looking XMR addresses) ───
DEMO_MINERS = [
    # (short_addr_prefix, worker_name, shares, hashrate, last_seen_minutes_ago)
    ("49H2j", "RigMaster_01",    15840, 1240.5, 2),
    ("44A7x", "SilentMiner",      9200,  815.2, 5),
    ("47Kk9", "CoffeeMiner",      4350,  420.0, 1),
    ("48Bn3", "NightOwl",         7890,  610.8, 3),
    ("41Vz6", "DustyRig",        12300,  980.3, 1),
    ("43Pp8", "LazyHash",         2100,  195.7, 8),
    ("45Qw2", "AlphaMiner",      18700, 1450.0, 0),
    ("42Mm5", "BudgetRig",        3400,  310.5, 4),
    ("46Rr1", "GhostWorker",      5600,  488.9, 2),
    ("49Tt7", "DeepMiner",        8900,  720.1, 1),
    ("44Ll4", "WeekendWarrior",   1200,  110.0, 15),
    ("47Nn8", "SteadyHash",       6700,  545.6, 3),
    ("48Cc2", "TurboRig",        10200,  890.4, 0),
    ("41Dd9", "ColdBoot",         2800,  240.3, 6),
    ("43Ff6", "HashHerder",       7100,  580.2, 2),
    ("45Gg3", "MineMaster",      14500, 1120.7, 1),
    ("42Hh8", "PixelPusher",      1900,  165.4, 12),
    ("46Jj5", "CoreCrusher",      8300,  670.9, 3),
    ("49Kk1", "ThreadKing",       5400,  430.1, 4),
    ("44Zz6", "QuietQuarry",      3700,  300.8, 5),
]

# ─── Past drawing results (for leaderboard/history) ───
DEMO_DRAWINGS = [
    # (jackpot_amount, winner_prefix, house_tax, hours_ago, total_tickets)
    (0.0312, "49H2j", 0.00312, 24,  342),
    (0.0287, "45Qw2", 0.00287, 48,  289),
    (0.0354, "48Cc2", 0.00354, 72,  401),
    (0.0268, "41Vz6", 0.00268, 96,  256),
    (0.0401, "47Kk9", 0.00401, 120, 467),
]

def generate_fake_xmr_address(prefix):
    """Generate a realistic-looking 95-char Monero address."""
    chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    body = prefix + "".join(random.choices(chars, k=89))
    return body[:95]

def flag_demo_rows():
    """Mark already-seeded demo miners, without re-seeding anything.

    The is_demo column arrived after the demo data did (Oct 24), so an
    existing install had 20 unlabelled simulated miners. Matching on the
    seeder's own worker names is how those rows get flagged; new rows are
    flagged on insert below as well. Idempotent.
    """
    names = [w for _p, w, _s, _h, _m in DEMO_MINERS]
    with db.get_db() as conn:
        conn.executemany(
            "UPDATE miners SET is_demo = 1 WHERE worker_name = ?",
            [(n,) for n in names],
        )


def seed():
    print("Checking for existing data...")
    existing = db.get_all_miners()
    if len(existing) >= 20:
        flag_demo_rows()
        print(f"Already have {len(existing)} miners — skipping seed "
              "(demo rows re-flagged).")
        return

    print("Seeding demo miners...")
    miner_ids = []
    now = time.time()

    for prefix, worker, shares, hashrate, mins_ago in DEMO_MINERS:
        addr = generate_fake_xmr_address(prefix)
        last_seen = now - (mins_ago * 60)
        with db.get_db() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO miners
                   (wallet_address, worker_name, shares_valid, shares_total, hashrate, last_seen, balance)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (addr, worker, shares, shares, hashrate, last_seen),
            )
            if cur.rowcount > 0:
                miner_id = cur.lastrowid
                miner_ids.append((miner_id, addr, worker))
                print(f"  + {worker} ({addr[:12]}...) — {shares} shares, {hashrate} H/s")
            else:
                # Already exists, get the ID
                row = conn.execute(
                    "SELECT id FROM miners WHERE wallet_address = ?", (addr,)
                ).fetchone()
                if row:
                    miner_ids.append((row["id"], addr, worker))

    # MARK THEM AS SIMULATED (Oct 24): the UI can then say so instead of
    # showing fake hashrate next to real miners. Idempotent: re-running
    # re-flags the same wallets.
    with db.get_db() as conn:
        conn.executemany(
            "UPDATE miners SET is_demo = 1 WHERE wallet_address = ?",
            [(addr,) for _mid, addr, _worker in miner_ids],
        )

    print(f"\nSeeded {len(miner_ids)} demo miners.")

    # ─── Seed tickets (free tickets from mining) ───
    print("Seeding tickets...")
    total_tickets = 0
    with db.get_db() as conn:
        for mid, addr, worker in miner_ids:
            # 1 ticket per 100 shares
            num_tickets = random.randint(5, 50)
            for _ in range(num_tickets):
                conn.execute(
                    "INSERT INTO tickets (miner_id, ticket_type, cost_xmr) VALUES (?, 'free', 0)",
                    (mid,),
                )
            total_tickets += num_tickets
    print(f"  Seeded {total_tickets} free tickets across miners.")

    # ─── Seed paid spins ───
    print("Seeding paid spin history...")
    num_paid_spins = random.randint(15, 30)
    with db.get_db() as conn:
        for _ in range(num_paid_spins):
            mid, addr, _ = random.choice(miner_ids)
            conn.execute(
                """INSERT INTO paid_spins (miner_id, amount_xmr, tickets_received, status)
                   VALUES (?, 0.001, 1, 'confirmed')""",
                (mid,),
            )
    print(f"  Seeded {num_paid_spins} confirmed paid spins.")

    # ─── Seed past drawings with winners ───
    print("Seeding past drawings...")
    for i, (jackpot, winner_prefix, house_tax, hours_ago, total_tix) in enumerate(DEMO_DRAWINGS, 1):
        drawn_at = now - (hours_ago * 3600)
        scheduled = drawn_at - 86400  # scheduled 24h before

        # Find a winner miner (use prefix match, or just pick random)
        winner = None
        for mid, addr, worker in miner_ids:
            if addr.startswith(winner_prefix):
                winner = (mid, addr, worker)
                break
        if not winner:
            winner = random.choice(miner_ids)

        # Create the drawing
        with db.get_db() as conn:
            cur = conn.execute(
                """INSERT INTO drawings
                   (jackpot_amount, winner_id, house_tax, total_tickets,
                    block_hash, commit_seed, reveal_seed, status, scheduled_for, drawn_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'drawn', ?, ?)""",
                (
                    jackpot,
                    winner[0],
                    house_tax,
                    total_tix,
                    hashlib.sha256(f"block-{i}".encode()).hexdigest(),
                    hashlib.sha256(f"commit-{i}".encode()).hexdigest()[:32],
                    hashlib.sha256(f"reveal-{i}".encode()).hexdigest()[:32],
                    scheduled,
                    drawn_at,
                ),
            )
            drawing_id = cur.lastrowid

            # Create payout for winner
            payout_amount = jackpot - house_tax  # 90%
            conn.execute(
                """INSERT INTO payouts (miner_id, amount_xmr, payout_type, drawing_id, status)
                   VALUES (?, ?, 'jackpot', ?, 'confirmed')""",
                (winner[0], round(payout_amount, 6), drawing_id),
            )

            # House tax payout
            conn.execute(
                """INSERT INTO payouts (miner_id, amount_xmr, payout_type, drawing_id, status)
                   VALUES (?, ?, 'house_tax', ?, 'confirmed')""",
                (winner[0], house_tax, drawing_id),
            )

            print(f"  Drawing #{i}: {payout_amount:.6f} XMR → {winner[2]} (jackpot was {jackpot:.4f})")

    # ─── Set the current jackpot balance ───
    # The jackpot formula is: base_setting + paid_spin_revenue - drawing_payouts
    # We need to calculate the correct base so the displayed jackpot = 0.0328
    # paid_spin_revenue = num_paid_spins * 0.001, drawing_payouts = sum of (jackpot - house_tax) for all drawings
    paid_spin_revenue = 0.028  # 28 spins * 0.001
    drawing_payouts = sum(j - h for j, _, h, _, _ in DEMO_DRAWINGS)  # ~0.14618
    desired_jackpot = 0.0328
    correct_base = round(desired_jackpot - paid_spin_revenue + drawing_payouts, 6)

    print("\nSetting jackpot balance...")
    db.set_setting("jackpot_balance", str(correct_base))
    db.set_setting("house_tax_accumulated", "0.0042")
    print(f"  Base setting: {correct_base} XMR")
    print(f"  Paid spin revenue: {paid_spin_revenue} XMR")
    print(f"  Drawing payouts: {round(drawing_payouts, 6)} XMR")
    print(f"  Displayed jackpot: {desired_jackpot} XMR")
    print(f"  House tax accumulated: 0.0042 XMR")

    # ─── Mark that seeding was done ───
    db.set_setting("demo_data_seeded", str(int(now)))
    db.set_setting("demo_data_note", "This is simulated activity data for display purposes")

    print("\n✅ Demo data seeded successfully!")
    print(f"   {len(miner_ids)} miners, {total_tickets} tickets, {len(DEMO_DRAWINGS)} past drawings")


if __name__ == "__main__":
    seed()
