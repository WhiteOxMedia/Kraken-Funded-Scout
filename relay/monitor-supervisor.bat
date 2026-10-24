@echo off
REM monitor-supervisor.bat - run relay/monitor-relay.py forever, restarting it if it dies.
REM No admin rights needed: plain python, plain console.
REM
REM External (check-host) probe runs every 15 minutes by default; the local four
REM probes run every 60 s, so a dead tunnel is noticed in about a minute.
cd /d "%~dp0.."
:loop
echo [%DATE% %TIME%] starting monitor >> ".relay\logs\monitor.out"
python "relay\monitor-relay.py" --external --external-every 900 --interval 60 --witness-url "https://site-1a0ea1f6ed6a49ebb2c0de8e532498c3.freebuff.page" >> ".relay\logs\monitor.out" 2>&1
echo [%DATE% %TIME%] monitor exited (%ERRORLEVEL%), restarting in 30s >> ".relay\logs\monitor.out"
timeout /t 30 /nobreak >nul
goto loop
