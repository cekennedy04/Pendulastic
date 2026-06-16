@echo off
REM ===========================================================================
REM  Launch the Biomechanics Master app using the project's .venv interpreter.
REM
REM  ALWAYS start the app this way (double-click, or run from a terminal).
REM  Do NOT run "python master_app.py" from a base Conda prompt - that OpenCV
REM  build cannot open the USB camera by index ("can't be used to capture by
REM  index"). The .venv has the working opencv-python build.
REM ===========================================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found at "%~dp0.venv".
    echo Create it and install requirements:
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" master_app.py
if errorlevel 1 (
    echo.
    echo [The app exited with an error - see the message above.]
    pause
)
