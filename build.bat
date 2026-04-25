@echo off
REM Build a distributable single-file LightStats.exe via PyInstaller.
REM Output lands in dist\LightStats.exe — copy that anywhere to run it.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual env...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create venv. Is Python installed and on PATH?
        pause
        exit /b 1
    )
)

REM Sync runtime deps; build.py installs pyinstaller itself if missing.
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error

".venv\Scripts\python.exe" build.py %*
if errorlevel 1 goto :error

echo.
echo Output: %cd%\dist\LightStats.exe
pause
exit /b 0

:error
echo.
echo Build failed. See output above.
pause
exit /b 1
