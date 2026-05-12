@echo off
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 tools\install_watchdog.py
    goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python tools\install_watchdog.py
    goto :done
)

echo Python was not found. Install Python from https://www.python.org/downloads/
echo Make sure "Add Python to PATH" is enabled during installation.

:done
pause
