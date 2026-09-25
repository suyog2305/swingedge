@echo off
rem ============================================================================
rem SwingEdge daily pull -- run by Windows Task Scheduler (task "SwingEdge Daily Pull"),
rem weekdays at 15:50 IST, with a second trigger at 20:00 as a safety net.
rem
rem screener.in refreshes a close in stages -- prices first; moving averages, 3/6-month returns
rem and volume later -- so eod.py polls from 15:50 until all of those have moved against the
rem previous scan (--wait-until 21:00, a check every 10 minutes) and builds the moment the close
rem is complete. The 20:00 trigger exits at once if the day is already built, and the scheduler
rem ignores it while the 15:50 run is still polling. The task wakes a sleeping PC for both.
rem
rem Until 2026-09-10 this file chained fetch_screener -> build_shortlist -> build_s2history
rem -> git by hand, and called fetch_screener WITHOUT a date. That defaulted to the previous
rem Friday, so every daily scan was written over one Friday-labelled file (two different
rem closes were found in data\scans\2026-09-04.json). It also made a single pull, so the
rem 6-month / 1-year returns and volume never reached the export.
rem
rem tools\eod.py is now the one maintained path. It dates the scan TODAY, makes three pulls
rem of the same universe and unions them by exact code (screener exports at most two extra
rem columns per pull), skips non-trading days when screener is still serving the last close,
rem rebuilds the shortlist and the Stage 2 journal, then commits, rebases and pushes -- and it
rem stops rather than mixing a fresh shortlist with a stale scan. This wrapper only logs.
rem
rem Logs to .secrets\daily_pull.log (gitignored). Run manually any time:  tools\daily_pull.cmd
rem ============================================================================
setlocal
cd /d "%~dp0.."
set "PATH=C:\Program Files\Git\cmd;%PATH%"
set "PY=C:\Python314\python.exe"
set "PYTHONIOENCODING=utf-8"
if not exist ".secrets" mkdir ".secrets"
set "LOG=.secrets\daily_pull.log"

echo(>> "%LOG%"
echo ====================================================================>> "%LOG%"
echo [%date% %time%] daily pull start (tools\eod.py --push --wait-until 21:00 --poll 10)>> "%LOG%"
"%PY%" tools\eod.py --push --wait-until 21:00 --poll 10 >> "%LOG%" 2>&1
echo [%date% %time%] finished with exit code %errorlevel%>> "%LOG%"
endlocal
