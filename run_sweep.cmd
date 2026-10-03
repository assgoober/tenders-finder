@echo off
REM Unattended wide sweep. Registered with Windows Task Scheduler so the job
REM runs without an agent session in the loop -- the first attempt wrapped it
REM in a scheduled Claude session, which started, sat silent for thirteen
REM minutes and wrote nothing.
cd /d "%~dp0"

REM Pin Playwright's browsers inside the project.
REM
REM By default Playwright looks in %LOCALAPPDATA%\ms-playwright -- per USER.
REM From 17/09 mod-balam failed every night with "Executable doesn't exist
REM at ...chromium_headless_shell-1148\...", while the same executable ran
REM fine by hand. The likeliest reading: the task runs under a different
REM account than the shell it was installed from, so it looks in a different
REM profile. A path inside the project is the same for every account.
REM
REM The install is idempotent and returns in a second when the browser is
REM already there; it is here so a fresh checkout, a Playwright upgrade, or a
REM wiped folder repairs itself instead of costing the home market a night.
REM A failed install is not fatal: mod-balam then aborts, and the digest says so.
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0.playwright-browsers"
REM Its output goes to a file and its exit code into the run, so a failed
REM install is in the sweep log and the digest rather than in a console
REM window nobody sees.
if not exist "%~dp0logs" mkdir "%~dp0logs"
python -m playwright install chromium > "%~dp0logs\playwright-install.log" 2>&1
set "PLAYWRIGHT_INSTALL_EXIT=%ERRORLEVEL%"

python "%~dp0scraper-service\run_sweep.py" %*
exit /b %ERRORLEVEL%
