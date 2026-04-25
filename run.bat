@echo off
REM Launcher for LightStats.
REM   * If dist\LightStats.exe exists → launch it (properly-branded, no Python dep).
REM   * Otherwise fall back to dev mode via .venv\Scripts\pythonw.exe main.py.
REM
REM Rebuild the frozen exe with build.bat when source changes.
setlocal
cd /d "%~dp0"

REM --- 1. Prefer the frozen build if present -------------------------------
if exist "dist\LightStats.exe" (
    start "" "dist\LightStats.exe"
    exit /b 0
)

REM --- 2. Dev-mode fallback: set up venv + deps, then launch via pythonw ---
if not exist ".venv\Scripts\pythonw.exe" (
    echo Creating virtual env...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create venv. Is Python installed and on PATH?
        pause
        exit /b 1
    )
)

REM Sync dependencies every run (cheap when already satisfied).
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo pip install failed. See output above.
    pause
    exit /b 1
)

REM Dev mode caveat: Task Manager shows "Python" instead of "LightStats",
REM because Store-installed Python re-execs via AppExecutionAlias. Build
REM the frozen .exe (build.bat) for proper branding.
start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0
