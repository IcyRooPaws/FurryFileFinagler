@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :no_venv

".venv\Scripts\python.exe" -m fff gui manager
if errorlevel 1 goto :failed
exit /b 0

:no_venv
echo FFF is not installed in this folder yet. Run setup.bat first.
pause
exit /b 1

:failed
set "manager_error=%errorlevel%"
echo FFF Manager exited with code %manager_error%.
pause
exit /b %manager_error%