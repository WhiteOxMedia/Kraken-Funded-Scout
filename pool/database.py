"""
Database models for the XMR Daily Jackpot Pool.
SQLite database stores: miners, shares, tickets, drawings, payouts.
"""
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
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
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
                FOREIGN KEY (miner_id) REFERENCES miners(id)
            );
        """)

        # Initialize default settings
        defaults = {
            "house_tax_percent": "10",
            "pool_fee_percent": "1",
            "free_tickets_per_shares": "100",
            "paid_spin_cost_xmr": "0.001",
            "paid_spin_tickets": "1",
            "drawing_interval_hours": "24",
            "min_jackpot_xmr": "0.01",
            "house_wallet": "86NjWuTLKm7eggeGw9PZ5LjFDZTHUtAQtWs7Bbu783yYfDRqSd6itapDek39rySHWCax6fnDAKQQXLiieRtRqvzCPeqTxkG",
            "upstream_pool": "pool.supportxmr.com:3333",
            "jackpot_balance": "0",
            "kraken_deposit_address": "86NjWuTLKm7eggeGw9PZ5LjFDZTHUtAQtWs7Bbu783yYfDRqSd6itapDek39rySHWCax6fnDAKQQXLiieRtRqvzCPeqTxkG",
            "payout_threshold_xmr": "0.01",
            "transfer_fee_buffer_xmr": "0.0001",
            "house_tax_accumulated": "0",
            "daily_paid_ticket_cap": "100",
            "jackpot_seed_xmr": "0.05",
            "signup_bonus_xmr": "0.003",
            "referral_ticket_reward": "100",
            "kraken_withdrawal_fee_xmr": "0.0027",
            "claim_window_days": "7",
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
            vals = {c: (r[c] if r[c] is not None else "") for c in ENCRYPTED_USER_COLS}
            if any(v and not str(v).startswith(_fc.PREFIX)
                   for v in vals.values()):
                conn.execute(
                    "UPDATE users SET email=?, phone=?, full_name=?, "
                    "wallet_address=?, wallet_idx=?, email_idx=? WHERE id=?",
                    (_fc.encrypt(vals["email"]), _fc.encrypt(vals["phone"]),
                     _fc.encrypt(vals["full_name"]),
                     _fc.encrypt(vals["wallet_address"]),
                     _fc.blind_index(vals["wallet_address"]),
                     _fc.blind_index(vals["email"].strip().lower(),
                                     domain="email"),
                     r["id"]))
                counts["users_sealed"] += 1
            elif not (r["wallet_idx"] or "") and vals["wallet_address"]:
                conn.execute(
                    "UPDATE users SET wallet_idx=?, email_idx=? WHERE id=?",
                    (_fc.blind_index(vals["wallet_address"]),
                     _fc.blind_index(vals["email"].strip().lower(),
                                     domain="email"),
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
            vals = {c: (r[c] if r[c] is not None else "") for c in ENCRYPTED_USER_COLS}
            if not any(str(v).startswith(_fc.PREFIX) for v in vals.values()):
                continue
            new = {c: _fc.reencrypt(vals[c]) for c in ENCRYPTED_USER_COLS}
            w_idx = _fc.blind_index(
                _fc.decrypt(new["wallet_address"]) if str(
                    new["wallet_address"]).startswith(_fc.PREFIX) else "")
            e_idx = _fc.blind_index(
                _fc.decrypt(new["email"]).strip().lower() if str(
                    new["email"]).startswith(_fc.PREFIX) else "",
                domain="email")
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


def get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, str(value), str(value)),
        )


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


def add_tickets(miner_id, ticket_type, count, cost_xmr=0, drawing_id=None):
    with get_db() as conn:
        for _ in range(count):
            conn.execute(
                "INSERT INTO tickets (miner_id, ticket_type, cost_xmr, drawing_id) VALUES (?, ?, ?, ?)",
                (miner_id, ticket_type, cost_xmr, drawing_id),
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
    with get_db() as conn:
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


def claim_drawing(drawing_id, wallet_address):
    """Mark a drawing as claimed by the winner."""
    with get_db() as conn:
        conn.execute(
            "UPDATE drawings SET claim_status = 'claimed', claimed_at = strftime('%s','now') WHERE id = ?",
            (drawing_id,),
        )


def expire_drawing(drawing_id):
    """Mark a drawing as expired (jackpot returned to pool)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE drawings SET claim_status = 'expired' WHERE id = ?",
            (drawing_id,),
        )


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
    with get_db() as conn:
        rows = conn.execute(
            """SELECT d.*, m.wallet_address as winner_wallet
               FROM drawings d
               LEFT JOIN miners m ON d.winner_id = m.id
               ORDER BY d.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_payout(miner_id, amount_xmr, payout_type, drawing_id=None):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO payouts (miner_id, amount_xmr, payout_type, drawing_id) VALUES (?, ?, ?, ?)",
            (miner_id, amount_xmr, payout_type, drawing_id),
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


def get_referral_stats(user_id):
    """Get referral stats for a user."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT referral_code, referral_count, referred_by FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return None

        # Count referred users who have active miners
        active_referrals = conn.execute(
            """SELECT COUNT(DISTINCT m.wallet_address) as cnt
            FROM users u
            JOIN miners m ON m.wallet_address = u.wallet_address
            WHERE u.referred_by = ? AND m.shares_valid > 0""",
            (row["referral_code"],)
        ).fetchone()

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
        "badges": badge_details,
        "badges_earned_count": len(badges),
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
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < strftime('%s','now')")


# ═══════════════════════════════════════════════════════════════
#  AUDIT LOG
# ═══════════════════════════════════════════════════════════════

def add_audit_log(user_id, action, details="", ip_address=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_log (user_id, action, details, ip_address) VALUES (?, ?, ?, ?)",
            (user_id, action, details, ip_address),
        )


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
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_verifications WHERE status IN ('pending', 'confirming') ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_payments(user_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_verifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
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

        return {
            "miners_total": miners_count,
            "miners_active": active_miners,
            "users_total": users_count,
            "drawings_total": drawings_count,
            "tickets_total": tickets_count,
            "payouts_total": payouts_count,
            "payouts_pending": pending_payouts,
            "total_paid_out": round(total_paid, 6),
            "total_house_tax": round(total_house_tax, 6),
        }
