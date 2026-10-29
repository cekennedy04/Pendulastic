@echo off
rem 64-bit PowerShell first; if NI DataSocket is only registered 32-bit (exit 3), retry in 32-bit.
set "PS64=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "PS32=%SystemRoot%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
"%PS64%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0play_setpoints.ps1" %*
if errorlevel 3 if not errorlevel 4 if exist "%PS32%" "%PS32%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0play_setpoints.ps1" %*
pause
