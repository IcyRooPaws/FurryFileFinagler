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

echo Launching the guided setup...
".venv\Scripts\python.exe" -m fff setup
if errorlevel 2 goto :failed
if errorlevel 1 goto :unfinished

echo Setup finished. Start FFF with manager.bat.
pause
exit /b 0

:unfinished
echo Setup isn't finished yet. FFF can't be used until it is.
echo Run setup.bat again, or open manager.bat, to continue where you left off.
pause
exit /b 1

:failed
set "setup_error=%errorlevel%"
echo Setup failed with exit code %setup_error%.
pause
exit /b %setup_error%