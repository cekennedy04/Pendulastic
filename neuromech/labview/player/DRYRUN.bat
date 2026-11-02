@echo off
rem Drag a player .csv onto this file. Checks the file and the timing; writes NOTHING to the rig.
call "%~dp0_run.bat" -File "%~1" -DryRun %2 %3 %4
