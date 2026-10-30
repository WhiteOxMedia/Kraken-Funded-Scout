"""
Database models for the XMR Daily Jackpot Pool.
SQLite database stores: miners, shares, tickets, drawings, payouts.
"""
import os
import sqlite3
import time
import json
from pathlib import Path
from contextlib import contextmanager

import field_crypto as _fc

DB_PATH = Path(__file__).parent.parent / "pool" / "jackpot.db"

# THE ENCRYPTED-AT-REST SURFACE (R2, Sep 26): users PII columns and the
# push-subscription store. All encryption lives behind field_crypto —
# the DB never sees plaintext for these fields once migrated.
ENCRYPTED_USER_COLS = ("email", "phone", "full_name", "wallet_address")


@contextmanager
def get_db():
    # CONCURRENT APPENDS: WAL lets readers run beside a writer, but two
    # writers still contend for the write lock. Without a busy timeout
    # sqlite3 raises "database is locked" the instant a second connection
    # holds it; with one, the append waits its turn and lands. Every
    # append-only store (profile_alert_log included) relies on this.
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # AT-REST HYGIENE (R2): scrub deleted content from the DB file —
    # without this, pre-encryption page images linger in freelist
    # pages even after the rows were rewritten as ciphertext
    conn.execute("PRAGMA secure_delete=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS miners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT UNIQUE NOT NULL,
                worker_name TEXT DEFAULT '',
                shares_total INTEGER DEFAULT 0,
                shares_valid INTEGER DEFAULT 0,
                shares_invalid INTEGER DEFAULT 0,
                hashrate REAL DEFAULT 0,
                last_seen REAL DEFAULT 0,
                balance REAL DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id INTEGER NOT NULL,
                ticket_type TEXT NOT NULL CHECK(ticket_type IN ('free', 'paid')),
                cost_xmr REAL DEFAULT 0,
                drawing_id INTEGER,
                created_at REAL DEFAULT (strftime('%s','now')),
                source TEXT DEFAULT '',
                FOREIGN KEY (miner_id) REFERENCES miners(id),
                FOREIGN KEY (drawing_id) REFERENCES drawings(id)
            );

            CREATE TABLE IF NOT EXISTS drawings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jackpot_amount REAL NOT NULL,
                winner_id INTEGER,
                winning_ticket_id INTEGER,
                house_tax REAL DEFAULT 0,
                block_hash TEXT,
                commit_seed TEXT,
                reveal_seed TEXT,
                total_tickets INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'drawn', 'cancelled')),
                scheduled_for REAL,
                drawn_at REAL,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (winner_id) REFERENCES miners(id),
                FOREIGN KEY (winning_ticket_id) REFERENCES tickets(id)
            );

            CREATE TABLE IF NOT EXISTS payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id INTEGER NOT NULL,
                amount_xmr REAL NOT NULL,
                payout_type TEXT NOT NULL CHECK(payout_type IN ('jackpot', 'mining', 'house_tax')),
                tx_hash TEXT,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'confirmed', 'failed')),
                drawing_id INTEGER,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (miner_id) REFERENCES miners(id),
                FOREIGN KEY (drawing_id) REFERENCES drawings(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paid_spins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id INTEGER NOT NULL,
                payment_tx TEXT,
                amount_xmr REAL,
                tickets_received INTEGER,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'confirmed', 'failed')),
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (miner_id) REFERENCES miners(id)
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                wallet_address TEXT,
                subaddress TEXT,
                subaddress_index INTEGER,
                is_admin INTEGER DEFAULT 0,
                display_name TEXT DEFAULT '',
                avatar TEXT DEFAULT '⛏️',
                bio TEXT DEFAULT '',
                referral_code TEXT UNIQUE,
                referred_by TEXT,
                referral_count INTEGER DEFAULT 0,
                total_earned REAL DEFAULT 0,
                total_spins INTEGER DEFAULT 0,
                total_jackpots INTEGER DEFAULT 0,
                signup_bonus_credited INTEGER DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now')),
                last_login REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY NOT NULL,
                user_id INTEGER NOT NULL,
                created_at REAL DEFAULT (strftime('%s','now')),
                expires_at REAL NOT NULL,
                ip_address TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                details TEXT DEFAULT '',
                ip_address TEXT,
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS profile_alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                alert_type TEXT NOT NULL
                    CHECK(alert_type IN ('BUY_NOW', 'GET_OUT_NOW')),
                payload TEXT DEFAULT '',
                ts TEXT DEFAULT '',
                created_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_profile_alert_user
                ON profile_alert_log (user_id, id DESC);

            CREATE TABLE IF NOT EXISTS admin_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_by INTEGER,
                updated_at REAL DEFAULT (strftime('%s','now')),
                FOREIGN KEY (updated_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS payment_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                txid TEXT NOT NULL,
                amount_xmr REAL NOT NULL,
                subaddress_index INTEGER,
                height INTEGER DEFAULT 0,
                confirmations INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'confirming', 'confirmed', 'credited', 'failed')),
                credits_awarded INTEGER DEFAULT 0,
                scanned_at REAL DEFAULT (strftime('%s','now')),
                credited_at REAL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_pv_txid ON payment_verifications(txid);

            CREATE TABLE IF NOT EXISTS miner_xp (
                miner_id INTEGER PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                mining_streak_days INTEGER DEFAULT 0,
                last_mine_day TEXT,
                total_shares_lifetime INTEGER DEFAULT 0,
                badges_earned TEXT DEFAULT '[]',
                super_tickets INTEGER DEFAULT 0,
                ticket_units INTEGER DEFAULT 0,
                streak_milestones INTEGER DEFAULT 0,
                jackpot_days INTEGER DEFAULT 0,
                last_jackpot_day TEXT,
                FOREIGN KEY (miner_id) REFERENCES miners(id)
            );
        """)

        # OPERATOR ADDRESSES COME FROM THE DEPLOYMENT (Oct 25). These were
        # hard-coded receive addresses in source; an address is an operator
        # identifier and must not live in a repo that can be cloned or
        # packaged. Unset means empty (payout refuses loudly) rather than a
        # silent default somebody else chose.
        house_wallet = os.environ.get("XMRJ_HOUSE_WALLET", "")
        kraken_deposit = os.environ.get("XMRJ_KRAKEN_DEPOSIT_ADDRESS", "")

        # Initialize default settings
        defaults = {
            "house_tax_percent": "10",
            "pool_fee_percent": "1",
            "free_tickets_per_shares": "100",
            "paid_spin_cost_xmr": "0.001",
            "paid_spin_tickets": "1",
            "drawing_interval_hours": "24",
            "min_jackpot_xmr": "0.01",
            "house_wallet": house_wallet,
            "upstream_pool": "pool.supportxmr.com:3333",
            "jackpot_balance": "0",
            # WHO FUNDED THE BASE (Oct 26): the two counters behind the
            # admin pot card's accrual-vs-received line. Derived state —
            # the engine reconciles them to jackpot_balance on read, so a
            # stale or missing value can only mislabel the split.
            "jackpot_src_accrual": "0",
            "jackpot_src_received": "0",
            "kraken_deposit_address": kraken_deposit,
            "payout_threshold_xmr": "0.01",
            "transfer_fee_buffer_xmr": "0.0001",
            "house_tax_accumulated": "0",
            "daily_paid_ticket_cap": "100",
            "jackpot_seed_xmr": "0.05",
            "signup_bonus_xmr": "0.003",
            "referral_ticket_reward": "100",
            "referral_cycle_cap": "10",
            "kraken_withdrawal_fee_xmr": "0.0027",
            "claim_window_days": "7",
            # THE STREAK BONUS (Oct 24): on a streak-milestone day the
            # tickets mined that day convert at 3x. It never touches the
            # miner's hashrate — only the jackpot side of the pool.
            "streak_bonus_every_days": "7",
            "streak_bonus_multiplier": "3",
            # THE PROGRESSION UNLOCK: days on which the mining bot
            # actually contributed to the jackpot pool. 100 of them
            # unlock Kraken Scout Pro and award the +1 unlock entry.
            "pro_unlock_days": "100",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

        # ─── Migrations: add columns to existing tables ───
        # SQLite ALTER TABLE ADD COLUMN is idempotent-safe with PRAGMA check
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(drawings)")}
        if "claim_deadline" not in cols:
            conn.execute("ALTER TABLE drawings ADD COLUMN claim_deadline REAL")
        if "claim_status" not in cols:
            # claim_status: 'pending' (awaiting wallet), 'claimed' (paid out), 'expired' (returned to pool)
            conn.execute(
                "ALTER TABLE drawings ADD COLUMN claim_status TEXT DEFAULT 'pending'"
            )
        if "claimed_at" not in cols:
            conn.execute("ALTER TABLE drawings ADD COLUMN claimed_at REAL")
        if "winner_wallet" not in cols:
            conn.execute("ALTER TABLE drawings ADD COLUMN winner_wallet TEXT")
        if "claim_leased_at" not in cols:
            # THE CLAIM LEASE (Oct 28): epoch when a claim reserved the
            # drawing.(NULL = no lease.) A lease is young property: a
            # live claim finishes in seconds, so any lease older than
            # the steal window means a claimant died mid-flight and the
            # drawing returns to the pool's book.
            conn.execute(
                "ALTER TABLE drawings ADD COLUMN claim_leased_at REAL")

        # ─── THE SAME-COIN PAYOUT COLUMNS (Oct 28) ───
        # Every money row carries the coin it was denominated in.
        # 'xmr' is the default and the past: a NULL/new row is an
        # XMR-denominated row, so every existing drawing and payout
        # keeps meaning exactly what it meant before. A 'vrsc' row was
        # PAID IN VERUS — the winner's coin decides it (see
        # process_winner_claim), and amount_coin carries the quantity
        # of that coin the prize was denominated in while amount_xmr
        # keeps its XMR-equivalent (the books stay in one unit).
        if "coin" not in cols:
            conn.execute(
                "ALTER TABLE drawings ADD COLUMN coin TEXT DEFAULT 'xmr'")
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(payouts)")}
        if "coin" not in pcols:
            conn.execute(
                "ALTER TABLE payouts ADD COLUMN coin TEXT DEFAULT 'xmr'")
        if "amount_coin" not in pcols:
            conn.execute(
                "ALTER TABLE payouts ADD COLUMN amount_coin REAL")

        # ─── THE FX-AT-MINT COLUMNS (Oct 28) ───
        # A same-coin row records WHICH rates priced it — fx_xmr_usd
        # and fx_vrsc_usd, captured at mint time — so reconciliation
        # can reprice amount_coin from the row itself and name the
        # exact book the treasury was charged against. NULL fx on an
        # 'xmr' row is the honest shape: the XMR leg pays face value
        # and priced nothing.
        if "fx_xmr_usd" not in pcols:
            conn.execute(
                "ALTER TABLE payouts ADD COLUMN fx_xmr_usd REAL")
        if "fx_vrsc_usd" not in pcols:
            conn.execute(
                "ALTER TABLE payouts ADD COLUMN fx_vrsc_usd REAL")

        # ─── THE DEMO FLAG (Oct 24 full-disclosure pass) ───
        # seed_demo.py fills the pool with simulated miners so a fresh
        # install is not an empty page. The stats and the leaderboard
        # then look like activity that does not exist. This flag is how
        # the UI can SAY which rows are simulated instead of passing
        # them off as real miners; the demo liveness refresher in
        # server.py also only touches flagged rows.
        mcols = {r["name"] for r in conn.execute("PRAGMA table_info(miners)")}
        if "is_demo" not in mcols:
            conn.execute(
                "ALTER TABLE miners ADD COLUMN is_demo INTEGER DEFAULT 0")
        # THE MINER'S OWN COIN (Oct 28): which coin this miner bot MINES
        # — set by the Verus relay path ('vrsc'); every other origin is
        # Monero ('xmr'). The drawing-side payout branch reads THIS to
        # decide the coin a win pays in.
        if "coin" not in mcols:
            conn.execute(
                "ALTER TABLE miners ADD COLUMN coin TEXT DEFAULT 'xmr'")

        # ─── Users table migrations ───
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "referral_code" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
        if "referred_by" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN referred_by TEXT")
        if "referral_count" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0")
        if "total_earned" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN total_earned REAL DEFAULT 0")
        if "total_spins" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN total_spins INTEGER DEFAULT 0")
        if "total_jackpots" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN total_jackpots INTEGER DEFAULT 0")
        if "signup_bonus_credited" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN signup_bonus_credited INTEGER DEFAULT 0")
        if "display_name" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT ''")
        # THE REGISTRATION ENVELOPE (user order, Sep 26 E2E): phone and
        # full name ride the registration form. Reversible: a column
        # drop is a one-liner, no data reshaping, no defaults destroyed.
        if "phone" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''")
        if "full_name" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN full_name TEXT DEFAULT ''")
        # THE KRAKEN REFERRAL ID (user order, Sep 26): the per-user id
        # the OPERATOR's referral source returns, attached via the
        # admin mapping UI. Distinct from the internal referral_code:
        # this is the identity Kraken (or the operator's provider)
        # knows the user by. Reversible: a column drop is a one-liner.
        if "kraken_referral_id" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN kraken_referral_id TEXT DEFAULT ''")
        # THE BLIND INDEX (R2, Sep 26): equality lookups on the encrypted
        # wallet column go through a keyed HMAC index instead of a
        # plaintext column — deterministic for lookups, opaque at rest.
        if "wallet_idx" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN wallet_idx TEXT DEFAULT ''")
        if "email_idx" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN email_idx TEXT DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_wallet_idx "
            "ON users(wallet_idx)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_email_idx "
            "ON users(email_idx)")
        if "avatar" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT '⛏️'")
        if "bio" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
        if "subaddress" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN subaddress TEXT")
        if "subaddress_index" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN subaddress_index INTEGER")
        if "last_login" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN last_login REAL DEFAULT 0")
        if "security_question" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN security_question TEXT")
        if "security_answer" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN security_answer TEXT")

        # ─── Comments table ───
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                message TEXT NOT NULL,
                status TEXT DEFAULT 'new' CHECK(status IN ('new', 'read', 'archived')),
                ip_address TEXT,
                created_at REAL DEFAULT (strftime('%s','now'))
            )
        """)
        # ─── Kraken referral projection (Sep 26) ───
        # Per-user referral status snapshots (one row per fetch — the
        # history IS the audit trail; money columns are USD, rounded
        # to cents at write time by the worker).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kraken_referral_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                referral_id TEXT,
                kraken_status TEXT,
                total_paid REAL DEFAULT 0,
                pending_amount REAL DEFAULT 0,
                expected_bonus_conservative REAL DEFAULT 0,
                expected_bonus_best REAL DEFAULT 0,
                seeded_equivalent REAL DEFAULT 0,
                estimate_used INTEGER DEFAULT 0,
                fetched_at REAL DEFAULT (strftime('%s','now')),
                source TEXT DEFAULT 'kraken_referral_api_v1',
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_krs_user "
            "ON kraken_referral_snapshot(user_id, fetched_at)")
        # THE single source of truth for the displayed projection — one
        # row per worker run (append-only history; latest row wins).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jackpot_projection (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                possible_future_jackpot REAL,
                referral_seed REAL,
                current_jackpot REAL,
                count_of_users_included INTEGER,
                anomaly INTEGER DEFAULT 0,
                source TEXT,
                updated_at REAL DEFAULT (strftime('%s','now'))
            )
        """)
        # THE KRAKEN CTA CLICK LEDGER (Oct 10): one row per clicked
        # referral CTA on the public page. Append-only, no session —
        # the counter MEASURES interest, it never spins up a login.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kraken_cta_click (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot TEXT NOT NULL,
                url TEXT,
                ip_address TEXT,
                user_id INTEGER,
                created_at REAL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kcc_slot "
            "ON kraken_cta_click(slot, created_at)")

        # ── Scout identity + cycle surfaces (Oct 17) ──────────────────────────
        # Backing tables for /api/scout/activate, /api/jackpot/status and
        # /api/scout/status. Idempotent (CREATE TABLE IF NOT EXISTS) and
        # additive only — no existing table or row is touched.
        #
        # Mirrors the on-chain model drafted in XMRJ_SOLANA_DEPLOYMENT_PLAN.md:
        #   identity_token   = activator's Solana wallet (the Signer of
        #                      activate_scout) — the on-chain identity primary
        #   aliases          = email / device_fingerprint / profile_id on the
        #                      same identity row, mapped to that wallet
        #   one-active-per-cycle = enforced in the service layer here and, on
        #                      chain, by the activate_scout guard.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scout_identities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity_token TEXT NOT NULL,
                email TEXT,
                device_fingerprint TEXT,
                profile_id TEXT,
                user_id INTEGER,
                created_at REAL DEFAULT (strftime('%s','now')),
                UNIQUE (identity_token),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scout_identities_token "
            "ON scout_identities (identity_token)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scout_identities_user "
            "ON scout_identities (user_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scout_jackpot_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                onchain_cycle_id TEXT NOT NULL,
                window_start REAL NOT NULL,
                window_end REAL NOT NULL,
                is_current INTEGER NOT NULL DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now')),
                UNIQUE (onchain_cycle_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scout_cycles_current "
            "ON scout_jackpot_cycles (is_current) "
            "WHERE is_current = 1")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scout_jackpot_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                onchain_entry_id TEXT NOT NULL,
                identity_id INTEGER NOT NULL,
                amount_xmr REAL NOT NULL DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now')),
                UNIQUE (onchain_entry_id),
                FOREIGN KEY (identity_id) REFERENCES scout_identities(id)
                    ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scout_entries_identity "
            "ON scout_jackpot_entries (identity_id)")

        # ── Scout Pro donation checkout (Oct 19) ─────────────────────────
        # One row per checkout. The address is the buyer's Monero
        # subaddress; the amount is the configured price at creation time.
        # Detection/fulfilment happens in scout_endpoints.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scout_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                identity_wallet TEXT,
                amount_xmr REAL NOT NULL,
                address TEXT NOT NULL,
                subaddress_index INTEGER,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','paid','expired','cancelled')),
                txid TEXT,
                confirmations INTEGER DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s','now')),
                expires_at REAL NOT NULL,
                paid_at REAL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scout_purchases_user "
            "ON scout_purchases (user_id, status)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_scout_purchases_txid "
            "ON scout_purchases (txid) WHERE txid IS NOT NULL")

        # Scout Pro entitlement lives on the account (days set by the
        # purchase; 0/null = not unlocked).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "scout_pro_until" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN scout_pro_until REAL DEFAULT 0")
        # The two unlock paths (Oct 24): a progression unlock is lifetime
        # (scout_pro_lifetime=1), a checkout grant is time-boxed in
        # scout_pro_until. The unlock entry is awarded at most once.
        for _col, _ddl in (
            ("scout_pro_lifetime",
             "ALTER TABLE users ADD COLUMN scout_pro_lifetime INTEGER DEFAULT 0"),
            ("scout_pro_unlock_source",
             "ALTER TABLE users ADD COLUMN scout_pro_unlock_source TEXT DEFAULT ''"),
            ("scout_pro_unlocked_at",
             "ALTER TABLE users ADD COLUMN scout_pro_unlocked_at REAL DEFAULT 0"),
            ("scout_pro_entry_awarded",
             "ALTER TABLE users ADD COLUMN scout_pro_entry_awarded INTEGER DEFAULT 0"),
            # Held unlock entries (Oct 24): an unlock entry is a ticket,
            # a ticket is owned by a miner, and a miner is owned by a
            # payout wallet. An account with no wallet yet keeps its
            # entry HERE instead of losing it — claim_pending_entries()
            # mints the real ticket as soon as a wallet is attached.
            ("pending_entries",
             "ALTER TABLE users ADD COLUMN pending_entries INTEGER DEFAULT 0"),
            # Referral entries (Oct 25): a referral pays ONE ENTRY TO EACH
            # SIDE at signup and never pot money. A side with no payout
            # wallet yet keeps its entry HERE instead of losing it —
            # claim_referral_entries() mints the ticket when a wallet
            # lands, the same deal unlock entries get.
            ("pending_referral_entries",
             "ALTER TABLE users ADD COLUMN pending_referral_entries "
             "INTEGER DEFAULT 0"),
        ):
            if _col not in cols:
                conn.execute(_ddl)

        # ─── The referral ledger (Oct 25) ───
        # One row per referred account EVER (UNIQUE), so a referral pays
        # once no matter how many times the signup path reaches it.
        # cycle_anchor is the drawn_at of the last completed drawing at
        # award time — the boundary the per-cycle cap counts against.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_awards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_user_id INTEGER NOT NULL UNIQUE,
                method TEXT NOT NULL DEFAULT 'email',
                cycle_anchor INTEGER NOT NULL DEFAULT 0,
                tickets INTEGER NOT NULL DEFAULT 0,
                referrer_granted INTEGER NOT NULL DEFAULT 0,
                referred_granted INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_referral_awards_cycle "
            "ON referral_awards(referrer_id, cycle_anchor)")

        # ─── The XMRJ referral accrual (Oct 25) ───
        # The INTERNAL programme's coin reward. There is no XMRJ token to
        # move: the XMRJ treasury program (Anchor source only, nothing
        # deployed on any cluster) books XMRJ as a u64 in its state PDA via
        # record_inflow_xmrj, and no mint exists anywhere. So an award is
        # ACCRUED here — the amount, the referrer, the Solana wallet the
        # pool knows at read time — and is payable when the treasury is
        # deployed. UNIQUE per referred account: one friend, one award.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_xmrj_awards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_user_id INTEGER NOT NULL UNIQUE,
                amount INTEGER NOT NULL DEFAULT 0,
                solana_wallet TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'accrued',
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_referral_xmrj_referrer "
            "ON referral_xmrj_awards(referrer_id)")

        # ─── Kraken payouts the operator records into the pot (Oct 25) ───
        # Kraken pays a referral bonus into the AFFILIATE's Kraken account,
        # never into the pool. The operator records the payout by hand; the
        # pool keeps its share (kraken_pool_share_pct, default 50) and the
        # referrer keeps the rest from Kraken directly. UNIQUE per referred
        # account per month, so a re-run cannot credit the pot twice.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kraken_referral_payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referred_user_id INTEGER NOT NULL,
                referrer_id INTEGER NOT NULL,
                gross_usd REAL NOT NULL,
                pool_usd REAL NOT NULL,
                pct INTEGER NOT NULL,
                period TEXT NOT NULL,
                ledger_id INTEGER,
                note TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                UNIQUE (referred_user_id, period)
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kraken_payouts_referrer "
            "ON kraken_referral_payouts(referrer_id, created_at)")

        # ─── THE DONATION ORDER KEY (Oct 26) ───
        # Idempotency for Scout Pro donation crediting used to be a scan of
        # jackpot_funding's settings-backed ledger — one JSON value capped
        # at LEDGER_MAX_ROWS (2000) and rewritten on every write. Once an
        # old order's receipt aged out of that cap, a replayed status poll
        # funded the pot a second time. This table is the key instead: one
        # row per order id, never pruned, and the INSERT is the claim. The
        # credit is written in the SAME transaction as the claim (callers
        # pass their get_db() connection in), so a crash between them
        # rolls both back rather than leaving money with no key, or a key
        # with no money.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS donation_credits (
                order_id TEXT PRIMARY KEY,
                amount_xmr REAL NOT NULL DEFAULT 0,
                jackpot_xmr REAL NOT NULL DEFAULT 0,
                treasury_xmr REAL NOT NULL DEFAULT 0,
                sponsor_id TEXT,
                source TEXT,
                base_xmr REAL,
                credited_at REAL DEFAULT (strftime('%s','now'))
            )
            """)

        # ─── Ticket provenance (Oct 24) ───
        tcols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(tickets)")}
        if "source" not in tcols:
            conn.execute(
                "ALTER TABLE tickets ADD COLUMN source TEXT DEFAULT ''")

        # ─── Miner progression columns (Oct 24) ───
        xcols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(miner_xp)")}
        for _col, _ddl in (
            ("ticket_units",
             "ALTER TABLE miner_xp ADD COLUMN ticket_units INTEGER DEFAULT 0"),
            ("streak_milestones",
             "ALTER TABLE miner_xp ADD COLUMN streak_milestones INTEGER DEFAULT 0"),
            ("jackpot_days",
             "ALTER TABLE miner_xp ADD COLUMN jackpot_days INTEGER DEFAULT 0"),
            ("last_jackpot_day",
             "ALTER TABLE miner_xp ADD COLUMN last_jackpot_day TEXT"),
        ):
            if _col not in xcols:
                conn.execute(_ddl)

    _init_scout_lifecycle()


def _init_scout_lifecycle() -> None:
    """Scout lifecycle table (Phase F) plus the idempotent backfill.

    Runs on its OWN connection AFTER the main init transaction commits:
    the backfill writes, so sharing init_db's open write transaction would
    wait on itself. Fail-soft — a telemetry table that cannot be created
    must never stop the pool from booting, but it must be loud.
    """
    try:
        import scout_lifecycle as _life
        _life.ensure_schema()
        _life.backfill()
    except Exception as _e:            # noqa: BLE001 — boot must survive
        print(f"[DB] scout lifecycle not initialised: {_e}")


def encrypt_existing_pii() -> dict:
    """THE ONE-TIME MIGRATION (R2, Sep 26): seal every plaintext PII
    value and the push-subscription blob under the ACTIVE key.
    IDEMPOTENT — envelopes, empty strings and NULLs pass through — so
    running it on boot is safe. Without a key it is a no-op and the
    system runs in the documented plaintext-compat mode."""
    counts = {"users_sealed": 0, "subs_blob_sealed": 0}
    if not _fc.available():
        return dict(counts, skipped="no key configured")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, phone, full_name, wallet_address, wallet_idx, "
            "email_idx FROM users").fetchall()
        for r in rows:
            # NULL stays NULL (review batch, Sep 27 CRITICAL): coalescing
            # five real NULL emails to "" would seal them into ONE shared
            # empty-email identity on the first keyed boot. create_user's
            # own convention: absent = NULL.
            vals = {c: r[c] for c in ENCRYPTED_USER_COLS}
            if any(v is not None and str(v) != ""
                   and not str(v).startswith(_fc.PREFIX)
                   for v in vals.values()):
                conn.execute(
                    "UPDATE users SET email=?, phone=?, full_name=?, "
                    "wallet_address=?, wallet_idx=?, email_idx=? WHERE id=?",
                    (_fc.encrypt(vals["email"])
                     if vals["email"] is not None else None,
                     _fc.encrypt(vals["phone"])
                     if vals["phone"] is not None else None,
                     _fc.encrypt(vals["full_name"])
                     if vals["full_name"] is not None else None,
                     _fc.encrypt(vals["wallet_address"])
                     if vals["wallet_address"] is not None else None,
                     _fc.blind_index(vals["wallet_address"])
                     if vals["wallet_address"] else None,
                     _fc.blind_index(vals["email"].strip().lower(),
                                     domain="email")
                     if vals["email"] else None,
                     r["id"]))
                counts["users_sealed"] += 1
            elif not (r["wallet_idx"] or "") and vals["wallet_address"]:
                conn.execute(
                    "UPDATE users SET wallet_idx=?, email_idx=? WHERE id=?",
                    (_fc.blind_index(vals["wallet_address"]),
                     _fc.blind_index(vals["email"].strip().lower(),
                                     domain="email")
                     if vals["email"] else None,
                     r["id"]))
                counts["users_sealed"] += 1
        raw = get_setting("push_subscriptions", "")
        if raw and not str(raw).startswith(_fc.PREFIX):
            try:
                import json as _json
                _json.loads(raw)                      # only seal valid JSON
                conn.execute("UPDATE settings SET value=? WHERE key='push_subscriptions'",
                             (_fc.encrypt(raw),))
                counts["subs_blob_sealed"] = 1
            except ValueError:
                pass
    return counts


def _seal_user_row(row: dict) -> dict:
    """Row-shape read hook: decrypt the PII columns transparently.
    Corrupt envelopes surface as row['<col>_error']='undecryptable'
    instead of crashing a read path — the data is still there under a
    repaired key."""
    if not row:
        return row
    for col in ENCRYPTED_USER_COLS:
        v = row.get(col)
        if isinstance(v, str) and v.startswith(_fc.PREFIX):
            try:
                row[col] = _fc.decrypt(v)
            except Exception:
                row[col + "_error"] = "undecryptable"
        row.setdefault(col, "")
    return row


def reencrypt_pii() -> dict:
    """Rotation companion: re-seal every stored PII envelope under the
    ACTIVE key. Idempotent, plain rows get sealed too. Run manually or
    on boot when a rotation happened; cheap at this table size."""
    resealed = 0
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, phone, full_name, wallet_address, wallet_idx, "
            "email_idx FROM users").fetchall()
        for r in rows:
            # NULL stays NULL under rotation too (review batch, Sep 27):
            # reencrypting a coalesced "" would mint a shared empty-email
            # identity across every no-email user.
            vals = {c: r[c] for c in ENCRYPTED_USER_COLS}
            if not any(str(v).startswith(_fc.PREFIX) for v in vals.values()):
                continue
            new = {c: (_fc.reencrypt(vals[c]) if vals[c] is not None
                       else None) for c in ENCRYPTED_USER_COLS}
            w_idx = (
                None if new["wallet_address"] is None else
                _fc.blind_index(
                    _fc.decrypt(new["wallet_address"]) if str(
                        new["wallet_address"]).startswith(_fc.PREFIX) else ""))
            e_idx = (
                None if new["email"] is None else
                _fc.blind_index(
                    _fc.decrypt(new["email"]).strip().lower() if str(
                        new["email"]).startswith(_fc.PREFIX) else "",
                    domain="email"))
            if any(new[c] != vals[c] for c in ENCRYPTED_USER_COLS) or \
                    w_idx != (r["wallet_idx"] or "") or \
                    e_idx != (r["email_idx"] or ""):
                conn.execute(
                    "UPDATE users SET email=?, phone=?, full_name=?, "
                    "wallet_address=?, wallet_idx=?, email_idx=? WHERE id=?",
                    (new["email"], new["phone"], new["full_name"],
                     new["wallet_address"], w_idx, e_idx, r["id"]))
                resealed += 1
    return {"rows_resealed": resealed}


def get_setting(key, default=None, conn=None):
    # `conn` joins the caller's OPEN transaction (Oct 26): a credit that
    # must commit its balance with its claim cannot open a second
    # connection — SQLite would block it on the writer lock.
    if conn is not None:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    with get_db() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value, conn=None):
    sql = ("INSERT INTO settings (key, value) VALUES (?, ?) "
           "ON CONFLICT(key) DO UPDATE SET value = ?")
    args = (key, str(value), str(value))
    if conn is not None:
        conn.execute(sql, args)
        return
    with get_db() as c:
        c.execute(sql, args)


def safe_pos_int(raw, fallback=100):
    """THE RAFFLE UNIT RULE'S GUARD (Oct 12 controller): the raffle unit
    size is a display contract (every valid share = 1 unit, N units =
    one complete ticket) — a corrupt setting must never zero the
    requirement (or the unit counter) for every visitor. Clamps to a
    positive int, falls back to the caller's default otherwise."""
    try:
        v = int(float(raw))
        return v if v > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def get_or_create_miner(wallet_address, worker_name=""):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM miners WHERE wallet_address = ?", (wallet_address,)
        ).fetchone()
        if row:
            return dict(row)
        conn.execute(
            "INSERT INTO miners (wallet_address, worker_name) VALUES (?, ?)",
            (wallet_address, worker_name),
        )
        row = conn.execute(
            "SELECT * FROM miners WHERE wallet_address = ?", (wallet_address,)
        ).fetchone()
        return dict(row)


def get_miner_by_wallet(wallet_address):
    """READ-ONLY miner lookup by WALLET: no mint. The poll routes use
    this instead of get_or_create_miner because every unauthenticated
    reader polling a wallet that has not yet submitted a share was
    minting a zombie miners row every poll cycle (Oct 29: the R111
    playtest resurrect loop came straight through here).

    NAMED get_miner_by_wallet, not get_miner: get_miner(miner_id)
    already exists below (jackpot_engine's winner lookup), and a
    duplicate name silently shadowed one side of it."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM miners WHERE wallet_address = ?", (wallet_address,)
        ).fetchone()
        return dict(row) if row else None


def update_miner_stats(wallet_address, shares_valid=0, shares_invalid=0, hashrate=0):
    with get_db() as conn:
        conn.execute(
            """UPDATE miners SET
                shares_valid = shares_valid + ?,
                shares_invalid = shares_invalid + ?,
                shares_total = shares_total + ? + ?,
                hashrate = ?,
                last_seen = strftime('%s','now')
            WHERE wallet_address = ?""",
            (shares_valid, shares_invalid, shares_valid, shares_invalid, hashrate, wallet_address),
        )


def add_tickets(miner_id, ticket_type, count, cost_xmr=0, drawing_id=None,
                source=""):
    """Mint tickets. `source` separates granted entries (an unlock
    reward) from shares-converted ones, so a granted ticket never eats
    the next share conversion."""
    with get_db() as conn:
        for _ in range(count):
            conn.execute(
                "INSERT INTO tickets (miner_id, ticket_type, cost_xmr, "
                "drawing_id, source) VALUES (?, ?, ?, ?, ?)",
                (miner_id, ticket_type, cost_xmr, drawing_id, source),
            )


def get_ticket_count(drawing_id=None):
    with get_db() as conn:
        if drawing_id:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id = ?", (drawing_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id IS NULL"
            ).fetchone()
        return row["cnt"]


def get_miner_ticket_count(miner_id, drawing_id=None):
    with get_db() as conn:
        if drawing_id:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM tickets WHERE miner_id = ? AND drawing_id = ?",
                (miner_id, drawing_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM tickets WHERE miner_id = ? AND drawing_id IS NULL",
                (miner_id,),
            ).fetchone()
        return row["cnt"]


def create_drawing(jackpot_amount, scheduled_for):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO drawings (jackpot_amount, scheduled_for) VALUES (?, ?)",
            (jackpot_amount, scheduled_for),
        )
        return cur.lastrowid


def assign_tickets_to_drawing(drawing_id):
    """Assign all unassigned tickets to a drawing."""
    with get_db() as conn:
        conn.execute(
            "UPDATE tickets SET drawing_id = ? WHERE drawing_id IS NULL",
            (drawing_id,),
        )
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE drawing_id = ?", (drawing_id,)
        ).fetchone()
        return row["cnt"]


def finalize_drawing(drawing_id, winner_id, winning_ticket_id, house_tax, block_hash, commit_seed, reveal_seed, total_tickets=0):
    """THE FINALIZATION CHOKE POINT, now self-defending (Sep 26): the
    live DB once carried drawn drawings whose winning_ticket_id was 0
    — impossible from any AUTOINCREMENT ring, so some writer fabricated
    the settlement (the code predates this repo's history; no path in
    git ever wrote the zero). The guard runs BEFORE any write, on the
    same connection: a drawing id that does not exist, a winner that is
    not a real miner, or a winning ticket that is not a real ticket of
    THIS drawing aborts with ValueError and the drawing stays pending —
    a fabricated settlement can never be persisted by any caller,
    current or future."""
    with get_db() as conn:
        if not conn.execute(
                "SELECT 1 FROM drawings WHERE id = ?", (drawing_id,)
        ).fetchone():
            raise ValueError(
                f"finalize_drawing: drawing {drawing_id} does not exist")
        if not conn.execute(
                "SELECT 1 FROM miners WHERE id = ?", (winner_id,)
        ).fetchone():
            raise ValueError(
                f"finalize_drawing: winner_id {winner_id} is not a real miner")
        if not conn.execute(
                "SELECT 1 FROM tickets WHERE id = ? AND drawing_id = ?",
                (winning_ticket_id, drawing_id),
        ).fetchone():
            raise ValueError(
                f"finalize_drawing: winning_ticket_id {winning_ticket_id} is "
                f"not a real ticket of drawing {drawing_id} "
                f"(ticket 0 never exists)")
        conn.execute(
            """UPDATE drawings SET
                winner_id = ?,
                winning_ticket_id = ?,
                house_tax = ?,
                block_hash = ?,
                commit_seed = ?,
                reveal_seed = ?,
                total_tickets = ?,
                status = 'drawn',
                drawn_at = strftime('%s','now'),
                claim_status = 'pending'
            WHERE id = ?""",
            (winner_id, winning_ticket_id, house_tax, block_hash, commit_seed, reveal_seed, total_tickets, drawing_id),
        )


def set_drawing_claim_deadline(drawing_id, deadline_ts):
    """Set the 7-day claim deadline for a drawing."""
    with get_db() as conn:
        conn.execute(
            "UPDATE drawings SET claim_deadline = ? WHERE id = ?",
            (deadline_ts, drawing_id),
        )


def set_drawing_winner_wallet(drawing_id, wallet_address):
    """Store the winner's wallet address on the drawing for quick lookup."""
    with get_db() as conn:
        conn.execute(
            "UPDATE drawings SET winner_wallet = ? WHERE id = ?",
            (wallet_address, drawing_id),
        )


def set_miner_coin(wallet_address, coin):
    """Stamp WHICH COIN this miner bot mines ('xmr' | 'vrsc'). Written
    by the Verus relay path at first accepted share; reads of the field
    default to 'xmr'. Also fixes an accidental flip: an existing row
    keeps its coin unless the caller is the coin's own pipeline."""
    coin = (coin or "").strip().lower()
    if coin not in ("xmr", "vrsc"):
        return False
    with get_db() as conn:
        conn.execute("UPDATE miners SET coin = ? WHERE wallet_address = ?",
                     (coin, wallet_address))
    return True


def acquire_claim_lease(drawing_id, steal_after_s=300):
    """THE CLAIM LEASE (Oct 28): atomically reserve a drawing for one
    claim. Two claims arriving together (double-click, fast retry, two
    screens) must not both pass the claim_status check and both mint a
    payout — this is ONE conditional UPDATE (pending -> 'claiming'), so
    the first writer owns the row and the loser's UPDATE matches 0
    rows. Returns True when THIS call holds the lease. A lease older
    than steal_after_s is a claimant that died mid-flight: the steal
    hands the drawing back to a live claimant instead of stranding it.
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE drawings SET claim_status = 'claiming', "
            "claim_leased_at = strftime('%s','now') "
            "WHERE id = ? AND (IFNULL(claim_status, 'pending') = 'pending' "
            "OR (claim_status = 'claiming' "
            "AND IFNULL(claim_leased_at, 0) > 0 "
            "AND strftime('%s','now') - claim_leased_at > ?))",
            (drawing_id, steal_after_s),
        )
        return cur.rowcount == 1


def release_claim_lease(drawing_id):
    """Return an abandoned lease to 'pending'. Only the lease owner's
    own state ('claiming' with no payout minted) is touched: a drawing
    already claimed or expired by someone else is left alone."""
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE drawings SET claim_status = 'pending', "
            "claim_leased_at = NULL "
            "WHERE id = ? AND claim_status = 'claiming'",
            (drawing_id,),
        )
        return cur.rowcount == 1


def claim_drawing(drawing_id, wallet_address, coin=None):
    """Mark a drawing as claimed by the winner. `coin` stamps WHICH
    coin the prize was claimed in (the winner's own miner coin, Oct 28);
    absent means the drawing's default 'xmr'.

    THE LEASE-BEARER CLAIM (Oct 28): ONLY the flight that holds the
    claim lease may flip a drawing to 'claimed'. The flip is gated on
    claim_status = 'claiming' — the one state ONLY acquire_claim_lease
    can create — and releases the lease on success. A caller WITHOUT
    the lease matches zero rows and returns False: the row it just
    read as 'pending' stays 'pending', the money it meant to mint is
    not minted against a drawing another thread has already claimed,
    expired or leased. `wallet_address` is not touched by the gate;
    the stamping write lives with the flip.
    """
    with get_db() as conn:
        if coin:
            cur = conn.execute(
                "UPDATE drawings SET claim_status = 'claimed', "
                "claimed_at = strftime('%s','now'), coin = ?, "
                "claim_leased_at = NULL "
                "WHERE id = ? AND claim_status = 'claiming'",
                (coin, drawing_id),
            )
        else:
            cur = conn.execute(
                "UPDATE drawings SET claim_status = 'claimed', "
                "claimed_at = strftime('%s','now'), "
                "claim_leased_at = NULL "
                "WHERE id = ? AND claim_status = 'claiming'",
                (drawing_id,),
            )
        return cur.rowcount == 1


def expire_drawing(drawing_id):
    """Mark a drawing as expired (jackpot returned to pool).

    THE LEASE-BEARER EXPIRY (Oct 28): the pump-side caller (the claim
    sweep, the pre-lease deadline branch) may retire ONLY a claim
    flight that has not started — the flip is CASed on
    claim_status = 'pending' (COALESCE covers a legacy NULL). A row
    someone is currently CLAIMING is not this caller's to touch: a
    rowcount of 0 means the flight is leased (or already claimed or
    expired) — check downstream, don't clobber. The lease holder's own
    expiry route goes through expire_leased_drawing.
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE drawings SET claim_status = 'expired', "
            "claim_leased_at = NULL "
            "WHERE id = ? "
            "AND COALESCE(claim_status, 'pending') = 'pending'",
            (drawing_id,),
        )
        return cur.rowcount == 1


def expire_leased_drawing(drawing_id):
    """EXPIRE A DRAWING THE CALLER HOLDS THE LEASE ON (Oct 28): the one
    writer allowed to flip 'claiming' -> 'expired'. A claim flight that
    acquired the lease re-checks the claim window AFTER its lease, and
    an overdue window retires the drawing in the holder's name — the
    lease precondition IS the ownership proof. Without the lease the
    flip matches zero rows and returns False: no lease, no rollover,
    the drawing stays exactly as the next gate finds it.
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE drawings SET claim_status = 'expired', "
            "claim_leased_at = NULL "
            "WHERE id = ? AND claim_status = 'claiming'",
            (drawing_id,),
        )
        return cur.rowcount == 1


def get_unclaimed_drawings():
    """Get all drawings that are drawn but not yet claimed or expired."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT d.*, m.wallet_address as winner_wallet_addr
               FROM drawings d
               LEFT JOIN miners m ON d.winner_id = m.id
               WHERE d.status = 'drawn' AND d.claim_status = 'pending'
               ORDER BY d.drawn_at""",
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_claim_for_wallet(wallet_address):
    """Check if a wallet address has a pending (unclaimed) drawing win."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT d.*, m.wallet_address as winner_wallet_addr
               FROM drawings d
               JOIN miners m ON d.winner_id = m.id
               WHERE d.status = 'drawn' AND d.claim_status = 'pending'
                 AND m.wallet_address = ?
               ORDER BY d.drawn_at DESC LIMIT 1""",
            (wallet_address,),
        ).fetchone()
        return dict(row) if row else None


def get_all_miners():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM miners ORDER BY shares_total DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_miner(miner_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM miners WHERE id = ?", (miner_id,)).fetchone()
        return dict(row) if row else None


def get_recent_drawings(limit=10):
    """Recent drawings, with the winner's MINING wallet on winner_wallet.

    drawings gained its own winner_wallet column (the claim address), so
    `d.*` plus an alias of the same name handed callers two columns under
    one key - sqlite answered with the claim address, which is NULL until
    a prize is claimed, and every winner rendered blank. The join is
    aliased separately here and merged explicitly, preferring the miner
    that actually won.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT d.*, m.wallet_address as miner_wallet,
                      COALESCE(m.is_demo, 0) as miner_is_demo,
                      COALESCE(m.coin, 'xmr') as miner_coin
               FROM drawings d
               LEFT JOIN miners m ON d.winner_id = m.id
               ORDER BY d.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["winner_wallet"] = d.pop("miner_wallet") or d.get("winner_wallet")
            # A drawing won by a simulated account must be labelable as
            # such: seeded history is display data, not a real payout.
            d["winner_is_demo"] = bool(d.pop("miner_is_demo", 0) or 0)
            # WHICH COIN the winner talked to the pool with (Oct 28).
            # After the claim the drawing's own coin is the truth of
            # what was PAID; BEFORE the claim nothing is paid yet, so the
            # miner's coin is the prize-in-prospect the surface may show
            # (a quantity must still wait for the claim's live rate).
            d["winner_coin"] = (
                (d.get("coin") or "")
                if str(d.get("claim_status") or "") == "claimed"
                else ""
            ) or d.pop("miner_coin", "xmr") or "xmr"
            out.append(d)
        return out


def add_payout(miner_id, amount_xmr, payout_type, drawing_id=None,
               coin="xmr", amount_coin=None,
               fx_xmr_usd=None, fx_vrsc_usd=None):
    """Mint one payout row. `coin` is the coin the payout was DENOMINATED
    in ('xmr' default; 'vrsc' for a same-coin Verus prize, Oct 28) and
    `amount_coin` that quantity; amount_xmr stays the XMR-equivalent so
    every existing reader of the ledger is unchanged.

    THE FX-AT-MINT STAMP (Oct 28): a same-coin row that was PRICED by
    the conversion carries the exact rates that priced it — fx_xmr_usd
    and fx_vrsc_usd, captured verbatim at mint time. Reconciliation
    reprices amount_coin from the row itself (fx_xmr_usd / fx_vrsc_usd
    x amount_xmr) and can name the book the treasury was charged
    against, instead of trusting whatever the rate feeds are reading
    TODAY."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO payouts (miner_id, amount_xmr, payout_type, "
            "drawing_id, coin, amount_coin, fx_xmr_usd, fx_vrsc_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (miner_id, amount_xmr, payout_type, drawing_id, coin or "xmr",
             amount_coin, fx_xmr_usd, fx_vrsc_usd),
        )
        return cur.lastrowid


def update_payout_status(payout_id, status, tx_hash=None):
    with get_db() as conn:
        if tx_hash:
            conn.execute(
                "UPDATE payouts SET status = ?, tx_hash = ? WHERE id = ?",
                (status, tx_hash, payout_id),
            )
        else:
            conn.execute(
                "UPDATE payouts SET status = ? WHERE id = ?", (status, payout_id)
            )


def get_pending_payouts():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payouts WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  USER / AUTH FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def create_user(username, password_hash, email=None, wallet_address=None, is_admin=False,
                security_question=None, security_answer=None,
                phone=None, full_name=None):
    """PII columns are sealed at rest; email uniqueness is enforced via
    the KEYED BLIND INDEX (email_idx) instead of the sealed column —
    equality semantics preserved, ciphertext never compared (equal
    plaintexts share an index value; different keys/index keys do not
    collide meaningfully)."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO users (username, email, password_hash, wallet_address,
               wallet_idx, email_idx, is_admin, security_question,
               security_answer, phone, full_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (username,
             # absent email stays NULL: the column keeps its UNIQUE
             # constraint, and '' would collide across no-email users
             _fc.encrypt(email) if email else None,
             password_hash,
             _fc.encrypt(wallet_address or ""),
             _fc.blind_index(wallet_address or ""),
             _fc.blind_index((email or "").strip().lower(), domain="email"),
             1 if is_admin else 0,
             security_question, security_answer,
             _fc.encrypt(phone or ""), _fc.encrypt(full_name or "")),
        )
        return cur.lastrowid


def email_exists(email) -> bool:
    """Duplicate-email check via the keyed blind index: deterministic
    under the active key, opaque at rest, and case-insensitive on the
    local part (matches the old UNIQUE column's practical behavior)."""
    if not email:
        return False
    idx = _fc.blind_index(str(email).strip().lower(), domain="email")
    if not idx:
        return False
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE email_idx = ? LIMIT 1",
            (idx,)).fetchone()
    return row is not None


def get_user_by_username(username):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return _seal_user_row(dict(row)) if row else None


def get_user(user_id):
    """Row read with transparent PII decryption."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?",
                           (user_id,)).fetchone()
    return _seal_user_row(dict(row)) if row else None


def get_user_by_email(email):
    """Equality lookup by email via the KEYED BLIND INDEX (users.email_idx).

    The email column is sealed at rest (see create_user), so a plain
    WHERE email = ? would compare ciphertext and never match. The index
    is keyed and case-folded, which is exactly the referral path's
    semantics: the friend types the referrer's address, we hash it.
    """
    if not email:
        return None
    idx = _fc.blind_index(str(email).strip().lower(), domain="email")
    if not idx:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email_idx = ? LIMIT 1", (idx,)
        ).fetchone()
    return _seal_user_row(dict(row)) if row else None


def get_user_by_wallet(wallet_address):
    """Equality lookup on an encrypted column via the KEYED BLIND INDEX
    (users.wallet_idx): deterministic under the active key, opaque at
    rest. Empty/None wallets never match."""
    if not wallet_address:
        return None
    idx = _fc.blind_index(str(wallet_address))
    if not idx:
        return None                                  # crypto unavailable
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE wallet_idx = ?", (idx,)).fetchone()
    return _seal_user_row(dict(row)) if row else None


def find_user_by_wallet(wallet_address):
    """Wallet -> account, with the no-key fallback.

    The blind index is empty when crypto is unavailable, so the unlock
    path (which runs once per miner-day at most) falls back to a scan of
    the decrypted wallet column rather than silently missing the account.
    """
    user = get_user_by_wallet(wallet_address)
    if user or not wallet_address:
        return user
    target = str(wallet_address)
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    for row in rows:
        candidate = _seal_user_row(dict(row))
        if (candidate.get("wallet_address") or "") == target:
            return candidate
    return None


def generate_referral_code(username):
    """Generate a unique referral code from username."""
    import secrets
    code = username.lower().replace(" ", "")[:8] + secrets.token_hex(3)
    return code


def get_user_by_referral_code(code):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE referral_code = ?", (code,)
        ).fetchone()
    return _seal_user_row(dict(row)) if row else None


def set_referral_code(user_id, code):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET referral_code = ? WHERE id = ?", (code, user_id)
        )


def set_kraken_referral_id(user_id, referral_id):
    """Attach (or replace) the user's Kraken referral id — the value
    the operator's source returns, mapped through the admin UI.
    Normalized: trimmed, case-folded to upper (Kraken ids are upper),
    empty string clears."""
    rid = str(referral_id or "").strip().upper()
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET kraken_referral_id = ? WHERE id = ?",
            (rid, user_id))
    return rid


def clear_kraken_referral_id(user_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET kraken_referral_id = '' WHERE id = ?",
            (user_id,))


def set_referred_by(user_id, referrer_code):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET referred_by = ? WHERE id = ?", (referrer_code, user_id)
        )


def increment_referral_count(referrer_code):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET referral_count = referral_count + 1 WHERE referral_code = ?",
            (referrer_code,)
        )


# ─── REFERRAL ENTRIES (Oct 25) ───
# The stated programme, in code: a referral pays ONE ENTRY TO EACH SIDE
# at signup, attributed by the referrer's email (the referral code stays
# as the legacy link form), capped at referral_cycle_cap awards per
# referrer per jackpot cycle. Entries only — a referral never pays pot
# money and never buys a share of the jackpot.


def referral_cycle_anchor():
    """The boundary a "jackpot cycle" counts from: the drawn_at of the
    most recent drawing, or 0 when nothing has been drawn yet."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(drawn_at), 0) AS anchor FROM drawings"
        ).fetchone()
    try:
        return int((row["anchor"] if row else 0) or 0)
    except (TypeError, ValueError):
        return 0


def _grant_or_hold_referral_entry(user_id, tickets):
    """Give a user their referral entry, or hold it until a wallet exists.

    A ticket belongs to a miner and a miner belongs to a payout wallet, so
    an account without one cannot hold a ticket yet. It keeps the entry on
    the account (pending_referral_entries) and claim_referral_entries()
    mints it the moment a wallet lands.
    """
    tickets = max(0, int(tickets or 0))
    if tickets <= 0:
        return "none"
    user = get_user(user_id) or {}
    wallet = (user.get("wallet_address") or "").strip()
    if wallet:
        miner = get_or_create_miner(wallet)
        add_tickets(miner["id"], "free", tickets, source="referral")
        return "granted"
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET pending_referral_entries = "
            "pending_referral_entries + ? WHERE id = ?", (tickets, user_id))
    return "held"


def award_referral_entries(referrer_id, referred_user_id, tickets,
                           method="email", cap=None):
    """Pay one entry to each side of a referral, once, inside the cap.

    Idempotent by construction: referral_awards.referred_user_id is
    UNIQUE, so a second call for the same friend returns "already" and
    mints nothing. The per-cycle limit counts awards for the referrer
    whose cycle_anchor equals the current one — when the next drawing
    lands the anchor moves and the allowance resets.

    Returns a status dict; callers treat "capped"/"already"/"self" as
    normal, not errors: the signup that triggered it still succeeds.
    """
    try:
        referrer_id = int(referrer_id)
        referred_user_id = int(referred_user_id)
    except (TypeError, ValueError):
        return {"status": "invalid"}
    if referrer_id <= 0 or referred_user_id <= 0:
        return {"status": "invalid"}
    if referrer_id == referred_user_id:
        return {"status": "self"}
    # Both accounts must exist: a ledger row pointing at a missing user
    # could never be granted or audited, so it is refused here.
    if not get_user(referrer_id) or not get_user(referred_user_id):
        return {"status": "unknown_user"}
    tickets = max(0, int(tickets or 0))

    if cap is None:
        try:
            cap = int(get_setting("referral_cycle_cap", "10"))
        except (TypeError, ValueError):
            cap = 10
    cap = max(0, cap)
    cycle = referral_cycle_anchor()

    with get_db() as conn:
        if conn.execute(
                "SELECT 1 FROM referral_awards WHERE referred_user_id = ?",
                (referred_user_id,)).fetchone():
            return {"status": "already", "cycle_anchor": cycle}
        used = int(conn.execute(
            "SELECT COUNT(*) AS n FROM referral_awards WHERE referrer_id = ? "
            "AND cycle_anchor = ?", (referrer_id, cycle)).fetchone()["n"] or 0)
        if used >= cap:
            return {"status": "capped", "cap": cap, "used": used,
                    "cycle_anchor": cycle}
        try:
            cur = conn.execute(
                "INSERT INTO referral_awards (referrer_id, referred_user_id, "
                "method, cycle_anchor, tickets) VALUES (?, ?, ?, ?, ?)",
                (referrer_id, referred_user_id, str(method or "email"),
                 cycle, tickets))
            award_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return {"status": "already", "cycle_anchor": cycle}

    referrer_state = _grant_or_hold_referral_entry(referrer_id, tickets)
    referred_state = _grant_or_hold_referral_entry(referred_user_id, tickets)
    with get_db() as conn:
        conn.execute(
            "UPDATE referral_awards SET referrer_granted = ?, "
            "referred_granted = ? WHERE id = ?",
            (1 if referrer_state == "granted" else 0,
             1 if referred_state == "granted" else 0, award_id))
    try:
        add_audit_log(
            referrer_id, "referral_entry",
            f"referred user {referred_user_id} via {method or 'email'}: "
            f"{tickets} tickets each ({referrer_state}/{referred_state}, "
            f"cycle {cycle}, {used + 1}/{cap})")
    except Exception:
        pass
    return {"status": "awarded", "award_id": award_id, "tickets": tickets,
            "cycle_anchor": cycle, "used": used + 1, "cap": cap,
            "referrer": referrer_state, "referred": referred_state}


def claim_referral_entries(user_id):
    """Turn held referral entries into real tickets once a wallet exists.

    Same shape as claim_pending_entries(): a conditional UPDATE is the
    claim, so only one caller can win the transition and a ticket is
    never minted twice.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT pending_referral_entries FROM users WHERE id = ?",
            (user_id,)).fetchone()
    held = int((row["pending_referral_entries"] if row else 0) or 0)
    if held <= 0:
        return {"claimed": 0, "held": 0}
    user = get_user(user_id) or {}
    wallet = (user.get("wallet_address") or "").strip()
    if not wallet:
        return {"claimed": 0, "held": held}
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE users SET pending_referral_entries = 0 "
            "WHERE id = ? AND pending_referral_entries = ?", (user_id, held))
        won = cur.rowcount == 1
    if not won:
        return {"claimed": 0, "held": 0}
    miner = get_or_create_miner(wallet)
    add_tickets(miner["id"], "free", held, source="referral")
    try:
        add_audit_log(user_id, "held_referral_claimed",
                      f"{held} held referral entries -> {wallet[:16]}...")
    except Exception:
        pass
    return {"claimed": held, "held": 0}


def referral_award_summary(user_id):
    """This cycle's referral awards for the dashboard: used, cap, and the
    entries still held for want of a wallet."""
    cycle = referral_cycle_anchor()
    try:
        cap = max(0, int(get_setting("referral_cycle_cap", "10")))
    except (TypeError, ValueError):
        cap = 10
    with get_db() as conn:
        used = int(conn.execute(
            "SELECT COUNT(*) AS n FROM referral_awards WHERE referrer_id = ? "
            "AND cycle_anchor = ?", (user_id, cycle)).fetchone()["n"] or 0)
        row = conn.execute(
            "SELECT pending_referral_entries FROM users WHERE id = ?",
            (user_id,)).fetchone()
    held = int((row["pending_referral_entries"] if row else 0) or 0)
    return {"awards_this_cycle": used, "cycle_cap": cap,
            "entries_held": held, "cycle_anchor": cycle,
            "remaining": max(0, cap - used)}


# ─── THE XMRJ AWARD (Oct 25, internal programme) ───
# The reward unit is an INTEGER, exactly like the treasury program's
# record_inflow_xmrj(amount: u64) — that program's own arithmetic, so a
# number accrued here can be booked there unchanged when it ships. No
# token, no mint, no chain call: this module only ever writes a ledger.
XMRJ_RATE_KEY = "referral_xmrj_award"
KRAKEN_SHARE_KEY = "kraken_pool_share_pct"
DEFAULT_KRAKEN_SHARE_PCT = 50


def referral_xmrj_rate():
    """XMRJ per successful referral. 0 (the default) means the operator
    has not set a rate yet — nothing is awarded and no surface invents
    one."""
    try:
        return max(0, int(float(get_setting(XMRJ_RATE_KEY, "0"))))
    except (TypeError, ValueError):
        return 0


def solana_wallet_for_user(user_id):
    """The user's connected Solana wallet — the identity_token the Scout
    activation flow records (scout_identities.identity_token). Empty when
    the account has not connected one: the award still accrues, it just
    has nowhere to be paid yet."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return ""
    with get_db() as conn:
        row = conn.execute(
            "SELECT identity_token FROM scout_identities WHERE user_id = ? "
            "ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    return str((row["identity_token"] if row else "") or "").strip()


def award_referral_xmrj(referrer_id, referred_user_id, amount=None):
    """Accrue the internal programme's XMRJ reward for one referral.

    Idempotent by construction: referral_xmrj_awards.referred_user_id is
    UNIQUE, so the same friend can never pay twice. `amount` defaults to
    the operator's setting; a rate of 0 is "unconfigured" and writes
    nothing rather than inventing a number.

    The wallet is snapshotted when one is already connected, and resolved
    live at display time otherwise — connecting a wallet later must never
    mean re-running the award.
    """
    try:
        referrer_id = int(referrer_id)
        referred_user_id = int(referred_user_id)
    except (TypeError, ValueError):
        return {"status": "invalid"}
    if referrer_id <= 0 or referred_user_id <= 0:
        return {"status": "invalid"}
    if referrer_id == referred_user_id:
        return {"status": "self"}
    if amount is None:
        amount = referral_xmrj_rate()
    try:
        amount = max(0, int(amount))
    except (TypeError, ValueError):
        return {"status": "invalid"}
    if amount <= 0:
        return {"status": "unconfigured", "amount": 0}
    if not get_user(referrer_id) or not get_user(referred_user_id):
        return {"status": "unknown_user"}

    wallet = solana_wallet_for_user(referrer_id)
    with get_db() as conn:
        if conn.execute(
                "SELECT 1 FROM referral_xmrj_awards WHERE referred_user_id "
                "= ?", (referred_user_id,)).fetchone():
            return {"status": "already", "amount": amount}
        try:
            cur = conn.execute(
                "INSERT INTO referral_xmrj_awards (referrer_id, "
                "referred_user_id, amount, solana_wallet, status) "
                "VALUES (?, ?, ?, ?, 'accrued')",
                (referrer_id, referred_user_id, amount, wallet))
            award_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return {"status": "already", "amount": amount}
    try:
        add_audit_log(
            referrer_id, "referral_xmrj",
            f"referred user {referred_user_id}: {amount} XMRJ accrued "
            f"(wallet {'set' if wallet else 'not connected yet'})")
    except Exception:
        pass
    return {"status": "awarded", "award_id": award_id, "amount": amount,
            "wallet": wallet}


def referral_xmrj_summary(user_id):
    """The internal card's numbers: the rate, what this account has
    accrued, and the Solana wallet it would be paid to."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        uid = 0
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total "
            "FROM referral_xmrj_awards WHERE referrer_id = ?",
            (uid,)).fetchone()
    wallet = solana_wallet_for_user(uid)
    count = int((row["n"] if row else 0) or 0)
    total = int((row["total"] if row else 0) or 0)
    rate = referral_xmrj_rate()
    return {
        "xmrj_per_referral": rate,
        "xmrj_awarded": total,
        "xmrj_referrals": count,
        "xmrj_status": "accrued",
        "solana_wallet": wallet,
        "xmrj_payable": bool(wallet and total > 0),
    }


# ─── KRAKEN PAYOUTS THE OPERATOR RECORDS (Oct 25) ───
def kraken_pool_share_pct():
    """The pool's share of a recorded Kraken payout, in whole percent.
    Default 50: the referrer keeps the other half from Kraken directly."""
    try:
        pct = int(float(get_setting(KRAKEN_SHARE_KEY,
                                    str(DEFAULT_KRAKEN_SHARE_PCT))))
    except (TypeError, ValueError):
        pct = DEFAULT_KRAKEN_SHARE_PCT
    return min(100, max(0, pct))


def _referrer_of(referred_user_id):
    """The account that referred this user: the referral_awards ledger
    first (it is the award that actually paid), then users.referred_by
    (the code the signup recorded)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT referrer_id FROM referral_awards WHERE referred_user_id "
            "= ?", (int(referred_user_id),)).fetchone()
    if row:
        return get_user(int(row["referrer_id"]))
    user = get_user(int(referred_user_id)) or {}
    code = (user.get("referred_by") or "").strip()
    return get_user_by_referral_code(code) if code else None


def record_kraken_referral_payout(referred_user_id, gross_usd, note=""):
    """Record a Kraken referral payout the operator has verified.

    The pool keeps kraken_pool_share_pct of the gross (default half) and
    the referrer keeps the rest from Kraken directly — this function never
    pays a user out of pool funds. Idempotent per referred account per
    calendar month, so re-running a receipt cannot inflate the pot.

    Returns a status dict; the caller treats every non-'recorded' status
    as a refusal, never as a crash.
    """
    try:
        uid = int(referred_user_id)
        gross = float(gross_usd)
    except (TypeError, ValueError):
        return {"status": "invalid"}
    if gross <= 0:
        return {"status": "invalid"}
    if not get_user(uid):
        return {"status": "unknown_user"}
    referrer = _referrer_of(uid)
    if not referrer:
        return {"status": "no_referrer"}
    referrer_id = int(referrer["id"])
    if referrer_id == uid:
        return {"status": "self"}
    pct = kraken_pool_share_pct()
    pool_usd = round(gross * pct / 100.0, 2)
    if pool_usd <= 0:
        return {"status": "no_pool_share", "pct": pct}
    period = time.strftime("%Y-%m")
    with get_db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO kraken_referral_payouts (referred_user_id, "
                "referrer_id, gross_usd, pool_usd, pct, period, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, referrer_id, round(gross, 2), pool_usd, pct, period,
                 str(note or "")[:200]))
            payout_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return {"status": "already", "period": period,
                    "referrer_id": referrer_id}

    import jackpot_funding as funding          # local: funding imports us
    ledger_id = funding.record_funding(
        "referral_bonus", pool_usd, sponsor_id=referrer_id,
        source="kraken_referral_payout")
    if ledger_id is None:                      # refused — unwind the receipt
        with get_db() as conn:
            conn.execute("DELETE FROM kraken_referral_payouts WHERE id = ?",
                         (payout_id,))
        return {"status": "ledger_refused", "pool_usd": pool_usd}
    with get_db() as conn:
        conn.execute(
            "UPDATE kraken_referral_payouts SET ledger_id = ? WHERE id = ?",
            (int(ledger_id), payout_id))
    try:
        add_audit_log(
            referrer_id, "kraken_referral_payout",
            f"referred user {uid}: ${gross:.2f} gross recorded, "
            f"${pool_usd:.2f} to the pot ({pct}%), ledger {ledger_id}")
    except Exception:
        pass
    return {"status": "recorded", "id": payout_id,
            "referrer_id": referrer_id,
            "referrer_username": referrer["username"],
            "gross_usd": round(gross, 2), "pool_usd": pool_usd,
            "referrer_keeps_usd": round(gross - pool_usd, 2),
            "pct": pct, "period": period, "ledger_id": int(ledger_id)}


def kraken_payout_summary(limit=10):
    """The admin card's read: the recorded payouts, newest first, plus
    the totals they add up to. Read-only."""
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT p.*, ru.username AS referred_username, "
            "rr.username AS referrer_username FROM kraken_referral_payouts p "
            "LEFT JOIN users ru ON ru.id = p.referred_user_id "
            "LEFT JOIN users rr ON rr.id = p.referrer_id "
            "ORDER BY p.id DESC LIMIT ?", (int(limit),)).fetchall()]
        agg = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(gross_usd), 0) AS gross, "
            "COALESCE(SUM(pool_usd), 0) AS pool "
            "FROM kraken_referral_payouts").fetchone()
    return {
        "payouts": rows,
        "count": int((agg["n"] if agg else 0) or 0),
        "gross_usd": round(float((agg["gross"] if agg else 0) or 0), 2),
        "pool_usd": round(float((agg["pool"] if agg else 0) or 0), 2),
        "pool_share_pct": kraken_pool_share_pct(),
        "xmrj_per_referral": referral_xmrj_rate(),
    }


def referral_admin_meter(limit_referrers=10, limit_recent=10):
    """The operator's view of the referral programme for THIS cycle.

    The cap is PER REFERRER, so the meter is per referrer — one global bar
    would hide a single account sitting at 10 while everyone else is at
    zero. Returns the cycle's totals, the busiest referrers against the cap,
    every entry still held for want of a wallet, and the newest awards.
    """
    cycle = referral_cycle_anchor()
    try:
        cap = max(0, int(get_setting("referral_cycle_cap", "10")))
    except (TypeError, ValueError):
        cap = 10
    with get_db() as conn:
        tot = conn.execute(
            "SELECT COUNT(*) AS awards, COALESCE(SUM(tickets), 0) AS tickets "
            "FROM referral_awards WHERE cycle_anchor = ?",
            (cycle,)).fetchone()
        referrers = conn.execute(
            "SELECT ra.referrer_id, u.username, COUNT(*) AS awards, "
            "COALESCE(SUM(ra.tickets), 0) AS tickets "
            "FROM referral_awards ra "
            "LEFT JOIN users u ON u.id = ra.referrer_id "
            "WHERE ra.cycle_anchor = ? GROUP BY ra.referrer_id "
            "ORDER BY awards DESC, ra.referrer_id ASC LIMIT ?",
            (cycle, int(limit_referrers))).fetchall()
        held_row = conn.execute(
            "SELECT COALESCE(SUM(pending_referral_entries), 0) AS entries, "
            "COALESCE(SUM(CASE WHEN COALESCE(pending_referral_entries, 0) > 0 "
            "THEN 1 ELSE 0 END), 0) AS accounts FROM users").fetchone()
        recent = conn.execute(
            "SELECT ra.id, ra.referrer_id, ru.username AS referrer, "
            "ra.referred_user_id, du.username AS referred, ra.method, "
            "ra.tickets, ra.referrer_granted, ra.referred_granted, "
            "ra.created_at FROM referral_awards ra "
            "LEFT JOIN users ru ON ru.id = ra.referrer_id "
            "LEFT JOIN users du ON du.id = ra.referred_user_id "
            "ORDER BY ra.id DESC LIMIT ?", (int(limit_recent),)).fetchall()
    rows = []
    for r in referrers:
        used = int(r["awards"] or 0)
        rows.append({
            "referrer_id": r["referrer_id"],
            "username": r["username"] or "(deleted)",
            "awards": used,
            "tickets": int(r["tickets"] or 0),
            "cap": cap,
            "remaining": max(0, cap - used),
            "at_cap": used >= cap,
        })
    return {
        "cycle_anchor": cycle,
        "cap": cap,
        "awards_this_cycle": int(tot["awards"] or 0),
        "tickets_this_cycle": int(tot["tickets"] or 0),
        "referrers_this_cycle": len(rows),
        "referrers_at_cap": sum(1 for r in rows if r["at_cap"]),
        "entries_held": int(held_row["entries"] or 0),
        "accounts_holding": int(held_row["accounts"] or 0),
        "top_referrers": rows,
        "recent": [dict(r) for r in recent],
    }


def get_referral_stats(user_id):
    """Get referral stats for a user."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT referral_code, referral_count, referred_by FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return None

        # Count referred users who have active miners. THE CIPHERTEXT
        # JOIN FIX (review batch, Sep 27 major): users.wallet_address is
        # SEALED at rest, so a SQL join against the miners' plaintext
        # column can never match (active_referrals read 0 forever). The
        # match now happens in Python via the same KEYED BLIND INDEX
        # the wallet lookups use: resolve each referred user's
        # ciphertext here, blind-index the plaintext, match the stored
        # wallet_idx values.
        referred = conn.execute(
            """SELECT id, wallet_address FROM users
            WHERE referred_by = ? AND wallet_address IS NOT NULL""",
            (row["referral_code"],)
        ).fetchall()
        referred_idx = set()
        for ru in referred:
            ct = ru["wallet_address"]
            try:
                plain = _fc.decrypt(ct) if str(ct).startswith(_fc.PREFIX) else ct
            except Exception:
                plain = ""                 # undecryptable: never counted
            if plain:
                referred_idx.add(_fc.blind_index(plain))
        active_referrals = {"cnt": 0}
        if referred_idx:
            # the miners table has NO wallet_idx column (the blind index
            # lives on users) - the miners' PLAINTEXT wallet addresses
            # are matched in Python: blind-index each active miner's
            # address and intersect with the referred users' indexes
            _active = conn.execute(
                """SELECT DISTINCT wallet_address FROM miners
                WHERE shares_valid > 0 AND wallet_address IS NOT NULL""").fetchall()
            hit = sum(
                1 for r in _active
                if _fc.blind_index(str(r["wallet_address"]))
                in referred_idx)
            active_referrals = {"cnt": hit}

        return {
            "referral_code": row["referral_code"],
            "referral_count": row["referral_count"],
            "active_referrals": active_referrals["cnt"] if active_referrals else 0,
            "referred_by": row["referred_by"],
        }


def mark_signup_bonus_credited(user_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET signup_bonus_credited = 1 WHERE id = ?", (user_id,)
        )


def get_all_users():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, email, wallet_address, is_admin, display_name, avatar, "
            "kraken_referral_id, "
            "total_earned, total_spins, total_jackpots, created_at, last_login FROM users ORDER BY created_at DESC"
        ).fetchall()
        return [_seal_user_row(dict(r)) for r in rows]


def update_user_profile(user_id, display_name=None, avatar=None, bio=None, wallet_address=None):
    with get_db() as conn:
        user = get_user(user_id)
        if not user:
            return
        conn.execute(
            """UPDATE users SET
                display_name = ?,
                avatar = ?,
                bio = ?,
                wallet_address = ?,
                wallet_idx = ?
            WHERE id = ?""",
            (
                display_name if display_name is not None else user["display_name"],
                avatar if avatar is not None else user["avatar"],
                bio if bio is not None else user["bio"],
                _fc.encrypt(wallet_address) if wallet_address is not None
                else user["wallet_address"],
                _fc.blind_index(wallet_address) if wallet_address is not None
                else (user.get("wallet_idx") or ""),
                user_id,
            ),
        )
    if wallet_address:
        # A held unlock entry becomes a real ticket the moment the
        # account gets a wallet (Oct 24). Idempotent — no hold, no-op.
        claim_pending_entries(user_id)
        # Same deal for a held referral entry (Oct 25).
        claim_referral_entries(user_id)


def update_user_password(user_id, new_password_hash):
    """Update a user's password (used for security question reset)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_password_hash, user_id),
        )


def verify_security_answer(user_id, answer):
    """Verify a security question answer. Returns True if correct."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT security_answer FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row or not row["security_answer"]:
            return False
        return row["security_answer"].strip().lower() == answer.strip().lower()


def get_security_question(user_id):
    """Get a user's security question (for the reset flow)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT security_question FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["security_question"] if row else None


# ─── Comments ────────────────────────────────────────────────

def add_comment(name, email, message, ip_address=None):
    """Add a comment/message from a site visitor."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO comments (name, email, message, ip_address) VALUES (?, ?, ?, ?)",
            (name, email, message, ip_address),
        )
        return cur.lastrowid


def get_comments(limit=100, status=None):
    """Get comments for admin dashboard. Optionally filter by status."""
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM comments WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM comments ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def update_comment_status(comment_id, status):
    """Mark a comment as read, archived, etc."""
    with get_db() as conn:
        conn.execute(
            "UPDATE comments SET status = ? WHERE id = ?",
            (status, comment_id),
        )


def update_user_stats(user_id, earned=None, spins=None, jackpots=None):
    with get_db() as conn:
        user = get_user(user_id)
        if not user:
            return
        conn.execute(
            """UPDATE users SET
                total_earned = ?,
                total_spins = ?,
                total_jackpots = ?
            WHERE id = ?""",
            (
                earned if earned is not None else user["total_earned"],
                spins if spins is not None else user["total_spins"],
                jackpots if jackpots is not None else user["total_jackpots"],
                user_id,
            ),
        )


def update_last_login(user_id):
    with get_db() as conn:
        conn.execute("UPDATE users SET last_login = strftime('%s','now') WHERE id = ?", (user_id,))


# ═══════════════════════════════════════════════════════════════
#  GAMIFICATION: XP, LEVELS, BADGES, STREAKS, SUPER TICKETS
# ═══════════════════════════════════════════════════════════════

# Level thresholds: level N requires N*1000 XP cumulative
# Level 1: 0, Level 2: 1000, Level 3: 3000, Level 4: 6000, etc.
def xp_for_level(level):
    """Total XP required to reach a given level."""
    return int(level * (level - 1) * 500)

def level_from_xp(xp):
    """Calculate level from total XP."""
    level = 1
    while xp_for_level(level + 1) <= xp:
        level += 1
    return level

# Badge definitions: (id, name, emoji, description, condition_check)
BADGE_DEFS = [
    ("first_share", "First Share", "⛏️", "Submitted your first mining share", None),
    ("share_100", "Century", "💯", "Mined 100 shares", None),
    ("share_1000", "Kilominer", "🔥", "Mined 1,000 shares", None),
    ("share_10000", "Megaminer", "💎", "Mined 10,000 shares", None),
    ("first_ticket", "Lucky Entry", "🎫", "Earned your first ticket", None),
    ("ticket_50", "Ticket Hoarder", "🎟️", "Held 50+ tickets at once", None),
    ("first_referral", "Recruiter", "🤝", "Got your first referral", None),
    ("referral_5", "Network Builder", "🌐", "5 active referrals", None),
    ("streak_7", "Week Warrior", "📅", "7-day mining streak", None),
    ("streak_30", "Month Master", "🏆", "30-day mining streak", None),
    ("jackpot_winner", "Jackpot Winner", "👑", "Won a daily jackpot", None),
    ("founding_miner", "Founding Miner", "⭐", "One of the first 30 miners", None),
]

def get_or_create_xp(miner_id):
    """Get XP record for a miner, creating if needed."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM miner_xp WHERE miner_id = ?", (miner_id,)).fetchone()
        if row:
            return dict(row)
        conn.execute("INSERT INTO miner_xp (miner_id) VALUES (?)", (miner_id,))
        row = conn.execute("SELECT * FROM miner_xp WHERE miner_id = ?", (miner_id,)).fetchone()
        return dict(row) if row else {"miner_id": miner_id, "xp": 0, "level": 1, "mining_streak_days": 0, "last_mine_day": None, "total_shares_lifetime": 0, "badges_earned": "[]", "super_tickets": 0}

def add_xp(miner_id, xp_gained):
    """Add XP to a miner and update their level. Returns new level if it changed."""
    xp_record = get_or_create_xp(miner_id)
    old_level = xp_record["level"]
    new_xp = xp_record["xp"] + xp_gained
    new_level = level_from_xp(new_xp)
    with get_db() as conn:
        conn.execute(
            "UPDATE miner_xp SET xp = ?, level = ? WHERE miner_id = ?",
            (new_xp, new_level, miner_id),
        )
    return new_level if new_level > old_level else None

def add_shares_lifetime(miner_id, shares):
    """Add to lifetime shares counter and award XP (1 XP per share)."""
    xp_record = get_or_create_xp(miner_id)
    new_total = xp_record["total_shares_lifetime"] + shares
    with get_db() as conn:
        conn.execute(
            "UPDATE miner_xp SET total_shares_lifetime = ? WHERE miner_id = ?",
            (new_total, miner_id),
        )
    # 1 XP per share
    add_xp(miner_id, shares)
    return new_total

def update_mining_streak(miner_id):
    """Update the mining streak for a miner. Call when shares are submitted."""
    from datetime import datetime, timedelta
    today = datetime.utcnow().strftime("%Y-%m-%d")
    xp_record = get_or_create_xp(miner_id)
    last_day = xp_record.get("last_mine_day")

    if last_day == today:
        return xp_record["mining_streak_days"]  # already counted today

    streak = xp_record["mining_streak_days"]
    if last_day:
        try:
            last_date = datetime.strptime(last_day, "%Y-%m-%d")
            yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
            if last_day == yesterday:
                streak += 1
            else:
                streak = 1  # reset
        except Exception:
            streak = 1
    else:
        streak = 1

    with get_db() as conn:
        conn.execute(
            "UPDATE miner_xp SET mining_streak_days = ?, last_mine_day = ? WHERE miner_id = ?",
            (streak, today, miner_id),
        )
    return streak

def award_badge(miner_id, badge_id):
    """Award a badge to a miner if they don't already have it. Returns True if newly awarded."""
    xp_record = get_or_create_xp(miner_id)
    import json
    badges = json.loads(xp_record.get("badges_earned", "[]"))
    if badge_id in badges:
        return False
    badges.append(badge_id)
    with get_db() as conn:
        conn.execute(
            "UPDATE miner_xp SET badges_earned = ? WHERE miner_id = ?",
            (json.dumps(badges), miner_id),
        )
    return True

def add_super_tickets(miner_id, count):
    """Add super tickets to a miner (earned via milestones)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE miner_xp SET super_tickets = super_tickets + ? WHERE miner_id = ?",
            (count, miner_id),
        )

def check_and_award_badges(miner_id, shares_valid=None, ticket_count=None, referral_count=None, streak=None, is_winner=False, is_founding=False):
    """Check all badge conditions and award any newly earned. Returns list of newly awarded badge IDs."""
    awarded = []
    xp_record = get_or_create_xp(miner_id)
    shares = shares_valid if shares_valid is not None else xp_record["total_shares_lifetime"]
    tickets = ticket_count or 0
    refs = referral_count or 0
    stk = streak if streak is not None else xp_record["mining_streak_days"]

    checks = [
        ("first_share", shares > 0),
        ("share_100", shares >= 100),
        ("share_1000", shares >= 1000),
        ("share_10000", shares >= 10000),
        ("first_ticket", tickets > 0),
        ("ticket_50", tickets >= 50),
        ("first_referral", refs >= 1),
        ("referral_5", refs >= 5),
        ("streak_7", stk >= 7),
        ("streak_30", stk >= 30),
        ("jackpot_winner", is_winner),
        ("founding_miner", is_founding),
    ]

    for badge_id, condition in checks:
        if condition and award_badge(miner_id, badge_id):
            awarded.append(badge_id)
            # Award XP for badge
            add_xp(miner_id, 100)

    return awarded

def get_gamification_stats(miner_id):
    """Get full gamification stats for a miner."""
    xp_record = get_or_create_xp(miner_id)
    import json
    badges = json.loads(xp_record.get("badges_earned", "[]"))
    badge_details = []
    for badge_id, name, emoji, desc, _ in BADGE_DEFS:
        badge_details.append({
            "id": badge_id,
            "name": name,
            "emoji": emoji,
            "description": desc,
            "earned": badge_id in badges,
        })

    current_level = xp_record["level"]
    next_level_xp = xp_for_level(current_level + 1)
    current_level_xp = xp_for_level(current_level)
    xp_into_level = xp_record["xp"] - current_level_xp
    xp_needed_for_next = next_level_xp - current_level_xp

    return {
        "miner_id": miner_id,
        "xp": xp_record["xp"],
        "level": current_level,
        "xp_into_level": xp_into_level,
        "xp_needed_for_next": xp_needed_for_next,
        "next_level": current_level + 1,
        "mining_streak_days": xp_record["mining_streak_days"],
        "total_shares_lifetime": xp_record["total_shares_lifetime"],
        "super_tickets": xp_record["super_tickets"],
        "jackpot_days": int(xp_record.get("jackpot_days") or 0),
        "badges": badge_details,
        "badges_earned_count": len(badges),
    }


# ═══════════════════════════════════════════════════════════════
#  PROGRESSION: STREAK BONUS + KRAKEN SCOUT PRO UNLOCK (Oct 24)
# ═══════════════════════════════════════════════════════════════

def _utc_day():
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")


def get_streak_bonus(streak_days):
    """The 3x ticket conversion, applied on milestone days only.

    A milestone day is every Nth consecutive mining day (default 7).
    The bonus changes how the day's shares convert into tickets; it does
    not touch hashrate, which is why the miner is never affected.
    """
    try:
        every = max(1, int(float(get_setting("streak_bonus_every_days", "7"))))
        mult = max(1, int(float(get_setting("streak_bonus_multiplier", "3"))))
    except (TypeError, ValueError):
        every, mult = 7, 3
    streak = int(streak_days or 0)
    on_milestone = streak > 0 and streak % every == 0
    if streak <= 0:
        to_next = every
    else:
        to_next = every - (streak % every) or every
    return {
        "active": on_milestone,
        "multiplier": mult if on_milestone else 1,
        "every_days": every,
        "bonus_multiplier": mult,
        "streak_days": streak,
        "next_milestone_day": streak + to_next,
        "days_to_milestone": to_next,
    }


def record_jackpot_day(miner_id):
    """Count a day on which this miner's bot contributed to the pool.

    Idempotent per UTC day: a miner may submit thousands of shares in a
    day and the day still counts once. Missing days pause progress; they
    never reset it.
    """
    today = _utc_day()
    rec = get_or_create_xp(miner_id)
    if rec.get("last_jackpot_day") == today:
        return int(rec.get("jackpot_days") or 0)
    days = int(rec.get("jackpot_days") or 0) + 1
    with get_db() as conn:
        conn.execute(
            "UPDATE miner_xp SET jackpot_days = ?, last_jackpot_day = ? "
            "WHERE miner_id = ?", (days, today, miner_id))
    return days


def _pro_row(user_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT scout_pro_until, scout_pro_lifetime, "
            "scout_pro_unlock_source, scout_pro_unlocked_at, "
            "scout_pro_entry_awarded, pending_entries FROM users WHERE id = ?",
            (user_id,)).fetchone()


def has_scout_pro(user_id):
    """THE ENTITLEMENT PREDICATE (Oct 24): does this account hold
    Kraken Scout Pro right now?

    One place decides, so the server-side signal gate and the browser's
    cached flag can never disagree about who is paid up: lifetime wins,
    otherwise the checkout window must still be open. A missing row or
    a bad id is simply not entitled.
    """
    import time as _time
    if user_id is None:
        return False
    row = _pro_row(user_id)
    if not row:
        return False
    return bool(int(row["scout_pro_lifetime"] or 0)) or \
        float(row["scout_pro_until"] or 0) > _time.time()


def unlock_scout_pro(user_id, source, grant_days=None):
    """Mark Pro unlocked and award its +1 jackpot entry, once.

    source='progression' sets the lifetime flag (the 100-day path); a
    checkout grant passes its day count instead. The unlock entry is a
    single free ticket per account, whether the unlock came from the
    streak path or a donation.
    """
    import time as _time
    now = _time.time()
    row = _pro_row(user_id)
    if not row:
        return {"unlocked": False, "entry_awarded": False}

    lifetime = int(row["scout_pro_lifetime"] or 0)
    until = float(row["scout_pro_until"] or 0)
    if grant_days:
        until = max(until, now) + int(grant_days) * 86400
        lifetime = max(lifetime, 0)
    else:
        lifetime = 1
        until = max(until, now)

    entry_awarded = False
    entry_held = False
    if not int(row["scout_pro_entry_awarded"] or 0):
        user = get_user(user_id)
        wallet = (user or {}).get("wallet_address") or ""
        if wallet:
            miner = get_or_create_miner(wallet)
            add_tickets(miner["id"], "free", 1, source="unlock")
            entry_awarded = True
        else:
            # NO WALLET, STILL AN ENTRY (Oct 24): a donor paying from a
            # browser-only account has no wallet and therefore no miner
            # to hang a ticket on. The old code minted nothing and
            # stamped the awarded flag anyway, so the promised entry
            # was burned the moment it was earned. The entry is HELD
            # on the account instead; the flag stays honest because it
            # now means "granted", and claim_pending_entries() turns
            # the hold into a real ticket.
            entry_held = True

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET scout_pro_until = ?, scout_pro_lifetime = ?, "
            "scout_pro_unlock_source = CASE WHEN scout_pro_unlock_source "
            "IS NULL OR scout_pro_unlock_source = '' THEN ? "
            "ELSE scout_pro_unlock_source END, "
            "scout_pro_unlocked_at = CASE WHEN scout_pro_unlocked_at "
            "IS NULL OR scout_pro_unlocked_at = 0 THEN ? "
            "ELSE scout_pro_unlocked_at END, "
            "scout_pro_entry_awarded = 1, "
            "pending_entries = pending_entries + ? WHERE id = ?",
            (until, lifetime, source, now, 1 if entry_held else 0, user_id))

    return {"unlocked": True, "entry_awarded": entry_awarded,
            "entry_held": entry_held,
            "until": int(until), "lifetime": bool(lifetime),
            "source": source}


def claim_pending_entries(user_id):
    """Turn held unlock entries into real tickets, once, as soon as the
    account has a payout wallet.

    Idempotent and cheap enough to call on every dashboard read: the
    conditional UPDATE (pending_entries = <exact count>) is the claim,
    so only one caller can win the transition and a ticket is never
    minted twice. With no wallet the entry simply stays held.
    """
    row = _pro_row(user_id)
    if not row:
        return {"claimed": 0, "held": 0}
    held = int(row["pending_entries"] or 0)
    if held <= 0:
        return {"claimed": 0, "held": 0}
    user = get_user(user_id) or {}
    wallet = (user.get("wallet_address") or "").strip()
    if not wallet:
        return {"claimed": 0, "held": held}
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE users SET pending_entries = 0 "
            "WHERE id = ? AND pending_entries = ?", (user_id, held))
        won = cur.rowcount == 1
    if not won:
        return {"claimed": 0, "held": 0}
    miner = get_or_create_miner(wallet)
    add_tickets(miner["id"], "free", held, source="unlock")
    try:
        add_audit_log(user_id, "held_entry_claimed",
                      f"{held} held unlock entry -> {wallet[:16]}...")
    except Exception:
        pass
    return {"claimed": held, "held": 0}


def pro_progress(miner_id, user_id=None):
    """Everything the dashboard needs to show progress toward Pro.

    THE SETTLED TOKEN RULE (Oct 24): a token IS an entry — there is no
    second balance to consult. This path therefore counts CONTRIBUTING
    DAYS and nothing else; the unlock awards its +1 entry through
    unlock_scout_pro. No token threshold gates it and none accelerates
    it, which is why this payload has no token field at all.
    """
    import time as _time
    if miner_id:
        rec = get_or_create_xp(miner_id)
        days = int(rec.get("jackpot_days") or 0)
        streak_days = rec.get("mining_streak_days")
    else:
        days, streak_days = 0, 0
    try:
        target = max(1, int(float(get_setting("pro_unlock_days", "100"))))
    except (TypeError, ValueError):
        target = 100
    bonus = get_streak_bonus(streak_days)

    unlocked = False
    source = ""
    unlocked_at = 0
    entry_awarded = False
    held = 0
    if user_id is not None:
        row = _pro_row(user_id)
        if row:
            unlocked = bool(int(row["scout_pro_lifetime"] or 0)) or \
                float(row["scout_pro_until"] or 0) > _time.time()
            source = row["scout_pro_unlock_source"] or ""
            unlocked_at = int(row["scout_pro_unlocked_at"] or 0)
            entry_awarded = bool(int(row["scout_pro_entry_awarded"] or 0))
            held = int(row["pending_entries"] or 0)

    return {
        "streak": bonus,
        "days_mined": days,
        "days_required": target,
        "days_remaining": max(0, target - days),
        "progress_percent": round(min(100.0, days * 100.0 / target), 1),
        "pro_unlocked": unlocked,
        "pro_unlock_source": source,
        "pro_unlocked_at": unlocked_at,
        # "credited" means a real ticket exists; a held entry is granted
        # but still waiting for a payout wallet to be minted onto.
        "unlock_entry_awarded": entry_awarded and not held,
        "pending_entries": held,
        "entry_held": bool(held),
    }


# ═══════════════════════════════════════════════════════════════
#  SESSION FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def create_session(token, user_id, expires_at, ip_address=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at, ip_address) VALUES (?, ?, ?, ?)",
            (token, user_id, expires_at, ip_address),
        )


def get_session(token):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token = ? AND expires_at > strftime('%s','now')",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token):
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_expired_sessions():
    """Sweep expired auth rows (get_session already refuses them —
    they are pure table growth). Returns the number removed so the
    scheduler's prune leg can audit real work and stay silent on a
    no-op day."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM sessions WHERE expires_at < strftime('%s','now')")
        return cur.rowcount


# ═══════════════════════════════════════════════════════════════
#  AUDIT LOG
# ═══════════════════════════════════════════════════════════════

def add_audit_log(user_id, action, details="", ip_address=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_log (user_id, action, details, ip_address) VALUES (?, ?, ?, ?)",
            (user_id, action, details, ip_address),
        )


# ═══════════════════════════════════════════════════════════════
#  PROFILE ALERT LOG (Oct 21)
# ═══════════════════════════════════════════════════════════════
#  The alert client writes here whenever a real alert fires with NFTY
#  disabled (checklist 4.1 / 4.2), and the profile page reads it back
#  (4.3 / 5.3). Only the two alert classes are storable - the CHECK
#  constraint rejects anything else, so an informational surface can
#  never leak into a member's alert history.

def add_profile_alert(user_id, alert_type, payload=None, ts=""):
    """One row per fired alert. Returns the stored entry."""
    if alert_type not in ("BUY_NOW", "GET_OUT_NOW"):
        raise ValueError("unsupported alert type: %r" % (alert_type,))
    blob = ""
    if payload is not None:
        try:
            blob = json.dumps(payload, separators=(",", ":"))
        except (TypeError, ValueError):
            blob = json.dumps({"unserializable": str(payload)})
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO profile_alert_log (user_id, alert_type, payload, ts) "
            "VALUES (?, ?, ?, ?)",
            (user_id, alert_type, blob, str(ts or "")),
        )
        row_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, alert_type, payload, ts, created_at "
            "FROM profile_alert_log WHERE id = ?", (row_id,)
        ).fetchone()
    return _alert_row(row)


def _alert_row(row):
    if row is None:
        return None
    payload = None
    raw = row["payload"]
    if raw:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = raw
    return {
        "id": row["id"],
        "type": row["alert_type"],
        "payload": payload,
        "ts": row["ts"] or "",
        "created_at": row["created_at"],
    }


def get_profile_alerts(user_id, limit=0):
    """Newest first, scoped to one account.

    limit=0 (the default) returns the WHOLE history, which is what the
    profile asks for. A positive limit pages it, capped at 200 rows so a
    caller cannot ask for an unbounded scan by accident. Ordering is by
    the append id, not by the client-supplied ts, so the order is stable
    even if two alerts share a second.
    """
    limit = int(limit or 0)
    sql = ("SELECT id, alert_type, payload, ts, created_at "
           "FROM profile_alert_log WHERE user_id = ? ORDER BY id DESC")
    params = [user_id]
    if limit > 0:
        sql += " LIMIT ?"
        params.append(min(limit, 200))
    with get_db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_alert_row(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  KRAKEN CTA CLICK LEDGER (Oct 10)
# ═══════════════════════════════════════════════════════════════

def record_cta_click(slot, url=None, ip_address=None, user_id=None):
    """One row per clicked Kraken referral CTA. Fire-and-forget on the
    client side; the server never needs a session to accept it."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO kraken_cta_click (slot, url, ip_address, user_id) "
            "VALUES (?, ?, ?, ?)",
            (slot, url, ip_address, user_id),
        )


def get_cta_click_summary(days=30):
    """Per-slot counts for the admin metrics row: all-time, and the
    last `days` (default 30) — plus today's count for the pulse read."""
    # ONE clock read (CodeRabbit minor on ddcdd1e): two separate
    # time.time() calls could straddle UTC midnight (-day_start landing
    # a hair before it), cribbing end-of-previous-day clicks into
    # today's window; now is a single coherent snapshot.
    now = time.time()
    day_start = now - (now % 86400)
    window_start = now - days * 86400
    with get_db() as conn:
        rows = conn.execute(
            "SELECT slot, "
            "COUNT(*) AS total, "
            "SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS in_window, "
            "SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS today, "
            "MAX(created_at) AS last_at "
            "FROM kraken_cta_click "
            "GROUP BY slot",
            (window_start, day_start),
        ).fetchall()
    return [
        {"slot": r["slot"], "total": r["total"],
         "in_window": r["in_window"] or 0, "today": r["today"] or 0,
         "last_at": r["last_at"]}
        for r in rows
    ]


def get_audit_logs(limit=50):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT a.*, u.username FROM audit_log a
            LEFT JOIN users u ON a.user_id = u.id
            ORDER BY a.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  ADMIN SETTINGS
# ═══════════════════════════════════════════════════════════════

def get_admin_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM admin_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_admin_setting(key, value, user_id=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO admin_settings (key, value, updated_by, updated_at) "
            "VALUES (?, ?, ?, strftime('%s','now')) "
            "ON CONFLICT(key) DO UPDATE SET value = ?, updated_by = ?, updated_at = strftime('%s','now')",
            (key, str(value), user_id, str(value), user_id),
        )


# ═══════════════════════════════════════════════════════════════
#  SUBADDRESS & PAYMENT VERIFICATION
# ═══════════════════════════════════════════════════════════════

def set_user_subaddress(user_id, subaddress, subaddress_index):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET subaddress = ?, subaddress_index = ? WHERE id = ?",
            (subaddress, subaddress_index, user_id),
        )


def get_all_subaddress_mappings():
    """Get all user_id -> subaddress_index mappings for payment scanning."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, subaddress_index FROM users WHERE subaddress_index IS NOT NULL"
        ).fetchall()
        return {r["subaddress_index"]: r["id"] for r in rows}


def record_payment(user_id, txid, amount_xmr, subaddress_index, height, confirmations):
    """Record a detected on-chain payment. Returns payment ID."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM payment_verifications WHERE txid = ?", (txid,)
        ).fetchone()
        if existing:
            return existing["id"]

        cur = conn.execute(
            """INSERT INTO payment_verifications
            (user_id, txid, amount_xmr, subaddress_index, height, confirmations, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (user_id, txid, amount_xmr, subaddress_index, height, confirmations),
        )
        return cur.lastrowid


def update_payment_status(payment_id, status, confirmations=None):
    with get_db() as conn:
        if confirmations is not None:
            conn.execute(
                "UPDATE payment_verifications SET status = ?, confirmations = ? WHERE id = ?",
                (status, confirmations, payment_id),
            )
        else:
            conn.execute(
                "UPDATE payment_verifications SET status = ? WHERE id = ?",
                (status, payment_id),
            )


def credit_payment(payment_id, tickets_awarded):
    with get_db() as conn:
        conn.execute(
            "UPDATE payment_verifications SET status = 'credited', credits_awarded = ?, credited_at = strftime('%s','now') WHERE id = ?",
            (tickets_awarded, payment_id),
        )


def get_pending_payments():
    """Deposit rows still awaiting confirmation, oldest first.

    payment_verifications has never had a created_at column (it stamps
    scanned_at and credited_at — the same trap get_user_payments
    documents), so ORDER BY created_at raised on EVERY call. The server's
    payment scanner swallowed that raise and with it every step after
    it, including the 7-day claim rollover. Order by a column the table
    actually carries.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_verifications WHERE status IN ('pending', 'confirming') "
            "ORDER BY COALESCE(scanned_at, 0) ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── THE DONATION ORDER KEY (Oct 26) ────────────────────────────────────
# One row per checkout order, written by the same transaction that raises
# the pot. The INSERT is the claim: whoever inserts owns the credit, and
# everyone else reads the row and moves nothing. See the table's comment
# in init_db for why the settings ledger could not carry this.
def get_donation_credit(order_id, conn=None):
    """The credit row for one order, or None when it never landed.

    `conn` reads inside the caller's open transaction, so a credit can
    see the claim it is in the middle of making.
    """
    sql = "SELECT * FROM donation_credits WHERE order_id = ?"
    key = (str(order_id),)
    if conn is not None:
        row = conn.execute(sql, key).fetchone()
        return dict(row) if row else None
    with get_db() as c:
        row = c.execute(sql, key).fetchone()
        return dict(row) if row else None


def claim_donation_credit(conn, order_id, amount_xmr, jackpot_xmr,
                          treasury_xmr, sponsor_id=None, source=""):
    """Claim one order INSIDE the caller's transaction.

    True means this caller owns the credit and must finish it (raise the
    pot, stamp the base) before that transaction commits. False means the
    order already has a row — an earlier credit or a concurrent poll —
    and nothing may move. INSERT OR IGNORE against the primary key is the
    whole mechanism: no read-then-write window, so two threads polling
    the same checkout cannot both win.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO donation_credits (order_id, amount_xmr, "
        "jackpot_xmr, treasury_xmr, sponsor_id, source, credited_at) "
        "VALUES (?, ?, ?, ?, ?, ?, strftime('%s','now'))",
        (str(order_id), float(amount_xmr), float(jackpot_xmr),
         float(treasury_xmr),
         None if sponsor_id is None else str(sponsor_id),
         str(source or "")[:120]))
    return cur.rowcount == 1


def set_donation_base(conn, order_id, base_xmr):
    """Stamp the pot value this credit produced (same transaction)."""
    conn.execute(
        "UPDATE donation_credits SET base_xmr = ? WHERE order_id = ?",
        (round(float(base_xmr), 12), str(order_id)))


def adopt_donation_credit(order_id, amount_xmr, jackpot_xmr, treasury_xmr,
                          sponsor_id=None, source="", base_xmr=None):
    """Adopt a credit that predates this table.

    An order credited before Oct 26 is protected only by its receipt in
    the prunable ledger. On sight, that protection is copied into the key
    table so it outlives the ledger's cap. INSERT OR IGNORE: an existing
    row (the ordinary replay) is never overwritten.
    """
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO donation_credits (order_id, amount_xmr, "
            "jackpot_xmr, treasury_xmr, sponsor_id, source, base_xmr, "
            "credited_at) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))",
            (str(order_id), float(amount_xmr), float(jackpot_xmr),
             float(treasury_xmr),
             None if sponsor_id is None else str(sponsor_id),
             str(source or "")[:120],
             None if base_xmr is None else round(float(base_xmr), 12)))
        row = conn.execute(
            "SELECT * FROM donation_credits WHERE order_id = ?",
            (str(order_id),)).fetchone()
        return dict(row) if row else None


def create_scout_purchase(user_id, identity_wallet, amount_xmr, address,
                          subaddress_index, expires_at):
    """Open a checkout. Any older pending order for the user is expired so a
    buyer always has exactly one live address/amount pair."""
    now = time.time()
    with get_db() as conn:
        conn.execute(
            "UPDATE scout_purchases SET status = 'expired' "
            "WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        )
        cur = conn.execute(
            "INSERT INTO scout_purchases "
            "(user_id, identity_wallet, amount_xmr, address, subaddress_index, "
            " status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (user_id, identity_wallet or None, float(amount_xmr), address,
             subaddress_index, now, float(expires_at)),
        )
        row = conn.execute(
            "SELECT * FROM scout_purchases WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row) if row else None


def get_scout_purchase(order_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM scout_purchases WHERE id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None


def get_open_scout_purchase(user_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM scout_purchases "
            "WHERE user_id = ? AND status = 'pending' AND expires_at > ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, time.time()),
        ).fetchone()
        return dict(row) if row else None


def scout_purchase_txid_used(txid):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM scout_purchases WHERE txid = ?", (txid,)
        ).fetchone()
        return row is not None


def mark_scout_purchase_paid(order_id, txid, confirmations):
    """Transition a pending checkout to paid exactly once.

    Returns True only for the call that performed the transition, so the
    entitlement is never granted twice by concurrent status polls.
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE scout_purchases SET status = 'paid', txid = ?, "
            "confirmations = ?, paid_at = strftime('%s','now') "
            "WHERE id = ? AND status = 'pending'",
            (txid, int(confirmations), int(order_id)),
        )
        return cur.rowcount == 1


def expire_scout_purchase(order_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE scout_purchases SET status = 'expired' "
            "WHERE id = ? AND status = 'pending'",
            (int(order_id),),
        )


def get_user_scout_pro(user_id):
    """Unix seconds until which Scout Pro is unlocked (0 = never)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT scout_pro_until FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return float(row[0] or 0) if row else 0.0


def set_user_scout_pro(user_id, until_ts):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET scout_pro_until = ? WHERE id = ?",
            (float(until_ts), int(user_id)),
        )


def get_user_payments(user_id):
    """The user's recent deposits, newest first.

    payment_verifications has never had a created_at column (it stamps
    scanned_at and credited_at), so the old ORDER BY raised on every
    call and /api/payment/status could only ever 500. Order by the
    column the table actually carries.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_verifications WHERE user_id = ? "
            "ORDER BY COALESCE(credited_at, scanned_at) DESC, id DESC LIMIT 20",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_admin_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM admin_settings ORDER BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}


def get_pool_summary():
    """Get full pool summary for admin dashboard."""
    with get_db() as conn:
        miners_count = conn.execute("SELECT COUNT(*) as c FROM miners").fetchone()["c"]
        users_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        drawings_count = conn.execute("SELECT COUNT(*) as c FROM drawings").fetchone()["c"]
        tickets_count = conn.execute("SELECT COUNT(*) as c FROM tickets").fetchone()["c"]
        payouts_count = conn.execute("SELECT COUNT(*) as c FROM payouts").fetchone()["c"]
        pending_payouts = conn.execute("SELECT COUNT(*) as c FROM payouts WHERE status='pending'").fetchone()["c"]
        total_paid = conn.execute(
            "SELECT COALESCE(SUM(amount_xmr), 0) as t FROM payouts WHERE status IN ('sent','confirmed')"
        ).fetchone()["t"]
        total_house_tax = conn.execute(
            "SELECT COALESCE(SUM(house_tax), 0) as t FROM drawings WHERE status='drawn'"
        ).fetchone()["t"]
        active_miners = conn.execute(
            "SELECT COUNT(*) as c FROM miners WHERE last_seen > strftime('%s','now') - 600"
        ).fetchone()["c"]
        # The operator's own numbers say which miners are simulated
        # (Oct 24): a seeded pool otherwise reads as 20 real miners.
        demo_miners = conn.execute(
            "SELECT COUNT(*) as c FROM miners WHERE is_demo = 1"
        ).fetchone()["c"]
        demo_active = conn.execute(
            "SELECT COUNT(*) as c FROM miners WHERE is_demo = 1 "
            "AND last_seen > strftime('%s','now') - 600"
        ).fetchone()["c"]

        return {
            "miners_total": miners_count,
            "miners_active": active_miners,
            "miners_demo": demo_miners,
            "miners_demo_active": demo_active,
            "users_total": users_count,
            "drawings_total": drawings_count,
            "tickets_total": tickets_count,
            "payouts_total": payouts_count,
            "payouts_pending": pending_payouts,
            "total_paid_out": round(total_paid, 6),
            "total_house_tax": round(total_house_tax, 6),
        }
