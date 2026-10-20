# Monero Project End-to-End Audit — Full Report

Date: 2026-10-15 20:40 UTC  
Auditor: Codebuff (live query over `C:\Users\birds\Workspace\monero_project\pool\jackpot.db`)  
Scope: miners, tickets, jackpot draw lifecycle, funding, payouts, security surface, probe traffic.
Weapons: full import-time sweep through `sqlite3.Row` with rowid-ordered settings, timestamps localized to UTC human form, closed-loop arithmetic via Python formula.

---

## 1. Project identity and current posture

Canonical pool database: `pool/jackpot.db` (SQLite).  
Live pool server artifact reviewed: `pool/server.py` (see §7).  
Installer bundle reviewed: `installer/installer_ui.py`, `installer/installer.py`, `installer/build_bundle.ps1`, `installer/install.ps1`.  
Front-end reviewed: `pool/static/app.js`, `README.md`.  
Test hygiene reviewed: `test_bundle_hygiene.py`, `test_kraken_cta_counter.py`.  
Scout policy reviewed: `scoutbot/governance/adjustment_policy.md`, `scoutbot/auto_adjust.py` (parameter block and coupling logic).  
Site text reviewed: _coordination notes_, `DIRECTIVE_REPO_NATIVE_ANCHORED.md`.  
Observed posture: pool is running in simulation / in-house demo mode; miner ingest and kraken referral scheduler are active but not funding anything live; jackpot is effectively drained against pending drawing obligations.

---

## 2. Database schema — quick map

Tables present (19 total, extracted live):

- admin_settings, audit_log, comments, drawings, jackpot_projection, kraken_cta_click, kraken_referral_snapshot, miner_xp, miners, paid_spins, payment_verifications, payouts, payouts_new, push_daily_report, sessions, settings, sqlite_sequence, tickets, users.

Payment-verification table has an evolved column set:
`id, user_id, txid, amount_xmr, subaddress_index, height, confirmations, status, credits_awarded, scanned_at, credited_at` — so the bookkeeping has matured beyond the minimal original design.

---

## 3. Config reconstruction — what the pool thinks it is

Live settings (48 rows), most important interpreted:

- pool_fee_percent = 1
- house_tax_percent = 10
- free_tickets_per_shares = 100
- paid_spin_cost_xmr = 0.001
- paid_spin_tickets = 1
- drawing_interval_hours = 24
- min_jackpot_xmr = 0.01
- min_players_for_jackpot = 30
- payout_threshold_xmr = 0.01
- transfer_fee_buffer_xmr = 0.0001
- claim_window_days = 7
- signup_bonus_xmr = 0.003
- referral_ticket_reward = 100
- kraken_withdrawal_fee_xmr = 0.0027
- jackpot_seed_xmr = 0.05
- house_wallet = 4AoPP7szGLmG9qG1HQg6fXH84nyprSP4tbUTiCXmCoagWqAKZYGerv63kradKAqpufNZ5H3PGMNeDfrAUa9iZtiyCUMcCms
- upstream_pool = pool.supportxmr.com:3333
- kraken_deposit_address = 86NjWuTLKm7eggeGw9PZ5LjFDZTHUtAQtWs7Bbu783yYfDRqSd6itapDek39rySHWCax6fnDAKQQXLiieRtRqvzCPeqTxkG
- jackpot_balance (encoded JSON) = `{"confirmed_lucky_draw_xmr":"0.05"}`
- house_tax_accumulated = 0.005 XMR

Scout / scheduler settings present:

- KRAKEN_REFERRAL_ENDPOINT = https://xmrjackpot.com/api/kraken-referral-source
- KRAKEN_REFERRAL_MODE = simulate
- SCHEDULER_ENABLED = 1
- miner_ingest_key = 89603b02d1d324b540b23e5722b4c4b506ea7831d08b4ba9443694742a503971
- kraken_referral_key_check = valid / tier=keyring / assets_visible=88 / ok=true
- SCHEDULER_LAST_KC = valid
- SCHEDULER_LAST_ORPHANS = total 0, edges 0
- SCHEDULER_HEARTBEAT = 1792099609.7466903
- SCHEDULER_LAST_WORKER = ran
- ORPHAN_DEADMAN_ALERTED = 2026-10-11
- PUSH_REPORT_PRUNED = 2026-10-15
- SESSIONS_PRUNED = 2026-10-15
- push_subscriptions / push_send_rate / push_prefs = present (encoded)
- VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, PUSH_OPERATOR_TOKEN = present
- api_key = xmr_7bd79f23550bf7118b385c74c87bb6e10c956f1c08d920ee / active=1

Notes:
- `jackpot_balance` is stored as JSON with key `confirmed_lucky_draw_xmr`. The audit’s closed-loop computation uses that key.
- `house_tax_accumulated` sits at 0.005 XMR and is not consumed by the jackpot drawdown formula in this audit’s closed loop.

---

## 4. Drawings — the jackpot history

Six drawings.

| id | jackpot | winner_id | winning_ticket_id | house_tax | status | claim_deadline | claim_status | winner_wallet | block_hash | commit_seed | reveal_seed | total_tickets | drawn_at |
|----|---------|-----------|-------------------|-----------|--------|----------------|--------------|---------------|------------|-------------|-------------|----------------|----------|
| 10 | 0.093 | 24 | None | 0.0093 | drawn | 2026-08-07 20:59:14 | expired | PENDING_Shale | None | None | None | 1 | 1785553154.0016658 |
| 11 | 0.093 | 25 | None | 0.0093 | drawn | 2026-08-07 20:59:14 | expired | PENDING_Nater | None | None | None | 1 | 1785553154.0016658 |
| 12 | 0.093 | 26 | None | 0.0093 | drawn | 2026-08-07 20:59:14 | expired | PENDING_Grant stringham | None | None | None | 1 | 1785553154.0016658 |
| 13 | 0.05 | None | None | 0.0 | cancelled | none | pending | None | None | None | None | 0 | 1790382380.2521532 |
| 14 | 0.05 | None | None | 0.0 | cancelled | none | pending | None | None | None | None | 0 | 1790388974.6097426 |
| 15 | 0.05 | 30 | 755 | 0.005 | drawn | 2026-10-02 22:28:54 | pending | 4AoPP7szGLmG9qG1HQg6fXH84nyprSP4tbUTiCXmCoagWqAKZYGerv63kradKAqpufNZ5H3PGMNeDfrAUa9iZtiyCUMcCms | 3770667 | 9d0ae85ad0e6bced26e7fd5a8d540272afde5d915c550becac32e9c26a5b03d5 | 8e5d0093b47ace5e31c270daaaf2270d301907a44f4ebd7900ec44d12adbaf18 | 181 | 1790396934.0 |

Interpretation:

- Drawings 10–12 are the oldest cohort: small 0.093 XMR prizes, winner wallet addresses are placeholder tokens, no block_hash / commit_seed / reveal_seed, and claim_status=expired now. They look like early demo draws that were never properly resolved to a real payout.
- Drawings 13–14 were cancelled. Both have jackpot 0.05 and no winner, no tickets. They share the funding ledger’s very first accrual timestamp band (first accrual ts=1790382380.174) — 13 was drawn_at 1790382380.252 and 14 at 1790388974.610. That is consistent with the first funding cycle failing the min_players / min_jackpot gate and cancelling.
- Drawing 15 is the only one with a real cryptographic closure: block_hash 3770667, 96-byte commit_seed and 96-byte reveal_seed present, winner_id=30, winning_ticket_id=755, 181 tickets, house_tax 0.005. It was drawn_at 1790396934.0 and had a 7-day claim deadline that has now expired (2026-10-02 22:28:54). Claim status is still "pending".

---

## 5. Closed-loop jackpot arithmetic

Live query results:
- settings.jackpot_balance = `{"confirmed_lucky_draw_xmr":"0.05"}`
- house_tax_accumulated = 0.005
- pending_payouts_to_winners = 0.29610000 XMR
- live_computed_jackpot = 0.00000000 XMR

Formula used by the audit:
`base = float(jackpot_balance.confirmed_lucky_draw_xmr)`  
`pending = SUM(jackpot_amount - house_tax) FROM drawings WHERE status='drawn' AND COALESCE(claim_status,'pending') != 'claimed'`  
`live = max(0.0, base - pending)`

So the pool’s own jackpot accounting is negative against its pending obligations:
- Base = 0.05 XMR
- Pending obligations = 0.2961 XMR
- Live = 0.00000000 XMR (floored to zero)

Pending obligation breakdown:
- Drawing 10: 0.093 - 0.0093 = 0.0837
- Drawing 11: 0.093 - 0.0093 = 0.0837
- Drawing 12: 0.093 - 0.0093 = 0.0837
- Drawing 15: 0.05 - 0.005 = 0.045
- Total = 0.2961 XMR

Observation: the pool’s in-memory jackpot balance is not sufficient to cover already-drawn prizes. That is a real accounting condition, not a cosmetic artifact.

---

## 6. Tickets — entry ledger

Tickets table schema: id, miner_id, ticket_type, cost_xmr, drawing_id, created_at.

Latest 60 tickets (all from miner_id=30, Birdsnbubbles):

- Tickets 870 down to 856 are free tickets with drawing_id=None, created between 1790399406 and 1790442133.
- Tickets 855 down to 811 are free tickets with drawing_id=15, all stamped created=1790389293.0.

Tickets by drawing:
- drawing_id=15 has 181 tickets. That matches drawing 15’s total_tickets=181 and the bulk ticket batch above.

Tickets by miner:
- miner_id=30: 196 tickets, spent 0.00000000 XMR.

Paid spins: 0 rows.  
So the only tickets in the system are free tickets earned by shares from the active miner. No paid spin transactions exist yet.

Interpretation:
- The ticket ledger is dominated by Birdsnbubbles’ free tickets.
- The drawing-15 cohort of 181 free tickets is fully accounted for in the ticket table, which is consistent with the winner being miner 30.

---

## 7. Payouts and payment verifications — who got paid

payouts: 0 rows.  
payouts_new: 0 rows.  
paid_spins: 0 rows.  
payment_verifications: 0 rows.  
comments: 0 rows.

So:
- There is no payout ledger with any completed or pending payout rows at this time.
- There are no payment verifications in the table.
- There is no paid-spin history.

That means drawing 15’s winner (miner 30) may or may not have been paid, depending on an out-of-band payout path, but there is no payout row in the local tables to confirm it. The closed-loop analysis treats it as still pending because claim_status != 'claimed'.

---

## 8. Miners — who is actually mining

Miners table:

| id | wallet | worker | shares_valid | shares_total | last_seen | hashrate | balance |
|----|--------|--------|--------------|--------------|-----------|----------|---------|
| 23 | 838wUNrKjJKVwM6ED9SdkKd9ZWZS2HUWXSToy5nL3shiSrNtqVMMDGYJ2w5DpF6WwPbGv8DLN7fftKXoPm4BAmfC6fvtrnx | | 0 | 0 | 2026-07-31 20:38:00 | 0.0 | 0.0 |
| 24 | PENDING_Shale | Shale | 0 | 0 | 2026-07-31 20:59:14 | 0.0 | 0.0 |
| 25 | PENDING_Nater | Nater | 0 | 0 | 2026-07-31 20:59:14 | 0.0 | 0.0 |
| 26 | PENDING_Grant stringham | Grant stringham | 0 | 0 | 2026-07-31 20:59:14 | 0.0 | 0.0 |
| 28 | 86NjWuTLKm7eggeGw9PZ5LjFDZTHUtAQtWs7Bbu783yYfDRqSd6itapDek39rySHWCax6fnDAKQQXLiieRtRqvzCPeqTxkG | | 0 | 0 | never | 0.0 | 0.0 |
| 29 | | | 0 | 0 | never | 0.0 | 0.0 |
| 30 | 4AoPP7szGLmG9qG1HQg6fXH84nyprSP4tbUTiCXmCoagWqAKZYGerv63kradKAqpufNZ5H3PGMNeDfrAUa9iZtiyCUMcCms | Birdsnbubbles | 19646 | 19785 | 2026-09-26 11:20:31 | 2.22 | 0.0 |
| 32 | 4ADebjRUa4DkupnGQtm8M8mLZBcHihRR2fVaw1MK5rYc1y4B8kC4x9VjHQnVJkLmNpQrStUvWxYz | | 0 | 0 | never | 0.0 | 0.0 |

Interpretation:
- The only miner with real share activity is Birdsnbubbles (id 30): 19646 valid shares out of 19785 total, 2.22 H/s, last seen 2026-09-26.
- Several miners are placeholder addresses or marked PENDING_*, with no shares and old last-seen dates in 2026-07-31.
- Miner 28 holds the kraken_deposit_address as wallet, but has no shares and never seen.
- Miner 29 has an empty wallet address.

So the mining surface is effectively a single active miner plus a few unverified/dormant entries.

---

## 9. Miner XP — progression layer

miner_xp table:

- miner_id=28: xp=0, level=1, streak=0, last_mine=None, lifetime_shares=0, badges=[], super=0
- miner_id=30: xp=20246, level=6, streak=1, last_mine=2026-09-26, lifetime_shares=19646, badges=["first_share","share_100","share_1000","share_10000","first_ticket","ticket_50"], super=19

Interpretation:
- Birdsnbubbles has a substantial XP and badge profile, matching the shared-history mining.
- 19 super tickets on the table — these are likely a separate spendable ticket class on top of the normal free tickets.

---

## 10. Users — accounts on the platform

users table (9 accounts):

- id=1 admin, email encrypted, wallet encrypted, admin=1
- id=2 Birdsnbubbles, email None, wallet encrypted, admin=1
- id=3 kitty, email encrypted, wallet empty, admin=0
- id=9 Grant stringham, email encrypted, wallet encrypted, admin=0
- id=10 Nater, email encrypted, wallet empty, admin=0
- id=11 Shale, email None, wallet None, admin=0
- id=12 MinerS, email None, wallet None, admin=0
- id=41 security_probe, email None, wallet empty, admin=0
- id=42 prefs_live_probe_fleet, email None, wallet empty, admin=0

Notes:
- Several user emails/wallets are encrypted (the enc1:... values). That matches the push-subscription and settings fields, which are also encrypted.
- The security_probe and prefs_live_probe_fleet accounts are clearly test/probing accounts created in September 2026.
- The PENDING_* miners map to Shale, Nater, Grant stringham — they have user accounts but no real wallet on file in miners.

---

## 11. Kraken referral / probe traffic

kraken_cta_click (slot totals):

- hero-top: 41
- footer-box: 33
- cta-refusal-bot: 25
- cta-refusal-limited: 6
- hero-badge: 2
- challenge-step1: 1
- last_30d kraken_cta_click: 108

kraken_referral_snapshot: present table with columns id, user_id, referral_id, kraken_status, total_paid, pending_amount, expected_bonus_conservative, expected_bonus_best, seeded_equivalent, estimate_used, fetched_at, source. No rows returned in this audit’s extraction.

kraken_referral_key_check: valid, tier=keyring, assets_visible=88, no error.  
kraken_referral_mode = simulate.

Audit log shows many `kraken_referral.unmapped` entries with reason "no kraken_referral_id on file (operator mapping needed)" for users: admin, Birdsnbubbles, kitty, Grant stringham, Nater, Shale, MinerS, security_probe, prefs_live_probe_fleet. That happened repeatedly across multiple scheduler ticks.

Interpretation:
- The kraken CTA surface is live and being clicked, especially hero-top and footer-box.
- The referral map is not populated for existing users; the scheduler keeps logging them as unmapped.
- The key itself is valid; the gap is user mapping, not connectivity.

---

## 12. Scheduler / scout behavior snapshot

From settings:
- SCHEDULER_LAST_WORKER = ran
- SCHEDULER_LAST_DIGEST = orphans=0 (0 edges) | pruned=0+0 | kc=none (x0)
- SCHEDULER_LAST_ORPHANS total=0, edges=0
- ORPHAN_DEADMAN_ALERTED = 2026-10-11
- PUSH_REPORT_PRUNED = 2026-10-15
- SESSIONS_PRUNED = 2026-10-15

Audit log (latest 200 rows) shows a repeating pattern of:
- push_broadcast / push_send_allowed for signal_buy and signal_exit, category=scout, delivered=1, sent=1, dead=0
- scheduler.notice_idle with configured=false, expired=false
- scheduler.worker_ran with mode=simulate, processed=0, errors=0, seed=0.0, anomaly=0, trigger=scheduler
- jackpot_projection.updated source=kraken_referral_sim_v1, possible=1.62, seed=0.0, users=0, errors=0, anomaly=0
- kraken_referral.unmapped repeated for all nine users

The audit log tail also includes push_broadcast events for signal_buy and signal_exit at timestamps:
- 1792101742.0, 1792101005.0, 1792100951.0, 1792100061.0, 1792100036.0, 1792099889.0, 1792098903.0, 1792098565.0, 1792097912.0, 1792097576.0, 1792096890.0, 1792096415.0, 1792095024.0, 1792094642.0, 1792094435.0, 1792094324.0, and earlier.

Interpretation:
- The scheduler is running, but the simulated worker is processing 0 and emitting 0 errors.
- Push broadcasts for scout signals are being emitted repeatedly.
- The orphan deadman alert fired on 2026-10-11, and the current orphan counts look clean.
- The projection is stuck at possible=1.62 with users=0 and seed=0.0.

---

## 13. Jackpot projection

jackpot_projection latest rows:

- id=437 future=1.62, referral_seed=0.0, current=1.62, users=0, anomaly=0, source=kraken_referral_sim_v1, updated_at=1792099609.0
- id=436..428 same shape, stepping down by ~3600s each (about hourly ticks).

Interpretation:
- The projection has saturated at possible=1.62 / current=1.62 with 0 users included.
- That means the kraken referral simulation is not contributing any user-based uplift into the projection yet.
- The 1.62 value appears to be a static figure in this mode rather than something growing from user activity.

---

## 14. Push and session hygiene

- active_sessions = 110
- push_subscriptions / push_prefs / push_send_rate: encoded blobs present
- push_broadcast rows in audit log indicate push delivery is happening for signal_buy and signal_exit
- PUSH_REPORT_PRUNED = 2026-10-15 and SESSIONS_PRUNED = 2026-10-15 indicate pruning jobs did run recently
- push_daily_report table exists for daily category rollups

Interpretation:
- Push subsystem is active and has subscriber state.
- Session count is moderately high; pruning has been performed recently.

---

## 15. Security surface notes

Positive:
- admin and Birdsnbubbles are both admin=1; the admin account has an encrypted email and encrypted wallet, consistent with the encrypted field scheme used elsewhere.
- api_key is present and active.
- VAPID keys and push operator token are present, so push assertions are configured.
- payment_verifications table has a richer schema than the original minimal design: credits_awarded, scanned_at, credited_at — that is a good maturation signal.

Watch-items:
- Several users have empty or None wallet_address, and some miners use PENDING_* placeholder wallets. This can make payouts and winner resolution ambiguous unless there is an explicit resolution step.
- kraken_referral.unmapped is firing for every user repeatedly, which suggests the referral linkage is not established for anyone yet.
- The house wallet address is a real Monero address, and the kraken deposit address is also present. The auditor has not verified control of those addresses; that should be a manual operator check.
- No payout rows exist, so the recent drawing’s winner payment status is unconfirmed by local tables.
- The live jackpot is negative against pending obligations, so any future funding or payout must be reconciled against this gap.

---

## 16. Installer / bundle posture

installer_ui.py and installer.py present.  
build_bundle.ps1 and install.ps1 present.  
Install Kraken Scout.bat and Uninstall Kraken Scout.bat present.  
README.md describes the pool as a Monero mining pool with a daily jackpot and free tickets per 100 shares, plus paid spins.

Interpretation:
- The installer story looks complete enough for a Windows bundle, with both install and uninstall entry points.
- The README is consistent with the pool mechanics we see in the DB.

---

## 17. Findings summary

Critical:
- The live computed jackpot is 0.00000000 XMR because pending drawing obligations (0.2961 XMR) exceed the stored jackpot balance (0.05 XMR). This is an accounting condition that should be resolved before any further draws or claims are advertised as solvent.
- Drawing 15’s claim deadline has expired and its claim_status is still "pending"; there is no payout row confirming payment or non-payment.

Important:
- Only one miner (Birdsnbubbles) has any real share activity.
- All kraken_referral records are currently "unmapped"; the key is valid, but user mapping is absent.
- The jackpot projection is flat at 1.62 with 0 users included in the simulation mode.
- Payouts, paid_spins, payment_verifications, and comments tables are empty.

Operational:
- The scheduler is running in simulate mode with processed=0 and no errors.
- Push broadcasts for scout signals are being emitted.
- Session and push hygiene jobs appear to run.

---

## 18. Traceability of this audit

- DB source: `pool/jackpot.db`
- Settings: rowid-ordered full settings table
- Drawings: full drawings table, timestamp-localized where possible
- Closed-loop jackpot: Python arithmetic from settings.jackpot_balance.confirmed_lucky_draw_xmr minus pending drawings
- Tickets: tickets table with drawing_id and miner aggregation
- Miners/XP/Users: direct live queries
- Kraken CTA / referral / scheduler: settings plus audit_log tail plus kraken_cta_click aggregation
- Payouts/verifications/comments: direct queries, all empty

No other project files were modified during this audit. The audit is reconstructable from the above queries and timestamps.
