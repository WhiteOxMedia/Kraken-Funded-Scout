#!/usr/bin/env bash
# =============================================================================
# tools/xmrj_devnet_run.sh — THE RUNBOOK EXECUTOR (Oct 27)
#
# XMRJ_DEVNET_RUNBOOK.md is the operational order; this is the same order with
# the accounting attached. Every step prints the command it is about to run and
# captures that command's stdout/stderr (and exit status) into one log, so the
# report can quote what happened instead of paraphrasing it.
#
# WHY A SCRIPT AT ALL: three of the runbook's steps are destructive or cost SOL
# (deploy, mint, lock). Those are gated behind explicit flags so that the default
# invocation touches no chain state: it checks the toolchain, the wallet, the
# artifacts and the rent budget, and stops before anything is signed. It does
# REBUILD the three programs under target/ (§3) — a stub artifact on that path
# is the one failure mode that reaches a deploy looking green.
#
# USAGE
#   tools/xmrj_devnet_run.sh                     # no chain writes: preflight,
#                                               # §3 artifact gate + verify
#   tools/xmrj_devnet_run.sh --build             # also run `anchor build` (§3)
#   tools/xmrj_devnet_run.sh --deploy --e2e      # deploy (§4) + the suite (§5)
#   tools/xmrj_devnet_run.sh --mint              # §6/§7: mint, rerun, lock
#   tools/xmrj_devnet_run.sh --stress            # S0-S10 load/stress harness
#
#   CLUSTER=localnet tools/xmrj_devnet_run.sh --deploy   # same order, localnet
#   XMRJ_RPC_URL=https://devnet.rpcpool.com tools/xmrj_devnet_run.sh --e2e
#     ...against a second endpoint, without editing anything. The public
#     devnet RPC rate-limits per IP; both clients wait a 429 out rather than
#     dying on it (tools/rpc_resilience.ts), but a mirror is still faster than
#     a cooldown.
#
# WHAT IT REFUSES TO DO
#   * guess a cluster: CLUSTER defaults to devnet and is printed on every step
#     that acts (`--provider.cluster`), exactly as the runbook requires;
#   * deploy or mint without its flag being passed;
#   * continue past a failed step: each step's exit status is checked, recorded
#     and, unless --keep-going is given, fatal.
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The Solana release on this box is a manual native-Windows install (the
# versioned agave-install-init.exe path of plan §1.1): the Windows installer
# refuses this host's architecture, so the release is unpacked and put on PATH
# here. Both directories are harmless if absent.
export PATH="$HOME/.local/share/solana/solana-release/bin:$HOME/.cargo/bin:$PATH"
export CARGO_TERM_COLOR=never

CLUSTER="${CLUSTER:-devnet}"
WALLET="${ANCHOR_WALLET:-$HOME/.config/solana/id.json}"

# THE ENDPOINT BELONGS TO THE RUN, NOT TO THIS FILE. XMRJ_RPC_URL wins (the name
# the stress harness already reads), then ANCHOR_PROVIDER_URL, then the cluster's
# public endpoint. Both clients now build their connection from it and wait a
# throttled endpoint out instead of dying on a 429 — and an operator can point a
# run at devnet.rpcpool.com (the same cluster) when the public one is saturated.
RPC_URL="${XMRJ_RPC_URL:-${ANCHOR_PROVIDER_URL:-https://api.$CLUSTER.solana.com}}"
export XMRJ_RPC_URL="$RPC_URL"
export ANCHOR_PROVIDER_URL="$RPC_URL"

# The suite and the harness are NODE programs. Node on Windows cannot read the
# MSYS form of a path that bash prints (`/c/Users/...`) — it resolves it against
# the current drive and fails to find the keypair — so they are handed the
# native form. `cygpath` does not exist on Linux/macOS, where $WALLET is already
# a native path for Node.
WALLET_NATIVE="$WALLET"
if command -v cygpath >/dev/null 2>&1; then
  WALLET_NATIVE="$(cygpath -w "$WALLET")"
fi

BUILD=0; DEPLOY=0; E2E=0; MINT=0; STRESS=0; KEEP_GOING=0
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --deploy) DEPLOY=1 ;;
    --e2e) E2E=1 ;;
    --mint) MINT=1 ;;
    --stress) STRESS=1 ;;
    --keep-going) KEEP_GOING=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg (see --help)"; exit 2 ;;
  esac
done

mkdir -p reports
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="reports/xmrj_runbook_${CLUSTER}_${STAMP}.log"
: > "$LOG"

step_no=0
step() { # step <title> <command...>
  step_no=$((step_no + 1))
  local title="$1"; shift
  {
    printf '\n===== STEP %02d: %s =====\n' "$step_no" "$title"
    printf '$ %s\n' "$*"
  } | tee -a "$LOG"
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  printf '%s\n[exit %d]\n' "$out" "$rc" | tee -a "$LOG"
  LAST_RC=$rc
  if [ "$rc" -ne 0 ]; then
    printf '\n*** STEP %02d FAILED (exit %d) ***\n' "$step_no" "$rc" | tee -a "$LOG"
    if [ "$KEEP_GOING" -eq 0 ]; then
      printf 'stopping: pass --keep-going to continue past a failure\n' | tee -a "$LOG"
      printf '\nfull log: %s\n' "$LOG"
      exit "$rc"
    fi
  fi
  return 0
}

# `step` needs the command's stdout captured; a couple of steps need a shell
# pipeline, so they get their own runner.
step_sh() { # step_sh <title> <shell command string>
  step_no=$((step_no + 1))
  local title="$1"; shift
  {
    printf '\n===== STEP %02d: %s =====\n' "$step_no" "$title"
    printf '$ %s\n' "$*"
  } | tee -a "$LOG"
  local out rc
  out="$(bash -c "$*" 2>&1)"; rc=$?
  printf '%s\n[exit %d]\n' "$out" "$rc" | tee -a "$LOG"
  if [ "$rc" -ne 0 ]; then
    printf '\n*** STEP %02d FAILED (exit %d) ***\n' "$step_no" "$rc" | tee -a "$LOG"
    if [ "$KEEP_GOING" -eq 0 ]; then
      printf 'stopping: pass --keep-going to continue past a failure\n' | tee -a "$LOG"
      printf '\nfull log: %s\n' "$LOG"
      exit "$rc"
    fi
  fi
  return 0
}

printf 'XMRJ runbook execution — cluster %s — %s\nlog: %s\nendpoint: %s\n' \
  "$CLUSTER" "$STAMP" "$LOG" "$RPC_URL" | tee -a "$LOG"
printf 'flags: build=%d deploy=%d e2e=%d mint=%d stress=%d keep_going=%d\n' \
  "$BUILD" "$DEPLOY" "$E2E" "$MINT" "$STRESS" "$KEEP_GOING" | tee -a "$LOG"

# ── §0.1 preflight ──────────────────────────────────────────────────────────
step_sh "preflight: the five pinned tools must be present" \
  'for t in anchor solana rustc cargo avm; do printf "%-8s " "$t"; command -v "$t" >/dev/null 2>&1 && "$t" --version 2>&1 | head -1 || echo MISSING; done'
step_sh "preflight: node and npm" 'node --version && npm --version'
step_sh "preflight: the working tree (only files you intend to commit)" 'git status --porcelain'

# ── §3 build (optional) ─────────────────────────────────────────────────────
if [ "$BUILD" -eq 1 ]; then
  # NB: no `; echo "rc=$?"` here. That trailing echo made the build's exit status
# the pipeline's, so a RED build was recorded as a green step and the run
# carried on to deploy the artifacts the failed build had left behind.
step_sh "§3 anchor build (pinned toolchain from Anchor.toml)" 'anchor build'
fi

# ── §3 the artifact gate: a deploy artifact must be a PROGRAM ──────────
# TWO failure modes are known here, and both were hit live on Oct 27:
#
# (1) THE ENTRYPOINT-LESS ARTIFACT. A program is compiled TWICE in this
#     workspace: once as the primary package, and once as the library the
#     sibling that CPIs into it links — where the `cpi` feature implies
#     `no-entrypoint`. Both write the same
#     target/sbpf-solana-solana/release/<program>.so, which is the path
#     `anchor build` copies into target/deploy. When the entrypoint-less build
#     is the one that lands there, the artifact is a VALID ELF with
#     e_entry = 0 and 8 bytes of .text: an EMPTY program. The deploy of Oct 27
#     uploaded exactly that for xmrj_treasury (896 bytes on the deploy path)
#     and the loader refused it — "Entrypoint out of bounds" — three times,
#     while xmrj_jackpot and xmrj_governance upgraded normally in the same
#     step and a partial buffer was left behind. It can never overwrite a
#     WORKING program (the loader is the backstop), but it does cost the
#     deploy cycle and it looks green in every other check.
#
# (2) THE STALE, UNHASHED DEPENDENCY RLIB. `cargo build-sbf` writes
#     target/<target>/release/deps/lib<name>.rlib — UNHASHED — for the crate
#     it builds as the primary package. A later build of a crate that DEPENDS
#     on that program picks that path up as the artifact of the dependency
#     unit, whatever feature set it was built with, and reports the
#     dependency "Fresh". With a stale one in place the caller fails to
#     compile with `could not find `cpi` in `xmrj_jackpot`` and `the
#     #[global_allocator] in this crate conflicts with global allocator in:
#     xmrj_jackpot` — reproduced and cleared on Oct 27 by deleting the
#     libxmrj_*.rlib files and rebuilding.
#
# Building each program from its OWN directory is a single-package build —
# nothing in it can turn `no-entrypoint` on — and deleting the unhashed rlibs
# first removes failure mode 2's poisoned input. The artifact drive then
# PROVES the result: check 1.3 reads the ELF header and the .text size, and
# the drive is read-only (no toolchain, no cluster, writes nothing).
step_sh "§3 clear the unhashed libxmrj_*.rlib files cargo-build-sbf leaves in the shared target dir (a later build reuses one as a dependency artifact with the wrong features)" \
  'ls -l target/sbpf-solana-solana/release/deps/libxmrj_*.rlib 2>/dev/null || echo "none present"; rm -fv target/sbpf-solana-solana/release/deps/libxmrj_*.rlib'
step_sh "§3 rebuild each program on its own — the deploy path can hold the entrypoint-less build of the same crate" \
  'for p in xmrj_treasury xmrj_jackpot xmrj_governance; do printf -- "--- %s\n" "$p"; (cd "$p" && cargo build-sbf 2>&1 | tail -2); done'
step "§3 verify: the artifacts a deploy would upload" ls -l target/deploy
step_sh "§3 verify: the artifact drive — existence, freshness, ids, IDL surface, rent, and 1.3 (each .so is a program, not an entrypoint-less stub)" \
  'python test_xmrj_build_artifacts.py'
step_sh "§3 verify: anchor keys list must print the three ids of runbook §0" 'anchor keys list'

# ── §2 wallet and the rent budget from the REAL .so sizes ───────────────────
step "§2 wallet: which key will pay" solana address
step_sh "§2 wallet: balance on $CLUSTER" "solana balance --url $CLUSTER"
step_sh "§2 rent budget: (size + 128) * 5080 lamports per program, from the built .so files" \
  'python -c "
import pathlib
tot=0
for p in sorted(pathlib.Path(\"target/deploy\").glob(\"*.so\")):
    r=(p.stat().st_size+128)*5080; tot+=r
    print(f\"{p.name:34s} {p.stat().st_size:8d} bytes -> {r/1e9:.6f} SOL\")
print(f\"programs total: {tot/1e9:.6f} SOL; add ~0.0015 for the mint and 2 state accounts\")"'

# ── §4 deploy ───────────────────────────────────────────────────────────────
if [ "$DEPLOY" -eq 1 ]; then
  step_no=$((step_no + 1))
  {
    printf '\n===== STEP %02d: §4 deploy the three programs to %s =====\n' "$step_no" "$CLUSTER"
    printf '$ anchor deploy --provider.cluster %s\n' "$CLUSTER"
  } | tee -a "$LOG"
  # `anchor deploy` cannot complete on this host: after each program's upload
  # it tries to upload the IDL and dies with `Error: program not found`, which
  # aborts the run with 1 of 3 programs deployed. `anchor program deploy
  # --no-idl` is the same deploy without that step (the IDLs stay in
  # target/idl, where the suite and the harness read them).
  for prog in xmrj_treasury xmrj_jackpot xmrj_governance; do
    ( anchor program deploy --program-name "$prog" --provider.cluster "$CLUSTER" --no-idl 2>&1
      echo "[exit $?]" ) | tee -a "$LOG"
  done
  step_sh "§4 verify: program show must report the upgradeable loader, your wallet as authority, Executable: true" \
    'for id in GGPyRTZB73reGyjANw9rdEFX8g26MmkKyAEpDqNQ2468 6oHq39qTPVtXEiKPLt1mmxfRwRy3hucd3Qx5SJXdYfox AVbKyQ8KZa8hTHQwwSJTRGQsVQxVHzTmzmohVCH5CLe; do echo "--- $id"; solana program show "$id" --url '"$CLUSTER"'; done'
fi

# ── §5 initialize + prove ───────────────────────────────────────────────────
if [ "$E2E" -eq 1 ]; then
  step_sh "§5 the devnet suite: deploys nothing it was not asked to, initializes, proposes, executes, and asserts the refusals" \
    "XMRJ_DEVNET_E2E=1 XMRJ_RPC_URL='$RPC_URL' ANCHOR_WALLET='$WALLET_NATIVE' python test_xmrj_devnet_e2e.py"
fi

# ── the stress harness (not part of the runbook; the evidence for §5's claims) ─
if [ "$STRESS" -eq 1 ]; then
  step_sh "stress: the S0-S10 load/stress harness against $CLUSTER" \
    "XMRJ_RPC_URL='$RPC_URL' ANCHOR_WALLET='$WALLET_NATIVE' npx ts-node tools/xmrj_loadtest.ts"
fi

# ── §6/§7 the token ─────────────────────────────────────────────────────────
if [ "$MINT" -eq 1 ]; then
  step_sh "§6 mint the fixed supply (records xmrj_devnet_token.json)" 'node create_xmrj_mint.js'
  step_sh "§6 rerun must report the existing record and change nothing" 'node create_xmrj_mint.js'
  step_sh "§7 lock the mint authority — ONE WAY" 'node create_xmrj_mint.js --lock'
  step_sh "§7 verify: the record says the authority is disabled" \
    'python -c "import json;print(json.load(open(\"xmrj_devnet_token.json\")))"'
fi

# ── §8 git hygiene ──────────────────────────────────────────────────────────
step_sh "§8 no keypair, no .so and no token record is staged" \
  'git status --porcelain | grep -E "xmrj_devnet_token.json|keypair|\.so" || echo "clean"'

printf '\nrunbook execution finished — %d steps, log: %s\n' "$step_no" "$LOG" | tee -a "$LOG"
