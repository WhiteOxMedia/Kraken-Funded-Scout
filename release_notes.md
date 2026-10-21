## Kraken-Funded-Scout — Release v1.0.0-preprod

### Summary
Final pre-production pass for the Kraken-Funded-Scout repository.

This is the first commit to the repository, establishing the working baseline for the project.

### Changes
- Remove dead wallet-connect modal code: `showWalletConnectModal()` / `hideWalletConnectModal()` and their landing CTA listeners deleted; buttons now navigate directly to the inline wallet field (dashboard editor when signed in, signup when not).
- Remove dead push test button wiring: `wireTestButtons()` and its `TEST_MSGS` table deleted — they referenced `btnTestJackpot` / `btnTestScout`, which no template carries, and were failing the Hostinger corrections gate (check 3.2).
- Profile alert log storage: append-only `profile_alert_log` table with a busy_timeout on the shared connection so concurrent appends wait instead of failing with “database is locked”.
- Profile alert log API: `GET /api/profile/alert-log` returns the full member history (newest first), `POST /api/profile/alert-log` records a fired alert with a timestamp.
- Image-tree assertions updated: now assert the legacy miner sprite assets are absent and no sprite-shaped leftover appears under any name; system_check.py treats a still-served sprite URL as a stale-bundle failure.
- Review bundle rebuilt to gate parity so the deploy artifact matches the current source.

### Verification
- Full test suite: 49 files, 1287 checks, 0 failures; all exit 0.
- Bundle parity: gate passes (12/12), zip + sha256 redistributed.
- Commit landed: `92a1436` on `main` via HTTPS push (the prior SSH path had no accepted host key and no matching local key, so the push used `gh`'s authenticated HTTPS session).

### Notable decision
The branch-protection work on `main` was deferred: the live repository-rulesets API rejected every candidate ruleset shape tried (required per-rule `enforcement` and exact `parameters` shapes the live server would not accept), and the legacy classic branch-protection PUT is not available on this personal repo. The protections requested — require PRs, block force pushes, require status checks — were therefore NOT applied. The tag + release below still proceed from commit `92a1436`.

### Tag
- `v1.0.0-preprod` — points at `92a1436`.
