# Admin dashboard — figure audit (Oct 26)

Scope: every number on the admin page (`/api/admin/panel` + the cards
injected into `#page-admin`) that a reader would take as **money**, traced
to its source, and graded. The question: which of them are estimates,
accruals, operator assertions or simulations wearing the clothes of cash?

The `jackpot_projection` advertisement is out of scope: it is behind
`pool/projection_ad.py` and refuses to be read from any `/api/admin` URL
(see `test_projection_ad_surface.py`).

Grades used below:

| grade | meaning |
|---|---|
| **A** | ledger or wallet fact — a row that was written when something really happened |
| **B** | an estimate/accrual that SAYS so in its own label |
| **C** | an estimate, accrual or assertion shown as money, unlabelled |
| **D** | not verifiable from this repository |

Measurements below come from a sandbox database driven through the real
FastAPI app (no production data, no wallet RPC, rate pinned at 500).

## The cards

| card / table | value shown | source | grade |
|---|---|---|---|
| Current Jackpot | XMR | `jackpot_engine.get_jackpot_balance()` — base balance minus drawn-unclaimed prizes | **C** (see 4) |
| House Tax Earned | XMR | `SUM(drawings.house_tax)` where `status='drawn'` | **C** (see 3) |
| Total Paid Out | XMR | `SUM(payouts.amount_xmr)` where `status IN ('sent','confirmed')` | **C** (see 2) |
| Active / Total Miners | counts | `miners.last_seen` 600 s / row count | **C** (see 5) — real rows, demo partly badged |
| Users / Drawings / Tickets | counts | row counts | **A**, demo unbadged (see 6) |
| Cold wallet — Pool wallet balance / unlocked | XMR | monero-wallet-rpc, live | **A** |
| Cold wallet — Kraken total (house-tax sent) | XMR | `payouts` type `house_tax`, status `sent` | **C** (see 2) |
| Cold wallet — House fees accumulated | XMR | setting `house_tax_accumulated` | **A** (the setting is the accrual book) |
| Cold wallet — House fees collected total | XMR | accumulated + sent + pending | **C — overstated ~2×** (see 1) |
| Cold wallet — on-chain to Kraken address | XMR | wallet RPC outgoing transfers to the deposit address | **A** |
| Payouts table | XMR rows + status | `payouts` | **A** (statuses may be asserted, see 2) |
| All Users — Earned | XMR | `users.total_earned`, incremented at claim | **A** |
| All Miners — Hashrate | H/s | proxy poller / seeded for `is_demo` | **A** (demo badged) |
| Referral Entries meter | counts | `referral_admin_meter()` | **A** |
| Kraken Referral Payouts card | USD gross / pool share | operator-entered, labelled "record what you actually received" | **B** |
| Kraken Referral Projection — Provider Config | mode badge | `provider_posture()` | **B** — SIMULATE is stated in words |
| Push report / decisions | counts | audit rollups ("nothing sampled, nothing estimated") | **A** |
| Kraken Scout Console (iframe) | whatever the upstream shows | remote Streamlit at `SCOUT_UPSTREAM` | **D** (see 7) |

## Findings

### 1. "House fees collected — total" reads about twice the fees earned

`pool/server.py:2927` computes

```python
house_fees_collected = accumulated + kraken_sent + kraken_pending
```

but the draw books the same tax twice. `run_drawing` writes a `house_tax`
payout row (`pool/jackpot_engine.py:522`) **and** adds the same amount to
`house_tax_accumulated` (`:525`). `db.add_payout` inserts with the schema
default `status='pending'` (`pool/database.py:1067`), so that row is
counted in `kraken_pending` while the identical money is still inside
`accumulated`. Nothing ever updates the draw-time row — the threshold send
inserts a *new* row for the amount it actually transferred — so the
double count is permanent, not a transient state.

Measured, sandbox:

| state | earned | accumulated | pending | card total | multiple |
|---|---|---|---|---|---|
| draw booked 0.02 tax | 0.02 | 0.02 | 0.02 | **0.04** | 2.00× |
| after a real 0.0199 send | 0.0199 | 0.00 | 0.02 | **0.0399** | 2.01× |

Two smaller defects live on the same line: `kraken_pending` is "every
status that is not `sent`", so `failed` rows count as money still coming,
and the draw-time bookkeeping row is displayed as a pending transfer that
will never happen.

On the live database (read-only query, Oct 26) the card is overstating
today: `house_tax_accumulated` = **0.10944427 XMR**, the one draw-time
house-tax row is `pending` at **0.10444427 XMR**, so the card computes
**0.21388854** while 0.13734427 XMR is the total ever booked as earned
across the five drawn rounds — 1.56× the earned figure, 1.95× the amount
actually accrued.

Minimal fix: exclude `payout_type='house_tax' AND status='pending'` rows
that were booked at draw time (they are the accrual, not a transfer), or
stop writing the draw-time payout row at all and let the accrual setting be
the only record until something is actually sent.

### 2. "Total Paid Out" and "Kraken total … sent" count an operator's click

`payouts.status='sent'` is set by the dashboard's **Mark Sent** button
(`pool/static/app.js:2300` → `POST /api/admin/payout/{id}/status`,
`pool/server.py:2881`) with no transaction hash required. The card then
reports the row as paid out. Measured: a 0.18 XMR jackpot row marked sent
with no hash raised the card to **0.1999** (0.18 + 0.0199 house tax).

The cold-wallet card is the one place that admits this: when the wallet RPC
is up it prints `on-chain to Kraken address: …` beside the ledger figure,
so the operator can see the claim and the chain disagree. The pool-overview
"Total Paid Out" card has no such cross-check.

No live occurrence yet: the live database has **0** jackpot rows marked
`sent` without a tx hash, so this is a trap for the first manual payout,
not a number currently wrong.

Minimal fix: either require `tx_hash` to move a row to `sent` (a separate
`marked_paid` status otherwise), or label the card "marked sent (ledger)".

### 3. Two "house tax" figures, three definitions, one page

* overview **House Tax Earned** = `SUM(drawings.house_tax)` — everything
  ever booked, including rounds whose winner never claimed
  (`pool/database.py:2777`);
* cold wallet **House fees accumulated** = the setting, zeroed on send;
* cold wallet **collected total** = the sum in finding 1.

All three are legitimate ledgers, none is labelled, and after a send they
diverge (measured: booked 0.02, accumulated 0.00). An operator reading the
page has no way to know which is "what we kept".

### 4. The pot itself is an accrual on top of receipts

`accrue_daily_funding` (`pool/jackpot_funding.py:285`) credits base XMR
every day with `min($20, 14-day average of referral bonuses + miner yield)`
(`compute_daily_jackpot`, `:263`). The card says **Current Jackpot** and
nothing else; the wallet balance sits on a different card. So a pot of 1.5
XMR may be 1.2 received plus 0.3 promised-by-formula, and the only cash
cross-check on the page is the cold-wallet card.

This is the charter's design, not a bug — but it is the reason every other
XMR figure that derives from the pot (House Tax Earned, the winner prize,
the claim payout) is a commitment rather than a receipt. Worth one line of
copy on the pot card, e.g. "includes the daily funding accrual; cash shown
under Cold Wallet".

### 5. Two "active miners" windows in the same payload

`/api/admin/stats` serves `miners_active` from a 600-second window
(`pool/database.py:2781`) and `pool_stats.active_miners` from 900 seconds
(`pool/jackpot_engine.py:681`). The card paints the 600 s figure; the public
`/api/stats` paints the 900 s one, so the operator's "Active Miners" can be
lower than the number the site is showing at the same moment.

### 6. Demo data is badged only on the miner cards

`miners_demo` / `miners_demo_active` are surfaced as "(N demo)"
(`pool/static/app.js:2190-2193`) and seeded miners carry a `demo` chip in
the table. `users_total`, `tickets_total` and `drawings_total` have no demo
dimension at all, so a seeded pool shows invented users and tickets as
plain counts. Lower severity (seeding is a dev tool), but it is the same
class of problem.

### 7. The Scout console iframe is an unlabelled trust boundary

The admin page embeds `/scout/`, a reverse proxy to a **remote** Streamlit
process (`pool/server.py:3732`, `:3805`) behind admin auth. The card calls
it "Live trading console". The pool neither computes nor labels those
figures, and nothing in this repository shows whether that upstream is
trading live or in paper mode. If the scout PC runs paper/default mode, its
P&L is simulated inside an operator dashboard.

Cannot be graded from here (grade D). The fix is either a "paper" badge
fed from the upstream's own state or dropping the "live" claim from the
card header.

## What is clean

* **Current Jackpot** is the ledger balance, not the referral projection
  (pinned by `test_admin_jackpot_source.py`), and the projection table is
  unreachable from every `/api/admin` URL (`test_projection_ad_surface.py`).
* **Wallet RPC** figures are live reads and say "offline — ledger totals
  only" when the node is down.
* **XMRJ per referral** and the pool-share percentage are settings, not
  balances, and the XMRJ accrual shown to users is labelled "payable when
  the XMRJ treasury is deployed".
* **Push report** and **decisions** are audit rollups with no estimates.
* **Referral Entries** is a count of enforced awards, not a projection.
* Dollar figures in `/api/admin/stats.pool_stats` carry `xmr_usd_rate`
  beside them, so the conversion is visible in the payload.

## Suggested order of repair

1. Finding 1 — the only figure that is wrong by a factor of two.
2. Finding 2 — one label, or require a tx hash.
3. Finding 4 — one sentence of copy on the pot card.
4. Findings 3, 5, 6 — naming and window consistency.
5. Finding 7 — needs a look at the upstream console before it can be
   labelled honestly.
