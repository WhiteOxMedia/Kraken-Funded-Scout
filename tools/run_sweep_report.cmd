@echo off
rem run_sweep_report.cmd - the Task Scheduler wrapper for the weekly
rem orphan report (task "CodebuffWeeklyOrphanReport"). The report is a
rem mode=ro read of pool/jackpot.db: it can never write the DB. The
rem EXIT CODE is the alert (0 all-clear, 1 violations found, 2 an edge
rem is UNPLANNED) and lands in Task Scheduler's Last Result; the full
rem per-edge receipt appends to logs_sweep_report.txt. See
rem "Scheduled alerting" in README.md.
cd /d "%~dp0.."
.venv\Scripts\python.exe tools\sweep_db.py --report >> logs_sweep_report.txt 2>&1
