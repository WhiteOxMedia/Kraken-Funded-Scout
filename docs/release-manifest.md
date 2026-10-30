# Release manifest

The public artifact is `dist/KrakenScoutBundle.zip`, served at
`https://xmrjackpot.com/downloads/KrakenScoutBundle.zip`. It is a
**customer** artifact: Scout, its runtime modules, the installer and the
miner payload. It is not a copy of either repository.

Before 2026-10-25 it was one: `git archive HEAD` of both trees produced
876 entries, including `AUDIT_MONERO_PROJECT.md`,
`E2E_ACCEPTANCE_REPORT.md`, `PHASE4*`, `PHASE5*`,
`KEY_MANAGEMENT_PLAN.md`, `verify_hostinger_corrections.py`,
`proxy_poller.py`, `jackpot_engine.py`, `pool/server.py`, every test, the
CI workflow and the operator's own xmrig `config.json`.

The rule now is **default-deny**. A file ships only when
`installer/release_manifest.json` names it. Forbidden rules win over
allow rules. Content rules fail the build outright.

## Files

| Path | Role |
| --- | --- |
| `installer/release_manifest.json` | The whitelist (data, committed, reviewed) |
| `installer/release_guard.py` | One implementation: prune, scan, zip check, version rule |
| `installer/generate_release_manifest.py` | Recomputes Scout's runtime file list |
| `installer/finalize_bundle.py` | Prunes + validates, then writes zip, `.sha256` and `.zip.json` |
| `installer/build_bundle.ps1` | Stages both trees, then calls the finalizer |
| `installer/check_release_hygiene.py` | CI gate over the repository |
| `pool/server.py` | Refuses to serve a zip that violates the manifest (503) |
| `test_release_guard.py` | Pins the guard's behaviour |

## Allowed

- `payload/scout/` — the import closure of `scout/app.py` (static
  imports, recursive), the launchers the product's shortcuts call
  (`Launch Kraken.bat`, `launch_stack.ps1`, `start_detached.ps1`, …),
  Streamlit theme, icons, audio cues, `pyproject.toml` / `uv.lock`.
- `payload/monero_project/installer/installer.py` and
  `installer_ui.py` — the setup flow. They talk to
  `https://xmrjackpot.com` over HTTPS.
- `payload/xmrig-6.26.0/xmrig.exe` + `SHA256SUMS` — the miner is a
  runtime dependency, not product code.
- Bundle root: `install.ps1`, `README.txt`, the two one-click `.bat`
  wrappers.

At the time of writing: **75 paths**.

## Forbidden

`.git/`, `.github/`, `__pycache__/`, `*.pyc`, `node_modules/`, `.venv/`,
`dist/`, `plans/`, `scratch/`, `reports/`, `artifacts*/`, `logs/`,
`battery_history/`, `test/`, `tests/`, `*.md`, `*.log`, `*.jsonl`,
`*.db*`, `*.lock`, `AUDIT_*`, `E2E_*`, `PHASE*`, `MEMO_*`, `*_REPORT*`,
`KEY_MANAGEMENT_PLAN*`, `COORDINATION_*`, `DIRECTIVE_*`, `test_*.py`,
`verify_hostinger_corrections.py`, `proxy_poller.py`,
`jackpot_engine.py`, `pool/server.py`, `pool/static/**`,
`pool/templates/**`, `config.json`, `start.cmd`, `start_pool.bat`,
`start_proxy.bat`, `start_wallet.bat`.

**Forbidden content** (build fails, nothing is silently pruned):

| Rule | Catches |
| --- | --- |
| `database-credential` | a connection string with an inline password |
| `bearer-token` | a hard-coded bearer credential |
| `api-key-literal` | `api_key = "…"`, `password = "…"`, operator tokens |
| `operator-wallet` | any Monero address baked into shipped code |
| `developer-rig-id` | the operator's xmrig rig id |
| `localhost-pool` | `127.0.0.1:3333/3334/9090/18082` |
| `preprod-version` | a non-production version label |
| `private-ntfy-topic` | `ntfy.sh/<topic>` |

Content is never auto-repaired: deleting a line from a program leaves a
broken program. Fix the source and rebuild.

## Release generation

```powershell
python installer/generate_release_manifest.py   # only when runtime files change
powershell -NoProfile -ExecutionPolicy Bypass -File installer\build_bundle.ps1
python cdn_purge.py                             # the edge caches the zip for 4h
```

`build_bundle.ps1` stages the two committed trees, removes the developer
xmrig config, and calls `finalize_bundle.py`, which:

1. runs the private-topic guard;
2. prunes everything not named by the manifest;
3. scans the survivors for forbidden content and fails on a hit;
4. zips deterministically, writes the SHA256 sidecar and
   `.zip.json` (version from the manifest, `release_manifest_sha256`).

Version policy: the customer version comes from the manifest
(`1.0.0`), never from `git describe`. `preprod`, `staging`, `dev-` and
`-rc` markers are refused by the guard. The exact commit is published
separately as `monero_head` / `scout_head`.

## Secret handling

- **Never** in source, tests, docs, or a bundle. Use environment
  variables or the deployment's secret store.
- `XMRJ_PROXY_API_TOKEN` — xmrig-proxy API token
  (was hard-coded `xmrjackpot2026`; treat that value as compromised and
  rotate it in `monero/proxy_config.json`).
- `XMRJ_HOUSE_WALLET`, `XMRJ_KRAKEN_DEPOSIT_ADDRESS` — operator payout
  addresses, read when the settings table is first created.
- `PUSH_OPERATOR_TOKEN` — generated server-side by `provision_push.py`
  into the settings table; never committed.
- `DATABASE_URL` — Scout's database. With none set, Scout uses a local
  SQLite file. The old hard-coded Postgres URL with its password is
  removed; **rotate that credential** — it shipped in previous bundles.
- `APP_PORT` (required), `APP_HOST` (defaults to `127.0.0.1`),
  `XMRJ_ALLOW_PRIVILEGED_PORT` (opt-in only), `XMRJ_STRATUM_URL`.

Rotation after any exposure: proxy token, database password, ntfy topic
(`push_topic.txt`, untracked), push operator token, Cloudflare tunnel
credentials. Check the old value is gone from git history and from
`dist/` before rebuilding.

## Serving rule

`pool/server.py` validates the zip on disk against the same manifest
before every (cached) response and answers **503** when it does not
match. A stale, hand-copied or half-built artifact is therefore not
downloadable even if it reaches `dist/`. A missing manifest is itself a
violation.

Deployment: one origin — `APP_PORT=3094`, bound to loopback, fronted by
the Cloudflare tunnel. `APP_PORT` has no default; a missing port stops
the process instead of picking 80.

## CI

`.github/workflows/ci.yml` → `release-hygiene`:

```bash
python installer/check_release_hygiene.py   # manifest, secrets, rig ids,
                                            # localhost endpoints, preprod,
                                            # install-script operator calls
python test_release_guard.py                # prune/scan/version contract
```

Both are blocking. The manifest drift check compares the committed list
with the Scout import closure when the sibling checkout is present; a new
runtime module fails until it is added to the manifest in review.
