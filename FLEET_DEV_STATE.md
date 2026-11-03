# FLEET_DEV_STATE.md — what the nine baseline drives assume, and how a fresh worktree satisfies it

The fleet (`run_fleet.py`) grades a fresh clone as:

```
90 drives | 2657 checks | PASS 79 | FLAKE 0 | SKIP 2 | REGRESSION 9
```

The nine REGRESSION rows are not broken code — they are **assumptions about
local dev state** that a fresh clone does not have, because the state is
operator-local or a build artifact (gitignored by design). This file is the
inventory; `tools/fleet_devstate.py` is the same inventory executable
(dry-run default, `--apply` to seed).

## The state items

| # | Item | Why gitignored | Seeded by `--apply`? |
|---|------|-----------------|-----------------------|
| 1 | `pool/jackpot.db` (app schema + the two standing fleet fixtures) | operator data — a live DB is a customer surface, never in git | **yes**: when the file is absent or a zero-table stray, `init_db()` seeds the schema and the fixture users are added **additively** |
| 2 | `pool/.keys/field_key` (the at-rest field key) | operator secret | **yes**: generated only when absent (a throwaway, disposable key) |
| 3 | `node_modules/` | dependency tree, rebuilt from the lockfile | **yes**: `npm ci --no-audit --no-fund` (needs the network) |
| 4 | `target/deploy/*.so` + `target/idl/*.json` | `anchor build` outputs | **no** — needs the Solana toolchain (`anchor build`) |

The two standing fixtures are `security_probe` and `prefs_live_probe_fleet`
(`test_fk_regression` 4.3 pins their NAMES); the seeder inserts them
additively so that drive reads 2/2 on a seeded box.

## Drive-by-drive: what each red row actually needs

| Drive | Baseline failure | State behind it | Fresh-worktree fix |
|-------|------------------|-----------------|--------------------|
| `test_fk_regression.py` | `"?mode=ro" … unable to open database file` (the live canaries §4) | 1 — reads `pool/jackpot.db` read-only and expects **both standing fixtures present** (4.3) | seeder item 1 |
| `test_kraken_seam_live.py` | `no such table: settings` (the settings tier's SELECT crashes before its own "nothing provisioned" skip can fire) | 1 — the read happens before nothing-provisioned is knowable | seeder item 1 (with nothing provisioned the drive then exits 0 with a named SKIP: honest) |
| `test_sweep_cli.py` | the bare-CLI rehearsal `1.1` against `sweep_db.py`'s `DEFAULT_DB` (the live `pool/jackpot.db`) | 1 — a zero-table (or missing) file has no statements to rehearse | seeder item 1 (the rehearsal is a dry-run and still never writes) |
| `test_referral_entries.py` / `test_referral_programs.py` | keyed blind-index failures (`1.2,1.3,1.4,2.1,4.1` / `1.2,1.3,2.1,4.4,6.2`) | 2 — `get_user_by_email` resolves through the keyed blind index; with no `pool/.keys` the index is empty and every award misses | seeder item 2 (alternatively each drive provisions its own hermetic key in a temp dir) |
| `test_xmrj_offline_gates.py` | `1.2` (and its red-first `4.1`) dies on `module not found` in the `npx ts-node` harness | 3 — the drive copies the unbuilt tree INSIDE the repo on purpose (ts-node must resolve from `node_modules`) | seeder item 3 |
| `test_rate_label.py` | `no such table: tickets` at 4.6 | seam of item 1 + **two database module instances** — see below | seeder item 1 removes the crash; the green fix is drive-side (see "The rate_label seam") |
| `test_xmrj_build_artifacts.py` | `1.1/1.2` refuse an unbuilt checkout | 4 — `anchor build` outputs | run `anchor build` on a machine with the Solana toolchain; until then the drive refuses as built (its own contract) |
| `test_kraken_cta_counter.py` | check `55` pins `tools/cta_probe.sh`'s SHA — the pin is LF-normalized, a Windows checkout lands the file as CRLF | **no dev state** — a drive-side pin bug | port the one-line fix (hash the EOL-normalized bytes); any real probe edit still re-pins. A Linux fresh clone greens as-is |

## The rate_label seam (why one state item is not enough)

The pool's house scripts import `database` **flat** (with `pool/` on
`sys.path`), but the server mounts the scout surface as a **package**
(`import pool.scout_endpoints`), and scout_endpoints imports
`from . import database as db`. Consequence: while conftest's sandbox
points the flat module at a tempfile, the *package* module still resolves
the default `pool/jackpot.db` — so the member-dashboard route (4.6) reads
the operator DB the sandbox never touched, and on a fresh clone that read
is what created the zero-table stray file.

The seeder removes the crash (the reads land on a schema'd file and a
missing session degrades to 401), but the green fix lives in the drive
itself: the live-DB probe that gates 4.x and grades its absence as a named
SKIP. Port that change so the drive is bisectable on every machine.

## The recipe

```bash
# audit a fresh clone's state, no writes
python tools/fleet_devstate.py            # dry-run receipt (exit 0 always)

# produce everything a fresh worktree can have hermetically
python tools/fleet_devstate.py --apply

# audit strictly (useful from another checkout / CI)
python tools/fleet_devstate.py --check

# seed a different worktree (the template-seed use)
python tools/fleet_devstate.py --root /path/to/other/worktree --apply
```

`--apply` is safe by construction: additive inserts only, never a rotate or
delete, and never a seeded key rotation — an existing key is reported and
left untouched.

## What stays honest anyway

1. `test_xmrj_build_artifacts.py` refuses an unbuilt checkout — its
   red-first mutations grade that refusal. Only `anchor build` greens it.
2. `test_kraken_seam_live.py` with nothing provisioned exits 0 with a SKIP —
   the operational fact (both seams resolve the same tier) can only be
   proven where credentials actually exist.
3. The rate_label 4.x tier reads the operator DB through the package
   module; on a stateless box with the drive-side gate ported it grades as
   a named SKIP, never a silent green.
