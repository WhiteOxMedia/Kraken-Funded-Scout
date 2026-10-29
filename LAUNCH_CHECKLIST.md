# LAUNCH CHECKLIST — xmrjackpot.com (Oct 29)

One sequence, in this order. A step that fails is a stop: fix it or abort.
Every step's exit code is its verdict — no "looked fine".

## 0. Preconditions (must hold before step 1)

- [ ] `pool/server.py` is running on :3094 and healthy:
      `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3094/api/stats` → `200`
      (down first? `./roll_3094.sh` restarts and verifies the surface).
- [ ] The working tree contains exactly the content being shipped:
      `git status --porcelain` read once — every modified file should be
      a change you intend to launch. Nothing stray, nothing half-done.
- [ ] Bundle parity is green (it is the artifact the steps below ship):
      `python test_bundle_parity.py` → `RESULT: 12 passed, 0 failed`.
      If red: `python make_bundle.py`, then re-run until 12/12.

## 1. THE FLEET (the release check)

```
./test            # run_fleet.py beneath; ~2 min plus retries
```

Expect: every drive `PASS` (or honest `SKIP: pool server down` rows with
`RESULT: 0 passed, 0 failed` if the tunnel is off), ZERO `REGRESSION`.
`FLAKE` rows are loud but do not block.

- **Block on:** any `REGRESSION` (fails twice). Fix the fix, don't
  footnote it — launching with a red gate re-opens the exact stumbles
  this month just closed.
- **Block on:** a `SKIP` you did not expect (the live-server drive must
  not be skipping while :3094 is verified up).

## 2. THE COMMIT (narrow, message-true)

```
git add <the modified files you verified in step 0>   # never git add -A
git commit -m "<what this ships>"
```

- The pre-commit hook (`githooks/pre-commit`) re-runs the whole fleet
  on the staged tree and **blocks on REGRESSION** — that run, not
  step 1, is the one that proves the commit. Budget ~2+ minutes.
- Confirm the hook ran and passed; `git log -1` shows the ship commit.
- If the gate blocks: stop. Fix, re-run step 1 if source changed, retry.
- Do NOT push anywhere: there is no deploy target. The commit + the
  running :3094 IS the deployment (README: "read before assuming a deploy").

## 3. THE EDGE PURGE (clear the 4-hour cache window)

```
python cdn_purge.py --dry-run    # prints the URL list, calls nothing
python cdn_purge.py              # ONE API call purges them all
```

Needs `CLOUDFLARE_API_TOKEN` (or `CF_API_TOKEN`). Run this after every
bundle rebuild and every localized-page change; the file purges the whole
list on purpose. Expect one purge job id and rc 0.

## 4. THE OPS RECEIPT (the box agrees with the site)

```
python ops_status.py
```

Expect exit **0** and `all clear`: listener PID + `/api/stats` 200,
scheduler heartbeat fresh, last orphan scan small, scheduled task Last
Result 0, dead-man marker sane. rc != 0 = stop and read the line it
names — that is the alert, not a warning.

## 5. THE TUNNEL SMOKE TEST (public surface proves served)

```
for p in / /download /es/download /status /winners /how-to-play \
         /sitemap.xml /api/version /api/stats; do
  echo "$(curl -s -o /dev/null -w "%{http_code}" --max-time 12 \
         "https://xmrjackpot.com$p") $p"
done
```

Expect every line `200`; the two localized pages must match the
languages shipped. Then the version and the artifact must answer TRUTH:

- [ ] `/api/version` payload matches the version the bundle step \
      stamped (same `?v=` the homepage cites).
- [ ] The review artifact is live and untampered:
      `curl -s https://xmrjackpot.com/downloads/KrakenScoutBundle.zip | sha256sum`
      equals the contents of `/downloads/KrakenScoutBundle.zip.sha256`.
- [ ] Sign-in and the claim surface still answer as gates (a signed-out
      `/api/v1/status` or `/api/gamification` returns `401`, not 500).
- [ ] One hero number: `/api/jackpot/projection` returns a fresh,
      provenance-tagged prize; the homepage hero shows the same source.

Anything 404/5xx/empty on the public origin = stop; fix; re-run 3–5.

## Aborts

- Step 1/2 red → fix code, re-enter at step 1.
- Step 3 fails (no token / purge refused) → the site can still serve,
  but a stale bundle may live at the edge for up to 4 h — retry, or
  knowingly accept the window and record it.
- Step 4 rc 1 → launch OFF until the named surface is healthy.
- Step 5 red → `./roll_3094.sh --tunnel` (restart + re-verify both
  surfaces), then re-run steps 4–5.

## Done definition

Steps 1–5 all green in ONE sitting, the commit is `git log -1`, and the
public origin answers the smoke test with the shipped version, the
matching zip hash, and fresh jackpot numbers. That is a launch.
