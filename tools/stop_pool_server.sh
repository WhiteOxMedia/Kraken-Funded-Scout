#!/usr/bin/env bash
# stop_pool_server.sh - the STOP toggle for the live pool checkout (Oct 29).
#
# Why it exists: /downloads/KrakenScoutBundle.zip streams the artifact
# through FileResponse, so Windows holds the zip open for exactly the
# life of a download; while that handle exists, Explorer refuses to
# delete/replace the file with the classic
#   "You require permission from <machine>\<user> to make changes"
# dialog (the name is just the file's owner, not a real grant you can
# click elsewhere). Stopping the server releases every handle, and old
# bundle versions become deletable.
#
# THE STOP (safe by construction):
#   1. Resolve the listener PID on --port (default 3094) from
#      Get-NetTCPConnection - never a process-name sweep.
#   2. Print the process image + command line for that PID and REFUSE
#      to kill unless the command line matches pool[/\]server.py (the
#      pool server's one entry point).
#   3. taskkill /T /F the verified PID, then poll until the port is
#      free (or report the leftover PID and exit 3).
#
#   --dry-run   resolve, print, and verify the gate WITHOUT killing;
#               and cross-reference the [Downloads] serving entries of
#               logs_server_restart.txt (pool/server.py's one line per
#               served artifact, Oct 30): the recent handler_pid values
#               are compared against the candidate PID(s), so a dry run
#               answers the "Explorer still locks dist/Kraken..." reader's
#               real question - DID the process I am about to stop serve
#               those downloads, i.e. does this stop release the handles.
#               LOG=<path> points the cross-reference elsewhere; a missing
#               or empty log is reported, never an error.
#   --all       ALSO stop other python pool/server.py processes that
#               have no listener (a wedged duplicate server that failed
#               its bind and sat around - the "old version" zombie);
#               each candidate still gets gate-checked individually.
#
# Start the server back with the checkout's usual command, e.g.
#   python pool/server.py   (see start.bat / roll_3094.sh for the fleet)
set -uo pipefail
PORT="${PORT:-3094}"
LOG="${LOG:-}"          # --dry-run's download cross-reference log; default: <repo>/logs_server_restart.txt
PORT_ARG=""
DRY=0; ALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --all) ALL=1; shift ;;
    --port)
      if [ $# -lt 2 ]; then echo "--port needs a value" >&2; exit 2; fi
      PORT_ARG="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
PORT="${PORT_ARG:-3094}"

MATCH='pool[\/]server\.py'

listener_pids() {
  powershell -NoProfile -Command \
    "Get-NetTCPConnection -State Listen -LocalPort $PORT -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique" \
    2>/dev/null | tr -d '\r' | grep -E '^[0-9]+$'
}

cmdline() {
  powershell -NoProfile -Command \
    "(Get-CimInstance Win32_Process -Filter 'ProcessId=$1').CommandLine" \
    2>/dev/null | tr -d '\r'
}

downloads_crossref() {   # $@ = candidate PIDs; DRY must be 1 to show
  local log="$LOG"
  if [ -z "$log" ]; then
    local repo
    repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    log="$repo/logs_server_restart.txt"
  fi
  if [ ! -f "$log" ]; then
    echo "downloads cross-ref: no logs_server_restart.txt at $log -"
    echo "  nothing [Downloads]-recorded to cross-reference"
    return 0
  fi
  local entries
  entries="$(grep -n '\[Downloads\]' "$log" 2>/dev/null | tail -5)"
  if [ -z "$entries" ]; then
    echo "downloads cross-ref: no [Downloads] serving entries in $log"
    return 0
  fi
  echo "downloads cross-ref: recent [Downloads] lines in $log"
  printf '%s\n' "$entries" | sed 's/^/  /'
  local matched="" pids_seen pid
  pids_seen="$(printf '%s\n' "$entries" \
    | grep -o 'handler_pid=[0-9]*' | sed 's/handler_pid=//' \
    | sort -u | tr '\n' ' ')"
  for pid in "$@"; do
    if printf '%s\n' "$entries" | grep -Eq "handler_pid=${pid}([^0-9]|$)"; then
      matched="$matched $pid"
    fi
  done
  if [ -n "${matched:-}" ]; then
    echo "  => the candidate(s)$matched served a recent download -"
    echo "      that is the process whose streaming handles this stop releases"
  else
    echo "  => none of the candidate PID(s) served the recent downloads"
    echo "      (handlers seen: ${pids_seen:-none} - stale log or another incarnation)"
  fi
  return 0
}

kill_one() {   # PID -> 0 killed / 1 gate refused / 2 nothing to do
  local pid="$1" cmd
  cmd="$(cmdline "$pid")"
  if [ -z "$cmd" ]; then echo "stop: PID $pid vanished before the gate" ; return 2; fi
  echo "  candidate PID $pid: $cmd"
  if ! printf '%s' "$cmd" | grep -qE "$MATCH"; then
    echo "  GATE REFUSED (not the pool server entry point) - left running"
    return 1
  fi
  if [ "$DRY" = 1 ]; then
    echo "  DRY-RUN: would taskkill /PID $pid"
    return 0
  fi
  taskkill //PID "$pid" //T //F >/dev/null 2>&1 || echo "  taskkill returned $? (may already be gone)"
  echo "  stop: PID $pid terminated"
  return 0
}

rc=0
lp="$(listener_pids)"
if [ -n "${lp:-}" ]; then
  for pid in $lp; do kill_one "$pid"; done
  if [ "$DRY" = 0 ]; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do   # poll the port free (<= 10s)
      [ -z "$(listener_pids)" ] && break
      sleep 1
    done
    if [ -n "$(listener_pids)" ]; then
      echo "stop: PORT ${PORT} STILL BOUND (pid $(listener_pids))"; rc=3
    else
      echo "port ${PORT} free"
    fi
  fi
else
  echo "no listener on :${PORT}"
fi

if [ "$DRY" = 1 ]; then
  # shellcheck disable=SC2086 -- the PID list only; word-splitting on digits is the point
  downloads_crossref $lp
fi

if [ "$ALL" = 1 ] && [ "$DRY" = 0 ]; then
  # wedged duplicates: python processes whose cmdline is the pool server
  # entry point but that hold NO listener (reviewed PIID by PIID above)
  powershell -NoProfile -Command \
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress" \
    2>/dev/null | python -c "
import re, sys, json
match = re.compile(r'''pool[\\/]server\.py''')
try: procs = json.load(sys.stdin)
except Exception: procs = []
if isinstance(procs, dict): procs = [procs]
for p in procs:
    cmd = p.get('CommandLine') or ''
    if match.search(cmd):
        print(str(p.get('ProcessId')))
" | while read -r pid; do
      [ -n "$pid" ] && kill_one "$pid"
    done
fi
exit $rc
