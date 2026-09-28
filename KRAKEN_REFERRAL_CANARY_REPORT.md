# Kraken Referral Projection — Canary Run Report
**Date:** Sep 26, 2026 · **Commit:** `c615049` on `master` (branch ref: `feature/kraken-referral-projection-20260926`) · **Environment:** production box `xmrjackpot.com` (uvicorn :3094 via tunnel), live DB backed up first

## Commands executed (exit statuses)
| Command | Exit | Note |
|---|---|---|
| `cp pool/jackpot.db _backup_jackpot_pre_referral.db` | 0 | 126,976-byte backup before any write |
| `python -c "... db.init_db()"` (live DB) | 0 | additive migration: `kraken_referral_snapshot`, `jackpot_projection` (+ index) |
| `python -c "... kr.run_worker()"` (live DB) | 0 | canary run #1 (unconfigured mode) |
| `for t in unit integration redesign prefs e2e ...` | 0 | full drive battery |
| `taskkill //PID <pid> //F` + hidden `pool\server.py` relaunch | 0 | production restart ×2 |
| `curl https://xmrjackpot.com/api/jackpot/projection` | 0 | live read model verified |
| Browser: card render + methodology modal + screenshots | ok | evidence captured |

## STOP CONDITION TRIGGERED — S0: no Kraken API credentials
`provider_mode() == "unconfigured"`: **no `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` / `KRAKEN_REFERRAL_ENDPOINT` exist anywhere in the system config** (verified: zero hits across `pool/*.py`, `installer/*.ps1`, and the live `settings` table). Per the charter's own honesty rules the worker **refused every live fetch** instead of fabricating referral money. The run completed in the fail-closed posture:
- 3 users audited `kraken_referral.unmapped` (no referral id on file)
- first mapped user audited `kraken_referral.fetch_error {"reason": "unconfigured", "error": "no Kraken API credentials provisioned"}` → worker halted (no speculative emails, no guessing)
- **0 snapshot rows written; projection persisted with source `kraken_referral_unconfigured_v1`, seed 0.00**
- UI card displays the REAL current jackpot ($1.62) with the notice "referral projection: not configured — showing current jackpot only"; the methodology modal shows source, mode, formula, and timestamp

Also noted: this site's `referral_code` is the INTERNAL invite system (user→user). There is no `kraken_referral_id` column; if Kraken ever returns per-user referral ids, they will land in `kraken_referral_snapshot.referral_id` and the discovery mapping will need that column (reversible one-line migration, same pattern as `phone`/`full_name`).

## What ran and was proven (sandbox/stub, all green)
| Drive | Result |
|---|---|
| `test_kraken_referral_unit.py` | **20 / 0** — normalization (Paid/Pending/Eligible/Ineligible/unknown, garbage money, non-dict), bonus formula (explicit wins, velocity×confidence, zero-velocity fail-closed, Paid floor), Kraken HMAC-SHA512 signing, provider honesty (unconfigured refuses), notice event registered |
| `test_kraken_referral_integration.py` | **35 / 0** — full pipeline through the real FastAPI app: discovery + unmapped audit, operator trigger (503 unconfigured / 200 simulate), simulate-mode run (6 mapped, statuses→rows, aggregate = Σ contributions, audits), `/api/jackpot/projection` (possible = current + seed, fresh, provenance), auth gates, **stubbed live Kraken**: success (6 rows), rate-limit recovery via exponential backoff, persistent-5xx degradation with `fetch_error` audits and genuine backoff delay, credential-removal downgrade, **delta-spike guard** (seed collapse 60→0 → `anomaly=1` + `jackpot_projection.delta_spike` audit), `referral_window` per-user 2/h cap + broadcast-limiter audit |
| `test_redesign_push.py` / `test_push_prefs_api.py` / `test_e2e_acceptance.py` | 68/0 · 10/0 · 34/0 (charter event-set pin extended transparently for `referral_window`) |

## Aggregated numbers (live)
- `possible_future_jackpot = $1.62` = real engine pot `$1.62` + `referral_seed $0.00` (0 user statuses — unconfigured, honest zero)
- `jackpot_projection.updated` audit: `{"source": "kraken_referral_unconfigured_v1", "possible": 0.0→1.62 after baseline fix, "seed": 0.0, "users": 0, "errors": 1, "anomaly": 0}`
- Live API: `GET /api/jackpot/projection → 200 {"possible_future_jackpot_usd":1.62,"current_jackpot_usd":1.62,"referral_seed_usd":0.0,"source":"kraken_referral_unconfigured_v1","mode":"unconfigured","fresh":true,"anomaly":0}`

## Sample snapshot rows (masked)
**None on the live DB — 0 rows is the honest result of S0.** The integration drive demonstrates the row shape the moment credentials exist:
`{"user_id": 2, "referral_id": "SIM-PEND-002", "kraken_status": "Pending", "total_paid": 0.0, "pending_amount": 25.0, "expected_bonus_conservative": 25.0, "expected_bonus_best": 25.0, "seeded_equivalent": 25.0, "estimate_used": 0, "source": "kraken_referral_sim_v1"}`

## Monitoring hooks (as built)
`kraken_referral.fetched` / `kraken_referral.unmapped` / `kraken_referral.fetch_error` (per user, audit rows) · `jackpot_projection.updated` / `jackpot_projection.delta_spike` (>25% configurable via `KRAKEN_PROJECTION_SPIKE_PCT`) · `push_send_allowed` / `push_rate_limited` incl. the new broadcast-scope rows. The worker summary dict reports `mode/source/processed/unmapped/errors/referral_seed/possible_future_jackpot/anomaly/batch_size/duration_s` per run.

## Defects found and fixed during this arc (own tests caught them)
1. `result`-extraction precedence bug in the live client (single-object `result` payloads were subscripted like lists) — caught by the stubbed live test.
2. Unmapped users were invisible to discovery (WHERE excluded them) — the charter's unmapped branch would never have run.
3. Baseline incoherence: the projection used the daily-accrual formula while the UI hero shows the engine pot — card read $0.00 under a $1.62 hero. Baseline is now `engine.get_pool_stats()` (the same number the plate renders).
4. Broadcast-level rate-limit refusals were silent (no audit) — now audited `push_rate_limited {"scope": "broadcast"}`.

## Remediation / what the operator must do to go live
1. **Provision Kraken credentials** (settings only, never code): `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `KRAKEN_REFERRAL_ENDPOINT` — then the operator trigger `POST /api/admin/kraken-referral/run` executes the live pipeline; spot-check `kraken_referral_snapshot` rows against the Kraken console before trusting the aggregate. **Note:** Kraken has no public per-user referral-status API — the endpoint must be the operator's actual source (Kraken private endpoint or an intermediary ledger export); the client adapts to `{error, result}` and `{data}` shapes.
2. If per-user Kraken referral ids become available, add `kraken_referral_id` to `users` (one-line reversible migration) and extend discovery.
3. Rollout ladder (charter): canary 1% → monitor `fetch_error` rate + `delta_spike` → 10% → 50% → 100%. The `subset_limit` body parameter on the operator trigger implements the canary slices.
4. The `referral_window` expiry notice is wired and rate-capped (2/h/user); call `kraken_referral.notify_window_closing()` from the window-schedule job when the operator defines the expiry calendar — and provision VAPID keys (see E2E report F5) or no phone will actually receive it.
5. Deletion/rollback: `git revert c615049` + server restart; the two new tables are additive and can be dropped without touching existing schema; `_backup_jackpot_pre_referral.db` is the pre-change image.
