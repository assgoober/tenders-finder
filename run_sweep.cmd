@echo off
REM Unattended wide sweep. Registered with Windows Task Scheduler so the job
REM runs without an agent session in the loop -- the first attempt wrapped it
REM in a scheduled Claude session, which started, sat silent for thirteen
REM minutes and wrote nothing.
cd /d "%~dp0"
python "%~dp0scraper-service\run_sweep.py" %*
exit /b %ERRORLEVEL%
