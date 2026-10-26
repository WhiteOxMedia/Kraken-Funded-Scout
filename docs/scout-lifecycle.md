# Scout fleet lifecycle (Phase F)

Scout is the product; the miner is a runtime dependency. This document defines
how the pool records **where every Scout is in its life**, so the fleet is
counted instead of inferred. Implementation: `pool/scout_lifecycle.py`; the
table is created and backfilled on boot by `pool/database.py::init_db()`.

## The four questions

| Question | Counted from | Never counted from |
| --- | --- | --- |
| How many Scouts were installed? | install beacons + activations | downloads, page views |
| How many activated? | `activation_complete = 1` | online/offline snapshots |
| How many ever connected? | `first_connect IS NOT NULL` | "currently connected" |
| How many submitted shares? | `first_share IS NOT NULL` | ticket counts, hashrate |

A share is not an accepted share: `first_valid_share` is a separate stamp, and
`connected_without_valid_share` lists rigs that authenticated but never got one
accepted.

## Telemetry fields

One row per Scout in `scout_lifecycle`:

| field | meaning | written by |
| --- | --- | --- |
| `install_id` | the machine's ledger key (`<uuid>` from the installer, `w:<wallet>` when the pool learned the wallet first) | beacon / registration / telemetry |
| `wallet_address` | bound at registration, or learned from telemetry | registration, poller, share ingest |
| `first_seen` | the row was created | any event |
| `installed_at` | the install beacon landed (files staged) | `POST /api/scout/install` |
| `first_connect` | first stratum authentication | poller, share ingest |
| `first_share` | first share of any kind | poller, share ingest |
| `first_valid_share` | first ACCEPTED share | poller, share ingest |
| `activation_complete` | registration landed (wallet bound into the miner config) | `POST /api/miner/register` |
| `last_seen` | most recent telemetry | poller, share ingest |

`first_*` are **first occurrence only**. A later event never rewrites them, so
history survives restarts, proxy counter resets and dormancy-window changes.
`last_seen` only moves forward.

## States

Derived on read; never stored, so there is one definition and no drift.

| state | condition |
| --- | --- |
| `NEW` | the row exists with no Scout fact yet (the ladder's base) |
| `INSTALLED` | install beacon received, registration never landed |
| `ACTIVATED` | registration landed, never authenticated |
| `CONNECTED` | authenticated, no accepted share yet |
| `MINING` | accepted share inside the dormancy window |
| `DORMANT` | had telemetry, silent longer than the dormancy window |

Precedence: `DORMANT` > `MINING` > `CONNECTED` > `ACTIVATED` > `INSTALLED` >
`NEW`. A rig that produced a share a month ago is **dormant**, not mining.

**Never-connected is not dormant.** A Scout that never authenticated is
reported in its own bucket, because "never worked" and "stopped working" are
different failures. It is split by whether activation data ever reached it:

- `without_activation` — the installer never registered (beacon only, or no
  Scout fact at all): the Scout never received activation data
- `after_activation` — activated, then never dialed in

Dormancy window: `XMRJ_DORMANT_DAYS` (default **7**). Changing it changes the
classification only.

## Write paths

Every write goes through `scout_lifecycle.record()`, which is fail-soft: a
telemetry error is logged and never blocks registration, share ingest, ticket
minting or a running poller.

- **Installer** (`installer/installer.py::opt_in`): opting IN sends one
  install beacon for the machine, then registers with the same `install_id`,
  so the beacon row becomes the activated record. Both calls are fail-soft, an
  offline box installs normally, and an **opt-out install sends nothing** —
  ticking the box is the consent.
- **Registration** (`POST /api/miner/register`): the installer's registration
  IS activation; it carries `install_id` so it lands on that machine's row.
- **Proxy poller**: records a connection for every worker the proxy reports,
  including a zero-share idle rig, plus share deltas when they move.
- **Share ingest** (`POST /api/miner/shares`): the same facts; a share cannot
  exist without a completed handshake, so it also stamps `first_connect`.

## Surfaces

```
POST /api/scout/install                 # unauthenticated beacon, rate-limited per IP
  { "install_id": "<8-64 [A-Za-z0-9._:-]>" }   # optional; server mints one when absent
  -> { "status": "ok", "install_id": "..." }

GET  /api/admin/scout-lifecycle?days=7  # admin session
  -> answers{}, by_state{}, never_connected{}, connected_without_valid_share{},
     dormant{}

python pool/scout_lifecycle.py [--days N] [--json]   # operator report on this host
```

`answers` is exactly the four questions: `scouts_installed`,
`scouts_activated`, `scouts_ever_connected`, `scouts_submitted_shares`.

## Backfill

`init_db()` seeds rows from the pre-existing `miners` table (idempotent):

- `first_seen` = miner `created_at`; `activation_complete = 1` (the historical
  installer registered in the same act);
- `first_connect` / `first_share` / `first_valid_share` from `last_seen` and
  the share counters, so real history survives;
- **`installed_at` stays NULL** — no beacon ever ran on those machines;
- wallets the payout gate would refuse (offline mainnet validation: bad
  checksum, `PENDING_*`, test and audit fixtures) are skipped; a wallet that
  makes validation raise counts as refused, so one corrupt row cannot abort
  the seed.

## Contract for future changes

- A download is not an install. Never count one.
- Activation requires a Scout fact: the installer's registration, not a
  browser event. An install beacon alone is `INSTALLED`.
- No telemetry before consent: an opt-out install reports nothing.
- Add a new lifecycle fact as a new column plus a flag on `record()`; never
  overload an existing timestamp.
