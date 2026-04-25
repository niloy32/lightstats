@echo off
REM Launch with console visible so Python tracebacks are immediately readable.
REM Use this when run.bat "does nothing" — this will show the error.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual env...
    python -m venv .venv || goto :error
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Launching main.py (console visible; Ctrl+C to quit)...
echo Crash log: %cd%\crash.log
echo.
".venv\Scripts\python.exe" main.py
echo.
echo App exited with code %errorlevel%.
pause
exit /b %errorlevel%

:error
echo.
echo Setup failed.
pause
exit /b 1
