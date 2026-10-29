# monero_project — XMR Daily Jackpot Pool

A Monero daily-jackpot pool: FastAPI server (`pool/server.py`, port 3094),
native push system, Kraken referral projection, and the Scout funnel
(see the sibling `kraken_scout` checkout for the trading half).

## The drive fleet

**Every `test_*.py` in the repo root is a drive.** New drives join
automatically — nothing is registered anywhere. Drives print a final
`RESULT: N passed, M failed` line (parsed for the ledger); prose-style
drives without one are graded by exit code. Every drive owns its own
sandbox (temp-dir DB, stubbed transports) — the live `pool/jackpot.db`
is never written by a drive, and external legs (wallet RPC, Kraken, the
push library) are stubbed at their boundaries so a drive is hermetic by
construction.

### Standing fleet fixtures (must survive DB sweeps)

Two probe accounts live in the live `pool/jackpot.db` on purpose. The
live-server drives never touch the DB file — they go through the API
like real clients — but they re-register-or-login these accounts every
run, so deleting them makes the next fleet run fail or re-bootstrap
into the 5/hour register limiter. Database cleanups must PRESERVE:

- `security_probe` — owned by `test_security.py` (cookie-hardening
  checks ride a real login).
- `prefs_live_probe_fleet` — owned by `test_prefs_live_server.py`.
  A unique suffix per run exhausted the register limiter under
  repeated fleet runs; the fixed name registers once, logs in every
  run after, and restores its own state at the end.

The rule the completed sweeps follow: fix the fixtures, remove the
probe debris around them, and leave an audit row with the full
manifest (`users.probe_swept`; `jackpot.orphans_swept` for the FK
sweep). Pre-sweep backups land next to the DB as
`_backup_jackpot_pre_*.db`.

The discipline lives in `sweep_lib.py` and its operator face is
`sweep_db.py`: a bare `python sweep_db.py` is a READ-ONLY dry run
(it rehearses the labeled statements on a throwaway backup-API
copy, prints the per-label counts and the gate verdict, and
writes nothing), while `python sweep_db.py --apply` performs the
sweep through `sweep_lib` - backup beside the DB, the
`PRAGMA foreign_key_check` gate INSIDE the transaction (a
violation rolls the whole sweep back), and the manifest audit
row. New FK edges need a new labeled statement pair in
`sweep_db.py`; the dry run flips to `would ROLLBACK` until one is
added. Pinned by `test_sweep_cli.py`.

The planning half is `python sweep_db.py --report` (backed by
`sweep_lib.orphans()`): it reads every FK edge from the schema
itself and reports per-edge violation counts with sample rowids
and the disposition a sweep would take (delete / null /
UNPLANNED) - all through a mode=ro connection, so a report can
run against the live primary with zero write access. Its EXIT
CODE is the alert (0 all-clear, 1 violations found, 2 an edge is
UNPLANNED), so cron/alerting reacts to the receipt itself.
Pinned by `test_fk_regression` 3c and `test_sweep_cli` section 6.

Scheduled alerting: the Windows task `CodebuffWeeklyOrphanReport`
(Sundays 06:15, user-level) runs `run_sweep_report.cmd`, which
appends the receipt to `logs_sweep_report.txt` and leaves the
exit code (0/1/2 above) in Task Scheduler's Last Result -
alerting reacts to the rc without parsing the log. The task is
registered by the tracked `register_sweep_report_task.py` (which
carries a `--check` self-audit: both alert properties, the 1h
execution limit, battery-safety, and the dead-man marker) and
runs `run_sweep_report_task.ps1`, a DUE-GATED wrapper - at most
one actual report per 7d window, so a StartWhenAvailable
catch-up (a missed Sunday fires at the next available start)
can never double-report, and every firing is rc-safe. The
pool scheduler polices the wrapper's last-ACTUAL-run marker
(`task_sweep_report_last.txt`): missing or older than 8 days
-> `alert=true` + one `scheduler.orphans_deadman` audit row per
day, because a scheduled task that stops running is the one
failure no report can reveal. Re-create the
task after moving the checkout (the wrapper resolves its own
directory, but the task's command line is absolute):
`schtasks //Create //TN CodebuffWeeklyOrphanReport //TR
"'<repo>un_sweep_report.cmd'" //SC WEEKLY //D SUN //ST 06:15 //F`.

The pool scheduler's weekly ops digest (leg 7, Sep 27) pushes ONE
line to the admin's push subscribers after each scan window:
orphans (total + edges), the week's prune counts (retention +
sessions), and the Kraken weekly verdict (count + last verdict).
It composes from the audit LEDGER inside the window - the same
Sunday 06:15 boundary the task fires on - so it reports the fresh
scan this week actually ran, and a stale scan prints as unknown
rather than an old number. Clean weeks send too (the all-clear is
the value); the receipt is ONE `scheduler.ops_digest` audit row
per week, the marker is stamped only on success (failures retry
next tick), and the dynamic line rides
`send_admin_push("ops_digest", extra={"_digest_line": ...})` - a
body override scoped in source to the digest event alone.
Operator dials: `SCHEDULER_DIGEST_ENABLED`,
`SCHEDULER_DIGEST_INTERVAL_S` (sub-weekly keeps interval
semantics); the block surfaces in the admin status under
`digest`.

```
./test                          # the whole fleet (run_fleet.py underneath)
./test --only push prefs        # substring subset
./test --fail-fast              # debug loop: stop at the first regression,
                                #   print that drive's full output
./test --history                # per-drive FLAKE% / REGRESSION% over time
python run_fleet.py --list      # show the discovered drives and exit
```

### The commit gate

`githooks/pre-commit` runs the entire fleet before every commit lands
(`git config core.hooksPath githooks` — set per clone, done
automatically by `python setup_dev.py`).

- **REGRESSION blocks the commit.** A failure that reproduces on the
  rerun is real; fix the drive, or bypass once with
  `git commit --no-verify`.
- **FLAKE does not block.** A failure that passes on the rerun is
  triaged as a flake and listed loudly — instability is a warning, not
  a wall.
- **`FLEET_ARGS` subsetting:** `FLEET_ARGS="--only push prefs" git commit …`
  runs a subset at the gate (for iterating on an unrelated one-liner).
- A gate run costs ~2 minutes; give your terminal a generous timeout.

### Skip semantics (and the triage vocabulary)

A drive may skip **itself** — honestly, on purpose. The live-server
drive (`test_prefs_live_server.py`) probes `:3094` first and answers
`SKIP: …` + `RESULT: 0 passed, 0 failed` (exit 0) when the pool server
is down, so the fleet stays green on boxes without the tunnel. The
runner gives a skipped drive one cooldown retry (65s — the stats rate
limiter's window) before accepting the skip, because a fleet run can
trip a limiter it caused itself.

The runner classifies every drive into exactly one of:

| Status | Meaning |
|---|---|
| `PASS` | green on the first run |
| `SCENARIO` | exit 0, no RESULT line (prose-style drive) |
| `SKIP` | the drive skipped itself (twice, after the cooldown retry) |
| `FLAKE` | failed the first run, passed the rerun — a warning, listed |
| `REGRESSION` | failed both runs — blocks the commit |

A SKIP is **never** a FAIL and **never** silently green: it is a named
row in the ledger with a reason. The same honesty rules apply in
reverse — a drive that answered green with zero checks when it meant to
verify something is a bug (the live-contract wrapper in the Scout repo
exists precisely because of that class).

History: every classified run appends one JSON line to
`fleet_history.jsonl` (gitignored — it churns per run, including inside
commit gates). `./test --history` turns it into per-drive flake rates,
so "this drive flakes every third run" is a number, not folklore.

## How run_battery.py differs (the Scout repo)

The sibling `kraken_scout` checkout has its own one-command sweep,
`run_battery.py`, and it is **not** the same tool as `run_fleet.py`:

| | `run_fleet.py` (this repo) | `run_battery.py` (kraken_scout) |
|---|---|---|
| **Role** | the commit gate — every commit survives the drives | the scheduled whole-verification sweep (~20 min, 100+ entries) |
| **Entries** | `test_*.py` drives, auto-discovered | `test_*.py` suites **plus** `diag_*` worlds (`DIAG_RUN` names the runnable ones; the rest are listed as SKIP so nothing silently disappears) |
| **Runtime control** | per-drive timeout + rerun triage | lanes by measured time: `--fast` (terminal-safe by construction, budget-bounded), `--slow` (the long tail), plus auto-chunking and resume (`battery_resume.json`) so a window death costs one entry |
| **History** | `fleet_history.jsonl` + `--history` rates | `battery_history/` JSON records + week-over-week delta printed after each run |

The two touch exactly once, deliberately: the battery's
**live-contract entry** (`test_pool_prefs_live`) executes this repo's
live prefs drive through **this repo's venv** (it needs the pool's
fastapi/starlette; the Scout venv does not carry them). That entry never
runs in the battery's `--fast` lane — fast is hermetic by construction
and a live surface has no place in it — and when the pool server is
down, its honest self-skip surfaces as a SKIP row in the battery's
ledger ("pool server down"), the same skip semantics both repos share.

## Runtime operations

**HOW xmrjackpot.com IS SERVED (read before assuming a deploy).
The public site is THIS repo, running LIVE on this machine — there is
NO hosting provider, NO deploy target, nothing to push to.** The
FastAPI server (`pool/server.py`) listens on localhost :3094 and a
Cloudflare Tunnel fronts it as https://xmrjackpot.com; miners reach
`mine.xmrjackpot.com:3333`, a DNS A record straight to this PC with
the port forwarded. Consequences any reader (human or agent) must
apply: editing `pool/` sources and restarting the :3094 server IS the
deployment; `dist_site_review/` (briefly mis-named `dist_hostinger/`
after the Sep 25 review round) is a reviewers-only artifact —
regenerated by `make_bundle.py`, verified by `test_bundle_parity.py`,
never shipped anywhere; `run_fleet.py`'s gate is the release check.
Verified live on Oct 10-11: the tunnel serves the committed build, and
both the user and the agent have misread the old dir name as a deploy
target, so this note stays pinned.

**The pool server on :3094.** Restart it (bash on Windows — the PID
comes from the listener, the server runs hidden and logs to a file):

ONE-COMMAND PATH (Oct 12): `./roll_3094.sh` does the whole recipe below
AND verifies the surface (page CTA tags + the counter gate triangle,
valid/bot/bad-slot). `./roll_3094.sh --tunnel` also re-checks
https://xmrjackpot.com so the public site is proven to serve the new
build. Manual recipe, when you want each step by hand:

```bash
taskkill //F //PID $(netstat -ano | grep ":3094" | grep LISTEN | awk '{print $5}' | head -1)
powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c set APP_PORT=3094&& .venv\\Scripts\\python.exe pool\\server.py > logs_server_restart.txt 2>&1' -WorkingDirectory 'C:\\Users\\birds\\Workspace\\monero_project' -WindowStyle Hidden"
sleep 9 && curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3094/api/stats
```

The `curl` health check is the confirmation step — 200 means the fleet
gate's live drive will run, 000 means it will self-skip (honestly).

**Languages and region (Oct 24).** `/download` and `/status` exist in
English, Spanish, Portuguese, Russian, German and French. The copy is
data, not markup: `pool/templates/i18n/<lang>.json`, rendered by
`pool/i18n_pages.py`. Add or change a sentence there, never in the
page builders, and run `test_i18n_region.py` — it fails if a language
is missing a key or drops a `{placeholder}`, because the silent
fallback would otherwise ship an English sentence inside a Spanish
page.

English owns the unprefixed URL; every other language lives under its
code (`/es/download`, `/de/status`), `/en/...` 301s to the unprefixed
page, and every page lists all six plus `x-default` as `hreflang`
alternates. A browser asking for Spanish is redirected once; the
selector at the top of each page sets `xmrj_lang`, and that choice
beats the header from then on. `/winners` and `/how-to-play` are
English-only on purpose — the download page says so in each language
instead of pretending otherwise, and only pages that really exist in a
language are listed in the sitemap or advertised as translations.

**Two products on /download (Oct 24).** The page offers the free miner bot
(`/api/miner-download`, a .bat that pins xmrig 6.26.0 by SHA256) and
Kraken Scout (`/downloads/KrakenScoutBundle.zip`). Scout is the paid
product: the download is free, activation is Scout Pro — the price and
duration come from the same settings the checkout reads
(`scout_pro_price_xmr`, `scout_pro_days`; 0.01 XMR / 30 days by
default) — and unlocking happens in the dashboard, not on the download
page. The checkout is Monero-only: it opens a fresh subaddress for the
order, matches the transfer, grants the entitlement, and credits the
donation to the pot once per order. There is no XMRJ unlock; the
`xmrj_*` Solana programs exist but are undeployed and uncalled (see
MEMO_COMPLIANCE_AUDIT.md B28). Two honesty rules ride with this: the
miner .bat writes a placeholder wallet, so the page says outright that those
shares are not credited and points at the dashboard installer instead;
and the xmrig pin is the release **zip's** SHA256, not the exe's
(Oct 24: the fallback named `xmrig-6.26.0-msvc-win64.zip`, which GitHub
404s, and its pin was the hash of the exe *inside* the archive — no zip
could ever match it, so every fallback install aborted as if the
download had been tampered with). Both installer copies in the bundle
carry the same pin: `build_bundle.ps1` now syncs
`payload/monero_project/installer/install.ps1` from the working-tree
installer, because the payload is `git archive HEAD` and can lag it.

Region needs no GeoIP service: Cloudflare already attaches
`CF-IPCountry` and the edge colo in `CF-Ray`, so `pool/i18n.py` maps
the country to a continent and the download page says whether the
visitor is in the relay's region or far from it. Two limits are
deliberate. There is no per-visitor latency probe — the browser cannot
open a TCP connection to `3333`, and timing the web endpoint would
measure the tunnel, not the relay — so the page gives qualitative
latency advice and no invented milliseconds. And no language claims
mining is blocked or illegal somewhere: no jurisdiction bans Monero
mining and no source lists "XMR-blocked countries", so the page
advises on distance only (a test asserts no catalog contains such a
claim).

**Cache purge (Oct 24).** Cloudflare serves the bundle zip with
`max-age=14400`, so a rebuild can be live at the origin and still be a
four-hour-old object at the edge. `python cdn_purge.py` is the step
that closes that window: it purges the bundle zip, its `.sha256`,
`/api/downloads/bundle-info`, `/download` in all six languages,
`/status`, `/api/version`, `/` and `/api/miner-download` in one API
call. Run it after **every** bundle rebuild and **every** localized-page
change; `/` additionally matters when the navbar, footer or global JS
moved, and `/api/miner-download` when the miner .bat changed. It purges
the whole list regardless, because a single call costs the same and
forgetting a conditional entry is the failure the file exists to
prevent. Two URLs that look like they belong are deliberately absent,
because neither is routed and purging them purged a 404 —
`/downloads/KrakenScoutBundle.zip.json` (the manifest is a file behind
`/api/downloads/bundle-info`) and `/index` (the front page is `/`);
`cdn_purge.py` carries the note. The token comes from
`CLOUDFLARE_API_TOKEN` / `CF_API_TOKEN` or a file path — `--dry-run`
prints the URLs without calling the API.

**Miner ingest key (Sep 27).** `/api/miner/shares` is an economy
writer — its share deltas backfill free jackpot tickets (100 shares
= 1 ticket) — so it is gated: a valid HMAC-SHA256 over
`"{ts}.{wallet}.{shares_valid}.{shares_invalid}"` (header
`x-miner-sig`, with `x-miner-ts` within ±300 s, signed with the
`miner_ingest_key` setting) is accepted from any source, and a
loopback caller is accepted ONLY while no key is provisioned
(pre-provisioning ops mode — the gate is fail-closed once the key
exists). Per-call deltas are capped at 500,000 shares, calls are
rate-limited per source IP, and the wallet shape rule is enforced.
Provision a key (the gate reads the setting per call — no restart
needed):

```bash
python -c "import secrets, sys; sys.path.insert(0, 'pool'); import database as db; db.set_setting('miner_ingest_key', secrets.token_hex(32))"
```

**Provisioned on this box (Sep 27):** the key exists, so the
no-key loopback ops mode is GONE — every unsigned or wrong-key
call is refused 403 from every source (verified live: loopback,
LAN, and wrong-key probes all refused with zero rows written;
receipt in the audit log as `ops.miner_ingest_key_provisioned`).
The proxy poller is unaffected by design (direct DB writes — it
has never used this route). The key is stored plaintext in the
`settings` table on purpose: the poller signing path needs it
readable by any process that writes the DB, and the settings
table itself is the trust boundary. The backup taken before the
change (`pool/jackpot.db.pre-ingestkey-*.bak`) also predates the
key — restoring it would silently re-open ops mode, so delete
those pre-ingestkey backups once the post-provisioning backups
are trusted. To rotate, re-run the one-liner (the new value
replaces the old; loopback closes again on the next call).

**Security headers + CSP (Sep 27).** Every response - pages, API,
static files, and error responses - carries the pinned set from
`SecurityHeadersMiddleware` (pool/server.py): CSP
(`default-src 'self'`, `script-src 'self'`, `style-src 'self'
'unsafe-inline'` - the templates style through style attributes -
`frame-ancestors 'none'`, `upgrade-insecure-requests`),
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
`X-Frame-Options: DENY`, `Permissions-Policy`, and COOP. Inline
event handlers are forbidden by the CSP: app.js injects
`data-action` attributes resolved by one delegated listener.
Pinned by test_security.py section E; when editing app.js, keep it
that way.

**The TLS flip (when the Hostinger front terminates HTTPS).** Two
things are deployment-gated and must move together: the session
cookie's `Secure` flag (pool/server.py sets `secure=is_https`, so
it happens automatically once requests arrive as HTTPS or with
`X-Forwarded-Proto: https`) and HSTS, which the front must add
(`Strict-Transport-Security: max-age=31536000; includeSubDomains`).
test_security.py B1.2 pins the LOCAL absence of `Secure` - flip
that pin only when the flip happens.

**Logs.** `logs/` carries `pool_stdout.log`, `pool_stderr.log`,
`server_restart.log`, and `xmrig.log`; the restart command above
tee's its own run to `logs_server_restart.txt` in the repo root. Log
files are runtime state — gitignored, safe to delete while the server
is stopped.

**Shipping a frontend change.** Static files and templates are
cache-busted by version query strings, so the workflow is:

1. edit `pool/static/*` and/or `pool/templates/index.html`;
2. bump the touched script's `?v=` in `pool/templates/index.html`
   (the served HTML must reference a version the browser has not
   seen — parity checks in the fleet are version-agnostic on
   purpose);
3. `python make_bundle.py` — regenerates the Hostinger bundle from
   the new files and runs the parity gate itself;
4. commit — the fleet gate re-proves bundle parity and the text/
   contract drives.

**Registering a live drive in the Scout battery.** When a pool drive
becomes a battery live-contract entry, copy the fill-in template
beside the Scout battery (`kraken_scout/LIVE_CONTRACT_TEMPLATE.py`) —
it encodes the whole convention: execute through the pool venv,
stream output verbatim, map the drive's honest self-skip signature
(RESULT 0/0 **plus** a `SKIP:` line) to the battery's SKIP vocabulary,
and add the wrapper to `LIVE_ENTRIES` so `--fast` stays hermetic.

## The payout contract (operator-facing, Oct 28)

**Read this before touching a payout, a claim, or a rate.** One
winner's prize moving out of the treasury has three rules attached;
they are enforced in code, pinned by drives, and described here as
the contract the operator operates against.

**Rule 1 — the winner's coin decides (same-coin payout).** A miner who
talked to the pool through the Verus relay is paid in VRSC from the
treasury; every other winner is paid XMR through the wallet RPC or
the standing manual leg. The pool's BOOKS stay in XMR-equivalent
units no matter what moved: `payouts.amount_xmr` keeps the XMR
number every old reader expects and `payouts.amount_coin` carries
the VRSC quantity the winner is owed. Without a Verus wallet RPC,
the VRSC leg is manual — the row waits in `pending` with
`tx_hash = pending_manual_treasury` until the treasury officer's
send flips it (the same admin tool that flips the XMR-from-Kraken
leg). The winners page and the 💸 admin card both show the coin and
the pending-treasury state, not just the equivalent. Pinned by
`test_vrsc_miner.py`, `test_vrsc_coin_surface.py`.

**Rule 2 — no payout at a stale rate (the stale-rate guard).** The
claim is the one place an old rate silently misprices real money
(bold labels on pages are merely old; a VRSC quantity SETS A
PAYOUT). Before any same-coin conversion, both legs are age-checked
— the VRSC/USD snapshot and the XMR/USD snapshot — in
`jackpot_funding.vrsc_conversion_guard()`:

- **fresh** (≤ `VRSC_RATE_STALE_S`, db setting, default **900 s**):
  pay silently.
- **warn** (> 900 s, < warn × 3): pay LOUDLY — audit row
  `vrsc_rate_stale_warned` carries both legs' ages, and the claim
  response payload carries the same sentence in `rate_warning`.
- **block** (≥ warn × 3 = 2700 s at defaults, or a leg with no
  snapshot at all): refuse with
  `VRSC payout blocked by the stale-rate guard: …`, audit row
  `vrsc_rate_stale_blocked`. The refusal happens BEFORE any db
  mutation, so the recovery is one step: refresh the rates (or pin
  an operator override — a pin is a vouched value, not a snapshot,
  and freshens its leg), then re-claim. Nothing is stranded; the
  claim window is untouched.

Operator knobs: `VRSC_RATE_STALE_S` moves the warn window, and the
block always scales as warn × 3. A zero/nonsense value falls back to
the 900 s constant. Pinned by `test_vrsc_rate_stale.py`.

**Rule 3 — one drawing, one payout (the claim lease).** A double
click, a fast retry, or two admin screens all arrive as two threads
that BOTH read `claim_status = 'pending'`. Reading is not owning:
the mint is gated by `db.acquire_claim_lease()` — ONE conditional
UPDATE (`pending → 'claiming'`) whose loser matches 0 rows and is
turned away at "another claim is in flight" before any money moves
(audit `claim_race_lost`). Every post-lease refusal releases the
lease, so refused claims stay re-claimable the ordinary way. A
claimed drawing can never be leased again; a lease older than the
**300 s steal window** means a claimant died mid-flight — the next
acquisition steals it and the prize is never stranded. Pinned by
`test_vrsc_concurrent_claim.py`.

**The operator's quick reference:**

| To check… | grep the audit log for | Act when you see it |
| --- | --- | --- |
| a payout waiting on the treasury leg | `vrsc_payout_pending_treasury` — or `tx_hash = pending_manual_treasury` rows in the 💸 card | send the VRSC, flip the row |
| a stale-rate warn | `vrsc_rate_stale_warned` | rates are > 15 min old — refresh before the next same-coin claim |
| a stale-rate block | `vrsc_rate_stale_blocked` | the claim refused; refresh/pin rates and tell the winner to re-claim (window still open) |
| a lost claim race | `claim_race_lost` | expected under double-clicking; no action unless same drawing repeats |

The claim-window facts the rules sit inside: a drawn prize is
claimable for `claim_window_days` (db setting, default 7), the sweep
expires it and returns the earmark to the pool, and everything above
is retriable inside the still-open window.

All three rules in one envelope: `python test_payout_contract_battery.py`
runs the three drives together as one battery entry — each drive
keeps its own child-`result:` echo (15 + 23 + 22 = 60 checks), and
one entry-wide `RESULT:` sum is what the fleet's ledger parses. The
entry joins the fleet by the same auto-discovery every `test_*.py`
ride uses (85 drives today), so the pre-commit gate runs the whole
envelope on every commit, not just the three drives individually —
a system-wide regression in the contract is one red row, not three.

## Related one-command tools

- `python setup_dev.py` — new-clone setup: venv from `uv.lock` + the
  commit-gate hook, then verifies both (idempotent).
- `python make_bundle.py` — regenerate the Hostinger review bundle from
  the live template + statics; runs the parity drive as its own gate.
- `python ops_status.py` — the one-command health receipt: bundle
  version, :3094 listener PID + `/api/stats` health, scheduler
  heartbeat age, last orphan scan (age + totals), the scheduled
  task's Last/Next Run + Last Result, the dead-man marker age, and
  the scout sibling's guard factor (latest value + history day-span —
  a read-only peek so the sizing brain's series stays visible on the
  receipt; absence is an honest NONE, never an alert).
  Strictly read-only (the DB opens `mode=ro`). Exit code is the
  alert: 0 all-clear, 1 when any surface needs a human — server
  down, stale heartbeat (the loop ticks hourly, so the threshold is
  1.5x the cadence), orphan build-up, failed task run, or a stale
  dead-man marker. `--json` for machines. Pinned by
  `test_ops_status.py`.
