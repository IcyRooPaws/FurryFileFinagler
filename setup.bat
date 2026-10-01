@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :failed
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo Installing FurryFileFinagler...
".venv\Scripts\python.exe" -m pip install -e . --no-deps
if errorlevel 1 goto :failed

echo Launching setup...
".venv\Scripts\python.exe" -m fff setup
if errorlevel 1 goto :failed

echo Setup finished.
pause
exit /b 0

:failed
set "setup_error=%errorlevel%"
echo Setup failed with exit code %setup_error%.
pause
exit /b %setup_error%