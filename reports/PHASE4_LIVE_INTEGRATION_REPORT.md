# Phase 4 — Live‑Integration Validation Report

**Repo:** `monero_project` (the deployment — per README, editing pool/ sources and
restarting the :3094 server IS the deploy; a Cloudflare tunnel fronts :3094 as
`https://xmrjackpot.com`, so there is no separate staging environment to deploy into).

**Executed:** 2026-10-14
**Git HEAD:** `b797f4d THE RAFFLE TICKET UNIT CONTROLLER - every valid share is now displayed as exactl...`

## What "Phase 4" meant here

Because this repo is the live system, Phase 4 was reframed as an integration
validation against the **actual running system** instead of a build-to-staging
deploy. The run exercised:

1. **Restart from current sources** — `bash roll_3094.sh` killed the old :3094
   listener and started `pool/server.py` via the README PowerShell recipe
   (`APP_PORT=3094`, log to `logs_server_restart.txt`).
2. **Live surface checks** — the same shared probe contract `roll_3094.sh` owns
   (via `source cta_probe.sh`): `/api/stats` 200, page carries `data-cta-slot`
   tags, the Kraken click counter accepts a valid browser-shaped click (200),
   refuses a bot UA (403), and rejects a bad slot (422).
3. **Staged bundle parity/hygiene trio** — `test_bundle_parity.py` and
   `test_bundle_hygiene.py` over `dist/KrakenScoutBundle.zip` + sha256 sidecar +
   manifest.
4. **Server-internal integration drive** — `test_bundle_serve.py` (admin/session/gate
   contract around the served bundle), run through the repo's `.venv` because the
   system `python` on this box lacks `httpx2`.

## Results

### 1. Server restart + live surface — PASS

```
roll_3094.sh — exit 0, 5/5 checks passed

[10:26:36] git HEAD: b797f4d ...
[10:26:36] old :3094 listener pid 15256 — killing (README recipe)
[10:26:39] starting server (README PowerShell recipe, APP_PORT=3094, log logs_server_restart.txt)
[10:26:41] new :3094 listener pid 16392 is up
[10:26:41] verifying local surface (http://127.0.0.1:3094 — the build uvicorn serves)
  PASS stats health (http 200)
  PASS page carries 16 data-cta-slot tags
  PASS counter valid click (http 200)
  PASS counter bot UA (http 403)
  PASS counter bad slot (http 422)
[10:26:41] verdict: 5 checks passed, 0 failed
[10:26:41] roll complete — local verified (tunnel not checked; rerun with --tunnel)
```

**What this validates**
- startup health: server came up from current sources and answered `/api/stats` 200
  inside the bounded startup wait (per the roll script's 45s deadline, dual-bounded
  with per-attempt `--max-time 10`).
- cross-component alignment: the served page still carries the expected CTA-slot
  surface and the Kraken click counter still enforces its contract (valid browser
  click accepted, bot UA rejected, unknown slot rejected).
- regression check: an old :3094 listener was present and was killed before restart,
  so this was a real restart-from-current-sources, not a no-op.

**Tunnel note:** `roll_3094.sh` does not check the public `https://xmrjackpot.com`
path by default; that requires `--tunnel` and a live tunnel. This run verified the
local build surface only. If the public-facing parity matters for sign-off, rerun
`bash roll_3094.sh --tunnel`.

### 2. Staged bundle parity — PASS

`python test_bundle_parity.py` — 7 passed, 0 failed.

### 3. Staged bundle hygiene — PASS

`python test_bundle_hygiene.py` — 14 passed, 0 failed.

As a concrete data point in the hygiene run: the topic guard reported the live
rotation-following leg was **PROVISIONED** and scanned with the seed, not empty —
i.e., the public artifact check ran against an actual live topic, not a no-op path.

### 4. Server-internal integration drive — PASS

Run through `.venv/Scripts/python.exe` (not the system `python` — see the dependency
note below).

`test_bundle_serve.py` — 20 passed, 0 failed.

Key contract-shaped results inside that drive:
- the served artifact on disk is a real zip whose manifest hash matches bytes on disk
  and whose `.sha256` sidecar matches
- the stable download/checksum/info endpoints behave as published (admin session leg
  serves the exact artifact with `application/zip` and `nosniff`; the info endpoint
  stays public: hash + monero head + scout head)
- the dashboard card reads the same manifest the server publishes
- the bundle is now a private artifact: anonymous GET on the zip and the checksum
  both return 401, a known non-admin session returns 403, a wrong token returns 401,
  and a stored-empty-setting + supplied-token case is fail-closed 401
- payload hygiene: installer present (805 entries), xmrig SHA256 pin rides inside
  the bundle's `install.ps1`, README ships at the bundle root

Note on pytest vs direct run: `test_bundle_serve.py` is a prose drive, not a pytest
test module — `python test_bundle_serve.py` exits 0 with the `RESULT: 20 passed, 0
failed` line that the fleet runner parses. Running it as `pytest test_bundle_serve.py`
right now yields an internal pytest error because the module ends with `sys.exit(0)`
(= a `SystemExit` pytest treats as an unhandled abort rather than a normal exit). That
is pytest-hostile script structure, not a drive failure: the actual drive ran and
passed when invoked the way the repo intends (direct python run). If pytest invocation
of this file matters as a workflow, that script shape is a separate, minor fix, but it
is not required to validate the bundle serve contract.

## Dependencies / environment

- Initial run of `test_bundle_serve.py` with the system `python` failed immediately:
  importing `fastapi.testclient.TestClient` raised
  `RuntimeError: The starlette.testclient module requires the httpx2 package to be installed.`
- Root cause: the default `python` on this box is the system Python (`C:\Python314\python.exe`),
  which does not carry `httpx2`. Starlette's test client checks for it at import.
- The repo's `.venv` already carries `httpx2`
  (`.venv/Scripts/httpx2.exe` + `httpx.exe` both present; `pip show httpx2` from the
  venv reports httpx2 2.13.1). So the fix that clears the drive is to run it through
  `.venv/Scripts/python.exe`, not through the system `python`.
- Concrete verification: after switching to the venv interpreter, `test_bundle_serve.py`
  returned exit 0 with `RESULT: 20 passed, 0 failed`.

## Summary

| Area | Verdict | Evidence |
|---|---|---|
| Restart from current sources | PASS | `roll_3094.sh` exit 0, new pid 16392, /api/stats 200 |
| Startup health | PASS | bounded startup wait satisfied |
| Live surface: CTA slots | PASS | 16 `data-cta-slot` tags on the served page |
| Live surface: Kraken counter contract | PASS | valid click 200, bot UA 403, bad slot 422 |
| Staged bundle parity | PASS | `test_bundle_parity.py` 7/7 |
| Staged bundle hygiene | PASS | `test_bundle_hygiene.py` 14/14 (topic guard PROVISIONED+scanned) |
| Server-internal integration drive | PASS | `test_bundle_serve.py` 20/20 via `.venv/Scripts/python.exe` |
| Public tunnel parity | NOT RUN | requires `roll_3094.sh --tunnel` + a live tunnel |

## Remaining gap for a fuller Phase 4 sign-off

- **Public tunnel parity** is unverified in this run. If Phase 4 is meant to also
  assert the *public* `xmrjackpot.com` surface reflects the restart, rerun with
  `--tunnel` (or otherwise probe the tunnel origin) when the tunnel is up.
- **pytest-native invocation** of `test_bundle_serve.py` is blocked by the script's
  current `sys.exit(0)` tail, which pytest treats as an abort. This is a pytest-hostile
  drive shape, not a bundle/server defect, and the drive is validated by its intended
  invocation (direct python run). If pytest invocation of this file is a desired workflow,
  that is a separate, minor script-shape fix.

Everything else that could run, ran and passed.
