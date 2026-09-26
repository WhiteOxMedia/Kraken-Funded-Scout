@echo off
rem THE ONE COMMAND (npm-test-style): run the full drive fleet.
rem Everything after "test" passes through to run_fleet.py, e.g.:
rem   test --list        test --only push prefs        test --timeout 600
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -X utf8 "%~dp0run_fleet.py" %*
exit /b %ERRORLEVEL%
