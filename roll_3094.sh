#!/usr/bin/env bash
# roll_3094.sh — roll the :3094 production pool server and verify it in one command.
#
# This is the README "Restart it" recipe as an executable, plus verification.
# The serving model (README, pinned note): editing pool/ sources and restarting
# the :3094 server IS the deployment; the Cloudflare tunnel fronts :3094 as
# https://xmrjackpot.com. Nothing here ships anywhere.
#
# Usage (bash on Windows, from the repo root):
#   ./roll_3094.sh              # roll + verify locally
#   ./roll_3094.sh --tunnel     # roll + verify locally AND through the tunnel
#
# What it does, in order, each step FAILING LOUD (exit 1) rather than guessing:
#   1. report git HEAD and the current :3094 listener PID
#   2. kill the old listener (taskkill //F //PID — the README's own line; none
#      listening = fine, the script is idempotent)
#   3. start the server hidden via the README PowerShell recipe
#      (APP_PORT=3094, stdout to logs_server_restart.txt)
#   4. wait-loop on the /api/stats health check (README: "200 means the gate's
#      live drive will run, 000 means it will self-skip"). --tunnel also checks
#      https://xmrjackpot.com, proving the public site serves the new build.
#   5. verify the surface: page carries data-cta-slot tags; the Kraken click
#      counter accepts a valid browser-shaped click (200), refuses a bot
#      user-agent (403 "not a browser click"), and rejects a bad slot (422
#      "unknown CTA slot"). exit 0 only if every check passed. The probes
#      are SHARED — cta_probe.sh defines them (roll_io annotates here).
#      roll_3094.sh owns nothing about them: expected codes, UA, origin,
#      payload shape and endpoint live in cta_probe.sh alone and the
#      counter drive pins that file, so a probe changed here routes
#      through the drive's single contract check.
set -euo pipefail
cd "$(dirname "$0")"

PASS_N=0; FAIL_N=0
BASE_URL=http://127.0.0.1:3094
SITE_URL=https://xmrjackpot.com

# verification layer: roll_3094.sh owns io orchestration only.
source "$(dirname "$0")/cta_probe.sh"

# ROLL IO ONLY BELOW THIS LINE (Oct 12) — the shared probe layer
# (single source of truth: cta_probe.sh) is loaded above; nothing here
# duplicates its checks. This file's job: kill the old :3094 listener,
# start the server, wait-loop on health, invoke the checks, count, verdict.

note() { printf '%s\n' "[$(date +%H:%M:%S)] $*"; }

note "git HEAD: $(git rev-parse --short HEAD) $(git log -1 --format=%s | head -c 80)..."

old_pid=$(netstat -ano | grep ":3094" | grep -i LISTEN | awk '{print $NF}' | head -1 || true)
if [ -n "${old_pid:-}" ]; then
  note "old :3094 listener pid $old_pid — killing (README recipe)"
  taskkill //F //PID "$old_pid" >/dev/null
  sleep 2
else
  note "no old :3094 listener — cold start"
fi

note "starting server (README PowerShell recipe, APP_PORT=3094, log logs_server_restart.txt)"
WIN_PWD=$(cygpath -w "$PWD")
powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c set APP_PORT=3094&& .venv\\Scripts\\python.exe pool\\server.py > logs_server_restart.txt 2>&1' -WorkingDirectory '$WIN_PWD' -WindowStyle Hidden"

deadline=$(( $(date +%s) + 45 ))
until [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL$_CTA_PROBE_HEALTH" 2>/dev/null || echo 000)" = 200 ]; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    printf 'FAIL server never answered /api/stats 200 within 45s — last log lines:\n'
    tail -5 logs_server_restart.txt 2>/dev/null || true
    exit 1
  fi
  sleep 3
done

new_pid=$(netstat -ano | grep ":3094" | grep -i LISTEN | awk '{print $NF}' | head -1)
note "new :3094 listener pid $new_pid is up"

note "verifying local surface ($BASE_URL — the build uvicorn serves)"
check_surface "$BASE_URL"

TUNNEL=0
if [ "${1:-}" = "--tunnel" ]; then
  note "verifying tunnel surface ($SITE_URL — what the public sees)"
  check_surface "$SITE_URL"
  TUNNEL=1
fi

note "verdict: $PASS_N checks passed, $FAIL_N failed"
[ "$FAIL_N" -eq 0 ] || exit 1
if [ "$TUNNEL" -eq 1 ]; then
  note "roll complete — the tunnel is serving this build"
else
  note "roll complete — local verified (tunnel not checked; rerun with --tunnel)"
fi
