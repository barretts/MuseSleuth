@echo off
setlocal

echo [1/9] Reset stale running jobs to pending...
python -c "import sqlite3; c=sqlite3.connect(r'Y:\music.db'); n=c.execute(\"UPDATE jobs SET status='pending', worker_id=NULL, claimed_at=NULL, updated_at=datetime('now') WHERE status='running'\").rowcount; c.commit(); c.close(); print('reset', n, 'running->pending')"
if errorlevel 1 goto :fail

echo [2/9] Backfill loudness jobs...
musesleuth backfill-jobs --db Y:\music.db --stage loudness
if errorlevel 1 goto :fail

echo [3/9] Run loudness...
musesleuth run --db Y:\music.db --stage loudness --workers 16
if errorlevel 1 goto :fail

echo [4/9] Run probe...
musesleuth run --db Y:\music.db --stage probe --workers 16
if errorlevel 1 goto :fail

echo [5/9] Run analyze...
musesleuth run --db Y:\music.db --stage analyze --workers 16
if errorlevel 1 goto :fail

echo [6/9] Run fingerprint_match...
musesleuth run --db Y:\music.db --stage fingerprint_match --workers 16
if errorlevel 1 goto :fail

echo [7/9] Run enrich...
musesleuth run --db Y:\music.db --stage enrich
if errorlevel 1 goto :fail

echo [8/9] Run derive_signals...
musesleuth run --db Y:\music.db --stage derive_signals --workers 16
if errorlevel 1 goto :fail

REM Optional: uncomment if you want ml_classify too
REM echo [9/9] Run ml_classify...
REM musesleuth run --db Y:\music.db --stage ml_classify --workers 16
REM if errorlevel 1 goto :fail

echo [done] Dashboard:
python -m musesleuth dashboard --db Y:\music.db

echo.
echo Completed successfully.
goto :eof

:fail
echo.
echo Failed with errorlevel %errorlevel%.
exit /b %errorlevel%
