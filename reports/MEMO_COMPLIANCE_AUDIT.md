# Product Memo Compliance Audit

**Date:** 2026-10-24 (updated after the UI end-to-end pass, same day; B8 reclassified 2026-10-25;
referral reward-claim audit added 2026-10-25 as B26/B27)
**Scope:** homepage (`pool/templates/index.html`, `pool/static/*`), dashboard, pool engine
(`pool/jackpot_engine.py`, `pool/database.py`, `pool/scout_endpoints.py`), push path,
desktop app (`scout/app.py`), treasury/jackpot crates (`xmrj_*`).
**Method:** read the shipping code and routes, then drive the running app (rendered pages,
`/api/*`, checkout modal, admin panel) against a throwaway database.

**Verdict (updated):** the visible layer now quotes the backend. Every number the copy
states — entry unit, payout split, draw cadence, claim window, Pro price, live miner
count — is rendered from the setting the engine or checkout reads, and the page discloses
which miners and drawings are simulated. Three money defects that made the page false
are closed (donation funds the pot, held entry is never burned, Pro signals are gated by
the server). What remains is the ledger-of-record question and the inert treasury.

---

## A. Enforced, verified

| Memo requirement | Where | State |
|---|---|---|
| No XMRJ / treasury / coin talk on the page | `index.html` | Text is clean; "XMRJ" survives only in HTML comments (`1573–1615`) |
| Social share buttons removed, codes behind buttons | `index.html:120–137`, `static/refblock.js` | Done |
| Partner offers framed as external, not pool-run (see B8) | `index.html:88–150` | Done — "Side Pot Partners" applied Oct 25 |
| Pro CTA on the homepage | `index.html:60` | Done, links to `/#dashboard`, price from `_pro_price()` |
| Streak, conversion, 100-day bar, both unlock paths, +1 entry, signals copy | `index.html`, `/api/gamification` | Done, fed by the server |
| Shares → entries | `jackpot_engine.allocate_free_tickets` | Works at `entry_unit()` shares per entry |
| Streak → 3× milestone conversion | `jackpot_engine`, `database.update_mining_streak` | Works; hashrate untouched |
| 100 days → Pro, +1 entry | `database.unlock_scout_pro`, `pro_progress` | Works; entry held until a wallet exists |
| Donation → jackpot | `scout_endpoints._credit_order_donation` → `jackpot_funding.credit_donation` | **Works now** (B2 closed), idempotent per order |
| Pro signals gated server-side | `/api/push/signals` | **Works now** (B5 closed): non-Pro gets `scout_pro_required: true` and no signals |
| Kraken referral payouts → jackpot | `kraken_challenge.py` → `jackpot_funding.py` | Works, capped at $20/day accrual |
| Kraken claims labelled partner-owned vs pool-owned | `index.html:155–170` legend + inline `Kraken-owned:` / `Pool-owned:` labels | Done Oct 25 (B22) |
| Referral rules: two-sided entry, email attribution, 10 per cycle | `db.award_referral_entries`, signup route, `/api/referral/stats` | Implemented Oct 25 (B23) |
| Referral card's streak and leaderboard "rewards" match the code | `index.html` rewards list, `db.check_and_award_badges`, `/api/miners` | Audited Oct 25 (B26): no referral streak is paid, the leaderboard pays no weekly prize |
| Funded tiers verified against Kraken's published price list | `index.html:266–339`, `1172`; source kraken.com/features/funded | Verified Oct 25 (B25) |
| Same labels on the six-language download page, no XMRJ/treasury claim | `pool/i18n_pages.py` `_owner_strip` + all six `templates/i18n/*.json` | Done Oct 25 (B24) |
| Demo data disclosed | `/api/stats`, `/api/miners`, `/api/social-proof`, `/winners`, leaderboard, admin | **Works now** (B17 closed) |
| Treasury / jackpot / conversion crates intact | `xmrj_treasury`, `xmrj_jackpot`, `xmrj_conversion_service`, `xmrj_governance` | Untouched, devnet IDs configured |

---

## B. Defects

### B1 — "one price, one currency" — CLOSED Oct 24
The template carries `{{PRO_PRICE_XMR}}` / `{{PRO_PRICE_USD}}`, filled by `_pro_price()`
(the reader `/scout/purchase` uses); the dashboard gets the same pair in
`/api/gamification → progression.pro_price`. The "$95" is gone from shipping copy and
`checkout` renders `amount_xmr` from the order.

### B2 — a donation never entered the jackpot — CLOSED Oct 24
`_fulfil_order` now calls `_credit_order_donation` on the paid transition, which calls
`jackpot_funding.credit_donation(order_id, …)` — an inflow category of its own, idempotent
per order id. A funding failure is audited (`scout_donation_credit_failed`) and never
rolls back an entitlement the buyer already paid for.

### B3 — the unlock's +1 entry was lost for wallet-less accounts — CLOSED Oct 24
`unlock_scout_pro` holds the entry (`pending_entries`) instead of marking it awarded;
`claim_pending_entries` mints it when a wallet lands, and the authenticated surfaces call
it. `pro_progress` reports `entry_held` vs `unlock_entry_awarded` truthfully.

### B4 — Pro entitlement never reached the browser — CLOSED Oct 24
`pool/static/scout_pro_bridge.js` reads `/api/scout/pro` and caches the server verdict into
the flag `push_client.js` gates on; it loads before the alert router and refreshes on every
login/logout.

### B5 — push endpoints had no Pro check — CLOSED Oct 24
`/api/push/signals` answers `{scout_pro_required: true, signals: []}` for a non-Pro
account; the paywall is no longer decorative in either direction.

### B6 — the desktop app's "Unlock Scout Pro" is a local flag — DISCLOSED Oct 24
It still only sets `state["pro_unlocked"]`, but it no longer claims to have bought
anything: the button reads "Enable Pro locally (preview)" and the status text says the
entitlement is decided by the pool account. A real entitlement call would need the
console to authenticate; that is a product decision, not a copy fix.

### B7 — the 100-day path ignores tokens — CLOSED Oct 24 (by decision)
Tokens ARE entries; the unlock counts contributing days and awards its +1 entry. No token
threshold gates or accelerates it. Proven by `test_entry_rule.py`.

### B8 — the $3,000 is Kraken's partner term, not a pool feature (RECLASSIFIED Oct 25, copy APPLIED)

The $3,000 in the referral block is **Kraken's** partner referral cap. Kraken administers
it, Kraken applies it to Kraken's programme, and Kraken pays against it. XMR Jackpot neither
sets it nor enforces it, so "no cap exists in code" was the wrong test — there is nothing on
this side to implement, and nothing the pool could enforce even if it wanted to. The earlier
finding is withdrawn: it is informational copy about an external programme, not a product
promise that needs a matching feature.

What the page must not imply is different, and narrower: that the pool delivers the ladder,
or that an invite link today funds the pot. The old block read "Kraken caps the referral
program at $3,000. Affiliate links that contribute to the jackpot are coming soon." — a
correct sentence, already attributing the cap to Kraken — but it sat under a
`$300 → $900 → $3,000` ladder and a "every account opened through this link feeds the
jackpot" line, which read as pool-side entitlements rather than a partner's terms.

**Applied Oct 25.** The block is now a **Side Pot Partners** section (`index.html:88–150`)
headed by the canonical copy, verbatim:

> Partner offers are external opportunities associated with the pool. Any referral rewards,
> bonuses, or promotional funding are governed by the partner's terms. As additional partners
> are added, portions of those relationships may contribute to the jackpot pool.

Kraken stays inside it as one named partner under a "Kraken — partner terms" subhead: the
ladder is "as Kraken publishes it", each rung reads "Kraken pays → $X", and the cap sentence
now reads "Kraken caps its own referral program at $3,000" followed by the attribution
(`index.html:142–143`) — administered and enforced by Kraken, not set, raised or paid against
by XMR Jackpot. The "every account… feeds the jackpot" line is gone, replaced by what the
ledger actually does: partner bonuses the operator records are credited into the pot, and
opening an account through the link credits nothing by itself. The steps list is titled
"Kraken's steps" and its last line credits Kraken for the payout.

The counter (`#refBlockClaimed`) is unchanged in mechanism and reworded in
`pool/static/refblock.js`: it reports partner bonuses the operator *recorded*, from
`/api/kraken/rewards-today`, not anything Kraken promises. That framing presents Kraken as
an external partner whose programme exists independently, leaves room for future
partner-funded contributions to the pot, and removes any implication that a referral feature
is being claimed but not built. The attribution obligations survive and are met: the invite
link keeps `rel="nofollow sponsored"` and its `data-cta-slot` (`index.html:125`), and any
partner-funded credit that *is* recorded continues through the existing operator path in
section A (`kraken_challenge.py` → `jackpot_funding.py`).

### B22 — Kraken's claims read as pool promises (P1, CLOSED Oct 25)

The page made Kraken's programme sound like pool revenue. The worst line, repeated at six
CTAs (hero microcopy, footer, wallet modal, Step Zero, Real Play banner), said a signup
would mean "Kraken pays you and pays the jackpot when the terms are met" — Kraken pays the
solver, never the pot; only the operator's recorded credit reaches it. The same implication
sat in "every Kraken account opened through that link feeds this jackpot" (hero),
"Kraken referral bonuses add to the pot as they come in" (hero), "the same bonus that
refills tomorrow's prize" (tier strip, Step Zero, step 5), "Connecting a wallet unlocks
Kraken-powered community bonuses that increase the grand prize" (wallet card and `app.js`'s
`PARTNER_BONUS_NOTE`), and the Pro referral list's unlabelled "Referral bonuses are paid
into the daily jackpot".

Now every Kraken claim says whose term it is:
* a **"Who owns what"** strip (`index.html:155–170`) splits **Kraken-owned** (the Funded
  Challenge, its rules, targets, scoring and account funding; the ladder, its $3,000 cap and
  every bonus — set and paid by Kraken) from **pool-owned** (entries, the 100-day Pro path,
  drawings and claims, Scout Pro, house tax — run by the pool, no Kraken part), and names the
  one interface: a bonus reaches the pot only after the operator records receiving it;
  Kraken never pays the jackpot directly.
* inline labels mark the mixed claims: the hero funding line (`187`), the wallet-bonus card
  (`655`), Step Zero (`755`), the tier strip and tier-table intro (`1146`), step 5 (`1189`),
  the referral reward list (`2079–2083`), and the signup/withdrawal line (`2095`).
* all eight "why this link" CTAs now read: Kraken pays the referral reward on Kraken's terms;
  the operator credits only the bonuses it actually records into the pot; Kraken does not pay
  the jackpot.
* `PARTNER_BONUS_NOTE` (`app.js:15`) no longer promises that connecting a wallet creates
  bonuses; it says a partner bonus is credited once the operator records receiving it.

Pinned by `test_ui_disclosure.py` section 8 (labels present, no "pays the jackpot" phrasing,
no automatic-inflow claims, tiers attributed, wallet note corrected) and section 9 (referral
rewards are entries, not pot funding — B9's actual defect).

### B9 — "Referral bonuses go into the jackpot": reclassified, CLOSED Oct 25

The finding assumed the pool's own referral programme was supposed to fund the pot. It is
not. That programme **pays entries, not XMR**: a referral earns the referrer bonus entries
(100 tickets — one entry at the default unit) once the referred account's miner starts
submitting valid shares, and the friend gets the signup tickets. Nothing in it touches the
prize pool, so "the claim is only half true" was the wrong frame — there is no pot
contribution to be missing.

The claim that *was* false sat on the page, not in the money: "Referral bonuses are paid into
the daily jackpot" and "Invite friends → grow the pool → increase the grand prize" both told
visitors referrals grow the prize. Both are gone (B22 pass); `index.html:2075` now reads
"Referral rewards are entries, not jackpot funding", and the reward list separates the
pool's entry rewards from Kraken's bonuses.

What the stated programme rules imply for the code is tracked separately in **B23**.

### B24 — the download page carried a treasury claim and unlabeled Kraken copy (P1, CLOSED Oct 25)

`/download` is generated from six catalogs (`pool/templates/i18n/*.json`), so every defect
shipped in six languages:
* `download.treasuryBody` claimed "XMRJ unlocks settle through the XMRJ treasury … the
  treasury's own funder account is the only path by which XMR reaches the pot — that split is
  what funds the daily jackpot." That is the opposite of what runs: nothing in the pool calls
  `xmrj_treasury` (B11), and the proven XMR→pot paths are the Scout Pro donation
  (`credit_donation`, once per order) and recorded partner bonuses.
* `download.lead` and `download.scoutBody` offered an "XMRJ unlock" (see B14).
* Scout was described as running "Kraken's funded challenge" — true, but with no statement of
  whose terms the challenge, tiers and funding are, so the paid unlock sat inside a
  partner claim with no label.

Now: `pool/i18n_pages.py` renders a **claim-source legend** (`_owner_strip`) directly under
the lead, in every language — **Kraken** (the Funded Challenge, its rules, targets and account
funding; the referral ladder and its cap — set and paid by Kraken) versus **XMR Jackpot**
(entries from mining shares, drawings and claims, Scout Pro), plus the interface line: a
Kraken bonus reaches the pot only after the operator records receiving it, and Kraken never
pays the jackpot directly. `download.scoutBody` now labels its own halves ("Kraken-owned:
the challenge, its tiers and its funding. Pool-owned: activation costs {price} XMR for {days}
days and includes one entry"), and `download.treasuryBody` was replaced with what the ledger
does: Scout Pro is the only paid item, its XMR is credited to the pot once per order, the
unlock adds exactly one entry, and recorded partner bonuses arrive the same way. No XMRJ, no
treasury, no \"only path\" claim — in any of the six catalogs.

Pinned by `test_ui_disclosure.py` section 10 (six pages: legend keys rendered, no `XMRJ`
anywhere in the HTML, no treasury wording in any language).

### B25 — the $20/$50/$90 tiers are right, the ownership and profit split were not (P1, CLOSED Oct 25)

**Verified against Kraken's own pages (fetched Oct 25):**
* [kraken.com/features/funded](https://www.kraken.com/features/funded) — "One fee per challenge:
  $20 for the $1K plan, $50 for $5K, or $90 for $10K. The fee is non-refundable"; "Start with
  $1K, $5K, or $10K in trading capital"; "backed by up to $10,000 in Kraken's capital".
* Same page and its disclosures — "Kraken Funded is operated by Payward Oceanic Ltd. (BVI)";
  the product "may be accessed through the Kraken user interface but it is not a regulated
  product and is offered by Breakout and POL"; the evaluation is Breakout's; the trader keeps
  **80%** of profits ("Kraken keeps 20%"); 12% profit target, 3% floor; **not available in
  Maine, Massachusetts or New York**; payouts discretionary.

So the tier ladder itself was correct — $20 → $1,000, $50 → $5,000, $90 → $10,000 — and is
now attributed to the price list it came from. What was wrong was the ownership: the page
said "Kraken.com created the challenge", "the rules, targets, and scoring belong to Kraken",
"capital provider: Kraken.com", "Kraken.com funds the account", and "the profits are yours".
Kraken is the brand and the interface; **Payward Oceanic Ltd. (BVI)** is the counterparty that
provides the funded account, **Breakout Trading Group, LLC** administers the evaluation, and
the split is 80/20.

Now: the challenge section names publisher, provider and evaluator, links the source, states
the 80% split, and carries the state restriction and the discretionary-payout caveat
(`index.html:266`, `280`, `325`, `339`, `1172`, `1190`). The tier strip reads
"$20/$50/$90 for $1K/$5K/$10K, as published by Kraken Funded", and the walkthrough footnote
replaces "tiers verified against the same guide the Kraken Challenge shows winners" with the
actual citation.

Pinned by `test_ui_disclosure.py` section 11.

### B23 — the stated referral rules are now code (P1, CLOSED Oct 25)

Stated (Oct 25): instant, **two-sided** — friend and referrer each get an extra entry when
the friend signs up — attributed by the referrer's **email**, and **capped at 10 per jackpot
cycle** "to keep things fair".

What the code does today:
* attribution is by **referral code** (`users.referred_by`), with no email path;
* the referrer is paid 100 tickets (`referral_ticket_reward`, one entry at the default unit)
  at the referred miner's **first valid share** — not at signup — and only if the referrer
  has a wallet (`jackpot_engine.py:305–315`);
* the friend receives the **signup bonus** every new wallet gets — 3 tickets
  (`server.py:2048–2055`), not a referral-specific entry, so a referred signup is worth the
  same as an unreferred one to the friend;
* **no cap**: there is no per-cycle counter, so "10 per jackpot cycle" is unenforced and
  unmetered, and nothing on the page states it.

**Implemented Oct 25.** `users.pending_referral_entries` and a `referral_awards` ledger
(UNIQUE on `referred_user_id`) carry the programme; `db.award_referral_entries()` pays
`entry_unit()` tickets to **each** side, resolves the referrer by email through the keyed
blind index (`db.get_user_by_email`), counts awards against
`referral_awards.cycle_anchor = MAX(drawings.drawn_at)` so the allowance resets when a new
drawing lands, and enforces `referral_cycle_cap` (10). A side without a payout wallet holds
its entry and `claim_referral_entries()` mints it when a wallet is attached. The signup route
accepts `referrer_email` and still honours `?ref=CODE` through the same ledger, and the old
first-share payout was deleted from `allocate_free_tickets` so a referral cannot pay twice.
`/api/referral/stats` now reports `awards_this_cycle`, `cycle_cap`, `remaining` and
`entries_held`, and the admin panel carries a read-only **referral meter**
(`GET /api/admin/referral-meter`): this cycle's awards, one bar per referrer against the
cap, referrers at the cap, every entry held for want of a wallet, and the newest awards.
The bar is per referrer because the cap is per referrer — an aggregate bar would hide one
account at 10 while the rest sit at zero.

The card states the shipped rule: "you both get an entry when they sign up with your email"
and "Limit 10 referral entries per jackpot cycle". Pinned by `test_referral_entries.py`
(44 checks: both sides paid by email, code fallback, once-per-friend, cap and reset, hold
and claim, self-referral, no pot inflow, the meter's numbers, read-only enforcement, the
403 for a non-admin, and section 10's audit of the card's streak/leaderboard claims).

### B26 — the referral card's last two "rewards" paid nothing (P2, CLOSED Oct 25)

The card's rewards list still carried two promises the engine has never paid:
* **"Referral streak bonuses"** — there is no referral streak. No column, counter or award
  keyed to referred accounts exists (checked over `users` and `referral_awards`). The only
  streak is the **mining** streak (`miner_xp.mining_streak_days`, `update_mining_streak`),
  and its bonus is a 3× conversion of that day's shares on every 7th consecutive
  contributing day (`get_streak_bonus`, `streak_bonus_multiplier`): jackpot side only,
  hashrate untouched, nothing to do with referrals.
* **"Weekly leaderboard"** — the leaderboard is a read. `/api/miners` returns
  `db.get_all_miners()` and `app.js` renders the top ten rows; no weekly window, ranking
  prize or minting path exists anywhere in `pool/` (no setting, table or route mentions a
  weekly prize). It is a display surface, not a reward.

The two lines now state what runs: "Badges, not bonuses: **Recruiter** at your first active
referral, **Network Builder** at 5 active" and "The leaderboard ranks the top miners — it
pays no separate weekly prize". Both are the code's own rule — `check_and_award_badges`
adjudicates `first_referral` at `refs >= 1` and `referral_5` at `refs >= 5`, where `refs` is
`get_referral_stats`' **active_referrals** (a referred account with a wallet and a miner
that has submitted shares), so a signup that never mines earns no badge; each badge pays
100 XP, and XP levels pay nothing. "Active" is in the copy because a bare "first referral"
would have promised a badge a passive signup cannot earn. "Ranks the top miners" replaces
an earlier "shows your rank": the table is `d.slice(0, 10)`, so anyone outside the top ten
has no rank on it. Pinned by `test_referral_entries.py` section 10 (no referral-streak
column, streak badges are the two mining ones, day 7 still converts at 3×, the badge
thresholds at 0/1/5 active referrals, both card lines present, the old promises absent,
`/api/miners` mints nothing, no weekly-prize setting).

The same pass turned up a third reward claim whose arithmetic does not hold: see **B27**.

### B27 — a referral pays 100 entries a side, not one (P1, OPEN)

The card says a referral pays "one entry each". The code calls
`award_referral_entries(..., tickets=engine.entry_unit())`, and `add_tickets` inserts **one
ticket row per ticket**, while a ticket row **is** one draw chance — the drawing picks a
winner by `OFFSET` over `tickets` (C.2: a token is an entry). At the default unit that is
**100 entries per side, per referral** (about the yield of 10,000 mined shares), and the
10-award cap allows up to 1,000 entries a side per cycle.

B9/B23 recorded "100 tickets — one entry at the default unit", reading `entry_unit()` as if
a ticket were a hundredth of an entry. No code path divides a ticket: `allocate_free_tickets`
mints **one** ticket per `entry_unit()` shares (`units // tickets_per`), so the award is 100×
the stated reward in draw weight.

Two ways to settle it, both product decisions:
* **pay what the card says** — pass `tickets=1` at the signup call site (then the cap,
the meter and the card all mean entries), with `test_referral_entries.py` 1.2/1.3 updated to
one ticket a side; or
* **state what ships** — the card and the meter say `entry_unit()` entries a side, and the
cap's real weight is disclosed.

Separately, `/api/referral/stats → tickets_earned` is a third number: `active_referrals ×
referral_ticket_reward` (100), which follows neither the ledger nor the cap and does not
move when awards are capped or held. The dashboard's "Entries Earned" tile reads it.

### B28 — the XMRJ treasury is a ledger with no token, no deployment and no caller (P1, OPEN)

Asked to reconcile the treasury against the paywall and the pot. The answer, from the code:

* **It exists and is coherent.** `xmrj_treasury` is a 544-line Anchor program: `initialize`
  (gated on the program's own upgrade authority), `record_inflow_xmrj` (gated on the state's
  operational key) splits an inflow into `reserve_bps` / `jackpot_bps`, governance-settable
  via `set_split_ratio` / `set_exchange_rate` / `set_operational_authority` through the
  pinned governance-authority PDA, and `fund_jackpot_with_xmr` forwards the recorded
  convertible share through a CPI to `xmrj_jackpot::fund_jackpot`, signed by a PDA only this
  program can sign for. `test_anchor_wiring.py` (54) and `test_xmrj_devnet_e2e.py`
  (20 structural, live half SKIPped) both pass.
* **The 70/30 is 70 reserve / 30 jackpot, and it is a test constant, not a deployment.**
  `tests/xmrj_devnet_e2e.ts:131–132` passes `INIT_RESERVE_BPS = 7_000`, `INIT_JACKPOT_BPS =
  3_000`. `record_inflow_xmrj` keeps the `reserve_bps` share (`total_reserve_xmrj`) and
  makes only the `jackpot_bps` share convertible — so the treasury reserves 70% and forwards
  30%, the opposite of "keeps 30%". No cluster holds either value: `Anchor.toml` states
  "NO PROGRAM IS DEPLOYED ON ANY CLUSTER YET", only keypairs exist in `target/deploy`, and
  the plan leaves the starting split to the deployer.
* **No token exists and none is moved.** There is no `spl_token`, mint or transfer CPI in
  either program; `record_inflow_xmrj(amount)` takes a `u64` from the operational key on
  attestation, and `fund_jackpot` / `trigger_payout` only add to and subtract from
  `jackpot_balance_xmr` / `total_payouts_xmr`. Even fully deployed, this treasury could not
  hold or pay XMRJ or XMR — it counts. The SPL mint in section 2 of the deployment plan is
  a manual `spl-token` step that has not been run.
* **`exchange_rate_bps` is written and never read.** `fund_jackpot_with_xmr` bounds
  `amount_xmr <= total_converted_xmrj - total_jackpot_funded_xmr`, so the XMRJ counter is
  compared 1:1 against XMR and no rate is applied (`FUNDING_INFLOW_XMRJ = 1_000_000` →
  `FUNDING_AMOUNT_XMR = 300_000` in the suite, at `INIT_EXCHANGE_RATE_BPS = 1`).
  `set_exchange_rate` is a governance lever over a value nothing consumes.
* **The unlock instruction does not exist.** No crate contains an unlock / Scout / Pro /
  entitlement instruction at all (checked across all four), so the paywall is not "unwired"
  from the treasury — there is nothing on-chain to call. `xmrj_conversion_service` is
  off-chain and log-only, with `set_split_ratio` still a `_reserve_bps` stub.
* **Nothing in the pool calls Solana.** `pool/` and `scout/` contain no xmrj or Solana RPC
  reference. The real paywall is a Monero checkout: `_checkout_subaddress` → matched
  `xmr.get_transfers` → `mark_scout_purchase_paid` → `unlock_scout_pro` (entitlement) and
  `credit_donation` (pot). That is the working funding path, plus Kraken's recorded
  bonuses — neither reads the treasury.

**Surface clean-up, Oct 25.** The code and docs still implied the connection, so the stale
pieces were removed rather than described:
* `POST /api/scout/activate` answered "bound account has no wallet — XMRJ unlock failed"
  though the route only inserts an identity; it now says a Scout needs a payout wallet.
* `pool/scout_endpoints.py`'s module docstring claimed an on-chain mirror at
  `xmrj_jackpot/src/scout_identity.rs` — a file that has never existed — and now states that
  the xmrj_* programs are undeployed and uncalled.
* `pool/static/scout_paywall.js` no longer defines or exports `startXmrjUnlockFlow` (which
  posted `action: "xmrj_unlock"`, an action the endpoint never handled), and the
  `xmrj_unlock_proof` field is gone from the activate payload and its docstring.
* `pool/static/scout_paywall_wire_patch.js` drops the `window.xmrjUnlockProof` global and the
  `btn-unlock-xmrj` stale-id entry; the JS wiring drives' fixtures and assertions follow
  (the activate payload is asserted to carry no xmrj field).
* `README.md` (§ “Two products on /download”) now says the checkout is Monero-only and that
  there is no XMRJ unlock, naming B28 for the undeployed programs — replacing the claim
  that an XMRJ unlock settles through `xmrj_treasury` and is the only path that adds XMR to
  the pot.
* `pool/i18n_pages.py`'s money-card comment claimed "this is the treasury split, and it is
  the only way XMR reaches the pot"; it now describes the donation path and says no XMRJ or
  treasury rail moves XMR.

Pinned by **`test_no_xmrj_claims.py`** (10 checks), which scans the README, both templates,
the six catalogs (by value, so the `download.treasury*` key names are not false positives),
the server and desktop-app Python, the browser modules, the installer and the launchers. It
fails on a line that pairs XMRJ with unlocking, on the banned identifiers (`xmrj_treasury`,
`xmrj_jackpot`, `xmrj_unlock`, `startXmrjUnlock`), and on "treasury" next to money words —
negation-aware, so "there is no XMRJ unlock" passes while "Unlock with XMRJ" fails. Sections
2 and 3 keep it honest: it asserts the scan is not vacuous (6 catalogs, both modules, the
template) and feeds fabricated claims plus a treasury KEY/VALUE catalog pair through the same
predicate; a real README mutation was caught before this line was written. CI runs it in the
`static-invariants` job.

To make the claim true would need, in order: the SPL mint and a vault the treasury actually
holds (with a transfer-checked inflow instruction), a Pro-unlock instruction, a deployed
program set, and a server-side caller. Until then B11/B14/B24's "no XMRJ" copy is the
correct description, and no money path depends on any of it.

### B10 — "every share = one entry" — CLOSED Oct 24
Settled as 1 unit = 1 share, `{{ENTRY_UNIT}}` units = 1 entry; every surface quotes
`engine.entry_unit()` and `test_entry_rule.py` proves a non-default unit propagates.

### B11 — the treasury is inert, and its mirror file is missing (P1, open)
`scout_endpoints.py:5` advertised an on-chain mirror at
`xmrj_jackpot/src/scout_identity.rs`; that file does not exist (the docstring was corrected
on Oct 25 — see B28). Nothing in the pool calls `xmrj_treasury` or `xmrj_jackpot`, so token
accounting and unlock logic live entirely in SQLite. There is no `[programs.mainnet]` table.

### B12 — signal naming (P2, open)
Memo: "buy-now and sell-now". UI and engine: `BUY_NOW` / `GET_OUT_NOW`, rendered as
"Buy Now" / "Exit Now". Pick one vocabulary.

### B13 — dead community links — CLOSED Oct 24
The three `href="#"` CTAs (Discord / Telegram / Twitter) are gone; the card states the
channels are not open yet.

### B14 — "XMRJ" was shipped in rendered copy after all (P2, CLOSED Oct 25)
The earlier note said XMRJ survived only in `index.html:1573–1615` schema comments. That was
wrong for one surface: `/download` rendered it in six languages — `download.lead` ("unlock
it with XMRJ or with Monero inside the app") and `download.scoutBody` ("or an XMRJ unlock"),
plus `download.treasuryBody`, which described XMRJ unlocks settling through an XMRJ treasury.
All three keys are rewritten in every catalog and the term is gone from the rendered page
(see B24). The schema comments remain in the template and are still invisible to visitors.

### B15 — three handlers shipped without decorators (P0, CLOSED Oct 24)
`api_get_subaddress`, `api_payment_status` and `scout_index_redirect` were defined but
never registered, so:
* the Real Play page's deposit box (`GET /api/payment/subaddress`) 404'd forever;
* the refund/support payment history (`GET /api/payment/status`) did not exist;
* `/scout` had no trailing-slash redirect for the console.
All three now answer (401 without a session, which is correct). `/api/payment/status` also
crashed on its first call — `get_user_payments` ordered by a `created_at` column
`payment_verifications` has never had; it now orders by `credited_at/scanned_at`.

### B16 — the winners block shipped as literal tokens (P1, CLOSED Oct 24)
`{{DRAW_ROWS}}`, `{{LATEST_DRAWING}}` and `{{DRAW_EMPTY}}` were filled only by `/winners`,
so the SPA homepage rendered the braces. `_template_html()` fills them for every surface.

### B17 — simulated miners and drawings were presented as real (P0, CLOSED Oct 24)
`seed_demo.py` inserts 20 fake miners and 5 fake drawings, and `server.py` refreshes their
`last_seen` and jitters their hashrate every 3 minutes, so a visitor read fake activity as
community activity. Now:
* `miners.is_demo` exists (migration + backfill by the seeder's own worker names);
* `/api/stats` publishes `demo_miners` / `demo_mining_sample`;
* `/api/miners` carries `is_demo`; the leaderboard tags rows and prints a note;
* `/api/social-proof` keeps simulated prizes OUT of `total_paid_out` / `total_drawings`
  and reports them as `simulated_paid_out` / `simulated_drawings`;
* `/winners` and the homepage winners feed label `(demo)`;
* the homepage hero carries a disclosure line naming the split;
* the admin panel shows real vs demo counts and tags demo rows.

### B18 — "Won 0.000000 XMR" on every visit (P2, CLOSED Oct 24)
`fetchSocialProof` read `recent_winner.payout_amount`; the API returns `prize_amount`.

### B19 — checkout blamed itself for a signed-out visitor (P2, CLOSED Oct 24)
A 401 from `/scout/purchase` rendered "Checkout is unavailable right now". It now says
"Sign in to start checkout — nothing was charged."

### B20 — the page denied the paid item it sells (P1, CLOSED Oct 24)
"No deposits — you never send us money", "there is nothing to purchase" and
"Still no deposits, no fees, no payments" sat next to a paid Scout Pro unlock. The FAQ now
says entry is free and names the optional donation with its live price.

### B21 — hardcoded pool size (P2, CLOSED Oct 24)
The comparison card said "100+ miners" while the live counter on the same page said 22.
It renders `{{TOTAL_MINERS}}` from the pool's own count.

---

## C. Decisions the memo cannot settle

1. **Entry unit** — settled: 1 unit = 1 valid share; one entry = `entry_unit()` units
   (default 100, guarded so 0/negative/junk fall back).
2. **What a token is** — settled: a token IS an entry (one `tickets` ledger); the Pro
   unlock counts contributing days only.
3. **Price source of truth** — settled in code: the operator's `scout_pro_price_xmr` is
   the charge, and the page prints it with the tracked USD conversion. A USD *peg* is still
   not implemented.
4. **Ledger of record** — SQLite (today) or the treasury program the memo says owns token
   accounting and unlock logic. Until this is answered, B11 cannot be closed.

## D. Suggested order of work

1. B11 — bridge progression into the treasury, or delete the claim that it is mirrored.
3. B6 — give the desktop console a real entitlement read, or keep it a disclosed preview.
4. B12 / B14 — one signal vocabulary; strip the XMRJ comments if source is audited.

## E. Reproducing the end-to-end UI audit

`test_ui_disclosure.py` drives the real app against a throwaway DB and pins the truth
surface: no unfilled tokens on any page, copy follows the settings (split, cadence, claim
window, Pro days, entry unit), one price across homepage / dashboard / checkout reader, the
demo split published and labeled, the browser-side disclosure present, the referral block
framed as an external partner offer (B8), every Kraken claim labelled partner-owned or
pool-owned (B22), the download page labeled in all six languages with no XMRJ/treasury
claim (B24), and the previously dead routes answering. `test_version_endpoint.py` additionally pins that `GET /` serves the
rendered template byte-for-byte.
