@echo off
rem ============================================================================
rem SwingEdge daily pull — run by Windows Task Scheduler (task "SwingEdge Daily Pull").
rem
rem   1. fetch the broad screener.in universe (raw_query in tools\screener_config.json)
rem      using your cookie in .secrets\screener_cookie.txt, and rebuild data\scans\
rem   2. rebuild the convergence shortlist -> data\daily\shortlist.json
rem   3. commit BOTH in one commit, rebase on origin, and push
rem
rem Logs to .secrets\daily_pull.log (gitignored). If the cookie is missing or the
rem fetch fails, it stops before step 2 and writes nothing. On non-trading days the
rem data is unchanged, so the commit is a clean no-op.
rem The rebase in step 3 keeps this from colliding with the daily gainers-news
rem cloud routine, which pushes data\daily\news.json around 18:30 IST.
rem
rem Run manually any time:  tools\daily_pull.cmd
rem ============================================================================
setlocal
cd /d "%~dp0.."
set "PATH=C:\Program Files\Git\cmd;%PATH%"
set "PY=C:\Python314\python.exe"
if not exist ".secrets" mkdir ".secrets"
set "LOG=.secrets\daily_pull.log"

echo(>> "%LOG%"
echo ====================================================================>> "%LOG%"
echo [%date% %time%] daily pull start>> "%LOG%"

rem ---- 1. scan -------------------------------------------------------------
"%PY%" tools\fetch_screener.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] fetch failed or no cookie - stopping before shortlist/commit>> "%LOG%"
  goto :done
)

rem ---- 2. shortlist --------------------------------------------------------
"%PY%" tools\build_shortlist.py >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: shortlist build failed - committing the scan anyway>> "%LOG%"

rem ---- 2b. Stage 2 day-by-day history -------------------------------------
"%PY%" tools\build_s2history.py --quiet >> "%LOG%" 2>&1
if errorlevel 1 echo [%date% %time%] WARNING: s2history build failed>> "%LOG%"

rem ---- 3. commit all, rebase, push ----------------------------------------
git add data/scans data/daily >> "%LOG%" 2>&1
git diff --cached --quiet
if not errorlevel 1 (
  echo [%date% %time%] nothing changed - nothing to commit>> "%LOG%"
  goto :done
)
git commit -m "Daily scan + shortlist + Stage 2 history" >> "%LOG%" 2>&1
git pull --rebase origin main >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] ERROR: rebase failed - resolve by hand, nothing pushed>> "%LOG%"
  goto :done
)
git push origin main >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] ERROR: push failed>> "%LOG%"
) else (
  echo [%date% %time%] committed and pushed>> "%LOG%"
)

:done
echo [%date% %time%] finished with exit code %errorlevel%>> "%LOG%"
endlocal
