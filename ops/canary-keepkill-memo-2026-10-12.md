# CANARY KEEP/KILL MEMO — THE RETUNE CAP CANARY (due 2026-10-12)
**Decision date:** Monday 2026-10-12 (the canary's `TIDE_RETUNE_CANARY_UNTIL` expires this day)
**Authorized by:** the Oct 5 ops review (APPROVED — `ops/ops-review-2026-10-05.md`)
**Data sources for every blank below:** `_tidebarweekly.txt` → "THE CAP CANARY'S WEEK" block + the dial line; `tide_retune_history.jsonl` (the release journal, live since Oct 5 ~10:47); `tide_retune_state.json` (the day ledger); `snapshot_outcomes.jsonl` / `contest_trades.jsonl` (the payer grades). Run `.venv\Scripts\python.exe -X utf8 test_tidebarweekly.py`-grade truth by reading the weekly report directly — every number in this memo is copied from it, never remembered.

---

## 1. WHAT WAS AUTHORIZED (pre-filled from the ops packet — do not edit)

| Item | Value |
|---|---|
| Scope | `TIDE_RETUNE_CANARY_PAIRS = BTC/USD, ETH/USD, UNI/USD` |
| Canary cap | `TIDE_RETUNE_CANARY_CAP = 2` slots/day per the valve's doors (base cap stays **1**) |
| Time box | `TIDE_RETUNE_CANARY_UNTIL = 2026-10-12` — **the canary dies by itself on this date** |
| Worst-case exposure | ≤ +1 extra deep read/day/pair, ≤ **+7 total** over the window |
| Rollback (any time) | delete/flip the 3 `TIDE_RETUNE_CANARY_*` keys in `exit_extension.dial.json` — live next tick, no restart |
| Guardrails on record | once-per-pair/day outranks the canary; QC mirror never spends; fail-closed doors (junk pairs, cap<1, base OFF, cap≤base, omitted/expired box); journal leak gate pinned |

## 2. THE BASELINE (Oct 5, captured at authorization)

- Band (quick 66–85, below the in-force bar 86): **101 candidates, 14 payers**, 60-min contract **$1.33/fire**, 7h contract **$10.20/fire** (96.3% payer) — the W41 reckoning.
- Verdict: **SLOTS BIND** at base cap 1 (releasable bound 2 on the week's clustering shape).
- The valve's releases to date: 1 (UNI/USD, Oct 3, pre-canary — released at base cap 1; failed its own deep read downstream).
- The journal started EMPTY Oct 5 ~10:47 (the running scanner predates the journal code; the pollution incident's 70 test rows were drive junk and were purged — see §6).

## 3. THE WEEK'S NUMBERS (fill from the reckoning's canary block on Oct 12)

From `THE CAP CANARY'S WEEK` in `_tidebarweekly.txt` (or run the reckoning):

| Metric | Value | Source |
|---|---|---|
| Canary releases (journal rows, trailing 7d) | _____ | journal count |
| Releases graded (popup joined / fill joined) | _____ | block lines |
| Payers (paper ≥ 1.5% mfe, or fill `pnl_net` > 0) | _____ | block lines |
| Real fills joined (and net $) | _____ | block lines / `contest_trades.jsonl` |
| **Payer rate vs the 50% keep bar** | _____% | block verdict line |
| Base-cap releases in the same window (non-canary) | _____ | day ledgers / journal |
| Deep-read slot burn on canary pairs (dud:payer) | _____ : 1 | popup outcomes |

## 4. THE DECISION RULES (pre-filled from the ops packet — do not edit)

**KEEP (raise the case to base cap 2, or re-arm the canary for a second week):** payer rate ≥ **50%** of graded canary releases AND slot burn ≤ **5:1** dud:payer on the canary pairs. (The band's 7h contract prices 96% payers — if the canary's realized grades land anywhere near that, the cap-1 posture is leaving money buried.)

**KILL (let the box expire — do nothing):** payer rate < 50%, or slot burn worse than 5:1, or fewer than 3 graded releases (the sample is too thin to widen on — re-arm only with a concrete no-recurrence story). Killing costs nothing: the canary already expired by clock.

**ESCALATE (to the operator before Oct 12):** any canary release that lost money on a REAL fill (`pnl_net` < 0) — the paper-grade contract priced these as payers, and a real loss is new information the 50% bar doesn't carry; or any journal/day-ledger disagreement (the reconciliation and the ledger must tell one story).

**The base-cap tripwire (independent of this memo, graded weekly):** flip base cap to 2 only when two of three hold — 60-min band EV ≥ $2.50/fire; band candidates spread ≥ 4 dates; 7h price ≥ $10/fire two consecutive weeks.

## 5. VERDICT (fill on Oct 12)

- [ ] **KEEP** — the case for cap 2 (or a second canary week) goes to the operator with §3's numbers.
- [ ] **KILL** — the box expires; the base posture stands; §3's numbers are filed as the canary's epitaph.
- [ ] **ESCALATED** — per §4, before the box expired.

**Operator:** ______________ **Date/Time:** ______________

## 6. NOTES ON THE RECORD (pre-filled — context for the grader)

- The Oct 5 memo-drafting pass caught the journal leak (drive rows in the live journal) BEFORE this memo existed; the cure is structural (`2fb1606`: the journal derives from the valve's own state file; leak-gate pins 4.2–4.4) and the live journal was reset to empty. The UNI/USD release of Oct 3 and the first canary-window release (UNI/USD, Oct 5 09:36 boot) predate journaling — they are recorded in the day-ledger history and §2, not in the journal counts of §3. From Oct 5 ~10:47 the journal is complete and self-sandboxing.
- The reckoning's canary note on the bound stands: `releasable` prices the BASE cap only; the canary's allowance is additional and unpriced by the week join (no pair identity there) — the journal IS the canary's reconciliation.
- The running scanner still needs a restart to pick up the journal-write call site (the dial keys are live, but releases before the restart will not journal). Until that restart, §3's journal counts are a floor, not the truth — prefer the day ledgers if they disagree.

*Filed Oct 5 by Buffy (Codebuff). The reckoning regenerates the evidence weekly; this memo consumes it.*
