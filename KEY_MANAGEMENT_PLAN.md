# KEY MANAGEMENT PLAN — Application-Level Encryption at Rest

**Scope:** users PII columns (`email`, `phone`, `full_name`, `wallet_address`)
and the push-subscription store (`settings.push_subscriptions`).
**Cipher:** AES-256-GCM (AEAD) via `pool/field_crypto.py`; envelope
`enc1:<key_id>:<b85(nonce‖ciphertext‖tag)>`; the key id is bound as AAD.
**Status:** live in production since Sep 26, 2026 (existing rows + subs blob
sealed at boot; `secure_delete=ON` set; VACUUM scrubbed pre-migration page
images; only remaining plaintext wallet in the file is the **public** pool
deposit address in `miners`/`settings`, which is public by design).

---

## 1. Key material

| Property | Value |
|---|---|
| Key | 32 random bytes (`secrets.token_bytes(32)`) — AES-256 |
| Storage (default) | `pool/.keys/field_key` — one hex line, chmod 600, **gitignored** |
| Storage (headless alt) | `FIELD_KEY_HEX` env var — used only when the key file is absent |
| Key id | 8 hex chars (non-secret label), stored INSIDE each envelope and bound as AEAD AAD |
| Derivation | Blind indexes use `SHA256(key ‖ "blind-index:" ‖ domain)` — never the AES key directly |

## 2. Daily operation

- **Nothing to do.** The active key is picked up automatically; every write
  seals; every read opens transparently (`get_user`, `get_all_users`,
  `get_user_by_wallet`, `email_exists`, the push send path).
- If `pool/.keys/field_key` is missing **and** `FIELD_KEY_HEX` is unset,
  the system runs in **plaintext-compat mode**: new values store as before,
  existing envelopes stay sealed and read back with a loud
  `<col>_error: "undecryptable"` flag instead of fake data. No crash, no
  silent corruption.

## 3. Backup (operator requirement — do this TODAY)

The key file is the ONLY way to open existing envelopes. Losing it loses the
PII columns (usernames/passwords/jackpot state survive; wallets, emails,
phones, names and push subscriptions do not).

1. Copy `pool/.keys/field_key` to storage **outside the DB host**
   (encrypted USB, password manager attachment, or an offline printout).
2. Verify the copy: `python -c "print(open('<backup>','r').read() == open('pool/.keys/field_key','r').read())"`
3. Record the key id (`253c907c` for the currently deployed key) in the
   operator log. The key file itself must never be committed, emailed
   unencrypted, or stored on the same disk as `jackpot.db`.

## 4. Rotation (compromise response or periodic hygiene)

1. **Back up first**: `jackpot.db` + the current key file.
2. Run rotation:
   ```powershell
   .venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'pool'); import field_crypto as fc; print(fc.generate_key_file())"
   ```
   The old key file moves to `.keys/field_key.retired.<old-id>` and stays
   **decrypt-only**; new writes seal under the new key immediately.
3. Re-encrypt stored data (reseals every envelope + rebuilds blind indexes
   under the new key):
   ```powershell
   .venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'pool'); import database as db; print(db.reencrypt_pii())"
   ```
   Push subscriptions reseal lazily on the next subscription write.
4. After confirming reads (login, admin panel, a test push), move the
   retired key file **off-host** and delete it locally.
5. Rotating invalidates NOTHING at Kraken/Facebook/etc. — the VAPID keys are
   separate and are rotated via the admin VAPID card (that rotation DOES
   require a browser re-subscribe campaign).

## 5. Failure / incident playbook

| Event | Effect | Recovery |
|---|---|---|
| Key file deleted, no backup | PII reads flag `undecryptable`; logins by username OK | Restore from the operator backup into `pool/.keys/field_key`; restart |
| Key file corrupted | Same as above for rows sealed by it | Restore; `db.reencrypt_pii()` to reseal everything |
| DB restored from old backup | Old rows may be plaintext or sealed by a retired key | Boot migration seals plaintexts automatically; run `reencrypt_pii()` for the rest |
| Suspected key compromise | Plaintext exposure risk | Rotate (section 4); audit `kraken_referral.*`/admin access rows; consider VAPID rotation too |

## 6. Guarantees and limits (honest scope)

- A stolen `jackpot.db` (backup leak,host compromise without the key dir)
  yields **no** PII, no push endpoints/keys, and no usable wallet-index
  correlations beyond row counts.
- The application (and anything that can read the key file or the process
  memory) can still read plaintext — this is encryption at rest, not DRM.
- `secure_delete=ON` + the one-time VACUUM scrub pre-migration page images;
  WAL/SHM sidecar files hold at most current ciphertext under normal
  operation. Host pagefiles/swap are out of scope.
- The pool's own Kraken **deposit address** and miner payout addresses are
  public by design and are intentionally NOT encrypted.

## 7. Verification anchors

- Drive: `test_field_crypto.py` (40 checks) — envelope semantics, at-rest
  sealing, blind-index lookups, migration idempotency, key-loss mode,
  full rotation cycle.
- Live evidence (Sep 26): boot log line
  `[crypto] at-rest migration: {'users_sealed': 7, 'subs_blob_sealed': 1}`;
  post-VACUUM scan — zero user-PII plaintexts in the DB file bytes;
  register→read→lookup all exercised against production.
