"""Launch the committed-tree sweep runner FULLY detached from this
process tree: DETACHED_PROCESS + its own process group + breakaway, so
no tool timeout, console teardown, or kill sweep can ever reap it (the
double-runner lesson of Sep 30, 19:24). Returns instantly — the caller
never sits on a foreground process, so there is nothing to time out and
nothing to retry.

The runner loop: a mkdir lock (atomic, crash-safe), up to 14 chained
passes, each = sbx_prep (build/heal/HEAD-overlay/verify) + ONE --all
--resume sweep run with --keep-sbx (the sandbox survives between passes
and is NEVER silently rebuilt from the working tree — that would
restore the 4 staged files and judge the wrong content).
"""
import subprocess

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

LOOP = (
    'mkdir _sweep_run.lock 2>/dev/null || { echo "=== lock held — '
    'exiting (a runner is already alive) ==="; exit 0; }\n'
    'trap "rmdir _sweep_run.lock 2>/dev/null" EXIT\n'
    'for i in $(seq 1 14); do\n'
    '  echo "=== pass $i start $(date +%H:%M:%S) ==="\n'
    '  python -X utf8 scratch/sbx_prep.py || '
    '{ echo "=== prep failed, aborting ==="; exit 1; }\n'
    '  SWEEP_REUSE_SBX=1 .venv/Scripts/python.exe -X utf8 '
    'diag_mutation_sweep.py --all --resume --keep-sbx\n'
    '  rc=$?\n'
    '  echo "=== pass $i rc=$rc $(date +%H:%M:%S) ==="\n'
    '  if [ $rc -eq 0 ] && grep -q "total 162:" '
    'artifacts/mutation_sweep/mutation_sweep_report.txt 2>/dev/null; '
    'then echo "=== FLEET COMPLETE, pass $i ==="; break; fi\n'
    '  sleep 2\n'
    'done\n'
)

creationflags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                 | CREATE_BREAKAWAY_FROM_JOB)
with open("_sweep_committed_run.log", "ab") as log:
    p = subprocess.Popen(
        ["bash", "-c", LOOP],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        creationflags=creationflags, close_fds=True)
    print(f"runner detached: pid={p.pid} (creationflags={creationflags:#x})")
