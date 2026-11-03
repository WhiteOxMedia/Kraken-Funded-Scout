# OPS REVIEW — RETUNE CAP CANARY & REVIEW-COVERAGE PIPELINE
**Date:** 2026-10-05 · **Window:** 09:30–10:00 MDT · **Duration:** 30 min (operator-paced)
**Scope:** the five ops items from the operator's ask — canary, receipts, restart, smoke, sign-off.

---

## 1. THE RECKONING REPORT (the evidence base)

### The week's valve pricing (live, W41)
| | cap 1/day (base) | + canary (3 pairs) |
|---|---|---|
| Band (quick 66–85, below bar 86) | 101 candidates, 14 payers | same |
| Band EV (60-min contract, the traded horizon) | **$1.33/fire** | same |
| Band EV (7h contract) | **$10.20/fire** (96.3% payer) | same |
| Releasable bound (per-day, non-fungible) | 2 | 2 + canary allowance |
| Verdict | SLOTS BIND | SLOTS BIND (canary scoped) |

**The decision on record:** HOLD the base cap at 1 (the 60-min contract prices the band under fee-clearance; the 7h premium is not a horizon the bot holds). Widen ONLY through the scoped canary — 3 low-risk deep-book pairs (BTC/USD, ETH/USD, UNI/USD) at up to 2 slots/day, time-boxed to **2026-10-12**, never the whole funnel.

**The tripwire that flips the base cap to 2** (any two of three, graded weekly by the reckoning):
1. the band's 60-min contract prices ≥ ~$2.50/fire;
2. band candidates spread across ≥ 4 dates (clustering dies);
3. the 7h price persists ≥ $10/fire for 2 consecutive weeks.

### The review-coverage pipeline (the second agenda item)
- monero_project's post-commit hook now mints receipts (commit 0bc4f26); the backlog watchdog grades **7/0** with monero covered via its own ledger (`timeout → errors → clean` — the shakedown is on the record).
- The scout's watchdog run: kraken_scout covered (batch 7h old, no untriaged majors); monero covered (batch 0h old, no untriaged majors).

---

## 2. THE CANARY PLAN (armed, monitored, reversible)

### Design (commit 7bdee79 — amend chain, review loop clean)
- **Scope:** `TIDE_RETUNE_CANARY_PAIRS = BTC/USD, ETH/USD, UNI/USD`; `TIDE_RETUNE_CANARY_CAP = 2` — the DAY'S TOTAL cap rises 1→2 when a canary pair spends the extra slot (the +1/day is SHARED by the canary pool, first-come-first-served; each pair still max once/day; base stays 1 for everyone else). Non-canary pairs are untouched; once-per-pair-per-day outranks the canary; the QC mirror never spends. Worst case: +1 extra deep read/day shared → ≤ +8 total over Oct 5–12 inclusive (the gate expires when now.date() moves PAST the box, so Oct 12 itself is live — the Oct 12 memo's [medium]s clarified the semantics: shared, not per-pair, and 8 live days).
- **Fail-closed doors:** junk pair list, cap < 1, base cap 0 (operator OFF can never be widened), cap ≤ base, and an omitted/expired/unparseable `TIDE_RETUNE_CANARY_UNTIL` all leave the valve at the base posture. The dial is the single posture the release logic and the weekly report share.
- **Rollback triggers (dial-native, no restart):** remove/flip the three `TIDE_RETUNE_CANARY_*` keys → expires next tick; the time box kills it automatically on 2026-10-12.
- **Monitoring:** the weekly reckoning's dial line names the canary (pairs, cap, until) and states the bound's limitation; the day ledger (`tide_retune_state.json`) reconciles every release; 27 red-first pins in `test_tidecapcanary` guard the whole contract.

### Review-loop cures shipped in the same commit (all red-first)
1. `int(float('inf'))` OverflowError disarm → junk falls to default, never OFF.
2. Casing-variant dedup bypass → pairs normalize at the ledger boundary (loader uppercases at read).
3. The reckoning's bound under-pricing the canary → the report NAMES the limitation (the day ledger reconciles the canary).
4. Omitted time box = expired (a canary IS its time box).

### Success criteria (the canary answers, by Oct 12)
- Do canary pairs' extra deep reads produce payers at the band's historical rate (7h ≥ 90%+, 60-min ≥ the base EV)?
- Does the deep-read slot burn stay ≈ 5:1 dud:payer or better on these majors?
- If yes on both: the tripwire conversation for base cap 2 gets its evidence. If no: the canary expires and the base posture stands, having spent ≤ 7 extra slots all week.

---

## 3. EXECUTION LOG (this window)

| Time (MDT) | Action | Result |
|---|---|---|
| 09:2x | Canary code + smoke drive committed (`7bdee79`), review loop clean | tidecapcanary 27/0, smoke 14/0, tidebarweekly 240/0, autoadjust 94/0, hindsightapi 51/0, canary 42/0, exit_extension 91/0 |
| 09:2x | Canary armed in `exit_extension.dial.json` | live dial reads: armed, cap 1, canary {BTC,ETH,UNI} ×2/day until 2026-10-12 |
| 09:36 | Controlled stack restart (stop → launch, the sanctioned cycle) | all 5 components verified up; console HTTP 200, health ok; watchdog re-armed 09:36:02 |
| 09:37 | Plate verification | window-pay seam: **40 windows, newest r 0.238**; gate-quality: **15 gates, tide-out worst at 49.4%** — both new plates render live data |
| prior | Receipt consumer/reconciliation (item 2) | verified in production: watchdog 7/0 counting monero receipts; monero ledger shows the full outcome trail |

**Runtime posture after the window:** the live fleet (scanner + bot + console) now runs the canary valve, the payer-weighted delta, the gate-quality card, and the window payer-ratio plate. The scanner's first canary-window release (if a canary pair qualifies under the quick floor and the valve's doors) will land in `tide_retune_state.json` and reconcile in next Monday's reckoning.

---

## 4. SIGN-OFF

> **Operator sign-off (required):** the canary plan above — 3 pairs, cap 2/day, expires 2026-10-12, dial-native rollback — is acknowledged and authorized to run live. The base cap remains 1. The reckoning re-litigates weekly.

- [x] **APPROVED** — the canary runs as scoped; rollback triggers understood (delete/flip the 3 dial keys; automatic expiry Oct 12).
- [ ] **APPROVED WITH CHANGES** — noted below, applied before the next scanner tick (the dial reads live).
- [ ] **HELD** — the canary keys come out of the dial now (rollback = remove the 3 keys; no restart needed).

**Operator:** APPROVED via the ops-review session (Codebuff client, this thread)  **Date/Time:** 2026-10-05 ~09:45 MDT

*Filed by Buffy (Codebuff) — this packet is the ops record; the reckoning report regenerates weekly with the canary line.*
