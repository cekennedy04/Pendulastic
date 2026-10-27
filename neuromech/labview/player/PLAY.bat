@echo off
rem Drag a player .csv onto this file (or: PLAY.bat R001.csv). Plays it into the rig's setpoint variable.
call "%~dp0_run.bat" -File "%~1" %2 %3 %4 %5 %6
