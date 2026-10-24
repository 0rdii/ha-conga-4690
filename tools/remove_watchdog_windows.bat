@echo off
setlocal
cd /d "%~dp0\.."

rem Factory default SSH login of the Conga 3x90/4090/4690/5490 family.
rem Change it here if you set a custom password on your robot.
set "SSH_USER=root"
set "SSH_PASSWORD=@3I#sc$RD%%xm^2S&"

set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON goto :nopython

rem The robot only offers ssh-rsa host keys, which recent paramiko releases dropped.
%PYTHON% -m pip install --user --quiet "paramiko<4"
%PYTHON% tools\remove_watchdog.py --user "%SSH_USER%" --password "%SSH_PASSWORD%"
goto :done

:nopython
echo Python was not found. Install Python from https://www.python.org/downloads/
echo Make sure "Add Python to PATH" is enabled during installation.

:done
pause
