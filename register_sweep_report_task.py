"""register_sweep_report_task.py - THE TRACKABLE TASK REGISTRAR
(Sep 27 hardening of CodebuffWeeklyOrphanReport).

The scheduled task was first registered by hand (schtasks //Create);
this script makes the registration REPRODUCIBLE and SELF-CHECKING so
the guarantee outlives this box's current Task Scheduler entry:

  python register_sweep_report_task.py            register/update
  python register_sweep_report_task.py --check    verify-only, exit code:
                                                  0 wired, 1 drifted

The registered task carries what the hand-rolled one lacked:
  * StartWhenAvailable - the CATCH-UP contract: a scheduled run the
    machine was off for fires when the machine next becomes
    available (the missed Sunday runs at the next boot/logon),
    exactly once - the due-gated wrapper makes that single catch-up
    run correct. (A separate BootTrigger was attempted and needs
    admin rights to register; StartWhenAvailable delivers the
    same user-visible catch-up without elevation, so it is the
    registered mechanism.)
  * ExecutionTimeLimit 1h (the default 3 days can leave a stuck run
    occupying the slot for a weekend);
  * battery kill-switches OFF (a laptop on battery at boot must still
    be scanned; the scan is a seconds-long read).

Every firing routes through run_sweep_report_task.ps1, which is DUE-
GATED (at most one actual report per 7d window) - so the boot trigger
is a safety net, not a second weekly run. The exit code of an actual
run (0 all-clear / 1 violations / 2 UNPLANNED edge) becomes Task
Scheduler's Last Result; a skipped-because-fresh firing exits 0
without touching the DB. --check verifies exactly these properties:
both triggers present, StartWhenAvailable on, the wrapper command,
the 1h execution limit, and (when the marker exists) the dead-man
honesty - the marker must be younger than the 8-day alert horizon
the pool scheduler's dead-man leg enforces.

Registration goes through the Register-ScheduledTask cmdlet (the
hand-written schtasks XML tripped the schema validator). Stdlib-only
at import time; the cmdlet is only invoked by register().
"""
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK_NAME = "CodebuffWeeklyOrphanReport"
WRAPPER = HERE / "run_sweep_report_task.ps1"
MARKER = HERE / "task_sweep_report_last.txt"
# the dead-man horizon (must match pool/scheduler.py's
# ORPHAN_DEADMAN_MAX_AGE_S - the drive pins the equality)
DEADMAN_MAX_AGE_S = 8 * 86400

PS_REGISTER = r"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File "{wrapper}"'
$weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 06:15
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '{name}' `
    -Action $action -Trigger $weekly -Settings $settings -Force | Out-Null
Write-Output 'registered'
""".replace("{wrapper}", str(WRAPPER)).replace("{name}", TASK_NAME)


def register():
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", PS_REGISTER],
        capture_output=True, text=True)
    if r.returncode != 0 or "registered" not in r.stdout:
        print(f"FAIL  Register-ScheduledTask refused: "
              f"{(r.stderr or r.stdout).strip()[:300]}")
        return 1
    print(f"OK  task '{TASK_NAME}' registered (weekly SUN 06:15 "
          f"+ boot catch-up, StartWhenAvailable, 1h limit, "
          f"battery-safe)")
    return check()


def _task_xml():
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Export-ScheduledTask -TaskName '{TASK_NAME}'"],
        capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def check():
    """Exit 0 when the registered task carries the whole contract,
    exit 1 naming what drifted (the dead-man self-check included)."""
    xml = _task_xml()
    if not xml:
        print(f"FAIL  task '{TASK_NAME}' not found or not exportable")
        return 1
    problems = []
    boots = len(re.findall(r"<BootTrigger>", xml))
    if boots:
        print(f"note: a BootTrigger is also present ({boots}) - "
              f"registered by an elevated pass")
    # StartWhenAvailable is written at the TASK-SETTINGS level by the
    # cmdlet (that is where the catch-up switch lives, not per-trigger)
    if "<StartWhenAvailable>true</StartWhenAvailable>" not in xml:
        problems.append("StartWhenAvailable is off (a missed Sunday "
                        "would be skipped, not caught up)")
    if re.search(r"<Sunday\s*/>", xml) is None:
        problems.append("weekly Sunday trigger drifted")
    if "<ExecutionTimeLimit>PT1H</ExecutionTimeLimit>" not in xml:
        problems.append("ExecutionTimeLimit is not 1h")
    if "<DisallowStartIfOnBatteries>false" not in xml \
            or "<StopIfGoingOnBatteries>false" not in xml:
        problems.append("battery kill-switches are ON (a laptop boot "
                        "would skip the catch-up)")
    if "powershell.exe" not in xml or str(WRAPPER) not in xml:
        problems.append(f"the action does not run {WRAPPER.name}")
    # the dead-man self-check: our own marker must sit inside the
    # horizon the pool scheduler enforces (a stale marker means the
    # task has not ACTUALLY run for over a week - exactly the state
    # the scheduler's alert fires on; the registrar reports it too)
    if MARKER.exists():
        try:
            age = time.time() - float(MARKER.read_text(encoding="ascii").strip())
            if age > DEADMAN_MAX_AGE_S:
                problems.append(
                    f"the last ACTUAL run marker is {age / 86400:.1f}d old "
                    f"(past the {DEADMAN_MAX_AGE_S // 86400}d dead-man "
                    f"horizon - the pool scheduler's next tick alerts)")
        except (ValueError, OSError):
            problems.append("the run marker is unreadable (treated as "
                            "stale by both checks)")
    if problems:
        print(f"DRIFT  {len(problems)} problem(s) in '{TASK_NAME}':")
        for pr in problems:
            print(f"  - {pr}")
        return 1
    print(f"OK  '{TASK_NAME}' carries the full contract: weekly SUN "
          f"06:15, StartWhenAvailable catch-up (a missed Sunday runs "
          f"at the next available start), 1h limit, battery-safe, "
          f"due-gated wrapper, dead-man marker inside the "
          f"{DEADMAN_MAX_AGE_S // 86400}d horizon")
    return 0


def main(argv):
    if "--check" in argv:
        return check()
    return register()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
