@echo off
rem ============================================================================
rem SwingEdge daily pull — run by Windows Task Scheduler (task "SwingEdge Daily Pull").
rem Pulls the broad screener.in universe (raw_query in tools\screener_config.json)
rem with your session cookie (.secrets\screener_cookie.txt), rebuilds data\scans\,
rem and commits + pushes. Logs to .secrets\daily_pull.log (gitignored).
rem Requires: your sessionid in .secrets\screener_cookie.txt (else it exits cleanly).
rem On non-trading days screener data is unchanged, so the commit is a clean no-op.
rem Run manually any time:  tools\daily_pull.cmd
rem ============================================================================
setlocal
cd /d "%~dp0.."
set "PATH=C:\Program Files\Git\cmd;%PATH%"
if not exist ".secrets" mkdir ".secrets"
set "LOG=.secrets\daily_pull.log"
echo(>> "%LOG%"
echo ==================================================================== >> "%LOG%"
echo [%date% %time%] daily pull start >> "%LOG%"
"C:\Python314\python.exe" tools\fetch_screener.py --commit >> "%LOG%" 2>&1
echo [%date% %time%] finished with exit code %errorlevel% >> "%LOG%"
endlocal
