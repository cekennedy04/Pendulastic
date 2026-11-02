@echo off
REM Runs recalibrate.ps1 from cmd.exe, where ".\recalibrate.ps1" only hands the
REM file to its default association and silently does nothing. -NoProfile so a
REM slow or broken profile cannot affect it; -ExecutionPolicy Bypass because an
REM unsigned local script is otherwise refused on a default Windows policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0recalibrate.ps1" %*
