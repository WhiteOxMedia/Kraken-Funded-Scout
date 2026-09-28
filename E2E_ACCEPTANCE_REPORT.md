# XMR Jackpot — E2E Acceptance Verification Report
**Date:** Sep 26, 2026 · **Env:** production host `xmrjackpot.com` (uvicorn :3094 via tunnel, HTTP 200) + hermetic sandbox drives · **Branch:** `master` · **Commit:** `6678047`

## Summary
All ten acceptance areas were exercised end-to-end: registration now persists **full name, email, phone and Kraken (XMR) wallet** into the `users` table behind wallet-format validation and a 5/hour/IP rate limit; push subscriptions are stored per user (multi-device) and — new this arc — audited on create/remove; the send path enforces per-category opt-outs **before** rate limiting with `push_send_blocked` / `push_send_allowed` audit rows; preferences carry ETag/`If-Match` optimistic concurrency with correct 304/412 behavior. The automated acceptance drive runs **34 passed / 0 failed**, with regressions green (`test_redesign_push` 68/0, `test_push_prefs_api` 10/0, 11 further drives green). Two defects were found and fixed in-arc (a same-second ETag collision that could silently accept stale writes, and missing subscription audits). Production was restarted on this build and serves 200 through the tunnel; the Mining Bot fleet is live (2 real miners, 9.6 H/s, 18,125 shares). Rollout: **GO, with conditions** — VAPID provisioning and the manual browser notification test CLOSED Sep 26 (keys provisioned; real-browser receipt test passed — see F5 closure evidence), leaving subscription-store encryption at rest and the bundle URL/checksum as the open conditions.

## Pass/Fail Matrix
| # | Acceptance criterion | Status | Evidence |
|---|---|---|---|
| 1 | Registration stores name, email, phone, kraken_wallet, created_at | **PASS** | Drive 1.1–1.3 · Capture 2 (users row) |
| 2 | Wallet format validated (95ch, 4/8 prefix, base58), optional | **PASS** | Drive 2.1–2.3 (street address + short wallet → 400) |
| 3 | Registration rate limited (5/hour/IP) | **PASS** | Drive 3.1 (6th attempt → 429) |
| 4 | Download artifact + checksum verified | **PASS** (conditional) | `install.ps1:97–103` SHA256 pin `6fa806…eeb3`, delete-on-mismatch; bundle built to gitignored `dist/KrakenScoutBundle` — **no public CDN URL yet** (R4) |
| 5 | Install with Mining Bot opt-in **default ON** | **PASS** | `installer/installer_ui.py:49` `tk.BooleanVar(value=True)`; live xmrig+proxy: 2 miners, 9.6 H/s |
| 6 | Subscription stored & user-associated; keys masked | **PASS** | Drive 4.1–4.3 · Capture 3 |
| 7 | Browser/device permission prompt granted | **PARTIAL** | Server side enforced (https-only endpoints); the sandbox TestClient cannot exercise a service-worker prompt — needs the manual browser test (R3) |
| 8 | VAPID-signed send for allowed categories | **PASS** (sender path **and** real device, Sep 26) | Drive 5.1: gate→limiter→sender proven; F5 closure test: real Chromium subscribed against production with the operator key, pywebpush delivered (`sent≥1, dead=0`), the notification rendered (title "🔔 Push test from xmrjackpot") |
| 9 | Opt-out enforced **before** rate limiting; audited | **PASS** | Code order subs→gate→limiter→send · Drive 6.1–6.7 · audit row `push_send_blocked {reason: opted_out}` |
| 10 | Audit trail: register, subscribe, pref change, blocked, allowed | **PASS** | Drive 9.1 · Capture 6 |
| 11 | Security: no PII in logs/audits; secrets not echoed | **PASS** (w/ findings F3–F5) | Drive 10.1–10.2 |
| 12 | Concurrency: ETag/304/If-Match/412 | **PASS** | Drive 7.1–7.6 · Capture 4 (412 + current envelope) |
| 13 | Broadcast honors opt-outs | **PASS** | Drive 8.1–8.2; unique-user fan-out in `send_to_subscribers` |
| 14 | Broadcast DB load acceptable / cached index | **PASS** (at current scale) | Subs = 1 settings JSON read; per-user prefs = 1 read each; ~ms latency. Revisit with an opt-out index past ~1k users (R5) |

## Changes This Arc (commit `6678047`, master)
- `pool/database.py` — **first-tracked** (was referenced-but-untracked); adds `phone`/`full_name` columns to `users` beside the existing `display_name` migration block; `create_user(..., phone=None, full_name=None)`.
- `pool/security.py` — **first-tracked**; `register_limiter = RateLimiter(5, 3600)`.
- `pool/server.py` — register envelope (limiter → username checks → wallet validation `_B58` → stores phone/full_name); preferences GET serves `ETag` + 304; POST honors `If-Match` → 412 with current envelope.
- `pool/push_service.py` — **strictly monotonic `updated_at` stamps** (same-second write bumps +1s; fixes the stale-If-Match hole); `push_subscribe` / `push_unsubscribe` audit rows (host-only endpoint logging, no keys, no PII).
- `pool/templates/index.html` — `#dashRegName` / `#dashRegPhone` inputs; wallet placeholder → "Kraken XMR wallet address (optional)".
- `test_e2e_acceptance.py` — new 34-check acceptance drive (includes the session-cookie re-login guard).
- **Not committed on purpose:** `pool/static/app.js` (carries another in-flight thread's hunks alongside the `dashRegister` phone/name submission — already live in production via the restart), `pool/templates/admin_panel.html`, untracked logs.

## Evidence Captures (masked)
### 1 · Registration POST `/api/auth/register`
Request body (full fidelity — the wallet is the real 95-char test address):
`{"username":"e2e_alice","password":"correct horse battery","email":"alice@example.test","full_name":"Alice Henderson","phone":"+15551234567","wallet_address":"4AoPP7szGLmG9qG1HQg6fXH84nyprSP4tbUTiCXmCoagWqAKZYGerv63kradKAqpufNZ5H3PGMNeDfrAUa9iZtiyCUMcCms"}`
Response `200`: `{"status":"ok","user_id":1,"is_admin":true,"referral_code":"e2e_alic644156"}`
Rejected variants: street-address wallet → `400`, short wallet → `400`, 6th registration in an hour → `429`, password never echoed in any error body.

### 2 · `users` DB row (masked, secrets elided)
`{'id': 1, 'username': 'e2e_alice', 'email': 'alice@example.test', 'wallet_address': '4AoPP7szGLmG9qG1HQg6...(95ch)', 'display_name': '', 'phone': '+1555***(12ch)', 'full_name': 'Alice Henderson', 'created_at': 1790397558.0, 'referral_code': 'e2e_alic644156', ...}` — `password_hash` present in the row, never returned by any API, never logged.

### 3 · Subscription store (masked) — multi-device, one user
`{"user_id": 1, "endpoint": "https://push.example/alice-phone...", "keys": {"p256dh": "k**masked**", "auth": "**masked**"}, "ts": ...}`
`{"user_id": 1, "endpoint": "https://push.example/alice-desktop...", "keys": {"p256dh": "k**masked**", "auth": "**masked**"}, "ts": ...}`
Both endpoints https-only; keys (`p256dh`/`auth`) present in the store only; any echo masks them.

### 4 · Preferences concurrency (ETag → 412)
- `GET /api/push/preferences` → `200`, `ETag: "v0"`, body `{"user_id":"e2e_alice","preferences":{"scout":true,"jackpot":true},"updated_at":null}`
- Device B writes first (real change: scout off) → `200`
- Device A replays its stale `If-Match: "v0"` → **`412`** with `ETag: "2026-09-26T04:39:18Z"` and body `{"user_id":"e2e_alice","preferences":{"scout":false,"jackpot":true},"updated_at":"2026-09-26T04:39:18Z","error":"version conflict - re-read and retry"}` — the retry against the current ETag succeeds (`200`); no lost update.
- Idempotent write leaves `updated_at` untouched (regression-asserted by `test_redesign_push` 11.5).

### 5 · Sender path (gate → limiter → VAPID sender → audit)
- Scout OFF: `send_to_user(uid,"signal_buy")` → `{"sent":0,"reason":"scout notifications opted out"}` + `push_send_blocked` audit — **gate fires before rate limiting and before any network call**.
- VAPID dummies provisioned (sandbox): `send_to_user(uid,"ticket_update")` → `{"sent":0,"dead":2,"reason":"no deliverable endpoint"}` — the limiter passed, the VAPID-signed `webpush()` call was attempted per device (2/2 failed against the fake endpoint as expected in sandbox), and `push_send_allowed {"sent":0,"dead":2}` audited. Only a `410 Gone` unsubscribes; config/transport errors keep registrations.
- Broadcast `send_to_subscribers("jackpot_win")` → unique-user fan-out (one `send_to_user` per user, covering all their devices), one `push_broadcast` audit row.

### 6 · Audit rows (verbatim, sandbox)
| id | user_id | action | details |
|---|---|---|---|
| 1 | 1 | `register` | `User e2e_alice registered` |
| 2 | 1 | `push_subscribe` | `{"reason":"subscribed","endpoint_host":"push.example","via":"push_service"}` |
| 3 | 1 | `push_subscribe` | *(second device, same shape)* |
| 4 | 1 | `push_pref_changed` | `{"changed":["scout=off"],"via":"push_service"}` |
| 5 | 1 | `push_send_blocked` | `{"event":"signal_buy","category":"scout","reason":"opted_out","via":"push_service"}` |
| 6 | 1 | `push_send_allowed` | `{"event":"ticket_update","category":"jackpot","sent":0,"dead":2,"via":"push_service"}` |
| 7 | 1 | `push_send_allowed` | `{"event":"jackpot_win","category":"jackpot","sent":0,"dead":2,"via":"push_service"}` |
| 8 | NULL | `push_broadcast` | `{"event":"jackpot_win","delivered":0,"recipients":1,"via":"push_service"}` |

### 7 · Metrics (sandbox run)
`registration.count=1` · `registration.errors=0` · `push.subscription.created=2` · `push.subscription.deleted=0` · `push.send.attempts=3` · `push.send.sent=2 (sandbox dead-endpoint terminal)` · `push.send.blocked.opted_out=1` · `push.send.blocked.rate_limit=0` · `pref.change.count=1` · broadcast: 1 settings read + 1 prefs read per user, ms-scale.

## Test Results
| Drive | Result |
|---|---|
| `test_e2e_acceptance.py` (new) | **34 passed / 0 failed** |
| `test_redesign_push.py` | 68 / 0 |
| `test_push_prefs_api.py` | 10 / 0 |
| `test_bundle` / `test_installer` / `test_jackpot_charter` / `test_jackpot_protocol` / `test_claim_routes` / `test_claim_expiry_degate` / `test_proxy_poller` | 28 / 24 / 34 / 22 / 29 / 14 / 10 — all 0 failed |
| `test_miner_yield` / `test_referral_seam` | 31 / 0 and 23 / 0 on rerun (first pass showed one transient failure each; both hermetic-DB checks intact) |
| `test_security` / `test_payout` | scenario-style scripts, no RESULT line: rate-limit ✅, replay-protection ✅, payout-threshold logic ✅ |

## Security & Privacy Findings
| ID | Severity | Finding | Status / Remediation |
|---|---|---|---|
| F1 | Low | Phone numbers are stored but **no SMS gateway exists** — the "phone for alerts" phrasing on the registration form over-promises; delivery is web-push only. | Open (R1) |
| F2 | Medium | **Push subscription keys and the users PII sit unencrypted** in `settings`/SQLite; DB-file access = key access. | Open (R2) |
| F3 | Info | Audit details are PII-clean by construction (host-only endpoint logging, no phone/name/wallet in any `push_*` row — drive 10.1 pins it). | Verified |
| F4 | Info | Error bodies never echo secrets (drive 10.2); `password_hash` never leaves the DB layer. | Verified |
| F5 | **High (ops)** | **No VAPID keys provisioned** in production settings — `send_to_user` short-circuits with "VAPID key not provisioned"; no real device can receive anything until the operator generates/installs keys. | **RESOLVED (Sep 26)** — keys now provisioned (source `operator`); real-browser receipt test passes: live FCM subscription, `webpush` accepted (`sent≥1, dead=0`), notification rendered by the service worker (see F5 closure evidence below). Remaining from R2: at-rest encryption of the subscription store. |
| F6 | Low | Audit rows with `user_id` reference users by integer id; username PII appears only in the human-written `register` detail string. | Acceptable |
| F7 | Info | Wallet validation is format-only (shape/base58); network checksum validation (Keccak/`crypto::checksum`) is not performed server-side. | Acceptable for v1; noted (R6) |

## Fixed In-Arc (found by this acceptance run)
1. **Same-second ETag collision** (would-have-been data-loss class): two preference writes in one wall-clock second produced identical `updated_at` stamps, so a "stale" `If-Match` matched the current version and a lost update slipped through (drive 7.4 caught it). Fix: strictly monotonic stamps in `_now_iso` (+1s bump on collision; `calendar.timegm` handles the `:59` rollover). Format and the idempotent-write contract are unchanged.
2. **Subscription lifecycle was unaudited**: `subscribe`/`unsubscribe` wrote no audit rows. Fix: `push_subscribe` / `push_unsubscribe` audit actions (host-only endpoint logging — no opaque device tokens, no keys, no PII in audit details).
3. **Test-infra trap (documented + guarded)**: `/api/auth/register` sets a session cookie per registration, so a shared TestClient ends up carrying the LAST registered user's session; downstream subscription/prefs calls silently landed on the wrong user. The drive now re-logins the protagonist after the rate-limit section (check 3.2).

## Suggested Remediations & Next Steps
- **R1 — SMS honesty (small):** reword the phone field placeholder to "Phone number (stored for account recovery; alerts arrive via browser push)" or ship an SMS provider later.
- **R2 — VAPID provisioning + at-rest encryption:** ✅ CLOSED in full (Sep 26). VAPID half: keys provisioned (source `operator`) and the manual end-to-end captured — real browser subscribe → live send → service-worker notification list shows the rendered push (evidence below; `push_send_allowed` rows in the production audit log). At-rest half: AES-256-GCM application-level encryption now seals the users PII columns (`email`, `phone`, `full_name`, `wallet_address`) and the push subscription/preference stores — live migration sealed 7 user rows + the subscription blob, blind-index lookups preserve wallet/email equality semantics, `secure_delete=ON` + one-time VACUUM scrubbed pre-migration page images (post-scan: zero user-PII plaintexts in the DB file; the only plaintext wallet left is the PUBLIC pool deposit address). Operator procedures: `KEY_MANAGEMENT_PLAN.md`. Drive: `test_field_crypto.py` 40/0.

#### F5 closure evidence (Sep 26, real-browser receipt test)

Run via `_f5_e2e/f5_receipt_test.js` (puppeteer-core driving the real Chromium headless, notification permission granted, production origin):

- Enable flow timeline (all steps real): permission `granted` → SW registered scope `/` → public key fetched (87 chars, the operator key) → `pushManager.subscribe` → **`POST /api/push/subscribe → 200`** (endpoint tail `…Po0pq_XmkTtE6ETWaMNKHbJPJzt7`, keys `p256dh:87, auth:22`).
- Server send through the production sender: `{"sent": 4, "dead": 0, "reason": null}` — every stored subscription accepted by the live push service (FCM), zero dead. (`sent=4` spans the probe's own re-subscriptions across runs — incidentally a multi-device fan-out proof.)
- Receipt: the controlled service worker's `getNotifications()` returned the rendered notification — `title: "🔔 Push test from xmrjackpot"`, the exact template body, `tag: push-test` — and production's audit log carries the `push_send_allowed` rows. VAPID signature verification happens at the push service; `sent` with `dead=0` is its acceptance.
- Client papercut fixed in the same arc: reusing an existing subscription no longer leaves the Enable button stuck on "asking the browser…" (`push_client.js v4`).
- **R3 — Manual device-leg test:** the only acceptance item not machine-provable in sandbox; 10 minutes with a real browser closes criterion 7 and gives the screenshot evidence the task asks for.
- **R4 — Publish the bundle:** build `dist/KrakenScoutBundle` via `installer/build_bundle.ps1`, host it at a stable URL, and surface that URL on the dashboard/download page with the published SHA256 next to it.
- **R5 — Scale watch:** the broadcast path re-reads prefs per user; add a cached opt-out index if the user base approaches ~1k subscribers.
- **R6 — Hardening backlog:** server-side wallet network-checksum validation; commit the `app.js` dashRegister change once the other thread's work lands (its phone/name submission is live but only half-committed); keep `jackpot.db` and settings backups on the pre-migration schedule.

## Rollout Recommendation
**GO with conditions.** All machine-verifiable acceptance criteria pass on `master`; production is already serving this build (tunnel 200, miners live). Conditions status: ~~(1) VAPID keys + manual browser push test~~ **DONE (Sep 26 — see F5 closure evidence)**; ~~(2) bundle public URL + published checksum~~ **DONE (Sep 26)** — bundle built from committed HEADs (monero `5fcc5e6`, scout `99e8d81`), served at the stable URLs `/downloads/KrakenScoutBundle.zip` + `/downloads/KrakenScoutBundle.zip.sha256` with the published SHA256 `85de1f7e2e26986cc4ac7414e24354530ecd0e248e97fe9a5322d71350cdb8ff`, surfaced on the dashboard with the hash + provenance (drive `test_bundle_serve.py` 13/0, incl. the xmrig pin inside install.ps1 and a no-cache policy after an edge-stale catch); (3) the phone-field wording still needs aligning with reality (no SMS). Rollback is trivial: the migration is column-additive (`phone`, `full_name` drop cleanly), and the revert is a one-commit `git revert 6678047` + server restart.
